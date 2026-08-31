"""THE HEADLINE MEASUREMENT. What is outage awareness worth, across severity?

Four arms, one severity sweep. Degenerate mode throughout, so the timing brain
is held fixed and every difference is the context layer:

  none      monitor off. The agent as it was before this work.
  pause     detect, and stop dispatching into a broken rail.
  suppress  detect, and stop feeding technical declines to the belief filter.
  both      detect, and do both.

WHY SUPPRESSION IS A SEPARATE ARM FROM PAUSING. They fix different damage.
Pausing saves ATTEMPTS -- the NPCI cap is 4 per mandate per cycle and an outage
burns them against failures that have nothing to do with the customer's
balance. Suppression saves the BELIEF: `w3.BeliefPD.observe(amount, success)`
takes no decline code (`w3.py:416`), `harness.py:270-276` passes success=False
for a technical decline, and the update hard-zeroes every balance bin at or
above the amount (`w3.py:432`). So the filter records "this customer had less
than Rs X" because a bank had a bad afternoon. Pooling makes it worse: one
belief object is shared by all k mandates, so one technical decline corrupts
all k at once.

BELIEF CORRUPTION IS MEASURED WITH S1's OWN INSTRUMENT. `sim.tests.reliability`
is imported directly rather than reimplemented, so the ECE reported here cannot
drift from the ECE gate S1_PD reports. Same binning, same definition.

PRE-REGISTERED, written before the first run (28 August 2026):

  E-OUT-1  At severity 0 (no outage), detection changes NOTHING measurable:
           |cycle_rec difference| < 0.2 pts and not significant. The measured
           false-alarm rate is 0/48 runs, so there should be nothing to react
           to. If this breaks, the detector is inventing outages.

  E-OUT-2  The gain grows with severity. This is the user's stated expectation
           and the one I am most trying to falsify.

  E-OUT-3  Under outage, ECE is WORSE without suppression, and suppression
           improves it. If suppression does not improve calibration, the whole
           "technical declines corrupt the belief" argument is wrong.

           SCORED AGAINST THE `suppress` ARM, NOT `both`. The first version of
           this check compared `both` against `none` and reported HELD -- but
           `both` turns out to be numerically IDENTICAL to `pause` at every
           severity, because a paused dispatch produces no technical decline
           for suppression to act on. So that check was crediting suppression
           with pausing's effect. Comparing the arm that isolates the mechanism
           is the only way the prediction can actually be wrong.

  E-OUT-4  `attempts_wasted_on_tech` falls under `pause` at every severity > 0.

  E-OUT-5  At severity 0.80 the best arm beats `none` by more than 1 point.

WHAT WOULD MAKE ALL OF THIS AN OVERSTATEMENT, said before the numbers:
  * Window placement is worst case. Every outage starts at hour 8, where
    99.22% of attempts land. An outage that misses that hour is harmless AND
    undetectable, so these are UPPER bounds on both damage and benefit.
  * Severity is a pure [GUESS]. No source found reports what fraction of UPI
    AutoPay mandate executions fail during a rail incident.
  * Duration is anchored on [REPORTED] UPI-wide figures (~995 min total across
    ~17 incidents Mar 2020-Mar 2025; longest ~207 min), none of which is about
    AutoPay specifically.
  * The oracle is not a tight bound here: it reads `bal[tt] - drained` with no
    topups (docs/06_MODEL_CARD.md §3 item 11), and it has no notion of the rail
    at all, so no oracle row is quoted in this table.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)
SIM = os.path.join(PKG, "sim")
if SIM not in sys.path:
    sys.path.insert(0, SIM)

import contextlib
import io

import numpy as np

import agent  # noqa: F401
import w3

from agent.tests._parallel import agent_job, run_jobs

POPS = [700, 701, 702, 703, 704, 705, 706, 707]
N, K, DAYS, SPEND, PE, RUN_SEED = 100, 5, 120, 1.05, 7, 7
SEVERITIES = [0.0, 0.15, 0.40, 0.80]
OUTAGE_DAYS = [20, 50, 80, 110]
DURATION_H = 6

ARMS = {
    "none":     dict(monitor_enabled=False, pause_on_outage=False,
                     suppress_tech_updates="never"),
    "pause":    dict(monitor_enabled=True, pause_on_outage=True,
                     suppress_tech_updates="never"),
    "suppress": dict(monitor_enabled=True, pause_on_outage=False,
                     suppress_tech_updates="outage_only"),
    "both":     dict(monitor_enabled=True, pause_on_outage=True,
                     suppress_tech_updates="outage_only"),
}


def ece_of(calib):
    """S1's own instrument. Stdout suppressed -- it prints a reliability table."""
    import tests as sim_tests
    if len(calib) < 50:
        return float("nan"), False
    with contextlib.redirect_stdout(io.StringIO()):
        return sim_tests.reliability(calib, "agent belief under outage")


def main() -> int:
    jobs = []
    for sev in SEVERITIES:
        for arm, kw in ARMS.items():
            for s in POPS:
                jobs.append((
                    (sev, arm, s), (N, K, s, SPEND, DAYS), RUN_SEED,
                    dict(payday_err=PE, pop_spend=SPEND, bcfg=w3.FITTED_BELIEF,
                         mode="degenerate", time_major=True,
                         collect_calib=True,
                         outage_kw=(None if sev == 0.0 else
                                    dict(days=OUTAGE_DAYS,
                                         duration_h=DURATION_H,
                                         severity=sev)),
                         **kw),
                    True))
    res = run_jobs(agent_job, jobs)

    def col(sev, arm, field):
        return np.array([res[(sev, arm, s)][field] for s in POPS], dtype=float)

    print("=" * 104)
    print("OUTAGE ABLATION -- what the context layer is worth, by severity")
    print(f"n={N}, k={K}, 8 populations, {DAYS}d, payday_err={PE}, "
          f"FITTED_BELIEF, {DURATION_H}h outages on days {OUTAGE_DAYS}")
    print("paired 2 SE vs the `none` arm at the same severity. Worst-case "
          "window placement (hour 8).")
    print("=" * 104)
    print(f"{'sev':>5s} {'arm':>9s} {'cycle_rec':>10s} {'vs none':>9s} "
          f"{'2SE':>6s} {'sig':>5s} {'surv':>7s} {'tech att':>9s} "
          f"{'suppr':>6s} {'paused':>7s} {'ECE':>7s} {'mono':>5s} {'auditV':>7s}")

    gains, eces = {}, {}
    for sev in SEVERITIES:
        base = col(sev, "none", "cycle_rec")
        for arm in ARMS:
            cr = col(sev, arm, "cycle_rec")
            d = cr - base
            m = d.mean() * 100
            se = (2 * d.std(ddof=1) / np.sqrt(len(d)) * 100) if d.std() > 0 else 0.0
            gains[(sev, arm)] = m
            calib = [tuple(x) for s in POPS
                     for x in res[(sev, arm, s)].get("calib", [])]
            ece, mono = ece_of(calib)
            eces[(sev, arm)] = ece
            av = sum(res[(sev, arm, s)]["audit_violations"] for s in POPS)
            print(f"{sev:5.2f} {arm:>9s} {cr.mean()*100:10.2f} {m:+9.3f} "
                  f"{se:6.3f} "
                  f"{('SIG' if abs(m) > se and se > 0 else 'n.s.'):>5s} "
                  f"{col(sev, arm, 'survival').mean()*100:7.2f} "
                  f"{col(sev, arm, 'attempts_wasted_on_tech').sum():9.0f} "
                  f"{col(sev, arm, 'tech_updates_suppressed').sum():6.0f} "
                  f"{col(sev, arm, 'paused_dispatch').sum():7.0f} "
                  f"{ece:7.4f} {str(mono):>5s} {av:7d}")
        print()

    print("=" * 104)
    print("PRE-REGISTERED CHECKS")
    print("=" * 104)
    v = []
    worst_null = max(abs(gains[(0.0, a)]) for a in ARMS if a != "none")
    v.append(("E-OUT-1 severity 0: detection changes nothing (<0.2 pts)",
              worst_null < 0.2, f"largest |change| = {worst_null:.3f} pts"))

    best = [max(gains[(sev, a)] for a in ARMS if a != "none")
            for sev in SEVERITIES]
    v.append(("E-OUT-2 best-arm gain grows with severity",
              all(best[i] <= best[i + 1] + 1e-9 for i in range(len(best) - 1)),
              " -> ".join(f"{x:+.3f}" for x in best)))

    e_none = eces[(0.80, "none")]
    e_supp = eces[(0.80, "suppress")]
    v.append(("E-OUT-3 suppression ALONE improves ECE at severity 0.80",
              not np.isnan(e_none) and not np.isnan(e_supp) and e_supp < e_none,
              f"none {e_none:.4f} -> suppress {e_supp:.4f}"))

    # Do the two mechanisms compose at all? If `both` is identical to `pause`,
    # suppression contributes nothing on top and the action space has two
    # levers where one does the work.
    same_as_pause = all(
        abs(gains[(sev, "both")] - gains[(sev, "pause")]) < 1e-9
        for sev in SEVERITIES)
    v.append(("E-OUT-6 pause and suppress compose (both != pause)",
              not same_as_pause,
              "`both` is identical to `pause` at every severity"
              if same_as_pause else "they differ"))

    waste_ok = all(col(sev, "pause", "attempts_wasted_on_tech").sum()
                   <= col(sev, "none", "attempts_wasted_on_tech").sum()
                   for sev in SEVERITIES if sev > 0)
    v.append(("E-OUT-4 pausing reduces attempts wasted on technical declines",
              waste_ok, "checked at every severity > 0"))

    v.append(("E-OUT-5 best arm beats `none` by >1 pt at severity 0.80",
              best[-1] > 1.0, f"{best[-1]:+.3f} pts"))

    hits = 0
    for name, passed, detail in v:
        hits += 1 if passed else 0
        print(f"  {'HELD ' if passed else 'BROKE'}  {name}   [{detail}]")
    print()
    print(f"Pre-registration record for this measurement: {hits}/{len(v)}")
    return 0 if hits == len(v) else 1


if __name__ == "__main__":
    raise SystemExit(main())
