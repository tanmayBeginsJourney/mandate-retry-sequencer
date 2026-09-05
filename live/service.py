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
from agent.execution.razorpay_api import (DEFAULT_FREQUENCY,
                                          MAX_AMOUNT_RANGE_PAISE,
                                          MIN_AMOUNT_PAISE, Outcome,
                                          RazorpayApi, Transport,
                                          VALID_FREQUENCIES,
                                          first_item, parse_payment_id,
                                          parse_token_from_payment,
                                          parse_token_status)
from agent.execution.razorpay_executor import (MandateBinding, PredeliveryJournal,
                                               RazorpayError, RazorpayExecutor)
from agent.execution.razorpay_mock import MockRazorpayApi, sign as mock_sign
from agent.execution.razorpay_predelivery import PredeliveryOrder, PredeliveryPhase
from agent.llm.compose import compose_outreach
from agent.llm.fallback import RuleBasedDiagnoser
from agent.policy import timing
# THE ESCALATION LADDER, AS PURE PREDICATES. The same module `agent/loop.py`
# asks, so the simulation and the live rail cannot disagree about when a
# reminder is due or when the fourth debit is held. It imports nothing and
# decides nothing on its own; see `_apply_intervention` and `_ladder_holds`.
from agent.recovery import (backup_link_collects, escalate_halts_cycle,
                            fourth_debit_blocked, is_funds_decline,
                            should_issue_backup_after_fail,
                            should_remind_after_fail)
from agent.policy.belief_book import BeliefBook
from agent.ports import (CaseView, InterventionKind, MandateRef, MoneyAction,
                         PendingNotification, Refused, family_of)
from live.config import LiveConfig, Mode
from live.domain import (ATTEMPT_PRESENTED, ATTEMPT_UNRESOLVED, AttemptState,
                         Customer, Mandate, MandateState, PaymentAttempt,
                         TOKEN_STATUS_STATE, Transition, advance,
                         advance_mandate, from_payment_entity)
from live.store import Store
from live import webhooks

#: The database key holding the clock origin. See `LiveService.now_t`.
EPOCH_ORIGIN_KEY = "epoch_origin"

#: The database key holding the offline demonstration clock's offset, in
#: hours. See `LiveService.advance_clock`.
CLOCK_OFFSET_KEY = "clock_offset_h"

#: One presentation plus three retries. `agent/constraints/rules.py` enforces
#: it; this is named here only so the scheduler can be told how many are left.
CAP = 4


def at_hour(t: int) -> str:
    """A simulated hour, written the way an operator reads it.

    THE SERVICE COUNTS IN HOURS AND NOBODY READS A MOMENT THAT WAY. `target_t`
    is hours since `epoch_origin`, so hour 752 and hour 600 are a day and a
    half apart and neither says so. Every message this module writes for a
    human names the day and the time instead; the field itself is unchanged and
    is still served as a number, so nothing downstream has to parse prose.
    """
    h = max(0, int(t))
    return f"day {h // 24}, {h % 24:02d}:00"


class LiveError(RuntimeError):
    """A request cannot be served. Carries a message safe to show an operator."""


#: Razorpay's documented limits on the customer object: `name` "must be between
#: 3-50 characters in length", `email` "a maximum length of 64 characters",
#: `contact` "a maximum length of 15 characters including country code".
#: [VERIFIED] razorpay.com Create a Customer, read 4 September 2026.
MIN_NAME, MAX_NAME, MAX_EMAIL, MAX_CONTACT = 3, 50, 64, 15


def validate_customer(name: str, email: str,
                      contact: str) -> tuple[str, str, str]:
    """Check what the provider documents, and nothing more.

    THIS IS NOT AN EMAIL VALIDATOR. RFC 5322 addresses are not worth parsing
    here and a regex that tries is wrong in both directions; the check is that
    the field is present, is inside Razorpay's length limit, and has the one
    shape their API requires. The provider is the authority on the rest and
    says so by rejecting the create call.

    The point of checking at all is ordering: a length Razorpay refuses should
    cost a validation error, not a half-finished registration.
    """
    name, email, contact = name.strip(), email.strip(), contact.strip()
    bad: list[str] = []
    if not MIN_NAME <= len(name) <= MAX_NAME:
        bad.append(f"name must be {MIN_NAME}-{MAX_NAME} characters")
    if not email or len(email) > MAX_EMAIL or email.count("@") != 1             or email.startswith("@") or email.endswith("@"):
        bad.append(f"email must be 1-{MAX_EMAIL} characters and contain one "
                   f"@ with something either side")
    digits = contact.lstrip("+")
    if not digits.isdigit() or len(contact) > MAX_CONTACT or len(digits) < 8:
        bad.append(f"contact must be digits, optionally prefixed with +, at "
                   f"most {MAX_CONTACT} characters")
    if bad:
        raise LiveError("; ".join(bad))
    return name, email, contact


@dataclass
class Decision:
    """What one tick did. Everything the console shows about a decision."""
    mandate_id: str
    at: int
    acted: bool
    reason: str
    #: THE SIMULATED HOUR THIS TICK REASONED IN, beside `at`, which is the
    #: wall-clock second it happened at. Both are recorded because they answer
    #: different questions: Stage 0's peak and lead rules are stated in
    #: simulated hours, and an operator comparing `target_t` against a UNIX
    #: timestamp is comparing two different clocks. Written on every path.
    now_t: int = 0
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
    cycle: int = 0
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
    #: NPCI presentations already spent in the cycle this tick reasoned about,
    #: against the regulatory ceiling. `attempts_used` counts attempts that
    #: REACHED THE PROVIDER -- see `_attempts_this_cycle` -- so an intent that
    #: never left the process is not one of them. Read-only; the cap check in
    #: `_schedule` reads the same number.
    attempts_used: int = 0
    attempts_cap: int = CAP
    #: The belief filter's coarse confidence about WHEN this customer is worth
    #: asking: narrow | medium | wide. It is the same label `CaseView` carries,
    #: and it is a label rather than the posterior for the reason
    #: `agent/llm/caseview.py` gives -- a band says how sure our model is, a
    #: number would say something about the customer. Empty on a tick that
    #: never reached the scheduler.
    uncertainty_band: str = ""
    #: EVERY STAGE 0 RULE'S ANSWER, not just the first objection. `submit`
    #: evaluates all five and writes all five to the audit log; this is the
    #: same five kept in memory so the console can show them without reading
    #: the log back. Empty on a tick that never submitted a money action --
    #: the pre-debit notification is adjudicated by one short-circuiting
    #: predicate, and what it found is already in `refused_rule`.
    gate_checks: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


#: The executor's pre-debit phase and the durable attempt state are the same
#: fact in two vocabularies: `PredeliveryOrder` is the executor port, shared
#: with the batch root, and `AttemptState` is what survives a restart. This is
#: the only place they are translated.
#:
#: `DEBIT_ATTEMPTED` MAPS TO `SUBMITTING`, THE WEAKEST THING IT CAN MEAN. The
#: executor writes that phase BEFORE the request leaves, so the phase asserts
#: "a debit may be at the provider" and nothing stronger. `SUBMITTED` -- the
#: provider answered with a payment id -- is a fact only `_execute` has, and it
#: is written there.
_PHASE_STATE: dict[PredeliveryPhase, AttemptState] = {
    PredeliveryPhase.ORDER_CREATED: AttemptState.ORDER_CREATED,
    PredeliveryPhase.NOTIFICATION_DELIVERED: AttemptState.NOTIFIED,
    PredeliveryPhase.NOTIFICATION_FAILED: AttemptState.NOTIFICATION_FAILED,
    PredeliveryPhase.DEBIT_ATTEMPTED: AttemptState.SUBMITTING,
}
_STATE_PHASE: dict[AttemptState, PredeliveryPhase] = {
    AttemptState.INTENT: PredeliveryPhase.NONE,
    AttemptState.ORDER_CREATED: PredeliveryPhase.ORDER_CREATED,
    AttemptState.NOTIFIED: PredeliveryPhase.NOTIFICATION_DELIVERED,
    AttemptState.NOTIFICATION_FAILED: PredeliveryPhase.NOTIFICATION_FAILED,
    AttemptState.SUBMITTING: PredeliveryPhase.DEBIT_ATTEMPTED,
    AttemptState.SUBMITTED: PredeliveryPhase.DEBIT_ATTEMPTED,
    AttemptState.UNKNOWN: PredeliveryPhase.DEBIT_ATTEMPTED,
    AttemptState.AUTHORIZED: PredeliveryPhase.DEBIT_ATTEMPTED,
    AttemptState.SUCCEEDED: PredeliveryPhase.DEBIT_ATTEMPTED,
    AttemptState.FAILED: PredeliveryPhase.DEBIT_ATTEMPTED,
}

# BOTH DIRECTIONS ARE TOTAL, AND THAT IS LOAD-BEARING. A state missing from
# `_STATE_PHASE` would leave the journal reporting a phase the executor treats
# as chargeable, so a row already submitted -- or one whose pre-debit notice
# failed -- would be charged again after a restart. `RazorpayExecutor.attempt`
# only permits ORDER_CREATED and NOTIFICATION_DELIVERED, and a partial table
# disarms that check. A phase missing from `_PHASE_STATE` would leave a
# provider fact with nowhere durable to go.
assert set(_STATE_PHASE) == set(AttemptState)
assert set(_PHASE_STATE) == set(PredeliveryPhase) - {PredeliveryPhase.NONE}


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
        #:
        #: DURABLE, BESIDE `epoch_origin`, AND FOR THE SAME REASON. It was in
        #: memory only, so a restart put the clock back to wall-clock time
        #: while every `target_t` on disk stayed where the advanced clock had
        #: written it -- the service rewound by however far the demonstration
        #: had run, and then refused to charge attempts it had already
        #: scheduled because their hour was suddenly in the future.
        #: `advance_clock` refuses in live mode, so this can only ever be
        #: non-zero offline.
        self.clock_offset_h = int(self.store.meta_get(CLOCK_OFFSET_KEY, "0")
                                  or 0)
        self._known_customers: set[int] = set()
        #: customer seq -> the last day its belief has been advanced to.
        self._advanced: dict[int, int] = {}
        #: Attempt ids whose outcome THIS PROCESS folded into the belief.
        #:
        #: The belief lives in memory and the attempt row lives on disk, so a
        #: resolved attempt on disk is not evidence that the filter running now
        #: has read it -- a service restarted on an existing database holds
        #: attempts it has never seen. The console reports the belief from this
        #: set rather than from the attempt's state, so it cannot claim a
        #: measurement the filter never took.
        self._folded: set[str] = set()
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
        # `advance_clock` is the only writer, so a stale value here would mean
        # the offset had moved without going through the live-mode refusal
        # above.
        # WRITTEN BEFORE THE CALLER IS TOLD. A crash between the two would
        # otherwise leave the next start reading an hour the operator has
        # already seen the console move past.
        self.store.meta_set(CLOCK_OFFSET_KEY, str(self.clock_offset_h))
        return self.clock_offset_h

    def next_decision_hour(self) -> int:
        """Hours to the next simulated hour at which a tick would do something.

        OFFLINE DEMONSTRATION ONLY, and it decides nothing: it moves the clock,
        and the scheduler is asked afterwards exactly as it would be on a timer.

        Two things change the answer a tick gives. A scheduled debit runs at
        its own target hour, which the attempt row already carries. And the
        belief filter advances once a day, so the probabilities the scheduler
        compares are the same at every hour of one day. Every hour between
        those two produces the tick that has just been produced, which is what
        made a fixed twelve-hour step take a hundred clicks to reach a retry.

        Never zero: the clock only moves forward.
        """
        now_t = self.now_t()
        # The next day boundary. The belief has not moved before then.
        soonest = (now_t // 24 + 1) * 24
        for m in self.store.mandates():
            if not m.chargeable:
                continue
            for a in self.store.attempts_for(m.id, limit=50):
                if (a.state in (AttemptState.ORDER_CREATED,
                                AttemptState.NOTIFIED)
                        and now_t < a.target_t < soonest):
                    soonest = a.target_t
        return max(1, soonest - now_t)

    def refresh(self) -> None:
        """Rebuild the bindings and the belief book from the store.

        The executor holds the SAME dict object throughout, so mutating it in
        place is what makes a newly-authorised mandate chargeable without
        rebuilding the gate.
        """
        self.bindings.clear()
        # Read each table once. The customer lookup and the per-customer
        # mandate count were both queries inside the loop, so a hundred
        # mandates meant two hundred extra round trips per refresh -- and
        # `refresh()` runs on every mandate change.
        customers = {c.id: c for c in self.store.customers()}
        mandates = self.store.mandates()
        per_customer: dict[str, int] = {}
        for m in mandates:
            per_customer[m.customer_id] = per_customer.get(m.customer_id, 0) + 1

        for m in mandates:
            c = customers.get(m.customer_id)
            if c is None:
                continue
            uid = self._ref(m, c).uid
            if uid in self.bindings:
                # TWO MANDATES CANNOT SHARE ONE uid. The executor charges the
                # token it finds under this key, so a collision means one
                # customer's debit runs on another's mandate. The database's
                # unique indexes on `customers.seq` and
                # `mandates(customer_id, index_no)` make this unreachable;
                # failing here rather than overwriting is what keeps a future
                # regression from being silent money movement.
                raise LiveError(
                    f"mandate identity {uid} is claimed by two mandates "
                    f"({m.id} and one already bound). Refusing to bind: the "
                    f"executor would charge one customer's token for the "
                    f"other's mandate.")
            self.bindings[uid] = MandateBinding(
                rzp_customer_id=m.rzp_customer_id or c.rzp_customer_id,
                rzp_token_id=m.rzp_token_id,
                rzp_email=c.email, rzp_contact=c.contact,
                charge_amount=m.charge_amount_paise / 100.0,
                est_salary=m.est_salary, est_payday=m.est_payday)
            if c.seq not in self._known_customers:
                self.book.add_customer(c.seq, m.est_salary, m.est_payday,
                                       max(1, per_customer[c.id]))
                self._known_customers.add(c.seq)
            # THE PAYMENT LINK'S ID IS DURABLE AND THE EXECUTOR'S MAP IS NOT.
            # `_issue_backup` derives the reference id the same way, so it is
            # recoverable from the row rather than needing a column of its own.
            if m.backup_status and m.backup_vendor_id:
                self.executor.adopt_backup(
                    uid, f"backup_{uid}_{m.cycle}", m.backup_vendor_id)
            self._readopt_mock_state(m, c)
        self._rehydrate_ledger()

    def _readopt_mock_state(self, m: Mandate, c: Customer) -> None:
        """Tell the offline mock about state it created before this process.

        THE MOCK ONLY. Real Razorpay remembers its own tokens and orders across
        our restarts; `MockRazorpayApi` keeps both in dictionaries that die
        with the process, so a service restarted on an existing database holds
        a `rzp_token_id` and an `order_id` the rail has never heard of. The
        next call answers "Token does not exist" or "Order does not exist"
        while the mandate's own row still says ACTIVE and chargeable, which is
        a wedge the console cannot explain -- and the order case is worse than
        it looks, because the executor writes SUBMITTING before the request
        leaves, so a refusal after that point is indistinguishable from a lost
        response and the attempt ends UNKNOWN awaiting a reconciliation the
        mock cannot answer either.

        Guarded on the concrete class rather than on the mode, so this can
        never run against a real rail even if a caller passes an unexpected
        configuration. Both `adopt_` methods take plain values; nothing in
        `agent/execution/` may import from `live/`.
        """
        if not isinstance(self.api, MockRazorpayApi):
            return
        rzp_customer = m.rzp_customer_id or c.rzp_customer_id
        if not m.rzp_token_id or not rzp_customer:
            return
        self.api.adopt_token(
            token_id=m.rzp_token_id, customer_id=rzp_customer,
            max_amount_paise=m.max_amount_paise, expire_at=m.expire_at,
            status=m.token_status or "confirmed",
            email=c.email, contact=c.contact)
        # Only orders a debit may still run against. A resolved attempt's order
        # is history, and re-declaring it would let a stale receipt block a
        # fresh one.
        for a in self.store.attempts_for(m.id, limit=50):
            if a.resolved or not a.order_id or not a.receipt:
                continue
            self.api.adopt_order(
                order_id=a.order_id, receipt=a.receipt,
                amount_paise=a.amount_paise, token_id=m.rzp_token_id,
                payment_after=a.payment_after,
                status="attempted" if a.payment_id else "created",
                payment_id=a.payment_id)

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
        customers = {c.id: c for c in self.store.customers()}
        for m in self.store.mandates():
            c = customers.get(m.customer_id)
            if c is None:
                continue
            uid = self._ref(m, c).uid
            self.ledger.open_cycle(uid, m.cycle)
            for a in reversed(self.store.attempts_for(m.id, limit=50)):
                if a.cycle != m.cycle:
                    continue
                if a.state in (AttemptState.ORDER_CREATED,
                               AttemptState.NOTIFIED):
                    # THE STORED notify_t, NEVER `target_t - 24`. The scheduler
                    # notified at an hour the peak rule may have pushed the
                    # target well past, so reconstructing it put a different
                    # number in the ledger than the one Stage 0 was given
                    # before the restart -- and the same attempt was refused in
                    # one process and allowed in the next.
                    self.ledger.set_pending(uid, PendingNotification(
                        notify_t=a.notify_t, target_t=a.target_t,
                        under_previous_notice=False))
                elif a.state in ATTEMPT_PRESENTED:
                    self.ledger.record_attempt(uid, a.cycle,
                                               a.outcome_code or "")

    @staticmethod
    def _ref(m: Mandate, c: Customer) -> MandateRef:
        return MandateRef(c.seq, m.index_no, m.merchant_id)

    # ------------------------------------------------------- registration
    def create_customer(self, *, name: str, email: str,
                        contact: str) -> Customer:
        """Validate, create at the provider, then allocate identity atomically.

        Validation comes FIRST so a malformed field is refused before a
        customer record exists at Razorpay that nothing here points at.
        """
        name, email, contact = validate_customer(name, email, contact)
        r = self.api.create_customer(name=name, email=email, contact=contact,
                                     notes={"source": "recovery-agent"})
        if not r.ok:
            raise LiveError(f"customer create failed: {r.error_description or r.outcome.value}")
        # `seq` is allocated and inserted inside one transaction. Reading the
        # next value here and writing it afterwards let two concurrent
        # registrations take the same one, and two customers sharing a `seq`
        # share the `c{seq}m{index}` identity the executor binds tokens to.
        c = self.store.allocate_customer(Customer(
            id=f"cus_{uuid.uuid4().hex[:12]}",
            rzp_customer_id=str(r.body.get("id") or ""),
            email=email, contact=contact, name=name))
        return c

    def start_registration(self, *, customer_id: str, charge_amount_paise: int,
                           max_amount_paise: int,
                           frequency: str = DEFAULT_FREQUENCY,
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
        lo, hi = MAX_AMOUNT_RANGE_PAISE
        if not lo <= max_amount_paise <= hi:
            # Checked here as well as in the client so the refusal does not
            # depend on which rail is configured, and so it arrives as an
            # operator-readable message rather than a ValueError.
            raise LiveError(
                f"max_amount {max_amount_paise} paise is outside UPI AutoPay's "
                f"documented range {lo}-{hi} for an ordinary merchant "
                f"category")
        if frequency not in VALID_FREQUENCIES:
            raise LiveError(f"frequency {frequency!r} is not one Razorpay "
                            f"accepts for UPI AutoPay: "
                            f"{sorted(VALID_FREQUENCIES)}")

        m = Mandate(id=f"mdt_{uuid.uuid4().hex[:12]}", customer_id=c.id,
                    rzp_customer_id=c.rzp_customer_id,
                    max_amount_paise=max_amount_paise,
                    charge_amount_paise=charge_amount_paise,
                    frequency=frequency,
                    expire_at=int(time.time()) + 10 * 365 * 24 * 3600,
                    est_salary=est_salary or charge_amount_paise / 100.0 * 60,
                    est_payday=est_payday, cycle_days=cycle_days,
                    cycle_start_t=self.now_t())
        # The mandate row, with its index allocated atomically, exists BEFORE
        # the provider is called: the other half of the `c{seq}m{index}`
        # identity is claimed under the same rule as `seq`.
        m = self.store.allocate_mandate_index(m)
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
                     reason="", now_t=now_t)

        blocked = self._blocked(m)
        if blocked:
            d.reason = blocked
            return self._record(d)

        attempts = self.store.attempts_for(m.id, limit=50)
        # BEFORE THE ROLLOVER, because the paths below that return without
        # rolling are reasoning about the cycle the mandate is still in. The
        # rollover recounts it against the cycle it opened.
        d.attempts_used = self._attempts_this_cycle(m, attempts)

        # An attempt already scheduled owns the decision. Either its hour has
        # come or it has not.
        scheduled = self._scheduled_attempt(m, attempts)
        if scheduled is not None:
            d.cycle = scheduled.cycle
            if scheduled.target_t > now_t:
                d.reason = (f"a debit is scheduled for {at_hour(scheduled.target_t)}"
                            f"; it is now {at_hour(now_t)}")
                d.attempt_id = scheduled.id
                d.target_t = scheduled.target_t
                d.attempt_state = scheduled.state.value
                return self._record(d)
            return self._execute(m, c, scheduled, now_t, d)

        # A debit is in flight or its outcome is unknown. Razorpay's own
        # instruction is "do not create another subsequent payment until you
        # get the status of the previous one" ([VERIFIED] razorpay.com UPI
        # create-subsequent-payments, read 4 September 2026), and charging
        # twice is the worst thing this can do.
        #
        # ACROSS EVERY CYCLE, NOT JUST THE CURRENT ONE. An unresolved attempt
        # left behind by a cycle that has rolled over is exactly the debit
        # whose outcome is unknown, and filtering it out by cycle number would
        # let the rollover itself authorise a second charge.
        open_now = [a for a in attempts if a.state in ATTEMPT_UNRESOLVED]
        if open_now:
            d.reason = (f"attempt {open_now[0].id} is {open_now[0].state.value}; "
                        f"the outcome of the previous debit must be known "
                        f"before another is submitted")
            d.attempt_id = open_now[0].id
            d.attempt_state = open_now[0].state.value
            return self._record(d)

        m = self._roll_cycle(m, now_t, c)
        d.cycle = m.cycle
        d.attempts_used = self._attempts_this_cycle(m, attempts)

        if self._collected(m, attempts):
            d.reason = (f"cycle {m.cycle} is collected; the next debit is "
                        f"cycle {m.cycle + 1}, which opens on "
                        f"{at_hour(self._cycle_close_day(m) * 24)}")
            return self._record(d)

        return self._schedule(m, c, now_t, d, attempts)

    # ------------------------------------------------------ cycle semantics
    #
    # ONE COLLECTION PER CYCLE, AND ONE CYCLE AT A TIME. Both are taken from
    # `sim/harness.py`, which is the model every published number was measured
    # on: a mandate is scheduled only while `not m["collected"]`, and at the
    # day boundary past `cycle_close` the cycle advances and the per-cycle
    # counters reset. NPCI's rule is the same shape -- one successful debit on
    # a token per billing cycle.
    #
    # `collected` is DERIVED rather than stored. A cycle is collected when one
    # of its attempts succeeded, which is a fact already on disk, and a second
    # copy of it is a second thing to keep true across a crash.

    @staticmethod
    def _cycle_close_day(m: Mandate) -> int:
        """The first day NOT in the current cycle. In DAYS, like the sim.

        `cycle_start_t` is a simulated HOUR and `timing.propose` compares this
        against a day number, so the conversion happens here and once.
        """
        return m.cycle_start_t // 24 + m.cycle_days

    @staticmethod
    def _collected(m: Mandate, attempts: list[PaymentAttempt]) -> bool:
        """Has this cycle's money arrived, by either route?

        A PAID BACKUP LINK COLLECTS THE CYCLE. It is not an attempt and spends
        no NPCI presentation, but the customer has paid and a mandate debit
        after it would take the money twice.
        """
        return (any(a.cycle == m.cycle and a.succeeded for a in attempts)
                or backup_link_collects(m.backup_status))

    def _roll_cycle(self, m: Mandate, now_t: int, c: Customer) -> Mandate:
        """Advance to the cycle `now_t` falls in, one cycle at a time.

        Durable: the new cycle number and its start hour are written before the
        next decision reads them, so a restart resumes in the same cycle rather
        than re-opening a closed one. Called only with no unresolved attempt
        outstanding, so a rollover can never strand a debit whose outcome is
        still unknown.
        """
        day, before = now_t // 24, m.cycle
        rolled = False
        while day >= self._cycle_close_day(m):
            m.cycle += 1
            m.cycle_start_t = self._cycle_close_day(m) * 24
            rolled = True
        if not rolled:
            return m
        self.store.put_mandate(m)
        self.store.record_transition(
            "mandate", m.id, f"cycle {before}", f"cycle {m.cycle}",
            Transition.APPLIED.value, "cycle",
            f"opened on {at_hour(m.cycle_start_t)}")
        # A fresh cycle restores the full NPCI allowance and drops any notice
        # left outstanding by the last one.
        self.ledger.open_cycle(self._ref(m, c).uid, m.cycle)
        # AND IT CLEARS THE LADDER. A link issued for last month must not hold
        # this month's first debit, and last month's reminders are not this
        # month's. `halted_cycle` needs no clearing: it is compared against the
        # cycle number, which has just moved.
        if m.backup_vendor_id or m.backup_status or m.reminders_sent:
            m.backup_vendor_id, m.backup_status = "", ""
            m.reminders_sent = 0
            self.store.put_mandate(m)
        return m

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

    @staticmethod
    def _scheduled_attempt(m: Mandate,
                           attempts: list[PaymentAttempt]) -> PaymentAttempt | None:
        """An attempt with a pre-debit order and no charge submitted yet.

        Not filtered by cycle: an order created before a rollover is still an
        order the customer was notified about, and it is executed or it is not
        -- abandoning it at a cycle boundary would leave Razorpay expecting a
        debit that never comes.
        """
        for a in attempts:
            if a.state in (AttemptState.ORDER_CREATED, AttemptState.NOTIFIED):
                return a
        return None

    @staticmethod
    def _attempts_this_cycle(m: Mandate,
                             attempts: list[PaymentAttempt]) -> int:
        """NPCI presentations spent in this cycle.

        Counts attempts that reached the provider, not rows. An INTENT that
        never left the process and an order whose notice failed both cost the
        customer nothing and must not consume one of the four.
        """
        return len([a for a in attempts
                    if a.cycle == m.cycle and a.state in ATTEMPT_PRESENTED])

    # -------------------------------------------------------------- tick A
    def _schedule(self, m: Mandate, c: Customer, now_t: int, d: Decision,
                  attempts: list[PaymentAttempt]) -> Decision:
        ref = self._ref(m, c)
        # COUNTED HERE, FROM THIS METHOD'S OWN ARGUMENTS, and not read off the
        # Decision. `_decide` puts the same number on `d` for the console, and
        # it would be tempting to reuse it -- but then the NPCI cap check would
        # depend on a display field having been populated correctly by the
        # caller, and a fifth debit is not a rendering bug. The assignment
        # below is the other direction: the scheduler's number overwrites the
        # display one, so the console can never show a count the cap check
        # did not use.
        attempts_used = self._attempts_this_cycle(m, attempts)
        d.attempts_used = attempts_used
        if attempts_used >= CAP:
            d.reason = f"the NPCI attempt cap of {CAP} is spent for this cycle"
            return self._record(d)

        if m.halted_cycle == m.cycle:
            d.reason = (f"cycle {m.cycle} is halted; the diagnosis layer "
                        f"stopped it and it reopens at the next cycle")
            return self._record(d)

        # ---- 0. THE LADDER, BEFORE THE SCHEDULER IS ASKED FOR AN HOUR.
        #
        # THE THIRD FAILED FUNDS ATTEMPT IS THE LAST SAFE MANDATE DEBIT, and
        # what replaces the fourth is a Payment Link. Deciding this before
        # `timing.propose` runs is deliberate: proposing an hour for a debit
        # that must not happen would put a target on the record and then
        # withdraw it, and the audit trail would carry a scheduled debit
        # nothing ever intended to send.
        last_code = self._last_code(m, attempts)
        if (attempts_used >= CAP - 1
                and (is_funds_decline(last_code)
                     or fourth_debit_blocked(m.backup_status))):
            if not m.backup_status:
                view = self._case_view(m, c, attempts_used, now_t // 24)
                diag = self.diagnoser.diagnose(view)
                d.intervention = diag.intervention.value
                d.root_cause = diag.root_cause.value
                d.diagnosis_source = diag.source
                d.diagnosis_id = diag.diagnosis_id
                self._issue_backup(m, ref, diag, now_t, view)
            _hold, why = self._resolve_backup(m, ref, now_t)
            d.reason = why or ("a backup checkout replaces the fourth mandate "
                               "debit")
            return self._record(d)

        # An open link from earlier in this cycle holds every debit under it,
        # not only the fourth.
        if m.backup_status:
            _hold, why = self._resolve_backup(m, ref, now_t)
            d.reason = why
            return self._record(d)

        day = now_t // 24
        # IN DAYS. `timing.propose` compares it against a day number and
        # multiplies it by 24 to bound the target hour; handing it an hour made
        # every cycle close roughly thirty times too late, so no cycle ever
        # closed and the scheduler kept spending attempts in a window that
        # should have ended.
        cycle_close = self._cycle_close_day(m)

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

        # ---- 1a. A PROVIDER RULE THE SCHEDULER DOES NOT MODEL.
        #
        # Razorpay states that a subsequent UPI payment must not be created on
        # the last day of the billing cycle, and that doing so makes the
        # payment fail [VERIFIED, "Create Subsequent Payments" for UPI, read
        # 5 September 2026]. `agent/policy/timing.py` bounds its window at
        # `dd < cycle_close`, so `cycle_close - 1` -- the last day IN the
        # cycle -- is a legal target, and this agent's whole behaviour is to
        # wait late in the cycle. It is therefore biased toward the exact day
        # the provider says will fail.
        #
        # HERE AND NOT IN `agent/constraints/stage0.py`. Stage 0's five rules
        # are shared with the simulation and each is asserted by a gate; a
        # sixth would change what the published measurements were audited
        # against. This is a property of Razorpay, not of the policy, so the
        # live rail bounds against it and the simulator does not model it.
        # `docs/results.md` says so.
        if prop.target_t // 24 >= cycle_close - 1:
            d.reason = (
                f"a debit on {at_hour(prop.target_t)} falls on the last day "
                f"of the billing cycle, which Razorpay refuses for UPI; the "
                f"cycle closes on {at_hour(cycle_close * 24)}")
            return self._record(d)

        d.target_t, d.notify_t = prop.target_t, prop.notify_t

        # ---- 2. THE DIAGNOSIS LAYER. It names a cause and picks an
        #         intervention. `Diagnosis` has no field for a time.
        view = self._case_view(m, c, attempts_used, day)
        # READ OFF THE VIEW, not recomputed from the belief. The band the
        # console shows is then by construction the band the diagnoser was
        # shown, rather than a second reading of a filter that has moved on.
        d.uncertainty_band = view.uncertainty_band
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

        # ---- 2a. WHAT THE INTERVENTION MEANS FOR THIS DEBIT.
        #
        # A NUDGE IS A REMINDER, NOT A SKIPPED DEBIT, and this used to read it
        # as one: every non-RETRY answer returned here, so a single funds
        # decline cancelled every remaining attempt in the cycle. The
        # diagnoser's own rationale says "Prompting the customer BEFORE the
        # next scheduled attempt", `agent/loop.py:_phase_decide` falls through
        # to timing for exactly that reason, and measured against the mock rail
        # the returning version spent 2 presentations in the first cycle, 1 in
        # the second and 0 in the third.
        #
        # It also put the diagnosis layer in charge of WHEN, which is the one
        # thing the architecture says it must never decide. The scheduler has
        # already chosen the hour above; what happens here is only what to do
        # at it.
        if not self._apply_intervention(m, c, ref, diag, d, now_t, view,
                                        attempts_used):
            return self._record(d)

        # ---- 3. THE INTENT IS DURABLE BEFORE ANYTHING LEAVES THIS PROCESS.
        aid = action_id(self.log.run_id, ref, m.cycle, prop.target_t,
                        attempts_used + 1)
        prior = self.store.attempt(aid)
        if prior is not None and prior.resolved:
            # THE SAME HOUR, ALREADY TRIED AND CLOSED. Both `action_id` and the
            # order receipt are derived from the target hour, so writing this
            # row again would replace a terminal attempt with a fresh INTENT
            # and then ask Razorpay for an order under a receipt it has already
            # seen. Reachable only after `_abandon_intent`: a notice that
            # failed cannot be un-failed, a fresh one needs a fresh order, and
            # a fresh order needs a fresh hour.
            d.reason = (f"the attempt for {at_hour(prop.target_t)} is already "
                        f"{prior.state.value}; a fresh notice needs a fresh "
                        f"order, and a fresh order needs a different hour")
            d.attempt_id, d.attempt_state = prior.id, prior.state.value
            return self._record(d)
        attempt = PaymentAttempt(
            id=aid, mandate_id=m.id, mandate_uid=ref.uid,
            amount_paise=m.charge_amount_paise, state=AttemptState.INTENT,
            receipt=RazorpayExecutor.receipt_for(f"notify:{ref.uid}", ref,
                                                 prop.target_t),
            target_t=prop.target_t, notify_t=prop.notify_t,
            payment_after=self.epoch_origin + prop.target_t * 3600,
            cycle=m.cycle)
        self.store.put_attempt(attempt)
        self.store.record_transition("attempt", aid, "",
                                     AttemptState.INTENT.value,
                                     Transition.APPLIED.value, "schedule",
                                     f"target {at_hour(prop.target_t)}")
        d.attempt_id = aid

        # ---- 4. STAGE 0 ADJUDICATES THE NOTIFICATION, and the pre-debit order
        #         is created inside it. An illegal target never reaches
        #         Razorpay, because the gate refuses before it calls out.
        refusal = self.gate.issue_notification(ref, m.cycle, prop.notify_t,
                                               prop.target_t, now_t)
        if refusal is not None:
            d.reason = f"Stage 0 refused the notification: {refusal.rule}"
            d.gate_verdict, d.refused_rule = "REFUSED", refusal.rule
            # The gate refuses before it calls the executor, so nothing was
            # sent and the intent written at step 3 has to be closed here. See
            # `_abandon_intent`.
            d.attempt_state = self._abandon_intent(
                attempt, f"Stage 0 refused the notification: {refusal.rule}")
            return self._record(d)

        pred = self.executor.predelivery_state(ref, prop.target_t)
        if pred is None or not pred.order_id:
            # `issue_notification` calls the executor and does not return what
            # it said, so the reason is read back off it. Clearing the pending
            # notice matters: the auditor rebuilds pendency from the log, and
            # one left outstanding reads as a second concurrent notice.
            why = self.executor.last_notify.get(ref.uid, {})
            detail = str(why.get("detail", "reason unrecorded"))
            d.reason = ("the pre-debit order was not created, so nothing was "
                        f"scheduled: {detail}")
            self.gate.clear_pending(ref, m.cycle, now_t, "order not created")
            d.attempt_state = self._abandon_intent(
                attempt, f"the pre-debit order was not created: {detail}")
            return self._record(d)

        attempt = self.store.attempt(aid) or attempt
        d.attempt_state = attempt.state.value
        d.reason = (f"pre-debit order created; the debit runs at "
                    f"{at_hour(prop.target_t)}")
        d.provider = {"order_id": pred.order_id}
        return self._record(d)

    # ------------------------------------------------------- the ladder
    #
    # THE ESCALATION LADDER, AND IT IS THE SAME ONE THE SIMULATION RUNS.
    # `agent/recovery.py` states it as pure predicates and both composition
    # roots ask it the same questions:
    #
    #   attempts 1 and 2 fail on funds -> a funding reminder
    #   attempt 3 fails on funds       -> a Payment Link REPLACES attempt 4
    #   while that link is open        -> the fourth mandate debit is held
    #
    # The last line is the point of the whole thing. Four failed presentations
    # end the mandate and forfeit every remaining billing cycle, so the fourth
    # debit is the expensive one; spending it on an account that has already
    # declined three times buys almost nothing and costs the customer. The link
    # collects the cycle if it is paid, and if it closes unpaid the cycle is
    # forfeited and the mandate lives.
    #
    # None of this ran here until now. The ladder was built, the gate exposed
    # it, the executor implemented it, and `live/service.py` called
    # `record_non_money`, which writes a log line and stops.

    def _last_code(self, m: Mandate,
                   attempts: list[PaymentAttempt] | None = None) -> str:
        """The newest decline code IN THIS CYCLE, or ""."""
        rows = attempts if attempts is not None else self.store.attempts_for(
            m.id, limit=50)
        for a in rows:
            if a.cycle == m.cycle and a.outcome_code and a.outcome_code != "OK":
                return a.outcome_code
        return ""

    def _outreach(self, view: CaseView, diag, purpose: str) -> str:
        """Customer- or merchant-facing copy for a non-money action.

        `client=None`, so this is the template and never a model call. The
        service's diagnoser is the deterministic rule engine and the console
        reports it as such; reaching for a model here to write a reminder
        would make that report a half-truth. `compose_outreach` runs the same
        governance filter either way, so the redaction boundary is unchanged.
        """
        return compose_outreach(view, diag, client=None, purpose=purpose).body

    def _send_reminder(self, m: Mandate, ref: MandateRef, diag, now_t: int,
                       view: CaseView) -> None:
        """Ask the customer to fund the account. Spends no NPCI attempt."""
        self.gate.send_reminder(
            ref, m.cycle, m.charge_amount_paise / 100.0, now_t,
            diagnosis_id=diag.diagnosis_id,
            message=self._outreach(view, diag, "reminder"),
            action_id=f"remind_{ref.uid}_{m.cycle}_{m.reminders_sent}")
        m.reminders_sent += 1
        self.store.put_mandate(m)

    def _issue_backup(self, m: Mandate, ref: MandateRef, diag, now_t: int,
                      view: CaseView) -> None:
        """Put a Payment Link where the fourth mandate debit would have gone.

        A notification already outstanding for that debit must not fire, so it
        is withdrawn first and the withdrawal is written down -- the auditor
        rebuilds pendency from the log, and one dropped silently reads as a
        second concurrent notice.

        A LINK THAT FAILS TO CREATE STILL HOLDS THE DEBIT. `backup_status` goes
        to "expired" rather than staying empty: failing open into a
        mandate-killing fourth attempt is worse than missing one cycle.
        """
        self.gate.clear_pending(ref, m.cycle, now_t,
                                "replaced by backup checkout")
        wr = self.gate.issue_backup_link(
            ref, m.cycle, m.charge_amount_paise / 100.0, now_t,
            diagnosis_id=diag.diagnosis_id,
            message=self._outreach(view, diag, "backup_link"),
            action_id=f"backup_{ref.uid}_{m.cycle}")
        if wr.executed:
            m.backup_vendor_id = wr.vendor_id
            m.backup_status = wr.status or "issued"
        else:
            m.backup_status = "expired"
        self.store.put_mandate(m)

    def _resolve_backup(self, m: Mandate, ref: MandateRef,
                        now_t: int) -> tuple[bool, str]:
        """Poll an open link. Returns (hold the mandate debit, why).

        Every state but "" holds it, and each for a different reason, so the
        reason is returned rather than reconstructed by the caller.
        """
        if not m.backup_status:
            return False, ""
        if m.backup_status == "paid":
            return True, ("the backup checkout was paid; this cycle is "
                          "collected and a debit would take the money twice")
        if m.backup_status in ("expired", "cancelled"):
            return True, ("the backup checkout closed unpaid; the fourth "
                          "debit is not fired, so the mandate survives into "
                          "the next cycle")
        wr = self.gate.poll_backup_link(ref, m.cycle, now_t)
        status = wr.status or m.backup_status
        if wr.credited or status == "paid":
            m.backup_status = "paid"
            self.store.put_mandate(m)
            c = self.store.customer(m.customer_id)
            if c is not None and c.seq in self._known_customers:
                # A PAID LINK IS EVIDENCE ABOUT THE BALANCE, exactly as a
                # collected debit is: the customer had the money. It reaches
                # the filter for the same reason and by the same call.
                self.book.record_outcome(c.seq,
                                         m.charge_amount_paise / 100.0, True)
            return True, ("the backup checkout was paid; this cycle is "
                          "collected")
        if status != m.backup_status:
            m.backup_status = status
            self.store.put_mandate(m)
        if status in ("expired", "cancelled"):
            return True, ("the backup checkout closed unpaid; the fourth "
                          "debit is not fired, so the mandate survives into "
                          "the next cycle")
        return True, ("a backup checkout is open; the fourth mandate debit "
                      "waits for it to be paid or to close")

    def _apply_intervention(self, m: Mandate, c: Customer, ref: MandateRef,
                            diag, d: Decision, now_t: int, view: CaseView,
                            attempts_used: int) -> bool:
        """Execute what the diagnosis chose. True = go on and debit.

        RETRY and NUDGE both continue to the debit; the difference between them
        is that NUDGE also sends a reminder. ESCALATE continues unless the
        decline is one that cannot be retried at all. STOP does not continue.

        Every branch here EXECUTES. `record_non_money` is kept for STOP, which
        is the one intervention with nothing to send.
        """
        kind = diag.intervention
        if kind is InterventionKind.NUDGE:
            # NOTHING IS SENT HERE, AND THAT IS NOT AN OMISSION. The reminder
            # this intervention asks for has already gone out: it fires from
            # the failed outcome, in `_ladder_after_outcome`, which is where
            # `should_remind_after_fail` caps it at two per cycle. Sending a
            # second one from the scheduling tick would message the customer
            # about their bank balance twice for one decline, and would do it
            # again on every tick until the cycle closed.
            #
            # `agent/loop.py` makes the same call in the same words: "Fail-path
            # reminders already fire in dispatch; fall through to timing so
            # attempts 1-3 still run."
            return True

        if kind is InterventionKind.ESCALATE:
            self.gate.send_escalate(
                ref, m.cycle, m.charge_amount_paise / 100.0, now_t,
                diagnosis_id=diag.diagnosis_id,
                brief=self._outreach(view, diag, "escalate"),
                action_id=f"escalate_{ref.uid}_{m.cycle}")
            # HANDING A RECOVERABLE FUNDS CASE TO A HUMAN MUST NOT STOP THE
            # RETRIES. `escalate_halts_cycle` is the same predicate the
            # simulation asks, and it halts only on a decline that a retry
            # cannot fix.
            if escalate_halts_cycle(self._last_code(m), diag.root_cause.value):
                m.halted_cycle = m.cycle
                self.store.put_mandate(m)
                d.reason = ("the diagnosis layer escalated a decline that "
                            "cannot be retried; queued for the merchant and "
                            "held for this cycle")
                return False
            d.reason = "escalated to the merchant; the attempt continues"
            return True

        if kind is InterventionKind.STOP:
            self.gate.record_non_money(ref, m.cycle, kind.value, now_t,
                                       diagnosis_id=diag.diagnosis_id)
            m.halted_cycle = m.cycle
            self.store.put_mandate(m)
            d.reason = "the diagnosis layer stopped this cycle"
            return False

        return True

    def _abandon_intent(self, attempt: PaymentAttempt, detail: str) -> str:
        """Close an intent that never left this process. Returns the new state.

        `_schedule` writes the intent to disk BEFORE it calls the provider, so
        a crash between the two leaves a row saying "we may have asked". Two
        branches after that write return without asking at all: Stage 0
        refusing the notification, and a pre-debit order the provider did not
        create. In both, no request reached Razorpay -- the gate refuses before
        it calls the executor, and `not pred.order_id` is the executor
        reporting that it has no order.

        A row left in INTENT is in `ATTEMPT_UNRESOLVED`, so the `open_now`
        guard in `_decide` blocks every later tick on it, for the life of the
        mandate. That guard is right and is not what changes here: it is what
        stops a second debit while the outcome of a first is unknown. What
        changes is that an attempt with no request behind it stops pretending
        to be one.

        `NOTIFICATION_FAILED` is the state the domain already defines for
        this: the customer did not get the notice, a notice that failed cannot
        be un-failed, and a fresh one needs a fresh order. It is terminal, so
        `open_now` stops blocking; it is not in `ATTEMPT_PRESENTED`, so it
        spends none of NPCI's four presentations.
        """
        verdict = advance(attempt.state, AttemptState.NOTIFICATION_FAILED)
        self.store.record_transition(
            "attempt", attempt.id, attempt.state.value,
            AttemptState.NOTIFICATION_FAILED.value, verdict.value, "schedule",
            detail)
        if verdict is not Transition.APPLIED:
            return attempt.state.value
        attempt.state = AttemptState.NOTIFICATION_FAILED
        attempt.resolved_at = int(time.time())
        if not attempt.raw_reason:
            attempt.raw_reason = "notification_not_issued"
        self.store.put_attempt(attempt)
        return attempt.state.value

    # -------------------------------------------------------------- tick B
    def _execute(self, m: Mandate, c: Customer, attempt: PaymentAttempt,
                 now_t: int, d: Decision) -> Decision:
        ref = self._ref(m, c)
        d.attempt_id = attempt.id
        d.target_t = attempt.target_t
        d.attempt_state = attempt.state.value

        # THE HOUR HAS COME AND THE LADDER HAS MOVED UNDER IT. A backup link
        # can be issued between scheduling and charging -- the third decline
        # resolves by webhook, which is exactly the window between the two
        # ticks -- and this order was created before that happened. Checked
        # here as well as in `_schedule` because the two run at different
        # times, and a scheduled debit is not an authorised one.
        if fourth_debit_blocked(m.backup_status):
            self.gate.clear_pending(ref, m.cycle, now_t,
                                    "held for the backup checkout")
            self._abandon_intent(attempt,
                                 "a backup checkout replaced this debit")
            d.attempt_state = AttemptState.NOTIFICATION_FAILED.value
            _hold, why = self._resolve_backup(m, ref, now_t)
            d.reason = why
            return self._record(d)

        # `notify_t` is READ, not recomputed. It is the hour the scheduler
        # actually notified at, and Stage 0's `pending` rule compares it
        # against the outstanding notice.
        action = MoneyAction(
            action_id=attempt.id, ref=ref,
            amount=attempt.amount_paise / 100.0, cycle=m.cycle,
            target_t=attempt.target_t,
            notify_t=attempt.notify_t, decided_at_t=now_t,
            kind=InterventionKind.RETRY)

        # ---- 5. EXECUTE. Stage 0 evaluates all five rules and reaches the
        #         executor only if every one of them permits.
        try:
            verdict = self.gate.submit(action)
            d.gate_checks = self._gate_checks(attempt.id)
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
            # The five rules ran and permitted the action; the refusal came
            # from the provider, after the gate. Recording them here is what
            # keeps "Stage 0 allowed this" and "the debit did not land" from
            # looking like the same event.
            d.gate_checks = self._gate_checks(attempt.id)
            # RE-READ, BECAUSE THE EXECUTOR MAY HAVE MOVED THE ROW. It writes
            # SUBMITTING before the request leaves, so the row says whether a
            # debit reached the rail -- which decides whether this is an
            # ambiguous submission or a request that never went out. Raising
            # before the send (an incomplete binding) must not burn an attempt.
            attempt = self.store.attempt(attempt.id) or attempt
            attempt.raw_reason = "request_refused"
            if attempt.state is AttemptState.SUBMITTING:
                self.store.record_transition("attempt", attempt.id,
                                             attempt.state.value,
                                             AttemptState.UNKNOWN.value,
                                             Transition.APPLIED.value, "submit",
                                             "request refused after submission")
                attempt.state = AttemptState.UNKNOWN
            self.store.put_attempt(attempt)
            # The gate's ledger did not see this attempt: `submit` raised
            # before recording it. Rebuilding from disk is what keeps the
            # in-process count and the one a restart would derive identical.
            self._rehydrate_ledger()
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

        THE DECLINE HISTORY IS THIS CYCLE'S, AND ONLY THIS CYCLE'S. It was the
        last ten attempts on the mandate, unfiltered, so a decline in August
        was still in the view in September -- and `RuleBasedDiagnoser` reads
        `n_recent_z9 >= 1` to choose NUDGE, so a fresh cycle opened already
        believing it had just been declined. `agent/loop.py` clears
        `decline_history` at rollover; this is the same rule, applied to a
        history that lives in a table instead of a list.
        """
        history = tuple(a.outcome_code for a
                        in reversed(self.store.attempts_for(m.id, limit=50))
                        if a.cycle == m.cycle and a.outcome_code
                        and a.outcome_code != "OK")
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

    def _gate_checks(self, action_id: str) -> list:
        """What Stage 0's five rules said about `action_id`, or nothing.

        The gate keeps one slot and tags it with what it adjudicated. A tag
        that does not match means another mandate's tick overwrote it between
        the submission and this read, and the honest answer is then an empty
        list rather than somebody else's verdicts.
        """
        tag, checks = self.gate.last_checks
        return [dict(c) for c in checks] if tag == action_id else []

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
            entity, why = self._provider_payment(a)
            if entity is None:
                # "NO PAYMENT YET" AND "COULD NOT ASK" ARE DIFFERENT ANSWERS.
                # Collapsing them reported a refused credential or a provider
                # outage as evidence that the debit never happened, which is
                # the reading that makes an operator resubmit.
                out.append({"attempt": a.id, "result": why,
                            "state": a.state.value})
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

    def _provider_payment(self, a: PaymentAttempt) -> tuple[dict | None, str]:
        """The provider's payment for this attempt, and why not if absent.

        Three outcomes, kept apart: the payment (resolve it), no payment on an
        order the provider answered for (genuinely nothing yet), and a call
        that did not succeed (we know nothing, and must not say we do).
        """
        if a.payment_id:
            r = self.api.fetch_payment(a.payment_id)
            if r.ok:
                return r.body, ""
            return None, self._unreachable("payment", r)
        if not a.order_id:
            return None, "no order was created, so there is nothing to ask about"
        r = self.api.fetch_order_payments(a.order_id)
        if not r.ok:
            return None, self._unreachable("order", r)
        item = first_item(r.body)
        if not item:
            return None, "the provider has no payment for this attempt yet"
        return item, ""

    @staticmethod
    def _unreachable(what: str, r) -> str:
        if r.outcome is Outcome.LOST:
            return (f"the provider did not answer when asked about the {what}; "
                    f"the outcome is still unknown")
        if r.outcome is Outcome.DENIED:
            return (f"the provider refused the credential when asked about the "
                    f"{what}; the outcome is still unknown")
        return (f"the provider rejected the {what} query "
                f"({r.error_code or r.status}); the outcome is still unknown")

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
        self._folded.add(a.id)
        self._ladder_after_outcome(m, c, a)

    def folded_in_session(self, attempt_id: str) -> bool:
        """Did the filter in THIS process read this attempt's outcome?"""
        return attempt_id in self._folded

    def _ladder_after_outcome(self, m: Mandate, c: Customer,
                              a: PaymentAttempt) -> None:
        """Run the escalation ladder on a resolved attempt.

        HERE, AND NOT IN `_schedule`, because the trigger is the OUTCOME and
        the outcome arrives out of band -- a webhook, or a reconciliation poll,
        both of which land in `apply_outcome`. Driving it from the next
        scheduling tick instead would delay the reminder until the agent next
        wanted to debit, which on a waiting mandate can be a week.

        Only a funds decline moves the ladder. `should_remind_after_fail` and
        `should_issue_backup_after_fail` are the same predicates the simulation
        asks, and both answer False on a technical or terminal code.
        """
        if a.state is not AttemptState.FAILED:
            return
        used = self._attempts_this_cycle(m, self.store.attempts_for(m.id,
                                                                    limit=50))
        ref = self._ref(m, c)
        view = self._case_view(m, c, used, self.now_t() // 24)
        diag = self.diagnoser.diagnose(view)
        if should_remind_after_fail(used, a.outcome_code, CAP):
            self._send_reminder(m, ref, diag, self.now_t(), view)
        if (should_issue_backup_after_fail(used, a.outcome_code, CAP)
                and not m.backup_status):
            self._issue_backup(m, ref, diag, self.now_t(), view)

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
                    self._release_dead_notice(a)
            out.append({"changed": res.changed, "detail": res.detail,
                        "mandate": res.mandate_id, "attempt": res.attempt_id})
        return out

    def _release_dead_notice(self, a: PaymentAttempt) -> None:
        """Drop the gate's pending notice when its attempt can never run.

        A failed pre-debit notice kills the attempt it belongs to, and the
        outstanding notification in the ledger belongs to that attempt. Left
        there it blocks every later notification for this mandate as a second
        concurrent one -- so the mandate would be correctly refused a debit and
        then incorrectly refused the fresh notice that could replace it.

        Cleared through `clear_pending` rather than by writing the ledger, so
        the cancellation reaches the audit trail: `auditor.py` rebuilds
        pendency from the log and a notice dropped silently reads as one still
        outstanding.
        """
        if a.state is not AttemptState.NOTIFICATION_FAILED:
            return
        m = self.store.mandate(a.mandate_id)
        c = self.store.customer(m.customer_id) if m else None
        if m is None or c is None:
            return
        self.gate.clear_pending(self._ref(m, c), a.cycle, self.now_t(),
                                "pre-debit notification failed")

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
