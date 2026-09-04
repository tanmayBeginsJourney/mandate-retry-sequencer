"""THE REDACTION BOUNDARY. Two functions, one place.

Everything `agent/llm` knows about a mandate comes through `build_case_view`
(what the DIAGNOSER sees, when it is choosing) or `build_explain_view` (what
the EXPLAINER sees, after everything has been chosen). If a field is not
constructed in one of them, nothing downstream can leak it, because it never
had it.

The two differ by a clock and by nothing else that matters: `CaseView` carries
no time, because a diagnoser must not choose one; `ExplainView` carries the
schedule, because by then the scheduler has already picked it and Stage 0 has
already ruled on it. The note above `build_explain_view` states that line in
full. Everything below applies to both.

WHAT DOES NOT CROSS. The expected balance, the raw payday posterior, the
customer's salary, the true payday, and every `p_success`. The governance rule
in docs/architecture.md says merchant-facing explanations must not
disclose the customer's financial state ("our model scores this window
highest", never "their balance has never recovered before the 3rd"). Enforcing
that by reviewing prose is a losing game; enforcing it by never handing the
prose-writer the number is not.

WHAT DOES CROSS, and why each is defensible to show a merchant:
  amount           -- the merchant's own subscription price. They set it.
  decline_history  -- their own transactions' response codes.
  attempts_used    -- their own retry count against a public regulatory cap.
  day_in_cycle     -- their own billing calendar.
  uncertainty_band -- a coarse label for OUR model's confidence, not the
                      customer's finances. "wide" says we are unsure when to
                      try; it does not say what is in the account.
  peer_mandate_success_recent -- a BOOLEAN, and the sharpest thing here. It
                      says another mandate on this account just succeeded. It
                      names no merchant and no amount. This is the moat made
                      visible to the narrative layer without disclosing a
                      balance, and it is the one field to re-examine if the
                      legal question in docs/results.md ("may an aggregator
                      use Merchant A's outcomes for Merchant B") resolves the
                      wrong way. That question is tagged [GUESS] and unread.

  bank             -- the REMITTER BANK's UPI handle, e.g. "@oksbi". Added
                      29 Aug 2026. This is not customer financial state: in
                      real UPI the payer's VPA carries the bank on its face, so
                      a merchant already sees it on their own transaction
                      report. What it buys is the one thing `RailMonitor`
                      structurally cannot say -- the monitor pools technical
                      declines across every bank, so a single-bank incident is
                      locally obvious and statistically invisible. A diagnoser
                      that can see "every failure I have is @oksbi" has
                      information the binomial tail has averaged away.
                      ⚠️ It is also the field most likely to invite an
                      inference about a person, so `governance.py` treats a
                      bank NAME in merchant-facing prose as a disclosure and
                      the rationale may not carry one.

`merchant_note` IS UNTRUSTED INPUT. It is free text supplied by a merchant and
is carried here so the injection tests have something to attack. Nothing may
treat it as an instruction.
"""
from __future__ import annotations

import hashlib
import json
from typing import Sequence

from agent.ports import CaseView, ExplainView, PaydayUncertainty, Rupees


def _hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def build_case_view(*, amount: Rupees, attempts_used: int, attempts_cap: int,
                    day: int, cycle_open: int, cycle_close: int,
                    decline_history: Sequence[str],
                    peer_success_recent: bool,
                    uncertainty: PaydayUncertainty,
                    merchant_note: str = "", bank: str = "") -> CaseView:
    hist = tuple(decline_history[-6:])
    payload = {
        "attempts_used": attempts_used,
        "attempts_cap": attempts_cap,
        "day_in_cycle": day - cycle_open,
        "days_left_in_cycle": cycle_close - day,
        # amount is bucketed into the hash so near-identical cases share a
        # cached LLM response instead of paying for the same answer twice
        "amount_bucket": int(amount // 250),
        "decline_history": list(hist),
        "peer": bool(peer_success_recent),
        "band": uncertainty.band,
        # In the hash, so two cases that differ only by bank do NOT share a
        # cached LLM response. A bank-shaped outage is the case the cache would
        # otherwise collapse into its bank-agnostic twin.
        "bank": bank,
    }
    return CaseView(
        case_hash=_hash(payload),
        attempts_used=attempts_used,
        attempts_cap=attempts_cap,
        day_in_cycle=day - cycle_open,
        days_left_in_cycle=cycle_close - day,
        amount=amount,
        decline_history=hist,
        n_recent_z9=sum(1 for c in hist if c == "Z9"),
        peer_mandate_success_recent=bool(peer_success_recent),
        uncertainty_band=uncertainty.band,
        merchant_note=merchant_note,
        bank=bank,
    )


# ------------------------------------------------- the explanation boundary
#
# THE SECOND BOUNDARY, AND IT IS SEPARATE ON PURPOSE. Overloading `CaseView`
# with a target hour would put a time into the object the DIAGNOSER reads,
# which is the one thing ADR-005 is about. Two builders, two types, two
# audiences: the diagnoser chooses and may not see a clock; the explainer
# describes and may.
#
# WHAT DOES NOT CROSS HERE, beyond everything `build_case_view` already
# withholds: `p_now`, `p_later` and `index_score`. They are `p_success` under
# other names -- the belief filter's estimate of whether this customer's
# account will have money -- and the rule at the top of this file excludes
# every `p_success` by name. The SCHEDULE those probabilities produced does
# cross, because an hour is our own decision, already made, already durable in
# the attempt row, and already on the operator's screen.
#
# `est_salary` AND `est_payday` DO NOT CROSS EITHER, even though `live/api.py`
# serves both to an authenticated operator. Operator-visible is not
# model-visible. This function is where that distinction is kept, and it is
# kept by not having the parameters.

def build_explain_view(*, amount: Rupees, attempts_used: int, attempts_cap: int,
                       day_in_cycle: int, days_left_in_cycle: int, cycle: int,
                       decline_history: Sequence[str],
                       uncertainty_band: str,
                       mandate_state: str, attempt_state: str,
                       blocked_because: str, conflicted: bool = False,
                       now_t: int, target_t: int, notify_t: int,
                       target_is_peak: bool,
                       gate_verdict: str,
                       gate_checks: Sequence[Sequence[str]],
                       root_cause: str, intervention: str,
                       diagnosis_source: str) -> ExplainView:
    """Everything the explanation layer will ever know, constructed once.

    `target_is_peak` is passed in rather than derived: NPCI's peak window is a
    constant in `w3`, `agent/llm` may not import `w3` (gate I1), and
    `agent.ports` may not import anything (gate I5). The caller knows; this
    layer is not allowed to.
    """
    hist = tuple(decline_history[-6:])
    checks = tuple((str(c[0]), str(c[1]), str(c[2])) for c in gate_checks)
    payload = {
        "attempts_used": attempts_used,
        "attempts_cap": attempts_cap,
        "day_in_cycle": day_in_cycle,
        "days_left_in_cycle": days_left_in_cycle,
        # Bucketed exactly as `build_case_view` buckets it, so two mandates at
        # near-identical prices share one cached explanation instead of paying
        # for the same paragraph twice.
        "amount_bucket": int(amount // 250),
        "decline_history": list(hist),
        "band": uncertainty_band,
        "mandate_state": mandate_state,
        "attempt_state": attempt_state,
        # In the hash: a contradicted attempt and a clean one are the same
        # row apart from this flag, and they need different explanations.
        "conflicted": bool(conflicted),
        "gate_verdict": gate_verdict,
        "gate_checks": [list(c) for c in checks],
        "root_cause": root_cause,
        "intervention": intervention,
        # THE SCHEDULE IS IN THE HASH AS AN OFFSET, NOT AS AN ABSOLUTE HOUR.
        # Two mandates whose debit sits the same distance ahead of the same
        # notice are the same case to explain; that they are in different weeks
        # of a simulated year is not a difference worth a second paid call.
        "hours_ahead": target_t - now_t,
        "lead": target_t - notify_t,
        "peak": bool(target_is_peak),
    }
    return ExplainView(
        explain_hash=_hash(payload),
        attempts_used=attempts_used, attempts_cap=attempts_cap,
        day_in_cycle=day_in_cycle, days_left_in_cycle=days_left_in_cycle,
        cycle=cycle, amount=amount, decline_history=hist,
        n_recent_z9=sum(1 for c in hist if c == "Z9"),
        uncertainty_band=uncertainty_band,
        mandate_state=mandate_state, attempt_state=attempt_state,
        blocked_because=blocked_because, conflicted=bool(conflicted),
        now_t=now_t, target_t=target_t, notify_t=notify_t,
        target_is_peak=bool(target_is_peak),
        gate_verdict=gate_verdict, gate_checks=checks,
        root_cause=root_cause, intervention=intervention,
        diagnosis_source=diagnosis_source,
    )
