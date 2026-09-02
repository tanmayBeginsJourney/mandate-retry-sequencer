"""The durable entities and the two state machines that govern them.

WHAT IS DELIBERATELY *NOT* HERE: most of Razorpay's object model. A payment
entity carries thirty-odd fields; this package stores the ones that change a
decision, close a reconciliation, or let a human find the transaction on
Razorpay's dashboard. Copying the rest would make every provider schema change
a migration here for no gain.

THE ORDERING RULE IS THE WHOLE POINT OF THIS FILE. Razorpay delivers webhooks
at least once and does not guarantee order ([VERIFIED], razorpay.com webhook
best practices, read 3 September 2026). So a state machine that simply assigns
whatever the newest message says will happily move a captured payment back to
`authorized` because a retry of an older event arrived late. Every transition
here goes through `advance`, which compares ranks and refuses to go backwards.

CONFLICTING TERMINALS ARE RECORDED, NOT RESOLVED. If a payment reports both
`captured` and `failed`, one of the two is wrong and this package cannot tell
which. It keeps the first terminal state, marks the attempt `conflicted`, and
leaves it for a human. Picking a winner by arrival time would be inventing an
answer to a question the provider did not answer.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from agent.ports import code_for_reason, is_pending


# ============================================================ mandate states
class MandateState(str, Enum):
    """Where a mandate is in its life. Only ACTIVE may be charged."""

    #: Registration started. An order exists, or an authorisation payment is in
    #: flight. NO TOKEN IS USABLE YET.
    PENDING = "PENDING"
    #: The provider says `recurring_details.status == "confirmed"`. This is the
    #: ONLY state in which a debit may be submitted.
    ACTIVE = "ACTIVE"
    #: The customer or their bank declined the mandate at authorisation.
    REJECTED = "REJECTED"
    #: Revoked after the fact, by the customer, the bank, or us.
    CANCELLED = "CANCELLED"
    #: Paused by the customer. UPI-only. May return to ACTIVE.
    PAUSED = "PAUSED"


#: Razorpay `token.recurring_details.status` -> ours. The five source values
#: are [VERIFIED] against the recurring-payments token entity documentation,
#: read 3 September 2026: initiated, confirmed, rejected, cancelled, paused.
#:
#: An unknown value maps to nothing and is handled by the caller rather than
#: defaulted to PENDING. Defaulting would turn a provider vocabulary change
#: into a mandate that quietly stops being chargeable with no error anywhere.
TOKEN_STATUS_STATE: dict[str, MandateState] = {
    "initiated": MandateState.PENDING,
    "confirmed": MandateState.ACTIVE,
    "rejected": MandateState.REJECTED,
    "cancelled": MandateState.CANCELLED,
    "paused": MandateState.PAUSED,
}

#: PAUSED is not terminal: the customer can resume a paused UPI mandate, so a
#: later `token.confirmed` legitimately moves it back to ACTIVE.
MANDATE_TERMINAL = frozenset({MandateState.REJECTED, MandateState.CANCELLED})


# ============================================================ attempt states
class AttemptState(str, Enum):
    """One debit's progress. The ranks below define what may follow what."""

    #: Written to disk BEFORE anything leaves this process. A row in this state
    #: after a restart means we do not know whether the provider heard us.
    INTENT = "INTENT"
    #: The Razorpay order exists. No payment has been submitted against it.
    ORDER_CREATED = "ORDER_CREATED"
    #: `order.notification.delivered`. The customer got the pre-debit notice.
    NOTIFIED = "NOTIFIED"
    #: `POST /payments/create/recurring` returned a payment id. THE PROVIDER
    #: HAS THE REQUEST. It has not told us the outcome.
    SUBMITTED = "SUBMITTED"
    #: `payment.authorized`. Money is committed but not yet captured.
    AUTHORIZED = "AUTHORIZED"
    #: `payment.captured`. Collected.
    SUCCEEDED = "SUCCEEDED"
    #: `payment.failed`, or an order-level refusal before any payment existed.
    FAILED = "FAILED"
    #: We cannot tell what happened -- a lost response, a deemed transaction.
    #: NOT a failure: the debit may have landed. Never retried automatically.
    UNKNOWN = "UNKNOWN"


#: Monotonic progress. `advance` refuses any move to a lower or equal rank, so
#: a redelivered webhook is a no-op and a late one cannot undo a newer fact.
#:
#: UNKNOWN sits above SUBMITTED and below the terminals on purpose. It is
#: reached from a lost response, and it must still be *resolvable* by the
#: authoritative payment state arriving later.
_RANK: dict[AttemptState, int] = {
    AttemptState.INTENT: 0,
    AttemptState.ORDER_CREATED: 1,
    AttemptState.NOTIFIED: 2,
    AttemptState.SUBMITTED: 3,
    AttemptState.UNKNOWN: 4,
    AttemptState.AUTHORIZED: 5,
    AttemptState.SUCCEEDED: 6,
    AttemptState.FAILED: 6,
}

ATTEMPT_TERMINAL = frozenset({AttemptState.SUCCEEDED, AttemptState.FAILED})

#: States in which the local record does not know the provider's final answer,
#: so a reconciliation poll is worth making. Defined as "everything that is not
#: terminal" rather than as a hand-written list: a state left out of a
#: hand-written list is one that would never be resolved and never noticed,
#: which is exactly what happened to `AUTHORIZED` until gate S3a said so.
#:
#: `INTENT` is in here because a crash between writing the intent and creating
#: the order leaves precisely that row. `AUTHORIZED` is in here because a
#: payment can be authorised and never captured.
ATTEMPT_UNRESOLVED = frozenset(set(AttemptState) - ATTEMPT_TERMINAL)


class Transition(str, Enum):
    """What `advance` did. The caller logs this; it does not guess."""
    APPLIED = "APPLIED"
    #: The event carried a state we already have or have passed. Duplicate
    #: delivery and out-of-order delivery both land here, which is why the
    #: webhook path can be careless about neither.
    IGNORED_STALE = "IGNORED_STALE"
    #: Two different terminal states for one payment. Kept, not resolved.
    CONFLICT = "CONFLICT"


def advance(current: AttemptState, proposed: AttemptState) -> Transition:
    """May `current` become `proposed`? Pure, so the tests can enumerate it."""
    if current is proposed:
        return Transition.IGNORED_STALE
    if current in ATTEMPT_TERMINAL:
        # A terminal state is final. A different terminal is a contradiction.
        return (Transition.CONFLICT if proposed in ATTEMPT_TERMINAL
                else Transition.IGNORED_STALE)
    if _RANK[proposed] <= _RANK[current]:
        return Transition.IGNORED_STALE
    return Transition.APPLIED


def advance_mandate(current: MandateState, proposed: MandateState) -> Transition:
    """Mandate transitions. Not a rank: this machine is not monotonic.

    An active mandate can be paused and resume; a cancelled one cannot come
    back. So the rule is "terminal states are final", and everything else is
    the provider's current word on the matter.
    """
    if current is proposed:
        return Transition.IGNORED_STALE
    if current in MANDATE_TERMINAL:
        return Transition.IGNORED_STALE
    return Transition.APPLIED


# ================================================================= entities
@dataclass
class Customer:
    """Ours and theirs, joined. Nothing derives one id from the other."""
    id: str                       # internal, ours
    rzp_customer_id: str          # provider
    email: str
    contact: str
    name: str = ""
    #: Small integer identity. `agent.ports.MandateRef` is `(customer_id,
    #: mandate_index, merchant_id)` as INTEGERS, because the simulation
    #: numbers its population. A live customer has a string id, so one integer
    #: is assigned here and used for nothing else. Deriving it from the string
    #: -- hashing `cust_ABC123` into an int -- would collide silently and make
    #: two customers share one belief.
    seq: int = 0
    created_at: int = field(default_factory=lambda: int(time.time()))


@dataclass
class Mandate:
    """One UPI AutoPay authorisation.

    `max_amount_paise` is the ceiling the CUSTOMER agreed to at authorisation.
    It is stored because a debit above it fails at the provider, and refusing
    locally with a clear reason beats spending an NPCI attempt to learn it.
    """
    id: str                       # internal
    customer_id: str              # internal Customer.id
    state: MandateState = MandateState.PENDING
    rzp_token_id: str = ""
    rzp_customer_id: str = ""
    #: The registration order and the authorisation payment. Kept so a human
    #: can find this mandate on Razorpay's dashboard from our records alone.
    registration_order_id: str = ""
    registration_payment_id: str = ""
    #: Provider's own word, verbatim, for display and for debugging a mapping
    #: that has gone wrong. Never interpreted except through TOKEN_STATUS_STATE.
    token_status: str = ""
    max_amount_paise: int = 0
    charge_amount_paise: int = 0
    frequency: str = ""
    expire_at: int = 0
    #: Identity as `agent.ports.MandateRef` spells it. `index_no` counts this
    #: customer's mandates from zero; `merchant_id` is the merchant the
    #: subscription belongs to, which is 1 for a single-merchant integration
    #: and is stored rather than assumed so the field means something the day
    #: it is not.
    index_no: int = 0
    merchant_id: int = 1
    #: THE COLD START, AND IT IS AN ASSUMPTION, NOT A MEASUREMENT. The belief
    #: filter needs a starting salary and payday to have a prior at all, and a
    #: real integration has no oracle for either -- the merchant knows what the
    #: subscription costs, not what the customer earns or when. These are the
    #: operator's stated estimate, recorded so that a decision made on them can
    #: be read back and questioned. Nothing in this repository measures how
    #: wrong they are on real customers.
    est_salary: float = 0.0
    est_payday: int = 1
    #: Billing period, in days, and the hour the current cycle opened.
    cycle_days: int = 30
    cycle: int = 0
    cycle_start_t: int = 0
    created_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))

    @property
    def chargeable(self) -> bool:
        return self.state is MandateState.ACTIVE and bool(self.rzp_token_id)

    def refusal_reason(self) -> str:
        """Why this mandate cannot be charged, or "" if it can."""
        if not self.rzp_token_id:
            return "no provider token: the mandate was never authorised"
        if self.state is not MandateState.ACTIVE:
            return (f"mandate state is {self.state.value}; only "
                    f"{MandateState.ACTIVE.value} may be charged")
        if self.expire_at and self.expire_at <= int(time.time()):
            return "mandate token has passed its expiry"
        return ""


@dataclass
class PaymentAttempt:
    """One debit, from intent to authoritative outcome.

    `id` IS Stage 0's `action_id`. Not a copy of it and not derived from it --
    the same string. That is what lets the audit trail, the provider's order
    receipt and this row all be joined without a mapping table, and it is why a
    crashed-and-restarted attempt re-derives the same identity instead of
    minting a second one.
    """
    id: str                       # == Stage 0 action_id
    mandate_id: str
    #: `agent.ports.MandateRef.uid` -- "c3m0". The audit trail and the
    #: executor's journal both key on this, and the store keys on
    #: `mandate_id`, so the row carries both rather than making every join
    #: reconstruct one from the other.
    mandate_uid: str
    amount_paise: int
    state: AttemptState = AttemptState.INTENT
    #: Provider identifiers, filled in as they become known.
    order_id: str = ""
    payment_id: str = ""
    #: Deterministic, and the provider treats it as an idempotency key: a
    #: second order create with the same receipt is rejected. [VERIFIED]
    #: razorpay.com Create an Order, read 3 September 2026.
    receipt: str = ""
    #: Our vocabulary (ports.FAMILY_CODES), set once an outcome is known.
    outcome_code: str = ""
    #: Razorpay's `error_reason`, verbatim. Audit and debugging only.
    raw_reason: str = ""
    #: Simulated hour the scheduler chose, and the wall-clock second it maps to.
    #: Both are kept because Stage 0 reasons in hours and Razorpay reasons in
    #: epoch seconds, and a single field cannot be both.
    target_t: int = 0
    payment_after: int = 0
    submitted_at: int = 0
    resolved_at: int = 0
    #: Set when two terminal states arrive for one payment. Needs a human.
    conflicted: bool = False
    cycle: int = 0
    created_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))

    @property
    def resolved(self) -> bool:
        return self.state in ATTEMPT_TERMINAL

    @property
    def succeeded(self) -> bool:
        return self.state is AttemptState.SUCCEEDED


@dataclass
class WebhookEvent:
    """One delivery. The row exists whether or not we could make sense of it.

    A rejected signature is STILL PERSISTED, with `signature_valid=0`. Dropping
    it would leave no trace of an attempt to forge an event, which is the one
    case where the log matters most.
    """
    event_id: str                 # x-razorpay-event-id. The dedup key.
    event_type: str
    received_at: int
    signature_valid: bool
    payload: str                  # raw body, verbatim
    processed_at: int = 0
    #: What ingestion decided. Free text for display, not a state machine.
    result: str = ""
    mandate_id: str = ""
    attempt_id: str = ""


# ===================================================== provider -> our state
#: Razorpay payment `status` -> our attempt state. Five values are documented
#: for the payment entity: created, authorized, captured, refunded, failed.
#: [VERIFIED] razorpay.com Fetch a Payment, read 3 September 2026.
#:
#: `refunded` is NOT mapped. A refund is a later event on a payment that was
#: captured, so treating it as an attempt state would regress a collected
#: cycle. Refunds are out of scope for this package and are left to reporting.
PAYMENT_STATUS_STATE: dict[str, AttemptState] = {
    "created": AttemptState.SUBMITTED,
    "authorized": AttemptState.AUTHORIZED,
    "captured": AttemptState.SUCCEEDED,
    "failed": AttemptState.FAILED,
}


@dataclass(frozen=True)
class ProviderPaymentView:
    """What one provider payment object says, in our vocabulary.

    Built by `from_payment_entity` so that the webhook path and the polling
    path cannot disagree about how to read the same object.
    """
    state: AttemptState
    outcome_code: str
    raw_reason: str
    payment_id: str
    order_id: str
    amount_paise: int


def from_payment_entity(entity: dict) -> ProviderPaymentView:
    """Read a Razorpay payment entity into our terms.

    A payment that reports `failed` with a reason our map calls INDETERMINATE
    -- a deemed transaction, a duplicate RRN -- is NOT recorded as FAILED. The
    provider is saying it does not know, and `AttemptState.FAILED` is a claim
    that it does. Rounding an unknown down to a failure is the reading that
    licenses a retry, and the retry is what double-charges the customer.
    """
    status = str(entity.get("status") or "")
    reason = str(entity.get("error_reason") or "")
    state = PAYMENT_STATUS_STATE.get(status, AttemptState.SUBMITTED)
    if reason and is_pending(reason):
        state = AttemptState.UNKNOWN
    return ProviderPaymentView(
        state=state,
        outcome_code=(code_for_reason(reason) if reason
                      else ("OK" if state is AttemptState.SUCCEEDED else "")),
        raw_reason=reason,
        payment_id=str(entity.get("id") or ""),
        order_id=str(entity.get("order_id") or ""),
        amount_paise=int(entity.get("amount") or 0),
    )
