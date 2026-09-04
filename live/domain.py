"""The durable entities and the two state machines that govern them.

WHAT IS DELIBERATELY NOT HERE: most of Razorpay's object model. A payment
entity carries thirty-odd fields; this stores the ones that change a decision,
close a reconciliation, or let a human find the transaction on the dashboard.

THE ORDERING RULE IS THE POINT OF THIS FILE. Razorpay delivers at least once
and does not guarantee order ([VERIFIED] razorpay.com webhook best practices,
read 3 September 2026), so a machine that assigns whatever the newest message
says will move a captured payment back to `authorized` when a retry of an
older event arrives late. Every transition goes through `advance`, which
compares ranks and refuses to go backwards.

CONFLICTING TERMINALS ARE RECORDED, NOT RESOLVED. If a payment reports both
`captured` and `failed`, one is wrong and this package cannot tell which. It
keeps the first, marks the attempt `conflicted`, and leaves it for a human.
Picking a winner by arrival time would invent an answer the provider did not
give.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from agent.ports import code_for_reason, is_pending


# ============================================================ mandate states
class MandateState(str, Enum):
    """Where a mandate is in its life. Only ACTIVE may be charged."""

    #: Registration started. NO TOKEN IS USABLE YET.
    PENDING = "PENDING"
    #: `recurring_details.status == "confirmed"`. The ONLY chargeable state.
    ACTIVE = "ACTIVE"
    #: Declined at authorisation, by the customer or their bank.
    REJECTED = "REJECTED"
    #: Revoked after the fact, by the customer, the bank, or us.
    CANCELLED = "CANCELLED"
    #: Paused. UPI-only, and NOT chargeable. A mandate the CUSTOMER paused can
    #: be resumed only by the customer, from their UPI app -- "you cannot
    #: resume a subscription paused by your customer", [VERIFIED] razorpay.com
    #: pause/resume, read 4 September 2026. So this service never moves a
    #: mandate out of PAUSED; it returns to ACTIVE only when the provider says
    #: the token is confirmed again.
    PAUSED = "PAUSED"


#: Razorpay `token.recurring_details.status` -> ours. The five source values
#: are [VERIFIED] against the recurring-payments token entity documentation,
#: read 3 September 2026.
#:
#: An unknown value maps to nothing and is handled by the caller rather than
#: defaulted, because defaulting would turn a provider vocabulary change into a
#: mandate that quietly stops being chargeable with no error anywhere.
TOKEN_STATUS_STATE: dict[str, MandateState] = {
    "initiated": MandateState.PENDING,
    "confirmed": MandateState.ACTIVE,
    "rejected": MandateState.REJECTED,
    "cancelled": MandateState.CANCELLED,
    "paused": MandateState.PAUSED,
}

#: PAUSED is not terminal: the customer can resume from their UPI app, and the
#: `token.confirmed` that follows legitimately moves it back to ACTIVE. Nothing
#: in this service can cause that transition.
MANDATE_TERMINAL = frozenset({MandateState.REJECTED, MandateState.CANCELLED})


# ============================================================ attempt states
class AttemptState(str, Enum):
    """One debit's progress. The ranks below define what may follow what."""

    #: On disk BEFORE anything leaves this process. Nothing has been sent, so
    #: a row still in this state after a restart has consumed no NPCI attempt.
    INTENT = "INTENT"
    #: The Razorpay order exists. No payment submitted against it.
    ORDER_CREATED = "ORDER_CREATED"
    #: `order.notification.delivered`. The customer got the pre-debit notice.
    NOTIFIED = "NOTIFIED"
    #: `order.notification.failed`. The customer did NOT get the notice, so
    #: this attempt may never be charged. Terminal: a notice that failed
    #: cannot be un-failed, and a fresh one needs a fresh order.
    NOTIFICATION_FAILED = "NOTIFICATION_FAILED"
    #: WRITTEN BEFORE THE DEBIT REQUEST LEAVES THE PROCESS, and the state that
    #: makes crash recovery possible. A row found here after a restart means a
    #: debit MAY be at the provider and we never saw the answer. It is never
    #: resubmitted; `reconcile` asks the order what it holds.
    SUBMITTING = "SUBMITTING"
    #: `POST /payments/create/recurring` returned a payment id. THE PROVIDER
    #: HAS THE REQUEST. It has not told us the outcome.
    SUBMITTED = "SUBMITTED"
    #: `payment.authorized`. Committed but not captured.
    AUTHORIZED = "AUTHORIZED"
    #: `payment.captured`. Collected.
    SUCCEEDED = "SUCCEEDED"
    #: `payment.failed`, or an order-level refusal before any payment existed.
    FAILED = "FAILED"
    #: We cannot tell what happened -- a lost response, a deemed transaction,
    #: a request the provider refused after the order may already have been
    #: paid. NOT a failure: the debit may have landed. Never auto-retried.
    UNKNOWN = "UNKNOWN"


#: Monotonic progress. `advance` refuses any move to a lower or equal rank, so
#: a redelivered webhook is a no-op and a late one cannot undo a newer fact.
#:
#: UNKNOWN sits above SUBMITTED and below the terminals on purpose: it is
#: reached from a lost response and must still be RESOLVABLE by the
#: authoritative payment state arriving later.
#:
#: NOTIFICATION_FAILED is terminal and therefore ranks with the terminals. A
#: `.delivered` arriving afterwards is refused as stale rather than reopening
#: the attempt: see `agent/execution/razorpay_predelivery.py` for why the
#: contradiction settles on the reading that does not debit.
_RANK: dict[AttemptState, int] = {
    AttemptState.INTENT: 0,
    AttemptState.ORDER_CREATED: 1,
    AttemptState.NOTIFIED: 2,
    AttemptState.SUBMITTING: 3,
    AttemptState.SUBMITTED: 4,
    AttemptState.UNKNOWN: 5,
    AttemptState.AUTHORIZED: 6,
    AttemptState.SUCCEEDED: 7,
    AttemptState.FAILED: 7,
    AttemptState.NOTIFICATION_FAILED: 7,
}

ATTEMPT_TERMINAL = frozenset({AttemptState.SUCCEEDED, AttemptState.FAILED,
                              AttemptState.NOTIFICATION_FAILED})

#: A debit request has left, or may have left, this process. These are the
#: states that have spent one of NPCI's four presentations; everything below
#: SUBMITTING has spent none. One definition, so the scheduler's count and the
#: ledger rebuilt at restart cannot disagree about what an attempt cost.
ATTEMPT_PRESENTED = frozenset({AttemptState.SUBMITTING, AttemptState.SUBMITTED,
                               AttemptState.UNKNOWN, AttemptState.AUTHORIZED,
                               AttemptState.SUCCEEDED, AttemptState.FAILED})

#: Where the local record does not know the provider's final answer, so a
#: reconciliation poll is worth making. Defined as the complement of the
#: terminals rather than as a hand-written list: a state left out of a
#: hand-written list would never be resolved and never noticed.
ATTEMPT_UNRESOLVED = frozenset(set(AttemptState) - ATTEMPT_TERMINAL)


class Transition(str, Enum):
    """What `advance` did. The caller logs this; it does not guess."""
    APPLIED = "APPLIED"
    #: A state we already have or have passed. Duplicate delivery and
    #: out-of-order delivery both land here.
    IGNORED_STALE = "IGNORED_STALE"
    #: Two different terminal states for one payment. Kept, not resolved.
    CONFLICT = "CONFLICT"


def advance(current: AttemptState, proposed: AttemptState) -> Transition:
    """May `current` become `proposed`? Pure, so the tests can enumerate it."""
    if current is proposed:
        return Transition.IGNORED_STALE
    if current in ATTEMPT_TERMINAL:
        return (Transition.CONFLICT if proposed in ATTEMPT_TERMINAL
                else Transition.IGNORED_STALE)
    if _RANK[proposed] <= _RANK[current]:
        return Transition.IGNORED_STALE
    return Transition.APPLIED


def advance_mandate(current: MandateState, proposed: MandateState) -> Transition:
    """Mandate transitions. Not a rank: this machine is not monotonic.

    An active mandate can pause and resume; a cancelled one cannot come back.
    So the rule is that terminal states are final and everything else is the
    provider's current word on the matter.
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
    #: `agent.ports.MandateRef` is three INTEGERS, because the simulation
    #: numbers its population. A live customer has a string id, so one integer
    #: is assigned here and used for nothing else. Hashing `cust_ABC123` into
    #: an int would collide silently and make two customers share one belief.
    seq: int = 0
    created_at: int = field(default_factory=lambda: int(time.time()))


@dataclass
class Mandate:
    """One UPI AutoPay authorisation.

    `max_amount_paise` is the ceiling the CUSTOMER agreed to. It is stored
    because a debit above it fails at the provider, and refusing locally with a
    clear reason beats spending an NPCI attempt to learn that.
    """
    id: str                       # internal
    customer_id: str              # internal Customer.id
    state: MandateState = MandateState.PENDING
    rzp_token_id: str = ""
    rzp_customer_id: str = ""
    #: Kept so a human can find this mandate on Razorpay's dashboard from our
    #: records alone.
    registration_order_id: str = ""
    registration_payment_id: str = ""
    #: The provider's own word, verbatim, for display and for debugging a
    #: mapping gone wrong. Interpreted only through TOKEN_STATUS_STATE.
    token_status: str = ""
    max_amount_paise: int = 0
    charge_amount_paise: int = 0
    frequency: str = ""
    expire_at: int = 0
    #: Identity as `MandateRef` spells it. `index_no` counts this customer's
    #: mandates from zero; `merchant_id` is stored rather than assumed so the
    #: field means something the day the integration is not single-merchant.
    index_no: int = 0
    merchant_id: int = 1
    #: THE COLD START, AND IT IS AN ASSUMPTION, NOT A MEASUREMENT. The belief
    #: filter needs a starting salary and payday to have a prior at all, and a
    #: real integration has no oracle for either. These are the operator's
    #: stated estimate, recorded so a decision made on them can be questioned.
    #: Nothing here measures how wrong they are on real customers.
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

    `id` IS Stage 0's `action_id` -- the same string, not a copy. That is what
    lets the audit trail, the provider's order receipt and this row be joined
    without a mapping table, and why a crashed-and-restarted attempt re-derives
    the same identity instead of minting a second one.
    """
    id: str                       # == Stage 0 action_id
    mandate_id: str
    #: `MandateRef.uid` -- "c3m0". The audit trail and the executor journal key
    #: on this; the store keys on `mandate_id`. The row carries both rather
    #: than making every join reconstruct one from the other.
    mandate_uid: str
    amount_paise: int
    state: AttemptState = AttemptState.INTENT
    order_id: str = ""
    payment_id: str = ""
    #: Deterministic, and the provider's idempotency key: a second order create
    #: with the same receipt is rejected. [VERIFIED] razorpay.com Create an
    #: Order ("has to be unique", max 40 characters), read 3 September 2026.
    receipt: str = ""
    #: Our vocabulary (ports.FAMILY_CODES), set once an outcome is known.
    outcome_code: str = ""
    #: Razorpay's `error_reason`, verbatim. Audit and debugging only.
    raw_reason: str = ""
    #: The simulated hour the scheduler chose, and the wall-clock second it
    #: maps to. Stage 0 reasons in hours, Razorpay in epoch seconds, and a
    #: single field cannot be both.
    target_t: int = 0
    #: THE HOUR THE SCHEDULER ACTUALLY NOTIFIED AT, stored rather than derived.
    #: `target_t - 24` is not it: the peak-hour rule pushes a target past the
    #: first legal slot, so the two differ whenever the notice falls in a peak
    #: window. Deriving it made the same attempt mean one thing in memory and
    #: another after a restart, and Stage 0's `pending` rule read the
    #: difference as a second concurrent notification.
    notify_t: int = 0
    payment_after: int = 0
    submitted_at: int = 0
    resolved_at: int = 0
    #: Two terminal states arrived for one payment. Needs a human.
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
    """One AUTHENTIC delivery. Every row here passed signature verification.

    A DELIVERY THAT FAILED VERIFICATION IS NOT ONE OF THESE. It is recorded in
    `rejected_deliveries` instead, and the reason is not tidiness: `event_id`
    is the deduplication key, so a forged delivery quoting a real event id
    would otherwise CLAIM that id and make Razorpay's genuine retry of the same
    event look like a duplicate -- dropping a real payment outcome on the floor
    at the request of whoever sent the forgery. Forensic visibility is kept;
    the dedup key is not surrendered to an unauthenticated sender.
    """
    event_id: str                 # x-razorpay-event-id. The dedup key.
    event_type: str
    received_at: int
    payload: str                  # raw body, verbatim
    processed_at: int = 0
    #: What ingestion decided. Free text for display, not a state machine.
    result: str = ""
    mandate_id: str = ""
    attempt_id: str = ""


# ===================================================== provider -> our state
#: Razorpay payment `status` -> our attempt state. Five values are documented:
#: created, authorized, captured, refunded, failed. [VERIFIED] razorpay.com
#: payment entity, read 3 September 2026.
#:
#: `refunded` is NOT mapped. A refund is a later event on a payment that was
#: captured, so treating it as an attempt state would regress a collected
#: cycle. Refunds are out of scope here and are left to reporting.
PAYMENT_STATUS_STATE: dict[str, AttemptState] = {
    "created": AttemptState.SUBMITTED,
    "authorized": AttemptState.AUTHORIZED,
    "captured": AttemptState.SUCCEEDED,
    "failed": AttemptState.FAILED,
}


@dataclass(frozen=True)
class ProviderPaymentView:
    """What one provider payment object says, in our vocabulary.

    Built by `from_payment_entity` so the webhook path and the polling path
    cannot disagree about how to read the same object.
    """
    state: AttemptState
    outcome_code: str
    raw_reason: str
    payment_id: str
    order_id: str
    amount_paise: int


def from_payment_entity(entity: dict) -> ProviderPaymentView:
    """Read a Razorpay payment entity into our terms.

    A payment reporting `failed` with a reason our map calls INDETERMINATE --
    a deemed transaction, a duplicate RRN -- is NOT recorded as FAILED. The
    provider is saying it does not know, and FAILED is a claim that it does.
    Rounding an unknown down to a failure is the reading that licenses a retry,
    and the retry is what double-charges the customer.

    A STATUS THIS MAP DOES NOT KNOW BECOMES `UNKNOWN`, NOT `SUBMITTED`. It used
    to default to SUBMITTED, which asserts the debit is in flight and nothing
    more is needed -- so a vocabulary Razorpay adds, or a `refunded` arriving
    on the polling path, would read as a healthy in-flight debit and stop being
    looked at. UNKNOWN says what is true: the payment exists and we cannot say
    what it did. It stays on the reconciliation queue and is never retried.
    """
    status = str(entity.get("status") or "")
    reason = str(entity.get("error_reason") or "")
    state = PAYMENT_STATUS_STATE.get(status, AttemptState.UNKNOWN)
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
