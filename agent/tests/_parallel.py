"""Every measurement runs in a FRESH short-lived process. Here is why.

28 August 2026. Long-lived processes that execute many `run_once` calls back to
back crash on this machine -- SIGSEGV, and sometimes SIGILL, at a DIFFERENT
point every time. Established by isolation:

  * a single `harness.run` at n=100: fine, repeatedly
  * `import agent, w3, harness`: fine
  * pure numpy allocation stress: fine
  * 8.7 GB of 15.7 GB free, so not exhaustion
  * six `run_once` calls in one process: crashed on ALL THREE code paths --
    customer-major, time-major, and time-major with the monitor -- which rules
    out the new outage code as the cause
  * `test_parity_vs_harness.py`, byte-identical to the version that passed
    24/24 earlier the same day, now segfaults before printing a line

That last one is the decisive evidence: the failing code is code that
demonstrably worked hours earlier and has not changed. This is the intermittent
0xC0000005 already recorded in NOTES.md, not a defect in `agent/`.

THE MITIGATION, and why it is not a workaround. `ProcessPoolExecutor` with
`max_tasks_per_child=1` gives every run a brand-new interpreter that exits
immediately afterwards, so nothing accumulates across runs. This is the same
shape `sim/runner.py` already uses, for the same machine, and it has an
independent benefit: a run cannot be contaminated by state left behind by the
previous one, which is a property worth having in a repo whose recurring defect
is shared state between the check and the thing checked.

A CRASHED WORKER IS A FAILED MEASUREMENT, NOT A MISSING ONE. `run_jobs` raises
if any job dies. Silently dropping a crashed run would quietly change the
population a mean is taken over -- which is exactly the kind of invisible
sample-selection this project has been burned by (error 4, end-of-horizon
censoring).

WINDOWS SPAWN: the worker lives in THIS module, which has no side effects on
import, so a re-importing child executes nothing. Callers still need
`if __name__ == "__main__":` -- see NOTES.md error 10, the 97 minutes.
"""
from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "1")

import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)


def agent_job(spec):
    """spec = (key, pop_spec, run_seed, run_kwargs, want_audit).

    pop_spec = (n, k, pop_seed, spend, days). Populations are rebuilt inside
    the worker from their spec rather than pickled, exactly as sim/runner.py
    does it -- `w3.make_pop` is deterministic in its seed, so this is
    identity-preserving by construction.
    """
    import tempfile
    key, pop_spec, run_seed, kw, want_audit = spec
    import agent  # noqa: F401
    from agent.batch import make_pop, run_once

    n, k, pop_seed, spend, days = pop_spec
    pop = make_pop(n, k, pop_seed, spend=spend, days=days)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        r = run_once(pop, run_seed, log_path=os.path.join(tmp, "a.jsonl"), **kw)
        if want_audit:
            from agent.audit.log import read_rows
            from agent.constraints.auditor import replay
            a = replay(read_rows(r["log_path"]))
            r["audit_violations"] = a.total()
            r["audit_executed"] = a.executed
            r["audit_recovered_paise"] = a.recovered_paise
        r.pop("log_path", None)
    return key, r


def harness_job(spec):
    """spec = (key, policy, pop_spec, run_seed, harness_kwargs)."""
    key, policy, pop_spec, run_seed, kw = spec
    import agent  # noqa: F401
    import harness
    from agent.batch import make_pop
    n, k, pop_seed, spend, days = pop_spec
    pop = make_pop(n, k, pop_seed, spend=spend, days=days)
    return key, harness.run(policy, pop, run_seed, **kw)


def run_jobs(fn, jobs, workers: int = 8) -> dict:
    """Run `jobs` through `fn`, one fresh process per job. Raises on any death."""
    from concurrent.futures import ProcessPoolExecutor
    jobs = list(jobs)
    if not jobs:
        return {}
    out = {}
    with ProcessPoolExecutor(max_workers=min(workers, len(jobs)),
                             max_tasks_per_child=1) as ex:
        for key, res in ex.map(fn, jobs, chunksize=1):
            out[key] = res
    if len(out) != len(jobs):
        raise RuntimeError(
            f"{len(jobs) - len(out)} of {len(jobs)} jobs did not return. A "
            f"crashed worker is a FAILED measurement, not a missing one -- "
            f"dropping it would silently change the sample a mean is over.")
    return out
