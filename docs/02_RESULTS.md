# 02 — RESULTS (the only valid numbers)

Produced by `sim/harness.py` + `sim/w3.py`. Anything not on this page is stale.

> ## UPDATE, 28 August 2026 — the belief filter was refitted and the numbers below it moved
>
> Three values inside `w3.BeliefPD` had been hand-set during the research phase
> and never checked: a stride-3 payday hypothesis grid, an invented
> `exp(-0.10 d)` prior, and a hand-derived cross-mandate spend correction.
> Fitting them **on training populations only** is worth **+11.66 pts
> (±1.61)** — gated as **S4** — and on held-out evaluation populations lifts
> `solo_shared_pd` from 82.16% to **95.57%** at ±7 days.
>
> **Every `_pd` row in the table below was produced with the OLD, unfitted
> filter.** They are retained because they are what the suite still measures
> for S2a/S2b/S2c, and because replacing them wholesale would erase the
> comparison. Read them as the *shipped-but-unfitted* configuration.
>
> **The pooling moat survives the refit and grows slightly:** +8.20 → **+9.61
> pts (±1.67)**, still significant. Pooling was not compensating for the bad
> prior.
>
> **The ML comparison lives in `06_MODEL_CARD.md`**, with the six-world
> table. Those numbers come from `sim/ml_study.py`, not the gated suite, and
> are labelled as such under the numbers rule in `CLAUDE.md`. Summary: **the
> fitted Bayes filter beats the ML baseline in all six worlds by 5–12 points,
> and a Bayes+ML hybrid is worse than the filter alone.**
>
> **The one baked-in population fact, stress-tested.** `w3.FITTED_BELIEF`
> carries `prior_day0=8.0`, an 8x prior weight on payday hypothesis 0, fitted
> on populations drawn with `payday_day0_frac=0.60`. Moving the WORLD's day-0
> fraction while holding the prior fixed (n=100, 8 evaluation populations,
> `payday_err=7`, 160 runs, zero Stage 0 violations):
>
> | `payday_day0_frac` | `payday_wait` | bayes shipped | **bayes fitted** | `ml_index` |
> |---|---|---|---|---|
> | 0.2 | 55.60% | 81.38% | **88.62%** | 76.59% |
> | 0.4 | 58.70% | 82.50% | **93.12%** | 82.29% |
> | 0.6 (fitted here) | 59.14% | 82.16% | **95.57%** | 86.18% |
> | 0.8 | 58.58% | 82.93% | **96.68%** | 91.38% |
>
> **No cliff: 6.95 points of degradation across a 4x change in the parameter**,
> never falling below the unfitted filter, and beating `payday_wait` by 33-38
> points throughout. And the margin over `ml_index` **grows** as the population
> moves away from the fit, +5.30 at 0.8 to **+12.03 at 0.2** — `ml_index`
> degrades 14.8 points across the sweep against the filter's 8.1. A wrong prior
> is recoverable by evidence; a wrong learned split is not.
>
> **Limit of that result:** the sweep moves the *fraction* at day 0, not *which
> day* the spike sits on. A population spiking on day 14 is a harsher test and
> has not been run.
>
> **A range every number on this page owes.** The `p_later` discount is still
> the hardcoded 0.92. Swept 28 Aug (item A3, declared at project start, never
> previously done): `solo_shared_pd` ranges **78.7%–83.1%** across discount
> 0.80–1.00. No point estimate here is tighter than that constant allows.

**Setup.** World calibrated so Razorpay's documented UPI schedule reproduces
~30% per-attempt approval (spend=1.05). 120-day horizon, 30-day billing cycles,
5 mandates/customer. Primary metric: **billing cycles collected ÷ cycles due**,
where a dead mandate forfeits all remaining cycles.

**Known bias risks in this design, stated up front:**
- The world model, the policies and the tests were all built by one party.
- `payday_err` is swept, but the *shape* of the payday distribution is assumed.
- Top-up probability is pinned at 0 except where stated. It matters — see below.
- **No real data has ever entered this project.** Every number on this page is
  simulation. See `06_MODEL_CARD.md`, "what this has never been tested on".

---

## The headline is conditional — REGENERATED 28 August 2026

n=100, **8 held-out populations** (seeds 700–707, never used to fit anything),
120-day horizon, paired 2 SE. `bayes fitted` is `solo_shared_pd` with
`w3.FITTED_BELIEF` — **the shipping policy**.

*Not gate-protected. Reproduce with `python sim/headline.py`.*

| Payday known to | `payday_wait` (5-line heuristic) | bayes shipped | **bayes fitted** | oracle | fitted − heuristic |
|---|---|---|---|---|---|
| ±1 day | **99.24%** | 93.61% | 95.73% | 100% | **−3.51** ±0.36 SIG — heuristic wins |
| ±3 days | 94.65% | 88.62% | **95.82%** | 100% | +1.17 ±1.35 **n.s. — tie** |
| ±5 days | 72.18% | 83.57% | **95.82%** | 100% | **+23.64** ±2.61 SIG |
| ±7 days | 59.14% | 82.16% | **95.57%** | 100% | **+36.43** ±3.37 SIG |
| ±10 days | 48.11% | 79.87% | **95.62%** | 100% | **+47.50** ±3.17 SIG |
| ±14 days | 40.01% | 73.40% | **93.16%** | 100% | **+53.15** ±2.90 SIG |

**The crossover sits between ±3 and ±5 days.** This is the number that decides
whether the project is worth building, and it remains an empirical fact about
Indian salary timing that we have not measured.

**Two things changed when the belief was fitted, and both matter for the pitch.**
The old version of this table (n=30, 4 seeds, unfitted belief) reported the
heuristic *beating* the system by 8.5 points at ±3 days. That region is gone:
at ±3 it is now a statistical tie, and the system only loses at ±1, by 3.5
points. And the fitted system is **flat at ~95–96% from ±1 all the way to
±10**, where the heuristic collapses from 99% to 48%. The system's value is not
that it is better on average — it is that **it does not care how wrong the
payday estimate is**, which is the whole product argument.

Decision taken: do not chase the payday parameter externally. Make the agent
*learn* payday online and report its own uncertainty. The posterior width
becomes a product feature.

## Full table — HISTORICAL (n=30, 4 seeds, unfitted belief)

⚠️ **Superseded. Kept for the policy ranking, not for the values.** These were
measured at n=30 with 4 seeds before the belief was fitted, and every `_pd` row
understates what the shipping configuration now achieves. Do not quote a number
from this table. The policy *ordering* is still correct and is why
`solo_shared_pd` was chosen.

| Policy | ±1d | ±3d | ±7d |
|---|---|---|---|
| `baseline_doc` — documented UPI schedule | 23.3% | 21.2% | 21.2% |
| `baseline_legal` — same, made legal | 30.8% | 28.3% | 28.3% |
| `payday_wait` — 5-line heuristic | 98.9% | 96.0% | 59.4% |
| `myopic` — pooled belief, greedy | 90.8% | 74.4% | 52.7% |
| `solo_naive` — no aggregate model | 51.9% | 50.5% | 50.5% |
| `solo_pop` — own obs, point payday | 99.8% | 85.6% | 63.1% |
| `solo_shared` — pooled, point payday | 98.9% | 85.5% | 62.6% |
| `portfolio` — pooled + coordinated budget | 98.9% | 78.3% | 53.6% |
| `solo_pop_pd` — own obs, payday posterior | — | 80.1% | 73.2% |
| **`solo_shared_pd` — pooled + payday posterior** | — | 87.5% | **83.4%** |
| `portfolio_pd` — above + coordinated budget | — | 81.5% | 77.3% |
| `oracle` — true balance and true future | 100.0% | 100.0% | 100.0% |

**Best policy is `solo_shared_pd`.** Not `portfolio`.

## The moat is payday discovery, not balance inference

| | ±3d | ±7d |
|---|---|---|
| pooling, point-estimate payday | −0.16 (n.s.) | −0.49 (n.s.) |
| **pooling, payday posterior** | **+7.32** SIG | **+10.23** SIG |

The moat was invisible because the belief kept a distribution over *balance* but
a single number for *payday* — it could not learn the variable that dominates.

Why an aggregator wins: one merchant sees **one debit per month** on an account.
An aggregator sees five. Payday discovery is a data-volume problem, and volume
is the one thing a single-merchant competitor cannot buy.

## Negative control — RE-MEASURED 27 Aug 2026, and it was inflated

`solo_placebo` pools with identical mechanics, timing and observation count, but
outcomes computed against a **different customer's** balance.

The old headline on this page was **+21.68 / +23.99 SIG**, presented as "the
strongest evidence we have." Re-run as three separate arms on the
payday-posterior policies at ±7d, 8 populations, n=100:

| arm | comparison | result |
|---|---|---|
| **S2a** the moat | `solo_pop_pd` → `solo_shared_pd` | **+9.53** pts (±1.81) SIG |
| **S2b** confound check | `solo_pop_pd` → `solo_placebo_pd` | **−14.51** pts (±2.24) — **not neutral** |
| **S2c** the old headline | `solo_placebo_pd` → `solo_shared_pd` | **+24.04** pts (±2.25) SIG |

S2c reproduces the old +23.99 almost exactly. **It is also the least
informative of the three.** For paired means, `S2c ≡ S2a + |S2b|` — an
algebraic identity, not an independent measurement (9.53 + 14.51 = 24.04).
**60% of that headline is placebo damage, not pooling benefit.**

The reason is that `solo_placebo` is not a clean control. It does not add
*neutral* extra update events; it adds *wrong* ones, computed against another
customer's balance (`harness.py:227`). Feeding a belief actively misleading
observations is worse than feeding it nothing, so the placebo arm is degraded
rather than merely uninformative, and subtracting it flatters the result.

**The defensible moat number is S2a: +9.53 pts (±1.81), significant.** That is
close to the +10.23 this page already claimed for pooling under the payday
posterior, and it stands on its own. **Do not quote +21.68 / +23.99 / +24.04 as
evidence that the benefit is information.** A control that matches update count
without supplying wrong information — label-shuffled observations at the matched
base rate — has not been built yet.

This also resolves an ambiguity flagged earlier: the old +23.99 figure was
produced on the **payday-posterior** pair, not the point-estimate pair. The S2
gate in `sim/tests.py` was testing the point-estimate trio, which is why it
disagreed. That gate is retained as `S2_LEGACY`.

## Other established results

- **Coordinated budgeting is harmful.** −5.95 pts (±3d), −6.10 pts (±7d), both
  significant. Cut. Do not reintroduce.
- **Whittle structure beats greedy** by +7.15 (±3d) to +24.54 (±7d) pts.
- **Headroom to the oracle: +18.5 to +22.7 pts**, significant everywhere. There
  is plenty left. A near-zero oracle gap is a symptom, not an achievement.
- **The documented UPI baseline is not legally executable.** ~978
  re-presentation violations per run: retrying at +1h/+2h re-presents a Z9 under
  the original notification. Making it legal is worth **+7.5 pts on its own.**
- **The payday assumption is forced.** Calibrating to ~30% approval: lumpy payday
  reaches 29.1%; 50% irregular income floors at 44.2%; fully irregular at 74.0%.
  If income were spread through the month, approval could not be 30%.

## Top-up sensitivity (run on the OLD harness — needs redoing on w3)

A failed attempt may prompt the customer to top up. Old harness, k=7:

| top-up prob | baseline | best system | gap |
|---|---|---|---|
| 0.00 | 41.9% | 76.0% | +34.1 |
| 0.25 | 54.5% | 79.7% | +25.2 |
| 0.50 | 62.4% | 80.9% | +18.5 |

Roughly half the apparent gain is "customers never top up." `w3` supports
`topup_p`; this sweep has **not** been redone there. Do it before the pitch.

## Test suite status

**24 gates. Five are red: S1 FAIL, S1_PD FAIL, S2b FAIL, S2_LEGACY FAIL,
M1 VACUOUS.** Enforced by `sim/gate.py`; reasons in `sim/known_failures.txt`.

**Runtime: ~66s for the full suite, ~34s for the fast tier** (was ~27 minutes).
The suite is now planned up front and run in parallel, and the belief filter's
forecast is incremental. Gate **T9** locks every policy's output to
`sim/t9_reference.json` byte for byte so none of that changed a result.

Two tiers: `git commit` runs `--tier fast` (code-correctness gates), `git push`
runs `--tier full` (adds S2a/b/c, S2_LEGACY, S3, S4).

**New since the 27 August rebuild:**
- **T9** — output identical to a reference captured before any optimisation.
  28 configs, 20 of them hashed at float level. Paired with a shared-RNG mutant.
- **S4** — the fitted belief configuration beats the shipped one, +11.66 pts
  (±1.61). Paired with the `ignore_bcfg` mutant, under which the gain collapses
  to +0.00. **This is the decision number for which probability engine ships.**
- **S1_PD** — S1's threshold applied to the filter that actually ships. It
  FAILS (ECE 0.026–0.040, not monotone). See below.

Rebuilt 27 August 2026: gates that only bind under contention (M1, S2, T5, T7)
now run at `payday_err=7` instead of the harness default of ±1 day, where the
world is uncontended and constraints are never reached. T3 was rewritten (it
had been a duplicate determinism check, not a leakage test), T7's cap clause
was switched from a mean to a per-event count, T1 was paired with the
`weak_oracle` mutant, and S3 was implemented. M7 and M9 from
`05_TEST_DESIGN.md` remain unimplemented, and T7 still does **not** implement
the conservation identity.

⚠️ **S1 (belief calibration) FAILS.** ECE 0.091 against a 0.10 threshold,
reliability curve not monotone, filter overconfident in its top decile (predicts
0.998, achieves 0.919). Note ECE is now *inside* the bound — the gate fails on
the **monotonicity** half. The threshold was declared before results were seen.
**Do not loosen it.** Nothing above is fully settled until it passes.

⚠️ **S2b (placebo neutrality) FAILS**, at −14.51 pts (±2.24) — see the negative
control section above. This is a finding about the control's design, not a code
defect, and it is left failing so the confound stays visible.

⚠️ **S2_LEGACY FAILS** at −0.40 pts (±0.22). This is the *original* S2, kept
unchanged: the point-estimate trio (`solo_shared` / `solo_placebo` / `solo_pop`)
at the uncontended ±1d operating point. It faithfully reproduces the −0.16 /
−0.49 (n.s.) null this page already reports for point-estimate pooling. It is
retained, failing, on purpose — the S2 rewrite replaced a red gate with three
new ones, and deleting the red gate at the same time would have been
indistinguishable from loosening a test to get green.

⚠️ **S1 HAS BEEN MEASURING THE WRONG FILTER FOR THE WHOLE PROJECT.** S1 runs
`portfolio`, which does not end in `_pd` and therefore carries `w3.Belief`, the
**point-estimate** payday filter. The recommended policy is `solo_shared_pd`,
which carries `w3.BeliefPD`. S1 was NOT repointed — its threshold is
pre-registered and quietly aiming it elsewhere would be indistinguishable from
moving a test. **S1_PD** is a new gate with the identical threshold on the real
filter, and it also fails: ECE 0.026–0.040, reliability curve not monotone.
Fitting the filter halved its ECE and did not order the curve; the remaining
break is structural (no balance floor at zero, and the hourly spend jitter is
approximated by a fixed 3-tap kernel).

⚠️ **M1 (attempt-cap mutant) is VACUOUS**, so the claim "mutation tests all fire"
that used to sit here was false. The cap mutant cannot trip the counter at
`payday_err=1`: the deepest any mandate-cycle reaches is **3 attempts**, against
`NPCI_MAX = 4`, so a 5th attempt never happens and the cap is never the binding
constraint. Diagnosed 27 August 2026 — see `NOTES.md`. The other mutants (M2–M6,
M8) do fire (608–1,119 violations vs a clean zero), and the oracle deferral bug,
deliberately restored, is caught (100.0% → 46.3%).

**Consequence: the attempt-cap guarantee is currently untested.** The counter in
`harness.py` can be disabled outright without any gate going red — verified by
experiment. Do not put the NPCI cap compliance claim in the pitch or the
architecture doc until M1 is fixed.
