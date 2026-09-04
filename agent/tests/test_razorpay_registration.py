"""Offline gates for UPI AutoPay registration.

The body-shape gates drive `RazorpayApi` against a stub transport and read the
request it actually built. An earlier form tested a separate builder function,
which meant the shape under test was not the shape the live script sent.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

# I2-EXEMPT: drives the mandate-registration payload through the live client against a stub transport.
from agent.execution.razorpay_api import RazorpayApi
from agent.execution.razorpay_registration import (AUTH_AMOUNT_PAISE,
                                                   registration_to_binding_fields,
                                                   RegistrationResult,
                                                   verify_checkout_signature)


class StubTransport:
    """Records the request and answers 200. No socket, no credential."""

    def request(self, method, url, body=None):
        return 200, {"id": "cust_stub"}

_results: list[tuple[bool, str]] = []


def ok(name: str, cond: bool) -> None:
    _results.append((bool(cond), name))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


def main() -> int:
    print("REGISTRATION GATES (offline)")
    api = RazorpayApi(StubTransport())

    api.create_customer(name="T", email="t@example.com",
                        contact="+919999999999")
    cust = api.last_request["body"]
    ok("customer body", cust["fail_existing"] == "0"
       and cust["email"] == "t@example.com")

    api.create_authorization_order(
        customer_id="cust_x", max_amount_paise=4990000,
        expire_at=2000000000, frequency="as_presented", receipt="r1")
    order = api.last_request["body"]
    ok("auth order amount Rs 1", order["amount"] == AUTH_AMOUNT_PAISE)
    ok("method upi", order["method"] == "upi")
    ok("token max_amount", order["token"]["max_amount"] == 4990000)
    ok("payment_capture", order["payment_capture"] is True)
    ok("it is the /orders endpoint", api.last_request["url"].endswith("/orders"))

    # Razorpay documents max_amount as 500..100000000 paise. The builder this
    # replaced only required >= 100, so a value the provider rejects reached
    # the network to find that out.
    for bad in (100, 100_000_001):
        try:
            api.create_authorization_order(
                customer_id="c", max_amount_paise=bad, expire_at=2000000000,
                frequency="monthly", receipt="r")
            ok(f"max_amount {bad} refused before the network", False)
        except ValueError:
            ok(f"max_amount {bad} refused before the network", True)
    try:
        api.create_authorization_order(
            customer_id="c", max_amount_paise=4990000, expire_at=2000000000,
            frequency="hourly", receipt="r")
        ok("an undocumented frequency is refused", False)
    except ValueError:
        ok("an undocumented frequency is refused", True)

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
