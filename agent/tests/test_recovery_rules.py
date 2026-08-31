"""Product rules for reminders vs last-attempt backup checkout.

    python agent/tests/test_recovery_rules.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from agent.llm.compose import template_body  # noqa: E402
from agent.ports import (CaseView, Diagnosis, InterventionKind,  # noqa: E402
                         RootCause)
from agent.recovery import (  # noqa: E402
    batch_legal_ceiling, diagnoser_stop_yields_to_backup, escalate_halts_cycle,
    fourth_debit_blocked, should_issue_backup_after_fail,
    should_remind_after_fail)


def main() -> int:
    failed = []

    def check(name, cond):
        print(f"  {'ok' if cond else 'FAIL'}  {name}")
        if not cond:
            failed.append(name)

    check("remind after 1st Z9", should_remind_after_fail(1, "Z9"))
    check("remind after 2nd Z9", should_remind_after_fail(2, "Z9"))
    check("no remind after 3rd Z9", not should_remind_after_fail(3, "Z9"))
    check("no remind on TECH", not should_remind_after_fail(1, "TECH"))
    check("backup after 3rd Z9", should_issue_backup_after_fail(3, "Z9"))
    check("no backup after 2nd", not should_issue_backup_after_fail(2, "Z9"))
    check("no backup on VI", not should_issue_backup_after_fail(3, "VI"))
    check("issued blocks 4th", fourth_debit_blocked("issued"))
    check("expired blocks 4th", fourth_debit_blocked("expired"))
    check("cancelled blocks 4th", fourth_debit_blocked("cancelled"))
    check("paid blocks 4th (no double charge)",
          fourth_debit_blocked("paid"))
    check("empty does not block", not fourth_debit_blocked(""))
    check("STOP at 3rd Z9 yields to backup",
          diagnoser_stop_yields_to_backup(3, "Z9"))
    check("STOP at 2nd Z9 does not yield",
          not diagnoser_stop_yields_to_backup(2, "Z9"))
    view = CaseView(
        case_hash="t", attempts_used=3, attempts_cap=4, day_in_cycle=20,
        days_left_in_cycle=10, amount=550.0, decline_history=("Z9", "Z9", "Z9"),
        n_recent_z9=3, peer_mandate_success_recent=False,
        uncertainty_band="medium")
    diag = Diagnosis(
        diagnosis_id="t", root_cause=RootCause.INSUFFICIENT_FUNDS,
        intervention=InterventionKind.NUDGE, confidence=0.6,
        rationale="Funds decline.", source="test", prompt_id="test")
    backup_copy = template_body(view, diag, purpose="backup_link").lower()
    check("backup copy says debit is paused", "paused" in backup_copy)
    check("backup copy says no double charge", "twice" in backup_copy)
    remind_copy = template_body(view, diag, purpose="reminder").lower()
    check("reminder copy has no pay-now link",
          "link" not in remind_copy and "http" not in remind_copy)
    check("VI escalate halts", escalate_halts_cycle("VI"))
    check("YE escalate halts", escalate_halts_cycle("YE"))
    check("lien escalate halts",
          escalate_halts_cycle("funds_blocked_by_mandate"))
    check("Z9 escalate does not halt", not escalate_halts_cycle("Z9"))
    check("120d 100x5 ceiling is 8000 not 2000",
          batch_legal_ceiling(500, 120, 30) == 500 * 4 * 4)
    check("one cycle is n*4",
          batch_legal_ceiling(8, 30, 30) == 8 * 4)

    if failed:
        print("FAILED:", failed)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
