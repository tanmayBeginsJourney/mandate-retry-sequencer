"""Route diagnosis to the LLM only when rules have no signal.

Production path (Option B-light): every STOP / ESCALATE / RETRY decision is
made by `RuleBasedDiagnoser`. The model is called only when `merchant_note`
is non-empty — the one field the rules cannot read.

Exception-facing copy uses `compose_outreach` separately (escalate only when
`compose_llm` is on). This class does not widen the action path.
"""
from __future__ import annotations

from agent.llm.fallback import RuleBasedDiagnoser
from agent.ports import CaseView, Diagnosis


def needs_llm_diagnosis(view: CaseView) -> bool:
    """True when unstructured merchant input must be read."""
    return bool((view.merchant_note or "").strip())


class MerchantNoteRoutedDiagnoser:
    """Rules by default; LLM overlay only on `merchant_note`."""

    def __init__(self, rules: RuleBasedDiagnoser, llm):
        self.rules = rules
        self.llm = llm
        self.stats = {"n_rule_only": 0, "n_routed": 0}

    @property
    def client(self):
        """Compose layer reads the transport from the inner model diagnoser."""
        return getattr(self.llm, "client", None)

    @property
    def prompt_id(self) -> str:
        return getattr(self.llm, "prompt_id", self.rules.prompt_id)

    def diagnose(self, view: CaseView) -> Diagnosis:
        if needs_llm_diagnosis(view):
            self.stats["n_routed"] += 1
            return self.llm.diagnose(view)
        self.stats["n_rule_only"] += 1
        return self.rules.diagnose(view)
