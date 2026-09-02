#!/usr/bin/env python3
"""B-1: the best NON-ADAPTIVE schedule, and what adaptivity is worth.

    py -3.12 agent/tests/test_nonclairvoyant_oracle.py

WHY THE CLAIRVOYANT ORACLE WAS NOT ENOUGH. `SimExecutor.constrained_oracle`
reads 100% at every cell, because with perfect foresight it needs ONE attempt
and the four-attempt cap never binds on it. So it cannot say whether the agent's
~88% recovery is close to what is achievable or far from it, and V5 claims have
been hedged since. docs/errors.md, "An unconstrained oracle used to justify a
modelling decision", 31 August 2026.

WHAT B-1 IS. The best schedule of at most `NPCI_MAX` attempt days, expressed as
offsets from the ESTIMATED payday -- the same noisy `est_payday` the agent is
given, +/-`payday_err` days -- chosen with full hindsight over the whole
population. It never sees a balance. Every attempt obeys the rules a real policy
obeys: no presentation on the due date, legal (non-peak) hours only, and the
cycle closes when the next one opens.

⚠️ **WHAT IT CANNOT ANSWER, AND THIS MATTERS MORE THAN WHAT IT CAN.** It is NOT
the Bayes-optimal policy and it is NOT an upper bound on the agent. A
Bayes-optimal policy is ADAPTIVE: it re-plans after every failure, because a
failed attempt is a censored observation that money was short at that moment.
B-1 is open-loop. **The agent can legitimately beat it, and if it does, the
margin is the measured value of adaptivity** -- which is the interesting
quantity, not a paradox.

So the question B-1 answers is narrow and worth having:

    does the agent beat the best fixed schedule, and by how much?

If the published 70-85% smart-retry band describes largely non-adaptive retry
systems -- and "smart retries + card updater + email" plausibly does -- then
agent-beats-B-1 is the V5 story, and the margin is what the belief filter buys.

PRE-REGISTERED 1 September 2026, BEFORE THIS WAS BUILT:

    agent >= B-1 + 2 pts   exceeds best non-adaptive; adaptivity worth the margin
    agent within +/-2 pts  matches non-adaptive; no measurable adaptive gain
    agent <= B-1 - 5 pts   DEFECT: the agent loses to a fixed schedule

B-2, a DP over discretised belief states, is deliberately NOT pre-committed.

NOT gate-protected. Policy-free apart from the agent numbers it is compared
against, which come from `test_canonical_world.py --confirm`.
"""
from __future__ import annotations

import itertools
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np

import agent  # noqa: F401
import w3

from agent.batch import at_risk_cycles, make_pop
# I2-EXEMPT: reads SimExecutor.estimates to build the non-clairvoyant oracle's offsets; there is no world-opinion route to a per-customer estimate.
from agent.execution.sim_executor import SimExecutor

from agent.tests import _canonical as _CAN

#: THE CANONICAL WORLD, IMPORTED rather than copied.
_C = ["--canonical"]
N, K_FIXED, DAYS, PE = _CAN.N, 5, 120, 7
POPS = list(range(700, 720))
BURN, CYC = _CAN.BURN, 30
SPENDS = (0.88, _CAN.SPEND)
EARLY = 10
RUN_KW = _CAN.run_kwargs(_C)

#: Measured agent recovery / early share, from test_canonical_world.py
#: --confirm at n=100, 20 populations. logs/w13_canonical_n100.txt.
#: HISTORICAL: measured before the canonical n moved to 500. Kept as the
#: reference these bounds were declared against, not as a current figure.
AGENT = {0.88: (0.9171, 0.4043), 0.93: (0.8840, 0.4264)}


def percell(pop_seed):
    return _CAN.pop_kwargs(pop_seed, _C)


def build_matrix(spend):
    """(hit, early) boolean matrices over at-risk cycles x payday offsets.

    `hit[i, o]` is True when an attempt placed at `est_payday + o` days, on the
    unique day that offset picks out inside cycle i's window, would have found
    enough balance at some legal hour. `early[i, o]` also requires that day to
    fall within EARLY days of the due date.

    THE OFFSET IS FROM THE ESTIMATE, NOT THE TRUTH. `SimExecutor.estimates`
    returns the same noisy `est_payday` the agent is given and no accessor
    returns a true payday or balance, so B-1 is built on exactly the signal the
    agent has.
    """
    hits, earlies = [], []
    for ps in POPS:
        pop = make_pop(N, K_FIXED, ps, spend=spend, days=DAYS, **percell(ps))
        ex = SimExecutor(pop, 907, PE, **RUN_KW)
        at_risk = at_risk_cycles(pop, 907, PE, **RUN_KW)
        for (uid, _cycle), due_day in at_risk.items():
            ci = int(uid.split("m")[0][1:])
            mi = int(uid.split("m")[1])
            w = ex.worlds[ci]
            amount = pop[ci]["mandates"][mi]["amount"]
            _sal, est_pay = ex.estimates(ci)
            hi = min(due_day + CYC, DAYS)
            h = np.zeros(CYC, dtype=bool)
            e = np.zeros(CYC, dtype=bool)
            for o in range(CYC):
                target = (est_pay + o) % CYC
                # The unique day in (due_day, hi) whose day-of-cycle matches.
                d = due_day + 1 + ((target - (due_day + 1)) % CYC)
                if d >= hi:
                    continue          # that offset has no legal day this cycle
                ok = any(w.bal[d * w3.HOURS + hh] >= amount
                         for hh in w3.LEGAL_HOURS)
                h[o] = ok
                e[o] = ok and (d - due_day) <= EARLY
            hits.append(h)
            earlies.append(e)
    return np.array(hits), np.array(earlies)


def best_schedule(M, cap):
    """Best set of <= cap offsets, by share of rows with any hit. Exhaustive."""
    MT = np.ascontiguousarray(M.T)
    best, best_combo = -1.0, ()
    for combo in itertools.combinations(range(MT.shape[0]), cap):
        score = np.logical_or.reduce(MT[list(combo)]).mean()
        if score > best:
            best, best_combo = float(score), combo
    return best, best_combo


def main() -> int:
    print("B-1 -- THE BEST NON-ADAPTIVE SCHEDULE. "
          "pre-registered 1 Sep 2026.")
    print(f"  n={N} x {len(POPS)} populations, canonical world, burn {BURN}, "
          f"mandate outflow ON.")
    print(f"  Offsets from the NOISY est_payday (+/-{PE}d), "
          f"<= {w3.NPCI_MAX} attempts, no due-date presentation, legal hours.")
    print("  Chosen with full HINDSIGHT over the population. It is not the")
    print("  Bayes-optimal policy and not an upper bound on the agent: the")
    print("  agent adapts and B-1 does not.")
    print()
    print("=" * 96)
    print(f"{'spend':>7}{'at-risk':>9}{'B-1 V5':>10}{'agent V5':>11}"
          f"{'margin':>9}{'B-1 V7':>10}{'agent V7':>11}{'margin':>9}")
    rows = []
    for sp in SPENDS:
        H, E = build_matrix(sp)
        b5, combo5 = best_schedule(H, w3.NPCI_MAX)
        b7, combo7 = best_schedule(E, w3.NPCI_MAX)
        a5, a7 = AGENT[sp]
        rows.append((sp, H.shape[0], b5, a5, b7, a7, combo5, combo7))
        print(f"{sp:>7.2f}{H.shape[0]:>9}{b5*100:>9.2f}%{a5*100:>10.2f}%"
              f"{(a5-b5)*100:>+9.2f}{b7*100:>9.2f}%{a7*100:>10.2f}%"
              f"{(a7-b7)*100:>+9.2f}")
    print()
    for sp, _n, _b5, _a5, _b7, _a7, c5, c7 in rows:
        print(f"  spend {sp:.2f}: best V5 schedule = est_payday + {list(c5)} days")
        print(f"              best V7 schedule = est_payday + {list(c7)} days")

    print()
    print("PRE-REGISTERED VERDICT (declared before this was built)")
    print("=" * 96)
    for sp, _n, b5, a5, _b7, _a7, _c5, _c7 in rows:
        d = (a5 - b5) * 100
        if d >= 2.0:
            v = (f"EXCEEDS the best non-adaptive schedule. "
                 f"Adaptivity is worth {d:+.2f} pts.")
        elif d > -5.0:
            v = (f"MATCHES non-adaptive ({d:+.2f} pts). "
                 f"No measurable adaptive gain.")
        else:
            v = (f"DEFECT: agent loses to a fixed schedule by {-d:.2f} pts. "
                 f"Investigate, do not report as a result.")
        print(f"  spend {sp:.2f}  V5: {v}")
    print()
    print("  BIAS. B-1 is chosen with hindsight over the SAME populations the")
    print("  agent is scored on, so it is flattered relative to a schedule that")
    print("  had to be picked in advance -- which makes the agent's margin over")
    print("  it a LOWER bound on the value of adaptivity, not an upper one.")
    print("  V7's B-1 optimises earliness directly, so it is the best a fixed")
    print("  schedule can do on that metric and not a by-product of the V5 fit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
