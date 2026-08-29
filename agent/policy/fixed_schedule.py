"""The fixed-schedule baseline, as an agent arm.

WHY THIS EXISTS. Every published recovery figure describes a *fixed-interval
retry schedule* -- charge on the due date, then retry daily. This project has
always measured against one (`harness.baseline_doc`), but the harness emits no
per-cycle record, so its recovery rate could not be computed and the project
had nothing to put beside the published 20-40% band. Running the same schedule
as an agent arm produces an audit log, passes the same Stage 0 gate, and
therefore lands in the same recovery-rate metric as everything else.
`docs/04_BUILD_PLAN.md`, the validation suite.

**IT SCHEDULES, IT DOES NOT THINK.** No belief, no forecast, no index. It
attempts on the earliest legal day, every day, until the cycle closes or the
attempt cap is reached. That is the point: it is the thing the timing policy has
to beat, and it must not accidentally borrow any of the timing policy's
machinery.

---

THE DOCUMENTED SCHEDULE CANNOT BE EXECUTED COMPLIANTLY, AND THAT IS A FINDING.

Razorpay documents charge on day T, then retry on T+1, T+2, T+3
(`docs/01_FACTS.md`, [VERIFIED]). NPCI requires at least 24 hours between the
pre-debit notification and the debit, and a mandate does not become actionable
until its cycle opens on day T -- `agent/loop.py` selects
`m.cycle_open <= day < m.cycle_close`. So the earliest notification is hour 8 of
day T and the earliest legal presentation is day T+1.

**The compliant rendering of a four-attempt documented schedule is therefore
T+1, T+2, T+3, T+4, not T, T+1, T+2, T+3.** The notification requirement costs
a full day off the front of every retry window, on every mandate, forever. That
is a real compliance-versus-recovery tension and it is measured rather than
asserted: this arm is the schedule made legal, and `harness.baseline_doc` is the
same schedule executed literally, where the harness counts 974 re-presentation
violations on its own population.

The literal arm is deliberately NOT built here. Stage 0 would refuse its
re-presentations, `scripts/prove_stage0_refuses.py` already demonstrates the
gate refusing an illegal debit end to end, and a third demonstration of the same
property is not worth a second scheduler.
"""
from __future__ import annotations

import agent  # noqa: F401  -- puts sim/ on the path
import harness

from agent.policy.timing import CAP, HOURS, Reason, TimingDecision
from agent.ports import InterventionKind, Rupees, ScheduleProposal


def propose_fixed(amount: Rupees, day: int, now_t: int, cycle_close: int,
                  attempts_used: int,
                  kind: InterventionKind = InterventionKind.RETRY,
                  cap: int = CAP) -> TimingDecision:
    """Attempt on the earliest legal day. No belief is consulted.

    Mirrors `agent.policy.timing.propose`'s signature and return type so the
    loop can swap one for the other without knowing which it holds, and takes
    `amount` it does not use for exactly that reason.
    """
    if kind is not InterventionKind.RETRY:
        return TimingDecision(None, Reason.NOT_A_MONEY_ACTION)
    if attempts_used >= cap:
        return TimingDecision(None, Reason.CYCLE_CLOSED)

    tgt_day = day + 1
    if tgt_day >= cycle_close:
        return TimingDecision(None, Reason.CYCLE_CLOSED)

    # `now_t + HOURS` is what enforces the >=24h notification lead at proposal
    # time. The gate enforces it again from its own ledger, because a policy
    # that filters its own choices cannot prove it did.
    target_t = harness.earliest_legal(tgt_day, now_t + HOURS)
    if target_t is None or target_t >= cycle_close * HOURS:
        return TimingDecision(None, Reason.NO_LEGAL_SLOT)

    # p_now / p_later / index_score are left at zero ON PURPOSE. This arm
    # consults no belief, and writing a probability into the audit trail that
    # nothing computed would make a fixed-schedule row indistinguishable from a
    # belief-driven one when the log is read back.
    return TimingDecision(
        ScheduleProposal(target_day=tgt_day, target_t=target_t, notify_t=now_t,
                         p_now=0.0, p_later=0.0, index_score=0.0),
        Reason.OK)
