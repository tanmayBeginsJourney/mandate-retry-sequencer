"""LAYER B, part 2: WHEN. The Whittle-style index decision.

This is `sim/harness.py`'s belief branch (lines 540-597), reproduced for one
mandate. The frozen model decides timing; nothing in this file is original
work and nothing in it should become original work.

THE LLM CANNOT REACH THIS FILE. `agent/tests/test_layer_isolation.py` asserts
`agent/policy/**` does not import `agent.llm`, and that `agent/llm/**` does not
import `agent.policy`, `w3`, or `harness`. The intervention KIND arrives here
as an enum and is used as a mode switch; no time, day, or delay ever crosses
that boundary, because `ports.Diagnosis` has no field that could carry one.

WHY `propose` RETURNS A REASON. "No attempt today" has four structurally
different causes and they are not interchangeable in the audit trail: a
non-positive index is a WAIT (the Whittle structure working), an empty cycle
is a STOP, no legal hour is a different STOP, and a non-RETRY intervention is
the LLM layer's choice. Recomputing the reason afterwards would mean a second
`forecast()`, which profiling puts at 53% of a run's cost.

THE FIVE THINGS THAT ARE EASY TO GET WRONG (docs/07_AGENT_BRIEF.md §3):
 1. `p_success(amount, P)` takes a posterior for a FUTURE day. Passing None
    silently asks "would this succeed today", which is a different question.
 2. `p_later` is ZERO on the last attempt -- with one attempt left there is no
    later opportunity, so waiting has no option value.
 3. `score <= 0` means WAIT, not fail.
 4. `earliest_legal(day, now_t + HOURS)` is what enforces the >=24h
    notification lead at proposal time. The gate enforces it again, from its
    own ledger, because a policy that filters its own choices cannot prove it.
 5. advance() once per day, observe() on every outcome. `BeliefBook` guards it.
"""
from __future__ import annotations

from dataclasses import dataclass

import agent  # noqa: F401  -- puts sim/ on the path
import harness
import w3

from agent.ports import InterventionKind, Rupees, ScheduleProposal

LOOKAHEAD_DAYS = harness.LOOKAHEAD_DAYS     # 12
CAP = w3.NPCI_MAX                           # 4
HOURS = w3.HOURS                            # 24
DEFAULT_DISCOUNT = 0.92                     # w3.index_score's default.


class Reason:
    OK = "ok"
    NOT_A_MONEY_ACTION = "not_a_money_action"
    CYCLE_CLOSED = "cycle_closed"
    WAIT = "wait"
    NO_LEGAL_SLOT = "no_legal_slot"


@dataclass(frozen=True)
class TimingDecision:
    proposal: ScheduleProposal | None
    reason: str
    p_now: float = 0.0
    p_later: float = 0.0
    index_score: float = 0.0


def propose(belief, amount: Rupees, day: int, now_t: int, cycle_close: int,
            attempts_used: int, kind: InterventionKind = InterventionKind.RETRY,
            discount: float = DEFAULT_DISCOUNT,
            cap: int = CAP) -> TimingDecision:
    """Where to put the next attempt, or why there isn't one."""
    if kind is not InterventionKind.RETRY:
        # Only RETRY moves money. See ports.MONEY_ACTIONS.
        return TimingDecision(None, Reason.NOT_A_MONEY_ACTION)

    fc = belief.forecast(day, LOOKAHEAD_DAYS)
    ahead = [(dd, P) for dd, P in fc if dd >= day + 1]
    if not ahead:
        return TimingDecision(None, Reason.CYCLE_CLOSED)
    tgt_day, p_tgt = ahead[0]
    if tgt_day >= cycle_close:
        return TimingDecision(None, Reason.CYCLE_CLOSED)

    p_now = float(belief.p_success(amount, p_tgt))
    later = [belief.p_success(amount, P) for dd, P in ahead[1:] if dd < cycle_close]
    p_later = float(max(later, default=0.0)) if (cap - attempts_used) > 1 else 0.0

    score = float(w3.index_score(p_now, p_later, amount, discount))
    if score <= 0:
        # WAIT. The future looks better than now. Not a stop.
        return TimingDecision(None, Reason.WAIT, p_now, p_later, score)

    target_t = harness.earliest_legal(tgt_day, now_t + HOURS)
    if target_t is None or target_t >= cycle_close * HOURS:
        return TimingDecision(None, Reason.NO_LEGAL_SLOT, p_now, p_later, score)

    return TimingDecision(
        ScheduleProposal(target_day=tgt_day, target_t=target_t, notify_t=now_t,
                         p_now=p_now, p_later=p_later, index_score=score),
        Reason.OK, p_now, p_later, score)
