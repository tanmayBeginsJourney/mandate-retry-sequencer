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
from agent.llm.compose import compose_outreach
from agent.llm.governance import sanitise
from agent.policy.belief_book import BeliefBook
from agent.policy.timing import (DEFAULT_CYCLE_VALUE, DEFAULT_DISCOUNT,
                                 Reason, propose)
from agent.ports import (TECH, Allowed, Diagnosis, InterventionKind,
                         MandateRef, MoneyAction, Refused, RootCause, StopRule,
                         INDETERMINATE_CODES, to_paise)
from agent.recovery import (UnresolvedCycle, batch_legal_ceiling,
                            escalate_halts_cycle, fourth_debit_blocked,
                            indeterminate_reason, is_funds_decline,
                            is_terminal_risk_decline, should_emit_risk_retry,
                            should_emit_risk_terminal,
                            should_issue_backup_after_fail,
                            should_remind_after_fail, should_report_unresolved)
from agent.state import MandateState

CAP = w3.NPCI_MAX
HOURS = w3.HOURS
DECISION_HOUR = w3.DECISION_HOUR        # 8
PEER_WINDOW_DAYS = 7


def _llm_client(ctx: LoopContext, purpose: str = ""):
    """LLM transport for exception-facing copy only (escalate briefs)."""
    if not ctx.compose_llm or purpose != "escalate":
        return None
    d = ctx.diagnoser
    return getattr(d, "client", None)


def _stub_diag(m, intervention=InterventionKind.NUDGE) -> Diagnosis:
    return Diagnosis(
        diagnosis_id=getattr(m, "_did", "") or "recovery",
        root_cause=RootCause.INSUFFICIENT_FUNDS,
        intervention=intervention, confidence=0.7,
        rationale="Funds decline on the mandate path.",
        source="recovery", prompt_id="recovery")


def _case_view(rt: CustomerRuntime, m, day: int, ctx: LoopContext):
    unc = ctx.book.uncertainty(rt.ci, m.ref.uid)
    peer = any(d >= day - PEER_WINDOW_DAYS and uid != m.ref.uid
               for d, uid in rt.recent_success)
    return build_case_view(
        amount=m.amount, attempts_used=m.attempts_used, attempts_cap=CAP,
        day=day, cycle_open=m.cycle_open, cycle_close=m.cycle_close,
        decline_history=m.decline_history, peer_success_recent=peer,
        uncertainty=unc, merchant_note=rt.c.get("merchant_note", ""),
        bank=rt.bank)


def _send_reminder(ctx: LoopContext, rt: CustomerRuntime, m, t: int) -> None:
    day = t // HOURS
    view = _case_view(rt, m, day, ctx)
    diag = _stub_diag(m, InterventionKind.NUDGE)
    copy = compose_outreach(view, diag, client=_llm_client(ctx, "reminder"),
                            purpose="reminder")
    wr = ctx.gate.send_reminder(m.ref, m.cycle, m.amount, t,
                                diagnosis_id=diag.diagnosis_id,
                                message=copy.body,
                                action_id=f"remind_{m.ref.uid}_{m.cycle}_{m.attempts_used}")
    m.reminders_sent += 1
    ctx.counters["reminders"] += 1
    ctx.counters["nudges"] += 1
    if wr.executed:
        ctx.counters["nudges_executed"] += 1


def _issue_backup(ctx: LoopContext, rt: CustomerRuntime, m, t: int) -> None:
    # A notification already queued for the fourth debit must not fire.
    ctx.gate.clear_pending(m.ref, m.cycle, t,
                           reason="replaced by backup checkout")
    m.pending = None
    day = t // HOURS
    view = _case_view(rt, m, day, ctx)
    diag = _stub_diag(m, InterventionKind.NUDGE)
    copy = compose_outreach(view, diag, client=_llm_client(ctx, "backup_link"),
                            purpose="backup_link")
    wr = ctx.gate.issue_backup_link(
        m.ref, m.cycle, m.amount, t,
        diagnosis_id=diag.diagnosis_id, message=copy.body,
        action_id=f"backup_{m.ref.uid}_{m.cycle}")
    ctx.counters["backup_links"] += 1
    if wr.executed:
        m.backup_vendor_id = wr.vendor_id
        m.backup_status = wr.status or "issued"
        m.backup_expire_t = t + 48
    else:
        # Link did not create. Still hold the fourth debit: failing open
        # into a mandate-killing attempt is worse than missing this cycle.
        m.backup_status = "expired"
        ctx.counters["backup_expired"] += 1


def _hold_if_ceiling(ctx: LoopContext, m, t: int) -> bool:
    """True = this mandate must not take a money action. Circuit breaker."""
    if ctx.counters["n_att"] < ctx.legal_ceiling:
        return False
    ctx.gate.clear_pending(m.ref, m.cycle, t,
                           reason="batch legal ceiling")
    m.pending = None
    if m.halted_in_cycle != m.cycle:
        m.halted_in_cycle = m.cycle
        ctx.stops[StopRule.BATCH_LEGAL_CEILING.value] += 1
        ctx.log.emit(EventKind.STOP, t, mandate_uid=m.ref.uid,
                     cycle=m.cycle, rule=StopRule.BATCH_LEGAL_CEILING.value,
                     terminal=False,
                     detail="batch legal ceiling "
                            f"{ctx.legal_ceiling}; holding remaining "
                            "live mandates")
    return True


def _emit_risk_if_needed(ctx: LoopContext, m, t: int, code: str,
                         *, cycle: int | None = None) -> None:
    """Audit-only runtime risk detection. Does not change scheduling."""
    cyc = m.cycle if cycle is None else cycle
    if m.collected and cycle is None:
        return
    if should_emit_risk_retry(collected=False,
                              already_emitted=(cycle is None
                                               and m.risk_retry_emitted),
                              decline_history=(m.decline_history
                                               if cycle is None else [code]),
                              code=code):
        if cycle is None:
            m.risk_retry_emitted = True
        ctx.counters["risk_retry"] += 1
        ctx.log.emit(EventKind.RISK_RETRY, t, mandate_uid=m.ref.uid,
                     cycle=cyc, decline_code=code,
                     intervention="bounded_retry",
                     detail="first insufficient-funds decline this cycle; "
                            "bounded retry on recovery schedule")
    elif should_emit_risk_terminal(collected=False,
                                   already_emitted=(cycle is None
                                                    and m.risk_terminal_emitted),
                                   code=code):
        if cycle is None:
            m.risk_terminal_emitted = True
        ctx.counters["risk_terminal"] += 1
        ctx.log.emit(EventKind.RISK_TERMINAL, t, mandate_uid=m.ref.uid,
                     cycle=cyc, decline_code=code,
                     intervention="stop_and_flag",
                     detail="hard decline this cycle; stop re-presenting, "
                            "flag for re-auth or manual outreach")


def _mark_pending_outcome(ctx: LoopContext, m, t: int, outcome) -> None:
    """Record indeterminate debit; halt cycle to block double-debit."""
    reason = indeterminate_reason(outcome.code,
                                  getattr(outcome, "raw_code", "") or "")
    if m.cycle not in m.unresolved_cycles:
        m.unresolved_cycles[m.cycle] = UnresolvedCycle(
            cycle=m.cycle, code=outcome.code, reason=reason)
    m.halted_in_cycle = m.cycle
    ctx.stops[StopRule.AGENT_STOP.value] += 1
    ctx.log.emit(EventKind.STOP, t, mandate_uid=m.ref.uid,
                 cycle=m.cycle, rule=StopRule.AGENT_STOP.value,
                 terminal=False,
                 detail="debit outcome indeterminate "
                        f"({reason}); cycle halted so no retry can "
                        "double-debit before async confirm")


def _resolve_earliest_pending(ctx: LoopContext, rt: CustomerRuntime, m,
                              t: int, day: int, outcome) -> None:
    """A definitive outcome resolves the oldest still-pending cycle."""
    if not m.unresolved_cycles:
        return
    earl = min(m.unresolved_cycles)
    pending = m.unresolved_cycles.pop(earl)
    code = outcome.code
    if outcome.success:
        ctx.collected_cycles[(m.ref.uid, earl)] = day
        m.got_cycles += 1
        ctx.counters["recovered_paise"] += to_paise(m.amount)
        ctx.stops[StopRule.COLLECTED.value] += 1
        return
    if is_funds_decline(code):
        _emit_risk_if_needed(ctx, m, t, code, cycle=earl)
    elif is_terminal_risk_decline(code):
        _emit_risk_if_needed(ctx, m, t, code, cycle=earl)
    # Other definitive codes: resolved without a risk row; pending cleared.
    _ = pending


def _emit_run_end_unresolved(ctx: LoopContext, runtimes, t: int) -> None:
    """Coverage metric: one row per mandate, not per cycle."""
    for rt in runtimes:
        for m in rt.mands:
            if not should_report_unresolved(m.unresolved_cycles):
                continue
            ctx.counters["n_unresolved"] += 1
            earliest = min(m.unresolved_cycles)
            last_cyc = max(m.unresolved_cycles)
            last = m.unresolved_cycles[last_cyc]
            ctx.log.emit(
                EventKind.UNRESOLVED, t, mandate_uid=m.ref.uid,
                n_unresolved_cycles=len(m.unresolved_cycles),
                earliest_unresolved_cycle=earliest,
                last_indeterminate_code=last.code,
                last_indeterminate_reason=last.reason,
                detail="coverage gap: "
                       f"{len(m.unresolved_cycles)} cycle(s) still pending "
                       f"at run end (earliest cycle {earliest}); "
                       f"last indeterminate outcome {last.reason}; "
                       "cycle was halted to prevent double-debit on "
                       "async confirm")


def _hold_last_attempt(ctx: LoopContext, m, t: int) -> None:
    """Close the cycle without a fourth debit. Idempotent per cycle."""
    if m.halted_in_cycle == m.cycle:
        return
    m.halted_in_cycle = m.cycle
    ctx.stops[StopRule.LAST_ATTEMPT_HELD.value] += 1
    ctx.log.emit(EventKind.STOP, t, mandate_uid=m.ref.uid,
                 cycle=m.cycle, rule=StopRule.LAST_ATTEMPT_HELD.value,
                 terminal=False,
                 detail="backup checkout closed unpaid; fourth "
                        "mandate debit not fired")


def _resolve_backup(ctx: LoopContext, rt: CustomerRuntime, m, t: int, day: int) -> bool:
    """Poll an open backup link. True = do not schedule a mandate debit."""
    if not m.backup_status:
        return False
    if m.backup_status == "paid":
        return True
    if m.backup_status in ("expired", "cancelled"):
        _hold_last_attempt(ctx, m, t)
        return True
    wr = ctx.gate.poll_backup_link(m.ref, m.cycle, t)
    if wr.status:
        m.backup_status = wr.status
    if wr.credited or wr.status == "paid":
        m.backup_status = "paid"
        m.collected = True
        m.got_cycles += 1
        ctx.counters["n_ok"] += 1
        ctx.counters["backup_paid"] += 1
        ctx.counters["recovered_paise"] += to_paise(m.amount)
        ctx.collected_cycles[(m.ref.uid, m.cycle)] = day
        ctx.stops[StopRule.COLLECTED.value] += 1
        ctx.book.record_outcome(rt.ci, m.amount, True, m.ref.uid)
        rt.recent_success.append((day, m.ref.uid))
        return True
    if wr.status in ("expired", "cancelled"):
        ctx.counters["backup_expired"] += 1
        _hold_last_attempt(ctx, m, t)
        return True
    return True  # still issued


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
    compose_llm: bool = False
    last_attempt_backup: bool = False
    remind_on_fail: bool = False
    # n_mandates × 4 × cycles in the horizon. Circuit breaker, not a budget.
    legal_ceiling: int = 2 ** 31 - 1
    bracket: bool = False
    coverage: bool = False
    lookahead: int | None = None
    #: P(a future billing cycle collects), used to price the mandate's
    #: continuation value on the LAST attempt of a cycle. 0.0 is inert and
    #: reproduces the behaviour every measurement before 1 September 2026 was
    #: taken on. See agent/policy/timing.py for the selection and the sweep.
    cycle_value: float = DEFAULT_CYCLE_VALUE
    #: W25. Choose by backward induction over (attempts left, days left)
    #: instead of the one-step index. OFF by default; the DP is measured, not
    #: shipped. See agent/policy/timing.py and agent/tests/test_plan_dp.py.
    plan: bool = False
    # ---- outage response switches, each measurable in isolation
    pause_on_outage: bool = False
    # "never" | "outage_only" | "always"
    suppress_tech_updates: str = "never"
    # ---- accumulators
    calib: list = field(default_factory=list)
    counters: dict = field(default_factory=lambda: {
        "n_att": 0, "n_ok": 0, "recovered_paise": 0, "waits": 0,
        "nudges": 0, "escalations": 0, "agent_stops": 0,
        "nudges_executed": 0, "escalations_executed": 0,
        "reminders": 0, "backup_links": 0, "backup_paid": 0,
        "backup_expired": 0,
        "paused_dispatch": 0, "paused_decisions": 0,
        "tech_updates_suppressed": 0, "tech_declines": 0,
        "attempts_wasted_on_tech": 0,
        "risk_retry": 0, "risk_terminal": 0, "n_unresolved": 0})
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
        if m.collected or fourth_debit_blocked(m.backup_status):
            ctx.gate.clear_pending(
                m.ref, m.cycle, t,
                reason="last attempt held for backup checkout")
            m.pending = None
            continue
        if _hold_if_ceiling(ctx, m, t):
            continue

        # ---- CONTEXT GATE. Not a Stage 0 rule -- a rail judgement. It decides
        # WHETHER to act now, never WHEN to act, so it stays off the timing
        # path. The pending notification is DROPPED rather than reused later:
        # holding a notification open across a pause and firing it hours later
        # is a re-presentation whose legality nobody here has established, and
        # docs/results.md does not cover it. Cancelling costs a day and is
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
        p_pred = (ctx.book.belief_for(rt.ci, m.ref.uid).p_success(m.amount)
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

        if getattr(outcome, "pending", False) or outcome.code in INDETERMINATE_CODES:
            _mark_pending_outcome(ctx, m, t, outcome)
            continue

        _resolve_earliest_pending(ctx, rt, m, t, day, outcome)

        if p_pred is not None:
            ctx.calib.append((p_pred, 1.0 if outcome.success else 0.0))

        if outcome.success:
            ctx.counters["n_ok"] += 1
            m.collected = True
            m.got_cycles += 1
            ctx.counters["recovered_paise"] += to_paise(m.amount)
            # WHICH cycle was collected, and on what day. Needed for the
            # recovery-rate metric (docs/results.md) and for nothing
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
            _emit_risk_if_needed(ctx, m, t, outcome.code)
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
            # The mandate uid is ignored when this customer pools, and is
            # what keeps the evidence inside one merchant when it does not.
            ctx.book.record_outcome(rt.ci, m.amount, outcome.success,
                                    m.ref.uid)

        if (not outcome.success
                and ctx.remind_on_fail
                and should_remind_after_fail(m.attempts_used, outcome.code, CAP)):
            _send_reminder(ctx, rt, m, t)
        if (not outcome.success
                and ctx.last_attempt_backup
                and should_issue_backup_after_fail(m.attempts_used, outcome.code,
                                                   CAP)
                and not m.backup_status):
            _issue_backup(ctx, rt, m, t)

        # A technical decline may auto-represent under the SAME notification.
        # A Z9 may not. (docs/results.md)
        if outcome.code == TECH and m.alive and m.attempts_used < CAP and not m.backup_status:
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
            m.backup_vendor_id = ""
            m.backup_status = ""
            m.backup_expire_t = 0
            m.reminders_sent = 0
            m.risk_retry_emitted = False
            m.risk_terminal_emitted = False
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

    # Fetched per MANDATE rather than once per customer. When this customer
    # pools, every iteration returns the same object and the same numbers,
    # so pooled behaviour is unchanged -- `posterior_summary` and
    # `expected` are pure reads (w3.py:550-563). When it does not pool,
    # each mandate has its own belief and fetching once would have used
    # mandate 1's posterior for all k. That is the defect this shape
    # prevents, and it is the same one harness.py:554-560 already had once.
    for m in live:
        if _resolve_backup(ctx, rt, m, t, day):
            continue
        if _hold_if_ceiling(ctx, m, t):
            continue

        unc = ctx.book.uncertainty(rt.ci, m.ref.uid)   # NO balance.
        belief = ctx.book.belief_for(rt.ci, m.ref.uid)
        peer = any(d >= day - PEER_WINDOW_DAYS and uid != m.ref.uid
                   for d, uid in rt.recent_success)
        view = build_case_view(
            amount=m.amount, attempts_used=m.attempts_used, attempts_cap=CAP,
            day=day, cycle_open=m.cycle_open, cycle_close=m.cycle_close,
            decline_history=m.decline_history, peer_success_recent=peer,
            uncertainty=unc, merchant_note=rt.c.get("merchant_note", ""),
            bank=rt.bank)

        last_code = m.decline_history[-1] if m.decline_history else ""
        if (ctx.last_attempt_backup and m.attempts_used >= CAP - 1
                and is_funds_decline(last_code) and not m.backup_status):
            _issue_backup(ctx, rt, m, t)
            continue

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

        # NUDGE is a reminder, not a skipped debit. Fail-path reminders already
        # fire in dispatch; fall through to timing so attempts 1-3 still run.
        if kind is InterventionKind.ESCALATE:
            copy = compose_outreach(view, diag, client=_llm_client(ctx, "escalate"),
                                    purpose="escalate")
            wr = ctx.gate.send_escalate(m.ref, m.cycle, m.amount, t,
                                        diagnosis_id=diag.diagnosis_id,
                                        brief=copy.body,
                                        action_id=diag.diagnosis_id)
            ctx.counters["escalations"] += 1
            if wr.executed:
                ctx.counters["escalations_executed"] += 1
            last = m.decline_history[-1] if m.decline_history else ""
            if escalate_halts_cycle(last, diag.root_cause.value):
                ctx.stops[StopRule.ESCALATED.value] += 1
                m.halted_in_cycle = m.cycle
                ctx.log.emit(EventKind.STOP, t, mandate_uid=m.ref.uid,
                             cycle=m.cycle, rule=StopRule.ESCALATED.value,
                             terminal=False,
                             detail="mandate cannot be retried; queued for "
                                    "merchant re-authorisation")
                continue
            # Recoverable funds: queue is written, retries continue.
        elif kind is InterventionKind.STOP:
            last = m.decline_history[-1] if m.decline_history else ""
            if (ctx.last_attempt_backup and m.attempts_used >= CAP - 1
                    and is_funds_decline(last)):
                _issue_backup(ctx, rt, m, t)
                continue
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

        if ctx.last_attempt_backup and m.attempts_used >= CAP - 1:
            last = m.decline_history[-1] if m.decline_history else ""
            if is_funds_decline(last) or fourth_debit_blocked(m.backup_status):
                if not m.backup_status:
                    _issue_backup(ctx, rt, m, t)
                continue

        if ctx.scheduler is None:
            td = propose(belief, m.amount, day, t, m.cycle_close,
                         m.attempts_used, kind=InterventionKind.RETRY,
                         discount=ctx.discount,
                         bracket=getattr(ctx, "bracket", False),
                         coverage=getattr(ctx, "coverage", False),
                         lookahead=getattr(ctx, "lookahead", None),
                         # Billing cycles this mandate still has after the
                         # current one. `cycles_due` counts cycles that CLOSE
                         # inside the horizon, which is the same denominator
                         # the recovery metric is reported against, so a
                         # forfeited cycle costs the objective exactly what it
                         # costs the score.
                         cycles_left=max(
                             0, m.cycles_due(ctx.days) - m.cycle - 1),
                         cycle_value=ctx.cycle_value,
                         plan=getattr(ctx, "plan", False))
        else:
            # A baseline arm. It never sees `belief` -- that is the whole
            # point of the seam, and it is why this branch cannot accidentally
            # borrow the timing policy's machinery.
            # `customer_id` is passed so a payday-anchored baseline can read
            # the SAME noisy estimate the agent's belief is seeded from.
            # `propose_fixed` ignores it. No scheduler can reach a true payday
            # or a balance: `estimates()` is the only accessor and it is noisy.
            td = ctx.scheduler(m.amount, day, t, m.cycle_close,
                               m.attempts_used, kind=InterventionKind.RETRY,
                               customer_id=m.ref.customer_id)

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
            elif td.reason == Reason.MANDATE_PRESERVED:
                # The cycle is forfeited to keep the mandate alive. Held for
                # the REST of the cycle, not just today: the odds only fall as
                # the window closes, so re-asking every morning would spend a
                # decision on a question already answered.
                m.halted_in_cycle = m.cycle
                ctx.stops[StopRule.MANDATE_PRESERVED.value] += 1
                ctx.log.emit(EventKind.STOP, t, mandate_uid=m.ref.uid,
                             cycle=m.cycle,
                             rule=StopRule.MANDATE_PRESERVED.value,
                             terminal=False,
                             p_now=td.p_now, index_score=td.index_score,
                             detail="last attempt declined: the odds of "
                                    "collecting did not cover the mandate's "
                                    "remaining billing cycles")
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
              scheduler=None, compose_llm: bool = False,
              last_attempt_backup: bool = False,
              remind_on_fail: bool = False,
              legal_ceiling: int | None = None,
              bracket: bool = False,
              coverage: bool = False,
              lookahead: int | None = None,
              cycle_value: float = DEFAULT_CYCLE_VALUE,
              plan: bool = False) -> dict:
    """Run the agent over a population. Same metric names as `harness.run`."""
    days = pop[0]["days"]
    cyc = pop[0]["cycle_days"]
    T = days * HOURS
    n_mand = sum(len(c["mandates"]) for c in pop)
    ceiling = (legal_ceiling if legal_ceiling is not None
               else batch_legal_ceiling(n_mand, days, cyc, CAP))

    ctx = LoopContext(gate=gate, book=book, log=log, diagnoser=diagnoser,
                      monitor=monitor or RailMonitor(harness.P_TECH,
                                                     enabled=False),
                      days=days, cyc=cyc, discount=discount,
                      log_ticks=log_ticks, collect_calib=collect_calib,
                      pause_on_outage=pause_on_outage,
                      suppress_tech_updates=suppress_tech_updates,
                      scheduler=scheduler, compose_llm=compose_llm,
                      last_attempt_backup=last_attempt_backup,
                      remind_on_fail=remind_on_fail,
                      legal_ceiling=ceiling, bracket=bracket,
                      coverage=coverage, lookahead=lookahead,
                      cycle_value=cycle_value, plan=plan)

    runtimes = []
    for ci, c in enumerate(pop):
        est_sal, est_pay = estimates(ci)
        book.add_customer(ci, est_sal, est_pay, len(c["mandates"]),
                          mandate_uids=[MandateRef(ci, mi, m["merchant"]).uid
                                        for mi, m in enumerate(c["mandates"])])
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

    _emit_run_end_unresolved(ctx, runtimes, T - 1)

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
        nudges_executed=cn["nudges_executed"],
        escalations_executed=cn["escalations_executed"],
        reminders=cn["reminders"],
        backup_links=cn["backup_links"],
        backup_paid=cn["backup_paid"],
        backup_expired=cn["backup_expired"],
        agent_stops=cn["agent_stops"],
        paused_dispatch=cn["paused_dispatch"],
        paused_decisions=cn["paused_decisions"],
        tech_declines=cn["tech_declines"],
        tech_updates_suppressed=cn["tech_updates_suppressed"],
        attempts_wasted_on_tech=cn["attempts_wasted_on_tech"],
        risk_retry=cn["risk_retry"],
        risk_terminal=cn["risk_terminal"],
        n_unresolved=cn["n_unresolved"],
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
