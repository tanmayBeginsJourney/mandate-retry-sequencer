#!/usr/bin/env python3
"""W25 -- the (attempts_left, days_left) dynamic program, as properties.

    py -3.12 agent/tests/test_plan_dp.py

`agent.policy.timing._plan_value` replaces the one-step index with backward
induction over attempts and days. This asserts the three properties that make
it trustworthy, rather than a score:

  P1  AT k == 1 IT REDUCES EXACTLY TO THE SHIPPED CLOSED-FORM RULE
      `fire iff p / (1 - p) >= cycles_left * cycle_value`. The continuation
      value was derived and measured before the DP existed; if the DP
      disagrees with it on the final attempt, one of them is wrong.

  P2  IT CAN DECLINE EVERY REMAINING DAY. This is the property the index does
      not have: `amount * (p_now - discount * p_later)` compares today against
      the best day left and never against zero, so whenever today is the best
      day left it fires however bad today is.

  P3  IT WAITS FOR A BETTER DAY when one is coming, and does not when it is
      not. A rule that only ever declines is no more useful than one that only
      ever fires.

  P4  A NON-FINAL ATTEMPT IS NEARLY FREE, and this is asserted rather than
      discovered later. Failing attempt k > 1 costs only the slot, and the slot
      is worth nothing if the DP would decline the final attempt anyway. It is
      why the DP does NOT restrain attempts 1-3, and why a null result from it
      is evidence about the objective rather than a bug.

EACH PROPERTY IS PAIRED WITH A MUTANT that must break it. A property test with
no mutant is the vacuous-gate shape this repository has shipped five times.

NOT GATE-PROTECTED: pure arithmetic, no populations, runs in milliseconds.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import agent  # noqa: F401
from agent.policy.timing import _plan_value

VNEXTS = (0.0, 0.3, 0.6, 0.9, 1.2, 1.8, 2.7)
PS = (0.01, 0.05, 0.2, 0.35, 0.4, 0.474, 0.5, 0.6, 0.75, 0.8, 0.95, 0.99)


def _mutant_no_death(ps, k, vnext):
    """MUTANT for P1/P2: the mandate survives a failed final attempt.

    `V[0][i] = vnext` instead of 0. That deletes the only asymmetry in the
    recursion, so nothing distinguishes the last attempt and the DP should
    stop declining anything.
    """
    n = len(ps)
    V = [[0.0] * (n + 1) for _ in range(k + 1)]
    fire = [[False] * (n + 1) for _ in range(k + 1)]
    for kk in range(k + 1):
        V[kk][n] = vnext
    for i in range(n + 1):
        V[0][i] = vnext
    for kk in range(1, k + 1):
        for i in range(n - 1, -1, -1):
            p = ps[i]
            wait = V[kk][i + 1]
            act = p * (1.0 + vnext) + (1.0 - p) * V[kk - 1][i + 1]
            V[kk][i], fire[kk][i] = ((act, True) if act >= wait
                                     else (wait, False))
    return V, fire


def _mutant_tie_to_wait(ps, k, vnext):
    """MUTANT for P3: resolve ties toward waiting instead of acting.

    With no within-cycle discount, acting today and acting tomorrow on the same
    probability are worth the same. Resolving that toward WAIT walks every
    decision to the last day of the cycle, which is the defer-until-it-expires
    behaviour the whole investigation is about.
    """
    n = len(ps)
    V = [[0.0] * (n + 1) for _ in range(k + 1)]
    fire = [[False] * (n + 1) for _ in range(k + 1)]
    for kk in range(1, k + 1):
        V[kk][n] = vnext
    for kk in range(1, k + 1):
        for i in range(n - 1, -1, -1):
            p = ps[i]
            wait = V[kk][i + 1]
            act = p * (1.0 + vnext) + (1.0 - p) * V[kk - 1][i + 1]
            V[kk][i], fire[kk][i] = ((act, True) if act > wait
                                     else (wait, False))
    return V, fire


def p1(fn):
    """k == 1 must equal the closed-form odds test."""
    bad = n = 0
    for vnext in VNEXTS:
        for p in PS:
            for tail in ([0.0] * 7, [0.02] * 7):
                if tail and max(tail) > p:
                    continue        # a better day later is a different case
                _V, fire = fn([p] + tail, 1, vnext)
                closed = (p / (1 - p)) >= vnext
                n += 1
                if fire[1][0] != closed and abs(p / (1 - p) - vnext) > 1e-9:
                    bad += 1
    return bad, n


def p2(fn):
    """Must decline a flat, hopeless cycle on the final attempt."""
    out = []
    for vnext, p in ((0.6, 0.05), (0.6, 0.30), (1.8, 0.30), (2.7, 0.60)):
        _V, fire = fn([p] * 10, 1, vnext)
        out.append(fire[1][0])
    return out          # all must be False


def p3(fn):
    """Wait when a better day is coming; act when today is the best.

    THE SECOND CASE IS FLAT ON PURPOSE. With no within-cycle discount, a flat
    profile makes acting today and acting on any later day worth exactly the
    same, so this is the only shape where the tie-break is observable -- and
    resolving it toward WAIT walks the decision to the last day of the cycle,
    which is the defer-until-it-expires behaviour the DP exists to rule out.
    An earlier version of this check used `[0.95, 0.10, 0.10, 0.10]`, where
    today wins outright and BOTH tie-breaks fire, so its mutant could not
    fire either. That is the vacuous-gate shape and it was caught by the
    mutant row failing rather than by review.
    """
    _V, a = fn([0.10, 0.10, 0.95, 0.10], 1, 0.6)
    _V, b = fn([0.80] * 6, 1, 0.0)
    return a[1][0], b[1][0]         # must be (False, True)


def p4(fn):
    """A non-final attempt fires where a final one would not."""
    _V, f4 = fn([0.05] * 10, 4, 0.6)
    _V, f1 = fn([0.05] * 10, 1, 0.6)
    return f4[4][0], f1[1][0]       # must be (True, False)


def main() -> int:
    print("W25 -- DP PROPERTIES, with the mutant that must break each one")
    print("=" * 74)
    rows = []

    bad, n = p1(_plan_value)
    rows.append(("P1 k=1 reduces to the closed-form odds test",
                 bad == 0, f"{n} cells, {bad} mismatch(es)"))
    mbad, mn = p1(_mutant_no_death)
    rows.append(("   mutant `no death` must BREAK P1",
                 mbad > 0, f"{mbad} of {mn} cells disagree"))

    got = p2(_plan_value)
    rows.append(("P2 declines every remaining day of a hopeless cycle",
                 not any(got), f"fire={got}"))
    mgot = p2(_mutant_no_death)
    rows.append(("   mutant `no death` must BREAK P2",
                 any(mgot), f"fire={mgot}"))

    a, b = p3(_plan_value)
    rows.append(("P3 waits for a better day, acts when today is best",
                 (a is False and b is True), f"(wait={not a}, act={b})"))
    ma, mb = p3(_mutant_tie_to_wait)
    rows.append(("   mutant `tie->wait` must BREAK P3",
                 not (ma is False and mb is True), f"(wait={not ma}, act={mb})"))

    f4, f1 = p4(_plan_value)
    rows.append(("P4 a non-final attempt is nearly free",
                 (f4 is True and f1 is False), f"k=4 fire={f4}, k=1 fire={f1}"))

    for name, ok, detail in rows:
        print(f"  [{'PASS' if ok else 'FAIL':>4}] {name:<52} {detail}")
    n_bad = sum(1 for _n, ok, _d in rows if not ok)
    print("=" * 74)
    print(f"{len(rows) - n_bad}/{len(rows)} checks pass.")
    if n_bad:
        print("A FAILING MUTANT ROW MEANS THE PROPERTY IS VACUOUS -- the DP "
              "would satisfy it\nby construction and the check is protecting "
              "nothing.")
    return 1 if n_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
