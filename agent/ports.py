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


# ------------------------------------------------------- the richer taxonomy
# Added 29 August 2026. `sim/w3.py` is frozen and its vocabulary is the three
# symbols above, which is all the belief filter needs -- it reasons about WHEN
# money is there and nothing else. It is not enough to name the families NPCI
# publishes, and that gap is precisely what a narrative layer is for:
#
#   a frozen account   -> STOP FOREVER. No retry ever helps.
#   a broken mandate   -> no retry helps either; the merchant must re-authorise.
#   a limit hit        -> the money IS there. A SMALLER debit works.
#   insufficient funds -> wait for money. This is the only one w3 models.
#   a technical decline-> the rail glitched. Try again; it costs an attempt.
#
# `w3.index_score` has no slot for "this account will never succeed again", so
# a frozen account looks to it like a very unlucky customer and it will spend
# attempts until the cap kills the mandate. A structural blind spot, not an
# unlearned parameter -- the same shape as the rail-outage argument.
#
# THE MEMBER CODES ARE [VERIFIED] against NPCI "UPI Error and Response Codes"
# v2.9 section 3.1, read via `agent/eval/golden_cases.yaml`'s research block.
# HOW OFTEN each family occurs is [GUESS]: no source found gives AutoPay
# -specific decline frequencies. The mix is SWEPT, never picked. See
# `agent/execution/declines.py`.
#
# This table lives in ports.py because `agent/llm` must be able to read it and
# rule I2 forbids `agent/llm` importing `agent.execution`. ports.py imports
# nothing, so it is the only lawful home for shared vocabulary.
FAMILY_OK = "OK"
FAMILY_FUNDS = "FUNDS"                    # Z9
FAMILY_TECH = "TECH"                      # TECH
FAMILY_ACCOUNT_SHUT = "ACCOUNT_SHUT"      # ZX, YE
FAMILY_MANDATE_BROKEN = "MANDATE_BROKEN"  # VD, VI, VF
FAMILY_LIMIT = "LIMIT"                    # Z8, IE
FAMILY_AMBIGUOUS = "AMBIGUOUS"            # U30 -- names nothing

#: Drawn uniformly within a family: nothing found ranks the members against
#: each other, and inventing a within-family split would stack a second [GUESS]
#: on the first for no gain.
FAMILY_CODES: dict[str, tuple[str, ...]] = {
    FAMILY_OK: ("OK",),
    FAMILY_FUNDS: ("Z9",),
    FAMILY_TECH: ("TECH",),
    FAMILY_ACCOUNT_SHUT: ("ZX", "YE"),
    FAMILY_MANDATE_BROKEN: ("VD", "VI", "VF"),
    FAMILY_LIMIT: ("Z8", "IE"),
    FAMILY_AMBIGUOUS: ("U30",),
}

CODE_FAMILY: dict[str, str] = {c: fam for fam, cs in FAMILY_CODES.items()
                               for c in cs}

#: The debit failed and NO retry can ever help. The only correct response is to
#: stop and hand the mandate back to the merchant.
TERMINAL_CODES = frozenset(FAMILY_CODES[FAMILY_ACCOUNT_SHUT]
                           + FAMILY_CODES[FAMILY_MANDATE_BROKEN])

#: The money exists; this particular request was refused for being too large or
#: too frequent. A smaller debit is the right answer -- and PARTIAL is still a
#: recommendation only, because its legality under one mandate is unestablished.
LIMIT_CODES = frozenset(FAMILY_CODES[FAMILY_LIMIT])

#: May be re-presented under the SAME pre-debit notification. Only a technical
#: decline may; every business decline needs a fresh one. docs/01_FACTS.md.
REPRESENTABLE_CODES = frozenset({TECH})


def family_of(code: str) -> str:
    """Family for a response code. Unknown codes are AMBIGUOUS, never guessed
    into a family -- a code we cannot name is exactly the U30 situation."""
    return CODE_FAMILY.get(code, FAMILY_AMBIGUOUS)


# ------------------------------------------------------------------- banks
# Added 29 August 2026. Lives here, not in `agent/execution/`, because gate I2
# forbids anything outside `constraints/stage0.py` importing `agent.execution`
# and the sweep needs these. `bank_of` is a pure function of a customer index
# and `BANK_HANDLES` is a table of strings: neither is execution, both are
# vocabulary, and ports.py imports nothing.
#
# `N_BANKS` and the UNIFORM assignment are [GUESS]. Real Indian UPI share is
# heavily skewed and nothing found gives per-bank AutoPay MANDATE share, so a
# skew we invented would be a constant with no source (rule 5). Uniform makes a
# single-bank outage cover about an eighth of customers; a realistic skew would
# make the largest bank's incident bigger and the smallest bank's smaller, so
# every single-bank number is the middle of a range nobody has measured.
N_BANKS = 8

#: Handles a merchant would already recognise: in real UPI the payer's VPA
#: carries the bank on its face (`@oksbi`, `@ybl`), so the remitter bank is
#: something the merchant can already see on their own transaction report. That
#: is why it is allowed across the redaction boundary -- `agent/llm/caseview.py`
#: has the argument, and `agent/llm/governance.py` still forbids NAMING it in
#: merchant-facing prose.
BANK_HANDLES = ("@oksbi", "@ybl", "@okhdfcbank", "@okicici", "@okaxis",
                "@paytm", "@ibl", "@upi")


def bank_of(customer_id: int, n_banks: int = N_BANKS) -> str:
    """Which bank holds this customer's account.

    Derived from a stable hash of the customer index rather than from any RNG,
    so it is identical across every run, seed and process and consumes nothing
    from the money path's stream. A bank assignment that moved with the seed
    would make a bank-scoped outage unreproducible."""
    import hashlib
    h = hashlib.blake2b(str(customer_id).encode(), digest_size=8).digest()
    return BANK_HANDLES[int.from_bytes(h, "big") % min(n_banks,
                                                       len(BANK_HANDLES))]


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
    # --- added 29 Aug 2026 with the richer decline taxonomy above. Purely
    # additive: every value the golden cases already use is unchanged.
    ACCOUNT_UNAVAILABLE = "ACCOUNT_UNAVAILABLE"   # frozen/dormant. STOP FOREVER.
    MANDATE_INVALID = "MANDATE_INVALID"           # revoked/expired/paused. Re-authorise.
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"             # money is there. Debit smaller.
    RAIL_OUTAGE = "RAIL_OUTAGE"                   # the rail, not this customer.
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
    bank: str = ""                          # remitter bank handle. See caseview.py.

    @property
    def decline_families(self) -> tuple[str, ...]:
        """`decline_history` mapped through `family_of`. Convenience only --
        the codes themselves are what a merchant sees on their report."""
        return tuple(family_of(c) for c in self.decline_history)

    @property
    def has_terminal_code(self) -> bool:
        """Did any attempt come back with a code no retry can ever fix?"""
        return any(c in TERMINAL_CODES for c in self.decline_history)


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
