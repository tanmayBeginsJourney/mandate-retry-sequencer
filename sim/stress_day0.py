#!/usr/bin/env python3
"""
STRESS THE ONE REMAINING BAKED-IN POPULATION FACT.

`w3.FITTED_BELIEF` carries `prior_day0=8.0`: eight times the prior weight on
payday hypothesis 0. That is there because the population puts a large spike at
day 0 -- `w3.make_pop(payday_day0_frac=0.60)` -- and it was fitted on training
populations drawn with exactly that 0.60.

It is legitimate Tier-2 aggregate knowledge: the ML baseline learned the same
fact from data, where `tgt_day_mod_cyc` was its 4th most-split feature. But it
is a population fact frozen into a constant, and the question a judge will ask
is what happens when the population is not the one it was fitted on.

So: move the WORLD's `payday_day0_frac` and leave the prior fixed at its fitted
value. This is the same discipline as the spend-decay study -- shift the world,
never the belief. If the belief were re-fitted per world the experiment would
measure nothing.

Reported against `ml_index` (which learned the day-0 spike from data and should
also degrade) and `payday_wait` (which has no payday model to be wrong about,
so it is the floor this must not fall through).
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
DAY0 = (0.2, 0.4, 0.6, 0.8)
N, K, DAYS, SPEND, PE = 100, 5, 120, 1.05, 7
OUT = os.path.join(HERE, "ml_artifacts", "stress_day0.json")

ARMS = [
    ("payday_wait", "payday_wait", None),
    ("bayes_shipped", "solo_shared_pd", "SHIPPED"),
    ("bayes_fair", "solo_shared_pd", "FAIR"),
    ("ml_index", "ml_index", "ML"),
    ("oracle", "oracle", None),
]


def paired(a, b):
    d = (np.asarray(b) - np.asarray(a)) * 100
    return float(d.mean()), float(2 * d.std(ddof=1) / np.sqrt(len(d)))


def main():
    import mlmodel
    jobs = []
    for d0 in DAY0:
        for s in EVAL:
            for label, pol, extra in ARMS:
                kw = dict(payday_err=PE, pop_spend=SPEND)
                if extra == "FAIR":
                    kw["bcfg"] = w3.FITTED_BELIEF
                elif extra == "SHIPPED":
                    kw["bcfg"] = SHIPPED
                elif extra == "ML":
                    kw["ml_predict"] = mlmodel.predict
                jobs.append((f"{d0}|{s}|{label}", pol,
                             (N, K, s, SPEND, DAYS, d0, 0.0), 900 + s, kw))
    print(f"{len(jobs)} runs: {len(DAY0)} day0 fractions x {len(EVAL)} "
          f"populations x {len(ARMS)} arms")
    res = runner.run_jobs(jobs)

    dirty = sum(res[k]["violations"] for k in res)
    print(f"Stage 0 violations across all {len(res)} runs: "
          f"{'NONE' if not dirty else dirty}")
    print(f"\nprior_day0 stays at {w3.FITTED_BELIEF['prior_day0']} throughout; "
          f"only the WORLD moves.")
    print(f"The fit was done at payday_day0_frac=0.60.\n")

    labels = [l for l, _, _ in ARMS]
    print(f"{'payday_day0_frac':<18}" + "".join(f"{l:>15}" for l in labels))
    out = {}
    rows = {}
    for d0 in DAY0:
        row = {l: [res[f"{d0}|{s}|{l}"]["cycle_rec"] for s in EVAL]
               for l in labels}
        rows[d0] = row
        mark = "   <-- fitted here" if d0 == 0.6 else ""
        print(f"{d0:<18}" + "".join(f"{np.mean(row[l])*100:>14.2f}%"
                                    for l in labels) + mark)
        out[str(d0)] = {l: float(np.mean(v) * 100) for l, v in row.items()}

    print(f"\n{'payday_day0_frac':<18}{'fair - ml':>19}{'fair - payday_wait':>22}")
    for d0 in DAY0:
        r = rows[d0]
        a, ae = paired(r["ml_index"], r["bayes_fair"])
        b, be = paired(r["payday_wait"], r["bayes_fair"])
        f = lambda m, e: f"{m:+6.2f}+/-{e:.2f} {'SIG ' if abs(m) > e else 'n.s.'}"
        print(f"{d0:<18}{f(a,ae):>19}{f(b,be):>22}")
        out[str(d0)]["fair_minus_ml"] = [a, ae]
        out[str(d0)]["fair_minus_paydaywait"] = [b, be]

    base = np.mean(rows[0.6]["bayes_fair"]) * 100
    worst = min(np.mean(rows[d]["bayes_fair"]) * 100 for d in DAY0)
    print(f"\nbayes_fair at the fitted population (0.60): {base:.2f}%")
    print(f"bayes_fair worst across the sweep         : {worst:.2f}%")
    print(f"total degradation                         : {base - worst:.2f} pts")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1)
    print(f"\nsaved {OUT}")


if __name__ == "__main__":
    main()
