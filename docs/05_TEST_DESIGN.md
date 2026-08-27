# TEST DESIGN — written before the harness, on purpose

Every defect this project has found was a case of the measuring apparatus
flattering the thing being measured. A test suite written after the harness
inherits the harness's assumptions and therefore cannot catch that class of
error. So the tests are specified first, and the harness is written to satisfy
a spec it did not author.

## The bar a test has to clear

A test earns its place only if I can state, in advance, **a concrete broken
implementation that would make it fail**. Three of the four gates in the
previous suite could not clear that bar:

| Old gate | Why it was vacuous |
|---|---|
| `assert violations == 0` | `live` already filtered `n < cap`; `chosen ⊆ live`. Tautology. |
| peak hours never violated | `SLOTS_PER_DAY=3` made peak windows unrepresentable. |
| oracle approval ≈ 100% | Oracle only fires when it already knows it will succeed. True by construction whether or not the oracle is correct — and it *was* incorrect. |

So every gate below is paired with a **mutant**: a deliberately broken variant
that the test must reject. A gate that no mutant can trip is deleted.

---

## Tier 1 — Mutation tests (the meta-tests)

These test the tests. Each one breaks the harness on purpose and asserts the
suite notices. **If a mutation test passes silently, the corresponding gate is
vacuous and must be rewritten.**

| ID | Mutation | Must be caught by |
|---|---|---|
| M1 | Policy dispatches a 5th attempt in a cycle | attempt-cap violation counter |
| M2 | Policy dispatches inside a peak hour | peak-window violation counter |
| M3 | Policy dispatches with <24h notification lead | notification-lead counter |
| M4 | Policy issues a 2nd pending notification for one mandate | pending-notification counter |
| M5 | Policy retries a Z9 decline under the old notification | re-presentation-eligibility counter |
| M6 | Belief is fed the true balance | leakage test |
| M7 | Forecast reads the real future array | leakage test |
| M8 | Oracle is crippled so it skips when funds are available | oracle-dominance test |
| M9 | Calibration target silently switched to recovery | calibration gate |

M8 is the one that would have caught the defect found in the last audit. M1–M5
are the ones the old suite claimed to have and did not.

---

## Tier 2 — Correctness invariants

**T1 — Oracle dominance.** The clairvoyant policy must weakly dominate every
other policy on the primary metric, at every contention level.
*Fails if:* the oracle defers when funds are present (the real defect found).
*This is the single most load-bearing test in the suite* — it is the only thing
that validates the upper bound, and the upper bound is what separates skill
from leakage.

**T2 — No future leakage.** Run the belief forecast twice: once normally, once
with the true balance array replaced by NaNs after the current slot. Output
must be bit-identical.
*Fails if:* any policy path touches `bal[t+1:]`.

**T3 — No true-balance leakage.** Same, with the true current balance poisoned.
Belief-based policies must be unaffected; only the oracle may change.

**T4 — Determinism.** Same seed → identical results, across process restarts.

**T5 — Budget monotonicity.** Increasing the attempt cap must not decrease
recovery for any policy.
*Fails if:* an index bug makes extra attempts actively harmful.

**T6 — Information monotonicity.** `solo_shared` with k=1 must be *exactly*
equal to `solo_own` with k=1 — with one mandate there is nothing to pool, so
any difference is a bug, not a finding.
*This is the test that would have caught a fake pooling effect.*

**T7 — Conservation.** recovered + dead + unresolved + lapsed = 1.0 exactly,
by count, for every policy. Balances never negative. Attempts never exceed cap.

---

## Tier 3 — Statistical validity (new; none of these existed)

**S1 — Belief calibration.** Bin every predicted P(success) into deciles and
compare to the empirical success frequency in that bin. Report expected
calibration error (ECE) and a reliability table.
*This has never been tested.* The entire project rests on the belief filter
being well-calibrated, and nobody has checked whether P(success)=0.7 actually
means 70%. A filter can be sharply wrong and still beat a bad baseline.
*Gate:* ECE < 0.10, and the reliability curve must be monotone increasing.

**S2 — Placebo pooling (negative control).** `solo_placebo` pools observations
across mandates as `solo_shared` does — but the pooled observations come from a
**different, randomly chosen customer**. Identical mechanics, identical
observation count, wrong information.
*If the placebo gains as much as real pooling, the +5.4 points is not
information — it is an artefact of the update schedule.*
This is the strongest available test of the project's central claim and it has
never been run.
*Gate:* real pooling must beat placebo pooling by a margin exceeding 2 SE.

**S3 — Seed stability.** Report standard errors across ≥8 independent
populations, not 4–6. Any headline difference smaller than 2 SE is reported as
non-significant, including differences we like.

**S4 — Calibration anchor independence.** Calibrate the world on each candidate
baseline and report how much the fitted spend parameter moves. A world whose
solvency depends on which policy anchors it is a world that cannot support
absolute claims.
*Not a pass/fail gate — a mandatory disclosure.*

---

## Tier 4 — Adversarial sensitivity (report, don't gate)

Each of these is a modelling choice previously pinned to a convenient value.
Every headline number must be reported as a range across these sweeps, not a
point.

| ID | Parameter | Previously | Why it matters |
|---|---|---|---|
| A1 | top-up probability after failure | pinned at 0 | rapid retries fail by construction at 0 |
| A2 | payday estimate error | ±1 day | the mechanism assumes we know payday |
| A3 | `p_later` discount | hardcoded 0.92 | sits directly on the "wait" decision |
| A4 | LTV multiplier | invented 6× | drives every rupee claim |
| A5 | horizon | 30d | previously dominated the result |
| A6 | payday dispersion | 60% day-0 | bimodal and arbitrary |

---

## What "not biased for success" means here

1. Every gate has a named mutant that trips it. No mutant, no gate.
2. Gates are two-sided. `oracle_dominance` fails if the oracle is too weak;
   `calibration` fails if approval is too high *or* too low.
3. The negative control (S2) is designed to **destroy** the project's central
   claim if the claim is false.
4. Significance is reported against the null of *no difference*, and
   non-significant results we like are reported as non-significant.
5. Thresholds are declared here, before any result is seen.
6. The competitive baseline (`payday_wait`) is a permanent row, not an
   afterthought, because it is what a good competing team would build.
