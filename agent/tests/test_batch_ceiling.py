"""Batch legal ceiling is a circuit breaker and writes BATCH_LEGAL_CEILING.

    python agent/tests/test_batch_ceiling.py
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
from agent.ports import StopRule  # noqa: E402


def main() -> int:
    failed = []

    def check(name, cond, detail=""):
        print(f"  {'ok' if cond else 'FAIL'}  {name}"
              + (f"  {detail}" if detail else ""))
        if not cond:
            failed.append(name)

    pop = w3.make_pop(n=3, k=1, rng=np.random.default_rng(1),
                      days=30, cycle_days=30, spend=1.05,
                      payday_day0_frac=0.0, irregular_frac=0.0)
    tmp = tempfile.mkdtemp(prefix="ceiling-")

    clean = run_once(pop, seed=11, payday_err=7, pop_spend=1.05,
                     mode="degenerate",
                     log_path=os.path.join(tmp, "clean.jsonl"))
    check("clean run does not fire the ceiling",
          clean["stops"].get(StopRule.BATCH_LEGAL_CEILING.value, 0) == 0,
          str(clean["stops"].get(StopRule.BATCH_LEGAL_CEILING.value)))
    check("clean run still takes money actions",
          clean["att_per_cycle"] > 0 or clean.get("gate_allowed", 0) > 0,
          f"att_per_cycle={clean['att_per_cycle']}")

    held = run_once(pop, seed=11, payday_err=7, pop_spend=1.05,
                    mode="degenerate", legal_ceiling=0,
                    log_path=os.path.join(tmp, "held.jsonl"))
    check("ceiling 0 takes no money actions",
          held["stops"].get(StopRule.BATCH_LEGAL_CEILING.value, 0) > 0
          and (held.get("gate_allowed") or 0) == 0,
          f"stops={held['stops'].get(StopRule.BATCH_LEGAL_CEILING.value)} "
          f"allowed={held.get('gate_allowed')}")
    rows = list(read_rows(os.path.join(tmp, "held.jsonl")))
    check("audit log names BATCH_LEGAL_CEILING",
          any(r.get("rule") == StopRule.BATCH_LEGAL_CEILING.value
              for r in rows))
    check("old name RUN_BUDGET is not what the log writes",
          not any(r.get("rule") == "RUN_BUDGET" for r in rows))

    one = run_once(pop, seed=11, payday_err=7, pop_spend=1.05,
                   mode="degenerate", legal_ceiling=1,
                   log_path=os.path.join(tmp, "one.jsonl"))
    check("ceiling 1 allows at most one money action",
          one.get("gate_allowed", 0) <= 1,
          str(one.get("gate_allowed")))
    check("ceiling 1 then holds the rest",
          one["stops"].get(StopRule.BATCH_LEGAL_CEILING.value, 0) > 0)

    if failed:
        print("FAILED:", failed)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
