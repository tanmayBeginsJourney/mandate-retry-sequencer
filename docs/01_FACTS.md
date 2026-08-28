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

## UPI rail outages — added 28 August 2026, for the agent's context layer

- `[REPORTED]` UPI suffered roughly **995 minutes of total downtime across ~17
  incidents between March 2020 and March 2025**. The longest single incident was
  **~207 minutes (July 2024)**. A March 2025 incident ran **~95 minutes**, and
  the **12 April 2025** incident was reported at **4–5 hours** and described as
  the longest in over three years, attributed to a surge of "Check Transaction
  Status" API calls from PSP banks.
  Sources: Business Standard and ORF Online summaries, read 28 August 2026.
  ⚠️ **The Business Standard page returned HTTP 403 and could not be read
  directly**; these figures come from search-result summaries and from the ORF
  piece, which corroborates that outages occurred in March, April and May 2025
  but gives **no per-incident durations**.
- `[REPORTED]` **NPCI's own uptime dashboard has not been updated past March
  2025**, per ORF. The public record of UPI availability is therefore incomplete
  by the operator's own admission. Treat any downtime total as a floor.
- `[GUESS]` **That any of this applies to UPI AutoPay MANDATE EXECUTION.** Every
  figure above describes UPI end-to-end (P2P/P2M). Nothing found states that
  mandate debits failed during those windows. The read-across is ours.
- `[GUESS]` **The rail monitor's three tuning constants**, all in
  `agent/context/rail_monitor.py`: `window_h=24` (how far back the detector
  looks), `min_attempts=8` (below this it refuses to evaluate), `hold_h=12`
  (minimum time in the OUTAGE state). None is derived from any source and
  **none has been swept.** Detection TPR is non-monotone in population size
  right at the `min_attempts` cliff, so that one matters most.
  The detection threshold `alpha_enter=1e-4` is NOT in this category: it is
  derived from a false-alarm target (~60 evaluations per run), and the
  realised false-alarm rate is measured at 0/48 runs rather than assumed.
- `[GUESS]` **Outage severity — what fraction of attempts fail inside a window.**
  Reported nowhere found. It is swept (0.15 / 0.40 / 0.80) and never picked.
  See `02_RESULTS.md`, the context-layer section.
- `[VERIFIED]` **`w3.BeliefPD.observe(amount, success)` takes no decline code**
  (`w3.py:416`), and `harness.py:270-276` passes `success=False` for a technical
  decline. The failure branch hard-zeroes every balance bin at or above the
  attempted amount (`w3.py:432`). So the filter cannot distinguish a bank glitch
  from an empty account. Read directly from the frozen source.
  **This fact is true and the intervention it motivated does not work — see the
  retraction below.**

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
  **Correction, 28 August 2026:** "no longer used" was wrong when written. It
  was still applied by `w3.index_score` whenever `attempts_left == 1`, with
  `ltv_mult=6.0` defaulted in `harness.run`. Swept over {0, 1, 6, 20}: it is a
  **no-op for every policy**, because `value` is strictly positive and so
  cannot change the index's sign, and non-budgeted policies commit every
  positive-score mandate regardless of rank. It was live and inert. Now
  genuinely removed. `[VERIFIED]` by sweep, NOTES.md 28 Aug 2026.
- `[VERIFIED]` **The 0.92 `p_later` discount is still live and is NOT inert.**
  Unlike the LTV multiplier it multiplies `p_later`, so it changes the sign of
  the index and therefore the wait/attempt decision. Swept 28 Aug 2026:
  `solo_shared_pd` ranges **78.7%–83.1%** over discount 0.80–1.00, with a broad
  plateau from 0.90 to 0.96 and the argmax moving between population sets
  (0.90 train, 0.94 eval). Kept at 0.92 — moving it to the evaluation argmax
  would be fitting a constant to the evaluation set. **Every headline that
  depends on it owes that range.**
- `[RETRACTED]` **"Spacing retries over days is the main win."** Tested directly:
  spreading the same attempts over four days buys **nothing** (−0.6 pts, n.s.).
  The win is waiting for payday specifically, not spacing.

- `[RETRACTED]` **"Technical declines corrupt the belief, so suppressing them
  will help."** Added and retracted the same day, 28 August 2026.
  The *mechanism* is `[VERIFIED]` and stands: `observe()` cannot see a decline
  code and a technical decline hard-zeroes balance mass, and because a pooled
  belief is one object shared by all `k` mandates, one glitch corrupts all `k`.
  The *inference* — that suppressing those updates improves the filter — was
  **measured and is false**. Suppression alone moves ECE from **0.0346 to
  0.0373 at severity 0.80: worse, not better** (`agent/tests/test_outage_ablation.py`,
  8 populations, S1's own `reliability()` binning). Suppressing a technical
  decline removes genuine information along with the noise, and on this measure
  the loss exceeds the gain.
  ⚠️ **The first version of that check reported HELD.** It compared the `both`
  arm against `none`, and `both` turns out to be numerically identical to
  `pause` — so the check was crediting suppression with pausing's effect. It was
  re-scored against the arm that isolates the mechanism, and then it broke.
  *A plausible mechanism verified in source is not evidence that acting on it
  helps.* That is the general lesson and it belongs next to the other twelve.
