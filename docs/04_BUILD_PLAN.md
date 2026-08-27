# 04 — BUILD PLAN

Nine days. Day 1 is 27 August. Deadline 5 September.

**Rule: if a task does not move us toward a running agent with an audit trail
and a measured batch result, it is out of scope this week.**

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
- **Open with the six errors.** Lead with error 5, the broken oracle.
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

> **CORRECTION, 28 August 2026 — the paragraph above is overstated and the ML
> study measured it.** The filter matches the functional *shape* of the world.
> It does not match its *parameters*, and in one respect it cannot:
> `BeliefPD.hyp` is a stride-3 grid `[0, 3, …, 27]`, so only **74%** of
> customers have a representable true payday, and among the 38% not paid on
> day 0 only **31.7%** do. `est_salary` is also wrong by ±30% by construction
> and `est_spend` is a population rate. So in-distribution comparisons are
> biased toward Bayes **less than this claimed**, and `ml_index` in fact beats
> `solo_shared_pd` in world A by +4.03 pts (±2.00). Do not repeat "true
> generative model" without this qualification. See `NOTES.md`, 28 August.

An ML baseline plus a **misspecification study** is therefore in scope,
and it is directly judged: "AI Judgment: whether AI tools, LLMs, or agents were
applied appropriately instead of forcing unnecessary tech stacks."

Two new policy variants are authorised, and only these two:
- **`explore`** — uniformly random legal day within the cycle, under the same
  Stage 0 constraints as everything else. Exists to generate an unbiased
  training set, not as a candidate policy.
- **`ml_index`** — identical index policy, constraint layer and metric, with
  **only** the probability engine swapped for an ML estimator. It is an
  ablation, not a product policy.

This does **not** reopen coordinated budgeting, and it does not authorise any
further policy variants. The agent build remains the deliverable.
