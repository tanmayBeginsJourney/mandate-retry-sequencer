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

from agent.ports import OK, TECH, Z9, AttemptOutcome, MandateRef, Rupees

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
    """

    def __init__(self, days: list[int], duration_h: int, severity: float,
                 start_hour: int = 8):
        self.windows = [(d * w3.HOURS + start_hour,
                         d * w3.HOURS + start_hour + duration_h)
                        for d in days]
        self.severity = severity
        self.duration_h = duration_h
        self.days = list(days)

    def p_tech_at(self, t: int) -> float:
        for lo, hi in self.windows:
            if lo <= t < hi:
                return self.severity
        return P_TECH

    def covers(self, t: int) -> bool:
        return any(lo <= t < hi for lo, hi in self.windows)

    def asdict(self) -> dict:
        return dict(days=self.days, duration_h=self.duration_h,
                    severity=self.severity, n_windows=len(self.windows))


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
                 per_customer_tech_rng: bool = False):
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

    # ---- what the loop is allowed to read: the NOISY estimates only.
    def estimates(self, customer_id: int) -> tuple[float, int]:
        """(est_salary, est_payday). Never the true salary, payday or balance.

        Error 2 in docs/03_ERRORS.md was a scheduler that could see the true
        balance array. There is no accessor here that returns one."""
        w = self.worlds[customer_id]
        return w.est_salary, w.est_payday

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
        p_tech = self.outage.p_tech_at(t) if self.outage else P_TECH
        in_outage = bool(self.outage and self.outage.covers(t))
        if in_outage:
            self.n_attempts_in_outage += 1

        if rng.random() < p_tech:
            code, success = TECH, False
            self.n_tech += 1
            if in_outage:
                self.n_tech_in_outage += 1
        elif avail >= amount:
            code, success = OK, True
        else:
            code, success = Z9, False

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
        with no topups (docs/06_MODEL_CARD.md §3, item 11), so any oracle row
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
