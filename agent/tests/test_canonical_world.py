#!/usr/bin/env python3
"""THE CANONICAL WORLD, scored as a REGION across pop_spend [0.80, 0.93].

    py -3.12 agent/tests/test_canonical_world.py            # n=40, whole region
    py -3.12 agent/tests/test_canonical_world.py --confirm  # n=100, key cells

ONE SCORING AXIS. `pop_spend` is the only quantity left free, and it is scored
across its externally derived range rather than pinned. Everything else is fixed
to a named external anchor, chosen before it was measured against any target:

  R1  k ~ 1 + Poisson(1), cap 8    ~95M active AutoPay mandates against a UPI
                                    base of ~500M users puts mandates-per-holder
                                    at 1-3, not the invented 5
  R2  payday in days 0-6           Payment of Wages Act: wages before the 7th
                                    day (<1000 workers) or the 10th
  R3  amount lognormal, salary-    published AutoPay ticket range Rs149-2,499
      independent, median Rs855     with a Rs15,000 regulatory cap. Decoupled
                                    because a subscription costs what it costs
  S1  burn_cycles=12               no anchor needed: a convergence setting,
                                    checked at 12 vs 16 (0.11 / 0.08 pts apart)
  S2  buffer ~ lognormal(median     75% of Indians have no emergency fund ->
      0.25 salaries, sigma 1.0)     P(buffer < 0.5) = 0.745 in the drawn sample
  S3  mandate_outflow=True         pop_spend is 1 minus the household savings
                                    rate, which is TOTAL money leaving the
                                    account; subscriptions are part of it, not
                                    stacked on top
      irregular_frac=0.00          TESTED AND LEFT OFF. See the note below.

`pop_spend` in [0.80, 0.93] is 1 minus RBI's FY25 household saving: ~18-20%
including physical assets gives 0.80, gross financial 11.8% gives 0.88, net
financial 7% gives 0.93. Which reading applies to a TRANSACTIONAL account is
unresolved -- physical-asset saving leaves the account, EPF is deducted before
the credit and never enters it, loan repayment leaves it but is netted against
saving. **No published decomposition separates them, so the range is reported
and no point inside it is declared.** Pinning 0.93 was rejected specifically
because it was identified after watching V1 move.

⚠️ **`irregular_frac` IS TESTED AND IMMATERIAL, NOT UNTRIED.** Multi-credit
income is the only mechanism that lifts V7's ceiling. Swept policy-free over
{0.20, 0.35, 0.60} x n_credits {4, 8, 12}: the ceiling reaches 87.57% at
`(0.60, 8)` and stays at 71.8-81.2% at the centre of the range. It is left at
0.00 for two reasons. **No source gives a payment-frequency mix for UPI AutoPay
HOLDERS** -- the 76%-of-workforce figure is the wrong population, since AutoPay
skews to smartphone and bank-account holders, and the only directional signal
found is that ~8% credit-card penetration makes AutoPay the mass-market rail,
which is a direction and not a value. And **it does not change any conclusion**:
the agent captures 52-64% of its V7 ceiling, so even at 87.57% the measured V7
lands near 46-56%, still far outside the published band. The passing cell was
also the top edge of the declared range at a swept `n_credits` sweet spot, which
is the shape of a curve fit and was refused on the same grounds as the
(0.70, 0.08) and W7 refined-grid traps.

WHAT THIS WORLD FIXED, all measured and all in NOTES.md, 31 August 2026:
error 33 (no steady state -- V1 ran 27.67% at 60d to 4.24% at 360d and its
agreement with the published band was the horizon cutting a transient), error 34
(collected mandates were handed back at every payday, gifting 22.5% of a salary
per cycle at k=5), and error 35 (retracted: "the oracle is 100%, therefore this
is a pure timing problem" -- `unwinnable_cycles` ignores the four-attempt cap by
design, and W2 was built on that inference).

NOT gate-protected. EVERY RUN IS ONE PROCESS (`_parallel.py`).
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

from agent.batch import at_risk_cycles, constrained_oracle, make_pop
from agent.tests._parallel import agent_job, run_jobs

CONFIRM = "--confirm" in sys.argv
N = 100 if CONFIRM else 40
K_FIXED, DAYS, PE = 5, 120, 7
POPS = list(range(700, 720))
BURN = 12
K_SEED, BUF_SEED = 4242, 9182
ARMS = (("agent", "degenerate"), ("fixed schedule", "doc_legal"))

#: THE CANONICAL WORLD. Every entry has a named external anchor; see the
#: module docstring. `pop_spend` is deliberately absent -- it is the axis.
CANONICAL = dict(k_mean=2.0, k_seed=K_SEED, k_max=8,
                 payday_mode="statutory",
                 amount_mode="absolute", amount_median=855.0,
                 buffer_median=0.25, buffer_sigma=1.0, buffer_seed=BUF_SEED,
                 irregular_frac=0.00)
RUN_KW = dict(burn_cycles=BURN, mandate_outflow=True)

#: The externally derived range. 0.80 / 0.88 / 0.93 are the three published RBI
#: readings; 0.85 and 0.90 fill in the shape.
REGION = (0.80, 0.85, 0.88, 0.90, 0.93)
#: --confirm runs the V1-in-band end and one midpoint at n=100.
KEY = (0.88, 0.93)

V1_BAND, V3_BAND = (0.08, 0.15), (0.20, 0.40)
V5_BAND, V7_BAND = (0.70, 0.85), (0.85, 0.95)


def percell(pop_seed):
    kw = dict(CANONICAL)
    kw["k_seed"] = K_SEED + pop_seed
    kw["buffer_seed"] = BUF_SEED + pop_seed
    return kw


def mean_se(xs):
    a = np.asarray(xs, dtype=float)
    return float(a.mean()), float(2 * a.std(ddof=1) / np.sqrt(len(a)))


def ceilings(spend):
    """Policy-free V5 and V7 ceilings from the constrained oracle."""
    ar = reach = early = 0
    for ps in POPS:
        pop = make_pop(N, K_FIXED, ps, spend=spend, days=DAYS, **percell(ps))
        A = at_risk_cycles(pop, 907, PE, **RUN_KW)
        C = constrained_oracle(pop, 907, PE, **RUN_KW)
        ar += len(A)
        reach += len(C)
        early += sum(1 for d in C.values() if d <= 10)
    return (reach / max(1, ar), early / max(1, reach))


def main() -> int:
    spends = KEY if CONFIRM else REGION
    jobs = []
    for sp in spends:
        for ps in POPS:
            for arm, mode in ARMS:
                jobs.append((f"{sp}|{ps}|{arm}",
                             (N, K_FIXED, ps, sp, DAYS, percell(ps)), 907,
                             dict(payday_err=PE, pop_spend=sp,
                                  bcfg=w3.FITTED_BELIEF, mode=mode, **RUN_KW),
                             False))
    print("THE CANONICAL WORLD -- scored as a REGION, not at a point.")
    print(f"{len(jobs)} runs: {len(spends)} spend levels x {len(POPS)} "
          f"populations x {len(ARMS)} arms, n={N} {DAYS}d payday_err=+/-{PE}")
    print(f"  {'CONFIRMATION n=100' if CONFIRM else 'region scan n=40'}; "
          f"burn {BURN}, mandate outflow ON, irregular_frac 0.00 "
          f"(tested, immaterial)")
    print("  pop_spend = 1 - household savings rate. 0.80 total / 0.88 gross "
          "financial / 0.93 net financial.")
    res = run_jobs(agent_job, jobs)

    out = {}
    print()
    print("=" * 108)
    print(f"{'spend':>7}{'arm':>16}{'V1':>9}{'recovery':>11}{'2 SE':>8}"
          f"{'<=10d':>8}{'surv':>8}{'cyc_rec':>9}{'at risk':>9}")
    for sp in spends:
        for arm, _m in ARMS:
            rows = [res[f"{sp}|{ps}|{arm}"] for ps in POPS]
            v1, _ = mean_se([r["recovery"]["first_presentation_failure_rate"]
                             for r in rows])
            rec, se = mean_se([r["recovery"]["recovery_rate"] for r in rows])
            early, _ = mean_se([r["recovery"]["early_share"] for r in rows])
            surv = float(np.mean([r["survival"] for r in rows]))
            cyc = float(np.mean([r["cycle_rec"] for r in rows]))
            ar = sum(r["recovery"]["at_risk"] for r in rows)
            out[(sp, arm)] = dict(v1=v1, rec=rec, se=se, early=early,
                                  surv=surv, cyc=cyc, at_risk=ar)
            print(f"{sp:>7.2f}{arm:>16}{v1*100:>8.2f}%{rec*100:>10.2f}%"
                  f"{se*100:>+8.2f}{early*100:>7.1f}%{surv*100:>7.1f}%"
                  f"{cyc*100:>8.2f}%{ar:>9}")
        print("-" * 108)

    print()
    print("THE REGION. Which targets hold in which sub-range.")
    print("=" * 108)
    print(f"{'spend':>7}{'V1':>16}{'V3':>16}{'V5':>16}{'V7':>16}"
          f"{'V7 ceiling':>13}{'capture':>10}")
    rows_out = []
    for sp in spends:
        a, f = out[(sp, "agent")], out[(sp, "fixed schedule")]
        v5c, v7c = ceilings(sp)
        cap = a["early"] / v7c if v7c else 0.0
        vals = [(a["v1"], V1_BAND), (f["rec"], V3_BAND),
                (a["rec"], V5_BAND), (a["early"], V7_BAND)]
        n_hit = sum(lo <= v <= hi for v, (lo, hi) in vals)
        rows_out.append((sp, n_hit, v5c, v7c, cap))
        print(f"{sp:>7.2f}"
              + "".join(f"{v*100:>9.2f}% {'HIT ' if lo <= v <= hi else 'miss'}"
                        for v, (lo, hi) in vals)
              + f"{v7c*100:>12.1f}%{cap*100:>9.1f}%")

    print()
    print("  V5 CEILING is 100.0% at every cell (constrained oracle, clairvoyant),")
    print("  so V5 measures AGENT BEHAVIOUR and is not world validation.")
    print("  V7 CEILING is the best a LEGAL schedule could reach inside 10 days.")
    print("  CAPTURE is measured V7 as a share of that ceiling -- the honest")
    print("  headline for this rail, because the 85-95% band is card dunning,")
    print("  where the customer fixes the instrument on demand. UPI AutoPay")
    print("  recovery waits on a roughly monthly salary credit. The published")
    print("  band contains no UPI data, which is the point and not a caveat.")
    print()
    best = max(rows_out, key=lambda r: r[1])
    print(f"  Best in region: {best[1]}/4 at pop_spend={best[0]:.2f}.")
    print("  No point in the region is declared as THE calibration.")
    print()
    print("  BIAS. Bands are [REPORTED], vendor-sourced, aggregating")
    print("  non-comparable customer bases. V5's 70-85% is the source's TOP")
    print("  PERFORMERS; its stated median is 47.6%. The world and the agent")
    print("  share an author, so none of this is independent evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
