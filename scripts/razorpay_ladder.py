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
from agent.ports import MandateRef

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
        #     captured response replayed. Proves what a live 401 would do to a
        #     real mandate rather than what the parser does to a dict.
        ex2 = _executor(_ReplayTransport(status, payload))
        end = ex2.attempt(MandateRef(0, 0, 0), 499.0, 0, action_id="probe")

        case = {
            "label": cap["label"],
            "http_status": status,
            "parser": {"code": out.code, "success": out.success,
                       "pending": out.pending, "raw_code": out.raw_code},
            "attempt": {"code": end.code, "success": end.success,
                        "pending": end.pending, "raw_code": end.raw_code},
        }
        rec["cases"].append(case)
        print(f"    {cap['label']}  (HTTP {status})")
        print(f"      _outcome_from_payment -> code={out.code!r} "
              f"success={out.success} pending={out.pending} "
              f"raw_code={out.raw_code!r}")
        print(f"      attempt()             -> code={end.code!r} "
              f"success={end.success} pending={end.pending} "
              f"raw_code={end.raw_code!r}")
    return rec


def rung45() -> dict:
    print("\nRUNG 4  authenticate for real and take a 200")
    print("RUNG 5  charge success@razorpay / failure@razorpay")
    kid = os.environ.get("RAZORPAY_KEY_ID", "")
    sec = os.environ.get("RAZORPAY_KEY_SECRET", "")
    if not kid or not sec:
        print("    SKIPPED -- no credentials on this machine.")
        print("    Needs: RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET (a rzp_test_ pair),")
        print("           and for rung 5 an AUTHORISED test mandate, i.e. a real")
        print("           customer_id and token_id from a completed AutoPay")
        print("           registration. Test-mode registration is mocked, so this")
        print("           is obtainable without a bank, but not without an account.")
        print("    NOT counted as a pass. Nothing in docs/ may claim these rungs.")
        return {"rung": "4-5", "state": "SKIPPED", "reason": "no credentials"}
    print("    Credentials present, but rungs 4-5 are NOT run automatically:")
    print("    they create entities on a real Razorpay account. Run them")
    print("    deliberately, with the account owner's knowledge.")
    return {"rung": "4-5", "state": "NOT RUN", "reason": "credentials present, "
            "creation of remote entities requires explicit intent"}


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
    results["rungs"].append(rung45())

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, sort_keys=True)
    print(f"\nTranscript written to {os.path.relpath(OUT, PKG)}")
    line("=")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
