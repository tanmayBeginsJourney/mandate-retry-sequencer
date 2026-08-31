"""Reminders are not Payment Links. Last-attempt checkout is. Quota is not a send.

    python agent/tests/test_workflows.py
"""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

import agent  # noqa: F401,E402
from agent.audit.log import AuditLog, read_rows  # noqa: E402
from agent.audit.jsonl_queue import read_jsonl  # noqa: E402
from agent.constraints.rules import AttemptLedger  # noqa: E402
from agent.constraints.stage0 import Stage0Gate  # noqa: E402
from agent.execution.razorpay_executor import (MandateBinding,  # noqa: E402
                                               RazorpayExecutor)
from agent.ports import MandateRef, WorkflowResult  # noqa: E402

REF = MandateRef(0, 0, 1)


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.link_status = "created"
        self.created_refs = set()

    def post(self, url, body, idempotency_key):
        self.calls.append(("POST", url, body, idempotency_key))
        if url.endswith("/cancel"):
            self.link_status = "cancelled"
            return 200, {"id": "plink_test1", "status": "cancelled"}
        if "payment_links" in url:
            ref = (body or {}).get("reference_id")
            if ref in self.created_refs:
                return 400, {"error": {
                    "code": "BAD_REQUEST_ERROR",
                    "description": (f"payment link with given reference_id: "
                                    f"{ref} already exists. Please create a "
                                    "payment link with a different "
                                    "reference_id")}}
            if ref:
                self.created_refs.add(ref)
            self.link_status = "created"
            return 200, {"id": "plink_test1", "short_url": "https://rzp.io/x",
                         "status": "created"}
        if url.endswith("/customers"):
            return 200, {"id": "cust_should_not", "entity": "customer"}
        return 200, {}

    def get(self, url):
        self.calls.append(("GET", url, None, None))
        if "reference_id=" in url:
            return 200, {"entity": "collection", "count": 1, "items": [
                {"id": "plink_test1", "status": self.link_status,
                 "short_url": "https://rzp.io/x"}]}
        return 200, {"id": "plink_test1", "status": self.link_status,
                     "short_url": "https://rzp.io/x"}


def _gate(ex, tmp):
    path = os.path.join(tmp, "w.jsonl")
    if os.path.exists(path):
        os.remove(path)
    log = AuditLog(path, "wf")
    ledger = AttemptLedger()
    ledger.open_cycle(REF.uid, 0)
    return Stage0Gate(ex, ledger, log), path


def main() -> int:
    failed = []

    def check(name, cond, detail=""):
        print(f"  {'ok' if cond else 'FAIL'}  {name}"
              + (f"  {detail}" if detail else ""))
        if not cond:
            failed.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        t = FakeTransport()
        ex = RazorpayExecutor(bindings={}, transport=t)
        ex.outbox_path = os.path.join(tmp, "outbox.jsonl")
        ex.queue_path = os.path.join(tmp, "queue.jsonl")
        gate, path = _gate(ex, tmp)

        wr = gate.send_reminder(REF, 0, 550.0, 56, diagnosis_id="d1",
                                message="Please add funds.", action_id="a1")
        check("remind is WorkflowResult", isinstance(wr, WorkflowResult))
        check("remind does not create a payment link",
              not any("payment_links" in str(c[1]) for c in t.calls),
              str(t.calls))
        check("remind writes outbox",
              any(r.get("kind") == "REMIND" for r in read_jsonl(ex.outbox_path)))

        wrb = gate.issue_backup_link(REF, 0, 550.0, 80, diagnosis_id="d2",
                                     message="Pay this period.", action_id="b1")
        check("backup executed", wrb.executed, wrb.detail)
        check("backup hit payment_links",
              any("payment_links" in str(c[1]) and c[0] == "POST"
                  for c in t.calls))
        check("backup vendor id", wrb.vendor_id.startswith("plink_"),
              wrb.vendor_id)
        wrb2 = gate.issue_backup_link(REF, 0, 550.0, 80, diagnosis_id="d2",
                                      message="Pay this period.", action_id="b1")
        check("replay recovers the same payment link",
              wrb2.executed and wrb2.vendor_id == wrb.vendor_id, wrb2.detail)
        ex._backup_ids.clear()
        wrb3 = gate.issue_backup_link(REF, 0, 550.0, 80, diagnosis_id="d2",
                                      message="Pay this period.", action_id="b1")
        check("crash replay recovers via reference_id",
              wrb3.executed and wrb3.vendor_id == wrb.vendor_id, wrb3.detail)
        notify = [c[2].get("notify") for c in t.calls
                  if c[0] == "POST" and "payment_links" in str(c[1])
                  and not str(c[1]).endswith("/cancel")]
        check("backup notify.sms is false",
              notify and notify[0].get("sms") is False, str(notify))

        wrp = gate.poll_backup_link(REF, 0, 81)
        check("poll hits GET",
              any(c[0] == "GET" and "payment_links" in str(c[1])
                  for c in t.calls), wrp.detail)

        wrc = gate.cancel_backup_link(REF, 0, 82)
        check("cancel executed", wrc.executed, wrc.detail)
        check("cancel status", wrc.status == "cancelled", wrc.status)

        wq = gate.send_escalate(REF, 0, 550.0, 90, diagnosis_id="d3",
                                brief="Mandate VI.", action_id="e1")
        check("escalate executed", wq.executed, wq.detail)
        check("escalate is merchant queue not customers API",
              wq.channel == "merchant_queue"
              and not any(str(c[1]).endswith("/customers") for c in t.calls),
              wq.channel)
        check("queue file has the brief",
              any("VI" in str(r.get("brief")) for r in read_jsonl(ex.queue_path)))

        rows = list(read_rows(path))
        check("remind in audit",
              any(r.get("action_kind") == "REMIND" for r in rows))
        check("backup in audit",
              any(r.get("action_kind") == "BACKUP_LINK" for r in rows))

        # Quota must not claim a send.
        t2 = FakeTransport()
        ex2 = RazorpayExecutor(bindings={}, transport=t2, max_live_nudges=0)
        ex2.outbox_path = os.path.join(tmp, "outbox2.jsonl")
        wrq = ex2.backup_checkout(REF, 550.0, 1, message="x", action_id="q")
        check("quota is not executed", wrq.executed is False, wrq.detail)
        check("quota did not POST a link",
              not any("payment_links" in str(c[1]) for c in t2.calls))

        check("ordinary Razorpay id with :5 suffix is not customer 5",
              ex.estimates(5) == (0.0, 0))
        b = MandateBinding(rzp_customer_id="cust_ABC:5", rzp_token_id="tok_x",
                           est_salary=400.0, est_payday=7, sim_customer_id=3)
        ex3 = RazorpayExecutor(bindings={"c3m0": b}, transport=FakeTransport())
        check("explicit sim_customer_id is used",
              ex3.estimates(3) == (400.0, 7))
        gate.log.close()

    if failed:
        print("FAILED:", failed)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
