#!/usr/bin/env python3
"""W10: the three population parameters that were invented and never sourced.

    py -3.12 agent/tests/test_realism_sweep.py            # exploratory, n=40
    py -3.12 agent/tests/test_realism_sweep.py --confirm  # n=100, final only

WHAT THIS IS NOT. It is not a new mechanism. W2 added insolvency and W7 added
transient holds; both moved V1 out of band, because both changed
`w3.balance_trace` and `at_risk_cycles()` reads the balance trace and nothing
else. This changes the POPULATION instead -- how many mandates a customer
holds, where in the month the salary lands, and what a subscription costs.

THE THREE CONSTANTS AND WHY THEY ARE SUSPECT.

  k = 5              no source anywhere in this repository. At k=5, 48.9% of
                     at-risk cycles have enough raw balance on the due date and
                     are at risk only because a SIBLING mandate drained the
                     account first. Half the world's difficulty at the scoring
                     calibration comes from a constant nobody sourced.
  payday_day0_frac   60% on day 0 and the other 40% UNIFORM over days 1-29,
                     which puts 13% of the population on a payday India's
                     Payment of Wages Act effectively forbids.
  amt_frac = 0.045   the debit scales with income, so a subscription costs a
                     190,000-rupee earner ten times what it costs a
                     19,000-rupee earner and every customer faces the identical
                     debit-to-income ratio.

`pop_spend` STAYS PINNED AT 0.80 AND IS NOT RE-DERIVED. 0.80 is `make_pop`'s
original default from 27 August 2026 and predates `agent/metrics.py` by three
days, so V1 at 0.80 is unfitted. `scripts/solve_operating_point.py` bisects
`pop_spend` against a target of 0.12 -- the midpoint of V1's published band --
so its `realistic = 0.7850` IS fitted to V1 and must never be scored on.
Re-deriving the operating point at the new k would buy back a V1 hit by
converting V1 into a fitted target. Not done.

PRE-REGISTERED 31 August 2026, BEFORE THIS RAN. W10-1 to W10-13,
printed and scored below. The external evidence and the plausible range
declared for every swept value are in the same entry, written before any
result was in view.

W10-2, W10-3, W10-9 and W10-11 all predict these fixes FAIL to move V5 and V7.
They are registered against the hope that realism fixes the scoreboard.

NOT gate-protected. EVERY RUN IS ONE PROCESS (`_parallel.py`).
docs/results.md.
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

from agent.batch import make_pop
# I2-EXEMPT: reads the world's at-risk set straight off the balance trace, policy-free.
from agent.execution.sim_executor import SimExecutor
from agent.tests._parallel import agent_job, run_jobs

CONFIRM = "--confirm" in sys.argv
N = 100 if CONFIRM else 40
K_FIXED, DAYS, PE = 5, 120, 7
SPEND = 0.80                      # PINNED. See the module docstring.
POPS = list(range(700, 720))      # 20 populations; at-risk shrinks with k
K_SEED = 4242
ARMS = (("agent", "degenerate"), ("fixed schedule", "doc_legal"))

# (label, extra make_pop kwargs). The first cell is today's world.
CELLS = [
    ("base k=5",        dict()),
    ("R1 k~2.0",        dict(k_mean=2.0, k_seed=K_SEED)),
    ("R1 k~2.5",        dict(k_mean=2.5, k_seed=K_SEED)),
    ("R1 k~3.0",        dict(k_mean=3.0, k_seed=K_SEED)),
    ("R2 k~3.0+pay",    dict(k_mean=3.0, k_seed=K_SEED,
                             payday_mode="statutory")),
    ("R3 k~3.0+amt",    dict(k_mean=3.0, k_seed=K_SEED,
                             amount_mode="absolute")),
    ("ALL k~3.0",       dict(k_mean=3.0, k_seed=K_SEED,
                             payday_mode="statutory",
                             amount_mode="absolute")),
    ("ALL k~2.0",       dict(k_mean=2.0, k_seed=K_SEED,
                             payday_mode="statutory",
                             amount_mode="absolute")),
]

V1_BAND, V3_BAND = (0.08, 0.15), (0.20, 0.40)
V5_BAND, V7_BAND = (0.70, 0.85), (0.85, 0.95)


def _percell(pop_kw, pop_seed):
    """Per-population `k_seed`. A single shared seed gave all 20 populations
    the IDENTICAL vector of mandate counts, so the k mixture was one draw
    reused twenty times and V1 came out non-monotone in mean k. Same failure
    mode as error 27 and the W7 hold generator: per-unit randomness has to be
    drawn per unit."""
    kw = dict(pop_kw)
    if "k_seed" in kw:
        kw["k_seed"] = kw["k_seed"] + pop_seed
    return kw


def mean_se(xs):
    a = np.asarray(xs, dtype=float)
    return float(a.mean()), float(2 * a.std(ddof=1) / np.sqrt(len(a)))


def salary_concentration(pop_kw) -> float:
    """Share of at-risk cycles held by the BOTTOM SALARY QUARTILE. Policy-free.

    W10-8. Runs no arm: `at_risk_cycles()` answers from the balance trace, so
    this cannot be contaminated by the thing it will be used to explain."""
    low = tot = 0
    for ps in POPS:
        pop = make_pop(N, K_FIXED, ps, spend=SPEND, days=DAYS, **pop_kw)
        sal = np.array([c["salary"] for c in pop])
        cut = np.percentile(sal, 25)
        ex = SimExecutor(pop, 907, PE)
        for uid, _due in ex.at_risk_cycles().items():
            ci = int(uid[0].split("m")[0][1:])
            tot += 1
            low += pop[ci]["salary"] <= cut
    return low / tot if tot else 0.0


def main() -> int:
    jobs = []
    for label, pop_kw in CELLS:
        for ps in POPS:
            for arm, mode in ARMS:
                jobs.append((f"{label}|{ps}|{arm}",
                             (N, K_FIXED, ps, SPEND, DAYS, _percell(pop_kw, ps)), 907,
                             dict(payday_err=PE, pop_spend=SPEND,
                                  bcfg=w3.FITTED_BELIEF, mode=mode),
                             False))
    print("W10 -- POPULATION REALISM. pre-registered 31 Aug 2026.")
    print(f"{len(jobs)} runs: {len(CELLS)} cells x {len(POPS)} populations x "
          f"{len(ARMS)} arms, n={N} {DAYS}d payday_err=+/-{PE} "
          f"pop_spend={SPEND} (PINNED, not re-derived)")
    print(f"  {'exploratory n=40' if not CONFIRM else 'CONFIRMATION n=100'}; "
          f"k mixture seed {K_SEED}")
    res = run_jobs(agent_job, jobs)

    out = {}
    print()
    print("=" * 104)
    print(f"{'cell':>16}{'arm':>16}{'V1 fail':>10}{'recovery':>11}"
          f"{'2 SE':>8}{'<=10d':>9}{'surv':>8}{'cyc_rec':>9}{'at risk':>9}")
    for label, _kw in CELLS:
        for arm, _m in ARMS:
            rows = [res[f"{label}|{ps}|{arm}"] for ps in POPS]
            f, _ = mean_se([r["recovery"]["first_presentation_failure_rate"]
                            for r in rows])
            rec, se = mean_se([r["recovery"]["recovery_rate"] for r in rows])
            early, _ = mean_se([r["recovery"]["early_share"] for r in rows])
            surv = float(np.mean([r["survival"] for r in rows]))
            cyc = float(np.mean([r["cycle_rec"] for r in rows]))
            ar = sum(r["recovery"]["at_risk"] for r in rows)
            out[(label, arm)] = dict(v1=f, rec=rec, se=se, early=early,
                                     surv=surv, cyc=cyc, at_risk=ar)
            print(f"{label:>16}{arm:>16}{f*100:>9.2f}%{rec*100:>10.2f}%"
                  f"{se*100:>+8.2f}{early*100:>8.1f}%{surv*100:>7.1f}%"
                  f"{cyc*100:>8.2f}%{ar:>9}")
        print("-" * 104)

    def A(label):     # the agent arm
        return out[(label, "agent")]

    def F(label):     # the fixed schedule
        return out[(label, "fixed schedule")]

    print()
    print("VALIDATION SCORECARD PER CELL -- published bands, none fitted")
    print("=" * 104)
    print(f"{'cell':>16}{'V1':>18}{'V3':>18}{'V5':>18}{'V7':>18}{'score':>8}")
    scores = {}
    for label, _kw in CELLS:
        vals = [("V1", A(label)["v1"], V1_BAND), ("V3", F(label)["rec"], V3_BAND),
                ("V5", A(label)["rec"], V5_BAND), ("V7", A(label)["early"], V7_BAND)]
        cells = []
        n_hit = 0
        for _t, v, (lo, hi) in vals:
            hit = lo <= v <= hi
            n_hit += hit
            cells.append(f"{v*100:6.2f}% {'HIT ' if hit else 'miss'}")
        scores[label] = n_hit
        print(f"{label:>16}" + "".join(f"{c:>18}" for c in cells) + f"{n_hit:>6}/4")

    print()
    print("PRE-REGISTERED PREDICTIONS (31 Aug 2026, before this ran)")
    print("=" * 104)
    conc_base = salary_concentration(dict())
    conc_r3 = salary_concentration(dict(amount_mode="absolute"))
    preds = [
        ("W10-1", "V3 rises as k falls; at k~3.0 it lands in 28-45%",
         0.28 <= F("R1 k~3.0")["rec"] <= 0.45,
         f"{F('R1 k~3.0')['rec']*100:.2f}% vs {F('base k=5')['rec']*100:.2f}% at k=5"),
        ("W10-2", "V5 stays ABOVE 90% at every k -- k is not a V5 lever",
         all(A(l)["rec"] > 0.90 for l in ("R1 k~2.0", "R1 k~2.5", "R1 k~3.0")),
         "  ".join(f"{l}={A(l)['rec']*100:.2f}%"
                   for l in ("R1 k~2.0", "R1 k~2.5", "R1 k~3.0"))),
        ("W10-3", "V7 moves by LESS than 5 pts across the k range",
         abs(max(A(l)["early"] for l in ("base k=5", "R1 k~2.0", "R1 k~2.5", "R1 k~3.0"))
             - min(A(l)["early"] for l in ("base k=5", "R1 k~2.0", "R1 k~2.5", "R1 k~3.0"))) < 0.05,
         "  ".join(f"{l}={A(l)['early']*100:.1f}%"
                   for l in ("base k=5", "R1 k~2.0", "R1 k~2.5", "R1 k~3.0"))),
        ("W10-4", "fixed-schedule survival rises >=3 pts from k=5 to k~2.0",
         F("R1 k~2.0")["surv"] - F("base k=5")["surv"] >= 0.03,
         f"{F('base k=5')['surv']*100:.1f}% -> {F('R1 k~2.0')['surv']*100:.1f}%"),
        ("W10-5", "R2 (statutory payday) moves all four targets by <3 pts",
         (abs(A("R2 k~3.0+pay")["v1"] - A("R1 k~3.0")["v1"]) < 0.03
          and abs(F("R2 k~3.0+pay")["rec"] - F("R1 k~3.0")["rec"]) < 0.03
          and abs(A("R2 k~3.0+pay")["rec"] - A("R1 k~3.0")["rec"]) < 0.03
          and abs(A("R2 k~3.0+pay")["early"] - A("R1 k~3.0")["early"]) < 0.03),
         f"dV1={((A('R2 k~3.0+pay')['v1']-A('R1 k~3.0')['v1'])*100):+.2f} "
         f"dV3={((F('R2 k~3.0+pay')['rec']-F('R1 k~3.0')['rec'])*100):+.2f} "
         f"dV5={((A('R2 k~3.0+pay')['rec']-A('R1 k~3.0')['rec'])*100):+.2f} "
         f"dV7={((A('R2 k~3.0+pay')['early']-A('R1 k~3.0')['early'])*100):+.2f}"),
        ("W10-7", "R3 (amount decoupled) RAISES V1 by 1-5 pts at k~3.0",
         0.01 <= A("R3 k~3.0+amt")["v1"] - A("R1 k~3.0")["v1"] <= 0.05,
         f"{A('R1 k~3.0')['v1']*100:.2f}% -> {A('R3 k~3.0+amt')['v1']*100:.2f}%"),
        ("W10-8", "R3 puts >40% of at-risk cycles in the bottom salary quartile",
         conc_r3 > 0.40,
         f"{conc_r3*100:.1f}% with R3, {conc_base*100:.1f}% without "
         f"(policy-free, k=5)"),
        ("W10-9", "R3 drops V5 by <8 pts and it stays above 85%",
         (A("R1 k~3.0")["rec"] - A("R3 k~3.0+amt")["rec"] < 0.08
          and A("R3 k~3.0+amt")["rec"] > 0.85),
         f"{A('R1 k~3.0')['rec']*100:.2f}% -> {A('R3 k~3.0+amt')['rec']*100:.2f}%"),
        ("W10-10", "R3 drops V7 by 2-10 pts",
         0.02 <= A("R1 k~3.0")["early"] - A("R3 k~3.0+amt")["early"] <= 0.10,
         f"{A('R1 k~3.0')['early']*100:.1f}% -> {A('R3 k~3.0+amt')['early']*100:.1f}%"),
        ("W10-11", "after ALL three fixes the world still scores at most 2/4",
         max(scores["ALL k~3.0"], scores["ALL k~2.0"]) <= 2,
         f"ALL k~3.0 = {scores['ALL k~3.0']}/4, ALL k~2.0 = {scores['ALL k~2.0']}/4"),
        ("W10-13", "V1 under a mean-2 mixture is above fixed k=2 (7.66%) "
                   "and lands in 8-11%",
         0.08 <= A("R1 k~2.0")["v1"] <= 0.11,
         f"{A('R1 k~2.0')['v1']*100:.2f}% (fixed k=2 measured 7.66% policy-free)"),
    ]
    n_held = 0
    for pid, desc, held, detail in preds:
        n_held += held
        print(f"  {'HELD ' if held else 'BROKE'}  {pid}  {desc}")
        print(f"           {detail}")
    print(f"\n  Pre-registration record: {n_held}/{len(preds)}")
    print()
    print("  W10-6 and W10-12 are not scorable here. W10-6 predicts payday_err=7")
    print("  becomes internally inconsistent with a statutory payday window and")
    print("  needs a payday_wait comparison; W10-12 names the NEXT step and is")
    print("  scored by whether topup_p and terminal declines are what close the")
    print("  residual, which this run cannot answer.")
    print()
    print("  BIAS. Every band above is [REPORTED], vendor-sourced, aggregating")
    print("  non-comparable customer bases, and V5's 70-85% is what the source")
    print("  calls TOP PERFORMERS -- its stated median is 47.6%. The k mixture")
    print("  changes the number of RNG draws per customer, so cells with")
    print("  different k are different worlds and not the same world with a")
    print("  mechanism overlaid; only R2 and R3 hold the draw sequence fixed.")
    return 0 if n_held == len(preds) else 1


if __name__ == "__main__":
    raise SystemExit(main())
