"""The shared vocabulary. Every layer speaks these types; none of them owns one.

THIS MODULE IMPORTS NOTHING FROM `agent`. That is deliberate and is checked by
`agent/tests/test_layer_isolation.py`. If ports.py could import a layer, the
layers could reach each other through it, and the whole isolation argument
would be decoration.

Two things in here are load-bearing for the architecture, not conveniences:

1. `Diagnosis` HAS NO TEMPORAL FIELD. No day, no hour, no target time, no
   delay. The LLM layer's only output type physically cannot express when to
   debit somebody. That is ADR-005 -- "an LLM must never be on the path that
   decides whether to debit a specific customer at a specific moment" --
   enforced by construction rather than by reviewer discipline. A prompt
   injection that says "retry at 11am" has nowhere to put the 11am.

2. Money is carried two ways on purpose. `Rupees` (float) is what the frozen
   belief filter is fed, so our probabilities stay bit-identical to sim/.
   `Paise` (int) is what the audit log stores, so sums are exact and SQL can
   add them up without float drift. Convert at the boundary, never sum a
   Rupees for a report.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Protocol, Sequence

Rupees = float          # fed to w3.BeliefPD. Never summed for a report.
Paise = int             # stored in the audit log. Exact.


def to_paise(r: Rupees) -> Paise:
    """Round-half-up to the paisa. One conversion point, so drift has one home."""
    return int(round(r * 100))


# --------------------------------------------------------------- decline codes
# These mirror w3.OK / w3.Z9 / w3.TECH. They are re-declared as plain strings
# rather than imported so that `agent.ports` stays dependency-free; the values
# are asserted equal to w3's in test_layer_isolation.py.
OK = "OK"
Z9 = "Z9"           # insufficient funds. Needs a FRESH notification to retry.
TECH = "TECH"       # technical decline. May auto-represent under the old one.


# ---------------------------------------------------------------------- time
@dataclass(frozen=True, order=True)
class Clock:
    """Absolute hour index, exactly as sim/harness.py counts time."""
    t: int

    @property
    def day(self) -> int:
        return self.t // 24

    @property
    def hour(self) -> int:
        return self.t % 24


# ------------------------------------------------------------------ identity
@dataclass(frozen=True, order=True)
class MandateRef:
    customer_id: int
    mandate_index: int
    merchant_id: int

    @property
    def uid(self) -> str:
        """Stable string key. Used as the audit log's mandate_uid."""
        return f"c{self.customer_id}m{self.mandate_index}"


# ------------------------------------------------------------------- actions
class InterventionKind(Enum):
    """What the agent can decide to do about a mandate.

    PARTIAL is deliberately absent. Whether a partial debit is permitted under
    one UPI AutoPay mandate is not established in docs/01_FACTS.md, and a
    merchant-acceptance rate for it would be an invented constant (rule 5).
    It survives as a RECOMMENDATION only -- see `Recommendation` below -- which
    credits zero money and never reaches the gate.
    """
    RETRY = "RETRY"           # money action: attempt a debit
    WAIT = "WAIT"             # no action today; re-decide tomorrow
    NUDGE = "NUDGE"           # non-money: ask the customer to fund the account
    ESCALATE = "ESCALATE"     # non-money: hand to a human / merchant queue
    STOP = "STOP"             # no further money action this cycle


MONEY_ACTIONS = frozenset({InterventionKind.RETRY})


class RootCause(Enum):
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    TIMING_MISMATCH = "TIMING_MISMATCH"     # money exists, we asked on the wrong day
    TECHNICAL = "TECHNICAL"
    MANDATE_AT_RISK = "MANDATE_AT_RISK"     # one attempt from death
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PendingNotification:
    """A pre-debit notification that has been issued and not yet consumed.

    `notify_t` is None for a re-presentation under a previous notification,
    which is legal ONLY after a technical decline.
    """
    notify_t: int | None
    target_t: int
    under_previous_notice: bool


@dataclass(frozen=True)
class MoneyAction:
    """A request to move money. The ONLY thing Stage 0 adjudicates."""
    action_id: str
    ref: MandateRef
    amount: Rupees
    cycle: int
    target_t: int
    notify_t: int | None
    decided_at_t: int
    kind: InterventionKind = InterventionKind.RETRY
    # what the policy layer thought, carried for the audit trail only
    p_now: float = 0.0
    p_later: float = 0.0
    index_score: float = 0.0
    diagnosis_id: str = ""


@dataclass(frozen=True)
class AttemptOutcome:
    t: int
    code: str               # OK / Z9 / TECH
    success: bool


@dataclass(frozen=True)
class Refusal:
    rule: str               # cap | peak | lead | pending | represent
    detail: str


@dataclass(frozen=True)
class Allowed:
    outcome: AttemptOutcome


@dataclass(frozen=True)
class Refused:
    refusal: Refusal


Decision = Allowed | Refused


# ------------------------------------------------------------- policy output
@dataclass(frozen=True)
class ScheduleProposal:
    """The timing layer's answer. Only the timing layer may construct one."""
    target_day: int
    target_t: int
    notify_t: int
    p_now: float
    p_later: float
    index_score: float


@dataclass(frozen=True)
class TimingSummary:
    """Belief summary for the POLICY layer. Carries rupee-denominated state."""
    expected_balance: Rupees
    payday_entropy: float
    top_hypothesis_weight: float


@dataclass(frozen=True)
class PaydayUncertainty:
    """Belief summary for the LLM layer. Carries NO rupee-denominated state.

    This is the redaction seam. `TimingSummary` has an expected balance in it;
    this does not, and this is the only one `agent/llm` is allowed to see. A
    narrative layer cannot leak a balance it was never handed.
    """
    payday_entropy: float
    top_hypothesis_weight: float

    @property
    def band(self) -> Literal["narrow", "medium", "wide"]:
        """Coarse label. The LLM sees this, not the float."""
        if self.top_hypothesis_weight >= 0.60:
            return "narrow"
        if self.top_hypothesis_weight >= 0.25:
            return "medium"
        return "wide"


# ---------------------------------------------------------------- LLM layer
@dataclass(frozen=True)
class CaseView:
    """The ONLY thing `agent/llm` ever sees about a mandate.

    Everything here is either a count, a coarse band, or the mandate's own
    contracted amount -- which the merchant already knows, because it is their
    own subscription price. There is no balance, no salary, no p_success, no
    payday, and no posterior.
    """
    case_hash: str
    attempts_used: int
    attempts_cap: int
    day_in_cycle: int
    days_left_in_cycle: int
    amount: Rupees                  # the merchant's own price. Not customer state.
    decline_history: tuple[str, ...]        # e.g. ("Z9", "Z9", "TECH")
    n_recent_z9: int
    peer_mandate_success_recent: bool       # did another merchant just succeed?
    uncertainty_band: str                   # narrow | medium | wide
    merchant_note: str = ""                 # UNTRUSTED free text from merchant metadata


@dataclass(frozen=True)
class Diagnosis:
    """LLM (or fallback) output.

    NOTE WHAT IS ABSENT: no day, no hour, no target_t, no delay, no
    "retry_at". The type cannot express a time. See the module docstring.
    """
    diagnosis_id: str
    root_cause: RootCause
    intervention: InterventionKind
    confidence: float
    rationale: str                          # merchant-facing. Governance-checked.
    source: Literal["llm", "fallback"]
    prompt_id: str = ""
    recommendations: tuple[str, ...] = ()   # e.g. "PARTIAL" -- credits zero money


# ------------------------------------------------------------------ stopping
class StopRule(Enum):
    COLLECTED = "COLLECTED"
    CAP_REACHED = "CAP_REACHED"
    CYCLE_CLOSED = "CYCLE_CLOSED"
    NO_LEGAL_SLOT = "NO_LEGAL_SLOT"
    MANDATE_DEAD = "MANDATE_DEAD"
    ESCALATED = "ESCALATED"
    AGENT_STOP = "AGENT_STOP"       # the diagnosis layer chose STOP
    RUN_BUDGET = "RUN_BUDGET"


# ------------------------------------------------------------------- ports
class Executor(Protocol):
    """The world. Only `agent.constraints.stage0.Stage0Gate` may hold one."""

    def attempt(self, ref: MandateRef, amount: Rupees, t: int) -> AttemptOutcome:
        ...


class Diagnoser(Protocol):
    """MUST NOT RAISE. Ever. An LLM failure is an event, not an exception."""

    def diagnose(self, view: CaseView) -> Diagnosis:
        ...
