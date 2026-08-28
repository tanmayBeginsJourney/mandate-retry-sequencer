#!/usr/bin/env python3
"""
Audit the fair-fight filter before believing it. It reports ~95.6% against a
100% oracle, and `03_ERRORS.md` says a near-zero oracle gap is a symptom, not
an achievement -- error 5 in this project's own history was exactly that. That
4.4-point gap IS now the near-zero-oracle-gap condition this project warns
about, and docs/02_RESULTS.md's "+18.5 to +22.7 pts of headroom" is a stale
figure from the unfitted filter. See error 13 in docs/03_ERRORS.md.

(This docstring said "97.53%" until 28 Aug 2026. That number is in
ml_artifacts/belief_fit.json and NOTHING reproduces it -- the identical call
signature measures 95.57%. Do not propagate it. See error 12.)

Three checks:

  1. DOES THE FITTED PRIOR GENERALISE ACROSS payday_err? This check earned
     its place. The FIRST fit was done at payday_err=7 only, and selected a
     hard window of half-width 7 -- the same number as the injected noise. It
     looked superb (+15.37) and was brittle: at payday_err=14 it measured
     -4.85, WORSE than the filter it replaced, because the true payday fell
     outside the window at weight 1e-6 and could never be recovered. The
     config was re-fitted against the mean across payday_err. Keep running
     this: a config whose gain peaks at the operating point it was fitted on
     is tuned to the harness, not fitted to the population.

  2. WHOSE CALIBRATION DOES S1 ACTUALLY MEASURE? S1 runs `portfolio`, which
     does NOT end in "_pd" and therefore carries w3.Belief -- the POINT
     ESTIMATE payday filter. The shipping policy is solo_shared_pd, which
     carries w3.BeliefPD. So S1 has never measured the filter that ships.

  3. CALIBRATION OF THE FILTER THAT ACTUALLY SHIPS, shipped vs fitted, using
     S1's own binning and threshold.

NOTE the `if __name__ == "__main__"` guard. Windows spawns rather than forks,
so a worker re-imports this module; without the guard it re-enters run_jobs and
multiprocessing refuses to start. The first draft of this file did exactly
that -- the trap is documented in sim/runner.py and it still caught me.
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

BASE = dict(stride=3, prior_w=None, prior_day0=1.0, spend_beta=0.045)
FAIR = w3.FITTED_BELIEF
EVAL = list(range(700, 708))


def paired(a, b):
    d = (np.asarray(b) - np.asarray(a)) * 100
    return float(d.mean()), float(2 * d.std(ddof=1) / np.sqrt(len(d)))


def reliability(cal, label):
    cal = np.asarray(cal, dtype=float)
    edges = np.linspace(0, 1, 11)
    rows, ece, ntot = [], 0.0, len(cal)
    for i in range(10):
        sel = cal[(cal[:, 0] >= edges[i])
                  & (cal[:, 0] < edges[i + 1] + (1e-9 if i == 9 else 0))]
        if len(sel) < 20:
            continue
        pr, em = sel[:, 0].mean(), sel[:, 1].mean()
        rows.append((edges[i], edges[i + 1], len(sel), pr, em))
        ece += len(sel) / ntot * abs(pr - em)
    mono = all(rows[i][4] <= rows[i + 1][4] + 0.02 for i in range(len(rows) - 1))
    verdict = "PASS" if (ece < 0.10 and mono) else "FAIL"
    print(f"\n   {label}: ECE={ece:.3f} monotone={mono}  -> S1's rule says "
          f"{verdict}   (n={ntot})")
    for lo, hi, n, pr, em in rows:
        print(f"      P in [{lo:.1f},{hi:.1f})  n={n:>6}  predicted={pr:.3f}"
              f"  actual={em:.3f}  {'over' if pr > em else 'under'} by "
              f"{abs(pr - em):.3f}")
    return ece, mono


def main():
    print("=" * 78)
    print("1. DOES THE FITTED PRIOR GENERALISE ACROSS payday_err?")
    print("=" * 78)
    print("   Every world in the misspecification study uses payday_err=7.")
    print("   A config whose gain PEAKS at the pe it was fitted on is tuned to")
    print("   the harness. The first fit did exactly that and went negative at")
    print("   pe=14.")
    print()
    print("   *** READ THIS BEFORE QUOTING THE TABLE BELOW. ***")
    print("   This script used to claim the current config 'is selected against")
    print("   the mean across pe'. That is NOT what sim/fit_belief.py does:")
    print("   it selects at PE=7 only (fit_belief.py:35) and has no prior_floor")
    print("   in its search space at all, so it CANNOT emit w3.FITTED_BELIEF.")
    print("   The claim was corrected 28 Aug 2026 -- see NOTES.md and error 12")
    print("   in docs/03_ERRORS.md. The OUTCOME below still holds and is the")
    print("   thing that matters: the gain grows with payday uncertainty rather")
    print("   than peaking at pe=7. Trust the measurement, not the provenance.")
    PES = (1, 3, 5, 7, 10, 14)
    jobs = []
    for pe in PES:
        for label, cfg in (("shipped", BASE), ("fair", FAIR)):
            for s in EVAL:
                jobs.append((f"{pe}|{label}|{s}", "solo_shared_pd",
                             (100, 5, s, 1.05, 120), 900 + s,
                             dict(payday_err=pe, pop_spend=1.05, bcfg=cfg)))
    res = runner.run_jobs(jobs)
    print(f"\n   {'payday_err':<12}{'shipped':>10}{'fitted':>10}{'gain':>22}")
    for pe in PES:
        a = [res[f"{pe}|shipped|{s}"]["cycle_rec"] for s in EVAL]
        b = [res[f"{pe}|fair|{s}"]["cycle_rec"] for s in EVAL]
        m, e = paired(a, b)
        mark = "   <-- fitted here" if pe == 7 else ""
        print(f"   {pe:<12}{np.mean(a)*100:>9.2f}%{np.mean(b)*100:>9.2f}%"
              f"   {m:+6.2f}+/-{e:.2f} {'SIG ' if abs(m) > e else 'n.s.'}{mark}")

    print()
    print("=" * 78)
    print("2. WHICH BELIEF DOES GATE S1 MEASURE?")
    print("=" * 78)
    for pol in ("portfolio", "solo_shared_pd"):
        cls = w3.BeliefPD if pol.endswith("_pd") else w3.Belief
        tag = ("   <-- what S1 runs" if pol == "portfolio"
               else "   <-- what the project recommends")
        print(f"   {pol:<18} -> {cls.__name__}{tag}")
    print("   So S1 gates the calibration of the POINT-ESTIMATE filter, not")
    print("   the payday-posterior filter the project actually recommends.")

    print()
    print("=" * 78)
    print("3. CALIBRATION OF THE FILTER THAT SHIPS (S1's binning, held-out)")
    print("=" * 78)
    cjobs = []
    for label, cfg in (("shipped", BASE), ("fair", FAIR)):
        for s in EVAL[:3]:
            cjobs.append((f"c|{label}|{s}", "solo_shared_pd",
                          (100, 5, s, 1.05, 120), 900 + s,
                          dict(payday_err=7, pop_spend=1.05, bcfg=cfg,
                               collect_calib=True)))
    cres = runner.run_jobs(cjobs)
    for label in ("shipped", "fair"):
        cal = []
        for s in EVAL[:3]:
            cal += cres[f"c|{label}|{s}"]["calib"]
        reliability(cal, f"BeliefPD {label}")


if __name__ == "__main__":
    main()
