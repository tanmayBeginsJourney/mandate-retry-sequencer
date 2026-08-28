"""THE CONTEXT LAYER: state of the RAIL, not state of a customer.

WHY THIS EXISTS, AND WHY THE OPTIMISER CANNOT DO IT.
`w3.BeliefPD` models one customer's balance and payday. It has no
representation of the payment rail at all -- not a weak one, not an unlearned
one. The rail is outside its ontology, and no amount of evidence teaches it a
variable it has no slot for.

Worse, `w3.BeliefPD.observe(amount, success)` TAKES NO DECLINE CODE
(`w3.py:416`). Verified in the frozen source: `harness.py:270-276` sets
`success = False` for a technical decline, and `harness.py:304` passes that
straight to `observe`. So a bank glitch and an empty account are the SAME
measurement to the filter -- and the update is not a gentle nudge. It is
`q[idx:] = 0.0` (`w3.py:432`): every balance bin at or above the attempted
amount is hard-zeroed. One technical decline permanently asserts "this customer
had less than Rs X".

At `P_TECH = 0.008` that is noise. Under an outage it corrupts the posterior
for every affected customer at once -- and because a pooled belief is ONE
object shared by all k mandates (`harness.py:207-215`), a single technical
decline corrupts all k at once. Pooling amplifies the damage, which is exactly
why an aggregator needs this and a single merchant does not have the data to
build it.

THE DETECTION ARGUMENT IS THE MOAT'S SECOND DIVIDEND.
Measured on this population: 99.22% of all attempts land at hour 8, ~19.2
attempts per day across 100 customers. One merchant holds roughly 1/60th of
the mandates, so a single merchant sees about ONE ATTEMPT EVERY THREE DAYS. It
cannot tell an outage from bad luck at any severity. The aggregator sees the
whole stream. `agent/tests/test_outage_detection.py` measures where the
crossover is instead of asserting it.

THE CONSTANTS, AND WHICH ARE INVENTED.
`base_rate` is `harness.P_TECH`, not ours. `alpha_enter` / `alpha_exit` are
DERIVED from a false-alarm target rather than picked, and the test statistic is
an EXACT binomial tail -- a normal approximation is invalid at these counts and
manufactured outages when it was used (see `_binom_tail`). The realised
false-alarm rate at severity=0 is MEASURED, not assumed. `window_h`,
`min_attempts` and `hold_h` ARE invented, are tagged [GUESS], and are swept --
never picked -- exactly as `topup_p` and `nudge_p` were.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from enum import Enum


class NonMonotonicTime(RuntimeError):
    """Time went backwards. Raised rather than tolerated.

    FOUND 28 AUGUST 2026, by the loop-order gate's own mutant.

    A rolling window is only meaningful if time moves forward. `_prune(t)`
    drops events older than `t - window_h`, so when a CUSTOMER-MAJOR loop
    finishes customer 0 at t=2879 and restarts customer 1 at t=0, the cut is
    -24 and NOTHING is pruned: the window still holds all 120 days of customer
    0's attempts, every one of them "in the last 24 hours". `z` goes enormous,
    OUTAGE latches, dispatch pauses forever, and recovery reads ~2% instead of
    ~95%.

    It did not crash. It produced a confident number that meant nothing, which
    is the exact failure this repo has now hit six times. So the misuse is now
    an exception instead of a plausible-looking result."""


class RailState(Enum):
    NORMAL = "NORMAL"
    OUTAGE = "OUTAGE"


@dataclass(frozen=True)
class RailVerdict:
    """What the monitor believes, and the arithmetic behind it.

    Carries NO customer identity and NO balance: this is a statement about the
    rail. It also carries no target time -- it GATES dispatch, it does not
    schedule it, which is what keeps it off the timing path.
    """
    state: RailState
    n_attempts: int
    n_tech: int
    observed_rate: float
    expected_rate: float
    p_value: float          # exact P(>= n_tech by chance). NOT a z-score.
    reason: str


class RailMonitor:
    """Cross-customer, cross-merchant technical-decline monitor.

    Fed every dispatched outcome from every customer. That is only possible
    because the agent loop iterates TIME on the outside and customers on the
    inside -- see `agent/loop.py`. A customer-major loop cannot support this,
    and `test_loop_order_equivalence.py` proves the restructure changed nothing
    else.
    """

    def __init__(self, base_rate: float, window_h: int = 24,
                 alpha_enter: float = 1e-4, alpha_exit: float = 0.05,
                 min_attempts: int = 8, hold_h: int = 12,
                 enabled: bool = True):
        self.base_rate = base_rate
        self.window_h = window_h          # [GUESS], swept
        # DERIVED, not picked. The detector is evaluated about once per
        # dispatch hour, so ~120 times over a 120-day horizon. alpha=1e-4
        # puts the chance of ANY false alarm across a whole run near 1.2%.
        # The realised false-alarm rate is MEASURED at severity=0 in
        # test_outage_detection.py rather than assumed from this arithmetic.
        self.alpha_enter = alpha_enter
        self.alpha_exit = alpha_exit
        self.min_attempts = min_attempts  # [GUESS], swept
        self.hold_h = hold_h              # [GUESS], swept
        self.enabled = enabled
        self._events: deque[tuple[int, bool]] = deque()   # (t, is_tech)
        self._last_t = -1
        self._state = RailState.NORMAL
        self._entered_t: int | None = None
        self.transitions: list[tuple[int, str, RailVerdict]] = []

    # ---- input
    def _check_time(self, t: int) -> None:
        if t < self._last_t:
            raise NonMonotonicTime(
                f"rail monitor saw t={t} after t={self._last_t}. A rolling "
                f"window needs monotonic time; this is what a CUSTOMER-MAJOR "
                f"loop does when it restarts the clock for the next customer. "
                f"Run the monitor under time_major=True.")
        self._last_t = t

    def record(self, t: int, code: str) -> None:
        """One dispatched outcome. Called for EVERY customer, at dispatch.

        A DISABLED MONITOR IS FULLY INERT: it accumulates nothing and asserts
        nothing. `loop.py` calls this unconditionally, so a disabled monitor
        must not impose the monotonic-time requirement -- otherwise the
        customer-major loop, which is the parity path and needs no monitor at
        all, could not run.
        """
        if not self.enabled:
            return
        self._check_time(t)
        self._events.append((t, code == "TECH"))

    def _prune(self, t: int) -> None:
        cut = t - self.window_h
        while self._events and self._events[0][0] < cut:
            self._events.popleft()

    @staticmethod
    def _binom_tail(k: int, n: int, p: float) -> float:
        """Exact P(X >= k) for X ~ Binomial(n, p).

        WHY EXACT AND NOT A z-SCORE. The first version of this file used a
        normal approximation and it was wrong in the direction that manufactures
        outages. With n=11 attempts in the window and p0=0.008 the expected
        count is 0.088, so a SINGLE ordinary technical decline scores z=3.09 --
        apparently a 1-in-1000 event -- when the true probability of seeing at
        least one is 8.5%. Entirely unremarkable. The detector fired 21-26 times
        on a horizon containing 3 outages.

        A normal approximation to a Binomial needs n*p of roughly 5 or more.
        Here n*p ranges from 0.09 to 0.8, so it never applies. Found 28 August
        2026 by reading the transition counts rather than the pass/fail line.
        """
        if k <= 0:
            return 1.0
        if k > n:
            return 0.0
        return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i)
                   for i in range(k, n + 1))

    # ---- output
    def assess(self, t: int) -> RailVerdict:
        """Current belief about the rail. Pure function of the window."""
        if self.enabled:
            self._check_time(t)
        self._prune(t)
        n = len(self._events)
        k = sum(1 for _, is_t in self._events if is_t)
        p0 = self.base_rate
        obs = k / n if n else 0.0
        pval = self._binom_tail(k, n, p0) if n else 1.0

        if n < self.min_attempts:
            reason = (f"only {n} attempts in the last {self.window_h}h, "
                      f"need {self.min_attempts} before the rate means anything")
        else:
            reason = (f"{k} technical declines in {n} attempts over "
                      f"{self.window_h}h = {obs:.1%} against a {p0:.1%} base "
                      f"rate; P(>={k} by chance) = {pval:.2g}")

        if not self.enabled:
            return RailVerdict(RailState.NORMAL, n, k, obs, p0, pval,
                               "monitoring disabled")

        prev = self._state
        if self._state is RailState.NORMAL:
            if n >= self.min_attempts and pval <= self.alpha_enter:
                self._state = RailState.OUTAGE
                self._entered_t = t
        else:
            held = self._entered_t is not None and (t - self._entered_t) >= self.hold_h
            # With dispatch paused the window empties on its own, so `n` falls
            # below min_attempts and the rate stops being measurable. That IS
            # the exit condition: no fresh evidence of a broken rail.
            quiet = n < self.min_attempts or pval >= self.alpha_exit
            if held and quiet:
                self._state = RailState.NORMAL
                self._entered_t = None

        v = RailVerdict(self._state, n, k, obs, p0, pval, reason)
        if self._state is not prev:
            self.transitions.append((t, f"{prev.value}->{self._state.value}", v))
        return v

    @property
    def state(self) -> RailState:
        return self._state
