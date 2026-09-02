"""Webhook verification, idempotent ingestion, and state reconciliation.

THE THREE PROPERTIES RAZORPAY DOCUMENTS, AND WHAT EACH ONE COSTS IF IGNORED.
[VERIFIED] razorpay.com webhook validation and best-practice pages, read
3 September 2026.

  1. The signature is HMAC-SHA256 over the RAW REQUEST BODY, keyed by the
     webhook secret, in `X-Razorpay-Signature`. Their documentation says, in
     as many words, do not parse or cast the body before signing it. So the
     bytes are verified first and parsed second, and `verify` takes `bytes`
     rather than a dict so a caller physically cannot hand it a re-serialised
     object.

  2. Delivery is AT LEAST ONCE, and `x-razorpay-event-id` is unique per event.
     Ignoring that means applying the same payment twice. Dedup is a primary
     key in SQLite, not a check in Python -- see `store.record_event`.

  3. Events MAY ARRIVE OUT OF ORDER. Ignoring that means a redelivered
     `payment.authorized` overwriting a `payment.captured` that already
     landed, and a collected cycle going back to uncollected. Every state
     change goes through `domain.advance`, which refuses to move backwards.

AND ONE MORE, WHICH IS A LATENCY BUDGET RATHER THAN A SEMANTIC: the endpoint
must answer 2xx within five seconds or the event is resent. So ingestion does
the smallest durable thing -- verify, insert, return -- and the interpretation
happens after the response. No LLM call, no provider call, no belief update
runs inside the request. `api.py` holds that split; this module provides the
two halves.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from live.domain import (TOKEN_STATUS_STATE, AttemptState, Transition,
                         WebhookEvent, advance, advance_mandate,
                         from_payment_entity)
from live.store import Store

SIGNATURE_HEADER = "X-Razorpay-Signature"
EVENT_ID_HEADER = "X-Razorpay-Event-Id"

#: Razorpay rejects webhook bodies far smaller than this; the cap is here so a
#: hostile or broken sender cannot make the process read an unbounded body into
#: memory before the signature is even checked. One megabyte is roughly two
#: orders of magnitude above the largest real payload.
MAX_BODY_BYTES = 1_048_576

#: The events this service acts on. Anything else is stored and acknowledged
#: but changes no state -- an unknown event is not an error, and 4xx-ing it
#: would make Razorpay retry it for 24 hours and then disable the webhook.
HANDLED_EVENTS = frozenset({
    "payment.authorized",
    "payment.captured",
    "payment.failed",
    "order.notification.delivered",
    "order.notification.failed",
    "token.confirmed",
    "token.rejected",
    "token.cancelled",
    "token.paused",
})


class WebhookRejected(Exception):
    """The delivery is not acceptable. Carries the status to answer with."""

    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def verify(raw_body: bytes, signature: str, secret: str) -> bool:
    """Constant-time HMAC-SHA256 check over the exact bytes received.

    Takes `bytes`, never a dict. A dict would have to be re-serialised to be
    hashed, and Python's serialisation is not Razorpay's -- different key
    order, different separators -- so the check would fail on every genuine
    event and the obvious "fix" is to stop checking.
    """
    if not signature or not secret:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


@dataclass(frozen=True)
class Ingested:
    """What `ingest` did. The HTTP layer turns this into a status code."""
    accepted: bool
    duplicate: bool
    event_id: str
    event_type: str
    detail: str


def ingest(store: Store, *, raw_body: bytes, signature: str, event_id: str,
           secret: str, now: int | None = None) -> Ingested:
    """Verify and durably record one delivery. Does NOT interpret it.

    This is everything that must happen before the 2xx. It is deliberately
    small: a signature check, a JSON parse for the event name, and one insert.

    A BAD SIGNATURE IS STILL RECORDED. The row goes in with
    `signature_valid=0` and the caller answers 400. Dropping it silently would
    leave no trace of a forgery attempt, which is the one delivery where the
    log matters most. The payload is stored either way because a signature
    dispute cannot be settled without the bytes that failed.
    """
    now = int(time.time()) if now is None else now

    if len(raw_body) > MAX_BODY_BYTES:
        raise WebhookRejected(413, "webhook body too large")

    valid = verify(raw_body, signature, secret)

    try:
        body = json.loads(raw_body.decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        body = {}

    event_type = str(body.get("event") or "")

    # An absent event id would collapse every delivery onto one dedup key, so
    # the body's own identity is used as the fallback. Hashing the raw bytes
    # means two genuinely identical payloads dedupe and two different ones do
    # not, which is the best available answer when the header is missing.
    key = (event_id or "").strip() or (
        "sha256:" + hashlib.sha256(raw_body).hexdigest())

    fresh = store.record_event(WebhookEvent(
        event_id=key, event_type=event_type, received_at=now,
        signature_valid=valid, payload=raw_body.decode("utf-8", "replace")))

    if not valid:
        raise WebhookRejected(400, "signature verification failed")

    if not fresh:
        # Razorpay redelivering an event it already sent. Acknowledged, and
        # deliberately not reprocessed: the first delivery either succeeded or
        # is on the unprocessed queue, and doing the work twice is the exact
        # harm the event id exists to prevent.
        return Ingested(True, True, key, event_type,
                        "duplicate delivery; already recorded")

    return Ingested(True, False, key, event_type, "recorded")


# ==================================================================== apply
@dataclass(frozen=True)
class Applied:
    """The result of interpreting one stored event."""
    changed: bool
    detail: str
    mandate_id: str = ""
    attempt_id: str = ""


def apply_event(store: Store, event: WebhookEvent, *,
                source: str = "webhook") -> Applied:
    """Interpret one recorded event against durable state.

    Runs AFTER the HTTP response. Safe to run twice on the same event: every
    write goes through a monotonic transition, so a second application is a
    no-op rather than a second effect. That is what makes crash recovery a
    matter of replaying `store.unprocessed_events()` rather than reasoning
    about which side of the crash each event fell on.
    """
    body = _payload(event)
    kind = event.event_type

    if kind not in HANDLED_EVENTS:
        return Applied(False, f"no handler for {kind or 'an unnamed event'}")

    if kind.startswith("payment."):
        return _apply_payment(store, event, body, source)
    if kind.startswith("token."):
        return _apply_token(store, event, body, source)
    if kind.startswith("order.notification."):
        return _apply_notification(store, event, body, source)
    return Applied(False, f"no handler for {kind}")


def _payload(event: WebhookEvent) -> dict:
    try:
        obj = json.loads(event.payload)
        return obj if isinstance(obj, dict) else {}
    except (ValueError, TypeError):
        return {}


def _entity(body: dict, key: str) -> dict:
    ent = ((body.get("payload") or {}).get(key) or {}).get("entity")
    return ent if isinstance(ent, dict) else {}


def _apply_payment(store: Store, event: WebhookEvent, body: dict,
                   source: str) -> Applied:
    ent = _entity(body, "payment")
    if not ent:
        return Applied(False, "payment event carried no entity")

    view = from_payment_entity(ent)

    # Correlate. The payment id is the strong join; the order id is the one we
    # have when the response that would have told us the payment id was lost.
    attempt = (store.attempt_by_payment(view.payment_id)
               or store.attempt_by_order(view.order_id))
    if attempt is None:
        return Applied(False, f"no local attempt for payment "
                              f"{view.payment_id or '?'} / order "
                              f"{view.order_id or '?'}")

    verdict = advance(attempt.state, view.state)
    store.record_transition(
        "attempt", attempt.id, attempt.state.value, view.state.value,
        verdict.value, source,
        detail=view.raw_reason or ent.get("status") or "")

    if verdict is Transition.IGNORED_STALE:
        # Either a duplicate or an event that arrived after a newer one. Both
        # are expected and neither is an error.
        return Applied(False, f"{view.state.value} does not follow "
                              f"{attempt.state.value}; ignored as stale",
                       attempt.mandate_id, attempt.id)

    if verdict is Transition.CONFLICT:
        attempt.conflicted = True
        store.put_attempt(attempt)
        return Applied(True, f"CONFLICT: provider reported "
                             f"{view.state.value} for an attempt already "
                             f"{attempt.state.value}; kept the first and "
                             f"flagged for review",
                       attempt.mandate_id, attempt.id)

    attempt.state = view.state
    if view.payment_id:
        attempt.payment_id = view.payment_id
    if view.outcome_code:
        attempt.outcome_code = view.outcome_code
    if view.raw_reason:
        attempt.raw_reason = view.raw_reason
    if view.state in (AttemptState.SUCCEEDED, AttemptState.FAILED):
        attempt.resolved_at = event.received_at
    store.put_attempt(attempt)
    return Applied(True, f"attempt -> {view.state.value}",
                   attempt.mandate_id, attempt.id)


def _apply_token(store: Store, event: WebhookEvent, body: dict,
                 source: str) -> Applied:
    ent = _entity(body, "token")
    token_id = str(ent.get("id") or "")
    mandate = store.mandate_by_token(token_id)
    if mandate is None:
        return Applied(False, f"no local mandate for token {token_id or '?'}")

    # The event name and the entity can disagree when an event is redelivered
    # after the token moved on. The ENTITY is the snapshot Razorpay took when
    # the event fired, so it is what the event is about; the name is a label.
    status = ""
    rd = ent.get("recurring_details")
    if isinstance(rd, dict):
        status = str(rd.get("status") or "")
    if not status:
        status = event.event_type.split(".", 1)[-1]

    state = TOKEN_STATUS_STATE.get(status)
    if state is None:
        return Applied(False, f"unknown token status {status!r}; state left "
                              f"at {mandate.state.value}")

    verdict = advance_mandate(mandate.state, state)
    store.record_transition("mandate", mandate.id, mandate.state.value,
                            state.value, verdict.value, source, status)
    if verdict is not Transition.APPLIED:
        return Applied(False, f"mandate already {mandate.state.value}; "
                              f"{state.value} ignored", mandate.id)

    mandate.state = state
    mandate.token_status = status
    if ent.get("max_amount"):
        mandate.max_amount_paise = int(ent["max_amount"])
    if ent.get("expired_at"):
        mandate.expire_at = int(ent["expired_at"])
    store.put_mandate(mandate)
    return Applied(True, f"mandate -> {state.value}", mandate.id)


def _apply_notification(store: Store, event: WebhookEvent, body: dict,
                        source: str) -> Applied:
    """`order.notification.delivered` / `.failed`.

    THIS IS THE ONLY EVIDENCE THAT THE PRE-DEBIT NOTICE REACHED THE CUSTOMER.
    A successful `POST /v1/orders` means Razorpay accepted the instruction to
    send one, which is a different fact. NPCI requires the notice 24 hours
    ahead of the debit, so the distinction is regulatory, not cosmetic.
    """
    ent = _entity(body, "notification")
    order_id = str(ent.get("order_id") or "")
    attempt = store.attempt_by_order(order_id)
    if attempt is None:
        return Applied(False, f"no local attempt for order {order_id or '?'}")

    delivered = event.event_type.endswith(".delivered")
    if not delivered:
        # A failed notice does not fail the payment -- nothing was charged. It
        # blocks the debit, which the service checks before submitting, so the
        # attempt state is left where it is and the fact is recorded.
        store.record_transition("attempt", attempt.id, attempt.state.value,
                                attempt.state.value,
                                Transition.APPLIED.value, source,
                                "notification failed")
        return Applied(True, "pre-debit notification FAILED; debit blocked",
                       attempt.mandate_id, attempt.id)

    verdict = advance(attempt.state, AttemptState.NOTIFIED)
    store.record_transition("attempt", attempt.id, attempt.state.value,
                            AttemptState.NOTIFIED.value, verdict.value,
                            source, "notification delivered")
    if verdict is not Transition.APPLIED:
        return Applied(False, f"notification arrived after "
                              f"{attempt.state.value}; ignored as stale",
                       attempt.mandate_id, attempt.id)
    attempt.state = AttemptState.NOTIFIED
    store.put_attempt(attempt)
    return Applied(True, "pre-debit notification delivered",
                   attempt.mandate_id, attempt.id)


def process_pending(store: Store, limit: int = 100) -> list[Applied]:
    """Interpret every accepted-but-unprocessed event, oldest first.

    Called after each ingest and again at startup. The startup call is the
    crash boundary between "we returned 2xx" and "we acted on it": the event is
    already durable, so recovery is a replay rather than a loss.
    """
    out: list[Applied] = []
    for ev in store.unprocessed_events(limit=limit):
        try:
            res = apply_event(store, ev)
        except Exception as e:                       # noqa: BLE001
            # One malformed event must not stop the queue. The type name is
            # recorded; the message is not, because a provider payload can
            # appear in it and payloads carry an email and a contact.
            store.mark_event_processed(ev.event_id,
                                       f"error: {type(e).__name__}")
            out.append(Applied(False, f"error: {type(e).__name__}"))
            continue
        store.mark_event_processed(ev.event_id, res.detail, res.mandate_id,
                                   res.attempt_id)
        out.append(res)
    return out
