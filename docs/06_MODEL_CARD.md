# 06 — MODEL CARD

**What ships, what it is worth, and what it has never been tested on.**
Frozen 28 August 2026 at tag `model-frozen`. Read this before touching `sim/`
or building on top of it. If you are building the agent, read
`07_AGENT_BRIEF.md` first — this page is the evidence behind it.

---

## 1. What ships

| | |
|---|---|
| **Policy** | `solo_shared_pd` — pooled cross-merchant observations, posterior over payday |
| **Probability engine** | `w3.BeliefPD`, configured with `w3.FITTED_BELIEF` |
| **Scoring** | `w3.index_score(p_now, p_later, amount, discount=0.92)` |
| **Constraint layer** | Stage 0, enforced in `sim/harness.py` — **currently counts violations, does not prevent them.** See `07_AGENT_BRIEF.md` §4. |
| **Metric** | billing cycles collected ÷ cycles due, over the full horizon. A dead mandate forfeits its remaining cycles, which prices mandate death without an invented LTV constant. |

**The shipping constants live in one place**, `sim/w3.py`:

```python
FITTED_BELIEF = dict(stride=1, prior_w=5, prior_day0=8.0,
                     prior_floor=0.1, spend_beta=0.0)
```

⚠️ **This block said `prior_w=9, prior_floor=0.5` until 1 September 2026 and
that was WRONG — it was stating constants the repository had stopped
shipping.** The values above are what `sim/w3.py` actually carries. The agent
also ships `cycle_value=0.6`, which lives in the objective rather than in this
dict and has no entry here.

Pass it as `harness.run(..., bcfg=w3.FITTED_BELIEF)`. Omitting it silently
gives you the **old unfitted** filter, which is ~13 points worse — that is not
a hypothetical, it is what gate **S4**'s mutant does on purpose.

**Selected by W24 on 1 September 2026, NOT by `sim/fit_belief.py`.** The
shipping values were re-selected on the CANONICAL world (`pop_spend=0.93`,
k~1+Poisson(1), statutory payday, burn-in 12, mandate outflow on) by mean
recovery of at-risk cycles across `payday_err {1,3,7,14}` on train populations
700–709, scored once on held-out 710–719 — the identical rule and grid that
selected the `[1,7]` steelman, so both sides of that comparison are chosen the
same way.

`sim/fit_belief.py` **could not have found this value**: it scores on the
pre-canonical world and its `prior_w` grid is `(5,7,9,12,15)`, which cannot
reach the canonical optimum. `sim/fitted_belief.json` therefore records
`matches_shipping=false` with that reason, and its `selected` block is left
unchanged as what that search actually returned. Error 12.

It is a **plateau, not a peak**: `prior_w` in {3,4,5} × `prior_floor` in
{0.05,0.1} all land between 91.8 and 92.7 train mean, on a 2 SE of about 3.

**Not gate-protected.** n=100, k=5, 120 days, `pop_spend=1.05`. Training mean
94.90%; former shipping 94.53%; unfitted baseline 83.42%. Evaluation mean
94.94% (former 95.29%). Oracle ceiling 100%. Headline tables below were
re-measured after this adoption.

**What each constant replaced, and why the old value was wrong:**

| | old | new | why |
|---|---|---|---|
| `stride` | 3 | 1 | The grid `[0,3,…,27]` left only **74%** of customers with a representable true payday, and only **31.7%** of those not paid on day 0. |
| prior | `exp(-0.10 d)` | window `w=9`, `day0×8`, floor `0.5` | Invented, never fitted. Put mass at distances the estimator cannot produce. |
| `spend_beta` | 0.045 | **0.0** | A hand-derived cross-mandate spend correction that turns out to be worth less than nothing. |

---

## 2. What it is worth

### Against the competitive baseline — the number that decides the project

`payday_wait` is the 5-line heuristic a good rival team builds in an afternoon:
wait for the estimated payday, then one attempt per day. It is a permanent row,
never omitted.

*n=100, 8 held-out populations (700–707), 120d, paired 2 SE. Not gate-protected;
reproduce with `python sim/headline.py`.*

⚠️ **SUPERSEDED 1 September 2026.** Pre-canonical world, and `payday_wait` is
not a steelmanned baseline — it targets the estimated payday on its first
attempt only, then retries daily, burning the NPCI cap in three days. Kept as
the record.

| Payday known to | `payday_wait` | **shipping** | oracle | difference |
|---|---|---|---|---|
| ±1 day | **99.24%** | 96.44% | 100% | **−2.81** ±0.46 SIG — heuristic wins |
| ±3 days | 94.65% | **96.06%** | 100% | +1.41 ±1.08 SIG |
| ±5 days | 72.18% | **95.38%** | 100% | **+23.20** ±2.57 SIG |
| ±7 days | 59.14% | **95.63%** | 100% | **+36.48** ±3.20 SIG |
| ±10 days | 48.11% | **94.14%** | 100% | **+46.03** ±3.62 SIG |
| ±14 days | 40.01% | **91.99%** | 100% | **+51.98** ±2.66 SIG |

*Every row above is superseded. Kept as the record.*

### The current table, against a steelmanned fixed schedule

`[1,7]`: two attempts at frozen offsets from the same noisy payday estimate the
agent gets, selected once on train populations 700-709 and never re-tuned.
Recovery of at-risk cycles, canonical world, `pop_spend=0.93`, held-out
710-719, n=100. `py -3.12 agent/tests/test_steelman_schedule.py`.

| Payday known to | `[1,7]` | **shipping** | difference |
|---|---|---|---|
| ±1 day | **99.75%** | 98.59% | **−1.16** — the fixed schedule wins |
| ±3 days | **98.51%** | 98.18% | **−0.33** |
| ±5 days | **96.63%** | 97.78% | **+1.15** |
| ±7 days | 90.88% | 94.43% | +3.55 |
| ±10 days | 66.93% | **90.76%** | **+23.83** |
| ±14 days | 55.47% | **89.88%** | **+34.41** |

**The crossover is between ±7 and ±10 days.** Below that, build the fixed
schedule. Above it, the gap is large and grows. Against the old strawman the
crossover looked like ±1 to ±3; steelmanning the baseline moved it by six
days, and moved it onto the wrong side of what the payroll evidence suggests.

⚠️ **SUPERSEDED 1 September 2026 (W24).** The payday prior was refitted on
the canonical world (`prior_w` 9 -> 5, `prior_floor` 0.5 -> 0.1) and the
mandate's continuation value was added to the objective (`cycle_value=0.6`).
**The crossover is now at ±5, not between ±7 and ±10.** Held-out margins
against `[1,7]`: −1.16 at ±1, −0.33 at ±3, +1.15 at ±5, +3.55 at ±7,
+23.83 at ±10, +34.41 at ±14. The figures above are the PRE-REPAIR agent and
are kept as the record. `py -3.12 agent/tests/test_steelman_schedule.py`.

The shipping filter sits at **92–96% from ±1 to ±14** while the heuristic falls
from 99% to 40%.

**We do not know where Indian salary timing actually sits on this axis.** It has
never been measured. The decision taken was to make the agent learn payday
online and expose its own uncertainty, so the posterior width becomes a product
feature rather than an assumption.

### Against an ML probability engine

*n=100, 8 populations, `payday_err=7`, 120d, paired 2 SE. 288 runs, zero Stage 0
violations. Not gate-protected; reproduce with `python sim/ml_study.py eval`.*

Both models were fitted on world A only; **neither is retrained on any shifted
world**, so under shift both are out of distribution.

| world | `payday_wait` | bayes shipped | **bayes fitted** | `ml_index` | hybrid | oracle |
|---|---|---|---|---|---|---|
| A: in-distribution | 59.14% | 82.16% | **95.63%** | 86.18% | 85.71% | 100% |
| decay 0.20 (world only) | 75.19% | 92.03% | **99.08%** | 93.60% | 93.31% | 100% |
| decay 0.70 (world only) | 49.91% | 69.45% | **82.09%** | 74.99% | 76.75% | 99.97% |
| payday spread 0.60→0.30 | 57.95% | 82.08% | **92.87%** | 77.94% | 78.94% | 100% |
| `irregular_frac` 0.5 | 71.07% | 89.72% | **96.31%** | 89.66% | 88.43% | 100% |
| `topup_p` 0.25 | 69.77% | 85.02% | **95.66%** | 87.28% | 87.08% | 100% |

Every `ml − fitted` and `hybrid − fitted` difference is negative and
significant, ranging −5.47 to −14.93.

**The ML arm is a real baseline, not a strawman.** It is a LightGBM model on 50
decision-time features including the censored-observation statistics the Bayes
filter uses and the cross-merchant features the moat is built on. Held-out AUC
0.946. It passes both leakage checks: shuffled-label AUC 0.459, and the
train/test split is **by population**, so no customer and no world draw is
shared. `sim/ml_diagnose.py` runs the audit.

**The hybrid** (`ml_index_pd`) feeds the GBDT four Bayes posterior summaries —
`p_success` for the candidate day, expected balance, payday-posterior entropy,
top-hypothesis weight. `bayes_p_success` becomes its most-split feature and it
still loses. A tree ensemble approximates that input with piecewise-constant
splits, and the fitted filter is accurate enough that smoothing it destroys more
than the residual adds. **A GBDT wrapped around a good probability is worse than
the probability.**

### The cross-merchant moat

**+7.32 pts (±2.02), gated as S2a_PD, on the filter that ships.** Unfitted
S2a is +9.53 (±1.81) on the same populations. Re-measured 1 September 2026;
`logs/w26_gate_full_moat_remeasure.txt`.

⚠️ **AND IT IS THE HARD-WORLD FIGURE. Added 30 August 2026 (W9), re-measured
1 September 2026.** Measured in the agent at two calibrations, pooling is worth
**+6.47 pts (±0.62) at `pop_spend=1.05` and +1.30 (±0.42) at `pop_spend=0.80`**
— a factor of five across the same range the headline is already reported over.
Every existing quotation of +9.53 in this repository is the unfitted hard-world
number. Quote S2a_PD for the shipping filter; quote +1.30 beside it for the
calibration that matches the published failure rate. `02_RESULTS.md`, W9;
`python agent/tests/test_pooling_consent.py` for the 0.80 cell, not
gate-protected.

**S2a_PD and the agent W9 figure no longer agree to a point.** They read 7.32
(gated harness) and 6.47 (ungated agent), 0.85 apart with overlapping intervals.
Until the W24 refit they were 8.34 and 8.46 and this section said they agreed;
that sentence is **withdrawn** and both figures are **superseded**. The gap has a named cause rather than being noise to
be waved at: the W24 change shipped as a **pair** — a narrower payday prior plus
`cycle_value=0.6` — and `sim/harness.py` prices no continuation value, so every
harness-path measurement sees the prior without its partner. Do not average
them, and do not present the harness figure as the agent's.

⚠️ **Do NOT quote S2c (+23.62) as independent evidence.** For paired means it is
algebraically S2a + |S2b|, and S2b is placebo *damage*, not pooling benefit. See
`02_RESULTS.md`.

### The `prior_day0` stress test

`prior_day0=8.0` bakes a population fact — 62% of customers are paid on day 0 —
into a constant. Moving the world's `payday_day0_frac` while holding the prior
fixed:

*160 runs, zero Stage 0 violations. Reproduce with `python sim/stress_day0.py`.*

| `payday_day0_frac` | `payday_wait` | **shipping** | `ml_index` | shipping − ml |
|---|---|---|---|---|
| 0.2 | 55.60% | **91.75%** | 76.59% | **+15.16** ±2.12 SIG |
| 0.4 | 58.70% | **93.03%** | 82.29% | +10.75 ±1.49 SIG |
| 0.6 ← fitted here | 59.14% | **94.79%** | 86.18% | +8.60 ±1.99 SIG |
| 0.8 | 58.58% | **95.36%** | 91.38% | +3.98 ±1.53 SIG |

*Re-measured 1 September 2026 on the shipped belief, `logs/w27_stress_day0_repaired.txt`. The superseded column read 90.79 / 93.31 / 95.63 / 97.26 with 4.84 points of degradation.*

**No cliff.** 3.04 points of gentle, monotone degradation across a 4× change in
the parameter; never falls below the *unfitted* filter; stays 35–39 points above
`payday_wait` throughout. And the margin over ML **grows** as the population
moves away from the fit, because `prior_day0` is a *prior* that evidence
reweights within a cycle or two, whereas the GBDT learned the same fact as hard
splits and cannot revise it. **A wrong prior is recoverable; a wrong learned
split is not.**

---

### ⚠️ The largest sensitivity in the agent is a guessed constant

`p_limit` — how often a debit is refused by a per-transaction or mandate limit
rather than by an empty account — costs, swept:

| `p_limit` | 0.00 | 0.05 | 0.15 |
|---|---|---|---|
| vs rate 0 | **+0.000** | **−2.29** | **−9.22** |

`[GUESS]` throughout; the NPCI code list names `Z8` and `IE` without saying how
often they fire. **Never quote −9.22 on its own** — it is the top of a guessed
range, the curve is steeply superlinear, and interpolating the middle is not
safe. `02_RESULTS.md` has the mechanism: it is the one failure family where the
money IS there, and the frozen policy re-presents the same amount until the cap
kills the mandate.

## 2c. The two world parameters that ship INERT. Added 30 August 2026.

Both exist, both are swept, **both default to 0.0 and neither is adopted.** A
reader who greps for them and finds no headline built on them is seeing the
intended state, not an oversight.

| parameter | what it models | swept over | why it ships at 0.0 |
|---|---|---|---|
| `p_missed_credit` (W2) | the salary credit does not arrive, so the cycle is genuinely uncollectable | {0.00, 0.03, 0.08} | Brings V5 into its published band at 0.08 — **and breaks V1 there.** Adopting it would trade a target the world hit unfitted for one it was tuned to. |
| `p_transient` + `transient_h` (W7) | a temporary hold blocks the balance for a few hours and then releases | {0.00, 0.05, 0.10, 0.20} × {24h, 48h} | Moves V3 hard and **breaks V1 doing it.** Across 14 swept worlds none hits more than 2 of 4 targets, and the best is still 0.00. |

**Both are guarded so they consume nothing at 0.0**, which is what keeps parity
bit-exact at 24/24 and gate T9 green. Both draw from their own per-customer
generators. Each swept world is therefore the base spending trace plus its
named mechanism rather than a fresh draw of that trace.

`agent/tests/test_insolvency_sweep.py` and `agent/tests/test_transient_sweep.py`
are the measurements. Neither is gate-protected; both are named so they can be
re-run.

## 3. What this has NEVER been tested on

Read this section before quoting anything above to anyone outside the team.
Eleven items, three of them added by the outside audit on 28 August 2026.

1. **No real data. Ever.** Every number in this project is simulation. No
   Razorpay transaction has been seen, no real mandate, no real decline code.
   The world is `w3.make_pop` + `w3.balance_trace`, written by the same party
   that wrote the policies and the tests.
2. **One external calibration anchor.** The world is tuned so the documented UPI
   retry schedule reproduces ~30% per-attempt approval. That is the *only* place
   reality touches the model, it is a `[REPORTED]` figure, and different anchors
   move the fitted spend parameter by ~2×. Every absolute percentage inherits
   that.
3. **The `prior_day0` sweep moves the fraction, not the day.** It varies *how
   many* customers are paid on day 0, never *which day* the spike sits on. A
   population spiking on day 14 is a harsher test because `prior_day0` boosts a
   fixed hypothesis index. **Not run.**
4. **`payday_err` is a knob, not a measurement.** The whole headline is
   conditional on a parameter of Indian salary timing nobody has measured.
5. **The 0.92 discount is hand-chosen.** ⚠️ **Corrected 28 Aug 2026: the
   78.7%–83.1% sweep was run on the UNFITTED filter.** On the shipping
   configuration the spread across 0.80–1.00 is **88.7%–95.6%**. **Every
   number on this page inherits a ~7-point band, not ~4.** The 0.90–0.96
   plateau does survive on the fitted filter (94.25–95.57), so the constant is
   not perched on a spike — but on this grid 0.92 is also the evaluation-set
   argmax, which is the situation error 8 warns about. Table in
   `02_RESULTS.md`. That table was measured on the former shipping prior and
   was not re-run after the 31 August adoption. Never fitted, because fitting it on the evaluation set is
   what produced error 8.
6. **Cross-merchant pooling may not be legal.** Whether a payment aggregator may
   use Merchant A's outcomes to schedule Merchant B's debit for the same
   customer is **unresolved** — `01_FACTS.md` tags it `[GUESS]`. The moat is the
   central claim and its legal basis is unread.
7. **Top-up is pinned at 0** in everything except the one `topup_p=0.25` row.
   On the OLD harness, roughly half the apparent gain was "customers never top
   up". ✅ **Redone on `w3` 29 August 2026, and the old worry does not
   survive**: sweeping the unconditional `topup_p` on the shipping
   configuration moves it **+0.02 pts (2 SE 0.59)**, while the same sweep moves
   `payday_wait` by **+11.4 pts**. The mechanism is live; the shipping policy
   simply has nothing left to recover at 95.3%. `02_RESULTS.md`, the action-space
   section.
8. ~~**Two of the five Stage 0 guarantees have no working test.**~~ The attempt
   cap (M1 is VACUOUS) and the pending notification (M4 passes by
   construction — its mutant increments the counter itself; caught by M4B on
   28 Aug 2026). See §4 and error 11.
   ✅ **RESOLVED 30 August 2026 — both repaired. All five rules are now
   tested in `sim/` and the suite has 0 vacuous gates.**
9. **n=100, 8 populations, one run seed each.** Not a large study.
10. **Mutants still run the unfitted filter, on purpose.** They test constraint
   counters, not the prior. The shipping configuration is covered by `S1_PD`,
   `T6_PD`, `S2a_PD`, `S4`, and T9 (`solo_pop_pd`, `solo_shared_pd`,
   `portfolio_pd` under `FITTED_BELIEF` at both operating points). T1, T7 and
   T8 include those policies. Error 13.
11. **The oracle is not clairvoyant about top-ups.** It reads
   `bal[tt] - drained` (`harness.py:524`) while dispatch reads
   `bal[t] - drained + topups[t]` (`harness.py:268`). Inert at `topup_p=0`;
   in the `topup_p=0.25` row above the 100% oracle is not a tight bound.
   The oracle is the upper bound the whole project is measured against, so a
   loose one flatters everything — and `topup_p` is exactly the sweep (A1)
   that is still open.

---

## 4. The four failing gates, and why each is failing

**Updated 31 August 2026.** The suite is **27 gates: 4 FAIL, 0 VACUOUS,
23 pass.** All four are listed in `sim/known_failures.txt` with a written
reason. None may be fixed by loosening a threshold.

✅ **ALL FIVE STAGE 0 RULES NOW HAVE A WORKING TEST.** M1 runs its mutant at
`cap_override=2` so the attempt-cap counter binds; the `pending` and `represent`
mutants create illegal state instead of writing the counters they are graded
on, so M4B is green. **The attempt cap and the pending notification are now
safe to claim** — the caveat that they were not is kept below, struck through,
as the record of what was wrong. Do not reintroduce it.

| Gate | State | Why it is red |
|---|---|---|
| **S1** | FAIL | Calibration of `w3.Belief` (point-estimate payday) via `portfolio`. ECE 0.091 — *inside* the 0.10 bound — but the reliability curve is **not monotone**. **S1 does not measure the shipping filter**; that was error 9. |
| **S1_PD** | FAIL | The same threshold on the filter that *does* ship. ECE **0.025**, still not monotone. Fitting halved the error and did not order the curve. The remaining break is structural: the filter's **diffusion leaks through the balance floor it does model** (the drain rounds to zero bins for 22 of 30 days and `np.convolve(..., "same")` discards its end taps), and it approximates the world's hourly `U(0.4,1.6)` spend jitter with a fixed 3-tap kernel. **Neither is a parameter you can fit.** *(This row previously said "the filter models no balance floor at zero". That attribution was WRONG and is corrected: `_shift` piles mass at bin 0 on every drain. See `sim/known_failures.txt`.)* |
| ~~**M1**~~ | ✅ **GREEN** | Was VACUOUS: the mutant could not trip the counter at either operating point, because the deepest any mandate-cycle reaches is 3 attempts at ±1d and 4 at ±7d against `NPCI_MAX=4`, so a 5th attempt never happened. **Fixed 30 August 2026** by running the mutant at `cap_override=2`, which tests the counter mechanism rather than the NPCI-specific value. The attempt-cap claim now has a working test. |
| **S2b** | FAIL | The placebo control is not neutral (−14.09 pts). Extra unmatched `observe()` calls damage this filter: a donor-balance outcome, a label-shuffle of real outcomes, and a posterior-predictive draw were all worse than own on the T9 population (NOTES.md, 31 August). Neutrality versus own is the wrong property. Quote S2a / S2a_PD, never S2c. |
| **S2_LEGACY** | FAIL | The retired point-estimate S2, kept unchanged and failing on purpose so the S2 rewrite is auditable rather than looking like test-loosening. Goes only when the point-estimate architecture is formally removed. |
| ~~**M4B**~~ | ✅ **GREEN** | **Added 28 Aug 2026, fixed 30 Aug.** No mutation branch may increment the counter its gate reads. Two did: `mutate="pending"` was **1066 counted, 1066 self-written, 0 independent**, so gate M4 was passing by construction. `pending` now drops the pending filter so the harness's own check counts the second notification; `represent` no longer double-writes and M5 fell to 304, all independent — exactly the number the 28 August instrumented analysis predicted was real. **It went green because the mutants were repaired, not because the detector was narrowed**, and M4B still parses `harness.py` and would flag either branch the moment a `V.<field> += 1` returned to it. |

---

## 4b. Reproducing the ungated numbers

`sim/ml_artifacts/` is **gitignored** — it holds a ~8 MB trained model and the
study outputs. Nothing in the gated suite needs it, but the ML and stress tables
above do. To rebuild from nothing, **in this order**:

```bash
py -3.12 sim/fit_belief.py --check  # verify the committed fit record matches shipping
python sim/ml_study.py data     # ~1 min  -> train.npz        (explore rows)
python sim/ml_study.py train    # ~1 min  -> model.pkl        (base GBDT)
python sim/ml_study.py data_pd  # ~2 min  -> train_pd.npz     (explore_pd rows)
python sim/ml_study.py train_hybrid  # ~1 min -> adds gb_hybrid to model.pkl
python sim/ml_study.py eval     # ~10 min -> misspec.json     (six-world table)
```

Independent of the artifacts, and worth running after any change to `sim/`:

```bash
python sim/headline.py          # ~3 min  the conditional headline table
python sim/fair_audit.py        # ~71s    does the fitted prior generalise?
python sim/stress_day0.py       # ~5 min  the prior_day0 stress test
python sim/verify_brief.py      # <1s     docs/07_AGENT_BRIEF.md matches code
python sim/ml_diagnose.py       # ~2 min  ML leak / candidate-day diagnostics
```

`py -3.12 sim/fit_belief.py` performs the full checkpointed search. It writes
`sim/fitted_belief.json`. If a rerun selects a different configuration it
exits non-zero and does not edit `w3.FITTED_BELIEF`.

⚠️ **`sim/ml_artifacts/` is gitignored, so none of the ML tables on this page is
reproducible from a clean clone without the rebuild above.** The fit mismatch
record is committed separately as `sim/fitted_belief.json`. A previous ignored
`belief_fit.json` reports **97.53%** for a call signature that measures
**95.57%**, and nothing reproduces 97.53%. `requirements.txt` pinned only
numpy until 28 Aug, so the recipe above could not be followed from it. It now
lists `lightgbm` and `scikit-learn` as well. The gated suite still needs numpy
alone.

**Anything you write that calls `runner.run_jobs` needs an
`if __name__ == "__main__":` guard.** Windows spawns rather than forks; without
the guard multiprocessing raises, and then *hangs instead of exiting*. That cost
97 minutes once — error 10 in `03_ERRORS.md`.

## 5. Running the suite

```bash
python sim/gate.py --tier fast     # ~34s — every gate that tests the CODE
python sim/gate.py --tier full     # ~100s idle — adds the STATISTICAL gates
```

`git commit` runs fast, `git push` runs full. Install both once per clone with
`scripts/install-hooks.sh`.

⚠️ **The full-tier figure is LOAD-DEPENDENT and "~81s" was optimistic.**
Re-measured 29 August 2026, three consecutive runs on an idle machine:
**100s / 102s / 98s**. One run earlier the same day, with other work in flight:
**223s**. The suite saturates eight worker processes. Budget ~100s idle, and do
not read a slow run as a hang — check CPU, not the clock (error 10).

**The statistical gates are never run at reduced n to fit a time budget.**
Shrinking S2/S3/S4 would be weakening a test — a statistical gate at low power
goes green for the wrong reason. They run properly or they do not run, and the
fast tier prints which gates it skipped.

**Gate T9 is what makes the split safe.** It compares every policy's output
against `sim/t9_reference.json` at both operating points — 34 configurations, 26
of them hashed at float level. Metrics catch a changed *decision*;
`calib_sha256` catches a changed *float*. Paired with a mutant that seeds the
worker pool from one shared RNG.

T9 locks the unfitted policies and `solo_pop_pd` / `solo_shared_pd` /
`portfolio_pd` under `w3.FITTED_BELIEF` at both operating points. Error 13's
coverage gap on the lock is closed.

**If you deliberately change behaviour**, re-baseline with
`python sim/t9_reference.py --recapture`. It prints the full field-level diff
before writing and tells you to paste it into `NOTES.md`. A reference
regenerated silently makes T9 a gate that cannot fail — which is the error this
project has made three times.

---

## 6. THE AGENT — added 28 August 2026. Read this before running anything in `agent/`.

`agent/` is the product. `sim/` is frozen and untouched by all of it. Three
things below will cost you hours if you skip them.

### 6a. EVERY MEASUREMENT MUST RUN ONE PROCESS PER RUN. This is not optional.

Long-lived Python processes that execute many `agent.batch.run_once` calls back
to back **crash on this machine** - SIGSEGV, sometimes SIGILL, at a different
point every time. Isolation performed 28 August 2026:

| probe | result |
|---|---|
| a single `harness.run` at n=100 | fine, repeatedly |
| `import agent, w3, harness` | fine |
| pure numpy allocation stress | fine |
| free memory | 8.7 GB of 15.7 GB - not exhaustion |
| six `run_once` calls in one process | crashed on **all three** code paths, including ones untouched by the new work |
| `test_parity_vs_harness.py`, byte-identical to the version that passed 24/24 that morning | segfaulted before printing a line |

That last row is the decisive one: **the failing code demonstrably worked hours
earlier and had not changed.** This is the intermittent `0xC0000005` already
recorded in `NOTES.md`, not a defect in `agent/`.

⚠️ **WHAT REMAINS UNEXPLAINED.** The root cause was NOT found. We do not know
whether it is numpy, the CPython build, the machine's memory, or something else,
and we do not know why it became reproducible on 28 August having been merely
intermittent before. **It is contained, not fixed.** If a fresh session sees a
crash in `agent/`, suspect this before suspecting the code - and check CPU, not
the clock (`03_ERRORS.md` error 10).

The containment is `agent/tests/_parallel.py`:
`ProcessPoolExecutor(max_tasks_per_child=1)`, one fresh interpreter per run,
nothing accumulates across runs. Same shape `sim/runner.py` already uses.
**Anything new that runs a batch of agent runs must go through it.** It also
**raises if any worker dies**, because a crashed run is a *failed* measurement,
not a missing one - silently dropping it would change the sample a mean is taken
over, which is error 4's shape. Side benefit: the parity gate went from 6m08s to
44s.

### 6b. The agent's gates, and what each is worth

| gate | what it proves |
|---|---|
| `test_layer_isolation.py` | five import-graph rules, each with a named mutant that is actually run. 5/5 trip. The LLM layer cannot reach the belief, the world, the gate or the timing layer. |
| `test_parity_vs_harness.py` | degenerate mode reproduces `harness.run("solo_shared_pd", ...)` **bit-exactly, 24/24 runs**, at pe1/pe7, fitted and unfitted. This is what makes every agent number comparable to a gated one. |
| `test_stage0_enforces.py` | 20/20. The gate refuses all five rules, AND an action injected *below* the gate is independently detected by `auditor.py` from the log alone. |
| `test_one_belief.py` | 11/11. One `BeliefPD` per CUSTOMER shared by all k mandates; double-`advance` raises. |
| `test_loop_order_equivalence.py` | customer-major and time-major are bit-identical with the monitor off; the monitor changes the answer with it on; and misusing it raises. |
| `test_action_ablation.py` | what each agent action is worth. See `02_RESULTS.md`. |
| `test_outage_detection.py` / `test_outage_ablation.py` | the context layer. See `02_RESULTS.md`. |
| `test_decline_sweep.py` | **added 29 Aug 2026.** What a richer decline taxonomy costs the frozen policy, and whether a bank-shaped outage is invisible to a monitor that pools banks. 2/2 pre-registered. Every rate is `[GUESS]` and swept. |
| `eval/run_eval.py` | **added 29 Aug 2026.** 40 registered cases + 7 taxonomy cases + 3 injection cases; deterministic arms always, `glm-5.3-flash` with a `glm-5.3` judge under `--llm --judge`. **Measured** - section 7 and `02_RESULTS.md`. |
| `test_detection_benchmark.py` | **added 29 Aug 2026.** Excess loss against a clairvoyant detection oracle, decomposed into delay / missed / dropout / late resumption / false alarms. Three gates, four crippled oracles as **window transforms rather than code branches**, all four caught, none by every gate. **G-1b is deliberately RED** — it found a defect in its own hours-based loss and is kept visible rather than repaired. See `02_RESULTS.md`. |
| `test_razorpay_mapping.py` | **added 29 Aug 2026.** Eight gates over the SECOND executor backend, all offline and keyless: Razorpay's 110 published `error_reason` values all map to a family (**and no mapped key was invented — that check found one, error 24**), the two dangerous families route the dangerous way, a transport failure returns `pending` rather than a fabricated decline, the idempotency key is deterministic per money action, Stage 0 refuses a peak-hour debit against the real client with **zero network calls**, `SimExecutor` still never sets `pending` (which is what keeps the parity gate honest), and the Payment Downtime feed parses. 44/44. Every gate names a mutant. |
| `test_smtp_reminder.py` | **added 31 Aug 2026.** Six offline gates on the funding-reminder SMTP path: skipped when unconfigured, honest failure on transport errors, outbox row written regardless, `executed=True` / `channel=email` / `smtp=smtp_sent` only after a successful `smtplib` send. 16/16. Mocked SMTP only — not gate-protected. |

Run them from the repo root with the interpreter named in `CLAUDE.md`. None of
them is part of `sim/gate.py`'s 25-gate suite, and none of their numbers is
gate-protected in the `--tier full` sense. Quote them the way the numbers rule
requires: name the script, say "not gate-protected".

### 6b-2. THE SECOND EXECUTOR BACKEND. Added 29 August 2026.

`agent/ports.py` declares one method — `attempt(ref, amount, t) -> AttemptOutcome`.
`SimExecutor` implements it against the frozen simulation;
`RazorpayExecutor` implements it against Razorpay's live API. **The loop, the
belief, Stage 0, the auditor and the audit trail are unchanged either way**,
because gate I2 already forbids every one of them from importing
`agent.execution` at all. The switch is one argument in `agent/batch.py`:
`run_once(..., executor=RazorpayExecutor(...))`.

⚠️ ~~**NOTHING IN IT HAS EVER TALKED TO RAZORPAY.**~~ **UPDATED 30 AUGUST
2026, AND THE LINE MOVED RATHER THAN VANISHED.** `scripts/razorpay_ladder.py`
sends real requests to `https://api.razorpay.com/v1/payments/create/recurring`
through the shipped transport. **No API key has been used by this project and
no request has ever been AUTHENTICATED**, so the rungs that ran are the ones
that need no account:

| rung | what it establishes | state |
|---|---|---|
| 0 | DNS, and a TLS 1.3 handshake against a DigiCert-issued `*.razorpay.com` certificate | **RUN** |
| 1 | an unauthenticated POST to the real charge URL returns a real status and a real error envelope | **RUN**, HTTP 401 |
| 2 | the same POST with a well-formed but fake `rzp_test_` key | **RUN**, HTTP 401, identical envelope |
| 3 | the shipped parser and the shipped `attempt()` on those real envelopes | **RUN** — and it **failed**, see error 28 |
| 4 | authenticate successfully and take a 200 | **RUN**, HTTP 200 on `GET /v1/payments?count=1` with a `rzp_test_` key |
| 5 | charge `success@razorpay` / `failure@razorpay` | **NOT RUN** — needs an authorised mandate, i.e. a `token_id` from a completed AutoPay registration |

`logs/razorpay_ladder.json` is the transcript. **Rung 5 is not counted as a
pass anywhere and nothing in `docs/` may claim it.**

⚠️ **What rung 4 does and does not establish.** It proves the credential is
accepted and that the shipped transport handles a success path — the
authentication half of the integration is real. It says nothing about the
recurring-charge **body**, because that endpoint charges a stored token and no
authorised mandate exists. `test mode` mocks the bank's approval, not the
registration flow, so obtaining a `token_id` is a checkout integration rather
than another connectivity rung.

**What rungs 1-3 do NOT establish.** They are rejected at the authentication
layer, so **Razorpay never read the request body.** The request shapes still
come from their public documentation, read 29 August 2026 and recorded in
`01_FACTS.md`; every unverified line is still marked `# UNVERIFIED` at the
line. **A doc-derived request body that has never received a 200 is still a
hypothesis.** What changed is that the *error envelope* is now recorded from
the wire instead of transcribed from a doc page — and that one contact found
two defects on the money path (errors 28 and 29) that eight green offline
gates could not.

| what | state |
|---|---|
| the reason -> family map, 110 values | **gated offline**, `test_razorpay_mapping.py` R1-R3 |
| a lost response becomes `pending`, never a decline | **gated**, R4 |
| deterministic idempotency key per money action | **gated**, R5 |
| Stage 0 refuses before the executor is reached, zero network | **gated**, R6, and demonstrated end to end by `scripts/prove_stage0_refuses.py` |
| `SimExecutor` unaffected by the `AttemptOutcome` change | **gated**, R7, and parity is still **bit-exact 24/24** |
| the Payment Downtime feed parses and normalises | **gated**, R8 |
| a refused CREDENTIAL is not recorded as a declined CUSTOMER | **gated**, R9, against the envelope captured from the live API. Error 28 |
| Stage 0 hands the executor the `action_id` it audited | **gated**, R10. Error 29 |
| every named mutant trips its own gate | **gated**, `--mutants`, 3/3. Error 30 |
| DNS, TLS and transport against the live API | **RUN**, `scripts/razorpay_ladder.py` rungs 0-2 |
| whether Razorpay accepts our recurring-charge body | **UNTESTED** — rungs 1-2 are rejected before the body is read, and rung 4 is a GET |
| whether an authenticated request succeeds at all | **RUN**, rung 4: HTTP 200 with a `rzp_test_` key |
| whether test mode returns populated `error_reason` values | **UNTESTED** |
| whether the Downtime feed is seeded in test mode | **UNTESTED** |
| the pre-debit notification call | **WIRED (decoupled flow).** `RazorpayExecutor.notify()` posts `POST /v1/orders` with the documented `notification` object (`token_id`, `payment_after`). Razorpay handles delivery of the regulatory pre-debit notification. Delivery is only considered proven when the corresponding `order.notification.delivered` event is observed — `ORDER_CREATED` is not delivery proof. **NOT RUN** against a live test token until rung 5a succeeds; delivery webhook **NOT RUN** until observed separately |
| funding-reminder email via SMTP | **RUN**, `scripts/prove_smtp_reminder.py`: one message through `RazorpayExecutor.remind()` → `agent/execution/smtp_delivery.py` (generic `SMTP_*` env vars, STARTTLS). Transcript `logs/smtp_reminder_proof.json`. Outbox JSONL row still written. **Not gate-protected.** |

**`AttemptOutcome` gained two optional fields** and both default to the old
behaviour: `pending` (the rail did not tell us whether the debit happened) and
`raw_code` (the vendor's own string, for the trail). Real UPI has a pending
state and a `bool` cannot express it; rounding an unknown down to "failed" is
the reading that licenses a retry, and a retry on an unknown is a double debit.
`SimExecutor` sets neither, R7 asserts that rather than assuming it, and
**`test_parity_vs_harness.py` was re-run after the change and is still
bit-exact 24/24 at pe1/pe7, fitted and unfitted.** So a modelling fix the real
rail genuinely needs cost nothing gated.

### 6c. `NOTIFICATION_CANCELLED` - do not remove it

`agent/constraints/auditor.py` rebuilds Stage 0 legality from the audit log
alone, sharing no code with the enforcer. In the outage-pause arms it reported
**45/112/182 `pending` violations** while the gate's own counter reported **0**.
**The auditor was right.** Pausing dropped a pending notification without
recording it, so from the log a withdrawn notification is indistinguishable from
a live one and the next one reads as a second concurrent notification.

`Stage0Gate.clear_pending` now emits `NOTIFICATION_CANCELLED` wherever a
notification is dropped (outage pause, cycle rollover). The two counts agree at
0. **If you add another path that drops a pending notification, it must emit
this event or the auditor will correctly report violations that did not happen.**

### 6d. THE AGENT'S HEADLINE IS A CAPABILITY CLAIM, NOT A RECOVERY NUMBER

Say this plainly and do not let it drift:

- The action space (retry / wait / nudge / escalate / stop) is worth
  **+0.136 pts at a 120-day horizon against a 2 SE of 0.205** on the frozen
  world — **not significant**, so the honest statement is that the action space
  has no measured effect on recovery at the shipping belief. Only the funding
  nudge at `p=0.25` and `p=0.50` clears its interval (+0.353 and +0.387).
  Its channel is mandate-death prevention, and the per-population gain still
  correlates with deaths avoided (r = +0.758, was +0.926). **It is no longer a
  curve that grows with the horizon**: on the shipped belief the gain is −0.053
  at 60 days, +0.136 at 120 and +0.010 at 180, none of them significant, and
  pre-registered check E-MECH-1 BROKE on that non-monotonicity
  (`logs/w27_stop_mech_repaired.txt`, record 2/4). Re-measured 1 September 2026,
  `logs/w27_abl_action_repaired.txt`. *(The **+0.498** this bullet carried was
  measured on the pre-W24 belief, and the +1.371 / +0.563 / +1.790 triple
  before it was pre-canonical. Both are superseded.)*
- The context layer (outage detection) is worth **0.000 pts at severities 0.00
  and 0.15, +0.017 at 0.40 and +0.051 at 0.80, none of them significant**, on
  the frozen world, `pause` and `both` alike and `suppress` flat throughout
  (`logs/w27_abl_outage_repaired.txt`). Severity is a pure `[GUESS]`. *(The
  "0.000 at every severity swept" phrasing this bullet used to carry was
  measured on the pre-W24 belief and is superseded — the conclusion is
  unchanged, the literal zeros are not.)*
- `NUDGE` is worth approximately zero and credits no money. `ESCALATE` is a
  zero-credit workflow action. `PARTIAL` is a recommendation only - its legality
  under one mandate is unestablished.

**What is defensible is the capability**: an aggregator detects a rail outage
with a measured false-alarm rate of **0/48 runs** and TPR **1.00** at n>=100,
severity 0.40, while a single merchant sees **0.38 attempts per 24h window**
against a floor of 8 and cannot evaluate the statistic at all. Plus a complete
audit trail, enforced constraints, and explicit stopping rules.

⚠️ **AMENDED 29 August 2026: "+0.256 is the whole recovery value" was the
DETECTOR's ceiling, not the problem's.** Measured against an oracle that knows
onset and recovery exactly, perfect detection is worth **+0.916 pts (2 SE
0.433, SIG)** at severity 0.80 where the shipping detector gets **+0.199
(n.s.)**. The gain decomposes with no residual: 1.4612 pts of outage damage
minus 0.5453 pts of unconditional pausing cost = +0.9159 measured. Quote
+0.256 as what the SHIPPING detector achieves and +0.916 as the headroom above
it; do not quote either alone.

**None of that makes pausing shippable.** Even the oracle is **significantly
negative at severity 0.15** (-0.413) and not significantly positive at 0.40,
because pausing costs half a point whether or not anything is wrong. The
crossover is between severity 0.40 and 0.80 and severity is a pure `[GUESS]`.
`02_RESULTS.md`, "THE DETECTION BENCHMARK".

Do not present either agent number as "money recovered". The money number is
still the one in section 2, and `payday_wait` is still a permanent row beside it.

**There is a measured batch number, in section 7b:** **99.38% against
`payday_wait`'s 90.29%, +9.08 pts (2 SE 1.84, SIG)**, Rs 7,511,500, zero Stage 0
refusals with an independent recount of zero. Run it with
`py -3.12 -m agent.batch_report --pops 10 --canonical`. *(98.01% / 57.70% /
+40.30 / Rs 6,203,060 is SUPERSEDED: measured before errors 33-35 were
fixed.)* The capability claim in this section
stands unchanged beside it.

---

## 7. THE LLM LAYER — added 29 August 2026. Built, measured, and bounded.

### 7a. What exists

| module | what it is |
|---|---|
| `agent/llm/client.py` | Z.ai transport. Cache keyed `(model, prompt_id, case_hash)`, hard budget, **never raises**. Reads `.env` from the repo root; `.env` is gitignored. |
| `agent/llm/prompts.py` | Versioned prompts. **The ID is part of the cache key**, so a prompt edit misses the cache and shows as a diff. Currently `glm-diag-v2` and `glm-judge-v2`. |
| `agent/llm/model_diagnoser.py` | `ModelDiagnoser`. An **overlay**, never a replacement. Any failure falls back to `RuleBasedDiagnoser`, emits `LLM_FAILURE`, and the row still says `source="fallback"`. |
| `agent/eval/golden_cases.yaml` | 40 registered cases + 7 `TX-` taxonomy cases + 3 `GC-I` injection cases. |
| `agent/eval/run_eval.py` | The eval. Deterministic arms always; `--llm`, `--judge`, `--replay`. |
| `agent/batch_report.py` | **The track deliverable.** Not `batch.py`, which is the composition root. |
| `agent/tests/test_decline_sweep.py` | What the decline taxonomy costs; bank-shaped outage detectability. |

**Diagnoser `glm-5.3-flash`, judge `glm-5.3`.** `run_eval.py --judge` **refuses
to run if the two SKU names are equal.** Flash never grades itself.

### 7b. What is measured

*Full tables in `02_RESULTS.md`. Author agreement, **not** accuracy — the cases,
the registered answers, the rubric and the baseline share one author.*

**Shipping path:** `MerchantNoteRoutedDiagnoser` calls the model only when
`merchant_note` is non-empty. Every other diagnosis tick uses the rule engine.

| | `merchant_note` registered (4) | injection (3) |
|---|---|---|
| `RuleBasedDiagnoser` | **4/4** | structural pass |
| routed LLM, `glm-diag-v3` | **2/4** | structural pass |

The full 40-case eval table is historical. Do not quote 6/21 vs 9/21 as
shipping evidence — those are cases the rules own in production.

**Batch:** deterministic **99.38%** against `payday_wait` **90.29%**,
**+9.08 pts (2 SE 1.84, SIG)**, ₹7,511,500 recovered, **zero Stage 0 refusals
with the independent auditor recounting zero over 8,702 executed money
actions**. No LLM column in the money table; `--llm` measures the overlay beside
the deterministic arm.

**Spend: $0.26** of a $5 budget, audited from the response caches. The per-run
budget counters sum to ~$0.16 and **under-report**, because each run is a fresh
process and the counter resets.

### 7c. THREE THINGS THAT BOUND EVERY LLM NUMBER

**1. `reasoning_effort=low`, and it is UNSWEPT.** Thinking cannot be disabled on
these SKUs — the API answers code `1210`. At the default the diagnoser emitted
**1,596 completion tokens** for an ~80-token answer and timed out; ninety
sequential calls did not finish in thirty minutes. **"The LLM scored X" means
"GLM-5.3-Flash at `reasoning_effort=low` scored X", and a higher setting may
score better. 10/21 may be a floor.** This is the first thing to sweep.

✅ **SWEPT 30 August 2026, and the caveat pointed the WRONG WAY.** `low` is the BEST of the three permitted settings on the ambiguous set: **10/21 at `low`, 7/21 at `high`, 9/21 at `max`** — and `max`'s row is the rule engine, because 32 of 50 calls hit the token cap and fell back. **10/21 is not a floor.** What IS invariant across all three settings is the terminal-code result, 4/4 against the rule engine's 0/4, which is the claim the LLM layer actually rests on. ⚠️ **And the marginal claim is not robust: at `high` the model LOSES to the rule engine on ambiguous cases, 7/21 against 9/21.** `02_RESULTS.md`, the reasoning_effort sweep.


**2. One draw per case.** `temperature=1.0`, responses cached, so every score is
a **single sample with no error bar.**

**3. The LLM cannot be called at every decision point.** The loop asks for a
diagnosis once per live mandate per decision hour — **119,667 times** over a
four-population batch. It runs under a hard per-run cap on **network** calls
(cache hits are free), giving a **94.8% fallback rate**. That is the design, not
a workaround — but it means **the batch's LLM arm is 95% deterministic and must
never be described as "the LLM's number".**

### 7d. What is open

* **19 judge-vs-author disagreements await human adjudication.** That is the
  validation step and the only one. `run_eval.py --llm --judge --replay` prints
  the table with reasoning, offline, for $0.00.
* **`reasoning_effort` unswept** (7c).
* **`WAIT` was cut on 29 Aug and the premise was wrong.** It was unreachable in
  the rule engine and was the LLM's *most-used* answer; removing it moved the
  ambiguous score 4/21 → 10/21. Both columns are in `02_RESULTS.md`; reverting
  is one commit. Error 20.
* **Judge false positives are real.** It flagged the approved phrasing "our
  model scores this window highest" as naming a time, three times. **An
  independent checker is a source of hypotheses, not of truth.**

### 7e. Reproducing it without a key

```
python agent/eval/run_eval.py --llm --judge --replay
```

**Byte-identical output in 0.35s, no network, $0.00 spent** — only the budget
line differs, correctly reporting zero. The caches are committed. That is what
makes an LLM number quotable under the numbers rule.
