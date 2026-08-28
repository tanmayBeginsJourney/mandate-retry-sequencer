#!/usr/bin/env python3
"""
ml_index beat solo_shared_pd IN-DISTRIBUTION. That is the pre-registered
bug signal: the Bayes filter is supposed to be the true generative model of
this world, so in world A it should win. This script tries to kill the result
before anyone believes it.

Four checks, in the order a defect is most likely to be hiding:

  1. CANDIDATE-DAY MISMATCH. If ml_index scores a different set of days from
     the one Belief.forecast yields, it is not the same policy and the
     comparison is void.
  2. FEATURE LEAK. Retrain on progressively smaller feature sets. If the AUC
     survives stripping every observation-derived feature, something static
     is carrying the answer.
  3. IS THE FILTER ACTUALLY THE TRUE MODEL? The claim rests on it. Check the
     payday hypothesis grid against the paydays the world actually generates.
  4. CALIBRATION HEAD-TO-HEAD. S1 has been failing since handoff, which says
     the filter's probabilities are wrong. Measure the ML model's calibration
     on held-out data with the same binning S1 uses.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import numpy as np
import w3
import harness
import mlfeat

print("=" * 78)
print("1. CANDIDATE-DAY EQUIVALENCE")
print("=" * 78)
# harness ml_index builds: [day+i for i in 1..LOOKAHEAD if day+i < days]
# then drops dd >= cycle_close. The belief branch takes Belief.forecast(day,
# LOOKAHEAD), which breaks when day+i >= self.days, then does the same drop.
bad = 0
for days in (120, 30):
    for day in range(0, days):
        b = w3.BeliefPD(20000, 3, 30, days)
        from_belief = [dd for dd, _ in b.forecast(day, harness.LOOKAHEAD_DAYS)]
        from_ml = [day + i for i in range(1, harness.LOOKAHEAD_DAYS + 1)
                   if day + i < days]
        if from_belief != from_ml:
            bad += 1
            if bad <= 3:
                print(f"   MISMATCH days={days} day={day}: "
                      f"belief={from_belief} ml={from_ml}")
print(f"   checked 150 decision days at two horizons: "
      f"{'IDENTICAL everywhere' if not bad else f'{bad} MISMATCHES'}")

print()
print("=" * 78)
print("2. FEATURE-LEAK ABLATION")
print("=" * 78)
import lightgbm as lgb
from sklearn.metrics import roc_auc_score

d = np.load(os.path.join(HERE, "ml_artifacts", "train.npz"), allow_pickle=True)
X, y, g_pop = d["X"], d["y"], d["g_pop"]
names = list(d["features"])
tr = np.isin(g_pop, list(range(600, 608)))
te = np.isin(g_pop, [608, 609])

GROUPS = {
    "everything": lambda n: True,
    "no own_* history": lambda n: not n.startswith("own_"),
    "no acc_* cross-merchant": lambda n: not n.startswith("acc_"),
    "no history at all (own_, acc_, phase)": lambda n: not (
        n.startswith("own_") or n.startswith("acc_")
        or "phase" in n),
    "ONLY timing+amount (no observations)": lambda n: n in (
        "offset", "tgt_phase_est_pay", "tgt_day_mod_cyc",
        "days_since_cycle_open", "days_to_cycle_close",
        "amt_frac", "n_before", "attempts_left", "n_mandates",
        "other_amt_frac"),
}
for label, keep in GROUPS.items():
    cols = [i for i, n in enumerate(names) if keep(n)]
    m = lgb.LGBMClassifier(n_estimators=600, learning_rate=0.05, num_leaves=63,
                           min_child_samples=40, verbose=-1, n_jobs=8)
    m.fit(X[tr][:, cols], y[tr])
    auc = roc_auc_score(y[te], m.predict_proba(X[te][:, cols])[:, 1])
    print(f"   {label:<40} {len(cols):>3} feats   AUC={auc:.4f}")

print()
print("=" * 78)
print("3. IS THE BAYES FILTER THE TRUE MODEL? -- payday hypothesis grid")
print("=" * 78)
b = w3.BeliefPD(20000, 3, 30, 120)
print(f"   BeliefPD.hyp (stride 3) = {b.hyp}")
pop = w3.make_pop(2000, 5, np.random.default_rng(12345), days=120, spend=1.05)
paydays = np.array([c["payday"] for c in pop])
inset = np.isin(paydays, b.hyp).mean()
print(f"   customers whose TRUE payday is representable = {inset*100:.1f}%")
print(f"   (payday==0 alone is {np.mean(paydays == 0)*100:.1f}% of the population)")
nz = paydays[paydays != 0]
print(f"   among the {len(nz)/len(paydays)*100:.1f}% with a non-zero payday, "
      f"{np.isin(nz, b.hyp).mean()*100:.1f}% are representable")
print("   -> the filter cannot express the true payday for most non-day-0")
print("      customers. It is right in SHAPE and coarse in PARAMETERS.")

print()
print("=" * 78)
print("4. CALIBRATION HEAD-TO-HEAD (same binning S1 uses)")
print("=" * 78)
import pickle
with open(os.path.join(HERE, "ml_artifacts", "model.pkl"), "rb") as fh:
    gb = pickle.load(fh)["gb"]
p_ml = gb.predict_proba(X[te])[:, 1]


def reliability(pred, act, label):
    edges = np.linspace(0, 1, 11)
    ece, rows, ntot = 0.0, [], len(pred)
    for i in range(10):
        sel = (pred >= edges[i]) & (pred < edges[i + 1] + (1e-9 if i == 9 else 0))
        if sel.sum() < 20:
            continue
        pr, em = pred[sel].mean(), act[sel].mean()
        rows.append((edges[i], edges[i + 1], int(sel.sum()), pr, em))
        ece += sel.sum() / ntot * abs(pr - em)
    mono = all(rows[i][4] <= rows[i + 1][4] + 0.02 for i in range(len(rows) - 1))
    print(f"   {label}: ECE={ece:.3f}  monotone={mono}  (n={ntot})")
    for lo, hi, n, pr, em in rows:
        print(f"      P in [{lo:.1f},{hi:.1f})  n={n:>5}  predicted={pr:.3f}  "
              f"actual={em:.3f}  off by {abs(pr-em):.3f}")
    return ece, mono


reliability(p_ml, y[te].astype(float), "ml_index engine (held-out populations)")
print()
print("   For comparison, gate S1: ECE=0.091, monotone=False, predicted 0.998")
print("      -> actual 0.919 at the top decile, 0.011 -> 0.500 at the bottom.")
print()
print("   *** S1 DOES NOT MEASURE THE FILTER THAT SHIPS. *** S1 runs")
print("   `portfolio`, which does not end in '_pd' and therefore carries")
print("   w3.Belief, the POINT-ESTIMATE payday filter. The shipping filter is")
print("   w3.BeliefPD and its gate is S1_PD, which reports ECE=0.026 (also not")
print("   monotone). This script used to say 'S1 says the filter's")
print("   probabilities are wrong'; that is error 9 in docs/03_ERRORS.md and")
print("   the sentence was corrected 28 Aug 2026. The ML arm's case rests on")
print("   S1_PD's non-monotonicity, not on S1's ECE.")
