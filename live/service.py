"""The live composition root. It wires; it does not decide.

`agent/batch.py` is the same module for the simulation. Gate L1 in
`agent/tests/test_layer_isolation.py` keeps this the only module in `live/`
that may hold an executor.

Shared with the simulation by identity, not by resemblance -- `BeliefBook`,
`timing.propose`, `RuleBasedDiagnoser`, `Stage0Gate`, `AuditLog`. The executor
and durable state are the only differences. `live/tests/test_parity.py`
asserts that with `is`.

THE ORDER ON THE MONEY PATH, AND WHY IT IS THAT ORDER:

    1. timing.propose            picks the hour
    2. Diagnoser                 picks the intervention
    3. store.put_attempt         the intent is durable
    4. Stage0Gate.issue_notification / .submit
    5. RazorpayExecutor          submits
    6. store.put_attempt         the acknowledgement
    7. webhook or reconcile      the authoritative outcome
    8. BeliefBook.record_outcome the belief

Step 1 precedes step 2 and cannot be reordered: `Diagnosis` has no field for a
time. Step 3 precedes step 5 so a crash between them leaves a row saying "we
may have asked" rather than nothing. Step 8 happens at 7, not at 5: a
submission is not an outcome, and reading it as one records an accepted debit
as an empty account.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field

import agent  # noqa: F401  -- puts sim/ on the path
from agent.audit.log import AuditLog, EventKind
from agent.constraints.rules import AttemptLedger
from agent.constraints.stage0 import Stage0Gate, action_id
# I2-EXEMPT / L1: this module is the live composition root. It is the only
# module in `live/` permitted to construct an executor, exactly as
# `agent/batch.py` is the only one in `agent/`.
from agent.execution.razorpay_api import (MIN_AMOUNT_PAISE, Outcome,
                                          RazorpayApi, Transport,
                                          first_item, parse_payment_id,
                                          parse_token_from_payment,
                                          parse_token_status)
from agent.execution.razorpay_executor import (MandateBinding, PredeliveryJournal,
                                               RazorpayError, RazorpayExecutor)
from agent.execution.razorpay_mock import MockRazorpayApi, sign as mock_sign
from agent.execution.razorpay_predelivery import PredeliveryOrder, PredeliveryPhase
from agent.llm.fallback import RuleBasedDiagnoser
from agent.policy import timing
from agent.policy.belief_book import BeliefBook
from agent.ports import (CaseView, InterventionKind, MandateRef, MoneyAction,
                         PendingNotification, Refused, family_of)
from live.config import LiveConfig, Mode
from live.domain import (ATTEMPT_UNRESOLVED, AttemptState, Customer, Mandate,
                         MandateState, PaymentAttempt, TOKEN_STATUS_STATE,
                         Transition, advance, advance_mandate,
                         from_payment_entity)
from live.store import Store
from live import webhooks

#: The database key holding the clock origin. See `LiveService.now_t`.
EPOCH_ORIGIN_KEY = "epoch_origin"

#: One presentation plus three retries. `agent/constraints/rules.py` enforces
#: it; this is named here only so the scheduler can be told how many are left.
CAP = 4

#: NPCI's pre-debit notice. Stage 0's `lead` rule enforces it; this is here so
#: the execute tick can reconstruct the notify time of an attempt it resumes.
LEAD_HOURS = 24


class LiveError(RuntimeError):
    """A request cannot be served. Carries a message safe to show an operator."""


@dataclass
class Decision:
    """What one tick did. Everything the console shows about a decision."""
    mandate_id: str
    at: int
    acted: bool
    reason: str
    intervention: str = ""
    root_cause: str = ""
    rationale: str = ""
    diagnosis_source: str = ""
    target_t: int = 0
    notify_t: int = 0
    p_now: float = 0.0
    p_later: float = 0.0
    index_score: float = 0.0
    attempt_id: str = ""
    diagnosis_id: str = ""
    gate_verdict: str = ""
    refused_rule: str = ""
    outcome_code: str = ""
    #: Carried beside `outcome_code` because the code for "not yet told" is the
    #: INDETERMINATE family's canonical member, and showing an operator
    #: `deemed_transaction` for a plain submission reads as a fault.
    outcome_raw: str = ""
    attempt_state: str = ""
    provider: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


#: The executor's pre-debit phase and the durable attempt state are the same
#: fact in two vocabularies: `PredeliveryOrder` is the executor port, shared
#: with the batch root, and `AttemptState` is what survives a restart. This is
#: the only place they are translated.
#:
#: EVERY ATTEMPT STATE MUST APPEAR HERE. A state left out would default to
#: ORDER_CREATED, and `RazorpayExecutor.attempt` accepts ORDER_CREATED -- so a
#: row already SUBMITTED would be resubmitted after a restart. That is the
#: executor's own last-line precondition, and a partial table disarms it.
_PHASE_STATE: dict[PredeliveryPhase, AttemptState] = {
    PredeliveryPhase.ORDER_CREATED: AttemptState.ORDER_CREATED,
    PredeliveryPhase.NOTIFICATION_DELIVERED: AttemptState.NOTIFIED,
    PredeliveryPhase.DEBIT_ATTEMPTED: AttemptState.SUBMITTED,
}
_STATE_PHASE: dict[AttemptState, PredeliveryPhase] = {
    AttemptState.INTENT: PredeliveryPhase.NONE,
    AttemptState.ORDER_CREATED: PredeliveryPhase.ORDER_CREATED,
    AttemptState.NOTIFIED: PredeliveryPhase.NOTIFICATION_DELIVERED,
    AttemptState.SUBMITTED: PredeliveryPhase.DEBIT_ATTEMPTED,
    AttemptState.UNKNOWN: PredeliveryPhase.DEBIT_ATTEMPTED,
    AttemptState.AUTHORIZED: PredeliveryPhase.DEBIT_ATTEMPTED,
    AttemptState.SUCCEEDED: PredeliveryPhase.DEBIT_ATTEMPTED,
    AttemptState.FAILED: PredeliveryPhase.DEBIT_ATTEMPTED,
}
assert set(_STATE_PHASE) == set(AttemptState)


class SqliteJournal(PredeliveryJournal):
    """The executor's pre-debit orders, read from and written to the attempts
    table.

    The base class keeps them in a dict, which is right for a batch run that
    begins and ends in one process. A service that forgets it created an order
    creates a second one; the provider refuses it on the receipt, which is safe
    but leaves the debit behind an unexpected rejection.

    NO IN-MEMORY COPY. The row is the only record, so a restart and a running
    process cannot disagree about what phase an order is in.
    """

    def __init__(self, store: Store):
        super().__init__()
        self._store = store

    def load(self, mandate_uid: str, target_t: int) -> PredeliveryOrder | None:
        row = self._store.attempt_for_target(mandate_uid, target_t)
        if row is None or not row.order_id:
            return None
        return PredeliveryOrder(
            mandate_uid=mandate_uid, target_t=target_t, order_id=row.order_id,
            amount_paise=row.amount_paise, payment_after=row.payment_after,
            phase=_STATE_PHASE[row.state])

    def save(self, rec: PredeliveryOrder) -> None:
        row = self._store.attempt_for_target(rec.mandate_uid, rec.target_t)
        if row is None:
            return
        changed = False
        if rec.order_id and row.order_id != rec.order_id:
            row.order_id, changed = rec.order_id, True
        if rec.payment_after and row.payment_after != rec.payment_after:
            row.payment_after, changed = rec.payment_after, True
        target = _PHASE_STATE.get(rec.phase)
        if target is not None and advance(row.state, target) is Transition.APPLIED:
            self._store.record_transition(
                "attempt", row.id, row.state.value, target.value,
                Transition.APPLIED.value, "provider", rec.phase.value)
            row.state, changed = target, True
        if changed:
            self._store.put_attempt(row)

    def all(self) -> list[PredeliveryOrder]:
        raise NotImplementedError(
            "the live journal is keyed by (mandate_uid, target_t) in SQLite; "
            "enumerate attempts through the store instead")


class LiveService:
    """One service, one database, one rail."""

    def __init__(self, config: LiveConfig, store: Store | None = None,
                 api=None, diagnoser=None, log_path: str | None = None):
        self.config = config
        self.store = store or Store(config.db_path)
        self.api = api if api is not None else self._build_api(config)
        self.started_at = int(time.time())

        # DECIDED ONCE PER DATABASE. The origin is what turns a simulated
        # `target_t` back into a wall-clock second; moving it between restarts
        # would silently redefine every hour already on disk.
        self.epoch_origin = int(self.store.meta_set_once(
            EPOCH_ORIGIN_KEY, str(self._midnight(self.started_at))))

        self.bindings: dict[str, MandateBinding] = {}
        self.executor = RazorpayExecutor(
            bindings=self.bindings, api=self.api,
            epoch_origin=self.epoch_origin,
            journal=SqliteJournal(self.store))
        self.ledger = AttemptLedger()
        self.log = AuditLog(
            log_path or os.path.join(os.path.dirname(self.store.path),
                                     f"audit-{self.started_at}.jsonl"),
            run_id=f"live-{self.started_at}")
        self.gate = Stage0Gate(self.executor, self.ledger, self.log)
        self.diagnoser = diagnoser or RuleBasedDiagnoser()
        self.book = BeliefBook(cycle_days=30, days=365, pop_spend=0.93)
        #: OFFLINE ONLY. Hours added to wall-clock time so a demonstration can
        #: watch a month of scheduling in a minute. See `advance_clock`.
        self.clock_offset_h = 0
        self._known_customers: set[int] = set()
        #: customer seq -> the last day its belief has been advanced to.
        self._advanced: dict[int, int] = {}
        self.decisions: list[Decision] = []
        #: One lock per mandate around the money path. Two concurrent ticks
        #: would both see no open attempt, both schedule, and both submit
        #: against one order. In-process only; across processes the attempt
        #: PRIMARY KEY, the receipt UNIQUE index and Razorpay's own
        #: one-payment-per-order rule are what bound it.
        self._mandate_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self.refresh()

    def _mandate_lock(self, mandate_id: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._mandate_locks.get(mandate_id)
            if lock is None:
                lock = self._mandate_locks[mandate_id] = threading.Lock()
            return lock

    # ------------------------------------------------------------- wiring
    @staticmethod
    def _build_api(config: LiveConfig):
        """Mock or real, decided by configuration and by nothing else.

        No fallback in either direction: `load()` has already refused a LIVE
        config without credentials.
        """
        if config.mode is Mode.OFFLINE:
            return MockRazorpayApi(seed=7)
        return RazorpayApi(Transport(config.key_id, config.key_secret),
                           config.api_base)

    @staticmethod
    def _midnight(ts: int) -> int:
        """Local midnight at or before `ts`.

        Anchoring hour 0 to midnight makes `target_t % 24` the real hour of the
        day, which is what the NPCI peak-window rule is about.
        """
        lt = time.localtime(ts)
        return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0,
                                lt.tm_wday, lt.tm_yday, lt.tm_isdst)))

    def now_t(self, now: int | None = None) -> int:
        """Wall clock -> simulated hour. The inverse of the executor's `_epoch`."""
        base = max(0, ((now or int(time.time())) - self.epoch_origin) // 3600)
        return base + self.clock_offset_h

    def advance_clock(self, hours: int) -> int:
        """Move the offline demonstration clock forward. Never backwards.

        REFUSED IN LIVE MODE. Stage 0's peak and lead rules read this clock, so
        a service that could be told the time could be told to debit outside
        the window the customer was notified for.
        """
        if self.config.is_live:
            raise LiveError(
                "the clock cannot be advanced in live mode: Stage 0's peak and "
                "lead rules read it, and a movable clock would let a debit run "
                "outside the window the customer was notified for")
        if hours <= 0:
            raise LiveError("the clock only moves forward")
        self.clock_offset_h += int(hours)
        return self.clock_offset_h

    def refresh(self) -> None:
        """Rebuild the bindings and the belief book from the store.

        The executor holds the SAME dict object throughout, so mutating it in
        place is what makes a newly-authorised mandate chargeable without
        rebuilding the gate.
        """
        self.bindings.clear()
        for m in self.store.mandates():
            c = self.store.customer(m.customer_id)
            if c is None:
                continue
            self.bindings[self._ref(m, c).uid] = MandateBinding(
                rzp_customer_id=m.rzp_customer_id or c.rzp_customer_id,
                rzp_token_id=m.rzp_token_id,
                rzp_email=c.email, rzp_contact=c.contact,
                charge_amount=m.charge_amount_paise / 100.0,
                est_salary=m.est_salary, est_payday=m.est_payday)
            if c.seq not in self._known_customers:
                n = len([x for x in self.store.mandates()
                         if x.customer_id == c.id])
                self.book.add_customer(c.seq, m.est_salary, m.est_payday,
                                       max(1, n))
                self._known_customers.add(c.seq)
        self._rehydrate_ledger()

    def _rehydrate_ledger(self) -> None:
        """REBUILD the gate's ledger from durable state. Not top it up.

        `AttemptLedger` is in memory and the two ticks of a debit can be a day
        apart, so a restart between them would leave the gate with no record of
        the outstanding notification and `check_pending` would refuse a debit
        Razorpay has already been told to expect.

        `open_cycle` RESETS THE COUNTER FIRST, and that is the correctness of
        it: `refresh()` runs on every mandate change, and replaying a log into
        a counter is idempotent only if the counter is zeroed. Without it one
        attempt reads as four after three refreshes -- the NPCI cap -- and the
        mandate silently stops being chargeable.
        """
        for m in self.store.mandates():
            c = self.store.customer(m.customer_id)
            if c is None:
                continue
            uid = self._ref(m, c).uid
            self.ledger.open_cycle(uid, m.cycle)
            for a in reversed(self.store.attempts_for(m.id, limit=50)):
                if a.cycle != m.cycle:
                    continue
                if a.state in (AttemptState.ORDER_CREATED,
                               AttemptState.NOTIFIED):
                    self.ledger.set_pending(uid, PendingNotification(
                        notify_t=a.target_t - LEAD_HOURS,
                        target_t=a.target_t, under_previous_notice=False))
                elif a.state is not AttemptState.INTENT:
                    # An INTENT row means nothing left the process, so it has
                    # consumed no NPCI attempt. Everything else has.
                    self.ledger.record_attempt(uid, a.cycle,
                                               a.outcome_code or "")

    @staticmethod
    def _ref(m: Mandate, c: Customer) -> MandateRef:
        return MandateRef(c.seq, m.index_no, m.merchant_id)

    # ------------------------------------------------------- registration
    def create_customer(self, *, name: str, email: str,
                        contact: str) -> Customer:
        r = self.api.create_customer(name=name, email=email, contact=contact,
                                     notes={"source": "recovery-agent"})
        if not r.ok:
            raise LiveError(f"customer create failed: {r.error_description or r.outcome.value}")
        c = Customer(id=f"cus_{uuid.uuid4().hex[:12]}",
                     rzp_customer_id=str(r.body.get("id") or ""),
                     email=email, contact=contact, name=name,
                     seq=self.store.next_customer_seq())
        self.store.put_customer(c)
        return c

    def start_registration(self, *, customer_id: str, charge_amount_paise: int,
                           max_amount_paise: int, frequency: str = "monthly",
                           est_salary: float = 0.0, est_payday: int = 1,
                           cycle_days: int = 30) -> Mandate:
        """Create the mandate row and the authorisation order.

        It stays PENDING until the provider says the token is `confirmed`. An
        order existing is not authorisation.
        """
        c = self.store.customer(customer_id)
        if c is None:
            raise LiveError(f"unknown customer {customer_id}")
        if charge_amount_paise < MIN_AMOUNT_PAISE:
            raise LiveError(f"charge amount must be at least "
                            f"{MIN_AMOUNT_PAISE} paise")
        if charge_amount_paise > max_amount_paise:
            raise LiveError("charge amount exceeds the mandate ceiling")

        m = Mandate(id=f"mdt_{uuid.uuid4().hex[:12]}", customer_id=c.id,
                    rzp_customer_id=c.rzp_customer_id,
                    max_amount_paise=max_amount_paise,
                    charge_amount_paise=charge_amount_paise,
                    frequency=frequency,
                    expire_at=int(time.time()) + 10 * 365 * 24 * 3600,
                    index_no=self.store.next_mandate_index(c.id),
                    est_salary=est_salary or charge_amount_paise / 100.0 * 60,
                    est_payday=est_payday, cycle_days=cycle_days,
                    cycle_start_t=self.now_t())
        receipt = f"reg_{m.id}"[:40]
        r = self.api.create_authorization_order(
            customer_id=c.rzp_customer_id,
            max_amount_paise=max_amount_paise, expire_at=m.expire_at,
            frequency=frequency, receipt=receipt,
            notes={"mandate_id": m.id})
        if not r.ok:
            raise LiveError(f"authorisation order failed: "
                            f"{r.error_description or r.outcome.value}")
        m.registration_order_id = str(r.body.get("id") or "")
        self.store.put_mandate(m)
        self.store.record_transition("mandate", m.id, "", m.state.value,
                                     Transition.APPLIED.value, "registration",
                                     "authorisation order created")
        self.refresh()
        return m

    def confirm_registration(self, mandate_id: str, payment_id: str) -> Mandate:
        """Read the authoritative token state after the customer authorised.

        A MANDATE IS NOT ACTIVE BECAUSE THIS CALL SUCCEEDED. It is active when
        `recurring_details.status` reads `confirmed` and on nothing else.
        """
        m = self.store.mandate(mandate_id)
        if m is None:
            raise LiveError(f"unknown mandate {mandate_id}")
        c = self.store.customer(m.customer_id)
        if c is None:
            raise LiveError("mandate has no customer")

        r = self.api.fetch_payment(payment_id)
        if not r.ok:
            raise LiveError(f"could not read the authorisation payment: "
                            f"{r.error_description or r.outcome.value}")
        token_id = parse_token_from_payment(r.body)
        if not token_id:
            raise LiveError("the authorisation payment carries no token yet; "
                            "the mandate may still be confirming")

        m.registration_payment_id = payment_id
        m.rzp_token_id = token_id
        status = ""
        tokens = self.api.fetch_customer_tokens(c.rzp_customer_id)
        if tokens.ok:
            for item in tokens.body.get("items") or []:
                if str(item.get("id") or "") == token_id:
                    status = parse_token_status(item)
                    if item.get("max_amount"):
                        m.max_amount_paise = int(item["max_amount"])
                    break
        state = TOKEN_STATUS_STATE.get(status)
        if state is None:
            raise LiveError(f"the provider reports token status {status!r}, "
                            f"which is not one of "
                            f"{sorted(TOKEN_STATUS_STATE)}; the mandate stays "
                            f"{m.state.value}")
        verdict = advance_mandate(m.state, state)
        self.store.record_transition("mandate", m.id, m.state.value,
                                     state.value, verdict.value,
                                     "registration", status)
        if verdict is Transition.APPLIED:
            m.state = state
        m.token_status = status
        self.store.put_mandate(m)
        self.refresh()
        return m

    def mock_authorize(self, mandate_id: str) -> Mandate:
        """Stand in for the customer approving the mandate in their UPI app.

        OFFLINE ONLY, AND IT RAISES IN LIVE MODE. No Razorpay endpoint
        authorises a mandate -- a human does it on a phone, through Checkout,
        which `scripts/razorpay_autopay_register.py` serves for real keys.
        """
        if self.config.is_live:
            raise LiveError(
                "authorisation cannot be simulated in live mode. A real "
                "customer must approve the mandate in their UPI app; run "
                "scripts/razorpay_autopay_register.py to serve that flow, "
                "then confirm with the payment id it returns.")
        m = self.store.mandate(mandate_id)
        if m is None:
            raise LiveError(f"unknown mandate {mandate_id}")
        if not m.registration_order_id:
            raise LiveError("the mandate has no authorisation order")
        authorize = getattr(self.api, "authorize", None)
        if authorize is None:
            raise LiveError("this rail cannot simulate an authorisation")
        r = authorize(m.registration_order_id)
        if not r.ok:
            raise LiveError(f"simulated authorisation failed: "
                            f"{r.error_description or r.outcome.value}")
        return self.confirm_registration(m.id, str(r.body.get("payment_id")))

    def cancel_mandate(self, mandate_id: str) -> Mandate:
        m = self.store.mandate(mandate_id)
        if m is None:
            raise LiveError(f"unknown mandate {mandate_id}")
        if not m.rzp_token_id:
            raise LiveError("the mandate has no provider token to cancel")
        r = self.api.delete_token(m.rzp_customer_id, m.rzp_token_id)
        if not r.ok:
            raise LiveError(f"cancel failed: "
                            f"{r.error_description or r.outcome.value}")
        verdict = advance_mandate(m.state, MandateState.CANCELLED)
        self.store.record_transition("mandate", m.id, m.state.value,
                                     MandateState.CANCELLED.value,
                                     verdict.value, "operator", "token deleted")
        if verdict is Transition.APPLIED:
            m.state = MandateState.CANCELLED
        m.token_status = "cancelled"
        self.store.put_mandate(m)
        self.refresh()
        return m

    # ------------------------------------------------------ the money path
    #
    # TWO TICKS, NOT ONE, AND THE REASON IS REGULATORY. NPCI requires the
    # customer to be notified at least 24 hours before an AutoPay debit, and
    # Razorpay issues that notice by way of an order carrying
    # `notification.payment_after`. So scheduling and charging cannot happen in
    # one call: the order is created now, the debit runs at the target hour,
    # and Stage 0's `lead` rule refuses anything closer together.
    #
    #   tick A  `_schedule`  scheduler -> diagnosis -> intent -> pre-debit order
    #   tick B  `_execute`   at the target hour -> Stage 0 -> the charge
    #
    # `decide` picks whichever is due, so a caller runs the same method on a
    # timer and the mandate advances one step each time.

    def decide(self, mandate_id: str, *, now: int | None = None) -> Decision:
        """One tick for one mandate. Schedules, charges, or explains why not.

        Serialised per mandate: the body reads state, decides on it and writes
        it back, and two threads interleaving there is how one debit becomes
        two requests against one order. See `_mandate_lock`.
        """
        with self._mandate_lock(mandate_id):
            return self._decide(mandate_id, now=now)

    def _decide(self, mandate_id: str, *, now: int | None = None) -> Decision:
        m = self.store.mandate(mandate_id)
        if m is None:
            raise LiveError(f"unknown mandate {mandate_id}")
        c = self.store.customer(m.customer_id)
        if c is None:
            raise LiveError("mandate has no customer")

        now_t = self.now_t(now)
        d = Decision(mandate_id=m.id, at=int(time.time()), acted=False,
                     reason="")

        blocked = self._blocked(m)
        if blocked:
            d.reason = blocked
            return self._record(d)

        # An attempt already scheduled for this cycle owns the decision. Either
        # its hour has come or it has not.
        scheduled = self._scheduled_attempt(m)
        if scheduled is not None:
            if scheduled.target_t > now_t:
                d.reason = (f"a debit is scheduled for hour "
                            f"{scheduled.target_t}; it is now hour {now_t}")
                d.attempt_id = scheduled.id
                d.target_t = scheduled.target_t
                d.attempt_state = scheduled.state.value
                return self._record(d)
            return self._execute(m, c, scheduled, now_t, d)

        # A debit is in flight or its outcome is unknown. Razorpay's guidance
        # is not to create another subsequent payment until the previous one's
        # status is known, and charging twice is the worst thing this can do.
        open_now = [a for a in self.store.attempts_for(m.id, limit=20)
                    if a.cycle == m.cycle and a.state in ATTEMPT_UNRESOLVED]
        if open_now:
            d.reason = (f"attempt {open_now[0].id} is {open_now[0].state.value}; "
                        f"the outcome of the previous debit must be known "
                        f"before another is submitted")
            d.attempt_id = open_now[0].id
            d.attempt_state = open_now[0].state.value
            return self._record(d)

        return self._schedule(m, c, now_t, d)

    def _blocked(self, m: Mandate) -> str:
        """Every reason this mandate may not be charged at all, in one place."""
        reason = m.refusal_reason()
        if reason:
            return reason
        allowed, why = self.config.may_debit()
        if not allowed:
            return why
        if m.charge_amount_paise > self.config.max_debit_paise:
            # OUR ceiling, not Razorpay's -- theirs is the mandate's max_amount
            # and they enforce it. This bounds what a bug in the amount path
            # can spend of a real balance.
            return (f"amount {m.charge_amount_paise} paise is above the "
                    f"configured ceiling of {self.config.max_debit_paise}")
        if m.max_amount_paise and m.charge_amount_paise > m.max_amount_paise:
            return (f"amount {m.charge_amount_paise} paise exceeds the "
                    f"mandate's authorised ceiling of {m.max_amount_paise}")
        return ""

    def _scheduled_attempt(self, m: Mandate) -> PaymentAttempt | None:
        """An attempt with a pre-debit order and no charge submitted yet."""
        for a in self.store.attempts_for(m.id, limit=20):
            if a.cycle == m.cycle and a.state in (AttemptState.ORDER_CREATED,
                                                  AttemptState.NOTIFIED):
                return a
        return None

    def _attempts_this_cycle(self, m: Mandate) -> int:
        return len([a for a in self.store.attempts_for(m.id, limit=20)
                    if a.cycle == m.cycle])

    # -------------------------------------------------------------- tick A
    def _schedule(self, m: Mandate, c: Customer, now_t: int,
                  d: Decision) -> Decision:
        ref = self._ref(m, c)
        attempts_used = self._attempts_this_cycle(m)
        if attempts_used >= CAP:
            d.reason = f"the NPCI attempt cap of {CAP} is spent for this cycle"
            return self._record(d)

        day = now_t // 24
        cycle_close = m.cycle_start_t + m.cycle_days * 24

        # ---- 1. THE DETERMINISTIC SCHEDULER. Nothing else picks a time.
        self._advance_to(c.seq, day)
        decision = timing.propose(
            self.book.belief_for(c.seq), m.charge_amount_paise / 100.0,
            day, now_t, cycle_close, attempts_used,
            kind=InterventionKind.RETRY, cycles_left=0)
        d.p_now, d.p_later = decision.p_now, decision.p_later
        d.index_score = decision.index_score
        if decision.proposal is None:
            d.reason = f"timing: {decision.reason}"
            return self._record(d)
        prop = decision.proposal
        d.target_t, d.notify_t = prop.target_t, prop.notify_t

        # ---- 2. THE DIAGNOSIS LAYER. It names a cause and picks an
        #         intervention. `Diagnosis` has no field for a time.
        view = self._case_view(m, c, attempts_used, day)
        diag = self.diagnoser.diagnose(view)
        d.intervention = diag.intervention.value
        d.root_cause = diag.root_cause.value
        d.rationale = diag.rationale
        d.diagnosis_source = diag.source
        d.diagnosis_id = diag.diagnosis_id
        self.log.emit(EventKind.DIAGNOSIS, now_t, mandate_uid=ref.uid,
                      cycle=m.cycle, diagnosis_id=diag.diagnosis_id,
                      root_cause=diag.root_cause.value,
                      intervention=diag.intervention.value,
                      source=diag.source)

        if diag.intervention is not InterventionKind.RETRY:
            d.reason = f"diagnosis chose {diag.intervention.value}"
            self.gate.record_non_money(ref, m.cycle, diag.intervention.value,
                                       now_t, diagnosis_id=diag.diagnosis_id)
            return self._record(d)

        # ---- 3. THE INTENT IS DURABLE BEFORE ANYTHING LEAVES THIS PROCESS.
        aid = action_id(self.log.run_id, ref, m.cycle, prop.target_t,
                        attempts_used + 1)
        attempt = PaymentAttempt(
            id=aid, mandate_id=m.id, mandate_uid=ref.uid,
            amount_paise=m.charge_amount_paise, state=AttemptState.INTENT,
            receipt=RazorpayExecutor.receipt_for(f"notify:{ref.uid}", ref,
                                                 prop.target_t),
            target_t=prop.target_t,
            payment_after=self.epoch_origin + prop.target_t * 3600,
            cycle=m.cycle)
        self.store.put_attempt(attempt)
        self.store.record_transition("attempt", aid, "",
                                     AttemptState.INTENT.value,
                                     Transition.APPLIED.value, "schedule",
                                     f"target hour {prop.target_t}")
        d.attempt_id = aid

        # ---- 4. STAGE 0 ADJUDICATES THE NOTIFICATION, and the pre-debit order
        #         is created inside it. An illegal target never reaches
        #         Razorpay, because the gate refuses before it calls out.
        refusal = self.gate.issue_notification(ref, m.cycle, prop.notify_t,
                                               prop.target_t, now_t)
        if refusal is not None:
            d.reason = f"Stage 0 refused the notification: {refusal.rule}"
            d.gate_verdict, d.refused_rule = "REFUSED", refusal.rule
            return self._record(d)

        pred = self.executor.predelivery_state(ref, prop.target_t)
        if pred is None or not pred.order_id:
            # `issue_notification` calls the executor and does not return what
            # it said, so the reason is read back off it. Clearing the pending
            # notice matters: the auditor rebuilds pendency from the log, and
            # one left outstanding reads as a second concurrent notice.
            why = self.executor.last_notify.get(ref.uid, {})
            d.reason = ("the pre-debit order was not created, so nothing was "
                        f"scheduled: {why.get('detail', 'reason unrecorded')}")
            self.gate.clear_pending(ref, m.cycle, now_t, "order not created")
            return self._record(d)

        attempt = self.store.attempt(aid) or attempt
        d.attempt_state = attempt.state.value
        d.reason = (f"pre-debit order created; the debit runs at hour "
                    f"{prop.target_t}")
        d.provider = {"order_id": pred.order_id}
        return self._record(d)

    # -------------------------------------------------------------- tick B
    def _execute(self, m: Mandate, c: Customer, attempt: PaymentAttempt,
                 now_t: int, d: Decision) -> Decision:
        ref = self._ref(m, c)
        d.attempt_id = attempt.id
        d.target_t = attempt.target_t
        d.attempt_state = attempt.state.value

        action = MoneyAction(
            action_id=attempt.id, ref=ref,
            amount=attempt.amount_paise / 100.0, cycle=m.cycle,
            target_t=attempt.target_t,
            notify_t=attempt.target_t - LEAD_HOURS, decided_at_t=now_t,
            kind=InterventionKind.RETRY)

        # ---- 5. EXECUTE. Stage 0 evaluates all five rules and reaches the
        #         executor only if every one of them permits.
        try:
            verdict = self.gate.submit(action)
        except RazorpayError as e:
            # THE PROVIDER REFUSED THE REQUEST, WHICH IS NOT THE SAME AS
            # REFUSING THE PAYMENT. It is never evidence about the customer, so
            # the belief is not touched -- `apply_outcome` runs only on a
            # terminal state and UNKNOWN is not one.
            #
            # UNKNOWN, NOT FAILED, AND THAT IS THE WHOLE POINT. "Order already
            # paid" is exactly what a resubmission after a crash mid-request
            # gets, and the order it names may hold a captured payment.
            # Recording FAILED there would report a collected cycle as
            # uncollected and spend an NPCI attempt on it. UNKNOWN is
            # non-terminal, is never retried automatically, and is what
            # `reconcile` resolves by asking the order what it holds.
            d.reason = f"the provider refused the request: {e}"
            d.gate_verdict = "ALLOWED"
            if advance(attempt.state, AttemptState.UNKNOWN) is Transition.APPLIED:
                self.store.record_transition("attempt", attempt.id,
                                             attempt.state.value,
                                             AttemptState.UNKNOWN.value,
                                             Transition.APPLIED.value, "submit",
                                             "request refused")
                attempt.state = AttemptState.UNKNOWN
            attempt.raw_reason = "request_refused"
            self.store.put_attempt(attempt)
            d.attempt_state = attempt.state.value
            return self._record(d)

        if isinstance(verdict, Refused):
            d.reason = f"Stage 0 refused: {verdict.refusal.rule}"
            d.gate_verdict, d.refused_rule = "REFUSED", verdict.refusal.rule
            return self._record(d)

        # ---- 6. RECORD THE ACKNOWLEDGEMENT. Not the outcome: there is none.
        out = verdict.outcome
        d.acted, d.gate_verdict = True, "ALLOWED"
        d.outcome_code = out.code
        d.outcome_raw = out.raw_code
        d.reason = "debit submitted; awaiting the authoritative outcome"
        raw = self.executor.raw.get(attempt.id, {})
        d.provider = {"http_status": raw.get("http_status"),
                      "payment_id": parse_payment_id(raw.get("body") or {}),
                      "order_id": attempt.order_id}
        attempt = self.store.attempt(attempt.id) or attempt
        attempt.payment_id = d.provider["payment_id"] or attempt.payment_id
        attempt.submitted_at = int(time.time())
        target = (AttemptState.UNKNOWN if out.raw_code == "transport_lost"
                  else AttemptState.SUBMITTED)
        if advance(attempt.state, target) is Transition.APPLIED:
            self.store.record_transition("attempt", attempt.id,
                                         attempt.state.value, target.value,
                                         Transition.APPLIED.value, "submit",
                                         out.raw_code)
            attempt.state = target
        self.store.put_attempt(attempt)
        d.attempt_state = attempt.state.value

        # ---- 7 and 8 happen elsewhere: the webhook, or `reconcile`.
        return self._record(d)


    def _case_view(self, m: Mandate, c: Customer, attempts_used: int,
                   day: int) -> CaseView:
        """The only thing the diagnosis layer ever sees about this mandate.

        No balance, no salary, no payday, no posterior, no provider id -- the
        band is a coarse label and `PaydayUncertainty` drops the expected
        balance before it can be read here.
        """
        history = tuple(a.outcome_code for a
                        in reversed(self.store.attempts_for(m.id, limit=10))
                        if a.outcome_code and a.outcome_code != "OK")
        unc = self.book.uncertainty(c.seq)
        day_in_cycle = max(0, day - m.cycle_start_t // 24)
        return CaseView(
            case_hash=m.id,
            attempts_used=attempts_used, attempts_cap=CAP,
            day_in_cycle=day_in_cycle,
            days_left_in_cycle=max(0, m.cycle_days - day_in_cycle),
            amount=m.charge_amount_paise / 100.0,
            decline_history=history,
            n_recent_z9=sum(1 for h in history if family_of(h) == "FUNDS"),
            peer_mandate_success_recent=False,
            uncertainty_band=unc.band)

    def _advance_to(self, customer_seq: int, day: int) -> None:
        """Age this customer's belief to `day`, exactly once per day.

        `BeliefBook.advance_day` raises on a second call for the same day: a
        filter aged twice is silently wrong. A live service ticks whenever it
        is asked, so the belief is walked forward one day at a time -- never
        skipped, never repeated.
        """
        last = self._advanced.get(customer_seq, -1)
        if day <= last:
            return
        for d in range(last + 1, day + 1):
            self.book.advance_day(customer_seq, d)
        self._advanced[customer_seq] = day

    def _record(self, d: Decision) -> Decision:
        self.decisions.append(d)
        del self.decisions[:-200]
        return d

    # ------------------------------------------------------ reconciliation
    def reconcile(self, limit: int = 50) -> list[dict]:
        """Ask the provider what happened to everything we are unsure about.

        THIS IS THE CRASH-RECOVERY PATH, and it is the same code either way: a
        process that died between submitting and hearing back leaves exactly
        the rows this query returns, and so does a webhook that never arrived.

        Two joins, in order of strength: the payment id if we have it, else the
        order -- which is all a lost submission leaves.
        """
        out: list[dict] = []
        for a in self.store.unresolved_attempts(ATTEMPT_UNRESOLVED, limit):
            if a.state is AttemptState.INTENT and not a.order_id:
                # Nothing was ever sent under this intent. Recovering it means
                # re-running the decision, not asking the provider about an
                # order that does not exist.
                out.append({"attempt": a.id, "result": "no provider request "
                                                       "was made"})
                continue
            entity = self._provider_payment(a)
            if entity is None:
                out.append({"attempt": a.id,
                            "result": "the provider has no payment for this "
                                      "attempt yet"})
                continue
            view = from_payment_entity(entity)

            # LEARNING AN IDENTIFIER IS NOT A STATE TRANSITION. A lost
            # submission has no payment id; it must be recorded even when the
            # answer does not advance the state, or the strong join is never
            # available and every later poll goes the long way round.
            learned = False
            if view.payment_id and not a.payment_id:
                a.payment_id, learned = view.payment_id, True
            if view.order_id and not a.order_id:
                a.order_id, learned = view.order_id, True
            if learned:
                self.store.put_attempt(a)

            verdict = advance(a.state, view.state)
            self.store.record_transition("attempt", a.id, a.state.value,
                                         view.state.value, verdict.value,
                                         "poll", view.raw_reason)
            if verdict is Transition.CONFLICT:
                a.conflicted = True
                self.store.put_attempt(a)
                out.append({"attempt": a.id, "result": "CONFLICT",
                            "provider_state": view.state.value})
                continue
            if verdict is not Transition.APPLIED:
                out.append({"attempt": a.id,
                            "result": ("identifiers recorded" if learned
                                       else "no change"),
                            "state": a.state.value,
                            "provider_state": view.state.value})
                continue
            a.state = view.state
            a.payment_id = view.payment_id or a.payment_id
            a.outcome_code = view.outcome_code or a.outcome_code
            a.raw_reason = view.raw_reason or a.raw_reason
            if view.state in (AttemptState.SUCCEEDED, AttemptState.FAILED):
                a.resolved_at = int(time.time())
            self.store.put_attempt(a)
            self.apply_outcome(a)
            out.append({"attempt": a.id, "result": a.state.value})
        return out

    def _provider_payment(self, a: PaymentAttempt) -> dict | None:
        if a.payment_id:
            r = self.api.fetch_payment(a.payment_id)
            return r.body if r.ok else None
        if not a.order_id:
            return None
        r = self.api.fetch_order_payments(a.order_id)
        if not r.ok:
            return None
        item = first_item(r.body)
        return item or None

    def apply_outcome(self, a: PaymentAttempt) -> None:
        """Fold a RESOLVED attempt into the belief.

        TERMINAL STATES ONLY, so neither a submission nor an unknown outcome
        reaches the filter: `w3.BeliefPD.observe(amount, False)` hard-zeroes
        every balance bin at or above the amount, and doing that on an outcome
        nobody knows teaches it something false.
        """
        if a.state not in (AttemptState.SUCCEEDED, AttemptState.FAILED):
            return
        m = self.store.mandate(a.mandate_id)
        if m is None:
            return
        c = self.store.customer(m.customer_id)
        if c is None or c.seq not in self._known_customers:
            return
        self.book.record_outcome(c.seq, a.amount_paise / 100.0,
                                 a.state is AttemptState.SUCCEEDED)

    # ------------------------------------------------------------ webhooks
    def deliver_mock_webhooks(self) -> int:
        """Post the mock rail's queued webhooks through the REAL ingest path.

        OFFLINE ONLY. Routing them through `handle_webhook` rather than
        applying them directly is what makes the offline demonstration exercise
        the same signature check, deduplication and monotonic state machine the
        live one does.
        """
        drain = getattr(self.api, "drain_webhooks", None)
        if drain is None or self.config.is_live:
            return 0
        sent = 0
        for event_id, _kind, body in drain():
            raw, signature = mock_sign(body, self.config.webhook_secret)
            try:
                self.handle_webhook(raw.encode(), signature, event_id)
                sent += 1
            except webhooks.WebhookRejected:
                # Cannot happen with a correctly signed body; swallowed so a
                # demonstration cannot die on its own scaffolding.
                pass
        if sent:
            self.process_webhooks()
        return sent

    def handle_webhook(self, raw_body: bytes, signature: str,
                       event_id: str) -> webhooks.Ingested:
        """Verify and record. Interpretation happens after the response.

        Razorpay resends any event not acknowledged 2xx within five seconds,
        so this does the smallest durable thing and returns.
        """
        return webhooks.ingest(self.store, raw_body=raw_body,
                               signature=signature, event_id=event_id,
                               secret=self.config.webhook_secret)

    def process_webhooks(self, limit: int = 100) -> list[dict]:
        """Interpret recorded events, then fold any resolved attempt in.

        Safe to replay: every state change goes through a monotonic transition.
        """
        results = webhooks.process_pending(self.store, limit=limit)
        out = []
        for res in results:
            if res.attempt_id:
                a = self.store.attempt(res.attempt_id)
                if a is not None:
                    self.apply_outcome(a)
            out.append({"changed": res.changed, "detail": res.detail,
                        "mandate": res.mandate_id, "attempt": res.attempt_id})
        return out

    # ------------------------------------------------------------- status
    def health(self) -> dict:
        """Cheap, and it never calls the provider. See `connectivity`."""
        return {"ok": True, "mode": self.config.mode.value,
                "started_at": self.started_at,
                "epoch_origin": self.epoch_origin,
                "now_t": self.now_t(),
                "clock_offset_h": self.clock_offset_h,
                "db": os.path.basename(self.store.path)}

    def connectivity(self) -> dict:
        """One read-only provider call. Charges nothing, creates nothing."""
        r = self.api.ping()
        return {"reachable": r.outcome is not Outcome.LOST,
                "authenticated": r.ok,
                **r.summary()}

    def snapshot(self) -> dict:
        """Everything the console renders in one request."""
        return {
            "config": self.config.describe(),
            "health": self.health(),
            "counts": self.store.summary(),
            "provider_calls": getattr(self.api, "calls", 0),
            "provider_lost": getattr(self.api, "lost", 0),
        }
