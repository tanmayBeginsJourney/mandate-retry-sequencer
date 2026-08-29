"""The recovery loop: detect -> diagnose -> choose -> schedule -> enforce ->
execute -> log.

TWO ITERATION ORDERS, ONE IMPLEMENTATION OF THE WORK.

`sim/harness.py:156` runs one customer across the whole horizon before starting
the next, and shares one technical-decline generator across all of them. Match
that nesting and the agent reproduces the harness bit-for-bit (24/24 runs,
`test_parity_vs_harness.py`).

But a cross-customer rail monitor cannot exist under that nesting. In
customer-major order, "everything the system has seen by time t" is "everything
customer 0 has done across 120 days" -- there is no such thing as the state of
the rail at hour t, because the other 99 customers have not been simulated yet.
Detecting an outage requires TIME on the outside.

So the per-customer work is written ONCE, split into four phases, and two
drivers call those phases in different orders. When the monitor is off the two
orders are provably identical -- there is no cross-customer state for the order
to matter to -- and `test_loop_order_equivalence.py` asserts exactly that,
paired with the mutant that proves the gate is not vacuous: turn the monitor
ON and the two orders MUST diverge, because now there is shared state.

Time-major mode also splits each hour by PHASE rather than by customer: every
customer dispatches, then every customer rolls over, then every customer
decides. That is the honest semantics -- at hour 8 the whole batch goes out
before anyone reacts to it -- and it means a decision made at hour 8 sees the
full hour of dispatch evidence rather than a prefix that depends on customer id.

WHAT THIS FILE MAY NOT IMPORT. Not `agent.execution` -- it never holds an
executor, only a gate. `agent/batch.py` is the composition root and is the one
place allowed to construct both.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import agent  # noqa: F401  -- puts sim/ on the path
import harness
import w3

from agent.audit.log import AuditLog, EventKind
from agent.constraints.stage0 import Stage0Gate, action_id
from agent.context.rail_monitor import RailMonitor, RailState
from agent.llm.caseview import build_case_view
from agent.llm.governance import sanitise
from agent.policy.belief_book import BeliefBook
from agent.policy.timing import DEFAULT_DISCOUNT, Reason, propose
from agent.ports import (TECH, Allowed, InterventionKind, MandateRef,
                         MoneyAction, Refused, StopRule, to_paise)
from agent.state import MandateState

CAP = w3.NPCI_MAX
HOURS = w3.HOURS
DECISION_HOUR = w3.DECISION_HOUR        # 8
PEER_WINDOW_DAYS = 7


@dataclass
class LoopContext:
    gate: Stage0Gate
    book: BeliefBook
    log: AuditLog
    diagnoser: object
    monitor: RailMonitor
    days: int
    cyc: int
    discount: float = DEFAULT_DISCOUNT
    # THE SCHEDULER SEAM. None means the belief-driven index policy, which is
    # what every existing measurement uses and what the parity gate compares
    # against the harness. A fixed-schedule baseline plugs in here so it passes
    # through the SAME Stage 0 gate and the SAME audit trail, which is what
    # makes its recovery rate comparable. See agent/policy/fixed_schedule.py.
    scheduler: object = None
    log_ticks: bool = False
    collect_calib: bool = False
    # ---- outage response switches, each measurable in isolation
    pause_on_outage: bool = False
    # "never" | "outage_only" | "always"
    suppress_tech_updates: str = "never"
    # ---- accumulators
    calib: list = field(default_factory=list)
    counters: dict = field(default_factory=lambda: {
        "n_att": 0, "n_ok": 0, "recovered_paise": 0, "waits": 0,
        "nudges": 0, "escalations": 0, "agent_stops": 0,
        "paused_dispatch": 0, "paused_decisions": 0,
        "tech_updates_suppressed": 0, "tech_declines": 0,
        "attempts_wasted_on_tech": 0})
    # Attempts and money split by WHO DIAGNOSED the mandate that produced them.
    # The executor never sees a `source`, so this compares like with like --
    # but which cases fall back is NOT random, so it describes the split and
    # does not measure an effect. `batch_report.py` says so where it prints it.
    by_source: dict = field(default_factory=lambda: {
        "llm": {"att": 0, "ok": 0, "paise": 0},
        "fallback": {"att": 0, "ok": 0, "paise": 0}})
    stops: dict = field(default_factory=lambda: {r.value: 0 for r in StopRule})
    # {(mandate_uid, cycle): day collected}. A record of outcomes, never an
    # input to a decision. Feeds the recovery-rate metric; see W0.
    collected_cycles: dict = field(default_factory=dict)


@dataclass
class CustomerRuntime:
    ci: int
    c: dict
    mands: list
    recent_success: list = field(default_factory=list)
    # The remitter bank's UPI handle. Carried here rather than looked up,
    # because `loop.py` may not hold an executor (rule I2) -- `batch.py`
    # passes it in as a plain string, exactly as it passes the noisy
    # estimates in.
    bank: str = ""


# ------------------------------------------------------------------- phases
def _phase_advance(rt: CustomerRuntime, t: int, ctx: LoopContext) -> None:
    if t % HOURS != 0:
        return
    # ONCE PER CUSTOMER PER DAY. BeliefBook raises on a double call.
    ctx.book.advance_day(rt.ci, t // HOURS)


def _phase_dispatch(rt: CustomerRuntime, t: int, ctx: LoopContext) -> None:
    day = t // HOURS
    for m in rt.mands:
        if not m.alive or m.pending is None or m.pending.target_t != t:
            continue

        # ---- CONTEXT GATE. Not a Stage 0 rule -- a rail judgement. It decides
        # WHETHER to act now, never WHEN to act, so it stays off the timing
        # path. The pending notification is DROPPED rather than reused later:
        # holding a notification open across a pause and firing it hours later
        # is a re-presentation whose legality nobody here has established, and
        # docs/01_FACTS.md does not cover it. Cancelling costs a day and is
        # unambiguously legal.
        #
        # DETECTION IS ASSESSED WHETHER OR NOT WE ACT ON IT. These were once
        # the same `if`, which made the detector structurally silent whenever
        # the response was switched off -- so the detection-power study measured
        # a TPR of 0.00 at every severity and every population size and called
        # it a held prediction. Assessing always is what lets detection and
        # response be ablated independently, which is the whole point of the
        # experiment.
        #
        # It is assessed PER DISPATCH, not once per hour, on purpose: 99.22% of
        # attempts land at hour 8, so an outage covering that hour hits one
        # batch. Reacting only from the next hour would mean the response could
        # never help at all. A real system sees results stream back within
        # seconds, so later items in the same batch can benefit from earlier
        # ones -- and that is the only channel through which pausing can pay.
        v = ctx.monitor.assess(t) if ctx.monitor.enabled else None
        if ctx.pause_on_outage and v is not None:
            if v.state is RailState.OUTAGE:
                m.pending = None
                ctx.gate.clear_pending(m.ref, m.cycle, t,
                                       reason="rail outage: dispatch paused")
                ctx.counters["paused_dispatch"] += 1
                ctx.log.emit(EventKind.STOP, t, mandate_uid=m.ref.uid,
                             cycle=m.cycle, rule="RAIL_OUTAGE_PAUSE",
                             terminal=False, detail=v.reason,
                             rail_p=v.p_value, rail_n=v.n_attempts,
                             rail_tech=v.n_tech)
                continue

        pend = m.pending
        m.pending = None
        a = MoneyAction(
            action_id=action_id(ctx.log.run_id, m.ref, m.cycle, t,
                                m.attempts_used + 1),
            ref=m.ref, amount=m.amount, cycle=m.cycle, target_t=t,
            notify_t=pend.notify_t, decided_at_t=t,
            kind=InterventionKind.RETRY,
            p_now=getattr(m, "_p_now", 0.0), p_later=getattr(m, "_p_later", 0.0),
            index_score=getattr(m, "_score", 0.0),
            diagnosis_id=getattr(m, "_did", ""))

        # S1's calibration input, computed BEFORE the update, exactly as
        # harness.py:279-280 does it.
        p_pred = (ctx.book.belief_for(rt.ci).p_success(m.amount)
                  if ctx.collect_calib else None)

        decision = ctx.gate.submit(a)
        if isinstance(decision, Refused):
            continue                    # the gate working, not a stop

        outcome = decision.outcome
        ctx.monitor.record(t, outcome.code)     # EVERY customer feeds this
        ctx.counters["n_att"] += 1
        src = getattr(m, "_source", "fallback")
        bucket = ctx.by_source.get(src)
        if bucket is not None:
            bucket["att"] += 1
        m.attempts_used += 1
        m.total_attempts += 1
        m.prev_code = outcome.code
        m.decline_history.append(outcome.code)

        if p_pred is not None:
            ctx.calib.append((p_pred, 1.0 if outcome.success else 0.0))

        if outcome.success:
            ctx.counters["n_ok"] += 1
            m.collected = True
            m.got_cycles += 1
            ctx.counters["recovered_paise"] += to_paise(m.amount)
            # WHICH cycle was collected, and on what day. Needed for the
            # recovery-rate metric (docs/04_BUILD_PLAN.md W0) and for nothing
            # else -- it records an outcome, it does not influence one, so
            # degenerate-mode parity with `harness.run` is untouched. The same
            # facts are independently recoverable from the OUTCOME rows of the
            # audit log, and `test_recovery_metric.py` checks the two agree.
            ctx.collected_cycles[(m.ref.uid, m.cycle)] = day
            if bucket is not None:
                bucket["ok"] += 1
                bucket["paise"] += to_paise(m.amount)
            rt.recent_success.append((day, m.ref.uid))
            ctx.stops[StopRule.COLLECTED.value] += 1
        else:
            if outcome.code == TECH:
                ctx.counters["tech_declines"] += 1
                ctx.counters["attempts_wasted_on_tech"] += 1
            if m.attempts_used >= CAP:
                m.alive = False
                ctx.stops[StopRule.MANDATE_DEAD.value] += 1
                ctx.log.emit(EventKind.STOP, t, mandate_uid=m.ref.uid,
                             cycle=m.cycle, rule=StopRule.MANDATE_DEAD.value,
                             terminal=True,
                             detail="attempt cap reached without collection; "
                                    "mandate forfeits its remaining cycles")

        # ---- THE BELIEF UPDATE, and the one place the rail state changes it.
        #
        # w3.BeliefPD.observe(amount, success) TAKES NO DECLINE CODE
        # (w3.py:416), and harness.py:270-276 passes success=False for a
        # technical decline. So the frozen filter treats "the bank glitched"
        # and "the account is empty" as the SAME censored measurement -- and
        # the update hard-zeroes every balance bin at or above the amount
        # (w3.py:432). At P_TECH=0.008 that is noise. Under an outage it is
        # corruption, and because a pooled belief is ONE object shared by all
        # k mandates, one technical decline corrupts all k at once.
        #
        # Suppressing it is an AGENT action with a cost: a suppressed
        # observation is information not used, and if the monitor is wrong we
        # have thrown away a real signal. Measured, not assumed.
        suppress = False
        if outcome.code == TECH:
            if ctx.suppress_tech_updates == "always":
                suppress = True
            elif ctx.suppress_tech_updates == "outage_only":
                suppress = (v is not None and v.state is RailState.OUTAGE)
        if suppress:
            ctx.counters["tech_updates_suppressed"] += 1
            ctx.log.emit(EventKind.NON_MONEY_ACTION, t, mandate_uid=m.ref.uid,
                         cycle=m.cycle, action_kind="SUPPRESS_TECH_UPDATE",
                         adjudicated=False, action_id=a.action_id,
                         detail="technical decline is not evidence about this "
                                "customer's balance")
        else:
            ctx.book.record_outcome(rt.ci, m.amount, outcome.success)

        # A technical decline may auto-represent under the SAME notification.
        # A Z9 may not. (docs/01_FACTS.md)
        if outcome.code == TECH and m.alive and m.attempts_used < CAP:
            nt = harness.earliest_legal(day, t + 1)
            if nt is not None and nt < m.cycle_close * HOURS:
                if ctx.gate.issue_notification(m.ref, m.cycle, None, nt, t) is None:
                    m.pending = ctx.gate.ledger.pending(m.ref.uid)


def _phase_rollover(rt: CustomerRuntime, t: int, ctx: LoopContext) -> None:
    if t % HOURS != 0:
        return
    day = t // HOURS
    for m in rt.mands:
        if day >= m.cycle_close and m.alive:
            # A notification outstanding at rollover is withdrawn, not carried
            # into the new cycle. Recorded for the same reason as the pause.
            ctx.gate.clear_pending(m.ref, m.cycle, t,
                                   reason="billing cycle closed")
            m.cycle += 1
            m.attempts_used = 0
            m.collected = False
            m.pending = None
            m.prev_code = None
            m.halted_in_cycle = None
            m.decline_history.clear()
            ctx.gate.ledger.open_cycle(m.ref.uid, m.cycle)


def _phase_decide(rt: CustomerRuntime, t: int, ctx: LoopContext) -> None:
    if t % HOURS != DECISION_HOUR:
        return
    day = t // HOURS
    mands = rt.mands
    live = [m for m in mands
            if m.alive and not m.collected and m.pending is None
            and m.cycle_open <= day < m.cycle_close
            and m.attempts_used < CAP
            and m.halted_in_cycle != m.cycle]
    if not live:
        return

    v = ctx.monitor.assess(t) if ctx.monitor.enabled else None
    if ctx.pause_on_outage and v is not None:
        if v.state is RailState.OUTAGE:
            ctx.counters["paused_decisions"] += len(live)
            ctx.log.emit(EventKind.STOP, t, customer_id=rt.ci,
                         rule="RAIL_OUTAGE_PAUSE", terminal=False,
                         detail=v.reason, n_mandates_held=len(live),
                         rail_p=v.p_value)
            return

    unc = ctx.book.uncertainty(rt.ci)        # NO balance. See belief_book.
    belief = ctx.book.belief_for(rt.ci)

    for m in live:
        peer = any(d >= day - PEER_WINDOW_DAYS and uid != m.ref.uid
                   for d, uid in rt.recent_success)
        view = build_case_view(
            amount=m.amount, attempts_used=m.attempts_used, attempts_cap=CAP,
            day=day, cycle_open=m.cycle_open, cycle_close=m.cycle_close,
            decline_history=m.decline_history, peer_success_recent=peer,
            uncertainty=unc, merchant_note=rt.c.get("merchant_note", ""),
            bank=rt.bank)

        diag = ctx.diagnoser.diagnose(view)
        safe_text, gov = sanitise(diag.rationale)
        ctx.log.emit(EventKind.DIAGNOSIS, t, mandate_uid=m.ref.uid,
                     cycle=m.cycle, diagnosis_id=diag.diagnosis_id,
                     case_hash=view.case_hash, root_cause=diag.root_cause.value,
                     intervention=diag.intervention.value,
                     confidence=diag.confidence, source=diag.source,
                     prompt_id=diag.prompt_id, rationale=safe_text,
                     governance_ok=gov.ok,
                     governance_reasons=list(gov.reasons) or None,
                     recommendations=list(diag.recommendations) or None)
        m._did = diag.diagnosis_id
        m._source = diag.source
        kind = diag.intervention

        if kind is InterventionKind.NUDGE:
            # Non-money, zero-credit. Kept as a recommendation surfaced to the
            # merchant: the unconditional topup ceiling puts its value at
            # +0.02 pts (2SE 0.59) on the shipping config.
            ctx.counters["nudges"] += 1
            ctx.gate.record_non_money(m.ref, m.cycle, "NUDGE", t,
                                      diag.diagnosis_id, credits_money=False)
            continue
        if kind is InterventionKind.ESCALATE:
            # ZERO-CREDIT WORKFLOW ACTION. It does NOT halt attempts. It was
            # measured at +0.759 pts when it halted them -- and that entire
            # gain was death-prevention, i.e. it was STOP wearing a different
            # trigger. Two actions doing one job is worse than one, so the
            # halting behaviour lives in STOP and this is now the audit trail
            # for "compliant escalation" and nothing else.
            ctx.counters["escalations"] += 1
            ctx.gate.record_non_money(m.ref, m.cycle, "ESCALATE", t,
                                      diag.diagnosis_id, credits_money=False,
                                      halts_attempts=False)
            ctx.stops[StopRule.ESCALATED.value] += 1
        elif kind is InterventionKind.STOP:
            ctx.counters["agent_stops"] += 1
            m.halted_in_cycle = m.cycle
            ctx.gate.record_non_money(m.ref, m.cycle, "STOP", t,
                                      diag.diagnosis_id)
            ctx.stops[StopRule.AGENT_STOP.value] += 1
            ctx.log.emit(EventKind.STOP, t, mandate_uid=m.ref.uid,
                         cycle=m.cycle, rule=StopRule.AGENT_STOP.value,
                         terminal=False,
                         detail="held back before the cap to preserve the "
                                "mandate's remaining billing cycles")
            continue

        if ctx.scheduler is None:
            td = propose(belief, m.amount, day, t, m.cycle_close,
                         m.attempts_used, kind=InterventionKind.RETRY,
                         discount=ctx.discount)
        else:
            # A baseline arm. It never sees `belief` -- that is the whole
            # point of the seam, and it is why this branch cannot accidentally
            # borrow the timing policy's machinery.
            td = ctx.scheduler(m.amount, day, t, m.cycle_close,
                               m.attempts_used, kind=InterventionKind.RETRY)

        if ctx.log_ticks:
            ctx.log.emit(EventKind.DECISION_TICK, t, mandate_uid=m.ref.uid,
                         cycle=m.cycle, verdict=td.reason, p_now=td.p_now,
                         p_later=td.p_later, index_score=td.index_score,
                         attempts_used=m.attempts_used)

        if td.proposal is None:
            if td.reason == Reason.WAIT:
                ctx.counters["waits"] += 1
            elif td.reason == Reason.CYCLE_CLOSED:
                ctx.stops[StopRule.CYCLE_CLOSED.value] += 1
            elif td.reason == Reason.NO_LEGAL_SLOT:
                ctx.stops[StopRule.NO_LEGAL_SLOT.value] += 1
            continue

        p = td.proposal
        m._p_now, m._p_later, m._score = p.p_now, p.p_later, p.index_score
        if ctx.gate.issue_notification(m.ref, m.cycle, p.notify_t,
                                       p.target_t, t) is None:
            m.pending = ctx.gate.ledger.pending(m.ref.uid)


PHASES = (_phase_advance, _phase_dispatch, _phase_rollover, _phase_decide)


# -------------------------------------------------------------------- driver
def run_agent(pop, seed, gate: Stage0Gate, book: BeliefBook, log: AuditLog,
              diagnoser, *, estimates, banks=None,
              monitor: RailMonitor | None = None,
              discount: float = DEFAULT_DISCOUNT, log_ticks: bool = False,
              time_major: bool = False, collect_calib: bool = False,
              pause_on_outage: bool = False,
              suppress_tech_updates: str = "never",
              scheduler=None) -> dict:
    """Run the agent over a population. Same metric names as `harness.run`."""
    days = pop[0]["days"]
    cyc = pop[0]["cycle_days"]
    T = days * HOURS

    ctx = LoopContext(gate=gate, book=book, log=log, diagnoser=diagnoser,
                      monitor=monitor or RailMonitor(harness.P_TECH,
                                                     enabled=False),
                      days=days, cyc=cyc, discount=discount,
                      log_ticks=log_ticks, collect_calib=collect_calib,
                      pause_on_outage=pause_on_outage,
                      suppress_tech_updates=suppress_tech_updates,
                      scheduler=scheduler)

    runtimes = []
    for ci, c in enumerate(pop):
        est_sal, est_pay = estimates(ci)
        book.add_customer(ci, est_sal, est_pay, len(c["mandates"]))
        runtimes.append(CustomerRuntime(
            ci=ci, c=c, bank=(banks or {}).get(ci, ""),
            mands=[MandateState(ref=MandateRef(ci, mi, m["merchant"]),
                                amount=m["amount"], due_day=m["due_day"],
                                cycle_days=cyc)
                   for mi, m in enumerate(c["mandates"])]))

    if time_major:
        for t in range(T):
            for phase in PHASES:
                for rt in runtimes:
                    phase(rt, t, ctx)
    else:
        for rt in runtimes:
            for t in range(T):
                for phase in PHASES:
                    phase(rt, t, ctx)

    # ---- accounting
    cyc_due = cyc_got = n_mand = n_alive = n_starved = 0
    for rt in runtimes:
        for m in rt.mands:
            n_mand += 1
            closed = m.cycles_due(days)
            cyc_due += closed
            cyc_got += min(m.got_cycles, closed)
            if m.alive:
                n_alive += 1
            if m.total_attempts == 0:
                n_starved += 1
            if m.attempts_used >= CAP and m.alive:
                ctx.stops[StopRule.CAP_REACHED.value] += 1

    cn = ctx.counters
    return dict(
        cycle_rec=cyc_got / cyc_due if cyc_due else 0.0,
        approval=cn["n_ok"] / cn["n_att"] if cn["n_att"] else 0.0,
        survival=n_alive / n_mand if n_mand else 0.0,
        att_per_cycle=cn["n_att"] / cyc_due if cyc_due else 0.0,
        starvation=n_starved / n_mand if n_mand else 0.0,
        cycles_due=cyc_due,
        recovered_paise=cn["recovered_paise"],
        gate_refusals=dict(gate.refusals),
        gate_allowed=gate.allowed,
        stops=ctx.stops,
        waits=cn["waits"],
        nudges=cn["nudges"],
        escalations=cn["escalations"],
        agent_stops=cn["agent_stops"],
        paused_dispatch=cn["paused_dispatch"],
        paused_decisions=cn["paused_decisions"],
        tech_declines=cn["tech_declines"],
        tech_updates_suppressed=cn["tech_updates_suppressed"],
        attempts_wasted_on_tech=cn["attempts_wasted_on_tech"],
        # Each transition carries the evidence that caused it, so a
        # detection study can EXPLAIN a firing instead of guessing at it.
        rail_transitions=[(t, lbl, v.n_attempts, v.n_tech, v.p_value)
                          for t, lbl, v in ctx.monitor.transitions],
        outcome_by_source={k: dict(v) for k, v in ctx.by_source.items()},
        calib=ctx.calib,
        # {(mandate_uid, cycle): day}. Combined with the world's at-risk set in
        # `batch.py` to give the recovery-rate metrics. See agent/metrics.py.
        collected_cycles=ctx.collected_cycles,
    )
