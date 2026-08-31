#!/usr/bin/env python3
"""A3 discount sweep on the shipping filter. Not gate-protected."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM = os.path.join(ROOT, "sim")
if SIM not in sys.path:
    sys.path.insert(0, SIM)

import numpy as np
import harness
import runner
import w3

DISC = [0.80, 0.85, 0.88, 0.90, 0.92, 0.94, 0.96, 0.98, 1.00]
POP_SPECS = [(100, 5, 700 + r, 1.05, 120) for r in range(8)]


def main() -> None:
    jobs = []
    for d in DISC:
        for spec in POP_SPECS:
            kw = dict(payday_err=7, discount=d, bcfg=w3.FITTED_BELIEF,
                      pop_spend=1.05)
            jobs.append(((d, spec[2]), "solo_shared_pd", spec, 907, kw))

    cache = runner.run_jobs(jobs)
    print("discount sweep: solo_shared_pd, pe=7, eval pops 700-707, FITTED_BELIEF")
    means = []
    for d in DISC:
        recs = [cache[(d, spec[2])]["cycle_rec"] for spec in POP_SPECS]
        m = float(np.mean(recs) * 100)
        means.append(m)
        print(f"  {d:.2f}  {m:.2f}%")
    print(f"  spread  {min(means):.2f}% - {max(means):.2f}% "
          f"({max(means) - min(means):.1f} pts)")


if __name__ == "__main__":
    main()
