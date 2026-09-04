"""Razorpay's HTTP surface. The only place in this repository that knows a URL.

`razorpay_executor.py` is the `Executor` port. This is the layer underneath:
authentication, paths, request bodies, response parsing, and the four things a
payment API can do to you. Splitting them gives the mandate lifecycle a home
that is not the money path, and leaves one transport, one credential handler
and one error classifier to audit. `razorpay_mock.py` implements the same
surface without a socket.

FOUR OUTCOMES, NOT TWO. The crash argument rests on this distinction.

  OK          2xx. The provider acted and told us.
  REJECTED    4xx naming a request problem. The provider did NOT act.
  DENIED      401/403. The credential was refused, so nothing reached payment
              processing. Separate from REJECTED because recording it as a
              customer decline teaches the belief filter that an account was
              empty on the strength of a wrong API key -- `docs/errors.md`,
              "An authentication failure recorded as a statement about the
              customer's balance".
  LOST        No response. The provider MAY have acted. Reconcile; never
              assume.

`LOST` is the one that costs money when it is collapsed into a failure.

IDEMPOTENCY: WHAT RAZORPAY ACTUALLY PROVIDES, WHICH IS NOT A HEADER. They
document idempotency for RazorpayX payouts (`X-Payout-Idempotency`) and for
refunds, and NONE for `POST /payments/create/recurring`. [VERIFIED]
razorpay.com Payout Idempotency, read 3 September 2026. A header the provider
ignores reads like a guarantee that is not there, so this client sends none.

Two documented properties stand in:

  1. An order's `receipt` "has to be unique" per account and a second create
     with the same value is rejected; `GET /v1/orders?receipt=` finds the one
     that already exists. ORDER CREATION is therefore idempotent on a
     deterministic receipt. [VERIFIED] razorpay.com Create an Order.
  2. "No further payment requests are permitted once the order moves to the
     `paid` state." [VERIFIED] razorpay.com Orders entity.

One order per debit attempt therefore makes a COLLECTED debit at-most-once at
the provider. It is weaker than an idempotency key in two ways this repository
does not paper over: a retried submission gets a rejection rather than a
replayed result, so the caller must still reconcile; and an order sitting in
`attempted` -- a previous payment authorised but not captured -- is not
documented as closed to a second payment. `payment_capture: true` is set on
every order here so that window is not held open, but the residual is real and
is recorded in `docs/results.md` rather than assumed away.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from enum import Enum

API_BASE = "https://api.razorpay.com/v1"

#: Every path this repository can reach, on one screen.
PATHS = {
    "customers": "/customers",
    "orders": "/orders",
    "recurring": "/payments/create/recurring",
    "payments": "/payments",
    "payment": "/payments/{payment_id}",
    "order_payments": "/orders/{order_id}/payments",
    "customer_tokens": "/customers/{customer_id}/tokens",
    "customer_token": "/customers/{customer_id}/tokens/{token_id}",
    "payment_links": "/payment_links",
    "payment_link": "/payment_links/{link_id}",
    "payment_link_cancel": "/payment_links/{link_id}/cancel",
}

#: The credential was refused. 401 is [VERIFIED] against the live API by
#: `scripts/razorpay_ladder.py`; 403 is grouped with it on the same reasoning
#: and has not been observed.
DENIED_STATUSES = (401, 403)

#: Razorpay's documented minimum mandate amount, across every merchant
#: category, is Rs 1. [VERIFIED] razorpay.com UPI AutoPay, read 3 September
#: 2026. A provider floor; any ceiling is ours and lives in configuration.
MIN_AMOUNT_PAISE = 100

#: `max_amount` on a mandate token accepts 500 to 100000000 paise, default
#: 9999900. [VERIFIED] razorpay.com recurring-payments authorisation reference.
MAX_AMOUNT_RANGE_PAISE = (500, 100_000_000)

#: Values Razorpay accepts for `token.frequency` on a mandate.
VALID_FREQUENCIES = frozenset({"daily", "weekly", "monthly", "quarterly",
                               "yearly", "as_presented"})


class Outcome(str, Enum):
    OK = "OK"
    REJECTED = "REJECTED"
    DENIED = "DENIED"
    LOST = "LOST"


@dataclass(frozen=True)
class ApiResponse:
    outcome: Outcome
    status: int | None
    body: dict = field(default_factory=dict)
    #: Taken only from the provider's own `error.code` / `error.description`,
    #: so it is safe to show an operator: it contains no request body and no
    #: credential.
    error_code: str = ""
    error_description: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.OK

    def summary(self) -> dict:
        return {"outcome": self.outcome.value, "status": self.status,
                "error_code": self.error_code,
                "error_description": self.error_description}


class Transport:
    """Basic-auth JSON over `urllib`. No dependency, and injectable.

    Injectable is the point: every offline gate passes a fake and the object
    under test is the real client. The credential becomes a header once, and
    `__repr__` is overridden so it cannot reach a traceback.
    """

    def __init__(self, key_id: str, key_secret: str, timeout: float = 20.0):
        token = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
        self._auth = f"Basic {token}"
        self.timeout = timeout

    def __repr__(self) -> str:
        return "<Transport credential=<redacted>>"

    def request(self, method: str, url: str,
                body: dict | None = None) -> tuple[int | None, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", self._auth)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, _as_dict(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:          # 4xx/5xx WITH a body
            try:
                return e.code, _as_dict(e.read().decode() or "{}")
            except Exception:
                return e.code, {}
        except Exception:                            # socket, DNS, TLS, timeout
            # No detail is returned on purpose: the exception text carries the
            # URL, and the URL carries a customer or payment id. The caller
            # decides what to say about a lost request.
            return None, {}


def _as_dict(raw: str) -> dict:
    try:
        obj = json.loads(raw)
    except ValueError:
        return {}
    return obj if isinstance(obj, dict) else {"items": obj}


class RazorpayApi:
    """Every Razorpay call this repository can make.

    Methods return an `ApiResponse` and never raise for a provider answer. They
    raise `ValueError` for a programming error -- an empty token id, a
    frequency not in Razorpay's list -- because a bug must not be
    indistinguishable from a decline.
    """

    def __init__(self, transport: Transport, api_base: str = API_BASE):
        self.api_base = api_base.rstrip("/")
        self._t = transport
        #: Per-process health counters for the operator console. Not business
        #: state and not persisted.
        self.calls = 0
        self.lost = 0
        #: The last request this client sent, for a proof transcript. Kept here
        #: so a caller needing the exact bytes does not build a second copy of
        #: the body -- which is how one endpoint ends up with two request
        #: shapes and only one of them maintained. Never contains a credential:
        #: the authorisation header is the transport's, not the body's.
        self.last_request: dict = {}

    # ------------------------------------------------------------ internals
    def url(self, key: str, **kw) -> str:
        return self.api_base + PATHS[key].format(**kw)

    def call(self, method: str, url: str,
             body: dict | None = None) -> ApiResponse:
        """The one place an HTTP status becomes an `Outcome`."""
        self.calls += 1
        self.last_request = {"method": method, "url": url, "body": body}
        status, payload = self._t.request(method, url, body)
        if status is None:
            self.lost += 1
            return ApiResponse(Outcome.LOST, None, {},
                               error_description="no response from the "
                                                 "payment provider")
        err = payload.get("error") or {}
        code = str(err.get("code") or "")
        desc = str(err.get("description") or "")
        if status in DENIED_STATUSES:
            return ApiResponse(Outcome.DENIED, status, payload, code, desc)
        if status >= 400:
            return ApiResponse(Outcome.REJECTED, status, payload, code, desc)
        return ApiResponse(Outcome.OK, status, payload)

    # ------------------------------------------------------------ customers
    def create_customer(self, *, name: str, email: str, contact: str,
                        notes: dict | None = None) -> ApiResponse:
        body: dict = {"name": name, "email": email, "contact": contact,
                      # "0" means return the existing customer rather than
                      # erroring when this contact is already registered, so a
                      # retried registration converges instead of failing.
                      "fail_existing": "0"}
        if notes:
            body["notes"] = notes
        return self.call("POST", self.url("customers"), body)

    # --------------------------------------------------------------- orders
    def create_authorization_order(self, *, customer_id: str,
                                   max_amount_paise: int, expire_at: int,
                                   frequency: str, receipt: str,
                                   auth_amount_paise: int = MIN_AMOUNT_PAISE,
                                   notes: dict | None = None) -> ApiResponse:
        """The mandate-registration order.

        The order amount is the AUTHORISATION amount, which for UPI is Rs 1 --
        not the recurring one.
        """
        if frequency not in VALID_FREQUENCIES:
            raise ValueError(f"frequency {frequency!r} is not one Razorpay "
                             f"accepts: {sorted(VALID_FREQUENCIES)}")
        lo, hi = MAX_AMOUNT_RANGE_PAISE
        if not lo <= max_amount_paise <= hi:
            raise ValueError(f"max_amount {max_amount_paise} paise is outside "
                             f"Razorpay's documented range {lo}-{hi}")
        body: dict = {
            "amount": auth_amount_paise,
            "currency": "INR",
            "payment_capture": True,
            "method": "upi",
            "customer_id": customer_id,
            "receipt": receipt[:40],
            "token": {"max_amount": max_amount_paise,
                      "expire_at": expire_at,
                      "frequency": frequency},
        }
        if notes:
            body["notes"] = notes
        return self.call("POST", self.url("orders"), body)

    def create_notification_order(self, *, amount_paise: int, receipt: str,
                                  token_id: str, payment_after: int,
                                  currency: str = "INR",
                                  notes: dict | None = None) -> ApiResponse:
        """The pre-debit order for one subsequent charge.

        `notification.payment_after` is the "UNIX timestamp post which the
        debit is supposed to happen" [VERIFIED] razorpay.com create-subsequent-
        payments, read 3 September 2026. Sending the object is also what takes
        the retry schedule off Razorpay: "We will not attempt any retry if the
        debit fails for tokens with the notification object in the created
        order." That is the whole reason this integration owns its own timing.

        A 2xx here means the instruction was accepted, NOT that the customer
        was told. Only `order.notification.delivered` is evidence of delivery.
        """
        if not token_id:
            raise ValueError("create_notification_order needs a token id")
        if amount_paise < MIN_AMOUNT_PAISE:
            raise ValueError(f"{amount_paise} paise is below Razorpay's "
                             f"documented minimum of {MIN_AMOUNT_PAISE}")
        body: dict = {
            "amount": amount_paise,
            "currency": currency,
            "payment_capture": True,
            "receipt": receipt[:40],
            "notification": {"token_id": token_id,
                             "payment_after": payment_after},
        }
        if notes:
            body["notes"] = notes
        return self.call("POST", self.url("orders"), body)

    def find_order_by_receipt(self, receipt: str) -> ApiResponse:
        """Recover an order whose id was lost. The crash path.

        The receipt is deterministic, so after a restart "did I already create
        this order" has an answer at the provider rather than a second order.
        """
        q = urllib.parse.urlencode({"receipt": receipt, "count": 1})
        return self.call("GET", f"{self.url('orders')}?{q}")

    def fetch_order_payments(self, order_id: str) -> ApiResponse:
        """Payments Razorpay recorded against one order.

        Resolves an ambiguous submission: the charge response was lost, so the
        payment id is unknown but the order id is not.
        """
        return self.call("GET", self.url("order_payments", order_id=order_id))

    # ------------------------------------------------------------- payments
    def create_recurring_payment(self, *, email: str, contact: str,
                                 amount_paise: int, order_id: str,
                                 customer_id: str, token_id: str,
                                 description: str, currency: str = "INR",
                                 notes: dict | None = None) -> ApiResponse:
        """Submit the debit.

        THE SUCCESS RESPONSE IS `{"razorpay_payment_id": "pay_..."}` AND
        NOTHING ELSE -- no `status`, no `error_reason`. [VERIFIED] against the
        create-subsequent-payments reference, read 3 September 2026.

        So an accepted submission says only that Razorpay has the request.
        Reading it as a payment entity -- finding no status, concluding
        "declined" -- records an accepted debit as an empty account, and
        `w3.py:432` then hard-zeroes every balance bin at or above the amount
        for every mandate that customer holds.
        """
        if not token_id:
            raise ValueError("create_recurring_payment needs a token id")
        if not order_id:
            raise ValueError("create_recurring_payment needs an order id")
        body: dict = {
            "email": email,
            "contact": contact,
            "amount": amount_paise,
            "currency": currency,
            "order_id": order_id,
            "customer_id": customer_id,
            "token": token_id,
            "recurring": True,
            "description": description[:255],
        }
        if notes:
            body["notes"] = notes
        return self.call("POST", self.url("recurring"), body)

    def fetch_payment(self, payment_id: str) -> ApiResponse:
        """The authoritative outcome, on demand. Not polled in a loop."""
        return self.call("GET", self.url("payment", payment_id=payment_id))

    # --------------------------------------------------------------- tokens
    def fetch_customer_tokens(self, customer_id: str) -> ApiResponse:
        return self.call("GET",
                         self.url("customer_tokens", customer_id=customer_id))

    def delete_token(self, customer_id: str, token_id: str) -> ApiResponse:
        """Cancel a mandate. Razorpay answers `{"deleted": true}`."""
        return self.call("DELETE",
                         self.url("customer_token", customer_id=customer_id,
                                  token_id=token_id))

    # -------------------------------------------------------- payment links
    def create_payment_link(self, body: dict) -> ApiResponse:
        return self.call("POST", self.url("payment_links"), body)

    def fetch_payment_link(self, link_id: str) -> ApiResponse:
        return self.call("GET", self.url("payment_link", link_id=link_id))

    def find_payment_link_by_reference(self, reference_id: str) -> ApiResponse:
        q = urllib.parse.urlencode({"reference_id": reference_id})
        return self.call("GET", f"{self.url('payment_links')}?{q}")

    def cancel_payment_link(self, link_id: str) -> ApiResponse:
        return self.call("POST",
                         self.url("payment_link_cancel", link_id=link_id), {})

    # ---------------------------------------------------------- diagnostics
    def ping(self) -> ApiResponse:
        """Reachable, and is the credential accepted?

        `GET /payments?count=1` reads, costs nothing, and answers 401 rather
        than 404 on a bad credential -- so it separates "unreachable" from
        "unauthenticated", which a health check must.
        """
        return self.call("GET", f"{self.url('payments')}?count=1")


# ------------------------------------------------------------------ parsing
def parse_payment_id(body: dict) -> str:
    """Payment id from a create/recurring response or a payment entity.

    Both spellings, because a create and a fetch flow into the same
    reconciliation code and the branch belongs here, once.
    """
    return str(body.get("razorpay_payment_id") or body.get("id") or "")


def parse_token_from_payment(body: dict) -> str:
    """`token_id` off a payment entity.

    Razorpay has used both a flat `token_id` and a nested `token` object across
    its integrations, so both are read.
    """
    tid = body.get("token_id")
    if tid:
        return str(tid)
    tok = body.get("token")
    if isinstance(tok, str):
        return tok
    if isinstance(tok, dict):
        return str(tok.get("id") or "")
    return ""


def parse_token_status(token: dict) -> str:
    """`recurring_details.status` -- the field that decides chargeability."""
    rd = token.get("recurring_details")
    if isinstance(rd, dict) and rd.get("status"):
        return str(rd["status"])
    return str(token.get("status") or "")


def parse_order_id(body: dict) -> str:
    return str(body.get("id") or "")


def first_item(body: dict) -> dict:
    """First entry of a Razorpay collection response, or {}."""
    items = body.get("items")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    return {}
