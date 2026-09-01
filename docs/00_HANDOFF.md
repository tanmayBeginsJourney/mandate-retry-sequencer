# 00 — HANDOFF

## If you are a fresh session, read this page and then `docs/04_BUILD_PLAN.md`. Nothing else is required to start.

**State, 30 August 2026.**

| | |
|---|---|
| The agent | **complete and measured.** Policy, constraints, context, execution, LLM layer, eval, batch report, and a second executor backend against Razorpay's real API. |
| The world | **actively being extended.** `sim/` is NOT frozen. `04_BUILD_PLAN.md` carries the queue. |
| The public artifacts | `README.md` and `docs/index.html`, both rewritten 29–30 August. Not drafts. |
| The repo | public, `mandate-retry-sequencer`. |
| Test suite | 27 gates, **4 red, 0 vacuous**. All four are findings with written reasons in `sim/known_failures.txt`. |

### The four commands that matter

```
py -3.12 -m agent.batch_report --pops 10 --canonical   # THE DELIVERABLE. ~26s, no key.
py -3.12 agent/tests/test_canonical_world.py --confirm # the four bands, on the frozen world
py -3.12 agent/tests/test_steelman_schedule.py         # the agent vs three fixed schedules
py -3.12 scripts/prove_stage0_refuses.py               # Stage 0 vs the REAL Razorpay client
```

These are the commands verified on this Windows machine. `python` resolves to
Python 3.14 without NumPy here; `py -3.12` resolves to Python 3.12 with the
pinned NumPy 2.4.2.

Everything above runs on a clean clone with numpy alone: no network, no
credentials, no model download. Only `--llm` needs a key.

### What is true about the numbers

> **THE BATCH.** 100 customers × about 2 mandates each, 10 held-out
> populations (seeds 710–719), 120 days, `payday_err=7`, `pop_spend=0.93`,
> 12 burn-in cycles: **99.38% of billing cycles collected against
> `payday_wait`'s 90.29%**, +9.08 pts (2 SE 1.84), ₹7,511,500, **zero Stage 0
> refusals with an independent recount of zero over 8,702 money actions.**
> Reproduce with `py -3.12 -m agent.batch_report --pops 10 --canonical`.
>
> ⚠️ **98.01% / 57.70% / +40.30 / ₹6,203,060 is SUPERSEDED.** That headline
> was measured on a world carrying three defects — no steady state, collected
> mandates handed back at every payday, and an invented mandate count of 5.
> All three are fixed. See NOTES.md, errors 33–35, 1 September 2026.
>
> **THE HEADLINE IS CONDITIONAL ON TWO PARAMETERS, NOT ONE.** `payday_err` was
> always known. `pop_spend` is one minus the household saving rate, and India's
> three published FY25 readings put it between 0.80 and 0.93. **No point in
> that range is declared.** The agent's edge across it runs **+0.93 → +9.08**,
> and below 0.90 the world carries too few at-risk cycles to measure a
> difference at all — 2 of them at 0.80, across a thousand customers.
> `logs/w24_conditional_repaired.txt`.
>
> **THE VALIDATION SUITE is what replaces a public benchmark, because none
> exists.** At `pop_spend=0.93`, n=100: due-date failure **10.50%** (2 SE 0.52,
> published 8–15%, HIT) and fixed-interval recovery **22.15%** (2 SE 1.95,
> published 20–40%, HIT by more than the measurement error), neither fitted,
> both on 100 populations — `py -3.12 agent/tests/test_v3_power.py`.
> Smart-retry recovery 95.24% (published 70–85%, MISS) and recoveries inside 10
> days 42.97% (published 85–95%, MISS), both on 20 populations —
> `py -3.12 agent/tests/test_canonical_world.py --confirm`.
>
> **V3 IS THE REBUTTAL TO V5.** Same world, same calibration, same run seed. V3
> runs a policy this project did not design and lands inside its band; a world
> calibrated easy would lift both. V5 is above its band because the agent is
> better than the published systems, not because the world is soft. Falsified
> if V3 drops below 20% at a larger sample, or holds only where V1 leaves its
> own band. V5 is measured half on the populations its prior was selected on;
> held-out it is 94.43%, still far above the band.
>
> **BOTH MISSES NOW HAVE A MEASURED CEILING BESIDE THEM.** A clairvoyant
> schedule that obeys the four-attempt cap, the 24-hour notice and legal hours
> collects **100%** of at-risk cycles, so the smart-retry row measures the
> agent rather than the world. The same schedule reaches only **51.5%** of them
> inside ten days, below the published band's floor; the agent reaches 42.97%,
> or **83.4% of what is available**. V1 and V7 cannot both be in band in any
> world with one salary credit a month.
>
> **W7 SEARCHED 14 ALTERNATIVE WORLDS AND NONE BEAT IT.** Transient failures
> were swept on 30 August. They move V3 hard, and they break V1 doing it. No
> world in that grid hit more than 2 of 4 targets. The canonical world hits 2
> of 4 as well, and the difference is that every one of its parameters now has
> a named external source rather than a chosen value.
>
> **THE MECHANISM THAT SELLS IT.** The fixed schedule spends all four attempts
> within four days of the due date, hits the NPCI cap while the account is
> empty, and the mandate dies. **Survival 85.3% against the agent's 98.4%.**
> Dunning harder costs the customer, measured.
>
> **AND THE ONE THAT DOES NOT.** Against a steelmanned fixed schedule — two
> attempts at frozen offsets from the same noisy payday estimate — the agent
> **loses** by 9.17 points at `payday_err=1`, 7.83 at ±3 and 6.14 at ±5, ties
> at ±7, and wins from between ±7 and ±10 upward. Real payday uncertainty in
> India is unmeasured and the statutory evidence points at the low side.
> `py -3.12 agent/tests/test_steelman_schedule.py`.

> ⚠️ **SUPERSEDED 1 September 2026 (W24).** The payday prior was refitted
> on the canonical world (`prior_w` 9 -> 5, `prior_floor` 0.5 -> 0.1) and the
> mandate's continuation value was added to the objective
> (`cycle_value=0.6`). **The crossover is now at ±5, not between ±7 and
> ±10**, and the margins against `[1,7]` on held-out populations 710-719 are
> −1.16 at ±1, −0.33 at ±3, +1.15 at ±5, +3.55 at ±7, +23.83 at ±10 and
> +34.41 at ±14. The figures above are the PRE-REPAIR agent and are kept as
> the record. `py -3.12 agent/tests/test_steelman_schedule.py`.

### The three traps a fresh session will otherwise walk into

1. **Do not report 3/4 validation targets by mixing calibrations.** Insolvency
   brings V5 into band at `p_missed_credit=0.08`, but V1 breaks there. A
   (0.70, 0.08) calibration satisfies both and then **misses both targets it
   was not fitted to.** Two hits from turning two dials are a curve fit.
   `pop_spend=0.80, p_missed_credit=0.00, p_transient=0.00` remains the
   reported calibration.
   ⚠️ **W7 offers the same trap in a third dial.** V3 crosses its whole
   published band between `p_transient` 0.00 and 0.05, so a finer grid would
   probably find a cell hitting V1 and V3 together — while V5 and V7 stay flat
   and outside their bands. **Do not refine that grid.** The measured curve is
   in `02_RESULTS.md` and it already says what a refined one would show.
2. **ONE BELIEF PER CUSTOMER, shared by all k mandates.** Build it per-mandate
   and you have silently built `solo_pop_pd`, which is 9.53 points worse, and
   nothing will tell you. `agent/policy/belief_book.py` enforces it.
3. **Every batch measurement runs one process per run** via
   `agent/tests/_parallel.py`. Long-lived processes segfault on this machine and
   the root cause was never found. `06_MODEL_CARD.md` §6a.

### What changed on 30 August, so you do not re-derive it

- **The freeze is lifted.** `sim/` is open. `CLAUDE.md` keeps what the freeze
  got right as ordinary discipline.
- **W0 landed**: the recovery-rate metric, which is the first quantity this
  project has ever produced that is comparable to a published figure.
- **W2 landed**: insolvent customers. After isolating its RNG stream, 3/5
  pre-registered predictions hold; the corrected table is in `02_RESULTS.md`.
- **W7 landed**: transient holds, **6/8 pre-registered, and the two breaks are
  the point.** V3 rises hard but breaks V1 doing it, so no recalibration was
  taken. **V7 did not move** (41.84% → 42.78% at best), which retires the claim
  that W7 would fix it and opens a new queue item: the agent is structurally
  blind to transient failures. It never presents on the due date, and
  `w3.BeliefPD.observe` takes no decline code, so it waits for payday on money
  already in the account — **15.1%** of transient-only cycles collected on the
  first legal day, **48.4%** taking over ten days.
- **A defect in W7's own measurement was found and fixed mid-run.** Holds were
  drawn from the money path's RNG, so turning them on re-drew the whole world.
  Fixing it moved V3 by 1.58 pts and **flipped a pre-registered prediction from
  HELD to BROKE**. The same flaw is present in W2's `p_missed_credit` and is
  flagged in the queue rather than silently repaired — fixing it would restate
  W2's published table.
- **M1 and M4B were FIXED.** Both Stage 0 rules that had no working test in
  `sim/` now have one. The suite went 6 red → 4 red, 1 vacuous → 0. **Do not
  reintroduce the old caveat that "two of the five constraint rules are
  untested" — it is no longer true.**
- **The pooling moat has a specific legal problem**, not a vague one.
  `01_FACTS.md` has the analysis and W9 has the response: measure the
  non-pooled configuration and make pooling consent-gated.
- **Three claims were corrected after being asserted without checking**: that
  both validation misses shared one cause (they are two), that the documented
  retry schedule's compliance gap was unfixable (it is queued), and that W7
  would move three validation targets at once (**it moves one, breaks a second,
  and diagnoses a third**). All three are in `NOTES.md`.

### Where things live

| | |
|---|---|
| What to do next | `docs/04_BUILD_PLAN.md` — **the queue is the first section** |
| What ships and what it is worth | `docs/06_MODEL_CARD.md` |
| Every result with its bias analysis | `docs/02_RESULTS.md` |
| Every external fact with a source tag | `docs/01_FACTS.md` |
| Errors found in this project's own work | `docs/03_ERRORS.md` |
| The interface between agent and simulation | `docs/07_AGENT_BRIEF.md` |
| The architecture document, one page | `docs/08_ARCHITECTURE.md` |
| The append-only decision log | `NOTES.md` |
| The rules you must follow | `CLAUDE.md` |

---

## Decided — do not relitigate


| Decision | Why |
|---|---|
| Track 3, mandate retry sequencing | Razorpay lists it as an example direction |
| Belief over balance **and** payday | Payday posterior is where the moat lives |
| `solo_shared_pd` is the policy, with `w3.FITTED_BELIEF` | Best measured. Pooling worth **+7.32 pts (±2.02)** on the shipping filter, gated as S2a_PD. Unfitted S2a is +9.53 (±1.81). Agent W9 (ungated) is +6.47 (±0.62) at the same calibration and +1.30 (±0.42) at `pop_spend=0.80`. Re-measured 1 Sep 2026; the ±1.36 / +8.34 / +8.46 figures are **superseded** — they were measured on the pre-W24 prior. |
| **No** coordinated budgeting | Measured −6 pts twice. Cut. |
| No LLM on the debit-timing path | **ADR-005** (below). Deliberate, defensible. |
| **Yes** LLM on diagnosis / intervention choice / audit narrative | Needed for the track, and honest |
| Cycle-based metric, no LTV constant | Death priced automatically |
| `payday_wait` is a permanent baseline row | It is what a good rival builds in an afternoon |

## ADR-005 — the one architectural decision cited by number

**There is no ADR document in this repo.** The number is cited in `CLAUDE.md`,
`07_AGENT_BRIEF.md` and the table above, so here it is in full, once:

> **An LLM must never be on the path that decides whether to debit a specific
> customer at a specific moment.** The LLM decides *what* to do and explains
> *why*; the belief filter and its index rule decide *when*; the constraint
> layer decides *whether it is allowed*.

*(Earlier revisions said "the bandit policy". `w3.index_score` is a one-step
lookahead in the style of a Whittle index — no exploration/exploitation trade,
no learned index, no indexability proof — so "bandit" overclaimed and was
corrected on 30 August 2026.)*

It is enforced by construction, not by review: `ports.Diagnosis` has **no
temporal field**, so the narrative layer's only output type physically cannot
express a debit time. `agent/eval/injection.py:diagnosis_has_temporal_field()`
asserts that by inspecting the type, and fails the day someone adds one.
`agent/llm/governance.py` scans the merchant-facing prose for times separately,
because a justification that recommends an hour is an LLM on the timing path via
the merchant's eyeballs.

## ~~THE MODEL IS FROZEN~~ — THE FREEZE WAS LIFTED ON 30 AUGUST 2026

⚠️ **This section contradicted the top of this same file for a day, which is
exactly the failure a cold-start document must not have.** The freeze is
**lifted**. `sim/` is open, and the world model has been the main line of work
since 30 August — W0, W2 and W7 all changed `sim/w3.py` after this paragraph
was written.

Tag `model-frozen` still marks the 28 August state and is still the reference
point for "what the reported numbers were measured on". The discipline the
freeze encoded survives it: **re-run before you re-quote**, one change at a
time, and a guarded default so existing numbers do not move.

The original text, kept as the record:

> No changes to `sim/w3.py`, `sim/harness.py` or the fitted constants before
> 5 September without explicit approval. `agent/` is built and its probability
> engine is `w3.BeliefPD` under `w3.FITTED_BELIEF`. The next session's work is
> the architecture document and the pitch, not more code. See `CLAUDE.md`.

## Resolved 28 August 2026

- **The belief filter's three hand-set values were never fitted.** Fitting them
  (stride, payday prior, cross-mandate spend correction) is worth **+11.66 pts
  (±1.61)**, gated as S4. More than the entire ML programme produced.
- **Is an ML model a better timing brain?** No. Against a *fitted* filter it
  loses in all six worlds by 5–12 points, and a Bayes+ML hybrid is worse than
  the filter alone. The earlier +4.03 ML win was a fitted model beating an
  unfitted one. `NOTES.md`, 28 August.
- **Does pooling survive a properly fitted filter?** Yes, and it shrinks each
  time the filter gets better. The first two figures below are **superseded**
  and are kept only to show the direction: +8.20 on the unfitted filter → +8.46
  (±1.07) on the 31 August prior → **+6.47 pts (±0.62)** on the shipped one, all at
  `pop_spend=1.05` in the agent (W9). At `pop_spend=0.80` it is **+1.30
  (±0.42)**. **The shrink is entirely the un-pooled arm improving, not the
  pooled arm getting worse.** In the gate: pooled 94.39% → 94.45%, own 86.05% →
  87.13%. In the agent at 1.05: pooled 95.37% → 97.60%, not-pooled 86.91% →
  91.13%. A sharper payday posterior gives a one-mandate belief more of what it
  previously had to borrow from the other four, so the moat is now measured
  against a stronger baseline. Re-measured 1 Sep 2026,
  `logs/w26_gate_full_moat_remeasure.txt` and
  `logs/w26_w9_pooling_consent_remeasure.txt`.
- **Suite runtime.** ~27 min → **~100s full / ~34s fast on an idle machine**,
  output proved byte-identical by T9. T9 locks unfitted policies and
  `solo_pop_pd` / `solo_shared_pd` / `portfolio_pd` under `FITTED_BELIEF` at
  both operating points (34 configs). The full-tier figure is **load-dependent**: 100/102/98s
  idle, 223s with other work in flight. `CLAUDE.md` has the measurement.
- **The 6× LTV multiplier and the 0.92 discount.** Swept. LTV was inert and is
  removed; the discount is live and now reported as a range.
- **`harness.py:554-560`** (was `:325` when this was written) — the placebo
  policies were scoring mandates 2..k off
  mandate 1's belief. Fixed. S2b now reads −14.09 (the −14.51 this line used
  to quote is stale; see `sim/known_failures.txt`).
- **Does the day-0 payday prior create a cliff when the population differs?**
  No. 3.04 pts of gentle degradation across `payday_day0_frac` 0.8→0.2 (re-measured on the shipped belief; the superseded figure was 4.84), and the
  margin over `ml_index` *grows* from +5.87 to +14.20 as the population moves
  away from the fit.

## Resolved 29 August 2026

- **What should the LLM layer be scored on?** Detection, against an oracle at
  the true change points — `agent/tests/test_detection_benchmark.py`. Then the
  diagnosis eval against 40 registered cases. Both built and measured.
- **`min_attempts` swept.** At n=100 over {4, 8, 16} the loss does **not order
  cleanly** on either metric. It stays at 8 because nothing measured argues for
  moving it — a weaker reason than was hoped for, and the true one.
  `window_h=24` and `hold_h=12` remain `[GUESS]` and unswept.
- **The demo printed Stage 0 violations that never happened.** `AuditLog`
  append-mode plus a fixed path meant a second run audited two concatenated
  runs as one. `LogFileNotEmpty` now makes it an exception at open time.
  Error 18.
- **The LLM layer is measured and routed.** Production calls the diagnoser only
  when `merchant_note` is set (`agent/llm/routed_diagnoser.py`). Rules own
  every other tick. Eval on that subset: **4/4** rule vs **2/4** routed LLM on
  registered `merchant_note` cases (31 August). Full 40-case table is historical.
  **Money headline is deterministic** — no LLM column in `batch_report`.
- **`WAIT` cut** — and the premise was true only of the rule engine. Error 20.
- **`RuleBasedDiagnoser` proposed a second debit on a collected cycle** (GC-40).
  Fixed in the component; the property had been living in the caller. Error 19.
- **The detection benchmark's own gate found a defect in its own metric.**
  G-1b is kept RED; G-1c reads the verdict. Error 17.
- **Can the agent run against a real payment API without changing anything
  else?** Yes, and it is built. `agent/execution/razorpay_executor.py`
  implements the same `ports.Executor` protocol; the switch is one argument in
  `batch.py`. ~~**Nothing in it has ever talked to Razorpay**~~ — **CORRECTED
  30 August 2026: `scripts/razorpay_ladder.py` sent real requests and took a
  live 401, which found errors 28 and 29. What is still true is narrower —
  no request has ever been AUTHENTICATED, so Razorpay has never read one of
  our request bodies.** See
  `06_MODEL_CARD.md` §6b-2 for the line between gated and untested.
- **Does Razorpay return NPCI decline codes?** No. It returns its own
  normalised `error_reason` from a published list of 110, and describes a
  mapping module that does the translation on their side. Our taxonomy was
  keyed on the wrong vocabulary; the map now lives in `ports.py`.
- **Does Razorpay's documented retry schedule corroborate the NPCI attempt
  cap?** Yes — T, T+1, T+2, T+3, then `halted`. `[VERIFIED]`. That is 1
  presentation plus 3 retries, which this project had only as `[REPORTED]`
  from practitioner accounts, and it means `baseline_doc` is a fair rendering
  of what the vendor documents rather than a strawman.
- **Is their Payment Downtime feed system-wide?** **No, and we said it was.**
  It is scoped by `vpa_handle`, using the same handle vocabulary as ours.
  Retracted in `01_FACTS.md`; error 26.
- **`AttemptOutcome` gained `pending`.** Real UPI has a state where nobody
  knows whether the debit landed. Optional, defaults to the old behaviour,
  `SimExecutor` never sets it, **parity still bit-exact 24/24**.

## Open — genuinely unresolved

0a. **Why this machine segfaults on long-lived processes. ROOT CAUSE NOT
   FOUND.** Many `agent.batch.run_once` calls in one process crash (SIGSEGV,
   sometimes SIGILL) at a different point each time; a test that passed 24/24
   in the morning segfaulted before printing a line that afternoon, unchanged.
   **Contained, not fixed** — every measurement runs one process per run via
   `agent/tests/_parallel.py`. See `06_MODEL_CARD.md` §6a.
0b. **19 judge-vs-author disagreements await human adjudication.** That is the
   validation step and the only one. `python agent/eval/run_eval.py --llm
   --judge --replay` prints the table with GLM-5.3's reasoning, offline, for
   $0.00. **The pattern to argue about:** on Z9 bursts with attempts left the
   author says RETRY/ESCALATE and BOTH model and judge say NUDGE. Two models
   agreeing is not evidence — they may share a pre-training prior.
0c. **`reasoning_effort` is unswept, and every LLM score depends on it.**
   Thinking cannot be disabled on these SKUs (API code 1210), so every score is
   for `low` with a 2,000-token cap. A higher setting may score better;
   **10/21 may be a floor.** First thing to sweep.

✅ **SWEPT 30 August 2026, and the caveat pointed the WRONG WAY.** `low` is the BEST of the three permitted settings on the ambiguous set: **10/21 at `low`, 7/21 at `high`, 9/21 at `max`** — and `max`'s row is the rule engine, because 32 of 50 calls hit the token cap and fell back. **10/21 is not a floor.** What IS invariant across all three settings is the terminal-code result, 4/4 against the rule engine's 0/4, which is the claim the LLM layer actually rests on. ⚠️ **And the marginal claim is not robust: at `high` the model LOSES to the rule engine on ambiguous cases, 7/21 against 9/21.** `02_RESULTS.md`, the reasoning_effort sweep.

0d. **The LLM is called 119,667 times over a 4-population batch** — once per
   live mandate per decision hour. It runs under a hard cap of 120 live calls
   per run with the rule engine handling the rest, giving a **94.8% fallback
   rate**. That is the design, not a workaround, but it means the batch's LLM
   arm is **95% deterministic** and must never be described as "the LLM's
   number".

0e. **Resolved 31 August 2026.** `batch_report.py` labels the two quantities
   `gate refused` and `illegal executed` and states that zero in both columns
   is not agreement. `scripts/prove_stage0_refuses.py` remains the test that
   exercises the auditor after an injected bypass.
0f. **Gate I2's matcher is broader than its stated intent.** It is
   `p.endswith(".py")` over every file under `agent/`, so it also forbids a
   module *inside* `agent/execution/` from importing a sibling. The rule's
   stated intent is "only `stage0.py` may HOLD an executor" and its named
   mutant trips either way. **Not changed** — rule 1 says a test I believe is
   wrong goes in `NOTES.md` and gets asked about, and the workaround (putting
   shared vocabulary in `ports.py`, which is where it belonged anyway) was
   free. Tanmay's call.
0g. **The Razorpay backend is untested against Razorpay.** No key, no request
   ever sent. `06_MODEL_CARD.md` §6b-2 has the gated/untested split.

1. **How accurately can payday be estimated in reality?** Still unmeasured, and
   still the one fact that decides whether the sophisticated version is worth
   building. What IS now measured is where the crossover sits, and it moved
   once the baseline was steelmanned. Against the frozen `[1,7]` schedule the
   agent loses by 9.17 points at ±1, 7.83 at ±3 and 6.14 at ±5, ties at ±7
   (−1.34), and wins by +20.73 at ±10 and +30.67 at ±14
   (`agent/tests/test_steelman_schedule.py`). So the open question is narrow
   and concrete: **is real payday uncertainty above or below about 8 days?**
   Resolution unchanged: make the agent learn payday online and expose its own
   uncertainty, so the posterior width is a product feature rather than an
   assumption. Do not chase the number externally.
2. ~~**Six gates are red on a clean checkout**~~ **Four are: S1, S1_PD, S2b,
   S2_LEGACY** (27 gates: 4 FAIL, **0 VACUOUS**, 23 pass). M1 and M4B were
   repaired on 30 August; the original text is kept below as the record.
   **M4B is new, 28 Aug, and is the one to read first:** gate M4's mutant
   increments `V.pending` itself, so the pending-notification constraint has
   no working test -- 1066 counted, 1066 self-written, 0 independent. With M1
   already vacuous that makes **two of the five Stage 0 rules unproven**.
   Neither may be claimed in the pitch. See error 11.


✅ **RESOLVED 30 August 2026.** M1 now runs its mutant at `cap_override=2` so the attempt-cap counter binds; the `pending` and `represent` mutants create illegal state instead of writing the counters they are graded on. **All five Stage 0 rules now have a working test in `sim/`, M4B is green, and the suite has 0 vacuous gates.** The paragraph above is kept as the record of what was wrong.
   **S1 measures the wrong filter** — it runs `portfolio`, which carries the
   point-estimate `w3.Belief`, not the `w3.BeliefPD` the project recommends.
   S1_PD was added with the identical threshold on the real filter and also
   fails. **The gate reports ECE 0.026** (populations Pc0-Pc2); the 0.040 that
   used to appear beside it is `sim/fair_audit.py`'s number on *different*
   populations. They are two measurements, not a range. The break looks
   structural:
   a diffusion that leaks through the modelled balance floor, and a fixed 3-tap kernel standing in for the
   world's hourly spend jitter.
   Historical, from the 27 August rebuild:
   - **S1** belief calibration: ECE 0.091, reliability curve not monotone.
     Overconfident in the top decile. Declared at handoff.
   - **M1** attempt-cap mutant is VACUOUS: the cap counter has no working
     test behind it. Found 27 August 2026, undeclared until then.
   - **S2** placebo pooling, the negative control on the central claim,
     fails. Found 27 August 2026, undeclared until then. See item 6.
   Reasons are in `sim/known_failures.txt`; the gate is `sim/gate.py`.
3. **Is cross-merchant pooling legal?** See `01_FACTS.md`. Unread: Razorpay's
   privacy policy, their merchant terms, RBI PA/PG directions.
4. **Peak-hour rule: hard reject or time-shift?** Sources disagree. Stage 0
   assumes hard reject, which is the conservative choice.
5. **Does exhausting attempts cancel a mandate, or halt it?** One news report
   says cancel; Razorpay's own docs suggest halt-and-manually-chargeable.
6. ~~**Does pooling actually beat placebo pooling?**~~ **RESOLVED 27–28 August.**
   The old S2 was testing the point-estimate trio, not the payday-posterior
   trio the moat is claimed for. Rebuilt as three arms. **S2a — the moat —
   passes at +9.53 pts (±1.81)** on the unfitted filter and **+7.32 pts (±2.02)
   as S2a_PD on the filter that ships** (re-measured 1 September 2026; it read
   +8.34 ±1.36 under the pre-W24 prior, and that figure is **superseded**).
   What remains open is
   narrower: **S2b
   shows the placebo is not a clean control** (−14.09 pts), because it injects
   *wrong* observations rather than neutral extra ones. A label-shuffle and a
   posterior-predictive control were both built and measured on 31 August;
   extra unmatched `observe()` calls damage this filter either way (NOTES.md).
   Neutrality versus own is the wrong property for any control that feeds
   non-true outcomes. Quote S2a / S2a_PD and **never** S2c.

## The three-way split — keep this true in the code

- **LLM** decides *what* to do and explains *why*
- **Belief filter + index rule** decides *when* (not a "bandit" — see ADR-005
  above)
- **Constraint layer** decides *whether it is allowed*

## What "done" looks like on 5 September

- [ ] Public repo, commits visible across the whole period — **push after
      every session; local has run ahead of the remote before**
- [x] Agent runs end to end over a batch of synthetic merchants
- [x] One number: money recovered, with `payday_wait` printed beside it
      — **99.38% vs 90.29%, +9.08 pts, reproduced on a clean clone in 26s**
- [x] Audit log: every money action, with reason, constraint check, outcome
- [x] Stopping rules explicit and demonstrable
- [x] One failure handled gracefully, on camera —
      `scripts/prove_stage0_refuses.py` is the one to film
- [x] **Architecture doc, one page** — `docs/08_ARCHITECTURE.md`, written
      30 August 2026. Linked from the README map and the public page's footer.
- [ ] 5-minute pitch video, opening with the errors (there are **thirty-two**)
- [x] `NOTES.md` full of real mess
- [x] A public page — `docs/index.html`, static, Pages from `/docs`.
      **Rewritten 29 August; no longer a draft.**
- [x] README rewritten 29 August; no longer a draft
- [x] **Repo pushed to a public GitHub remote.** `origin` is
      `github.com/tanmayBeginsJourney/mandate-retry-sequencer`.
      ⚠️ **Local is AHEAD of `origin/main`** — check `git status` before
      assuming a judge can see the latest work. A commit that exists only
      locally is not a deliverable.
- [ ] **World v2** — realistic operating point, insolvent customers, mandate
      cancellation, success decay. Spec in `04_BUILD_PLAN.md`. **In progress,
      not a caveat.** W0, W2 and W7 have landed; W1, W3, W4, W6, W8 have not.
- [x] **The validation suite** — the simulator scored against published figures
      it was never fitted to, in place of a public benchmark, because none
      exists. **2 of 4 measurable targets hit, neither fitted**, and the
      calibration has since survived W2's and W7's attempts to unseat it:
      across every alternative world swept, none scores better.
      `04_BUILD_PLAN.md`. V2, V4, V6, V8 are still unbuilt.
- [ ] **Judge-facing docs**, plain English, engineering and business impact.
      Written AFTER World v2 lands, so it is written once.
