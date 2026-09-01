# 03 — THE THIRTY-TWO ERRORS

**Errors 1-18 all made the project look BETTER than it was.** Errors 19-23,
added 29 August 2026, break the streak: 19 and 23 were latent defects that had
not yet flattered anything, and 20 was a decision taken for unrelated reasons
that then moved a headline six points in the flattering direction. **Errors
24-26, added later the same day while building the Razorpay backend, resume
it — all three made something look better than it was, and 25 flattered a
COMPLIANCE claim.** Read all of them before you optimise anything, because
they are the shapes you will reintroduce.

They are also the strongest asset for the panel, who explicitly ask what broke
and how you recovered.

Errors 1–6 are from the research phase. Errors 7–10 were found on 28 August
2026 and are the more instructive half, because by then the project already had
a test suite, a pre-registration habit and a rule about treating large
improvements as bugs — and made them anyway.

**Errors 14–16 were found on 28 August 2026 while building `agent/`'s context
layer, and all three are the SAME SHAPE as 1–13: a guardrail that reported
green while measuring nothing.** One went green *underneath a paragraph I had
just written explaining why it was green*. The count stood at sixteen when
that was written and the pattern has not changed once since.

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
*Guard:* gate **S4** holds the configured-vs-default result (+11.66 pts,
±1.61) and is paired with the `ignore_bcfg` mutant, under which the measured
gain collapses to +0.00. S4 proves the configuration is applied; it does not
prove how the values were selected. `sim/fitted_belief.json` now records that
the reproducible fit selects a different prior.

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
after any change to `FITTED_BELIEF`. The retained shipping configuration's
gain grows with payday uncertainty (+2.12 at ±1 to +19.76 at ±14) instead of
peaking. This is a robustness result, not evidence that the reproducible fit
selected the configuration.

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
✅ **FIXED 30 August 2026, once the freeze lifted.** The `pending` mutant now
drops the pending filter in `live` so a second notification is genuinely
issued and the harness's own check counts it; `represent` no longer
double-writes; M1 runs at `cap_override=2`. Both bans are lifted and M4B is
green.
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
- **`prior_floor=0.25`** (a superseded value; see the note at the end of this
  error) — the string `prior_floor` appears **nowhere** in
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
*What is NOT wrong:* the constant's measured behaviour. After adoption its gain
grows from +2.82 at ±1 day to +18.58 at ±14 (`sim/fair_audit.py`) instead of
peaking at the operating point it was selected on — exactly the property error 8
demands. Provenance now matches the script.
**Re-run 31 August 2026, then adopted.** The fitter now includes `prior_floor`
and selects against mean cycle collection over all six stated `payday_err`
cells. It selected `(stride=1, prior_w=9, prior_day0=8, prior_floor=0.5,
spend_beta=0)`. That configuration was adopted as `w3.FITTED_BELIEF`. The
previous shipping values `(1, 12, 8, 0.25, 0)` remain in `sim/fitted_belief.json`
as `former_shipping`.

⚠️ **BOTH OF THOSE CONFIGURATIONS ARE NOW SUPERSEDED.** W24 re-selected the
shipping prior on the canonical world on 1 September 2026: `prior_w` 9 → 5,
`prior_floor` 0.5 → 0.1, plus `cycle_value` 0 → 0.6 in the agent objective.
`sim/fit_belief.py` structurally could not have found it — its `prior_w` grid
is `(5,7,9,12,15)` and it scores on the pre-canonical world — so
`sim/fitted_belief.json` now records `matches_shipping=false`. The paragraph
above is the record of what error 12 fixed, not a statement of what ships. The train winner scored 94.90% against former 94.53%; on
held-out evaluation the former led 95.29% to 94.94%. Shipping the train winner
is the point of the split. Headline tables were re-measured after adoption.

*Guard:* `sim/fitted_belief.json` commits the full train/evaluation record.
`sim/fit_belief.py --check` verifies that its shipping field matches
`w3.FITTED_BELIEF` and that `matches_shipping` agrees with both configs.

**13. The byte-lock does not cover the thing that ships.**
Gate T9 is sold in `CLAUDE.md` and `06_MODEL_CARD.md` §5 as what makes the
fast/full tier split safe: it hashes "the raw float64 bytes of every predicted
`P(success)` at every dispatch", so it "catches a changed *float* anywhere in
the belief filter". Its reference is 14 policies × 2 operating points, and
**not one of the 28 passes `bcfg`** (`t9_reference.py:54-56`). Every locked
configuration is the **unfitted** `BeliefPD`. The entire fitted-prior branch —
`w3.py:358-367`, where `prior_w`, `prior_day0` and `prior_floor` do their work
— is outside the lock. More broadly, **only 2 of 25 gates run the shipping
configuration** (the coverage when this error was logged; **superseded**
31 August — see below) (`tests.py:567` S1_PD, `tests.py:645-647` S4), and one of those
two is red. The gated moat number S2a (+9.53) is also measured unfitted.
Mechanism: the fitted config arrived *after* T9's reference was captured, and
nothing re-asked what the lock covered. A gate named for a property
("output identical") rather than a subject — the same mechanism as error 9.
*Guard:* stated at the top of `06_MODEL_CARD.md` §5 and in `02_RESULTS.md`, so
nobody reads T9's green as covering `FITTED_BELIEF`. The real repair is to add
the fitted configs to `t9_reference.py:POLICIES` and re-capture, which is a
deliberate re-baseline and belongs after 5 September.

**Resolved 31 August 2026 (morning).** T9 now includes `solo_shared_pd` under
`FITTED_BELIEF` at both existing operating points. A second recapture the same
day, after the new prior was adopted, changed only those two fitted cases.
Three of 25 gates ran the fitted configuration: S1_PD, S4, and T9.
**Superseded the same afternoon.**

**Resolved 31 August 2026 (afternoon).** The remaining coverage gap was the
moat and the lock, not a sentence. S2a_PD gates pooling on `FITTED_BELIEF`
(+7.32 ±2.02 on the shipped constants; it read +8.34 ±1.36 when this entry was
written, under the pre-W24 prior). T6_PD is the k=1 identity on the shipping
filter. T9 locks
own, pooled, and coordinated under `FITTED_BELIEF` at both operating points
(34 configs). T1, T7 and T8 include those policies. Five dedicated gates of
27 run the shipping configuration. Stage 0 mutants stay unfitted on purpose:
they test constraint counters, and changing their prior can make a gate
vacuous.

A working *neutral* extra-update control was also attempted (label-shuffle
and posterior-predictive). Both damaged the filter relative to own, because
this `observe()` is a hard truncation, not a martingale. That is why S2b
stays red. Quote S2a / S2a_PD.

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

## The tally at sixteen — SUPERSEDED, see "The tally after twenty-three" at the end

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

Not one of the numbered errors, but the counter-example worth recording. In the
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

---

# Errors 17-19 — found 29 August 2026, building the detection benchmark

---

**17. An excess-loss metric that rewarded silence, caught by its own gate.**

The detection benchmark reports the agent as excess loss against an oracle that
knows the true outage windows, counted in **detector-hours of disagreement**.
Gate G-1b says the oracle must weakly dominate every statistical detector. It
did not: at severity 0.15 the oracle scored **72.0** hours against the
detectors' 37.5-51.9, and `M-BLIND` — a crippled oracle that never fires at
all — scored **24.0**, better than the real oracle at every severity.

The arithmetic, once looked at. A detector that **never fires** accrues at most
`MISSED` = 4 windows × 6h = **24 hours**, capped by the window length. A detector
that **fires correctly** holds OUTAGE until the next time anything consults it —
hour 8 the following day, up to **18 hours per window**, so up to 72. **Under an
unweighted hour count, silence is cheaper than correctness**, the least
sensitive detector wins, and `min_attempts=16` looked best precisely because it
detected least.

*Mechanism:* the loss's **time base** was wall clock, but the monitor is only
consulted when the loop has work to do. Hours between 14:00 and the next 08:00,
when nobody asks the monitor anything and no dispatch is possible, were being
counted as errors that changed nothing. A metric named for a property
("disagreement with the truth") rather than for the decisions it affects.

*Guard:* **G-1b is kept RED and is NOT repaired.** Repairing a metric after it
returns an inconvenient answer is indistinguishable from moving a threshold
(rule 1), and this repo already keeps S1, S1_PD, S2b and S2_LEGACY red on
exactly that principle. **G-1c** was added beside it — the same dominance claim
counted on **decision-points**, one per day at hour 8, which is the time base the
bandit literature already uses (regret is summed over rounds at which the
algorithm acts). On decision-points the oracle scores 0.00 everywhere and the
detectors 4.00-5.38, and G-1c is what the suite verdict reads.

*The encouraging half:* **the gate found a defect in its own metric**, in the
same session that wrote it, rather than an outside reader finding it months
later. That is the first time on this project.

---

**18. The demo printed compliance violations that never happened.**

`python -m agent.demo` displayed:

```
gate refusals        {'cap': 0, ..., 'pending': 0}
independent recount  {'cap': 24, ..., 'pending': 282}
```

`AuditLog` opens its file in `"a"` mode — deliberately, because append-only is a
property a reader can check by eye — and `demo.py` wrote to the **fixed** paths
`agent/runs/demo_full.jsonl` and `demo_degenerate.jsonl`. So every invocation
appended to the previous one's log, and `auditor.replay`, whose only input is
the file, audited **two concatenated runs as if they were one**: the same
mandate's cycle appears twice, so attempts double against the cap and a
notification from run A reads as concurrent with one from run B.

Verified: the file held **2 distinct `run_id`s**. Replayed whole: `cap 24,
pending 282`. Replayed **per `run_id`: 0 and 0**. The agent was fine; the display
was not.

*Mechanism:* "one log file is one run" was an assumption every reader of a log
makes — `replay` sorts by `seq`, and `seq` restarts at 1 for each run — and
**nothing enforced it**. A fresh clone looked clean on its first run and lied on
its second, which is why nothing caught it. This is error 14's shape: a
component returning a confident wrong number instead of failing.

*Guard:* `agent/audit/log.py:LogFileNotEmpty` makes it an **exception at open
time** anywhere in the repo, and `demo.py` clears its two fixed paths first. The
one legitimate append — `test_stage0_enforces` Half B, which models a rogue
writer inside **one** run — passes `allow_append=True` explicitly. Verified by
running the demo twice in a row: clean both times.

---

**19. A correctness property that lived in the caller instead of the component.**

`RuleBasedDiagnoser` returned **RETRY on a billing cycle that had already
collected** (golden case GC-40). Its first branch tested only for `TECH`, so an
`OK` fell through to the peer-success branch, which fires on
`peer_mandate_success_recent` plus a non-wide band and proposes a second debit.
**Charging a customer twice is the worst outcome this system can produce** —
worse than never collecting, because it costs a refund, a complaint and probably
the mandate.

It was harmless in production only because `agent/loop.py` filters
`not m.collected` out of `live` before it ever calls a diagnoser.

*Mechanism:* a component with a `Protocol` interface was correct only under an
assumption **its single current caller happened to satisfy and nothing stated**.
That is the same shape as every vacuous gate in this document — the check and
the thing checked sharing an assumption neither writes down. A second caller, or
a reordering of `live`, would have shipped a double debit.

*Guard:* fixed **in the component**, as a first branch guarded on
`attempts_used >= 1` so it is correct under both readings of `decline_history`'s
scope (the loop clears it at rollover; the golden-case convention says it may
span cycles). Confirmed the defect was masked rather than latent: **full mode is
unchanged at four populations after the fix.** GC-40 is the anchor case that
keeps it visible — any diagnoser returning RETRY there fails the set outright
whatever it scores elsewhere.

---

# Errors 20-23 — found 29 August 2026, building and running the LLM layer

Four more, and the pattern has still not changed. Two are guardrails that
measured nothing; one is a decision taken on a premise that was true of the
wrong component; one is a hole an *independent checker on a different model*
found in a lexical net we wrote ourselves.

**20 and 22 are the two most expensive shapes in this list for a fresh session
to reintroduce**, because both look like housekeeping while you are doing them.

---

**20. A decision taken on a premise that was true of one component and false of
the other.**

`WAIT` was cut from the action space on three grounds: unreachable from every
branch of `RuleBasedDiagnoser`, exactly one supporting golden case (GC-22), and
measured at approximately zero by the action ablation. All three are true. All
three are statements about **the rule engine**.

They are false of the model. In the first live eval `WAIT` was
`glm-5.3-flash`'s **most-used answer — 11 of 40 registered cases**. Removing it
moved the model's ambiguous-case score from **4/21 to 10/21**: same model, same
cases, same temperature, one word removed from a prompt's list and from an
enum. The change flipped the headline comparison from "the LLM is much worse
than thirty lines of if-else" to "the LLM beats it".

*Mechanism:* a property was measured on the component that had it (the rule
engine, where WAIT was dead code) and generalised to the component that did not
(the model, where it was live and load-bearing). Nothing in the process asked
"dead for whom?". The ablation number that justified it — `~0` — was measured
before a model-backed diagnoser existed, so it could not have been about one.
*This is error 9's shape at the level of a DECISION rather than a gate:* a
finding named after a concept ("WAIT is worth nothing") rather than after the
subject it was measured on.

*What is NOT wrong:* cutting it may still be right. The simpler action space is
defensible and the six points may be an artefact of a model over-reaching for
"do nothing today". The defect is that the decision was taken **without that
being a question**.

*Guard:* both columns — WAIT-in and WAIT-out — are kept side by side in
`02_RESULTS.md` rather than the old one being replaced, so the size of the
effect is visible to anyone who reads the table. GC-22, whose registered answer
is now unreachable, **stays in the denominator**: dropping it would flatter every
arm by removing a case none of them can win, and `agent/eval/cases.py`'s
self-test prints the orphan rather than crashing on it. **New rule: before
removing anything from a shared vocabulary, measure it on every consumer of that
vocabulary, not on the one that made it look dead.**

---

**21. The judge-disagreement count was computed twice, by two pieces of code,
and they disagreed.**

The eval's summary block printed **18 judge-vs-author disagreements**. The
pre-registered check, forty lines later, scored **19**. Both were reading the
same forty judge verdicts.

The cause: the summary built a list `dis` and the check built its own, and both
then tested membership with `row in dis` — where `row` is a dict containing
dataclass instances. That comparison is by value, not identity, so it did not
mean what it looked like it meant. E-JUDGE-2's concentration ratio was therefore
computed against a denominator **the reader never saw**, and the ratio decides
whether the case file's `expert_agreement` flag can be trusted.

*Mechanism:* the same quantity derived independently in two places, which is
this project's signature failure — the auditor-versus-gate split is the
*deliberate* version of it and the whole point is that it is deliberate. This
one was an accident, and it was **in the code doing the checking**, which is
where a self-audit is blindest (see errors 11-13).

*Guard:* `agent/eval/run_eval.py:judge_disagreements()` computes it **once**, keys
by case id rather than by object identity, and both consumers read its result.
Its docstring says why it exists. **New rule: a number printed in a summary and
a number scored in a check must come from the same call, not from two
expressions that look alike.**

---

**22. An LLM wired into a loop that asks for a diagnosis 119,667 times.**

`agent/loop.py` calls `diagnoser.diagnose()` once per **live mandate** per
**decision hour**. The eval exercises fifty fixed cases, so it ran in seconds
and gave no hint of anything. The batch report, at n=100 × k=5 over four
populations and 120 days, asks for **119,667 diagnoses**. Wired to a network
call at 2-8 seconds each, that is days of wall clock and an unbounded bill; the
first attempt was killed after twelve minutes having produced no output, and it
had already made ~165 unplanned paid calls.

*Mechanism:* a component was validated on the harness that exercises it
**cheaply** and then deployed into the loop that exercises it **exhaustively**,
with nothing in between asking how many times it would be called. The eval's
fifty calls and the batch's hundred and nineteen thousand differ by three orders
of magnitude and the interface is identical, so the code gave no warning. Same
family as error 10 — a cost that is obvious once measured and invisible until
then.

*What is NOT wrong:* the fix is not a faster model. No production recovery agent
calls an LLM sixty thousand times a day either; it calls one on the novel cases
and lets rules handle the routine ones. **A bounded call budget IS the design.**

*Guard:* `ModelDiagnoser(max_live_calls=...)` is a hard per-run cap on **network**
calls; cache hits are free and do not count, so it bites on novelty rather than
volume. Every refusal is a logged `LLM_FAILURE` with its reason, and
`batch_report.py` prints the resulting **fallback rate (94.8%)** beside the money
rather than burying it. **The LLM arm of the batch is 95% deterministic and the
report says so — it must never be described as "the LLM's number".**
Two supporting fixes came out of the same incident: the response cache is now
written **as results arrive** (the first live run lost thirty minutes of paid
calls because it saved only at the end), and calls run concurrently with
progress printed, because thirty minutes of silence is indistinguishable from a
hang.

---

**23. The lexical net had a hole, the prompt was coaching it, and a different
model found both.**

`agent/llm/governance.py` exists to catch a merchant-facing rationale that
discloses the customer's financial state. GLM-5.3, judging, flagged two
rationales that `governance.check` had passed:

* *"recent activity on the account indicates **money reached it** recently"*
* *"a recent successful mandate on this account confirms **funds reach it**"*

Both are paraphrases of `peer_mandate_success_recent`, a boolean the `CaseView`
legitimately carries and which `caseview.py` defends on the grounds that it
"names no merchant and no amount". **Restating "another transaction succeeded"
as "this customer has money" converts a transaction fact into a claim about a
person, which is precisely the thing the rule forbids** — and the net had no
pattern for it.

Worse: the diagnoser's own prompt contained the line *"means money reached the
account"*. **We were coaching the phrasing we were failing to catch.**

*Mechanism:* the redaction boundary's guarantee is "the narrative layer cannot
leak a number it was never given", and that guarantee is intact — the model has
no balance. It simply **inferred** one from a field it was allowed to see and
said it out loud. The lexical net was written against the disclosures we
imagined, which were possessive ("their balance") rather than inferential
("funds reach it"). A checker written by the same party that wrote the thing
checked, again.

*Guard:* patterns added for `money/funds/cash reach*`, `is funded`, `has funds`,
`good for it`, `can pay`; the prompt line rewritten to forbid restating the
boolean. **The fix went into governance and into the prompt, never into the
judge** — the same principle as `NOTIFICATION_CANCELLED`, where the fix went into
the audit trail and never into the auditor. Post-fix **7 of 40 rationales fail
governance and all 7 are genuine**, each replaced by `SAFE_FALLBACK` before a
merchant sees it.

*And the judge was not simply right.* Three of its `names_a_time` flags were
**rejected**: it flagged *"our model scores this window highest"*, which is the
exact phrasing `07_AGENT_BRIEF.md` §2 prescribes as compliant. **An independent
checker is a source of hypotheses, not a source of truth**, and adopting its
verdicts wholesale would have broken the approved wording. Accepted on leakage,
rejected on time, both recorded.

---

# Errors 24-26 — found 29 August 2026, building the Razorpay backend

Three more. **24 and 26 made the project look better than it was; 25 made a
COMPLIANCE claim look better than it was**, which is the most expensive of the
three because that claim is on the front of the batch report.

All three were found in a single afternoon of building an integration against a
vendor's published documentation, and all three are the same underlying move:
**asserting something about a thing we had not read.**

---

**24. A coverage check that could not see an invented entry.**

`agent/ports.py:REASON_FAMILY` maps Razorpay's published `error_reason` values
onto our decline families. Gate **R1a** in
`agent/tests/test_razorpay_mapping.py` checks that every reason in Razorpay's
published list has a family, against `agent/execution/razorpay_reasons.txt` —
their list, committed verbatim.

R1a passed. The map contained **`deemed_transaction_unknown`**, which appears
**nowhere in Razorpay's list**. It was typed while writing the table and sat
there, in a structure whose docstring cites a primary source, indistinguishable
from the 110 entries that are real.

*Mechanism:* R1a tests one direction of a two-directional relationship. "Every
reason of theirs is covered by ours" and "every entry of ours came from theirs"
are **different claims**, and only the first had a check. A gate named for a
property — "the map is complete" — that tests completeness and not provenance.
That is error 9's shape and error 13's shape: a gate named after a concept
rather than after the object it is supposed to constrain.

The rule this violates was already written down. Rule 4 says an untagged
factual claim is a rumour and must not go in code comments. A fabricated
identifier inside a table sourced to a document is a rumour that has been given
a citation, which is worse than an untagged one — it *looks* checked.

*Guard:* gate **R1b** computes `set(REASON_FAMILY) - set(published)` and fails
on anything left over. It found the invented key on its first run. Legitimate
extras — there is exactly one, and it exists because Razorpay's own spreadsheet
contains the typo `psp_app_ not_available` with a space in it — must be
declared in `ports.KNOWN_EXTRA_KEYS` **with a written reason**, and **R1b2**
fails if a declared extra has no reason. That list is debt in the same sense as
`sim/known_failures.txt`: adding a line to silence R1b is the same offence as
loosening a threshold.

> **New rule: a check that one set covers another is not a check that the two
> sets are the same.** If a table claims a source, test both directions.

---

**25. "The gate and the auditor agree" is a tautology in the only regime anyone
ever observes it.**

This is the important one, because it is about a claim that is **on the front
page of the deliverable** and in `00_HANDOFF.md`'s headline block.

`python -m agent.batch_report` prints, per Stage 0 rule:

```
                   arm        rule  gate refused  auditor found  agree?
  agent, deterministic        peak             0              0     yes
                             TOTAL             0              0     yes   over 4511 executed money actions
```

and `02_RESULTS.md` presents it as the two-implementation cross-check:
`auditor.py` may not import `rules.py` or `stage0.py`, gate I3 enforces that,
so agreement is supposed to be evidence.

**The two columns are not the same quantity.** `Stage0Gate.refusals` counts
what the gate **stopped**. `auditor.replay` counts violations that **actually
happened**, re-derived from the log. In a clean run both are zero — but they
are zero for *unrelated reasons*: the gate refused nothing because nothing
illegal was proposed, and the auditor found nothing because nothing illegal
occurred. **Two numbers that are both zero because the world was quiet are not
two implementations agreeing.**

Found by accident. `scripts/prove_stage0_refuses.py` deliberately submits a
peak-hour debit, so the gate refuses three times (`peak` at issue, `peak` and
`pending` at dispatch) and the auditor still reports zero. The first draft of
that script printed **"The two agree"** directly underneath `{peak: 2,
pending: 1}` and `{all zero}` — a caption written before its own output was
read. That is error 14's shape exactly: a confident sentence sitting under a
number that contradicts it.

*Mechanism:* a cross-check whose two sides are only comparable **when the
enforcer has already failed**, presented in a display where the enforcer never
fails. The auditor's power is real — it caught the `NOTIFICATION_CANCELLED`
hole when the gate said 0 and it said 45/112/182, and it was right — but that
power is only exercised in the failing regime, and nothing in the normal output
says so. A reader is invited to read "0, 0, agree" as corroboration when it is
a pair of unrelated zeros.

*What is NOT wrong:* the architecture. The auditor genuinely shares no code
with the enforcer, I3 genuinely enforces it, and
`test_stage0_enforces.py` Half B genuinely injects illegal actions below the
gate and catches all five. **The defect is in what the numbers are said to
mean, not in what the code does.**

*Guard:* `prove_stage0_refuses.py` now has a fourth step that moves money
**below** the gate — writing exactly the rows the gate writes on success,
touching no counter — and shows the auditor finding it from the log alone
(`peak: 1`). The script states the distinction in its own output. `README.md`
and this entry state it in prose.

**Resolved 31 August 2026.** `agent/batch_report.py` now labels the quantities
`gate refused` and `illegal executed` and states that zero in both columns is
not agreement. The injected-bypass proof remains the check that exercises the
auditor in the failing regime.

> **New rule: before presenting two numbers as a cross-check, state the regime
> in which they could differ, and check that the display is ever in it.**

---

**26. Asserted a competitor's product had a limitation, without reading their
documentation.**

The outage argument was drafted as: *their* downtime feed is system-wide, *our*
detector is bank-shaped, 2026's incidents were bank-shaped while NPCI reported
the system healthy, therefore we see what they cannot.

**Razorpay's Payment Downtime API is not system-wide.** Its `instrument` object
carries `vpa_handle` — `oksbi`, `ybl` — the **same handle vocabulary** as
`ports.BANK_HANDLES`, and reports `ALL` only when the whole of UPI is affected.
There are `payment.downtime.started` / `.updated` / `.resolved` webhooks, it is
available with test keys, and they document it plainly. **They already publish
bank-scoped downtime.** `[VERIFIED]`, `01_FACTS.md`.

*Mechanism:* **error 1, in a new costume.** Error 1 was quoting the wrong
payment rail's retry schedule by pattern-matching a familiar shape instead of
reading the rail-specific document. This is the same move applied to a
competitor's feature list: a plausible limitation, asserted because it made our
moat argument work, about a product whose documentation is public and was not
opened. The guard that came out of error 1 — "every constraint traces to a
rail-specific source in `01_FACTS.md`" — covers *constraints* and never
covered *competitive claims*, which had no tagging discipline at all.

*What survives, and it is narrower:* their feed measures **their** traffic mix
(their `flow` field enumerates `collect` / `intent` / `in_app`, and nothing in
their documentation says AutoPay mandate execution is what is being measured,
while 99.22% of our attempts land in one hour of the day); `severity` is a
three-valued label and not a rate a scheduler can act on; **a PSP is marked
down only when every handle under it is down**, which is their words and is a
conservative trigger appropriate to a status page and wrong for an actuator;
and we have a measured detection latency and a measured false-alarm rate where
theirs is unstated. The posture is **complement, not replacement**, and the
obvious combined design — their feed as a prior, ours as the likelihood — is
**not built and not measured.**

*Guard:* retracted in `01_FACTS.md` under RETRACTIONS. The four surviving
differences are written into `agent/execution/razorpay_downtime.py`'s module
docstring in the form that can be checked against their docs, and
`agrees_with()` deliberately returns a **label and not a score**, because
turning agreement between two imperfect detectors of two different populations
into an accuracy would be inventing evidence.

> **New rule: a claim about someone else's product is a factual claim and needs
> a source tag like any other.** `01_FACTS.md` now carries them.

---

# Error 27 — found 30 August 2026, building W7

**27. An enrichment parameter that perturbed the stream it was supposed to leave
alone, so a controlled experiment changed two things at once.**

W7 adds transient failures to the world: a temporary hold that blocks the
balance for a few hours and then releases. The rate is swept, and the whole
point of a sweep is that the cells differ **only** in the thing being swept.

The first implementation drew the holds inside `w3.balance_trace` from `rng` —
the generator the money path uses. The draw is `days` values taken before the
spend loop, so at any non-zero rate **every later draw shifted and every
customer's entire balance trace was re-drawn.** Each cell of the sweep was
therefore a different world *plus* holds, not the same world *with* holds.

*How it was caught:* not by a gate. It was caught while building a diagnostic
that needed the set of cycles at risk *only because of a hold* — which requires
the transient world to be the base world plus holds. It is not, so the set
difference was meaningless, and the reason it was meaningless was the defect.
**The measurement that exposed it was being built for a different purpose.**

*What it cost:* V3 at the lowest swept rate moved **39.06% → 40.64%** once the
generators were separated. The published band's ceiling is 40%. So the defect
**flipped a pre-registered prediction from HELD to BROKE**, and without the fix
the project would have recorded 7/8 and a V3 hit from a comparison that was
changing two things at once.

*Mechanism:* the rule this breaks was already written down, twice, in the file
next door. `agent/execution/sim_executor.py` says it for the decline taxonomy
and again for the nudge: *turning enrichment on must not shift a single draw
taken by the money path, or the enriched world would be a DIFFERENT world rather
than the same world with better labels.* `DeclineState` obeys it with its own
generator; the nudge obeys it with `nrng`; W2 and W7 did not. **A convention
enforced by comment in one file and by nothing at all in the next.**

*Guard:* holds now come from a per-customer generator seeded the way
`harness.py:158` seeds `donor_bal`. `p_transient > 0` **without** a `hold_rng`
raises, because a silent fallback to `rng` is exactly the defect. Verified: the
balance array is bit-identical outside held hours and the generator position is
unmoved at both `p=0` and `p>0`.

**Resolved 31 August 2026.** `p_missed_credit` now draws from a separate
per-customer generator and raises if a positive rate is passed without it. A
construction check proves that a no-miss overlay leaves both the balance trace
and the next money RNG draw bit-identical. The corrected 48-run sweep moved the
W2 table and reduced its pre-registration record from 5/5 to 3/5; W2-2 and
W2-4 broke. The corrected numbers are in `02_RESULTS.md`.

*The shape, for the tally:* **an invariant stated as a comment in one module and
relied on by another.** Errors 11 and 13 are the same family — a rule the
measuring apparatus is assumed to follow, with nothing checking that it does.
This is the fourth time the defect has been in the apparatus rather than in the
product, and the second time it was found only because something else was being
built on top of it.

---

## The tally after twenty-seven — SUPERSEDED, see "The tally after thirty" at the end

The seven guardrails that measured nothing are unchanged (`assert violations
== 0`; peak hours unrepresentable; oracle approval ~100%; **M1**; **M4**; the
loop-order mutant; **E-DET-2/3**). Errors 17-26 add these shapes, none of which
is a vacuous gate:

| shape | instance |
|---|---|
| a metric whose time base counted moments that changed nothing | **17** (G-1b hours) |
| an invariant every reader assumed and nothing enforced | **18** (one log, one run) |
| a correctness property living in the caller, not the component | **19** (GC-40) |
| a property measured on one component and generalised to another | **20** (WAIT) |
| one quantity derived twice, in two places, disagreeing | **21** (judge count) |
| a component validated cheaply and deployed exhaustively | **22** (119,667 calls) |
| a checker written by the party that wrote the thing checked | **23** (governance) |
| a set-coverage check standing in for a provenance check | **24** (invented code) |
| a cross-check whose two sides can only differ in a regime the display never enters | **25** (gate vs auditor) |
| a claim about someone else's product, asserted rather than read | **26** (downtime feed) |
| an invariant stated as a comment in one module and relied on by another | **27** (W7's hold generator) |

**The two encouraging lines.** Error 23 was found by an independent checker
running on a *different model family*, on its first outing, in the measuring
apparatus — which is exactly where `CLAUDE.md` says self-audit is blindest.
And errors 24 and 25 were both found by gates and scripts **written in the same
session as the code they broke**, before anything was committed — 24 by a check
deliberately pointed in the opposite direction to the obvious one, 25 by a demo
built to make the enforcer fail on purpose.

The two-implementation discipline has now caught something three times:
`auditor.py` versus `Stage0Gate`, GLM-5.3 versus our own regex list, and R1b
versus R1a.

⚠️ **And the count is still the point.** *(SUPERSEDED — the count is now
thirty; this paragraph is kept as the record of the tally at twenty-seven.)*
Twenty-seven errors, and **the ones
found by an outsider or by a deliberately adversarial check are consistently
the ones a careful self-audit missed.** Errors 11-13 came from an outside
reader with `docs/` and half a day. Error 23 came from a different model.
Errors 24 and 25 came from checks written to disagree with their own author.
Nothing in this list was found by re-reading code and feeling confident.

---

# Errors 28-30 — found 30 August 2026, sending the first real Razorpay request

**All three were found by one action: pointing the shipped transport at the
real API with no credentials and reading what came back.** No key, no account,
no money, about ninety seconds of network time. Every offline gate in
`test_razorpay_mapping.py` was green before and after.

**28. An authentication failure was recorded as a statement about the
customer's bank balance.**

`RazorpayExecutor.attempt` passed every response that had an HTTP status to
`_outcome_from_payment`. That function looks for `error.reason` and for a
payment `status`. A real Razorpay authentication failure has neither:

```
401 {"error": {"code": "BAD_REQUEST_ERROR", "description": "Authentication failed"}}
```

so the parser fell through to its last branch and returned the AMBIGUOUS code
`U30` with `success=False, pending=False`. **That is a decline.** `agent/loop.py`
hands it to `BeliefBook.record_outcome`, and `w3.py:432` hard-zeroes every
balance bin at or above the amount.

*What it would have cost:* a wrong, expired or revoked API key would have
taught the belief filter that the customer's account was empty — for every
mandate, on every attempt, silently. **One belief is shared by all `k` mandates
of a customer**, which is the project's central claim, so one bad response
corrupts all `k` at once. All four legal NPCI attempts would have burned and
every mandate would have died at the cap. The run would not have crashed. It
would have printed a plausible recovery rate for a world in which nothing had
been asked of anybody.

*How it was caught:* by sending the request. Nothing else would have done it —
and specifically, **the offline gates could not have.** Every fixture in
`test_razorpay_mapping.py` was a *payment object* transcribed from Razorpay's
docs, `{"status": "failed", "error": {"reason": ...}}`. Not one represented an
*API-level* rejection, because nobody had ever seen one. The suite was total
over the vocabulary it knew and blind to the vocabulary it had never met.

*Mechanism:* the file had already reasoned about this exact hazard one level
down. Design decision 2 in its own docstring says a transport failure must be
`pending` and never a decline, because *"returning `Z9` would tell the belief
filter the account was empty — which is a lie about the customer derived from a
fact about our network."* **The same sentence applies word for word to a
refused credential.** The author thought about the socket and did not think
about the key. A principle stated for one instance and not generalised.

*Guard:* `RazorpayExecutor._is_configuration_fault` splits "Razorpay rejected
the REQUEST" from "Razorpay reported on a PAYMENT", and `attempt` raises
`RazorpayError` — already declared as the home for configuration faults —
rather than returning an outcome. Gate **R9** asserts it against the envelope
captured from the wire, which is the only fixture in that file Razorpay wrote,
and its named mutant `blind` restores the old behaviour and prints the `U30`
decline so the defect is visible in the test output. R9c and R9d prove the fix
does not overreach: a documented payment decline is still a decline and a
captured 200 is untouched.

*The asymmetry that decided the fix:* raising when we should not stops the run
with a message naming the status and the envelope. Declining when we should not
corrupts every belief and reports a number that looks fine. Loud and wrong is
recoverable; quiet and wrong is what this catalogue is made of.

---

**29. A guarantee documented in the callee and never wired at the caller.**

`RazorpayExecutor.attempt`'s docstring: *"When Stage 0 passes it the idempotency
key is tied to the audited action; without it the key falls back to the mandate
and hour, which is still deterministic but is a weaker guarantee across runs."*

**Stage 0 never passed it.** `stage0.py:171` read

```python
outcome = self._executor.attempt(a.ref, a.amount, a.target_t)
```

with `a.action_id` on the line above, already computed, already written into
the audit trail as the identity of that money action. So every idempotency key
the real backend could ever have produced was the weaker fallback, and the
stronger guarantee existed only in prose — in a file whose stated purpose is
that a retried request after a crash cannot become a second debit.

*How it was caught:* while writing the test for error 28, by asking what
`action_id` a real dispatch would actually carry.

*Mechanism:* **a property tested at the callee and never at the caller.** Gate
R5 checks key derivation thoroughly — stable, collision-free, right shape — by
calling the executor **directly**. `SimExecutor` has no idempotency to protect,
so the one integration path that exercises Stage 0 could never notice. The
seam between two correct components, which is where errors 19 and 21 also
lived.

*Guard:* `ports.Executor.attempt` now carries `action_id: str = ""`,
`SimExecutor` accepts and ignores it, Stage 0 passes it. Gate **R10** puts a
real `MoneyAction` through `Stage0Gate` and asserts the key the transport saw
equals `idempotency_key(a.action_id, ...)` and is **not** the fallback; its
mutant `drop` reproduces the old call and turns R10b and R10c red. Parity with
the frozen harness is still bit-exact 24/24 — an ignored argument cannot change
a draw.

---

**30. A test file that advertised a mutation runner it did not have.**

`test_razorpay_mapping.py`, line 12, in capitals: *"EVERY GATE CARRIES A NAMED
MUTANT AND `--mutants` RUNS THEM."* There was no `--mutants` flag. Gates R2-R8
run their mutants inline, so their claims were sound; **R1 carried a `mutant`
parameter that nothing anywhere ever passed**, and the two gates written for
errors 28 and 29 were about to be added in the same shape.

*How it was caught:* by trying to use the advertised feature.

*Mechanism:* **the measuring apparatus describing itself wrongly** — errors 11,
12 and 13 exactly, and the fifth time the defect has been in the apparatus
rather than in the product. A sentence in a docstring is not a check, and this
project has now written that down four times and been caught by it five.

*Guard:* `--mutants` is real. It runs each named mutant in isolation and
requires it to turn at least one clean check red; a mutant that breaks nothing
is reported **VACUOUS** and fails the run, which is the same rule `sim/gate.py`
applies. **3/3 trip.** The fix was to make the sentence true, never to delete
it — deleting it would have removed the record that it was once false.

---

## The tally after thirty — SUPERSEDED, see "The tally at thirty-two" at the end

Errors 28-30 add these shapes:

| shape | instance |
|---|---|
| a fault in OUR configuration recorded as evidence about the WORLD | **28** (401 as a decline) |
| a principle stated for one instance and not generalised to its twin | **28** (socket considered, credential not) |
| a guarantee tested at the callee and never at the caller | **29** (the unpassed `action_id`) |
| a test file documenting a capability it does not have | **30** (the missing `--mutants`) |

**The line worth keeping from this batch.** Errors 28 and 29 sat behind a suite
that was *total over the vocabulary it knew*: 111 reasons mapped, every
dangerous case routed, every mutant tripping, 53/53 green. It had never seen a
response Razorpay wrote. **One unauthenticated request — no key, no account, no
money, ninety seconds — found two defects on the money path that eight offline
gates could not.** That is the same lesson as errors 11-13 in a different
costume: the cheapest outside contact beats another pass of self-audit.

**And the direction is back to normal.** The 30 August doc audit found ten
defects that all made the project look *worse* than it was. These three make it
look **better** than it was, which is the direction `CLAUDE.md` warns about and
the direction most of this catalogue runs in.

⚠️ **And the count is still the point.** *(SUPERSEDED — the count is now
thirty-two; kept as the record of the tally at thirty.)* Thirty errors, and **the ones found by
an outsider, by a deliberately adversarial check, or by contact with something
this project did not write are consistently the ones a careful self-audit
missed.** Errors 11-13 came from an outside reader with `docs/` and half a day.
Error 23 came from a different model family. Errors 24 and 25 came from checks
written to disagree with their own author. Errors 28 and 29 came from a server
in Mumbai answering a request. **Nothing in this list was found by re-reading
code and feeling confident.**

---

# Errors 31-32 — found 30 August 2026, sweeping the model's reasoning setting

**31. A response cache keyed on some of the inputs that change the answer, and
not on the two the experiment was about.**

`agent/llm/client.py` cached responses under `(model, prompt_id, case_hash)`.
The request body also carries `reasoning_effort` and `max_tokens`, and both
change what the model returns — the same file's own docstring says so in
capitals: *"a reasoning model on its lowest reasoning setting may well answer
worse than the same model on `high`."*

Neither was in the key.

*What it would have cost:* `docs/00_HANDOFF.md` calls sweeping
`reasoning_effort` "the first thing to sweep", and that sweep would have done
one of two things.

1. **Hit the `low` cache and returned the `low` answers at `high`**, reporting
   *"reasoning effort makes no difference"*. A false negative manufactured by
   the measuring apparatus. **This is the worse of the two, because it looks
   like a result** — a clean table, a plausible conclusion, and nothing
   anywhere to say the model was never asked.
2. Or, on a cold cache, **written `high` answers under the `low` key**, so
   committing the cache would silently change what `--replay` reproduces and
   break the byte-identical-offline claim the whole eval rests on.

*How it was caught:* by reading the key function before spending money, because
the sweep's first pre-registered prediction was *"the cache genuinely misses"* —
a construction check written to catch exactly this, and the only reason it was
looked at.

*Mechanism:* **a rule applied to one parameter and not to its neighbours.**
`prompt_id` is in the key **on purpose**, and the docstring boasts about it:
"a prompt edit misses the cache and shows as a diff". `reasoning_effort` sits
three lines below `prompt_id` in the same request body and got none of that
reasoning. This is the same shape as error 28, where a docstring reasoned
carefully about transport failures and never considered credential failures —
**the second time in one day that a correct principle was stated for one
instance and not generalised to its twin.**

*Guard:* the key now carries both settings, except at the exact values the
committed caches were recorded at (`low`, 2000), which keep the three-field key
so all 385 + 80 paid responses stay valid. Verified before spending:
`--replay` still reproduces 10/21, 13/19, 4/4 and 19 judge disagreements,
offline, 50/50 cached, $0.00. `ModelDiagnoser`'s live-call cap was building the
key separately with the old signature and is now aligned — otherwise the cap
would have asked about a different cache entry than the one the call would hit,
which is a second instance of the same defect hiding inside the first.

*The near-miss worth recording:* at `reasoning_effort=max`, 32 of 50 calls hit
the token cap, returned unparseable payloads and fell back to the rule engine.
That arm reports **9/21 ambiguous and 19/19 clean — exactly the rule engine's
scores, including the best clean score in the whole table.** Reported from the
score table alone it is "max is the best setting". What stopped that was
`ModelDiagnoser`'s `n_fallback` counter and its reason string printing beside
the score. **That is not an error; it is the one place today where existing
instrumentation caught something before it became one**, and it is why the
runtime-fallback counters are worth their cost.

---

**32. A hardcoded caveat, and a new flag that turned it into a lie.**

`agent/batch_report.py` ends with a block titled "WHAT THIS NUMBER IS NOT",
whose entire job is to stop a reader mis-reading the number above it. One line
read:

> `* The decline taxonomy is OFF here (every rate 0), so this is the world
> without frozen accounts, broken mandates or limit hits.`

Adding `--declines` — a flag whose only purpose is to turn the taxonomy **on** —
left that line printing unchanged. **A run with the taxonomy on announced that
the taxonomy was off, inside the section that exists to prevent exactly that
misreading.**

*How it was caught:* by reading the output of the first run rather than only
the numbers at the top. It survived writing the flag, running it, and reading
the headline table.

*What it cost:* nothing, because it lived for about four minutes. It is in this
catalogue anyway, because the shape is general and the next instance may not be
noticed the same afternoon.

*Mechanism:* **a caveat is a claim, and claims printed unconditionally go stale
exactly like claims written in documents.** This project built an entire doc
gate today for retracted sentences surviving in Markdown, and then shipped the
identical failure in a `print()` a few hours later. The gate cannot see it:
`sim/verify_docs.py` scans documents, not string literals in `agent/`.

*Guard:* the caveat is now conditional, and the `--declines` branch says loudly
that the configuration is not the published one and must not be compared to the
headline. **No automated guard exists for the general case** — a print-literal
scanner was considered and not built, because the honest version of it is the
same idea as the doc gate pointed at source files, and one gate written today
against a real list of retractions is worth more than a second gate written
speculatively. If a third instance turns up, build it.

---

## The tally at thirty-two

| shape | instance |
|---|---|
| a cache keyed on some inputs that change the answer, not all | **31** (`reasoning_effort`) |
| a principle stated for one instance and not generalised to its twin | **28** (socket vs credential), **31** (`prompt_id` vs `reasoning_effort`) |
| a caveat printed unconditionally, made false by a new flag | **32** (`--declines`) |

**Both of today's last two errors are in the measuring and reporting apparatus
rather than in the product**, which makes it six of the last nine. That is not
a coincidence and it is not a run of bad luck: `agent/` is gated, mutation
tested and byte-locked, and the things that *describe* it — caches, caveats,
docstrings, tallies — are protected by attention alone.

**And the direction is worth noting.** Error 31 would have made the project
look better than it was: a sweep that silently replayed cached answers would
have reported "the setting does not matter", which retires an awkward caveat
for free. Error 32 would have made a non-published configuration look like the
published one. Both are the usual direction for this catalogue, and both were
caught by a check written to be sceptical rather than by a re-read.

⚠️ **The count, at thirty-two.** The pattern has not changed: **the errors
found by an outsider, by a deliberately adversarial check, or by contact with
something this project did not write are the ones a careful re-read missed.**
Errors 11-13 came from an outside reader. Error 23 came from a different model
family. Errors 24 and 25 came from checks written to disagree with their own
author. Errors 28 and 29 came from a server in Mumbai answering a request.
Error 31 came from a pre-registered construction check written to catch exactly
it. **Nothing in this list was found by re-reading code and feeling
confident.**
