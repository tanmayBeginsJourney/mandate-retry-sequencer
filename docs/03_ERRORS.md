# 03 — THE SIXTEEN ERRORS

Every one of these made the project look **better** than it was. Read them
before you optimise anything, because they are the shapes you will reintroduce.

They are also the strongest asset for the panel, who explicitly ask what broke
and how you recovered.

Errors 1–6 are from the research phase. Errors 7–10 were found on 28 August
2026 and are the more instructive half, because by then the project already had
a test suite, a pre-registration habit and a rule about treating large
improvements as bugs — and made them anyway.

**Errors 14–16 were found on 28 August 2026 while building `agent/`'s context
layer, and all three are the SAME SHAPE as 1–13: a guardrail that reported
green while measuring nothing.** One went green *underneath a paragraph I had
just written explaining why it was green*. The count is now sixteen and the
pattern has not changed once.

**Errors 11–13 were found earlier the same day, by a reader who had been given
`docs/` and told to check it against `sim/` — and they are the sharpest of the
set.** By then the project had all of the above *plus* ten written-up errors,
a mutation-testing discipline, a doc/code contract checker and a freeze. All
three got through anyway. Every one is a case of a guardrail that reported
green while measuring nothing, which is this project's signature failure and
now has three more instances than it did that morning.

---

**1. Quoted the wrong payment rail's retry schedule.**
Used card retry timings for a UPI problem. Mechanism: pattern-matched a familiar
schedule instead of reading the rail-specific doc.
*Guard:* every constraint traces to a rail-specific source in `01_FACTS.md`.

**2. A simulation whose policy could secretly see the future.**
The scheduler had access to the true balance array. Mechanism: shared state
between world and policy.
*Guard:* `Belief.forecast()` takes no argument carrying ground truth. Test T2
asserts bit-identical output when the future is poisoned.

**3. Calibrated against the wrong real-world metric.**
Tuned the world to match a figure that measured something else.
*Guard:* calibration anchors on per-attempt approval only, and the anchor
sensitivity is a mandatory disclosure (different anchors move the fitted spend
parameter by 2×).

**4. Headline metric inflated by end-of-horizon censoring.**
Mandates unresolved at day 30 were quietly excluded, flattering the result.
*Guard:* billing cycles are counted over the full horizon whether or not the
mandate survived. A dead mandate forfeits its remaining cycles.

**5. A broken oracle that made the system look near-optimal.**
The "clairvoyant" upper bound used index `value × (p_now − p_later)` with both in
{0,1}. If money was available now **and** later, the index was 0, so it **skipped**
— and could defer past the horizon. It skipped 29,161 opportunities where it had
the money. Reported headroom was +1.65 pts; real headroom is ~+7 (old harness),
+18 to +23 (new).
The gate that should have caught it — "oracle approval ≈ 100%" — was **vacuous**:
the oracle only fires when it already knows it will succeed, so 100% approval is
guaranteed whether or not it is correct.
*Guard:* test T1 asserts the oracle weakly dominates every policy. Mutation test
M8 restores the bug deliberately and requires the suite to catch it.

**6. A belief filter that was never checked for calibration.**
Predicted P(success)=0.757 where reality was 0.172. Cause: the spend rate was
hardcoded in `Belief.step` and no longer matched the calibrated world.
**The same hardcode exists in `legacy/sim4.py`**, in the opposite direction — a
plausible mechanical cause of the 18% unresolved mandates that triggered the
previous audit.
*Guard:* test S1 bins predicted probabilities and compares to empirical
frequency. **It currently FAILS.** That is honest, not broken.

**7. Declared an ML model the winner by giving it the only fitted opponent.**
An ML probability engine (`ml_index`) beat the Bayes filter in-distribution by
**+4.03 pts**, which looked like a real architectural finding. It was not. The
GBDT had been fitted to 800 training customers; the Bayes filter was carrying
three values a human had typed in during the research phase and never checked —
a stride-3 payday grid, an invented `exp(-0.10 d)` prior, and a hand-derived
cross-mandate spend correction. Fitting those three **on the same training
populations** flipped the result: the filter now wins in all six worlds by 5–12
points, and a Bayes+ML hybrid is *worse* than the filter alone.
Mechanism: a comparison where one side had been tuned and the other had not,
with nothing in the process asking whether the baseline was fairly configured.
*Guard:* `sim/fit_belief.py` is committed, so the fit is reproducible and its
objective is visible. Gate **S4** holds the result (+11.66 pts, ±1.61) and is
paired with the `ignore_bcfg` mutant, under which the measured gain collapses
to +0.00. **Before comparing anything to a baseline, ask when the baseline was
last fitted.**

**8. A fitted constant that peaked exactly where it was fitted.**
The first fit of the payday prior was selected at `payday_err=7` and produced a
*hard* window of half-width 7 — the same number as the injected payday noise.
It measured **+15.37 pts** and 97.53% recovery, within 2.5 points of a
clairvoyant oracle. Checked against the one parameter the study never varied,
it degraded to **−4.85 at `payday_err=14` — worse than the filter it
replaced** — because a true payday outside the window got prior weight 1e-6 and
could never be recovered by any amount of evidence.
Mechanism: hyperparameter selected at a single operating point, then evaluated
at that same operating point.
*Guard:* `sim/fair_audit.py` sweeps `payday_err` and is expected to be re-run
after any change to `FITTED_BELIEF`. The rule it enforces: **a fitted value
whose benefit peaks at the operating point it was fitted on is tuned to the
harness, not fitted to the population.** The refitted version selects against
the *mean* across `payday_err` and its gain now grows with payday uncertainty
(+2.12 at ±1 to +19.76 at ±14) instead of peaking.

**9. The calibration gate measured a filter that was never the product.**
Gate S1 — "the entire project rests on the belief filter being
well-calibrated" — runs the `portfolio` policy. `portfolio` does not end in
`_pd`, so it carries `w3.Belief`, the **point-estimate** payday filter. The
recommended policy is `solo_shared_pd`, which carries `w3.BeliefPD`. For the
whole life of the project the calibration gate was pointed at something that
does not ship, and conclusions about "the filter's probabilities" were drawn
from it anyway — including one drawn on 28 August, in this repo, from this gate.
Mechanism: a gate named after a concept (`belief calibration`) rather than
after the object it instantiates, so nobody re-checked which object that was.
*Guard:* **S1_PD** applies S1's identical threshold to `w3.BeliefPD` under
`FITTED_BELIEF`. It also fails (ECE 0.026, not monotone). S1 was **not**
repointed — its threshold is pre-registered, and quietly aiming a gate at a
different subject is indistinguishable from moving it until it agrees.

**10. Waited 97 minutes for a process that had done 0.3 seconds of work.**
A script was left running while other work proceeded. It had consumed **0.3 CPU
seconds in 97.8 minutes** and had no child processes: it was hung, not
computing. It was the leftover of an invocation that hit the Windows spawn
`RuntimeError` — `runner.run_jobs` called at module level with no
`if __name__ == "__main__":` guard — after which multiprocessing left
non-daemon threads alive and the interpreter never exited. The trap was already
documented in `sim/runner.py`; it was walked into an hour later in a new file.
Mechanism: elapsed wall time treated as evidence of work.
*Guard:* every script calling `run_jobs` has the guard. Before waiting on any
long job, check CPU, not the clock:
`powershell "Get-Process python | Select-Object Id, CPU, StartTime"`.
A Python process at ~0 CPU is hung. Measured runtime of the script in question:
**71 seconds.**

**11. A mutation test that increments the counter it is graded on.**
Gate M4 asks: does the harness notice a second pending notification? It runs a
mutant and requires `vdetail["pending"]` to move. It moved — 1066 — and M4 has
reported **PASS** for the life of the suite. The 1066 are the mutant's own
writes. `harness.py:610-612` increments `V.pending` *inside the mutation
branch*, and the only independent detector, `if m["pend"] is not None` at
`harness.py:607`, is unreachable because `live` at `harness.py:349-351` has
already filtered `m["pend"] is None`. Instrumented count: **1066 counted, 1066
self-written, 0 independent.** `mutate="represent"` does the same at
`harness.py:333`, but there the independent check still fires (304 of 608), so
M5 binds and merely double-counts.
Mechanism: the mutant is the one piece of code in the suite whose job is to be
adversarial, and it was written by the same hand, in the same file, with write
access to the scoreboard. Every *other* vacuous gate this project found was a
weak assertion; this one is a **compromised witness**. It is also the fourth
instance of "the check and the thing checked share state" — the same shape as
the old `assert violations == 0`, which `live` had already guaranteed.
*Consequence:* two of the five Stage 0 rules — `cap` (error via M1) and
`pending` — have **no working test**. The pending-notification compliance claim
joins the attempt-cap claim in being **banned from the pitch**.
*Guard:* gate **M4B**, added 28 Aug 2026. It parses `sim/harness.py` and fails
if any `V.<field> += 1` sits inside a `mutate == ...` branch. It is static
because the harness returns only the counter, so from outside a self-written
violation and a real one are the same integer. It reports VACUOUS if it ever
flags all five mutants, which is its own falsifiability check. It is **red and
listed in `sim/known_failures.txt`**: the repair is in `harness.py`, which is
frozen, and would move T9's reference. **New rule: a mutant may create illegal
state and nothing else. A mutant that touches a counter is not a test.**

**12. The script that "proves" the fitted constant cannot produce it.**
`w3.FITTED_BELIEF` is the frozen five-value config the whole model rests on.
`w3.py:41-63`, `06_MODEL_CARD.md` §1 and error 7's own guard all say it was
selected by `sim/fit_belief.py`, which is committed *precisely* so the fit is
reproducible. Two of the five values cannot come out of that file:
- **`prior_floor=0.25`** — the string `prior_floor` appears **nowhere** in
  `fit_belief.py`. Every config it evaluates inherits `BeliefPD`'s default
  `1e-6`, i.e. the *hard* window error 8 identifies as the brittle failure.
  The soft floor is described everywhere as "the important part".
- **the objective** — `fit_belief.py:35` sets `PE = 7` and never loops over
  `payday_err`. `w3.py:62`, `belief_fit.json`'s `note` and (until today)
  `fair_audit.py`'s printed output all claim selection against the **mean
  across `payday_err` in {1,3,5,7,10,14}**. That is the stated repair for
  error 8, and it is not in the code.

Measured, identical call signature, eval populations 700–707, `pe=7`: what the
script *can* emit scores **94.98%**; `w3.FITTED_BELIEF` scores **95.57%**.
Separately, `ml_artifacts/belief_fit.json` — the only stored provenance record
— reports **97.53%** for a call signature that measures 95.57%, and that same
97.53% is attributed in error 8 below to the *brittle first fit*.
Mechanism: the guard for errors 7 and 8 was "the fit is reproducible and its
objective is visible". Nobody ran it again. A committed script is not a
reproduction; **only a re-run is a reproduction.**
*What is NOT wrong:* the constant's measured behaviour. Its gain grows from
+2.12 at ±1 day to +19.76 at ±14 (`sim/fair_audit.py`) instead of peaking at
the operating point it was selected on — exactly the property error 8 demands.
**The value is fine; the provenance is fiction.**
*Guard:* a warning header in `sim/fit_belief.py` stating what it cannot
produce, `fair_audit.py`'s false claim removed, and this entry. **Not fixed by
extending the search** — that re-opens a frozen constant eight days before a
deadline, which is error 8's other half. Post-deadline job.

**13. The byte-lock does not cover the thing that ships.**
Gate T9 is sold in `CLAUDE.md` and `06_MODEL_CARD.md` §5 as what makes the
fast/full tier split safe: it hashes "the raw float64 bytes of every predicted
`P(success)` at every dispatch", so it "catches a changed *float* anywhere in
the belief filter". Its reference is 14 policies × 2 operating points, and
**not one of the 28 passes `bcfg`** (`t9_reference.py:54-56`). Every locked
configuration is the **unfitted** `BeliefPD`. The entire fitted-prior branch —
`w3.py:358-367`, where `prior_w`, `prior_day0` and `prior_floor` do their work
— is outside the lock. More broadly, **only 2 of 25 gates run the shipping
configuration** (`tests.py:567` S1_PD, `tests.py:645-647` S4), and one of those
two is red. The gated moat number S2a (+9.53) is also measured unfitted.
Mechanism: the fitted config arrived *after* T9's reference was captured, and
nothing re-asked what the lock covered. A gate named for a property
("output identical") rather than a subject — the same mechanism as error 9.
*Guard:* stated at the top of `06_MODEL_CARD.md` §5 and in `02_RESULTS.md`, so
nobody reads T9's green as covering `FITTED_BELIEF`. The real repair is to add
the fitted configs to `t9_reference.py:POLICIES` and re-capture, which is a
deliberate re-baseline and belongs after 5 September.

---

## Three vacuous gates (all of them, in the old suite)

Worth its own section, because it is the deepest lesson.

| Old gate | Why it could never fail |
|---|---|
| `assert violations == 0` | `live` already filtered `n < cap`; `chosen ⊆ live`. A tautology. |
| peak hours never violated | 3 slots/day made peak windows unrepresentable. |
| oracle approval ≈ 100% | True by construction, correct or not. |

**Every automatic guardrail in the suite passed by construction.**

The rule that came out of it, now in `docs/05_TEST_DESIGN.md`: a gate earns its
place only if you can name, in advance, a concrete broken implementation that
would make it fail. No mutant, no gate.

While building the *new* suite under that rule, the suite immediately caught
three defects in its own author's code — including one gate that was vacuous on
first run, because the mutant reset the same counter the check was reading.

### A fourth, in the NEW suite, found 28 August 2026

That last clause turned out to describe a live defect, not just a war story.
**M4 was vacuous for the same reason and reported PASS** — see error 11. So
the running total of guardrails-that-measured-nothing is:

| gate | suite | why it could never fail |
|---|---|---|
| `assert violations == 0` | old | `live` filtered `n < cap`; `chosen ⊆ live` |
| peak hours never violated | old | 3 slots/day made peak unrepresentable |
| oracle approval ≈ 100% | old | true by construction |
| **M1** attempt cap | **new** | the cap is never the binding constraint |
| **M4** pending notification | **new** | **the mutant increments the counter itself** |

The rule that has to come out of *this* one, because "no mutant, no gate" was
already in force and did not prevent it:

> **A mutant may create illegal state and nothing else.** If the mutation
> branch writes to the scoreboard, the gate is measuring the mutant, not the
> harness. Gate M4B enforces this by parsing the source.

And the meta-lesson, which is the one worth putting on camera: **this project
found its own errors 1–10, and an outside reader found 11–13 in an afternoon
using nothing but `docs/` and `sim/`.** Self-audit has a floor. The three that
survived were all in the *measuring* apparatus, which is exactly where a
self-audit is blindest, because you check your results against your tests and
never check your tests against a stranger.

---

# Errors 14-16 - found 28 August 2026, building `agent/`

All three were found while building the outage-detection context layer. None was
found by a test failing. Two were found by reading numbers that a *passing* test
had printed, and one by an independent checker disagreeing with the component it
was checking.

**14. A monitor that produced a confident wrong number instead of crashing -
and the gate went green on it, underneath my own explanation of why.**
The rail monitor keeps a rolling window of decline outcomes and prunes events
older than `t - window_h`. The agent loop can iterate customer-major (matching
`harness.py:156`, which is what gives bit-exact parity) or time-major (required
for anything cross-customer). Under **customer-major the clock restarts at t=0
for every customer**, so the prune cut goes negative and **nothing is ever
pruned**: the window accumulates one customer's entire 120-day history, every
event of it counted as "in the last 24 hours". OUTAGE latched permanently,
dispatch never resumed, and recovery read **1.97%** against time-major's 79.41%.
It did not crash. It returned a number.
The loop-order gate's mutant required the two orders to *diverge*. They diverged
enormously, the gate went **green**, and I wrote "the monitor genuinely reads
cross-customer state" directly underneath it. That was not the reason they
diverged. The reason was time travel.
*Mechanism:* a gate whose pass condition ("these differ") was satisfied by a
defect rather than by the property it was meant to demonstrate. Same family as
error 9 - a gate named for a property rather than a subject.
*Guard:* `agent/context/rail_monitor.py` raises `NonMonotonicTime` when time
goes backwards, so the misuse is an exception rather than a plausible number.
The gate gained a **third half** asserting it raises, and the mutant was
re-pointed at time-major monitor-on vs monitor-off, which can only differ for
the intended reason. **A component that returns a confident wrong answer is
worse than one that crashes.**

**15. A normal approximation where the expected count was 0.09, which
manufactured outages out of ordinary noise.**
The detector compared observed technical declines in a window against the
`P_TECH = 0.008` base rate using a z-score. With **n=11 attempts in the window
the expected count is 0.088**, so a **single ordinary technical decline** scored
**z = 3.09** - apparently a 1-in-1000 event. The exact probability of seeing at
least one is **8.5%**. Entirely unremarkable. The detector fired **21-26 times**
on a horizon containing 3 outages.
A normal approximation to a Binomial needs `n*p` of roughly 5 or more. Here
`n*p` ranges from **0.09 to 0.8**, so it never applied at any population size
this project runs.
*Mechanism:* a textbook statistic used outside the regime where it is valid, in
a place where being wrong produces *more* alarms - so the failure looked like
sensitivity rather than error. Nothing failed; the transition count was simply
implausible if you looked at it.
*Guard:* `RailMonitor._binom_tail` computes the exact `P(X >= k)` and the
threshold is a derived false-alarm target (`alpha=1e-4`), not a z-score.
Transitions dropped to **6** on a 3-outage horizon (3 enters, 3 exits), and the
false-alarm rate is now **measured** at 0/48 runs rather than assumed.

**16. The detector was structurally silent whenever the response was switched
off - and two pre-registered checks reported HELD on the silence.**
Detection and response were wired through the same `if`: both `assess()` calls
sat inside `if ctx.pause_on_outage:`. The detection-power study deliberately ran
with the response **off**, to measure detection without the response confounding
it. So nothing ever asked the detector anything, and it reported a true-positive
rate of **0.00 at every severity and every population size**, including 44
attempts per day at severity 0.40.
Two pre-registered checks passed on that output: "TPR is non-decreasing in n"
(a sequence of six zeros is non-decreasing) and "TPR < 0.5 at small n" (zero is
less than 0.5). The measurement reported **5/6 predictions held** while
measuring nothing at all.
*Mechanism:* the classic vacuous-gate shape in a new place - a metric whose null
value satisfies the assertion. The ablation switch meant to *isolate* a
mechanism instead *disabled* it.
*Guard:* detection is now assessed whenever the monitor is enabled, regardless
of whether anything acts on the verdict, so the two are independently ablatable.
Both checks carry explicit **vacuity guards** that report VACUOUS rather than
HELD when the detector never fires anywhere. **A pre-registered prediction that
a null result satisfies is not a prediction.**

## The tally after sixteen

| gate | suite | why it could never fail |
|---|---|---|
| `assert violations == 0` | old | `live` filtered `n < cap`; `chosen` was a subset of `live` |
| peak hours never violated | old | 3 slots/day made peak unrepresentable |
| oracle approval ~= 100% | old | true by construction |
| **M1** attempt cap | new | the cap is never the binding constraint |
| **M4** pending notification | new | the mutant increments the counter itself |
| **loop-order mutant** | agent | it diverged for a defect's reason, not the property's |
| **E-DET-2 / E-DET-3** | agent | a null result satisfied the assertion |

**Seven now.** The rule that comes out of errors 14-16, on top of "no mutant, no
gate" and "a mutant may create illegal state and nothing else":

> **State what the metric reads when the thing you are measuring is ABSENT, and
> check that your assertion FAILS on that value.** A prediction satisfied by
> zero is satisfied by a disconnected wire.

### The one that did work: the independent auditor caught a real hole

Not an error in the sixteen, but the counter-example worth recording. In the
outage-pause arms `agent/constraints/auditor.py` reported **45/112/182 `pending`
violations** while `Stage0Gate`'s own counter reported **0**. The auditor was
right: pausing dropped a pending notification without writing anything to the
log, so from the log alone a withdrawn notification is indistinguishable from a
live one, and the next notification for that mandate reads as a second
concurrent one. The audit trail was incomplete.
`NOTIFICATION_CANCELLED` is now emitted wherever a notification is dropped, and
the two counts agree at 0. **The fix went into the trail, never into the
auditor.** This is the first time the deliberate two-implementation split - the
auditor shares no code with the enforcer, enforced by an import-graph gate - has
caught anything, and it caught something real on its first outing.
