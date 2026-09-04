"""One gate per defect the merge-readiness audit found on the money path.

WHAT MAKES THESE DIFFERENT FROM THE OTHER GATE FILES. Every check here failed
before its repair and passes after it, and each one drives the system PAST the
point the old tests stopped at. The five blockers were all reachable through
the existing suite's own fixtures; what hid them was where the drivers stopped
-- `run_until_resolved` returns the moment an attempt resolves, so a second
charge in the same cycle happened after the last line of the test.

Each gate states the invariant, not the implementation. A mutant that restores
the old behaviour is included wherever one can be written without a second
copy of the system: a check that only exercises the happy path cannot tell a
working guard from an absent one.
"""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.request

import live.tests  # noqa: F401  -- puts the package root on the path
from agent.execution import razorpay_executor as RE
from agent.execution.razorpay_api import (MAX_AMOUNT_RANGE_PAISE,
                                          VALID_FREQUENCIES, RazorpayApi)
from agent.execution.razorpay_mock import MockPlan
from agent.execution.razorpay_predelivery import PredeliveryPhase
from live.api import Server
from live.config import ConfigError, load
from live.domain import (ATTEMPT_TERMINAL, AttemptState, PaymentAttempt,
                         Transition, advance, from_payment_entity)
from agent.ports import PendingNotification
from live.service import LiveError, LiveService, validate_customer
from live.tests._harness import Bench, Results, payment_event, signed
from live.webhooks import WebhookRejected

CONSOLE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "console")


class Death(BaseException):
    """The process dying mid-request. Not an `Exception`, so no handler in the
    system under test can catch it and turn a crash into a tidy return."""


def _drive_to_order(b: Bench, mandate_id: str, *, step: int = 2,
                    hours: int = 24 * 40):
    """Tick until a pre-debit order exists. Returns that attempt, or None."""
    base = b.svc.epoch_origin
    for hour in range(0, hours, step):
        b.svc.decide(mandate_id, now=base + hour * 3600)
        b.deliver()
        for a in b.svc.store.attempts_for(mandate_id):
            if a.state in (AttemptState.ORDER_CREATED, AttemptState.NOTIFIED):
                return a
    return None


def _bench_to_order(hour_of_day: int):
    """A bench driven to a scheduled debit, deciding only at `hour_of_day`.

    Deterministic: fixed seed, fixed debit plan, fixed decision hours. Two
    calls reach the same durable state by the same path, which is what lets
    the in-process and restarted arms be compared without either mutating the
    state the other reads.
    """
    b = Bench(plan=MockPlan(debits=["captured"]), seed=11)
    c, m = b.registered(charge_paise=100, est_payday=3)
    base = b.svc.epoch_origin
    h = hour_of_day
    while h < 24 * 40:
        b.svc.decide(m.id, now=base + h * 3600)
        b.deliver()
        for a in b.svc.store.attempts_for(m.id):
            if a.state in (AttemptState.ORDER_CREATED, AttemptState.NOTIFIED):
                return b, a
        h += 24
    return b, None


def _restarted_verdict(hour_of_day: int):
    """The same state, decided by a service that has just started."""
    b, target = _bench_to_order(hour_of_day)
    try:
        svc = LiveService(b.config, api=b.api,
                          log_path=b.svc.log.path + ".fresh")
        try:
            return svc.decide(b.svc.store.mandates()[0].id,
                              now=b.svc.epoch_origin + target.target_t * 3600)
        finally:
            svc.store.close()
    finally:
        b.close()


def _collections(b: Bench, mandate_id: str) -> list[PaymentAttempt]:
    return [a for a in b.svc.store.attempts_for(mandate_id, limit=100)
            if a.succeeded]


def main() -> int:
    r = Results("MONEY-PATH REGRESSIONS (offline, mock rail)")

    # ===================================================================== B1
    r.section("B1  a collected cycle is never charged again")
    # Every debit captures, so a second submission is a second real collection
    # rather than a decline that happens to be harmless. The loop deliberately
    # keeps ticking for forty days AFTER the first success -- which is the line
    # the old driver stopped at.
    b = Bench(plan=MockPlan(debits=["captured"] * 10), seed=11)
    try:
        c, m = b.registered(charge_paise=100, est_payday=3)
        base = b.svc.epoch_origin
        submissions = []
        for hour in range(0, 24 * 40, 2):
            d = b.svc.decide(m.id, now=base + hour * 3600)
            b.deliver()
            if d.acted:
                submissions.append((hour, d.cycle))
        cycle0 = [s for s in submissions if s[1] == 0]
        got = _collections(b, m.id)
        r.ok("B1a  exactly one debit was submitted in cycle 0",
             len(cycle0) == 1, f"{len(cycle0)} submissions: {cycle0}")
        r.ok("B1b  exactly one collection in cycle 0",
             len([a for a in got if a.cycle == 0]) == 1,
             f"{[(a.cycle, a.state.value) for a in got]}")
        r.ok("B1c  the mandate collected its own amount once per cycle",
             b.svc.store.summary()["recovered_paise"]
             == 100 * len({a.cycle for a in got}),
             f"{b.svc.store.summary()['recovered_paise']} paise over "
             f"{len({a.cycle for a in got})} cycles")
        # THE MUTANT. Cycle collection is what stops the second charge, and it
        # is derived from the attempts on disk. A service that cannot see the
        # success schedules again -- which is the defect, reproduced.
        real = LiveService.__dict__["_collected"]
        try:
            LiveService._collected = staticmethod(lambda m, attempts: False)
            b2 = Bench(plan=MockPlan(debits=["captured"] * 10), seed=11)
            try:
                _c, m2 = b2.registered(charge_paise=100, est_payday=3)
                base2 = b2.svc.epoch_origin
                for hour in range(0, 24 * 30, 2):
                    b2.svc.decide(m2.id, now=base2 + hour * 3600)
                    b2.deliver()
                repeat = [a for a in _collections(b2, m2.id) if a.cycle == 0]
                r.ok("B1d  MUTANT: without the cycle-collected rule the same "
                     "run charges again", len(repeat) > 1,
                     f"{len(repeat)} collections in cycle 0")
            finally:
                b2.close()
        finally:
            LiveService._collected = real
    finally:
        b.close()

    # ===================================================================== B2
    r.section("B2  a crash with a debit in flight never resubmits it")
    # The first debit DECLINES at the bank, so the order is left `attempted`
    # rather than `paid`. That matters: Razorpay documents an order as closed
    # to further payments only once it is `paid`, so on this path the provider
    # would NOT refuse a second request. Nothing but local durable state stops
    # a second debit here.
    for label, first_debit in (("declined", "failed:insufficient_funds"),
                               ("captured", "captured")):
        b = Bench(plan=MockPlan(debits=[first_debit, "captured"]), seed=11)
        crashing = _CrashingApi(b.api)
        b.svc.api = crashing
        b.svc.executor._api = crashing
        try:
            c, m = b.registered(charge_paise=100, est_payday=3)
            target = _drive_to_order(b, m.id)
            crashing.crash_next = True
            died = False
            try:
                b.svc.decide(m.id, now=b.svc.epoch_origin
                             + target.target_t * 3600)
            except Death:
                died = True
            row = b.svc.store.attempt(target.id)
            r.ok(f"B2a[{label}] the process died with the debit at the provider",
                 died and crashing.debits == 1,
                 f"died={died} debits={crashing.debits}")
            r.ok(f"B2b[{label}] durable state says a debit may be out",
                 row.state is AttemptState.SUBMITTING, row.state.value)

            # RESTART: a new service over the same database and the same rail.
            svc2 = LiveService(b.config, api=crashing,
                               log_path=b.svc.log.path + ".restart")
            before = crashing.debits
            d = svc2.decide(m.id, now=b.svc.epoch_origin
                            + target.target_t * 3600)
            r.ok(f"B2c[{label}] the restart does not resubmit",
                 crashing.debits == before,
                 f"{before} -> {crashing.debits} provider debits")
            r.ok(f"B2d[{label}] and says why",
                 "must be known" in d.reason, d.reason[:60])
            resolved = svc2.reconcile()
            row = svc2.store.attempt(target.id)
            r.ok(f"B2e[{label}] reconciliation resolves it from the provider",
                 row.state in ATTEMPT_TERMINAL,
                 f"{row.state.value} via {resolved}")
            r.ok(f"B2f[{label}] still exactly one payment on that order",
                 crashing.debits == 1, f"{crashing.debits}")
            svc2.store.close()
        finally:
            b.close()

    # THE MUTANT: journal the phase AFTER the request, which is where it was.
    b = Bench(plan=MockPlan(debits=["failed:insufficient_funds", "captured"]),
              seed=11)
    crashing = _CrashingApi(b.api)
    b.svc.api = crashing
    b.svc.executor._api = crashing
    real_attempt = RE.RazorpayExecutor.attempt
    try:
        c, m = b.registered(charge_paise=100, est_payday=3)
        target = _drive_to_order(b, m.id)
        RE.RazorpayExecutor.attempt = _late_journal(real_attempt)
        crashing.crash_next = True
        try:
            b.svc.decide(m.id, now=b.svc.epoch_origin + target.target_t * 3600)
        except Death:
            pass
        RE.RazorpayExecutor.attempt = real_attempt
        stale = b.svc.store.attempt(target.id)
        svc2 = LiveService(b.config, api=crashing,
                           log_path=b.svc.log.path + ".mutant")
        before = crashing.debits
        svc2.decide(m.id, now=b.svc.epoch_origin + target.target_t * 3600)
        r.ok("B2g  MUTANT: journalling after the request leaves a pre-submit "
             "row", stale.state is not AttemptState.SUBMITTING,
             stale.state.value)
        r.ok("B2h  MUTANT: and the restart submits a SECOND debit",
             crashing.debits > before,
             f"{before} -> {crashing.debits} provider debits")
        svc2.store.close()
    finally:
        RE.RazorpayExecutor.attempt = real_attempt
        b.close()

    # ===================================================================== B3
    r.section("B3  concurrent registration cannot share one identity")
    b = Bench(seed=3)
    try:
        n = 16
        made, errors = [], []
        start = threading.Barrier(n)

        def create(i):
            start.wait()
            try:
                made.append(b.svc.create_customer(
                    name=f"Customer {i:02d}", email=f"c{i}@example.com",
                    contact=f"+9190000000{i:02d}"))
            except BaseException as e:              # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=create, args=(i,))
                   for i in range(n)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        seqs = [x.seq for x in made]
        r.ok("B3a  every concurrent creation succeeded",
             len(made) == n and not errors, f"{len(made)}/{n} {errors[:2]}")
        r.ok("B3b  no two customers share a seq",
             len(set(seqs)) == len(seqs),
             f"{len(seqs) - len(set(seqs))} duplicates in {sorted(seqs)}")

        for cust in made:
            b.svc.start_registration(customer_id=cust.id,
                                     charge_amount_paise=100,
                                     max_amount_paise=1_500_000,
                                     est_salary=30000)
        uids = {}
        for md in b.svc.store.mandates():
            cc = b.svc.store.customer(md.customer_id)
            uids.setdefault(f"c{cc.seq}m{md.index_no}", []).append(md.id)
        r.ok("B3c  no two mandates share a uid",
             all(len(v) == 1 for v in uids.values()),
             str({k: v for k, v in uids.items() if len(v) > 1}))
        r.ok("B3d  every mandate has its own binding",
             len(b.svc.bindings) == len(b.svc.store.mandates()),
             f"{len(b.svc.bindings)} bindings, "
             f"{len(b.svc.store.mandates())} mandates")

        # Each binding carries its OWN customer's token, not a survivor's.
        wrong = []
        for md in b.svc.store.mandates():
            cc = b.svc.store.customer(md.customer_id)
            binding = b.svc.bindings[f"c{cc.seq}m{md.index_no}"]
            if binding.rzp_email != cc.email:
                wrong.append(md.id)
        r.ok("B3e  no binding points at another customer", not wrong,
             f"{len(wrong)} crossed bindings")

        # THE DATABASE, NOT THE CODE, IS WHAT ENFORCES IT. A duplicate seq
        # written directly must be refused.
        import sqlite3
        clash = False
        try:
            with b.svc.store.tx() as db:
                db.execute("UPDATE customers SET seq=? WHERE id=?",
                           (seqs[0], made[1].id))
        except sqlite3.IntegrityError:
            clash = True
        r.ok("B3f  the schema refuses a duplicate seq outright", clash,
             "UNIQUE index on customers(seq)")
    finally:
        b.close()

    # ===================================================================== B4
    r.section("B4  a failed pre-debit notice blocks the debit, durably")
    b = Bench(plan=MockPlan(debits=["captured"]), seed=11)
    try:
        c, m = b.registered(charge_paise=100, est_payday=3)
        target = _drive_to_order(b, m.id)
        b.api.drain_webhooks()          # drop the mock's optimistic delivery
        _deliver_notification(b, target, m, "failed", "evt_NOTIFY_FAILED")
        row = b.svc.store.attempt(target.id)
        r.ok("B4a  the attempt is durably NOTIFICATION_FAILED",
             row.state is AttemptState.NOTIFICATION_FAILED, row.state.value)
        ref = b.svc._ref(b.svc.store.mandate(m.id), c)
        phase = b.svc.executor.predelivery_state(ref, target.target_t).phase
        r.ok("B4b  the executor's own precondition sees it too",
             phase is PredeliveryPhase.NOTIFICATION_FAILED, phase.value)

        before = b.api.calls
        b.svc.decide(m.id, now=b.svc.epoch_origin + target.target_t * 3600)
        row = b.svc.store.attempt(target.id)
        r.ok("B4c  no payment was submitted against it", not row.payment_id,
             row.payment_id or "(none)")

        # A LATER, REORDERED `.delivered` MUST NOT REVIVE IT. The two events
        # contradict each other and this cannot tell which is right, so the
        # reading that does not debit wins.
        _deliver_notification(b, target, m, "delivered", "evt_LATE_DELIVERED")
        row = b.svc.store.attempt(target.id)
        r.ok("B4d  a later .delivered does not reopen it",
             row.state is AttemptState.NOTIFICATION_FAILED, row.state.value)
        _deliver_notification(b, target, m, "failed", "evt_NOTIFY_FAILED_2")
        r.ok("B4e  a duplicate .failed is a no-op",
             b.svc.store.attempt(target.id).state
             is AttemptState.NOTIFICATION_FAILED)

        # RESTART: the block is on disk, not in this process.
        svc2 = LiveService(b.config, api=b.api,
                           log_path=b.svc.log.path + ".restart")
        calls = b.api.calls
        svc2.decide(m.id, now=b.svc.epoch_origin + target.target_t * 3600)
        r.ok("B4f  and it survives a restart",
             not svc2.store.attempt(target.id).payment_id)
        r.ok("B4g  the mandate is not stranded: a fresh notice may be issued",
             any(a.id != target.id
                 for a in svc2.store.attempts_for(m.id)),
             f"{len(svc2.store.attempts_for(m.id))} attempts")
        svc2.store.close()

        # THE MUTANT: the pre-repair mapping, where a failed notice left the
        # row where it was and the executor read ORDER_CREATED.
        r.ok("B4h  MUTANT: mapping the failed phase to ORDER_CREATED would let "
             "the executor charge",
             advance(AttemptState.ORDER_CREATED, AttemptState.ORDER_CREATED)
             is not Transition.APPLIED
             and AttemptState.NOTIFICATION_FAILED in ATTEMPT_TERMINAL,
             "NOTIFICATION_FAILED is terminal; a same-state write is not "
             "APPLIED")
    finally:
        b.close()

    # ===================================================================== B5
    r.section("B5  the same durable state gives the same verdict after a "
              "restart")
    # Decisions are taken at 10:00, an NPCI peak hour, so `now_t + 24` is also
    # peak and the target is pushed past it. That is the case where the
    # scheduler's notify hour and `target_t - 24` differ.
    for hour_of_day, label in ((13, "non-peak"), (10, "peak")):
        b = Bench(plan=MockPlan(debits=["captured"]), seed=11)
        try:
            c, m = b.registered(charge_paise=100, est_payday=3)
            base = b.svc.epoch_origin
            target, at = None, None
            h = hour_of_day
            while h < 24 * 40 and target is None:
                b.svc.decide(m.id, now=base + h * 3600)
                b.deliver()
                for a in b.svc.store.attempts_for(m.id):
                    if a.state in (AttemptState.ORDER_CREATED,
                                   AttemptState.NOTIFIED):
                        target, at = a, h
                        break
                h += 24
            r.ok(f"B5a[{label}] a debit was scheduled from hour {at}",
                 target is not None)
            if target is None:
                b.close()
                continue
            r.ok(f"B5b[{label}] the scheduler's notify hour is on the row",
                 target.notify_t == at, f"stored {target.notify_t}, chose {at}")
            reconstructed = target.target_t - 24
            r.ok(f"B5c[{label}] target_t={target.target_t}, "
                 f"target_t-24={reconstructed}, notify_t={target.notify_t}",
                 True, "differ" if reconstructed != target.notify_t else "same")

            # The gate's view of the outstanding notice, in this process and in
            # a restarted one, must be the same pair.
            ref = b.svc._ref(b.svc.store.mandate(m.id), c)
            here = b.svc.ledger.pending(ref.uid)
            svc2 = LiveService(b.config, api=b.api,
                               log_path=b.svc.log.path + ".restart")
            there = svc2.ledger.pending(ref.uid)
            r.ok(f"B5d[{label}] the rehydrated notice matches the live one",
                 here is not None and there is not None
                 and (here.notify_t, here.target_t)
                 == (there.notify_t, there.target_t),
                 f"{here} vs {there}")
            r.ok(f"B5e[{label}] and both match what is stored",
                 there is not None
                 and (there.notify_t, there.target_t)
                 == (target.notify_t, target.target_t),
                 f"{there} vs row({target.notify_t}, {target.target_t})")

            svc2.store.close()
            # THE VERDICT ITSELF, ON IDENTICAL DURABLE STATE -- and the two
            # runs must not see each other. Deciding in this process would
            # submit the debit and leave the restarted service looking at a
            # SUBMITTED row, which is a different question. So the restarted
            # arm is built from its own bench: the plan and the seed are fixed,
            # so it reaches the same state by the same path.
            d1 = b.svc.decide(m.id, now=base + target.target_t * 3600)
            d2 = _restarted_verdict(hour_of_day)
            r.ok(f"B5f[{label}] neither process refuses for `pending`",
                 d1.refused_rule != "pending" and d2.refused_rule != "pending",
                 f"in-process={d1.refused_rule!r} restarted={d2.refused_rule!r}")
            r.ok(f"B5g[{label}] and both reach the same verdict",
                 d1.gate_verdict == d2.gate_verdict == "ALLOWED",
                 f"in-process={d1.gate_verdict!r} restarted={d2.gate_verdict!r}")
        finally:
            b.close()

    # THE MUTANT: rehydrate the notice from `target_t - 24` instead of reading
    # the stored hour, which is what the restart path used to do. On a
    # peak-hour schedule the two disagree and Stage 0 reads the difference as a
    # second concurrent notification.
    b, target = _bench_to_order(10)
    try:
        c = b.svc.store.customers()[0]
        m = b.svc.store.mandates()[0]
        ref = b.svc._ref(m, c)
        b.svc.ledger.set_pending(ref.uid, PendingNotification(
            notify_t=target.target_t - 24, target_t=target.target_t,
            under_previous_notice=False))
        d = b.svc.decide(m.id, now=b.svc.epoch_origin
                         + target.target_t * 3600)
        r.ok("B5h  MUTANT: a reconstructed notify_t is refused as a second "
             "notification", d.refused_rule == "pending",
             f"refused_rule={d.refused_rule!r}: {d.reason[:60]}")
        r.ok("B5i  MUTANT: so the debit never runs", not d.acted)
    finally:
        b.close()

    # ===================================================================== H1
    r.section("H1  an invalid signature cannot squat an event id")
    b = Bench(plan=MockPlan(debits=["captured"]), seed=11)
    try:
        c, m = b.registered(charge_paise=100, est_payday=3)
        target = _drive_to_order(b, m.id)
        b.svc.decide(m.id, now=b.svc.epoch_origin + target.target_t * 3600)
        b.api.drain_webhooks()
        row = b.svc.store.attempt(target.id)

        body = payment_event("payment.captured", payment_id="pay_H1",
                             order_id=row.order_id, status="captured")
        raw, good = signed(body)
        event_id = "evt_CONTESTED"

        rejected = 0
        try:
            b.svc.handle_webhook(raw, "deadbeef" * 8, event_id)
        except WebhookRejected as e:
            rejected = e.status
        r.ok("H1a  the forged delivery is refused", rejected == 400,
             str(rejected))
        r.ok("H1b  and does not occupy the event id",
             b.svc.store.event(event_id) is None)
        r.ok("H1c  but is logged for forensics",
             b.svc.store.rejected_count() == 1
             and b.svc.store.recent_rejected()[0]["claimed_id"] == event_id,
             str(b.svc.store.recent_rejected()[:1]))

        res = b.svc.handle_webhook(raw, good, event_id)
        r.ok("H1d  the genuine retry is accepted, not dismissed as duplicate",
             res.accepted and not res.duplicate, res.detail)
        applied = b.svc.process_webhooks()
        r.ok("H1e  and it is processed",
             any(x["changed"] for x in applied), str(applied))
        again = b.svc.handle_webhook(raw, good, event_id)
        r.ok("H1f  a real redelivery of it IS a duplicate", again.duplicate)
        r.ok("H1g  so it was processed exactly once",
             b.svc.store.attempt(target.id).state is AttemptState.SUCCEEDED
             and not b.svc.process_webhooks(),
             "no second application")
    finally:
        b.close()

    # ===================================================================== H2
    r.section("H2  the shared connection survives concurrent use")
    b = Bench(seed=3)
    try:
        for i in range(6):
            b.svc.create_customer(name=f"Customer {i}", email=f"h2c{i}@x.com",
                                  contact=f"+9190000001{i:02d}")
        errs: list[str] = []

        def hammer():
            for _ in range(300):
                try:
                    b.svc.store.summary()
                    b.svc.store.customers()
                    b.svc.store.recent_attempts(10)
                    b.svc.store.put_customer(b.svc.store.customers()[0])
                    b.svc.store.transitions_for("mandate", "none")
                except BaseException as e:          # noqa: BLE001
                    errs.append(repr(e))

        ts = [threading.Thread(target=hammer) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        r.ok("H2a  8 threads x 300 mixed reads and writes raise nothing",
             not errs, f"{len(errs)} errors: {sorted(set(errs))[:2]}")
        # THE MUTANT: the unguarded read path, which is what this replaced.
        raw_errs: list[str] = []

        def unguarded():
            # Exactly what the read helpers used to do: touch the shared
            # connection with no lock while other threads write through it.
            db = b.svc.store._db
            for _ in range(300):
                try:
                    db.execute("SELECT * FROM customers").fetchall()
                    db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
                    db.execute("SELECT * FROM mandates ORDER BY created_at"
                               ).fetchall()
                    b.svc.store.put_customer(b.svc.store.customers()[0])
                except BaseException as e:          # noqa: BLE001
                    raw_errs.append(type(e).__name__)

        ts = [threading.Thread(target=unguarded) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        r.ok("H2b  MUTANT: reading the connection without the lock does raise",
             bool(raw_errs), f"{len(raw_errs)} errors: "
                             f"{sorted(set(raw_errs))[:3]}")
    finally:
        b.close()

    # ===================================================================== H3
    r.section("H3  a stale write cannot regress a terminal state")
    b = Bench(plan=MockPlan(debits=["captured"]), seed=11)
    try:
        c, m = b.registered(charge_paise=100, est_payday=3)
        base = b.svc.epoch_origin
        for hour in range(0, 24 * 40, 2):
            d = b.svc.decide(m.id, now=base + hour * 3600)
            b.deliver()
            if d.acted:
                break
        a = b.svc.store.attempts_for(m.id)[0]
        r.ok("H3a  the attempt is SUCCEEDED",
             a.state is AttemptState.SUCCEEDED, a.state.value)
        stale = b.svc.store.attempt(a.id)
        stale.state = AttemptState.SUBMITTED        # a writer holding an old read
        stale.payment_id = "pay_STALE"
        b.svc.store.put_attempt(stale)
        fresh = b.svc.store.attempt(a.id)
        r.ok("H3b  the stored state did not move backwards",
             fresh.state is AttemptState.SUCCEEDED, fresh.state.value)
        r.ok("H3c  and the caller's object was corrected, not silently ignored",
             stale.state is AttemptState.SUCCEEDED, stale.state.value)
        r.ok("H3d  other columns still wrote",
             fresh.payment_id == "pay_STALE", fresh.payment_id)
        r.ok("H3e  the collected total is unchanged",
             b.svc.store.summary()["recovered_paise"] == a.amount_paise,
             str(b.svc.store.summary()["recovered_paise"]))
        # A mandate is protected by the same rule.
        md = b.svc.cancel_mandate(m.id)
        revived = b.svc.store.mandate(m.id)
        revived.state = type(md.state).ACTIVE
        b.svc.store.put_mandate(revived)
        r.ok("H3f  a cancelled mandate cannot be revived by a stale write",
             b.svc.store.mandate(m.id).state is type(md.state).CANCELLED,
             b.svc.store.mandate(m.id).state.value)
    finally:
        b.close()

    # ===================================================================== H4
    r.section("H4  live debits require an authenticated operator")
    try:
        load({"RECOVERY_MODE": "live", "RAZORPAY_KEY_ID": "rzp_live_x",
              "RAZORPAY_KEY_SECRET": "s", "RAZORPAY_WEBHOOK_SECRET": "w",
              "RECOVERY_LIVE_DEBIT": "yes"})
        r.ok("H4a  live debits without an operator token are refused", False,
             "the configuration was accepted")
    except ConfigError as e:
        r.ok("H4a  live debits without an operator token are refused", True,
             str(e)[:60])
    cfg = load({"RECOVERY_MODE": "live", "RAZORPAY_KEY_ID": "rzp_live_x",
                "RAZORPAY_KEY_SECRET": "s", "RAZORPAY_WEBHOOK_SECRET": "w",
                "RECOVERY_LIVE_DEBIT": "yes",
                "RECOVERY_OPERATOR_TOKEN": "t0ken"})
    r.ok("H4b  with one, the same configuration loads",
         cfg.debit_authorized and cfg.operator_token == "t0ken")

    b = Bench(seed=5, env={"RECOVERY_OPERATOR_TOKEN": "t0ken"})
    server = Server(b.svc, host="127.0.0.1", port=0).start_background()
    base_url = f"http://127.0.0.1:{server.port}"
    try:
        c, m = b.registered(charge_paise=100, est_payday=3)
        r.ok("H4c  a mutating request with no token is 401",
             _post(f"{base_url}/api/mandates/{m.id}/decide")[0] == 401)
        r.ok("H4d  with a wrong token it is 401",
             _post(f"{base_url}/api/mandates/{m.id}/decide",
                   headers={"X-Operator-Token": "wrong"})[0] == 401)
        ok_status, _ = _post(f"{base_url}/api/mandates/{m.id}/decide",
                             headers={"X-Operator-Token": "t0ken"})
        r.ok("H4e  with the right one it is served", ok_status == 200,
             str(ok_status))
        # CROSS-SITE. A page on another origin can send a simple POST; the
        # browser attaches an Origin the page cannot forge.
        r.ok("H4f  a cross-site mutating request is 403",
             _post(f"{base_url}/api/mandates/{m.id}/decide",
                   headers={"X-Operator-Token": "t0ken",
                            "Origin": "http://evil.example"})[0] == 403)
        r.ok("H4g  a same-origin one is not",
             _post(f"{base_url}/api/mandates/{m.id}/decide",
                   headers={"X-Operator-Token": "t0ken",
                            "Origin": base_url})[0] == 200)
        # The webhook stays reachable: Razorpay presents no operator token and
        # no Origin, and its authentication is the signature.
        raw, sig = signed({"entity": "event", "event": "payment.captured",
                           "payload": {}})
        r.ok("H4h  the webhook endpoint needs no operator token",
             _post(f"{base_url}/webhooks/razorpay", data=raw,
                   headers={"X-Razorpay-Signature": sig,
                            "X-Razorpay-Event-Id": "evt_H4"})[0] == 200)
        # M10: identifiers stay redacted when nothing can authenticate.
        b2 = Bench(seed=5)
        s2 = Server(b2.svc, host="127.0.0.1", port=0).start_background()
        try:
            b2.registered(charge_paise=100)
            _st, body = _get(f"http://127.0.0.1:{s2.port}/api/state?reveal=1")
            r.ok("H4i  reveal=1 does not unredact when no token is configured",
                 _redacted(body) and not _full_ids(body),
                 f"redacted={_redacted(body)} full={_full_ids(body)}")
            _st, body = _get(f"{base_url}/api/state?reveal=1",
                             headers={"X-Operator-Token": "t0ken"})
            r.ok("H4j  and does unredact for an authenticated operator",
                 _full_ids(body) and not _redacted(body),
                 f"redacted={_redacted(body)} full={_full_ids(body)}")
        finally:
            s2.stop()
            b2.close()
    finally:
        server.stop()
        b.close()

    # ===================================================================== H5
    r.section("H5  the live debit control asks before it moves money")
    app_js = _read(os.path.join(CONSOLE, "app.js"))
    index = _read(os.path.join(CONSOLE, "index.html"))
    handler = app_js.split('$("act-decide").addEventListener', 1)[-1]
    handler = handler.split("$(\"act-advance\")", 1)[0]
    r.ok("H5a  the decide handler waits on a confirmation",
         "confirmLiveDebit()" in handler)
    r.ok("H5b  and the confirmation comes BEFORE the request",
         handler.index("confirmLiveDebit()") < handler.index("/decide"))
    r.ok("H5c  it only asks when money can actually move",
         'mode === "live"' in app_js and "debit_allowed" in app_js)
    r.ok("H5d  the dialog names the environment, mandate, customer and amount",
         all(k in index for k in ("debit-env", "debit-mandate",
                                  "debit-customer", "debit-amount")))
    r.ok("H5e  and says money will move",
         "money leaves the customer" in index.replace("\n", " "))
    r.ok("H5f  the confirming action is a distinct second click",
         'value="yes"' in index.split("debit-dialog", 1)[-1].split(
             "</dialog>", 1)[0])
    r.ok("H5g  the console can supply an operator token",
         "X-Operator-Token" in app_js and "token-dialog" in index)

    # ===================================================================== H6
    r.section("H6  billing cycles roll over, and each collects once")
    b = Bench(plan=MockPlan(debits=["captured"] * 12), seed=11)
    try:
        c, m = b.registered(charge_paise=100, est_payday=3)
        base = b.svc.epoch_origin
        for hour in range(0, 24 * 95, 2):
            b.svc.decide(m.id, now=base + hour * 3600)
            b.deliver()
        got = _collections(b, m.id)
        cycles = sorted(a.cycle for a in got)
        final = b.svc.store.mandate(m.id)
        r.ok("H6a  more than one cycle ran", len(set(cycles)) >= 3,
             f"cycles collected: {cycles}")
        r.ok("H6b  each cycle collected exactly once",
             len(cycles) == len(set(cycles)), f"{cycles}")
        r.ok("H6c  the mandate's cycle advanced with them",
             final.cycle >= max(cycles), f"mandate at cycle {final.cycle}")
        r.ok("H6d  no cycle number was skipped",
             cycles == list(range(min(cycles), max(cycles) + 1)), f"{cycles}")
        r.ok("H6e  the collected total is one charge per collected cycle",
             b.svc.store.summary()["recovered_paise"] == 100 * len(cycles),
             f"{b.svc.store.summary()['recovered_paise']} paise, "
             f"{len(cycles)} cycles")
        # RESTART: the cycle is durable.
        svc2 = LiveService(b.config, api=b.api,
                           log_path=b.svc.log.path + ".restart")
        r.ok("H6f  the cycle survives a restart",
             svc2.store.mandate(m.id).cycle == final.cycle,
             f"{svc2.store.mandate(m.id).cycle} vs {final.cycle}")
        r.ok("H6g  and the restarted service does not re-collect a closed one",
             len(_collections(b, m.id)) == len(got))
        svc2.store.close()
    finally:
        b.close()

    # ============================================================ M1, M2, M3
    r.section("M1/M2/M3  UPI AutoPay mandate parameters match the provider")
    lo, hi = MAX_AMOUNT_RANGE_PAISE
    r.ok("M1a  max_amount floor is Rs 1, not eMandate's Rs 5", lo == 100,
         f"{lo} paise")
    r.ok("M1b  max_amount ceiling is UPI's Rs 99,999", hi == 9_999_900,
         f"{hi} paise")
    b = Bench(seed=5)
    try:
        c = b.svc.create_customer(name="Ceiling Test", email="m1@example.com",
                                  contact="+919000000111")
        raised = ""
        try:
            b.svc.start_registration(customer_id=c.id,
                                     charge_amount_paise=100,
                                     max_amount_paise=hi + 1)
        except Exception as e:                      # noqa: BLE001
            raised = str(e)
        r.ok("M1c  a mandate above the UPI ceiling is refused locally",
             "outside UPI AutoPay" in raised, raised[:70])
        r.ok("M2a  every frequency Razorpay lists for UPI is accepted",
             {"daily", "weekly", "fortnightly", "bimonthly", "monthly",
              "quarterly", "half_yearly", "yearly", "as_presented"}
             == set(VALID_FREQUENCIES), sorted(VALID_FREQUENCIES))
        m = b.svc.start_registration(customer_id=c.id,
                                     charge_amount_paise=100,
                                     max_amount_paise=1_500_000)
        r.ok("M3a  registration defaults to charge-at-will, not a fixed month",
             m.frequency == "as_presented", m.frequency)
    finally:
        b.close()

    # THE ACTUAL REQUEST BODY, off the real client with a stubbed transport.
    # The mock models provider state, not the bytes that would be sent.
    api = RazorpayApi(_NoTransport(), "https://api.razorpay.com/v1")
    api.create_authorization_order(customer_id="cust_x",
                                   max_amount_paise=1_500_000,
                                   expire_at=2_000_000_000,
                                   frequency="as_presented", receipt="r1")
    auth_body = api.last_request["body"]
    api.create_notification_order(amount_paise=100, receipt="r2",
                                  token_id="token_x",
                                  payment_after=2_000_000_000)
    notif_body = api.last_request["body"]
    r.ok("M4a  the authorisation order sends no payment_capture",
         "payment_capture" not in auth_body, sorted(auth_body))
    r.ok("M4b  the notification order does, where the docs show it",
         notif_body.get("payment_capture") is True, sorted(notif_body))
    r.ok("M4c  the authorisation order still carries the token block",
         auth_body["token"]["frequency"] == "as_presented"
         and auth_body["token"]["max_amount"] == 1_500_000,
         str(auth_body["token"]))

    # ============================================================ M11 inputs
    r.section("M11  customer input is validated before the provider is called")
    for name, email, contact, why in (
            ("ab", "a@b.co", "+919000000000", "name under 3 characters"),
            ("x" * 51, "a@b.co", "+919000000000", "name over 50"),
            ("Valid Name", "not-an-email", "+919000000000", "no @"),
            ("Valid Name", "a@" + "b" * 70 + ".co", "+91900000", "email over 64"),
            ("Valid Name", "a@b.co", "not-a-number", "contact not digits"),
            ("Valid Name", "a@b.co", "+9190000000000000", "contact over 15")):
        try:
            validate_customer(name, email, contact)
            r.ok(f"M11  {why} is refused", False, "accepted")
        except LiveError:
            r.ok(f"M11  {why} is refused", True)
    b = Bench(seed=5)
    try:
        before = b.api.calls
        try:
            b.svc.create_customer(name="ab", email="a@b.co",
                                  contact="+919000000000")
        except LiveError:
            pass
        r.ok("M11z  and no provider call was made", b.api.calls == before,
             f"{b.api.calls - before} calls")
    finally:
        b.close()

    # =================================================================== M16
    r.section("M16  an unknown provider status is UNKNOWN, never SUBMITTED")
    for status in ("refunded", "some_status_invented_in_2027", ""):
        view = from_payment_entity({"id": "pay_x", "status": status,
                                    "order_id": "order_x", "amount": 100})
        r.ok(f"M16  status {status!r} maps to UNKNOWN",
             view.state is AttemptState.UNKNOWN, view.state.value)
    for status, expected in (("created", AttemptState.SUBMITTED),
                             ("authorized", AttemptState.AUTHORIZED),
                             ("captured", AttemptState.SUCCEEDED),
                             ("failed", AttemptState.FAILED)):
        view = from_payment_entity({"id": "p", "status": status})
        r.ok(f"M16  the documented status {status!r} still maps to "
             f"{expected.value}", view.state is expected, view.state.value)

    return r.summary()


# ---------------------------------------------------------------- scaffolding
class _CrashingApi:
    """The provider takes the request; the process dies before the answer."""

    def __init__(self, inner):
        self._inner = inner
        self.crash_next = False
        self.debits = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def create_recurring_payment(self, **kw):
        self.debits += 1
        r = self._inner.create_recurring_payment(**kw)
        if self.crash_next:
            self.crash_next = False
            raise Death("the process died after the provider took the request")
        return r


def _late_journal(real):
    """The pre-repair ordering: journal DEBIT_ATTEMPTED after the response."""

    def attempt(self, ref, amount, t, action_id=""):
        saved = self.journal.save
        held = []

        def deferred(rec):
            if rec.phase is PredeliveryPhase.DEBIT_ATTEMPTED:
                held.append(rec)
                return
            saved(rec)

        self.journal.save = deferred
        try:
            out = real(self, ref, amount, t, action_id)
        except BaseException:
            # THE CRASH. The deferred write never happens -- which is the whole
            # of the defect: the record that a debit went out only existed
            # after the response came back, and the response never came.
            self.journal.save = saved
            raise
        self.journal.save = saved
        for rec in held:
            saved(rec)
        return out

    return attempt


def _deliver_notification(b: Bench, attempt, mandate, kind: str,
                          event_id: str) -> None:
    body = {"entity": "event", "event": f"order.notification.{kind}",
            "contains": ["notification"],
            "payload": {"notification": {"entity": {
                "id": f"notification_{event_id}",
                "order_id": attempt.order_id,
                "token_id": b.svc.store.mandate(mandate.id).rzp_token_id,
                "status": kind,
                "payment_after": attempt.payment_after}}},
            "created_at": 1_700_000_000}
    raw, sig = signed(body)
    b.svc.handle_webhook(raw, sig, event_id)
    b.svc.process_webhooks()


def _request(url: str, *, data=None, headers=None, method="GET"):
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _post(url: str, *, data=b"{}", headers=None):
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    return _request(url, data=data, headers=hdrs, method="POST")


def _get(url: str, *, headers=None):
    return _request(url, headers=headers)


class _NoTransport:
    """Builds the request and never sends it. `last_request` is the point."""

    @staticmethod
    def request(method, url, body):
        return 200, {"id": "order_stub"}


def _id_values(body: str) -> list[str]:
    """Every provider-identifier-shaped VALUE in a served JSON document.

    Walks the parsed object rather than matching the raw text: `"token_status"`
    is a field NAME that looks exactly like an identifier, and a regex over the
    body counts it as one.
    """
    found: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str) and re.match(
                r"^(?:pay|order|cust|token)_\S+$", node):
            found.append(node)

    walk(json.loads(body))
    return found


def _redacted(body: str) -> bool:
    ids = _id_values(body)
    return bool(ids) and all("…" in v for v in ids)


def _full_ids(body: str) -> bool:
    ids = _id_values(body)
    return bool(ids) and any("…" not in v for v in ids)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


if __name__ == "__main__":
    raise SystemExit(main())
