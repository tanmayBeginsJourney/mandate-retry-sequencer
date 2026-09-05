"""The recovery ladder, on the live rail, end to end.

WHAT THE LADDER IS. `agent/recovery.py` states it as pure predicates and both
composition roots ask it the same questions:

    attempts 1 and 2 fail on funds  ->  a funding reminder
    attempt 3 fails on funds        ->  a Payment Link REPLACES attempt 4
    while that link is open         ->  the fourth mandate debit is held
    the link is paid                ->  the cycle is collected
    the link closes unpaid          ->  the cycle is forfeited, the mandate lives

The last two lines are the point. NPCI allows four presentations per cycle and
a mandate that fails all four is dead, forfeiting every remaining billing
cycle. So the fourth debit is the expensive one, and spending it on an account
that has already declined three times buys very little and costs the customer
the subscription.

WHAT THIS GATE WAS WRITTEN FOR. None of it ran here. The ladder was built, the
gate exposed it and the executor implemented it, and `live/service.py` answered
every non-RETRY intervention with `record_non_money`, which writes a log line
and stops. Worse, it RETURNED on that answer, so a single funds decline
cancelled every remaining attempt in the cycle -- and since the decline history
was read across cycle boundaries, the next cycle opened already believing it
had just been declined. Measured on the mock rail before the repair, one
mandate declining on funds throughout spent 2 presentations in its first cycle,
1 in its second and 0 in its third, sent no reminder and created no link.

The mutants in W2 and W6 restore each of those two behaviours on its own, so a
green W1 cannot be green for some other reason.
"""
from __future__ import annotations

import live.tests  # noqa: F401  -- puts the package root on the path
from agent.execution.razorpay_mock import MockPlan, MockRazorpayApi
from agent.policy import timing
from agent.ports import InterventionKind
from live.domain import ATTEMPT_PRESENTED, AttemptState
from live.service import LiveService
from live.tests._harness import WEBHOOK_SECRET, Bench, Results

FUNDS = "failed:insufficient_funds"

#: The ceiling on one debit, raised for this gate only. `DEFAULT_MAX_DEBIT_PAISE`
#: is 500 -- a Rs 5 limit on one LIVE debit -- and the ladder needs an amount a
#: person would recognise. Nothing here can reach a rail that moves money.
ENV = {"RECOVERY_MAX_DEBIT_PAISE": "300000"}


def _drive(b: Bench, mandate_id: str, *, hours: int = 24 * 30, step: int = 4,
           until=None) -> list:
    """Advance the demonstration clock and tick, exactly as the console does."""
    out, spent = [], 0
    while spent <= hours:
        out.append(b.svc.decide(mandate_id))
        b.svc.deliver_mock_webhooks()
        if until is not None and until():
            return out
        b.svc.advance_clock(step)
        spent += step
    return out


def _presented(b: Bench, mandate_id: str, cycle: int | None = None) -> list:
    return [a for a in b.svc.store.attempts_for(mandate_id, limit=50)
            if a.state in ATTEMPT_PRESENTED
            and (cycle is None or a.cycle == cycle)]


def _wf(b: Bench, kind: str) -> list:
    """Workflow actions the executor actually performed, by kind."""
    return [row for row in b.svc.executor.workflow_log
            if row.get("kind") == kind]


def main() -> int:
    r = Results("THE RECOVERY LADDER  live/tests/test_ladder.py")

    # ===================================================================== W1
    r.section("W1  three debits, two reminders, a Payment Link, no fourth debit")
    b = Bench(plan=MockPlan(debits=[FUNDS] * 12), seed=11, env=ENV)
    try:
        _c, m = b.registered(charge_paise=189900, est_payday=2)
        _drive(b, m.id, hours=24 * 30,
               until=lambda: bool(b.svc.store.mandate(m.id).backup_status))
        mand = b.svc.store.mandate(m.id)
        cycle0 = _presented(b, m.id, cycle=0)

        r.ok("W1a  three presentations are spent in the cycle, not one",
             len(cycle0) == 3, f"{len(cycle0)} presented")
        r.ok("W1b  every one of them declined on funds",
             all(a.outcome_code == "Z9" for a in cycle0),
             str([a.outcome_code for a in cycle0]))
        r.ok("W1c  a funding reminder followed attempts 1 and 2",
             mand.reminders_sent == 2, f"reminders_sent {mand.reminders_sent}")
        r.ok("W1d  and the reminders reached the executor",
             len(_wf(b, "REMIND")) == 2, str(len(_wf(b, "REMIND"))))
        r.ok("W1e  a Payment Link was created at the provider",
             len(b.api._links) == 1 and mand.backup_status == "issued",
             f"{len(b.api._links)} links, status {mand.backup_status!r}")
        r.ok("W1f  and the mandate carries its id durably",
             bool(mand.backup_vendor_id), mand.backup_vendor_id)

        # THE CLAIM THE LADDER EXISTS FOR. Drive to the end of the cycle and
        # the fourth presentation must never happen.
        _drive(b, m.id, hours=24 * 12)
        cycle0 = _presented(b, m.id, cycle=0)
        r.ok("W1g  the fourth mandate debit is never fired",
             len(cycle0) == 3, f"{len(cycle0)} presented in cycle 0")
        r.ok("W1h  the mandate is alive and chargeable at the next cycle",
             b.svc.store.mandate(m.id).chargeable
             and b.svc.store.mandate(m.id).cycle > 0,
             f"cycle {b.svc.store.mandate(m.id).cycle}")
        last = b.svc.decide(m.id)
        r.ok("W1i  and the next cycle is not blocked by the old link",
             "backup checkout" not in last.reason, last.reason[:80])
    finally:
        b.close()

    # ===================================================================== W2
    r.section("W2  the mutant: let a non-RETRY intervention cancel the debit")
    b = Bench(plan=MockPlan(debits=[FUNDS] * 12), seed=11, env=ENV)
    try:
        _c, m = b.registered(charge_paise=189900, est_payday=2)
        # The pre-repair behaviour, and nothing else: every intervention that
        # is not RETRY stops the tick.
        b.svc._apply_intervention = lambda *a, **k: False
        _drive(b, m.id, hours=24 * 30)
        cycle0 = _presented(b, m.id, cycle=0)
        r.ok("W2a  the mutant spends fewer presentations",
             len(cycle0) < 3, f"{len(cycle0)} presented")
        r.ok("W2b  and no Payment Link is ever created",
             len(b.api._links) == 0, f"{len(b.api._links)} links")
        r.ok("W2c  and no reminder is ever sent",
             b.svc.store.mandate(m.id).reminders_sent == 0)
    finally:
        b.close()

    # ===================================================================== W3
    r.section("W3  a NUDGE sends a reminder AND the debit still runs")
    b = Bench(plan=MockPlan(debits=[FUNDS] * 12), seed=11, env=ENV)
    try:
        _c, m = b.registered(charge_paise=189900, est_payday=2)
        decisions = _drive(
            b, m.id, hours=24 * 30,
            until=lambda: len(_presented(b, m.id, cycle=0)) >= 2)
        nudges = [d for d in decisions if d.intervention == "NUDGE"]
        r.ok("W3a  the diagnoser chose NUDGE at least once", bool(nudges),
             str([d.intervention for d in decisions if d.intervention]))
        r.ok("W3b  a tick that chose NUDGE still scheduled the debit",
             any("pre-debit order created" in d.reason for d in nudges),
             str([d.reason[:40] for d in nudges][:3]))
        r.ok("W3c  so the cycle reaches a second presentation",
             len(_presented(b, m.id, cycle=0)) >= 2)
    finally:
        b.close()

    # ===================================================================== W4
    r.section("W4  each cycle gets its own Payment Link, never the last one's")
    b = Bench(plan=MockPlan(debits=[FUNDS] * 24), seed=11, env=ENV)
    try:
        _c, m = b.registered(charge_paise=189900, est_payday=2)
        ids: list[str] = []
        for _ in range(2):
            _drive(b, m.id, hours=24 * 40, until=lambda: bool(
                b.svc.store.mandate(m.id).backup_status)
                and b.svc.store.mandate(m.id).backup_vendor_id not in ids)
            vid = b.svc.store.mandate(m.id).backup_vendor_id
            if vid and vid not in ids:
                ids.append(vid)
            _drive(b, m.id, hours=24 * 12)      # into the next cycle
        r.ok("W4a  two cycles produced two different links", len(ids) == 2,
             str(ids))
        # THE DEFECT THIS CATCHES. `RazorpayExecutor` cached the link on the
        # mandate uid alone, so the second cycle replayed the first cycle's --
        # and had that link been paid, the new cycle would have been reported
        # collected with no money moving.
        r.ok("W4b  and the provider holds both", len(b.api._links) == len(ids),
             f"{len(b.api._links)} links at the provider")
    finally:
        b.close()

    # ===================================================================== W5
    r.section("W5  a paid link collects the cycle and stops the debit")
    b = Bench(plan=MockPlan(debits=[FUNDS] * 12), seed=11, env=ENV)
    try:
        _c, m = b.registered(charge_paise=189900, est_payday=2)
        _drive(b, m.id, hours=24 * 30,
               until=lambda: bool(b.svc.store.mandate(m.id).backup_status))
        vid = b.svc.store.mandate(m.id).backup_vendor_id
        # The customer paying the link. There is no endpoint for that on a real
        # rail either -- it happens on a phone -- so the mock's state is set
        # directly, exactly as `authorize` stands in for the UPI app.
        b.api._links[vid]["status"] = "paid"
        before = len(_presented(b, m.id, cycle=0))
        d = b.svc.decide(m.id)
        mand = b.svc.store.mandate(m.id)
        r.ok("W5a  the poll reads the payment", mand.backup_status == "paid",
             mand.backup_status)
        r.ok("W5b  the cycle reports collected", "collected" in d.reason,
             d.reason[:80])
        r.ok("W5c  and no further debit is submitted",
             len(_presented(b, m.id, cycle=0)) == before,
             f"{before} -> {len(_presented(b, m.id, cycle=0))}")
    finally:
        b.close()

    # ===================================================================== W6
    r.section("W6  the decline history is this cycle's, not the mandate's")
    b = Bench(plan=MockPlan(debits=[FUNDS] * 24), seed=11, env=ENV)
    try:
        _c, m = b.registered(charge_paise=189900, est_payday=2)
        _drive(b, m.id, hours=24 * 30,
               until=lambda: bool(b.svc.store.mandate(m.id).backup_status))
        _drive(b, m.id, hours=24 * 12,
               until=lambda: b.svc.store.mandate(m.id).cycle > 0)
        mand = b.svc.store.mandate(m.id)
        view = b.svc._case_view(mand, b.svc.store.customer(mand.customer_id),
                                0, b.svc.now_t() // 24)
        r.ok("W6a  a fresh cycle sees no decline from the last one",
             view.decline_history == (), str(view.decline_history))
        r.ok("W6b  and the ladder state resets with it",
             not mand.backup_status and mand.reminders_sent == 0,
             f"{mand.backup_status!r} / {mand.reminders_sent}")

        # THE MUTANT for W6a: read the history the way it used to be read.
        unfiltered = tuple(
            a.outcome_code for a
            in reversed(b.svc.store.attempts_for(mand.id, limit=10))
            if a.outcome_code and a.outcome_code != "OK")
        r.ok("W6c  reading it unfiltered would carry the old declines in",
             len(unfiltered) > 0, str(unfiltered))
    finally:
        b.close()

    # ===================================================================== W7
    r.section("W7  the ladder survives a restart")
    b = Bench(plan=MockPlan(debits=[FUNDS] * 12), seed=11, env=ENV)
    try:
        _c, m = b.registered(charge_paise=189900, est_payday=2)
        _drive(b, m.id, hours=24 * 30,
               until=lambda: bool(b.svc.store.mandate(m.id).backup_status))
        before = b.svc.store.mandate(m.id)
        b.svc.store.close()
        api2 = MockRazorpayApi(seed=11, plan=MockPlan(debits=[FUNDS] * 12))
        svc2 = LiveService(b.config, api=api2,
                           log_path=b.svc.log.path + ".restart")
        try:
            after = svc2.store.mandates()[0]
            r.ok("W7a  the link id is on disk",
                 after.backup_vendor_id == before.backup_vendor_id,
                 after.backup_vendor_id)
            r.ok("W7b  and so is its status and the reminder count",
                 after.backup_status == before.backup_status
                 and after.reminders_sent == before.reminders_sent,
                 f"{after.backup_status!r} / {after.reminders_sent}")
            # A restarted process must not fire the fourth debit either. The
            # mock has forgotten the link, so the poll cannot confirm it -- and
            # the ladder still holds, because failing open into a
            # mandate-killing attempt is the worse answer.
            d = svc2.decide(after.id)
            r.ok("W7c  the fourth debit is still held after the restart",
                 "backup checkout" in d.reason, d.reason[:80])
            r.ok("W7d  and no fourth presentation exists",
                 len([a for a in svc2.store.attempts_for(after.id, limit=50)
                      if a.state in ATTEMPT_PRESENTED and a.cycle == 0]) == 3)
        finally:
            svc2.store.close()
    finally:
        b.close()

    # ===================================================================== W8
    r.section("W8  no debit is created on the last day of the billing cycle")
    # A PROVIDER RULE THE SCHEDULER DOES NOT MODEL. Razorpay: "For UPI, do not
    # create subsequent payments on the last day of the cycle. This will cause
    # the payment to fail." [VERIFIED, "Create Subsequent Payments" for UPI,
    # read 5 September 2026]. `agent/policy/timing.py` bounds its window at
    # `dd < cycle_close`, so the last day IN the cycle is a legal target, and
    # this agent deliberately waits late in the cycle -- from day 28 of a
    # thirty-day cycle it targets day 29 for every payday estimate tried. The
    # live rail bounds against the rule and the simulator does not, which is
    # why this gate lives here and no published measurement moves.
    b = Bench(plan=MockPlan(debits=[FUNDS] * 12), seed=11, env=ENV)
    try:
        c, m = b.registered(charge_paise=189900, est_payday=2)
        # Move the clock to the day before the cycle closes. Nothing is ticked
        # on the way, so no attempt, reminder or link exists to reach the
        # scheduler through a different branch.
        b.svc.advance_clock(24 * 28)
        mand = b.svc.store.mandate(m.id)
        close = b.svc._cycle_close_day(mand)
        today = b.svc.now_t() // 24
        r.ok("W8a  the fixture stands one day short of the cycle close",
             today == close - 2, f"today {today}, cycle closes day {close}")

        # THE MUTANT FIRST: the scheduler on its own DOES target the last day,
        # so a refusal below is the guard and not an empty proposal set.
        b.svc._advance_to(c.seq, today)
        proposed = timing.propose(
            b.svc.book.belief_for(c.seq), mand.charge_amount_paise / 100.0,
            today, b.svc.now_t(), close, 0,
            kind=InterventionKind.RETRY, cycles_left=0)
        r.ok("W8b  without the guard the scheduler targets the last day",
             proposed.proposal is not None
             and proposed.proposal.target_t // 24 == close - 1,
             str(proposed.proposal and proposed.proposal.target_t // 24))

        d = b.svc.decide(m.id)
        r.ok("W8c  the live rail schedules no debit", not d.target_t,
             str(d.target_t))
        r.ok("W8d  and the refusal names the provider rule",
             "last day of the billing cycle" in d.reason
             and "Razorpay" in d.reason, d.reason[:130])
        r.ok("W8e  nothing is presented", not _presented(b, m.id),
             str(_presented(b, m.id)))
    finally:
        b.close()

    return r.summary()


if __name__ == "__main__":
    raise SystemExit(main())
