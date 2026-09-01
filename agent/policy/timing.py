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

#: P(a future billing cycle collects), used to price the mandate's continuation
#: value on the LAST attempt of a cycle. See `propose`.
#:
#: SELECTED 1 September 2026 on TRAIN populations 700-709 by mean recovery
#: across payday_err {1,3,7,14}; `0.0` was the previous behaviour and is one
#: cell of that sweep. It is a PLATEAU, not a peak -- 0.3 / 0.6 / 0.9 score
#: 95.19 / 95.59 / 95.55 train mean against 92.68 at 0.0, on a 2 SE of about 3
#: points -- so the value is not load-bearing to two decimal places and the
#: sweep is the result, not the argmax.
#:
#: IT IS A PROBABILITY AND THE SWEEP'S 1.2 AND 1.8 CELLS ARE OUTSIDE ITS
#: DEFINITION. They scored marginally higher (95.49, 95.44 at prior_w=5; 95.73
#: at prior_w=4) and are NOT eligible: a mandate's next cycle cannot be worth
#: more than one collection of it. Recorded because taking that argmax would
#: have been a fitted constant with no derivation behind it, which is the
#: failure mode CLAUDE.md rule 5 names.
#:
#: The derivable value is the agent's own cycle collection rate, about 0.98,
#: which scores 95.55 -- inside the plateau and statistically identical.
DEFAULT_CYCLE_VALUE = 0.6


class Reason:
    OK = "ok"
    NOT_A_MONEY_ACTION = "not_a_money_action"
    CYCLE_CLOSED = "cycle_closed"
    WAIT = "wait"
    NO_LEGAL_SLOT = "no_legal_slot"
    #: The LAST attempt of a cycle, declined because the odds of collecting did
    #: not cover the mandate's remaining cycles. See `propose`.
    MANDATE_PRESERVED = "mandate_preserved"


@dataclass(frozen=True)
class TimingDecision:
    proposal: ScheduleProposal | None
    reason: str
    p_now: float = 0.0
    p_later: float = 0.0
    index_score: float = 0.0


def _plan_value(ps, k: int, vnext: float):
    """Backward induction over (attempts left, days left). Returns (V, fire).

    `ps[i]` is P(collect) on the i-th remaining day of the cycle, in day order,
    from the filter's own forecast. `k` is attempts remaining. `vnext` is the
    value of the mandate's FUTURE cycles given it survives this one, in units
    of `amount` -- `cycles_left * cycle_value`, the same quantity the
    last-attempt rule uses.

    Value, in units of `amount`, of standing at day i with k attempts left:

        V(k, n) = vnext     cycle closed with an attempt unspent: the mandate
                            is ALIVE and keeps its future cycles
        V(0, i) = 0         the last attempt was spent and failed: the mandate
                            is DEAD and its future is gone
        V(k, i) = max( WAIT : V(k, i+1),
                       FIRE : p_i * (1 + vnext) + (1 - p_i) * V(k-1, i+1) )

    THE ASYMMETRY BETWEEN THOSE TWO TERMINAL VALUES IS THE WHOLE MECHANISM.
    Reaching the end of the cycle having declined every day keeps `vnext`;
    failing the final attempt does not. Nothing else in the recursion knows
    that the fourth attempt is different, and nothing needs to.

    NO DISCOUNT INSIDE THE CYCLE, and that is a decision rather than an
    omission. `w3.index_score`'s 0.92 is a ONE-OFF penalty on deferring, not a
    calibrated daily rate; compounding it across a 30-day cycle would reach
    0.08 and crush the value of waiting for a reason no measurement supports.
    At 1.0 the k == 1 case reduces EXACTLY to the closed-form rule the
    continuation value already ships --

        fire iff p / (1 - p) > cycles_left * cycle_value

    -- which is a property `test_plan_dp.py` asserts rather than a claim.
    No new constant is introduced.

    WHY THIS IS NOT THE ONE-STEP INDEX WITH MORE ARITHMETIC. The index
    `amount * (p_now - discount * p_later)` compares today against the best
    remaining day and never against ZERO, so whenever today is the best day
    left it fires, however bad today is. `V` compares FIRE against WAIT
    against the option of reaching the cycle's end having spent nothing, so it
    can decline every remaining day. `fire[k][0]` is that decision for today.

    Cost is O(k * n) floats per decision, k <= 4 and n <= the forecast
    horizon, over a forecast the policy has already computed.
    """
    n = len(ps)
    V = [[0.0] * (n + 1) for _ in range(k + 1)]
    fire = [[False] * (n + 1) for _ in range(k + 1)]
    for kk in range(1, k + 1):
        V[kk][n] = vnext
    for kk in range(1, k + 1):
        for i in range(n - 1, -1, -1):
            p = ps[i]
            wait = V[kk][i + 1]
            act = p * (1.0 + vnext) + (1.0 - p) * V[kk - 1][i + 1]
            # TIES GO TO ACTING. With no within-cycle discount, firing today
            # and firing tomorrow on the same probability have identical value,
            # and a strict `>` resolves that toward waiting -- which walks the
            # decision to the last day of the cycle and reproduces exactly the
            # defer-until-it-expires behaviour this whole investigation is
            # about. Acting on a tie is also the correct reading: an earlier
            # attempt leaves more days behind it for a retry.
            if act >= wait:
                V[kk][i], fire[kk][i] = act, True
            else:
                V[kk][i], fire[kk][i] = wait, False
    return V, fire


def propose(belief, amount: Rupees, day: int, now_t: int, cycle_close: int,
            attempts_used: int, kind: InterventionKind = InterventionKind.RETRY,
            discount: float = DEFAULT_DISCOUNT,
            cap: int = CAP, bracket: bool = False,
            coverage: bool = False,
            lookahead: int | None = None,
            cycles_left: int = 0,
            cycle_value: float = DEFAULT_CYCLE_VALUE,
            plan: bool = False) -> TimingDecision:
    """Where to put the next attempt, or why there isn't one.

    `bracket` is W15 and is OFF by default, so the shipping arm is unchanged.

    THE DEFECT IT ADDRESSES. This rule only ever proposes TOMORROW: `ahead[0]`
    is the target and every other day in the window is used only as `p_later`,
    the option value that decides wait-versus-now. So the action space is
    "attempt tomorrow or wait", and the policy has no way to place an attempt
    on a chosen future day.

    Measured cost, 1 September 2026: the best NON-ADAPTIVE schedule at the
    agent's own attempt budget scores 95.3% against the agent's 89.5% on
    held-out populations. The schedule that beats it is two offsets from the
    estimated payday, `[7, 28]`, and it wins because they are complementary --
    offset 7 covers estimate errors of about -7 to +3 and offset 28 covers
    +2 to +7. Spending a second attempt on the OTHER side of the payday-estimate
    error is exactly what the posterior should already imply and what this rule
    could not express.

    WHAT `bracket` CHANGES, and only after the first attempt of a cycle: the
    target becomes the day in the remaining window with the highest success
    probability, instead of tomorrow. The posterior has already been updated by
    the failure -- `observe()` runs on every outcome -- so its argmax has
    ALREADY moved off the day that just failed. No new constant is introduced
    and no separation distance is invented: the bracketing falls out of the
    posterior rather than being imposed on it.

    It stays inside the policy layer. The LLM cannot reach this file, timing
    never crosses the diagnosis boundary, and ADR-005 is untouched.

    ---------------------------------------------------------------------
    THE CONTINUATION VALUE. `cycles_left` and `cycle_value`, added 1 Sept 2026.
    ---------------------------------------------------------------------

    `p_later` is set to ZERO on the last attempt of a cycle, and the comment
    above says why: with one attempt left there is no later opportunity inside
    this cycle. That is true and it is also the whole defect. Failing the last
    attempt does not merely lose the cycle -- it KILLS THE MANDATE, and a dead
    mandate forfeits every cycle it had left. So the later opportunity a last
    attempt gives up is not a later day. It is the mandate's next cycle, and
    the objective priced it at nothing.

    With `p_later = 0` the score reduces to `amount * p_now`, which is positive
    for ANY positive probability, so the fourth attempt fired unconditionally.
    Measured on the canonical world at `payday_err=1`, 10 held-out populations,
    n=100:

      * 38 fourth attempts fired at a mean believed p_now of 0.269
      * 29 of 1,986 mandates died
      * **31 at-risk cycles were forfeited by a mandate that died in an EARLIER
        cycle -- 100% of the "never attempted" loss class**, and 52.5% of every
        reachable at-risk cycle the agent lost
      * refusing only the fourth attempt scored +3.53 points of recovery
        (paired 2 SE 3.39) and took deaths to zero

    THE RULE, DERIVED RATHER THAN CHOSEN. Let `V` be the expected value of the
    mandate's remaining cycles given it survives. Firing the last attempt wins
    `amount + V` with probability `p` and loses everything with probability
    `1 - p`; holding forfeits this cycle and keeps `V`. So

        fire  iff  p * (amount + V) > V  iff  p * amount > (1 - p) * V

    which is the same shape the index already has, with `(1 - p_now) * V` in
    the slot `discount * p_later` occupies inside a cycle. `V` is
    `cycles_left * cycle_value * amount`, so the rule is an odds test:

        fire  iff  p_now / (1 - p_now)  >  cycles_left * cycle_value

    `cycles_left` is a fact the loop already holds -- `cycles_due(horizon)`
    minus the current cycle -- and a real merchant holds the same fact, because
    a mandate is registered with an end date. `cycle_value` is the probability a
    future cycle collects; it is the one estimated quantity, it is swept rather
    than asserted, and **`cycle_value = 0.0` reproduces the previous behaviour
    exactly**, which is what keeps every existing measurement regenerable.

    The term applies ONLY on the last attempt. Attempts before it consume a
    slot but kill nothing, and the within-cycle option value already prices a
    consumed slot through `p_later`.
    """
    if kind is not InterventionKind.RETRY:
        # Only RETRY moves money. See ports.MONEY_ACTIONS.
        return TimingDecision(None, Reason.NOT_A_MONEY_ACTION)

    # W19. The forecast horizon is 12 days against a 30-day billing cycle, so
    # for more than half the cycle the agent cannot SEE the payday it is
    # waiting for: `p_later` is computed over days that all look poor, the
    # index turns positive, and it spends an attempt early on a day it would
    # not have chosen with sight of the whole window.
    #
    # Measured 1 September 2026: with a PERFECT payday estimate (payday_err=0)
    # the agent still trails the [1,7] fixed schedule by 10.84 points, so the
    # residual is not payday inference. It is flat in payday_err, which is what
    # a fixed horizon limit looks like.
    #
    # `lookahead=None` keeps harness.LOOKAHEAD_DAYS and is the shipping path.
    fc = belief.forecast(day, LOOKAHEAD_DAYS if lookahead is None else lookahead)
    ahead = [(dd, P) for dd, P in fc if dd >= day + 1]
    if not ahead:
        return TimingDecision(None, Reason.CYCLE_CLOSED)
    if coverage and (cap - attempts_used) >= 2:
        # W17. Pick the best PAIR of days, not the best single day.
        #
        # THERE IS NO ENTROPY THRESHOLD HERE, AND THAT IS THE POINT. The brief
        # asked for "collapse to the mode when the posterior is confident, with
        # a derived threshold". No threshold needs deriving: an objective that
        # scores a PAIR of days against the payday posterior collapses to the
        # mode on its own when the posterior is concentrated, and spreads when
        # it is not. A threshold would be a free parameter standing in for a
        # comparison the filter can already make.
        #
        # WHAT IT MAXIMISES. `_pj` gives P(success on this day | payday
        # hypothesis h), one number per hypothesis. For a pair of days the
        # chance of collecting on at least one is, per hypothesis,
        # `a + b - a*b`, and the expected value is that dotted with the payday
        # posterior `w`. Scoring the PAIR rather than each day separately is
        # what makes the second attempt worth placing where the first one is
        # weak: two days that succeed under the same hypotheses add almost
        # nothing, two that succeed under different ones add a lot.
        #
        # THIS IS THE DIFFERENCE FROM W15, WHICH FAILED. W15 targeted the
        # argmax of the updated posterior -- where the posterior IS. Correlation
        # was never in the objective, so it could not tell that its second
        # attempt duplicated the first. Here it is the whole objective.
        window = [(dd, P) for dd, P in ahead if dd < cycle_close]
        pj = getattr(belief, "_pj", None)
        if pj is not None and len(window) >= 2:
            import numpy as _np
            M = _np.array([pj(amount, P) for _dd, P in window])   # (D, H)
            wts = _np.asarray(belief.w)
            # Expected collection for every ordered pair, vectorised.
            joint = M[:, None, :] + M[None, :, :] - M[:, None, :] * M[None, :, :]
            vals = joint @ wts                                    # (D, D)
            _np.fill_diagonal(vals, -1.0)
            i, j = _np.unravel_index(int(_np.argmax(vals)), vals.shape)
            # Execute in day order; the later day is re-planned next tick, so
            # a failure updates the posterior before the pair is re-scored.
            lo = min(int(i), int(j))
            tgt_day, p_tgt = window[lo]
            p_now = float(belief.p_success(amount, p_tgt))
            p_later = 0.0
            score = float(w3.index_score(p_now, p_later, amount, discount))
            if score <= 0:
                return TimingDecision(None, Reason.WAIT, p_now, p_later, score)
            target_t = harness.earliest_legal(tgt_day, now_t + HOURS)
            if target_t is None or target_t >= cycle_close * HOURS:
                return TimingDecision(None, Reason.NO_LEGAL_SLOT,
                                      p_now, p_later, score)
            return TimingDecision(
                ScheduleProposal(target_day=tgt_day, target_t=target_t,
                                 notify_t=now_t, p_now=p_now, p_later=p_later,
                                 index_score=score),
                Reason.OK, p_now, p_later, score)

    if plan:
        # W25. THE DYNAMIC PROGRAM over (attempts left, days left).
        #
        # Same action space as the one-step rule -- attempt tomorrow, or wait --
        # and the same target, `ahead[0]`. The only thing that changes is HOW
        # the choice is made: `_plan_value` can decline every remaining day of
        # the cycle, which the index cannot express because it compares now
        # against later and never against zero.
        window = [(dd, P) for dd, P in ahead if dd < cycle_close]
        if not window:
            return TimingDecision(None, Reason.CYCLE_CLOSED)
        ps = [float(belief.p_success(amount, P)) for _dd, P in window]
        k = max(1, cap - attempts_used)
        vnext = float(cycles_left) * float(cycle_value)
        V, fire = _plan_value(ps, k, vnext)
        p_now = ps[0]
        p_later = float(max(ps[1:], default=0.0))
        # Reported so the audit trail carries a comparable number: the value of
        # acting minus the value of waiting, scaled to rupees like the index.
        score = float(amount * (V[k][0] - V[k][1]))
        if not fire[k][0]:
            # Declining the LAST attempt is a different audit fact from
            # declining an earlier one: it forfeits the cycle to keep the
            # mandate, rather than holding out for a better day in it.
            reason = (Reason.MANDATE_PRESERVED if k == 1 and vnext > 0
                      else Reason.WAIT)
            return TimingDecision(None, reason, p_now, p_later, score)
        tgt_day, p_tgt = window[0]
    elif bracket and attempts_used >= 1:
        # W15. Commit the remaining attempt to the best day left in the window
        # rather than to tomorrow. `p_later` is zero because the day has been
        # chosen: there is no option value left to trade against.
        window = [(dd, P) for dd, P in ahead if dd < cycle_close]
        if not window:
            return TimingDecision(None, Reason.CYCLE_CLOSED)
        tgt_day, p_tgt = max(
            window, key=lambda dp: belief.p_success(amount, dp[1]))
        p_now = float(belief.p_success(amount, p_tgt))
        p_later = 0.0
        score = float(w3.index_score(p_now, p_later, amount, discount))
        if score <= 0:
            return TimingDecision(None, Reason.WAIT, p_now, p_later, score)
    else:
        tgt_day, p_tgt = ahead[0]
        if tgt_day >= cycle_close:
            return TimingDecision(None, Reason.CYCLE_CLOSED)

        p_now = float(belief.p_success(amount, p_tgt))
        later = [belief.p_success(amount, P) for dd, P in ahead[1:] if dd < cycle_close]
        last = (cap - attempts_used) <= 1
        p_later = float(max(later, default=0.0)) if not last else 0.0

        score = float(w3.index_score(p_now, p_later, amount, discount))
        if last and cycles_left > 0 and cycle_value > 0.0:
            # The mandate's continuation value. See the docstring. Subtracted
            # rather than folded into `w3.index_score`, which is shared with
            # sim/harness.py and byte-locked by gate T9.
            score -= (1.0 - p_now) * cycles_left * cycle_value * amount
            if score <= 0:
                return TimingDecision(None, Reason.MANDATE_PRESERVED,
                                      p_now, p_later, score)
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
