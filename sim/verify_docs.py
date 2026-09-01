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
    Retraction(
        id="old-headline-4030",
        pattern=r"98\.01|57\.70|\+?40\.30|6,203,060",
        why="The batch headline is 99.37% against payday_wait's 90.29%, "
            "+9.08 pts (2 SE 2.01), Rs 7,496,430, over 10 held-out "
            "populations on the canonical world. The 98.01/57.70/+40.30/"
            "Rs 6,203,060 figures were measured on a world with no steady "
            "state, mandate outflow missing and k fixed at an invented 5 "
            "(NOTES.md errors 33-35, 1 Sep 2026). Reproduce the current one "
            "with `py -3.12 -m agent.batch_report --pops 10 --canonical`.",
        retracted_on="2026-09-01",
    ),
    Retraction(
        id="uplift-curve-3648",
        pattern=r"\+?36\.48|\+3\.52 (to|->|-)|\+2\.81 (to|->|-)",
        why="pop_spend is now an externally derived REGION [0.80, 0.93] -- "
            "one minus the RBI household saving rate -- and the uplift across "
            "it runs +0.93 to +9.08, not +3.52 to +36.48. pop_spend=1.05 is "
            "off the scale entirely. Below 0.90 the world carries too few "
            "at-risk cycles to measure a difference: 2 at 0.80. "
            "logs/w21_conditional_canonical.txt.",
        retracted_on="2026-09-01",
    ),
    Retraction(
        id="crossover-1-to-3",
        pattern=r"crossover (sits |is )?(between|at) .{0,4}(1|one) and .{0,6}(3|three)"
                r"|above or below ~?4 days",
        why="Against the STEELMANNED fixed schedule [1,7] the crossover is "
            "between +/-7 and +/-10 days, not +/-1 to +/-3. `payday_wait` is "
            "not a steelman: it targets the estimated payday on its first "
            "attempt only, then retries daily and burns the NPCI cap in three "
            "days. agent/tests/test_steelman_schedule.py.",
        retracted_on="2026-09-01",
    ),
    Retraction(
        id="prerepair-agent-figures",
        # Written without backslashes on purpose: this shell collapses
        # them in heredocs, and an earlier version of this pattern
        # shipped with a literal backspace byte where a word boundary
        # was meant. The selftest caught it.
        pattern=r"(?<![0-9.])88[.]40[ ]?%|(?<![0-9.])42[.]64[ ]?%|(?<![0-9.])82[.]7[ ]?%|(?<![0-9.])90[.]58[ ]?%|(?<![0-9.])90[.]68[ ]?%|(?<![0-9.])90[.]49[ ]?%|(?<![0-9.])89[.]54[ ]?%|(?<![0-9.])87[.]66[ ]?%|(?<![0-9.])86[.]14[ ]?%",
        why="PRE-REPAIR FIGURES. These are the agent measured under the OLD "
            "belief constants (prior_w=9, prior_floor=0.5, no continuation "
            "value). On the shipped defaults the same measurements are: V5 "
            "95.24%, V7 42.97%, V7 capture 83.4%, and the steelman rows "
            "98.59 / 98.18 / 97.78 / 94.43 / 90.76 / 89.88 across payday_err "
            "1/3/5/7/10/14. Re-measured 1 September 2026 -- "
            "logs/w24_canonical_repaired.txt and "
            "agent/tests/test_steelman_schedule.py. A FIGURE is a current "
            "claim even when the sentence around it carries no stale framing, "
            "which is why this rule is separate from crossover-7-to-10: that "
            "one caught the WORDS and every number beside them stayed live.",
        retracted_on="2026-09-01",
    ),
    Retraction(
        id="crossover-7-to-10",
        pattern=r"crossover (sits |is )?(between|at) .{0,6}(7|seven) and .{0,7}(10|ten)"
                r"|above or below (about )?ten days"
                r"|behind by 9\.17|9\.17 points at|7\.83 at|6\.14 at",
        why="SUPERSEDED 1 September 2026 by W24. The payday prior was refitted "
            "on the canonical world (prior_w 9 -> 5, prior_floor 0.5 -> 0.1) "
            "and the mandate's continuation value was added to the objective "
            "(cycle_value=0.6). On held-out populations 710-719 the margin "
            "against [1,7] is -1.16 at payday_err=1, -0.33 at 3, +1.15 at 5, "
            "+3.55 at 7, +23.83 at 10 and +34.41 at 14, so the CROSSOVER IS AT "
            "+/-5, not between +/-7 and +/-10. The -9.17 / -7.83 / -6.14 "
            "figures are the pre-repair agent. "
            "agent/tests/test_steelman_schedule.py.",
        retracted_on="2026-09-01",
    ),
    Retraction(
        id="action-space-1371",
        pattern=r"1\.371|\+?0\.563 (pts|points)|1\.790 (pts|points)",
        why="On the canonical world the action space is worth +0.498 pts at "
            "120 days, -0.102 at 60 and +0.845 at 180. STOP and ESCALATE are "
            "0.000 and never fire; the whole gain is the last-attempt hold, "
            "which correlates with deaths avoided at r=+0.926. "
            "logs/w17_abl_stop_canonical.txt.",
        retracted_on="2026-09-01",
    ),
    Retraction(
        id="v7-all-cycles-denominator",
        pattern=r"35\.8% of at-risk|mean \*?\*?14\.7 days",
        why="Those are the ALL-CYCLES figures, not the at-risk ones. On "
            "at-risk cycles the due-date-to-money gap averages 9.40 days and "
            "59.8% have money inside ten days. W6 was specified against the "
            "wrong denominator. The measured V7 ceiling is 51.5% and the "
            "agent reaches 42.64%, so it is BELOW the ceiling, not above it.",
        retracted_on="2026-09-01",
    ),
    Retraction(
        id="outage-pause-negative",
        pattern=r"-0\.529|\+0\.058 (pts|points)",
        why="On the canonical world outage detection is worth 0.000 pts at "
            "every severity swept (0.00, 0.15, 0.40, 0.80) for pause, "
            "suppress and both alike, and none is significant. "
            "logs/w17_abl_outage_canonical.txt.",
        retracted_on="2026-09-01",
    ),
    Retraction(
        id="stale-pooling-moat",
        # Bare figures, guarded so a longer number containing them (1.363,
        # 38.34) does not trip the rule. Written without backslash-d for the
        # reason recorded on `prerepair-agent-figures`.
        pattern=r"(?<![0-9.])8[.]34(?![0-9])"
                r"|(?<![0-9.])8[.]46(?![0-9])"
                r"|(?<![0-9.])8[.]20 unfitted"
                r"|(?<![0-9.])3[.]38(?![0-9])"
                r"|(?<![0-9.])3[.]86(?![0-9])"
                r"|(?<![0-9.])4[.]79 (pts|points)"
                r"|(?<![0-9.])1[.]4[8] at 0[.]80"
                r"|(?<![0-9.])1[.]50 at 0[.]80"
                r"|agree to a point",
        why="STALE POOLING MOAT. These were measured with prior_w=9, "
            "prior_floor=0.5 -- the belief constants replaced by W24 on "
            "1 September 2026. S2a_PD is now +7.32 pts (+/-2.02), not +8.34 "
            "(+/-1.36); the agent W9 figure is +6.47 (+/-0.62) at "
            "pop_spend=1.05 and +1.30 (+/-0.42) at 0.80, not +8.46 and +3.38; "
            "half-consent costs 2.77 and 0.57, not 3.86/1.50 or 4.79/1.48. "
            "S2a (unfitted, +9.53 +/-1.81) did NOT move and is not covered "
            "here. S2a_PD and W9 no longer 'agree to a point' -- they are 0.85 "
            "apart, because sim/harness.py prices no continuation value and so "
            "measures the W24 prior without the cycle_value=0.6 it shipped "
            "with. Re-measured in logs/w26_gate_full_moat_remeasure.txt and "
            "logs/w26_w9_pooling_consent_remeasure.txt. A FIGURE is a current "
            "claim: quoting +8.34 next to correct prose is still a false "
            "statement, which is why this rule catches digits and not framing.",
        retracted_on="2026-09-01",
    ),
    Retraction(
        id="stale-action-ablation",
        pattern=r"(?<![0-9.])0[.]498(?![0-9])"
                r"|(?<![0-9.])2[.]064(?![0-9])"
                r"|(?<![0-9.])0[.]651(?![0-9])"
                r"|(?<![0-9.])0[.]532(?![0-9])"
                r"|(?<![0-9.])1[.]363(?![0-9])"
                r"|(?<![0-9.])2[.]889(?![0-9])"
                r"|(?<![0-9.])98[.]81\s?%",
        why="STALE ACTION-SPACE ABLATION. Re-measured on the canonical world "
            "and the SHIPPED belief on 1 September 2026: the whole action "
            "space is +0.136 pts against a 2 SE of 0.205 -- NOT SIGNIFICANT. "
            "Only NUDGE at p=0.25 and p=0.50 clears its interval (+0.353, "
            "+0.387). Degenerate collection is 99.21%, not 98.81%. "
            "logs/w27_abl_action_repaired.txt. Three generations of this "
            "figure are now retired: +1.371 (pre-canonical, see "
            "action-space-1371), +2.064 (pre-canonical, other conditions), and "
            "+0.498 (canonical world, PRE-W24 belief). ESCALATE and STOP are "
            "still worth exactly 0.000 and still never fire -- that is the one "
            "claim here that survived every re-measurement.",
        retracted_on="2026-09-01",
    ),
    Retraction(
        id="outage-exactly-zero",
        pattern=r"0[.]000 (pts|points) at every severity"
                r"|every arm at every severity is 0[.]000"
                r"|0[.]000 points at\s+every",
        why="On the SHIPPED belief the outage ablation is +0.000 at severities "
            "0.00 and 0.15, +0.017 at 0.40 and +0.051 at 0.80 -- none of them "
            "significant. logs/w27_abl_outage_repaired.txt. The CONCLUSION is "
            "unchanged and always has been (acting on outage detection buys "
            "nothing this experiment can measure), but the literal zeros were "
            "the pre-W24 belief. Quote the intervals, not the zeros: a column "
            "of exact zeros reads as a stronger claim than the data supports.",
        retracted_on="2026-09-01",
    ),
    Retraction(
        id="stale-shipping-constants",
        # Only as a STATEMENT of what ships. The W24 arrows ("prior_w 9 -> 5")
        # are the record of the change and must stay quotable, so the pattern
        # requires the assignment form and not the arrow form.
        pattern=r"prior_w\s*=\s*9(?![0-9])"
                r"|prior_floor\s*=\s*0[.]5(?![0-9])"
                r"|prior_w\s*=\s*12(?![0-9])"
                r"|prior_floor\s*=\s*0[.]25(?![0-9])",
        why="THE SHIPPING CONSTANTS ARE prior_w=5, prior_floor=0.1 since W24 "
            "on 1 September 2026, plus cycle_value=0.6 in the agent's "
            "objective. docs/06_MODEL_CARD.md published prior_w=9, "
            "prior_floor=0.5 as the shipping block for a full day after the "
            "change -- a document stating constants the code had stopped "
            "carrying. Historical mentions are fine next to a marker; a live "
            "assignment is not. Write the change as an arrow (prior_w 9 -> 5) "
            "when recording it, which this rule deliberately does not match.",
        retracted_on="2026-09-01",
    ),
    Retraction(
        id="no-balance-floor",
        pattern=r"no balance floor at zero"
                r"|models no balance floor"
                r"|does not model the balance floor",
        why="WRONG MECHANISM, corrected 1 September 2026 (W24). The floor IS "
            "modelled -- `_shift` piles mass at bin 0 on every drain. What is "
            "broken is the DIFFUSION: the modelled drain rounds to zero bins "
            "for 22 of a cycle's 30 days, and np.convolve(p, k, 'same') "
            "discards the end taps, so 12% of the mass in bin 0 falls off the "
            "bottom daily and renormalisation pushes it back up. The filter "
            "manufactures money. sim/known_failures.txt carries the corrected "
            "attribution and w3.BeliefPD(monotone_drain=True) is the repair.",
        retracted_on="2026-09-01",
    ),
    Retraction(
        id="detection-tpr-one-at-n100",
        # "1.00 at n>=100" in its several spellings, and the severity-0.15 row
        # that fell with it. The bare 0.38 is NOT matched: it is still the
        # correct one-merchant attempts-per-window figure three lines away.
        pattern=r"true-positive rate of 1\.00"
                r"|TPR 1\.00 at n"
                r"|1\.00 at n\s*(>=|\u2265)\s*100"
                r"|1\.00 at severity 0\.40, response OFF",
        why="MEASURED at last on 2 September 2026 and it BROKE. TPR at n=100, "
            "severity 0.40 is 0.75, not 1.00; 1.00 is reached at n=200 and "
            "not before. Pre-registered check E-DET-4 (TPR >= 0.8 at n=100) "
            "broke with it and the record fell 6/6 -> 4/6. The FALSE-ALARM "
            "half held exactly (0 of 48 at severity 0) and may still be "
            "quoted. Mechanism: the repaired filter wastes fewer attempts on "
            "a degraded rail and wasted attempts are the detector's whole "
            "sample. logs/w28_detection_power.txt.",
        retracted_on="2026-09-02",
    ),
    Retraction(
        id="stale-bank-shaped-detection",
        # Guarded so a longer number containing them cannot trip the rule,
        # following `stale-pooling-moat`'s convention. 0.22 is deliberately
        # NOT matched -- it collides with unrelated live figures -- so this
        # rule covers the pooled rate, the best single bank, the 3.5x ratio
        # and the wrong bank identity.
        pattern=r"(?<![0-9.])0[.]78(?![0-9])"
                r"|(?<![0-9.])0[.]41(?![0-9])\s*(\||$|<)"
                r"|3\.5(x|\u00d7) less detectable"
                r"|@upi.{0,30}worst single",
        why="RE-MEASURED on the shipped belief. The bank-shaped outage table "
            "is 0.72 pooled / 0.38 best single / 0.21 mean single, and the "
            "WORST single bank is @oksbi at 0.06, not @upi at 0.09 -- a named "
            "example was wrong, not just a digit. The ratio in the heading is "
            "3.4x. logs/w27_decline_sweep_repaired.txt, which had already "
            "superseded the published figures two hours before anyone "
            "transcribed it.",
        retracted_on="2026-09-02",
    ),
    Retraction(
        id="discount-seven-points",
        pattern=r"discount.{0,80}roughly 7 points"
                r"|moves the headline by roughly 7"
                r"|full spread.{0,20}4\.7 pts",
        why="RE-RUN on the shipped belief 2 September 2026. The discount "
            "sweep spans 3.9 points (91.31% at 1.00 to 95.16% at 0.88), not "
            "4.7 and not the ~7 first published. The argmax also moved off "
            "the shipped 0.92 to 0.88, which retires the old "
            "chosen-on-the-evaluation-set flag and replaces it with a "
            "smaller one. logs/w28_discount_sweep.txt.",
        retracted_on="2026-09-02",
    ),
    Retraction(
        id="stale-headline-two-se",
        pattern=r"2 SE 2\.01(?![0-9])"
                r"|\(2 SE 2\.01, SIG\)"
                r"|(?<![0-9.])8,832(?![0-9])"
                r"|COLLECTED\s+7535",
        why="Stale copies of the headline batch's own transcript. "
            "logs/w24_headline_repaired.txt: 2 SE is 1.84 (att/cyc 1.446), "
            "the batch executes 8,702 money actions, and COLLECTED is 7548 "
            "with CYCLE_CLOSED 307. The README's Quickstart block is a "
            "verbatim paste of that run's stdout and had drifted from what "
            "the command prints.",
        retracted_on="2026-09-02",
    ),
    Retraction(
        id="stale-baseline-at-pe1",
        pattern=r"99\.24%.{0,40}3\.5 points above the agent"
                r"|3\.5 points above the agent"
                r"|67\.58 points ahead",
        why="Two pre-repair figures. On the current page sweep "
            "(logs/w27_page_sweep_repaired.txt) payday_wait collects 99.80% "
            "at payday_err=1 against the agent's 99.81% -- a tie, not 3.5 "
            "points ahead. And the agent is 75.58 points ahead of `naive` at "
            "+/-1, not 67.58 (logs/w25_steelman_final.txt); 67.58 was a "
            "transcription slip, never a measurement.",
        retracted_on="2026-09-02",
    ),
    Retraction(
        id="pausing-reduces-collection",
        pattern=r"[Pp]ausing dispatch during a detected outage <b>reduces collection</b>"
                r"|pausing .{0,30}reduces collection at\s*\n?\s*moderate severity",
        why="The re-measure on the shipped belief found NOTHING significant "
            "at any severity: 0.000 / 0.000 / +0.017 / +0.051, every one "
            "inside its own error bar (logs/w27_abl_outage_repaired.txt). "
            "The page's own footnote three lines below this sentence already "
            "said so; the headline had survived the re-measure because the "
            "number beside it is hydrated from JSON and the sentence is not.",
        retracted_on="2026-09-02",
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
        # Added 1 Sep 2026: this rule shipped on 31 Aug with no canary, so it
        # was an untested tripwire for a day -- the exact shape of defect the
        # selftest exists to catch.
        "only-3-of-25-shipping": "Only 3 of 25 gates run the shipping filter.",
        "retired-headline": "Collection went from 41.7% to 76.3% in the study.",
        "readme-150-lines": "README.md the front door. Under 150 lines, on purpose.",
        "stale-error-count": "There are twenty-seven errors in this project.",
        "both-misses-one-cause": "Both misses share one cause, which is insolvency.",
        "llm-score-is-a-floor": "Every score is at low and 10/21 may be a floor.",
        "nothing-to-diagnose": "The LLM does not move the money because there is nothing to diagnose.",
        "w7-moves-three-targets": "W7 is the only item that moves three validation targets.",
        "old-headline-4030": "The agent collects 98.01% against the baseline's 57.70%, +40.30 pts, Rs 6,203,060.",
        "uplift-curve-3648": "The uplift runs +3.52 to +36.48 across the plausible range.",
        "crossover-1-to-3": "The crossover sits between +/-1 and +/-3 days.",
        "prerepair-agent-figures":
            "Recovery under smart retry timing is 88.40%, the agent reaches "
            "42.64% inside ten days, and it collects 90.58% at +/-1 day.",
        "crossover-7-to-10": "The crossover sits between +/-7 and +/-10 days, "
                             "and the agent is behind by 9.17 points at "
                             "payday_err=1.",
        "action-space-1371": "The action space is worth 1.371 pts at a 120-day horizon.",
        "v7-all-cycles-denominator": "Only 35.8% of at-risk cycles have money inside ten days.",
        "outage-pause-negative": "Pausing on outage measured -0.529 points and is significant.",
        "stale-pooling-moat":
            "Pooling is worth +8.34 pts on the shipping filter and +3.38 at "
            "pop_spend=0.80; the agent measures +8.46, so S2a_PD and W9 "
            "agree to a point, and half consent costs 3.86 and 1.50 at 0.80.",
        "stale-action-ablation":
            "The action space is worth +0.498 pts at 120 days, +1.363 at 60 "
            "and +2.889 at 180, against a policy collecting 98.81% of cycles; "
            "the pre-canonical figure was +2.064 with NUDGE at +0.651 and "
            "+0.532.",
        "outage-exactly-zero":
            "The context layer is worth 0.000 pts at every severity swept.",
        "stale-shipping-constants":
            "FITTED_BELIEF = dict(stride=1, prior_w=9, prior_day0=8.0, "
            "prior_floor=0.5, spend_beta=0.0)",
        "no-balance-floor":
            "The remaining break is structural: the filter models no balance "
            "floor at zero.",
        "detection-tpr-one-at-n100":
            "The agent detects a degraded UPI rail with a true-positive rate "
            "of 1.00 at n>=100.",
        "stale-bank-shaped-detection":
            "A bank-shaped outage is 3.5x less detectable: every bank pooled "
            "0.78, @okaxis 0.41, and @upi is the worst single bank.",
        "discount-seven-points":
            "Sweeping the discount across a plausible range moves the "
            "headline by roughly 7 points.",
        "stale-headline-two-se":
            "The agent is +9.08 points, 2 SE 2.01, over 8,832 money actions, "
            "with COLLECTED 7535 stopping rows.",
        "stale-baseline-at-pe1":
            "At +/-1 day of error payday_wait collects 99.24%, 3.5 points "
            "above the agent, and the agent is 67.58 points ahead of naive.",
        "pausing-reduces-collection":
            "Pausing dispatch during a detected outage <b>reduces collection</b> "
            "at moderate severity, measured across 8 populations.",
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
