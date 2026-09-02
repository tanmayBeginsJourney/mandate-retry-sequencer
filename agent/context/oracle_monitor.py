"""THE DETECTION ORACLE. Knows onset and recovery exactly; acts at the true
change points. The upper bound the statistical detector is measured against.

WHY AN ORACLE AT ALL, AND WHY THIS ONE.
The recovery channel saturates: outage awareness is worth +0.256 pts at
severity 0.80 and is significantly NEGATIVE at 0.40 (docs/results.md). A
number with a ceiling that small cannot rank detectors. Detection can -- but
only against something. This is that something.

The construction is borrowed from the piecewise-stationary restless-bandit
literature: arXiv 2604.10177 measures excess regret against "an oracle that
restarts the base algorithm at the true change points", precisely so the
stationary performance of the base solver factors out and what remains is the
cost of exploration and detection. Same idea, different loss: our base solver
is frozen, so what factors out is the timing brain and what remains is the
context layer. [VERIFIED] from that paper's abstract and HTML full text,
29 Aug 2026. Not ported -- their domain is public-health resource allocation
with no money and no NPCI constraints. We take the evaluation shape and cite it.

WHY IT IS UNREACHABLE BY ANY REAL DETECTOR. Not "hard": unreachable. A
statistical detector needs evidence; evidence arrives only when an attempt is
dispatched; dispatch happens after onset. The gap between onset and the first
piece of evidence is a floor no algorithm can go under. This oracle has no
evidence requirement at all -- it reads the answer.

WHAT IT MUST NEVER TOUCH. It gates dispatch and nothing else, exactly as
`RailMonitor` does. It holds no balance, no customer identity, no target time,
and it is never consulted by `agent/policy/`. The clairvoyant-oracle error
in docs/errors.md was a scheduler that could see the future; this object can
see part of the future
and is therefore confined to the same narrow contract, constructed only by
`agent/batch.py`, and stamped into every run's provenance so no result can
quote it by accident.

THE MUTANTS ARE WINDOW TRANSFORMS, NOT CODE BRANCHES. Read this before adding
one. Rule 1a in CLAUDE.md, added after error 11: a mutant may create illegal
state and nothing else. A mutant that lives inside the object it is meant to
falsify can special-case itself, can write to the scoreboard, and can be
quietly exempted when it becomes inconvenient -- that is exactly how gate M4
came to report PASS on 1066 violations it had written itself.

So there is no mutant branch in this file. A crippled oracle differs from the
true oracle ONLY in the list of numbers it is handed. Every arm executes
byte-identical code. `MUTANTS` below maps a name to a pure function on windows;
the grader that computes excess loss lives in
`agent/tests/test_detection_benchmark.py` and reads only the transition log,
which is the trajectory being measured rather than any counter.
"""
from __future__ import annotations

from agent.context.rail_monitor import (NonMonotonicTime, RailState,
                                        RailVerdict)

Window = tuple[int, int]


# ------------------------------------------------------------ the mutants
# Each is a pure function (windows, horizon_t) -> windows. Nothing here can
# touch a counter, because none of these returns anything but numbers.

def _blind(w: list[Window], T: int) -> list[Window]:
    """Never reports an outage. Cripples: missed detection, totally."""
    return []


def _late(w: list[Window], T: int) -> list[Window]:
    """Fires one window-length after onset, and stops one window-length after
    recovery. Cripples: detection delay. Every hour of every real window is
    unflagged and every flagged hour is wrong."""
    return [(lo + (hi - lo), min(hi + (hi - lo), T)) for lo, hi in w]


def _latch(w: list[Window], T: int) -> list[Window]:
    """Enters correctly and never leaves. Cripples: late resumption. This is
    the failure mode error 14 actually produced in the field -- the monitor
    latched OUTAGE for the rest of the horizon and recovery read 1.97%."""
    return [(lo, T) for lo, _hi in w]


def _phantom(w: list[Window], T: int) -> list[Window]:
    """Correct on the real windows, plus two fabricated ones on days 25 and 55
    where nothing is wrong. Cripples: false positives."""
    fake = [(d * 24 + 8, d * 24 + 14) for d in (25, 55)]
    return sorted(list(w) + [(lo, hi) for lo, hi in fake if hi <= T])


MUTANTS = {
    "blind": _blind,
    "late": _late,
    "latch": _latch,
    "phantom": _phantom,
}


def crippled(windows: list[Window], horizon_t: int, name: str) -> list[Window]:
    """Apply a named mutant to a window list. Raises on an unknown name so a
    typo cannot silently produce an uncrippled oracle reported as crippled."""
    if name not in MUTANTS:
        raise KeyError(f"unknown oracle mutant {name!r}; "
                       f"known: {sorted(MUTANTS)}")
    return MUTANTS[name](list(windows), horizon_t)


# ------------------------------------------------------------- the oracle
class OracleRailMonitor:
    """Clairvoyant drop-in for `RailMonitor`. Same contract, no evidence.

    Implements exactly the surface `agent/loop.py` uses -- `enabled`,
    `record(t, code)`, `assess(t)`, `state`, `transitions` -- so the loop cannot
    tell the difference and no arm gets a different code path.
    """

    #: stamped into provenance and into every verdict's reason string, so a
    #: number produced by this object announces itself in the audit log.
    KIND = "CLAIRVOYANT"

    def __init__(self, windows: list[Window], enabled: bool = True,
                 label: str = "oracle"):
        self.windows = [(int(lo), int(hi)) for lo, hi in windows]
        self.enabled = enabled
        self.label = label
        self._last_t = -1
        self._state = RailState.NORMAL
        self.transitions: list[tuple[int, str, RailVerdict]] = []
        # Deliberately present and deliberately unused: the oracle records no
        # evidence. Named so a reader can see that the absence is the point.
        self.n_recorded = 0

    # ---- input
    def _check_time(self, t: int) -> None:
        """Same monotonic-time contract as RailMonitor, and for the same
        reason. The oracle does not NEED monotonic time -- it keeps no window.
        It is enforced anyway so that an oracle arm cannot be run under a loop
        order the statistical arm could not be run under, which would make the
        comparison meaningless in a way nothing would report."""
        if t < self._last_t:
            raise NonMonotonicTime(
                f"oracle rail monitor saw t={t} after t={self._last_t}. Run "
                f"under time_major=True, same as RailMonitor.")
        self._last_t = t

    def record(self, t: int, code: str) -> None:
        if not self.enabled:
            return
        self._check_time(t)
        self.n_recorded += 1        # counted, never read. The oracle is deaf.

    # ---- output
    def covers(self, t: int) -> bool:
        return any(lo <= t < hi for lo, hi in self.windows)

    def assess(self, t: int) -> RailVerdict:
        if self.enabled:
            self._check_time(t)
        down = self.covers(t)
        if not self.enabled:
            return RailVerdict(RailState.NORMAL, 0, 0, 0.0, 0.0, 1.0,
                               "monitoring disabled")
        prev = self._state
        self._state = RailState.OUTAGE if down else RailState.NORMAL
        v = RailVerdict(
            self._state, 0, 0, 1.0 if down else 0.0, 0.0,
            0.0 if down else 1.0,
            f"{self.KIND} [{self.label}]: t={t} "
            f"{'inside' if down else 'outside'} a known window. No evidence "
            f"was used. Unreachable by any real detector.")
        if self._state is not prev:
            self.transitions.append((t, f"{prev.value}->{self._state.value}", v))
        return v

    @property
    def state(self) -> RailState:
        return self._state
