"""Runtime risk detection: RISK_RETRY, RISK_TERMINAL, UNRESOLVED at run end.

    python agent/tests/test_risk_detection.py
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
from agent.audit.log import EventKind, read_rows  # noqa: E402
from agent.batch import run_once  # noqa: E402
from agent.execution.sim_executor import SimExecutor  # noqa: E402
from agent.ports import AttemptOutcome  # noqa: E402
from agent.recovery import (  # noqa: E402
    UnresolvedCycle, is_terminal_risk_decline, should_emit_risk_retry,
    should_emit_risk_terminal, should_report_unresolved)


class PendingOnceExecutor:
    """First debit returns pending; everything else delegates to sim."""

    def __init__(self, inner: SimExecutor):
        self._inner = inner
        self._pending_fired = False

    def attempt(self, ref, amount, t, action_id=""):
        if not self._pending_fired:
            self._pending_fired = True
            return AttemptOutcome(
                t=t, code="deemed_transaction", success=False, pending=True,
                raw_code="gateway_pending")
        return self._inner.attempt(ref, amount, t, action_id=action_id)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class AlwaysPendingExecutor:
    """Every debit is indeterminate — for coverage-metric tests."""

    def __init__(self, inner: SimExecutor):
        self._inner = inner

    def attempt(self, ref, amount, t, action_id=""):
        return AttemptOutcome(
            t=t, code="deemed_transaction", success=False, pending=True,
            raw_code="timeout")

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _rows(rows, kind: str) -> list[dict]:
    return [r for r in rows if r.get("kind") == kind]


def main() -> int:
    failed = []

    def check(name, cond, detail=""):
        print(f"  {'ok' if cond else 'FAIL'}  {name}"
              + (f"  {detail}" if detail else ""))
        if not cond:
            failed.append(name)

    check("VI is terminal risk", is_terminal_risk_decline("VI"))
    check("Z9 is not terminal risk", not is_terminal_risk_decline("Z9"))
    check("first Z9 emits retry risk",
          should_emit_risk_retry(collected=False, already_emitted=False,
                                 decline_history=["Z9"], code="Z9"))
    check("second Z9 does not re-emit",
          not should_emit_risk_retry(collected=False, already_emitted=True,
                                   decline_history=["Z9", "Z9"], code="Z9"))
    check("unresolved dict triggers report",
          should_report_unresolved({0: UnresolvedCycle(0, "deemed_transaction",
                                                       "deemed_transaction")}))
    check("empty dict does not report",
          not should_report_unresolved({}))

    pop = w3.make_pop(n=12, k=1, rng=np.random.default_rng(3),
                      days=60, cycle_days=30, spend=4.0,
                      payday_day0_frac=0.0, irregular_frac=0.0)
    tmp = tempfile.mkdtemp(prefix="risk-detect-")
    log_path = os.path.join(tmp, "retry.jsonl")
    res = run_once(pop, seed=7, payday_err=7, pop_spend=4.0,
                   mode="degenerate", log_path=log_path)
    rows = list(read_rows(log_path))
    retry_rows = _rows(rows, EventKind.RISK_RETRY)
    check("integration: RISK_RETRY rows in audit log", bool(retry_rows),
          f"count={len(retry_rows)}")
    check("integration: risk_retry counter matches log",
          res.get("risk_retry", 0) == len(retry_rows))
    check("n_unresolved not folded into recovery dict",
          "n_unresolved" in res and res["n_unresolved"] == 0)
    check("recovery rate ignores n_unresolved",
          res.get("recovery") is not None
          and "n_unresolved" not in (res.get("recovery") or {}))

    term_log = os.path.join(tmp, "terminal.jsonl")
    term_pop = w3.make_pop(n=6, k=1, rng=np.random.default_rng(5),
                           days=45, cycle_days=30, spend=1.05)
    term_res = run_once(term_pop, seed=9, payday_err=7, pop_spend=1.05,
                        mode="degenerate", decline_kw={"p_mandate_broken": 1.0},
                        log_path=term_log)
    term_rows = _rows(list(read_rows(term_log)), EventKind.RISK_TERMINAL)
    check("integration: RISK_TERMINAL on broken mandate", bool(term_rows))

    # Left pending at run end — one row per mandate with cycle count.
    un_log = os.path.join(tmp, "unresolved.jsonl")
    un_pop = w3.make_pop(n=1, k=1, rng=np.random.default_rng(1),
                         days=65, cycle_days=30, spend=1.05)
    inner = SimExecutor(un_pop, seed=3, payday_err=7)
    un_res = run_once(un_pop, seed=3, payday_err=7, pop_spend=1.05,
                      mode="degenerate",
                      executor=AlwaysPendingExecutor(inner),
                      log_path=un_log)
    un_rows = _rows(list(read_rows(un_log)), EventKind.UNRESOLVED)
    check("UNRESOLVED fires when pending at run end",
          len(un_rows) == 1, f"count={len(un_rows)}")
    check("one row per mandate not per cycle",
          len(un_rows) == un_res.get("n_unresolved", -1) == 1)
    if un_rows:
        check("row names cycle count",
              un_rows[0].get("n_unresolved_cycles", 0) >= 2,
              str(un_rows[0].get("n_unresolved_cycles")))
        check("row carries indeterminate reason",
              "timeout" in un_rows[0].get("last_indeterminate_reason", ""),
              str(un_rows[0].get("last_indeterminate_reason")))
        check("halt rationale in detail",
              "double-debit" in un_rows[0].get("detail", "").lower())

    # Late definitive outcome resolves pending — no UNRESOLVED row.
    resolve_log = os.path.join(tmp, "resolved.jsonl")
    resolve_pop = w3.make_pop(n=1, k=1, rng=np.random.default_rng(2),
                              days=65, cycle_days=30, spend=4.0,
                              payday_day0_frac=0.0, irregular_frac=0.0)
    r_inner = SimExecutor(resolve_pop, seed=5, payday_err=7)
    resolve_res = run_once(
        resolve_pop, seed=5, payday_err=7, pop_spend=4.0,
        mode="degenerate", executor=PendingOnceExecutor(r_inner),
        log_path=resolve_log)
    resolve_rows = _rows(list(read_rows(resolve_log)), EventKind.UNRESOLVED)
    check("UNRESOLVED suppressed after late definitive outcome",
          len(resolve_rows) == 0 and resolve_res.get("n_unresolved", 1) == 0,
          f"unresolved={len(resolve_rows)} n_unresolved="
          f"{resolve_res.get('n_unresolved')}")
    retro = _rows(list(read_rows(resolve_log)), EventKind.RISK_RETRY)
    check("resolved pending reclassified as RISK_RETRY when Z9",
          bool(retro), f"risk_retry rows={len(retro)}")

    if failed:
        print("FAILED:", failed)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
