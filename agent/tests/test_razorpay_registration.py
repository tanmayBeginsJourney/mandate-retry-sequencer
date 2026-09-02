"""Offline gates for UPI AutoPay registration body shapes."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

# I2-EXEMPT: drives the mandate-registration payload through the live executor against a stub transport.
from agent.execution.razorpay_registration import (AUTH_AMOUNT_PAISE,
                                                   build_auth_order_body,
                                                   build_customer_body,
                                                   registration_to_binding_fields,
                                                   RegistrationResult,
                                                   verify_checkout_signature)

_results: list[tuple[bool, str]] = []


def ok(name: str, cond: bool) -> None:
    _results.append((bool(cond), name))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


def main() -> int:
    print("REGISTRATION GATES (offline)")
    cust = build_customer_body(name="T", email="t@example.com", contact="+919999999999")
    ok("customer body", cust["fail_existing"] == "0" and cust["email"] == "t@example.com")

    order = build_auth_order_body(
        customer_id="cust_x", max_amount_paise=4990000,
        expire_at=2000000000, frequency="as_presented", receipt="r1")
    ok("auth order amount Rs 1", order["amount"] == AUTH_AMOUNT_PAISE)
    ok("method upi", order["method"] == "upi")
    ok("token max_amount", order["token"]["max_amount"] == 4990000)
    ok("payment_capture", order["payment_capture"] is True)

    res = RegistrationResult(
        customer_id="cust_x", order_id="order_x", payment_id="pay_x",
        token_id="token_x", email="t@example.com", contact="+919999999999",
        name="T", max_amount_paise=4990000, charge_amount_paise=49900)
    binding = registration_to_binding_fields(res)
    ok("binding customer_id", binding["rzp_customer_id"] == "cust_x")
    ok("binding token_id", binding["rzp_token_id"] == "token_x")
    ok("binding charge_amount", binding["charge_amount"] == 499.0)

    ok("signature verify rejects garbage",
       not verify_checkout_signature(
           order_id="o", payment_id="p", signature="bad", key_secret="sec"))

    n_fail = sum(1 for p, _ in _results if not p)
    print(f"\n{'PASS' if not n_fail else 'FAIL'}  {len(_results)-n_fail}/{len(_results)}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
