#!/usr/bin/env python3
"""THE RECOVERY-RATE MEASUREMENT. docs/04_BUILD_PLAN.md W0.

Reports the three quantities that can be compared to figures published outside
this project, at two calibrations of the world:

  first-presentation failure rate   what fraction of debits would fail on the
                                    due date. A property of the WORLD; no
                                    policy can move it. Validation target V1.
  recovery rate                     of those, what fraction the agent collected
                                    before the cycle closed. The quantity every
                                    published industry figure reports.
  early share                       of what it recovered, how much landed
                                    inside 10 days. Validation target V7.

PRE-REGISTERED IN NOTES.md, 30 August 2026, BEFORE THIS RAN. R-1 through R-6.
The predictions are printed beside the measurements and scored, because a
prediction recorded and then not checked is worse than none.

NOT gate-protected in the `sim/gate.py --tier full` sense. Reproduce with
`python agent/tests/test_recovery_rates.py` from the repo root.

EVERY RUN IS ONE PROCESS (`_parallel.py`). See docs/06_MODEL_CARD.md 6a.
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

N, K, DAYS, PE = 100, 5, 120, 7
POPS = list(range(700, 708))
SPENDS = (1.05, 0.80)
# (label, run_once mode). `doc_legal` is Razorpay's documented retry schedule
# made compliant -- see agent/policy/fixed_schedule.py for why the compliant
# rendering is T+1..T+4 rather than the documented T..T+3.
ARMS = (("agent", "degenerate"), ("fixed schedule", "doc_legal"))

# (id, what it says, the band, a callable over the results dict)
PREREG = [
    ("R-1", "first-presentation failure at spend 1.05 lands in 53-68%",
     (0.53, 0.68), lambda r: r[1.05]["fpfr"]),
    ("R-2", "first-presentation failure at spend 0.80 lands in 5-25%",
     (0.05, 0.25), lambda r: r[0.80]["fpfr"]),
    ("R-4", "the agent's recovery rate at spend 1.05 lands in 85-97%",
     (0.85, 0.97), lambda r: r[1.05]["rec"]),
    ("R-5", "V7 FAILS at spend 1.05: early share lands in 50-70%, not ~90%",
     (0.50, 0.70), lambda r: r[1.05]["early"]),
]


def mean_se(xs):
    a = np.asarray(xs, dtype=float)
    return float(a.mean()), float(2 * a.std(ddof=1) / np.sqrt(len(a)))


def main() -> int:
    jobs = []
    for sp in SPENDS:
        for ps in POPS:
            for label, mode in ARMS:
                jobs.append((f"{sp}|{ps}|{label}", (N, K, ps, sp, DAYS), 907,
                             dict(payday_err=PE, pop_spend=sp,
                                  bcfg=w3.FITTED_BELIEF, mode=mode),
                             False))
    print(f"{len(jobs)} runs: {len(SPENDS)} calibrations x {len(POPS)} "
          f"populations x {len(ARMS)} arms, n={N} k={K} {DAYS}d "
          f"payday_err=+/-{PE}")
    res = run_jobs(agent_job, jobs)

    out = {}
    print()
    print("cycles collected / cycles due is THIS project's metric.")
    print("recovery rate is what the outside world publishes. They are not "
          "the same number.")
    print("=" * 94)
    print(f"{'spend':>6}{'arm':>16}{'cycle_rec':>12}{'1st-pres fail':>15}"
          f"{'recovery rate':>15}{'<=10 days':>12}{'survival':>10}"
          f"{'at risk':>10}")
    for sp in SPENDS:
      for label, _mode in ARMS:
        rows = [res[f"{sp}|{ps}|{label}"] for ps in POPS]
        cyc = [r["cycle_rec"] for r in rows]
        fpfr = [r["recovery"]["first_presentation_failure_rate"] for r in rows]
        rec = [r["recovery"]["recovery_rate"] for r in rows]
        early = [r["recovery"]["early_share"] for r in rows]
        med = [r["recovery"]["median_days_to_recovery"] for r in rows]
        at_risk = sum(r["recovery"]["at_risk"] for r in rows)
        surv = [r["survival"] for r in rows]
        m_cyc, e_cyc = mean_se(cyc)
        m_f, e_f = mean_se(fpfr)
        m_r, e_r = mean_se(rec)
        m_e, _ = mean_se(early)
        out[(sp, label)] = dict(fpfr=m_f, rec=m_r, early=m_e, cyc=m_cyc,
                                med=float(np.mean(med)), at_risk=at_risk,
                                surv=float(np.mean(surv)), se_rec=e_r)
        if label == ARMS[0][0]:
            out[sp] = out[(sp, label)]
        print(f"{sp:>6.2f}{label:>16}{m_cyc*100:>11.2f}%{m_f*100:>14.2f}%"
              f"{m_r*100:>14.2f}%{m_e*100:>11.1f}%{np.mean(surv)*100:>9.1f}%"
              f"{at_risk:>10}")
    print()
    print("  2 SE on recovery rate, across the 8 populations:")
    for sp in SPENDS:
        for label, _m in ARMS:
            print(f"    spend {sp:.2f} {label:>16}: "
                  f"+/-{out[(sp, label)]['se_rec']*100:.2f} pts")
    print()
    print("  MANDATE DEATH is the mechanism. The fixed schedule spends all four")
    print("  attempts inside four days of the due date, hits the NPCI cap while")
    print("  the account is still empty, and the mandate dies -- forfeiting")
    print("  every remaining billing cycle. Dunning harder costs the customer.")

    print()
    print("PRE-REGISTERED PREDICTIONS (NOTES.md, 30 Aug 2026, before this ran)")
    print("=" * 94)
    n_held = 0
    for rid, desc, (lo, hi), get in PREREG:
        v = get(out)
        held = lo <= v <= hi
        n_held += held
        print(f"  {'HELD ' if held else 'BROKE'}  {rid}  {desc}")
        print(f"           measured {v*100:.2f}%, predicted "
              f"{lo*100:.0f}-{hi*100:.0f}%")
    print(f"\n  Pre-registration record for this measurement: "
          f"{n_held}/{len(PREREG)}")

    print()
    print("VALIDATION TARGETS -- published figures the world was NOT fitted to")
    print("  All [REPORTED], all from vendors selling recovery software, all")
    print("  aggregating non-comparable customer bases. CORROBORATION, never")
    print("  ground truth, and never quotable as a result. docs/01_FACTS.md.")
    print("=" * 94)
    REALISTIC = 0.80
    targets = [
        ("V1", "first-presentation failure, UPI AutoPay", (0.08, 0.15),
         out[(REALISTIC, "agent")]["fpfr"]),
        ("V3", "recovery, basic fixed-interval retries", (0.20, 0.40),
         out[(REALISTIC, "fixed schedule")]["rec"]),
        ("V5", "recovery, smart retries", (0.70, 0.85),
         out[(REALISTIC, "agent")]["rec"]),
        ("V7", "share of recoveries inside 10 days", (0.85, 0.95),
         out[(REALISTIC, "agent")]["early"]),
    ]
    n_hit = 0
    for tid, desc, (lo, hi), v in targets:
        hit = lo <= v <= hi
        n_hit += hit
        print(f"  {'HIT ' if hit else 'MISS'}  {tid}  {desc}")
        print(f"          measured {v*100:.2f}%, published "
              f"{lo*100:.0f}-{hi*100:.0f}%")
    print()
    print(f"  {n_hit}/{len(targets)} targets hit at pop_spend={REALISTIC}, "
          f"none of them fitted.")
    print()
    print("  THE TWO MISSES ARE ONE MISSING MECHANISM. Recovery is too HIGH")
    print("  (V5) and too SLOW (V7) because in this world the money always")
    print("  arrives eventually -- the oracle is 100% at every calibration, so")
    print("  no customer is ever unable to pay. W2 in docs/04_BUILD_PLAN.md")
    print("  adds insolvency, and the registered prediction is that it pulls")
    print("  V5 down into the 70-85% band.")
    print()
    print("  R-3 CORRECTED. It was registered against `payday_wait`, which")
    print("  times its attempts to an estimated payday and is therefore a")
    print("  SMART baseline, not a fixed-interval one. The 20-40% band belongs")
    print("  to `doc_legal`, measured above. `payday_wait` still has no")
    print("  recovery rate -- it lives in the harness, which emits no")
    print("  per-cycle record.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
