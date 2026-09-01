"""A STEELMANNED payday-anchored baseline. No belief, no adaptation.

WHY THIS EXISTS. `harness.payday_wait` is described as "the competitive
baseline: what a good rival team builds in an afternoon", and it is not. It
targets the estimated payday on its FIRST attempt only; every retry after that
is `day + 1`, so after one miss it degenerates into daily retries, burns the
NPCI cap in three days and kills the mandate. It also never revises `est_pay`.

The honest answer to "is your baseline steelmanned" was no.

WHAT THIS ARM IS. Attempts placed at fixed OFFSETS from the estimated payday --
the same noisy `est_payday` the agent is given, and nothing else. It consults no
belief, updates nothing, and adapts to no outcome. It is deliberately the
DUMBEST policy that still spends its attempts in the right shape.

WHERE THE OFFSETS COME FROM, and they are not invented. `[7, 28]` is the
exhaustive optimum over all C(30, 4) offset sets, selected on populations
700-709 and scored on held-out 710-719, in the B-1 computation of 1 September
2026 (`agent/tests/test_nonclairvoyant_oracle.py`). It wins because the two are
complementary against a +/-7 day estimate error: offset 7 covers errors of about
-7 to +3, offset 28 (i.e. -2) covers +2 to +7.

RUNNING IT AS A REAL ARM IS THE POINT. B-1 was arithmetic over a balance array:
it paid nothing for mandate death, nothing for sibling drain, and nothing for
the notification rule. This arm goes through the same loop, the same Stage 0
gate and the same audit trail as the agent, so its number is comparable rather
than merely indicative.

If the belief filter cannot beat two fixed offsets, that is the finding, and it
belongs in the open.
"""
from __future__ import annotations

import agent  # noqa: F401  -- puts sim/ on the path
import harness
import w3

from agent.policy.timing import CAP, HOURS, Reason, TimingDecision
from agent.ports import InterventionKind, Rupees, ScheduleProposal

#: THE HONEST BASELINE. One schedule, selected ONCE on train populations
#: 700-709 by mean hit rate across payday_err {1,3,7,14}, then frozen. This is
#: what a rival can actually deploy: pick a schedule without knowing in advance
#: how good your payday estimate will be.
GENERAL_OFFSETS = (1, 7)

#: The payday_err=7 optimum, kept as a second arm so both are visible. It is
#: NOT the honest baseline: it was selected AT one noise level and scores 62.67%
#: at payday_err=1 where the general schedule scores 100%. Per-noise-level
#: selection is an ORACLE -- a merchant does not know the level in advance.
B1_OFFSETS = (7, 28)


class PaydayOffsetScheduler:
    """Callable with the same shape as `agent.policy.timing.propose`.

    Holds the executor's `estimates` accessor, which returns the NOISY
    `(est_salary, est_payday)` pair -- the same one the agent's belief is
    seeded from. It cannot reach a true payday or a balance: no accessor on
    `SimExecutor` returns one.
    """

    def __init__(self, estimates, offsets=GENERAL_OFFSETS,
                 cycle_days: int = 30):
        self.estimates = estimates
        self.offsets = tuple(sorted(set(int(o) % cycle_days for o in offsets)))
        self.cyc = cycle_days

    def __call__(self, amount: Rupees, day: int, now_t: int, cycle_close: int,
                 attempts_used: int,
                 kind: InterventionKind = InterventionKind.RETRY,
                 cap: int = CAP, customer_id: int | None = None
                 ) -> TimingDecision:
        if kind is not InterventionKind.RETRY:
            return TimingDecision(None, Reason.NOT_A_MONEY_ACTION)
        if attempts_used >= min(cap, len(self.offsets)):
            return TimingDecision(None, Reason.CYCLE_CLOSED)
        if customer_id is None:
            raise ValueError(
                "PaydayOffsetScheduler needs customer_id: it is anchored on "
                "that customer's estimated payday and is meaningless without "
                "one. A silent fallback would make it a calendar schedule.")

        _sal, est_pay = self.estimates(customer_id)
        # EACH OFFSET MAPS TO ITS UNIQUE DAY INSIDE THIS CYCLE'S WINDOW, and
        # the attempts are then taken in CHRONOLOGICAL order.
        #
        # Searching forward from today instead would be wrong and was the first
        # implementation: once the earlier offset has fired, the later one's
        # next occurrence can fall past `cycle_close`, so the arm silently
        # forfeits its second attempt. B-1 places both offsets inside the
        # window by construction, so an arm that cannot is not the same policy
        # and would understate the baseline.
        lo = cycle_close - self.cyc + 1          # earliest legal presentation
        days = sorted(
            lo + ((int(est_pay) + o - lo) % self.cyc) for o in self.offsets)
        ahead = [d for d in days if d >= day + 1 and d < cycle_close]
        if not ahead:
            return TimingDecision(None, Reason.CYCLE_CLOSED)
        tgt_day = ahead[0]

        target_t = harness.earliest_legal(tgt_day, now_t + HOURS)
        if target_t is None or target_t >= cycle_close * HOURS:
            return TimingDecision(None, Reason.NO_LEGAL_SLOT)

        # Zeroed on purpose, exactly as in `fixed_schedule`: this arm computes
        # no probability, and writing one into the audit trail would make its
        # rows indistinguishable from a belief-driven arm's on read-back.
        return TimingDecision(
            ScheduleProposal(target_day=tgt_day, target_t=target_t,
                             notify_t=now_t, p_now=0.0, p_later=0.0,
                             index_score=0.0),
            Reason.OK)
