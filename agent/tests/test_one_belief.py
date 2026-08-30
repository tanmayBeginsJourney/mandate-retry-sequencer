"""ONE BELIEF PER CUSTOMER. The moat, asserted.

This is the cheapest test in the repo and it guards the most expensive mistake
available. Build one `BeliefPD` per MANDATE instead of per CUSTOMER and you
have silently built `solo_pop_pd` -- the arm the cross-merchant moat is
measured AGAINST. It costs 9.53 points (gate S2a) and NOTHING else would tell
you: both policies exist, both run clean, and the suite is green either way.

The named mutant: give `BeliefBook.add_customer` a per-mandate belief dict and
drive each mandate's own object. The `moat` check below goes red, and so does
the parity gate -- degenerate mode stops matching `solo_shared_pd` and starts
matching `solo_pop_pd`.

It also checks the double-advance guard. `w3.BeliefPD.advance()` has no guard
of its own (`w3.py:400-409`); calling it twice for one day ages the belief 2x
and destroys it silently. "Silently" is the word that makes it expensive.
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
import harness
import w3

from agent.policy.belief_book import BeliefBook, DoubleAdvance

RESULTS = []


def ok(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def main() -> int:
    book = BeliefBook(30, 120, 1.05, w3.FITTED_BELIEF)
    book.add_customer(0, 20000.0, 5, n_mandates=5)
    book.add_customer(1, 30000.0, 12, n_mandates=5)

    b0 = book.belief_for(0)
    ok("FITTED_BELIEF applied without raising", b0 is not None)
    ok("spend_beta popped, not passed to BeliefPD",
       book.spend_beta == w3.FITTED_BELIEF["spend_beta"],
       f"{book.spend_beta}")
    ok("stride=1 -> 30 payday hypotheses, not 10", len(b0.hyp) == 30,
       f"{len(b0.hyp)} hypotheses")

    # THE MOAT: k mandates, ONE object.
    refs = [book.belief_for(0) for _ in range(5)]
    ok("moat: all 5 mandates share ONE belief object",
       len({id(r) for r in refs}) == 1, f"{len({id(r) for r in refs})} objects")
    ok("moat: different customers do NOT share",
       id(book.belief_for(0)) != id(book.belief_for(1)))

    # An observation on ANY mandate must move the shared object.
    #
    # The amount has to sit INSIDE the belief's current support to carry any
    # information. A fresh BeliefPD puts all its mass in the lowest couple of
    # bins (`w3.py:371` seeds p0 at 8% of est_salary), so "failed at Rs 5000"
    # is a measurement the filter already agreed with -- balance < 5000 was
    # certain -- and correctly changes nothing. That is the censored-observation
    # model working, not a dead update. Rs 400 straddles the support.
    before = book.belief_for(0).expected()
    book.record_outcome(0, 400.0, False)
    after = book.belief_for(0).expected()
    ok("moat: one observe moves the object every mandate holds",
       before != after, f"{before} -> {after}")

    # And the converse, which is what makes the check above non-vacuous: an
    # observation the filter already knew the answer to must move nothing.
    book.record_outcome(0, 5_000_000.0, False)
    ok("moat: an uninformative observation correctly changes nothing",
       book.belief_for(0).expected() == after,
       f"{after} -> {book.belief_for(0).expected()}")

    # The double-advance guard.
    book.advance_day(0, 0)
    try:
        book.advance_day(0, 0)
        ok("double advance raises", False, "it did not raise")
    except DoubleAdvance:
        ok("double advance raises", True)
    try:
        book.advance_day(0, -1)
        ok("backwards advance raises", False, "it did not raise")
    except DoubleAdvance:
        ok("backwards advance raises", True)
    book.advance_day(0, 1)
    ok("normal advance still works", True)

    # ---- W9: pooling is a setting, and withholding it must actually withhold
    # These checks exist because "pooling=none" that quietly still pooled would
    # report the consent-gated configuration as costing nothing, which is the
    # convenient answer and would be a lie the rest of the suite cannot see.
    from agent.policy.belief_book import PoolingError

    nb = BeliefBook(30, 120, 1.05, w3.FITTED_BELIEF, pooling="none")
    nb.add_customer(0, 20000.0, 5, n_mandates=3,
                    mandate_uids=["c0m0", "c0m1", "c0m2"])
    ok("W9: pooling='none' builds one belief PER MANDATE",
       nb.n_objects_for(0) == 3, f"{nb.n_objects_for(0)} objects")

    b_a = nb.belief_for(0, "c0m0")
    before_b = nb.belief_for(0, "c0m1").expected()
    nb.record_outcome(0, 400.0, False, "c0m0")
    ok("W9: an observation moves ONLY its own mandate's belief",
       b_a.expected() != before_b
       and nb.belief_for(0, "c0m1").expected() == before_b,
       "this is exactly the information the non-pooled config gives up")

    try:
        nb.belief_for(0)
        ok("W9: a non-pooled book refuses to guess which belief", False,
           "it returned one instead of raising")
    except PoolingError:
        ok("W9: a non-pooled book refuses to guess which belief", True)

    # advance_day is still called ONCE per customer and must age each of the
    # k beliefs exactly once. Ageing them k times is the silent destruction
    # the double-advance guard exists for.
    nb.advance_day(0, 0)
    ok("W9: one advance_day ages every belief the customer owns, once",
       all(nb._last_day[k] == 0 for k in nb._owned[0]))

    # Consent at 100% must BE pooling, and at 0% must BE not-pooling. Two
    # routes to one state that disagree is a defect, not a finding.
    cb_all = BeliefBook(30, 120, 1.05, w3.FITTED_BELIEF, pooling="consented",
                        consent={0})
    cb_all.add_customer(0, 20000.0, 5, n_mandates=3,
                        mandate_uids=["c0m0", "c0m1", "c0m2"])
    cb_none = BeliefBook(30, 120, 1.05, w3.FITTED_BELIEF, pooling="consented",
                         consent=set())
    cb_none.add_customer(0, 20000.0, 5, n_mandates=3,
                         mandate_uids=["c0m0", "c0m1", "c0m2"])
    ok("W9: a consenting customer pools", cb_all.n_objects_for(0) == 1)
    ok("W9: a non-consenting customer does not", cb_none.n_objects_for(0) == 3)

    try:
        BeliefBook(30, 120, 1.05, w3.FITTED_BELIEF, pooling="sometimes")
        ok("W9: an unknown pooling mode raises", False, "it did not raise")
    except ValueError:
        ok("W9: an unknown pooling mode raises", True)

    # est_spend must come from the constant, not be hardcoded to its fitted 0.0
    b2 = BeliefBook(30, 120, 1.05, dict(w3.FITTED_BELIEF, spend_beta=0.5))
    b2.add_customer(0, 20000.0, 5, n_mandates=5)
    ok("spend_beta actually reaches est_spend (a refit would propagate)",
       abs(b2.belief_for(0).est_spend - 1.05 * (1 + 4 * 0.5)) < 1e-9,
       f"{b2.belief_for(0).est_spend}")

    print("=" * 70)
    print("ONE BELIEF PER CUSTOMER -- the moat")
    print("=" * 70)
    fails = 0
    for name, passed, detail in RESULTS:
        fails += 0 if passed else 1
        print(f"  {'PASS' if passed else 'FAIL'}  {name}"
              + (f"   [{detail}]" if not passed else ""))
    print()
    print(f"{len(RESULTS) - fails}/{len(RESULTS)} checks passed")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
