"""Regression tests for the previous broken diagnoser branches.

    python agent/tests/test_fallback_safety.py

These must fail on the pre-fix RuleBasedDiagnoser: unknown outcomes and
terminal codes could fall through to RETRY.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from agent.llm.fallback import RuleBasedDiagnoser  # noqa: E402
from agent.llm.prompts import DIAGNOSER_SYSTEM  # noqa: E402
from agent.ports import (CaseView, InterventionKind, RootCause,  # noqa: E402
                         TERMINAL_CODES)


def _view(hist, **kw) -> CaseView:
    d = dict(case_hash="t", attempts_used=1, attempts_cap=4, day_in_cycle=10,
             days_left_in_cycle=20, amount=550.0, decline_history=tuple(hist),
             n_recent_z9=hist.count("Z9"), peer_mandate_success_recent=False,
             uncertainty_band="medium")
    d.update(kw)
    return CaseView(**d)


def main() -> int:
    d = RuleBasedDiagnoser()
    failed = []

    def check(name, cond, detail=""):
        print(f"  {'ok' if cond else 'FAIL'}  {name}"
              + (f"  {detail}" if detail else ""))
        if not cond:
            failed.append(name)

    x = d.diagnose(_view(["deemed_transaction"]))
    check("indeterminate last code is STOP, not RETRY",
          x.intervention is InterventionKind.STOP
          and x.root_cause is RootCause.OUTCOME_UNKNOWN,
          x.intervention.value)

    x = d.diagnose(_view(["duplicate_rrn_found"]))
    check("duplicate_rrn_found is STOP",
          x.intervention is InterventionKind.STOP)

    x = d.diagnose(_view(["Z9", "deemed_transaction"],
                         peer_mandate_success_recent=True,
                         uncertainty_band="narrow"))
    check("peer-success does not override an unknown outcome",
          x.intervention is InterventionKind.STOP)

    for code in sorted(TERMINAL_CODES):
        x = d.diagnose(_view([code]))
        check(f"terminal {code} is not RETRY",
              x.intervention is not InterventionKind.RETRY,
              x.intervention.value)

    x = d.diagnose(_view(["ZX", "TECH"]))
    check("terminal anywhere in history is not RETRY",
          x.intervention is not InterventionKind.RETRY,
              x.intervention.value)

    x = d.diagnose(_view(["funds_blocked_by_mandate"]))
    check("lien is not RETRY",
          x.intervention is not InterventionKind.RETRY,
              x.intervention.value)

    x = d.diagnose(_view(["Z9"]))
    check("ordinary Z9 can still NUDGE or RETRY",
          x.intervention in (InterventionKind.NUDGE, InterventionKind.RETRY),
          x.intervention.value)

    banned = "money reached the account"
    check("diagnoser prompt does not coach that phrase",
          banned not in DIAGNOSER_SYSTEM.lower(),
          "phrase still present" if banned in DIAGNOSER_SYSTEM.lower() else "")

    if failed:
        print(f"\n{len(failed)} FAILED")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
