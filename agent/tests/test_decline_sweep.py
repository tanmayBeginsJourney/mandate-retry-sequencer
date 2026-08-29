"""THE DECLINE-MIX SWEEP. What does a richer decline taxonomy cost, and is a
bank-shaped outage really invisible to the monitor?

Pre-registered in `NOTES.md`, 29 August 2026, as E-MIX-1 and E-MIX-2, before
this file existed.

WHY SWEPT AND NOT PICKED. No source found gives AutoPay-specific decline
frequencies -- `agent/eval/golden_cases.yaml`'s research block read NPCI's
published code list directly and it names the codes without ranking them. So
every rate here is `[GUESS]` and is reported as a curve, exactly as `topup_p`,
`nudge_p` and outage `severity` are. Picking one would be rule 5.

THE TWO THINGS BEING MEASURED ARE DIFFERENT IN KIND.

E-MIX-1 is about the POLICY: `w3.index_score` reads a probability and a
discount and has no slot for "this account will never succeed again", so a
frozen account looks to it like an unlucky customer and it spends attempts
against a certainty until the cap kills the mandate. The sweep prices that.

E-MIX-2 is about the DETECTOR: `RailMonitor` pools technical declines across
every customer and therefore across every bank. That pooling is what gives an
aggregator 22.5 attempts per 24h window against a single merchant's 0.38 -- and
it is also what hides a single-bank incident, because at N_BANKS=8 a one-bank
outage lifts the pooled rate by about an eighth of its severity. Locally
overwhelming, statistically invisible.

EVERY RUN IS ONE PROCESS (`agent/tests/_parallel.py`,
`max_tasks_per_child=1`). See `docs/06_MODEL_CARD.md` section 6a -- the machine
fault is contained, not fixed.
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

from agent.ports import BANK_HANDLES, bank_of
from agent.tests._parallel import agent_job, run_jobs

N, K, DAYS, SPEND, PE, RUN_SEED = 100, 5, 120, 1.05, 7, 7
POPS = [700, 701, 702, 703, 704, 705, 706, 707]

#: [GUESS], every one. Swept, never picked.
SHUT_RATES = [0.0, 0.01, 0.03, 0.06]
BROKEN_RATES = [0.0, 0.02, 0.05]
LIMIT_RATES = [0.0, 0.05, 0.15]
AMBIG_RATES = [0.0, 0.15, 0.40]

OUTAGE_DAYS = [20, 50, 80, 110]
DURATION_H, SEVERITY = 6, 0.80
N_BANK = 200            # the bank study needs volume; detection is volume-bound


def _base(**kw):
    return dict(payday_err=PE, pop_spend=SPEND, bcfg=w3.FITTED_BELIEF,
                mode="degenerate", time_major=True, **kw)


def build_jobs():
    jobs = []
    # ---- one axis at a time, so a curve is a curve and not a surface
    for axis, rates in (("shut", SHUT_RATES), ("broken", BROKEN_RATES),
                        ("limit", LIMIT_RATES), ("ambig", AMBIG_RATES)):
        for r in rates:
            kw = {"shut": dict(p_account_shut=r),
                  "broken": dict(p_mandate_broken=r),
                  "limit": dict(p_limit=r),
                  "ambig": dict(p_ambiguous=r)}[axis]
            for s in POPS:
                jobs.append((("mix", axis, r, s), (N, K, s, SPEND, DAYS),
                             RUN_SEED, _base(decline_kw=kw), False))
    # ---- the bank study
    ok = dict(days=OUTAGE_DAYS, duration_h=DURATION_H, severity=SEVERITY)
    for s in POPS:
        jobs.append((("bank", "none", s), (N_BANK, K, s, SPEND, DAYS), RUN_SEED,
                     _base(monitor_enabled=True, pause_on_outage=False,
                           outage_kw=dict(ok)), False))
        for h in BANK_HANDLES:
            jobs.append((("bank", h, s), (N_BANK, K, s, SPEND, DAYS), RUN_SEED,
                         _base(monitor_enabled=True, pause_on_outage=False,
                               outage_kw=dict(ok, banks=[h])), False))
    return jobs


def _windows():
    return [(d * 24 + 8, d * 24 + 8 + DURATION_H) for d in OUTAGE_DAYS]


def _detected(transitions):
    """How many of the four windows were flagged, with the same 24h grace the
    detection-power study uses."""
    wins = _windows()
    n = 0
    for lo, hi in wins:
        if any(lo <= t < hi + 24 and lbl.endswith("->OUTAGE")
               for t, lbl, *_ in transitions):
            n += 1
    return n


def main() -> int:
    jobs = build_jobs()
    print(f"{len(jobs)} runs, one process each")
    res = run_jobs(agent_job, jobs)

    def col(key, field):
        return np.array([res[key + (s,)][field] for s in POPS], dtype=float)

    print()
    print("=" * 100)
    print("E-MIX-1 -- what the richer taxonomy costs the frozen policy")
    print(f"n={N}, k={K}, {DAYS}d, payday_err={PE}, FITTED_BELIEF, "
          f"{len(POPS)} populations. Every rate is [GUESS].")
    print("=" * 100)
    print(f"{'axis':>8s} {'rate':>6s} {'cycle_rec':>10s} {'vs rate 0':>10s} "
          f"{'2SE':>6s} {'survival':>9s} {'dead-acct attempts':>19s}")
    curves = {}
    for axis, rates in (("shut", SHUT_RATES), ("broken", BROKEN_RATES),
                        ("limit", LIMIT_RATES), ("ambig", AMBIG_RATES)):
        base = col(("mix", axis, rates[0]), "cycle_rec")
        seq = []
        for r in rates:
            cr = col(("mix", axis, r), "cycle_rec")
            d = cr - base
            m = d.mean() * 100
            se = (2 * d.std(ddof=1) / np.sqrt(len(d)) * 100) if d.std() > 0 else 0.0
            seq.append(m)
            print(f"{axis:>8s} {r:6.2f} {cr.mean()*100:10.2f} {m:+10.3f} "
                  f"{se:6.3f} {col(('mix', axis, r), 'survival').mean()*100:8.2f}% "
                  f"{col(('mix', axis, r), 'exec_terminal_attempts').sum():19.0f}")
        curves[axis] = seq
        print()

    print("=" * 100)
    print("E-MIX-2 -- is a bank-shaped outage invisible to a monitor that "
          "pools banks?")
    print(f"n={N_BANK}, severity {SEVERITY}, four {DURATION_H}h windows, "
          f"{len(POPS)} populations. Detection = windows flagged of 4.")
    print("=" * 100)
    share = {h: sum(1 for ci in range(N_BANK) if bank_of(ci) == h)
             for h in BANK_HANDLES}
    allb = np.array([_detected(res[("bank", "none", s)]["rail_transitions"])
                     for s in POPS], dtype=float)
    print(f"{'scope':>14s} {'customers':>10s} {'windows flagged /4':>20s} "
          f"{'detection rate':>15s}")
    print(f"{'every bank':>14s} {N_BANK:10d} {allb.mean():20.2f} "
          f"{allb.mean()/4:15.2f}")
    per = {}
    for h in BANK_HANDLES:
        v = np.array([_detected(res[("bank", h, s)]["rail_transitions"])
                      for s in POPS], dtype=float)
        per[h] = v
        print(f"{h:>14s} {share[h]:10d} {v.mean():20.2f} {v.mean()/4:15.2f}")
    worst_single = max(v.mean() for v in per.values())
    mean_single = float(np.mean([v.mean() for v in per.values()]))
    print(f"{'best single':>14s} {'':10s} {worst_single:20.2f} "
          f"{worst_single/4:15.2f}")

    print()
    print("=" * 100)
    print("PRE-REGISTERED CHECKS (NOTES.md, 29 Aug 2026, before this file)")
    print("=" * 100)
    v = []
    shut = curves["shut"]
    mono = all(shut[i] >= shut[i + 1] - 1e-9 for i in range(len(shut) - 1))
    v.append(("E-MIX-1 cycle_rec falls monotonically in p_account_shut, and "
              "loses >2 pts at the top",
              mono and shut[-1] < -2.0,
              " -> ".join(f"{x:+.2f}" for x in shut)
              + f"   monotone={mono}"))
    v.append(("E-MIX-2 a single-bank outage is detected strictly less often, "
              "and below 0.5 while all-bank is >=0.5",
              worst_single < allb.mean() and worst_single / 4 < 0.5
              and allb.mean() / 4 >= 0.5,
              f"all-bank {allb.mean()/4:.2f}, best single bank "
              f"{worst_single/4:.2f}, mean single {mean_single/4:.2f}"))
    hits = 0
    for name, passed, detail in v:
        hits += 1 if passed else 0
        print(f"  {'HELD ' if passed else 'BROKE'}  {name}")
        print(f"           [{detail}]")
    print()
    print(f"Pre-registration record for this measurement: {hits}/{len(v)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
