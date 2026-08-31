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
*If the placebo gains as much as real pooling, the pooling gain is not
information - it is an artefact of the update schedule.*
(This sentence originally quoted `+5.4 points`. That figure is
`[RETRACTED]` - see `01_FACTS.md`. CLAUDE.md rule 6 forbids quoting
retired numbers, so it has been removed. The gate itself is unchanged.)
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

---

# IMPLEMENTATION STATUS — appended 27 August 2026

**Everything above this line is the original pre-registration and has not been
edited**, apart from removing one retracted number that rule 6 forbids quoting.
It records what was specified before the harness existed. This section records
what was actually built, which is not the same thing. Where they differ, the
difference is the finding.

## Added after the fact, 28 August 2026

| ID | What it does | Mutant |
|---|---|---|
| **T8** | Every `COMPLIANT` policy must record zero Stage 0 violations, and `baseline_doc` must record some. **Not in the pre-registration above and not previously listed here — added to this table 28 Aug 2026.** An undeclared gate is a small version of the same problem as a vacuous one: nobody agreed in advance what it was for. | `baseline_doc` is the mutant: it is a real policy that must come back dirty (974 re-presentations) |
| **T9** | Every policy's output must equal `sim/t9_reference.json` exactly, at both operating points. Metrics catch a changed decision; `calib_sha256` catches a changed float. Locks unfitted policies and `solo_pop_pd` / `solo_shared_pd` / `portfolio_pd` under `FITTED_BELIEF` (34 configs). | worker pool seeded from one shared RNG instead of per-run seeds |
| **S4** | The fitted belief configuration beats the shipped one by >2 SE. The decision number for which probability engine ships. ⚠️ **This ID was pre-registered for a different test** — see "Specified but NOT implemented". | `ignore_bcfg` — drops the fitted config, gain collapses to +0.00 |
| **S1_PD** | S1's threshold, unchanged, applied to `w3.BeliefPD` — the filter that actually ships. S1 runs `portfolio`, which carries `w3.Belief`, so it has never measured the product. **Note S1 runs at `pe=1` and S1_PD at `pe=7`**: same threshold, different operating point, so 0.091 vs 0.026 is not a like-for-like comparison. | shares S1's binning; fails on monotonicity |
| **M4B** | No mutation branch may increment the violation counter its gate reads. Static parse of `sim/harness.py`. **Added 28 Aug 2026 after M4 was found to be vacuous-but-green.** | its own falsifiability check: reports VACUOUS if it flags all five mutants, i.e. if it cannot discriminate |

**Four of the six Tier-4 adversarial sweeps are now discharged**, 28 Aug 2026.
The list below named six modelling choices pinned to convenient values. Status:

| ID | parameter | status |
|---|---|---|
| A1 | top-up probability | **still pinned at 0** except one `topup_p=0.25` row. ✅ **Redone on `w3` 29 Aug 2026:** the unconditional sweep on the shipping configuration is worth **+0.02 pts (2 SE 0.59)**, against **+11.4 pts** for `payday_wait`. The old-harness worry — that ~half the gain was "customers never top up" — does not carry over. |
| A2 | payday estimate error | **DONE** — swept ±1 to ±14, `sim/headline.py`. The crossover against `payday_wait` is between ±3 and ±5 days. |
| A3 | `p_later` discount | **DONE** — swept 0.80–1.00. Not a no-op: it changes the index's sign. Broad plateau, argmax moves between population sets. Reported as a range, 78.7%–83.1%, never as a point. |
| A4 | LTV multiplier | **DONE, and removed.** Swept over {0,1,6,20}: a **no-op for every policy**, because `value` is strictly positive so it cannot flip the index's sign, and non-budgeted policies commit every positive-score mandate regardless of rank. It was live and inert. |
| A5 | horizon | **still pinned at 120 days.** |
| A6 | payday dispersion | **DONE** — `payday_day0_frac` swept 0.2–0.8 with the prior held fixed, `sim/stress_day0.py`. 6.95 pts of degradation, no cliff. |

A1 and A5 remain open and are the two places an unswept assumption could still
be flattering the result.

## Specified but NOT implemented

| ID | Status |
|---|---|
| **M7** forecast reads the real future array | **Never implemented.** `tests.py` comments claim "M6/M7" over a block that records only M6. T2 covers the same property structurally, but the mutant does not exist. |
| **M9** calibration target silently switched | **Never implemented.** |
| **T7 conservation identity** | **Never implemented.** `recovered + dead + unresolved + lapsed == 1.0 by count` is not computed anywhere. `harness.run` does not return the counts it needs. T7 checks bounds and the per-event cap only — do not read it as covering conservation. |
| **S4 calibration-anchor independence** | **Never implemented — and its ID was reused.** Added to this table 28 Aug 2026. See below. |

### ⚠️ S4's ID was reused for a different test. Declared 28 August 2026.

The **pre-registered** S4, in Tier 3 above, is *"Calibration anchor
independence — calibrate the world on each candidate baseline and report how
much the fitted spend parameter moves. **Not a pass/fail gate — a mandatory
disclosure.**"* That test **does not exist.**

The S4 that ships is *"the fitted belief configuration beats the shipped one by
>2 SE"* — a different subject, a different question, and a **hard pass/fail
gate**. It was added in the table above as though it were new, and this
"Specified but NOT implemented" list did not mention that a pre-registered ID
had been consumed.

This is **error 9's exact mechanism** — a gate identified by a concept rather
than by the object it instantiates, so nobody re-checked which object that was.
It is recorded here rather than repaired: renaming the live gate now would
churn `known_failures.txt`, `gate.py` output and four documents eight days
before the deadline, for no change in what is measured.

**If you implement the anchor-independence disclosure, call it `S5`.** Do not
reuse `S4`, and do not rename the live one.

*(The anchor-sensitivity fact itself is not lost — `06_MODEL_CARD.md` §3.2
carries it as a mandatory disclosure: different anchors move the fitted spend
parameter by ~2×. It is prose, not a gate, which is what the pre-registration
asked for. What is missing is the systematic per-baseline sweep.)*

## Implemented differently from the spec

- **T3.** Specified as "no true-balance leakage, with the true current balance
  poisoned". As originally written it ran `solo_pop` twice and compared — a
  determinism check wearing a leakage test's name, duplicating T4 and testing
  nothing about leakage. Rewritten 27 Aug to the property the spec is actually
  after: a belief's predictions must not move when `w3.balance_trace` is
  poisoned. Paired with a `_LeakyPD` mutant it must catch.
- **T7.** The cap clause compared `att_per_cycle`, a **mean**, against the cap.
  A mean cannot exceed 4 unless the breach is population-wide, so one mandate
  taking a 5th attempt was invisible. Now reads the per-event counter
  `vdetail["cap"]`.
- **T1.** Now runs at both contention levels, as "at every contention level"
  requires, and is paired with the `weak_oracle` mutant — if a crippled oracle
  is still not beaten, T1 reports VACUOUS instead of passing.
- **T6.** Spec names a policy `solo_own`. **No such policy exists.** The
  harness has `solo_naive` / `solo_pop` / `solo_shared` / `solo_placebo` and
  their `_pd` variants. The gate compares `solo_pop` with `solo_shared`.
  **T6_PD** is the same identity on `solo_pop_pd` / `solo_shared_pd` under
  `FITTED_BELIEF`.
- **S2.** Rebuilt as three arms (`S2a` moat, `S2b` confound check, `S2c` the
  old headline) on the **payday-posterior** policies at ±7d. **S2a_PD** is
  the moat on `FITTED_BELIEF`. The original point-estimate gate is retained,
  still failing, as `S2_LEGACY`. See `02_RESULTS.md` — the old
  real-minus-placebo headline was ~60% placebo damage, because `solo_placebo`
  injects wrong observations rather than neutral extra ones. A label-shuffle
  and a posterior-predictive control were measured on 31 August and also
  damaged the filter; extra unmatched `observe()` is not a martingale here.
- **S3.** Implemented as a test of the significance machinery (a positive
  control that must read significant, a null control that must not), reporting
  the headline with its SE but deliberately not gating on it.

## Operating point — added to the spec after the fact

Gates that only bind under contention (**M1, S2, T5, T7**) run at
`payday_err=7`. At the harness default of ±1 day the world is uncontended:
policies hit payday nearly every time, recovery is ~97%, and a constraint that
is never reached cannot be tested. Everything else stays at the default.

~~**This did not fix M1**, which is VACUOUS at both operating points: the
deepest any mandate-cycle reaches is 3 attempts at ±1d and 4 at ±7d, against
`NPCI_MAX = 4`, so the 5th attempt that trips the counter never occurs. The
attempt-cap guarantee therefore has no working test behind it, and T7's cap
clause reads the same counter, so both cap gates are exactly as strong as M1.~~

✅ **RESOLVED 30 August 2026, and the diagnosis above is exactly what pointed at
the fix.** The problem was never the operating point — it was that the mutant
asked for an attempt the world could not reach. **M1 now runs its mutant at
`cap_override=2`,** where a 3rd attempt is illegal and the mutant reaches it, so
the counter binds and the gate trips. That proves the cap counter *works*,
which is strictly more than it proved before and strictly less than the claim
"the value 4 is the right cap" — `sim/verify_brief.py` asserts `NPCI_MAX`
separately. **The attempt-cap guarantee now has a working test.**

⚠️ **T7's own mutant is still weaker than this document's bar, and that has NOT
changed.** Declared 28 Aug 2026. The bar at the top of this page is "a concrete
broken *implementation*". T7's mutant is a **hand-edited dictionary** — a real
result copied, then `cycle_rec` set to 1.5 and `vdetail["cap"]` set to 1. That
tests the checker function, not the harness. What *has* changed is that this no
longer combines with M1 to leave the cap counter untested by any gate that runs
the simulator: **M1 is now that gate.** T7 remains a weak test of a strong
claim, on its own.

## ⚠️ M4 IS VACUOUS TOO, AND IT REPORTS PASS. Declared 28 August 2026.

The mutation tier's whole premise is that a mutant creates an illegal *state*
and a separate piece of code notices. Two of the five mutants USED TO increment
the counter themselves (**both repaired 30 August 2026; M4B is green**):

```
harness.py:610-612   if mutate == "pending":    ... V.pending   += 1
harness.py:331-333   if mutate == "represent":  ... V.represent += 1
```

Instrumented measurement (portfolio, pop `P`, seed 7):

| mutant | counted | written by the mutant | found independently |
|---|---|---|---|
| `pending` | 1066 | **1066** | **0** |
| `represent` | 608 | 304 | 304 |

`pending`'s only independent detector is `if m["pend"] is not None`
(`harness.py:607`), and `live` (`harness.py:349-351`) has already filtered
`m["pend"] is None`, so it is unreachable — the same tautology as the old
`assert violations == 0`. **M4 has been passing by construction.** M5 still
binds; it merely double-counts.

**New rule, and it belongs at the top of this page with the others:**

> **A mutant may create illegal state and nothing else.** A mutation branch
> that writes to a counter is grading itself. Gate **M4B** enforces this by
> parsing the source, because the harness returns only the integer and from
> outside a self-written violation is indistinguishable from a real one.

M4B is red and in `sim/known_failures.txt`. The repair is in `harness.py`
(frozen) and would move T9's reference, so it is a post-5-September job with a
written procedure in that file.

---

## The agent's gates — added 28 August 2026, extended 29 August

`agent/tests/` holds **twelve gate scripts**. They are **not** part of
`sim/gate.py`'s 25-gate suite and none of their numbers is gate-protected in
the `--tier full` sense; quote them the way the numbers rule requires, by
naming the script. The table of what each one proves is in
`06_MODEL_CARD.md` §6b.

They are held to this document's bar: **a gate earns its place only if you can
name, in advance, a concrete broken implementation that would make it fail.**
`test_layer_isolation.py` goes further and actually RUNS its five mutants
against synthetic source on every invocation, reporting VACUOUS — treated as
failure — if any rule stops being trippable.

**Errors 14 and 16 were both failures of that bar, in this directory, on the day
it was written.** One gate's mutant passed because the two arms diverged for a
defect's reason rather than the property's; two pre-registered checks passed on
an all-zero metric. Both are written up in `03_ERRORS.md`. The rule that came out
of them, and which any new agent gate must satisfy:

> **State what the metric reads when the thing being measured is ABSENT, and
> check that the assertion FAILS on that value.** A prediction satisfied by zero
> is satisfied by a disconnected wire.

Two agent gates now carry explicit vacuity guards that report VACUOUS rather
than HELD when the detector never fires.

### A third rule, added 29 August 2026 after error 24

`test_razorpay_mapping.py` checks that our map of Razorpay's 110 published
`error_reason` values is complete. It passed while the map contained
`deemed_transaction_unknown`, an identifier that appears **nowhere** in
Razorpay's list and was typed while writing the table.

The gate could not have found it. "Every reason of theirs is covered by ours"
and "every entry of ours came from theirs" are different claims about the same
two sets, and only the first had a check. Adding the second — one line,
`set(ours) - set(theirs)` — found it on the first run.

> **A check that one set covers another is not a check that the two sets are
> the same. If a table cites a source, test both directions.**

This is the same shape as errors 9 and 13 — a gate named for a property rather
than for the object it constrains — arriving in a new place for the third time.
Legitimate exceptions go in a declared list **with a written reason per entry**,
the way `sim/known_failures.txt` works, and a further gate fails if a declared
exception has no reason. There is exactly one today, and it exists because
Razorpay's own spreadsheet contains a typo.

### And a fourth, from error 25

`prove_stage0_refuses.py` was written to show Stage 0 refusing an illegal debit.
It does. Its first draft also printed **"The two agree"** under a gate count of
`{peak: 2, pending: 1}` and an auditor count of `{all zero}` — a caption written
before its own output was read, under two numbers that are **not the same
quantity**.

> **Before presenting two numbers as a cross-check, state the regime in which
> they could differ, and check that the display is ever in it.**

A cross-check that can only disagree when the enforcer fails proves nothing in a
run where the enforcer works. The repair is not better wording: it is a test
that **makes the enforcer fail** and shows the other side binding. That is now
step 4 of the script.
