#!/usr/bin/env python3
"""HOW MUCH OF THE HEADLINE IS THE WORLD BEING HARSH?

`pop_spend` sets how much of the salary a customer spends per cycle. At the
value the whole project reports on -- 1.05 -- the account cannot cover the
debit on its due date 53% of the time. Public secondary sources put real UPI
AutoPay failure at 8-20%. If the gap over the baseline only exists because the
world is unusually poor, that has to be said out loud.

Nothing frozen is touched: `pop_spend` is already an argument of
`harness.run`, and this script only reads.

Reproduce with `python scripts/spend_sweep.py`. NOT a gated number.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SIM = os.path.join(ROOT, "sim")
if SIM not in sys.path:
    sys.path.insert(0, SIM)

import numpy as np
import w3
import runner

EVAL = list(range(700, 708))
SPENDS = (0.60, 0.80, 0.90, 1.05)
N, K, DAYS, PE = 100, 5, 120, 7

ARMS = [
    ("payday_wait", "payday_wait", None),
    ("agent", "solo_shared_pd", "FIT"),
    ("oracle", "oracle", None),
]


def paired(a, b):
    d = (np.asarray(b) - np.asarray(a)) * 100
    return float(d.mean()), float(2 * d.std(ddof=1) / np.sqrt(len(d)))


def main():
    jobs = []
    for sp in SPENDS:
        for s in EVAL:
            for label, pol, cfg in ARMS:
                kw = dict(payday_err=PE, pop_spend=sp)
                if cfg == "FIT":
                    kw["bcfg"] = w3.FITTED_BELIEF
                jobs.append((f"{sp}|{s}|{label}", pol,
                             (N, K, s, sp, DAYS), 900 + s, kw))
    print(f"{len(jobs)} runs: {len(SPENDS)} spend levels x {len(EVAL)} "
          f"populations x {len(ARMS)} arms, payday_err={PE}")
    res = runner.run_jobs(jobs)
    dirty = sum(res[k]["violations"] for k in res)
    print(f"Stage 0 violations: {'NONE' if not dirty else dirty}\n")

    print("n=100, 8 held-out populations (700-707), 120d, payday_err=+/-7")
    print(f"{'spend':>7}{'baseline':>12}{'agent':>10}{'oracle':>10}"
          f"{'agent - baseline':>22}{'approval':>11}")
    out = {}
    for sp in SPENDS:
        row = {l: [res[f"{sp}|{s}|{l}"]["cycle_rec"] for s in EVAL]
               for l, _, _ in ARMS}
        appr = np.mean([res[f"{sp}|{s}|payday_wait"]["approval"] for s in EVAL])
        m, e = paired(row["payday_wait"], row["agent"])
        sig = "SIG " if abs(m) > e else "n.s."
        print(f"{sp:>7.2f}{np.mean(row['payday_wait'])*100:>11.2f}%"
              f"{np.mean(row['agent'])*100:>9.2f}%"
              f"{np.mean(row['oracle'])*100:>9.2f}%"
              f"{m:>+14.2f}+/-{e:.2f} {sig}"
              f"{appr*100:>10.1f}%")
        out[str(sp)] = {l: float(np.mean(v) * 100) for l, v in row.items()}
        out[str(sp)]["agent_minus_baseline"] = [m, e]
        out[str(sp)]["baseline_approval"] = float(appr)

    out_dir = os.path.join(SIM, "ml_artifacts")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "spend_sweep.json"), "w") as fh:
        json.dump(out, fh, indent=1)


if __name__ == "__main__":
    main()
