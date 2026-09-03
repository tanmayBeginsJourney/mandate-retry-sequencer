# Results

Every number here comes from simulation. No Razorpay transaction, mandate or
decline code has been observed by this project, and the simulation and the agent
share an author, so none of it is independent evidence of real-world
performance.

The system is described in [architecture.md](architecture.md). Defects found in
the measuring apparatus, and the controls added for them, are in
[errors.md](errors.md).

---

## The metric

The primary metric is **billing cycles collected ÷ billing cycles due**, over
the full horizon. A mandate that dies forfeits its remaining cycles, so the
metric prices mandate death without an invented lifetime-value constant.

Published industry figures are **recovery rates**: of the payments that failed,
the share eventually collected. That is a different quantity, since cycle
collection counts cycles that never failed. Both appear below and every table
names which one it is.

**Revenue at risk is defined by the world, not by a policy.** For every
mandate-cycle, `SimExecutor.at_risk_cycles()` asks whether a debit presented on
its due date would have cleared, reading the balance trace directly. The trace
is deterministic in `(population, seed)`, so the denominator is identical for
every arm. A denominator taken from each arm's own first attempt would move
between arms, and would score the agent on a denominator its own waiting had
shrunk.

---

## The canonical world

One world is canonical, and every current result is measured on it.
`agent/tests/_canonical.py` is the only definition; every script imports it.

| | |
|---|---|
| Customers per population | 500 |
| Mandates per customer | `1 + Poisson(1)` capped at 8 |
| Horizon | 120 days, 30-day billing cycles |
| Burn-in | 12 cycles simulated before day 0 and discarded |
| Mandate outflow | on |
| Payday | statutory window |
| Debit amounts | salary-independent, median ₹855 |
| Carry-over buffer | lognormal(0.25, 1.0) of salary, applied before each credit |
| `pop_spend` | 0.93 for the headline; scored across the region [0.80, 0.93] |
| `payday_err` | ±7 days for the headline; swept 1 to 14 |
| Populations | train 700–709, held out 710–719 |
| Run seed | 7 for the batch, 907 for the baseline comparisons |
| Merchants | mandates spread over 60 synthetic merchants |

Drawn over the ten held-out populations that gives **10,000 mandates across
5,000 customers**: a mean of 2.00 per customer, a maximum of 8, and 63.4%
holding more than one.

`pop_spend` is the share of a salary a customer spends per cycle, set to one
minus India's household saving rate. Three published FY25 readings — about
18–20% including physical assets, 11.8% gross financial, 7% net financial — put
it between 0.80 and 0.93. **No single point inside that range is declared.** 0.93
is the end of the range where the world's due-date failure rate falls inside the
published band, and the only end where enough debits fail to measure a
difference.

Burn-in has no free parameter: it is a convergence setting, checked by running
it longer. The carry-over buffer and the mandate outflow exist because without
them the world is a savings accumulator, and the due-date failure rate becomes a
function of how long the run is —
[errors.md](errors.md#the-world-had-no-steady-state). Every mechanism was
declared in writing before the measurement that would have selected it.

---

## Sample size

`n` is not inherited. It was measured, because the canonical world draws about
two mandates per customer where an earlier world fixed five, and an `n` chosen
under the second buys 2.5× the mandates under the first.

*The canonical experiment at five values of `n`, everything else held fixed: the
same ten held-out populations, the same k distribution, `pop_spend=0.93`,
`payday_err=7`, run seed 7, both arms. Agent measurement.*
`py -3.12 agent/tests/test_scale_n.py`, transcript `logs/w29_scale_n.txt`.

| n | mandates | cycles due | money actions | at risk | `payday_wait` | agent | uplift | 2 SE | recovery | V1 | wall |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 100 | 1,986 | 6,017 | 8,702 | 557 | 90.29% | 99.38% | +9.08 | 1.84 | 95.64% | 9.24% | 31s |
| 250 | 4,972 | 15,056 | 22,100 | 1,638 | 90.56% | 99.10% | +8.53 | 1.41 | 94.46% | 10.87% | 86s |
| **500** | **10,000** | **30,299** | **44,271** | **3,160** | **90.41%** | **99.12%** | **+8.70** | **0.68** | **94.94%** | **10.43%** | **185s** |
| 1000 | 20,063 | 60,806 | 88,641 | 6,331 | 90.55% | 99.11% | +8.56 | 0.40 | 94.24% | 10.41% | 358s |
| 2000 | 40,036 | 121,378 | 176,718 | 12,638 | 90.60% | 99.12% | +8.52 | 0.34 | 94.32% | 10.41% | 677s |

The n=100 row is the **superseded** canonical configuration. It is kept here
because it is the evidence for replacing it, not because any current figure comes
from it.

**n=100 is optimistic on every headline at once.** Against the n=2000 row it
reads +0.57 on the uplift, +1.32 on recovery of at-risk cycles and −1.17 on the
first-presentation failure rate — each of them larger than the interval the
experiment reports at n≥500, and each in the direction that flatters the result.

**n=500 is within 0.19 points of n=2000 on the uplift, 0.00 on the agent's own
collection and 0.02 on V1, at 3.6× the speed.** Past that point `n` is no longer
what limits the number.

### What limits it instead

*The same ten populations and the same world at n=500, on four independent run
seeds. Agent measurement.* `py -3.12 agent/tests/test_scale_n.py --seeds`.

| run seed | `payday_wait` | agent | uplift | within-seed paired 2 SE |
|---|---|---|---|---|
| 7 | 90.41% | 99.12% | +8.70 | 0.68 |
| 101 | 91.40% | 99.11% | +7.71 | 0.76 |
| 202 | 89.74% | 99.00% | +9.26 | 0.67 |
| 303 | 91.80% | 99.17% | +7.38 | 0.57 |

| | mean | sd | range | spread |
|---|---|---|---|---|
| uplift | 8.262 | 0.873 | 7.376 – 9.260 | 1.885 |
| `payday_wait` | 90.839 | 0.937 | 89.738 – 91.799 | 2.061 |
| agent | 99.101 | 0.074 | 98.999 – 99.175 | 0.176 |
| recovery of at-risk | 94.444 | 0.592 | 93.720 – 94.936 | 1.216 |

**The across-seed 2 SE of the uplift is 0.87; the within-seed paired 2 SE across
populations is 0.67.** They measure different things and neither contains the
other. Every interval quoted in this document is the second kind. A re-run on a
fresh run seed moves the uplift by about a point, and **the published intervals
do not include that.**

Almost all of it is the baseline. The agent's own collection spans 0.18 points
across those four seeds; `payday_wait` spans 2.06. `payday_wait` waits for the
estimated payday, so a run seed that draws better estimates helps it directly
and helps the agent barely at all.

Raising `n` to 1000 would shrink a 0.19-point error while leaving a 1.89-point
one untouched, at twice the compute. 500 is where the two cross.

---

## Gate-protected and agent-measured numbers

**Gate-protected** figures come from `sim/gate.py --tier full`, which runs on
every push and blocks on any unexpected failure; the gate name is given.
**Agent measurements** come from a script under `agent/tests/` or `scripts/`:
reproducible, with a committed transcript under `logs/`, but not run by the
gate. The batch headline is in this class. Every table below names its command,
its transcript and its class.

---

## Primary result

*Cycles collected. Full agent mode, 10 held-out populations (710–719), run seed
7, `payday_err=7`, `pop_spend=0.93`. Agent measurement.*
`py -3.12 -m agent.batch_report --pops 10 --canonical`, transcript
`logs/w30_headline_n500.txt`.

| arm | cycles collected | ₹ recovered | mandate survival | attempts/cycle |
|---|---|---|---|---|
| `payday_wait` (rival) | 90.41% | — | 90.70% | 1.276 |
| agent, deterministic | **99.12%** | **₹37,164,850** | 99.95% | 1.461 |

**+8.70 points, 2 SE 0.68** across populations — and about ±0.9 more across run
seeds, which that interval does not contain. See [Sample size](#sample-size).

Stopping rules that fired: `COLLECTED` 37,734, `CYCLE_CLOSED` 1,651,
`LAST_ATTEMPT_HELD` 204, `MANDATE_DEAD` 5. Stage 0 refused **0** actions and the
independent auditor recounted **0** illegal actions that executed, over
**44,271** executed money actions — two different quantities, and both being
zero on a clean run is not a cross-check
([architecture.md](architecture.md#enforcement-and-an-independent-recount)).

Recovery in the same run: 3,000 of 3,160 at-risk cycles, **94.94%**, with 44.20%
inside ten days and a median of 11.7 days. First-presentation failure 10.43%.

The command also prints the full chain behind one payment. That chain is a
separate, smaller run — one population of 40 customers on the same canonical
world with every decision tick logged, 7,189 events — because keeping every tick
for 500 customers produces a log nobody reads.

**The at-risk set excludes technical declines and the richer decline taxonomy**,
both properties of the rail rather than of funding, which makes it smaller than
a real failed-payment population and flatters every recovery rate here. The
headline batch also runs with the decline taxonomy off, so it contains no frozen
accounts, revoked mandates or limit hits. The taxonomy is implemented and its
cost is measured: with the limit-decline rate swept at 0.00 / 0.05 / 0.15 the
cost is 0.00 / 2.87 / 13.46 points. No source publishes those rates, which is
why they are swept rather than set.

---

## Baselines

- **`naive`** — Razorpay's documented schedule made legal: T+1 to T+4. It never
  uses a payday estimate.
- **`payday_wait`** — estimates the payday, waits for it, then attempts once a
  day. A simple 5 line heuristic. It targets the
  estimate on its first attempt only and then retries daily, so after one miss
  it burns the NPCI cap in three days.
- **`[1,7]`** — two attempts at frozen offsets from the same noisy payday
  estimate the agent is given. The offsets were selected once on train
  populations 700–709 by mean hit rate across `payday_err {1, 3, 7, 14}` and
  then frozen, which is what a merchant could deploy, since nobody knows their
  own estimate error in advance. It holds no belief and adapts to no outcome.

`[1,7]` is the strongest baseline. It exists because `payday_wait` is not a
steelman, and the comparison against it decides whether the approach is worth
building.

*Recovery of at-risk cycles. 10 held-out populations, 120 days,
`pop_spend=0.93`, run seed 907, paired 2 SE. Agent measurement.*
`py -3.12 agent/tests/test_steelman_schedule.py`, transcript
`logs/w30_steelman_n500.txt`.

| Payday known to | `naive` | `[1,7]` | agent | agent − `[1,7]` | paired 2 SE | |
|---|---|---|---|---|---|---|
| ±1 day | 19.79% | 99.89% | 97.90% | −2.00 | 0.73 | agent behind |
| ±3 days | 19.79% | 98.76% | 97.14% | −1.62 | 1.02 | agent behind |
| ±5 days | 19.79% | 95.97% | 96.89% | +0.91 | 0.96 | tie |
| ±7 days | 19.79% | 91.86% | 94.02% | +2.16 | 1.27 | agent ahead |
| ±10 days | 19.79% | 71.35% | 91.40% | +20.05 | 3.25 | agent ahead |
| ±14 days | 19.79% | 55.69% | 89.59% | +33.90 | 3.45 | agent ahead |

**The agent loses at ±1 and ±3 by more than the measurement error, ties at ±5,
and wins from ±7 upward.** The two losses were previously described as inside
their measurement error. They were being compared against each arm's own spread
across populations, which is the wrong interval for a difference between arms
that ran the same populations under the same seed; the paired interval above is
about half as wide and both losses clear it.

The agent is nearly flat across the range because it recovers the payday from
observed outcomes; `[1,7]` collapses because it cannot; `naive` is unaffected
because it never uses an estimate.

`[1,7]` loses no mandates at any level tested: 100.0% survival at all six,
against the agent's 99.0–99.5%. The agent's value at the predictable end of the
range is not higher collection; it is that collection does not depend on the
payday estimate being good.

**A large margin over `naive` is not evidence for the belief filter.** At ±1 day
the agent is 78.10 points ahead of `naive` and `[1,7]` is 80.10 ahead. Two fixed
offsets take more of that margin than the agent does at ±1 and ±3; from ±5
upward the agent takes more.

**How accurately payday can be estimated in India is unmeasured**, so the
operating point on this table is unknown. The Code on Wages requires payment by
the 7th or 10th of the month, most firms pay on the last working day, and
government salaries land on a fixed date. That evidence points at the low end,
where the frozen schedule wins.

---

## Sensitivity to world hardness

*Cycles collected, 10 held-out populations, 120 days, `payday_err=7`, run seed 7,
paired 2 SE. Agent measurement.*
`py -3.12 agent/tests/test_conditional_headline.py`, transcript
`logs/w30_conditional_n500.txt`. The 0.93 row reproduces the batch headline.

| `pop_spend` | `payday_wait` | agent | agent − baseline | at-risk cycles |
|---|---|---|---|---|
| 0.80 | 99.06% | 99.96% | +0.89 ±0.24 | 49 |
| 0.85 | 97.40% | 99.78% | +2.38 ±0.57 | 493 |
| 0.88 | 95.77% | 99.64% | +3.88 ±0.74 | 1,133 |
| 0.90 | 94.21% | 99.42% | +5.21 ±0.74 | 1,766 |
| 0.93 | 90.41% | 99.12% | +8.70 ±0.68 | 3,160 |

**The advantage is a curve in world hardness, not a number**, running +0.89 to
+8.70 across the externally derived region.

The last column is the number of billing cycles a debit on the due date would
not have covered. Both arms collect every cycle that was never at risk, so the
whole difference is carried by that column. At `pop_spend=0.80` there are 49 such
cycles across five thousand customers, so that row is a thin measurement rather
than a representative one.

`pop_spend` is a sharper dial than the row spacing suggests: solving for a target
failure rate rather than reading one off puts 12% due-date failure at
`pop_spend=0.7850` and 50% at 0.9627, so **0.18 of spend spans the whole
interesting region** (`py -3.12 scripts/solve_operating_point.py`,
`logs/w1_solve.txt`).

The published industry benchmark for retry optimisation is a 6–8% uplift; that
source does not state whether it means percentage points or relative uplift, so
the comparison is loose. No parameter was fitted against either figure.

### The page's slider

*The same configuration across `payday_err`. Agent measurement.*
`py -3.12 agent/tests/test_page_sweep.py`, transcript
`logs/w30_page_sweep_n500.txt`. Written to `sim/page_sweep.json`, which
`scripts/build_page_data.py` reads; the page does not carry a transcribed copy.

| `payday_err` | `payday_wait` | agent | difference | 2 SE | verdict |
|---|---|---|---|---|---|
| ±1 | 99.45% | 99.42% | −0.03 | 0.12 | tie |
| ±3 | 98.80% | 99.41% | +0.61 | 0.16 | agent wins |
| ±5 | 93.53% | 99.42% | +5.89 | 0.38 | agent wins |
| ±7 | 90.41% | 99.12% | +8.70 | 0.68 | agent wins |
| ±10 | 87.49% | 98.86% | +11.37 | 0.77 | agent wins |
| ±14 | 85.25% | 98.40% | +13.15 | 0.72 | agent wins |

Read the at-risk column of the table above it before this one. `payday_wait` is
the harness's own baseline and is weaker than `[1,7]`; this table is not a
substitute for [Baselines](#baselines).

---

## Pooling

`BeliefBook` holds one belief state per customer, shared across that customer's
mandates, so an outcome on one subscription updates the estimate used to time all
of them. The mechanism is implemented, it is exercised by every run, and it is
switchable: `pooling` takes `"all"`, `"none"` or `"consented"` plus a
per-customer consent set, so the non-pooled and consent-gated configurations are
measured rather than argued about.

**What it is worth has now been measured on the canonical world: 0.16 points
against a paired 2 SE of 0.16**, which is not distinguishable from zero. The
same measurement on the gate suite's world, where every customer holds five
mandates, gives 6.47 points. Both are current measurements of the same
implemented mechanism in two populations; the second is a sensitivity
experiment, not the headline.

*Cost of running fully non-pooled, degenerate mode, 10 held-out populations,
120 days, `payday_err=7`, run seed 907, paired 2 SE. Agent measurement.*
`py -3.12 agent/tests/test_pooling_consent.py --canonical`, transcript
`logs/w30_pooling_canonical.txt`; without the flag it runs the gate suite's
world, transcript `logs/w30_pooling_gateworld.txt`.

| world | mandates per customer | `pop_spend` | not pooling costs |
|---|---|---|---|
| **canonical** | **`1 + Poisson(1)`, mean 2.0** | **0.93** | **0.16 ±0.16** |
| gate suite | 5, fixed | 1.05 | 6.47 ±0.62 |
| gate suite | 5, fixed | 0.80 | 1.30 ±0.42 |

**Pooling pays in proportion to how many mandates a customer holds, and the
canonical world holds about two.** 63.4% of its customers hold more than one, so
the mechanism is present, not absent — but with a mean of two there is one extra
observation to share, and at `pop_spend=0.93` the pooled arm already collects
99.01%, so there is almost no headroom for the extra observation to recover.

**The size of the effect is conditional on the mandate count**, and the mandate
count is derived rather than observed. Published aggregates — about 95 million
active AutoPay mandates against a UPI base past 500 million users — put the mean
in the range 1 to 3, centre 2; the exact `1 + Poisson(1)` distribution is a
modelling approximation, because no source gives a per-consumer distribution.
How much turns on the count is measured rather than assumed: at the canonical
mean of two, pooling is worth nothing measurable; at a fixed five, above the top
of the derived range, it is worth 6.47 points on a harder calibration. Full
derivation in [Sources](#sources). **The aggregator argument is therefore conditional on a
population figure derived from aggregate statistics, not on an observed
distribution.**

Four of five predictions registered before this measurement broke on the
canonical world, all in the direction that removes the claim. Prediction 1 gave
4–14 points and measured 0.16. Prediction 4 gave 3–7 points for consent-gating
at 50% and measured 0.07. Prediction 5 gave the non-pooled agent beating
`payday_wait` by more than 20 points and it beats it by 7.96. The record for
this measurement on the canonical world is 2 of 5, and the pre-registration is
what makes that readable.

### The consent curve

Pooling is a per-customer permission. What withholding it costs, on the gate
suite's world where the effect is large enough to have a shape:

| arm | `pop_spend=1.05` | vs pooled | `pop_spend=0.80` | vs pooled |
|---|---|---|---|---|
| pooled (`all`) | 97.60% ±0.56 | — | 99.82% ±0.19 | — |
| consent 75% | 96.53% ±0.76 | −1.06 ±0.29 | 99.59% ±0.29 | −0.23 ±0.18 |
| consent 50% | 94.82% ±0.88 | −2.77 ±0.42 | 99.25% ±0.41 | −0.57 ±0.25 |
| consent 25% | 93.50% ±0.82 | −4.10 ±0.48 | 98.86% ±0.53 | −0.96 ±0.37 |
| not pooled (`none`) | 91.13% ±0.87 | −6.47 ±0.62 | 98.52% ±0.58 | −1.30 ±0.42 |

Consent at 100% is bit-identical to pooled and consent at 0% to non-pooled, in
10 of 10 populations each: two routes to one state that disagreed would be a
defect. On the canonical world the same curve is flat, because its endpoints are
0.16 points apart.

Gate `S2a` measures the same quantity in the harness rather than the agent, at
`pop_spend=1.05` with five mandates: +9.53 points (±1.81) unfitted and +7.32
(±2.02) on the shipping filter as `S2a_PD`. Those are gate-protected and they
are measurements of the gate suite's world, not of the canonical one.

### Legal status

Whether cross-merchant pooling — a payment aggregator using one merchant's
transaction outcomes to schedule another merchant's debit for the same customer
— is lawful in India **has not been established either way**. The treatment is
jurisdiction- and provider-dependent, and it is a question of law rather than of
engineering, so it sits outside the scope of this evaluation. What is on the
record, all secondary:

- `[REPORTED]` No single Indian statute or RBI circular addresses the scenario
  directly.
- `[REPORTED]` UPI AutoPay mandates are structurally per-merchant, and the NPCI
  specification contains no cross-mandate or cross-merchant retry logic.
- `[REPORTED]` RBI's consolidated Payment Aggregator Directions, 2025 impose
  merchant segregation across onboarding, KYC and transaction monitoring.
- `[REPORTED]` India's DPDP Act 2023 requires data be used only for the purpose
  it was collected for, with specific and informed consent; the DPDP Rules
  notified in 2025 phase that in.

That reading was produced by a language model, was not written by a lawyer, and
was not checked against the primary documents. Razorpay's merchant terms, their
privacy policy, the RBI PA Directions 2025 text and the October 2025 NPCI
circular on Merchant Identifier Codes have not been read.

The engineering response is to make the question a runtime setting rather than a
design assumption: pooling is a per-customer permission, the non-pooled and
consent-gated configurations are measured above, and the system ships whichever
answer an operator's counsel reaches. **Engineering consent is not a legal
conclusion.**

---

## External validation

There is no public benchmark for payment retry scheduling: no shared dataset, no
held-out set, no leaderboard. The only formal artifacts in the space are patents
on machine-learned dunning, which describe methods and publish no data.

What exists is aggregate statistics published by companies that sell recovery
software. They are second-hand, they aggregate customer bases that are not
comparable, and one states in its own methodology note that its figures are
ranges rather than laws. The world was not fitted to any of them.

**The four rows do not share a sample size.** V1 and V3 are properties of the
world and of a policy this project did not write, and are measured on **100
populations** (`py -3.12 agent/tests/test_v3_power.py`, transcript
`logs/w30_v3_power_n500.txt`). V5 and V7 measure the agent, on **20**
(`py -3.12 agent/tests/test_canonical_world.py --confirm`, transcript
`logs/w30_canonical_n500.txt`). All at n=500, `pop_spend=0.93`, run seed 907.
Agent measurements.

| | measured | populations | published | verdict | ceiling |
|---|---|---|---|---|---|
| **V1** first-presentation failure rate | 10.62% ±0.24 | 100 | 8–15% | **hit** | — |
| **V3** recovery under fixed-interval retries | 21.80% ±0.71 | 100 | 20–40% | **hit** | — |
| **V5** recovery under smart retry timing | 94.19% ±0.72 | 20 | 70–85% | miss, too high | 100% |
| **V7** share of recoveries inside 10 days | 42.90% | 20 | 85–95% | miss, too slow | 51.9% |

**Read V3 from the 100-population run, not the 20.** On the same 20 populations
that carry V5 and V7, V3 reads 20.28% against a 2 SE of 2.04 — inside the band,
but not distinguishably above its floor. At 100 populations it reads 21.80%
against a 2 SE of 0.71, clearing the floor by 1.80. The two are the same
quantity in the same world at different sample sizes, and the second is the one
with the power to answer the question. This was checked because the 20-population
figure would, on its own, have partly met the falsification condition below.

**Why V5 sits above its band.** V3 and V5 are measured in the same world, at the
same calibration, with the same run seed. V3 runs a policy this project did not
design and lands inside its published band, clearing the floor by more than its
measurement error. A world calibrated to be easy would lift both. V3 is in band
and V5 is 9 points above it, so the excess is a property of the agent rather than
of the world.

**What would falsify that.** V3 dropping below 20% at a larger sample, which
would mean the world is harder than the published baseline range and V5's excess
is not the agent. Or V3 sitting in band only at a calibration where V1 leaves its
own band, which would be two dials fitted to two targets rather than a world
matching on both.

**The selection half of the sample no longer matters.** `test_canonical_world.py`
scores populations 700–719, and 700–709 are the populations the belief prior was
selected on. Split at `pop_spend=0.93`, V5 is **94.02% ±1.17** on the held-out
ten and **94.37% ±0.90** on the selection ten — a 0.35-point gap inside both
intervals. Quote the held-out figure. At n=100 the same split showed a 0.81-point
gap, which is what made this worth separating in the first place.

**At `pop_spend=0.88`** the same run reads V1 3.70%, V3 19.85% ±3.01, V5 93.77%
±1.64, V7 43.98% against a ceiling of 52.5%. V1 leaves its band there, which is
why 0.93 is the end of the region that matches the public record.

**The ceiling column** is a clairvoyant schedule that still obeys the
four-attempt cap, the 24-hour notice rule and legal presentation hours.

- **V5's ceiling is 100% at every cell**, so V5 measures the agent's behaviour
  rather than the world's difficulty; it is not a failed world check. It also
  closes recovery as a direction of work — 94.19% against a 100% ceiling leaves
  under six points of headroom, and taking any of it pushes V5 further out of
  band.
- **V7's ceiling is 51.9%, below the published band's floor of 85%.** The rail
  cannot reach that band. The agent captures 82.6% of what is available. The
  band is drawn from card dunning, where a customer can fix the instrument on
  demand; UPI AutoPay recovery waits on a roughly monthly salary credit.
- **V1 and V7 cannot both be in band** in any world with one salary credit a
  month. Where V1 is in band the V7 ceiling is 47–52%; where the V7 ceiling
  clears its floor, V1 is near 2%.

**What was tried and did not fix the misses.** Temporary account holds were
expected to account for V7 and, swept across 14 alternative worlds, moved it by
under one point. Adding customers who genuinely cannot pay brings V5 into band
and moves V1 out of it. Income paid in several instalments a month is the only
mechanism found that lifts the ten-day ceiling — swept over irregular-income
fractions of 0.20 to 0.60 and 4 to 12 credits a month, it reaches 87.57% at the
top corner and 71.8–81.2% in the middle (`logs/w12_irregular_ceiling.txt`,
measured policy-free). It ships off: no source gives a payment-frequency mix for
UPI AutoPay holders, and even the top corner leaves V7 around 73%, still below
its band. Those sweeps predate the belief repair and the current `n`; they are
quoted for direction only.

**What this comparison is not.** Both bands are `[REPORTED]` and vendor-sourced.
V5's band is the source's top performers; that source's stated median is 47.6%.
The world and the agent share an author. This is an internal consistency
argument, not independent evidence.

---

## The diagnosis layer

The shipping path routes the diagnoser to the model only when `merchant_note` is
non-empty. Everything else stays on the deterministic rule engine.

*40 registered golden cases, 3 injection cases and a terminal-code block. Prompt
`glm-diag-v3`, `glm-5.3-flash`, `reasoning_effort=low`.*
`py -3.12 agent/eval/run_eval.py --llm --judge --replay` replays from committed
response caches in 0.5s at $0.00; transcript `logs/w30_llm_eval_replay.txt`. The
live measurement cost $0.08.

| | rule engine | routed LLM |
|---|---|---|
| `merchant_note` cases (4 registered) | 4/4 | 2/4 |
| Injection cases (3) | no injected string survives sanitisation | no injected string survives sanitisation |
| Terminal decline codes | `STOP` or `ESCALATE` on 4/4 | `STOP` or `ESCALATE` on 4/4 |
| Full 40-case set | 28/40 | 26/40 |

**The model does not currently beat the rule engine on the path it ships on**,
and the routed subset is four cases, too small to establish either direction.
The full 40-case comparison is kept in the harness for history and is not
shipping evidence: those cases were written before routing existed, and the rule
engine owns every decision tick outside the routed subset.

All eight pre-registered checks held on this replay.

**The judge is a different model.** `glm-5.3` judges `glm-5.3-flash`. It
disagrees with the registered answer on 13 of 29 judged cases, and the
disagreement is concentrated where the case file predicted it would be: 9 of the
12 judged cases flagged as low expert agreement, against 24% among the other 17.
It reports zero financial-state leaks and zero times in the merchant-facing
prose. Three of its earlier "names a time" flags were rejected because they
flagged the exact phrasing this project prescribes as compliant: an independent
checker is a source of hypotheses, not a source of truth.

**The money figures do not depend on the model.** `agent.batch_report` runs the
deterministic path, and simulated populations carry no merchant notes, so the
routed path has nothing to route. The model's `STOP` and `ESCALATE` decisions do
prevent debits when it is invoked; what it cannot do is name a day or an hour,
and the scheduler is called with a fixed `RETRY` intent that no diagnosis
reaches.

---

## Outage detection

An empty account and a degraded payment rail both produce a failed debit, and
the balance estimate cannot tell them apart. `rail_monitor.py` computes the
exact binomial tail probability of the technical declines seen in a rolling
24-hour window against the `P_TECH = 0.008` base rate, and suppresses the belief
update when the rail looks degraded. It refuses to evaluate a window holding
fewer than eight attempts.

**Two worlds are measured, and they answer different questions.** The detection
study sweeps population size to find where the detector starts working, and runs
the higher-volume world — five mandates per customer at `pop_spend=1.05` — where
that crossover is visible. The canonical run answers whether the world the
headline is measured on has the volume.

*6-hour outages on days 20 and 40, 60 days, `payday_err=7`, worst-case placement
at the decision hour. Agent measurement.*
`py -3.12 agent/tests/test_outage_detection.py --canonical`, transcript
`logs/w30_detect_canonical.txt`; without the flag, `logs/w30_detect_study.txt`.

| customers | canonical: attempts / 24h | TPR at severity 0.40 | detector study: attempts / 24h | TPR at 0.40 |
|---|---|---|---|---|
| 25 | 1.9 | 0.10 | 5.6 | 0.12 |
| 50 | 3.9 | 0.30 | 11.6 | 0.88 |
| 100 | 7.6 | 0.70 | 23.1 | 0.75 |
| 200 | 15.4 | 1.00 | 46.3 | 1.00 |
| **500** (canonical) | **38.5** | **1.00** | — | — |

**At the canonical 500 customers the world carries 38.5 attempts per 24-hour
window, nearly five times the detector's floor, and every injected outage at
severity 0.40 is caught.** At severity 0.15 it is caught in 0.80 of runs.

**At 100 customers the canonical world sits on the floor and still detects.**
Its mean is 7.6 attempts per window against a floor of 8, and the detector fires
in 0.70 of runs, because window volume varies around that mean and individual
windows clear it. A claim that a hundred-customer book at two mandates per
customer cannot detect an outage was published on the strength of the mean
alone; it is wrong, and the measurement above is what replaces it.

**False alarms are 0 of 70 runs at severity 0** in the canonical world and 0 of
48 in the detector study.

**Two pre-registered checks broke in both worlds.** E-DET-4 required a
true-positive rate of at least 0.8 at 100 customers; it is 0.70 canonical and
0.75 in the detector study. E-DET-2 required the rate to be non-decreasing in
population size, and at severity 0.15 it is not. The records are 5/7 and 4/6.

**Volume is why an aggregator can run this test and a merchant cannot.**
Mandates are spread over 60 merchants, so one merchant sees a sixtieth of the
stream: 0.64 attempts per 24-hour window at the canonical 500 customers, against
a floor of 8. A single merchant never reaches the floor at any population size
tested, in either world.

### Bank-shaped outages

Customers are spread across eight bank handles. An incident at one bank lifts
the pooled failure rate by roughly an eighth of its severity, below the detection
threshold.

*n=200, severity 0.80, four 6-hour windows, 8 populations, the detector study's
world. Share of outage windows detected.*
`py -3.12 agent/tests/test_decline_sweep.py`, transcript
`logs/w27_decline_sweep_repaired.txt`. **Not re-run at the canonical `n`.**

| scope | detected |
|---|---|
| every bank | 0.72 |
| `@okaxis`, the best single bank | 0.38 |
| mean over the eight single banks | 0.21 |
| `@oksbi`, the worst single bank | 0.06 |

Pooling makes a rail-wide outage about 3.4× more detectable than a bank-shaped
one. Razorpay's Payment Downtime API already publishes bank-scoped downtime, so
this is not a capability a merchant lacks. What differs is narrower: their feed
measures their own traffic mix, `severity` is a three-valued label rather than a
rate a scheduler can act on, a PSP is marked down only when every handle under
it is down, and this detector has a measured latency and false-alarm rate where
theirs is unstated. The posture is complement, not replacement, and the combined
design is not built and not measured.

### What acting on detection is worth

*10 held-out populations, 120 days, degenerate mode, paired 2 SE against the
monitor-off arm at the same severity. Agent measurement.*
`py -3.12 agent/tests/test_outage_ablation.py --canonical`, transcript
`logs/w30_abl_outage_n500.txt`.

| severity | pausing dispatch is worth | 2 SE | significant |
|---|---|---|---|
| 0.00 | +0.000 | 0.000 | no |
| 0.15 | +0.050 | 0.037 | yes |
| 0.40 | +0.082 | 0.065 | yes |
| 0.80 | +0.221 | 0.056 | yes |

**Pausing is worth a small, measurable amount that grows with severity, and it
is a fifth of a point at the top of the range.** At the earlier sample size the
same arms measured +0.000 / +0.017 / +0.051 and nothing was significant; that was
a null result from insufficient power, not a zero. The pre-registered bar for
pausing being worth having was more than one point at severity 0.80 (E-OUT-5) and
it is not met, so pausing stays off by default. What ships is detection plus
suppression of the belief update.

Severity is a `[GUESS]`: no source reports what fraction of AutoPay executions
fail during a UPI incident. Window placement is worst-case — every outage starts
at the hour where almost every attempt lands — so these are upper bounds on both
the damage and the benefit.

Two of six pre-registered checks broke. Suppression alone makes calibration worse
at severity 0.80, not better (ECE 0.0819 without it, 0.0858 with it), which
contradicts E-OUT-3 and the argument behind it. And `both` is numerically
identical to `pause` at every severity, because a paused dispatch produces no
technical decline for suppression to act on: the two do not compose.

**Pausing also suppresses the evidence detection needs.** A paused attempt
produces no outcome and the detector counts outcomes, which is why detection
power is measured with the response off.

---

## The action space

The agent in degenerate mode — retry only, deterministic diagnoser — reproduces
the simulation harness bit-exactly, so every point of difference between
degenerate mode and a richer arm is the agent rather than the timing model.

*10 held-out populations, 120 days, `payday_err=7`, paired 2 SE against
degenerate. Agent measurement.*
`py -3.12 agent/tests/test_action_ablation.py --canonical`, transcript
`logs/w30_abl_action_n500.txt`.

| arm | cycles collected | vs degenerate | 2 SE | significant |
|---|---|---|---|---|
| degenerate | 99.03% | — | — | — |
| every action off, workflows off | 99.03% | +0.000 | 0.000 | — |
| every action off | 99.10% | +0.068 | 0.099 | no |
| + nudge at `p=0.10` | 99.17% | +0.131 | 0.092 | yes |
| + nudge at `p=0.25` | 99.20% | +0.167 | 0.100 | yes |
| + nudge at `p=0.50` | 99.36% | +0.322 | 0.139 | yes |
| + escalate | 99.10% | +0.068 | 0.099 | no |
| + stop | 99.10% | +0.068 | 0.099 | no |
| everything, `p=0.25` | 99.20% | +0.167 | 0.100 | yes |

**Read the second and third rows together.** `mode="full"` does not only open
the action space: it also turns on the backup checkout and the fail-path
reminders. The third row is degenerate plus those two workflows and no actions,
and the +0.068 it carries is theirs. Every full-mode row below it contains the
same +0.068. The second row turns the workflows off as well and is bit-identical
to degenerate in all ten populations.

**`ESCALATE` and `STOP` are worth exactly 0.000 against the row that isolates
them** — identical collection, identical rupees, identical survival to the
every-action-off arm. That is the one claim in this family that has survived
every re-measurement.

**Only the funding nudge clears its interval**, and `nudge_p` is a swept
parameter rather than a chosen one, because this world models no customer
response to a reminder. The whole action space at the swept midpoint is worth
+0.167 points.

Two of nine pre-registered checks broke: `ESCALATE` was predicted in [−0.3, 0.0]
and measures +0.068, and the full arm was predicted not to beat degenerate and
beats it by 0.167. E-ABL-1 — the consistency check that the ablation isolates
what it claims to — now holds. It could not hold as originally registered,
because it was scored against the arm that carries the two workflows.

### Where `STOP` gets its value

*The same populations across three horizons. Agent measurement.*
`py -3.12 agent/tests/test_stop_mechanism.py --canonical`, transcript
`logs/w30_abl_stop_n500.txt`.

| horizon | degenerate | + `STOP` | gain | 2 SE | significant | mandates dead, degenerate → `STOP` |
|---|---|---|---|---|---|---|
| 60 days | 98.21% | 97.95% | −0.253 | 0.280 | no | 160 → 2 |
| 120 days | 99.03% | 99.10% | +0.068 | 0.099 | no | 76 → 4 |
| 180 days | 99.45% | 99.53% | +0.079 | 0.051 | yes | 60 → 4 |

The gain grows with the horizon, which is what the proposed mechanism —
preserved mandates collecting in later cycles — requires. It is negative at 60
days, where holding an attempt back costs a cycle that the horizon then never
gives back. The per-population correlation between the gain and the deaths
avoided is +0.263, well under the 0.5 that was pre-registered, so deaths are not
the whole channel and the mechanism is only partly confirmed.

---

## The discount factor

The timing score contains a hand-chosen 0.92 discount on `p_later`. It is not
inert: it multiplies `p_later`, so it changes the sign of the index and
therefore the wait-or-attempt decision.

*`solo_shared_pd` with the shipping belief, `payday_err=7`, evaluation
populations 700–707, 9 grid points, n=100. Agent measurement, and it predates
the canonical `n`.* `py -3.12 scripts/discount_sweep.py`, transcript
`logs/w28_discount_sweep.txt`.

| discount | 0.80 | 0.85 | **0.88** | 0.90 | **0.92 (ships)** | 0.94 | 0.96 | 0.98 | 1.00 |
|---|---|---|---|---|---|---|---|---|---|
| recovery | 93.46% | 94.77% | **95.16%** | 94.90% | **94.72%** | 93.95% | 93.26% | 92.71% | 91.31% |

**Every number on this page owes a band of about 3.9 points to this constant.**
The plateau spans 0.88–0.92 within 0.44 points, so the shipped value is not
perched on a spike.

The argmax on this grid is 0.88, not the shipped 0.92. The constant was **not**
re-selected against it, because re-selecting a constant on an evaluation set is
the fit that produced an earlier defect in this project. The shipped value is
0.44 points below the best cell on this grid, and that is stated rather than
closed. The levels here are from the smaller sample; the shape is what is
quoted.

---

## Negative results

**A frozen two-offset schedule beats the agent when payday is well known.** See
[Baselines](#baselines). The crossover is at ±5 days, and the available evidence
about Indian payroll practice points at the losing side.

**Backward induction over the whole cycle makes things worse.** Extending the
continuation value from the last attempt to every attempt as a dynamic program
breaks all three of its pre-registered checks: mandate deaths rise from 144 to
3,295, attempts per cycle rise rather than fall, and mean recovery falls by over
eight points. `plan` ships off and stays off, kept with its property test.
`logs/w25_dp_monotone_stage_e.txt`, measured at n=100.

**The generatively correct belief filter does not pay.** The world's balance is
non-increasing between salary credits and the shipped filter's is not.
`w3.BeliefPD(monotone_drain=True)` applies the diffusion kernel to the drain,
whose support is non-negative, which is the correct model. Over 120
population-cells it is **−0.26 points against a paired 2 SE of 0.65** —
indistinguishable on recovery — and it is off because it kills more mandates at
the shipping horizon, 389 against 144, which is what the declared tie-break on
survival penalises. It defaults to `False`, an unconfigured `BeliefPD` is
bit-identical to before, and gate `T9` locks that byte for byte across 34
configurations, so the repair cannot silently reach a published number.

**Coordinated budgeting across a customer's mandates is harmful.** The
`portfolio` policies lose to `solo_shared_pd` in every table and in the `T9`
reference. Cut.

**The negative control is not neutral, and it is left failing.** `solo_placebo`
pools with identical mechanics, timing and observation count, but computes
outcomes against a *different* customer's balance. Gate `S2b` requires it to be
neutral against the non-pooled arm and it measures **−14.09 points**. Extra
unmatched `observe()` calls damage this filter, because the update is a hard
truncation rather than a martingale — a donor balance, a label shuffle and a
posterior-predictive draw were all worse than the non-pooled arm. The
consequence for the pooling claim is direct: an earlier headline figure for
pooling was placebo-versus-pooled, and for paired means that is algebraically
`S2a + |S2b|`. **Most of that figure was placebo damage rather than pooling
benefit.** Quote `S2a` or `S2a_PD`, never the placebo comparison.

**Suppressing technical declines does not improve calibration on its own.** The
mechanism is real — a technical decline hard-zeroes balance mass, and a pooled
belief carries that damage to all of the customer's mandates — but suppression
alone moves calibration error the wrong way at high severity, because it removes
genuine information along with the noise.

**Top-ups no longer explain the result.** On an earlier harness roughly half the
apparent gain was "customers never top up". Re-run on the current world, an
unconditional top-up sweep moves the shipping policy by +0.02 points (2 SE 0.59)
while moving `payday_wait` by +11.4. The mechanism is live; the shipping policy
has nothing left to recover.

---

## The live rail

Everything in this section is about `live/`, the service that runs the same
decision layers against Razorpay. **None of it is evidence about Razorpay.** The
gates run against a mock rail written from Razorpay's published documentation.
They establish that the state machine, the crash handling and the safety
boundaries behave as described; they establish nothing about whether Razorpay
accepts these request bodies, because Razorpay has never read one.

### What has and has not touched Razorpay

| | Evidence |
|---|---|
| Test-mode API authentication | **exercised** — HTTP 200 on `GET /v1/payments` with an `rzp_test_` key, transcript `logs/razorpay_ladder.json` |
| The shape of a real API-level error envelope | **exercised** — an unauthenticated POST to the recurring-charge endpoint returns `code` and `description` alone, same transcript |
| Test-mode Customer and Payment Link create, fetch, cancel | implemented, runnable against `rzp_test_` keys; **no transcript is committed** |
| UPI AutoPay mandate registration | **not available** on the test account used here. UPI and Recurring Payments are on-demand Razorpay features and were not provisioned, so no `token_id` exists for any request to reference |
| Pre-debit notification order | implemented; **not demonstrated** against an authorised mandate |
| `order.notification.delivered` from Razorpay | **not observed** |
| Webhook signature verification against a payload Razorpay signed | **not observed** |
| A recurring charge on an authorised mandate | implemented; **never submitted** |

No live API key exists for this project. Razorpay's current documentation
requires verified website details before live keys can be generated, and the
verification takes up to three working days. Everything below therefore stops at
the boundary where money would move.

### The offline gates

`py -3.12 -m live.tests.run_all` — seven gate files, 218 checks, about four
seconds. Each file runs in its own process. None needs a key, opens a socket to
Razorpay, or can move money.

| Gate file | Checks | What it establishes |
|---|---|---|
| `test_config` | 24 | The mode switch fails closed in both directions. Live mode with a missing credential raises rather than demoting to the mock; the debit flag in offline mode raises rather than being ignored; only the literal word `yes` enables real debits; no secret appears in what the console is shown |
| `test_state_machine` | 22 | Every ordered pair of attempt states, exhaustively. No transition walks backwards, terminal states are final, two different terminals are recorded as a conflict rather than resolved, and a deemed transaction reads as unknown rather than failed |
| `test_webhooks` | 32 | Signature over raw bytes; a re-serialised body fails, which is why the verifier takes bytes. A duplicate delivery adds no row. A late `payment.authorized` cannot displace a `payment.captured`. A forged signature is rejected **and recorded**. An unhandled event type is acknowledged rather than 4xx'd |
| `test_flow` | 45 | The lifecycle end to end, and seven crash boundaries: a lost response stays unknown and blocks a second debit, an order lost to a crash is recovered by its receipt rather than created twice, an accepted-but-uninterpreted webhook is replayed at startup and replaying it again is a no-op, a restart mid-debit finishes the debit without creating a second attempt |
| `test_safety` | 42 | `Diagnosis` has no temporal, monetary or identity field. A prompt injection in the merchant note produces a diagnosis and nothing else. Stage 0 refuses a peak-hour debit with zero provider calls. No route accepts an amount. The demonstration clock cannot move in live mode |
| `test_parity` | 15 | The live service and the batch run reach the **same objects** — `timing.propose`, `BeliefBook`, `Stage0Gate` — checked by identity. `live/service.py` defines no decision function of its own, and nothing under `agent/` imports `live/` |
| `test_api` | 38 | The served surface over a real socket: content security policy, path traversal, body limits, the header names Razorpay sends, and that loading the console makes no provider call and creates no attempt |

The import-graph gates in `agent/tests/test_layer_isolation.py` gained two rules
for the new package and now run **nine named mutants, nine tripped**, over 87
files under `agent/` and 18 under `live/`, with zero violations.

### What the mock does, and what that is worth

The mock answers the way Razorpay's documentation says Razorpay answers, and it
fails on purpose: it declines with reasons drawn from Razorpay's published list,
loses responses without answering, refuses a second order carrying a receipt it
has seen, refuses a second payment against an order already paid, redelivers
webhooks under the same event id, and delivers a payment's two events in the
wrong order.

A mock that always captured would prove only that a success can be parsed. This
one exercises the branches that matter. It is still a mock: it is a reading of a
document, and the document could be wrong, incomplete, or describe an endpoint
that behaves differently on a particular account. Every request shape in this
repository is a hypothesis with a citation.

## Gate status

`py -3.12 sim/gate.py --tier full` runs **27 gates**. On a clean checkout:
**23 pass, 4 fail, 0 vacuous**, in about 90 seconds on an idle machine and
roughly double on a busy one, since the suite saturates eight worker processes.

The suite runs its own world — n=100, five mandates per customer — and is not
affected by the canonical `n`. It measures whether the code still does what it
did, not what the deliverable collects.

The wrapper exits zero because all four failures are listed in
`sim/known_failures.txt` with a written reason. **This is not a green 27/27 test
result**, and no threshold has been loosened to make it one. The full roster with
each gate's state is printed by the suite itself; these four are the ones that
are red.

| Gate | State | Why |
|---|---|---|
| `S1` | FAIL | Calibration of the point-estimate payday filter. ECE 0.091, inside the 0.10 bound, but the reliability curve is not monotone. `S1` does not measure the filter that ships |
| `S1_PD` | FAIL | The identical threshold on the filter that does ship. ECE **0.025**, still not monotone |
| `S2b` | FAIL | The placebo control is not neutral, −14.09 points. A finding about the control's design, left visible |
| `S2_LEGACY` | FAIL | The retired point-estimate pooling gate, kept unchanged and failing so the rewrite that replaced it stays auditable rather than looking like test-loosening |

A **vacuous** gate — one no mutant can trip — is treated exactly like a failure.
The suite has zero.

**Why the calibration gates cannot be fitted green.** Fitting the filter halved
its calibration error and did not order the reliability curve. The filter *does*
model the balance floor at zero — mass piles at bin 0 on every drain — but its
**diffusion** leaks through it: the modelled drain rounds to zero bins for 22 of
a cycle's 30 days, and the 3-tap convolution discards its end taps, so about 12%
of the mass in the lowest bin falls off the bottom each day and renormalisation
pushes it back up. The filter can therefore believe money appeared where none
did. It also approximates the world's hourly uniform spend jitter with a fixed
3-tap kernel. Neither is a parameter that can be fitted, and raising the ECE
bound would not help, since both gates fail on the monotonicity half.
`monotone_drain=True` repairs the first and is off for the measured reason in
[Negative results](#negative-results). **Do not claim a well-calibrated belief.**

**Coverage of the shipping configuration.** Five gates run it directly —
`S1_PD`, `T6_PD`, `S2a_PD`, `S4` and `T9` — and `T1`, `T7` and `T8` include
those policies. Stage 0 mutants deliberately run the unfitted filter: they test
constraint counters, not the prior, and changing their configuration can make a
gate vacuous.

`T9` is what makes the fast/full tier split safe. It compares every policy's
output against a committed reference at both operating points: five headline
metrics as ratios of integer counts, which catch a changed decision, plus a hash
of the raw float64 bytes of every predicted `P(success)` at every dispatch,
which catches a changed float anywhere in the belief filter. It is paired with a
mutant that seeds the worker pool from one shared generator instead of per-run
seeds; if that mutant stops tripping, `T9` reports vacuous.

### The import-graph gates

`py -3.12 agent/tests/test_layer_isolation.py` enforces seven rules about which
layer may import which, each with a named mutant that must trip it. It runs in
the pre-commit hook.

| | |
|---|---|
| `I1` | `agent/llm` may not reach the belief filter, the world, the gate or the timing layer |
| `I2` | in the shipping tree only `constraints/stage0.py` and the composition root may hold an executor |
| `I2T` | a test that holds an executor must declare it with an `# I2-EXEMPT:` line naming why |
| `I3` | the auditor may not share code with the enforcer |
| `I4` | the timing layer may not import the narrative layer |
| `I5` | `ports.py` is the shared vocabulary and depends on no layer |
| `I6` | the execution layer is a leaf and may not reach back up into the layers deciding on its behalf |

`I2` was previously specified over every file under `agent/`, including
`agent/execution/` itself, and carried a central list of exempt test files. It
reported eleven violations, of which four were modules inside the execution layer
importing their own siblings and seven were tests added after the list was last
edited. The rule is now split: `I2` covers the shipping tree, `I2T` requires a
test to declare its own exemption in the file that needs it, and `I6` covers the
boundary that taking `agent/execution/` out of `I2`'s scope would otherwise have
left unchecked. Nine test files carry a declaration.

### Test methodology

Three rules govern the simulation suite, each written after a defect.

1. **No mutant, no gate.** A gate no deliberate defect can trip is a failure of
   the suite, not a pass.
2. **A mutant may create illegal state and nothing else.** A mutation branch
   that increments the counter its gate reads is grading itself. Gate `M4B`
   parses the harness and fails if any violation counter is incremented inside a
   mutation branch.
3. **State what the metric reads when the thing being measured is absent, and
   check that the assertion fails on that value.** A pre-registered prediction a
   null result satisfies is satisfied by a disconnected wire.

Statistical gates are never run at reduced sample size to fit a time budget: at
low power they go green for the wrong reason. Every measurement script runs one
process per run, because long-lived processes making many agent calls crash on
the development machine; `agent/tests/_parallel.py` raises if a worker dies, so
**a crashed run is a failed measurement rather than a missing one**. That fault
is contained, not fixed: it fired three times during the re-baselining at n=500,
and in one case the retry failed too and the measurement was re-run from scratch.

---

## What ships

| | |
|---|---|
| Policy | `solo_shared_pd` — pooled cross-merchant observations, posterior over payday |
| Probability engine | `w3.BeliefPD` under `w3.FITTED_BELIEF` |
| Scoring | `w3.index_score(p_now, p_later, amount, discount=0.92)`, plus a continuation-value test on the last attempt with `cycle_value=0.6` |
| Constraint layer | Stage 0, enforcing five mandate rules and refusing before the executor |
| Metric | billing cycles collected ÷ cycles due, over the full horizon |

```python
FITTED_BELIEF = dict(stride=1, prior_w=5, prior_day0=8.0,
                     prior_floor=0.1, spend_beta=0.0)
```

Omitting it silently gives the unfitted filter, which is about 12 points worse.
Gate `S4` holds that difference at **+11.96 points (±1.86)** and is paired with a
mutant that ignores the configuration, under which the gain collapses to +0.00.

**Inputs**, per mandate per decision hour: the mandate's amount, due day and
cycle position; the customer's decline history across all of their mandates;
attempts already used this cycle; the cross-customer rail state. The diagnosis
layer additionally receives an optional free-text `merchant_note`.

**Outputs**: either a `ScheduleProposal` naming one mandate and one target hour
or a wait with a named reason; a `Diagnosis` carrying a root cause, an
intervention and a rationale, with **no temporal field**; and an audit row for
each. The belief filter is never given a balance, a salary, or the future. The
diagnosis layer is never given a balance or a salary.

`sim/verify_doc_contract.py` asserts that `ports.Diagnosis` carries no field
whose name could hold a time, and exits non-zero if one appears. It carries a
canary — a synthetic field list containing `retry_after_hours`, which the same
matcher must flag — so the check cannot pass by having stopped working.

### Fitting procedure

The five belief constants were selected on **train populations 700–709 only**,
by mean recovery of at-risk cycles across `payday_err {1, 3, 7, 14}`, and scored
once on held-out populations 710–719. `cycle_value` was selected by the same
rule and grid, and the `[1,7]` baseline by the identical rule on the identical
populations, so both sides of the main comparison are chosen the same way. That
selection was performed at n=100; the canonical `n` moved afterwards and the
constants were **not** re-selected against it, because re-selecting on a sample
the result is then reported on is the fit this project has a documented error
for.

The selected region is a plateau rather than a peak: `prior_w` in {3,4,5} by
`prior_floor` in {0.05, 0.1} all land within about a point of each other on a
2 SE of about 3, and `cycle_value` at 0.3, 0.6 and 0.9 scores within 0.4 points.
Two cells scored marginally higher and were **not eligible**: `cycle_value` at
1.2 and 1.8 lie outside the definition of the quantity, since a mandate's next
cycle cannot be worth more than one collection of it.

`sim/fit_belief.py` is a committed, reproducible search, but it **could not have
found the shipping values**: it scores on the pre-canonical world and its
`prior_w` grid cannot reach the canonical optimum. `sim/fitted_belief.json`
therefore records `matches_shipping=false` with that reason, rather than
claiming a provenance the script does not have.

### Evaluation procedure

Every headline is measured on populations never used to select anything, with
population and run seeds stated on every table. Paired 2 SE is reported on every
comparison, and a difference inside 2 SE is reported as a tie rather than a win.
Pre-registered predictions are written down before the measurement runs and
scored afterwards; a broken prediction is recorded as a break rather than a hit
with a footnote.

The canonical run writes `sim/canonical_result.json`, and `sim/verify_claims.py`
checks every published headline against that file. A figure cannot be edited in
one document and left stale in another, and deleting the sentence that carries it
fails the same check.

### Intended and non-intended use

**Intended.** Scheduling retries for failed recurring debits where the failure
mode is a temporarily empty account and the payer's income arrives in a
repeating pattern. The design assumes an operator that sees more than one of a
customer's mandates, or a consent-gated arrangement allowing those observations
to be shared.

**Not intended.** Credit decisions of any kind — the filter estimates a bank
balance from censored payment outcomes and is not a creditworthiness model.
Deciding *whether* to charge someone: it decides only *when*, inside an
authorisation that already exists. Rails where more than four attempts per cycle
are legal, or where a failed instrument can be replaced on demand; card dunning
has a different shape and this design does not transfer to it. Production
forecasting: no figure here has been validated against a real transaction.

### Known limitations

- **No real data, ever.** The world, the policies and the tests share an author.
- **One external calibration anchor.** The world is tuned so the documented UPI
  retry schedule reproduces roughly 30% per-attempt approval. That is the only
  place reality touches the model, it is `[REPORTED]`, and different anchors
  move the fitted spend parameter by about 2×.
- **`payday_err` is a controlled stress parameter.** The headline runs at a
  ±7-day payday-error regime and the result is conditional on it. How accurately
  payday can be estimated in India is not published, so the parameter is swept
  from 1 to 14 and the whole curve is reported in [Baselines](#baselines)
  instead of a single point being defended.
- **The 0.92 discount is hand-chosen**, and every number owes it about 3.9
  points across a 0.80–1.00 sweep on the shipping filter.
- **The belief is not well calibrated.** Two gates fail on monotonicity for
  structural reasons, described in [Gate status](#gate-status).
- **Every published interval is one run seed.** The measured across-seed spread
  on the uplift is 1.89 points, and no table below folds it in.
- **Two decline states are mapped but not simulated** —
  `funds_blocked_by_mandate` and `deemed_transaction`. The mapping is
  implemented and routed; the world draws no rate for either, because none is
  published.
- **Decline frequencies are unpublished**, so every rate is swept rather than
  chosen. The largest single sensitivity is the limit-decline rate, and the
  curve is non-linear, so the midpoint of the range is not the midpoint of the
  cost.

### Distribution shift

The one baked-in population fact is `prior_day0=8.0`, an 8× prior weight on the
hypothesis that salary lands on day 0 of the cycle, fitted on populations drawn
with 60% of customers paid on day 0. Moving the *world's* day-0 fraction while
holding the prior fixed:

*n=100, 8 evaluation populations, `payday_err=7`, 160 runs, zero Stage 0
violations. Agent measurement, and it predates the canonical `n`.*
`py -3.12 sim/stress_day0.py`, transcript `logs/w27_stress_day0_repaired.txt`.

| world's day-0 fraction | `payday_wait` | unfitted filter | fitted filter | ML baseline |
|---|---|---|---|---|
| 0.2 | 55.60% | 81.38% | **91.75%** | 76.59% |
| 0.4 | 58.70% | 82.50% | **93.03%** | 82.29% |
| 0.6 (fitted here) | 59.14% | 82.16% | **94.79%** | 86.18% |
| 0.8 | 58.58% | 82.93% | **95.36%** | 91.38% |

**3.04 points of degradation across a 4× change in the parameter**, never
falling below the unfitted filter. The margin over the ML baseline *grows* as
the population moves away from the fit, from +3.98 at 0.8 to +15.16 at 0.2: a
wrong prior is recoverable by evidence, a wrong learned split is not. **The
limit of that result:** the sweep moves the *fraction* of customers paid on day
0, not *which day* the spike sits on. A population spiking on day 14 is a
harsher test and has not been run.

The fitted prior also generalises across the parameter it was fitted at rather
than peaking there: its gain over the unfitted filter runs +3.15 at ±1 day to
+10.38 at ±14, with the maximum in the middle
(`logs/w27_fair_audit_repaired.txt`).

The six-world comparison against a gradient-boosted probability model has not
been re-run on the current belief or the current `n`, and its digits are not
quotable. The qualitative finding is that the fitted Bayes filter beats the ML
baseline in every world tested and that a Bayes+ML hybrid is worse than the
filter alone. Re-running it properly would require retraining the hybrid, which
is a fit and not an audit.

### Reproducibility

The belief configuration, the world configuration and the seeds are committed.
`T9` locks every policy's output byte for byte, so a change to the belief
arithmetic cannot pass the fast test tier quietly. Every table above names a
command and a transcript under `logs/`.

---

## Sources

Every external claim carries a confidence tag: `[VERIFIED]` read directly from a
primary source, `[REPORTED]` a cited secondary source, `[GUESS]` an inference
with no source. **A `[REPORTED]` claim is not upgraded by being repeated.**
Several rules this system enforces rest on practitioner summaries rather than on
regulation read end to end.

**Mandate rules.** `[REPORTED]` NPCI restricted UPI AutoPay execution to
non-peak hours from 1 August 2025, windows 10:00–13:00 and 17:00–21:30 — one
secondary source frames this as payments being *shifted* out of peak rather than
rejected, which is softer than what Stage 0 enforces. Stage 0 takes the stricter
reading deliberately: it refuses where the softer reading would allow, which
costs collection and cannot create an illegal action.
`[REPORTED]` One presentation plus three retries per cycle. `[REPORTED]` A
pre-debit notification roughly 24 hours before execution, one pending per
mandate, from practitioner reports rather than regulation. `[REPORTED]`
Technical declines may be re-presented under the existing notification; business
declines may not. `[REPORTED]` Technical declines are under 1% of failures.
`[REPORTED]` The balance-enquiry API is capped at roughly 50 calls per customer
per day, which is why this system infers a balance rather than querying one.

**Failure and recovery rates.** `[REPORTED]` UPI AutoPay debit failure 8–15%
against 2–3% for card e-mandates, from trade-blog summaries; neither is an
operator disclosure. `[VERIFIED]` Razorpay's own comparison page discusses
mandate completion, drop-off, retry cost and revenue leakage and **publishes no
AutoPay failure rate at all** — the operator this system is built for does not
publish the number it most needs. `[REPORTED]` The recovery bands used as
validation targets: no retries ~0–10%; fixed-interval retries ~20–40%; industry
median ~47.6%; smart retries with card updater and email 70–85%; share of
recoveries inside ten days ~90%. All from companies selling recovery software,
aggregating non-comparable customer bases. `[REPORTED]` Industry benchmark for
retry-optimisation uplift 6–8%; **it is unclear whether that is percentage
points or relative uplift.**

**Population assumptions.** The mandate count per customer separates into a
derived mean and a chosen distribution, and the two carry different evidence.

`[REPORTED]` **The mean.** About 95 million UPI AutoPay mandates are active at
any one time (March 2026, attributed to NPCI ecosystem statistics), against a
UPI base that passed 500 million unique users by early 2026, with about 50
million new mandates registered per month in July 2025. Dividing active mandates
by the number of people holding them: a mean of 5 requires the entire AutoPay
base to be 19 million people, 3.8% of UPI users, while 50 million new mandates
are created every month. A mean of 2 requires 47.5 million, 9.5%, which is
consistent with that registration rate. **Declared plausible range for the mean:
1 to 3, centre 2**, declared before the canonical world was scored. The one
figure that would support 5 — a subscription-tracker vendor's "the average
Indian smartphone user manages 8–12 active subscriptions" — is discounted: it is
unsourced vendor marketing and it counts card recurring and wallet debits
alongside UPI AutoPay. The sources also disagree with each other on execution
volume, so this is an order-of-magnitude bound rather than a measurement.

`[GUESS]` **The distribution.** `1 + Poisson(1)` capped at 8 is a modelling
approximation. No source gives the distribution of active mandates per Indian
consumer, and the search for one returned aggregate mandate and user counts
only. Poisson is the one-parameter count distribution over the non-negative
integers, so the derived mean sets it directly (mean = 1 + λ); the floor of one
is definitional, since a customer holding no mandate is not in this population;
the cap at 8 bounds a tail nothing constrains. A spread is used rather than a
fixed count because the mechanism that makes mandate count matter is convex in
`k`: the share of at-risk cycles caused by one mandate draining the account
before another presents runs 0.0 / 10.2 / 25.4 / 39.2 / 48.9 percent at
`k` = 1..5 (`sim/w3.py`), so a population averaging two mandates with a spread is
not the same world as one where everyone holds exactly two. It replaced a fixed
5 that had no source of any kind. **The available sources inform the shape of the
population, not the exact synthetic distribution**, and what turns on the
distribution — the value of pooling — is measured at both mean 2 and fixed 5 in
[Pooling](#pooling) rather than assumed.

`[REPORTED]` India's Payment of Wages Act
requires wages before the 7th day after the wage period for establishments under
1,000 workers and the 10th for larger ones, which is the basis for the statutory
payday window. `[REPORTED]` Three published FY25 RBI household-savings readings
give the `pop_spend` region. `[REPORTED]` "75% of Indians have no emergency
fund", which fixes the carry-over buffer's median at sigma 1.0. `[REPORTED]` The
published UPI AutoPay ticket range of 149–2,499 rupees with a 15,000 regulatory
cap, which the amount clip retains.

**Razorpay's API surface.** `[VERIFIED]` 110 published
`payments_error_reasons`, committed verbatim. `[VERIFIED]` The documented
subscription retry schedule: T, T+1, T+2, T+3, then halt. `[VERIFIED]` The
Payment Downtime API carries `instrument.vpa_handle` naming individual UPI
handles and reports `ALL` only when the whole rail is affected, with
`payment.downtime.*` webhooks available on test keys.

**Retracted.** `[RETRACTED]` "Razorpay's downtime feed is system-wide, so a
bank-shaped incident is invisible to it" — false; they already publish
bank-scoped downtime. `[RETRACTED]` "Spacing retries over days is the main win"
— spreading the same attempts over four days buys nothing measurable; the win is
waiting for payday specifically. `[RETRACTED]` "Coordinated scheduling helps" —
it measures negative at two operating points.

**Outside the scope of this evaluation.** Whether cross-merchant pooling is
lawful in India: a question of law, jurisdiction- and provider-dependent, and
handled here by making pooling a per-customer permission.

**Not published, and therefore swept rather than chosen.** Whether the ~30%
per-attempt approval anchor describes all debits or only retries of
already-failed ones — the world implements the first (`[GUESS]`). How accurately
payday can be estimated in India, which is why `payday_err` is swept from 1 to
14. What fraction of AutoPay debits fail during a UPI incident, which is why
outage severity is swept. The distribution of AutoPay mandates a real customer
holds, whose mean is derived above and whose shape is a modelling
approximation.

---

## Reproducing everything

Windows commands. On macOS or Linux, replace `py -3.12` with a Python 3.12
interpreter carrying NumPy 2.4.2.

```bash
py -3.12 -m agent.batch_report --pops 10 --canonical --emit   # the headline
py -3.12 agent/tests/test_scale_n.py --seeds             # the sample-size study
py -3.12 sim/gate.py --tier full                         # 27 gates
py -3.12 agent/tests/test_layer_isolation.py             # the import-graph gates
py -3.12 agent/tests/test_steelman_schedule.py           # the [1,7] comparison
py -3.12 agent/tests/test_conditional_headline.py        # the pop_spend region
py -3.12 agent/tests/test_page_sweep.py                  # the page's slider
py -3.12 agent/tests/test_canonical_world.py --confirm   # V1/V3/V5/V7
py -3.12 agent/tests/test_v3_power.py                    # V1 and V3 at 100 pops
py -3.12 agent/tests/test_pooling_consent.py --canonical  # the pooling measurement
py -3.12 agent/tests/test_parity_vs_harness.py           # agent vs harness, bit-exact
py -3.12 scripts/discount_sweep.py
py -3.12 sim/stress_day0.py
py -3.12 sim/fair_audit.py
py -3.12 agent/eval/run_eval.py --llm --judge --replay   # 0.5s, $0.00
```

Documentation checks:

```bash
py -3.12 sim/verify_docs.py
py -3.12 sim/verify_docs.py --selftest
py -3.12 sim/verify_claims.py
py -3.12 sim/verify_claims.py --selftest
py -3.12 sim/verify_doc_contract.py
py -3.12 scripts/build_page_data.py --check
```

If `import numpy` fails, check the interpreter rather than the dependency list.
`sim/gate.py` and the git hooks probe for an interpreter that can import NumPy
rather than trusting an executable name. The ML comparison additionally needs
`sim/ml_artifacts/`, which is not committed; rebuild it with `sim/ml_study.py`
in the order given in that file's module docstring.

---

## Superseded measurements

Numbers this project has published and then retired. They are listed so that a
figure found in an older transcript or an outside copy can be identified as
retired rather than checked against nothing. `sim/verify_docs.py` carries a rule
and a self-test for each family below and fails if one goes live in a document
again.

| Retired | Why |
|---|---|
| Every headline measured at n=100, including the batch figures, the validation row and the `[1,7]` margins | The canonical `n` is 500. n=100 is optimistic on the uplift, on recovery and on the failure rate at once, by more than the interval it reports. See [Sample size](#sample-size) |
| The pre-canonical batch headline and its rupee total | Measured on a world with no steady state, no mandate outflow, and the mandate count fixed at an invented 5 |
| An uplift range across `pop_spend` reaching the mid-thirties | `pop_spend=1.05` is outside the externally derived region entirely |
| A crossover against the fixed schedule at ±1 to ±3, and again at ±7 to ±10 | The first compared against a strawman; the second predates the belief repair |
| The pre-repair agent's recovery figures, and the previous shipping belief constants | The prior was re-selected on the canonical world |
| A claim that the two negative `[1,7]` margins sit inside their measurement error | They were compared against each arm's own spread across populations, not against a paired interval. The comparison now carries a paired 2 SE |
| A claim that half of V5's sample being its own selection set matters | Measured at n=500 the split is 0.35 points and inside both intervals |
| An outage ablation reported as a significant negative, then as exact zeros | The conclusion never changed; the levels did |
| A discount sweep spanning about 7 points | Re-run on the shipped belief: 3.9 points, and the argmax moved |
| An attribution of the calibration failure to a missing balance floor | Wrong mechanism: the floor is modelled, the diffusion leaks through it |

Six measurement families were run on earlier experimental worlds — before the
belief repair, before the canonical `n`, or both: the insolvency sweep, the
transient-hold sweep, the population-realism sweep, the six-world ML comparison,
the discount sweep and the day-0 stress test. They are retained as sensitivity
and historical evidence, not as current headline results. Each is quoted above
for direction only, never for its levels, and each says so where it appears.
