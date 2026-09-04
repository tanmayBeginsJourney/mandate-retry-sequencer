"""Twelve recovery states an operator actually meets, as `ExplainView`s.

WHAT THIS FILE IS FOR. `agent/llm/explain.py` has a deterministic template and
a model arm, and nothing yet says which is better. These are the states the
explanation has to cover: a collection, a funds decline, a tick where nothing
happened, a Stage 0 refusal, a lost response, a failed notification, a
contradiction, an unauthorised mandate, a cycle rollover, a terminal decline,
a spent cap, and an indeterminate outcome.

THEY ARE CONSTRUCTED, NOT SAMPLED, and that is the main threat to everything
measured on them. A real operator's distribution is mostly EX-02 and EX-03
repeated; this set is deliberately flat so every branch of the template is
exercised at least once. So a per-case result here is evidence about a state,
and the mean across cases is NOT an estimate of field performance. Nothing in
the report treats it as one.

THE FEW-SHOT EXAMPLES ARE HELD OUT FROM THIS SET. `EXAMPLES` at the bottom is
three separate states, written for the prompt and never scored. Drawing them
from the twelve would be fitting a prompt on its own evaluation set, which
CLAUDE.md's rule 8 forbids and docs/errors.md records the consequences of.

EX-07 WAS A KNOWN GAP AND IS KEPT AS THE REGRESSION FOR IT. A payment that
reported two contradicting terminal states is `PaymentAttempt.conflicted`, and
`ExplainView` had no field for it -- so every arm saw `SUCCEEDED` and every arm
said the cycle collected. That was a confidently wrong answer produced by a
correct layer working from an incomplete view, and no prompt could have fixed
it. The field was added afterwards; this case is what would notice its removal.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from agent.llm.caseview import build_explain_view          # noqa: E402
from agent.ports import ExplainView                        # noqa: E402

_PASS5 = [("cap", "PASS", ""), ("peak", "PASS", ""), ("lead", "PASS", ""),
          ("pending", "PASS", ""), ("represent", "PASS", "")]


def _v(**kw) -> ExplainView:
    """A view with the ordinary defaults, overridden per case."""
    base = dict(
        amount=550.0, attempts_used=0, attempts_cap=4, day_in_cycle=5,
        days_left_in_cycle=25, cycle=1, decline_history=(),
        uncertainty_band="medium", mandate_state="ACTIVE", attempt_state="",
        blocked_because="", conflicted=False,
        now_t=120, target_t=0, notify_t=0,
        target_is_peak=False, gate_verdict="", gate_checks=[],
        root_cause="", intervention="", diagnosis_source="fallback")
    base.update(kw)
    return build_explain_view(**base)


#: (id, what an operator is actually asking, view)
CASES: list[tuple[str, str, ExplainView]] = [

    ("EX-01", "the cycle collected -- why is nothing scheduled?",
     _v(attempts_used=1, day_in_cycle=8, days_left_in_cycle=22,
        decline_history=("Z9", "OK"), attempt_state="SUCCEEDED",
        now_t=192, target_t=168, notify_t=144, gate_verdict="ALLOWED",
        gate_checks=_PASS5, root_cause="UNKNOWN", intervention="STOP",
        uncertainty_band="narrow")),

    ("EX-02", "a funds decline, and a fresh debit is scheduled",
     _v(attempts_used=1, day_in_cycle=11, days_left_in_cycle=19,
        decline_history=("Z9",), attempt_state="ORDER_CREATED",
        now_t=264, target_t=312, notify_t=288, gate_verdict="",
        gate_checks=[], root_cause="INSUFFICIENT_FUNDS",
        intervention="RETRY", uncertainty_band="medium")),

    ("EX-03", "nothing happened this tick -- why not?",
     _v(attempts_used=1, day_in_cycle=13, days_left_in_cycle=17,
        decline_history=("Z9",), attempt_state="",
        now_t=312, target_t=0, notify_t=0, uncertainty_band="wide")),

    ("EX-04", "Stage 0 refused -- on what, and what happens now?",
     _v(attempts_used=1, day_in_cycle=11, days_left_in_cycle=19,
        decline_history=("Z9",), attempt_state="NOTIFIED",
        now_t=275, target_t=275, notify_t=251, target_is_peak=True,
        gate_verdict="REFUSED",
        gate_checks=[("cap", "PASS", ""),
                     ("peak", "REFUSED",
                      "target hour 11:00 is inside an NPCI peak window"),
                     ("lead", "PASS", ""),
                     ("pending", "REFUSED",
                      "outstanding notification targets t=312, action targets t=275"),
                     ("represent", "PASS", "")],
        root_cause="INSUFFICIENT_FUNDS", intervention="RETRY")),

    ("EX-05", "the response was lost -- did the customer get charged?",
     _v(attempts_used=2, day_in_cycle=14, days_left_in_cycle=16,
        decline_history=("Z9",), attempt_state="UNKNOWN",
        now_t=340, target_t=336, notify_t=312, gate_verdict="ALLOWED",
        gate_checks=_PASS5, uncertainty_band="medium")),

    ("EX-06", "the pre-debit notice failed -- is this attempt dead?",
     _v(attempts_used=1, day_in_cycle=12, days_left_in_cycle=18,
        decline_history=("Z9",), attempt_state="NOTIFICATION_FAILED",
        now_t=290, target_t=312, notify_t=288, uncertainty_band="medium")),

    ("EX-07", "two terminal states arrived for one payment",
     # WAS THE KNOWN GAP, AND IS NOW THE FIX. Every arm read this as a clean
     # collection because `ExplainView` had no `conflicted` field. The flag was
     # added because this case found that, and the shipping template now leads
     # with the contradiction instead of with the collection.
     _v(conflicted=True,
        attempts_used=2, day_in_cycle=15, days_left_in_cycle=15,
        decline_history=("Z9", "OK"), attempt_state="SUCCEEDED",
        now_t=360, target_t=336, notify_t=312, gate_verdict="ALLOWED",
        gate_checks=_PASS5, root_cause="UNKNOWN", intervention="STOP")),

    ("EX-08", "the mandate was never authorised",
     _v(mandate_state="PENDING",
        blocked_because="no provider token: the mandate was never authorised",
        day_in_cycle=2, days_left_in_cycle=28, now_t=48,
        uncertainty_band="wide")),

    ("EX-09", "a new billing cycle opened",
     _v(cycle=3, attempts_used=0, day_in_cycle=0, days_left_in_cycle=30,
        decline_history=("Z9", "Z9"), attempt_state="",
        now_t=1440, uncertainty_band="medium")),

    ("EX-10", "a terminal decline -- the account is frozen",
     _v(attempts_used=2, day_in_cycle=16, days_left_in_cycle=14,
        decline_history=("Z9", "ZX"), attempt_state="FAILED",
        now_t=384, target_t=360, notify_t=336, gate_verdict="ALLOWED",
        gate_checks=_PASS5, root_cause="ACCOUNT_UNAVAILABLE",
        intervention="STOP", uncertainty_band="medium")),

    ("EX-11", "the attempt cap is spent",
     _v(attempts_used=4, day_in_cycle=22, days_left_in_cycle=8,
        decline_history=("Z9", "Z9", "TECH", "Z9"), attempt_state="FAILED",
        now_t=528, target_t=504, notify_t=480, gate_verdict="REFUSED",
        gate_checks=[("cap", "REFUSED",
                      "4 attempts already used this cycle, cap is 4"),
                     ("peak", "PASS", ""), ("lead", "PASS", ""),
                     ("pending", "REFUSED",
                      "no notification is outstanding for this mandate"),
                     ("represent", "PASS", "")],
        root_cause="MANDATE_AT_RISK", intervention="STOP",
        uncertainty_band="wide")),

    ("EX-12", "the outcome is genuinely unknown, and a retry would double-charge",
     _v(attempts_used=2, day_in_cycle=17, days_left_in_cycle=13,
        decline_history=("Z9", "deemed_transaction"), attempt_state="UNKNOWN",
        now_t=408, target_t=384, notify_t=360, gate_verdict="ALLOWED",
        gate_checks=_PASS5, root_cause="OUTCOME_UNKNOWN",
        intervention="STOP", uncertainty_band="medium")),
]


# ------------------------------------------------------- few-shot examples
#
# HELD OUT. None of these is in CASES, and none shares a state with one: a
# lien, a technical decline re-presented under the old notice, and a paused
# mandate. Fitting a prompt on the set it is scored against is the defect
# CLAUDE.md's rule 8 names: do not fit a constant on the evaluation set.
#
# The three were chosen for the three things the current prompt is being asked
# to improve at: attributing a refusal to the right layer, explaining why NO
# debit happened, and saying an outcome is unknown without rounding it to a
# failure.
EXAMPLES: list[tuple[ExplainView, str]] = [

    (_v(attempts_used=2, day_in_cycle=9, days_left_in_cycle=21,
        decline_history=("Z9", "funds_blocked_by_mandate"),
        attempt_state="FAILED", now_t=228, target_t=216, notify_t=192,
        gate_verdict="ALLOWED", gate_checks=_PASS5,
        root_cause="FUNDS_LIENED", intervention="ESCALATE",
        uncertainty_band="narrow"),
     "The debit reached the rail and was refused because another mandate had "
     "already claimed the money, so this is a queueing conflict rather than an "
     "empty account. Stage 0 permitted the attempt; the refusal came from the "
     "bank afterwards. The diagnosis escalated rather than retrying, since a "
     "further presentation would meet the same claim."),

    (_v(attempts_used=1, day_in_cycle=6, days_left_in_cycle=24,
        decline_history=("TECH",), attempt_state="SUBMITTING",
        now_t=150, target_t=150, notify_t=126, gate_verdict="ALLOWED",
        gate_checks=[("cap", "PASS", ""), ("peak", "PASS", ""),
                     ("lead", "PASS", ""), ("pending", "PASS", ""),
                     ("represent", "PASS",
                      "prev_code=TECH may auto-represent")],
        root_cause="TECHNICAL", intervention="RETRY",
        uncertainty_band="narrow"),
     "The previous failure was TECH, a rail glitch rather than anything about "
     "this customer, which is the one decline that may be re-presented under "
     "the existing notice. That is why Stage 0's represent rule passed without "
     "a fresh notification being issued. The request has left the process and "
     "the provider has not yet answered."),

    (_v(mandate_state="PAUSED",
        blocked_because="mandate state is PAUSED; only ACTIVE may be charged",
        attempts_used=1, day_in_cycle=14, days_left_in_cycle=16,
        decline_history=("Z9",), now_t=336, uncertainty_band="medium"),
     "No debit was attempted and none will be while the mandate is paused: the "
     "scheduler is never consulted for a mandate that cannot be charged, so "
     "there is no window and no Stage 0 verdict to report. A paused UPI "
     "mandate can only be resumed by the customer from their own app, so this "
     "will not clear on its own."),
]
