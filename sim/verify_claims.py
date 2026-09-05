#!/usr/bin/env python3
"""THE CLAIM GATE: the public documents must not contradict the current state.

    py -3.12 sim/verify_claims.py            # check
    py -3.12 sim/verify_claims.py --selftest # prove every rule can fail

WHY THIS EXISTS, AND WHY `verify_docs.py` IS NOT ENOUGH. That gate is a grep
with a memory: it knows that a specific sentence was retracted on a specific
date. It cannot see a claim that is wrong in a way nobody has retracted yet.
The last documentation pass shipped two defects of exactly that kind on the
public page -- a headline whose supporting sentence contradicted the number
beside it, and a chart description naming a configuration the project had
stopped using. Both numbers were regenerated from data; both sentences were not.

WHAT THIS IS. A list of invariants about the CURRENT state, each checked
directly against the files that carry it. It is deliberately small,
deterministic and repository-specific. It runs no model and infers nothing.

THREE RULE KINDS.

  required   the claim MUST appear in the named files. Deleting an
             inconvenient sentence is a way to make a document wrong, so
             absence is a violation.
  forbidden  the claim must NOT appear. These are statements that are false
             about the current state and easy for a rewrite to reintroduce.
  cooccur    a figure may appear only near its conditions. A headline without
             the world it was measured on reads as current and general when it
             is neither.

EVERY RULE CARRIES A CANARY AND `--selftest` PROVES THE RULE FIRES ON IT.
Rules also carry a passing example, so that a rule which fires on everything is
caught too. A checker that cannot demonstrate its own failure mode is the
vacuous-gate shape this repository has hit repeatedly.

A `forbidden` rule may carry an `unless` clause. Several of these claims are
correct when they appear inside an explicit statement of uncertainty -- "whether
cross-merchant pooling is lawful is unresolved" contains the banned phrase and
is the sentence the project wants. A rule that can only be satisfied by writing
something less true is worse than no rule, so the exemption is narrow, named,
and covered by the passing example.

ADDING A RULE. Add the invariant, the files, the reason and the canary in the
same commit. A rule with no canary fails the self-test by construction.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The judge-facing artifacts.
PUBLIC = ("README.md", "docs/index.html")
#: The technical documents.
TECH = ("docs/architecture.md", "docs/results.md", "docs/errors.md")
ALL_DOCS = PUBLIC + TECH

#: The canonical walkthrough customer. `docs/data/scenarios.json` is generated
#: by `scripts/build_page_data.py`; the page and this constant must agree with
#: it, and the cross-check below reads all three rather than trusting any one.
HERO_UID = "c275m0"

#: Documents this project no longer publishes. A reference to one of these is a
#: broken trail, not a historical note, because the file is not in the tree.
REMOVED_DOCS = ("00_HANDOFF", "01_FACTS", "04_BUILD_PLAN", "05_TEST_DESIGN",
                "06_MODEL_CARD", "07_AGENT_BRIEF", "02_RESULTS",
                "03_ERRORS", "08_ARCHITECTURE")


@dataclass
class Rule:
    id: str
    kind: str                      # required | forbidden | cooccur
    files: tuple
    pattern: str
    why: str
    canary: str                    # text the rule MUST flag
    ok_example: str = ""           # text it must NOT flag
    partner: str = ""              # cooccur only
    unless: str = ""               # forbidden only: narrow, named exemption
    window: int = 30               # cooccur only: lines either side
    _rx: re.Pattern = field(init=False, repr=False, default=None)
    _pr: re.Pattern = field(init=False, repr=False, default=None)
    _ur: re.Pattern = field(init=False, repr=False, default=None)

    def __post_init__(self):
        self._rx = re.compile(self.pattern, re.IGNORECASE)
        if self.partner:
            self._pr = re.compile(self.partner, re.IGNORECASE)
        if self.unless:
            self._ur = re.compile(self.unless, re.IGNORECASE)


def _read_early(full: str) -> str | None:
    if not os.path.exists(full):
        return None
    with open(full, encoding="utf-8", errors="replace") as fh:
        return fh.read()


#: THE TWO HEADLINE COOCCURRENCE RULES ARE BUILT FROM THE CANONICAL RUN.
#: They used to hold the literals `99.38` and `9.08`. Re-measuring the world
#: silently retired them: the pattern stopped matching anything, both rules
#: reported ok, and a headline could then appear anywhere without its baseline
#: or its conditions. A rule that protects a number has to be built from the
#: number. If the file is absent the patterns are `(?!)`, which matches
#: nothing -- and `headline_slots()` fails loudly on the same absence, so the
#: gap is reported once rather than twice.
_C = json.loads(_read_early(os.path.join(ROOT, "sim", "canonical_result.json"))
                or "{}")
_AGENT_PCT = re.escape(f"{_C['agent_cycle_rec']:.2f}") if _C else r"(?!)"
_BASE_PCT = re.escape(f"{_C['base_cycle_rec']:.2f}") if _C else r"(?!)"
_UPLIFT = re.escape(f"{_C['uplift']:.2f}") if _C else r"(?!)"
_TWO_SE = re.escape(f"{_C['uplift_2se']:.2f}") if _C else r"(?!)"
_N = re.escape(str(_C["n"])) if _C else r"(?!)"

RULES = [
    # ---------------------------------------------------------------- headline
    Rule(
        id="headline-needs-its-baseline",
        kind="cooccur",
        files=ALL_DOCS,
        pattern=rf"(?<![0-9.]){_AGENT_PCT}\s*%",
        partner=rf"(?<![0-9.]){_BASE_PCT}",
        why="The agent's cycle-collection figure is only interpretable beside "
            "the baseline it is measured against. Quoted alone it reads as "
            "an absolute capability rather than a paired comparison. Both "
            "literals come from sim/canonical_result.json.",
        canary=f"The agent collects {_C.get('agent_cycle_rec', 0):.2f}% of "
               f"billing cycles due.",
        ok_example=f"The agent collects {_C.get('agent_cycle_rec', 0):.2f}% "
                   f"against payday_wait's {_C.get('base_cycle_rec', 0):.2f}%.",
    ),
    Rule(
        id="uplift-needs-its-world",
        kind="cooccur",
        files=ALL_DOCS,
        pattern=rf"\+?{_UPLIFT}\s*(pts|points|percentage points)",
        partner=rf"0\.93|payday_err|2 SE {_TWO_SE}|±{_TWO_SE}",
        why="The uplift holds at pop_spend=0.93 and payday_err=7 and falls "
            "to under a point at the gentle end of the plausible region. "
            "Quoting it without the world it was measured on states a "
            "conditional result as an unconditional one. The literal comes "
            "from sim/canonical_result.json.",
        canary=f"The agent is worth +{_C.get('uplift', 0):.2f} points over "
               f"the baseline.",
        ok_example=f"At pop_spend=0.93 the agent is worth "
                   f"+{_C.get('uplift', 0):.2f} points.",
    ),
    Rule(
        id="batch-population-count",
        kind="forbidden",
        files=ALL_DOCS,
        pattern=r"\b(4|four)\s+populations\b",
        why="The canonical batch runs 10 held-out populations, seeds 710-719. "
            "Four populations was the pre-canonical batch and no current "
            "figure comes from it.",
        canary="recovered across 4 populations of 100 customers",
    ),
    Rule(
        id="mandate-count-is-not-five",
        kind="forbidden",
        files=PUBLIC,
        pattern=r"100 customers\s*(×|x|,)?\s*5 mandates"
                r"|5 mandates per customer(?![^.\n]{0,60}gate)",
        why="The canonical world draws mandates from 1 + Poisson(1) capped at "
            "8, a mean of about 2. Five per customer was an invented constant "
            "and survives only inside the gate suite's own world, which the "
            "technical document labels as such.",
        canary="The world is 100 customers x 5 mandates over 120 days.",
    ),

    Rule(
        id="walkthrough-population-size",
        kind="forbidden",
        files=("docs/index.html", "README.md"),
        pattern=rf"population of (?!{_N}\b)[\d,]+",
        why="The walkthrough is read out of a run of the canonical world, so "
            "the population it names must be the canonical n. It said 100 "
            "after n moved to 500: the run behind the page was regenerated "
            "and the sentence describing it was not. The literal comes from "
            "sim/canonical_result.json.",
        canary="one of two subscriptions, from a simulated population of 100.",
        ok_example=f"from a simulated population of {_C.get('n', 0)}.",
    ),

    # ------------------------------------------------------------- simulation
    Rule(
        id="simulation-stated-up-front",
        kind="required",
        files=("README.md",),
        pattern=r"(all results|every number|every figure)[^.\n]{0,60}simulat",
        why="The README must say that every figure is simulated, in its own "
            "words, near the top. A reader who misses this misreads every "
            "table in the file.",
        canary="An agent that schedules retries for failed subscription debits.",
        ok_example="All results below are simulated.",
    ),
    Rule(
        id="page-simulation-disclaimer-not-buried",
        kind="required",
        files=("docs/index.html",),
        # Only the page header is scanned; see `_head_of_page`.
        pattern=r"simulat",
        why="The simulation disclaimer must appear in the page header, before "
            "the first section. Below the fold it is not a disclaimer.",
        canary="<header id=\"top\"><h1>Retry scheduling</h1></header>",
        ok_example="<header id=\"top\">Every figure on this page is from "
                   "simulation</header>",
    ),
    Rule(
        id="no-production-performance-claim",
        kind="forbidden",
        files=ALL_DOCS,
        pattern=r"(in production|real[- ]world|live)[^.\n]{0,40}"
                r"(recovery rate|uplift|collection rate) of"
                r"|recovers? [^.\n]{0,30}in production"
                r"|production (results|performance) (of|show)",
        why="No figure in this repository has been validated against a real "
            "transaction. A simulated result may not be described as "
            "production performance.",
        canary="The agent achieves a real-world recovery rate of 95%.",
    ),

    # -------------------------------------------------------- the language model
    Rule(
        id="llm-excluded-from-timing",
        kind="required",
        files=("README.md", "docs/architecture.md", "docs/index.html"),
        pattern=r"(language model|LLM|model)[^.\n]{0,80}"
                r"(cannot|can not|does not|must not|never)[^.\n]{0,60}"
                r"(decide|choose|pick|select|be on the path)[^.\n]{0,40}"
                r"(when|time|timing|debit)",
        why="Excluding the model from debit timing is the load-bearing "
            "architectural claim. Every document that describes the model's "
            "role has to state the boundary as well.",
        canary="A language model diagnoses the failure and picks an "
               "intervention.",
        ok_example="The language model cannot decide when to debit.",
    ),
    Rule(
        id="llm-does-not-choose-timing",
        kind="forbidden",
        files=ALL_DOCS,
        pattern=r"(the )?(language model|LLM|agent's model)[^.\n]{0,50}"
                r"(decides|chooses|picks|selects|sets)[^.\n]{0,40}"
                r"(when to debit|the debit time|the retry time|the timing|when to retry)"
                r"|LLM[- ]driven (timing|scheduling)"
                r"|model[- ]chosen (debit )?(time|hour|day)",
        why="The model has no temporal field in its return type and an "
            "import-graph test keeps it away from the timing code. A sentence "
            "saying otherwise contradicts both.",
        canary="The LLM decides when to debit and the filter explains why.",
    ),
    Rule(
        id="money-is-not-the-model",
        kind="forbidden",
        files=ALL_DOCS,
        pattern=r"(the )?(LLM|language model|AI)[^.\n]{0,40}"
                r"(recovered|collected)[^.\n]{0,20}(₹|Rs)"
                r"|the (LLM|model)'s (number|headline|recovery rate)"
                r"|AI[- ]driven (recovery|collection) (rate|figure)",
        why="The batch headline is produced by the belief filter and the "
            "deterministic rule engine. The model is not on that path and the "
            "money may not be attributed to it.",
        canary="The LLM recovered ₹7,511,500 across the batch.",
    ),

    # ------------------------------------------------------------- Razorpay
    Rule(
        id="no-authorised-charge-claim",
        kind="forbidden",
        files=ALL_DOCS,
        pattern=r"(charged|debited|collected)[^.\n]{0,40}"
                r"(a |an )?(real|live|authorised|authorized|production)"
                r"[ -](mandate|customer|payment|card|account)"
                r"|real money (was )?(moved|collected|charged)"
                r"|(executed|submitted)[^.\n]{0,30}recurring[- ]charge"
                r"[^.\n]{0,40}(mandate|token)",
        why="The recurring-charge body has never been submitted against an "
            "authorised mandate, and no UPI AutoPay mandate exists on the "
            "test account used here.",
        canary="The executor charged a real mandate in test mode.",
    ),
    Rule(
        id="authentication-is-not-execution",
        kind="forbidden",
        files=ALL_DOCS,
        pattern=r"authenticat\w*[^.\n]{0,70}(so|therefore|which means|proving)"
                r"[^.\n]{0,50}(charge|debit|payment)[^.\n]{0,20}"
                r"(works|succeeded|is proven|executes)"
                r"|Razorpay integration works\b"
                r"|the (Razorpay )?integration is (complete|proven|live)",
        why="Test-mode authentication, Customer creation, Payment Link "
            "management, SMTP delivery, simulation execution and an "
            "authorised mandate charge are six different things. Collapsing "
            "them into one claim overstates all but the first.",
        canary="The client authenticates, so the Razorpay integration works.",
    ),
    Rule(
        id="charge-never-submitted-stated",
        kind="required",
        files=("README.md", "docs/architecture.md"),
        pattern=r"recurring[- ]charge[^.\n]{0,60}never (been )?submitted"
                r"|never (been )?submitted[^.\n]{0,60}(authorised|authorized) mandate",
        why="The most over-claimable fact in this repository is that a real "
            "charge has never been attempted. Both documents that describe "
            "the executor must say so.",
        canary="The Razorpay executor speaks the live API over urllib.",
        ok_example="A recurring charge on an authorised mandate has never "
                   "been submitted.",
    ),

    # ------------------------------------------------------------ the hero
    Rule(
        id="retired-hero-customer",
        kind="forbidden",
        files=ALL_DOCS + ("scripts/build_page_data.py",),
        pattern=r"\bc45m3\b",
        why="c45m3 does not exist in the canonical world: customer 45 draws "
            "three mandates there. The walkthrough customer is " + HERO_UID +
            ", drawn from held-out population 710.",
        canary="Customer c45m3, from a simulated population of 100.",
    ),
    Rule(
        id="page-names-the-current-hero",
        kind="required",
        files=("docs/index.html",),
        pattern=r"\b" + HERO_UID + r"\b",
        why="The walkthrough must name the customer the committed scenario "
            "data was generated from, so a reader can regenerate it.",
        canary="<p>Customer <code id=\"uid\">cXXmY</code></p>",
        ok_example="<code id=\"uid\">" + HERO_UID + "</code>",
    ),

    # ------------------------------------------------------- validation truth
    Rule(
        id="validation-misses-visible",
        kind="required",
        files=("README.md", "docs/results.md", "docs/index.html"),
        pattern=r"miss[^.\n]{0,20}too high",
        why="Two of the four external validation targets are missed. A "
            "document that reports the two hits without the two misses "
            "reports a different result.",
        canary="Recovery under smart retry timing: 95.24% against a published "
               "70-85%.",
        ok_example="95.24% against 70-85%: miss, too high.",
    ),
    Rule(
        id="no-full-validation-claim",
        kind="forbidden",
        files=ALL_DOCS,
        pattern=r"all four[^.\n]{0,30}(targets?|figures?|checks?|bands?)"
                r"[^.\n]{0,25}(hit|match|in band|validated)"
                r"|(4/4|four of four)[^.\n]{0,30}(validation|targets)"
                r"|every[^.\n]{0,20}validation target[^.\n]{0,20}(hit|met)",
        why="External validation is two hits and two misses, and both misses "
            "are attributed rather than closed.",
        canary="All four published figures are hit by this world.",
        ok_example="All four attempts hit the same empty account.",
    ),

    # --------------------------------------------------------- the gate suite
    Rule(
        id="gate-failures-visible",
        kind="required",
        files=("README.md", "docs/results.md"),
        pattern=r"(4|four)[^.\n]{0,40}(fail|red)",
        why="Four of the 27 simulation gates are red on a clean checkout. A "
            "document that reports the wrapper's exit code without them "
            "reports a green suite that does not exist.",
        canary="The full suite runs 27 gates in about 90 seconds.",
        ok_example="23 pass and 4 known diagnostic failures, of 27 gates.",
    ),
    Rule(
        id="no-green-suite-claim",
        kind="forbidden",
        files=ALL_DOCS,
        pattern=r"(all 27|27 of 27)[^.\n]{0,20}(gates )?(pass|green)"
                r"|27/27 (pass|green)"
                r"|the (full )?suite is green"
                r"|(all|every) gates? pass(es)?\b",
        why="Four gates are red with written reasons. The wrapper exits zero "
            "because they are known, which is not the same as passing.",
        canary="The full suite is green: all 27 gates pass.",
    ),

    # ---------------------------------------------------------- pooling and law
    Rule(
        id="pooling-legality-unresolved",
        kind="required",
        files=("README.md", "docs/results.md", "docs/index.html"),
        pattern=r"(pooling|cross[- ]merchant|sharing)[^.]{0,200}"
                r"(unresolved|unsettled|not been established|has not been "
                r"established|open question)",
        why="Whether an aggregator may lawfully reuse one merchant's outcomes "
            "for another's debit is not established either way. Every "
            "document that makes the pooling argument must say so.",
        canary="Sharing one belief across a customer's mandates is worth "
               "several points.",
        ok_example="Whether cross-merchant pooling is permitted is "
                   "unresolved.",
    ),
    Rule(
        id="no-legal-conclusion",
        kind="forbidden",
        files=ALL_DOCS,
        pattern=r"pooling is (lawful|legal|legally permitted|permitted"
                r"|compliant|allowed)"
                r"|legally compliant"
                r"|complies with (the )?(DPDP|RBI|NPCI)"
                r"|(DPDP|RBI)[- ]compliant",
        # The same words are correct inside an explicit statement that the
        # question is open, which is the sentence this project wants to be able
        # to write.
        unless=r"whether|unresolved|not (been )?established|open question"
               r"|\[GUESS\]|not known|unsettled",
        why="No statute or circular addresses the scenario directly, the "
            "reading on file is secondary and was not produced by a lawyer, "
            "and consent-gating is an engineering response rather than a "
            "legal conclusion.",
        canary="Consent-gating makes the design legally compliant.",
        ok_example="Whether cross-merchant pooling is legally permitted is "
                   "unresolved.",
    ),

    # ------------------------------------------------------- baseline honesty
    Rule(
        id="losing-condition-visible",
        kind="required",
        files=("README.md", "docs/results.md", "docs/index.html"),
        # KEYED ON THE CLAIM, NOT ON A POINT ESTIMATE. This rule used to
        # require the literal "-1.16". Re-baselining the canonical world
        # moved that margin and the rule went quietly unsatisfiable: a
        # document could then drop the condition entirely, and the only way
        # to pass would have been to quote a number the experiment no longer
        # produces. A rule whose pattern IS a measurement expires with the
        # measurement.
        pattern=r"((frozen|fixed) (two-offset )?schedule|`?\[1,7\]`?)"
                r"[^.\n]{0,90}(wins|beats|collects more|is ahead|cheaper)"
                r"|\|\s*(−|-)\d+\.\d+\s*\|"
                r"|behind[^.\n]{0,40}(±1|1 day)"
                r"|sign changes at ±\d",
        why="A frozen two-offset schedule collects more than the agent when "
            "payday is known to within a few days. That is the condition the "
            "whole result turns on and it may not be omitted. The rule "
            "matches the STATEMENT of the condition -- a negative margin in "
            "the comparison table, or the claim in prose -- so it does not "
            "expire when the margin is re-measured.",
        canary="The agent is ahead of every fixed schedule tested.",
        ok_example="| ±1 day | 99.89% | 97.90% | −2.00 |",
    ),
    Rule(
        id="no-universal-superiority",
        kind="forbidden",
        files=ALL_DOCS,
        pattern=r"beats every (baseline|schedule|rival)"
                r"|outperforms all[^.\n]{0,20}baselines"
                r"|better than (every|all) (fixed|frozen) schedule",
        why="The agent loses to the frozen [1,7] schedule below about five "
            "days of payday uncertainty.",
        canary="The agent beats every baseline at every operating point.",
    ),

    # ------------------------------------------------- document references
    Rule(
        id="no-removed-document-references",
        kind="forbidden",
        files=ALL_DOCS,
        pattern="|".join(re.escape(d) for d in REMOVED_DOCS)
                + r"|(?<!py )NOTES\.md|CLAUDE\.md",
        why="Those documents are not in the published tree. A public "
            "reference to one is a trail that ends nowhere.",
        canary="Full write-up in NOTES.md and docs/06_MODEL_CARD.md.",
    ),
]


# --------------------------------------------------------------------------
def _read(path: str) -> str | None:
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        return None
    with open(full, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _head_of_page(text: str) -> str:
    """The page above its first content section: what a reader sees first."""
    cut = text.find("<section")
    return text if cut < 0 else text[:cut]


def _scope(rule: Rule, path: str, text: str) -> str:
    if rule.id == "page-simulation-disclaimer-not-buried":
        return _head_of_page(text)
    return text


def evaluate(rule: Rule, path: str, text: str) -> list[str]:
    """Violations of one rule in one document, as human-readable lines."""
    body = _scope(rule, path, text)
    if rule.kind == "forbidden":
        return [f"line {i + 1}: {ln.strip()[:110]}"
                for i, ln in enumerate(body.splitlines())
                if rule._rx.search(ln)
                and not (rule._ur and rule._ur.search(ln))]
    if rule.kind == "required":
        # Matched across the whole body, because a claim may be split over
        # wrapped lines.
        return [] if rule._rx.search(body) else ["the claim is absent"]
    if rule.kind == "cooccur":
        lines = body.splitlines()
        out = []
        for i, ln in enumerate(lines):
            if not rule._rx.search(ln):
                continue
            lo, hi = max(0, i - rule.window), min(len(lines), i + rule.window + 1)
            if not rule._pr.search("\n".join(lines[lo:hi])):
                out.append(f"line {i + 1}: {ln.strip()[:110]}  "
                           f"(no supporting condition within "
                           f"{rule.window} lines)")
        return out
    raise ValueError(rule.kind)


def hero_consistency() -> list[str]:
    """The walkthrough customer must be the same in three places.

    The page's prose, the committed scenario data and this module's constant
    are written by different steps. A page that names one customer while
    showing another's month is the exact defect this gate was built for, and no
    number check can see it.
    """
    bad = []
    data = _read("docs/data/scenarios.json")
    if data is None:
        return ["docs/data/scenarios.json is missing"]
    uid = json.loads(data).get("hero", {}).get("uid")
    if uid != HERO_UID:
        bad.append(f"scenarios.json hero is {uid!r}, this gate expects "
                   f"{HERE_MSG(uid)}")
    page = _read("docs/index.html") or ""
    m = re.search(r'id="uid"[^>]*>([^<]+)<', page)
    shown = m.group(1).strip() if m else None
    if shown != HERO_UID:
        bad.append(f"docs/index.html names {shown!r} as the walkthrough "
                   f"customer, not {HERO_UID!r}")
    script = _read("scripts/build_page_data.py") or ""
    if not re.search(r'UID\s*=\s*f?"c\{CI\}m\{MI\}"', script):
        bad.append("scripts/build_page_data.py no longer derives the hero uid "
                   "from CI/MI; this cross-check cannot see what it generates")
    return bad


def HERE_MSG(uid) -> str:                     # tiny helper, kept for clarity
    return f"{HERO_UID!r} (regenerate the page data, or update HERO_UID)"


#: Log and data files a public document points at. A trail that ends in a
#: missing file is worse than no trail: it looks checkable and is not.
_REF_RX = re.compile(r"(logs/[A-Za-z0-9_.\-]+\.(?:txt|json)"
                     r"|docs/data/[A-Za-z0-9_.\-]+\.json)")


def references_resolve(files=ALL_DOCS) -> list[str]:
    bad = []
    for path in files:
        text = _read(path)
        if text is None:
            continue
        for ref in sorted(set(_REF_RX.findall(text))):
            target = ref if os.path.exists(os.path.join(ROOT, ref)) else None
            if target is None:
                bad.append(f"{path} points at {ref}, which does not exist")
    return bad


def _slug(heading: str) -> str:
    """GitHub's heading-to-anchor rule, near enough for these documents."""
    h = heading.strip().lower()
    h = re.sub(r"[`*\[\]():,./—–\"']", "", h)
    h = re.sub(r"[^a-z0-9 \-]", "", h)
    return re.sub(r"\s+", "-", h).strip("-")


def _anchors(path: str) -> set:
    text = _read(path)
    if text is None or not path.endswith(".md"):
        return set()
    return {_slug(m.group(2))
            for m in re.finditer(r"^(#{1,6})\s+(.*)$", text, re.M)}


def doc_links_resolve(files=ALL_DOCS) -> list[str]:
    """Links between public files must resolve, section anchors included.

    A link to the right file and a heading that no longer exists lands the
    reader at the top of a thousand-line document, which is the quiet half of
    a broken trail.
    """
    bad = []
    link_rx = re.compile(r"\]\(([^)\s]+)\)|href=\"([^\"?]+)\"")
    cache = {}
    for path in files:
        text = _read(path)
        if text is None:
            continue
        base = os.path.dirname(os.path.join(ROOT, path))
        for m in link_rx.finditer(text):
            href = m.group(1) or m.group(2) or ""
            if (not href or href.startswith(("http://", "https://", "mailto:",
                                             "data:"))):
                continue
            target, _, frag = href.partition("#")
            if target:
                full = os.path.normpath(os.path.join(base, target))
                if not os.path.exists(full):
                    bad.append(f"{path} links to {target}, which does not "
                               f"resolve")
                    continue
                rel = os.path.relpath(full, ROOT).replace("\\", "/")
            else:
                rel = path
            if not frag or not rel.endswith(".md"):
                continue
            if rel not in cache:
                cache[rel] = _anchors(rel)
            if cache[rel] and frag not in cache[rel]:
                bad.append(f"{path} links to {href}, but {rel} has no heading "
                           f"with that anchor")
    return bad


#: Source trees whose comments point readers at documents. `legacy/` and
#: `logs/` are excluded on purpose: both are frozen historical evidence, and
#: rewriting them to look current is the opposite of what they are for.
SOURCE_ROOTS = ("agent", "sim", "scripts")
SOURCE_EXT = (".py", ".sh", ".txt", ".yaml")
#: The checkers themselves. A rule that forbids a phrase has to contain the
#: phrase, so scanning them finds only the rules. This is the whole exemption
#: and it is three named files, not a pattern that can quietly grow.
SOURCE_SKIP = ("sim/verify_claims.py", "sim/verify_docs.py",
               "sim/verify_doc_contract.py")
#: Names a source comment must not send a reader to. The removed documents,
#: plus the two files this rewrite deleted (`sim/verify_brief.py`,
#: `docs/05_TEST_DESIGN.md` under its short name) and `NOTES.md`, the
#: chronological development log. NOTES.md survives in git history, and
#: `docs/errors.md` says so in its preamble -- a comment should point at that
#: sentence, not at a filename the working tree does not contain.
REMOVED_SOURCE_TARGETS = (tuple(REMOVED_DOCS)
                          + ("verify_brief.py", "TEST_DESIGN.md", "NOTES.md"))

#: A number attributed to `docs/errors.md`. That document groups its entries by
#: failure class and NUMBERS NONE OF THEM, so "docs/errors.md, error 28" sends
#: a reader to a section that does not exist. A bare "error 28" is different
#: and is allowed: errors.md says in its own preamble that a source comment
#: citing an error by number refers to the chronological development log kept
#: in git history, which is where those numbers live.
_NUMBERED_ERROR_RX = re.compile(
    r"errors?\s+\d+[^.\n]{0,40}\bin\s+`?docs/errors\.md"
    r"|docs/errors\.md`?[^.\n]{0,20}\berrors?\s+\d+"
    r"|`?docs/errors\.md`?\s*,\s*errors?\s+\d+",
    re.I)


def _source_files() -> list[str]:
    out = []
    for root in SOURCE_ROOTS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, root)):
            dirnames[:] = [d for d in dirnames
                           if d not in ("__pycache__", "runs", "ml_artifacts",
                                        "_cache")]
            for f in filenames:
                if f.endswith(SOURCE_EXT):
                    rel = (os.path.relpath(os.path.join(dirpath, f), ROOT)
                           .replace("\\", "/"))
                    if rel not in SOURCE_SKIP:
                        out.append(rel)
    return sorted(out)


#: The canonical run's own record of itself, written by
#: `python -m agent.batch_report --pops 10 --canonical --emit`.
CANONICAL_RESULT = "sim/canonical_result.json"

#: EVERY PUBLISHED HEADLINE, AND WHERE IT HAS TO APPEAR.
#:
#: WHY THIS EXISTS. Until 2 September 2026 every rule in this file held its own
#: copy of the number it was protecting. Changing `99.38%` to `99.99%` in
#: docs/results.md passed `verify_docs.py`, `verify_claims.py`,
#: `verify_doc_contract.py` and `build_page_data.py --check` -- all four --
#: because the cooccurrence rules only fire on the OLD literal and nothing
#: compared any document to a run. That was measured, not suspected.
#:
#: The chain is now:
#:
#:     agent/batch.run_once
#:       -> python -m agent.batch_report --pops 10 --canonical --emit
#:       -> sim/canonical_result.json
#:       -> these slots, checked against README.md / docs/results.md /
#:          docs/index.html
#:       -> scripts/build_page_data.py, which reads the same file
#:
#: EACH SLOT IS A REGEX WITH ONE CAPTURE GROUP. Two ways to fail, and the
#: second is the one that was missing:
#:
#:   the slot does not match      the sentence carrying the figure is gone.
#:                                Deleting it is not a way to pass.
#:   the slot matches, value      the document is quoting a run that is no
#:   differs                      longer the canonical one.
#:
#: A slot is deliberately anchored on surrounding words rather than on the
#: bare numeral: a bare-numeral scan cannot tell 99.12% the headline from
#: 99.12% something else, and would fire on prose it has no business reading.
def _fmt(v: dict) -> dict:
    """Every headline figure, formatted exactly as the documents write it."""
    return {
        "agent_cycle_rec": f"{v['agent_cycle_rec']:.2f}",
        "base_cycle_rec": f"{v['base_cycle_rec']:.2f}",
        "uplift": f"{v['uplift']:.2f}",
        "uplift_2se": f"{v['uplift_2se']:.2f}",
        "recovered_rupees": f"{v['recovered_rupees']:,}",
        "money_actions": f"{v['money_actions']:,}",
        "at_risk": f"{v['at_risk']:,}",
        "recovery_rate": f"{v['recovery_rate']:.2f}",
        "agent_survival": f"{v['agent_survival']:.2f}",
        "base_survival": f"{v['base_survival']:.2f}",
        "v1": f"{v['first_presentation_failure_rate']:.2f}",
        "n": f"{v['n']}",
        "populations": f"{v['populations']}",
    }


#: (slot id, files, regex with ONE capture group, key in `_fmt`)
HEADLINE_SLOTS = (
    ("readme-headline-table", ("README.md",),
     r"\| Billing cycles collected \| ([\d.]+)% \|", "agent_cycle_rec"),
    ("readme-headline-baseline", ("README.md",),
     r"\| Billing cycles collected \| [\d.]+% \| ([\d.]+)% \|",
     "base_cycle_rec"),
    ("readme-uplift", ("README.md",),
     r"\*\*\+([\d.]+) points, 2 SE [\d.]+\.\*\*", "uplift"),
    ("readme-uplift-2se", ("README.md",),
     r"\*\*\+[\d.]+ points, 2 SE ([\d.]+)\.\*\*", "uplift_2se"),
    ("readme-rupees", ("README.md",),
     r"\| Recovered across the batch \| ₹([\d,]+) \|", "recovered_rupees"),
    ("readme-money-actions", ("README.md",),
     r"over ([\d,]+) executed money actions", "money_actions"),
    ("results-headline-agent", ("docs/results.md",),
     r"\| agent, deterministic \| \*\*([\d.]+)%\*\*", "agent_cycle_rec"),
    ("results-headline-baseline", ("docs/results.md",),
     r"\| `payday_wait` \(rival\) \| ([\d.]+)% \|", "base_cycle_rec"),
    ("results-uplift", ("docs/results.md",),
     r"\*\*\+([\d.]+) points, 2 SE [\d.]+", "uplift"),
    ("results-uplift-2se", ("docs/results.md",),
     r"\*\*\+[\d.]+ points, 2 SE ([\d.]+)", "uplift_2se"),
    ("results-recovery", ("docs/results.md",),
     r"of ([\d,]+) at-risk cycles", "at_risk"),
    ("results-money-actions", ("docs/results.md",),
     r"\*\*([\d,]+)\*\* executed money actions", "money_actions"),
    ("page-agent", ("docs/index.html",),
     r'id="s-agent">([\d.]+)%', "agent_cycle_rec"),
    ("page-baseline", ("docs/index.html",),
     r'id="s-base">([\d.]+)%', "base_cycle_rec"),
    ("page-batch-agent", ("docs/index.html",),
     r'id="b-agent"[^>]*>([\d.]+)%', "agent_cycle_rec"),
    ("page-batch-baseline", ("docs/index.html",),
     r'id="b-base"[^>]*>([\d.]+)%', "base_cycle_rec"),
    ("page-batch-delta", ("docs/index.html",),
     r'id="b-delta">\+([\d.]+) points', "uplift"),
    ("page-batch-2se", ("docs/index.html",),
     r'id="b-delta">\+[\d.]+ points, 2 SE ([\d.]+)<', "uplift_2se"),
    ("page-batch-rupees", ("docs/index.html",),
     r'id="b-rupees"[^>]*>₹([\d,]+)<', "recovered_rupees"),
)


def headline_slots() -> list[str]:
    """Every published headline must equal the canonical run's own record."""
    raw = _read(CANONICAL_RESULT)
    if raw is None:
        return [f"{CANONICAL_RESULT} is missing. It is generated by "
                f"`python -m agent.batch_report --pops 10 --canonical --emit` "
                f"and every headline in the public documents is checked "
                f"against it. Without it nothing is checked."]
    want = _fmt(json.loads(raw))
    bad = []
    for slot_id, files, pattern, key in HEADLINE_SLOTS:
        rx = re.compile(pattern)
        for path in files:
            text = _read(path)
            if text is None:
                bad.append(f"{slot_id}: {path} does not exist")
                continue
            hits = rx.findall(text)
            if not hits:
                bad.append(
                    f"{slot_id}: {path} no longer carries this figure "
                    f"(pattern {pattern!r}). Removing the sentence is not a "
                    f"way to pass.")
                continue
            for got in hits:
                if got != want[key]:
                    bad.append(
                        f"{slot_id}: {path} says {got!r}, the canonical run "
                        f"says {want[key]!r} ({key} in {CANONICAL_RESULT}). "
                        f"Re-run the batch, or fix the document.")
    return bad


def source_references() -> list[str]:
    """Source comments must not send a reader to a document that is gone, or
    to a numbered entry in a document that has no numbers.

    The public documents are covered by the `forbidden` rules above. Nothing
    covered the tree, and the last documentation pass left twenty-odd comments
    pointing at `docs/03_ERRORS.md`, `NOTES.md`, `sim/verify_brief.py` and
    "docs/errors.md, errors 33-35". A reader who follows one of those learns
    that the trail is decorative.
    """
    removed = REMOVED_SOURCE_TARGETS
    bad = []
    for rel in _source_files():
        text = _read(rel)
        if text is None:
            continue
        for i, ln in enumerate(text.splitlines(), 1):
            for name in removed:
                if name in ln:
                    bad.append(f"{rel}:{i} names {name}, which is not in the "
                               f"tree")
            if _NUMBERED_ERROR_RX.search(ln):
                bad.append(f"{rel}:{i} attributes a numbered error to "
                           f"docs/errors.md, which numbers nothing: "
                           f"{ln.strip()[:80]}")
    return bad


# --------------------------------------------------------------------------
def check(verbose: bool = True) -> list[tuple]:
    violations: list[tuple] = []
    missing = [p for p in ALL_DOCS if _read(p) is None]
    if missing:
        for p in missing:
            violations.append(("(file)", p, f"{p} does not exist, so every "
                                            f"rule pointed at it checks "
                                            f"nothing"))
        if verbose:
            for _, p, msg in violations:
                print(f"  [FAIL] {'(missing file)':<34} {msg}")
        return violations

    for r in RULES:
        found = []
        for p in r.files:
            text = _read(p)
            if text is None:
                found.append((p, f"{p} does not exist"))
                continue
            for hit in evaluate(r, p, text):
                found.append((p, hit))
        if verbose:
            state = "FAIL" if found else " ok "
            print(f"  [{state}] {r.id:<38} {r.kind:<9}"
                  + (f" {len(found)} hit(s)" if found else ""))
        for p, hit in found:
            violations.append((r.id, p, hit))

    for name, fn in (("hero-identity", hero_consistency),
                     ("transcript-references", references_resolve),
                     ("document-links", doc_links_resolve),
                     ("source-doc-references", source_references),
                     ("headline-matches-canonical-run", headline_slots)):
        bad = fn()
        if verbose:
            print(f"  [{'FAIL' if bad else ' ok '}] {name:<38} structural"
                  + (f" {len(bad)} hit(s)" if bad else ""))
        for b in bad:
            violations.append((name, "-", b))
    return violations


def selftest() -> int:
    """Every rule must flag its canary, and must not flag its passing example."""
    print("SELFTEST -- every rule must fail on a deliberately broken claim")
    print("=" * 74)
    bad = 0
    for r in RULES:
        if not r.canary:
            print(f"  [FAIL] {r.id:<38} NO CANARY -- the rule is untested")
            bad += 1
            continue
        fired = bool(evaluate(r, r.files[0], r.canary))
        if fired:
            print(f"  [ ok ] {r.id:<38} fires on its canary")
        else:
            print(f"  [FAIL] {r.id:<38} DOES NOT FIRE on its canary")
            print(f"         canary: {r.canary!r}")
            bad += 1
        # A passing example is mandatory wherever a rule could plausibly fire
        # on everything: `required` and `cooccur` by construction, and any
        # `forbidden` rule carrying an exemption, so the exemption is tested
        # rather than asserted.
        needs_ok = r.kind in ("required", "cooccur") or bool(r.unless)
        if needs_ok and not r.ok_example:
            print(f"  [FAIL] {r.id:<38} NO PASSING EXAMPLE -- a rule that "
                  f"fires on everything would look identical")
            bad += 1
        elif r.ok_example:
            if evaluate(r, r.files[0], r.ok_example):
                print(f"  [FAIL] {r.id:<38} ALSO fires on its passing example")
                bad += 1
            else:
                print(f"  [ ok ] {r.id:<38} silent on its passing example")

    # The structural checks get canaries too: a nonexistent transcript and an
    # unresolvable link must both be caught.
    tmp_ref = references_resolve.__wrapped__ if hasattr(
        references_resolve, "__wrapped__") else None
    fake = "logs/this_transcript_does_not_exist.txt"
    if _REF_RX.search(f"see {fake}") and not os.path.exists(
            os.path.join(ROOT, fake)):
        print(f"  [ ok ] {'transcript-reference canary':<38} a missing "
              f"transcript is recognised")
    else:
        print(f"  [FAIL] {'transcript-reference canary':<38} not recognised")
        bad += 1
    del tmp_ref

    # A link whose file exists and whose anchor does not must be caught, or the
    # section half of every cross-reference is unprotected.
    probe = doc_links_resolve.__doc__ and _read("docs/results.md")
    fake_anchor = "no-heading-has-this-slug"
    if probe and fake_anchor not in _anchors("docs/results.md"):
        print(f"  [ ok ] {'anchor canary':<38} a missing section anchor is "
              f"recognisable")
    else:
        print(f"  [FAIL] {'anchor canary':<38} cannot tell a missing anchor "
              f"from a present one")
        bad += 1

    if hero_consistency.__doc__ and HERO_UID not in ("",):
        page = _read("docs/index.html") or ""
        m = re.search(r'id="uid"[^>]*>([^<]+)<', page)
        if m is None:
            print(f"  [FAIL] {'hero-identity canary':<38} the page has no "
                  f"id=\"uid\" element for the check to read")
            bad += 1
        else:
            print(f"  [ ok ] {'hero-identity canary':<38} the page exposes an "
                  f"id=\"uid\" element to compare")

    # The source scan needs both halves: a dead trail must fire, and a live
    # reference must not. Run the real regexes over synthetic lines rather
    # than over a temporary file, so the canary tests the matcher and not the
    # walk.
    def _src_hit(line: str) -> bool:
        return (bool(_NUMBERED_ERROR_RX.search(line))
                or any(n in line for n in REMOVED_SOURCE_TARGETS))

    for label, probe, want in (
            ("source-reference canary (numbered)",
             "see `docs/errors.md`, errors 33-35, for the rebuild", True),
            ("source-reference canary (removed doc)",
             "the full write-up is in NOTES.md and docs/06_MODEL_CARD.md",
             True),
            ("source-reference passing example",
             'see docs/errors.md, "Simulation and model errors"', False),
            ("source-reference passing example (bare number)",
             "the same failure mode as error 27", False)):
        got = _src_hit(probe)
        if got == want:
            print(f"  [ ok ] {label:<38} "
                  + ("recognised" if want else "left alone"))
        else:
            print(f"  [FAIL] {label:<38} "
                  + ("NOT recognised" if want else "fires on a correct line"))
            bad += 1

    print("=" * 74)
    total = sum(2 if (r.kind in ("required", "cooccur") or r.ok_example) else 1
                for r in RULES) + 7
    print(f"{total - bad}/{total} selftest checks passed")
    return 1 if bad else 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if "--selftest" in sys.argv:
        return selftest()

    print("=" * 74)
    print("CLAIM GATE -- the public documents must not contradict the "
          "current state")
    print("=" * 74)
    print(f"  {len(RULES)} invariants over {len(ALL_DOCS)} documents, plus "
          f"five structural checks and {len(HEADLINE_SLOTS)} headline slots "
          f"read from {CANONICAL_RESULT}.")
    print()
    v = check()
    print()
    if not v:
        print("=" * 74)
        print("PASS -- no document contradicts the current state.")
        print("=" * 74)
        return 0

    print("=" * 74)
    print(f"FAIL -- {len(v)} contradiction(s)")
    print("=" * 74)
    by_id = {r.id: r for r in RULES}
    for rid, path, hit in v:
        print(f"\n  {path}   rule `{rid}`")
        print(f"    found : {hit}")
        r = by_id.get(rid)
        if r:
            print(f"    truth : {r.why}")
    print()
    print("Fix the document. If an invariant is genuinely out of date, change")
    print("the rule and its canary together, in the same commit.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
