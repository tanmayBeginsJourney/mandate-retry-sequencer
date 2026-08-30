#!/usr/bin/env python3
"""W9: what cross-merchant pooling is worth in the AGENT, and what withholding
it costs.

Pooling -- one belief per CUSTOMER, shared by all k mandates -- is this
project's central architectural claim and the reason it argues for running at
an aggregator rather than a merchant. It is also the part of the design with a
live legal question attached: mandates are structurally per-merchant, and
India's DPDP Rules 2025 operationalise consent and purpose limitation.
`docs/01_FACTS.md` has the analysis and marks it `[GUESS]`.

Gate S2a measures pooling in the HARNESS (+9.53 pts, +/-1.81). This measures it
in the AGENT, and it measures the thing a product actually has to decide:
**what does it cost to pool only for the customers who agreed?**

PRE-REGISTERED IN NOTES.md, 30 August 2026, BEFORE THIS RAN. W9-1 to W9-5,
printed and scored below. W9-4 is registered AGAINST the convenient answer: it
predicts consent-gating is NOT free, because "it costs nothing" would be
pleasant and would also mean the moat is not reproducible in the agent.

W9-2 is a construction check, not a finding: consent at 100% must be
bit-identical to `pooling="all"`, and consent at 0% bit-identical to
`pooling="none"`. Two routes to one state that disagree is a defect.

NOT gate-protected. `python agent/tests/test_pooling_consent.py` from the root.
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

from agent.tests._parallel import agent_job, harness_job, run_jobs

N, K, DAYS, PE = 100, 5, 120, 7
POPS = list(range(700, 708))
SEED = 907
#: Both calibrations, reported together. 1.05 is the repository default and the
#: hard world; 0.80 is where the due-date failure rate matches the published
#: record. Every effect in this project is larger at 1.05, so reporting only
#: that one would overstate what pooling is worth.
SPENDS = (1.05, 0.80)
CONSENT = (1.00, 0.75, 0.50, 0.25, 0.00)


def mean_se(xs):
    a = np.asarray(xs, dtype=float)
    return float(a.mean()), float(2 * a.std(ddof=1) / np.sqrt(len(a)))


def paired_gap(rows_a, rows_b):
    """Paired difference in points, with a paired 2 SE. Paired because both
    arms run the SAME populations with the SAME seed, so the population is a
    matched pair and an unpaired SE would throw away that structure."""
    d = np.array([a - b for a, b in zip(rows_a, rows_b)], dtype=float) * 100
    return float(d.mean()), float(2 * d.std(ddof=1) / np.sqrt(len(d)))


def _kw(spend, **extra):
    return dict(payday_err=PE, pop_spend=spend, bcfg=w3.FITTED_BELIEF,
                mode="degenerate", **extra)


def main() -> int:
    jobs = []
    for spend in SPENDS:
        spec = (N, K, None, spend, DAYS)
        for ps in POPS:
            s = (N, K, ps, spend, DAYS)
            jobs.append((f"{spend}|{ps}|all", s, SEED,
                         _kw(spend, pooling="all"), False))
            jobs.append((f"{spend}|{ps}|none", s, SEED,
                         _kw(spend, pooling="none"), False))
            for c in CONSENT:
                jobs.append((f"{spend}|{ps}|c{c:.2f}", s, SEED,
                             _kw(spend, pooling="consented", consent_frac=c),
                             False))
        del spec

    # payday_wait is a permanent comparator and comes from the frozen harness,
    # not from the agent. It has no belief and therefore no pooling, so it is
    # the same row in every arm.
    hjobs = [((spend, ps), "payday_wait", (N, K, ps, spend, DAYS), SEED,
              dict(payday_err=PE, pop_spend=spend))
             for spend in SPENDS for ps in POPS]

    print(f"{len(jobs)} runs: {len(SPENDS)} calibrations x {len(POPS)} "
          f"populations x {2 + len(CONSENT)} arms, n={N} k={K} {DAYS}d "
          f"payday_err=+/-{PE}, degenerate mode, FITTED_BELIEF")
    print("Degenerate mode on purpose: this measures the BELIEF architecture,")
    print("not the action space. See the pre-registration's bias note.")
    res = run_jobs(agent_job, jobs)
    hres = run_jobs(harness_job, hjobs)

    out = {}
    for spend in SPENDS:
        print()
        print("=" * 96)
        print(f"pop_spend = {spend}")
        print("=" * 96)
        print(f"{'arm':>22}{'cycle_rec':>12}{'2 SE':>9}{'vs pooled':>12}"
              f"{'2 SE':>9}{'survival':>11}")

        def cyc(tag):
            return [res[f"{spend}|{ps}|{tag}"]["cycle_rec"] for ps in POPS]

        pooled = cyc("all")
        rows = [("pooled (all)", "all"), ("not pooled (none)", "none")]
        rows += [(f"consent {c:.0%}", f"c{c:.2f}") for c in CONSENT]
        for label, tag in rows:
            v = cyc(tag)
            m, e = mean_se(v)
            surv, _ = mean_se([res[f"{spend}|{ps}|{tag}"]["survival"]
                               for ps in POPS])
            if tag == "all":
                gapstr = f"{'--':>12}{'':>9}"
            else:
                g, ge = paired_gap(v, pooled)
                gapstr = f"{g:>+11.2f}{ge:>9.2f}"
            print(f"{label:>22}{m*100:>11.2f}%{e*100:>8.2f}{gapstr}"
                  f"{surv*100:>10.1f}%")
            out[(spend, tag)] = v

        # payday_wait, the permanent comparator.
        base = [hres[(spend, ps)]["cycle_rec"] for ps in POPS]
        m, _ = mean_se(base)
        print(f"{'payday_wait (rival)':>22}{m*100:>11.2f}%")
        out[(spend, "baseline")] = base

    # ---------------------------------------------------------------- score
    HARD = SPENDS[0]
    pooled = out[(HARD, "all")]
    none_ = out[(HARD, "none")]
    gap_full, _ = paired_gap(pooled, none_)
    gap_half, _ = paired_gap(pooled, out[(HARD, "c0.50")])

    ident_hi = np.array(out[(HARD, "c1.00")]) == np.array(pooled)
    ident_lo = np.array(out[(HARD, "c0.00")]) == np.array(none_)

    means = [float(np.mean(out[(HARD, f"c{c:.2f}")])) for c in CONSENT]
    # CONSENT runs high -> low, so collection should be non-increasing.
    worst_rise = max((means[i + 1] - means[i]) * 100
                     for i in range(len(means) - 1))
    se_band = mean_se(out[(HARD, "c0.50")])[1] * 100

    base = out.get((HARD, "baseline"))
    lead_none = (float(np.mean(none_)) - float(np.mean(base))) * 100 if base \
        else float("nan")

    print()
    print("PRE-REGISTERED (NOTES.md, 30 Aug 2026, before this ran)")
    print("=" * 96)
    checks = [
        ("W9-1", f"not pooling costs 4-14 pts at pop_spend={HARD}",
         4.0 <= gap_full <= 14.0, f"{gap_full:+.2f} pts"),
        ("W9-2", "consent 100% == pooled, and consent 0% == not pooled, exactly",
         bool(ident_hi.all() and ident_lo.all()),
         f"{int(ident_hi.sum())}/{len(POPS)} and {int(ident_lo.sum())}/{len(POPS)} identical"),
        ("W9-3", "the loss grows monotonically as consent falls",
         worst_rise <= se_band,
         f"largest rise between adjacent cells {worst_rise:+.2f} pts, "
         f"2 SE {se_band:.2f}"),
        ("W9-4", "consent-gating is NOT free: 3-7 pts at 50% consent",
         3.0 <= gap_half <= 7.0, f"{gap_half:+.2f} pts"),
        ("W9-5", "the non-pooled agent still beats payday_wait by > 20 pts",
         lead_none > 20.0, f"{lead_none:+.2f} pts"),
    ]
    n_held = 0
    for cid, desc, held, detail in checks:
        n_held += bool(held)
        print(f"  {'HELD ' if held else 'BROKE'}  {cid}  {desc}")
        print(f"           measured {detail}")
    print(f"\n  Pre-registration record: {n_held}/{len(checks)}")

    print()
    print("HOW THIS COULD BE WRONG")
    print("=" * 96)
    print("  * Degenerate mode isolates the belief architecture and is also the")
    print("    flattering choice for W9-1: the action space cannot compensate")
    print("    for a weaker belief here because it is switched off.")
    print("  * pop_spend=1.05 is the hard world and every effect in this")
    print("    project is larger there. Both calibrations are printed above so")
    print("    the smaller number is not hidden.")
    print("  * 8 populations, one seed each. Not a large study.")
    print("  * The consenting set is drawn from its OWN generator, seeded off")
    print("    the run seed and never touching the money path -- error 27's")
    print("    rule. If it shared the stream, each consent rate would be a")
    print("    different world PLUS consent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
