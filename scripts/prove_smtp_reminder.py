"""Prove funding-reminder SMTP delivery (one real send, no Razorpay).

    py -3.12 scripts/prove_smtp_reminder.py

Requires SMTP_* and RECOVERY_NOTIFY_EMAIL in .env. Does not call Razorpay,
create Payment Links, or debit. Writes logs/smtp_reminder_proof.json.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from agent.execution.razorpay_executor import RazorpayExecutor  # noqa: E402
from agent.execution.smtp_delivery import SMTP_SENT  # noqa: E402
from agent.llm.client import _load_dotenv  # noqa: E402
from agent.ports import MandateRef  # noqa: E402

OUT = os.path.join(PKG, "logs", "smtp_reminder_proof.json")

PROOF_SUBJECT = "[Recovery Agent] SMTP funding reminder integration test"
PROOF_BODY = (
    "This is a Test Mode integration proof that the recovery agent's "
    "funding-reminder path successfully sent an email through the "
    "configured SMTP relay.\n\n"
    "This message does not imply a real customer experienced a Z9 decline."
)


class _NoNetTransport:
    def post(self, url, body, key):
        raise RuntimeError(f"unexpected Razorpay POST: {url}")

    def get(self, url):
        raise RuntimeError(f"unexpected Razorpay GET: {url}")


def _required_env() -> list[str]:
    missing = []
    for key in ("SMTP_HOST", "SMTP_PASSWORD", "SMTP_FROM", "RECOVERY_NOTIFY_EMAIL"):
        if not os.environ.get(key, "").strip():
            missing.append(key)
    return missing


def main() -> int:
    _load_dotenv()
    missing = _required_env()
    if missing:
        print("BLOCKED: missing required environment variables:")
        for k in missing:
            print(f"  - {k}")
        return 2

    host = os.environ.get("SMTP_HOST", "").strip()
    port = os.environ.get("SMTP_PORT", "587").strip() or "587"
    user = os.environ.get("SMTP_USER", "").strip()
    sender = os.environ.get("SMTP_FROM", "").strip()
    recipient = os.environ.get("RECOVERY_NOTIFY_EMAIL", "").strip()

    print("SMTP funding-reminder proof (one outbound message)")
    print(f"  host:      {host}")
    print(f"  port:      {port}")
    print(f"  user:      {user or '(none)'}")
    print(f"  from:      {sender}")
    print(f"  to:        {recipient}")
    print()

    tmp = tempfile.mkdtemp(prefix="smtp-proof-")
    outbox = os.path.join(tmp, "outbox.jsonl")
    ex = RazorpayExecutor(bindings={}, transport=_NoNetTransport())
    ex.outbox_path = outbox
    ex.notify_email = recipient

    started = time.time()
    wr = ex.remind(
        MandateRef(0, 0, 1), 499.0, t=int(time.time()),
        message=PROOF_BODY,
        action_id="smtp_reminder_proof",
        email_subject=PROOF_SUBJECT,
    )
    elapsed_ms = int((time.time() - started) * 1000)

    smtp_rec = ex._last_smtp
    ok = (wr.executed and wr.channel == "email"
          and wr.detail.startswith(SMTP_SENT) and smtp_rec is not None
          and smtp_rec.status == SMTP_SENT)

    transcript = {
        "generated_by": "scripts/prove_smtp_reminder.py",
        "timestamp_unix": int(time.time()),
        "elapsed_ms": elapsed_ms,
        "state": "PASS" if ok else "FAIL",
        "workflow_result": {
            "executed": wr.executed,
            "channel": wr.channel,
            "status": wr.status,
            "detail": wr.detail.split(" outbox=")[0],
            "outbox_path": "written",
        },
        "smtp": {
            "host": host,
            "port": int(port),
            "user_set": bool(user),
            "from": sender,
            "to": recipient,
            "subject": PROOF_SUBJECT,
            "status": smtp_rec.status if smtp_rec else "missing",
            "detail": smtp_rec.detail if smtp_rec else "",
            "phases": list(smtp_rec.phases) if smtp_rec else [],
            "smtp_code": smtp_rec.smtp_code if smtp_rec else None,
        },
        "note": "No Razorpay API called. No payment or debit performed.",
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(transcript, f, indent=2, sort_keys=True)

    print("RESULT")
    print(f"  executed:  {wr.executed}")
    print(f"  channel:   {wr.channel}")
    print(f"  smtp:      {wr.detail.split(' outbox=')[0]}")
    print(f"  phases:    {', '.join(smtp_rec.phases) if smtp_rec else '(none)'}")
    print(f"  elapsed:   {elapsed_ms} ms")
    print(f"  transcript: {os.path.relpath(OUT, PKG)}")

    if not ok:
        print("\nFAIL — SMTP server did not accept the message.")
        if smtp_rec and smtp_rec.status.startswith("smtp_failed"):
            print(f"  failure class: {smtp_rec.status}")
            print(f"  detail: {smtp_rec.detail}")
        return 1

    print("\nPASS — SMTP server accepted the funding-reminder proof message.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
