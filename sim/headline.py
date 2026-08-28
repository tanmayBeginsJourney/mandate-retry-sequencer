#!/usr/bin/env python3
"""
THE CONDITIONAL HEADLINE: does the sophisticated system beat the 5-line
heuristic, and at what payday uncertainty does that flip?

This is the number that decides whether the project is worth building. The
version in docs/02_RESULTS.md before 28 August 2026 was measured at n=30 with
4 seeds on the UNFITTED belief, and reported a crossover between +/-3 and +/-7
days. This regenerates it at n=100 across 8 held-out populations on the fitted
filter.

Arms:
  payday_wait      the competitive baseline -- what a good rival team builds in
                   an afternoon. Waits for the estimated payday, one attempt
                   per day, no belief filter and no index.
  bayes_shipped    solo_shared_pd with the old hand-set belief values.
  bayes_fitted     solo_shared_pd with w3.FITTED_BELIEF. THE SHIPPING POLICY.
  oracle           true balance and true future. Upper bound; must dominate.

NOT a gated number. Reproduce with `python sim/headline.py`.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import numpy as np
import w3
import runner

SHIPPED = dict(stride=3, prior_w=None, prior_day0=1.0, spend_beta=0.045)
EVAL = list(range(700, 708))
PES = (1, 3, 5, 7, 10, 14)
N, K, DAYS, SPEND = 100, 5, 120, 1.05
OUT = os.path.join(HERE, "ml_artifacts", "headline.json")

ARMS = [
    ("payday_wait", "payday_wait", None),
    ("bayes_shipped", "solo_shared_pd", SHIPPED),
    ("bayes_fitted", "solo_shared_pd", "FIT"),
    ("oracle", "oracle", None),
]


def paired(a, b):
    d = (np.asarray(b) - np.asarray(a)) * 100
    return float(d.mean()), float(2 * d.std(ddof=1) / np.sqrt(len(d)))


def main():
    jobs = []
    for pe in PES:
        for s in EVAL:
            for label, pol, cfg in ARMS:
                kw = dict(payday_err=pe, pop_spend=SPEND)
                if cfg == "FIT":
                    kw["bcfg"] = w3.FITTED_BELIEF
                elif cfg:
                    kw["bcfg"] = cfg
                jobs.append((f"{pe}|{s}|{label}", pol,
                             (N, K, s, SPEND, DAYS), 900 + s, kw))
    print(f"{len(jobs)} runs: {len(PES)} payday_err x {len(EVAL)} populations "
          f"x {len(ARMS)} arms")
    res = runner.run_jobs(jobs)
    dirty = sum(res[k]["violations"] for k in res)
    print(f"Stage 0 violations: {'NONE' if not dirty else dirty}\n")

    labels = [l for l, _, _ in ARMS]
    print("cycles collected / cycles due, n=100, 8 held-out populations "
          "(700-707), 120d")
    print(f"{'payday known to':<18}" + "".join(f"{l:>16}" for l in labels)
          + f"{'fitted - heuristic':>22}")
    out = {}
    for pe in PES:
        row = {l: [res[f"{pe}|{s}|{l}"]["cycle_rec"] for s in EVAL]
               for l in labels}
        m, e = paired(row["payday_wait"], row["bayes_fitted"])
        verdict = f"{m:+6.2f}+/-{e:.2f} {'SIG ' if abs(m) > e else 'n.s.'}"
        print(f"+/-{pe:<15}" + "".join(f"{np.mean(row[l])*100:>15.2f}%"
                                       for l in labels) + f"{verdict:>22}")
        out[str(pe)] = {l: float(np.mean(v) * 100) for l, v in row.items()}
        out[str(pe)]["fitted_minus_heuristic"] = [m, e]

    print("\nThe crossover -- the payday uncertainty at which the system starts")
    print("beating the heuristic -- is where the last column turns positive.")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1)
    print(f"saved {OUT}")


if __name__ == "__main__":
    main()
