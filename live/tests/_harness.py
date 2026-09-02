"""Shared scaffolding for the live gates. No assertions of its own."""
from __future__ import annotations

import os
import shutil
import tempfile

import live.tests  # noqa: F401  -- puts the package root on the path
from agent.execution.razorpay_mock import MockPlan, MockRazorpayApi, sign
from live.config import load
from live.service import LiveService

WEBHOOK_SECRET = "whsec_offline_gate_secret"


class Results:
    """Collects PASS/FAIL lines so a gate file prints one summary."""

    def __init__(self, title: str):
        self.title = title
        self.rows: list[tuple[bool, str, str]] = []
        print("=" * 78)
        print(title)
        print("=" * 78)

    def ok(self, name: str, cond, detail: str = "") -> bool:
        good = bool(cond)
        self.rows.append((good, name, detail))
        print(f"  {'PASS' if good else 'FAIL'}  {name}"
              + (f"   {detail}" if detail else ""))
        return good

    def section(self, text: str) -> None:
        print(f"\n{text}")

    def summary(self) -> int:
        bad = [r for r in self.rows if not r[0]]
        print()
        print("-" * 78)
        print(f"{len(self.rows) - len(bad)}/{len(self.rows)} checks passed")
        for _, name, detail in bad:
            print(f"  FAILED  {name}   {detail}")
        return 1 if bad else 0


class Bench:
    """A throwaway service on a mock rail, in a temporary directory."""

    def __init__(self, *, plan: MockPlan | None = None, seed: int = 5,
                 env: dict | None = None):
        self.dir = tempfile.mkdtemp(prefix="live-gate-")
        base = {"RECOVERY_MODE": "offline",
                "RECOVERY_DB": os.path.join(self.dir, "recovery.db"),
                "RAZORPAY_WEBHOOK_SECRET": WEBHOOK_SECRET}
        base.update(env or {})
        self.config = load(base)
        self.api = MockRazorpayApi(seed=seed, plan=plan)
        self.svc = LiveService(self.config, api=self.api,
                               log_path=os.path.join(self.dir, "audit.jsonl"))

    # ------------------------------------------------------------ lifecycle
    def registered(self, *, charge_paise: int = 100, est_payday: int = 3,
                   token_status: str = "confirmed"):
        """A customer with one authorised mandate. Returns (customer, mandate)."""
        c = self.svc.create_customer(name="Gate Customer",
                                     email="gate@example.com",
                                     contact="+919000000000")
        m = self.svc.start_registration(
            customer_id=c.id, charge_amount_paise=charge_paise,
            max_amount_paise=1_500_000, est_salary=30000,
            est_payday=est_payday)
        auth = self.api.authorize(m.registration_order_id, status=token_status)
        m = self.svc.confirm_registration(m.id, auth.body["payment_id"])
        self.deliver()
        return c, m

    def deliver(self) -> list[dict]:
        """Post every queued webhook through the real verification path."""
        for event_id, _kind, body in self.api.drain_webhooks():
            raw, signature = sign(body, WEBHOOK_SECRET)
            self.svc.handle_webhook(raw.encode(), signature, event_id)
        return self.svc.process_webhooks()

    def run_until_resolved(self, mandate_id: str, *, max_hours: int = 24 * 40,
                           step: int = 4) -> list:
        """Tick the service forward until an attempt reaches a terminal state."""
        out = []
        base = self.svc.epoch_origin
        for hour in range(0, max_hours, step):
            d = self.svc.decide(mandate_id, now=base + hour * 3600)
            out.append(d)
            self.deliver()
            m = self.svc.store.mandate(mandate_id)
            if m and any(a.resolved for a
                         in self.svc.store.attempts_for(mandate_id)):
                break
        return out

    def close(self) -> None:
        self.svc.store.close()
        shutil.rmtree(self.dir, ignore_errors=True)


def signed(body: dict, secret: str = WEBHOOK_SECRET) -> tuple[bytes, str]:
    raw, signature = sign(body, secret)
    return raw.encode(), signature


def payment_event(kind: str, *, payment_id: str, order_id: str,
                  status: str, reason: str | None = None,
                  amount: int = 100) -> dict:
    return {"entity": "event", "event": kind, "contains": ["payment"],
            "payload": {"payment": {"entity": {
                "id": payment_id, "entity": "payment", "amount": amount,
                "currency": "INR", "status": status, "order_id": order_id,
                "method": "upi", "error_reason": reason}}},
            "created_at": 1_700_000_000}
