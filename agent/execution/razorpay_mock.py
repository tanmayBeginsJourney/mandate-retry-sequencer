"""A deterministic stand-in for Razorpay that fails on purpose.

WHY IT MUST NOT BE A HAPPY PATH. A mock that always answers `captured` proves
the code can parse a success. The interesting half of this system is what
happens when the rail declines, loses a response, delivers the same webhook
twice, delivers two in the wrong order, or answers a debit for a mandate the
customer paused ten minutes ago. Those faults are in the model, not behind a
switch somebody has to remember.

WHAT IT MODELS: customers, registration orders, token issuance and
confirmation; a token that can be rejected, cancelled, paused or resumed; the
receipt-uniqueness rule; one payment per paid order, including the refusal of a
second; a debit that captures, declines with a real Razorpay `error_reason`, or
disappears without an answer; and the webhook stream that follows, with
duplicates, reordering and drops.

WHAT IT DOES NOT MODEL: money, settlement, NPCI, or the UPI app. It answers the
way Razorpay's documentation says Razorpay answers, and nothing more. IT IS NOT
EVIDENCE ABOUT THE LIVE RAIL and no result here treats it as any.

DETERMINISM. Every choice comes from a seeded `random.Random`, so a failing
test is reproducible. `MockPlan` overrides the seed entirely when one exact
sequence is under test, so a gate never has to search for a lucky seed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import random
import time
from dataclasses import dataclass, field

from agent.execution.razorpay_api import (MIN_AMOUNT_PAISE, ApiResponse,
                                          Outcome, VALID_FREQUENCIES)

#: Reasons drawn from Razorpay's published `error_reason` list, one per family
#: `agent/ports.py` names. Spelled exactly as their document spells them:
#: `REASON_FAMILY` is keyed on those strings, so a near-miss would silently
#: exercise the AMBIGUOUS default instead of the mapping under test.
DECLINE_REASONS = (
    "insufficient_funds",          # FUNDS
    "bank_technical_error",        # TECH
    "transaction_limit_exceeded",  # LIMIT
    "funds_blocked_by_mandate",    # LIEN
    "debit_instrument_blocked",    # ACCOUNT_SHUT
    "deemed_transaction",          # INDETERMINATE -- must never be retried
)

#: Reasons the random path may produce. `deemed_transaction` and
#: `debit_instrument_blocked` are excluded because both are absorbing in a way
#: that would end a run early and mask the behaviour a sweep is measuring;
#: tests that want them ask for them by name through `MockPlan.debits`.
_RANDOM_DECLINES = DECLINE_REASONS[:4]


@dataclass
class MockPlan:
    """An explicit script, for tests that need one exact sequence.

    Anything left at its default falls back to seeded random behaviour, so a
    test pins the one thing it cares about without describing a whole run.
    """
    #: Consumed in order by `create_recurring_payment`. Each entry is
    #: "captured", "failed:<reason>", or "lost".
    debits: list[str] = field(default_factory=list)
    #: The status registration settles on.
    token_status: str = "confirmed"
    #: Fail this many order creates before succeeding, to exercise retry.
    order_failures: int = 0
    #: Emit every webhook twice, with the SAME event id.
    duplicate_webhooks: bool = False
    #: Emit each payment's webhooks newest-first.
    reorder_webhooks: bool = False
    #: Emit none at all, so reconciliation has to find the answer itself.
    drop_webhooks: bool = False


@dataclass
class _Order:
    id: str
    receipt: str
    amount: int
    token_id: str
    payment_after: int
    status: str = "created"          # created | attempted | paid
    payment_id: str = ""


class MockRazorpayApi:
    """The `RazorpayApi` surface, without a socket.

    A separate class rather than a `RazorpayApi` with a fake transport, because
    half of what it models is provider STATE -- receipt uniqueness, one payment
    per order, a token that can be cancelled -- and that state has nowhere to
    live inside a transport stub.
    """

    def __init__(self, seed: int = 7, plan: MockPlan | None = None,
                 clock=None):
        self.rng = random.Random(seed)
        self.plan = plan or MockPlan()
        self._clock = clock or (lambda: int(time.time()))
        self.api_base = "mock://razorpay"
        self.calls = 0
        self.lost = 0
        self._n = 0
        self._customers: dict[str, dict] = {}
        self._orders: dict[str, _Order] = {}
        self._receipts: dict[str, str] = {}
        self._tokens: dict[str, dict] = {}
        self._payments: dict[str, dict] = {}
        self._links: dict[str, dict] = {}
        self._debits = list(self.plan.debits)
        #: Webhooks Razorpay would have sent. The harness drains and posts
        #: them, so the offline flow goes through the same signature
        #: verification and ingestion code as the live one.
        self._outbox: list[tuple[str, str, dict]] = []

    # ------------------------------------------------------------- helpers
    def url(self, key: str, **kw) -> str:
        """The URL the real client would have used.

        The executor records it in its proof transcript. `live/tests/
        test_flow.py` gate F1 asserts this class implements every public method
        of `RazorpayApi`, because a missing one fails as an AttributeError
        inside a `try` somewhere and looks like a business rule refusing.
        """
        from agent.execution.razorpay_api import PATHS
        return self.api_base + PATHS[key].format(**kw)

    def call(self, method: str, url: str, body: dict | None = None) -> ApiResponse:
        raise NotImplementedError(
            "MockRazorpayApi models provider state, not raw HTTP. Call the "
            "named methods; there is no generic escape hatch to a rail that "
            "does not exist.")

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}_MOCK{self._n:08d}"

    def _ok(self, body: dict) -> ApiResponse:
        self.calls += 1
        return ApiResponse(Outcome.OK, 200, body)

    def _rejected(self, code: str, desc: str, status: int = 400) -> ApiResponse:
        self.calls += 1
        return ApiResponse(Outcome.REJECTED, status,
                           {"error": {"code": code, "description": desc}},
                           code, desc)

    def _lost(self) -> ApiResponse:
        self.calls += 1
        self.lost += 1
        return ApiResponse(Outcome.LOST, None, {},
                           error_description="no response from the payment "
                                             "provider")

    def _emit(self, event_type: str, key: str, entity: dict) -> None:
        if self.plan.drop_webhooks:
            return
        event_id = self._id("evt")
        body = {"entity": "event", "account_id": "acc_MOCK",
                "event": event_type, "contains": [key],
                "payload": {key: {"entity": entity}},
                "created_at": self._clock()}
        self._outbox.append((event_id, event_type, body))
        if self.plan.duplicate_webhooks:
            # The SAME event id. That is what a Razorpay redelivery looks like,
            # and dedup keyed on the id is the only thing that catches it.
            self._outbox.append((event_id, event_type, body))

    def drain_webhooks(self) -> list[tuple[str, str, dict]]:
        """Take everything queued, reordering if the plan says to."""
        out, self._outbox = self._outbox, []
        return list(reversed(out)) if self.plan.reorder_webhooks else out

    # ----------------------------------------------------------- customers
    def create_customer(self, *, name: str, email: str, contact: str,
                        notes: dict | None = None) -> ApiResponse:
        for c in self._customers.values():
            if c["email"] == email and c["contact"] == contact:
                return self._ok(c)                  # fail_existing="0"
        cid = self._id("cust")
        ent = {"id": cid, "entity": "customer", "name": name, "email": email,
               "contact": contact, "created_at": self._clock()}
        self._customers[cid] = ent
        return self._ok(ent)

    # -------------------------------------------------------------- orders
    def _new_order(self, *, amount: int, receipt: str, token_id: str,
                   payment_after: int, extra: dict) -> ApiResponse:
        if self.plan.order_failures > 0:
            self.plan.order_failures -= 1
            return self._rejected("SERVER_ERROR",
                                  "The server encountered an error", 502)
        if receipt in self._receipts:
            # The real API's wording, because the recovery path matches on it.
            # A mock with different phrasing would leave that branch untested.
            return self._rejected(
                "BAD_REQUEST_ERROR",
                f"An order with receipt {receipt} already exists")
        oid = self._id("order")
        self._orders[oid] = _Order(id=oid, receipt=receipt, amount=amount,
                                   token_id=token_id,
                                   payment_after=payment_after)
        self._receipts[receipt] = oid
        return self._ok({"id": oid, "entity": "order", "amount": amount,
                         "amount_paid": 0, "amount_due": amount,
                         "currency": "INR", "receipt": receipt,
                         "status": "created", "attempts": 0,
                         "created_at": self._clock(), **extra})

    def create_authorization_order(self, *, customer_id: str,
                                   max_amount_paise: int, expire_at: int,
                                   frequency: str, receipt: str,
                                   auth_amount_paise: int = MIN_AMOUNT_PAISE,
                                   notes: dict | None = None) -> ApiResponse:
        if frequency not in VALID_FREQUENCIES:
            raise ValueError(f"frequency {frequency!r} is not one Razorpay "
                             f"accepts")
        if customer_id not in self._customers:
            return self._rejected("BAD_REQUEST_ERROR",
                                  "Customer does not exist")
        r = self._new_order(amount=auth_amount_paise, receipt=receipt,
                            token_id=f"pending:{customer_id}",
                            payment_after=0,
                            extra={"token": {"max_amount": max_amount_paise,
                                             "expire_at": expire_at,
                                             "frequency": frequency}})
        return r

    def create_notification_order(self, *, amount_paise: int, receipt: str,
                                  token_id: str, payment_after: int,
                                  currency: str = "INR",
                                  notes: dict | None = None) -> ApiResponse:
        if not token_id:
            raise ValueError("create_notification_order needs a token id")
        if amount_paise < MIN_AMOUNT_PAISE:
            raise ValueError(f"{amount_paise} paise is below the documented "
                             f"minimum of {MIN_AMOUNT_PAISE}")
        if token_id not in self._tokens:
            return self._rejected("BAD_REQUEST_ERROR", "Token does not exist")
        r = self._new_order(
            amount=amount_paise, receipt=receipt, token_id=token_id,
            payment_after=payment_after,
            extra={"notification": {"token_id": token_id,
                                    "payment_after": payment_after,
                                    "id": self._id("notification")}})
        if r.ok:
            note = r.body["notification"]
            # Delivery is a separate fact from order creation and the webhook
            # is its only evidence, which is exactly why it is a separate emit.
            self._emit("order.notification.delivered", "notification",
                       {"id": note["id"], "order_id": r.body["id"],
                        "token_id": token_id, "status": "delivered",
                        "payment_after": payment_after,
                        "delivered_at": self._clock()})
        return r

    def find_order_by_receipt(self, receipt: str) -> ApiResponse:
        o = self._orders.get(self._receipts.get(receipt, ""))
        if o is None:
            return self._ok({"entity": "collection", "count": 0, "items": []})
        return self._ok({"entity": "collection", "count": 1,
                         "items": [{"id": o.id, "entity": "order",
                                    "amount": o.amount, "receipt": o.receipt,
                                    "status": o.status, "currency": "INR"}]})

    def fetch_order_payments(self, order_id: str) -> ApiResponse:
        o = self._orders.get(order_id)
        if o is None:
            return self._rejected("BAD_REQUEST_ERROR", "Order does not exist",
                                  400)
        items = [dict(self._payments[o.payment_id])] if o.payment_id else []
        return self._ok({"entity": "collection", "count": len(items),
                         "items": items})

    # ------------------------------------------------------- authorisation
    def authorize(self, order_id: str, *,
                  status: str | None = None) -> ApiResponse:
        """Stand in for the customer approving the mandate in their UPI app.

        NOT A RAZORPAY ENDPOINT, and named so it cannot be mistaken for one.
        Registration is completed by a human on a phone; no server call does
        it. This exists so the offline suite can reach the post-authorisation
        states at all.
        """
        o = self._orders.get(order_id)
        if o is None or not o.token_id.startswith("pending:"):
            return self._rejected("BAD_REQUEST_ERROR",
                                  "Not a registration order")
        customer_id = o.token_id.split(":", 1)[1]
        st = status or self.plan.token_status
        tid, pid = self._id("token"), self._id("pay")
        token = {"id": tid, "entity": "token", "token": tid[-14:],
                 "method": "upi",
                 "vpa": {"username": "mock", "handle": "okmock"},
                 "recurring": True,
                 "recurring_details": {"status": st, "failure_reason": None},
                 "max_amount": 1_500_000,
                 "expired_at": self._clock() + 10 * 365 * 24 * 3600,
                 "created_at": self._clock(), "customer_id": customer_id}
        self._tokens[tid] = token
        payment = {"id": pid, "entity": "payment", "amount": o.amount,
                   "currency": "INR",
                   "status": "captured" if st == "confirmed" else "failed",
                   "order_id": order_id, "method": "upi",
                   "captured": st == "confirmed", "customer_id": customer_id,
                   "token_id": tid,
                   "error_reason": (None if st == "confirmed"
                                    else "mandate_creation_declined"),
                   "created_at": self._clock()}
        self._payments[pid] = payment
        o.status = "paid"
        o.payment_id = pid
        o.token_id = tid
        self._emit(f"token.{st}", "token", token)
        return self._ok({"payment_id": pid, "token_id": tid,
                         "order_id": order_id, "token_status": st,
                         "payment": payment})

    def adopt_token(self, *, token_id: str, customer_id: str,
                    max_amount_paise: int, expire_at: int = 0,
                    status: str = "confirmed", email: str = "",
                    contact: str = "") -> dict:
        """Re-declare a token this mock issued in an EARLIER PROCESS.

        Provider state lives in this object's dictionaries, so it dies with the
        process. A durable caller -- one that wrote `rzp_token_id` to a
        database -- restarts holding a token id the mock has never heard of,
        and every later call answers "Token does not exist" while the caller's
        own record says the mandate is confirmed and chargeable. Real
        Razorpay remembers its tokens across our restarts; this is the smallest
        thing that makes the mock do the same.

        NOT A RAZORPAY ENDPOINT, and named so it cannot be mistaken for one.
        The caller passes plain values -- there is nothing importable from
        `live/` here, and there must not be: this package is a leaf.

        An id already present is left alone. A restart must not overwrite a
        token whose status has moved on inside this process.
        """
        if not token_id or not customer_id:
            raise ValueError("adopt_token needs both a token id and a "
                             "customer id")
        now = self._clock()
        self._customers.setdefault(
            customer_id, {"id": customer_id, "entity": "customer", "name": "",
                          "email": email, "contact": contact,
                          "created_at": now})
        token = self._tokens.get(token_id)
        if token is None:
            token = {"id": token_id, "entity": "token",
                     "token": token_id[-14:], "method": "upi",
                     "vpa": {"username": "mock", "handle": "okmock"},
                     "recurring": True,
                     "recurring_details": {"status": status,
                                           "failure_reason": None},
                     "max_amount": int(max_amount_paise),
                     "expired_at": int(expire_at) or now + 10 * 365 * 24 * 3600,
                     "created_at": now, "customer_id": customer_id}
            self._tokens[token_id] = token
        return dict(token)

    def adopt_order(self, *, order_id: str, receipt: str, amount_paise: int,
                    token_id: str, payment_after: int = 0,
                    status: str = "created", payment_id: str = "") -> dict:
        """Re-declare an order this mock created in an EARLIER PROCESS.

        The companion to `adopt_token`, and needed for the same reason. A
        pre-debit order outstanding when the process dies leaves a durable row
        carrying its `order_id`; without this the debit that follows is
        refused with "Order does not exist", which the executor cannot
        distinguish from a request that was sent and lost, so the attempt ends
        UNKNOWN and waits for a reconciliation the mock cannot answer either.

        NOT A RAZORPAY ENDPOINT. An order already present is left alone.
        """
        if not order_id or not receipt:
            raise ValueError("adopt_order needs both an order id and a receipt")
        if order_id in self._orders:
            return {"id": order_id}
        self._orders[order_id] = _Order(
            id=order_id, receipt=receipt, amount=int(amount_paise),
            token_id=token_id, payment_after=int(payment_after),
            status=status, payment_id=payment_id)
        self._receipts.setdefault(receipt, order_id)
        return {"id": order_id, "receipt": receipt, "status": status}

    def set_token_status(self, token_id: str, status: str) -> ApiResponse:
        """The customer pausing, resuming or revoking the mandate later."""
        tok = self._tokens.get(token_id)
        if tok is None:
            return self._rejected("BAD_REQUEST_ERROR", "Token does not exist",
                                  400)
        tok["recurring_details"]["status"] = status
        self._emit(f"token.{status}", "token", tok)
        return self._ok(tok)

    # ------------------------------------------------------------ payments
    def _next_debit(self) -> str:
        if self._debits:
            return self._debits.pop(0)
        roll = self.rng.random()
        if roll < 0.55:
            return "captured"
        if roll < 0.92:
            return "failed:" + self.rng.choice(_RANDOM_DECLINES)
        return "lost"

    def create_recurring_payment(self, *, email: str, contact: str,
                                 amount_paise: int, order_id: str,
                                 customer_id: str, token_id: str,
                                 description: str, currency: str = "INR",
                                 notes: dict | None = None) -> ApiResponse:
        if not token_id:
            raise ValueError("create_recurring_payment needs a token id")
        if not order_id:
            raise ValueError("create_recurring_payment needs an order id")
        o = self._orders.get(order_id)
        if o is None:
            return self._rejected("BAD_REQUEST_ERROR", "Order does not exist")
        if o.status == "paid":
            # THE PROPERTY THE CRASH ARGUMENT RESTS ON, and it is the only one
            # Razorpay documents: "No further payment requests are permitted
            # once the order moves to the `paid` state." A submission retried
            # after a lost response gets this, not a second debit. An order
            # left `attempted` is NOT modelled as closed, because the docs do
            # not say it is -- see `razorpay_api.py`.
            return self._rejected("BAD_REQUEST_ERROR", "Order already paid")
        tok = self._tokens.get(token_id)
        if tok is None:
            return self._rejected("BAD_REQUEST_ERROR", "Token does not exist")
        st = tok["recurring_details"]["status"]
        if st != "confirmed":
            return self._rejected(
                "BAD_REQUEST_ERROR",
                f"Token is not in the confirmed state (status: {st})")
        if amount_paise > int(tok.get("max_amount") or 0):
            return self._rejected(
                "BAD_REQUEST_ERROR",
                "Payment amount exceeds the mandate's max amount")

        verdict = self._next_debit()
        pid = self._id("pay")
        base = {"id": pid, "entity": "payment", "amount": amount_paise,
                "currency": currency, "order_id": order_id, "method": "upi",
                "customer_id": customer_id, "token_id": token_id,
                "email": email, "contact": contact,
                "created_at": self._clock()}

        if verdict == "lost":
            # The provider recorded a submission; the caller never learns the
            # payment id. Reconciliation has to find it from the order, which
            # is the ambiguous-submission case in full.
            o.status = "attempted"
            o.payment_id = pid
            self._payments[pid] = {**base, "status": "created",
                                   "error_reason": None}
            return self._lost()

        if verdict.startswith("failed:"):
            reason = verdict.split(":", 1)[1]
            payment = {**base, "status": "failed", "captured": False,
                       "error_code": "BAD_REQUEST_ERROR",
                       "error_description": "Payment failed",
                       "error_source": "bank", "error_step": "payment_authy",
                       "error_reason": reason}
            self._payments[pid] = payment
            o.status, o.payment_id = "attempted", pid
            self._emit("payment.failed", "payment", payment)
            return self._ok({"razorpay_payment_id": pid})

        payment = {**base, "status": "captured", "captured": True,
                   "error_reason": None}
        self._payments[pid] = payment
        o.status, o.payment_id = "paid", pid
        # Both events, in the documented order. `reorder_webhooks` inverts them
        # so the out-of-order gate has something genuine to invert.
        self._emit("payment.authorized", "payment",
                   {**payment, "status": "authorized", "captured": False})
        self._emit("payment.captured", "payment", payment)
        return self._ok({"razorpay_payment_id": pid})

    def settle(self, payment_id: str, status: str = "captured",
               reason: str | None = None) -> ApiResponse:
        """The rail eventually making up its mind about a pending payment.

        NOT A RAZORPAY ENDPOINT. A payment left `created` by a lost response
        does resolve at some point -- the money moves or it does not -- and a
        mock with no way to express that would leave reconciliation untestable
        against anything but a payment that was already finished.
        """
        p = self._payments.get(payment_id)
        if p is None:
            return self._rejected("BAD_REQUEST_ERROR",
                                  "The id provided does not exist", 400)
        p["status"] = status
        p["captured"] = status == "captured"
        p["error_reason"] = reason
        order = self._orders.get(str(p.get("order_id") or ""))
        if order is not None:
            order.status = "paid" if status == "captured" else "attempted"
        self._emit(f"payment.{status}", "payment", p)
        return self._ok(dict(p))

    def fetch_payment(self, payment_id: str) -> ApiResponse:
        p = self._payments.get(payment_id)
        if p is None:
            return self._rejected("BAD_REQUEST_ERROR",
                                  "The id provided does not exist", 400)
        return self._ok(dict(p))

    # -------------------------------------------------------------- tokens
    def fetch_customer_tokens(self, customer_id: str) -> ApiResponse:
        items = [t for t in self._tokens.values()
                 if t.get("customer_id") == customer_id]
        return self._ok({"entity": "collection", "count": len(items),
                         "items": items})

    def delete_token(self, customer_id: str, token_id: str) -> ApiResponse:
        tok = self._tokens.get(token_id)
        if tok is None or tok.get("customer_id") != customer_id:
            return self._rejected("BAD_REQUEST_ERROR", "Token does not exist",
                                  400)
        tok["recurring_details"]["status"] = "cancelled"
        self._emit("token.cancelled", "token", tok)
        return self._ok({"deleted": True})

    # -------------------------------------------------------- payment links
    def create_payment_link(self, body: dict) -> ApiResponse:
        ref = str(body.get("reference_id") or "")
        for link in self._links.values():
            if ref and link.get("reference_id") == ref:
                return self._rejected(
                    "BAD_REQUEST_ERROR",
                    f"Payment link with reference id {ref} already exists")
        lid = self._id("plink")
        link = {"id": lid, "entity": "payment_link", "status": "created",
                "amount": body.get("amount"), "currency": "INR",
                "reference_id": ref,
                "short_url": f"https://rzp.io/i/{lid[-8:]}"}
        self._links[lid] = link
        return self._ok(link)

    def fetch_payment_link(self, link_id: str) -> ApiResponse:
        link = self._links.get(link_id)
        if link is None:
            return self._rejected("BAD_REQUEST_ERROR",
                                  "Payment link does not exist", 400)
        return self._ok(dict(link))

    def find_payment_link_by_reference(self, reference_id: str) -> ApiResponse:
        items = [dict(x) for x in self._links.values()
                 if x.get("reference_id") == reference_id]
        return self._ok({"entity": "collection", "count": len(items),
                         "items": items})

    def cancel_payment_link(self, link_id: str) -> ApiResponse:
        link = self._links.get(link_id)
        if link is None:
            return self._rejected("BAD_REQUEST_ERROR",
                                  "Payment link does not exist", 400)
        link["status"] = "cancelled"
        return self._ok(dict(link))

    # ---------------------------------------------------------- diagnostics
    def ping(self) -> ApiResponse:
        return self._ok({"entity": "collection", "count": 0, "items": []})


def sign(body: dict, secret: str) -> tuple[str, str]:
    """The exact bytes and the signature Razorpay would send for `body`.

    Returned together because the signature covers those bytes and no others.
    A caller that re-serialises the dict gets a different string and a valid
    signature for a body it is not sending; handing back both makes that
    mistake awkward to make.
    """
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True)
    sig = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return raw, sig
