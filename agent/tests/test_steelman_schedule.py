#!/usr/bin/env python3
"""THE AGENT AGAINST THREE FIXED SCHEDULES, across payday estimate error.

    py -3.12 agent/tests/test_steelman_schedule.py

WHY THE COMPARATOR CHANGED. `harness.payday_wait` was described as "what a good
rival team builds in an afternoon" and it is not one. It targets the estimated
payday on its FIRST attempt only; every retry after that is `day + 1`, so after
one miss it degenerates into daily retries, burns the four-attempt NPCI cap in
three days and kills the mandate. Beating it is not evidence of much.

`[1,7]` is the steelman: two attempts, at fixed offsets from the same noisy
payday estimate the agent is given, and nothing else. It holds no belief,
updates nothing and adapts to no outcome. The offsets were selected ONCE, on
train populations 700-709, by mean hit rate across payday_err {1, 3, 7, 14},
then frozen -- which is what a merchant could actually deploy, since nobody
knows their own estimate error in advance. Scored here on held-out 710-719.

All four arms run end to end through the same loop, the same Stage 0 gate and
the same audit trail, so the numbers are comparable rather than indicative.

  agent          the shipping policy in degenerate mode
  [1,7]          the steelman above
  naive          Razorpay's documented schedule, made legal: T+1..T+4
  payday_wait    the harness baseline, kept as a permanent row

THE RESULT THIS IS HERE TO SHOW. The agent's edge is CONDITIONAL on payday
uncertainty. It LOSES to the frozen schedule at +/-1, +/-3 and +/-5, ties at
+/-7, and wins from somewhere between +/-7 and +/-10 upward, by a widening
margin. Real payday uncertainty in India is unmeasured, and the statutory and
payroll evidence points at the low side, which is the losing side.

NOT GATE-PROTECTED. Every run is one process (`_parallel.py`).
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

N, K, DAYS, SPEND, RUN_SEED = 100, 5, 120, 0.93, 907
#: HELD OUT: 700-709 were used to select the offsets.
POPS = list(range(710, 720))
ERRS = (1, 3, 5, 7, 10, 14)
#: (label, run_once kwargs). `payday_wait` is not here -- it runs through the
#: harness and is added separately.
ARMS = (("agent", dict(mode="degenerate")),
        ("[1,7]", dict(mode="payday_offsets")),
        ("naive", dict(mode="doc_legal")))
CAN = dict(k_mean=2.0, k_max=8, payday_mode="statutory",
           amount_mode="absolute", amount_median=855.0,
           buffer_median=0.25, buffer_sigma=1.0, irregular_frac=0.0)
RK = dict(burn_cycles=12, mandate_outflow=True)


def pc(ps):
    kw = dict(CAN)
    kw["k_seed"] = 4242 + ps
    kw["buffer_seed"] = 9182 + ps
    return kw


def ms(xs):
    a = np.asarray(xs, float)
    return a.mean(), 2 * a.std(ddof=1) / np.sqrt(len(a))


def main() -> int:
    ajobs, hjobs = [], []
    for pe in ERRS:
        for ps in POPS:
            spec = (N, K, ps, SPEND, DAYS, pc(ps))
            for lbl, kw in ARMS:
                ajobs.append((f"{pe}|{ps}|{lbl}", spec, RUN_SEED,
                              dict(payday_err=pe, pop_spend=SPEND,
                                   bcfg=w3.FITTED_BELIEF, **kw, **RK), False))
            hjobs.append((f"{pe}|{ps}|payday_wait", "payday_wait", spec,
                          RUN_SEED, dict(payday_err=pe, pop_spend=SPEND, **RK)))
    print("THE AGENT vs THREE FIXED SCHEDULES, canonical world.")
    print(f"{len(ajobs) + len(hjobs)} runs: {len(ERRS)} payday_err levels x "
          f"{len(POPS)} held-out populations x 4 arms, n={N}, {DAYS}d, "
          f"pop_spend={SPEND}, run seed {RUN_SEED}.")
    res = run_jobs(agent_job, ajobs)
    res.update(run_jobs(harness_job, hjobs))

    labels = [l for l, _ in ARMS] + ["payday_wait"]
    print()
    print("=" * 84)
    print("RECOVERY of at-risk cycles. The difference column is against "
          "[1,7], the steelman.")
    print("`payday_wait` runs through the harness, which computes no at-risk "
          "denominator: cycles only.")
    print(f"{'payday_err':>11}{'arm':>14}{'recovery':>11}{'2 SE':>8}"
          f"{'cycles':>9}{'surv':>8}{'vs [1,7]':>11}")
    G = {}
    for pe in ERRS:
        for lbl in labels:
            rows = [res[f"{pe}|{ps}|{lbl}"] for ps in POPS]
            # `harness.run` reports no recovery-of-at-risk rate. It is not a
            # missing field to paper over: the harness does not compute the
            # at-risk denominator, and inventing one here from a different
            # code path is how two arms stop sharing a denominator.
            if "recovery" in rows[0]:
                r, se = ms([x["recovery"]["recovery_rate"] for x in rows])
            else:
                r, se = float("nan"), float("nan")
            c = float(np.mean([x["cycle_rec"] for x in rows]))
            sv = float(np.mean([x["survival"] for x in rows]))
            G[(pe, lbl)] = (r, se, c, sv)
        base = G[(pe, "[1,7]")][0]
        for lbl in labels:
            r, se, c, sv = G[(pe, lbl)]
            if r != r:
                print(f"{pe:>11}{lbl:>14}{'--':>11}{'--':>8}"
                      f"{c*100:>8.2f}%{sv*100:>7.1f}%{'--':>11}")
                continue
            d = "" if lbl == "[1,7]" else f"{(r - base) * 100:>+11.2f}"
            print(f"{pe:>11}{lbl:>14}{r*100:>10.2f}%{se*100:>+8.2f}"
                  f"{c*100:>8.2f}%{sv*100:>7.1f}%{d}")
        print("-" * 84)

    print()
    print("THE CROSSOVER. Where the agent stops losing to the frozen "
          "schedule.")
    print("=" * 84)
    prev = None
    cross = None
    for pe in ERRS:
        d = (G[(pe, "agent")][0] - G[(pe, "[1,7]")][0]) * 100
        if prev is not None and prev < 0 <= d:
            cross = pe
        prev = d
        print(f"  payday_err {pe:>2}: agent - [1,7] = {d:>+7.2f} pts")
    print(f"  Sign change between the levels tested: "
          f"{'at payday_err=' + str(cross) if cross else 'none in range'}")

    print()
    print("AGAINST THE NAIVE SCHEDULE, which is what a merchant runs today.")
    print("=" * 84)
    for pe in ERRS:
        a = (G[(pe, "agent")][0] - G[(pe, "naive")][0]) * 100
        s = (G[(pe, "[1,7]")][0] - G[(pe, "naive")][0]) * 100
        print(f"  payday_err {pe:>2}: agent {a:>+7.2f}   [1,7] {s:>+7.2f}")
    print("  A large margin over `naive` is not evidence for the belief "
          "filter. Two fixed")
    print("  offsets take most of it, and take more of it than the agent "
          "does below +/-7.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
