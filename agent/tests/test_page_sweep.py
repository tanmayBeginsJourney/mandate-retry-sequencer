#!/usr/bin/env python3
"""THE PAGE'S PAYDAY-ERROR SLIDER. W23, re-measured after W24.

    py -3.12 agent/tests/test_page_sweep.py

WHY THIS FILE EXISTS AT ALL. `scripts/build_page_data.py` carries this table as
the constant `SWEEP`, and `docs/index.html`'s slider reads it. Its only
provenance was `logs/w23_page_sweep_canonical.txt` -- a scratchpad run with no
committed script behind it. That is the defect `test_conditional_headline.py`
was promoted to fix, on the neighbouring table, and this one was missed by the
same pass: a number the repository quotes must have a command a reader can run.

It was also STALE. The transcript predates the W24 belief repair (`prior_w`
9 -> 5, `prior_floor` 0.5 -> 0.1, `cycle_value` 0 -> 0.6, `sim/w3.py` changed
at 2026-09-01 18:08), so every `agent` cell in it was the pre-repair agent. The
+/-7 row is supposed to BE the headline cell, and after the batch was re-run it
disagreed with `agent.batch_report --canonical` by a hundredth -- the exact
symptom of a transcribed table outliving its source.

CONDITIONS ARE THE HEADLINE'S, and identical to
`test_conditional_headline.py`'s: n=100, 10 held-out populations, 120 days,
`pop_spend=0.93`, `mode="full"`, canonical world, run seed matching
`agent/batch_report.py`, `time_major=True`. The +/-7 row must reproduce
`py -3.12 -m agent.batch_report --pops 10 --canonical` rather than sitting
beside it at a different value.

READ THE `at risk` COLUMN BEFORE THE UPLIFT COLUMN. At small `payday_err` the
rival is nearly perfect because the world is nearly uncontended, so the uplift
there is arithmetic on very little and is reported as a tie.

NOT GATE-PROTECTED. Every run is one process (`_parallel.py`).
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np

import agent  # noqa: F401
import w3
from agent.tests import _canonical as _CAN
from agent.tests._parallel import agent_job, harness_job, run_jobs

N, K, DAYS, SPEND = 100, 5, 120, 0.93
RUN_SEED = 7
POPS = list(_CAN.POPS)
LEVELS = (1, 3, 5, 7, 10, 14)

#: The page prints a word, not a number, wherever the interval covers zero.
#: Transcribing "agent wins" onto a tie is the failure this column prevents.
def verdict(delta, two_se):
    return "agent wins" if delta > two_se else "tie"


def ms(xs):
    a = np.asarray(xs, float)
    return a.mean(), 2 * a.std(ddof=1) / np.sqrt(len(a))


def main() -> int:
    rk = _CAN.run_kwargs(argv=["--canonical"])
    ajobs, hjobs = [], []
    for pe in LEVELS:
        for ps in POPS:
            spec = (N, K, ps, SPEND, DAYS,
                    dict(_CAN.pop_kwargs(ps, argv=["--canonical"])))
            ajobs.append((f"{pe}|{ps}", spec, RUN_SEED,
                          dict(payday_err=pe, pop_spend=SPEND,
                               bcfg=w3.FITTED_BELIEF, mode="full",
                               # See test_conditional_headline.py: time_major
                               # also switches per_customer_tech_rng, and
                               # omitting it draws a different technical-decline
                               # stream, which moves the headline cell.
                               time_major=True, **rk),
                          False))
            hjobs.append((f"{pe}|{ps}", "payday_wait", spec, RUN_SEED,
                          dict(payday_err=pe, pop_spend=SPEND, **rk)))
    print("PAGE SWEEP -- agent vs payday_wait, canonical world, "
          "batch_report config.")
    print(f"{len(ajobs) + len(hjobs)} runs: {len(LEVELS)} levels x "
          f"{len(POPS)} held-out pops x 2 arms, n={N}, {DAYS}d, "
          f"pop_spend={SPEND}, run seed {RUN_SEED}, mode=full.")
    A = run_jobs(agent_job, ajobs)
    H = run_jobs(harness_job, hjobs)

    print()
    print(f"{'payday_err':>11}{'payday_wait':>13}{'agent':>10}{'delta':>9}"
          f"{'2 SE':>8}{'at risk':>10}  verdict")
    rows = []
    for pe in LEVELS:
        a = [A[f"{pe}|{ps}"] for ps in POPS]
        h = [H[f"{pe}|{ps}"] for ps in POPS]
        am, _ = ms([x["cycle_rec"] for x in a])
        hm, _ = ms([x["cycle_rec"] for x in h])
        d, dse = ms([x["cycle_rec"] - y["cycle_rec"] for x, y in zip(a, h)])
        ar = sum(x["recovery"]["at_risk"] for x in a)
        v = verdict(d * 100, dse * 100)
        rows.append((pe, hm * 100, am * 100, d * 100, dse * 100, v))
        print(f"{pe:>11}{hm*100:>12.2f}%{am*100:>9.2f}%{d*100:>+9.2f}"
              f"{dse*100:>8.2f}{ar:>10}  {v}")

    print()
    print("SWEEP for scripts/build_page_data.py -- transcribe, do not compute.")
    print("The delta is printed from the paired difference, NOT from")
    print("subtracting the two rounded columns; those can disagree by a")
    print("hundredth and a page that disagrees with its own source invites")
    print("the reader to check nothing else.")
    print("SWEEP = [")
    for pe, hm, am, d, dse, v in rows:
        print(f"    ({pe:<2d} {hm:.2f}, {am:.2f}, {d:+.2f}, {dse:.2f}, "
              f'"{v}"),'.rjust(0))
    print("]")
    print()
    print("  The +/-7 row is the headline cell and must reproduce "
          "`agent.batch_report --pops 10 --canonical`.")
    print("  BIAS: `payday_wait` is the harness's own baseline and is weaker "
          "than the frozen `[1,7]` schedule. Read this beside "
          "`test_steelman_schedule.py`, never instead of it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
