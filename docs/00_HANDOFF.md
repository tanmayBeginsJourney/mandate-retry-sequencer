# 00 — HANDOFF

## Where things stand, 29 August 2026 (updated end of day)

Research and simulation: **done and FROZEN.** Stop doing it.
Production code: **`agent/` is complete and every layer is measured.**
Constraint layer, action space, context layer, detection benchmark, decline
taxonomy and **the LLM layer** all exist, all run, and all have numbers.
Added later on 29 August: a **second executor backend** against Razorpay's real
API, a **public page**, and a **README**.
Deadline: **5 September 2026 — 7 days from today.**

**The commands that matter:**

```
python -m agent.batch_report --pops 4      # THE DELIVERABLE. ~50s, no key.
python -m agent.batch_report --llm         # the LLM overlay. ~15 min, needs a key.
python agent/eval/run_eval.py --llm --judge --replay   # the eval, offline, $0
python scripts/prove_stage0_refuses.py     # Stage 0 vs the REAL Razorpay client
python agent/tests/test_razorpay_mapping.py            # the backend gates, 44/44
python scripts/build_page_data.py --check  # the page's data is reproducible
```

**Only `--llm` needs a key.** Everything else in that list runs on a fresh
clone with numpy alone: no network, no model download, no credentials. The
eval's model responses are committed as caches, so `--replay` reproduces the
whole thing offline for $0.00. `--llm` wants a Z.ai key in `.env` at the repo
root (`ZAI_API_KEY=...`, gitignored, read automatically); without one its LLM
arm silently becomes the deterministic arm, and the report says so.
`07_AGENT_BRIEF.md` §0 has the full table.

⚠️ **`prove_stage0_refuses.py` needs no RAZORPAY key either**, which is the
point of it: Stage 0 adjudicates before the executor is reached, so the refusal
can be shown against the real client with the network unplugged.

> **THE BATCH NUMBER.** 100 customers x 5 mandates over 4 held-out populations,
> 120 days, `payday_err=7`: **94.36% of billing cycles collected against
> `payday_wait`'s 57.70% — +36.66 pts (2 SE 2.47, SIG), Rs 5,994,430**, with
> **zero Stage 0 refusals and an independent recount of zero over 8,954
> executed money actions**. `payday_wait` is a permanent row and **at
> `payday_err` of about +/-1 day it BEATS us.**
>
> **THE LLM.** `glm-5.3-flash` diagnosing, `glm-5.3` judging (a different SKU;
> the harness refuses to run if they are equal). It **beats the rule engine on
> ambiguous cases 10/21 vs 9/21 and on terminal decline codes 4/4 vs 0/4**,
> loses on clean cases 13/19 vs 19/19, and **does not move the batch money**
> (94.33% vs 94.36%). $0.26 spent. **Every LLM score is at
> `reasoning_effort=low`, which is unswept.**
>
> **THE CAPABILITY CLAIM, unchanged.** An aggregator detects a rail outage a
> single merchant structurally cannot: 22.5 attempts per 24h window against
> 0.38, against a floor of 8. `02_RESULTS.md`.

**If you are the next session, read `docs/07_AGENT_BRIEF.md` first, then
`docs/06_MODEL_CARD.md`.** Between them they carry everything needed to build
the agent without reading the simulation code.

Four simulation harnesses have been written. The current one (`sim/`) is sound,
tested and frozen. The old one (`legacy/`) is defective and frozen.

## Decided — do not relitigate

| Decision | Why |
|---|---|
| Track 3, mandate retry sequencing | Razorpay lists it as an example direction |
| Belief over balance **and** payday | Payday posterior is where the moat lives |
| `solo_shared_pd` is the policy, with `w3.FITTED_BELIEF` | Best measured. Pooling worth +9.61 pts (±1.67) on the FITTED filter (ungated); S2a gates +9.53 on the UNFITTED one — see error 13 |
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
> *why*; the bandit policy decides *when*; the constraint layer decides
> *whether it is allowed*.

It is enforced by construction, not by review: `ports.Diagnosis` has **no
temporal field**, so the narrative layer's only output type physically cannot
express a debit time. `agent/eval/injection.py:diagnosis_has_temporal_field()`
asserts that by inspecting the type, and fails the day someone adds one.
`agent/llm/governance.py` scans the merchant-facing prose for times separately,
because a justification that recommends an hour is an LLM on the timing path via
the merchant's eyeballs.

## THE MODEL IS FROZEN (tag `model-frozen`, 28 August 2026)

No changes to `sim/w3.py`, `sim/harness.py` or the fitted constants before
5 September without explicit approval. `agent/` is built and its probability
engine is `w3.BeliefPD` under `w3.FITTED_BELIEF`. The next session's work is
the architecture document and the pitch, not more code. See `CLAUDE.md`.

## Resolved 28 August 2026

- **The belief filter's three hand-set values were never fitted.** Fitting them
  (stride, payday prior, cross-mandate spend correction) is worth **+11.66 pts
  (±1.61)**, gated as S4. More than the entire ML programme produced.
- **Is an ML model a better timing brain?** No. Against a *fitted* filter it
  loses in all six worlds by 5–12 points, and a Bayes+ML hybrid is worse than
  the filter alone. The earlier +4.03 ML win was a fitted model beating an
  unfitted one. `NOTES.md`, 28 August.
- **Does pooling survive a properly fitted filter?** Yes, and it grows:
  +8.20 → **+9.61 pts (±1.67)**.
- **Suite runtime.** ~27 min → **~100s full / ~34s fast on an idle machine**,
  output proved byte-identical by T9 — but T9's lock covers only the UNFITTED
  filter (error 13). The full-tier figure is **load-dependent**: 100/102/98s
  idle, 223s with other work in flight. `CLAUDE.md` has the measurement.
- **The 6× LTV multiplier and the 0.92 discount.** Swept. LTV was inert and is
  removed; the discount is live and now reported as a range.
- **`harness.py:554-560`** (was `:325` when this was written) — the placebo
  policies were scoring mandates 2..k off
  mandate 1's belief. Fixed. S2b now reads −14.09 (the −14.51 this line used
  to quote is stale; see `sim/known_failures.txt`).
- **Does the day-0 payday prior create a cliff when the population differs?**
  No. 6.95 pts of gentle degradation across `payday_day0_frac` 0.8→0.2, and the
  margin over `ml_index` *grows* from +5.30 to +12.03 as the population moves
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
- **The LLM layer is measured.** `glm-5.3-flash` diagnosing, `glm-5.3` judging.
  10/21 ambiguous (rule engine 9/21), 4/4 terminal (rule engine 0/4), 13/19
  clean. **It does not move the batch money.** $0.26 spent.
- **`WAIT` cut** — and the premise was true only of the rule engine. Error 20.
- **`RuleBasedDiagnoser` proposed a second debit on a collected cycle** (GC-40).
  Fixed in the component; the property had been living in the caller. Error 19.
- **The detection benchmark's own gate found a defect in its own metric.**
  G-1b is kept RED; G-1c reads the verdict. Error 17.
- **Can the agent run against a real payment API without changing anything
  else?** Yes, and it is built. `agent/execution/razorpay_executor.py`
  implements the same `ports.Executor` protocol; the switch is one argument in
  `batch.py`. **Nothing in it has ever talked to Razorpay** — see
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
0d. **The LLM is called 119,667 times over a 4-population batch** — once per
   live mandate per decision hour. It runs under a hard cap of 120 live calls
   per run with the rule engine handling the rest, giving a **94.8% fallback
   rate**. That is the design, not a workaround, but it means the batch's LLM
   arm is **95% deterministic** and must never be described as "the LLM's
   number".

0e. **`batch_report.py` still prints `agree? yes` over two zeros.** The gate's
   refusal count and the auditor's violation count are **not the same
   quantity** — one is what was stopped, the other is what illegally happened —
   and in a clean run they are both zero for unrelated reasons. That is
   presented as a two-implementation cross-check and it is not one in that
   regime. **Error 25.** The wording has NOT been changed, because the honest
   fix is deciding what the panel should show when nothing illegal happened,
   which is a judgement about the deliverable. `scripts/prove_stage0_refuses.py`
   demonstrates the distinction and the auditor genuinely binding.
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
   building. What IS now measured is where the crossover sits: the system
   loses to `payday_wait` at ±1 day, ties at ±3, and wins by +23.6 at ±5
   rising to +53.2 at ±14 (`06_MODEL_CARD.md` §2). So the open question is
   narrow and concrete: **is real payday uncertainty above or below ~4 days?**
   Resolution unchanged: make the agent learn payday online and expose its own
   uncertainty, so the posterior width is a product feature rather than an
   assumption. Do not chase the number externally.
2. **Six gates are red on a clean checkout: S1, S1_PD, M1, M4B, S2b,
   S2_LEGACY** (25 gates: 5 FAIL, 1 VACUOUS, 19 pass).
   **M4B is new, 28 Aug, and is the one to read first:** gate M4's mutant
   increments `V.pending` itself, so the pending-notification constraint has
   no working test -- 1066 counted, 1066 self-written, 0 independent. With M1
   already vacuous that makes **two of the five Stage 0 rules unproven**.
   Neither may be claimed in the pitch. See error 11.
   **S1 measures the wrong filter** — it runs `portfolio`, which carries the
   point-estimate `w3.Belief`, not the `w3.BeliefPD` the project recommends.
   S1_PD was added with the identical threshold on the real filter and also
   fails. **The gate reports ECE 0.026** (populations Pc0-Pc2); the 0.040 that
   used to appear beside it is `sim/fair_audit.py`'s number on *different*
   populations. They are two measurements, not a range. The break looks
   structural:
   no balance floor at zero, and a fixed 3-tap kernel standing in for the
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
   passes at +9.53 pts (±1.81)** and survives the belief refit (+9.61, ±1.67
   on held-out populations). What remains open is narrower: **S2b shows the
   placebo is not a clean control** (−14.09 pts), because it injects *wrong*
   observations rather than neutral extra ones. A control that matches the
   update count without supplying misinformation — label-shuffled observations
   at the matched base rate — has still not been built. Until it is, quote
   S2a and **never** S2c.

## The three-way split — keep this true in the code

- **LLM** decides *what* to do and explains *why*
- **Bandit policy** decides *when*
- **Constraint layer** decides *whether it is allowed*

## What "done" looks like on 5 September

- [ ] Public repo, commits visible across the whole period
- [x] Agent runs end to end over a batch of synthetic merchants
- [x] One number: money recovered, with `payday_wait` printed beside it
      — **94.36% vs 57.70%, +36.66 pts, reproduced on a clean clone in 47s**
- [x] Audit log: every money action, with reason, constraint check, outcome
- [x] Stopping rules explicit and demonstrable
- [x] One failure handled gracefully, on camera —
      `scripts/prove_stage0_refuses.py` is the one to film
- [ ] Architecture doc, one page
- [ ] 5-minute pitch video, opening with the errors (there are **twenty-six**)
- [x] `NOTES.md` full of real mess
- [x] A public page — `docs/index.html`, static, Pages from `/docs`.
      **Rewritten 29 August; no longer a draft.**
- [x] README rewritten 29 August; no longer a draft
- [ ] **Repo actually pushed to a public GitHub remote.** `git remote -v` is
      empty. 28 commits exist locally and none of them is visible to a judge.
      This is a hard deliverable and it is the only one that is one command away.
- [ ] **World v2** — realistic operating point, insolvent customers, mandate
      cancellation, success decay. Spec in `04_BUILD_PLAN.md`. **In progress,
      not a caveat.**
- [ ] **The validation suite** — the simulator scored against published figures
      it was never fitted to. This is what replaces a public benchmark, because
      no public benchmark exists. `04_BUILD_PLAN.md`.
- [ ] **Judge-facing docs**, plain English, engineering and business impact.
      Written AFTER World v2 lands, so it is written once.
