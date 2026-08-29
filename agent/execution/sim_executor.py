"""The world the agent acts on. Backed by the FROZEN model in `sim/w3.py`.

This is a second implementation of `sim/harness.py`'s dispatch half, and that
is worth stating plainly rather than burying: `harness.run` is a monolith with
no "step one hour" entry point, so an agent that owns its own loop must own its
own execution too. `sim/` is frozen and untouched; the balance trace, the spend
profile and the outcome rule all come from `w3`.

BECAUSE IT IS A SECOND IMPLEMENTATION, IT NEEDS A PARITY TEST.
`agent/tests/test_parity_vs_harness.py` runs the agent in degenerate mode
(retry-only, fallback diagnoser) against `harness.run("solo_shared_pd", ...)`
on identical inputs. Without that test the agent's headline number is a number
from ungated code quoted next to gated ones, which is the numbers rule
violated in the most expensive available place.

RNG ORDER IS REPRODUCED DELIBERATELY, not incidentally. `harness.run` draws
from one generator in a fixed order:

    rng = default_rng(seed)               trng = default_rng(seed + 777)
    rng.shuffle(donors)                   -- consumed even though we have no
                                             placebo arm, because skipping it
                                             would shift every later draw
    per customer:  balance_trace(c, rng)  -- days*24 + 1 uniforms
                   est_salary  = rng.uniform(0.7, 1.3)
                   est_payday  = rng.integers(-payday_err, payday_err + 1)

`donor_bal` is built from its own `default_rng(seed + 31*ci)` and therefore
consumes nothing from the shared stream, so it is not reproduced.
`trng` yields one `.random()` per dispatch for the technical-decline draw, plus
one per failure when `topup_p > 0`.

`drained` IS RESET LAZILY, and that is exactly equivalent. `harness` clears it
at hour 0 of every payday. It is only ever READ at dispatch, so clearing it on
the first dispatch after a payday boundary gives identical results and removes
the need for a per-day hook -- which in turn keeps `agent/loop.py` from ever
having to hold an executor.
"""
from __future__ import annotations

import numpy as np

import agent  # noqa: F401  -- puts sim/ on the path
import harness
import w3

# BANK_HANDLES / bank_of / N_BANKS moved to ports.py on 29 Aug 2026:
# gate I2 forbids anything outside constraints/stage0.py importing
# agent.execution, and the decline sweep needs them. A hash of a customer
# index and a table of strings are vocabulary, not execution.
from agent.ports import (BANK_HANDLES, FAMILY_ACCOUNT_SHUT, FAMILY_CODES,
                         FAMILY_LIMIT, FAMILY_MANDATE_BROKEN, N_BANKS,
                         OK, TECH, Z9, AttemptOutcome, MandateRef,
                         Rupees, bank_of)

P_TECH = harness.P_TECH             # 0.008


class OutageSchedule:
    """Windows during which the RAIL is degraded, not the customer's balance.

    THIS LIVES IN agent/, NOT sim/. `P_TECH` is read from the frozen harness
    as the base rate; the elevation is ours and `sim/` is untouched.

    DURATION ANCHOR -- all [REPORTED], none [VERIFIED], and none of it is about
    AutoPay specifically:
      * ~995 minutes of total UPI downtime across ~17 incidents, March 2020 to
        March 2025.
      * Longest single incident ~207 minutes (July 2024).
      * 12 April 2025: reported at 4-5 hours, described as the longest in over
        three years. March 2025: ~95 minutes.
      Sources: Business Standard and ORF Online summaries, found 28 Aug 2026.

    THREE REASONS THE SWEEP GOES WIDER THAN THAT ANCHOR:
      1. Those figures describe UPI end-to-end (P2P/P2M). NOTHING found says
         AutoPay MANDATE EXECUTION failed in those windows. The read-across is
         ours and is a [GUESS].
      2. They are secondary reporting. The Business Standard page returned 403
         and could not be read directly; ORF confirms outages in March, April
         and May 2025 but gives no per-incident durations, and notes NPCI's own
         uptime dashboard has not been updated past March 2025 -- so the public
         record is incomplete by its own admission.
      3. Severity -- what FRACTION of attempts fail during a window -- is not
         reported anywhere found. It is pure [GUESS] and is swept from 0.05 to
         0.80.

    WINDOW PLACEMENT IS WORST-CASE, ON PURPOSE. Measured on this population,
    99.22% of all attempts land at hour 8 (2288 of 2306), because the decision
    runs at hour 8 and `earliest_legal(day+1, t+24)` returns hour 8 again. An
    outage that misses hour 8 is harmless BY CONSTRUCTION. Placing every window
    to start at hour 8 therefore measures the worst case, and every number
    downstream is an UPPER bound on both the damage and the value of detecting
    it. Said out loud because an outage model that quietly missed the dispatch
    hour would report "outages don't matter" for the wrong reason.

    BANK-SCOPED OUTAGES, added 29 August 2026. `banks=None` means every bank,
    which is what every existing measurement used and what the defaults still
    do. `banks=["@oksbi"]` scopes the window to one remitter.

    WHY THAT MATTERS AND WHY IT IS THE HARD CASE. `RailMonitor` pools technical
    declines across every customer and therefore across every bank, which is
    exactly what gives an aggregator 22.5 attempts per 24h window against one
    merchant's 0.38. But pooling is also what HIDES a single-bank incident: at
    N_BANKS=8 a one-bank outage raises the pooled technical-decline rate by
    roughly an eighth of its severity, which the binomial tail will not see
    while the affected eighth of customers is failing outright. India's 2026
    incidents were repeatedly bank-shaped while NPCI's own dashboard reported
    the system healthy [GUESS -- the read-across from public reporting is ours,
    see docs/01_FACTS.md]. So a bank-scoped window is locally obvious and
    statistically invisible, and that gap is where a judgement call has room to
    beat a threshold test. It is the reason this parameter exists.
    """

    def __init__(self, days: list[int], duration_h: int, severity: float,
                 start_hour: int = 8, banks: list[str] | None = None):
        self.windows = [(d * w3.HOURS + start_hour,
                         d * w3.HOURS + start_hour + duration_h)
                        for d in days]
        self.severity = severity
        self.duration_h = duration_h
        self.days = list(days)
        self.banks = list(banks) if banks else None

    def covers(self, t: int) -> bool:
        """TIME only. This is the benchmark's ground-truth window and it stays
        bank-agnostic on purpose: the outage is a fact about the world at time
        t, and whether a given attempt was exposed to it is a separate
        question, answered by `affects`."""
        return any(lo <= t < hi for lo, hi in self.windows)

    def affects(self, t: int, bank: str | None = None) -> bool:
        """Time AND bank. With `banks=None` this is identical to `covers`, so
        every measurement taken before 29 Aug 2026 is unchanged."""
        if not self.covers(t):
            return False
        return self.banks is None or bank in self.banks

    def p_tech_at(self, t: int, bank: str | None = None) -> float:
        return self.severity if self.affects(t, bank) else P_TECH

    def asdict(self) -> dict:
        return dict(days=self.days, duration_h=self.duration_h,
                    severity=self.severity, n_windows=len(self.windows),
                    banks=self.banks)


# ============================================================================
# THE RICHER DECLINE TAXONOMY
# ============================================================================
# RICHER DECLINE CODES, AND WHY THEY LIVE HERE AND NOT IN `sim/`.
#
# `sim/w3.py` is FROZEN and its outcome vocabulary is three symbols: OK, Z9,
# TECH. That is enough to model *when money is there*, which is all the belief
# filter reasons about. It is not enough to name the families NPCI actually
# publishes, and the difference is the whole reason a narrative layer has
# anything to do:
#
#     a frozen account       means STOP FOREVER -- no retry ever helps
#     a broken mandate       means no retry ever helps either, for a different
#                            reason, and the merchant must re-authorise
#     a limit hit            means the money IS there and a SMALLER debit works
#     insufficient funds     means wait for money
#     a technical decline    means the rail glitched; try again
#
# `w3.index_score` cannot represent any of that. It reads a probability of
# success and a discount. It has no slot for "this account will never succeed
# again", so a frozen account looks to it exactly like a very unlucky customer
# and it will keep spending attempts on it until the cap kills the mandate. That
# is a structural blind spot, not an unlearned parameter, and it is the same
# shape as the rail-outage argument in `agent/context/rail_monitor.py`.
#
# WHERE THE CODES COME FROM. `agent/eval/golden_cases.yaml`'s `research` block,
# which read NPCI's "UPI Error and Response Codes" v2.9 section 3.1 directly.
# The families and their member codes are [VERIFIED] against that document. What
# is NOT verified, and is tagged [GUESS] everywhere it appears, is HOW OFTEN each
# family occurs: no source found gives AutoPay-specific decline frequencies, and
# the case file says so explicitly. So the mix is SWEPT, never picked --
# the same discipline `topup_p`, `nudge_p` and outage `severity` are held to.
#
# DEFAULTS ARE ALL ZERO AND THAT IS LOAD-BEARING. With `DeclineMix()` unset the
# executor emits exactly OK / Z9 / TECH, byte for byte, so
# `test_parity_vs_harness.py` still reproduces `harness.run` and every gated
# number is untouched. Enrichment is opt-in, exactly like `OutageSchedule`.
#
# THE THREE LATENT STATES ARE STICKY, NOT PER-ATTEMPT. A frozen account does not
# un-freeze because you tried again -- that is the entire point of it. So
# `account_shut` and `mandate_broken` are absorbing: once entered, every
# subsequent attempt on that account (or that mandate) returns the family's code
# regardless of balance. A per-attempt coin flip would produce a world where
# retrying eventually works, which is the world we already have and the one the
# LLM has nothing to add to.


class DeclineMix:
    """How often each non-Z9 family happens. Every rate is [GUESS] and swept.

    All zero by default: `SimExecutor` then emits exactly the frozen
    vocabulary and parity with `harness.run` is bit-exact.

    Rates are per-CUSTOMER or per-MANDATE onset probabilities over the whole
    horizon, not per-attempt, because two of the three states are absorbing.
    """

    def __init__(self, p_account_shut: float = 0.0,
                 p_mandate_broken: float = 0.0,
                 p_limit: float = 0.0,
                 p_ambiguous: float = 0.0):
        self.p_account_shut = p_account_shut       # per customer, per horizon
        self.p_mandate_broken = p_mandate_broken   # per mandate, per horizon
        self.p_limit = p_limit                     # per attempt that HAD money
        self.p_ambiguous = p_ambiguous             # per failure, relabel to U30
        self.enabled = any((p_account_shut, p_mandate_broken, p_limit,
                            p_ambiguous))

    def asdict(self) -> dict:
        return dict(p_account_shut=self.p_account_shut,
                    p_mandate_broken=self.p_mandate_broken,
                    p_limit=self.p_limit, p_ambiguous=self.p_ambiguous)

    def __repr__(self) -> str:
        return f"DeclineMix({self.asdict()})"


class DeclineState:
    """Per-run latent state: which accounts are shut, which mandates are broken.

    Drawn once from its OWN generator, seeded off the run seed the way
    `harness.py:158` seeds `donor_bal`, so turning enrichment on consumes
    nothing from the money path's stream and cannot shift a single balance
    draw. That is what keeps `DeclineMix()` -> parity and
    `DeclineMix(...)` -> the same world plus labels.
    """

    def __init__(self, mix: DeclineMix, pop, seed: int, days: int):
        self.mix = mix
        self.shut_from: dict[int, int] = {}       # customer_id -> hour
        self.broken_from: dict[str, int] = {}     # mandate uid -> hour
        if not mix.enabled:
            return
        rng = np.random.default_rng(seed + 5150)
        T = days * 24
        for ci, c in enumerate(pop):
            if rng.random() < mix.p_account_shut:
                # Onset uniform over the horizon. A shut that lands on the last
                # day is nearly inert and one on the first is maximal; sweeping
                # the RATE and averaging over onset is the honest version of
                # "we do not know when accounts get frozen".
                self.shut_from[ci] = int(rng.integers(0, T))
            for mi, _m in enumerate(c["mandates"]):
                if rng.random() < mix.p_mandate_broken:
                    self.broken_from[f"c{ci}m{mi}"] = int(rng.integers(0, T))

    def terminal_family(self, customer_id: int, uid: str, t: int) -> str | None:
        """Is this mandate permanently dead at time t, and why?

        Account-shut is checked first: if both are true the account is the
        bigger fact, and a merchant re-authorising the mandate would still get
        nothing."""
        if customer_id in self.shut_from and t >= self.shut_from[customer_id]:
            return FAMILY_ACCOUNT_SHUT
        if uid in self.broken_from and t >= self.broken_from[uid]:
            return FAMILY_MANDATE_BROKEN
        return None


class CustomerWorld:
    __slots__ = ("bal", "topups", "drained", "epoch", "payday", "cyc",
                 "est_salary", "est_payday")

    def __init__(self, bal, topups, payday, cyc, est_salary, est_payday):
        self.bal = bal
        self.topups = topups
        self.drained = 0.0
        self.epoch: int | None = None
        self.payday = payday
        self.cyc = cyc
        self.est_salary = est_salary
        self.est_payday = est_payday


class SimExecutor:
    """Implements `agent.ports.Executor`. Only Stage0Gate may hold one."""

    def __init__(self, pop, seed: int, payday_err: int, topup_p: float = 0.0,
                 topup_lag: int = 2, topup_life: int = 48,
                 topup_mult: float = 1.15, spend_decay=None,
                 nudge_p: float = 0.0, outage: "OutageSchedule | None" = None,
                 per_customer_tech_rng: bool = False,
                 declines: "DeclineMix | None" = None, n_banks: int = N_BANKS):
        self.pop = pop
        self.days = pop[0]["days"]
        self.cyc = pop[0]["cycle_days"]
        self.T = self.days * w3.HOURS
        self.topup_p = topup_p
        self.topup_lag = topup_lag
        self.topup_life = topup_life
        self.topup_mult = topup_mult
        self.nudge_p = nudge_p
        self.outage = outage
        self.per_customer_tech_rng = per_customer_tech_rng
        self.n_attempts = 0
        self.n_success = 0
        self.n_nudges = 0
        self.n_nudges_took = 0
        self.n_tech = 0
        self.n_tech_in_outage = 0
        self.n_attempts_in_outage = 0
        # ---- the richer decline taxonomy. ALL RATES DEFAULT TO ZERO, and that
        # is what keeps `test_parity_vs_harness.py` bit-exact: with an unset
        # mix every branch below collapses to the frozen OK/Z9/TECH vocabulary.
        self.declines = declines or DeclineMix()
        self.n_banks = n_banks
        self.banks = {ci: bank_of(ci, n_banks) for ci in range(len(pop))}
        self.code_counts: dict[str, int] = {}
        self.n_terminal_attempts = 0        # attempts spent on a dead account

        rng = np.random.default_rng(seed)
        # ONE shared technical-decline generator reproduces harness.run's draw
        # order exactly -- but only under a CUSTOMER-MAJOR loop, because that
        # is the order harness consumes it in. The cross-customer rail monitor
        # needs a TIME-MAJOR loop, which would consume this stream in a
        # different order and change every outcome for a reason that has
        # nothing to do with the agent. So time-major mode gives each customer
        # its own generator, seeded deterministically the way harness seeds
        # `donor_bal` (`harness.py:158`). Per-customer streams are independent
        # of iteration order, which is what makes the two loop orders provably
        # identical -- see test_loop_order_equivalence.py.
        self.trng = np.random.default_rng(seed + 777)
        self._ctrng = {ci: np.random.default_rng(seed + 777 + 31 * ci)
                       for ci in range(len(pop))}
        # A SEPARATE generator for nudge take-up, mirroring how harness gives
        # `explore` its own `erng`: adding the nudge must not shift a single
        # draw taken by the money path, or degenerate-mode parity would break
        # for a reason that has nothing to do with the agent.
        self.nrng = np.random.default_rng(seed + 9119)
        # A THIRD generator for the decline taxonomy, for the same reason the
        # nudge has its own: turning enrichment on must not shift a single draw
        # taken by the money path, or the enriched world would be a DIFFERENT
        # world rather than the same world with better labels.
        self.drng = np.random.default_rng(seed + 5150)

        # Consumed for RNG-order parity with harness.run. We have no placebo
        # arm, so the result is discarded -- but the draws are not.
        donors = list(range(len(pop)))
        rng.shuffle(donors)

        self.worlds: dict[int, CustomerWorld] = {}
        for ci, c in enumerate(pop):
            bal = w3.balance_trace(c, rng, decay=spend_decay)
            est_sal = c["salary"] * rng.uniform(0.7, 1.3)
            est_pay = int((c["payday"] +
                           rng.integers(-payday_err, payday_err + 1)) % self.cyc)
            topups = np.zeros(self.T + topup_lag + topup_life + 2)
            self.worlds[ci] = CustomerWorld(bal, topups, c["payday"], self.cyc,
                                            est_sal, est_pay)

        # Drawn from its OWN generator, so this consumes nothing from `rng`.
        self.dstate = DeclineState(self.declines, pop, seed, self.days)

    # ---- what the WORLD says about revenue at risk, before any policy acts
    def at_risk_cycles(self) -> dict[tuple[str, int], int]:
        """Mandate-cycles a debit on the due date would NOT have covered.

        Returns `{(mandate_uid, cycle): due_day}`.

        WHY THIS LIVES HERE. It is a question about the world, not about a
        policy: `w3.balance_trace` is deterministic in `(pop, seed)`, so every
        arm run on the same population sees the identical trace and therefore
        the identical at-risk set. That is what makes recovery rates from
        different arms comparable -- a denominator taken from each arm's own
        first attempt would move between arms, and would score the agent on a
        denominator its own waiting had shrunk.

        It also has to live here because nothing else may read a balance.
        Gate I2 forbids every module under `agent/` except
        `constraints/stage0.py` and the composition root from importing
        `agent.execution` at all, so a metrics module cannot reach the world;
        the world has to answer for itself.

        THE WALK. Every mandate presents exactly once per cycle, on its
        `cycle_open` day -- which IS its due date, since
        `cycle_open = due_day + cycle * cycle_days` -- at `w3.DECISION_HOUR`.
        Drain accumulates inside a payday epoch and resets at each payday,
        which is `attempt()`'s rule reproduced rather than re-derived.

        TWO THINGS IT DELIBERATELY DOES NOT MODEL, both of which make this set
        SMALLER than a real failed-payment population and therefore flatter
        every recovery rate computed against it:

          * technical declines (`P_TECH`) and rail outages. Those are
            properties of the rail, not of funding. Including them would make
            the denominator depend on an RNG draw and on the outage schedule,
            so two arms with different outage settings would no longer share a
            denominator.
          * the decline taxonomy -- frozen accounts, revoked mandates, limit
            hits. Same reason, and it is off by default.

        Both are counted separately by the loop and reported beside this.
        """
        at_risk: dict[tuple[str, int], int] = {}
        for ci, c in enumerate(self.pop):
            w = self.worlds[ci]
            drained = 0.0
            epoch: int | None = None
            # (day, mandate index) in mandate-list order, which is the order
            # `harness.run` dispatches a customer's mandates in. Two mandates
            # can fall due on the same day, and then the order decides which
            # one drains first; it is arbitrary and it is not neutral.
            due: list[tuple[int, int, float, int, bool]] = []
            for mi, m in enumerate(c["mandates"]):
                # Cycles that CLOSE inside the horizon, matching
                # `MandateState.cycles_due` exactly -- the denominator this
                # metric is reported against. A trailing cycle that opens
                # before the horizon ends but closes after it is NOT due.
                n_due = max(0, (self.days - m["due_day"]) // self.cyc)
                cycle = 0
                while True:
                    day = m["due_day"] + cycle * self.cyc
                    if day >= self.days:
                        break
                    # Trailing presentations still DRAIN -- another mandate's
                    # due cycle can fall after them in the same payday epoch --
                    # but they are never recorded as at risk.
                    due.append((day, mi, m["amount"], cycle, cycle < n_due))
                    cycle += 1
            due.sort(key=lambda r: (r[0], r[1]))

            for day, mi, amount, cycle, counts in due:
                ep = (day - w.payday) // self.cyc
                if epoch is None or ep != epoch:
                    drained = 0.0
                    epoch = ep
                t = day * w3.HOURS + w3.DECISION_HOUR
                avail = max(w.bal[t] - drained, 0.0)
                if avail >= amount:
                    drained += amount
                elif counts:
                    at_risk[(f"c{ci}m{mi}", cycle)] = day
        return at_risk

    # ---- what the loop is allowed to read: the NOISY estimates only.
    def estimates(self, customer_id: int) -> tuple[float, int]:
        """(est_salary, est_payday). Never the true salary, payday or balance.

        Error 2 in docs/03_ERRORS.md was a scheduler that could see the true
        balance array. There is no accessor here that returns one."""
        w = self.worlds[customer_id]
        return w.est_salary, w.est_payday

    def _pick(self, family: str) -> str:
        """One member code from a family, uniformly. Uses the decline
        generator, never the money path's."""
        codes = FAMILY_CODES[family]
        return codes[int(self.drng.integers(0, len(codes)))]

    # ---- the money path
    def attempt(self, ref: MandateRef, amount: Rupees, t: int) -> AttemptOutcome:
        w = self.worlds[ref.customer_id]
        day = t // w3.HOURS

        # lazy payday replenishment -- see the module docstring
        epoch = (day - w.payday) // w.cyc
        if w.epoch is None or epoch != w.epoch:
            w.drained = 0.0
            w.epoch = epoch

        avail = max(w.bal[t] - w.drained + w.topups[t], 0.0)
        self.n_attempts += 1

        rng = (self._ctrng[ref.customer_id] if self.per_customer_tech_rng
               else self.trng)
        bank = self.banks[ref.customer_id]
        p_tech = self.outage.p_tech_at(t, bank) if self.outage else P_TECH
        # `covers` is time-only, so `n_attempts_in_outage` keeps counting every
        # attempt that landed in a window whether or not that attempt's bank was
        # the one having the incident. That is what the detection benchmark's
        # G-3 witness means and it must not quietly change meaning.
        in_outage = bool(self.outage and self.outage.covers(t))
        exposed = bool(self.outage and self.outage.affects(t, bank))
        if in_outage:
            self.n_attempts_in_outage += 1

        # ONE DRAW, ALWAYS CONSUMED, WHATEVER THE OUTCOME. The enriched world
        # has to be the same world with better labels, not a different world:
        # if a terminal state short-circuited before this line, the technical
        # -decline stream would shift and every subsequent customer's outcomes
        # would move for a reason that has nothing to do with the taxonomy.
        u = rng.random()

        # ---- is this account or mandate permanently dead?  STICKY, ABSORBING.
        terminal = (self.dstate.terminal_family(ref.customer_id, ref.uid, t)
                    if self.declines.enabled else None)

        if terminal is not None:
            code, success = self._pick(terminal), False
            self.n_terminal_attempts += 1
        elif u < p_tech:
            code, success = TECH, False
            self.n_tech += 1
            if in_outage:
                self.n_tech_in_outage += 1
        elif avail >= amount:
            # The money IS there. A limit decline is the one failure mode where
            # that is true, and it is why a smaller debit is the right answer
            # and a later retry of the same amount is not.
            if self.declines.p_limit and self.drng.random() < self.declines.p_limit:
                code, success = self._pick(FAMILY_LIMIT), False
            else:
                code, success = OK, True
        else:
            code, success = Z9, False

        # ---- the catch-all. U30 names nothing, and a merchant who sees it
        # learns only that something went wrong. Relabelling AFTER the true
        # outcome is decided is deliberate: the world still knows what really
        # happened, and the agent does not. That asymmetry is the point.
        if (not success and self.declines.p_ambiguous
                and self.drng.random() < self.declines.p_ambiguous):
            code = FAMILY_CODES["AMBIGUOUS"][0]

        self.code_counts[code] = self.code_counts.get(code, 0) + 1

        if success:
            self.n_success += 1
            w.drained += amount
        elif self.topup_p > 0 and self.trng.random() < self.topup_p:
            cr = amount * self.topup_mult
            lo = min(t + self.topup_lag, self.T)
            hi = min(t + self.topup_lag + self.topup_life, self.T)
            w.topups[lo:hi] += cr

        return AttemptOutcome(t=t, code=code, success=success)

    # ---- the non-money path
    def nudge(self, ref: MandateRef, amount: Rupees, t: int) -> bool:
        """Ask the customer to fund the account. Returns whether it took.

        THE MODEL, AND WHY IT IS A SWEEP AND NOT A CONSTANT. `harness.run`
        already carries `topup_p`: the probability a customer tops up after a
        failed debit, worth `amount * topup_mult` for `topup_life` hours from
        `t + topup_lag`. That mechanism IS a nudge -- but in the harness it
        fires on EVERY failure, unprompted, so it is an upper bound on what a
        nudge could be worth rather than a nudge.

        Here it fires only when the agent sends one, at rate `nudge_p`. No
        value is picked: `nudge_p` is swept and the nudge's worth is reported
        as a curve, the same way the headline is reported conditional on
        `payday_err`. There is no measured Indian UPI nudge take-up rate in
        docs/01_FACTS.md and inventing one would break rule 5.

        Two limits worth saying out loud. The credited amount reuses
        `topup_mult=1.15`, which is the harness's constant for an unprompted
        top-up, not a measured response to a prompt. And at `nudge_p > 0` the
        oracle stops being a tight upper bound -- it reads `bal[tt] - drained`
        with no topups (docs/06_MODEL_CARD.md Â§3, item 11), so any oracle row
        quoted beside a nudge curve is loose.
        """
        self.n_nudges += 1
        if self.nudge_p <= 0 or self.nrng.random() >= self.nudge_p:
            return False
        self.n_nudges_took += 1
        w = self.worlds[ref.customer_id]
        cr = amount * self.topup_mult
        lo = min(t + self.topup_lag, self.T)
        hi = min(t + self.topup_lag + self.topup_life, self.T)
        w.topups[lo:hi] += cr
        return True
