"""Full-mode last-attempt backup must not fire a fourth mandate debit.

    python agent/tests/test_backup_loop.py
"""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

import numpy as np  # noqa: E402

import agent  # noqa: F401,E402
import w3  # noqa: E402
from agent.audit.log import read_rows  # noqa: E402
from agent.batch import run_once  # noqa: E402


def _attempts_per_cycle(log_path: str) -> dict[tuple, int]:
    rows = list(read_rows(log_path))
    by: dict[tuple, int] = {}
    for r in rows:
        if r.get("kind") != "MONEY_ACTION" or r.get("gate_verdict") != "ALLOWED":
            continue
        key = (r.get("mandate_uid"), r.get("cycle"))
        by[key] = by.get(key, 0) + 1
    return by


def main() -> int:
    failed = []

    def check(name, cond, detail=""):
        print(f"  {'ok' if cond else 'FAIL'}  {name}"
              + (f"  {detail}" if detail else ""))
        if not cond:
            failed.append(name)

    pop = w3.make_pop(n=8, k=1, rng=np.random.default_rng(1),
                      days=60, cycle_days=30, spend=3.0,
                      payday_day0_frac=0.0, irregular_frac=0.0)
    tmp = tempfile.mkdtemp(prefix="backup-loop-")
    log_path = os.path.join(tmp, "run.jsonl")
    outbox = os.path.join(tmp, "outbox.jsonl")
    queue = os.path.join(tmp, "queue.jsonl")
    os.environ["RECOVERY_OUTBOX"] = outbox
    os.environ["RECOVERY_QUEUE"] = queue
    res = run_once(pop, seed=11, payday_err=7, pop_spend=3.0,
                   mode="full", allow_nudge=True, allow_escalate=True,
                   allow_stop=True, log_path=log_path)
    rows = list(read_rows(log_path))
    by_cycle = _attempts_per_cycle(log_path)
    over = {k: n for k, n in by_cycle.items() if n > 3}
    check("full mode: no mandate-cycle fired a fourth debit",
          not over, str(over))
    check("backup links were issued in full mode",
          res.get("backup_links", 0) > 0 or
          any(r.get("action_kind") == "BACKUP_LINK" for r in rows),
          f"backup_links={res.get('backup_links')}")
    check("reminders fired on early funds fails",
          res.get("reminders", 0) > 0 or
          any(r.get("action_kind") == "REMIND" for r in rows),
          f"reminders={res.get('reminders')}")
    check("survival is not zero when 4th debit is held",
          res["survival"] > 0.0)

    deg_log = os.path.join(tmp, "deg.jsonl")
    deg = run_once(pop, seed=11, payday_err=7, pop_spend=3.0,
                   mode="degenerate", log_path=deg_log)
    check("degenerate does not issue backup links",
          deg.get("backup_links", 0) == 0,
          str(deg.get("backup_links")))

    # The belief index often leaves the fourth attempt unspent. A daily
    # schedule does not, so it is the control that can actually fire a
    # mandate-killing fourth debit.
    lit_log = os.path.join(tmp, "legal.jsonl")
    legal = run_once(pop, seed=11, payday_err=7, pop_spend=3.0,
                     mode="doc_legal", log_path=lit_log)
    legal_fourth = {k: n for k, n in _attempts_per_cycle(lit_log).items()
                    if n >= 4}
    check("fixed schedule spends the fourth attempt when backup is off",
          bool(legal_fourth),
          f"backup_links={legal.get('backup_links')}")

    held_log = os.path.join(tmp, "held.jsonl")
    held = run_once(pop, seed=11, payday_err=7, pop_spend=3.0,
                    mode="doc_legal", last_attempt_backup=True,
                    remind_on_fail=True, log_path=held_log)
    held_over = {k: n for k, n in _attempts_per_cycle(held_log).items()
                 if n > 3}
    check("fixed schedule with backup does not fire a fourth debit",
          not held_over, str(held_over))
    check("fixed schedule with backup still issues the link",
          held.get("backup_links", 0) > 0,
          str(held.get("backup_links")))

    if failed:
        print("FAILED:", failed)
        return 1
    print("all checks passed")
    print(f"  full attempts {res['att_per_cycle']:.3f} "
          f"survival {res['survival']*100:.1f}% "
          f"reminders {res.get('reminders')} "
          f"backup {res.get('backup_links')} "
          f"legal-fourth {len(legal_fourth)} "
          f"held-backup {held.get('backup_links')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
