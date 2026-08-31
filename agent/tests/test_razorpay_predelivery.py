"""Gates for UPI AutoPay decoupled pre-debit flow (offline, no API key).

    py -3.12 agent/tests/test_razorpay_predelivery.py

ORDER_CREATED is not NOTIFICATION_DELIVERED. These gates enforce that split.
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from agent.execution.razorpay_executor import MandateBinding, RazorpayExecutor
from agent.execution.razorpay_predelivery import (NOTIFICATION_DELIVERED,
                                                  ORDER_CREATED,
                                                  PredeliveryPhase,
                                                  WEBHOOK_DELIVERED,
                                                  WEBHOOK_FAILED,
                                                  parse_notification_webhook)
from agent.ports import MandateRef

_results: list[tuple[bool, str, str]] = []


def ok(name: str, cond: bool, detail: str = "") -> bool:
    _results.append((bool(cond), name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   {detail}" if detail else ""))
    return bool(cond)


class QueueTransport:
    """Scripted POST responses in order."""

    def __init__(self, posts: list[tuple[int, dict]]):
        self.posts = list(posts)
        self.calls: list[tuple[str, dict, str]] = []

    def post(self, url, body, idempotency_key):
        self.calls.append((url, body, idempotency_key))
        if not self.posts:
            return 500, {"error": {"code": "TEST", "description": "no script"}}
        return self.posts.pop(0)

    def get(self, url):
        return 404, {}


REF = MandateRef(1, 0, 0)
UID = "c1m0"
FUTURE_T = int(time.time()) + 26 * 3600


def _ex(transport, **binding_kw) -> RazorpayExecutor:
    b = MandateBinding(
        rzp_customer_id="cust_test",
        rzp_token_id=binding_kw.pop("rzp_token_id", "token_abc"),
        rzp_email=binding_kw.pop("rzp_email", "test@example.com"),
        rzp_contact=binding_kw.pop("rzp_contact", "+919876543210"),
        charge_amount=binding_kw.pop("charge_amount", 499.0),
        **binding_kw,
    )
    return RazorpayExecutor(bindings={UID: b}, transport=transport)


def gate_P1() -> None:
    print("\nP1  successful order creation stores ORDER_CREATED, not DELIVERED")
    t = QueueTransport([(200, {"id": "order_test123", "amount": 49900})])
    ex = _ex(t)
    out = ex.notify(REF, 0.0, notify_t=FUTURE_T - 24, target_t=FUTURE_T)
    ok("P1a  notify executed", out["executed"] is True)
    ok("P1b  phase is ORDER_CREATED", out["phase"] == ORDER_CREATED)
    ok("P1c  order_id returned", out["order_id"] == "order_test123")
    st = ex.predelivery_state(REF, FUTURE_T)
    ok("P1d  state phase ORDER_CREATED",
       st is not None and st.phase == PredeliveryPhase.ORDER_CREATED)
    ok("P1e  POST /orders once", len(t.calls) == 1 and "/orders" in t.calls[0][0])
    notif = t.calls[0][1].get("notification") or {}
    ok("P1f  notification object", notif.get("token_id") == "token_abc"
       and notif.get("payment_after") == FUTURE_T
       and t.calls[0][1].get("payment_capture") is True)


def gate_P2() -> None:
    print("\nP2  missing token_id refuses order creation")
    t = QueueTransport([])
    ex = _ex(t, rzp_token_id="")
    out = ex.notify(REF, 0.0, notify_t=FUTURE_T - 24, target_t=FUTURE_T)
    ok("P2a  not executed", out["executed"] is False)
    ok("P2b  no HTTP call", len(t.calls) == 0)
    ok("P2c  detail mentions token", "token" in out["detail"].lower())


def gate_P3() -> None:
    print("\nP3  Razorpay order creation failure")
    t = QueueTransport([(400, {"error": {"code": "BAD_REQUEST_ERROR",
                                           "description": "invalid token"}})])
    ex = _ex(t)
    out = ex.notify(REF, 0.0, notify_t=FUTURE_T - 24, target_t=FUTURE_T)
    ok("P3a  not executed", out["executed"] is False)
    ok("P3b  phase failed", out["phase"] == "ORDER_CREATE_FAILED")
    ok("P3c  no stored order", ex.predelivery_state(REF, FUTURE_T) is None)


def gate_P4() -> None:
    print("\nP4  notification-delivered webhook parsing")
    payload = {
        "event": WEBHOOK_DELIVERED,
        "payload": {"notification": {"entity": {
            "id": "notif_1", "order_id": "order_xyz", "token_id": "token_abc",
            "status": "delivered", "payment_after": FUTURE_T,
            "delivered_at": FUTURE_T - 3600,
        }}},
    }
    wh = parse_notification_webhook(payload)
    ok("P4a  parsed", wh is not None and wh.event == WEBHOOK_DELIVERED)
    ok("P4b  order_id", wh is not None and wh.order_id == "order_xyz")


def gate_P5() -> None:
    print("\nP5  notification-failed webhook parsing")
    payload = {
        "event": WEBHOOK_FAILED,
        "payload": {"notification": {"entity": {
            "id": "notif_2", "order_id": "order_bad", "token_id": "token_abc",
            "status": "failed", "payment_after": FUTURE_T,
        }}},
    }
    wh = parse_notification_webhook(payload)
    ok("P5a  parsed", wh is not None and wh.event == WEBHOOK_FAILED)


def gate_P6() -> None:
    print("\nP6  webhook advances phase; ORDER_CREATED alone is not DELIVERED")
    t = QueueTransport([(200, {"id": "order_wh", "amount": 49900})])
    ex = _ex(t)
    ex.notify(REF, 0.0, notify_t=FUTURE_T - 24, target_t=FUTURE_T)
    st = ex.predelivery_state(REF, FUTURE_T)
    ok("P6a  starts ORDER_CREATED",
       st is not None and st.phase == PredeliveryPhase.ORDER_CREATED)
    payload = {
        "event": WEBHOOK_DELIVERED,
        "payload": {"notification": {"entity": {
            "id": "notif_d", "order_id": "order_wh", "token_id": "token_abc",
            "status": "delivered", "payment_after": FUTURE_T,
            "delivered_at": FUTURE_T - 100,
        }}},
    }
    res = ex.ingest_notification_webhook(payload)
    ok("P6b  webhook matched", res.get("matched") is True)
    ok("P6c  phase NOTIFICATION_DELIVERED",
       st.phase == PredeliveryPhase.NOTIFICATION_DELIVERED)
    ok("P6d  transcript phase label",
       ex.predelivery_log[-1]["phase"] == NOTIFICATION_DELIVERED)


def gate_P7() -> None:
    print("\nP7  attempt() with valid order_id uses create/recurring + order_id")
    t = QueueTransport([
        (200, {"id": "order_att", "amount": 49900}),
        (200, {"status": "captured", "id": "pay_1"}),
    ])
    ex = _ex(t)
    ex.notify(REF, 0.0, notify_t=FUTURE_T - 24, target_t=FUTURE_T)
    out = ex.attempt(REF, 499.0, FUTURE_T, action_id="a1")
    ok("P7a  success", out.success is True)
    ok("P7b  two POSTs", len(t.calls) == 2)
    ok("P7c  recurring URL", "create/recurring" in t.calls[1][0])
    ok("P7d  order_id on wire", t.calls[1][1].get("order_id") == "order_att")
    ok("P7e  no second order", sum(1 for u, _, _ in t.calls if "/orders" in u) == 1)


def gate_P8() -> None:
    print("\nP8  attempt() without order_id refuses")
    t = QueueTransport([])
    ex = _ex(t)
    out = ex.attempt(REF, 499.0, FUTURE_T, action_id="a1")
    ok("P8a  not success", out.success is False)
    ok("P8b  raw_code", out.raw_code == "missing_predelivery_order")
    ok("P8c  no HTTP", len(t.calls) == 0)


def gate_P9() -> None:
    print("\nP9  attempt() blocked after notification failed webhook")
    t = QueueTransport([(200, {"id": "order_fail", "amount": 49900})])
    ex = _ex(t)
    ex.notify(REF, 0.0, notify_t=FUTURE_T - 24, target_t=FUTURE_T)
    ex.ingest_notification_webhook({
        "event": WEBHOOK_FAILED,
        "payload": {"notification": {"entity": {
            "id": "notif_f", "order_id": "order_fail", "token_id": "token_abc",
            "status": "failed", "payment_after": FUTURE_T,
        }}},
    })
    out = ex.attempt(REF, 499.0, FUTURE_T, action_id="a1")
    ok("P9a  not success", out.success is False)
    ok("P9b  raw_code", out.raw_code == "notification_failed")


def main() -> int:
    print("=" * 78)
    print("RAZORPAY PREDELIVERY GATES (offline)")
    print("=" * 78)
    gate_P1()
    gate_P2()
    gate_P3()
    gate_P4()
    gate_P5()
    gate_P6()
    gate_P7()
    gate_P8()
    gate_P9()
    n_fail = sum(1 for passed, _, _ in _results if not passed)
    print("\n" + "=" * 78)
    print(f"{'PASS' if n_fail == 0 else 'FAIL'}  {len(_results) - n_fail}/{len(_results)}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
