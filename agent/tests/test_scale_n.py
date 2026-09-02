"""THE SCALE STUDY. Does the canonical conclusion depend on n?

The canonical world draws mandates per customer from `1 + Poisson(1)` -- a mean
of about 2, where an earlier world fixed 5. Fewer mandates per customer means
fewer money actions per customer and fewer at-risk cycles per customer, so an
`n` that was adequate at k=5 is not adequate at k~2 by inheritance. It has to
be re-measured, and that is what this does.

WHAT IS HELD FIXED. Everything except `n` and, in the seed study, the run seed:
the k distribution, the ten held-out populations (710-719), `pop_spend=0.93`,
`payday_err=7`, the fitted belief, the burn-in, mandate outflow, the decline
taxonomy (off), and both arms. `agent/tests/_canonical.py` is the single source
for all of it -- this file defines no world of its own.

THREE VARIANCES, AND THEY ARE NOT THE SAME NUMBER.

  finite-sample     shrinks with n. Read it off the scale table: the paired
                    2 SE across populations at a fixed run seed.
  population        the spread BETWEEN the ten populations. Part of the 2 SE
                    above, and it does NOT shrink to zero with n, because each
                    population is a different draw of customers.
  run-seed          the same ten populations, a different stochastic run. The
                    published 2 SE is computed at ONE run seed and therefore
                    says nothing about it. `--seeds` measures it.

The interval quoted in docs/results.md is the paired 2 SE across populations.
If run-seed variation is comparable to it, that interval is understated, and
this script is how that was checked rather than assumed.

    py -3.12 agent/tests/test_scale_n.py                  # scale table
    py -3.12 agent/tests/test_scale_n.py --seeds          # + run-seed study
    py -3.12 agent/tests/test_scale_n.py --ns 100,500     # a subset

NOT GATE-PROTECTED. Every run is one process (`_parallel.py`).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np

import agent  # noqa: F401  -- puts sim/ on the path
import w3
from agent.tests import _canonical as _CAN
from agent.tests._parallel import agent_job, harness_job, run_jobs

#: The canonical world, minus the one dimension under study. `_CANON` forces
#: the canonical branch of `_canonical.py` regardless of this script's argv:
#: there is no old-world mode here, so there is no flag to read.
_CANON = ["--canonical"]
POPS = list(_CAN.POPS)
SPEND = _CAN.SPEND
K_CAP, DAYS, PE = 5, 120, 7
RUN_SEED = 7
NS = (100, 250, 500, 1000, 2000)
#: Independent run seeds for the variance study. 7 is the published one; the
#: other three were fixed before the first run and are otherwise arbitrary.
SEEDS = (7, 101, 202, 303)
OUT = os.path.join(ROOT, "sim", "ml_artifacts", "scale_n.json")


def _pair(agent_v, base_v):
    d = (np.asarray(agent_v, float) - np.asarray(base_v, float)) * 100
    return float(d.mean()), float(2 * d.std(ddof=1) / np.sqrt(len(d)))


def _mandates(n: int) -> int:
    """Total mandates across the ten populations. World arithmetic, no policy."""
    from agent.batch import make_pop
    return sum(len(c["mandates"])
               for ps in POPS
               for c in make_pop(n, K_CAP, ps, spend=SPEND, days=DAYS,
                                 **_CAN.pop_kwargs(ps, _CANON)))


def _cell(n: int, run_seed: int) -> dict:
    """One (n, run seed) cell: both arms over the ten held-out populations."""
    kw = dict(payday_err=PE, pop_spend=SPEND, bcfg=w3.FITTED_BELIEF,
              time_major=True, mode="full", **_CAN.run_kwargs(_CANON))
    spec = {ps: (n, K_CAP, ps, SPEND, DAYS, _CAN.pop_kwargs(ps, _CANON))
            for ps in POPS}
    ajobs = [(ps, spec[ps], run_seed, kw, False) for ps in POPS]
    hjobs = [(ps, "payday_wait", spec[ps], run_seed,
              dict(payday_err=PE, pop_spend=SPEND, **_CAN.run_kwargs(_CANON)))
             for ps in POPS]
    t0 = time.time()
    ares = run_jobs(agent_job, ajobs)
    hres = run_jobs(harness_job, hjobs)
    wall = time.time() - t0

    acr = [ares[ps]["cycle_rec"] for ps in POPS]
    hcr = [hres[ps]["cycle_rec"] for ps in POPS]
    up, se = _pair(acr, hcr)
    rec = [ares[ps]["recovery"] for ps in POPS]
    return dict(
        n=n, run_seed=run_seed, pops=len(POPS),
        cycles_due=int(sum(ares[ps]["cycles_due"] for ps in POPS)),
        executed=int(sum(ares[ps]["gate_allowed"] for ps in POPS)),
        agent_cycle_rec=float(np.mean(acr) * 100),
        base_cycle_rec=float(np.mean(hcr) * 100),
        uplift=up, uplift_2se=se,
        agent_cycle_rec_by_pop=[float(x * 100) for x in acr],
        base_cycle_rec_by_pop=[float(x * 100) for x in hcr],
        at_risk=int(sum(r["at_risk"] for r in rec)),
        recovered=int(sum(r["recovered"] for r in rec)),
        recovery_rate=float(np.mean([r["recovery_rate"] for r in rec]) * 100),
        early_share=float(np.mean([r["early_share"] for r in rec]) * 100),
        median_days=float(np.mean([r["median_days_to_recovery"] for r in rec])),
        v1_first_fail=float(np.mean(
            [r["first_presentation_failure_rate"] for r in rec]) * 100),
        survival=float(np.mean([ares[ps]["survival"] for ps in POPS]) * 100),
        base_survival=float(np.mean([hres[ps]["survival"] for ps in POPS]) * 100),
        att_per_cycle=float(np.mean([ares[ps]["att_per_cycle"] for ps in POPS])),
        recovered_paise=int(sum(ares[ps]["recovered_paise"] for ps in POPS)),
        stage0_refusals=int(sum(sum(ares[ps]["gate_refusals"].values())
                                for ps in POPS)),
        mandate_dead=int(sum(ares[ps]["stops"].get("MANDATE_DEAD", 0)
                             for ps in POPS)),
        wall_s=round(wall, 1),
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ns", default=",".join(str(x) for x in NS))
    ap.add_argument("--seeds", action="store_true",
                    help="also run the run-seed variance study at --seed-n")
    ap.add_argument("--seed-n", type=int, default=500)
    a = ap.parse_args(argv)
    ns = [int(x) for x in a.ns.split(",") if x.strip()]

    print("=" * 118)
    print("SCALE STUDY -- what n does to the canonical conclusion")
    print("=" * 118)
    print(_CAN.banner(_CANON))
    print(f"payday_err=+/-{PE}, {DAYS}d, run seed {RUN_SEED}, "
          f"{len(POPS)} held-out populations {POPS[0]}-{POPS[-1]}. "
          f"Only n moves.")
    print()

    rows = []
    for n in ns:
        m = _mandates(n)
        r = _cell(n, RUN_SEED)
        r["mandates"] = m
        rows.append(r)
        print(f"  n={n:<5d} done in {r['wall_s']:6.1f}s   "
              f"uplift {r['uplift']:+.2f} (2 SE {r['uplift_2se']:.2f})",
              flush=True)

    print()
    print(f"{'n':>6}{'mandates':>10}{'cycles due':>11}{'executed':>10}"
          f"{'at risk':>9}{'baseline':>10}{'agent':>9}{'uplift':>9}"
          f"{'2 SE':>7}{'recovery':>10}{'V1':>7}{'surv':>8}{'att/cyc':>9}"
          f"{'wall s':>8}")
    for r in rows:
        print(f"{r['n']:>6}{r['mandates']:>10}{r['cycles_due']:>11}"
              f"{r['executed']:>10}{r['at_risk']:>9}"
              f"{r['base_cycle_rec']:>9.2f}%{r['agent_cycle_rec']:>8.2f}%"
              f"{r['uplift']:>+9.2f}{r['uplift_2se']:>7.2f}"
              f"{r['recovery_rate']:>9.2f}%{r['v1_first_fail']:>6.2f}%"
              f"{r['survival']:>7.2f}%{r['att_per_cycle']:>9.3f}"
              f"{r['wall_s']:>8.1f}")

    if len(rows) > 1:
        ref = rows[-1]
        print()
        print(f"Distance from the largest cell run (n={ref['n']}), in points:")
        print(f"{'n':>6}{'uplift delta':>14}{'baseline delta':>16}"
              f"{'agent delta':>13}{'recovery delta':>16}{'V1 delta':>10}"
              f"{'speedup':>9}")
        for r in rows:
            print(f"{r['n']:>6}{r['uplift'] - ref['uplift']:>+14.2f}"
                  f"{r['base_cycle_rec'] - ref['base_cycle_rec']:>+16.2f}"
                  f"{r['agent_cycle_rec'] - ref['agent_cycle_rec']:>+13.2f}"
                  f"{r['recovery_rate'] - ref['recovery_rate']:>+16.2f}"
                  f"{r['v1_first_fail'] - ref['v1_first_fail']:>+10.2f}"
                  f"{ref['wall_s'] / max(r['wall_s'], 0.1):>8.1f}x")

    seed_rows = []
    if a.seeds:
        print()
        print("=" * 118)
        print(f"RUN-SEED VARIANCE at n={a.seed_n}. Same ten populations, same "
              f"world, {len(SEEDS)} independent run seeds.")
        print("=" * 118)
        for s in SEEDS:
            r = _cell(a.seed_n, s)
            r["mandates"] = _mandates(a.seed_n)
            seed_rows.append(r)
            print(f"  seed {s:<5d} baseline {r['base_cycle_rec']:6.2f}%  "
                  f"agent {r['agent_cycle_rec']:6.2f}%  "
                  f"uplift {r['uplift']:+6.2f}  "
                  f"(within-seed paired 2 SE {r['uplift_2se']:.2f})  "
                  f"recovery {r['recovery_rate']:6.2f}%  "
                  f"at risk {r['at_risk']}", flush=True)
        print()
        for field, label in (("uplift", "uplift"),
                             ("base_cycle_rec", "baseline"),
                             ("agent_cycle_rec", "agent"),
                             ("recovery_rate", "recovery of at-risk"),
                             ("v1_first_fail", "V1")):
            v = np.array([r[field] for r in seed_rows], float)
            print(f"  {label:>20}: mean {v.mean():7.3f}  sd {v.std(ddof=1):6.3f}"
                  f"  range [{v.min():.3f}, {v.max():.3f}]"
                  f"  spread {v.max() - v.min():.3f}")
        u = np.array([r["uplift"] for r in seed_rows], float)
        across = 2 * u.std(ddof=1) / np.sqrt(len(u))
        within = float(np.mean([r["uplift_2se"] for r in seed_rows]))
        print()
        print(f"  ACROSS-SEED 2 SE of the uplift: {across:.2f} on "
              f"{len(SEEDS)} seeds.")
        print(f"  WITHIN-SEED paired 2 SE across populations: {within:.2f} "
              f"(mean over seeds).")
        print("  These measure different things. The published interval is the")
        print("  second. The first is what a re-run on a fresh run seed would")
        print("  move by, and it is reported beside it rather than folded in.")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(dict(canonical=_CAN.banner(_CANON),
                       run_seed=RUN_SEED, pops=POPS, spend=SPEND,
                       payday_err=PE, days=DAYS,
                       scale=rows, seeds=seed_rows), fh, indent=1)
    print()
    print(f"saved {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
