"""What survives the process dying, and what an unsent attempt costs.

THE DEFECT THIS GATE WAS WRITTEN FOR. A service restarted on its own database
wedged every mandate it held, permanently, and the console could not tell:
`state ACTIVE`, `chargeable true`, `blocked_because ""`. Three faults compounded
into one symptom.

  1. The demonstration clock's offset was in memory and its origin was on
     disk, so the simulated hour REWOUND on restart while every `target_t`
     already written stayed where the advanced clock had put it.
  2. `MockRazorpayApi` keeps tokens in a dict that dies with the process. The
     database still carried `rzp_token_id`, so the next pre-debit order was
     asked for against a token the rail had never heard of and came back
     "Token does not exist".
  3. `_schedule` writes the intent to disk BEFORE it calls the provider. The
     failure branch returned without resolving that row, INTENT is in
     `ATTEMPT_UNRESOLVED`, and the `open_now` guard in `_decide` then refused
     every later tick with "the outcome of the previous debit must be known".

Fault 3 is the one that made it permanent, and it is a defect on its own: no
request had left the process, so there was no outcome to be unknown.
`POST /api/reconcile` says so correctly -- "no provider request was made" --
and has nothing to fix.

WHAT IS NOT REPAIRED HERE, ON PURPOSE. `INTENT` stays in `ATTEMPT_UNRESOLVED`
and the `open_now` guard is untouched. That guard is what stops a second debit
while the outcome of a first is unknown, and Razorpay's own instruction
requires it. What changed is that an attempt with no request behind it stops
claiming to be one: it ends `NOTIFICATION_FAILED`, which is terminal, and which
`ATTEMPT_PRESENTED` excludes, so it spends none of NPCI's four presentations.
"""
from __future__ import annotations

import live.tests  # noqa: F401  -- puts the package root on the path
from agent.execution.razorpay_mock import MockPlan, MockRazorpayApi, sign
from live.domain import AttemptState
from live.service import CLOCK_OFFSET_KEY, LiveService
from live.tests._harness import WEBHOOK_SECRET, Bench, Results

FUNDS = "failed:insufficient_funds"

#: The ceiling on one debit, raised for the ladder sections only.
#: `DEFAULT_MAX_DEBIT_PAISE` is 500 -- a Rs 5 limit on one LIVE debit -- and a
#: Payment Link needs an amount a person would recognise. Nothing here can
#: reach a rail that moves money.
LADDER_ENV = {"RECOVERY_MAX_DEBIT_PAISE": "300000"}


def _deliver(svc: LiveService, api: MockRazorpayApi) -> None:
    """Post every queued webhook through the real verification path."""
    for event_id, _kind, body in api.drain_webhooks():
        raw, signature = sign(body, WEBHOOK_SECRET)
        svc.handle_webhook(raw.encode(), signature, event_id)
    svc.process_webhooks()


def _drive(svc: LiveService, api: MockRazorpayApi, mandate_id: str, *,
           hours: int = 24 * 45, step: int = 4, until=None) -> list:
    """Advance the demonstration clock and tick, exactly as the console does.

    `now` is pinned to the epoch origin so the simulated hour is the clock
    offset and nothing else. Real time passing during the gate then cannot
    move the answer.
    """
    out = []
    spent = 0
    while spent <= hours:
        out.append(svc.decide(mandate_id, now=svc.epoch_origin))
        _deliver(svc, api)
        if until is not None and until():
            return out
        svc.advance_clock(step)
        spent += step
    return out


def _collected(svc: LiveService, mandate_id: str) -> bool:
    return any(a.succeeded for a in svc.store.attempts_for(mandate_id))


def _states(svc: LiveService, mandate_id: str) -> list[str]:
    return [a.state.value for a in svc.store.attempts_for(mandate_id)]


def main() -> int:
    r = Results("RESTART AND UNSENT ATTEMPTS  live/tests/test_restart.py")

    # ------------------------------------------------------------------ R1
    r.section("R1  a service restarted on its own database keeps working")
    b = Bench(plan=MockPlan(debits=["captured"]), seed=11)
    try:
        _c, m = b.registered(charge_paise=100, est_payday=3)
        _drive(b.svc, b.api, m.id, until=lambda: _collected(b.svc, m.id))
        r.ok("R1a  the first cycle collects before the restart",
             _collected(b.svc, m.id), str(_states(b.svc, m.id)))

        before_t = b.svc.now_t(now=b.svc.epoch_origin)
        offset = b.svc.clock_offset_h
        r.ok("R1b  the advanced clock is on disk, not only in memory",
             b.svc.store.meta_get(CLOCK_OFFSET_KEY) == str(offset),
             f"stored {b.svc.store.meta_get(CLOCK_OFFSET_KEY)!r}, "
             f"in memory {offset}")

        # THE RESTART. A new service AND a new rail object: the process dying
        # takes the mock's token dictionary with it, which is the whole of
        # fault 2.
        b.svc.store.close()
        api2 = MockRazorpayApi(seed=11, plan=MockPlan(debits=["captured"]))
        svc2 = LiveService(b.config, api=api2,
                           log_path=b.svc.log.path + ".restart")
        try:
            after_t = svc2.now_t(now=svc2.epoch_origin)
            r.ok("R1c  the simulated hour does not go backwards",
                 after_t >= before_t, f"hour {before_t} -> hour {after_t}")
            r.ok("R1d  and does not silently jump forward either",
                 after_t == before_t, f"hour {before_t} -> hour {after_t}")

            mid = svc2.store.mandates()[0].id
            decisions = _drive(
                svc2, api2, mid, hours=24 * 45,
                until=lambda: any(
                    a.state in (AttemptState.ORDER_CREATED,
                                AttemptState.NOTIFIED,
                                AttemptState.SUBMITTED,
                                AttemptState.SUCCEEDED)
                    and a.cycle > 0
                    for a in svc2.store.attempts_for(mid)))
            reasons = " | ".join(d.reason for d in decisions)
            r.ok("R1e  the restarted rail still knows the token",
                 "Token does not exist" not in reasons,
                 reasons[-160:])
            r.ok("R1f  no mandate is wedged on an unresolved intent",
                 "is INTENT" not in reasons, reasons[-160:])
            r.ok("R1g  the next cycle reaches a pre-debit order",
                 any(a.cycle > 0 and a.state is not AttemptState.INTENT
                     for a in svc2.store.attempts_for(mid)),
                 str(_states(svc2, mid)))
        finally:
            svc2.store.close()
    finally:
        b.close()

    # ------------------------------------------------------------------ R2
    r.section("R2  a token the mock has forgotten is re-adopted, and only "
              "for the mock")
    b = Bench(seed=11)
    try:
        _c, m = b.registered(charge_paise=100)
        token = b.svc.store.mandates()[0].rzp_token_id
        fresh = MockRazorpayApi(seed=11)
        r.ok("R2a  a fresh mock has never heard of the token",
             token not in fresh._tokens)
        svc2 = LiveService(b.config, api=fresh,
                           log_path=b.svc.log.path + ".adopt")
        try:
            r.ok("R2b  building a service on the same database re-adopts it",
                 token in fresh._tokens)
            r.ok("R2c  and re-adopts it as confirmed",
                 fresh._tokens[token]["recurring_details"]["status"]
                 == "confirmed")
            r.ok("R2d  with the mandate's own ceiling, not a default",
                 fresh._tokens[token]["max_amount"]
                 == b.svc.store.mandates()[0].max_amount_paise)
            # THE MUTANT for R2b. `_readopt_mock_token` is guarded on the
            # concrete class, so an api that is not the mock must be left
            # alone -- a real rail has no such method and calling one would be
            # an AttributeError on the money path.
            fresh._tokens[token]["recurring_details"]["status"] = "paused"
            svc2.refresh()
            r.ok("R2e  a token whose status has moved on is not overwritten",
                 fresh._tokens[token]["recurring_details"]["status"]
                 == "paused")
        finally:
            svc2.store.close()
    finally:
        b.close()

    # ------------------------------------------------------------------ R2b
    r.section("R2b  a pre-debit order outstanding at the restart is still "
              "chargeable")
    b = Bench(plan=MockPlan(debits=["captured"]), seed=11)
    try:
        _c, m = b.registered(charge_paise=100, est_payday=3)
        _drive(b.svc, b.api, m.id, until=lambda: any(
            a.state in (AttemptState.ORDER_CREATED, AttemptState.NOTIFIED)
            for a in b.svc.store.attempts_for(m.id)))
        pending = [a for a in b.svc.store.attempts_for(m.id) if not a.resolved]
        r.ok("R2b1 the drive stops with an order outstanding",
             len(pending) == 1 and bool(pending[0].order_id),
             str(_states(b.svc, m.id)))

        b.svc.store.close()
        api3 = MockRazorpayApi(seed=11, plan=MockPlan(debits=["captured"]))
        svc3 = LiveService(b.config, api=api3,
                           log_path=b.svc.log.path + ".order")
        try:
            mid = svc3.store.mandates()[0].id
            decisions = _drive(svc3, api3, mid,
                               until=lambda: _collected(svc3, mid))
            reasons = " | ".join(d.reason for d in decisions)
            # WITHOUT `adopt_order` the debit is refused with "Order does not
            # exist". The executor writes SUBMITTING before the request leaves,
            # so that refusal is indistinguishable from a lost response and the
            # attempt ends UNKNOWN -- unresolved, never auto-retried, and
            # waiting on a reconciliation the mock cannot answer either.
            r.ok("R2b2 the restarted rail still knows the order",
                 "Order does not exist" not in reasons, reasons[-160:])
            r.ok("R2b3 the scheduled debit is not stranded in UNKNOWN",
                 AttemptState.UNKNOWN.value not in _states(svc3, mid),
                 str(_states(svc3, mid)))
            r.ok("R2b4 and it collects", _collected(svc3, mid),
                 str(_states(svc3, mid)))
        finally:
            svc3.store.close()
    finally:
        b.close()

    # ------------------------------------------------------------------ R3
    r.section("R3  an attempt whose order was never created resolves, "
              "spends nothing, and does not wedge the mandate")
    b = Bench(seed=11)
    try:
        _c, m = b.registered(charge_paise=100, est_payday=3)
        # Set AFTER registration: the registration order goes through
        # `_new_order` too, and failing it would test a different thing.
        b.api.plan.order_failures = 1
        _drive(b.svc, b.api, m.id, hours=24 * 40,
               until=lambda: any(
                   a.state is AttemptState.NOTIFICATION_FAILED
                   for a in b.svc.store.attempts_for(m.id)))
        attempts = b.svc.store.attempts_for(m.id)
        failed = [a for a in attempts
                  if a.state is AttemptState.NOTIFICATION_FAILED]
        r.ok("R3a  the attempt ends NOTIFICATION_FAILED, not INTENT",
             len(failed) == 1, str(_states(b.svc, m.id)))
        r.ok("R3b  no row is left in INTENT",
             AttemptState.INTENT.value not in _states(b.svc, m.id),
             str(_states(b.svc, m.id)))

        mand = b.svc.store.mandate(m.id)
        used = LiveService._attempts_this_cycle(mand, attempts)
        r.ok("R3c  it spends none of NPCI's four presentations", used == 0,
             f"attempts_this_cycle {used}")

        # THE POINT OF THE GATE. Before the repair every tick from here on
        # answered "attempt ... is INTENT" and the mandate never recovered.
        onward = _drive(b.svc, b.api, m.id, hours=24 * 40,
                        until=lambda: _collected(b.svc, m.id))
        onward_reasons = " | ".join(d.reason for d in onward)
        r.ok("R3d  the next tick proceeds instead of reporting an intent",
             "is INTENT" not in onward_reasons, onward_reasons[-160:])
        r.ok("R3e  and the mandate goes on to collect",
             _collected(b.svc, m.id), str(_states(b.svc, m.id)))
        r.ok("R3f  the transition is on the audit trail, sourced to the "
             "scheduler",
             any(t["source"] == "schedule"
                 and t["to_state"] == AttemptState.NOTIFICATION_FAILED.value
                 for t in b.svc.store.transitions_for("attempt",
                                                      failed[0].id)))
    finally:
        b.close()

    # ------------------------------------------------------------------ R4
    r.section("R4  the mutant: leave the intent unresolved and the mandate "
              "wedges again")
    b = Bench(seed=11)
    try:
        _c, m = b.registered(charge_paise=100, est_payday=3)
        b.api.plan.order_failures = 1
        # Restore the pre-repair behaviour and nothing else: write the
        # transition nowhere and leave the row where `_schedule` put it. If
        # this does NOT wedge, R3d is passing for a reason other than the
        # repair.
        b.svc._abandon_intent = lambda attempt, detail: attempt.state.value
        reasons = " | ".join(
            d.reason for d in _drive(b.svc, b.api, m.id, hours=24 * 40))
        r.ok("R4a  the mutant strands a row in INTENT",
             AttemptState.INTENT.value in _states(b.svc, m.id),
             str(_states(b.svc, m.id)))
        r.ok("R4b  and every later tick refuses on it",
             "is INTENT" in reasons, reasons[-160:])
        r.ok("R4c  and nothing is ever collected",
             not _collected(b.svc, m.id), str(_states(b.svc, m.id)))
    finally:
        b.close()

    # ------------------------------------------------------------------ R5
    r.section("R5  a Payment Link paid after a restart is still detected")
    # THE DEFECT. `backup_vendor_id` and `backup_status` are durable columns,
    # but the executor's map from mandate to link id is a dictionary that dies
    # with the process. A restarted service could not name the link, so
    # `fetch_backup` answered "no backup link id", the status stayed wherever
    # the last process left it, and a link the customer PAID after the restart
    # was never read. The cycle was never marked collected and the merchant's
    # books said uncollected on money that had arrived. It fails safe -- no
    # second debit -- and it misreports revenue.
    #
    # The rail keeps the SAME api object across the restart, because that is
    # what a real provider does: Razorpay remembers its own Payment Link. What
    # was forgotten is this side of the mapping, and that is what is repaired.
    b = Bench(plan=MockPlan(debits=[FUNDS] * 12), seed=11, env=LADDER_ENV)
    try:
        _c, m = b.registered(charge_paise=189900, est_payday=2)
        _drive(b.svc, b.api, m.id, hours=24 * 30,
               until=lambda: bool(b.svc.store.mandate(m.id).backup_status))
        before = b.svc.store.mandate(m.id)
        r.ok("R5a  the first process issued a link",
             before.backup_status == "issued" and bool(before.backup_vendor_id),
             f"{before.backup_status!r} / {before.backup_vendor_id}")

        b.svc.store.close()
        svc2 = LiveService(b.config, api=b.api,
                           log_path=b.svc.log.path + ".restart")
        try:
            after = svc2.store.mandates()[0]
            uid = svc2._ref(after,
                            svc2.store.customer(after.customer_id)).uid
            r.ok("R5b  the restarted executor can name the link again",
                 svc2.executor._backup_ids.get(uid, ("", ""))[1]
                 == before.backup_vendor_id,
                 str(svc2.executor._backup_ids))

            # The customer paying. There is no endpoint for that on a real rail
            # either -- it happens on a phone -- so the mock's state is set
            # directly, exactly as `authorize` stands in for the UPI app.
            b.api._links[before.backup_vendor_id]["status"] = "paid"
            d = svc2.decide(after.id, now=svc2.epoch_origin)
            mand = svc2.store.mandate(after.id)
            r.ok("R5c  the poll reads the payment made after the restart",
                 mand.backup_status == "paid", mand.backup_status)
            r.ok("R5d  and the cycle reports collected",
                 "collected" in d.reason, d.reason[:80])
        finally:
            svc2.store.close()
    finally:
        b.close()

    # ------------------------------------------------------------------ R6
    r.section("R6  the mutant: forget the link id and the payment is missed")
    b = Bench(plan=MockPlan(debits=[FUNDS] * 12), seed=11, env=LADDER_ENV)
    try:
        _c, m = b.registered(charge_paise=189900, est_payday=2)
        _drive(b.svc, b.api, m.id, hours=24 * 30,
               until=lambda: bool(b.svc.store.mandate(m.id).backup_status))
        vid = b.svc.store.mandate(m.id).backup_vendor_id
        b.svc.store.close()
        svc2 = LiveService(b.config, api=b.api,
                           log_path=b.svc.log.path + ".mutant")
        try:
            # Restore the pre-repair state and nothing else: the restarted
            # executor holds no id for the link it inherited.
            svc2.executor._backup_ids.clear()
            b.api._links[vid]["status"] = "paid"
            mid = svc2.store.mandates()[0].id
            svc2.decide(mid, now=svc2.epoch_origin)
            mand = svc2.store.mandate(mid)
            r.ok("R6a  without the adoption the paid link is never read",
                 mand.backup_status != "paid", mand.backup_status)
            r.ok("R6b  so the cycle is reported uncollected on money that "
                 "arrived",
                 not _collected(svc2, mid), str(_states(svc2, mid)))
        finally:
            svc2.store.close()
    finally:
        b.close()

    return r.summary()


if __name__ == "__main__":
    raise SystemExit(main())
