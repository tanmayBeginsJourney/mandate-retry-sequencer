"""LAYER B, part 1: the probability engine. ONE BeliefPD per CUSTOMER.

READ THIS BLOCK BEFORE CHANGING ANYTHING IN THIS FILE.

All `k` mandates of one customer SHARE ONE `w3.BeliefPD` OBJECT. Every
mandate's outcome is folded into that one object. `advance()` and `observe()`
are called ONCE PER CUSTOMER, never once per mandate.

That sharing IS the cross-merchant moat. `sim/harness.py:207-215` does exactly
this for `solo_shared_pd`, and building one belief per mandate instead
silently produces `solo_pop_pd` -- the arm the moat is measured AGAINST. The
cost is 9.53 points (gate S2a) and nothing in the suite would report it,
because both policies exist and both run clean.

`w3.BeliefPD.advance()` has NO guard against being called twice for the same
day (`w3.py:400-409`); a double call ages the belief 2x and destroys it
silently. This class adds that guard, because "silently" is the word that
makes it expensive.

CONFIGURING IT. `w3.BeliefPD(..., **w3.FITTED_BELIEF)` RAISES. `spend_beta`
belongs to the harness, not the belief: it derives `est_spend`. It is popped
here and used, rather than hardcoded to its fitted 0.0, so a future refit
propagates instead of silently not applying.
"""
from __future__ import annotations

import agent  # noqa: F401  -- puts sim/ on the path
import w3

from agent.ports import PaydayUncertainty, Rupees, TimingSummary


class DoubleAdvance(RuntimeError):
    """Raised rather than tolerated. See the module docstring."""


class BeliefBook:
    """Owns every customer's belief. The only place a BeliefPD is constructed."""

    def __init__(self, cycle_days: int, days: int, pop_spend: float,
                 bcfg: dict | None = None):
        cfg = dict(bcfg) if bcfg else {}
        # spend_beta is the harness's, not BeliefPD's. Popping it is what stops
        # BeliefPD(**FITTED_BELIEF) from raising.
        self.spend_beta = cfg.pop("spend_beta", 0.045)
        self.cfg = cfg
        self.cycle_days = cycle_days
        self.days = days
        self.pop_spend = pop_spend
        self._b: dict[int, w3.BeliefPD] = {}
        self._last_day: dict[int, int] = {}
        self._n_mandates: dict[int, int] = {}

    def add_customer(self, customer_id: int, est_salary: float,
                     est_payday: int, n_mandates: int) -> None:
        if customer_id in self._b:
            raise RuntimeError(f"customer {customer_id} already has a belief")
        eff_spend = self.pop_spend * (1 + (n_mandates - 1) * self.spend_beta)
        self._b[customer_id] = w3.BeliefPD(
            est_salary, est_payday, self.cycle_days, self.days,
            est_spend=eff_spend, pop_info=True, **self.cfg)
        self._last_day[customer_id] = -1
        self._n_mandates[customer_id] = n_mandates

    # ---- the shared object
    def belief_for(self, customer_id: int) -> w3.BeliefPD:
        return self._b[customer_id]

    def n_distinct_objects(self) -> int:
        """Used by test_one_belief.py. k mandates must map to 1 object."""
        return len({id(b) for b in self._b.values()})

    # ---- driving it. ONCE per customer.
    def advance_day(self, customer_id: int, day: int) -> None:
        last = self._last_day[customer_id]
        if day == last:
            raise DoubleAdvance(
                f"advance_day({customer_id}, {day}) called twice. A BeliefPD "
                f"advanced twice for one day is aged 2x and is silently wrong. "
                f"advance() is per CUSTOMER, not per mandate.")
        if day < last:
            raise DoubleAdvance(f"advance_day went backwards: {last} -> {day}")
        self._b[customer_id].advance(day)
        self._last_day[customer_id] = day

    def record_outcome(self, customer_id: int, amount: Rupees,
                       success: bool) -> None:
        """Fold in one censored measurement. Called ONCE per outcome, for any
        mandate -- every other mandate of this customer already sees it,
        because they are the same object."""
        self._b[customer_id].observe(amount, success)

    # ---- the two summaries. The split is the redaction seam.
    def timing_summary(self, customer_id: int) -> TimingSummary:
        """For the POLICY layer. Carries rupee-denominated state."""
        ent, topw, expb = self._b[customer_id].posterior_summary()
        return TimingSummary(expected_balance=expb, payday_entropy=ent,
                             top_hypothesis_weight=topw)

    def uncertainty(self, customer_id: int) -> PaydayUncertainty:
        """For the LLM layer. Carries NO rupee-denominated state.

        `posterior_summary()` returns expected balance as its third element.
        It is dropped here and never crosses into `agent/llm`. A narrative
        layer cannot disclose a balance it was never handed -- which is the
        governance rule from docs/07_AGENT_BRIEF.md §2 made structural
        instead of editorial."""
        ent, topw, _expected_balance_dropped = \
            self._b[customer_id].posterior_summary()
        return PaydayUncertainty(payday_entropy=ent, top_hypothesis_weight=topw)
