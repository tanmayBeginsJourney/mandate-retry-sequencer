"""W-gates: signature, duplication, ordering, and malformed input.

Razorpay documents three properties this code has to survive, and each one has
a gate here that fails if the property is assumed away.

  * The signature is HMAC-SHA256 over the RAW body. A verifier that parses
    first and re-serialises gets a different byte string and a different
    digest, so it rejects every genuine event -- and the obvious repair is to
    stop verifying.
  * Delivery is AT LEAST ONCE, keyed by `x-razorpay-event-id`. A handler that
    ignores that applies one payment twice.
  * Order is NOT guaranteed. A handler that trusts arrival order lets a
    redelivered `payment.authorized` overwrite a `payment.captured` that has
    already landed, and a collected cycle goes back to uncollected.
"""
from __future__ import annotations

import json

import live.tests  # noqa: F401
from live.tests._harness import Bench, Results, WEBHOOK_SECRET, payment_event, signed
from live.webhooks import MAX_BODY_BYTES, WebhookRejected, verify
from live.domain import AttemptState


def _ingest(bench: Bench, body: dict, *, event_id: str,
            secret: str = WEBHOOK_SECRET, mangle=None):
    raw, signature = signed(body, secret)
    if mangle is not None:
        raw = mangle(raw)
    try:
        return bench.svc.handle_webhook(raw, signature, event_id), None
    except WebhookRejected as e:
        return None, e


def main() -> int:
    r = Results("LIVE WEBHOOK GATES (offline)")

    # ------------------------------------------------------------------ W1
    r.section("W1  the signature is computed over the exact bytes received")
    body = {"event": "payment.captured", "payload": {}}
    raw, sig = signed(body)
    r.ok("W1a  a correct signature verifies", verify(raw, sig, WEBHOOK_SECRET))
    r.ok("W1b  the wrong secret does not",
         verify(raw, sig, "some-other-secret") is False)
    r.ok("W1c  a single flipped byte does not",
         verify(raw[:-1] + b"X", sig, WEBHOOK_SECRET) is False)
    r.ok("W1d  an empty signature does not",
         verify(raw, "", WEBHOOK_SECRET) is False)
    r.ok("W1e  an empty secret does not", verify(raw, sig, "") is False)
    # THE MUTANT THIS GATE EXISTS FOR: parse the body, re-serialise it, sign
    # that. Python's json.dumps is not Razorpay's, so the digest differs and
    # every genuine event would be rejected.
    reserialised = json.dumps(json.loads(raw.decode())).encode()
    r.ok("W1f  MUTANT: re-serialising the body before signing breaks it",
         reserialised != raw
         and verify(reserialised, sig, WEBHOOK_SECRET) is False,
         "which is why verify() takes bytes and not a dict")

    # ------------------------------------------------------------------ W2
    r.section("W2  a bad signature is rejected, recorded, and takes no id")
    b = Bench()
    try:
        res, err = _ingest(b, body, event_id="evt_forged",
                           secret="attacker-guess")
        r.ok("W2a  it is rejected with 400",
             res is None and err is not None and err.status == 400,
             getattr(err, "reason", ""))
        # RECORDED, BUT NOT UNDER THE EVENT ID IT CLAIMED. `event_id` is the
        # deduplication key; letting an unauthenticated sender occupy one means
        # Razorpay's genuine delivery of that event arrives as a duplicate and
        # is never processed. See W2f.
        r.ok("W2b  it does not occupy the event id it claimed",
             b.svc.store.event("evt_forged") is None)
        logged = b.svc.store.recent_rejected()
        r.ok("W2c  but it is logged, with the id it claimed",
             len(logged) == 1 and logged[0]["claimed_id"] == "evt_forged",
             str(logged[:1]))
        r.ok("W2d  an invalid event is never queued for processing",
             not any(e.event_id == "evt_forged"
                     for e in b.svc.store.unprocessed_events()))
        r.ok("W2e  processing it changes nothing",
             b.svc.process_webhooks() == [])
        # The genuine delivery of the same event still gets through.
        good, gerr = _ingest(b, body, event_id="evt_forged")
        r.ok("W2f  the real event with that id is accepted, not dismissed as "
             "a duplicate",
             gerr is None and good is not None and not good.duplicate,
             getattr(good, "detail", getattr(gerr, "reason", "")))
    finally:
        b.close()

    # ------------------------------------------------------------------ W3
    r.section("W3  duplicate delivery is a no-op")
    b = Bench()
    try:
        c, m = b.registered()
        # Registration already delivered its own events, so the assertions
        # below count the DELTA. An absolute count here would be measuring the
        # fixture rather than the deduplication.
        before = b.svc.store.summary()["events"]
        ev = payment_event("payment.captured", payment_id="pay_dup01",
                           order_id="order_dup01", status="captured")
        first, _ = _ingest(b, ev, event_id="evt_same")
        second, _ = _ingest(b, ev, event_id="evt_same")
        r.ok("W3a  the first delivery is new",
             first is not None and first.duplicate is False)
        r.ok("W3b  the second is recognised as a duplicate",
             second is not None and second.duplicate is True)
        r.ok("W3c  only one row was added for the two deliveries",
             b.svc.store.summary()["events"] - before == 1,
             f"{b.svc.store.summary()['events'] - before} added")
        # A DIFFERENT event id carrying the SAME payload is NOT a duplicate at
        # the transport layer. It is caught one level down, by the state
        # machine refusing to apply a transition it has already applied.
        third, _ = _ingest(b, ev, event_id="evt_different_id")
        r.ok("W3d  a different event id is accepted as a new delivery",
             third is not None and third.duplicate is False)
        r.ok("W3e  which makes two rows for three deliveries",
             b.svc.store.summary()["events"] - before == 2,
             f"{b.svc.store.summary()['events'] - before} added")
    finally:
        b.close()

    # ------------------------------------------------------------------ W4
    r.section("W4  out-of-order delivery cannot walk a payment backwards")
    b = Bench(seed=11)
    try:
        c, m = b.registered()
        b.run_until_resolved(m.id)
        resolved = [a for a in b.svc.store.attempts_for(m.id) if a.resolved]
        if not resolved:
            r.ok("W4   SKIPPED -- no attempt reached a terminal state", False,
                 "the scheduler did not fire inside the window")
        else:
            a = resolved[0]
            before = a.state
            # A LATE `authorized` for a payment already captured. Razorpay
            # documents that the two can arrive in either order.
            late = payment_event("payment.authorized",
                                 payment_id=a.payment_id or "pay_late",
                                 order_id=a.order_id, status="authorized")
            _ingest(b, late, event_id="evt_late_authorized")
            b.svc.process_webhooks()
            after = b.svc.store.attempt(a.id)
            r.ok("W4a  the terminal state survives a late earlier event",
                 after.state is before, f"{before.value} -> {after.state.value}")
            r.ok("W4b  the transition is recorded as stale, not applied",
                 any(t["verdict"] == "IGNORED_STALE" for t
                     in b.svc.store.transitions_for("attempt", a.id)))
    finally:
        b.close()

    # ------------------------------------------------------------------ W5
    r.section("W5  two different terminal states are a CONFLICT, not a race")
    b = Bench(seed=11)
    try:
        c, m = b.registered()
        b.run_until_resolved(m.id)
        resolved = [a for a in b.svc.store.attempts_for(m.id) if a.resolved]
        if not resolved:
            r.ok("W5   SKIPPED -- no attempt reached a terminal state", False)
        else:
            a = resolved[0]
            other = ("payment.captured" if a.state is AttemptState.FAILED
                     else "payment.failed")
            status = "captured" if other.endswith("captured") else "failed"
            ev = payment_event(other, payment_id=a.payment_id or "pay_c",
                               order_id=a.order_id, status=status,
                               reason=None if status == "captured"
                               else "insufficient_funds")
            _ingest(b, ev, event_id="evt_contradiction")
            b.svc.process_webhooks()
            after = b.svc.store.attempt(a.id)
            r.ok("W5a  the first terminal state is kept",
                 after.state is a.state, after.state.value)
            r.ok("W5b  the attempt is flagged for a human",
                 after.conflicted is True)
            r.ok("W5c  the contradiction is in the transition log",
                 any(t["verdict"] == "CONFLICT" for t
                     in b.svc.store.transitions_for("attempt", a.id)))
    finally:
        b.close()

    # ------------------------------------------------------------------ W6
    r.section("W6  malformed and hostile bodies")
    b = Bench()
    try:
        raw, sig = b"not json at all", ""
        import hmac, hashlib
        sig = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
        res = b.svc.handle_webhook(raw, sig, "evt_garbage")
        r.ok("W6a  a correctly signed non-JSON body is accepted and recorded",
             res.accepted is True,
             "the signature is what authenticates; the content is data")
        r.ok("W6b  it names no event", res.event_type == "")
        r.ok("W6c  processing it changes nothing and does not raise",
             all(not x["changed"] for x in b.svc.process_webhooks()))

        big = b"x" * (MAX_BODY_BYTES + 1)
        try:
            b.svc.handle_webhook(big, "sig", "evt_big")
            too_big = None
        except WebhookRejected as e:
            too_big = e
        r.ok("W6d  an oversized body is refused with 413",
             too_big is not None and too_big.status == 413)

        # A missing event id would collapse every delivery onto one dedup key,
        # so the body's own hash stands in.
        ev = payment_event("payment.captured", payment_id="pay_noid",
                           order_id="order_noid", status="captured")
        res, _ = _ingest(b, ev, event_id="")
        r.ok("W6e  a missing event id falls back to a hash of the body",
             res is not None and res.event_id.startswith("sha256:"),
             res.event_id[:20] if res else "")
        again, _ = _ingest(b, ev, event_id="")
        r.ok("W6f  and that fallback still deduplicates",
             again is not None and again.duplicate is True)

        unknown = {"entity": "event", "event": "payment.dispute.created",
                   "payload": {}}
        res, err = _ingest(b, unknown, event_id="evt_unknown")
        r.ok("W6g  an unhandled event type is accepted, not 4xx'd",
             res is not None and err is None,
             "a 4xx makes Razorpay retry for 24h then disable the webhook")
        r.ok("W6h  and it changes no state",
             all(not x["changed"] for x in b.svc.process_webhooks()))
    finally:
        b.close()

    # ------------------------------------------------------------------ W7
    r.section("W7  an event for a mandate we do not have is handled, not fatal")
    b = Bench()
    try:
        ev = payment_event("payment.failed", payment_id="pay_orphan",
                           order_id="order_orphan", status="failed",
                           reason="insufficient_funds")
        res, err = _ingest(b, ev, event_id="evt_orphan")
        r.ok("W7a  it is accepted", res is not None and err is None)
        results = b.svc.process_webhooks()
        r.ok("W7b  processing reports no local attempt rather than raising",
             len(results) == 1 and results[0]["changed"] is False
             and "no local attempt" in results[0]["detail"],
             results[0]["detail"] if results else "")
        r.ok("W7c  the event is marked processed so it is not retried forever",
             not b.svc.store.unprocessed_events())
    finally:
        b.close()

    return r.summary()


if __name__ == "__main__":
    raise SystemExit(main())
