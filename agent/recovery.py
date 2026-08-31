"""When to remind, when to issue a backup checkout, when to halt.

NPCI allows four mandate attempts per billing cycle. Failing the fourth
kills the mandate and forfeits later cycles. This module is the product
rule for that last slot:

- After failed attempts 1 and 2 (insufficient funds): send a funding
  reminder. The remaining mandate attempts still run.
- After failed attempt 3: issue a Payment Link and do not fire the fourth
  mandate debit while the link is issued. A paid link collects this cycle
  without spending the fourth attempt, so the mandate survives.
- If the link expires or is cancelled unpaid: still do not fire the
  fourth debit. Collecting this cycle is forfeited; the mandate is not.

Terminal / lien / unknown outcomes are not funding problems. They do not
get a backup checkout.

These functions have no I/O. The loop calls them; Stage 0 executes.
"""
from __future__ import annotations

from dataclasses import dataclass

from agent.ports import (FAMILY_ACCOUNT_SHUT, FAMILY_FUNDS,
                         FAMILY_MANDATE_BROKEN, LIEN_CODES, TERMINAL_CODES,
                         Z9, family_of)

# One presentation + three retries. Same value as w3.NPCI_MAX; kept here so
# this module does not import the world.
ATTEMPT_CAP = 4
BACKUP_LINK_LIFE_HOURS = 48


@dataclass(frozen=True)
class UnresolvedCycle:
    """One billing cycle whose debit outcome is still unknown."""
    cycle: int
    code: str
    reason: str


def indeterminate_reason(code: str, raw_code: str = "") -> str:
    """Greppable label for audit rows. `raw_code` is verbatim rail text."""
    if raw_code and raw_code != code:
        return f"{code} ({raw_code})"
    return code


def should_report_unresolved(unresolved_cycles: dict[int, UnresolvedCycle]) -> bool:
    """Coverage metric: cycles still pending when the run finalizes."""
    return bool(unresolved_cycles)


def is_funds_decline(code: str) -> bool:
    return code == Z9 or family_of(code) == FAMILY_FUNDS


def is_terminal_risk_decline(code: str) -> bool:
    """Hard decline: mandate dead, account shut, or lien. No bounded retry."""
    return code in TERMINAL_CODES or code in LIEN_CODES


def should_emit_risk_retry(*, collected: bool, already_emitted: bool,
                           decline_history: list[str], code: str) -> bool:
    """First insufficient-funds decline this cycle while still uncollected."""
    if collected or already_emitted:
        return False
    if not is_funds_decline(code):
        return False
    return sum(1 for c in decline_history if is_funds_decline(c)) == 1


def should_emit_risk_terminal(*, collected: bool, already_emitted: bool,
                              code: str) -> bool:
    """First terminal hard decline this cycle while still uncollected."""
    if collected or already_emitted:
        return False
    return is_terminal_risk_decline(code)


def should_remind_after_fail(attempts_used: int, code: str,
                             cap: int = ATTEMPT_CAP) -> bool:
    """After attempt 1 or 2 fails on funds, remind. Not after the 3rd
    (that slot is the backup checkout) and not on technical/terminal codes."""
    return (is_funds_decline(code)
            and 0 < attempts_used < cap - 1)


def should_issue_backup_after_fail(attempts_used: int, code: str,
                                   cap: int = ATTEMPT_CAP) -> bool:
    """The third failed funds attempt is the last safe mandate debit.
    Replace the fourth with a Payment Link."""
    return is_funds_decline(code) and attempts_used == cap - 1


def fourth_debit_blocked(backup_status: str | None) -> bool:
    """Once a backup link exists, the fourth mandate debit must not run.

    Issued: wait for pay/expire/cancel. Paid: this cycle is collected;
    a debit would double-charge. Expired or cancelled unpaid: this cycle
    is forfeited so the mandate survives.
    """
    return backup_status in ("issued", "paid", "cancelled", "expired")


def backup_link_collects(backup_status: str | None) -> bool:
    return backup_status == "paid"


def escalate_halts_cycle(code: str | None, root_cause: str = "") -> bool:
    """Handing a recoverable funds case to the merchant must not stop retries.
    A broken mandate, a frozen account, or a lien cannot be retried."""
    if code in LIEN_CODES or root_cause in ("FUNDS_LIENED",):
        return True
    fam = family_of(code) if code else ""
    if fam in (FAMILY_MANDATE_BROKEN, FAMILY_ACCOUNT_SHUT):
        return True
    if root_cause in ("MANDATE_INVALID", "ACCOUNT_UNAVAILABLE"):
        return True
    return False


def diagnoser_stop_yields_to_backup(attempts_used: int, last_code: str,
                                    cap: int = ATTEMPT_CAP) -> bool:
    """STOP-before-cap is the old way to save the mandate. The backup
    checkout is strictly better on a funds decline: it can still collect
    this cycle without spending the fourth attempt."""
    return should_issue_backup_after_fail(attempts_used, last_code, cap)


def batch_legal_ceiling(n_mandates: int, days: int, cycle_days: int,
                        cap: int = ATTEMPT_CAP) -> int:
    """Batch-wide legal maximum on money actions.

    Per cycle the cap is n_mandates × 4. A run of several billing cycles
    multiplies by how many cycles fit in the horizon. Using n_mandates × 4
    with no cycle multiplier would trip on every 120-day batch.
    """
    if n_mandates < 0 or days <= 0 or cycle_days <= 0:
        return 0
    n_cyc = (days + cycle_days - 1) // cycle_days
    return n_mandates * cap * n_cyc
