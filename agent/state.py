"""Mutable per-mandate bookkeeping. Facts, not decisions.

This is the POLICY's view of a mandate. It is deliberately NOT what Stage 0
adjudicates against: the gate reads `agent.constraints.rules.AttemptLedger`,
which it writes itself. If the loop corrupts its own `attempts_used`, the gate
still refuses correctly, because the two counts have different authors.

That mirrors `sim/harness.py:146`, whose ledger carries the comment "Written
only by the harness at dispatch" for exactly this reason, and it is the fix for
the oldest vacuous gate in the project: `assert violations == 0` could not fail
because `live` had already guaranteed the condition it asserted.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agent.ports import MandateRef, PendingNotification, Rupees


@dataclass
class MandateState:
    ref: MandateRef
    amount: Rupees
    due_day: int
    cycle_days: int
    cycle: int = 0
    attempts_used: int = 0
    alive: bool = True
    collected: bool = False
    pending: PendingNotification | None = None
    prev_code: str | None = None
    got_cycles: int = 0
    total_attempts: int = 0
    # set to the cycle index the agent chose to stop acting in, so a STOP or
    # ESCALATE holds for the rest of THAT cycle and releases at rollover
    halted_in_cycle: int | None = None
    decline_history: list[str] = field(default_factory=list)

    @property
    def cycle_open(self) -> int:
        return self.due_day + self.cycle * self.cycle_days

    @property
    def cycle_close(self) -> int:
        return self.due_day + (self.cycle + 1) * self.cycle_days

    def cycles_due(self, horizon_days: int) -> int:
        """Billing cycles that CLOSED inside the horizon, whether or not the
        mandate survived to see them.

        A dead mandate forfeits its remaining cycles, which prices mandate
        death directly and is why this project needs no invented LTV constant
        (docs/01_FACTS.md, the retracted 6x multiplier). Mirrors
        `sim/harness.py:619`."""
        return max(0, (horizon_days - self.due_day) // self.cycle_days)
