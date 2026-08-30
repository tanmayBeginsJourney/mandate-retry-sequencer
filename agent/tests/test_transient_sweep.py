#!/usr/bin/env python3
"""W7: transient holds -- the third class of decline this world never had.

Every failure here was either "the money is not there and will not be until
payday" or, since W2, "the money never arrives". Real declines include a large
third class: a lien, a momentary shortfall, a balance topped up the same
evening. **The money is real and it is back within a day or two.**
`harness.P_TECH` is 0.008 and auto-represents, which is not this.

The hypothesis under test, written down on 30 August 2026 before this existed:
ONE missing mechanism explains three of the four validation misses -- V3 too
low, V5 too high, V7 far too slow.

`p_transient` is the per-customer, per-DAY probability that a temporary hold
blocks the whole available balance for `transient_h` hours. It is SWEPT, never
picked: no source gives a rate for how often an Indian savings account carries
a lien, so a chosen value would be an invented constant (CLAUDE.md rule 5).

WHY THE DURATION IS SWEPT TOO, AND IT IS THE INTERESTING HALF. The agent never
presents on the due date -- it needs 24h notice and only becomes actionable on
day T -- so its first attempt is T+1. A 24h hold is invisible to it. A 48h hold
is not: it takes a Z9 at T+1, `observe(amount, False)` censors its posterior
above `amount`, and from there it waits for payday -- while the fixed schedule,
which does not think, knocks again at T+2 and gets paid.

PRE-REGISTERED IN NOTES.md, 30 August 2026, BEFORE THIS RAN. W7-0 to W7-7,
printed and scored below. **W7-7 is the one registered against our own
interest**: it predicts that buying V3 costs V1, the one target this world hit
without being fitted to it.

NOT gate-protected. `python agent/tests/test_transient_sweep.py` from the root.
EVERY RUN IS ONE PROCESS (`_parallel.py`). docs/06_MODEL_CARD.md 6a.
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

from agent.batch import make_pop, unwinnable_cycles
from agent.tests._parallel import agent_job, run_jobs

N, K, DAYS, PE = 100, 5, 120, 7
POPS = list(range(700, 708))
SPEND = 0.80          # the calibration whose failure rate matches the record
# (p_transient, transient_h). The zero cell is the world as it shipped on
# 30 August; the duration is irrelevant there and is not swept twice.
CELLS = [(0.00, 24)] + [(r, h) for r in (0.05, 0.10, 0.20) for h in (24, 48)]
# Both readings of the registered V5 clause need a calibration to be scored at.
# See NOTES.md, 30 August: at 0.00 V5 is out of band already, so "do not move
# it out" is only literally scorable at 0.08.
MISSED = (0.00, 0.08)
ARMS = (("agent", "degenerate"), ("fixed schedule", "doc_legal"))

# Published bands. [REPORTED], vendor-sourced, corroboration not ground truth.
V1_BAND = (0.08, 0.15)
V3_BAND = (0.20, 0.40)
V5_BAND = (0.70, 0.85)
V7_BAND = (0.85, 0.95)

# The oracle is measured on three cells, not fourteen: it is there to confirm
# transients do NOT make cycles uncollectable (the money comes back), so the
# baseline and the two most extreme cells bracket the answer. Every extra cell
# is 8 more full balance-trace builds in the parent process.
ORACLE_CELLS = [(0.00, 24), (0.20, 24), (0.20, 48)]


def mean_se(xs):
    a = np.asarray(xs, dtype=float)
    return float(a.mean()), float(2 * a.std(ddof=1) / np.sqrt(len(a)))


def oracle_ceiling(p_tr: float, tr_h: int) -> float:
    """Share of cycles ANY schedule could still collect. Policy-free."""
    hit = tot = 0
    for ps in POPS:
        pop = make_pop(N, K, ps, spend=SPEND, days=DAYS)
        due = sum(max(0, (DAYS - m["due_day"]) // pop[0]["cycle_days"])
                  for c in pop for m in c["mandates"])
        hit += due - len(unwinnable_cycles(pop, 907, PE, p_transient=p_tr,
                                           transient_h=tr_h))
        tot += due
    return hit / tot


def key(p_tr, tr_h, pm, ps, label):
    return f"{p_tr}|{tr_h}|{pm}|{ps}|{label}"


def main() -> int:
    jobs = []
    for p_tr, tr_h in CELLS:
        for pm in MISSED:
            for ps in POPS:
                for label, mode in ARMS:
                    jobs.append((key(p_tr, tr_h, pm, ps, label),
                                 (N, K, ps, SPEND, DAYS), 907,
                                 dict(payday_err=PE, pop_spend=SPEND,
                                      bcfg=w3.FITTED_BELIEF, mode=mode,
                                      p_missed_credit=pm,
                                      p_transient=p_tr, transient_h=tr_h),
                                 False))
    print(f"{len(jobs)} runs: {len(CELLS)} transient cells x {len(MISSED)} "
          f"insolvency rates x {len(POPS)} populations x {len(ARMS)} arms, "
          f"n={N} k={K} {DAYS}d pop_spend={SPEND} payday_err=+/-{PE}")
    res = run_jobs(agent_job, jobs)

    out = {}
    for p_tr, tr_h in CELLS:
        for pm in MISSED:
            for label, _m in ARMS:
                rows = [res[key(p_tr, tr_h, pm, ps, label)] for ps in POPS]
                g = lambda k: [r["recovery"][k] for r in rows]  # noqa: E731
                m_cyc, _ = mean_se([r["cycle_rec"] for r in rows])
                m_f, e_f = mean_se(g("first_presentation_failure_rate"))
                m_r, e_r = mean_se(g("recovery_rate"))
                m_e, _ = mean_se(g("early_share"))
                m_s, _ = mean_se([r["survival"] for r in rows])
                out[(p_tr, tr_h, pm, label)] = dict(
                    cyc=m_cyc, fpfr=m_f, se_fpfr=e_f, rec=m_r, se_rec=e_r,
                    early=m_e, surv=m_s,
                    at_risk=sum(r["recovery"]["at_risk"] for r in rows),
                    med=float(np.mean(g("median_days_to_recovery"))))

    for pm in MISSED:
        print()
        print(f"p_missed_credit = {pm:.2f}")
        print("=" * 104)
        print(f"{'p_tr':>6}{'hold':>6}{'arm':>16}{'cycle_rec':>11}"
              f"{'1st-pres fail':>15}{'recovery':>11}{'<=10 days':>11}"
              f"{'survival':>10}{'at risk':>10}")
        for p_tr, tr_h in CELLS:
            for label, _m in ARMS:
                d = out[(p_tr, tr_h, pm, label)]
                hold = "-" if p_tr == 0 else f"{tr_h}h"
                print(f"{p_tr:>6.2f}{hold:>6}{label:>16}{d['cyc']*100:>10.2f}%"
                      f"{d['fpfr']*100:>14.2f}%{d['rec']*100:>10.2f}%"
                      f"{d['early']*100:>10.1f}%{d['surv']*100:>9.1f}%"
                      f"{d['at_risk']:>10}")

    print()
    print("ORACLE CEILING -- do transient holds make cycles UNCOLLECTABLE?")
    print("  They should not: the money comes back. If this falls, the")
    print("  mechanism built is not the mechanism specified.")
    print("=" * 104)
    for p_tr, tr_h in ORACLE_CELLS:
        c = oracle_ceiling(p_tr, tr_h)
        hold = "-" if p_tr == 0 else f"{tr_h}h"
        print(f"  p_transient={p_tr:.2f} hold={hold:>4}   "
              f"collectable by SOME schedule: {c*100:.2f}%")

    # ---------------------------------------------------------------- score --
    base = 0.00, 24
    nz = [(r, h) for r, h in CELLS if r > 0]
    A = lambda c, pm=0.00: out[(c[0], c[1], pm, "agent")]        # noqa: E731
    F = lambda c, pm=0.00: out[(c[0], c[1], pm, "fixed schedule")]  # noqa: E731

    v1_min_nz = min(A(c)["fpfr"] for c in nz)
    v3_max = max(F(c)["rec"] for c in nz)
    v3_in_band = [c for c in nz if V3_BAND[0] <= F(c)["rec"] <= V3_BAND[1]]
    v7_max = max(A(c)["early"] for c in nz)
    v5_min = min(A(c)["rec"] for c in nz)
    v5_08 = [A(c, 0.08)["rec"] for c in nz if c[1] == 24]
    gap_base = (A(base)["rec"] - F(base)["rec"]) * 100
    top24, top48 = (0.20, 24), (0.20, 48)
    gap_top = (A(top24)["rec"] - F(top24)["rec"]) * 100

    # W7-6: is the fixed schedule's edge over the agent larger at 48h than 24h?
    dur_pairs = [(r, (F((r, 48))["rec"] - A((r, 48))["rec"])
                  - (F((r, 24))["rec"] - A((r, 24))["rec"]))
                 for r in (0.05, 0.10, 0.20)]

    # W7-7: in the LOWEST cell where V3 reaches its band, has V1 broken?
    reach = sorted([c for c in nz if F(c)["rec"] >= V3_BAND[0]],
                   key=lambda c: (c[0], c[1]))
    w7_7_cell = reach[0] if reach else None
    w7_7_v1 = A(w7_7_cell)["fpfr"] if w7_7_cell else float("nan")

    print()
    print("PRE-REGISTERED (NOTES.md, 30 Aug 2026, before this was built)")
    print("=" * 104)
    # (id, text, band, value shown, scale, unit, held). `held` is passed
    # explicitly rather than derived, because two of these are quantified over
    # a set of cells ("EVERY 24h cell", "at EVERY rate") and a single
    # summary statistic can only ever test one side of a band.
    checks = [
        ("W7-0", "V1 rises at every non-zero rate (the mechanism does anything)",
         (0.1368, 1.0), v1_min_nz, 100.0, "%", v1_min_nz > 0.1368),
        ("W7-1", "V3 rises and at least one cell lands it inside 20-40%",
         (0.20, 0.60), v3_max, 100.0, "%",
         bool(v3_in_band) and 0.20 <= v3_max <= 0.60),
        ("W7-2", "V7, the agent's early share, rises above 60% somewhere",
         (0.60, 1.0), v7_max, 100.0, "%", v7_max >= 0.60),
        ("W7-3", "V5 does not fall below 70% at p_missed=0.00  [reading i]",
         (0.70, 1.0), v5_min, 100.0, "%", v5_min >= 0.70),
        ("W7-4", "V5 stays inside 70-85% at p_missed=0.08, EVERY 24h cell [ii]",
         V5_BAND, min(v5_08) if v5_08 else 0.0, 100.0, "%",
         bool(v5_08) and all(V5_BAND[0] <= v <= V5_BAND[1] for v in v5_08)),
        ("W7-5", "the agent's lead over the fixed schedule SHRINKS",
         (5.0, 35.0), gap_base - gap_top, 1.0, " pts",
         5.0 <= gap_base - gap_top <= 35.0),
        ("W7-6", "the fixed schedule's edge is larger at 48h than at 24h",
         (0.0, 1.0), min(d for _r, d in dur_pairs), 100.0, " pts",
         all(d > 0 for _r, d in dur_pairs)),
        ("W7-7", "V1 BREAKS above 15% in the lowest cell where V3 reaches 20%",
         (0.15, 1.0), w7_7_v1, 100.0, "%", w7_7_cell is not None
         and w7_7_v1 > 0.15),
    ]
    n_held = 0
    for cid, desc, (lo, hi), v, scale, unit, held in checks:
        held = bool(held)
        n_held += held
        print(f"  {'HELD ' if held else 'BROKE'}  {cid}  {desc}")
        print(f"           measured {v*scale:.2f}{unit}, predicted "
              f"{lo*scale:.2f}-{hi*scale:.2f}{unit}")
    print(f"\n  Pre-registration record: {n_held}/{len(checks)}")
    if v5_08:
        print(f"  W7-4 detail -- V5 at p_missed=0.08 across the 24h cells: "
              f"{min(v5_08)*100:.2f}% to {max(v5_08)*100:.2f}%")
    print(f"  W7-7 detail -- lowest cell reaching V3>=20%: {w7_7_cell}")

    print()
    print("  W7-1 detail -- cells where V3 lands inside the published 20-40%:")
    print(f"    {v3_in_band if v3_in_band else 'NONE'}")
    print("  W7-6 detail -- (fixed - agent) recovery gap, 48h minus 24h:")
    for r, d in dur_pairs:
        print(f"    p_transient={r:.2f}: {d*100:+.2f} pts")

    print()
    print("THE FOUR VALIDATION TARGETS, EVERY CELL, ONE CALIBRATION EACH")
    print("  Reading a target from one cell and another from a different cell")
    print("  is the (0.70, 0.08) trap. Each ROW below is one world.")
    print("=" * 104)
    print(f"{'p_tr':>6}{'hold':>6}{'p_missed':>10}"
          f"{'V1 8-15%':>13}{'V3 20-40%':>13}{'V5 70-85%':>13}"
          f"{'V7 85-95%':>13}{'hits':>6}")
    best = None
    for pm in MISSED:
        for p_tr, tr_h in CELLS:
            vals = [(V1_BAND, A((p_tr, tr_h), pm)["fpfr"]),
                    (V3_BAND, F((p_tr, tr_h), pm)["rec"]),
                    (V5_BAND, A((p_tr, tr_h), pm)["rec"]),
                    (V7_BAND, A((p_tr, tr_h), pm)["early"])]
            hits = sum(1 for (lo, hi), v in vals if lo <= v <= hi)
            cells = "".join(f"{v*100:>8.2f}{'  HIT' if lo <= v <= hi else ' MISS'}"
                            for (lo, hi), v in vals)
            hold = "-" if p_tr == 0 else f"{tr_h}h"
            print(f"{p_tr:>6.2f}{hold:>6}{pm:>10.2f}{cells}{hits:>6}")
            if best is None or hits > best[0]:
                best = (hits, p_tr, tr_h, pm)
    print()
    print(f"  Best single world: {best[0]}/4 targets at p_transient={best[1]:.2f}"
          f", hold={best[2]}h, p_missed={best[3]:.2f}")
    print()
    print("  NO TRANSIENT RATE IS ADOPTED ON THE STRENGTH OF THIS RUN, and that")
    print("  was decided before it ran (NOTES.md). `p_transient` ships at 0.0")
    print("  and inert, like `p_missed_credit`. Choosing the rate that puts V3")
    print("  in band would FIT V3, and V3 would stop being the independent")
    print("  corroboration that makes it worth quoting at all. W7's deliverable")
    print("  is a direction and a curve, not a calibration.")

    # The raw table, so a later re-scoring never needs another 224 runs.
    import json
    dump = os.path.join(ROOT, "logs", "w7_transient_sweep.json")
    os.makedirs(os.path.dirname(dump), exist_ok=True)
    with open(dump, "w", encoding="utf-8") as fh:
        json.dump({f"{k[0]}|{k[1]}|{k[2]}|{k[3]}": v for k, v in out.items()},
                  fh, indent=1, sort_keys=True)
    print()
    print(f"  raw table written to {os.path.relpath(dump, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
