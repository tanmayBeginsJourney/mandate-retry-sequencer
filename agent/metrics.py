"""Recovery-rate metrics — the numbers that can be compared to the outside world.

WHY THIS MODULE EXISTS. This project's primary metric is **cycles collected /
cycles due**, which counts cycles that never failed. Every published figure in
the payments industry is a **recovery rate**: of the payments that *failed*,
the fraction eventually collected. They are different quantities, and until
this module existed nothing the project reported could be compared to anything
outside it. `docs/04_BUILD_PLAN.md`, W0.

THE TWO INPUTS COME FROM DIFFERENT PLACES ON PURPOSE.

  * the **at-risk set** is a property of the WORLD and is produced by
    `SimExecutor.at_risk_cycles()`. It is identical for every arm run on the
    same population, which is what makes two arms' recovery rates comparable.
  * the **collected set** is a property of the POLICY and is produced by the
    loop.

This module holds no world and no policy. It is arithmetic over two dicts, so
it imports nothing from either layer and trips none of the five import rules in
`agent/tests/test_layer_isolation.py`.

WHAT A RECOVERY RATE HERE IS NOT. The at-risk set excludes technical declines
and the decline taxonomy — see `SimExecutor.at_risk_cycles` for why — so it is
smaller than a real failed-payment population, and every rate below is
therefore flattered. Say so wherever these numbers appear.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# The window the published "~90% of recoveries land inside N days" figure uses.
# It is a REPORTING bucket, not a parameter of anything: changing it changes
# which column of the table you read, never what the agent does.
EARLY_WINDOW_DAYS = 10


@dataclass(frozen=True)
class RecoveryMetrics:
    """Every field is a count or a ratio of counts. No estimates."""

    cycles_due: int
    at_risk: int
    recovered: int
    recovered_paise: int
    days_to_recovery: list[int] = field(default_factory=list)

    @property
    def first_presentation_failure_rate(self) -> float:
        """Of all cycles due, the share a due-date debit would not have covered.

        A property of the WORLD alone — no policy can move it. This is
        validation target V1 and it is the honest answer to "how hard is this
        world?"."""
        return self.at_risk / self.cycles_due if self.cycles_due else 0.0

    @property
    def recovery_rate(self) -> float:
        """Of the cycles at risk, the share this policy collected.

        The quantity every published industry figure reports."""
        return self.recovered / self.at_risk if self.at_risk else 0.0

    @property
    def early_share(self) -> float:
        """Of what was recovered, the share landing inside EARLY_WINDOW_DAYS.

        Validation target V7; the published figure is ~90%."""
        if not self.days_to_recovery:
            return 0.0
        early = sum(1 for d in self.days_to_recovery if d <= EARLY_WINDOW_DAYS)
        return early / len(self.days_to_recovery)

    @property
    def median_days_to_recovery(self) -> float:
        if not self.days_to_recovery:
            return 0.0
        s = sorted(self.days_to_recovery)
        mid = len(s) // 2
        return float(s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2)

    def as_dict(self) -> dict:
        return dict(
            cycles_due=self.cycles_due,
            at_risk=self.at_risk,
            recovered=self.recovered,
            recovered_paise=self.recovered_paise,
            first_presentation_failure_rate=self.first_presentation_failure_rate,
            recovery_rate=self.recovery_rate,
            early_share=self.early_share,
            median_days_to_recovery=self.median_days_to_recovery,
        )


def compute(at_risk: dict, collected: dict, cycles_due: int,
            amounts: dict) -> RecoveryMetrics:
    """Combine a world's at-risk set with one policy's collected set.

    `at_risk`   {(mandate_uid, cycle): due_day}       — from the executor
    `collected` {(mandate_uid, cycle): day collected} — from the loop
    `amounts`   {mandate_uid: rupees}                 — for the money figure

    A cycle counts as recovered when the policy collected it AND the world says
    a due-date debit would not have. A cycle the policy collected that was
    never at risk is not a recovery: nothing needed recovering.
    """
    recovered = 0
    paise = 0
    gaps: list[int] = []
    for key, due_day in at_risk.items():
        day = collected.get(key)
        if day is None:
            continue
        recovered += 1
        paise += int(round(amounts.get(key[0], 0.0) * 100))
        # Days from the due date to the attempt that landed. Zero is possible
        # in principle and would mean a same-day collection the world said
        # could not clear; `test_recovery_metric.py` asserts it never happens.
        gaps.append(day - due_day)
    return RecoveryMetrics(cycles_due=cycles_due, at_risk=len(at_risk),
                           recovered=recovered, recovered_paise=paise,
                           days_to_recovery=gaps)
