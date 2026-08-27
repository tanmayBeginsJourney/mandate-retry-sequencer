# 02 — RESULTS (the only valid numbers)

Produced by `sim/harness.py` + `sim/w3.py`. Anything not on this page is stale.

**Setup.** World calibrated so Razorpay's documented UPI schedule reproduces
~30% per-attempt approval (spend=1.05). 120-day horizon, 30-day billing cycles,
5 mandates/customer, 4 seeds, n=30 customers. Primary metric: **billing cycles
collected ÷ cycles due**, where a dead mandate forfeits all remaining cycles.

**Known bias risks in this design, stated up front:**
- Small n (30) and few seeds (4). Error bars are wide. Re-run bigger before the pitch.
- The world model, the policies and the tests were all built by one party in one pass.
- `payday_err` is swept, but the *shape* of the payday distribution is assumed.
- Top-up probability is pinned at 0 in these runs. It matters — see below.

---

## The headline is conditional

| Payday known to | `payday_wait` (5-line heuristic) | best full system | verdict |
|---|---|---|---|
| ±1 day | 98.9% | 99.8% | tie — no reason to build |
| ±3 days | **96.0%** | 87.5% | **heuristic wins by 8.5** |
| ±7 days | 59.4% | **83.4%** | **system wins by 17.8** (±7.5, SIG) |

The crossover sits between ±3 and ±7 days. **This is the number that decides
whether the project is worth building**, and it is an empirical fact about
Indian salary timing we have not measured.

Decision taken: do not chase it externally. Make the agent *learn* payday online
and report its own uncertainty. The posterior width becomes a product feature.

## Full table

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

**21 gates. Four are red: S1 FAIL, S2b FAIL, S2_LEGACY FAIL, M1 VACUOUS.**
Enforced at commit time by `sim/gate.py`; reasons in `sim/known_failures.txt`.
Full suite runtime is **~27 minutes**.

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
