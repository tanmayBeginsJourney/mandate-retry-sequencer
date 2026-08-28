"""Deterministic diagnosers. THE DEFAULT PATH, and the one the gated number
comes from.

The batch result must never depend on a network call -- a headline number that
needs an API key is not reproducible, and docs/CLAUDE.md's numbers rule would
forbid quoting it. So the LLM is an overlay measured against this, not a
dependency of this. Build the failure path first, then the happy path
(docs/04_BUILD_PLAN.md, Day 5).

TWO DIAGNOSERS:

`RetryOnlyDiagnoser` is DEGENERATE MODE. It returns RETRY, always. It exists so
the parity test can isolate the policy from the agent: with retry-only and no
LLM, the agent should reproduce `harness.run("solo_shared_pd", ...)`. Every
point of difference between degenerate and full mode is then attributable to
the agent's action space rather than to the timing brain, which is the number
the track actually asks for.

`RuleBasedDiagnoser` is the real fallback. It is also what the LLM is scored
against in the eval harness: an LLM that cannot beat thirty lines of if-else
is not worth its latency, and finding that out is the "AI Judgment" criterion
doing its job rather than being asserted at.

NEITHER MAY RAISE. `ports.Diagnoser` says so. An LLM failure is an event in
the audit log, not an exception in the recovery loop.
"""
from __future__ import annotations

import hashlib

from agent.ports import CaseView, Diagnosis, InterventionKind, RootCause

PROMPT_ID_RETRY_ONLY = "det-retry-only-v1"
PROMPT_ID_RULES = "det-rules-v1"


def _did(prompt_id: str, view: CaseView) -> str:
    return hashlib.sha256(
        f"{prompt_id}|{view.case_hash}".encode()).hexdigest()[:16]


class RetryOnlyDiagnoser:
    """Degenerate mode. The agent reduced to the frozen policy."""

    prompt_id = PROMPT_ID_RETRY_ONLY

    def diagnose(self, view: CaseView) -> Diagnosis:
        return Diagnosis(
            diagnosis_id=_did(self.prompt_id, view),
            root_cause=RootCause.INSUFFICIENT_FUNDS,
            intervention=InterventionKind.RETRY,
            confidence=1.0,
            rationale="Degenerate mode: retry whenever the timing model "
                      "schedules an attempt.",
            source="fallback",
            prompt_id=self.prompt_id,
        )


class RuleBasedDiagnoser:
    """The real deterministic fallback.

    Every branch below is a claim about what the agent should do, and every one
    of them is measured in the action ablation rather than asserted. If a
    branch turns out to be worth nothing, it gets cut -- that is what the
    ablation is for.
    """

    prompt_id = PROMPT_ID_RULES

    def __init__(self, *, allow_nudge: bool = True, allow_escalate: bool = True,
                 allow_stop: bool = True):
        # The ablation switches. Each action can be turned off independently so
        # its contribution is measurable in isolation.
        self.allow_nudge = allow_nudge
        self.allow_escalate = allow_escalate
        self.allow_stop = allow_stop

    def diagnose(self, view: CaseView) -> Diagnosis:
        cause, action, conf, why = self._decide(view)
        recs: tuple[str, ...] = ()
        if cause is RootCause.INSUFFICIENT_FUNDS and view.n_recent_z9 >= 2:
            # PARTIAL is a RECOMMENDATION only. It credits zero money and never
            # reaches the gate: whether a partial debit is permitted under one
            # UPI AutoPay mandate is not established in docs/01_FACTS.md, and a
            # merchant-acceptance rate for it would be an invented constant.
            recs = ("PARTIAL",)
        return Diagnosis(
            diagnosis_id=_did(self.prompt_id, view),
            root_cause=cause, intervention=action, confidence=conf,
            rationale=why, source="fallback", prompt_id=self.prompt_id,
            recommendations=recs)

    def _decide(self, view: CaseView):
        last = view.decline_history[-1] if view.decline_history else None
        left = view.attempts_cap - view.attempts_used

        if last == "TECH":
            return (RootCause.TECHNICAL, InterventionKind.RETRY, 0.9,
                    "Last decline was technical, not a funding problem. "
                    "Re-presenting under the existing authorisation.")

        if left <= 1 and view.n_recent_z9 >= 2 and view.days_left_in_cycle > 3:
            if self.allow_stop:
                return (RootCause.MANDATE_AT_RISK, InterventionKind.STOP, 0.7,
                        "One attempt from the regulatory cap after repeated "
                        "declines. Holding it back protects the mandate for "
                        "future billing cycles.")

        if view.peer_mandate_success_recent and view.uncertainty_band != "wide":
            return (RootCause.TIMING_MISMATCH, InterventionKind.RETRY, 0.8,
                    "Another authorisation on this account cleared recently "
                    "and our model scores this window highly.")

        if view.n_recent_z9 >= 3 and view.days_left_in_cycle <= 3:
            if self.allow_escalate:
                return (RootCause.INSUFFICIENT_FUNDS, InterventionKind.ESCALATE,
                        0.6, "Repeated declines with the cycle closing. "
                             "Referring to the merchant for manual handling.")

        if view.n_recent_z9 >= 1 and self.allow_nudge and view.days_left_in_cycle > 2:
            return (RootCause.INSUFFICIENT_FUNDS, InterventionKind.NUDGE, 0.6,
                    "Recent decline suggests the account was not funded at the "
                    "time of the request. Prompting the customer before the "
                    "next scheduled attempt.")

        return (RootCause.INSUFFICIENT_FUNDS, InterventionKind.RETRY, 0.7,
                "Proceeding with the attempt our timing model scores highest "
                "in the remaining window.")
