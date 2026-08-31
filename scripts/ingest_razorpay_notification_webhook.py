"""Append one Razorpay notification webhook to the proof transcript.

Reads JSON from a file or stdin, parses order.notification.delivered / .failed,
and appends a sanitized row to logs/razorpay_notification_evidence.jsonl.

    py -3.12 scripts/ingest_razorpay_notification_webhook.py webhook.json

Does not call Razorpay. Does not debit.
"""
from __future__ import annotations

import json
import os
import sys

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from agent.execution.razorpay_predelivery import (envelope_record,
                                                  parse_notification_webhook)

OUT = os.path.join(PKG, "logs", "razorpay_notification_evidence.jsonl")


def main() -> int:
    if len(sys.argv) < 2:
        raw = sys.stdin.read()
    else:
        with open(sys.argv[1], encoding="utf-8") as f:
            raw = f.read()
    payload = json.loads(raw)
    wh = parse_notification_webhook(payload)
    if wh is None:
        print("Not order.notification.delivered or order.notification.failed")
        return 1
    phase = ("NOTIFICATION_DELIVERED" if wh.event.endswith(".delivered")
             else "NOTIFICATION_FAILED")
    row = envelope_record(
        phase=phase, http_method="WEBHOOK", url="",
        request_body=None, http_status=200, response_body=payload,
        extra={"order_id": wh.order_id, "notification_id": wh.notification_id,
               "status": wh.status})
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    print(f"Appended {phase} for order {wh.order_id} -> {os.path.relpath(OUT, PKG)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
