#!/usr/bin/env python3
"""V3 AND V1 AT FIVE TIMES THE SAMPLE. Does the V3 band hit survive?

    py -3.12 agent/tests/test_v3_power.py

WHY THIS EXISTS. `test_canonical_world.py --confirm` scores V3 on twenty
populations and reports **20.41% against a published band of 20-40%, clearing
the floor by 0.41 points with a 2 SE of 4.10**. That is a coin flip, and V3
carries more weight than its margin suggests: it is the only target measured on
a POLICY THIS PROJECT DID NOT WRITE running in this world, so it is the evidence
that the world is not calibrated easy. The V5 argument rests on it.

WHAT IS MEASURED. Both quantities here are POLICY-FREE with respect to the
agent, which is what makes a larger sample legitimate rather than a second bite:

  V1  first-presentation failure rate = at-risk cycles / cycles due. A property
      of the world. No policy touches it.
  V3  recovery of at-risk cycles by `doc_legal` -- Razorpay's documented
      schedule made legal, T+1..T+4. `mode="doc_legal"` installs
      `agent.policy.fixed_schedule.propose_fixed`, which consults no belief and
      never reads `cycle_value`, so **neither V1 nor V3 can move when the
      agent's belief constants move.** The 1 September repair
      (`prior_w` 9->5, `prior_floor` 0.5->0.1, `cycle_value` 0->0.6) is
      invariant here by construction, and the re-run confirms it rather than
      assuming it.

So this is not a re-measurement hoping for a better number. The point estimate
is expected to land where it already is; the interval is what changes.

100 populations against the canonical 20. Held-out is irrelevant for these two:
no selection was ever performed against V1 or V3, so there is no set to hold
out from.

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
from agent.tests._parallel import agent_job, run_jobs

N, K_FIXED, DAYS, PE = 100, 5, 120, 7
#: 100 populations. The canonical suite uses 700-719; this extends the same
#: family rather than moving to a different one.
POPS = list(range(700, 800))
SPEND = 0.93
K_SEED, BUF_SEED, BURN = 4242, 9182, 12
CANONICAL = dict(k_mean=2.0, k_max=8, payday_mode="statutory",
                 amount_mode="absolute", amount_median=855.0,
                 buffer_median=0.25, buffer_sigma=1.0, irregular_frac=0.00)
RUN_KW = dict(burn_cycles=BURN, mandate_outflow=True)
V1_BAND, V3_BAND = (0.08, 0.15), (0.20, 0.40)
#: The 20-population figures this is testing, from
#: logs/w13_canonical_n100.txt / test_canonical_world.py --confirm.
PRIOR_V1, PRIOR_V1_SE = 0.1058, 0.0
PRIOR_V3, PRIOR_V3_SE = 0.2041, 0.0410


def percell(ps):
    kw = dict(CANONICAL)
    kw["k_seed"] = K_SEED + ps
    kw["buffer_seed"] = BUF_SEED + ps
    return kw


def mean_se(xs):
    a = np.asarray(xs, dtype=float)
    return float(a.mean()), float(2 * a.std(ddof=1) / np.sqrt(len(a)))


def main() -> int:
    jobs = [(f"{ps}", (N, K_FIXED, ps, SPEND, DAYS, percell(ps)), 907,
             dict(payday_err=PE, pop_spend=SPEND, bcfg=w3.FITTED_BELIEF,
                  mode="doc_legal", **RUN_KW), False)
            for ps in POPS]
    print("V3 AND V1 AT FIVE TIMES THE SAMPLE")
    print(f"{len(jobs)} runs: {len(POPS)} populations x 1 arm (doc_legal), "
          f"n={N}, {DAYS}d, payday_err=+/-{PE}, pop_spend={SPEND}")
    print("doc_legal consults no belief, so the 1 Sept belief repair cannot "
          "move either number.")
    res = run_jobs(agent_job, jobs)

    v1s = [res[f"{ps}"]["recovery"]["first_presentation_failure_rate"]
           for ps in POPS]
    v3s = [res[f"{ps}"]["recovery"]["recovery_rate"] for ps in POPS]
    at_risk = sum(res[f"{ps}"]["recovery"]["at_risk"] for ps in POPS)
    due = sum(res[f"{ps}"]["recovery"]["cycles_due"] for ps in POPS)

    v1, v1se = mean_se(v1s)
    v3, v3se = mean_se(v3s)

    print()
    print("=" * 88)
    print(f"{'target':>8}{'n pops':>8}{'value':>10}{'2 SE':>8}"
          f"{'band':>14}{'verdict':>10}{'floor margin':>15}")
    for name, val, se, band, prior, prior_se in (
            ("V1", v1, v1se, V1_BAND, PRIOR_V1, PRIOR_V1_SE),
            ("V3", v3, v3se, V3_BAND, PRIOR_V3, PRIOR_V3_SE)):
        lo, hi = band
        hit = lo <= val <= hi
        print(f"{name:>8}{len(POPS):>8}{val*100:>9.2f}%{se*100:>+8.2f}"
              f"{f'{lo*100:.0f}-{hi*100:.0f}%':>14}"
              f"{'HIT' if hit else 'MISS':>10}"
              f"{(val - lo)*100:>+14.2f}")
    print("-" * 88)
    print(f"{'was (20 pops)':>8}        {PRIOR_V1*100:>9.2f}%"
          f"{PRIOR_V1_SE*100:>+8.2f}   V1")
    print(f"{'':>8}        {PRIOR_V3*100:>9.2f}%{PRIOR_V3_SE*100:>+8.2f}   V3")
    print(f"  at-risk cycles {at_risk} of {due} due, pooled over "
          f"{len(POPS)} populations")

    print()
    print("DOES THE V3 HIT SURVIVE?")
    print("=" * 88)
    lo = V3_BAND[0]
    margin = (v3 - lo) * 100
    shrink = (1 - v3se / PRIOR_V3_SE) * 100 if PRIOR_V3_SE else float("nan")
    print(f"  floor margin {margin:+.2f} points against a 2 SE of "
          f"{v3se*100:.2f} (was {PRIOR_V3_SE*100:.2f}, "
          f"{shrink:.0f}% narrower)")
    if v3 < lo:
        print("  VERDICT: the hit does NOT survive. V3 is BELOW its published "
              "floor at this sample size.")
        print("  The 20-population hit was inside the noise and must be "
              "withdrawn.")
    elif margin > v3se * 100:
        print("  VERDICT: the hit SURVIVES and the margin now exceeds 2 SE.")
        print("  V3 is inside its published band by more than the "
              "measurement error.")
    else:
        print("  VERDICT: the hit survives as a POINT ESTIMATE but the margin "
              "is still inside 2 SE.")
        print("  V3 lands in band; the interval still reaches below the "
              "floor. Report it as marginal.")
    print()
    print("WHAT THIS IS EVIDENCE FOR, AND WHAT WOULD FALSIFY IT")
    print("=" * 88)
    print("  V3 is the only validation target measured on a policy this "
          "project did not")
    print("  write. It running in band is the evidence that the canonical "
          "world is not")
    print("  calibrated easy -- which is the whole rebuttal to V5 sitting "
          "ABOVE its band.")
    print()
    print("  FALSIFIED IF: V3 drops below 20% at a larger sample (the world "
          "is harder than")
    print("  the published baseline range and V5's excess is not the agent), "
          "or if V3 is")
    print("  only in band at a calibration where V1 leaves ITS band (two "
          "dials fitted to")
    print("  two targets, which is the trap docs/00_HANDOFF.md names).")
    print()
    print("  BIAS. Both bands are [REPORTED], vendor-sourced, and aggregate "
          "non-comparable")
    print("  customer bases. The world and the agent share an author. "
          "Neither number is")
    print("  independent evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
