"""A-gates: the HTTP surface, over a real socket.

`test_safety.py` drives the `Api` object directly, which checks routing and
authorisation. These gates drive a running server, because the things that go
wrong at the socket are different things: body limits, content types, path
traversal, the header names Razorpay actually sends, and whether a webhook
really is answered before the work behind it runs.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import live.tests  # noqa: F401
from live.api import Server, redact
from live.tests._harness import Bench, Results, WEBHOOK_SECRET, payment_event, signed


def call(url: str, *, method: str = "GET", body: bytes | None = None,
         headers: dict | None = None) -> tuple[int, dict, dict]:
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode()
            return resp.status, _json(raw), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, _json(e.read().decode()), dict(e.headers)


def _json(raw: str) -> dict:
    try:
        out = json.loads(raw or "{}")
        return out if isinstance(out, dict) else {"_": out}
    except ValueError:
        return {"_raw": raw[:200]}


def main() -> int:
    r = Results("LIVE HTTP GATES (offline, real socket)")
    b = Bench(seed=11)
    server = Server(b.svc, host="127.0.0.1", port=0).start_background()
    base = f"http://127.0.0.1:{server.port}"
    try:
        c, m = b.registered()

        # -------------------------------------------------------------- A1
        r.section("A1  health and readiness answer without a token")
        status, body, _ = call(f"{base}/health")
        r.ok("A1a  health is 200", status == 200, str(body)[:60])
        r.ok("A1b  it names the mode", body.get("mode") == "offline")
        status, body, _ = call(f"{base}/ready")
        r.ok("A1c  readiness is 200", status == 200)
        r.ok("A1d  it reports the backlog rather than the provider's health",
             "unprocessed_events" in body and "unresolved_attempts" in body,
             "a provider outage must not make a running service look dead")

        # -------------------------------------------------------------- A2
        r.section("A2  the console is served with a tight policy")
        status, _, headers = call(f"{base}/")
        r.ok("A2a  the console loads", status == 200)
        csp = headers.get("Content-Security-Policy", "")
        r.ok("A2b  it declares a content security policy", bool(csp))
        r.ok("A2c  which allows no third-party origin",
             "default-src 'none'" in csp and "connect-src 'self'" in csp, csp[:60])
        r.ok("A2d  and forbids framing", "frame-ancestors 'none'" in csp)
        r.ok("A2e  nosniff is set", headers.get("X-Content-Type-Options")
             == "nosniff")

        r.section("A3  static serving cannot escape the console directory")
        for attack in ("/../config.py", "/..%2fconfig.py",
                       "/app.js/../../config.py"):
            status, _, _ = call(base + attack)
            r.ok(f"A3   {attack} is refused", status in (403, 404), str(status))

        # -------------------------------------------------------------- A4
        r.section("A4  the webhook endpoint")
        ev = payment_event("payment.captured", payment_id="pay_http01",
                           order_id="order_http01", status="captured")
        raw, signature = signed(ev)
        status, body, _ = call(f"{base}/webhooks/razorpay", method="POST",
                               body=raw,
                               headers={"X-Razorpay-Signature": signature,
                                        "X-Razorpay-Event-Id": "evt_http01",
                                        "Content-Type": "application/json"})
        r.ok("A4a  a signed event is accepted", status == 200, str(body))
        r.ok("A4b  it reports the event type",
             body.get("event") == "payment.captured")
        status, body, _ = call(f"{base}/webhooks/razorpay", method="POST",
                               body=raw,
                               headers={"X-Razorpay-Signature": signature,
                                        "X-Razorpay-Event-Id": "evt_http01"})
        r.ok("A4c  a redelivery is acknowledged as a duplicate",
             status == 200 and body.get("duplicate") is True, str(body))

        status, body, _ = call(f"{base}/webhooks/razorpay", method="POST",
                               body=raw,
                               headers={"X-Razorpay-Signature": "0" * 64,
                                        "X-Razorpay-Event-Id": "evt_forged"})
        r.ok("A4d  a forged signature is 400", status == 400, str(body))
        r.ok("A4e  and the error names no secret",
             "whsec" not in str(body) and WEBHOOK_SECRET not in str(body))

        # The 413 has to ARRIVE, not just be generated. A server that refuses
        # without draining leaves the sender mid-write and it sees a reset,
        # which a webhook provider reports as an outage rather than as a
        # rejection. This assertion is the difference between the two.
        status, _, _ = call(f"{base}/webhooks/razorpay", method="POST",
                            body=b"x" * (1_048_576 + 10),
                            headers={"X-Razorpay-Signature": "x"})
        r.ok("A4f  an oversized body gets a 413 the sender can read",
             status == 413, str(status))
        status, body, _ = call(f"{base}/api/customers", method="POST",
                               body=b"x" * (64 * 1024 + 10))
        r.ok("A4h  and so does an oversized operator request", status == 413,
             str(status))

        # A WEBHOOK MUST NEVER REQUIRE AUTHENTICATION. Razorpay cannot send an
        # operator token, and a 401 would make it retry for 24 hours and then
        # disable the endpoint. Asserted on a server that DOES require a token
        # everywhere else, so the gate is about the exemption and not about a
        # service that happens to have auth switched off.
        guarded = Bench(seed=12, env={"RECOVERY_OPERATOR_TOKEN": "g" * 32})
        gserver = Server(guarded.svc, host="127.0.0.1", port=0).start_background()
        gbase = f"http://127.0.0.1:{gserver.port}"
        try:
            status, _, _ = call(f"{gbase}/api/state")
            r.ok("A4g  the operator API on that server does need one",
                 status == 401, str(status))
            gev = payment_event("payment.captured", payment_id="pay_noauth",
                                order_id="order_noauth", status="captured")
            graw, gsig = signed(gev)
            status, gbody, _ = call(f"{gbase}/webhooks/razorpay", method="POST",
                                    body=graw,
                                    headers={"X-Razorpay-Signature": gsig,
                                             "X-Razorpay-Event-Id": "evt_noauth"})
            r.ok("A4i  and the webhook route does not",
                 status == 200, f"{status} {gbody}")
            status, _, _ = call(f"{gbase}/webhooks/razorpay", method="POST",
                                body=graw,
                                headers={"X-Razorpay-Signature": "0" * 64,
                                         "X-Razorpay-Event-Id": "evt_noauth2"})
            r.ok("A4j  the signature is still what authenticates it",
                 status == 400, str(status))
        finally:
            gserver.stop()
            guarded.close()

        # -------------------------------------------------------------- A5
        r.section("A5  the operator API validates its input")
        status, body, _ = call(f"{base}/api/customers", method="POST",
                               body=b"not json",
                               headers={"Content-Type": "application/json"})
        r.ok("A5a  a non-JSON body is 400", status == 400, str(body))
        status, body, _ = call(f"{base}/api/customers", method="POST",
                               body=b'["a list"]')
        r.ok("A5b  a JSON array is 400", status == 400)
        status, body, _ = call(f"{base}/api/mandates", method="POST",
                               body=json.dumps({"customer_id": "nope",
                                                "charge_amount_paise": 100,
                                                "max_amount_paise": 1000}
                                               ).encode())
        r.ok("A5c  an unknown customer is 400, not a traceback",
             status == 400 and "unknown customer" in str(body), str(body)[:60])
        status, body, _ = call(f"{base}/api/mandates", method="POST",
                               body=json.dumps({"customer_id": c.id,
                                                "charge_amount_paise": 10,
                                                "max_amount_paise": 1000}
                                               ).encode())
        r.ok("A5d  an amount below Razorpay's minimum is 400",
             status == 400 and "at least" in str(body), str(body)[:60])
        status, body, _ = call(f"{base}/api/mandates", method="POST",
                               body=json.dumps({"customer_id": c.id,
                                                "charge_amount_paise": 9000,
                                                "max_amount_paise": 1000}
                                               ).encode())
        r.ok("A5e  a charge above the mandate ceiling is 400", status == 400,
             str(body)[:60])
        status, _, _ = call(f"{base}/api/nonexistent")
        r.ok("A5f  an unknown route is 404", status == 404)
        status, _, _ = call(f"{base}/api/mandates/mdt_nope")
        r.ok("A5g  an unknown mandate is 404", status == 404)

        # -------------------------------------------------------------- A6
        r.section("A6  the only money route takes no parameters")
        # THE AUTHORITATIVE TEST OF THE INJECTION BOUNDARY. The body names an
        # amount, an hour inside the NPCI peak window, and somebody else's
        # token. None of the three may reach the attempt.
        status, body, _ = call(
            f"{base}/api/mandates/{m.id}/decide", method="POST",
            body=json.dumps({"amount_paise": 9_999_999,
                             "target_t": 11,
                             "token": "token_attacker"}).encode())
        r.ok("A6a  decide accepts the request", status == 200, str(body)[:70])
        decision = body.get("decision", {})
        attempts = b.svc.store.attempts_for(m.id)
        r.ok("A6b  and ignores the amount in the body",
             all(a.amount_paise == m.charge_amount_paise for a in attempts),
             str([a.amount_paise for a in attempts]))
        r.ok("A6c  and the hour in the body",
             all(a.target_t != 11 for a in attempts),
             str([a.target_t for a in attempts]))
        r.ok("A6d  and the token in the body",
             all(a.mandate_id == m.id for a in attempts)
             and "token_attacker" not in str(body))
        r.ok("A6e  the decision reports the scheduler's own reason",
             bool(decision.get("reason")), str(decision.get("reason"))[:60])

        # -------------------------------------------------------------- A7
        r.section("A7  redaction")
        r.ok("A7a  a payment id is shortened",
             redact("pay_MOCK00000012") == "pay_…0012",
             redact("pay_MOCK00000012"))
        r.ok("A7b  redaction is recursive through lists and dicts",
             redact({"a": ["token_ABCDEFGH"]}) == {"a": ["token_…EFGH"]})
        r.ok("A7c  ordinary strings are untouched",
             redact("insufficient_funds") == "insufficient_funds")
        r.ok("A7d  a short value that only looks like an id is untouched",
             redact("pay_x") == "pay_x")
        status, body, _ = call(f"{base}/api/mandates/{m.id}")
        r.ok("A7e  a served route redacts by default", status == 200
             and m.rzp_token_id not in str(body))
        # REVEALING REQUIRES REAL AUTHENTICATION, not merely reaching the
        # route. This server has no operator token configured, so it cannot
        # tell who is asking and does not hand out a customer's provider ids.
        status, body, _ = call(f"{base}/api/mandates/{m.id}?reveal=1")
        r.ok("A7f  reveal=1 alone does not unredact when no token is "
             "configured",
             status == 200 and m.rzp_token_id not in str(body))

        # -------------------------------------------------------------- A8
        r.section("A8  loading the console cannot move money")
        before = len(b.svc.store.recent_attempts(100))
        calls_before = b.api.calls
        call(f"{base}/")
        call(f"{base}/app.js")
        call(f"{base}/app.css")
        call(f"{base}/health")
        call(f"{base}/api/state")
        r.ok("A8a  no attempt was created",
             len(b.svc.store.recent_attempts(100)) == before)
        r.ok("A8b  and no provider call was made", b.api.calls == calls_before,
             f"{b.api.calls - calls_before} calls")
    finally:
        server.stop()
        b.close()

    return r.summary()


if __name__ == "__main__":
    raise SystemExit(main())
