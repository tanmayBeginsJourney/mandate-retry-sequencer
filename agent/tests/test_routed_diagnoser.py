"""Merchant-note routing: LLM only when rules have no signal.

    python agent/tests/test_routed_diagnoser.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from agent.llm.caseview import build_case_view  # noqa: E402
from agent.llm.fallback import RuleBasedDiagnoser  # noqa: E402
from agent.llm.routed_diagnoser import (  # noqa: E402
    MerchantNoteRoutedDiagnoser, needs_llm_diagnosis)
from agent.ports import (CaseView, Diagnosis, InterventionKind, PaydayUncertainty,
                         RootCause)  # noqa: E402


class _FakeLlm:
    def __init__(self):
        self.calls = 0

    def diagnose(self, view):
        self.calls += 1
        return Diagnosis(
            diagnosis_id="llm", root_cause=RootCause.UNKNOWN,
            intervention=InterventionKind.STOP, confidence=0.5,
            rationale="from llm", source="llm", prompt_id="test")


def _view(note: str = "") -> CaseView:
    unc = PaydayUncertainty(payday_entropy=0.0, top_hypothesis_weight=0.5)
    return build_case_view(
        amount=500.0, attempts_used=1, attempts_cap=4, day=10,
        cycle_open=0, cycle_close=30, decline_history=("Z9",),
        peer_success_recent=False, uncertainty=unc,
        merchant_note=note)


def main() -> int:
    failed = []

    def check(name, cond):
        print(f"  {'ok' if cond else 'FAIL'}  {name}")
        if not cond:
            failed.append(name)

    check("empty note does not need llm",
          not needs_llm_diagnosis(_view("")))
    check("non-empty note needs llm",
          needs_llm_diagnosis(_view("customer says payday moved")))

    rules = RuleBasedDiagnoser()
    fake = _FakeLlm()
    routed = MerchantNoteRoutedDiagnoser(rules, fake)

    d0 = routed.diagnose(_view(""))
    check("empty note uses rules", d0.source == "fallback" and fake.calls == 0)
    check("stats count rule-only", routed.stats["n_rule_only"] == 1)

    d1 = routed.diagnose(_view("salary on the 7th now"))
    check("note uses llm", d1.source == "llm" and fake.calls == 1)
    check("stats count routed", routed.stats["n_routed"] == 1)

    if failed:
        print("FAILED:", failed)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
