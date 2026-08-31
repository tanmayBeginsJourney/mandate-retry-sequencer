"""Test Mode UPI AutoPay mandate registration helpers.

Official flow (Razorpay PG recurring / UPI Autopay, read 31 August 2026):
  1. POST /v1/customers  -> customer_id
  2. POST /v1/orders with method=upi, customer_id, token{max_amount,expire_at,frequency}
     -> order_id  (UPI auth amount is Rs 1 / 100 paise per recurring docs)
  3. Razorpay Standard Checkout (recurring: 1) -> authorisation payment
  4. GET /v1/payments/:id  -> payment_id, token_id

Output fields map directly to MandateBinding:
  rzp_customer_id, rzp_token_id, rzp_email, rzp_contact, charge_amount
"""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any

from agent.execution.razorpay_predelivery import sanitize_envelope

API_BASE = "https://api.razorpay.com/v1"

#: UPI mandate registration auth amount. [REPORTED] recurring-payments API:
#: "The order amount for UPI is Rs 1."
AUTH_AMOUNT_PAISE = 100

VALID_FREQUENCIES = frozenset({
    "daily", "weekly", "monthly", "quarterly", "yearly", "as_presented",
})


@dataclass(frozen=True)
class RegistrationSession:
    """Server-side state for one Checkout authorisation attempt."""
    customer_id: str
    order_id: str
    email: str
    contact: str
    name: str
    auth_amount_paise: int
    max_amount_paise: int
    frequency: str
    receipt: str


@dataclass(frozen=True)
class RegistrationResult:
    customer_id: str
    order_id: str
    payment_id: str
    token_id: str
    email: str
    contact: str
    name: str
    max_amount_paise: int
    charge_amount_paise: int
    token_status: str = ""
    payment_status: str = ""


def default_expire_at(*, years: int = 10) -> int:
    return int(time.time()) + years * 365 * 24 * 3600


def build_customer_body(*, name: str, email: str, contact: str,
                        notes: dict | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "email": email,
        "contact": contact,
        "fail_existing": "0",
    }
    if notes:
        body["notes"] = notes
    return body


def build_auth_order_body(*, customer_id: str, max_amount_paise: int,
                          expire_at: int, frequency: str = "as_presented",
                          receipt: str = "",
                          auth_amount_paise: int = AUTH_AMOUNT_PAISE,
                          notes: dict | None = None) -> dict[str, Any]:
    """POST /v1/orders body for UPI AutoPay mandate registration.

    Shape from Razorpay recurring UPI examples:
    method=upi, customer_id, token{max_amount, expire_at, frequency}.
    """
    if frequency not in VALID_FREQUENCIES:
        raise ValueError(f"invalid frequency {frequency!r}")
    if max_amount_paise < auth_amount_paise:
        raise ValueError("max_amount_paise must be >= auth_amount_paise")
    body: dict[str, Any] = {
        "amount": auth_amount_paise,
        "currency": "INR",
        "payment_capture": True,
        "method": "upi",
        "customer_id": customer_id,
        "receipt": receipt[:40],
        "token": {
            "max_amount": max_amount_paise,
            "expire_at": expire_at,
            "frequency": frequency,
        },
    }
    if notes:
        body["notes"] = notes
    return body


def parse_customer_id(payload: dict) -> str:
    return str(payload.get("id") or "")


def parse_order_id(payload: dict) -> str:
    return str(payload.get("id") or "")


def parse_payment_token(payload: dict) -> tuple[str, str, str]:
    """Return (payment_id, token_id, status) from GET /v1/payments/:id."""
    pid = str(payload.get("id") or "")
    status = str(payload.get("status") or "")
    token_id = str(payload.get("token_id") or "")
    if not token_id:
        token = payload.get("token")
        if isinstance(token, str):
            token_id = token
        elif isinstance(token, dict):
            token_id = str(token.get("id") or "")
    return pid, token_id, status


def verify_checkout_signature(*, order_id: str, payment_id: str,
                              signature: str, key_secret: str) -> bool:
    """Verify Razorpay Checkout handler signature."""
    if not (order_id and payment_id and signature and key_secret):
        return False
    msg = f"{order_id}|{payment_id}".encode()
    expected = hmac.new(key_secret.encode(), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def registration_to_binding_fields(res: RegistrationResult) -> dict[str, Any]:
    """Map a RegistrationResult to MandateBinding / .env fields."""
    return {
        "rzp_customer_id": res.customer_id,
        "rzp_token_id": res.token_id,
        "rzp_email": res.email,
        "rzp_contact": res.contact,
        "charge_amount": res.charge_amount_paise / 100.0,
        "max_amount_paise": res.max_amount_paise,
        "registration_order_id": res.order_id,
        "registration_payment_id": res.payment_id,
    }


def env_snippet(res: RegistrationResult) -> str:
    """Lines to paste into .env after successful registration."""
    return "\n".join([
        f"RAZORPAY_TEST_CUSTOMER_ID={res.customer_id}",
        f"RAZORPAY_TEST_TOKEN_ID={res.token_id}",
        f"RAZORPAY_DEFAULT_EMAIL={res.email}",
        f"RAZORPAY_DEFAULT_CONTACT={res.contact}",
        f"RAZORPAY_TEST_AMOUNT_PAISE={res.charge_amount_paise}",
        f"RAZORPAY_MANDATE_MAX_PAISE={res.max_amount_paise}",
        f"# registration_order_id={res.order_id}",
        f"# registration_payment_id={res.payment_id}",
    ])


def transcript_record(*, phase: str, http_method: str, url: str,
                      request_body: dict | None, http_status: int | None,
                      response_body: dict | None,
                      extra: dict | None = None) -> dict:
    row = {
        "phase": phase,
        "http_method": http_method,
        "url": url,
        "http_status": http_status,
        "request": sanitize_envelope(request_body or {}),
        "response": sanitize_envelope(response_body or {}),
    }
    if extra:
        row.update(sanitize_envelope(extra))
    return row
