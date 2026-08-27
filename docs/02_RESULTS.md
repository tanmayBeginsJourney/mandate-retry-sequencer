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

## Negative control (this is the strongest evidence we have)

`solo_placebo` pools with identical mechanics, timing and observation count, but
outcomes computed against a **different customer's** balance.

| | ±3d | ±7d |
|---|---|---|
| real pooling − placebo pooling | **+21.68** SIG | **+23.99** SIG |

The benefit is information, not an artefact of the update schedule.

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

17 gates. Mutation tests all fire (696–1,141 violations vs a clean zero).
The oracle deferral bug, deliberately restored, is caught (100.0% → 50.5%).

⚠️ **S1 (belief calibration) FAILS.** ECE 0.098 against a 0.10 threshold,
reliability curve not monotone, filter overconfident in its top decile (predicts
0.998, achieves 0.916). The threshold was declared before results were seen.
**Do not loosen it.** Nothing above is fully settled until it passes.
