#!/usr/bin/env python3
"""
THE FAIR FIGHT: fit the three hand-set handicaps on the Bayes side.

*** WARNING, ADDED 28 AUGUST 2026: THIS SCRIPT DOES NOT REPRODUCE
*** w3.FITTED_BELIEF, AND DOCS THAT SAY IT DOES ARE WRONG.

Two of the five values in w3.FITTED_BELIEF cannot come out of this file:

  prior_floor=0.25   The string "prior_floor" appears NOWHERE below. BASE
                     omits it and none of the three sweeps sets it, so every
                     configuration this script evaluates inherits BeliefPD's
                     default prior_floor=1e-6 -- the HARD window that error 8
                     in docs/03_ERRORS.md identifies as the brittle failure.
                     The soft floor is described everywhere as "the important
                     part" and the script that supposedly chose it has no knob
                     for it.

  the objective      PE = 7 below, and score() passes it unchanged. There is
                     no loop over payday_err anywhere in this file. w3.py's
                     comment, ml_artifacts/belief_fit.json's `note` field and
                     (until today) fair_audit.py's own printed output all say
                     the config was "re-selected against the MEAN across
                     payday_err in {1,3,5,7,10,14}". It was not, here.

Measured 28 Aug 2026, identical call signature, eval populations 700-707,
pe=7:

    95.57%   w3.FITTED_BELIEF  (prior_floor=0.25)  <- what ships, what docs quote
    94.98%   what this script can emit (floor 1e-6)
    82.16%   BASE, as shipped before the fit

So re-running this WILL disagree with the frozen constant. Per
docs/06_MODEL_CARD.md section 4b that disagreement "is a finding" -- it is now
error 12 in docs/03_ERRORS.md, and it is written up rather than papered over.

DO NOT extend the search to close the gap before 5 September. Adding
prior_floor to the grid re-opens the fitted constant, which is frozen at tag
`model-frozen`, and the constant's measured behaviour is fine: the gain grows
from +2.12 at pe=1 to +19.76 at pe=14 (sim/fair_audit.py) instead of peaking
at the operating point it was selected on, which is the property error 8 asks
for. What is broken is the PROVENANCE, not the value. Fixing provenance means
extending this search AND re-validating, and that is a post-deadline job.


The ML model was allowed to fit itself to 800 training customers. The Bayes
filter was not: its payday grid, its prior and its cross-mandate spend
correction were all hand-set and never checked. This script gives it the same
opportunity, and nothing more.

WHAT THE FIT IS ALLOWED TO SEE. Selection is by `cycle_rec` on the TRAINING
populations (seeds 600-607) -- the same 800 customers the GBDT trained on --
and every reported number comes from the EVALUATION populations (700-707),
which neither model has seen. The objective is an outcome. Nothing here reads
c["payday"], c["salary"] or c["spend"].

Search is COORDINATE-WISE, not a full grid: stride first, then the prior, then
the spend correction. That is cheaper and it can miss an interaction; it is
labelled rather than dressed up as exhaustive.
"""
import itertools
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import numpy as np
import runner

TRAIN = list(range(600, 608))
EVAL = list(range(700, 708))
N, K, DAYS, SPEND, POP_SPEND, PE = 100, 5, 120, 1.05, 1.05, 7
OUT = os.path.join(HERE, "ml_artifacts", "belief_fit.json")

BASE = dict(stride=3, prior_w=None, prior_day0=1.0, spend_beta=0.045)


def score(cfgs, seeds, pol="solo_shared_pd"):
    """{label: mean cycle_rec} over `seeds`, one job per (cfg, seed)."""
    jobs = []
    for label, cfg in cfgs.items():
        for s in seeds:
            jobs.append((f"{label}|{s}", pol, (N, K, s, SPEND, DAYS), 900 + s,
                         dict(payday_err=PE, pop_spend=POP_SPEND, bcfg=cfg)))
    res = runner.run_jobs(jobs)
    return {label: float(np.mean([res[f"{label}|{s}"]["cycle_rec"]
                                  for s in seeds])) for label in cfgs}


def stage(name, cfgs, seeds=TRAIN):
    t0 = time.perf_counter()
    sc = score(cfgs, seeds)
    best = max(sc, key=sc.get)
    print(f"\n--- {name}  ({len(cfgs)} configs x {len(seeds)} train populations, "
          f"{time.perf_counter()-t0:.0f}s)")
    for label in sorted(sc, key=lambda x: -sc[x]):
        mark = "  <-- best" if label == best else ""
        print(f"    {label:<40} {sc[label]*100:6.2f}%{mark}")
    return cfgs[best], sc


def sweep_stride(cfg, tag):
    return stage(f"{tag}: stride", {
        f"stride={s}": dict(cfg, stride=s) for s in (1, 2, 3)
    })[0]


def sweep_prior(cfg, tag):
    grid = {"prior=exp(-0.10d) [as shipped]": dict(cfg, prior_w=None,
                                                   prior_day0=1.0)}
    for w, d0 in itertools.product((5, 7, 9, 12, 15), (1.0, 2.0, 4.0, 8.0)):
        grid[f"prior=uniform(w={w}) day0x{d0:g}"] = dict(
            cfg, prior_w=w, prior_day0=d0)
    return stage(f"{tag}: payday prior", grid)[0]


def sweep_beta(cfg, tag):
    return stage(f"{tag}: spend_beta", {
        f"spend_beta={b:g}": dict(cfg, spend_beta=b)
        for b in (0.0, 0.02, 0.045, 0.07, 0.10, 0.14)
    })[0]


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    print("Selecting on TRAINING populations 600-607 by cycle_rec, pe=7, n=100.")
    print(f"Baseline (all three handicaps as shipped): {BASE}")

    # TWO PASSES, and the second one is not ceremony. The first run of this
    # search picked stride=2 in pass 1 -- chosen against the OLD prior -- and
    # once the prior was fitted, stride=1 became better on both train and eval.
    # Coordinate-wise search is only honest if you go round twice and say what
    # moved. It can still miss an interaction the second pass does not surface.
    cfg = dict(BASE)
    for tag in ("pass 1", "pass 2"):
        cfg = sweep_stride(cfg, tag)
        cfg = sweep_prior(cfg, tag)
        cfg = sweep_beta(cfg, tag)
        print(f"\n  after {tag}: {cfg}")

    print("\n" + "=" * 78)
    print(f"FITTED CONFIG: {cfg}")
    print("=" * 78)

    # --- report on held-out evaluation populations --------------------------
    both = {"as shipped": BASE, "fair fight": cfg}
    tr = score(both, TRAIN)
    ev = score(both, EVAL)
    print(f"\n{'':<14}{'train 600-607':>16}{'eval 700-707':>16}")
    for label in ("as shipped", "fair fight"):
        print(f"{label:<14}{tr[label]*100:>15.2f}%{ev[label]*100:>15.2f}%")
    print(f"{'gain':<14}{(tr['fair fight']-tr['as shipped'])*100:>15.2f} "
          f"{(ev['fair fight']-ev['as shipped'])*100:>14.2f}")

    with open(OUT, "w") as fh:
        json.dump(dict(fitted=cfg, base=BASE, train=tr, eval=ev), fh, indent=1)
    print(f"\nsaved {OUT}")


if __name__ == "__main__":
    main()
