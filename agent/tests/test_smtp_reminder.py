"""Offline gates for funding-reminder SMTP delivery.

    py -3.12 agent/tests/test_smtp_reminder.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from agent.audit.jsonl_queue import read_jsonl  # noqa: E402
# I2-EXEMPT: drives the reminder path through the executor and the SMTP delivery stub.
from agent.execution.razorpay_executor import RazorpayExecutor  # noqa: E402
from agent.execution.smtp_delivery import (SMTP_SENT, SMTP_SKIPPED,  # noqa: E402
                                         deliver_smtp)
from agent.ports import MandateRef  # noqa: E402

REF = MandateRef(0, 0, 1)
_results: list[tuple[bool, str]] = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    _results.append((bool(cond), name))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   {detail}" if detail else ""))


class _NoNetTransport:
    def post(self, url, body, key):
        raise AssertionError(f"unexpected network POST {url}")

    def get(self, url):
        raise AssertionError(f"unexpected network GET {url}")


class _FakeSMTP:
  instances: list["_FakeSMTP"] = []

  def __init__(self, host, port, timeout=20):
    self.host, self.port, self.timeout = host, port, timeout
    self.login_args = None
    self.sent = None
    _FakeSMTP.instances.append(self)

  def __enter__(self):
    return self

  def __exit__(self, *a):
    return False

  def starttls(self):
    return None

  def login(self, user, password):
    self.login_args = (user, password)

  def send_message(self, msg):
    self.sent = msg
    return {}


def _env(**kw):
    base = {k: "" for k in (
        "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD",
        "SMTP_FROM", "RECOVERY_NOTIFY_EMAIL")}
    base.update(kw)
    return patch.dict(os.environ, base, clear=False)


def gate_M1() -> None:
    print("\nM1  SMTP disabled -> smtp_skipped")
    with _env(SMTP_HOST="", RECOVERY_NOTIFY_EMAIL="a@example.com"):
        r = deliver_smtp("a@example.com", "s", "b")
        ok("status skipped", r.status == SMTP_SKIPPED)


def gate_M2() -> None:
    print("\nM2  missing recipient -> smtp_skipped")
    with _env(SMTP_HOST="smtp.example.com", RECOVERY_NOTIFY_EMAIL=""):
        r = deliver_smtp("", "s", "b")
        ok("status skipped", r.status == SMTP_SKIPPED)


def gate_M3() -> None:
    print("\nM3  configured SMTP -> smtp_sent with phases")
    _FakeSMTP.instances.clear()
    with _env(SMTP_HOST="smtp.example.com", SMTP_PORT="587",
              SMTP_USER="user", SMTP_PASSWORD="secret",
              SMTP_FROM="from@example.com",
              RECOVERY_NOTIFY_EMAIL="to@example.com"):
        r = deliver_smtp("to@example.com", "Subj", "Body",
                         smtp_factory=_FakeSMTP)
        ok("smtp_sent", r.status == SMTP_SENT)
        ok("phases", "starttls_ok" in r.phases and "send_ok" in r.phases)
        ok("auth", _FakeSMTP.instances[-1].login_args == ("user", "secret"))


def gate_M4() -> None:
    print("\nM4  SMTP exception -> honest failure")
    class _BoomSMTP:
        def __init__(self, *a, **k):
            raise OSError("connection refused")
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    with _env(SMTP_HOST="smtp.example.com", SMTP_USER="u",
              SMTP_PASSWORD="p", SMTP_FROM="f@x.com"):
        r = deliver_smtp("to@example.com", "s", "b", smtp_factory=_BoomSMTP)
        ok("failed", r.status.startswith("smtp_failed:"))
        ok("no sent phase", "send_ok" not in r.phases)


def gate_M5() -> None:
    print("\nM5  remind writes outbox even when SMTP fails")
    _FakeSMTP.instances.clear()

    class _FailSend(_FakeSMTP):
        def send_message(self, msg):
            raise OSError("broken pipe")

    with tempfile.TemporaryDirectory() as tmp:
        with _env(SMTP_HOST="smtp.example.com", SMTP_PORT="587",
                  SMTP_USER="u", SMTP_PASSWORD="p",
                  SMTP_FROM="from@example.com",
                  RECOVERY_NOTIFY_EMAIL="to@example.com"):
            ex = RazorpayExecutor(bindings={}, transport=_NoNetTransport())
            ex.outbox_path = os.path.join(tmp, "out.jsonl")
            ex.notify_email = "to@example.com"
            with patch("agent.execution.smtp_delivery.smtplib.SMTP", _FailSend):
                wr = ex.remind(REF, 550.0, 10, message="hello", action_id="a1")
            ok("not executed", wr.executed is False)
            ok("outbox channel", wr.channel == "outbox")
            ok("outbox row", any(r.get("kind") == "REMIND"
                                 for r in read_jsonl(ex.outbox_path)))
            ok("smtp failed in detail", "smtp_failed" in wr.detail)


def gate_M6() -> None:
    print("\nM6  successful SMTP -> executed=True channel=email")
    _FakeSMTP.instances.clear()
    with tempfile.TemporaryDirectory() as tmp:
        with _env(SMTP_HOST="smtp.example.com", SMTP_PORT="587",
                  SMTP_USER="u", SMTP_PASSWORD="p",
                  SMTP_FROM="from@example.com",
                  RECOVERY_NOTIFY_EMAIL="to@example.com"):
            ex = RazorpayExecutor(bindings={}, transport=_NoNetTransport())
            ex.outbox_path = os.path.join(tmp, "out.jsonl")
            ex.notify_email = "to@example.com"
            with patch("agent.execution.smtp_delivery.smtplib.SMTP", _FakeSMTP):
                wr = ex.remind(REF, 550.0, 10, message="hello", action_id="a1")
            ok("executed", wr.executed is True)
            ok("channel email", wr.channel == "email")
            ok("status sent", wr.status == "sent")
            ok("outbox still written",
               any(r.get("kind") == "REMIND" for r in read_jsonl(ex.outbox_path)))
            ok("no network", True)


def main() -> int:
    print("=" * 72)
    print("SMTP REMINDER GATES (offline)")
    print("=" * 72)
    gate_M1()
    gate_M2()
    gate_M3()
    gate_M4()
    gate_M5()
    gate_M6()
    n_fail = sum(1 for p, _ in _results if not p)
    print("\n" + "=" * 72)
    print(f"{'PASS' if n_fail == 0 else 'FAIL'}  {len(_results) - n_fail}/{len(_results)}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
