"""UPI AutoPay pre-debit state — decoupled flow (Razorpay PG S2S).

Official integration (read 31 August 2026):
  POST /v1/orders  with  notification: { token_id, payment_after }
  POST /v1/payments/create/recurring  with  order_id from above

Three distinct states — do not conflate them:
  ORDER_CREATED            Razorpay accepted the order-with-notification request.
  NOTIFICATION_DELIVERED   Webhook order.notification.delivered observed.
  NOTIFICATION_FAILED      Webhook order.notification.failed observed.

ORDER_CREATED is NOT proof the customer received the regulatory pre-debit alert.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# Proof transcript labels (grep-friendly).
ORDER_CREATED = "ORDER_CREATED"
NOTIFICATION_DELIVERED = "NOTIFICATION_DELIVERED"
NOTIFICATION_FAILED = "NOTIFICATION_FAILED"
DEBIT_ATTEMPTED = "DEBIT_ATTEMPTED"

WEBHOOK_DELIVERED = "order.notification.delivered"
WEBHOOK_FAILED = "order.notification.failed"

_REDACT_KEYS = frozenset({
    "email", "contact", "vpa", "token", "token_id", "customer_id",
    "authorization", "key_id", "key_secret", "password",
})


class PredeliveryPhase(str, Enum):
    NONE = "NONE"
    ORDER_CREATED = ORDER_CREATED
    NOTIFICATION_DELIVERED = NOTIFICATION_DELIVERED
    NOTIFICATION_FAILED = NOTIFICATION_FAILED
    DEBIT_ATTEMPTED = DEBIT_ATTEMPTED


@dataclass
class PredeliveryOrder:
    """Per-(mandate, target_t) Razorpay order for one debit cycle."""
    mandate_uid: str
    target_t: int
    order_id: str = ""
    amount_paise: int = 0
    payment_after: int = 0
    phase: PredeliveryPhase = PredeliveryPhase.NONE
    http_status: int | None = None
    error_code: str = ""
    error_detail: str = ""
    notification_id: str = ""
    delivered_at: int | None = None
    webhook_events: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NotificationWebhook:
    """Parsed pre-debit notification webhook."""
    event: str
    order_id: str
    token_id: str
    notification_id: str
    status: str
    payment_after: int | None
    delivered_at: int | None


def effective_amount_paise(notify_amount: float, binding_amount: float) -> int:
    """Amount in paise for the order body.

    Stage 0 calls notify with amount=0.0; live bindings carry charge_amount.
    """
    rupees = notify_amount if notify_amount > 0 else binding_amount
    if rupees <= 0:
        return 0
    return int(round(rupees * 100))


def build_order_body(*, amount_paise: int, currency: str, receipt: str,
                     token_id: str, payment_after: int) -> dict[str, Any]:
    """POST /v1/orders body per UPI AutoPay subsequent-payments guide."""
    return {
        "amount": amount_paise,
        "currency": currency,
        "payment_capture": True,
        "receipt": receipt,
        "notification": {
            "token_id": token_id,
            "payment_after": payment_after,
        },
    }


def parse_order_id(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""
    oid = payload.get("id")
    return str(oid) if oid else ""


def parse_notification_webhook(payload: dict) -> NotificationWebhook | None:
    """Parse order.notification.delivered / order.notification.failed."""
    if not isinstance(payload, dict):
        return None
    event = str(payload.get("event") or "")
    if event not in (WEBHOOK_DELIVERED, WEBHOOK_FAILED):
        return None
    ent = (((payload.get("payload") or {}).get("notification") or {})
           .get("entity"))
    if not isinstance(ent, dict):
        return None
    return NotificationWebhook(
        event=event,
        order_id=str(ent.get("order_id") or ""),
        token_id=str(ent.get("token_id") or ""),
        notification_id=str(ent.get("id") or ""),
        status=str(ent.get("status") or ""),
        payment_after=(int(ent["payment_after"])
                       if ent.get("payment_after") is not None else None),
        delivered_at=(int(ent["delivered_at"])
                      if ent.get("delivered_at") is not None else None),
    )


def apply_notification_webhook(rec: PredeliveryOrder,
                               wh: NotificationWebhook) -> PredeliveryOrder:
    """Advance phase only on delivery webhooks; never downgrade DELIVERED."""
    rec.webhook_events.append(wh.event)
    rec.notification_id = wh.notification_id or rec.notification_id
    if wh.payment_after is not None:
        rec.payment_after = wh.payment_after
    if wh.delivered_at is not None:
        rec.delivered_at = wh.delivered_at
    if wh.event == WEBHOOK_DELIVERED:
        rec.phase = PredeliveryPhase.NOTIFICATION_DELIVERED
    elif wh.event == WEBHOOK_FAILED:
        if rec.phase != PredeliveryPhase.NOTIFICATION_DELIVERED:
            rec.phase = PredeliveryPhase.NOTIFICATION_FAILED
    return rec


def sanitize_envelope(obj: Any, *, depth: int = 0) -> Any:
    """Redact secrets and PII for logs/transcripts."""
    if depth > 12:
        return "..."
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in _REDACT_KEYS or "secret" in kl or "password" in kl:
                out[k] = "<redacted>"
            elif kl == "authorization":
                out[k] = "<redacted>"
            else:
                out[k] = sanitize_envelope(v, depth=depth + 1)
        return out
    if isinstance(obj, list):
        return [sanitize_envelope(x, depth=depth + 1) for x in obj[:50]]
    if isinstance(obj, str):
        if re.match(r"^rzp_(live|test)_[A-Za-z0-9]+$", obj):
            return obj[:12] + "…" if len(obj) > 12 else obj
        if "@" in obj and "." in obj:
            return "<redacted_email>"
        if obj.startswith("+") and obj[1:].isdigit():
            return "<redacted_phone>"
        if obj.startswith("token_"):
            return obj[:10] + "…"
        if obj.startswith("cust_"):
            return obj[:10] + "…"
        return obj
    return obj


def envelope_record(*, phase: str, http_method: str, url: str,
                    request_body: dict | None, http_status: int | None,
                    response_body: dict | None,
                    extra: dict | None = None) -> dict:
    """One row for logs/razorpay_predelivery_*.json(l)."""
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
