#!/usr/bin/env python3
"""THE HEADLINE ACROSS THE `pop_spend` REGION. W21, re-measured after W24.

    py -3.12 agent/tests/test_conditional_headline.py

WHY THIS FILE EXISTS AT ALL. The table it produces is quoted in `README.md`,
`docs/02_RESULTS.md` and `docs/08_ARCHITECTURE.md`, and until now its only
provenance was `logs/w21_conditional_canonical.txt` — a scratchpad run with no
committed script behind it. A number the repository quotes must have a command
a reader can run; that was the rule the steelman table was promoted for, and
this table was missed by it.

WHAT IT MEASURES. `pop_spend` is one minus the RBI household saving rate. The
region [0.80, 0.93] is externally derived and no point in it is declared. Each
cell is the agent against `payday_wait` at that spend level.

READ THE `at risk` COLUMN BEFORE THE UPLIFT COLUMN. At `pop_spend=0.80` the
world carries a couple of at-risk cycles across a thousand customers, so the
uplift there is arithmetic on almost nothing. The region has one informative
end and that is a property of the world, not a result.

CONDITIONS ARE THE HEADLINE'S: n=100, 10 held-out populations, 120 days,
`payday_err=7`, `mode="full"`, canonical world, run seed matching
`agent/batch_report.py` so the `pop_spend=0.93` cell reproduces the headline
rather than sitting beside it at a different value.

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

N, K, DAYS, PE = 100, 5, 120, 7
RUN_SEED = 7
POPS = list(_CAN.POPS)
REGION = (0.80, 0.85, 0.88, 0.90, 0.93)


def pc(ps, spend):
    kw = dict(_CAN.pop_kwargs(ps, argv=["--canonical"]))
    return kw


def ms(xs):
    a = np.asarray(xs, float)
    return a.mean(), 2 * a.std(ddof=1) / np.sqrt(len(a))


def main() -> int:
    rk = _CAN.run_kwargs(argv=["--canonical"])
    ajobs, hjobs = [], []
    for sp in REGION:
        for ps in POPS:
            spec = (N, K, ps, sp, DAYS, pc(ps, sp))
            ajobs.append((f"{sp}|{ps}", spec, RUN_SEED,
                          dict(payday_err=PE, pop_spend=sp,
                               bcfg=w3.FITTED_BELIEF, mode="full",
                               # MATCHES agent/batch_report.py. `time_major`
                               # also switches `per_customer_tech_rng`, so
                               # omitting it draws a different technical-decline
                               # stream and the 0.93 cell then misses the
                               # headline by a few hundredths -- which is
                               # exactly what it did on the first run.
                               time_major=True, **rk),
                          False))
            hjobs.append((f"{sp}|{ps}", "payday_wait", spec, RUN_SEED,
                          dict(payday_err=PE, pop_spend=sp, **rk)))
    print("THE CONDITIONAL HEADLINE ON THE CANONICAL WORLD.")
    print(f"{len(ajobs) + len(hjobs)} runs: {len(REGION)} pop_spend levels x "
          f"{len(POPS)} held-out populations x 2 arms, n={N}, {DAYS}d, "
          f"payday_err=+/-{PE}, run seed {RUN_SEED}, mode=full.")
    print("pop_spend = 1 - the RBI household saving rate. The region is "
          "[0.80, 0.93]; no point in it is declared.")
    A = run_jobs(agent_job, ajobs)
    H = run_jobs(harness_job, hjobs)

    print()
    print("=" * 88)
    print(f"{'pop_spend':>10}{'payday_wait':>13}{'agent':>10}{'uplift':>10}"
          f"{'2 SE':>8}{'at risk':>10}{'agent surv':>12}{'rival surv':>12}")
    for sp in REGION:
        a = [A[f"{sp}|{ps}"] for ps in POPS]
        h = [H[f"{sp}|{ps}"] for ps in POPS]
        am, _ = ms([x["cycle_rec"] for x in a])
        hm, _ = ms([x["cycle_rec"] for x in h])
        d, dse = ms([x["cycle_rec"] - y["cycle_rec"] for x, y in zip(a, h)])
        ar = sum(x["recovery"]["at_risk"] for x in a)
        asv = float(np.mean([x["survival"] for x in a]))
        hsv = float(np.mean([x["survival"] for x in h]))
        print(f"{sp:>10.2f}{hm*100:>12.2f}%{am*100:>9.2f}%{d*100:>+10.2f}"
              f"{dse*100:>8.2f}{ar:>10}{asv*100:>11.1f}%{hsv*100:>11.1f}%")
    print("=" * 88)
    print("  The 0.93 cell is the headline cell and must reproduce "
          "`agent.batch_report --pops 10 --canonical`.")
    print("  BIAS: `payday_wait` is the harness's own baseline and is weaker "
          "than the")
    print("  frozen `[1,7]` schedule. Read this table beside "
          "`test_steelman_schedule.py`,")
    print("  never instead of it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
