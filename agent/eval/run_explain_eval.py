"""Does the model earn its call over the deterministic explanation template?

    py -3.12 agent/eval/run_explain_eval.py            live, ~$0.15
    py -3.12 agent/eval/run_explain_eval.py --replay   from cache, $0.00

FOUR ARMS, AND THE THIRD EXISTS TO SEPARATE TWO CHANGES THAT WOULD OTHERWISE
BE ONE:

    template   agent/llm/explain.py, no model, no network.
    v1         glm-explain-v1, the prompt as shipped in prompts.py.
    v2         v1 rewritten. NO EXAMPLES. Isolates wording.
    v3         v2 plus three held-out few-shot examples. Isolates examples.

v2 is the control for v3. Without it, "few-shot helped" and "the rewrite
helped" are the same measurement, and the question asked was specifically
whether EXAMPLES do the work.

THE MAIN THREAT TO EVERYTHING BELOW: the template, all three prompts and this
scorer share an author, and that author has a stake in the answer. Three
mitigations, none of them sufficient alone:

  * The rubric is written and committed BEFORE any output is generated, and
    most of it is mechanical -- a contradiction against the view is a string
    test, not a judgement.
  * An INDEPENDENT JUDGE on a different SKU (glm-5.3, 743B, against the
    diagnoser's glm-5.3-flash 320B-A18B) scores every output BLIND to which
    arm produced it, in shuffled order.
  * Every raw output is printed. A reader who disagrees with a score can
    read the sentence it was given for.

The judge is a source of hypotheses, not a source of truth -- three of its
flags in the diagnoser eval were rejected on inspection. Where the judge and
the mechanical checks disagree, both are reported.

PRE-REGISTERED, BEFORE THE FIRST CALL (see `PREDICTIONS`). Five statements
that could be wrong, recorded so a result that merely confirms what was hoped
is distinguishable from one that survived a chance to fail.

WHAT IS NOT MEASURED. Twelve constructed states are not a sample of an
operator's day, so no mean here estimates field performance. Per-state results
are the unit; the aggregate is a summary of this set and nothing else.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from agent.eval.explain_cases import CASES, EXAMPLES          # noqa: E402
from agent.llm.client import (DIAGNOSER_MODEL, JUDGE_MODEL,   # noqa: E402
                              ResponseCache, ZaiClient, case_key)
from agent.llm.explain import explain, template_explanation   # noqa: E402
from agent.llm.governance import check_explanation            # noqa: E402
from agent.llm.prompts import EXPLAIN_SCHEMA, render_explain  # noqa: E402
from agent.ports import Explanation, family_of                # noqa: E402

NL = chr(10)
CACHE_DIR = os.path.join(HERE, "_cache")

#: A SEPARATE CACHE FILE. `_cache/glm-5.3-flash.json` is committed and is what
#: makes the diagnoser eval reproduce its published numbers byte-identically;
#: this experiment does not write into it.
EXPLAIN_CACHE = os.path.join(CACHE_DIR, "explain-glm-5.3-flash.json")
JUDGE_CACHE = os.path.join(CACHE_DIR, "explain-judge-glm-5.3.json")


# ============================================================== predictions
PREDICTIONS = [
    ("P1", "the template is factually faithful on 12/12 -- it is derived from "
           "the view -- and scores lowest of the four on explanatory value"),
    ("P2", "v1 produces at least one governance failure across the 12, most "
           "likely the numeric rule, because its user block is a wall of "
           "figures and it asks for prose about them"),
    ("P3", "few-shot (v3) beats no-few-shot (v2) on layer attribution by at "
           "least 2 cases of 12"),
    ("P4", "no arm recommends a different action, time or amount on any case: "
           "the prompts forbid it and the type cannot carry it"),
    ("P5", "EX-07 is missed by all four arms -- `conflicted` is not in the "
           "view, so every arm reports a clean collection"),
]


# ============================================================ the candidates
#
# v2 AND v3 LIVE HERE, NOT IN prompts.py, ON PURPOSE. A candidate prompt in the
# shipping module is a prompt somebody wires up before it has won anything.
# The winner gets promoted with an ID; the losers stay in the experiment.

V2_ID = "glm-explain-v2"
V3_ID = "glm-explain-v3"

_SHARED_RULES = """\
WHO DECIDED WHAT. Attribute every part of the outcome to the right layer, and \
never to the wrong one:
  The SCHEDULER chose WHEN. A belief filter estimates when this customer's \
account is most likely to hold money and picks an hour inside NPCI's rules. It \
is not a model and it does not read your text.
  The DIAGNOSER chose WHAT: a root cause and one of RETRY, NUDGE, ESCALATE, \
STOP. It cannot express a time.
  STAGE 0 decided WHETHER IT WAS ALLOWED, using five rules -- cap, peak, lead, \
pending, represent. All five are evaluated even after one refuses, so more \
than one can be REFUSED at once.
  RAZORPAY executed, or refused, or did not answer.
  YOU EXPLAIN. You decided none of it and you are not being asked to.

WHAT THE STAGE 0 RULES MEAN:
  cap        the cycle's four attempts are spent.
  peak       the target hour falls in an NPCI peak window.
  lead       fewer than a full day between the notice and the debit.
  pending    no notice is outstanding, or the one outstanding is for a \
different target hour.
  represent  a re-presentation with no fresh notice. Only a technical decline \
may do that.

WHAT THE RESPONSE CODES MEAN:
  OK collected. Z9 insufficient funds, the commonest failure. TECH a rail \
glitch, not about this customer, and the only code that may be re-presented \
under the existing notice. Z8/IE a limit was hit or funds are blocked for a \
mandate -- THE MONEY IS THERE. ZX/YE the account is frozen, dormant or closed: \
TERMINAL, no retry can ever succeed. VD/VI/VF the mandate itself is broken: \
TERMINAL, the merchant must re-authorise. U30 a catch-all that names nothing. \
deemed_transaction / duplicate_rrn_found: the outcome is genuinely UNKNOWN and \
a retry risks charging twice.

THE SETTING. A mandate is a standing authorisation to debit one customer for \
one merchant. A billing cycle is 30 days. NPCI permits four debit attempts per \
mandate per cycle; exhausting them without collecting kills the mandate and \
forfeits every future cycle. A debit needs a customer notice at least a day \
earlier and may not land in an NPCI peak window.

RULES FOR WHAT YOU WRITE.

1. DO NOT WRITE ANY NUMBER. Not an hour, not a day, not a count, not an \
amount, not a date, not a position in a sequence. The console prints every \
figure beside your text, so a number from you is a second copy that can only \
disagree with the first. Response codes are the exception -- Z9, U30, Z8 are \
names, not quantities -- and so is the phrase "Stage 0".
   WRONG: "the debit is set for hour 288"   RIGHT: "the debit sits outside the \
peak windows"
   WRONG: "3 of 4 attempts are gone"        RIGHT: "one presentation is left \
in this cycle"
2. DO NOT RESTATE THE FIELDS. The operator is already looking at every value \
you were given, laid out in a table. A sentence that names a field and its \
value has told them nothing. Write the part that is NOT on the screen: what \
caused what, which layer is responsible, and what it means for the money.
3. AT LEAST ONE SENTENCE MUST BE CAUSAL -- because, so, which means, since. If \
you cannot find a cause in the evidence, say what is unknown and why that \
matters, which is also causal.
4. Do not recommend a different action, a different hour, or a different \
amount. Do not say what "should" happen. If the decision looks wrong on the \
evidence, say what the evidence shows and stop.
5. Do not disclose or infer the customer's financial state. No balance, no \
salary, no payday, no "they cannot afford it" -- you have not been told any of \
those. "The account was not funded at the time of the request" describes a \
transaction and is fine. Do not name a bank.
6. AT MOST THREE SENTENCES. Plain English, for a colleague who runs this \
console daily. No headings, no bullets, no preamble, no restating the question.

Return ONLY a JSON object with the single key "explanation"."""

V2_SYSTEM = """\
You are the Recovery Analyst for an automated subscription-recovery agent \
handling UPI AutoPay mandates in India.

YOUR JOB IS THE ONE THING THE INTERFACE CANNOT DO. An operator is looking at a \
screen that already shows every field below: the state, the hour, the attempt \
count, the response codes, the five Stage 0 verdicts. What the screen cannot \
show is why those values are what they are, which layer produced each of them, \
and what follows for the money. That is what you write, and it is all you \
write.

Every decision here was made before you were called, by deterministic layers, \
and is already recorded. You are not being asked what should happen.

""" + _SHARED_RULES

V3_SYSTEM = V2_SYSTEM


def _user_block(view) -> str:
    """The case, identically for v2 and v3, so the arms differ only by examples."""
    checks = "\n".join(
        f"  {rule:<10s} {verdict}" + (f"  -- {detail}" if detail else "")
        for rule, verdict, detail in view.gate_checks) or "  (not adjudicated)"
    return f"""\
STATE
mandate                  : {view.mandate_state}
current attempt          : {view.attempt_state or "(none open)"}
cannot be charged because: {view.blocked_because or "(not blocked)"}
billing cycle            : {view.cycle}
day within cycle         : {view.day_in_cycle} ({view.days_left_in_cycle} left of 30)
attempts used this cycle : {view.attempts_used} of {view.attempts_cap}
subscription amount      : Rs {view.amount:.0f}
response codes, oldest first : {", ".join(view.decline_history) or "(none yet)"}
insufficient-funds declines among them : {view.n_recent_z9}

WHAT THE SCHEDULER CHOSE (already fixed; describe it, do not set it)
current hour             : {view.now_t}
customer notified at hour: {view.notify_t or "(no notice issued)"}
debit targeted for hour  : {view.target_t or "(nothing scheduled)"}
hours of notice given    : {view.lead_hours}
target in an NPCI peak window : {"yes" if view.target_is_peak else "no"}
timing model's confidence about this customer : {view.uncertainty_band}

WHAT THE DIAGNOSER CHOSE
root cause    : {view.root_cause or "(no diagnosis this tick)"}
intervention  : {view.intervention or "(none)"}
decided by    : {view.diagnosis_source}

WHAT STAGE 0 SAID
overall : {view.gate_verdict or "(not adjudicated)"}
{checks}

Explain this recovery state. Remember: no numbers, no restating the fields, at
most three sentences. Return the JSON object."""


def render_v2(view) -> tuple[str, str]:
    return V2_SYSTEM, _user_block(view)


def render_v3(view) -> tuple[str, str]:
    """v2 plus three worked examples, appended to the SYSTEM prompt.

    In the system block rather than as fake turns: the transport sends exactly
    one system and one user message, and inventing an assistant turn that never
    happened would make the cache key describe a conversation the API never saw.
    """
    shots = []
    for i, (v, answer) in enumerate(EXAMPLES, 1):
        shots.append(f"--- EXAMPLE {i} ---\n{_user_block(v)}\n\n"
                     f'{{"explanation": "{answer}"}}')
    return (V2_SYSTEM + "\n\nTHREE WORKED EXAMPLES. Match their shape: what "
            "caused what, which layer owns it, what it means for the money. "
            "Notice that none of them writes a figure, and that each one says "
            "something the field list above it does not.\n\n"
            + "\n\n".join(shots)), _user_block(view)


def template_v1_baseline(view) -> str:
    """THE DESCRIPTIVE TEMPLATE, FROZEN AS THE MEASUREMENT SAW IT.

    `agent/llm/explain.py` no longer contains this: the causal version won the
    experiment and was promoted in its place. The loser is kept here, verbatim,
    because it is the baseline every number in this file is a comparison
    against -- deleting it would leave a report quoting a margin over something
    that no longer exists anywhere.

    It is called by the `template` arm and by nothing else. It does not ship.
    """
    bits: list[str] = []
    if view.blocked_because:
        bits.append(f"This mandate cannot be charged at all: "
                    f"{view.blocked_because}.")
    else:
        bits.append(f"The mandate is {view.mandate_state.lower()} and the "
                    f"agent is working the current billing cycle.")
    fams = view.decline_families
    if fams:
        phrase = _V1_FAMILY.get(fams[-1], "the last response code is unmapped")
        bits.append(phrase[0].upper() + phrase[1:]
                    + f" ({view.decline_history[-1]}).")
    else:
        bits.append("No attempt has come back with a decline yet.")
    if view.target_t:
        window = ("inside an NPCI peak window, which Stage 0 refuses"
                  if view.target_is_peak else "outside the peak windows")
        notice = ("after the required day of notice" if view.lead_hours >= 24
                  else "with less notice than the rules require")
        bits.append(f"The timing model placed the debit {window}, {notice}; "
                    f"its confidence about this customer is "
                    f"{view.uncertainty_band}.")
    if view.intervention:
        bits.append(f"The diagnosis names "
                    f"{view.root_cause.replace('_', ' ').lower()} and chose "
                    f"{view.intervention}.")
    refused = [r for r, verdict, _ in view.gate_checks if verdict == "REFUSED"]
    if refused:
        bits.append("Stage 0 refused: "
                    + "; ".join(_V1_RULE.get(r, r) for r in refused) + ".")
    elif view.gate_verdict == "ALLOWED":
        bits.append("Stage 0 evaluated every rule and permitted the debit.")
    return " ".join(bits)


_V1_FAMILY = {
    "OK": "the last attempt collected",
    "FUNDS": "the last decline was an insufficient-funds response",
    "TECH": "the last decline was technical rather than a funding problem",
    "ACCOUNT_SHUT": "the account is closed, dormant or frozen",
    "MANDATE_BROKEN": "the mandate itself has failed",
    "LIMIT": "a limit refused the request, so the money was there",
    "LIEN": "another mandate had already claimed the funds",
    "INDETERMINATE": "the previous outcome is genuinely unknown",
    "AMBIGUOUS": "the last response code names no cause",
}
_V1_RULE = {
    "cap": "the cycle's attempt allowance is spent",
    "peak": "the hour falls inside an NPCI peak window",
    "lead": "the customer was not given a full day's notice",
    "pending": "the outstanding notice does not match this debit",
    "represent": "a re-presentation was attempted with no fresh notice",
}


#: (name, prompt_id, renderer, deterministic_fn). Exactly one of `renderer`
#: and `deterministic_fn` is set.
#:
#: `template` is the frozen descriptive baseline above; `template2` is the
#: causal one that WON and now ships as `explain.template_explanation`. The
#: names are kept as the report quotes them.
ARMS = [
    ("template", None, None, template_v1_baseline),
    ("template2", None, None, template_explanation),
    ("v1", "glm-explain-v1", render_explain, None),
    ("v2", V2_ID, render_v2, None),
    ("v3", V3_ID, render_v3, None),
]


# ============================================== mechanical scoring (rubric)
#
# WRITTEN BEFORE ANY OUTPUT EXISTED. Each check is a claim the text makes that
# the view contradicts, so "unfaithful" is a string test rather than a taste.

_CODE_IN_TEXT = re.compile(
    r"\b(?:Z8|Z9|ZX|YE|VD|VI|VF|IE|U30|TECH|deemed_transaction|"
    r"duplicate_rrn_found|funds_blocked_by_mandate)\b")

_RECOMMEND = [
    r"\bshould (?:retry|wait|stop|escalate|nudge|be|have)\b",
    r"\brecommend\w*\b", r"\bsuggest\w*\b", r"\bwould be better\b",
    r"\binstead,? (?:the|we|it|you)\b", r"\bconsider \w+ing\b",
    r"\bought to\b", r"\bit would be (?:wiser|safer|better)\b",
    r"\bmy advice\b", r"\bI would\b",
]

_MISATTRIB = [
    # the diagnosis picking a time, or the scheduler picking an action
    r"\bdiagnos\w+ (?:chose|picked|set|selected) (?:the )?(?:hour|window|time|slot)\b",
    r"\b(?:scheduler|timing model) (?:chose|picked|selected) (?:to )?"
    r"(?:retry|stop|escalate|nudge)\b",
    r"\bstage 0 (?:chose|picked|decided) (?:to )?(?:retry|stop|escalate|nudge)\b",
    r"\bmodel (?:chose|picked|set) (?:the )?(?:hour|window|time|debit)\b",
    r"\bstage 0 (?:scheduled|set) (?:the )?(?:debit|hour|window)\b",
]

_CAUSAL = [r"\bbecause\b", r"\bso that\b", r"\bso the\b", r"\bso this\b",
           r"\bso there\b", r"\bwhich means\b", r"\bsince\b", r"\bwhich is why\b",
           r"\bthat is why\b", r"\bas a result\b", r"\brather than\b",
           r"\bmeaning\b", r"\bwhich leaves\b", r"\bhence\b"]

#: The three layers, and the words that credit each. Attribution is scored
#: only where the view HAS that layer's output to attribute.
_LAYER_WORDS = {
    "scheduler": [r"\bscheduler\b", r"\btiming model\b", r"\btiming layer\b",
                  r"\bbelief filter\b", r"\bwindow was chosen\b"],
    "diagnosis": [r"\bdiagnos\w+\b", r"\bintervention\b", r"\broot cause\b"],
    "stage0": [r"\bstage 0\b", r"\bconstraint layer\b", r"\bthe gate\b"],
}


def _any(patterns, text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _hits(patterns, text: str) -> list[str]:
    return [m.group(0) for p in patterns
            for m in re.finditer(p, text, re.IGNORECASE)]


def score(view, text: str) -> dict:
    """Mechanical rubric. No judgement, no model."""
    low = text.lower()
    bad: list[str] = []

    # ---- factual faithfulness: claims the view contradicts
    if view.gate_verdict == "REFUSED" and not re.search(
            r"refus|blocked|would not permit|did not permit|stopped", low):
        bad.append("F1 Stage 0 refused and the text does not say so")
    if view.gate_verdict == "REFUSED" and re.search(
            r"stage 0[^.]{0,40}(?:permitted|allowed|approved)", low):
        bad.append("F2 says Stage 0 permitted a refused action")
    if view.gate_verdict == "ALLOWED" and re.search(
            r"stage 0[^.]{0,40}(?:refus|blocked|reject)", low):
        bad.append("F3 says Stage 0 refused an allowed action")

    named = set(_CODE_IN_TEXT.findall(text))
    unseen = named - set(view.decline_history)
    if unseen:
        bad.append(f"F4 names codes not in the history: {sorted(unseen)}")
    # F5 FIRES ON AN ASSERTION, NOT ON THE WORD. Its first form matched
    # "declin" anywhere, so "No attempt has come back with a decline yet" --
    # which DENIES a decline, and is the correct sentence for an unauthorised
    # mandate -- was scored as claiming one. A checker that punishes the right
    # answer produces a ranking of who avoids its bug.
    if not view.decline_history:
        for sentence in re.split(r"(?<=[.;])\s+", low):
            if "declin" not in sentence:
                continue
            if re.search(r"\bno\b|\bnot\b|\bnone\b|\bnever\b|\byet\b|"
                         r"\bhasn'?t\b|\bhave not\b|\bhas not\b", sentence):
                continue
            bad.append("F5 claims a decline where the history is empty")
            break

    if view.intervention == "STOP" and re.search(
            r"\b(?:will|is going to|is scheduled to|plans to) retry\b", low):
        bad.append("F6 asserts a retry where the diagnosis chose STOP")
    if view.attempt_state == "SUCCEEDED" and re.search(
            r"\b(?:debit|payment|attempt) failed\b", low):
        bad.append("F7 calls a succeeded attempt failed")
    if view.attempt_state == "UNKNOWN" and re.search(
            r"\b(?:the )?(?:debit|payment|attempt) (?:failed|was declined)\b", low):
        bad.append("F8 rounds an UNKNOWN outcome down to a failure")
    if view.blocked_because and re.search(
            r"\b(?:debit|attempt) (?:is|was) scheduled\b", low):
        bad.append("F9 asserts a schedule for a mandate that cannot be charged")
    if not view.target_t and re.search(r"\bthe debit (?:is|sits|falls)\b", low):
        bad.append("F10 describes a debit window where none was proposed")

    gov = check_explanation(text)

    # ---- attribution, scored only where the layer produced something
    want, got, wrong = [], [], _hits(_MISATTRIB, text)
    if view.target_t:
        want.append("scheduler")
        if _any(_LAYER_WORDS["scheduler"], text):
            got.append("scheduler")
    if view.intervention:
        want.append("diagnosis")
        if _any(_LAYER_WORDS["diagnosis"], text):
            got.append("diagnosis")
    if view.gate_verdict:
        want.append("stage0")
        if _any(_LAYER_WORDS["stage0"], text):
            got.append("stage0")

    return {
        "words": len(text.split()),
        "unfaithful": bad,
        "gov_ok": gov.ok,
        "gov_reasons": list(gov.reasons),
        "recommends": _hits(_RECOMMEND, text),
        "misattributes": wrong,
        "attrib_want": want,
        "attrib_got": got,
        "causal": len(_hits(_CAUSAL, text)),
    }


# ===================================================== the independent judge
JUDGE_ID = "glm-explain-judge-v1"

JUDGE_SYSTEM = """\
You are grading one OPERATOR-FACING EXPLANATION written about an automated \
UPI AutoPay subscription-recovery agent. You are not the agent and you are not \
being asked what you would have written.

THE SETTING. Four deterministic layers act, in order. A SCHEDULER (a belief \
filter, not a model) chooses WHEN to attempt a debit. A DIAGNOSER chooses WHAT \
to do -- RETRY, NUDGE, ESCALATE or STOP -- and cannot express a time. STAGE 0 \
decides WHETHER the action is legal, using five rules (cap, peak, lead, \
pending, represent), all of which are evaluated even after one refuses. \
RAZORPAY then executes, refuses, or fails to answer.

CODES: OK collected. Z9 insufficient funds. TECH a rail glitch that may be \
re-presented under the existing notice. Z8/IE a limit was hit and the money is \
there. ZX/YE frozen or dormant: terminal. VD/VI/VF the mandate is broken: \
terminal. U30 names nothing. deemed_transaction / duplicate_rrn_found: the \
outcome is genuinely unknown and retrying risks a double charge.

THE READER IS AN OPERATOR WHO IS ALREADY LOOKING AT A TABLE OF EVERY FIELD \
BELOW. They can see the state, the hours, the counts, the codes and the five \
verdicts. An explanation that restates those values has told them nothing. A \
good one says what caused what, which layer is responsible, and what it means \
for the money.

The explanation is deliberately forbidden from writing any number, because the \
interface prints them all. Do NOT penalise it for describing the schedule in \
words instead of naming an hour -- that is the required style, not a failing.

SCORE THREE THINGS, each 1 to 5.

faithfulness -- is every claim supported by the state given?
  5 nothing asserted that the state does not support.  4 one loose phrasing.
  3 an unsupported implication.  2 a claim the state contradicts.
  1 several, or a wrong outcome asserted confidently.

explanatory_value -- does it explain WHY, rather than restating WHAT?
  5 names a mechanism the table cannot show.  4 real causation, thinly drawn.
  3 one causal link among restatement.  2 almost pure restatement.
  1 restatement only, or empty.

operator_usefulness -- after reading it, does the operator know something they
did not know from the table alone?
  5 answers the question the state raises.  4 useful, partial.
  3 mildly useful.  2 barely.  1 nothing.

THEN FOUR YES/NO QUESTIONS. Be strict; these are compliance checks.
  merely_restates -- is it substantially a list of the field values in prose?
  misattributes -- does it credit a decision to the wrong layer? (e.g. the \
diagnoser choosing an hour, Stage 0 choosing an intervention, the scheduler \
choosing an action)
  recommends_action -- does it say what SHOULD be done, or propose a different \
action, hour or amount?
  leaks_state -- does it state or imply the customer's balance, salary, \
payday, or ability to pay, or name their bank?

Return ONLY a JSON object with keys: faithfulness, explanatory_value, \
operator_usefulness, merely_restates, misattributes, recommends_action, \
leaks_state, comment."""

JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["faithfulness", "explanatory_value", "operator_usefulness",
                 "merely_restates", "misattributes", "recommends_action",
                 "leaks_state", "comment"],
    "properties": {
        "faithfulness": {"type": "integer", "minimum": 1, "maximum": 5},
        "explanatory_value": {"type": "integer", "minimum": 1, "maximum": 5},
        "operator_usefulness": {"type": "integer", "minimum": 1, "maximum": 5},
        "merely_restates": {"type": "boolean"},
        "misattributes": {"type": "boolean"},
        "recommends_action": {"type": "boolean"},
        "leaks_state": {"type": "boolean"},
        "comment": {"type": "string", "maxLength": 300},
    },
}


def judge_user(view, text: str) -> str:
    return (_user_block(view).replace(
        "Explain this recovery state. Remember: no numbers, no restating the "
        "fields, at most three sentences. Return the JSON object.", "")
        + f"\nTHE EXPLANATION UNDER TEST\n{text}\n\nGrade it. "
          f"Return the JSON object.")


# ================================================================== running
def run_arm(name, prompt_id, renderer, det_fn, client,
            draw: int = 0) -> list[Explanation]:
    """One pass over the cases. `draw` varies the cache key, nothing else.

    THE TRANSPORT SAMPLES AT temperature=1.0 -- the vendor's setting for this
    SKU, and `client.py` says out loud that a cached score is therefore ONE
    DRAW per case rather than a mean over draws. A single pass showed one arm
    returning unparseable output on half the cases and another on none of
    them, which is either a real difference between the prompts or two
    unlucky draws. Repeating the pass under a different key is what tells
    them apart; it is not a retry, and a failed draw is kept.
    """
    out = []
    for cid, _q, view in CASES:
        if renderer is None:
            body = det_fn(view)
            gov = check_explanation(body)
            out.append(Explanation(
                body=body, source="template" if gov.ok else "template_withheld",
                prompt_id=f"{name}-deterministic",
                withheld_reasons=() if gov.ok else gov.reasons,
                explain_hash=view.explain_hash))
            continue
        system, user = renderer(view)
        ch = view.explain_hash if draw == 0 else f"{view.explain_hash}#d{draw}"
        r = client.complete(system=system, user=user, prompt_id=prompt_id,
                            case_hash=ch, schema=EXPLAIN_SCHEMA)
        if not r.ok or not isinstance(r.parsed, dict):
            # THE DOMINANT FAILURE MODE, AND IT IS NOT A PROMPT PROBLEM. The
            # SKU is asked for a json_schema object with strict=True and
            # sometimes returns a bare JSON STRING -- the schema's VALUE
            # without its key. `client._parse` requires a dict, correctly, so
            # the layer falls back. Recorded as a fallback, never repaired by
            # coercing a string into an object: a transport that ignores the
            # schema half the time is a fact to measure, not to paper over.
            out.append(Explanation(body=template_explanation(view),
                                   source="template",
                                   prompt_id=f"{prompt_id}+fallback",
                                   explain_hash=view.explain_hash))
            continue
        raw = str(r.parsed.get("explanation", "")).strip()
        gov = check_explanation(raw)
        if not raw:
            out.append(Explanation(body=template_explanation(view),
                                   source="template",
                                   prompt_id=f"{prompt_id}+fallback",
                                   explain_hash=view.explain_hash))
        elif not gov.ok:
            out.append(Explanation(body=raw, source="template_withheld",
                                   prompt_id=prompt_id,
                                   withheld_reasons=gov.reasons,
                                   explain_hash=view.explain_hash))
        else:
            out.append(Explanation(body=raw, source="model",
                                   prompt_id=prompt_id,
                                   explain_hash=view.explain_hash))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", action="store_true",
                    help="cache only; no network, no spend")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--budget", type=float, default=1.0)
    ap.add_argument("--draws", type=int, default=1,
                    help="passes per model arm, to separate a prompt "
                         "difference from a sampling difference")
    a = ap.parse_args(argv)

    os.makedirs(CACHE_DIR, exist_ok=True)
    from agent.llm.client import Budget
    key = None if not a.replay else "REPLAY-NO-KEY"
    client = ZaiClient(model=DIAGNOSER_MODEL, api_key=key,
                       cache=ResponseCache(EXPLAIN_CACHE),
                       budget=Budget(limit_usd=a.budget),
                       timeout_s=60.0, max_retries=1)
    if a.replay:
        client.api_key = ""

    print("=" * 108)
    print("EXPLANATION-QUALITY EXPERIMENT")
    print(f"{len(CASES)} constructed states x {len(ARMS)} arms. "
          f"model={DIAGNOSER_MODEL} effort={client.reasoning_effort} "
          f"max_tokens={client.max_tokens}")
    print("Constructed states, not sampled. Per-state results are the unit; "
          "the mean is a summary of this set only.")
    print("=" * 108)

    results: dict[str, list[Explanation]] = {}
    draws: dict[str, list[list[Explanation]]] = {}
    for name, pid, renderer, det_fn in ARMS:
        print(f"  running {name} ...", flush=True)
        n = 1 if renderer is None else a.draws
        draws[name] = [run_arm(name, pid, renderer, det_fn, client, d)
                       for d in range(n)]
        results[name] = draws[name][0]
    if client.cache:
        client.cache.save()

    scores = {name: [score(v, e.body) for (_c, _q, v), e
                     in zip(CASES, results[name])]
              for name in results}

    # ------------------------------------------------------------- outputs
    for i, (cid, question, view) in enumerate(CASES):
        print("\n" + "=" * 108)
        print(f"{cid}  {question}")
        print(f"      mandate={view.mandate_state} attempt="
              f"{view.attempt_state or '-'} codes="
              f"{','.join(view.decline_history) or '-'} "
              f"used={view.attempts_used}/{view.attempts_cap} "
              f"gate={view.gate_verdict or '-'} "
              f"target_t={view.target_t or '-'} act={view.intervention or '-'}")
        print("=" * 108)
        for name in results:
            e, s = results[name][i], scores[name][i]
            flags = []
            if not s["gov_ok"]:
                flags.append("GOV")
            if s["unfaithful"]:
                flags.append("UNFAITHFUL")
            if s["recommends"]:
                flags.append("RECOMMENDS")
            if s["misattributes"]:
                flags.append("MISATTRIB")
            tag = (" [" + ",".join(flags) + "]") if flags else ""
            print(f"\n  -- {name} ({e.source}, {s['words']}w, "
                  f"causal={s['causal']}, attrib="
                  f"{len(s['attrib_got'])}/{len(s['attrib_want'])}){tag}")
            for line in _wrap(e.body, 100):
                print(f"     {line}")
            for b in s["unfaithful"]:
                print(f"     !! {b}")
            for g in s["gov_reasons"]:
                print(f"     !! governance: {g}")
            for r in s["recommends"]:
                print(f"     !! recommends: {r!r}")
            for m in s["misattributes"]:
                print(f"     !! misattributes: {m!r}")

    # ---------------------------------------- transport reliability by draw
    print(NL + "=" * 108)
    print("TRANSPORT RELIABILITY -- did a usable object come back at all?")
    print("A fallback here is not a bad explanation. It is NO explanation, "
          "and the operator sees the template.")
    print("=" * 108)
    print(f"  {'arm':<10s} {'draws':>6s} {'model-answered':>15s} "
          f"{'fell back':>10s} {'rate':>7s}")
    for name in results:
        passes = draws[name]
        tot = sum(len(p) for p in passes)
        fell = sum(1 for p in passes for e in p
                   if e.prompt_id.endswith("+fallback"))
        print(f"  {name:<10s} {len(passes):>6d} {tot - fell:>15d} "
              f"{fell:>10d} {(tot - fell) / tot:>6.0%}")

    # ------------------------------------------------------------ the judge
    judged = {}
    if not a.no_judge:
        jclient = ZaiClient(model=JUDGE_MODEL,
                            api_key=("" if a.replay else None),
                            cache=ResponseCache(JUDGE_CACHE),
                            budget=Budget(limit_usd=a.budget),
                            timeout_s=90.0, max_retries=1)
        if a.replay:
            jclient.api_key = ""
        print("\n" + "=" * 108)
        print(f"INDEPENDENT JUDGE -- {JUDGE_MODEL}, blind to the arm, "
              f"shuffled order")
        print("=" * 108)
        jobs = [(name, i) for name in results for i in range(len(CASES))]
        random.Random(20260904).shuffle(jobs)
        for name, i in jobs:
            view, text = CASES[i][2], results[name][i].body
            r = jclient.complete(
                system=JUDGE_SYSTEM, user=judge_user(view, text),
                prompt_id=JUDGE_ID,
                case_hash=case_key(JUDGE_ID, {"h": view.explain_hash,
                                              "t": text}),
                schema=JUDGE_SCHEMA)
            judged[(name, i)] = r.parsed if (r.ok and isinstance(r.parsed, dict)) else None
        if jclient.cache:
            jclient.cache.save()
        n_bad = sum(1 for v in judged.values() if v is None)
        if n_bad:
            print(f"  {n_bad} judge calls returned nothing usable and are "
                  f"excluded rather than scored as zero.")

    # ----------------------------------------------------------- the tables
    print("\n" + "=" * 108)
    print("MECHANICAL RUBRIC  (written before any output existed)")
    print("=" * 108)
    print(f"  {'arm':<10s} {'gov ok':>7s} {'faithful':>9s} {'recommends':>11s} "
          f"{'misattrib':>10s} {'attribution':>12s} {'causal/case':>12s} "
          f"{'words':>6s}")
    for name in results:
        S = scores[name]
        n = len(S)
        want = sum(len(s["attrib_want"]) for s in S)
        got = sum(len(s["attrib_got"]) for s in S)
        print(f"  {name:<10s} {sum(1 for s in S if s['gov_ok']):>4d}/{n:<2d} "
              f"{sum(1 for s in S if not s['unfaithful']):>6d}/{n:<2d} "
              f"{sum(1 for s in S if s['recommends']):>8d}    "
              f"{sum(1 for s in S if s['misattributes']):>7d}    "
              f"{got:>6d}/{want:<5d} "
              f"{sum(s['causal'] for s in S) / n:>11.2f} "
              f"{sum(s['words'] for s in S) / n:>6.0f}")

    if judged:
        print("\n" + "=" * 108)
        print(f"INDEPENDENT JUDGE ({JUDGE_MODEL}) -- a source of hypotheses, "
              f"not of truth")
        print("=" * 108)
        print(f"  {'arm':<10s} {'faithful':>9s} {'explanatory':>12s} "
              f"{'useful':>8s} {'restates':>9s} {'misattrib':>10s} "
              f"{'recommends':>11s} {'leaks':>6s}")
        for name in results:
            rows = [judged[(name, i)] for i in range(len(CASES))
                    if judged.get((name, i))]
            if not rows:
                continue
            m = lambda k: sum(r[k] for r in rows) / len(rows)      # noqa: E731
            c = lambda k: sum(1 for r in rows if r[k])             # noqa: E731
            print(f"  {name:<10s} {m('faithfulness'):>9.2f} "
                  f"{m('explanatory_value'):>12.2f} "
                  f"{m('operator_usefulness'):>8.2f} "
                  f"{c('merely_restates'):>6d}/{len(rows):<2d} "
                  f"{c('misattributes'):>7d}/{len(rows):<2d} "
                  f"{c('recommends_action'):>8d}/{len(rows):<2d} "
                  f"{c('leaks_state'):>3d}/{len(rows):<2d}")

        print("\n  PER-CASE operator_usefulness (judge), template vs best model arm")
        print(f"  {'case':<8s} " + " ".join(f"{n:>9s}" for n in results))
        for i, (cid, _q, _v) in enumerate(CASES):
            cells = []
            for name in results:
                r = judged.get((name, i))
                cells.append(f"{r['operator_usefulness']:>9d}" if r else f"{'-':>9s}")
            print(f"  {cid:<8s} " + " ".join(cells))

    # ------------------------------------------------------- pre-registered
    print("\n" + "=" * 108)
    print("PRE-REGISTERED PREDICTIONS (written before the first call)")
    print("=" * 108)
    for pid, text in PREDICTIONS:
        print(f"  {pid}  {text}")

    print("\n" + "=" * 108)
    print(f"SPEND  diagnoser {client.budget.asdict()}")
    print(f"       cache     {EXPLAIN_CACHE}: "
          f"{len(client.cache.data) if client.cache else 0} responses")
    print("=" * 108)
    return 0


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines







if __name__ == "__main__":
    raise SystemExit(main())
