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

# I2-EXEMPT: drives the pre-debit notification order through the live executor against a stub transport.
from agent.execution.razorpay_executor import (SUBMITTED_RAW, MandateBinding,
                                               RazorpayExecutor)
from agent.execution.razorpay_predelivery import (NOTIFICATION_DELIVERED,
                                                  ORDER_CREATED,
                                                  PredeliveryPhase,
                                                  WEBHOOK_DELIVERED,
                                                  WEBHOOK_FAILED,
                                                  parse_notification_webhook)
from agent.ports import INDETERMINATE_CODES, MandateRef

_results: list[tuple[bool, str, str]] = []


def ok(name: str, cond: bool, detail: str = "") -> bool:
    _results.append((bool(cond), name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   {detail}" if detail else ""))
    return bool(cond)


class QueueTransport:
    """Scripted responses, consumed in order. Records every request."""

    def __init__(self, posts: list[tuple[int, dict]],
                 gets: list[tuple[int, dict]] | None = None):
        self.posts = list(posts)
        self.gets = list(gets or [])
        self.calls: list[tuple[str, dict | None]] = []

    def request(self, method, url, body=None):
        self.calls.append((url, body))
        if method == "GET":
            if not self.gets:
                return 404, {}
            return self.gets.pop(0)
        if not self.posts:
            return 500, {"error": {"code": "TEST", "description": "no script"}}
        return self.posts.pop(0)


REF = MandateRef(1, 0, 0)
UID = "c1m0"

#: Stage 0 counts simulated hours; Razorpay wants a Unix second. `ORIGIN` is
#: the wall-clock second simulated hour 0 maps to, and the executor is the only
#: place the two meet. `TARGET_H` is 26 hours out, which clears the 24-hour
#: pre-debit notice with an hour to spare.
ORIGIN = int(time.time())
TARGET_H = 26
PAYMENT_AFTER = ORIGIN + TARGET_H * 3600


def _ex(transport, **binding_kw) -> RazorpayExecutor:
    b = MandateBinding(
        rzp_customer_id="cust_test",
        rzp_token_id=binding_kw.pop("rzp_token_id", "token_abc"),
        rzp_email=binding_kw.pop("rzp_email", "test@example.com"),
        rzp_contact=binding_kw.pop("rzp_contact", "+919876543210"),
        charge_amount=binding_kw.pop("charge_amount", 499.0),
        **binding_kw,
    )
    return RazorpayExecutor(bindings={UID: b}, transport=transport,
                            epoch_origin=ORIGIN)


def gate_P1() -> None:
    print("\nP1  successful order creation stores ORDER_CREATED, not DELIVERED")
    t = QueueTransport([(200, {"id": "order_test123", "amount": 49900})])
    ex = _ex(t)
    out = ex.notify(REF, 0.0, notify_t=TARGET_H - 24, target_t=TARGET_H)
    ok("P1a  notify executed", out["executed"] is True)
    ok("P1b  phase is ORDER_CREATED", out["phase"] == ORDER_CREATED)
    ok("P1c  order_id returned", out["order_id"] == "order_test123")
    st = ex.predelivery_state(REF, TARGET_H)
    ok("P1d  state phase ORDER_CREATED",
       st is not None and st.phase == PredeliveryPhase.ORDER_CREATED)
    ok("P1e  POST /orders once",
       len(t.calls) == 1 and t.calls[0][0].endswith("/orders"))
    notif = (t.calls[0][1] or {}).get("notification") or {}
    ok("P1f  notification object", notif.get("token_id") == "token_abc"
       and notif.get("payment_after") == PAYMENT_AFTER
       and (t.calls[0][1] or {}).get("payment_capture") is True)


def gate_P2() -> None:
    print("\nP2  missing token_id refuses order creation")
    t = QueueTransport([])
    ex = _ex(t, rzp_token_id="")
    out = ex.notify(REF, 0.0, notify_t=TARGET_H - 24, target_t=TARGET_H)
    ok("P2a  not executed", out["executed"] is False)
    ok("P2b  no HTTP call", len(t.calls) == 0)
    ok("P2c  detail mentions token", "token" in out["detail"].lower())


def gate_P3() -> None:
    print("\nP3  Razorpay order creation failure")
    t = QueueTransport([(400, {"error": {"code": "BAD_REQUEST_ERROR",
                                           "description": "invalid token"}})])
    ex = _ex(t)
    out = ex.notify(REF, 0.0, notify_t=TARGET_H - 24, target_t=TARGET_H)
    ok("P3a  not executed", out["executed"] is False)
    ok("P3b  phase failed", out["phase"] == "ORDER_CREATE_FAILED")
    ok("P3c  no stored order", ex.predelivery_state(REF, TARGET_H) is None)


def gate_P4() -> None:
    print("\nP4  notification-delivered webhook parsing")
    payload = {
        "event": WEBHOOK_DELIVERED,
        "payload": {"notification": {"entity": {
            "id": "notif_1", "order_id": "order_xyz", "token_id": "token_abc",
            "status": "delivered", "payment_after": PAYMENT_AFTER,
            "delivered_at": PAYMENT_AFTER - 3600,
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
            "status": "failed", "payment_after": PAYMENT_AFTER,
        }}},
    }
    wh = parse_notification_webhook(payload)
    ok("P5a  parsed", wh is not None and wh.event == WEBHOOK_FAILED)


def gate_P6() -> None:
    print("\nP6  webhook advances phase; ORDER_CREATED alone is not DELIVERED")
    t = QueueTransport([(200, {"id": "order_wh", "amount": 49900})])
    ex = _ex(t)
    ex.notify(REF, 0.0, notify_t=TARGET_H - 24, target_t=TARGET_H)
    st = ex.predelivery_state(REF, TARGET_H)
    ok("P6a  starts ORDER_CREATED",
       st is not None and st.phase == PredeliveryPhase.ORDER_CREATED)
    payload = {
        "event": WEBHOOK_DELIVERED,
        "payload": {"notification": {"entity": {
            "id": "notif_d", "order_id": "order_wh", "token_id": "token_abc",
            "status": "delivered", "payment_after": PAYMENT_AFTER,
            "delivered_at": PAYMENT_AFTER - 100,
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
    ex.notify(REF, 0.0, notify_t=TARGET_H - 24, target_t=TARGET_H)
    out = ex.attempt(REF, 499.0, TARGET_H, action_id="a1")
    ok("P7a  success", out.success is True)
    ok("P7b  two POSTs", len(t.calls) == 2)
    ok("P7c  recurring URL", "create/recurring" in t.calls[1][0])
    ok("P7d  order_id on wire", (t.calls[1][1] or {}).get("order_id") == "order_att")
    ok("P7e  no second order", sum(1 for u, _ in t.calls if u.endswith("/orders")) == 1)


def gate_P8() -> None:
    print("\nP8  attempt() without order_id refuses")
    t = QueueTransport([])
    ex = _ex(t)
    out = ex.attempt(REF, 499.0, TARGET_H, action_id="a1")
    ok("P8a  not success", out.success is False)
    ok("P8b  raw_code", out.raw_code == "missing_predelivery_order")
    ok("P8c  no HTTP", len(t.calls) == 0)


def gate_P9() -> None:
    print("\nP9  attempt() blocked after notification failed webhook")
    t = QueueTransport([(200, {"id": "order_fail", "amount": 49900})])
    ex = _ex(t)
    ex.notify(REF, 0.0, notify_t=TARGET_H - 24, target_t=TARGET_H)
    ex.ingest_notification_webhook({
        "event": WEBHOOK_FAILED,
        "payload": {"notification": {"entity": {
            "id": "notif_f", "order_id": "order_fail", "token_id": "token_abc",
            "status": "failed", "payment_after": PAYMENT_AFTER,
        }}},
    })
    out = ex.attempt(REF, 499.0, TARGET_H, action_id="a1")
    ok("P9a  not success", out.success is False)
    ok("P9b  raw_code", out.raw_code == "notification_failed")


def gate_P10() -> None:
    print("\nP10 an ACCEPTED submission is pending, not collected and not "
          "declined")
    print("    mutant: read {\"razorpay_payment_id\": ...} as a payment entity")
    print("            -- no status, so `declined`, which tells the belief")
    print("            filter the account was empty (w3.py:432) for every")
    print("            mandate this customer holds")
    # This is the documented response to POST /v1/payments/create/recurring:
    # a payment id and nothing else. No status, no error_reason.
    t = QueueTransport([
        (200, {"id": "order_sub", "amount": 49900}),
        (200, {"razorpay_payment_id": "pay_accepted"}),
    ])
    ex = _ex(t)
    ex.notify(REF, 0.0, notify_t=TARGET_H - 24, target_t=TARGET_H)
    out = ex.attempt(REF, 499.0, TARGET_H, action_id="a_sub")
    ok("P10a not recorded as a success", out.success is False)
    ok("P10b recorded as PENDING", out.pending is True)
    ok("P10c the code is INDETERMINATE, so no retry is licensed",
       out.code in INDETERMINATE_CODES, out.code)
    ok("P10d raw_code distinguishes it from a lost response",
       out.raw_code == SUBMITTED_RAW, out.raw_code)


def gate_P11() -> None:
    print("\nP11 an order lost to a crash is RECOVERED, not created twice")
    print("    mutant: mint a fresh receipt on retry, so the provider's")
    print("            receipt-uniqueness rule never fires and the same debit")
    print("            gets two orders")
    # Razorpay rejects a second order carrying a receipt it has seen. The
    # recovery is to ask which order already holds that receipt, not to pick a
    # new one -- a new receipt would buy a successful create and a duplicate
    # order for one debit.
    t = QueueTransport(
        posts=[(400, {"error": {"code": "BAD_REQUEST_ERROR",
                                "description": "An order with receipt "
                                               "rcv_x already exists"}})],
        gets=[(200, {"entity": "collection", "count": 1,
                     "items": [{"id": "order_recovered", "status": "created"}]})])
    ex = _ex(t)
    out = ex.notify(REF, 0.0, notify_t=TARGET_H - 24, target_t=TARGET_H)
    ok("P11a notify still succeeds", out["executed"] is True, out["detail"])
    ok("P11b it reuses the order the provider already has",
       out["order_id"] == "order_recovered", out["order_id"])
    ok("P11c the recovery was a lookup by receipt",
       any("receipt=" in u for u, _ in t.calls),
       str([u for u, _ in t.calls]))
    ok("P11d exactly one order create was attempted",
       sum(1 for u, b in t.calls if u.endswith("/orders") and b is not None) == 1)


def gate_P12() -> None:
    print("\nP12 a second notify for the same target does not create a "
          "second order")
    t = QueueTransport([(200, {"id": "order_once", "amount": 49900})])
    ex = _ex(t)
    first = ex.notify(REF, 0.0, notify_t=TARGET_H - 24, target_t=TARGET_H)
    second = ex.notify(REF, 0.0, notify_t=TARGET_H - 24, target_t=TARGET_H)
    ok("P12a both report the same order",
       first["order_id"] == second["order_id"] == "order_once")
    ok("P12b only one provider call was made", len(t.calls) == 1,
       f"{len(t.calls)} calls")


def gate_P13() -> None:
    print("\nP13 without an epoch origin the executor REFUSES rather than "
          "sending a 1970 timestamp")
    print("    mutant: fall back to payment_after=target_t, which is what")
    print("            reading a simulated hour as a Unix second does")
    t = QueueTransport([(200, {"id": "order_noclock", "amount": 49900})])
    b = MandateBinding(rzp_customer_id="cust_test", rzp_token_id="token_abc",
                       rzp_email="test@example.com",
                       rzp_contact="+919876543210", charge_amount=499.0)
    ex = RazorpayExecutor(bindings={UID: b}, transport=t)   # no epoch_origin
    out = ex.notify(REF, 0.0, notify_t=TARGET_H - 24, target_t=TARGET_H)
    ok("P13a not executed", out["executed"] is False)
    ok("P13b no HTTP call was made", len(t.calls) == 0)
    ok("P13c the detail names the missing origin",
       "epoch_origin" in out["detail"], out["detail"])


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
    gate_P10()
    gate_P11()
    gate_P12()
    gate_P13()
    n_fail = sum(1 for passed, _, _ in _results if not passed)
    print("\n" + "=" * 78)
    print(f"{'PASS' if n_fail == 0 else 'FAIL'}  {len(_results) - n_fail}/{len(_results)}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
