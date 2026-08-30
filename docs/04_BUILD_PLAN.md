# 04 — BUILD PLAN

**Deadline 5 September 2026. Read `docs/00_HANDOFF.md` first for state; this
file is what to DO.**

---

# THE QUEUE — start at the top. Updated 30 August 2026.

Ordered by how much each moves the submission, not by how interesting it is.
**Every item below is fixable.** Things that genuinely are not fixable live in
the README's Limitations section and nowhere else.

| # | Item | Why it ranks here | State |
|---|---|---|---|
| ~~**1**~~ | ~~**Architecture doc**~~ | ✅ **DONE 30 August 2026.** `docs/08_ARCHITECTURE.md`, one page: the problem, the division of labour, ADR-005, the layer diagram, the three seams, the decision rule, the stopping rules, the measured result with **both** conditioning parameters as tables, the runtime failure-recovery table, and what is not tested. Linked from the README map and the public page footer. | **DONE** |
| **2** | **Pitch video, 5 minutes** | Judged deliverable, never started. Open with the errors, then the mechanism, then the demo, then the conditional result. | not started |
| **3** | **Razorpay TEST MODE — send a real request** | **Rungs 0–3 are DONE and they found two defects on the money path** (errors 28 and 29). `scripts/razorpay_ladder.py`, transcript in `logs/razorpay_ladder.json`. **Rungs 4–5 are BLOCKED: there are no Razorpay credentials on this machine.** They need a `rzp_test_` key pair, and rung 5 additionally needs an authorised test mandate — test-mode registration is mocked, so it is obtainable without a bank but not without an account. **This is the only queue item blocked on something outside the repo.** | **floor DONE, 4–5 need a key** |
| ~~**4**~~ | ~~**Put the validation suite on `docs/index.html`**~~ | ✅ **DONE 30 August 2026.** New section 07, *"Does this world behave like the real one?"*, with the four-row table, why two hits at one calibration are harder to arrange than one, the two misses and their two separate causes, and the fact that better-scoring calibrations were found twice and rejected both times. The old section 07 (limits) is now 08 and the nav carries a `Validation` link. Verified rendered: same figure width as the existing results section, no horizontal overflow, hit/miss tags resolve in both light and dark, no console errors. | **DONE** |
| ~~**5**~~ | ~~**W9 — non-pooled default + consent-gating**~~ | ✅ **DONE 30 August 2026, 5/5 pre-registered.** `BeliefBook` takes `pooling` in `{all, none, consented}` plus a per-customer consent set; `"all"` is the default and parity is still bit-exact 24/24. **The cost of withholding pooling is now measured at two calibrations instead of argued:** 9.54 pts at `pop_spend=1.05`, **3.47 at 0.80**, and 4.79 / 1.48 at half consent. **The finding is that the moat is a curve in world hardness like the headline is, and every existing quotation of +9.53 was the hard-world figure missing its conditional** — now fixed in seven files. Also fixed: `run_once` stamped `policy="solo_shared_pd"` unconditionally, so a non-pooled run would have been mislabelled *in the audit trail*. | **DONE** |
| ~~**6**~~ | ~~**Package the RUNTIME failure-recovery story**~~ | ✅ **DONE 30 August 2026.** The rubric's *"Failure Recovery: how the applicant identified system failures **at runtime** and engineered graceful fallbacks."* Nine rows — LLM→rule-engine fallback (94.8%), governance rejection, Stage 0 refusal, outage suppression, the `pending` outcome, idempotent retries, the refused-credential raise, crashed-worker detection, `LogFileNotEmpty` — now appear in **all three** places: `08_ARCHITECTURE.md`, a new README section *"When something breaks while it is running"* with the real `--mutants` output, and the public page's section 03. The framing that carries it is that **the fallback is the 95% path, not a cold branch**. | **DONE** |
| ~~**7**~~ | ~~**W5 — decline taxonomy ON by default**~~ | ✅ **MEASURED 30 August 2026, and the reason for doing it was WRONG.** `--declines` on `batch_report`. With the taxonomy on and terminal codes everywhere, the LLM arm scores **87.39% against the deterministic 88.54%** — 1.15 points BEHIND, not ahead, though the 2 SE bands (1.52 / 2.67) overlap so it is indistinguishable from zero. **The standing explanation — "the LLM does not move the money because there is nothing to diagnose" — is refuted.** What the LLM arm does do is stop 2.2× as often and kill 5 fewer mandates, trading collection for survival. ⚠️ **And the test cannot detect a small effect:** the 150-call cap makes the LLM arm 93.3% fallback, so only 520 of 9,910 money attempts were model-sourced. An uncapped batch is ~120,000 live calls, about **$120**. **Adopting the taxonomy as the default is still a deliverable decision and has NOT been taken.** | **measured; not adopted** |
| **8** | **The agent is blind to transient failures** | **Measured 30 August, not guessed.** It never presents on the due date, and `w3.BeliefPD.observe` takes no decline code, so a lien and an empty account give the identical posterior. It waits for payday on money already in the account — **15.1%** of transient-only cycles collected on the first legal day, **48.4%** over ten days. Fix is a design choice: let the belief condition on the decline family, or add a "re-present sooner" intervention. **Neither puts the LLM on the timing path.** | diagnosed, not built |
| ~~**9**~~ | ~~**A doc gate**~~ | ✅ **DONE 30 August 2026, and it immediately found five more survivors.** `sim/verify_docs.py`: twelve retracted claims, each with the regex, the date, and what is true now. Two modes — **banned** in `README.md` and `docs/index.html` (a judge should not have to parse a correction), **marked** elsewhere (the record is kept on purpose). `NOTES.md` is never scanned. `--selftest` proves every rule fires on its own canary, 14/14, so the gate cannot go vacuous. Wired into `scripts/pre-commit`. | **DONE** |
| 10 | **W6 — due dates that cluster near paydays** | **No longer "partly subsumed by W7" — W7 measured V7 and did not move it** (42.78% at best against 41.84%). W6 is one of V7's two remaining causes. | specced below |
| ~~11~~ | ~~**W1 — declare the operating point**~~ | ✅ **DONE 30 August 2026, 4/4 pre-registered.** `scripts/solve_operating_point.py` inverts the relationship: name the failure rate, bisect for the spend. **`realistic` = `pop_spend` 0.7850 → 11.87% due-date failure; `stressed` = 0.9627 → 50.13%.** The search runs **no policy** — the failure rate is a property of the world, so a declared point cannot be contaminated by what it will measure. **The finding is W1-4: 0.1777 of spend spans 12%→50% failure**, so `pop_spend` is a far sharper dial than any document treated it as, and the published sweep's grid (0.60/0.80/0.90/1.05) puts three of its four steps outside the interesting region. **Nothing was adopted and no headline was re-run.** | **DONE — declared, not adopted** |
| 12 | **W8 — the two Razorpay decline states** | `funds_blocked_by_mandate` and `deemed_transaction` are routed by the diagnosis layer and modelled by nothing. Add to `DeclineMix` at swept rates. | not started |
| 13 | **The agent forfeits the due date** | Actionable only from day T and needs 24h notice, so it can never present on T. **W7 priced this**: it is half of why transients are missed. | diagnosed, not built |
| ~~14~~ | ~~**Anchor `DeclineMix`'s sweep against a published mix**~~ | ✅ **DONE 30 August 2026, and the answer is mostly no.** Three combined cells were added so the sweep produces a decline *shape* at all, and it is printed against Churnkey's card mix. **Insufficient funds straddles (41.4–69.6% vs 45–55%) — anchored. Technical sits entirely below (1.1–1.9% vs 10–15%) and that is EXPECTED**, because `01_FACTS.md` carries a UPI-specific claim of under 1% of failures and the two published sources contradict each other. **Hard flags sit entirely below (4.9–11.5% vs 25–33%) and remain genuinely invented** — either UPI has far fewer terminal declines, or those two rates are swept several times too low, and nothing here distinguishes them. `--shape-only` reruns just the 24 cells. | **DONE — 1 row anchored, 1 explained, 1 still a guess** |
| ~~15~~ | ~~Sweep `reasoning_effort`~~ | ✅ **DONE 30 August 2026, $0.172, and the caveat pointed the WRONG WAY.** `low` is the best of the three permitted settings on the ambiguous set — **10/21 low, 7/21 high, 9/21 max** — so 10/21 is not a floor. **`max`'s row is the rule engine**: 32 of 50 calls hit the 2,000-token cap and fell back, and it reports the rule engine's exact scores. **The terminal-code result, 4/4 against 0/4, is invariant across all three settings** — the claim the LLM layer rests on does not depend on a configuration. ⚠️ **The marginal one does: at `high` the model LOSES on ambiguous cases, 7/21 against 9/21.** Also fixed **error 31**: the cache key omitted `reasoning_effort`, so this sweep would have silently replayed the `low` answers. | **DONE** |
| 16 | Adjudicate the 19 judge-vs-author disagreements | The eval's only validation step. | not started |
| 17 | **Error 27 in W2: `p_missed_credit` shifts the money path's RNG** | W7's equivalent was fixed to draw from its own generator; W2's was not, so each insolvency rate is a fresh draw of the world plus missed credits. W2's 5/5 were directional and stand. Repairing it **restates W2's published table** — a call about the deliverable, not a bug fix. | flagged, Tanmay's call |
| 18 | Build V2, V4, V6, V8 | Four validation targets are still "not built", so the suite scores 4 of 9. | not started |

⚠️ **Items 1–4 are the submission. Everything below 6 is the project.** If time
runs short, a judge sees the architecture doc, the video, the README and the
page — not the queue.

**Recently closed, so nobody re-opens them:**

- **The Razorpay ladder, rungs 0–3.** Done 30 August 2026.
  `scripts/razorpay_ladder.py` sends real requests through the shipped
  transport with no credentials and records what comes back.
  **It found two defects on the money path and one in the test file**, all
  fixed: a refused credential was being recorded as a declined customer
  (**error 28**), Stage 0 never passed the `action_id` its own trail audited so
  the real backend's idempotency key was always the weaker fallback (**error
  29**), and `test_razorpay_mapping.py` advertised a `--mutants` runner that did
  not exist (**error 30**). New gates **R9** and **R10**, and `--mutants` is now
  real at 3/3. Parity still bit-exact 24/24.

- **W7 — transient failures.** Done, **6/8 pre-registered**, and the two that
  broke are worth more than the six that held. `p_transient` + `transient_h` in
  `w3.balance_trace`, guarded and inert at 0.0, holds drawn from their own
  generator so the transient world is the base world PLUS holds.
  `agent/tests/test_transient_sweep.py`. **It did not licence a
  recalibration** — see the W7 section below and `NOTES.md`, 30 August.
- **W0 — the recovery-rate metric.** Done. `agent/metrics.py`,
  `test_recovery_metric.py` (5 checks, 5 mutants), `test_recovery_rates.py`.
- **W2 — insolvent customers.** Done, 5/5 pre-registered.
  `p_missed_credit`, guarded and inert at 0.0.
- **The fixed-schedule baseline.** Done. `agent/policy/fixed_schedule.py`.
- **M1 and M4B — ~~the two Stage 0 rules with no working test~~.** **FIXED
  30 August 2026.** M1 runs its mutant at `cap_override=2` so the attempt-cap
  counter actually binds; the `pending` and `represent` mutants no longer
  increment the counters they are graded on. **The suite went from 6 red gates
  to 4, and from 1 vacuous to 0.** All four remaining reds are findings, not
  debt — see `sim/known_failures.txt`.

**Do not re-add an item to the README's Limitations section because it is
open.** Open work belongs here. The README lists only what cannot be fixed from
where this project stands: no obtainable real data, unpublished decline
frequencies, unresolved law, two structurally-unfittable calibration gates, and
compute-bound sample size.

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

✅ **SOLVED 30 August 2026.** `realistic` = **`pop_spend` 0.7850 → 11.87%**;
`stressed` = **0.9627 → 50.13%**. `scripts/solve_operating_point.py`,
`logs/w1_operating_points.json`. ⚠️ **The declared `stressed` point is NOT the
repository default.** The default is `pop_spend=1.05`, which measures 68.71% —
a harder world than `stressed` names. Two different worlds; do not blur them.

Cost: every headline re-runs. The sweep in `02_RESULTS.md` already shows the
shape, so there are no surprises waiting — at ~15% failure the agent is worth
about +6 points, at ~60% about +36.

## W2. Customers who genuinely cannot pay — ✅ DONE 30 August 2026

*Built as `p_missed_credit`, swept {0.00, 0.03, 0.08}, guarded so it is
inert at 0.0. 5/5 pre-registered. `agent/tests/test_insolvency_sweep.py`.
The result argued against adopting it as the default calibration — see
`NOTES.md`, 30 August. Spec kept below for the reasoning.*

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

~~Consequence, and it is the reason to do it: **the diagnosis layer currently
does not move the money** (94.33% against 94.36%) *because there is nothing in
the world to diagnose*. Every failure is insufficient funds, and the frozen
policy is exactly indifferent to decline reasons. Switch the taxonomy on and
terminal codes exist — the case where the LLM already scores **4/4 against the
rule engine's 0/4**, and where no retry can ever succeed. The LLM layer stops
being an overlay that changes nothing and starts being load-bearing.~~

⚠️ **MEASURED 30 August 2026, AND THE PARAGRAPH ABOVE WAS WRONG.** The taxonomy
was switched on. Terminal codes exist. **The LLM arm still does not move the
money — 87.39% against the deterministic 88.54%, i.e. slightly behind**, with
overlapping error bars. So "there is nothing to diagnose" was not a sufficient
explanation, and the LLM layer did not become load-bearing.

**Two things stop that being a clean verdict on the model.** It stops 2.2× as
often and kills fewer mandates, trading collection for survival on a metric
that scores the trade as a loss. And the 150-call cap leaves the arm **93.3%
fallback**, so only 520 of 9,910 money attempts were model-sourced — a design
that caps the model at 5% of decisions cannot measure whether it would move the
money if it ran. Uncapped is ~120,000 calls, roughly **$120**. `NOTES.md`,
30 August.

Rates stay `[GUESS]` and stay swept, exactly as now.

## W7. Transient failures — ✅ DONE 30 August 2026. 6/8 pre-registered.

*Built as `p_transient` + `transient_h` in `w3.balance_trace`: a temporary hold
that blocks the whole available balance for `transient_h` hours, drawn per
customer per day from **its own generator** so the transient world is the base
world plus holds rather than a fresh draw of it. Guarded and inert at 0.0.
Swept {0.00, 0.05, 0.10, 0.20} × {24h, 48h} × `p_missed_credit` {0.00, 0.08},
never picked. `agent/tests/test_transient_sweep.py`; raw table in
`logs/w7_transient_sweep.json`.*

**THE RESULT DID NOT LICENCE A RECALIBRATION, AND THAT WAS DECIDED BEFORE IT
RAN.** `p_transient` ships at 0.0. Full scoring and both broken predictions in
`NOTES.md`, 30 August; the summary:

| id | prediction | outcome |
|---|---|---|
| W7-0 | V1 rises at every non-zero rate | HELD |
| **W7-1** | **at least one swept cell lands V3 inside 20–40%** | **BROKE** — none does. V3 overshoots to 40.64% at the lowest rate swept, and 2 SE is ±2.03, so the miss is inside one standard error |
| **W7-2** | **V7 rises above 60%** | **BROKE** — 42.78% at best, against 41.84% |
| W7-3 | V5 stays above 70% at `p_missed=0.00` | HELD (min 87.25%) |
| W7-4 | V5 stays in 70–85% at `p_missed=0.08` | HELD (81.16–82.92%) |
| W7-5 | the agent's lead over the fixed schedule shrinks | HELD (−33.95 pts) |
| W7-6 | the fixed schedule's edge is larger at 48h than 24h | HELD (+6.00 to +9.02 pts) |
| **W7-7** | **V1 breaks above 15% before V3 reaches its band** | **HELD — 17.50%.** Registered against our own interest |

**Three things it settled.**

1. **Transients are not free.** They enlarge the at-risk set, so V3 rises only
   by breaking V1 — the one target this world hit unfitted. No single world in
   the grid hits more than 2 of 4 targets, and the best is still
   `p_transient=0.00`.
2. **The grid is too coarse at the bottom, and refining it would be the trap.**
   V3 crosses the whole 20–40% band between 0.00 and 0.05. A rate near 0.02
   would very likely hit V1 and V3 together — and V5 (96–97%) and V7 (42–43%)
   are flat in `p_transient`, so that cell would be **2/4 with both unfitted
   targets missing**. That is the (0.70, 0.08) trap in a new costume.
   **The inversion is the honest result: if the published 20–40% band is real,
   this world says the transient rate is under 5% of account-days.**
3. **V7's cause is not transients.** The claim that W7 moves three targets was
   wrong. See the new queue item 2.

### The original statement of W7, kept

## W7 (original). Transient failures — the highest-value world item.

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

## W8. Simulate the two Razorpay decline states

`funds_blocked_by_mandate` (the money is there and another mandate has claimed
it) and `deemed_transaction` (the response was lost; the customer may already
have been charged) are routed by the diagnosis layer and modelled by nothing.
Add both to `DeclineMix` at **swept** rates, exactly as `p_account_shut`,
`p_mandate_broken` and `p_limit` already are. No source gives a frequency for
either, which is an argument for sweeping — not for leaving them unmodelled.

Both are cases a timing score cannot express, so they are where the diagnosis
layer's value should show up. `deemed_transaction` in particular has an
asymmetric cost: retrying risks a double debit, which is worse than not
collecting.

## W9. Measure the non-pooled configuration, and make pooling consent-gated

Cross-merchant pooling — one belief per **customer** rather than per mandate —
is worth **+9.53 pts** and is the largest single component of the result. Its
legality is unresolved, and the review recorded in `01_FACTS.md` (LLM-generated,
unverified, treat with care) points at merchant segregation under the RBI PA
Directions 2025 and at per-merchant mandate identity under NPCI's October 2025
Merchant Identifier Code circular.

**`solo_pop_pd` already exists** — one belief per mandate, no pooling. So:

1. Run it beside the shipping policy and report both numbers everywhere.
2. Make the non-pooled arm the **default** configuration, with pooling an
   explicit opt-in gated on consent.
3. State the price of compliance in points rather than leaving it implied.

That turns an unresolved legal question from a hole in the argument into a
product decision with a measured cost, and it is one run away.

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
`pop_spend=0.80`, no insolvency, no transients — which after W7 is a choice
that has been tested rather than a default that was never questioned. W7 swept
14 alternative worlds and **none of them hits more than 2 of 4 either**, so the
reported calibration is the best available and not merely the first one tried.

⚠️ **W2 landed and the honest reading got harder.** Insolvency brings V5 into
band at `p_missed_credit=0.08`, but V1 breaks there; a (0.70, 0.08) calibration
satisfies V1 and V5 together and then **misses both targets it was not fitted
to** — V3 falls to 18.75% and V7 to 32.86%. Two hits produced by turning two
dials are a curve fit, not corroboration. **The 0.80 / 0.00 pair, where two
unfitted targets hit, remains the stronger evidence and the reported
calibration.** `NOTES.md`, 30 August, and W7 below. The
fixed-schedule arm that made V3 measurable is `agent/policy/fixed_schedule.py`;
`02_RESULTS.md` has the table and the caveats.

⚠️ **"Both misses are W2" was wrong and W7 disproved half of it.** V5 is
insolvency, as W2 showed. **V7 is not**, and it is not transients either: W7
moved it from 41.84% to 42.78% at best. V7 has two causes and both are open —
the due-date/payday offset (W6) and the agent's structural blindness to
transient failures (queue item 2).

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
- **Open with the errors** (there are **thirty**; see `03_ERRORS.md`). Lead
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
and none may be reopened. *(This line used to read "the model is frozen". The
freeze was lifted on 30 August 2026; these four stay closed on their merits,
not on the freeze.)*

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
