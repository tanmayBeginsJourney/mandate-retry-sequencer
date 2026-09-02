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

---------------------------------------------------------------------------
POOLING IS NOW A SETTING, AND IT CAN BE WITHHELD PER CUSTOMER. W9, added
30 August 2026.
---------------------------------------------------------------------------

Everything above describes `pooling="all"`, which is still the default and is
still what every published number was measured on.

    pooling="all"        every customer's k mandates share one belief
    pooling="none"       every MANDATE gets its own belief -- this IS the
                         non-pooled configuration, `solo_pop_pd` in harness
                         terms, the arm the moat is measured against
    pooling="consented"  shared for customers in `consent`, per-mandate for
                         everyone else

WHY THE SETTING EXISTS, AND WHY IT IS NOT A HEDGE. Sharing one customer's
outcomes across merchants is the moat, and it is also the part of this design
with a real legal question attached: mandates are structurally per-merchant,
and India's DPDP Rules 2025 -- notified 14 November 2025 -- operationalise the
DPDP Act's consent and purpose-limitation provisions. `docs/results.md` has
the analysis and is explicit that it is `[GUESS]`, not settled law.

A system that can ONLY run pooled cannot answer that question; one that treats
pooling as a per-customer permission can ship in either regime and **price the
difference**. The price is measured, not asserted:
`agent/tests/test_pooling_consent.py`.

At `pooling="all"` the mandate key is ignored entirely and the object graph is
exactly what it was before this change, which is why parity with
`harness.run("solo_shared_pd", ...)` is still bit-exact 24/24.
"""
from __future__ import annotations

import agent  # noqa: F401  -- puts sim/ on the path
import w3

from agent.ports import PaydayUncertainty, Rupees, TimingSummary

POOLING_MODES = ("all", "none", "consented")


class DoubleAdvance(RuntimeError):
    """Raised rather than tolerated. See the module docstring."""


class PoolingError(RuntimeError):
    """Raised rather than guessed.

    A non-pooled book asked about a customer without being told WHICH mandate
    cannot answer. Returning an arbitrary one of the k beliefs would be
    silently wrong, and "silently wrong about which belief" is the exact
    failure this class exists to prevent.
    """


class BeliefBook:
    """Owns every customer's belief. The only place a BeliefPD is constructed."""

    def __init__(self, cycle_days: int, days: int, pop_spend: float,
                 bcfg: dict | None = None, pooling: str = "all",
                 consent=None):
        cfg = dict(bcfg) if bcfg else {}
        # spend_beta is the harness's, not BeliefPD's. Popping it is what stops
        # BeliefPD(**FITTED_BELIEF) from raising.
        self.spend_beta = cfg.pop("spend_beta", 0.045)
        self.cfg = cfg
        self.cycle_days = cycle_days
        self.days = days
        self.pop_spend = pop_spend
        if pooling not in POOLING_MODES:
            raise ValueError(
                f"pooling must be one of {POOLING_MODES}, got {pooling!r}")
        self.pooling = pooling
        #: Customer ids permitted to pool. Read only when pooling=="consented".
        #: An empty set there means nobody consented, which is `pooling="none"`
        #: reached by a different route -- and it must measure the same. A gate
        #: asserts that, because two routes to one state that disagree is a
        #: defect waiting to be shipped.
        self.consent = set(consent or ())
        self._b: dict = {}
        self._last_day: dict = {}
        self._n_mandates: dict[int, int] = {}
        #: customer id -> the belief keys it owns. One when pooled, k when not.
        self._owned: dict[int, list] = {}

    # ---- who shares with whom
    def pools(self, customer_id: int) -> bool:
        """Does this customer's evidence cross merchant boundaries?"""
        if self.pooling == "all":
            return True
        if self.pooling == "none":
            return False
        return customer_id in self.consent

    def _key(self, customer_id: int, mandate_uid=None):
        if self.pools(customer_id):
            return ("c", customer_id)
        if mandate_uid is None:
            raise PoolingError(
                f"customer {customer_id} is not pooling, so a belief cannot be "
                f"identified without a mandate. Pass mandate_uid.")
        return ("m", mandate_uid)

    def add_customer(self, customer_id: int, est_salary: float,
                     est_payday: int, n_mandates: int,
                     mandate_uids=None) -> None:
        if customer_id in self._owned:
            raise RuntimeError(f"customer {customer_id} already has a belief")
        eff_spend = self.pop_spend * (1 + (n_mandates - 1) * self.spend_beta)

        def _new():
            return w3.BeliefPD(est_salary, est_payday, self.cycle_days,
                               self.days, est_spend=eff_spend, pop_info=True,
                               **self.cfg)

        if self.pools(customer_id):
            keys = [("c", customer_id)]
        else:
            if not mandate_uids:
                raise PoolingError(
                    f"customer {customer_id} is not pooling, so add_customer "
                    f"needs mandate_uids to build one belief per mandate.")
            keys = [("m", u) for u in mandate_uids]
            if len(keys) != n_mandates:
                raise PoolingError(
                    f"customer {customer_id}: {len(keys)} mandate_uids against "
                    f"n_mandates={n_mandates}. `est_spend` is derived from "
                    f"n_mandates, so a mismatch silently mis-configures every "
                    f"filter this customer owns.")
        for k in keys:
            self._b[k] = _new()
            self._last_day[k] = -1
        self._owned[customer_id] = keys
        self._n_mandates[customer_id] = n_mandates

    # ---- the shared object
    def belief_for(self, customer_id: int, mandate_uid=None) -> w3.BeliefPD:
        return self._b[self._key(customer_id, mandate_uid)]

    def n_distinct_objects(self) -> int:
        """Used by test_one_belief.py. k mandates must map to 1 object."""
        return len({id(b) for b in self._b.values()})

    def n_objects_for(self, customer_id: int) -> int:
        """1 when this customer pools, k when it does not.

        The whole difference between the two configurations, as one integer a
        test can assert on rather than infer from a score.
        """
        return len({id(self._b[k]) for k in self._owned[customer_id]})

    # ---- driving it. ONCE per customer.
    def advance_day(self, customer_id: int, day: int) -> None:
        """Advance every belief this customer owns, exactly once.

        Still called once per CUSTOMER per day by the loop, in both modes. A
        non-pooled customer owns k objects and each is advanced once here, so
        the caller never has to know which mode it is in. That is what stops a
        non-pooled run from ageing its beliefs k times -- the same silent
        destruction the double-advance guard exists for.
        """
        for k in self._owned[customer_id]:
            last = self._last_day[k]
            if day == last:
                raise DoubleAdvance(
                    f"advance_day({customer_id}, {day}) called twice. A "
                    f"BeliefPD advanced twice for one day is aged 2x and is "
                    f"silently wrong. advance() is per CUSTOMER, not per "
                    f"mandate.")
            if day < last:
                raise DoubleAdvance(
                    f"advance_day went backwards: {last} -> {day}")
            self._b[k].advance(day)
            self._last_day[k] = day

    def record_outcome(self, customer_id: int, amount: Rupees,
                       success: bool, mandate_uid=None) -> None:
        """Fold in one censored measurement.

        Pooled: called ONCE per outcome, for any mandate -- every other mandate
        of this customer already sees it, because they are the same object.

        Not pooled: it lands on that mandate's belief and nowhere else, which
        is exactly the information the non-pooled configuration gives up, and
        exactly what W9 measures the price of.
        """
        self._b[self._key(customer_id, mandate_uid)].observe(amount, success)

    # ---- the two summaries. The split is the redaction seam.
    def timing_summary(self, customer_id: int,
                       mandate_uid=None) -> TimingSummary:
        """For the POLICY layer. Carries rupee-denominated state."""
        b = self._b[self._key(customer_id, mandate_uid)]
        ent, topw, expb = b.posterior_summary()
        return TimingSummary(expected_balance=expb, payday_entropy=ent,
                             top_hypothesis_weight=topw)

    def uncertainty(self, customer_id: int,
                    mandate_uid=None) -> PaydayUncertainty:
        """For the LLM layer. Carries NO rupee-denominated state.

        `posterior_summary()` returns expected balance as its third element.
        It is dropped here and never crosses into `agent/llm`. A narrative
        layer cannot disclose a balance it was never handed -- which is the
        governance rule from docs/architecture.md made structural
        instead of editorial."""
        b = self._b[self._key(customer_id, mandate_uid)]
        ent, topw, _expected_balance_dropped = b.posterior_summary()
        return PaydayUncertainty(payday_entropy=ent, top_hypothesis_weight=topw)
