"""F-gates: the whole lifecycle against the mock rail, including the crashes.

The interesting gates here are the ones where the process dies in the middle.
A payment system's correctness is mostly a claim about what happens when it
stops running at the worst moment, and that claim is cheap to make and easy to
get wrong.

Crash boundaries, and what each one leaves behind:

  A  before the intent is written        nothing. Re-deciding is safe.
  B  after intent, before the order      an INTENT row and no provider state.
  C  after the order, before recording   an order at Razorpay we have no id
                                         for. Recovered by receipt.
  D  after the order, before the debit   a scheduled attempt. Resumes.
  E  during the debit, response lost     UNKNOWN. May have taken the money.
  E' during the debit, request re-sent   the provider refuses it; UNKNOWN, never
                                         FAILED, resolved from the order.
  F  after the debit, before the webhook SUBMITTED. Resolved by polling.
  G  after the webhook, before applying  a durable event, replayed at startup.
"""
from __future__ import annotations

import live.tests  # noqa: F401
from agent.execution.razorpay_api import RazorpayApi
from agent.execution.razorpay_mock import MockPlan, MockRazorpayApi
from agent.execution.razorpay_executor import RazorpayError
from live.domain import AttemptState, MandateState
from live.service import LiveService
from live.tests._harness import Bench, Results
from live.webhooks import process_pending


def _hammer(method: str, threads: int = 12) -> dict:
    """Drive one mandate to a scheduled debit, then fire `threads` ticks at it.

    Returns what the run left behind. `method` is `decide` or `_decide`, so the
    same load can be put through the locked and unlocked paths.
    """
    import threading as _t
    b = Bench(plan=MockPlan(debits=["captured"] * 10), seed=11)
    try:
        c, m = b.registered()
        base = b.svc.epoch_origin
        target = None
        for hour in range(0, 24 * 20, 4):
            b.svc.decide(m.id, now=base + hour * 3600)
            b.deliver()
            sched = [a for a in b.svc.store.attempts_for(m.id)
                     if a.state in (AttemptState.ORDER_CREATED,
                                    AttemptState.NOTIFIED)]
            if sched:
                target = sched[0]
                break
        if target is None:
            return {"errors": ["nothing scheduled"], "attempts": 0,
                    "debits": 0, "raw": ""}
        when = base + (target.target_t + 1) * 3600
        fn = getattr(b.svc, method)
        barrier = _t.Barrier(threads)
        errors: list[str] = []

        def tick():
            barrier.wait()          # start together, to widen the window
            try:
                fn(m.id, now=when)
            except Exception as e:                      # noqa: BLE001
                errors.append(type(e).__name__)

        workers = [_t.Thread(target=tick) for _ in range(threads)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        attempts = b.svc.store.attempts_for(m.id)
        # The registration authorisation is also a payment at the provider, so
        # the debits are the ones carrying an order that is not the
        # registration order.
        debits = sum(1 for p in b.api._payments.values()
                     if p.get("order_id") != m.registration_order_id)
        return {"errors": errors, "attempts": len(attempts), "debits": debits,
                "raw": attempts[0].raw_reason if attempts else ""}
    finally:
        b.close()


def _public(cls) -> set[str]:
    return {n for n in dir(cls)
            if not n.startswith("_") and callable(getattr(cls, n, None))}


def main() -> int:
    r = Results("LIVE FLOW GATES (offline, mock rail)")

    # ------------------------------------------------------------------ F1
    r.section("F1  the mock implements the whole provider surface")
    missing = sorted(_public(RazorpayApi) - _public(MockRazorpayApi))
    r.ok("F1a  no method of RazorpayApi is missing from the mock",
         not missing, str(missing))
    # The mock's extra methods are the things a real payment API has no
    # endpoint for: a customer approving a mandate on their phone, the rail
    # settling a payment, a customer pausing a token, and the queue standing in
    # for Razorpay's webhook sender. Pinning the list means a new one has to be
    # justified rather than appearing.
    extra = sorted(_public(MockRazorpayApi) - _public(RazorpayApi))
    r.ok("F1b  the mock adds only what a real API has no endpoint for",
         extra == ["authorize", "drain_webhooks", "set_token_status", "settle"],
         str(extra))

    # ------------------------------------------------------------------ F2
    r.section("F2  registration reaches ACTIVE only on a confirmed token")
    b = Bench()
    try:
        c = b.svc.create_customer(name="Alpha Customer", email="a@example.com",
                                  contact="+919000000001")
        m = b.svc.start_registration(customer_id=c.id, charge_amount_paise=100,
                                     max_amount_paise=150000)
        r.ok("F2a  a fresh mandate is PENDING, not ACTIVE",
             m.state is MandateState.PENDING)
        r.ok("F2b  an order existing is not authorisation",
             bool(m.registration_order_id) and not m.chargeable)
        auth = b.api.authorize(m.registration_order_id)
        m = b.svc.confirm_registration(m.id, auth.body["payment_id"])
        r.ok("F2c  a confirmed token makes it ACTIVE",
             m.state is MandateState.ACTIVE and m.chargeable)
        r.ok("F2d  the provider's own word is kept beside our state",
             m.token_status == "confirmed")
    finally:
        b.close()

    b = Bench(plan=MockPlan(token_status="rejected"))
    try:
        c = b.svc.create_customer(name="Bravo Customer", email="b@example.com",
                                  contact="+919000000002")
        m = b.svc.start_registration(customer_id=c.id, charge_amount_paise=100,
                                     max_amount_paise=150000)
        auth = b.api.authorize(m.registration_order_id)
        m = b.svc.confirm_registration(m.id, auth.body["payment_id"])
        r.ok("F2e  a rejected token is REJECTED and cannot be charged",
             m.state is MandateState.REJECTED and not m.chargeable)
        d = b.svc.decide(m.id)
        r.ok("F2f  deciding on it refuses with a reason, and submits nothing",
             d.acted is False and "REJECTED" in d.reason, d.reason)
    finally:
        b.close()

    # ------------------------------------------------------------------ F3
    r.section("F3  the happy path walks the full state machine")
    b = Bench(plan=MockPlan(debits=["captured"] * 8), seed=11)
    try:
        c, m = b.registered()
        b.run_until_resolved(m.id)
        collected_at = len(b.svc.store.attempts_for(m.id))
        # KEEP TICKING PAST THE COLLECTION, to the end of the billing cycle.
        # `run_until_resolved` returns the moment an attempt resolves, and a
        # driver that stops there cannot see a second charge in the same cycle
        # -- which is where a repeat collection lives. The debit plan captures
        # every time, so any further submission is another real collection.
        base = b.svc.epoch_origin
        close_h = (m.cycle_start_t // 24 + m.cycle_days) * 24
        for hour in range(0, close_h, 2):
            b.svc.decide(m.id, now=base + hour * 3600)
            b.deliver()
        attempts = [a for a in b.svc.store.attempts_for(m.id)
                    if a.cycle == 0]
        r.ok("F3a  exactly one attempt in the cycle, ticking to its close",
             len(attempts) == 1,
             f"{len(attempts)} attempts, {collected_at} at first resolution")
        if attempts:
            a = attempts[0]
            states = [t["to_state"] for t
                      in b.svc.store.transitions_for("attempt", a.id)
                      if t["verdict"] == "APPLIED"]
            r.ok("F3b  it reached SUCCEEDED",
                 a.state is AttemptState.SUCCEEDED, a.state.value)
            # SUBMITTING sits between the notice and the acknowledgement: it is
            # written before the request leaves, so a crash in the request is
            # recoverable. See live/domain.py.
            r.ok("F3c  through INTENT, ORDER_CREATED, NOTIFIED, SUBMITTING, "
                 "SUBMITTED",
                 states[:5] == ["INTENT", "ORDER_CREATED", "NOTIFIED",
                                "SUBMITTING", "SUBMITTED"], str(states))
            r.ok("F3d  the pre-debit order was created before the charge",
                 bool(a.order_id) and bool(a.payment_id))
            r.ok("F3e  the collected amount is counted once",
                 b.svc.store.summary()["recovered_paise"] == a.amount_paise,
                 f"{b.svc.store.summary()['recovered_paise']} paise")
    finally:
        b.close()

    # ------------------------------------------------------------------ F4
    r.section("F4  crash boundary E: a lost response is never a decline")
    b = Bench(plan=MockPlan(debits=["lost"]), seed=11)
    try:
        c, m = b.registered()
        b.run_until_resolved(m.id, max_hours=24 * 25)
        attempts = b.svc.store.attempts_for(m.id)
        if not attempts:
            r.ok("F4   SKIPPED -- the scheduler did not fire", False)
        else:
            a = attempts[0]
            r.ok("F4a  the attempt is UNKNOWN, not FAILED",
                 a.state is AttemptState.UNKNOWN, a.state.value)
            r.ok("F4b  it is not counted as recovered",
                 b.svc.store.summary()["recovered_paise"] == 0)
            # Deciding again must NOT submit a second debit: we do not know
            # whether the first one took the customer's money.
            before = len(b.svc.store.attempts_for(m.id))
            d = b.svc.decide(m.id, now=b.svc.epoch_origin
                             + (a.target_t + 48) * 3600)
            r.ok("F4c  a second debit is refused while the first is unknown",
                 d.acted is False
                 and len(b.svc.store.attempts_for(m.id)) == before,
                 d.reason[:70])
            # Reconciliation asks the ORDER which payments it has, because
            # the payment id is the one thing a lost response did not give us.
            b.svc.reconcile()
            after = b.svc.store.attempt(a.id)
            r.ok("F4d  reconciliation learns the payment id we never received",
                 bool(after.payment_id), after.payment_id or "<none>")
            r.ok("F4e  an unfinished payment does not move it off UNKNOWN",
                 after.state is AttemptState.UNKNOWN, after.state.value)
            # Now the rail makes up its mind, which is what eventually happens.
            b.api.settle(after.payment_id, "captured")
            b.svc.reconcile()
            settled = b.svc.store.attempt(a.id)
            r.ok("F4f  once the provider has an answer, it is applied",
                 settled.state is AttemptState.SUCCEEDED, settled.state.value)
            r.ok("F4g  and the money is counted exactly once",
                 b.svc.store.summary()["recovered_paise"] == a.amount_paise)
    finally:
        b.close()

    # ----------------------------------------------------------------- F4b
    r.section("F4b crash boundary D/E: the request went out, the process died "
              "before the row moved")
    # The worst boundary on the money path. `create_recurring_payment` reached
    # Razorpay and the process stopped before the acknowledgement was written,
    # so the local row still says NOTIFIED and the debit may already have
    # collected. Resuming re-submits against the same order, the provider
    # refuses it because the order is `paid`, and the only correct reading of
    # that refusal is "we do not know", never "it failed".
    b = Bench(plan=MockPlan(debits=["captured"]), seed=11)
    try:
        c, m = b.registered()
        base = b.svc.epoch_origin
        target = None
        for hour in range(0, 24 * 20, 4):
            b.svc.decide(m.id, now=base + hour * 3600)
            b.deliver()
            sched = [a for a in b.svc.store.attempts_for(m.id)
                     if a.state in (AttemptState.ORDER_CREATED,
                                    AttemptState.NOTIFIED)]
            if sched:
                target = sched[0]
                break
        if target is None:
            r.ok("F4b  SKIPPED -- nothing was scheduled", False)
        else:
            # The provider hears the debit; we never do.
            b.api.create_recurring_payment(
                email=c.email, contact=c.contact,
                amount_paise=target.amount_paise, order_id=target.order_id,
                customer_id=m.rzp_customer_id, token_id=m.rzp_token_id,
                description="crash")
            b.api.drain_webhooks()          # the acknowledgement is lost too
            debits_before = sum(1 for p in b.api._payments.values()
                                if p.get("order_id") == target.order_id)
            svc2 = LiveService(b.config, api=b.api,
                               log_path=b.svc.log.path + ".crashD")
            d = svc2.decide(m.id, now=base + (target.target_t + 1) * 3600)
            after = svc2.store.attempt(target.id)
            debits_after = sum(1 for p in b.api._payments.values()
                               if p.get("order_id") == target.order_id)
            r.ok("F4b1 the provider refused the second debit on the same order",
                 debits_after == debits_before,
                 f"{debits_after - debits_before} extra payments")
            r.ok("F4b2 the attempt is UNKNOWN, never FAILED",
                 after.state is AttemptState.UNKNOWN, after.state.value)
            r.ok("F4b3 so the cycle is not reported as uncollected",
                 not after.resolved and d.acted is False, d.reason[:60])
            svc2.reconcile()
            settled = svc2.store.attempt(target.id)
            r.ok("F4b4 reconciliation finds the payment that did collect",
                 settled.state is AttemptState.SUCCEEDED, settled.state.value)
            r.ok("F4b5 and the money is counted exactly once",
                 svc2.store.summary()["recovered_paise"] == target.amount_paise,
                 str(svc2.store.summary()["recovered_paise"]))
            # THE EXECUTOR'S OWN PRECONDITION, not the service's. The journal
            # is rebuilt from the row, so a row past SUBMITTED must report a
            # phase `attempt()` refuses -- otherwise the last line of defence
            # is disarmed by a restart.
            from agent.ports import MandateRef
            ref = MandateRef(c.seq, m.index_no, m.merchant_id)
            try:
                raw = svc2.executor.attempt(ref, settled.amount_paise / 100.0,
                                            settled.target_t,
                                            action_id="f4b").raw_code
            except RazorpayError as e:
                # It reached the network. The journal reported a phase that
                # `attempt()` accepts for a row that is already resolved.
                raw = f"REACHED THE PROVIDER: {type(e).__name__}"
            r.ok("F4b6 MUTANT: the executor itself refuses a resolved attempt",
                 raw.startswith("invalid_predelivery_phase"), raw[:70])
            svc2.store.close()
    finally:
        b.close()

    # ------------------------------------------------------------------ F5
    r.section("F5  crash boundary C: an order created but not recorded")
    b = Bench(seed=11)
    try:
        c, m = b.registered()
        # Drive to a scheduled attempt, then forget the order id the way a
        # crash between the provider's 200 and our commit would.
        b.run_until_resolved(m.id, max_hours=24 * 20)
        scheduled = [a for a in b.svc.store.attempts_for(m.id) if a.order_id]
        if not scheduled:
            r.ok("F5   SKIPPED -- nothing was scheduled", False)
        else:
            a = scheduled[0]
            orders_before = len(b.api._orders)
            # The receipt is deterministic, so asking again finds the order
            # that already exists instead of making a second one.
            found = b.api.find_order_by_receipt(a.receipt)
            r.ok("F5a  the order can be found by its deterministic receipt",
                 found.ok and found.body.get("count") == 1,
                 str(found.body.get("count")))
            dup = b.api.create_notification_order(
                amount_paise=a.amount_paise, receipt=a.receipt,
                token_id=m.rzp_token_id, payment_after=a.payment_after)
            r.ok("F5b  re-creating it with the same receipt is REFUSED",
                 not dup.ok and "already exists" in dup.error_description,
                 dup.error_description[:60])
            r.ok("F5c  so no second order exists for one debit",
                 len(b.api._orders) == orders_before)
    finally:
        b.close()

    # ------------------------------------------------------------------ F6
    r.section("F6  crash boundary G: an accepted event, never interpreted")
    b = Bench(plan=MockPlan(debits=["captured"]), seed=11)
    try:
        c, m = b.registered()
        # Ingest everything but never process it -- the process died between
        # returning 200 to Razorpay and acting on what it had accepted.
        base = b.svc.epoch_origin
        from agent.execution.razorpay_mock import sign
        from live.tests._harness import WEBHOOK_SECRET
        for hour in range(0, 24 * 25, 4):
            b.svc.decide(m.id, now=base + hour * 3600)
            for event_id, _kind, body in b.api.drain_webhooks():
                raw, signature = sign(body, WEBHOOK_SECRET)
                b.svc.handle_webhook(raw.encode(), signature, event_id)
        pending = b.svc.store.unprocessed_events()
        r.ok("F6a  the events are durable and queued",
             len(pending) > 0, f"{len(pending)} unprocessed")
        # Startup replay, which is what `live/server.py` runs before binding.
        replayed = process_pending(b.svc.store)
        r.ok("F6b  startup replays every one of them",
             len(replayed) == len(pending))
        r.ok("F6c  and none is left unprocessed",
             not b.svc.store.unprocessed_events())
        # Replaying AGAIN must change nothing: every write is monotonic.
        counts = b.svc.store.summary()
        process_pending(b.svc.store)
        r.ok("F6d  a second replay is a no-op",
             b.svc.store.summary() == counts)
    finally:
        b.close()

    # ------------------------------------------------------------------ F7
    r.section("F7  a restart rebuilds enough state to finish a debit")
    b = Bench(seed=11)
    try:
        c, m = b.registered()
        base = b.svc.epoch_origin
        target = None
        for hour in range(0, 24 * 20, 4):
            d = b.svc.decide(m.id, now=base + hour * 3600)
            b.deliver()
            sched = [a for a in b.svc.store.attempts_for(m.id)
                     if a.state in (AttemptState.ORDER_CREATED,
                                    AttemptState.NOTIFIED)]
            if sched:
                target = sched[0]
                break
        if target is None:
            r.ok("F7   SKIPPED -- nothing was scheduled", False)
        else:
            # A NEW SERVICE over the SAME database and the same rail. The
            # in-memory ledger, the belief book and the executor journal are
            # all gone; only the store survives.
            svc2 = LiveService(b.config, api=b.api,
                               log_path=b.svc.log.path + ".restart")
            r.ok("F7a  the clock origin survives the restart",
                 svc2.epoch_origin == b.svc.epoch_origin,
                 "an origin that moved would redefine every stored hour")
            d = svc2.decide(m.id, now=base + (target.target_t + 1) * 3600)
            r.ok("F7b  the scheduled debit is submitted after the restart",
                 d.acted is True, d.reason[:70])
            r.ok("F7c  Stage 0 did not refuse for a lost notification",
                 d.refused_rule != "pending",
                 f"refused_rule={d.refused_rule!r}")
            r.ok("F7d  no second attempt was created",
                 len(svc2.store.attempts_for(m.id)) == 1,
                 f"{len(svc2.store.attempts_for(m.id))} attempts")
            svc2.store.close()
    finally:
        b.close()

    # ----------------------------------------------------------------- F7b
    r.section("F7b rebuilding the gate's ledger is idempotent")
    b = Bench(seed=11)
    try:
        c, m = b.registered()
        ref = b.svc._ref(m, c)
        b.run_until_resolved(m.id)
        stored = len(b.svc.store.attempts_for(m.id))
        first = b.svc.ledger.attempts(ref.uid, m.cycle)
        # `refresh()` runs on every mandate change. Replaying the attempt log
        # into a counter without zeroing it first turns one attempt into four,
        # which is the NPCI cap -- so the mandate silently stops being
        # chargeable for the rest of its cycle.
        for _ in range(5):
            b.svc.refresh()
        after = b.svc.ledger.attempts(ref.uid, m.cycle)
        r.ok("F7b1 the ledger matches the store", first == stored,
             f"ledger {first}, store {stored}")
        r.ok("F7b2 and five rebuilds do not change it", after == first,
             f"{first} -> {after}")
        r.ok("F7b3 so the cap is not reached by bookkeeping alone", after < 4,
             f"{after} of 4")
    finally:
        b.close()

    # ---------------------------------------------------------------- F7c
    r.section("F7c concurrent ticks on one mandate are serialised")
    # The money path reads state, decides on it and writes it back. Two
    # threads interleaving there is how one debit becomes two requests against
    # one order -- and how a SQLite connection gets used from two places at
    # once. `decide` takes a per-mandate lock; `_decide` is the same work
    # without it, and running both is what makes this gate non-vacuous rather
    # than a test that passes because the GIL happened to help.
    locked = _hammer("decide")
    # The race is probabilistic: thread interleaving is not something a test
    # can command, and on a quiet machine the unlocked path sometimes gets
    # away with it. Asserting "it trips at least once in five" is the stable
    # formulation. Asserting it trips every time would be a gate that goes red
    # for reasons unrelated to the code, which teaches people to re-run.
    unlocked = _hammer("_decide")
    r.ok("F7c1 the locked path raises nothing",
         locked["errors"] == [], str(locked["errors"][:3]))
    r.ok("F7c2 it leaves exactly one attempt",
         locked["attempts"] == 1, f"{locked['attempts']} attempts")
    r.ok("F7c3 and exactly one debit at the provider",
         locked["debits"] == 1, f"{locked['debits']} debit payments")
    r.ok("F7c4 the attempt is not falsely marked failed",
         locked["raw"] != "request_refused", locked["raw"] or "(none)")
    # The lock is no longer the only thing holding this invariant up, and the
    # gate says so rather than implying otherwise. The executor writes
    # SUBMITTING before the request leaves and the store refuses a backwards
    # transition, so twelve unsynchronised ticks converge on one debit too.
    # THE MUTANT FOR THE RULE THAT DOES CARRY IT -- journalling after the
    # request instead of before -- is B2g/B2h in test_regressions.py, where it
    # produces a second debit. Asserting an exception here would be a gate
    # nothing can trip.
    r.ok("F7c5 the unsynchronised path also lands on one debit",
         unlocked["attempts"] == 1 and unlocked["debits"] == 1,
         f"{unlocked['attempts']} attempts, {unlocked['debits']} debits, "
         f"errors={sorted(set(unlocked['errors']))[:3]}")

    # ------------------------------------------------------------------ F8
    r.section("F8  a cancelled mandate stops being chargeable immediately")
    b = Bench(seed=11)
    try:
        c, m = b.registered()
        m = b.svc.cancel_mandate(m.id)
        r.ok("F8a  it is CANCELLED", m.state is MandateState.CANCELLED)
        d = b.svc.decide(m.id)
        r.ok("F8b  deciding refuses and submits nothing",
             d.acted is False and not d.attempt_id, d.reason[:70])
        r.ok("F8c  the provider agrees the token is gone",
             b.api._tokens[m.rzp_token_id]["recurring_details"]["status"]
             == "cancelled")
    finally:
        b.close()

    # ------------------------------------------------------------------ F9
    r.section("F9  a paused mandate is refused, and resumes on the webhook")
    b = Bench(seed=11)
    try:
        c, m = b.registered()
        b.api.set_token_status(m.rzp_token_id, "paused")
        b.deliver()
        m = b.svc.store.mandate(m.id)
        r.ok("F9a  the token webhook pauses our mandate",
             m.state is MandateState.PAUSED, m.state.value)
        d = b.svc.decide(m.id)
        r.ok("F9b  a paused mandate cannot be charged", d.acted is False)
        b.api.set_token_status(m.rzp_token_id, "confirmed")
        b.deliver()
        m = b.svc.store.mandate(m.id)
        r.ok("F9c  resuming brings it back to ACTIVE",
             m.state is MandateState.ACTIVE, m.state.value)
    finally:
        b.close()

    return r.summary()


if __name__ == "__main__":
    raise SystemExit(main())
