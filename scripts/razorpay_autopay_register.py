"""Test Mode UPI AutoPay registration harness — ONE mandate, manual Checkout.

Creates a customer + authorisation order, serves a local Checkout page, and
records customer_id, order_id, payment_id, token_id. Does NOT run recurring
debit and does NOT call rung 5a.

    py -3.12 scripts/razorpay_autopay_register.py

Requires rzp_test_ keys in .env. Opens http://127.0.0.1:8765/
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from agent.execution.razorpay_executor import API_BASE, _UrllibTransport
from agent.execution.razorpay_registration import (AUTH_AMOUNT_PAISE,
                                                   RegistrationResult,
                                                   RegistrationSession,
                                                   build_auth_order_body,
                                                   build_customer_body,
                                                   default_expire_at,
                                                   env_snippet,
                                                   parse_customer_id,
                                                   parse_order_id,
                                                   parse_payment_token,
                                                   registration_to_binding_fields,
                                                   transcript_record,
                                                   verify_checkout_signature)
from agent.llm.client import _load_dotenv

OUT = os.path.join(PKG, "logs", "razorpay_autopay_registration.json")
DEFAULT_PORT = 8765


def _require_test_keys() -> tuple[str, str]:
    _load_dotenv()
    kid = os.environ.get("RAZORPAY_KEY_ID", "").strip()
    sec = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
    if not kid or not sec:
        raise SystemExit(
            "Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in .env (test mode).")
    if not kid.startswith("rzp_test_"):
        raise SystemExit(
            "REFUSED: key is not rzp_test_. This harness is Test Mode only.")
    return kid, sec


def _contact(raw: str) -> str:
    c = raw.strip()
    if c.startswith("+"):
        return c
    if c.isdigit() and len(c) == 10:
        return "+91" + c
    return c


def create_registration_session(transport: _UrllibTransport, *, kid: str,
                                name: str, email: str, contact: str,
                                max_amount_paise: int,
                                charge_amount_paise: int,
                                frequency: str) -> tuple[RegistrationSession, list]:
    """Create customer + auth order. Returns session and transcript rows."""
    log: list[dict] = []
    receipt = f"rcv_reg_{int(time.time())}"[:40]
    cust_body = build_customer_body(
        name=name, email=email, contact=contact,
        notes={"purpose": "autopay_registration_harness"})
    st, cust_payload = transport.post(f"{API_BASE}/customers", cust_body,
                                      "rcv_reg_customer")
    log.append(transcript_record(
        phase="CUSTOMER_CREATED", http_method="POST",
        url=f"{API_BASE}/customers", request_body=cust_body,
        http_status=st, response_body=cust_payload))
    if st is None or st >= 400:
        err = (cust_payload.get("error") or {})
        raise RuntimeError(
            f"customer create failed HTTP {st}: {err.get('description')}")
    customer_id = parse_customer_id(cust_payload)
    if not customer_id:
        raise RuntimeError("customer create response missing id")

    order_body = build_auth_order_body(
        customer_id=customer_id,
        max_amount_paise=max_amount_paise,
        expire_at=default_expire_at(),
        frequency=frequency,
        receipt=receipt,
        notes={"purpose": "autopay_registration_harness",
               "charge_amount_paise": charge_amount_paise})
    st, order_payload = transport.post(f"{API_BASE}/orders", order_body,
                                       "rcv_reg_order")
    log.append(transcript_record(
        phase="AUTH_ORDER_CREATED", http_method="POST",
        url=f"{API_BASE}/orders", request_body=order_body,
        http_status=st, response_body=order_payload))
    if st is None or st >= 400:
        err = (order_payload.get("error") or {})
        raise RuntimeError(
            f"order create failed HTTP {st}: {err.get('description')}")
    order_id = parse_order_id(order_payload)
    if not order_id:
        raise RuntimeError("order create response missing id")

    session = RegistrationSession(
        customer_id=customer_id, order_id=order_id,
        email=email, contact=contact, name=name,
        auth_amount_paise=AUTH_AMOUNT_PAISE,
        max_amount_paise=max_amount_paise,
        frequency=frequency, receipt=receipt)
    return session, log


def complete_registration(transport: _UrllibTransport, session: RegistrationSession,
                          *, payment_id: str, order_id: str, signature: str,
                          key_secret: str, charge_amount_paise: int,
                          log: list) -> RegistrationResult:
    if order_id != session.order_id:
        raise ValueError(f"order_id mismatch: got {order_id}, "
                         f"expected {session.order_id}")
    if not verify_checkout_signature(
            order_id=order_id, payment_id=payment_id,
            signature=signature, key_secret=key_secret):
        raise ValueError("checkout signature verification failed")

    url = f"{API_BASE}/payments/{payment_id}"
    st, pay_payload = transport.get(url)
    log.append(transcript_record(
        phase="PAYMENT_FETCHED", http_method="GET", url=url,
        request_body=None, http_status=st, response_body=pay_payload,
        extra={"payment_id": payment_id}))
    if st is None or st >= 400:
        err = (pay_payload.get("error") or {})
        raise RuntimeError(
            f"payment fetch failed HTTP {st}: {err.get('description')}")
    pid, token_id, pay_status = parse_payment_token(pay_payload)
    if not token_id:
        raise RuntimeError(
            "payment has no token_id yet — mandate may still be confirming; "
            "retry GET /v1/payments/{payment_id} or check Dashboard")

    token_status = ""
    tok_url = f"{API_BASE}/customers/{session.customer_id}/tokens"
    st2, tok_payload = transport.get(tok_url)
    log.append(transcript_record(
        phase="TOKENS_LISTED", http_method="GET", url=tok_url,
        request_body=None, http_status=st2, response_body=tok_payload))
    if st2 == 200 and isinstance(tok_payload, dict):
        for item in tok_payload.get("items") or []:
            if str(item.get("id") or "") == token_id:
                token_status = str(item.get("status") or "")
                break

    return RegistrationResult(
        customer_id=session.customer_id,
        order_id=session.order_id,
        payment_id=pid or payment_id,
        token_id=token_id,
        email=session.email,
        contact=session.contact,
        name=session.name,
        max_amount_paise=session.max_amount_paise,
        charge_amount_paise=charge_amount_paise,
        token_status=token_status,
        payment_status=pay_status,
    )


def _checkout_html(*, key_id: str, session: RegistrationSession,
                   port: int) -> str:
    prefill = json.dumps({
        "name": session.name,
        "email": session.email,
        "contact": session.contact,
    })
    opts = {
        "key": key_id,
        "order_id": session.order_id,
        "customer_id": session.customer_id,
        "recurring": "1",
        "name": "Recovery Agent Test Registration",
        "description": "UPI AutoPay mandate authorisation (Rs 1, Test Mode)",
        "image": "https://razorpay.com/favicon.png",
        "prefill": json.loads(prefill),
        "theme": {"color": "#0f766e"},
        "notes": {"purpose": "autopay_registration_harness"},
    }
    options_json = json.dumps(opts)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>UPI AutoPay Test Registration</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 42rem; margin: 2rem auto; padding: 0 1rem; }}
    button {{ font-size: 1rem; padding: 0.6rem 1.2rem; cursor: pointer; }}
    .warn {{ background: #fff7ed; border: 1px solid #fdba74; padding: 0.75rem; border-radius: 6px; }}
    pre {{ background: #f4f4f5; padding: 0.75rem; overflow-x: auto; }}
    #status {{ margin-top: 1rem; }}
  </style>
</head>
<body>
  <h1>UPI AutoPay mandate registration (Test Mode)</h1>
  <div class="warn">
    <p><strong>Test Mode only.</strong> Authorises one UPI AutoPay mandate for Rs 1.
    Use UPI ID <code>success@razorpay</code> in Checkout (Collect flow).</p>
    <p>customer_id: <code>{session.customer_id}</code><br/>
       order_id: <code>{session.order_id}</code></p>
  </div>
  <p><button id="pay">Open Razorpay Checkout</button></p>
  <div id="status"></div>
  <script src="https://checkout.razorpay.com/v1/checkout.js"></script>
  <script>
    const options = {options_json};
    options.handler = function (response) {{
      document.getElementById('status').innerHTML = '<p>Verifying payment…</p>';
      fetch('/complete', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(response),
      }}).then(r => r.json()).then(data => {{
        const el = document.getElementById('status');
        if (data.ok) {{
          el.innerHTML = '<h2>Registration complete</h2><pre>' +
            JSON.stringify(data, null, 2) + '</pre>' +
            '<p>You can close this tab and return to the terminal.</p>';
        }} else {{
          el.innerHTML = '<p style="color:#b91c1c">' + (data.error || 'failed') + '</p>';
        }}
      }}).catch(e => {{
        document.getElementById('status').innerHTML =
          '<p style="color:#b91c1c">' + e + '</p>';
      }});
    }};
    options.modal = {{ ondismiss: function() {{ console.log('checkout closed'); }} }};
    const rzp = new Razorpay(options);
    document.getElementById('pay').onclick = function(e) {{
      rzp.open();
      e.preventDefault();
    }};
  </script>
</body>
</html>"""


class _Handler(BaseHTTPRequestHandler):
    session: RegistrationSession | None = None
    transport: _UrllibTransport | None = None
    key_secret: str = ""
    charge_amount_paise: int = 0
    transcript: list = []
    done: threading.Event = threading.Event()
    result: RegistrationResult | None = None
    error: str = ""

    def log_message(self, fmt, *args):
        return  # quiet

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            assert self.session is not None
            kid = os.environ.get("RAZORPAY_KEY_ID", "")
            html = _checkout_html(key_id=kid, session=self.session,
                                  port=self.server.server_port)
            self._send(200, html.encode("utf-8"))
            return
        if parsed.path == "/health":
            self._send(200, b"ok", "text/plain")
            return
        self._send(404, b"not found")

    def do_POST(self):
        if self.path != "/complete":
            self._send(404, b"not found")
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send(400, json.dumps({"ok": False, "error": "bad json"}).encode(),
                       "application/json")
            return
        try:
            assert self.session and self.transport
            res = complete_registration(
                self.transport, self.session,
                payment_id=str(payload.get("razorpay_payment_id") or ""),
                order_id=str(payload.get("razorpay_order_id") or ""),
                signature=str(payload.get("razorpay_signature") or ""),
                key_secret=self.key_secret,
                charge_amount_paise=self.charge_amount_paise,
                log=self.transcript)
            _Handler.result = res
            _Handler.done.set()
            body = json.dumps({
                "ok": True,
                "customer_id": res.customer_id,
                "order_id": res.order_id,
                "payment_id": res.payment_id,
                "token_id": res.token_id,
                "token_status": res.token_status,
                "payment_status": res.payment_status,
                "binding": registration_to_binding_fields(res),
            }, indent=2)
            self._send(200, body.encode("utf-8"), "application/json")
        except Exception as e:                          # noqa: BLE001
            _Handler.error = str(e)
            self._send(400, json.dumps({"ok": False, "error": str(e)}).encode(),
                       "application/json")


def verify_token(transport: _UrllibTransport, customer_id: str,
                   token_id: str) -> dict:
    """GET customer tokens and return the matching token entity."""
    url = f"{API_BASE}/customers/{customer_id}/tokens"
    st, payload = transport.get(url)
    if st is None or st >= 400:
        err = (payload.get("error") or {})
        raise RuntimeError(f"token list failed HTTP {st}: {err.get('description')}")
    for item in payload.get("items") or []:
        if str(item.get("id") or "") == token_id:
            return item
    raise RuntimeError(f"token_id {token_id!r} not found on customer {customer_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--name", default=os.environ.get(
        "RAZORPAY_TEST_NAME", "Recovery Test User"))
    parser.add_argument("--email", default=os.environ.get(
        "RAZORPAY_DEFAULT_EMAIL", "recovery.test@example.com"))
    parser.add_argument("--contact", default=os.environ.get(
        "RAZORPAY_DEFAULT_CONTACT", "+919876543210"))
    parser.add_argument("--max-amount-paise", type=int, default=int(
        os.environ.get("RAZORPAY_MANDATE_MAX_PAISE", "4990000")))
    parser.add_argument("--charge-amount-paise", type=int, default=int(
        os.environ.get("RAZORPAY_TEST_AMOUNT_PAISE", "49900")))
    parser.add_argument("--frequency", default=os.environ.get(
        "RAZORPAY_MANDATE_FREQUENCY", "as_presented"))
    parser.add_argument("--verify-token", action="store_true",
                        help="Only verify an existing token_id via API")
    args = parser.parse_args()

    kid, sec = _require_test_keys()
    transport = _UrllibTransport(kid, sec, timeout=30.0)

    if args.verify_token:
        cid = os.environ.get("RAZORPAY_TEST_CUSTOMER_ID", "").strip()
        tid = os.environ.get("RAZORPAY_TEST_TOKEN_ID", "").strip()
        if not cid or not tid:
            raise SystemExit("Set RAZORPAY_TEST_CUSTOMER_ID and "
                             "RAZORPAY_TEST_TOKEN_ID")
        tok = verify_token(transport, cid, tid)
        print(json.dumps(sanitize(tok), indent=2))
        return 0

    email = args.email.strip()
    contact = _contact(args.contact)
    if args.charge_amount_paise > args.max_amount_paise:
        raise SystemExit("charge amount exceeds mandate max_amount")

    print("Creating Test Mode customer + UPI auth order…")
    session, transcript = create_registration_session(
        transport, kid=kid, name=args.name, email=email, contact=contact,
        max_amount_paise=args.max_amount_paise,
        charge_amount_paise=args.charge_amount_paise,
        frequency=args.frequency)

    _Handler.session = session
    _Handler.transport = transport
    _Handler.key_secret = sec
    _Handler.charge_amount_paise = args.charge_amount_paise
    _Handler.transcript = transcript
    _Handler.done.clear()
    _Handler.result = None
    _Handler.error = ""

    host = "127.0.0.1"
    httpd = HTTPServer((host, args.port), _Handler)
    url = f"http://{host}:{args.port}/"
    print()
    print("=" * 72)
    print("OPEN THIS URL IN YOUR BROWSER:")
    print(f"  {url}")
    print("=" * 72)
    print(f"customer_id: {session.customer_id}")
    print(f"order_id:    {session.order_id}")
    print("Then click 'Open Razorpay Checkout', choose UPI, enter success@razorpay")
    print("Waiting for Checkout completion (Ctrl+C to abort)…")

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        while not _Handler.done.wait(timeout=1.0):
            pass
    except KeyboardInterrupt:
        print("\nAborted.")
        httpd.shutdown()
        return 1
    httpd.shutdown()

    res = _Handler.result
    if not res:
        print(f"FAILED: {_Handler.error or 'unknown'}")
        return 1

    record = {
        "generated_by": "scripts/razorpay_autopay_register.py",
        "mode": "test",
        "registration": {
            "customer_id": res.customer_id,
            "order_id": res.order_id,
            "payment_id": res.payment_id,
            "token_id": res.token_id,
            "token_status": res.token_status,
            "payment_status": res.payment_status,
            "max_amount_paise": res.max_amount_paise,
            "charge_amount_paise": res.charge_amount_paise,
        },
        "mandate_binding": registration_to_binding_fields(res),
        "transcript": _Handler.transcript,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, sort_keys=True)

    print()
    print("REGISTRATION COMPLETE")
    print(f"  customer_id: {res.customer_id}")
    print(f"  order_id:    {res.order_id}")
    print(f"  payment_id:  {res.payment_id}")
    print(f"  token_id:    {res.token_id}")
    print(f"  token_status:{res.token_status or '(see API)'}")
    print(f"  transcript:  {os.path.relpath(OUT, PKG)}")
    print()
    print("Paste into .env:")
    print("-" * 72)
    print(env_snippet(res))
    print("-" * 72)
    return 0


def sanitize(obj):
    from agent.execution.razorpay_predelivery import sanitize_envelope
    return sanitize_envelope(obj)


if __name__ == "__main__":
    raise SystemExit(main())
