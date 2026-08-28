"""The five Stage 0 rules, as pure predicates, plus the ledger they read.

THE ENFORCEMENT HALF. `auditor.py` is the other half and shares no code with
this file -- see its docstring for why that separation is the whole point.

WHAT IS SHARED WITH THE AUDITOR AND WHAT IS NOT. Both import the regulatory
CONSTANTS from `w3` (PEAK hours, NPCI_MAX, HOURS). That is deliberate: those
are one external fact each, sourced in docs/01_FACTS.md, and duplicating them
would create a second place for the fact to go stale. Neither imports the
other's LOGIC. What is being cross-checked here is whether the enforcement
works, not whether NPCI's peak window is 10:00-13:00.

TWO OF THESE FIVE HAVE NO REFERENCE IMPLEMENTATION.
`sim/harness.py`'s counters for `cap` (gate M1 is VACUOUS) and `pending`
(gate M4 passes by construction -- its mutant increments the counter itself,
error 11) have never been shown to work. So they are not a spec that can be
ported, and the tests behind them here are written from the rule text in
docs/01_FACTS.md, not from the harness. See test_stage0_enforces.py.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import agent  # noqa: F401  -- puts sim/ on the path
import w3

from agent.ports import (TECH, MandateRef, MoneyAction, PendingNotification,
                         Refusal)

CAP = w3.NPCI_MAX               # 4 attempts per mandate per billing cycle
HOURS = w3.HOURS                # 24
PEAK = w3.PEAK                  # {10,11,12,17,18,19,20,21}


class AttemptLedger:
    """The gate's OWN source of truth. Written only by the gate.

    Modelled on `sim/harness.py:146`, whose ledger comment reads "Written only
    by the harness at dispatch" for exactly this reason: a policy that corrupts
    its own attempt counter must still be caught. The loop's `MandateState` is
    the policy's bookkeeping; this is the regulator's.
    """

    def __init__(self) -> None:
        self._attempts: dict[tuple[str, int], int] = defaultdict(int)
        self._pending: dict[str, PendingNotification | None] = {}
        self._prev_code: dict[tuple[str, int], str | None] = {}

    # ---- reads
    def attempts(self, uid: str, cycle: int) -> int:
        return self._attempts[(uid, cycle)]

    def pending(self, uid: str) -> PendingNotification | None:
        return self._pending.get(uid)

    def prev_code(self, uid: str, cycle: int) -> str | None:
        return self._prev_code.get((uid, cycle))

    # ---- writes (gate only)
    def record_attempt(self, uid: str, cycle: int, code: str) -> None:
        self._attempts[(uid, cycle)] += 1
        self._prev_code[(uid, cycle)] = code
        self._pending[uid] = None

    def set_pending(self, uid: str, p: PendingNotification | None) -> None:
        self._pending[uid] = p

    def open_cycle(self, uid: str, cycle: int) -> None:
        """A new billing cycle resets the per-cycle attempt count."""
        self._attempts[(uid, cycle)] = 0
        self._prev_code[(uid, cycle)] = None
        self._pending[uid] = None


# --------------------------------------------------------------- predicates
# Each returns a Refusal or None. None means "this rule permits the action".

def check_cap(ledger: AttemptLedger, a: MoneyAction) -> Refusal | None:
    used = ledger.attempts(a.ref.uid, a.cycle)
    if used >= CAP:
        return Refusal("cap", f"{used} attempts already used this cycle, cap is {CAP}")
    return None


def check_peak(ledger: AttemptLedger, a: MoneyAction) -> Refusal | None:
    h = a.target_t % HOURS
    if h in PEAK:
        return Refusal("peak", f"target hour {h:02d}:00 is inside an NPCI peak window")
    return None


def check_lead(ledger: AttemptLedger, a: MoneyAction) -> Refusal | None:
    if a.notify_t is None:
        return None                     # re-presentation; check_represent owns it
    lead = a.target_t - a.notify_t
    if lead < HOURS:
        return Refusal("lead", f"{lead}h between notification and execution, need {HOURS}h")
    return None


def check_pending(ledger: AttemptLedger, a: MoneyAction) -> Refusal | None:
    """At most one pending notification per mandate at a time.

    At dispatch the pending notification MUST be this action's own. Anything
    else means a second notification was issued while one was outstanding.
    """
    p = ledger.pending(a.ref.uid)
    if p is None:
        return Refusal("pending", "no notification is outstanding for this mandate")
    if p.target_t != a.target_t or p.notify_t != a.notify_t:
        return Refusal("pending",
                       f"outstanding notification targets t={p.target_t}, "
                       f"action targets t={a.target_t}")
    return None


def check_represent(ledger: AttemptLedger, a: MoneyAction) -> Refusal | None:
    """A Z9 may not be re-presented under the old notification. TECH may."""
    if a.notify_t is not None:
        return None                     # fresh notification; always permitted
    prev = ledger.prev_code(a.ref.uid, a.cycle)
    if prev != TECH:
        return Refusal("represent",
                       f"re-presentation with no fresh notification after "
                       f"prev_code={prev!r}; only {TECH} may auto-represent")
    return None


ALL_RULES = (
    ("cap", check_cap),
    ("peak", check_peak),
    ("lead", check_lead),
    ("pending", check_pending),
    ("represent", check_represent),
)


# ------------------------------------------------- pre-authorisation (issue)
def check_notification(ledger: AttemptLedger, ref: MandateRef, cycle: int,
                       notify_t: int | None, target_t: int) -> Refusal | None:
    """Adjudicates ISSUING a notification, before any money is at stake.

    `pending` is genuinely a rule about issuance, not about dispatch: the
    violation is "a second notification was issued while one was outstanding".
    Checking it only at dispatch would let the illegal issue happen and then
    catch its consequence, which is the shape sim/harness.py's unreachable
    `if m["pend"] is not None` fails at.
    """
    if ledger.pending(ref.uid) is not None:
        return Refusal("pending",
                       "a notification is already outstanding for this mandate")
    if ledger.attempts(ref.uid, cycle) >= CAP:
        return Refusal("cap", "attempt cap already reached for this cycle")
    if target_t % HOURS in PEAK:
        return Refusal("peak", f"target hour {target_t % HOURS:02d}:00 is a peak hour")
    if notify_t is not None and target_t - notify_t < HOURS:
        return Refusal("lead", f"{target_t - notify_t}h lead, need {HOURS}h")
    if notify_t is None and ledger.prev_code(ref.uid, cycle) != TECH:
        return Refusal("represent", "no fresh notification and prev decline was not TECH")
    return None
