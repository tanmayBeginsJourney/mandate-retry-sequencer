# 04 — BUILD PLAN

Deadline **5 September 2026**. Day 1 was 27 August; **today is 29 August.**
The simulation is finished and frozen, and `agent/` is complete. Seven days
remain and the remaining work is all presentation.

**Rule: if a task does not move us toward a running agent with an audit trail
and a measured batch result, it is out of scope.**

## PROGRESS — updated 29 August 2026, end of day

**The plan below is running about five days ahead of itself.** Days 3-7 are
done; what is left is the architecture doc and the pitch.

| Day | Planned | State |
|---|---|---|
| 3 | `agent/` skeleton, wire in the frozen policy | **DONE.** Bit-exact with `harness.run` on 24/24 runs |
| 4 | Stage 0 as enforced middleware | **DONE.** Refuses; `prove_stage0_refuses.py` shows it refusing a REAL Razorpay debit with zero network |
| 5 | Audit log queryable; stopping rules explicit | **DONE.** Append-only JSONL, 8 stop rules, `python -m agent.demo` |
| 6 | LLM layer | **DONE.** `glm-5.3-flash` diagnosing, `glm-5.3` judging, $0.26 spent, caches committed so it replays offline for $0 |
| 7 | The batch number | **DONE.** 94.36% vs `payday_wait` 57.70%, +36.66 pts, reproduced on a clean clone in 47s. Top-up sweep still not redone on `w3` (A1, still open) |
| 8 | Architecture doc | **not started** — the last real deliverable |
| 9 | Pitch video | **not started** |

**Also done, and not in any plan:** a second executor backend against
Razorpay's real API (`agent/execution/razorpay_executor.py`, gated offline,
never called Razorpay), a static public page (`docs/index.html`), and a README.
**Both the README and the page were rewritten on 29 August and are no longer
drafts.**

## What is actually left, 29 August 2026, end of day

Ordered by what a judge sees first, not by what is most interesting to build.

| # | Item | State | Why it ranks here |
|---|---|---|---|
| 1 | **Push to a public GitHub remote** | `git remote -v` is **empty** | A public repo is a stated deliverable. 28 commits, including the PRE-REGISTER ones, are invisible until this happens. Minutes of work. |
| 2 | **Restate the headline as conditional on world hardness** | not started | The README and the page both open with `+36.66 pts`, measured in a world where 60% of debits fail. `02_RESULTS.md` now has the sweep; the two judge-facing artifacts do not. A Razorpay judge reaches this objection unaided. |
| 3 | **Architecture doc** | not started | The last unstarted judged deliverable. |
| 4 | **A judge-facing entry point to `docs/`** | not started | Eight files, 4,600 lines, all written for the next engineer. Nothing tells a judge which three to open. |
| 5 | **Pitch video** | not started | |
| 6 | Adjudicate the 19 judge-vs-author disagreements | open item 0b | The eval's only validation step. |
| 7 | Sweep `reasoning_effort` | open item 0c | Every LLM score is at `low`; 10/21 may be a floor. |

**Leave `sim/` frozen.** The spend sweep that produced item 2 is additive and
read-only (`scripts/spend_sweep.py`); it changes no frozen byte.

---

# WORLD v2 — the spec. Added 30 August 2026.

**The problem this solves is not accuracy. It is trust.**

A judge cannot check a simulated result. All they can ask is whether the world
behaves like the thing it claims to model. Today the only answer is a single
calibration anchor (~30% per-attempt approval) that the world was *tuned to* —
and reporting agreement with the number you fitted to is circular. Everything
below exists to replace that with something a stranger can check.

**There is no public benchmark to report against.** Confirmed 30 August 2026:
no shared dataset, no leaderboard, no held-out set for payment retry
scheduling. The only formal artifacts in the space are patents on
machine-learned dunning, which publish methods and no data. `NOTES.md`,
30 August, has the search.

**What exists instead is a set of published aggregate statistics**, listed in
`01_FACTS.md`. They are vendor benchmarks and trade-press figures, every one
`[REPORTED]` at best, and they are not ground truth. But they are numbers this
project did not fit to — and a world that reproduces several of them *at once*
is doing something a world tuned to one anchor cannot fake. That is the whole
idea.

---

## W0. The recovery-rate metric — ✅ DONE 30 August 2026

**Built, gated, measured and wired into `batch_report.py`.** `agent/metrics.py`,
`SimExecutor.at_risk_cycles()`, `agent/tests/test_recovery_metric.py` (5 checks,
5 mutants, all trip), `agent/tests/test_recovery_rates.py` (the measurement).
Parity is still bit-exact 24/24 and isolation still 5/5; the batch headline is
unchanged at 94.36% / 57.70% / +36.66.

**V1 is hit and was not fitted: 13.68% first-presentation failure at
`pop_spend=0.80`, inside the published 8–15%.** Full table and the two broken
predictions in `02_RESULTS.md`. **R-3 (`payday_wait`'s recovery rate) is
deferred to the validation suite**, because the baselines live in the frozen
harness and emit no per-cycle record — implementing `baseline_doc` as an agent
arm is that suite's first task.

**What the measurement changed about the plan below.** Recovery comes out at
90–97% against a published 70–85%, and only 37% of it lands inside 10 days
against a published ~90%. Both have one cause — in this world the money always
arrives eventually — so **W2 is now evidenced rather than assumed**, and it is
the next thing to build.

### The original statement of W0, kept

## W0. The recovery-rate metric — prerequisite for everything else

Every published figure in the space is a **recovery rate**: of the payments
that *failed*, what fraction was eventually collected. This project reports
**cycles collected / cycles due**, which counts cycles that never failed at
all. **The two cannot be compared, and the project currently has no number that
maps onto any external figure.**

Add, derived from the audit log, changing no policy:

- `recovery_rate` — of mandate-cycles whose first presentation failed, the
  fraction collected before the cycle closed
- `days_to_recovery` — the distribution, so the "~90% of recoveries land inside
  10 days" figure becomes checkable
- `first_presentation_failure_rate` — the world's headline realism number

Cost: no re-runs. It is a second read over trails that already exist.

## W1. A declared operating point, rather than an emergent one

Today the first-presentation failure rate is a *side effect* of `pop_spend`,
and at the shipping value of 1.05 it is **53%**. Published UPI AutoPay failure
is 8–15%; direct debit generally is 3–5%.

Make the failure rate an explicit calibrated input and solve for the spend
parameter that produces it, rather than picking a spend and discovering the
failure rate afterwards. Ship **two** declared operating points and report both
everywhere, the way `payday_err` is already reported:

| point | first-presentation failure | what it represents |
|---|---|---|
| `realistic` | ~12% | the rail as the public record describes it |
| `stressed` | ~50% | today's world, kept so every existing number stays comparable |

Cost: every headline re-runs. The sweep in `02_RESULTS.md` already shows the
shape, so there are no surprises waiting — at ~15% failure the agent is worth
about +6 points, at ~60% about +36.

## W2. Customers who genuinely cannot pay

Today the oracle is **100% at every calibration tested**: every mandate-cycle
is winnable on some day, so the agent solves a pure *timing* problem and never
a *collectability* one. Real recovery is both, and the published "~0–10%
recovery with no retries" figure is only meaningful in a world where some
failures are permanent.

Add a fraction of cycles where no day in the cycle has enough balance. Two
consequences worth having:

- **the oracle stops being 100% by construction**, which retires the standing
  worry in `06_MODEL_CARD.md` §3 item 11 that a small oracle gap measures
  filter-world match rather than scheduling skill
- **knowing when to stop becomes worth something**, because attempts spent on
  an uncollectable cycle are pure cost

## W3. Customer-initiated cancellation, with a hazard that responds to pressure

Today a mandate dies exactly one way: by exhausting its attempts. Real mandates
are cancelled by customers — the trade press reports ~18% cancellation — and
cancellation is not independent of how hard the merchant is dunning.

Model a per-cycle cancellation hazard that **rises with failed attempts and
nudges**. This is the highest-value change on this page, for one reason:

> **It is what makes the agent's restraint valuable.** `STOP` is currently
> worth **+1.371 pts** because mandate death is rare and cheap. Under a
> pressure-responsive hazard, an agent that declines to burn a fourth attempt
> is protecting a live subscription, and the action space stops being a
> rounding error. It also turns a defensive result into a product argument:
> *dunning harder costs you the customer, and the agent knows when to stop.*

## W4. Decay of debit success over the mandate's life

The trade press reports ~85% success in month 1 decaying to ~70% by month 6 —
stale accounts, balance erosion, subscription fatigue. Nothing here models a
mandate ageing. A time-varying hazard makes the 120-day horizon mean something
and makes `days_to_recovery` comparable to the published distribution.

## W5. Turn the decline taxonomy ON by default

**This one is nearly free — the code already exists** (`DeclineMix` in
`agent/execution/sim_executor.py`) and the headline batch deliberately runs
with every rate at zero. Frozen accounts, revoked mandates, limit hits.

Consequence, and it is the reason to do it: **the diagnosis layer currently
does not move the money** (94.33% against 94.36%) *because there is nothing in
the world to diagnose*. Every failure is insufficient funds, and the frozen
policy is exactly indifferent to decline reasons. Switch the taxonomy on and
terminal codes exist — the case where the LLM already scores **4/4 against the
rule engine's 0/4**, and where no retry can ever succeed. The LLM layer stops
being an overlay that changes nothing and starts being load-bearing.

Rates stay `[GUESS]` and stay swept, exactly as now.

## W7. Transient failures — the highest-value world item. Added 30 August 2026.

**One mechanism explains three of the four validation misses**, and it is the
only outstanding item that moves more than one.

Every failure in this world is "the money is not there and will not be until
payday", plus, since W2, "the money never arrives". Real declines include a
large third class: a temporary hold, a momentary shortfall, a balance topped up
the same evening — **the money is back within a day or two.**
`harness.P_TECH = 0.008` auto-represents and is not this.

Add a swept transient-failure rate and three targets move together:

* **V3 rises into 20–40%.** A fixed schedule retrying T+1…T+4 exists to catch
  precisely this class. It is why the published band is 20–40% and not near
  zero — and why ours currently measures 18.75%, very nearly nothing.
* **V7 rises.** Recoveries land inside ten days because the money returned
  inside ten days.
* **V5 holds.** The agent already catches these; it simply waits longer than it
  needs to.

**Registered before building:** transients at a swept rate move V3 into 20–40%
and V7 above 60%, without moving V5 out of 70–85%.

⚠️ It will also *reduce* the agent's measured advantage, because a fixed
schedule that catches transients is a better baseline than the one measured
today. That is the correct direction and it should be reported as such.

## W6. Due dates that cluster near paydays

**Added 30 August 2026, after V7's miss was traced.** `w3.make_pop` draws
`due_day` and `payday` independently, so the gap between a debit and the money
that would cover it is uniform over the cycle — mean 14.7 days. **Only 35.8% of
at-risk cycles have money inside ten days**, against a published ~90% of
recoveries landing there. The agent already recovers 42.6% inside ten days,
which is above the ceiling this world sets, so **V7 cannot be fixed by a better
policy, and W2 will not touch it.**

Real subscription billing is not uniform: people subscribe just after being
paid, and merchants bill on the 1st. Draw `due_day` with mass concentrated a few
days after `payday`, with the concentration swept rather than picked.

⚠️ This one cuts both ways and that must be said when it lands. Clustering due
dates near paydays makes the world easier — more debits succeed on the due date,
so the at-risk set shrinks and **the agent has less left to win**. Expect the
headline gap to fall. That is the correct direction: the current uniform offset
is quietly inflating the problem the agent is solving.

---

# THE VALIDATION SUITE — what replaces a public benchmark

One anchor is fitted. Everything else is **scored, not fitted**, and the suite
goes red when the world drifts away from it. Targets and sources in
`01_FACTS.md`; each needs a tolerance declared before the world is tuned, the
way `05_TEST_DESIGN.md` already requires.

| # | Target the world must reproduce | Published | Measured at `pop_spend=0.80` |
|---|---|---|---|
| **V1** | first-presentation failure rate, UPI AutoPay | 8–15% | **13.68% — HIT** |
| V2 | recovery rate under **no** retries | ~0–10% | not built |
| **V3** | recovery rate under a **fixed-interval** schedule | ~20–40% | **27.85% — HIT** |
| V4 | recovery rate, industry median across mixed approaches | ~47.6% | not built |
| **V5** | recovery rate under **smart retry timing** | 70–85% | **97.38% — MISS, too high** |
| V6 | smart retry timing vs fixed intervals, relative | ~+25% | not built |
| **V7** | share of recoveries landing inside the first 10 days | ~90% | **41.84% — MISS, too slow** |
| V8 | involuntary share of total subscription churn | 20–40% | not built |
| V9 | mandate cancellation rate over the horizon | ~18% | 23.4% mandate death, indicative |

**Scored 30 August 2026: 2 of 4 measurable targets hit, neither fitted**, at
`pop_spend=0.80` with no insolvency.

⚠️ **W2 landed and the honest reading got harder.** Insolvency brings V5 into
band at `p_missed_credit=0.08`, but V1 breaks there; a (0.70, 0.08) calibration
satisfies V1 and V5 together and then **misses both targets it was not fitted
to** — V3 falls to 18.75% and V7 to 32.86%. Two hits produced by turning two
dials are a curve fit, not corroboration. **The 0.80 / 0.00 pair, where two
unfitted targets hit, remains the stronger evidence and the reported
calibration.** `NOTES.md`, 30 August, and W7 below. The
fixed-schedule arm that made V3 measurable is `agent/policy/fixed_schedule.py`;
`02_RESULTS.md` has the table and the caveats. **Both misses are W2** — recovery
is too high and too slow because no customer in this world is ever unable to
pay.

**V3 and V5 are the pair that carries the pitch.** `baseline_doc` is a faithful
rendering of Razorpay's own documented schedule, so V3 asks whether this world
makes a documented fixed schedule behave the way the industry reports fixed
schedules behave. V5 asks whether the agent lands where the industry reports
smart retries land. If both hold, the claim stops being *"we beat our own
baseline in our own world"* and becomes:

> **This simulator reproduces published behaviour it was never fitted to, and
> inside it the agent moves recovery from the published fixed-schedule band to
> the published smart-retry band.**

**Rules that keep this from becoming decoration.** Fit to at most one target and
say which. Declare every tolerance before tuning. A target the world misses
stays visible and red — `sim/known_failures.txt` is the existing pattern, and it
is why a red suite reads as evidence here rather than as embarrassment. And
these figures come from companies selling recovery software: corroboration,
never ground truth, and the docs must say so wherever they appear.

---

# JUDGE-FACING DOCS — written once, after World v2 lands

Plain English, the register of the rewritten `README.md` and `docs/index.html`.
**Not written before the world work lands**, or it gets written twice.

Three things a judge needs and cannot currently get:

1. **What the world is** — how a customer is generated, what is random, what is
   fixed, what is calibrated and against what. With the validation table above,
   this is the answer to *why should I trust any of this*.
2. **The engineering impact** — a language model that cannot reach the timing
   path by construction; a constraint layer that refuses rather than counts,
   with an independent recount sharing no code; one belief per customer rather
   than per mandate; a second executor backend behind the same interface.
3. **The business impact** — stated in money and in churn, not in points.
   Recovery-rate lift against failed-payment volume; involuntary churn as
   20–40% of all subscription churn; ~120M UPI AutoPay mandates created monthly
   in India (`01_FACTS.md`). What one point of recovery is worth at that scale.


**Scope added and not in the original plan:** a context layer (rail-outage
detection), built because the action space measured only +1.371 pts against a
policy already at 95.31% — the world is saturated at the scheduling task. The
context layer's own recovery value is +0.256 pts at the most extreme severity
swept; its defensible claim is a capability, not a number. `02_RESULTS.md`.

## WHERE WE ACTUALLY WERE, 28 August — HISTORICAL, kept for the reasoning

⚠️ **The day table below is the plan as it stood on 28 August and its dates
have been overtaken.** Days 3-7 all landed on 28-29 August. Read it for why
the ordering was chosen, not for what to do today — the current state is the
PROGRESS block above.

Days 1–2 were spent on the simulation, not the agent, and that was the right
call — the model was carrying a dead constant, an unfitted belief and a
calibration gate pointed at the wrong object. It is now frozen at tag
`model-frozen`. **Nothing in `sim/` needs further work before the deadline.**

| Day | Date | What |
|---|---|---|
| 3 | **29 Aug** | `agent/` skeleton: detect → diagnose → choose → execute → log. Wire `w3.BeliefPD` + `w3.FITTED_BELIEF` + `w3.index_score` in as the timing brain. Every action returns a structured record, not a print. |
| 4 | **30 Aug** | **Stage 0 as enforced middleware.** Today it only *counts* violations after the fact. Making it *refuse* is the first real product task — see `07_AGENT_BRIEF.md`. |
| 5 | **31 Aug** | Audit log as a queryable artifact: action, reason, constraint check, outcome, timestamp. Stopping rules explicit and demonstrable. |
| 6 | **1 Sep** | LLM layer: root-cause diagnosis, intervention choice, per-action justification. **Build the failure path first**, then the happy path. |
| 7 | **2 Sep** | The batch number. Money recovered over synthetic merchants, with `payday_wait` printed beside it, always. Redo the top-up sweep on `w3`. |
| 8 | **3 Sep** | Architecture doc, one page. |
| 9 | **4 Sep** | Pitch video. **Open with the errors** — lead with error 5, the broken oracle, then error 7, the ML result that reversed. |
| — | **5 Sep** | Submit. Buffer is Day 9; something will break. |

## Original day-by-day detail (kept — the content still applies)

## Day 1–2 — agent skeleton
- `agent/` package. Loop: **detect → diagnose → choose → execute → log**
- Wire `w3.BeliefPD` + the index in as the *timing* brain. Do not rewrite them.
- Repo public from the first commit. Visible history is judged.
- Every action returns a structured record, not a print.

## Day 3–4 — constraints and audit
- Stage 0 as enforced middleware every action passes through, not a filter
  policies apply to themselves. **This is what makes the violation counters
  meaningful** — see `03_ERRORS.md`, the vacuous gates section.
- Audit log as a first-class queryable artifact: action, reason, constraint
  check, outcome, timestamp.
- Stopping rules explicit: attempt cap, mandate death, cycle close, escalation.

## Day 5 — the LLM layer
- Root-cause diagnosis from decline history + belief state
- Intervention choice: retry / nudge / partial / escalate / stop
- Human-readable justification per money action
- **Graceful fallback when the LLM fails or returns garbage.** Judged
  explicitly. Build the failure path first, then the happy path.
- Governance constraint, enforced in code: merchant-facing explanations must not
  disclose the customer's financial state. Say "our model scores this window
  highest," never "their balance has never recovered before the 3rd."

## Day 6 — the batch number
- Run over synthetic merchants. Produce money recovered.
- Print `payday_wait` beside it. Always. Never show our number alone.
- Redo the top-up sweep on `w3` — roughly half the apparent gain may be
  "customers never top up." Better we find it than a judge.

## Day 7 — architecture doc
- One page. Compress the research here. This is where Notion gets distilled.

## Day 8 — pitch video
- **Open with the errors** (there are **twenty-six**; see `03_ERRORS.md`). Lead
  with error 5, the broken oracle, then error 7, the ML result that reversed,
  then **error 11** — the mutation test that graded itself, found by an
  outside reader against a suite built specifically to prevent it. Error 11 is
  the strongest of the set for the "Failure Recovery" criterion, because the
  guard (gate M4B) is committed, red, and honest about being blocked.
- Then the mechanism, then the demo, then the conditional result.
- The conditional result is a *strength*: it shows we measured whether the
  sophisticated thing was worth it and are prepared to say when it isn't.

## Day 9 — buffer
Something will break. It always does.

## Explicitly out of scope
- Chasing the payday parameter through external sources
- Rewriting the Notion knowledge base
- Reintroducing coordinated budgeting

## Scope change, 27 August 2026 — read this before citing the list above

Two items were removed from the out-of-scope list by an explicit decision, and
the reason is recorded here so it is not mistaken for scope creep:

- ~~Further simulation research beyond one honest batch number~~
- ~~Any new policy variant~~

**Why.** The suite rebuild showed the belief filter is the *true generative
model* of this world — `w3.Belief` is hand-built to match `w3.balance_trace`,
same spend shape, same payday model. That makes every in-distribution
comparison biased toward Bayes by construction, and it means we cannot claim
the timing brain is a good choice without testing it where its assumptions are
wrong.

> **CORRECTION, 28 August 2026 — the paragraph above is overstated.** The
> filter matches the functional *shape* of the world but not its *parameters*.
> The stride-3 grid `[0, 3, …, 27]` left only **74%** of customers with a
> representable true payday; `est_salary` is wrong by ±30% by construction and
> `est_spend` was a population rate.
>
> **Those three parameters have since been fitted** (`w3.FITTED_BELIEF`,
> `sim/fit_belief.py`), which is worth **+11.66 pts (±1.61)**, gated as S4.
>
> **And that reversed the ML result.** An intermediate finding on this page's
> earlier revision — "`ml_index` beats `solo_shared_pd` in world A by +4.03" —
> is **superseded and must not be quoted.** It compared a fitted GBDT against
> an unfitted filter. Against the fitted filter, ML loses in all six worlds by
> 5–12 points and a Bayes+ML hybrid is worse than the filter alone. That is
> error 7 in `03_ERRORS.md`. Current numbers: `06_MODEL_CARD.md`.

An ML baseline plus a **misspecification study** is therefore in scope,
and it is directly judged: "AI Judgment: whether AI tools, LLMs, or agents were
applied appropriately instead of forcing unnecessary tech stacks."

Four research-only policy variants were built. **None of them is the product**
and none may be reopened — the model is frozen:

- **`explore`** — uniformly random legal day within the cycle, under the same
  Stage 0 constraints as everything else. Generates an unbiased training set.
- **`ml_index`** — identical index policy, constraint layer and metric, with
  **only** the probability engine swapped for a GBDT. An ablation.
- **`explore_pd`** — as `explore`, but carrying the payday-posterior belief so
  each training row can be tagged with the filter's own summaries. Authorised
  28 August for the hybrid.
- **`ml_index_pd`** — the hybrid: the same index policy, scored by a GBDT that
  is additionally handed four Bayes posterior summaries. Authorised 28 August.
  **It loses to the plain fitted filter by 5–10 points in every world.**

This does **not** reopen coordinated budgeting. **The shipping policy is
`solo_shared_pd` with `w3.FITTED_BELIEF`.** The agent build is the deliverable.
