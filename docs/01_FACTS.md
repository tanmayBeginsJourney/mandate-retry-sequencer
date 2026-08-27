# 01 — FACTS

Every external fact this project relies on, with source and confidence.
**If a claim is not in this file, it is not established.** Do not add to this
file without a link.

Tags: `[VERIFIED]` primary source read directly · `[REPORTED]` secondary ·
`[GUESS]` our inference · `[RETRACTED]` previously believed, now false.

---

## The competition

- `[VERIFIED]` Track 3 bar: "Build an agent that detects revenue at risk,
  determines the right intervention, and executes a bounded recovery workflow."
  And: "Show measured money recovered across a batch, with compliant escalation,
  stopping rules, and an audit trail."
- `[VERIFIED]` "Mandate retry sequencer" is one of Razorpay's own listed example
  directions for this track. The idea is explicitly in scope.
- `[VERIFIED]` Deliverables: public repo, 5-minute pitch video, architecture doc.
  Shortlisted go straight to a panel. Applications close **5 September 2026**.
- `[REPORTED]` Judged on, among other things, "AI Judgment: whether AI tools,
  LLMs, or agents were applied appropriately instead of forcing unnecessary tech
  stacks" and "Failure Recovery: how the applicant identified system failures at
  runtime and engineered graceful fallbacks."
  Source: coursejoiner.com summary of the programme.
- `[VERIFIED]` Applicants are asked to "explain what broke during development and
  how they recovered from it." **This is why NOTES.md matters.**

## Payments domain

- `[REPORTED]` NPCI restricted UPI AutoPay mandate execution to non-peak hours
  from 1 August 2025. Peak windows: 10:00–13:00 and 17:00–21:30.
  ⚠️ One secondary source frames this as payments being *shifted* out of peak
  rather than *rejected*. Softer than our Stage 0 assumes. **Unresolved.**
- `[REPORTED]` NPCI permits one presentation plus three retries per mandate cycle.
- `[REPORTED]` Pre-debit notification required ~24h before execution; one pending
  notification per mandate at a time. Established in Blocker B1 via practitioner
  reports, not from a regulation read end to end.
- `[REPORTED]` Technical declines may be auto-re-presented under the existing
  notification. Business declines (Z9, insufficient funds) may not — each retry
  needs a fresh notification.
- `[REPORTED]` Technical declines are under 1% of failures. Nearly all failures
  are insufficient funds. This is why a technical-vs-business classifier was
  rejected: the bucket is empty.
- `[REPORTED]` Balance-enquiry API capped at ~50 calls per customer per day. This
  is why we infer balance rather than query it.
- `[REPORTED]` Industry benchmark for retry optimisation uplift: **6–8%**.
  ⚠️ It is unclear whether this is percentage points or relative uplift. Treat
  the ambiguity as live. Do not compare a point-gain to it without saying so.
- `[GUESS]` Razorpay's documented UPI retry schedule (+10 min, +1 hour, same day).
  Read from their docs, but see the RETRACTIONS below — we now believe this
  schedule may not be legally executable post-August 2025.

## Data governance

- `[REPORTED]` India's DPDP Act 2023 requires data be used only for the purpose
  for which it was collected; consent must be specific and informed.
- `[REPORTED]` DPDP Rules 2025 phase compliance in; consent/notice provisions
  reportedly not fully effective until 13 May 2027. **Verify against the Gazette
  before repeating.**
- `[GUESS]` Whether a payment aggregator may use Merchant A's transaction
  outcomes to schedule Merchant B's debit for the same customer. **This is the
  legal basis for our entire moat and it is unresolved.** Nobody has read
  Razorpay's privacy policy, their merchant terms, or the RBI PA/PG directions.
- `[GUESS]` India's Account Aggregator framework may be a useful precedent — an
  RBI-regulated pattern for consented cross-institution financial data sharing.
  Worth an hour. Not yet read.

## Academic

- `[VERIFIED]` Li & Varakantham, "Towards Soft Fairness in Restless Multi-Armed
  Bandits." The often-quoted "Whittle starves ~50% of arms" figure comes from a
  **single illustrative example** in their experimental analysis, not a general
  result. Do not frame our fairness finding as overturning the literature.
- `[VERIFIED]` Niño-Mora & Pellitero García (arXiv 2601.06976) establish Whittle
  indexability for a **two-state** partially observed model with reset dynamics.
  Ours is a continuous distribution over rupees. **Cite as adjacent, not as our
  branch.**
- `[VERIFIED]` That same paper finds a **myopic index rule is highly competitive
  with Whittle on most instances.** We tested this: our Whittle structure beats
  greedy by +7 to +24 points depending on operating point. The comparison is a
  permanent harness row. Keep it.

---

# RETRACTIONS — things this project believed and got wrong

Read these. They are the most likely place for you to reintroduce an error.

- `[RETRACTED]` **"41.7% → 76.3% of mandates collected."** Dead. Came from a
  simulation with no notification model, unrepresentable peak hours, three
  vacuous gates and a broken oracle.
- `[RETRACTED]` **"Pooling observations across merchants is worth +5.4 points."**
  Measured at −0.2 to −0.5 points (not significant) under the original
  architecture. The moat only appears once the belief has a payday posterior.
- `[RETRACTED]` **"Coordinated scheduling is worth +1.5 to +2.1 points."**
  Now measured at **−6 points**, significant, at two operating points. Coordinated
  budgeting is harmful. It has been cut.
- `[RETRACTED]` **"Portfolio is within 1.65 points of the oracle — pooled
  observations get us most of the way to perfect knowledge."** Artefact of an
  oracle that skipped whenever it foresaw another opportunity. Real headroom is
  +18 to +23 points.
- `[RETRACTED]` **"The payday assumption is our biggest liability."** Inverted.
  It is the only assumption in this model family consistent with the observed
  ~30% approval rate. Reclassified as a constraint.
- `[RETRACTED]` **The 6× LTV multiplier.** No longer needed and no longer used.
  Counting billing cycles due over the full horizon prices mandate death
  automatically — a dead mandate forfeits its remaining cycles.
- `[RETRACTED]` **"Spacing retries over days is the main win."** Tested directly:
  spreading the same attempts over four days buys **nothing** (−0.6 pts, n.s.).
  The win is waiting for payday specifically, not spacing.
