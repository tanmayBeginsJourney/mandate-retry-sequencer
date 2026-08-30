"""The Razorpay ladder: climb as far as the credentials on this machine allow.

WHY A LADDER. `agent/execution/razorpay_executor.py` was written on 29 August
2026 against Razorpay's published documentation and, until this script ran, had
never sent a byte. A request body derived from a doc page and never given a
response is a hypothesis, and `docs/06_MODEL_CARD.md` §6b-2 says so. The cheap
mistake would be to treat "we have no API key" as "nothing can be tested", when
in fact the bottom of the ladder needs no account at all: an unauthenticated
request still produces a REAL status line and a REAL error envelope from
Razorpay's own servers, and that is enough to check the transport, the URL, and
what our parser does with a response we did not write ourselves.

    rung 0   DNS + TLS to api.razorpay.com                     no credentials
    rung 1   POST the real recurring-charge URL, no credential no credentials
    rung 2   POST it again with a well-formed but fake test key no credentials
    rung 3   put BOTH real envelopes through the shipped parser no credentials
    rung 4   authenticate for real, take a 200                  needs a key
    rung 5   charge success@razorpay / failure@razorpay         needs a key
             and a test mandate

Rungs 4 and 5 print SKIPPED with exactly what they need. They are not silently
counted as passes, and nothing in `docs/` may claim them until they run.

WHAT THIS MOVES AND WHAT IT DOES NOT. Rungs 1 and 2 are rejected at the
authentication layer, so Razorpay never reads the request body. This script
therefore says NOTHING about whether our body shape is right -- the largest
standing unknown in that file -- and it moves no money, touches no account, and
creates no entity. It exercises the shipped `_UrllibTransport` and the shipped
`_outcome_from_payment`, not a re-implementation of either, because a test that
re-implements the thing under test proves only that it can be written twice.

Run:  python scripts/razorpay_ladder.py
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import sys
import time

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from agent.execution.razorpay_executor import (API_BASE, MandateBinding,
                                               RazorpayExecutor,
                                               _UrllibTransport)
from agent.llm.client import _load_dotenv
from agent.ports import MandateRef

# Credentials live in `.env` at the repo root, which is gitignored. Reuse the
# loader the LLM client already uses so there is one place that reads it and one
# rule about precedence: an environment variable set by the caller always wins.
# NOTHING IN THIS SCRIPT EVER PRINTS A KEY.
_load_dotenv()

HOST = "api.razorpay.com"
CHARGE_URL = f"{API_BASE}/payments/create/recurring"
OUT = os.path.join(PKG, "logs", "razorpay_ladder.json")

#: The body the executor would send. Reproduced here ONLY so the captured
#: transcript records what was on the wire; the executor builds its own.
PROBE_BODY = {
    "amount": 49900,
    "currency": "INR",
    "customer_id": "cust_ladder_probe",
    "token": "token_ladder_probe",
    "recurring": "1",
    "description": "connectivity probe, no mandate exists",
    "notes": {"probe": "scripts/razorpay_ladder.py"},
}


def line(ch: str = "-") -> None:
    print(ch * 78)


def rung0() -> dict:
    """DNS, then a real TLS handshake. No HTTP request."""
    print("\nRUNG 0  DNS + TLS to api.razorpay.com")
    rec: dict = {"rung": 0}
    try:
        ip = socket.gethostbyname(HOST)
        rec["dns"] = ip
        print(f"    DNS      {HOST} -> {ip}")
    except Exception as e:
        rec["dns_error"] = repr(e)
        print(f"    DNS      FAILED: {e!r}")
        return rec
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((HOST, 443), timeout=15) as sock:
            with ctx.wrap_socket(sock, server_hostname=HOST) as tls:
                cert = tls.getpeercert()
                rec["tls_version"] = tls.version()
                subject = dict(x[0] for x in cert.get("subject", ()))
                issuer = dict(x[0] for x in cert.get("issuer", ()))
                rec["cert_cn"] = subject.get("commonName")
                rec["cert_issuer"] = issuer.get("organizationName")
                rec["cert_expires"] = cert.get("notAfter")
        print(f"    TLS      {rec['tls_version']}, cert CN={rec['cert_cn']!r}, "
              f"issuer={rec['cert_issuer']!r}")
        print(f"    expires  {rec['cert_expires']}")
        rec["ok"] = True
    except Exception as e:
        rec["tls_error"] = repr(e)
        rec["ok"] = False
        print(f"    TLS      FAILED: {e!r}")
    return rec


def _probe(label: str, key_id: str, key_secret: str) -> dict:
    """One POST through the SHIPPED transport. Returns the recorded exchange."""
    t = _UrllibTransport(key_id, key_secret, timeout=20.0)
    started = time.time()
    try:
        status, payload = t.post(CHARGE_URL, PROBE_BODY, "rcv_ladder_probe")
        err = None
    except Exception as e:                      # socket / DNS / TLS
        status, payload, err = None, {}, repr(e)
    ms = int((time.time() - started) * 1000)
    print(f"    {label}")
    print(f"      POST   {CHARGE_URL}")
    print(f"      status {status}   ({ms} ms)")
    if err:
        print(f"      transport error: {err}")
    print(f"      body   {json.dumps(payload, indent=2, sort_keys=True)}")
    return {"label": label, "url": CHARGE_URL, "http_status": status,
            "body": payload, "transport_error": err, "elapsed_ms": ms}


def rung1() -> dict:
    print("\nRUNG 1  unauthenticated POST to the real recurring-charge endpoint")
    print("        (empty credential; Razorpay rejects before reading the body)")
    rec = _probe("no credential", "", "")
    rec["rung"] = 1
    return rec


def rung2() -> dict:
    print("\nRUNG 2  the same POST with a well-formed but FAKE test key")
    print("        (this is the shape a real misconfiguration takes)")
    rec = _probe("fake rzp_test_ key",
                 "rzp_test_0000000000000000", "0000000000000000000000000000")
    rec["rung"] = 2
    return rec


class _ReplayTransport:
    """Returns a captured exchange. Nothing leaves the machine."""

    def __init__(self, status, payload):
        self.status, self.payload = status, payload
        self.posts = 0

    def post(self, url, body, idempotency_key):
        self.posts += 1
        return self.status, self.payload

    def get(self, url):
        return self.status, self.payload


def _executor(transport) -> RazorpayExecutor:
    ref_uid = "c0m0"
    return RazorpayExecutor(
        bindings={ref_uid: MandateBinding("cust_probe", "token_probe")},
        transport=transport)


def rung3(captured: list[dict]) -> dict:
    """Put the REAL envelopes through the shipped parser and the shipped
    `attempt()`. This is the rung that can find a defect."""
    print("\nRUNG 3  the shipped parser, on the REAL envelopes captured above")
    rec: dict = {"rung": 3, "cases": []}
    for cap in captured:
        status, payload = cap["http_status"], cap["body"]
        if status is None:
            print(f"    {cap['label']}: no response captured, skipping")
            continue

        # (a) the pure parser, in isolation.
        ex = _executor(_ReplayTransport(status, payload))
        out = ex._outcome_from_payment(payload, t=0)

        # (b) the whole money path, exactly as agent/loop.py calls it, with the
        #     captured response replayed.
        #
        #     `attempt` RAISES on a request-level rejection, and that is the
        #     expected result here: a refused credential is a configuration
        #     fault, not a decline, so it must not come back as an
        #     AttemptOutcome the belief filter would learn from. The raise is
        #     recorded as the outcome of this rung.
        ex2 = _executor(_ReplayTransport(status, payload))
        raised = None
        end = None
        try:
            end = ex2.attempt(MandateRef(0, 0, 0), 499.0, 0, action_id="probe")
        except Exception as e:                      # noqa: BLE001
            raised = type(e).__name__

        case = {
            "label": cap["label"],
            "http_status": status,
            "parser": {"code": out.code, "success": out.success,
                       "pending": out.pending, "raw_code": out.raw_code},
            "attempt": ({"raised": raised} if raised else
                        {"code": end.code, "success": end.success,
                         "pending": end.pending, "raw_code": end.raw_code}),
        }
        rec["cases"].append(case)
        print(f"    {cap['label']}  (HTTP {status})")
        print(f"      _outcome_from_payment -> code={out.code!r} "
              f"success={out.success} pending={out.pending} "
              f"raw_code={out.raw_code!r}")
        if raised:
            print(f"      attempt()             -> raised {raised} "
                  f"(expected: a refused request is not a decline)")
        else:
            print(f"      attempt()             -> code={end.code!r} "
                  f"success={end.success} pending={end.pending} "
                  f"raw_code={end.raw_code!r}")
    return rec


def _creds():
    return (os.environ.get("RAZORPAY_KEY_ID", ""),
            os.environ.get("RAZORPAY_KEY_SECRET", ""))


def rung4() -> dict:
    """Authenticate for real against a READ-ONLY endpoint.

    `GET /v1/payments?count=1` lists payments. It creates nothing, charges
    nothing and is safe to run against any account. A 200 here proves the
    credential is accepted and the transport handles a success path, which the
    unauthenticated rungs cannot show.
    """
    print("\nRUNG 4  authenticate for real, read-only")
    kid, sec = _creds()
    if not kid or not sec:
        print("    SKIPPED -- no RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET.")
        print("    Put them in .env at the repo root. NOT counted as a pass.")
        return {"rung": 4, "state": "SKIPPED", "reason": "no credentials"}

    mode = "test" if kid.startswith("rzp_test_") else "LIVE"
    print(f"    key mode: {mode}  (prefix {kid[:9]!r})")
    if mode != "test":
        print("    REFUSED. This key is not a rzp_test_ key. This script will")
        print("    not authenticate with a live key: every rung below creates")
        print("    or reads real merchant data. Use a test-mode key.")
        return {"rung": 4, "state": "REFUSED", "reason": "not a test-mode key"}

    t = _UrllibTransport(kid, sec, timeout=20.0)
    url = f"{API_BASE}/payments?count=1"
    started = time.time()
    try:
        status, payload = t.get(url)
        err = None
    except Exception as e:                      # noqa: BLE001
        status, payload, err = None, {}, repr(e)
    ms = int((time.time() - started) * 1000)
    print(f"      GET    {url}")
    print(f"      status {status}   ({ms} ms)")
    if err:
        print(f"      transport error: {err}")
    shape = (sorted(payload.keys()) if isinstance(payload, dict) else type(payload).__name__)
    print(f"      keys   {shape}")
    if isinstance(payload, dict) and "count" in payload:
        print(f"      count  {payload.get('count')}")
    ok = status == 200
    print(f"    {'PASS' if ok else 'FAIL'} -- the credential is "
          f"{'accepted' if ok else 'NOT accepted'} by the API")
    # Keep the SHAPE, never the contents: a payment list is merchant data.
    return {"rung": 4, "state": "PASS" if ok else "FAIL", "http_status": status,
            "response_keys": shape if isinstance(shape, list) else None,
            "elapsed_ms": ms, "transport_error": err}


def rung5() -> dict:
    """Charge a mandate against success@razorpay / failure@razorpay.

    NOT RUN, and the reason is a missing PREREQUISITE rather than a missing
    key. `payments/create/recurring` charges a stored token, and a token only
    exists after an AutoPay mandate has been authorised through the checkout
    flow by a customer. Test mode mocks the bank's approval, not the flow: it
    still needs a customer, an order with `method=upi` and
    `token.max_amount`, and a completed authorisation that returns a
    `token_id`.

    That is a registration integration, not a connectivity rung, and running it
    would create customers, orders and tokens on the account. It is reported as
    NOT RUN rather than counted, and `MandateBinding` is the place the
    `customer_id`/`token_id` pair is supplied once it exists.
    """
    print("\nRUNG 5  charge success@razorpay / failure@razorpay")
    kid, _ = _creds()
    if not kid:
        print("    SKIPPED -- no credentials.")
        return {"rung": 5, "state": "SKIPPED", "reason": "no credentials"}
    print("    NOT RUN. Needs an AUTHORISED mandate, not just a key: a token_id")
    print("    from a completed UPI AutoPay registration. Test mode mocks the")
    print("    bank approval, not the registration flow, so this needs a")
    print("    customer, an order with method=upi and token.max_amount, and a")
    print("    checkout authorisation. That is a registration integration and")
    print("    it creates entities on the account.")
    print("    NOT counted as a pass. Nothing in docs/ may claim this rung.")
    return {"rung": 5, "state": "NOT RUN",
            "reason": "no authorised mandate: token_id required"}


def main() -> int:
    line("=")
    print("THE RAZORPAY LADDER")
    print("Climbing as far as the credentials on this machine allow.")
    print("Rungs 1-2 move no money, create no entity, and are rejected at the")
    print("authentication layer before the request body is read.")
    line("=")

    results = {"generated_by": "scripts/razorpay_ladder.py",
               "api_base": API_BASE, "rungs": []}

    r0 = rung0()
    results["rungs"].append(r0)
    if not r0.get("ok"):
        print("\nRUNG 0 failed: no usable network. Rungs 1-3 need one.")
        line("=")
        return 1

    r1 = rung1()
    r2 = rung2()
    results["rungs"] += [r1, r2]
    results["rungs"].append(rung3([r1, r2]))
    results["rungs"].append(rung4())
    results["rungs"].append(rung5())

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, sort_keys=True)
    print(f"\nTranscript written to {os.path.relpath(OUT, PKG)}")
    line("=")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
