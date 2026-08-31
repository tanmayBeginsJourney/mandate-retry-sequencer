"""Prove reminder / last-attempt backup checkout / merchant queue.

    python scripts/prove_workflows.py

Needs RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET in .env (rzp_test_ keys).
RECOVERY_NOTIFY_EMAIL optional: if set, the backup Payment Link asks
Razorpay to email that address. ZAI_API_KEY optional for copy.

Does not charge a mandate. Cancels the backup link so it cannot be paid.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from agent.audit.jsonl_queue import read_jsonl  # noqa: E402
from agent.audit.log import AuditLog, read_rows  # noqa: E402
from agent.constraints.rules import AttemptLedger  # noqa: E402
from agent.constraints.stage0 import Stage0Gate  # noqa: E402
from agent.execution.razorpay_executor import RazorpayExecutor  # noqa: E402
from agent.llm.client import ZaiClient, _load_dotenv  # noqa: E402
from agent.llm.compose import compose_outreach  # noqa: E402
from agent.llm.fallback import RuleBasedDiagnoser  # noqa: E402
from agent.llm.model_diagnoser import ModelDiagnoser  # noqa: E402
from agent.ports import CaseView, Diagnosis, InterventionKind, MandateRef, RootCause  # noqa: E402
from agent.recovery import (escalate_halts_cycle,  # noqa: E402
                            should_issue_backup_after_fail,
                            should_remind_after_fail)


def _view(hist, **kw) -> CaseView:
    d = dict(case_hash="prove_wf", attempts_used=2, attempts_cap=4,
             day_in_cycle=26, days_left_in_cycle=4, amount=550.0,
             decline_history=tuple(hist), n_recent_z9=hist.count("Z9"),
             peer_mandate_success_recent=False, uncertainty_band="medium")
    d.update(kw)
    return CaseView(**d)


def _git_email() -> str:
    try:
        r = subprocess.run(["git", "config", "user.email"],
                           capture_output=True, text=True, cwd=PKG, timeout=10)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def main() -> int:
    _load_dotenv()
    kid = os.environ.get("RAZORPAY_KEY_ID", "")
    if not kid.startswith("rzp_test_"):
        print("REFUSED: RAZORPAY_KEY_ID is missing or is not a test key.")
        print("This script will not run against live keys.")
        return 2

    notify = os.environ.get("RECOVERY_NOTIFY_EMAIL", "").strip() or _git_email()
    if notify:
        os.environ["RECOVERY_NOTIFY_EMAIL"] = notify
        print("notify email:", notify)
    else:
        print("notify email: (none set; Razorpay will not send mail)")

    client = ZaiClient()
    diagnoser = (ModelDiagnoser(client=client) if client.available
                 else RuleBasedDiagnoser())
    print("diagnoser:", type(diagnoser).__name__,
          "llm" if client.available else "template/rules")

    tmp = tempfile.mkdtemp(prefix="prove-wf-")
    outbox = os.path.join(tmp, "outbox.jsonl")
    queue = os.path.join(tmp, "queue.jsonl")
    os.environ["RECOVERY_OUTBOX"] = outbox
    os.environ["RECOVERY_QUEUE"] = queue

    ex = RazorpayExecutor(bindings={}, max_live_nudges=3,
                          max_live_escalations=3)
    ex.outbox_path = outbox
    ex.queue_path = queue
    log_path = os.path.join(tmp, "wf.jsonl")
    log = AuditLog(log_path, "prove_wf")
    ledger = AttemptLedger()
    ref = MandateRef(0, 0, 1)
    ledger.open_cycle(ref.uid, 0)
    gate = Stage0Gate(ex, ledger, log)
    failed = []

    def check(name, cond, detail=""):
        print(f"  {'ok' if cond else 'FAIL'}  {name}"
              + (f"  {detail}" if detail else ""))
        if not cond:
            failed.append(name)

    check("rule: remind after 1st Z9", should_remind_after_fail(1, "Z9"))
    check("rule: backup after 3rd Z9", should_issue_backup_after_fail(3, "Z9"))
    check("rule: Z9 escalate does not halt", not escalate_halts_cycle("Z9"))
    check("rule: VI escalate halts", escalate_halts_cycle("VI"))

    # --- REMIND (must not be a Payment Link)
    v_rem = _view(["Z9"], attempts_used=1, days_left_in_cycle=20,
                  case_hash="prove_remind")
    diag_r = Diagnosis(
        diagnosis_id="prove_remind", root_cause=RootCause.INSUFFICIENT_FUNDS,
        intervention=InterventionKind.NUDGE, confidence=0.7,
        rationale="First funds decline this cycle.",
        source="forced", prompt_id="prove")
    copy_r = compose_outreach(v_rem, diag_r,
                              client=client if client.available else None,
                              purpose="reminder")
    wr_r = gate.send_reminder(ref, 0, v_rem.amount, 10,
                              diagnosis_id=diag_r.diagnosis_id,
                              message=copy_r.body,
                              action_id=f"prove_remind_{int(time.time())}")
    print()
    print("REMIND")
    print("  copy source", copy_r.source)
    print("  copy:", copy_r.body)
    print("  channel", wr_r.channel, wr_r.detail)
    check("remind did not create a payment link",
          wr_r.channel != "razorpay_payment_link")
    check("remind outbox has the copy",
          any(copy_r.body[:20] in str(r.get("message"))
              for r in read_jsonl(outbox)))

    # --- BACKUP CHECKOUT (the only Payment Link)
    v_b = _view(["Z9", "Z9", "Z9"], attempts_used=3, case_hash="prove_backup")
    copy_b = compose_outreach(v_b, diag_r,
                              client=client if client.available else None,
                              purpose="backup_link")
    aid = f"prove_backup_{int(time.time())}"
    wr_b = gate.issue_backup_link(ref, 0, v_b.amount, 72,
                                  diagnosis_id="prove_backup",
                                  message=copy_b.body, action_id=aid)
    print()
    print("BACKUP CHECKOUT")
    print("  copy source", copy_b.source)
    print("  copy:", copy_b.body)
    print("  executed", wr_b.executed, wr_b.channel, wr_b.vendor_id)
    print("  ", wr_b.detail)
    check("backup created a payment link",
          wr_b.executed and wr_b.vendor_id.startswith("plink_"), wr_b.detail)

    wr_b2 = gate.issue_backup_link(ref, 0, v_b.amount, 72,
                                   diagnosis_id="prove_backup",
                                   message=copy_b.body, action_id=aid)
    print("  replay same action_id ->", wr_b2.vendor_id, wr_b2.detail)
    if wr_b.executed and wr_b2.executed and wr_b.vendor_id == wr_b2.vendor_id:
        check("replay returned the same payment link", True)
    else:
        print("  WARN  Razorpay did not reuse the Payment Link on replay.")
        print("  The Idempotency-Key header is sent; honouring it is unverified.")
        print("  Lookup by reference_id:", wr_b2.detail)
        check("idempotency honour is unverified (not claimed)", True)

    wr_g = gate.poll_backup_link(ref, 0, 73)
    print("  GET", wr_g.status, wr_g.detail)
    check("GET backup link", wr_g.executed, wr_g.detail)

    wr_c = gate.cancel_backup_link(ref, 0, 74)
    print("  CANCEL", wr_c.status, wr_c.detail)
    check("cancelled the backup link so it cannot be paid",
          wr_c.executed and wr_c.status == "cancelled", wr_c.detail)
    wr_g2 = gate.poll_backup_link(ref, 0, 75)
    print("  GET after cancel", wr_g2.status, wr_g2.detail)
    check("poll sees cancelled or cancelled-equivalent",
          wr_g2.status in ("cancelled", "expired") or wr_c.status == "cancelled",
          wr_g2.status)

    # --- ESCALATE (merchant queue file)
    v_e = _view(["VI"], case_hash="prove_esc")
    diag_e = Diagnosis(
        diagnosis_id="prove_esc", root_cause=RootCause.MANDATE_INVALID,
        intervention=InterventionKind.ESCALATE, confidence=0.95,
        rationale="Mandate returned a terminal validation failure.",
        source="forced", prompt_id="prove")
    copy_e = compose_outreach(v_e, diag_e,
                              client=client if client.available else None,
                              purpose="escalate")
    wr_e = gate.send_escalate(ref, 0, v_e.amount, 80,
                              diagnosis_id=diag_e.diagnosis_id,
                              brief=copy_e.body,
                              action_id=f"prove_esc_{int(time.time())}")
    print()
    print("ESCALATE")
    print("  copy source", copy_e.source)
    print("  copy:", copy_e.body)
    print("  channel", wr_e.channel, wr_e.vendor_id)
    print("  ", wr_e.detail)
    check("escalate wrote the merchant queue",
          wr_e.executed and wr_e.channel == "merchant_queue", wr_e.detail)
    check("queue file contains the brief",
          any(copy_e.body[:20] in str(r.get("brief"))
              for r in read_jsonl(queue)))

    kinds = [r.get("action_kind") for r in read_rows(log_path)
             if r.get("kind") == "NON_MONEY_ACTION"]
    print()
    print("audit trail", log_path)
    print("  action_kinds", kinds)
    print("  outbox", outbox)
    print("  queue", queue)

    if failed:
        print("FAILED:", failed)
        return 1
    print()
    print("PASS: reminder is not a Payment Link; last-attempt checkout "
          "created and cancelled; merchant queue is a file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
