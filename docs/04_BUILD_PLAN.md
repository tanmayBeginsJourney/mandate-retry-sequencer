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
never called Razorpay), a static public page (`docs/index.html`), and a
README. **Tanmay is rewriting the README and the page** — treat both as drafts
and do not polish them.

**What a fresh session should actually do next**, in order:

1. **The architecture doc.** It is the only unstarted judged deliverable.
2. **Adjudicate the 19 judge-vs-author disagreements** (open item 0b). That is
   the only validation step the eval has.
3. **Sweep `reasoning_effort`** (open item 0c). Every LLM score is at `low` and
   10/21 may be a floor.
4. Leave `sim/` alone.

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
