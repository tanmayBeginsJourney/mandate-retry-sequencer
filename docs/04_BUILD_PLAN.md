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
- Further simulation research beyond one honest batch number
- Chasing the payday parameter through external sources
- Rewriting the Notion knowledge base
- Reintroducing coordinated budgeting
- Any new policy variant
