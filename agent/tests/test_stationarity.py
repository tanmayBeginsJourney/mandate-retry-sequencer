#!/usr/bin/env python3
"""W11: does this world have a steady state, and what does it score when it does.

    py -3.12 agent/tests/test_stationarity.py            # exploratory, n=40
    py -3.12 agent/tests/test_stationarity.py --confirm  # n=100

THE DEFECT THIS REPAIRS. `w3.balance_trace` credits one salary per cycle and
spends `pop_spend x salary` per cycle, and NOTHING consumes the surplus. At
`pop_spend=0.80` every customer banks a fifth of a salary every month without
bound: end-of-cycle balance runs 0.02x / 0.23x / 0.43x / 0.63x of a monthly
salary across four cycles, and the at-risk rate collapses 29.80% / 8.62% /
3.05% / 0.75%. So the due-date failure rate is a function of how long the run
is -- 27.67% at 60 days, 13.72% at 120, 4.24% at 360 -- and V1's agreement with
a published 8-15% band is the horizon cutting a decaying transient at the right
place. See docs/errors.md, "The world had no steady state".

THE REPAIR IS TWO PARTS AND THEY ARE NOT SEPARABLE.

  S1  burn-in      simulate whole cycles before day 0 and throw them away, so
                   the measurement window starts from a balance the world made.
                   No free parameter: it is a convergence setting.
  S2  buffer       at each payday, carry-over above `buffer x salary` LEAVES the
                   account -- an RD, an SIP, an auto-sweep FD. A SIP is itself a
                   UPI AutoPay mandate, so the mechanism is in-domain.

Burn-in WITHOUT the buffer just accumulates for longer and makes the world
easier without limit; the buffer WITHOUT burn-in does not bind inside 120 days
at the larger buffer values. W11-4b is the registered check on exactly that.

THE CANONICAL BUFFER WAS COMMITTED BEFORE THIS RAN and is NOT the V1-optimal
cell: lognormal(median 0.25 monthly salaries, sigma 1.0), fixed by the published
"75% of Indians have no emergency fund" figure -- P(buffer < 0.5) = 0.756 at
sigma 1.0. The scalar sweep exists to show the curve, not to pick from.
the development log.

`pop_spend` STAYS AT 0.80 and is now derived rather than inherited:
`pop_spend = 1 - household savings rate`, and RBI's FY25 household saving
including physical assets is ~18-20%. The derivation names no validation target.

PRE-REGISTERED: W11-1 to W11-12 in the development log, with W11-4 VOID
and replaced by W11-4b/W11-4c when S1 changed from an explicit initial draw to
burn-in. W11-5, W11-6 and W11-8 all predict the repair does NOT fix the
scoreboard.

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

from agent.tests._parallel import agent_job, run_jobs

CONFIRM = "--confirm" in sys.argv
N = 100 if CONFIRM else 40
K_FIXED, DAYS, PE = 5, 120, 7
SPEND = 0.80
POPS = list(range(700, 720))
BURN = 12                 # cycles; convergence checked separately (W11-4c)
K_SEED, BUF_SEED = 4242, 9182
ARMS = (("agent", "degenerate"), ("fixed schedule", "doc_legal"))

# The canonical buffer. Median fixed by the 75%-no-emergency-fund figure.
CANON = dict(buffer_median=0.25, buffer_sigma=1.0)

# OPTION (c): no single calibration is declared. The externally derived range
# is pop_spend = 1 - household savings rate, and RBI's FY25 readings give 0.93
# (net financial), 0.88 (gross financial) and 0.80 (including physical assets).
# All three are reported. Which one applies to a TRANSACTIONAL account is
# unresolved and is deliberately NOT settled by which one scores.
SPENDS = (0.80, 0.85, 0.88, 0.90, 0.93)

# (label, pop_spend, extra make_pop kwargs, burn_cycles)
CELLS = [("today", 0.80, dict(), 0),
         ("burn only", 0.80, dict(), BURN)]
CELLS += [(f"canon {s:.2f}", s, dict(CANON), BURN) for s in SPENDS]
CELLS += [("R1R2R3 0.80", 0.80, dict(CANON, k_mean=2.0, k_seed=K_SEED,
                                     payday_mode="statutory",
                                     amount_mode="absolute"), BURN),
          ("R1R2R3 0.88", 0.88, dict(CANON, k_mean=2.0, k_seed=K_SEED,
                                     payday_mode="statutory",
                                     amount_mode="absolute"), BURN)]

V1_BAND, V3_BAND = (0.08, 0.15), (0.20, 0.40)
V5_BAND, V7_BAND = (0.70, 0.85), (0.85, 0.95)


def percell(pop_kw, pop_seed):
    """Per-population seeds for every per-customer draw. A shared seed gives
    every population the identical vector, which is the defect found in W10."""
    kw = dict(pop_kw)
    for key, base in (("k_seed", K_SEED), ("buffer_seed", BUF_SEED)):
        if key == "buffer_seed" and "buffer_median" in kw:
            kw[key] = base + pop_seed
        elif key in kw:
            kw[key] = base + pop_seed
    return kw


def mean_se(xs):
    a = np.asarray(xs, dtype=float)
    return float(a.mean()), float(2 * a.std(ddof=1) / np.sqrt(len(a)))


def main() -> int:
    jobs = []
    for label, spend, pop_kw, burn in CELLS:
        for ps in POPS:
            for arm, mode in ARMS:
                jobs.append((f"{label}|{ps}|{arm}",
                             (N, K_FIXED, ps, spend, DAYS,
                              percell(pop_kw, ps)), 907,
                             dict(payday_err=PE, pop_spend=spend,
                                  bcfg=w3.FITTED_BELIEF, mode=mode,
                                  burn_cycles=burn),
                             False))
    print("W11 -- STATIONARITY. pre-registered 31 Aug 2026.")
    print(f"{len(jobs)} runs: {len(CELLS)} cells x {len(POPS)} populations x "
          f"{len(ARMS)} arms, n={N} {DAYS}d payday_err=+/-{PE} "
          f"pop_spend swept over {SPENDS} burn_cycles={BURN}")
    print("  buffer is in MULTIPLES OF ONE MONTHLY SALARY. CANONICAL is "
          "lognormal(median 0.25, sigma 1.0),")
    print("  committed before this ran and NOT selected as the V1-optimal cell.")
    res = run_jobs(agent_job, jobs)

    out = {}
    print()
    print("=" * 104)
    print(f"{'cell':>16}{'arm':>16}{'V1 fail':>10}{'recovery':>11}"
          f"{'2 SE':>8}{'<=10d':>9}{'surv':>8}{'cyc_rec':>9}{'at risk':>9}")
    for label, _sp, _kw, _b in CELLS:
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

    def A(l):
        return out[(l, "agent")]

    def F(l):
        return out[(l, "fixed schedule")]

    print()
    print("VALIDATION SCORECARD PER CELL")
    print("=" * 104)
    print(f"{'cell':>16}{'V1':>18}{'V3':>18}{'V5':>18}{'V7':>18}{'score':>8}")
    scores = {}
    for label, _sp, _kw, _b in CELLS:
        vals = [(A(label)["v1"], V1_BAND), (F(label)["rec"], V3_BAND),
                (A(label)["rec"], V5_BAND), (A(label)["early"], V7_BAND)]
        n_hit = sum(lo <= v <= hi for v, (lo, hi) in vals)
        scores[label] = n_hit
        print(f"{label:>16}"
              + "".join(f"{v*100:>11.2f}% {'HIT ' if lo <= v <= hi else 'miss':>5}"
                        for v, (lo, hi) in vals)
              + f"{n_hit:>6}/4")

    cans = [f"canon {x:.2f}" for x in SPENDS]
    print()
    print("PRE-REGISTERED PREDICTIONS (31 Aug 2026, before this ran)")
    print("=" * 104)
    print("  W11-5/6/7 were registered against a BUFFER sweep at pop_spend=0.80.")
    print("  That design was replaced by this SPEND sweep at the canonical")
    print("  buffer when option (c) was taken -- the buffer cells carried 27-441")
    print("  at-risk cycles and had no power. The conditioning variable is")
    print("  therefore NOT the one registered. They are scored below on their")
    print("  SUBSTANCE, and that substitution is stated rather than hidden.")
    print()
    preds = [
        ("W11-5", "[substance] V5 stays ABOVE 90% across the swept range",
         all(A(c)["rec"] > 0.90 for c in cans),
         "  ".join(f"{c.split()[1]}={A(c)['rec']*100:.1f}%" for c in cans)),
        ("W11-6", "[substance] V7 moves by less than 5 pts across the range",
         max(A(c)["early"] for c in cans) - min(A(c)["early"] for c in cans) < 0.05,
         "  ".join(f"{c.split()[1]}={A(c)['early']*100:.1f}%" for c in cans)),
        ("W11-7", "[substance] V3 moves by less than 5 pts from today to "
                  "canon 0.88",
         abs(F("canon 0.88")["rec"] - F("today")["rec"]) < 0.05,
         f"{F('today')['rec']*100:.2f}% -> {F('canon 0.88')['rec']*100:.2f}%"),
        ("W11-8", "the world still scores at most 2/4 EVERYWHERE in the "
                  "externally derived range",
         max(scores[c] for c in cans) <= 2,
         "  ".join(f"{c.split()[1]}={scores[c]}/4" for c in cans)),
        ("W11-12", "cycle_rec FALLS from today's 99.60%",
         A("canon 0.88")["cyc"] < 0.9960,
         f"today={A('today')['cyc']*100:.2f}%  "
         f"canon 0.88={A('canon 0.88')['cyc']*100:.2f}%"),
    ]
    n_held = 0
    for pid, desc, held, detail in preds:
        n_held += held
        print(f"  {'HELD ' if held else 'BROKE'}  {pid}  {desc}")
        print(f"           {detail}")
    print()
    print(f"  Pre-registration record for this run: {n_held}/{len(preds)}")
    print("  W11-1/2/3/4b/4c were scored POLICY-FREE and all five HELD.")
    print("  the development log.")
    print()
    print("  W11-1 (horizon independence) and W11-4c (burn-in convergence) are")
    print("  POLICY-FREE and are measured by scripts/check_stationarity.py, not")
    print("  here -- they are properties of the world and running an arm to")
    print("  answer them would be measuring the wrong thing.")
    print("  W11-9/10/11 are the headline and pooling numbers and need the")
    print("  1.05 calibration and the pooling arms. Separate runs.")
    print()
    print("  BIAS. Bands are [REPORTED], vendor-sourced. V5's 70-85% is the")
    print("  source's TOP PERFORMERS; its median is 47.6%. Burn-in changes the")
    print("  number of RNG draws per customer, so a burn-in cell is a different")
    print("  world from a non-burn-in one and not the same world plus a fix.")
    return 0 if n_held == len(preds) else 1


if __name__ == "__main__":
    raise SystemExit(main())
