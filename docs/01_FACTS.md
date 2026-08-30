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

### ⚠️ How often does a real AutoPay debit actually fail? Added 29 August 2026

**This is the anchor the whole world model hangs on, and the public record is
thin, second-hand and inconsistent with the value in use.**

- `[REPORTED]` **UPI AutoPay debit failure is 8–15%**, against 2–3% for card
  e-mandates. Source: trade-blog summaries read 29 August 2026
  (`bighelpers.in`, `productgrowth.in`). ⚠️ Neither is an operator disclosure
  and neither cites one.
- `[REPORTED]` **Subscription debits succeed ~85% in month 1, decaying to ~70%
  by month 6**, with ~20% debit failure and ~18% mandate cancellation quoted
  alongside. Same class of source, same caveat. The *decay* is the interesting
  half and this project models nothing like it.
- `[VERIFIED]` **Razorpay's own comparison page discusses mandate completion,
  drop-off, retry cost and revenue leakage and publishes no failure rate at
  all.** Read 29 August 2026,
  <https://razorpay.com/blog/upi-autopay-vs-card-e-mandates/>. The only number
  it gives is that card e-mandate "failure rates spiked to 20%+ in some
  categories" after RBI's 2021 authentication rules. **The operator being
  pitched to does not publish the number this project most needs.**
- `[GUESS]` **That the ~30% per-attempt approval anchor below describes ALL
  debits rather than RETRIES of already-failed ones.** The world implements the
  first. If it was ever meant as the second, `pop_spend=1.05` is calibrated
  against the wrong population. **Nobody has resolved which.** This is now the
  highest-value unresolved question in the project — see `NOTES.md`,
  29 August, and the spend sweep in `02_RESULTS.md`.
### Recovery-rate benchmarks — the validation targets. Added 30 August 2026.

**There is no public benchmark dataset for payment retry scheduling.** Checked
30 August 2026: no shared task, no held-out set, no leaderboard, nothing of the
SWE-bench shape. The only formal artifacts in the space are **patents** on
machine-learned dunning, which describe methods and publish no data. So the
project cannot report on a benchmark, and the validation suite in
`04_BUILD_PLAN.md` exists in its place.

**These are the targets that suite scores against.** All `[REPORTED]`, all from
companies that sell recovery software, aggregating non-comparable customer
bases. One source states in its own methodology note that its figures are
"ranges drawn from publicly reported subscription-billing data, not laws."
**They are corroboration, never ground truth, and must never be quoted as a
result.** Their value is that this project did not fit to them.

| target | published value |
|---|---|
| recovery rate, no retries | ~0–10% |
| recovery rate, basic fixed-interval retries | ~20–40% |
| recovery rate, industry median across mixed approaches | ~47.6% |
| recovery rate, smart retries + card updater + email | 70–85% |
| smart retry timing alone vs fixed intervals | ~+25% relative |
| share of recoveries landing inside the first 10 days | ~90% |
| card failure rate / ACH & direct-debit failure rate | ~15% / 3–5% |
| involuntary share of total subscription churn | 20–40% |
| average subscription churn (voluntary / involuntary) | 3.27% (2.41 / 0.86) |
| median annual involuntary churn | 1.25% |
| subscription revenue lost to failed payments, 2025 | ~$129B |

Sources, read 30 August 2026: `retentionlens.com/state-of-involuntary-churn`,
`baremetrics.com/blog/subscription-payment-recovery-benchmarks`,
`recurly.com/research/churn-rate-benchmarks/`, `slickerhq.com`. Baremetrics
states a sample of 119 active Recover customers, May 2026; the others do not
state one.

⚠️ **METRIC MISMATCH, and it blocks every comparison above.** Each figure is a
*recovery rate* — of payments that failed, the fraction eventually collected.
This project reports *cycles collected / cycles due*, which counts cycles that
never failed. **They are different quantities.** W0 in `04_BUILD_PLAN.md` adds
the recovery-rate metric; until it lands, none of this table may be compared to
anything the project reports.

- `[REPORTED]` **~120 million UPI AutoPay mandates are created every month in
  India.** Razorpay's own comparison page, read 29 August 2026. ⚠️ The same
  page also says "3 million UPI Autopay mandates created monthly" — **the two
  figures are inconsistent and the page does not reconcile them.** Use for
  order-of-magnitude framing only, and say which figure you used.

- `[VERIFIED]` **What the calibration costs, measured.** At `pop_spend=1.05` the
  simulated account cannot cover the debit on its due date **53%** of the time
  and the agent beats the baseline by **+36.43** pts. At `pop_spend=0.80` the
  baseline's per-attempt approval is **84.6%** — inside the band the sources
  above report — and the agent is worth **+6.29** pts (2 SE 1.42), which is
  inside the 6–8% industry benchmark below. Reproduce with
  `python scripts/spend_sweep.py`. Table in `02_RESULTS.md`.

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

## Razorpay's own API surface — added 29 August 2026

Read directly from Razorpay's public documentation on 29 August 2026, while
building `agent/execution/razorpay_executor.py`. Except where marked below,
everything in this section is documentation read rather than behaviour
observed. That distinction is
the whole difference between `[VERIFIED]` and "it works".

⚠️ **One thing here IS now behaviour observed, added 30 August 2026.**
`scripts/razorpay_ladder.py` POSTs the real recurring-charge endpoint with no
credential. It returns

```
401 {"error": {"code": "BAD_REQUEST_ERROR", "description": "Authentication failed"}}
```

`[VERIFIED]` — transcript in `logs/razorpay_ladder.json`, reproducible with no
account. **An API-level rejection carries `code` and `description` and nothing
else: no `reason`, no `source`, no `step`, no `metadata`.** That is the fact
that distinguishes it from a payment-level decline, and reading it off the wire
found errors 28 and 29. The claim that a *payment-level* failure carries
`reason`, `source`, `step` and `metadata.payment_id` remains `[REPORTED]` from
the docs — it needs a key to observe.

- `[VERIFIED]` **Razorpay's documented retry schedule for a failed subscription
  charge is T, T+1, T+2, T+3, after which the subscription moves to `halted`.**
  Source: their Payment Retries page, which gives the same schedule for cards
  and for UPI. **This matters twice.** It independently corroborates the NPCI
  attempt cap — 1 presentation plus 3 retries — which this file previously
  carried only as `[REPORTED]` from practitioner accounts. And it means
  `harness.baseline_doc`, the naive comparator this project has measured
  against from the beginning, is a fair rendering of what the vendor actually
  documents rather than a strawman we drew. **This is one of the very few
  places where our comparator is validated against the vendor's own material.**
  ⚠️ Their page makes **no mention** of NPCI attempt caps, pre-debit
  notification, or peak-hour restrictions — only bank holidays for e-mandate.
  So it corroborates the *number* and says nothing about the other four Stage 0
  rules.

- `[VERIFIED]` **Razorpay does not return NPCI decline codes.** The documented
  payment surface carries `error_code` (`BAD_REQUEST_ERROR` / `GATEWAY_ERROR` /
  `SERVER_ERROR`), `error_description`, `error_source`, `error_step`,
  `error_reason`, and `acquirer_data` (which holds `rrn`). `error_reason` is
  drawn from a published list, and Razorpay's own material describes an error
  mapping module that translates NPCI codes into merchant-legible terms — so
  the normalisation is deliberate on their side. **Consequence: our decline
  taxonomy, which is keyed on NPCI codes, needs a `razorpay_reason -> family`
  translation and not a code lookup.** Built as `REASON_FAMILY` in
  `agent/ports.py`.

- `[VERIFIED]` **The published list holds 114 rows, 110 distinct
  `error_reason` values.** Downloaded as `payments_error_reasons.xlsx` from
  razorpay.com and committed verbatim as
  `agent/execution/razorpay_reasons.txt`. Their per-method pages
  (`/docs/errors/payments/upi/`, `/docs/errors/payments/list/`) corroborate the
  UPI subset. ⚠️ The spreadsheet contains a typo — `psp_app_ not_available`,
  with a space — and we cannot tell which spelling the API emits without a key,
  so both are mapped and the extra one is declared in
  `ports.KNOWN_EXTRA_KEYS`.

- `[VERIFIED]` **`funds_blocked_by_mandate` is a real Razorpay decline reason:**
  the money is in the account and another mandate has already claimed it. This
  is **cross-merchant contention appearing in the production error vocabulary
  of the aggregator we are pitching to** — the closest thing this project has to
  external evidence that the problem it was built for exists. A merchant who
  sees only their own debits cannot distinguish it from an empty account.
  ⚠️ `[GUESS]` — **how often it occurs.** Not modelled, not swept, no number
  attached anywhere. `sim/w3.py` is frozen and models no such state.

- `[VERIFIED]` **`deemed_transaction` and `duplicate_rrn_found` are real
  Razorpay decline reasons** meaning the outcome is unknown rather than
  failed — the response was lost and the customer may already have been
  charged. Retrying risks a double debit, which error 19 in `03_ERRORS.md`
  establishes is the worst outcome this system can produce. Motivated
  `AttemptOutcome.pending` and the `INDETERMINATE` family.
  ⚠️ `[GUESS]` — frequency. Not modelled, not swept.

- `[VERIFIED]` **The Payment Downtime API exists, is scoped by instrument, and
  is available with test keys.** `GET /v1/payments/downtimes` and
  `GET /v1/payments/downtimes/:id`; webhooks `payment.downtime.started`,
  `.updated`, `.resolved`. The downtime object carries `method`, `begin`,
  `end`, `status`, `scheduled`, `severity` ∈ {`high`,`medium`,`low`}, and an
  `instrument` object which for UPI holds `vpa_handle` (e.g. `oksbi`, or `ALL`
  when the whole of UPI is affected), `psp`, and `flow`.
  ⚠️ **THIS RETRACTS A CLAIM WE WERE ABOUT TO MAKE.** The outage argument was
  drafted as "their feed is system-wide, ours is bank-shaped". **That is
  false** — their feed is handle-scoped, using the same handle vocabulary as
  `ports.BANK_HANDLES`. See the retraction below.

- `[VERIFIED]` **A PSP is marked down only when *all* the handles associated
  with it are down** — their words. A conservative trigger, appropriate for a
  status page and wrong for an actuator.

- `[REPORTED]` **In test mode, mandate registration and authentication are
  mocked; token creation and charge requests can be simulated.** `success@razorpay`
  and `failure@razorpay` are the test VPAs. Test and live keys are functionally
  identical against separate data.
  ⚠️ `[GUESS]` — **whether test mode returns populated `error_reason` values or
  a single generic failure**, and **whether the Downtime API returns any data
  in test mode.** Their docs invite you to try the endpoint with test keys and
  do not say whether test data is seeded. Unresolvable without a key.

## Data governance

- `[REPORTED]` India's DPDP Act 2023 requires data be used only for the purpose
  for which it was collected; consent must be specific and informed.
- `[REPORTED]` DPDP Rules 2025 phase compliance in; consent/notice provisions
  reportedly not fully effective until 13 May 2027. **Verify against the Gazette
  before repeating.**
- `[GUESS]` Whether a payment aggregator may use Merchant A's transaction
  outcomes to schedule Merchant B's debit for the same customer. **This is the
  legal basis for the moat and it is unresolved.**

  ### What a legal review turned up, 30 August 2026

  ⚠️ **SOURCE DISCIPLINE FIRST. The analysis below is LLM-generated and was not
  written by a lawyer or checked against the primary documents.** It is
  `[REPORTED]` at best, every citation in it is unverified, and it must not be
  presented as legal advice or as a settled reading. It is recorded because it
  is more specific than the previous "nobody has read the terms", and because
  it changes what the project should ship.

  - `[REPORTED]` **No single Indian statute or RBI circular states the
    prohibition directly.** There is no Indian analogue to the Visa
    "transaction laundering" rule for this scenario.
  - `[REPORTED]` **UPI AutoPay mandates are structurally per-merchant.** Each
    is bound to a merchant identity, reportedly validated through a Merchant
    Identifier Code derived from PAN (NPCI circular, October 2025), and the
    1-execution-plus-3-retries framework applies to each mandate in isolation.
    **The NPCI specification contains no cross-mandate or cross-merchant retry
    logic** — a Spotify mandate and a Netflix mandate are separate instructions
    to the payer's bank, and the architecture provides no hook for one to
    influence the other.
  - `[REPORTED]` **RBI's consolidated Payment Aggregator Directions, 2025**
    impose data sovereignty, consumer-protection and transparency obligations,
    and **merchant segregation** across onboarding, KYC and transaction
    monitoring. Using one merchant's outcomes to schedule another's debits is
    cross-merchant data utilisation the framework does not contemplate.
  - `[REPORTED]` **The Account Aggregator framework signals the philosophy**:
    sharing a customer's financial data between entities requires prior,
    specific, revocable consent with purpose and duration defined up front.
  - `[REPORTED]` **Practical exposure** even absent a specific prohibition:
    merchant service agreements scope each mandate to its own billing cycle;
    RBI Ombudsman and NPCI grievance routes exist; the IT Act s43A and the DPDP
    Act require personal data be processed for specified, legitimate purposes.

  **What this means for the project, and it is not "give up".** The pooled
  configuration is worth **+9.53 points in the hard world and +3.47 at
  `pop_spend=0.80`** (W9, `02_RESULTS.md`) and it is the single largest component
  of the result. The response is to stop treating pooling as the default and
  start treating it as **consent-gated**, with the non-pooled configuration
  measured and reported beside it. `solo_pop_pd` — one belief per mandate rather
  than per customer — already exists as a policy arm, so the compliant-by-default
  number is measurable today rather than hypothetical. Queued as **W9** in
  `04_BUILD_PLAN.md`.

  Still unread, and now specifically named: Razorpay's merchant terms and
  privacy policy, the RBI PA Directions 2025 text, and the October 2025 NPCI
  circular on Merchant Identifier Codes.
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

- `[RETRACTED]` **"Razorpay's downtime feed is system-wide, so a bank-shaped
  incident is invisible to it."** Believed on 29 August 2026 and false. The
  Payment Downtime API's `instrument.vpa_handle` names individual handles
  (`oksbi`, `ybl`, …) and reports `ALL` only when the whole of UPI is affected.
  Razorpay already publishes bank-scoped downtime. **The moat argument does not
  get to be "they cannot see bank-level incidents."**
  What survives, stated narrowly enough to be checked, is in
  `agent/execution/razorpay_downtime.py`: their feed measures *their* traffic
  mix (their `flow` field enumerates `collect`/`intent`/`in_app`, and nothing
  says AutoPay mandate execution is what is measured); `severity` is a
  three-valued label and not a rate a scheduler can act on; a PSP is only
  marked down when every handle under it is down; and we have a measured
  detection latency and false-alarm rate where theirs is unstated. **The correct
  posture is complement, not replacement**, and the combined design — theirs as
  a prior, ours as the likelihood — is **not built and not measured.**

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

---

## External literature — read 29 August 2026, abstracts and where stated full text

Read for **evaluation methodology only**. Neither is ported: both are
public-health / recommender resource-allocation settings with the wrong state
space, no money, and no NPCI constraints. Neither has usable public code.

- `[VERIFIED]` **arXiv 2511.09324, "MARBLE: Multi-Armed Restless Bandits in
  Latent Markovian Environment"** — Amiri, Avrachenkov, El Mimouni, Magnússon.
  Submitted 12 Nov 2025, revised 9 April 2026. Augments an RMAB with a latent
  Markov state that switches over time and induces nonstationarity.
  **The latent state is GLOBAL, not per-arm** — verified from Definition III.2
  in the PDF, which indexes every arm's transition kernel
  `p^i_e(s'|s,a) = Pr[S^i_{k+1}=s' | S^i_k=s, A^i_k=a, E_k=e]` by the *same*
  environment state `E_k`, with one latent chain `{E_k}` and transition matrix
  `H`. So "a hidden variable switching and affecting all arms at once" is the
  correct reading and it is the formalisation of our outage.
  **Three limits on how far it may be cited.** (1) It proposes **no change
  detection at all**: its algorithm, synchronous Q-learning with Whittle
  indices, converges to an *environment-averaged* Q-function (Eq. 12) — it
  marginalises the regime out rather than reacting to it, which is the opposite
  of what our context layer does. (2) Assumptions III.1 and III.4 require the
  latent chain to be irreducible, aperiodic and started from its stationary
  distribution; four 6-hour outages in 120 days is a heavily skewed stationary
  distribution and an environment-averaged index would essentially ignore it.
  (3) Its experiments are a recommender-system digital twin. **Cite it for the
  formalism, never for the method.** No public code found.
  https://arxiv.org/abs/2511.09324
- `[VERIFIED]` **arXiv 2604.10177, "A Modularized Framework for
  Piecewise-Stationary Restless Bandits"** — Li, Lin, Hsieh, Huang, 11 April
  2026. (The abbreviation "PS-RMAB" names the *problem*, not the method; the
  method is the modular framework.) Combines an arbitrary RMAB base solver with
  a change detector and a diminishing-exploration schedule. **This is the
  methodology we borrowed:** it defines *excess regret* against a
  **segment-wise oracle that knows the true change points and restarts the base
  algorithm at each one**, so the base solver's stationary performance factors
  out and what remains is the cost of exploration and detection. Verified from
  the abstract and the HTML full text.
  **What is theirs and what is ours.** Their Theorem 4.1 decomposes the bound
  into exploration cost, detection delay, and false alarms / missed detections;
  they instantiate the detector with M-UCB and prove `Õ(√LMKT)`. Our five-way
  partition (delay / missed / dropout / late resumption / false alarm), the
  decision-point time base, and the three gates are **ours** and must not be
  attributed to them. We have no exploration term — the base solver is frozen.
  A repository exists at `github.com/OliverKuanTa/PS-RMAB` but holds **one
  commit and an empty README**, so: **no usable public code.**
  https://arxiv.org/abs/2604.10177

---

## The models we use, and what they cost — 29 August 2026

- `[VERIFIED]` **Z.ai pricing, read directly from
  https://docs.z.ai/guides/overview/pricing on 29 August 2026.**
  USD per million tokens:

  | SKU | input | cached input | output |
  |---|---|---|---|
  | `glm-5.3-flash` (diagnoser) | **$0.075** | $0.015 | **$0.25** |
  | `glm-5.3` (judge) | **$1.4** | $0.26 | **$4.4** |

  The Flash figures are a **50% promotional discount** off list ($0.15 / $0.03 /
  $0.50) running to **24:00 on 9 September 2026 (UTC+8)**, which covers this
  project's whole window. `glm-5.3` carries no promotion. **The judge is ~19x
  the diagnoser per input token**, which is why it runs once per case and the
  diagnoser runs on everything.
  **After 9 September the Flash half of any cost estimate doubles.**

- `[VERIFIED]` **Thinking cannot be disabled on either SKU.** Sending
  `thinking={"type":"disabled"}` returns
  `{"error":{"code":"1210","message":"This model always engages in thinking and
  cannot be disabled; please use low, high, or max"}}`. Read from the API's own
  response, not from documentation.
  **Consequence, measured:** at the default effort the diagnoser returned
  **1,596 completion tokens** for an answer whose schema holds about eighty,
  took 31.7s when it succeeded, and timed out at 45s and 90s on two other
  probes. Ninety sequential calls did not finish in thirty minutes. Every
  request therefore sends `reasoning_effort="low"` with a 2,000-token cap, and
  **every LLM number in `02_RESULTS.md` is a number for that setting.** The
  effort sweep has not been run.

- `[REPORTED]` `glm-5.3` is described as a 743B base model and
  `glm-5.3-flash` as 320B-A18B. Used here only to establish that **the judge and
  the diagnoser are different SKUs**, which `run_eval.py --judge` enforces by
  refusing to run when the two model names are equal. The parameter counts
  themselves are not load-bearing for any claim.

## NPCI decline families — what the codes mean, and what we do NOT know

- `[VERIFIED]` **NPCI publishes "UPI Error and Response Codes" v2.9**, and
  section 3.1 covers the codes a failed AutoPay mandate execution actually
  carries, each classified TD (technical decline) or BD (business decline). Read
  via `pdftotext` from a CDN mirror and cross-checked against an Axis Bank
  mirror of identical content. **Neither URL is `npci.org.in`**, and that should
  be said. Full sourcing in `agent/eval/golden_cases.yaml`'s `research` block.

  The families the agent models, all `[VERIFIED]` as to **meaning**:

  | family | codes | what it means for a retry |
  |---|---|---|
  | insufficient funds | `Z9` | wait for money. The only one `sim/w3.py` models. |
  | technical | `TECH` | the rail glitched. May re-present under the same notification. |
  | account shut | `ZX`, `YE` | **no retry can ever succeed.** Dormant, blocked or frozen. |
  | mandate broken | `VD`, `VI`, `VF` | **no retry can ever succeed.** The merchant must re-authorise. |
  | limit hit | `Z8`, `IE` | **the money IS there.** A smaller debit would work. |
  | ambiguous | `U30` | a catch-all. It names nothing. |

- `[GUESS]` **HOW OFTEN each family occurs is not known and no source was
  found.** The NPCI document names the codes and does not rank them; nothing
  found gives AutoPay-specific decline frequencies. Every rate in
  `agent/execution/sim_executor.py:DeclineMix` is therefore **swept, never
  picked**, and reported as a curve — `02_RESULTS.md`. **`p_limit` is the
  largest single sensitivity in the agent (0.00 / 0.05 / 0.15 →
  0.00 / −2.87 / −13.46 pts) and must never be quoted as a point.**

- `[REPORTED]` **Razorpay operates a test mode** with its own API key pair,
  test UPI VPAs (`success@razorpay` / `failure@razorpay`) and mocked mandate
  registration and authentication. No real money moves. Read 30 August 2026,
  Razorpay docs (`razorpay.com/docs/payments/dashboard/test-live-modes/` and
  the UPI AutoPay S2S recurring pages).
  ⚠️ `[GUESS]` **whether `POST /v1/payments/create/recurring` — the endpoint
  `agent/execution/razorpay_executor.py` calls — is reachable in test mode
  without Razorpay enabling S2S recurring on the account.** Not established.
  Queue item 3 is written as a ladder for exactly that reason.

- `[REPORTED]` **The DPDP Rules, 2025 were notified on 14 November 2025**,
  operationalising the Digital Personal Data Protection Act, 2023, whose s.4
  (consent) and s.5 (purpose limitation) require free, specific, informed
  consent and restrict processing to the purpose communicated at collection.
  Phased compliance. Source: MeitY notification as summarised by Wikipedia and
  PIB, read 30 August 2026; **the primary MeitY text was not read** (PIB
  returned HTTP 403), so this is `[REPORTED]` and not `[VERIFIED]`.
  **Why it matters here:** this, not the RBI PA Directions, is the on-point
  instrument for cross-merchant pooling. The PA Directions''' segregation
  requirements govern **funds**, not customer data. DPDP points at
  consent-gating as the design the law would require — which is what W9 builds.

- `[GUESS]` **The published decline MIX is card, not UPI.** One vendor
  (Churnkey) publishes a breakdown of failed subscription payments: roughly
  half insufficient funds, a quarter to a third risk-management hard flags,
  10-15% card issues. `[REPORTED]`, read 30 August 2026. **It describes CARD
  subscriptions in a mostly non-Indian base, so it is not a UPI AutoPay decline
  distribution and the claim that no source gives AutoPay-specific rates
  stands.** Its use is to anchor the RANGE `DeclineMix` is swept over, so the
  sweep stops being pure invention. Do not calibrate to it.

- `[GUESS]` **How often an account carries a temporary hold.** W7's
  `p_transient` — the per-customer, per-day probability that a lien, a
  momentary shortfall or a pending transaction blocks the balance. **No source
  found gives a rate**, for India or anywhere, so it is **swept
  {0.00, 0.05, 0.10, 0.20} × {24h, 48h} and never picked**, and it ships at
  0.0. The hold duration is equally unsourced and is swept for a specific
  reason: 24h is under the agent's notification lead and 48h is over it, so the
  two cells bracket the interval the outcome is most sensitive to.

  **What the sweep lets us say in the other direction, and it is the useful
  half.** If the published 20–40% fixed-interval recovery band describes
  reality, then this world places the transient rate **under 5% of
  account-days** — because at 5% the fixed schedule already recovers 40.64%.
  That is an inference from a measured curve, it is `[GUESS]` like everything
  else here, and it is adopted nowhere. `02_RESULTS.md`, W7.

- `[GUESS]` **Bank assignment.** `agent/ports.py` spreads customers uniformly
  over `N_BANKS = 8` handles. Real Indian UPI share is heavily skewed and
  nothing found gives **per-bank AutoPay mandate share**, so a skew we invented
  would be a constant with no source. Uniform makes a single-bank outage cover
  about an eighth of customers; a realistic skew would make the largest bank's
  incident more detectable and the smallest bank's less. **Every single-bank
  detection figure is the middle of a range nobody has measured.**
