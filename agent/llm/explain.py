"""The Recovery Analyst. It explains a decision; it does not make one.

WHAT THIS LAYER IS FOR. `agent/llm/model_diagnoser.py` was measured against
the rule engine and lost on the path it would ship on -- 2/4 against 4/4 on
merchant-note cases, 26/40 against 28/40 overall (docs/results.md). So the
model is not on the decision path and is not going onto it. This module is
what a model is actually good at here: turning a decision that has already
been made, by four deterministic layers, into two sentences an operator can
read.

THE INVARIANT, AND IT IS ONE SENTENCE:

    THE DIAGNOSIS CANNOT CHOOSE A TIME.
    THE EXPLANATION MAY DESCRIBE A TIME ALREADY CHOSEN BY THE SCHEDULER.

By the time `explain` is called, `timing.propose` has picked the hour, the
diagnoser has picked the intervention, Stage 0 has ruled on both, and the
attempt row is on disk. There is nothing left for this layer to influence,
and three separate things stop it trying:

  * `ports.Explanation` holds prose and provenance. It has no intervention,
    no amount, no hour, no mandate and no verdict, so a model that wanted to
    decide something has nowhere to put the decision -- the same construction
    that makes `ports.Diagnosis` unable to express a time.
  * `ExplainView` is a frozen record of primitives. This layer never receives
    a `Mandate`, a `PaymentAttempt`, a `BeliefBook`, a `Stage0Gate` or an
    executor, so it holds no handle it could mutate.
  * Nothing on the money path calls it. It is reached from one operator route
    and from tests, and the live gates assert that `_decide`, `_schedule` and
    `_execute` never mention it.

IT MUST NOT RAISE, for the reason `ports.Diagnoser` must not: a narrative
failure is an event, not an exception in a service that is holding a mandate
lock. Every path here returns an `Explanation`. A transport that raises, a
model that returns nothing parseable, a missing key, an exhausted budget and
prose that fails governance all end at the deterministic template.

THREE SOURCES, NOT TWO, AND THE THIRD IS THE POINT. `compose.py` reports a
governance failure as "template", so "the model was never asked" and "the
model answered and its words were withheld" look identical in its output. The
first is routine and the second is the row a reviewer most wants to find, so
they are separated here:

    template            no client, or none requested.
    model               the model answered and its words passed.
    template_withheld   the model answered and its words did NOT pass. The
                        operator sees the template, and `withheld_reasons`
                        names the rules that fired.
"""
from __future__ import annotations

from agent.llm.governance import check_explanation
from agent.llm.prompts import EXPLAIN_PROMPT_ID, EXPLAIN_SCHEMA, render_explain
from agent.ports import ExplainView, Explanation

#: Families the template names in words rather than in codes. `family_of` is
#: reached through `agent.ports`, which is the only module `agent/llm` may
#: import for a payment fact (gate I1 forbids `agent.execution`, so the
#: Razorpay reason taxonomy lives in ports for exactly this reason).
#: WHY a decline family means what it means. Every clause here is already
#: written down elsewhere in the codebase -- `ports.py` on what each family
#: names, `fallback.py` on why an unknown outcome is never retried, `domain.py`
#: on why a failed notice is terminal. Nothing is new knowledge; it is
#: knowledge moved into the explanation.
_WHY_FAMILY = {
    "OK": "the cycle has collected, and NPCI allows one collection per "
          "billing cycle, so nothing further is due until the next one opens",
    "FUNDS": "the account was not funded when the request landed, which is "
             "the one failure a different hour can fix, so the scheduler is "
             "the layer that matters here",
    "TECH": "the rail glitched rather than the customer refusing, and TECH is "
            "the only code that may be re-presented under the existing "
            "notice, so a retry costs no fresh notification",
    "ACCOUNT_SHUT": "the account is frozen, dormant or closed, so no retry "
                    "can ever succeed and spending an attempt on one only "
                    "brings the mandate closer to death",
    "MANDATE_BROKEN": "the authorisation itself has failed, so no retry can "
                      "succeed and the merchant has to re-authorise before "
                      "anything can be collected",
    "LIMIT": "a limit refused the request, which means the money was there "
             "and a smaller debit would have cleared",
    "LIEN": "another mandate had already claimed the balance, so this is a "
            "queueing conflict between merchants rather than an empty account",
    "INDETERMINATE": "the provider did not say whether the debit landed, so "
                     "retrying risks charging the customer twice and the "
                     "attempt is never re-presented automatically",
    "AMBIGUOUS": "the response code names no cause, so nothing here "
                 "identifies whether time would help",
}

#: What each Stage 0 rule protects, in words. Keyed by the rule names in
#: `constraints.rules.ALL_RULES`, which this module may not import (gate I1).
#: Each value is written to follow the word "because".
_WHY_RULE = {
    "cap": "the cycle's NPCI presentations are spent, so the mandate is held "
           "back to survive into the next cycle rather than being pushed to "
           "death in this one",
    "peak": "the hour fell inside an NPCI peak window, which the rail refuses "
            "outright",
    "lead": "the customer was not given the full day of notice NPCI requires "
            "before an AutoPay debit",
    "pending": "the notice outstanding for this mandate is not the one this "
               "debit was raised under, and two concurrent notices is the "
               "condition the rule exists to prevent",
    "represent": "a re-presentation was attempted with no fresh notice, which "
                 "only a technical decline is allowed to do",
}

#: What an attempt state means for the money. Absent states need no sentence.
_WHY_ATTEMPT = {
    "UNKNOWN": "the provider never gave an answer, so the debit may have "
               "landed; it is never retried automatically and reconciliation "
               "resolves it by asking the order what it holds",
    "NOTIFICATION_FAILED": "the pre-debit notice never reached the customer, "
                           "and a notice that failed cannot be un-failed, so "
                           "this attempt is finished and a fresh one needs a "
                           "new order",
    "SUBMITTING": "the request left this process before any answer came back, "
                  "so whether it reached the rail is not yet known",
    "SUBMITTED": "the provider holds the request and has not yet said what it "
                 "did with it",
}


def template_explanation(view: ExplainView) -> str:
    """The deterministic explanation. THE DEFAULT, and the replacement.

    IT EXPLAINS RATHER THAN DESCRIBES, and that was a measured decision. The
    first version of this function reported the state in sentences and scored
    ZERO causal connectives on all twelve states of
    `agent/eval/explain_cases.py`; an independent judge marked it "merely
    restates the fields" on twelve of twelve. This version encodes the
    mechanism, and the same judge scored it 3.67 of 5 for operator usefulness
    against the descriptive version's 1.75. It remains free, offline,
    deterministic and always available, which the model arms are not.

    IT CONTAINS NO DIGITS, and that is a constraint rather than a coincidence.
    This string is what `explain` falls back to when a model's prose fails
    `check_explanation`, so a template carrying a figure would be a
    replacement that fails the rule it is replacing a failure of. Decline
    codes are the one numeric-looking thing here and the checker exempts them
    by name.
    """
    out: list[str] = []

    if view.blocked_because:
        # Nothing downstream ran, so nothing downstream is worth reporting.
        return (f"No debit was attempted and none will be: "
                f"{view.blocked_because}. The scheduler is never consulted "
                f"for a mandate that cannot be charged, so there is no window "
                f"and no Stage 0 verdict to report.")

    # FIRST, AND BEFORE THE OUTCOME IS NAMED. A conflicted attempt carries a
    # terminal state that `domain.advance` kept and a second, contradicting one
    # that it refused -- so `attempt_state` alone reads as a clean result and
    # is not one. Reporting the collection first and the contradiction second
    # would bury the only sentence that matters.
    if view.conflicted:
        out.append("Two different terminal outcomes arrived for this one "
                   "payment, so what the state machine kept is the first of "
                   "two contradicting answers and cannot be trusted on its "
                   "own. This package does not pick a winner, because the "
                   "provider gave no way to tell which is right; the row is "
                   "held for a human and no further money action runs on it.")

    fams = view.decline_families
    if fams and not view.conflicted:
        why = _WHY_FAMILY.get(fams[-1])
        if why:
            out.append(f"The most recent response was "
                       f"{view.decline_history[-1]}, which means {why}.")

    st = _WHY_ATTEMPT.get(view.attempt_state)
    if st:
        out.append(f"The open attempt is {view.attempt_state}: {st}.")

    refused = [r for r, verdict, _ in view.gate_checks if verdict == "REFUSED"]
    if refused:
        # ONE CLAUSE PER RULE, JOINED AS A LIST. The first form appended the
        # rule phrases after a bare "refused, on " and produced "Stage 0
        # refused, on the hour fell inside an NPCI peak window ... and on the
        # notice outstanding ...", which is not English. Two rules refusing at
        # once is the normal case, not the exception -- `submit` evaluates all
        # five even after one refuses -- so the multi-rule form is the one that
        # has to read well.
        parts = [f"the {r} rule fired because {_WHY_RULE.get(r, r)}"
                 for r in refused]
        joined = (parts[0] if len(parts) == 1
                  else "; ".join(parts[:-1]) + "; and " + parts[-1])
        out.append(f"Stage 0 refused: {joined}. The refusal is the constraint "
                   f"layer's, not the diagnosis layer's, and nothing reached "
                   f"the provider.")
    elif view.gate_verdict == "ALLOWED":
        out.append("Stage 0 evaluated all five rules and permitted the "
                   "action, so anything that went wrong afterwards came from "
                   "the provider rather than from our own constraints.")

    if not view.target_t and not view.attempt_state and not view.gate_verdict:
        if view.attempts_used == 0 and view.day_in_cycle == 0:
            out.append("A new billing cycle has opened, which restores the "
                       "full NPCI allowance and drops any notice left "
                       "outstanding by the last one.")
        else:
            out.append("The scheduler proposed no window this tick, so no "
                       "debit, no notice and no diagnosis were produced; the "
                       "timing layer scores the remaining days and declines "
                       "to spend an attempt until one scores well enough.")

    if view.intervention and view.intervention != "RETRY":
        # NOT "moves no money": `governance._FINANCIAL_STATE` matches the bare
        # phrase "no money", and it is right to -- a net that had to work out
        # whose money was meant would be a parser. The rule is to rephrase the
        # prose, never to widen the net.
        out.append(f"The diagnosis chose {view.intervention}, a non-money "
                   f"action that costs none of the cycle's attempts.")
    elif view.intervention == "RETRY" and view.target_t:
        out.append("The diagnosis chose to retry; the hour it runs at came "
                   "from the timing model, not from the diagnosis, which has "
                   "no way to express one.")

    return " ".join(out) or ("Nothing has happened on this mandate in the "
                             "current cycle.")


def explain(view: ExplainView, *, client=None, log=None) -> Explanation:
    """Explain `view`. NEVER RAISES, NEVER DECIDES.

    `client` is a `ZaiClient` or anything with the same `complete` signature.
    Passing None -- the default -- is the deterministic path and makes no
    network call, which is what every offline gate runs on.
    """
    templ = template_explanation(view)

    if client is None:
        # Checked, not assumed. If the template ever grows a figure or a
        # weekday, the same rule catches it as would catch the model.
        gov = check_explanation(templ)
        return Explanation(
            body=templ if gov.ok else _stripped(templ),
            source="template", prompt_id="template-explain-v1",
            withheld_reasons=() if gov.ok else gov.reasons,
            explain_hash=view.explain_hash)

    system, user = render_explain(view)
    try:
        r = client.complete(system=system, user=user,
                            prompt_id=EXPLAIN_PROMPT_ID,
                            case_hash=view.explain_hash,
                            schema=EXPLAIN_SCHEMA)
    except Exception as e:                              # noqa: BLE001
        # `ZaiClient.complete` is documented never to raise, and does not. A
        # DIFFERENT transport might, and this module's contract with a service
        # holding a mandate lock is that it comes back. The failure is recorded
        # as an event, exactly as `ModelDiagnoser` records its own.
        return _fell_back(view, templ, f"{type(e).__name__}: {e}", log)

    if not r.ok or not isinstance(r.parsed, dict):
        return _fell_back(view, templ,
                          r.error or "the model returned no object this layer "
                                     "could read as an explanation", log)

    # ONLY `explanation` IS READ. A model that also returned `intervention`,
    # `target_t` or `amount_paise` has returned fields with nowhere to go:
    # `Explanation` has no slot for any of them and this line does not look.
    raw = str(r.parsed.get("explanation", "")).strip()
    if not raw:
        return _fell_back(view, templ, "the model returned an empty "
                                       "explanation", log)

    gov = check_explanation(raw)
    if not gov.ok:
        # REPLACED, NOT EDITED, for the reason `governance.sanitise` gives:
        # editing prose to remove a disclosure leaves its shape behind and
        # invites an argument about whether the redaction was thorough.
        return Explanation(body=templ, source="template_withheld",
                           prompt_id=EXPLAIN_PROMPT_ID,
                           withheld_reasons=gov.reasons,
                           explain_hash=view.explain_hash)
    return Explanation(body=raw, source="model", prompt_id=EXPLAIN_PROMPT_ID,
                       explain_hash=view.explain_hash)


def _fell_back(view: ExplainView, templ: str, reason: str,
               log) -> Explanation:
    """A call that did not produce usable prose. `source` says `template`.

    NOT `template_withheld`. The distinction is whether a model's words were
    rejected or never arrived, and collapsing them would make the audit trail
    report a transport outage as a governance incident.
    """
    if log is not None:
        from agent.audit.log import EventKind
        log.emit(EventKind.LLM_FAILURE, 0, case_hash=view.explain_hash,
                 prompt_id=EXPLAIN_PROMPT_ID, detail=reason, stage="explain")
    return Explanation(body=templ, source="template",
                       prompt_id=f"{EXPLAIN_PROMPT_ID}+fallback",
                       explain_hash=view.explain_hash)


def _stripped(text: str) -> str:
    """Last resort if the TEMPLATE itself fails governance.

    It should not, and `agent/tests/test_explain_layer.py` asserts it does not
    across a spread of views. This exists so that the failure is a visibly
    useless sentence rather than a leak, and so the assertion has something to
    be an assertion ABOUT.
    """
    return ("An explanation could not be produced for this recovery state "
            "under the operator display policy.")
