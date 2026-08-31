"""THE DOC GATE: stop a retracted claim from outliving its retraction.

    python sim/verify_docs.py            # check
    python sim/verify_docs.py --selftest # prove the checker can fail

WHY THIS EXISTS. On 30 August 2026 a session corrected several claims, wrote in
its handoff note that it had swept every copy, and had missed three -- one of
them on `docs/index.html`, the artifact with the widest audience, where it was
telling judges the project had two untested compliance rules that had already
been fixed. The session AFTER that one found five more survivors of the same
kind, including a second one in that same file's mandate-rule table.

The pattern is not carelessness. It is structural: **a correction lands in the
file the session is editing, and the same sentence survives in four others
because nobody greps.** `sim/verify_brief.py` protects exactly one document
against the code. Nothing protected any document against a claim this project
had already withdrawn.

WHAT THIS IS AND IS NOT. It is a grep with a memory. It cannot tell whether a
sentence is true; it only knows that a specific sentence was retracted on a
specific date, and where that sentence is still allowed to appear. That is a
small check, and it would have caught eight of the defects found across
30 August 2026 -- which is more than any gate in `sim/tests.py` caught that day.

TWO MODES PER RULE.

  * `banned_in`  -- the phrase must not appear AT ALL. Used for the two
    judge-facing artifacts, `README.md` and `docs/index.html`, where the house
    style is to rewrite rather than to strike through: a judge should not have
    to parse a correction to learn what is true.

  * `marked_in`  -- the phrase MAY appear, but only next to a retraction
    marker, because this project deliberately keeps the record of what it used
    to believe. Deleting a retracted sentence loses the error; leaving it
    unmarked leaves a false statement in the repo. Marked is the third option
    and it is the one the house style asks for.

`NOTES.md` is never scanned. It is an append-only log of what was believed at
the time, and rewriting history there is forbidden by rule 8.

ADDING A RULE. When you retract a claim, add it here in the same commit. The
`why` field is not decoration -- it is what the next reader needs in order to
fix a hit rather than silence it. A rule with no `why` is a tripwire nobody can
act on.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Text that means "this sentence is on the record as withdrawn". Checked on
#: the hit's own line and within a window either side, because the house style
#: puts the marker in the paragraph, not always in the sentence.
MARKERS = (
    # explicit strike-through, and the words this project uses for a withdrawal
    "~~", "retracted", "resolved", "superseded", "withdrawn", "corrected",
    "no longer true", "was false", "are now false", "is now false",
    "kept as the record", "kept struck through", "was wrong", "overclaim",
    "used to", "earlier revision", "previously", "until 30 aug",
    "refuted", "does not survive", "claim under test",
    "until 30 august", "fixed 30 aug", "fixed 30 august", "✅",
    # rule 6 of CLAUDE.md quotes the retired numbers in order to ban them
    "is **dead**", "retired number", "never quote", "stale text",
)
MARKER_WINDOW = 8          # lines either side

#: Judge-facing. A correction here is a rewrite, never a strike-through.
PUBLIC = ("README.md", "docs/index.html")
#: Everything else that carries claims. NOTES.md is deliberately absent.
INTERNAL = ("CLAUDE.md", "docs/00_HANDOFF.md", "docs/01_FACTS.md",
            "docs/02_RESULTS.md", "docs/03_ERRORS.md", "docs/04_BUILD_PLAN.md",
            "docs/05_TEST_DESIGN.md", "docs/06_MODEL_CARD.md",
            "docs/07_AGENT_BRIEF.md", "docs/08_ARCHITECTURE.md")


@dataclass
class Retraction:
    id: str
    pattern: str                       # regex, IGNORECASE
    why: str                           # what is true now
    retracted_on: str
    banned_in: tuple = PUBLIC
    marked_in: tuple = INTERNAL
    _rx: re.Pattern = field(init=False, repr=False, default=None)

    def __post_init__(self):
        self._rx = re.compile(self.pattern, re.IGNORECASE)


RETRACTIONS = [
    Retraction(
        id="stage0-untested",
        pattern=r"(two of the (five|5) )?(stage 0|mandate) rules? .{0,40}no working test"
                r"|no working test.{0,60}(stage 0|mandate rule)"
                r"|two of the five stage 0 rules",
        why="All five Stage 0 rules have had a working test since 30 Aug 2026. "
            "M1 runs its mutant at cap_override=2 and the pending/represent "
            "mutants no longer grade themselves. The suite has 0 vacuous gates.",
        retracted_on="2026-08-30",
    ),
    Retraction(
        id="model-frozen",
        pattern=r"the model is frozen|sim/ is frozen|the freeze (is|remains) in (force|effect)",
        why="The freeze was LIFTED on 30 Aug 2026. sim/ is open and the world "
            "model has been the main line of work since. Tag `model-frozen` "
            "still marks the 28 Aug state.",
        retracted_on="2026-08-30",
    ),
    Retraction(
        id="bandit",
        pattern=r"bandit polic|the bandit\b|bandit (decides|chooses)",
        why="w3.index_score is a one-step lookahead in the STYLE of a Whittle "
            "index. No exploration/exploitation trade, no learned index, no "
            "indexability proof. Call it a belief filter plus an index rule.",
        retracted_on="2026-08-30",
    ),
    Retraction(
        id="no-request-ever-sent",
        pattern=r"no request has ever been sent|never sent a byte"
                r"|nothing in it has ever talked to razorpay"
                r"|have never been sent",
        why="scripts/razorpay_ladder.py talks to the live API. The client "
            "also AUTHENTICATES: rung 4 takes an HTTP 200 on GET /v1/payments "
            "with a rzp_test_ key. What remains true is narrower and is the "
            "only form allowed -- Razorpay has never read a recurring-charge "
            "BODY, because that endpoint charges a stored token and no "
            "authorised mandate exists. Check logs/razorpay_ladder.json for "
            "which rungs actually ran before writing any of this down.",
        retracted_on="2026-08-30",
    ),
    Retraction(
        id="headline-one-parameter",
        pattern=r"conditional on (one|a single) parameter",
        why="The headline is conditional on TWO: payday_err AND pop_spend. The "
            "uplift runs +3.51 to +36.43 across world hardness and is +6.29 at "
            "pop_spend=0.80.",
        retracted_on="2026-08-29",
    ),
    Retraction(
        id="six-red-gates",
        pattern=r"six (gates are red|of twenty-five|of 25)"
                r"|(6|five|5) (FAIL|red).{0,30}(1|one) VACUOUS"
                r"|25 gates, (5|6) FAIL",
        why="Four gates are red on a clean checkout and ZERO are vacuous, since "
            "M1 and M4B were repaired on 30 Aug 2026. The four are S1, S1_PD, "
            "S2b and S2_LEGACY.",
        retracted_on="2026-08-30",
    ),
    Retraction(
        id="only-3-of-25-shipping",
        pattern=r"[Oo]nly [23] of 25 gates"
                r"|[Oo]nly [23]/25 gates"
                r"|Three of 25 gates now run",
        why="Coverage expanded 31 Aug 2026: S1_PD, T6_PD, S2a_PD, S4, and T9 "
            "(own/pooled/coordinated under FITTED_BELIEF) run the shipping "
            "filter. T1/T7/T8 include those policies. Do not restate the old "
            "coverage number as current. Historical mentions in error 13 must "
            "sit next to a retraction marker.",
        retracted_on="2026-08-31",
    ),
    Retraction(
        id="retired-headline",
        pattern=r"41\.7\s*%.{0,12}76\.3\s*%|76\.3\s*%.{0,12}41\.7\s*%"
                r"|\+\s*5\.4\s*(pts|points).{0,30}pool"
                r"|\+\s*1\.5\s*[-–]\s*2\.1\s*(pts|points)",
        why="CLAUDE.md rule 6. These came from a simulation with three vacuous "
            "gates and a broken oracle. Current numbers live only in "
            "docs/02_RESULTS.md.",
        retracted_on="2026-08-27",
    ),
    Retraction(
        id="readme-150-lines",
        pattern=r"under 150 lines",
        why="The README is ~500 lines since the 29 Aug rewrite, on purpose: it "
            "SHOWS command output rather than describing it. A line count in a "
            "document is a staleness generator and should not be reintroduced.",
        retracted_on="2026-08-30",
    ),
    # ---- WITHDRAWN 30 August 2026, later the same day, and this comment is
    # the record rather than a deletion.
    #
    #   id="pooling-already-consent-gated"
    #   pattern=r"(also reports|reports) the non-pooled configuration"
    #           r"|treats pooling as consent-gated"
    #   why="W9 is UNBUILT. The project does not report the non-pooled
    #        configuration and does not consent-gate pooling."
    #
    # THE CLAIM BECAME TRUE. W9 shipped: `BeliefBook` takes
    # `pooling={"all","none","consented"}`, the non-pooled configuration is
    # measured at two calibrations (`agent/tests/test_pooling_consent.py`), and
    # both are reported. A rule that bans an accurate sentence is worse than no
    # rule, because the only way past it is to write something less true.
    #
    # This is the one direction this file has to be able to move in. A
    # retraction list that can only grow eventually forbids the truth. The
    # protocol in this module's docstring was followed: the reasoning is in
    # NOTES.md, 30 August 2026, in the same commit that removed the rule.
    Retraction(
        id="stale-error-count",
        # Every count this project has ever published, so a superseded one
        # cannot sit unmarked. The CURRENT count is deliberately absent: a rule
        # that has to be edited every time the thing it guards changes is a
        # rule that will be wrong between edits.
        pattern=r"twenty-(six|seven|eight|nine) errors|thirty errors"
                r"|2[6-9] errors|30 errors|31 errors"
                r"|(THE )?TWENTY-(SIX|SEVEN|EIGHT|NINE) ERRORS"
                r"|(THE )?THIRTY ERRORS",
        why="The error count changes as errors are found, and every document "
            "that states one goes stale together. docs/03_ERRORS.md is the "
            "source of truth -- read the tally at the END of that file and "
            "propagate it everywhere in the same commit. A superseded count "
            "may stay as a marked record; it may not stay live.",
        retracted_on="2026-08-30",
    ),
    Retraction(
        id="both-misses-one-cause",
        pattern=r"both (validation )?misses (share|have) (one|a single|the same) cause"
                r"|(one|a single) cause .{0,30}both misses",
        why="V5 and V7 have TWO different causes. V5 is insolvency (W2); V7 is "
            "the due-date/payday offset (W6). Asserted as one cause, checked, "
            "corrected on 30 Aug 2026.",
        retracted_on="2026-08-30",
    ),
    Retraction(
        id="llm-score-is-a-floor",
        pattern=r"10/21 may be a floor|may be a floor|scores? .{0,20}may be a floor",
        why="SWEPT 30 Aug 2026. `low` is the BEST of the three permitted "
            "settings on the ambiguous set: 10/21 low, 7/21 high, 9/21 max "
            "(and max's row is the rule engine -- 32 of 50 calls hit the token "
            "cap and fell back). 10/21 is not a floor. The sharper caveat that "
            "replaced it: at `high` the model LOSES to the rule engine.",
        retracted_on="2026-08-30",
    ),
    Retraction(
        id="nothing-to-diagnose",
        pattern=r"nothing (in the world )?to diagnose"
                r"|because there is nothing to diagnose",
        why="TESTED 30 Aug 2026 and refuted. With the decline taxonomy ON and "
            "terminal codes everywhere, the LLM arm is 87.39% against the "
            "deterministic 88.54% -- behind, not ahead. It is not a sufficient "
            "explanation for the flat result. What IS true: the 150-call cap "
            "leaves the arm 93.3% fallback, so the test cannot detect a small "
            "effect either way.",
        retracted_on="2026-08-30",
    ),
    Retraction(
        id="w7-moves-three-targets",
        pattern=r"mov(es|e) three validation targets"
                r"|three validation targets at once",
        why="W7 moves ONE target into range (V3), BREAKS a second (V1), and "
            "diagnoses a third (V7, which it did not move: 41.84% -> 42.78%). "
            "Measured 30 Aug 2026.",
        retracted_on="2026-08-30",
    ),
]


# --------------------------------------------------------------------------
def _marked(lines: list[str], i: int) -> bool:
    lo = max(0, i - MARKER_WINDOW)
    hi = min(len(lines), i + MARKER_WINDOW + 1)
    blob = "\n".join(lines[lo:hi]).lower()
    return any(m in blob for m in MARKERS)


def _scan(path: str, r: Retraction, require_marker: bool) -> list[tuple]:
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        return []
    with open(full, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    hits = []
    for i, line in enumerate(lines):
        if not r._rx.search(line):
            continue
        if require_marker and _marked(lines, i):
            continue
        hits.append((path, i + 1, line.strip()[:120]))
    return hits


def check(verbose: bool = True) -> list[tuple]:
    violations = []
    for r in RETRACTIONS:
        found = []
        for p in r.banned_in:
            found += [(*h, "BANNED") for h in _scan(p, r, require_marker=False)]
        for p in r.marked_in:
            found += [(*h, "UNMARKED") for h in _scan(p, r, require_marker=True)]
        if verbose:
            state = "FAIL" if found else " ok "
            print(f"  [{state}] {r.id:<28} retracted {r.retracted_on}"
                  + (f"   {len(found)} hit(s)" if found else ""))
        for path, ln, text, kind in found:
            violations.append((r, path, ln, text, kind))
    return violations


def selftest() -> int:
    """Every rule must fire on a string it is supposed to catch.

    A doc gate that cannot fail is the vacuous-gate shape this project has hit
    five times. Each rule declares a canary below; the rule must match it.
    A rule with no canary is itself a failure.
    """
    canaries = {
        "stage0-untested": "Two of the five Stage 0 rules have no working test.",
        "model-frozen": "Remember that the model is frozen until 5 September.",
        "bandit": "The bandit policy decides when to debit.",
        "no-request-ever-sent": "No API key is used and no request has ever been sent.",
        "headline-one-parameter": "The headline is conditional on one parameter.",
        "six-red-gates": "Six gates are red on a clean checkout.",
        "retired-headline": "Collection went from 41.7% to 76.3% in the study.",
        "readme-150-lines": "README.md the front door. Under 150 lines, on purpose.",
        "stale-error-count": "There are twenty-seven errors in this project.",
        "both-misses-one-cause": "Both misses share one cause, which is insolvency.",
        "llm-score-is-a-floor": "Every score is at low and 10/21 may be a floor.",
        "nothing-to-diagnose": "The LLM does not move the money because there is nothing to diagnose.",
        "w7-moves-three-targets": "W7 is the only item that moves three validation targets.",
    }
    print("SELFTEST -- every rule must fire on a sentence it is meant to catch")
    print("=" * 74)
    bad = 0
    for r in RETRACTIONS:
        canary = canaries.get(r.id)
        if canary is None:
            print(f"  [FAIL] {r.id:<28} NO CANARY -- the rule is untested")
            bad += 1
            continue
        if r._rx.search(canary):
            print(f"  [ ok ] {r.id:<28} fires")
        else:
            print(f"  [FAIL] {r.id:<28} DOES NOT FIRE on its own canary")
            print(f"         canary: {canary!r}")
            bad += 1
    # And the marker logic must actually suppress a marked hit.
    lines = ["~~Two of the five Stage 0 rules have no working test.~~",
             "RESOLVED 30 August 2026."]
    if _marked(lines, 0):
        print(f"  [ ok ] {'marker suppression':<28} a struck-through hit is allowed")
    else:
        print(f"  [FAIL] {'marker suppression':<28} markers are not being seen")
        bad += 1
    # ...and must NOT suppress an unmarked one.
    if not _marked(["Two of the five Stage 0 rules have no working test."], 0):
        print(f"  [ ok ] {'marker strictness':<28} an unmarked hit is still a hit")
    else:
        print(f"  [FAIL] {'marker strictness':<28} everything looks marked")
        bad += 1
    print("=" * 74)
    print(f"{len(RETRACTIONS) + 2 - bad}/{len(RETRACTIONS) + 2} selftest checks passed")
    return 1 if bad else 0


def main() -> int:
    # These documents are full of arrows, rupee signs and struck-through text,
    # and the default Windows console codec is cp1252. A gate that crashes on
    # its own findings reports nothing, which is worse than reporting late.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if "--selftest" in sys.argv:
        return selftest()

    print("=" * 74)
    print("DOC GATE -- retracted claims must not outlive their retraction")
    print("=" * 74)
    print(f"  {len(RETRACTIONS)} retractions, {len(PUBLIC)} judge-facing files "
          f"(no strike-throughs allowed), {len(INTERNAL)} internal files "
          f"(marked record allowed).")
    print("  NOTES.md is never scanned: it records what was believed at the time.")
    print()
    v = check()
    print()
    if not v:
        print("=" * 74)
        print("PASS -- no retracted claim is live anywhere it should not be.")
        print("=" * 74)
        return 0

    print("=" * 74)
    print(f"FAIL -- {len(v)} live retracted claim(s)")
    print("=" * 74)
    for r, path, ln, text, kind in v:
        print(f"\n  {path}:{ln}   [{kind}]   rule `{r.id}`")
        print(f"    found : {text}")
        print(f"    truth : {r.why}")
        if kind == "BANNED":
            print("    fix   : REWRITE it. This is a judge-facing artifact and a")
            print("            reader should not have to parse a correction.")
        else:
            print("    fix   : correct it, or keep it as the record and mark it")
            print("            (~~strike through~~, or RETRACTED / RESOLVED /")
            print("            SUPERSEDED within a few lines).")
    print()
    print("Do NOT silence a hit by deleting the rule. If the rule is genuinely")
    print("wrong, say so in NOTES.md and change it there, in the same commit.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
