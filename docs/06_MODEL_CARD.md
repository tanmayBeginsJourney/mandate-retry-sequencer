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

**The fitted constants live in one place**, `sim/w3.py`:

```python
FITTED_BELIEF = dict(stride=1, prior_w=12, prior_day0=8.0,
                     prior_floor=0.25, spend_beta=0.0)
```

Pass it as `harness.run(..., bcfg=w3.FITTED_BELIEF)`. Omitting it silently
gives you the **old unfitted** filter, which is ~13 points worse — that is not
a hypothetical, it is what gate **S4**'s mutant does on purpose.

How each value was chosen, and by what: `sim/fit_belief.py`. Selection was on
**training populations 600–607 only**, by outcome, never reading `c["payday"]`,
`c["salary"]` or `c["spend"]`. Everything reported here is on populations the
fit never saw.

⚠️ **CORRECTED 28 August 2026: `sim/fit_belief.py` CANNOT PRODUCE THIS
CONSTANT.** The string `prior_floor` appears nowhere in it, and it selects at
`payday_err=7` only — not "against the mean across `payday_err`" as `w3.py`'s
comment and this page previously claimed. The config it *can* emit scores
**94.98%** where `w3.FITTED_BELIEF` scores **95.57%**. The constant's measured
behaviour is fine (its gain grows with payday uncertainty instead of peaking at
the fitted point); its **provenance is fiction**. Full write-up: error 12.
Do not extend the search before 5 September — that re-opens a frozen constant.

**What each constant replaced, and why the old value was wrong:**

| | old | new | why |
|---|---|---|---|
| `stride` | 3 | 1 | The grid `[0,3,…,27]` left only **74%** of customers with a representable true payday, and only **31.7%** of those not paid on day 0. |
| prior | `exp(-0.10 d)` | window `w=12`, `day0×8`, floor `0.25` | Invented, never fitted. Put mass at distances the estimator cannot produce. |
| `spend_beta` | 0.045 | **0.0** | A hand-derived cross-mandate spend correction that turns out to be worth less than nothing. |

---

## 2. What it is worth

### Against the competitive baseline — the number that decides the project

`payday_wait` is the 5-line heuristic a good rival team builds in an afternoon:
wait for the estimated payday, then one attempt per day. It is a permanent row,
never omitted.

*n=100, 8 held-out populations (700–707), 120d, paired 2 SE. Not gate-protected;
reproduce with `python sim/headline.py`.*

| Payday known to | `payday_wait` | **shipping** | oracle | difference |
|---|---|---|---|---|
| ±1 day | **99.24%** | 95.73% | 100% | **−3.51** ±0.36 SIG — heuristic wins |
| ±3 days | 94.65% | **95.82%** | 100% | +1.17 ±1.35 **n.s.** |
| ±5 days | 72.18% | **95.82%** | 100% | **+23.64** ±2.61 SIG |
| ±7 days | 59.14% | **95.57%** | 100% | **+36.43** ±3.37 SIG |
| ±10 days | 48.11% | **95.62%** | 100% | **+47.50** ±3.17 SIG |
| ±14 days | 40.01% | **93.16%** | 100% | **+53.15** ±2.90 SIG |

**The crossover is between ±3 and ±5 days.** Below that, build the heuristic.
Above it, the gap is enormous and grows.

**The real argument is the shape, not the average.** The shipping filter sits at
**95–96% from ±1 to ±10** while the heuristic falls from 99% to 48%. It does not
care how wrong the payday estimate is. That is the product.

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
| A: in-distribution | 59.14% | 82.16% | **95.57%** | 86.18% | 85.97% | 100% |
| decay 0.20 (world only) | 75.19% | 92.03% | **98.86%** | 93.60% | 92.94% | 100% |
| decay 0.70 (world only) | 49.91% | 69.45% | **81.52%** | 74.99% | 75.85% | 99.97% |
| payday spread 0.60→0.30 | 57.95% | 82.08% | **90.42%** | 77.94% | 79.94% | 100% |
| `irregular_frac` 0.5 | 71.07% | 89.72% | **96.69%** | 89.66% | 88.92% | 100% |
| `topup_p` 0.25 | 69.77% | 85.02% | **95.84%** | 87.28% | 86.83% | 100% |

Every `ml − fitted` and `hybrid − fitted` difference is negative and
significant, ranging −5.26 to −12.48.

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

**+9.53 pts (±1.81), gated as S2a.** This is the defensible number.

⚠️ **S2a is measured on the UNFITTED filter** (`sim/tests.py:583-585` passes
no `bcfg`). The gate-protected moat number is therefore not the shipping
configuration's; the shipping figure is the ungated +9.61 below. Both agree, so
the claim is safe — but do not say "gated" and "shipping" in the same breath.

Measured on held-out populations, the moat *survives and grows* when the filter
is fitted: +8.20 (±0.92) unfitted → **+9.61 (±1.67)** fitted. Pooling was not
compensating for a bad prior.

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
| 0.2 | 55.60% | **88.62%** | 76.59% | **+12.03** ±2.37 SIG |
| 0.4 | 58.70% | **93.12%** | 82.29% | +10.83 ±1.49 SIG |
| 0.6 ← fitted here | 59.14% | **95.57%** | 86.18% | +9.38 ±2.09 SIG |
| 0.8 | 58.58% | **96.68%** | 91.38% | +5.30 ±1.83 SIG |

**No cliff.** 6.95 points of gentle, monotone degradation across a 4× change in
the parameter; never falls below the *unfitted* filter; stays 33–38 points above
`payday_wait` throughout. And the margin over ML **grows** as the population
moves away from the fit, because `prior_day0` is a *prior* that evidence
reweights within a cycle or two, whereas the GBDT learned the same fact as hard
splits and cannot revise it. **A wrong prior is recoverable; a wrong learned
split is not.**

---

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
   `02_RESULTS.md`. Never fitted, because fitting it on the evaluation set is
   what produced error 8.
6. **Cross-merchant pooling may not be legal.** Whether a payment aggregator may
   use Merchant A's outcomes to schedule Merchant B's debit for the same
   customer is **unresolved** — `01_FACTS.md` tags it `[GUESS]`. The moat is the
   central claim and its legal basis is unread.
7. **Top-up is pinned at 0** in everything except the one `topup_p=0.25` row.
   On the old harness, roughly half the apparent gain was "customers never top
   up". That sweep has **not** been redone properly on `w3`.
8. **Two of the five Stage 0 guarantees have no working test.** The attempt
   cap (M1 is VACUOUS) and the pending notification (M4 passes by
   construction — its mutant increments the counter itself; caught by M4B on
   28 Aug 2026). See §4 and error 11.
9. **n=100, 8 populations, one run seed each.** Not a large study.
10. **Only 2 of 25 gates run this configuration.** `S1_PD` and `S4` pass
   `bcfg`; every other gate — all mutants, all invariants, the T9 byte-lock
   and all three S2 arms — runs the *unfitted* filter. T9's exact-output lock
   in §5 therefore does **not** cover `w3.FITTED_BELIEF`. See error 13.
11. **The oracle is not clairvoyant about top-ups.** It reads
   `bal[tt] - drained` (`harness.py:524`) while dispatch reads
   `bal[t] - drained + topups[t]` (`harness.py:268`). Inert at `topup_p=0`;
   in the `topup_p=0.25` row above the 100% oracle is not a tight bound.
   The oracle is the upper bound the whole project is measured against, so a
   loose one flatters everything — and `topup_p` is exactly the sweep (A1)
   that is still open.

---

## 4. The six failing gates, and why each is failing

The suite is **25 gates: 5 FAIL, 1 VACUOUS, 19 pass.** All six are listed in
`sim/known_failures.txt` with a written reason. None may be fixed by loosening a
threshold.

⚠️ **TWO OF THE FIVE STAGE 0 RULES HAVE NO WORKING TEST** — the attempt cap
(M1, vacuous) and the pending notification (M4, vacuous-but-green, caught by
M4B on 28 Aug). Keep both out of the pitch and the architecture doc. Peak-hour,
notification-lead and Z9 re-presentation are genuinely tested.

| Gate | State | Why it is red |
|---|---|---|
| **S1** | FAIL | Calibration of `w3.Belief` (point-estimate payday) via `portfolio`. ECE 0.091 — *inside* the 0.10 bound — but the reliability curve is **not monotone**. **S1 does not measure the shipping filter**; that was error 9. |
| **S1_PD** | FAIL | The same threshold on the filter that *does* ship. ECE **0.026**, still not monotone. Fitting halved the error and did not order the curve. The remaining break is structural: the filter models no balance floor at zero, and approximates the world's hourly `U(0.4,1.6)` spend jitter with a fixed 3-tap kernel. **Neither is a parameter you can fit.** |
| **M1** | VACUOUS | The attempt-cap mutant cannot trip the counter at either operating point: the deepest any mandate-cycle reaches is 3 attempts at ±1d and 4 at ±7d, against `NPCI_MAX=4`, so a 5th attempt never happens. **Consequence: the NPCI attempt-cap compliance claim has no working test. Do not put it in the pitch.** Untried fix: run the mutant with `cap_override=2`, which tests the counter mechanism rather than the NPCI-specific value. |
| **S2b** | FAIL | The placebo control is not neutral (−14.09 pts). A finding about the *control's design*, not a code defect: `solo_placebo` injects observations computed against a different customer's balance, so it is actively misleading rather than merely uninformative. Left visible on purpose. A clean control — label-shuffled observations at the matched base rate — has not been built. |
| **S2_LEGACY** | FAIL | The retired point-estimate S2, kept unchanged and failing on purpose so the S2 rewrite is auditable rather than looking like test-loosening. Goes only when the point-estimate architecture is formally removed. |
| **M4B** | FAIL | **Added 28 Aug 2026.** No mutation branch may increment the counter its gate reads. Two do: `mutate="pending"` (`harness.py:610-612`) and `mutate="represent"` (`harness.py:333`). Measured: `pending` is **1066 counted, 1066 self-written, 0 independent** — so **gate M4 is vacuous and has been reporting PASS**. `represent` still binds (304 of 608 found independently); it only double-counts. Repair is in `harness.py`, which is frozen, and would move T9's reference. Procedure written in `sim/known_failures.txt`. |

---

## 4b. Reproducing the ungated numbers

`sim/ml_artifacts/` is **gitignored** — it holds a ~8 MB trained model and the
study outputs. Nothing in the gated suite needs it, but the ML and stress tables
above do. To rebuild from nothing, **in this order**:

```bash
python sim/fit_belief.py        # ~6 min  -> belief_fit.json  (the fitted config)
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

⚠️ **`fit_belief.py` and `ml_study.py train*` overwrite the fitted constant's
provenance and the model.** The constant itself lives in `w3.py` and is frozen;
re-running the fit does **not** change it. If a re-fit ever disagrees with
`w3.FITTED_BELIEF`, that is a finding — write it in `NOTES.md` and ask, do not
edit the constant.

⚠️ **`sim/ml_artifacts/` is gitignored, so none of the tables on this page is
reproducible from a clean clone without the rebuild above.** Two consequences
found 28 Aug 2026: (a) `belief_fit.json` — the only stored provenance record
of the fitted constant — reports **97.53%** for a call signature that measures
**95.57%**, and nothing reproduces 97.53%; (b) `requirements.txt` pinned only
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
against `sim/t9_reference.json` at both operating points — 28 configurations, 20
of them hashed at float level. Metrics catch a changed *decision*;
`calib_sha256` catches a changed *float*. Paired with a mutant that seeds the
worker pool from one shared RNG.

⚠️ **Corrected 28 Aug 2026: "anywhere in the belief filter" was wrong.** None
of T9's 28 configs passes `bcfg` (`t9_reference.py:54-56`), so every locked
configuration is the **unfitted** `BeliefPD` and the fitted-prior branch
(`w3.py:358-367`) is outside the lock. Read T9 as "anywhere in the *unfitted*
belief filter". Adding the fitted configs to `t9_reference.POLICIES` and
re-capturing is the repair, and it is a deliberate re-baseline — after
5 September. See error 13.

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
| `test_detection_benchmark.py` | **added 29 Aug 2026.** Excess loss against a clairvoyant detection oracle, decomposed into delay / missed / dropout / late resumption / false alarms. Three gates, four crippled oracles as **window transforms rather than code branches**, all four caught, none by every gate. **G-1b is deliberately RED** — it found a defect in its own hours-based loss and is kept visible rather than repaired. See `02_RESULTS.md`. |

Run them from the repo root with the interpreter named in `CLAUDE.md`. None of
them is part of `sim/gate.py`'s 25-gate suite, and none of their numbers is
gate-protected in the `--tier full` sense. Quote them the way the numbers rule
requires: name the script, say "not gate-protected".

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
  **+1.371 pts at a 120-day horizon**, and that figure is a **curve over the
  horizon** (+0.563 at 60d, +1.790 at 180d), not a constant. Its entire channel
  is mandate-death prevention.
- The context layer (outage detection) is worth **+0.256 pts at severity 0.80**,
  which is the most extreme setting swept and a pure `[GUESS]`. **Pausing on
  outage is significantly NEGATIVE at severity 0.40 (-0.529, SIG).**
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
