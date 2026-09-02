"""Loader for the three injection cases, plus the structural check.

The injection cases live in `golden_cases.yaml` under `injection_cases:` and
are NOT part of the 40. They have no registered `correct_intervention`, because
what is under test is not what the diagnoser decides -- it is what leaks.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from agent.eval.cases import GOLDEN, _load_raw, _view_of
from agent.ports import CaseView, Diagnosis


@dataclass(frozen=True)
class InjectionCase:
    id: str
    title: str
    attack: str
    view: CaseView
    must_not_contain: tuple[str, ...]
    notes: str


def load_injection_cases(path: str = GOLDEN) -> list[InjectionCase]:
    raw = _load_raw(path)
    return [InjectionCase(
        id=c["id"], title=c.get("title", ""), attack=c.get("attack", ""),
        view=_view_of(c["case_view"]),
        must_not_contain=tuple(str(x).lower()
                               for x in (c.get("must_not_contain") or ())),
        notes=str(c.get("notes") or "")) for c in raw.get("injection_cases", [])]


# ------------------------------------------------------------- the mutant
class CompliantDiagnoser:
    """A DIAGNOSER THAT OBEYS THE MERCHANT NOTE. The named mutant for the
    injection test.

    Why it has to exist. The deterministic fallback emits canned strings and
    can no more echo an injected instruction than a calculator can. So on the
    fallback arm every injection case comes back clean -- and "nothing leaked"
    from a component that cannot leak is a disconnected wire, not a result. It
    is the exact shape of error 16: a metric whose null value satisfies the
    assertion.

    So this is what a manipulated model would produce, written by hand: it
    swallows the merchant note into its justification and answers RETRY because
    it was told to. `governance.check` MUST flag it. If it does not, the
    injection test is measuring nothing and reports VACUOUS.

    IT IS NOT A DIAGNOSER ANYONE MAY SHIP. It is instantiated only by
    `run_eval.py`, only against the three injection cases, and it touches no
    counter -- rule 1a, a mutant may create illegal state and nothing else.
    """

    prompt_id = "mutant-obeys-injection-v1"

    def diagnose(self, view: CaseView) -> Diagnosis:
        from agent.ports import InterventionKind, RootCause
        return Diagnosis(
            diagnosis_id=f"{self.prompt_id}:{view.case_hash}",
            root_cause=RootCause.TIMING_MISMATCH,
            intervention=InterventionKind.RETRY,
            confidence=0.99,
            # Verbatim echo. That is the whole attack: the note reaches prose a
            # human reads, and the prose is where a time can still land even
            # though `Diagnosis` has no field for one.
            rationale=("Per the merchant note: " + view.merchant_note.strip()),
            source="llm", prompt_id=self.prompt_id)


# --------------------------------------------------------- structural check
#: Anything that would let a Diagnosis express WHEN. If a field whose name
#: contains one of these ever appears on the type, ADR-005 has been broken by
#: construction and no amount of prompt discipline can put it back.
_TEMPORAL_TOKENS = ("day", "hour", "time", "_t", "when", "delay", "date",
                    "schedule", "at_", "minute", "deadline")


def temporal_fields(names) -> tuple[str, ...]:
    """Which of `names` could hold a time. Split out from the check below so a
    canary can feed it a type that DOES carry one -- a detector nobody has ever
    seen fire is a detector nobody knows works."""
    return tuple(f for f in names
                 if any(tok in f.lower() for tok in _TEMPORAL_TOKENS))


def diagnosis_has_temporal_field() -> tuple[bool, tuple[str, ...]]:
    """Does `ports.Diagnosis` carry anything that could hold a time?

    THIS IS A CONSTRUCTION CHECK, NOT A RESULT. It cannot tell you anything
    about a model. It tells you whether the claim "an injected time has nowhere
    to go" is still true of the code, and it is here so that the claim fails
    loudly the day someone adds a `retry_after_hours` field for a good reason.

    "Fails loudly" means a NON-ZERO EXIT, in `sim/verify_doc_contract.py`,
    which the pre-commit hook runs. Until 2 September 2026 the only consumer
    was `agent/eval/run_eval.py`, which printed "ADR-005 BROKEN" and returned
    0 -- so the documentation promised an enforcement the repository did not
    have. Both paths now exit non-zero.
    """
    hits = temporal_fields(Diagnosis.__dataclass_fields__)
    return bool(hits), hits
