#!/usr/bin/env python3
"""W1: solve for the spend rate that produces a DECLARED failure rate.

    python scripts/solve_operating_point.py

THE PROBLEM THIS FIXES. Until now the world's first-presentation failure rate
was a SIDE EFFECT. Somebody picked `pop_spend`, ran the world, and read the
failure rate off the other end -- 68.71% at the repository default of 1.05,
against a published UPI AutoPay figure of 8-15%. That is backwards. The failure
rate is the thing the public record actually constrains, and the spend rate is
an unobservable knob.

So this inverts it: name the failure rate, solve for the spend that produces it,
and declare the result as a named operating point that every future run can
refer to by name instead of by a bare float.

WHY THIS IS CHEAP, AND WHY THAT MATTERS. The first-presentation failure rate is
a property of the WORLD and no policy moves it -- `SimExecutor.at_risk_cycles()`
answers it directly from `w3.balance_trace`, which is deterministic in
`(pop, seed)`. So the search below runs no agent, no baseline and no belief
filter: it builds populations, asks the world how many cycles a due-date debit
would not have covered, and bisects. That is seconds per point instead of
minutes, and -- more importantly -- it means the declared operating point
cannot be contaminated by the policy being measured on it.

WHAT IT DOES NOT DO. It does not re-run any headline. Declaring the points is
the deliverable; adopting one as the default is a separate decision with a
re-run attached, and `docs/results.md` W1 says the stressed point stays so
every existing number stays comparable.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import agent  # noqa: F401
import w3

from agent.batch import make_pop
from agent.execution.sim_executor import SimExecutor

N, K, DAYS, PE, SEED = 100, 5, 120, 7, 907
POPS = list(range(700, 708))

#: The two points to solve for. Targets come from `docs/results.md`:
#: published UPI AutoPay debit failure is 8-15% `[REPORTED]`. The stressed
#: point is not a published figure -- it is where this repository has been
#: operating all along, declared so it stops being accidental.
TARGETS = {
    "realistic": 0.12,
    "stressed": 0.50,
}
TOL = 0.002          # half a percentage point of the target, halved again
MAX_ITERS = 24


def failure_rate(spend: float) -> float:
    """Share of mandate-cycles a debit on the DUE DATE would not have covered.

    Policy-free: no agent, no baseline, no belief. Averaged over the same eight
    held-out populations every other measurement here uses.
    """
    at_risk = due = 0
    for ps in POPS:
        pop = make_pop(N, K, ps, spend=spend, days=DAYS)
        ex = SimExecutor(pop, SEED, PE)
        at_risk += len(ex.at_risk_cycles())
        cyc = pop[0]["cycle_days"]
        due += sum(max(0, (DAYS - m["due_day"]) // cyc)
                   for c in pop for m in c["mandates"])
    return at_risk / due


def solve(target: float, lo: float = 0.30, hi: float = 1.60):
    """Bisection. The failure rate is monotone increasing in spend -- more
    spending, emptier account on the due date -- and that is ASSERTED below
    rather than assumed, because a non-monotone objective would make the
    bisection return a confident wrong answer."""
    f_lo, f_hi = failure_rate(lo), failure_rate(hi)
    if not (f_lo < f_hi):
        raise RuntimeError(
            f"failure rate is not increasing in spend: f({lo})={f_lo:.4f} "
            f"vs f({hi})={f_hi:.4f}. Bisection would be meaningless.")
    if not (f_lo <= target <= f_hi):
        raise RuntimeError(
            f"target {target:.3f} is outside the bracket "
            f"[{f_lo:.4f}, {f_hi:.4f}] at spend [{lo}, {hi}].")
    trace = [(lo, f_lo), (hi, f_hi)]
    for _ in range(MAX_ITERS):
        mid = (lo + hi) / 2
        f = failure_rate(mid)
        trace.append((mid, f))
        if abs(f - target) <= TOL:
            return mid, f, trace
        if f < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2, failure_rate((lo + hi) / 2), trace


def main() -> int:
    print("=" * 78)
    print("W1 -- SOLVING FOR A DECLARED OPERATING POINT")
    print("=" * 78)
    print(f"  n={N} k={K} {DAYS}d, {len(POPS)} held-out populations, seed {SEED}.")
    print("  The first-presentation failure rate is a property of the WORLD.")
    print("  No policy is run here, so the declared point cannot be")
    print("  contaminated by the thing it will be used to measure.")
    print()
    print("  Anchors already published in docs/results.md:")
    for s in (0.80, 1.05):
        print(f"    pop_spend={s:.2f}  ->  {failure_rate(s)*100:6.2f}% "
              f"first-presentation failure")
    print()

    out = {}
    for name, target in TARGETS.items():
        spend, got, trace = solve(target)
        out[name] = dict(pop_spend=round(spend, 4),
                         first_presentation_failure=round(got, 6),
                         target=target, iters=len(trace))
        print(f"  {name:>10s}  target {target:5.1%}  ->  "
              f"pop_spend = {spend:.4f}   measured {got:6.2%}   "
              f"({len(trace)} evaluations)")

    path = os.path.join(ROOT, "logs", "w1_operating_points.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(dict(design=dict(n=N, k=K, days=DAYS, payday_err=PE,
                                   seed=SEED, populations=POPS, tol=TOL),
                       points=out), fh, indent=2, sort_keys=True)
    print()
    print(f"  written to {os.path.relpath(path, ROOT)}")
    print()
    print("  WHAT THIS DOES NOT DO. It declares the points; it does not adopt")
    print("  one. Adopting `realistic` as the default re-runs every headline,")
    print("  and docs/results.md keeps `stressed` precisely so the existing")
    print("  numbers stay comparable. The spend sweep in docs/results.md already")
    print("  shows the shape, so nothing is waiting to surprise anyone.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
