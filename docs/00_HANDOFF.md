# 00 — HANDOFF

## Where things stand, 28 August 2026

Research and simulation: **done and FROZEN.** Stop doing it.
Production code: **none exists.** That is the entire problem.
Deadline: **5 September 2026 — 8 days from today.**

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
| `solo_shared_pd` is the policy, with `w3.FITTED_BELIEF` | Best measured. Pooling worth +9.53 pts (±1.81), gated as S2a |
| **No** coordinated budgeting | Measured −6 pts twice. Cut. |
| No LLM on the debit-timing path | ADR-005. Deliberate, defensible. |
| **Yes** LLM on diagnosis / intervention choice / audit narrative | Needed for the track, and honest |
| Cycle-based metric, no LTV constant | Death priced automatically |
| `payday_wait` is a permanent baseline row | It is what a good rival builds in an afternoon |

## THE MODEL IS FROZEN (tag `model-frozen`, 28 August 2026)

No changes to `sim/w3.py`, `sim/harness.py` or the fitted constants before
5 September without explicit approval. Next session is `agent/`, and its
probability engine is `w3.BeliefPD` under `w3.FITTED_BELIEF`. See `CLAUDE.md`.

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
- **Suite runtime.** ~27 min → **~66s full / ~34s fast**, output proved
  byte-identical by T9.
- **The 6× LTV multiplier and the 0.92 discount.** Swept. LTV was inert and is
  removed; the discount is live and now reported as a range.
- **`harness.py:325`** — the placebo policies were scoring mandates 2..k off
  mandate 1's belief. Fixed; worth 0.42 of S2b's −14.51.
- **Does the day-0 payday prior create a cliff when the population differs?**
  No. 6.95 pts of gentle degradation across `payday_day0_frac` 0.8→0.2, and the
  margin over `ml_index` *grows* from +5.30 to +12.03 as the population moves
  away from the fit.

## Open — genuinely unresolved

1. **How accurately can payday be estimated in reality?** Still unmeasured, and
   still the one fact that decides whether the sophisticated version is worth
   building. What IS now measured is where the crossover sits: the system
   loses to `payday_wait` at ±1 day, ties at ±3, and wins by +23.6 at ±5
   rising to +53.2 at ±14 (`06_MODEL_CARD.md` §2). So the open question is
   narrow and concrete: **is real payday uncertainty above or below ~4 days?**
   Resolution unchanged: make the agent learn payday online and expose its own
   uncertainty, so the posterior width is a product feature rather than an
   assumption. Do not chase the number externally.
2. **Five gates are red on a clean checkout: S1, S1_PD, M1, S2b, S2_LEGACY.**
   **S1 measures the wrong filter** — it runs `portfolio`, which carries the
   point-estimate `w3.Belief`, not the `w3.BeliefPD` the project recommends.
   S1_PD was added with the identical threshold on the real filter and also
   fails (ECE 0.026–0.040, not monotone). The remaining break looks structural:
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
- [ ] Agent runs end to end over a batch of synthetic merchants
- [ ] One number: money recovered, with `payday_wait` printed beside it
- [ ] Audit log: every money action, with reason, constraint check, outcome
- [ ] Stopping rules explicit and demonstrable
- [ ] One failure handled gracefully, on camera
- [ ] Architecture doc, one page
- [ ] 5-minute pitch video, opening with the errors (there are ten)
- [ ] `NOTES.md` full of real mess
