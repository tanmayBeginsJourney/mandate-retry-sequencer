"""Provider-neutral SMTP delivery for funding reminders.

Uses only stdlib smtplib and environment variables:
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM
"""
from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable

SMTP_SENT = "smtp_sent"
SMTP_SKIPPED = "smtp_skipped"


@dataclass(frozen=True)
class SmtpDeliveryResult:
    """Outcome of one SMTP send attempt. Never carries secrets."""
    status: str
    detail: str = ""
    phases: tuple[str, ...] = ()
    smtp_code: int | None = None
    recipient: str = ""
    sender: str = ""


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def deliver_smtp(
    to_addr: str,
    subject: str,
    body: str,
    *,
    smtp_factory: Callable[..., smtplib.SMTP] | None = None,
) -> SmtpDeliveryResult:
    """Send one message via STARTTLS SMTP. Never raises."""
    recipient = (to_addr or "").strip()
    host = _env("SMTP_HOST")
    if not host or not recipient:
        return SmtpDeliveryResult(
            status=SMTP_SKIPPED,
            detail="missing SMTP_HOST or recipient",
            recipient=recipient)

    sender = _env("SMTP_FROM", "recovery@localhost")
    port = int(_env("SMTP_PORT", "587") or "587")
    user = _env("SMTP_USER")
    password = _env("SMTP_PASSWORD")
    phases: list[str] = []

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(body)

    factory = smtp_factory or smtplib.SMTP
    try:
        with factory(host, port, timeout=20) as s:
            phases.append("connected")
            s.starttls()
            phases.append("starttls_ok")
            if user:
                s.login(user, password)
                phases.append("auth_ok")
            refused = s.send_message(msg)
            phases.append("send_ok")
        detail = "message accepted by SMTP server"
        if refused:
            detail = f"partial refusal: {len(refused)} recipient(s)"
        return SmtpDeliveryResult(
            status=SMTP_SENT,
            detail=detail,
            phases=tuple(phases),
            recipient=recipient,
            sender=sender)
    except smtplib.SMTPAuthenticationError as e:
        return SmtpDeliveryResult(
            status=f"smtp_failed:SMTPAuthenticationError",
            detail=str(e)[:200],
            phases=tuple(phases),
            smtp_code=getattr(e, "smtp_code", None),
            recipient=recipient,
            sender=sender)
    except smtplib.SMTPRecipientsRefused as e:
        return SmtpDeliveryResult(
            status="smtp_failed:SMTPRecipientsRefused",
            detail=str(e)[:200],
            phases=tuple(phases),
            recipient=recipient,
            sender=sender)
    except smtplib.SMTPException as e:
        return SmtpDeliveryResult(
            status=f"smtp_failed:{type(e).__name__}",
            detail=str(e)[:200],
            phases=tuple(phases),
            smtp_code=getattr(e, "smtp_code", None),
            recipient=recipient,
            sender=sender)
    except OSError as e:
        return SmtpDeliveryResult(
            status=f"smtp_failed:{type(e).__name__}",
            detail=str(e)[:200],
            phases=tuple(phases),
            recipient=recipient,
            sender=sender)
    except Exception as e:                          # noqa: BLE001
        return SmtpDeliveryResult(
            status=f"smtp_failed:{type(e).__name__}",
            detail=str(e)[:200],
            phases=tuple(phases),
            recipient=recipient,
            sender=sender)
