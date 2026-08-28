"""HOW MANY CUSTOMERS DOES OUTAGE DETECTION NEED? The moat's second dividend.

A single merchant sees its own mandates and nothing else. An aggregator sees
every merchant's. If detecting a rail outage needs more attempts per window
than one merchant ever has, then this capability is structurally unavailable to
a single merchant -- not harder, unavailable. That is a moat argument, and it
is worth a measured crossover rather than an assertion.

PRE-REGISTERED, written before the first run (28 August 2026):

  E-DET-1  FALSE ALARMS. At severity=0 (no outage at all), fewer than 5% of
           runs raise any OUTAGE. Derived, not hoped: the detector evaluates
           about once per dispatch hour, ~60 times over this horizon, at
           alpha=1e-4, so the run-level false-alarm rate should be under ~1%.
           Measuring it matters because the FIRST version of this detector used
           a normal approximation and fired 21-26 times on a horizon with 3
           outages -- a single ordinary technical decline scored z=3.09.

  E-DET-2  TPR rises monotonically with the number of customers, at fixed
           severity. If it does not, the detector is not volume-limited and the
           moat argument is wrong.

  E-DET-3  At n<=10 customers, TPR < 0.5 even at severity 0.40. Too few
           attempts land in a 24h window to distinguish an outage from noise.

  E-DET-4  At n=100, TPR >= 0.8 at severity 0.40.

  E-DET-5  THE MOAT CLAIM. A single merchant's attempt volume stays BELOW
           `min_attempts` (8) per 24h window at every n tested, so a
           single-merchant detector never even evaluates the statistic. Predict
           this holds at all n <= 200.

WHAT WOULD MAKE THIS WRONG. Window placement is worst-case: every outage starts
at hour 8, which is where 99.22% of attempts land. An outage that misses the
dispatch hour is invisible AND harmless, so these detection rates are UPPER
bounds. Said out loud rather than buried -- the same placement choice that
makes the damage measurable also makes detection look as good as it can.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

import numpy as np

import agent  # noqa: F401
import w3

from agent.tests._parallel import agent_job, run_jobs

K, DAYS, SPEND, PE, RUN_SEED = 5, 60, 1.05, 7, 7
N_VALUES = [5, 10, 25, 50, 100, 200]
SEVERITIES = [0.0, 0.15, 0.40]
POPS_SIG = [700, 701, 702, 703, 704, 705, 706, 707]
POPS_NULL = [700, 701, 702, 703, 704, 705, 706, 707]
OUTAGE_DAYS = [20, 40]
DURATION_H = 6
N_MERCHANTS = 60            # w3.make_pop draws merchants from range(60)
MIN_ATTEMPTS = 8            # RailMonitor default


def _windows():
    return [(d * 24 + 8, d * 24 + 8 + DURATION_H) for d in OUTAGE_DAYS]


def _classify(transitions):
    """Split NORMAL->OUTAGE transitions into true and false alarms."""
    wins = _windows()
    tp = fp = 0
    for t, label, *_evidence in transitions:
        if not label.endswith("->OUTAGE"):
            continue
        # An alarm counts as true if it lands inside a window or within the
        # 24h detection window that follows one -- evidence takes time to
        # accumulate and the monitor looks back 24h.
        if any(lo <= t < hi + 24 for lo, hi in wins):
            tp += 1
        else:
            fp += 1
    return tp, fp


def main() -> int:
    jobs = []
    for sev in SEVERITIES:
        pops = POPS_NULL if sev == 0.0 else POPS_SIG
        for n in N_VALUES:
            for s in pops:
                jobs.append((
                    (sev, n, s), (n, K, s, SPEND, DAYS), RUN_SEED,
                    dict(payday_err=PE, pop_spend=SPEND, bcfg=w3.FITTED_BELIEF,
                         mode="degenerate", time_major=True,
                         monitor_enabled=True, pause_on_outage=False,
                         suppress_tech_updates="never",
                         outage_kw=(None if sev == 0.0 else
                                    dict(days=OUTAGE_DAYS,
                                         duration_h=DURATION_H,
                                         severity=sev))),
                    False))
    res = run_jobs(agent_job, jobs)

    print("=" * 96)
    print("OUTAGE DETECTION POWER -- true-positive rate vs population size")
    print(f"k={K}, {DAYS}d, payday_err={PE}, FITTED_BELIEF, {DURATION_H}h "
          f"outages on days {OUTAGE_DAYS}, worst-case placement (hour 8)")
    print("=" * 96)
    print(f"{'severity':>9s} {'n cust':>7s} {'runs':>5s} {'att/day':>8s} "
          f"{'att/24h win':>12s} {'detected':>9s} {'TPR':>6s} "
          f"{'false alarms':>13s}")

    tpr = {}
    fpr_null = None
    for sev in SEVERITIES:
        pops = POPS_NULL if sev == 0.0 else POPS_SIG
        for n in N_VALUES:
            rs = [res[(sev, n, s)] for s in pops]
            att_day = np.mean([r["att_per_cycle"] * r["cycles_due"] / DAYS
                               for r in rs])
            tps = [_classify(r["rail_transitions"])[0] for r in rs]
            fps = [_classify(r["rail_transitions"])[1] for r in rs]
            det = sum(1 for x in tps if x > 0)
            rate = det / len(rs)
            tpr[(sev, n)] = rate
            n_fp_runs = sum(1 for x in fps if x > 0)
            print(f"{sev:9.2f} {n:7d} {len(rs):5d} {att_day:8.1f} "
                  f"{att_day:12.1f} {det:5d}/{len(rs):<3d} {rate:6.2f} "
                  f"{n_fp_runs:6d}/{len(rs):<3d} runs")
        if sev == 0.0:
            fpr_null = sum(1 for n in N_VALUES for s in POPS_NULL
                           if sum(_classify(res[(0.0, n, s)]["rail_transitions"]))
                           > 0) / (len(N_VALUES) * len(POPS_NULL))
        print()

    # ---- what the detector actually saw when it fired
    print("=" * 96)
    print("WHAT FIRED IT -- evidence at each true detection (severity 0.40)")
    print("=" * 96)
    print(f"{'n cust':>7s} {'pop':>5s} {'t':>6s} {'window n':>9s} "
          f"{'tech':>5s} {'P(>=k by chance)':>17s}")
    shown = 0
    for n in N_VALUES:
        for s_ in POPS_SIG:
            for tr in res[(0.40, n, s_)]["rail_transitions"]:
                t, label = tr[0], tr[1]
                if not label.endswith("->OUTAGE") or len(tr) < 5:
                    continue
                if not any(lo <= t < hi + 24 for lo, hi in _windows()):
                    continue
                print(f"{n:7d} {s_:5d} {t:6d} {tr[2]:9d} {tr[3]:5d} "
                      f"{tr[4]:17.2e}")
                shown += 1
                if shown >= 14:
                    break
            if shown >= 14:
                break
        if shown >= 14:
            break
    print()

    # ---- the moat arithmetic
    print("=" * 96)
    print("THE MOAT: what ONE MERCHANT would see")
    print("=" * 96)
    print(f"Mandates are spread over {N_MERCHANTS} merchants, so one merchant "
          f"holds n*k/{N_MERCHANTS} of them.")
    print(f"{'n cust':>7s} {'aggregator att/24h':>19s} "
          f"{'1 merchant att/24h':>19s} {'merchant reaches min_attempts=8?':>34s}")
    merchant_ok = []
    for n in N_VALUES:
        rs = [res[(0.40, n, s)] for s in POPS_SIG]
        att_day = np.mean([r["att_per_cycle"] * r["cycles_due"] / DAYS
                           for r in rs])
        per_merchant = att_day / N_MERCHANTS
        ok = per_merchant >= MIN_ATTEMPTS
        merchant_ok.append(ok)
        print(f"{n:7d} {att_day:19.1f} {per_merchant:19.2f} "
              f"{('YES' if ok else 'no  -- cannot even evaluate'):>34s}")

    print()
    print("=" * 96)
    print("PRE-REGISTERED CHECKS")
    print("=" * 96)
    v = []
    v.append(("E-DET-1 false-alarm rate at severity=0 is under 5% of runs",
              fpr_null is not None and fpr_null < 0.05,
              f"{fpr_null:.1%} of runs raised an alarm with no outage present"))

    for sev in (0.15, 0.40):
        seq = [tpr[(sev, n)] for n in N_VALUES]
        mono = all(seq[i] <= seq[i + 1] + 1e-9 for i in range(len(seq) - 1))
        # An all-zero sequence is monotone and proves NOTHING. Reporting that
        # as HELD is exactly the vacuous-gate shape this repo keeps hitting --
        # it happened on the first run of this very test.
        alive = max(seq) > 0
        v.append((f"E-DET-2 TPR non-decreasing in n at severity {sev}",
                  mono and alive,
                  " -> ".join(f"{x:.2f}" for x in seq)
                  + ("" if alive else "   VACUOUS: detector never fired")))

    small = max(tpr[(0.40, n)] for n in (5, 10))
    big = tpr[(0.40, 200)]
    v.append(("E-DET-3 TPR < 0.5 at n<=10 even at severity 0.40",
              small < 0.5 and big > 0,
              f"max TPR at n in (5,10) = {small:.2f}"
              + ("" if big > 0 else "   VACUOUS: detector never fired anywhere")))
    v.append(("E-DET-4 TPR >= 0.8 at n=100, severity 0.40",
              tpr[(0.40, 100)] >= 0.8, f"{tpr[(0.40, 100)]:.2f}"))
    v.append(("E-DET-5 one merchant never reaches min_attempts at any n",
              not any(merchant_ok),
              "merchant volume stays below 8 attempts per 24h window"))

    hits = 0
    for name, passed, detail in v:
        hits += 1 if passed else 0
        print(f"  {'HELD ' if passed else 'BROKE'}  {name}   [{detail}]")
    print()
    print(f"Pre-registration record for this measurement: {hits}/{len(v)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
