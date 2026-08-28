# NOTES — append only

Decision log and failure log. **A judged deliverable** — the panel asks what
broke and how you recovered. Do not tidy this up. Append, never rewrite.

Format: `## YYYY-MM-DD — short title`, then what happened, what you did, what
you'd do differently.

---

## 2026-08-27 — handoff from research phase

Research complete, no production code. Moving to local build in Claude Code.

Carried over: six self-found errors (see `docs/03_ERRORS.md`), all of which
flattered the project. Current results in `docs/02_RESULTS.md` — headline is
deliberately conditional, not a single number.

Known open failure: **S1 belief calibration fails** (ECE 0.098 vs 0.10
threshold, non-monotone reliability curve, overconfident in top decile). Left
failing on purpose. The threshold was declared before results were seen.

---

## 2026-08-27 — test gate installed, and it immediately found something

Set up the commit-time gate before doing any build work. `sim/gate.py` runs
`sim/tests.py`, parses the `[  ok  ] / [ FAIL ] / [VACUOUS]` lines, and blocks
the commit on any bad gate that is not listed in `sim/known_failures.txt`.
Installed as a git pre-commit hook via `scripts/install-hooks.sh`.

### The handoff's picture of the baseline was wrong

`CLAUDE.md` says "Currently S1 (belief calibration) FAILS and is known." The
first clean run of the suite, on the untouched handoff tree, gives:

```
SUITE: 17 gates, 2 FAIL, 1 VACUOUS, 14 pass
[VACUOUS] M1  5th attempt in a cycle       mutant did not trip the counter
[ FAIL ]  S1  belief calibration           ECE=0.091, monotone=False
[ FAIL ]  S2  real pooling beats placebo   real-placebo = -0.40 pts (+/-0.22)
```

So three problems, not one. **M1 and S2 are undeclared.** Both are now in
`known_failures.txt` with reasons, but neither is understood, and neither
should be treated as blessed. Two things worth flagging now:

- **S2 is the negative control failing in the direction that kills the claim.**
  Real pooling (-0.96 pts vs own) is not beating placebo pooling (-0.57 pts);
  both are slightly negative. This gate was written specifically to destroy the
  pooling claim if the claim is false. It is doing that. Untriaged.
- S1's numbers do not match this file's earlier entry either: recorded here as
  ECE 0.098, measured 0.091. ECE is now *inside* the 0.10 bound and the gate is
  failing purely on the monotonicity half. Possibly an environment difference
  (see below), possibly not.

Environment note: no numpy on the default interpreter. The `python` and
`python3` on PATH are msys2 builds with no numpy and no pip. The suite runs on
`~/AppData/Local/Programs/Python/Python312/python.exe` (numpy 2.4.2). The hook
probes for an interpreter that can import numpy rather than assuming a name.
Nobody has checked what version the research-phase numbers were produced on, so
the ECE discrepancy above is not yet attributable.

### The mutation-test principle earned its keep on day 0

Deliberately broke the attempt-cap violation check in `sim/harness.py`
(`if ledger[lk] >= cap:` -> `>= cap + 100`), staged it, and tried to commit,
expecting the gate to block naming M1.

**It did not. The gate printed `GATE PASS ... Commit allowed`.** Zero gates
changed status. The commit was stopped only by the second, unrelated tripwire.

The mechanism: that line only *counts* violations, it does not prevent the
attempt — enforcement is the `m["n"] < cap` filters elsewhere. M1 is the only
gate that proves the counter works, and M1 is already VACUOUS. So the counter
can be disabled entirely and nothing goes red. **While M1 is vacuous, the
NPCI attempt-cap claim has no working test behind it.** Do not quote that
claim in the pitch or the architecture doc until M1 is fixed.

Control, to confirm the gate itself is not broken: broke the peak-hour check
instead (M2's mutant works). The gate blocked immediately, named M2, printed
"Fix the code. Do NOT loosen the test.", exit 1. So the gate mechanism is fine;
the hole is specific to M1.

Both breaks reverted; suite is back to the M1/S1/S2 baseline above.

### Bypass on the record

This commit uses `git commit --no-verify`. The hook blocks any commit touching
`sim/tests.py` or `sim/known_failures.txt`, and this is the commit that *adds*
`known_failures.txt`, so it trips its own wire on the first use. Nothing in
`sim/tests.py` was changed — verified: the file is byte-identical to the
handoff commit. No threshold was moved.

### Open, for triage before any build work

1. M1 vacuous — why does the `cap` mutant not trip the counter?
2. S2 — the pooling claim's own negative control says the claim is false.
3. S1 ECE 0.098 vs 0.091 — environment, or a real change?

---

## 2026-08-27 — M1 diagnosed; docs corrected; environment pinned

### Correction to the entry above, and to the handoff docs

The 27 August entry above, `CLAUDE.md`, `docs/00_HANDOFF.md` and
`docs/02_RESULTS.md` all said or implied that **S1 is the only known failure**.
That was false. On a clean checkout it is **S1 FAIL, S2 FAIL, M1 VACUOUS**.
`docs/02_RESULTS.md` additionally claimed "Mutation tests all fire," which M1
directly contradicts.

Those four documents have now been corrected to state all three. This entry is
the history of that change; per rule 8 the earlier entry above is left standing
rather than rewritten. Nothing in `sim/tests.py` was touched.

### M1: why the attempt-cap mutant cannot trip the counter

Time-boxed diagnosis, ~40 minutes. Both suggested hypotheses were checked.

**Hypothesis B (mangled `pop_spend`) — ruled out.** `sim/tests.py:38-39` passes
`pop_spend=1.05` explicitly to *both* the clean and the mutant run, on top of the
`functools.partial` at line 13 that already sets it. Redundant, but identical.
It is not the cause. (The sed damage is real but cosmetic and elsewhere: line 1
is `import functools` sitting *above* the module docstring, so what reads as the
docstring is actually a no-op string expression.)

**Hypothesis A (mandates collected before a 5th attempt) — confirmed, with a
sharper mechanism than "collected".** Instrumented copy of the harness, mutant
run, `portfolio`, seed 7, `pop_spend=1.05`:

```
commits        1066
n at commit    {0: 1017, 1: 48, 2: 1}     <- 49 of 1066 had anything to reset
max attempts in any one (mandate, cycle):  3
NPCI_MAX = 4  ->  V.cap fires on the 5th attempt
```

The mutant (`m["n"] = 0` at commit time) works exactly as designed: it removes
the attempt-cap constraint. **The problem is that the cap was never binding.**
At the operating point the suite runs at, the deepest any mandate-cycle ever
reaches is 3 attempts against a cap of 4. Removing a constraint that is already
slack changes nothing — clean and mutant runs are byte-identical on every
metric (att/cycle 1.178, approval 0.883, recovery 0.969, all five violation
counters 0). So the counter is never exercised and the gate is vacuous.

Why the cap is slack: `sim/tests.py` never passes `payday_err`, so the whole
suite runs at the harness default `payday_err=1` (`sim/harness.py:87`). At ±1
day the policy hits payday nearly every time, per-attempt approval is 88% and
recovery 96.9%, so mandates are collected on the first or second attempt and
never need a fifth.

Confirmed by sweeping the operating point (instrumented copy, same population):

| `payday_err` | max attempts/cycle | mutant `V.cap` | recovery |
|---|---|---|---|
| 1 (suite default) | 3 | **0 — VACUOUS** | 0.969 |
| 3 | 5 | 1 — fires | 0.796 |
| 7 | 4 | **0 — VACUOUS** | 0.495 |

So M1 is not a broken mutant and not a broken counter. It is a gate being run at
an operating point where the thing it tests cannot happen. Note it only fires at
±3d, and then on a **single** violation — even the fixed version would be a
knife-edge gate, not a robust one. Whoever fixes this should not just switch the
operating point and declare victory.

**Did not touch the harness**, per the time-box.

### The same root cause probably explains S2

S2 also runs at the default `payday_err=1` — `docs/02_RESULTS.md` calls ±1 day
"tie — no reason to build". And S2 compares `solo_shared` / `solo_placebo` /
`solo_pop`: the **point-estimate payday** trio, not the `*_pd` posterior trio the
moat is claimed for. `02_RESULTS.md` already reports the point-estimate pooling
effect as −0.16 / −0.49 (n.s.). Measured real-vs-own here is −0.96, which matches
the ±1d column of the full table (98.9% vs 99.8%) almost exactly.

`solo_placebo_pd` **already exists** in the harness (`sim/harness.py:47,50,53`),
so the posterior-architecture negative control could be run. Not doing it: S2 is
explicitly on hold pending a decision about what it should have been testing.

### Environment pinned

`requirements.txt` added: `numpy==2.4.2`, CPython 3.12.0. **The numpy version
that produced the handoff numbers was never recorded.** So the ECE difference
(`02_RESULTS.md` said 0.098, measured 0.091) is **currently unattributable** —
it could be a numpy/BLAS difference or a real change, and there is no way to
tell without knowing what the original ran on. Do not treat 0.091 as evidence
that anything improved. It is worth noting the direction: 0.091 is *inside* the
0.10 bound, so S1 now fails purely on the monotonicity half of the gate.

---

## 2026-08-27 — PRE-REGISTRATION: S2 rebuild, operating point, dead gates

**Written before any of it was run.** Predictions below are committed in advance
so that "we got the result we wanted" is checkable rather than assertable. Where
I already know an answer from an earlier session I say so — that is prior
knowledge, not a prediction, and it does not count in my favour.

### Change being made

1. Gates that only bind under contention (M1, S2, T5, T7) move to
   `payday_err=7`. Everything else stays at the harness default `payday_err=1`.
2. S2 is rebuilt as three arms at ±7d on the **payday-posterior** policies, plus
   the old point-estimate gate kept as `S2_LEGACY` at its original operating
   point so the change is auditable rather than a quiet substitution.
3. T1 and T7 get real mutants; T3 gets implemented or deleted; S3 gets built.

### A correction I owe first

Last session I said **T1 "cannot fail"**. That was overstated and I should not
have said it. T1 computes `orc` from a live oracle run — if the oracle
*regresses*, `orc` drops and policies exceed it, so T1 fires. The `weak_oracle`
mutant drops the oracle to 46.3% against a best policy of 96.9%, so T1 would
fire on it. What is actually true is weaker and duller: **T1 has no margin.**
With a correct oracle at 100.0% and the best policy at 96.9%, only an oracle
regression larger than 3.1 points is detectable. A subtle oracle bug survives.
That is a real weakness, but "vacuous" was the wrong word and I am correcting it
before building on it.

### Pre-registered predictions

**S2a — `solo_pop_pd` → `solo_shared_pd` (the moat).** Docs claim +10.23 SIG at
±7d. Gate: PASS if mean > 2 SE over 8 populations.
*Prediction:* positive and significant, but **smaller than +10.23** — the docs'
figure came from n=30 / 4 seeds and we run n=100 / 8 seeds. Point estimate
guess **+4 to +12 pts**. If this lands at ≈0, the moat does not reproduce and
that is the headline finding of the day.

**S2b — `solo_pop_pd` → `solo_placebo_pd` (the confound check).** Gate: placebo
is "neutral" if |mean| < 2 SE.
*Prediction:* **I expect this to fail neutrality, with placebo well below own,
around −5 to −20 pts.** Reason: `solo_placebo` does not inject *neutral* extra
update events, it injects *wrong* ones — outcomes computed against a different
customer's balance (`harness.py:227`). Feeding a belief actively misleading
observations should be worse than feeding it nothing, not the same. If that is
right, the placebo arm is not a clean negative control.

**S2c — `solo_shared_pd` → `solo_placebo_pd` (the doc's headline).** Docs claim
+21.68 / +23.99 SIG. Gate: PASS if mean > 2 SE.
*Prediction:* **large and positive, roughly S2a + |S2b|**, so plausibly ~+15 to
+30 — i.e. it will probably reproduce the doc's impressive number. **And it will
be the least informative of the three**, because most of its magnitude comes
from the placebo arm being damaged rather than from pooling being good.

**The decision rule, committed now:** if S2b shows placebo significantly below
own, then **S2c must not be quoted as evidence for the moat**, and the claim
rests on S2a alone. I am writing this down before seeing the numbers precisely
so that a big S2c cannot be retro-fitted into support.

**M1 at ±7d — prior knowledge, not a prediction.** I already swept this last
session: at `payday_err=7` the cap mutant produced `V.cap = 0` with a maximum of
4 attempts per mandate-cycle against `NPCI_MAX = 4`. **So I expect moving M1 to
±7d to leave it VACUOUS.** The operating-point fix, as specified, will probably
not fix M1. Recording that up front so it is not presented afterwards as a
discovery.

**T5 at ±7d:** expect PASS, with a smaller margin than at ±1d.
**T7 at ±7d** with a per-event cap check: expect PASS on clean runs.
**T1:** expect dominance to hold and the crippled-oracle self-check to fire.
**S3 headline** (`solo_shared_pd` − `payday_wait` at ±7d, 8 seeds): docs claim
+17.8 (±7.5). Expect positive and significant, magnitude uncertain.
**Oracle at ±7d:** docs say 100.0%. Expect it still saturates.

### What would make me wrong

- S2a ≈ 0 → the moat does not reproduce; the project's central claim is in
  trouble and no amount of S2c rescues it.
- S2b ≈ 0 → I am wrong about the confound, the placebo really is neutral, and
  the doc's +21.68 headline is sound as written.
- M1 fires at ±7d → my prior sweep did not generalise across populations.

---

## 2026-08-27 — RESULTS vs the pre-registration

Scored against the predictions committed in `fb50332`, before any of this ran.
Suite is now **21 gates, 3 FAIL, 1 VACUOUS, 17 pass**, runtime **1595s (~27 min)**.

### Where I was right

| Arm | Predicted | Measured | Verdict |
|---|---|---|---|
| **S2a** moat | positive, SIG, **below +10.23**, guessed +4 to +12 | **+9.53** (±1.81) SIG | right |
| **S2b** confound | **fails neutrality**, placebo below own, −5 to −20 | **−14.51** (±2.24) | right |
| **S2c** headline | large, ≈ S2a + \|S2b\|, +15 to +30 | **+24.04** (±2.25) | right |
| **M1** at ±7d | stays VACUOUS (prior knowledge, not a prediction) | still VACUOUS | as declared |

**The moat reproduces.** +9.53 pts against the +10.23 the results doc claimed.
That is the one genuinely good result of the day and it stands on its own.

**The confound is real, and bigger than the moat.** S2c decomposes exactly:
9.53 + 14.51 = 24.04. That is not a coincidence, it is an algebraic identity
for paired means — `(real − plac) = (real − own) + (own − plac)`. So S2c
contains no information that S2a and S2b do not already carry, and **60% of the
+24 headline is the placebo arm being damaged**, not pooling being good.

The decision rule committed in advance was: *if S2b shows placebo significantly
below own, S2c must not be quoted as evidence for the moat.* S2b does. So it
must not, and `docs/02_RESULTS.md` has been rewritten accordingly. The old
"+21.68 / +23.99 — the benefit is information, not an artefact" was the
strongest-sounding claim on the page and it was inflated about 2.5×.

This also settles the question I could not answer from the docs last session:
the old +23.99 was produced on the **payday-posterior** pair. Our S2c gives
+24.04 on that same pair. The gate in `tests.py` was simply testing a different
(point-estimate) trio, which is why the two disagreed.

### Where I was wrong

**T1, twice, and both my earlier statements were wrong.** I first said T1
"cannot fail". I then corrected that to "it fires under weak_oracle but has
3.1 points of slack". The second version was also wrong, in two ways. The
margin is **0.4 pts**, not 3.1 — I had computed it against `portfolio` (96.9%)
instead of the best policy in the list (`solo_pop`, ~99.6%). And I had the
direction backwards: a *smaller* margin makes T1 *more* sensitive, not less,
because the gate fires as soon as the oracle drops below the best policy. At a
0.4 pt margin T1 catches any oracle regression beyond 0.4 points. It is neither
vacuous nor slack. It now also runs at both contention levels and is paired with
the `weak_oracle` mutant, which it catches. I should not have called it dead.

**T5 margin.** I predicted a smaller margin at ±7d than at ±1d. Wrong, and
backwards for the same reason as T1: at ±1d it was 96.4% vs 96.9% (0.5 pts), at
±7d it is 43.3% vs 49.3% (**6.0 pts**). Where the cap actually binds, raising it
helps more, which is obvious in hindsight.

### The S3 null control is weaker than I would like

S3 passes: positive control +76.13 (±1.61) reads significant, null control
−2.60 (±6.00) reads non-significant. But the null control's error bar is very
wide because `payday_wait` is highly sensitive to the run seed that sets its
payday estimate. A null control that passes because its SE is ±6.00 would not
catch a machinery that inflates significance modestly. It is a real gate with a
real mutant, but do not oversell it.

### Gates repaired

- **T3** was a duplicate determinism check wearing a leakage test's name.
  Rewritten to the property `05_TEST_DESIGN.md` specifies — a belief's
  predictions must not move when the world's balance array is poisoned — with a
  `_LeakyPD` mutant that must be caught. Passes; mutant caught.
- **T7** cap clause moved from the mean `att_per_cycle` to per-event
  `vdetail["cap"]`, so one mandate taking a 5th attempt is now visible. Paired
  with a poisoned-result mutant. Passes; poison caught on 2 clauses.
  **Still does not implement the conservation identity** — `harness.run` does
  not return the counts, and that is a harness change I did not make.
- **S3** implemented, 8 populations, with both controls described above.
- **M7 and M9** from `05_TEST_DESIGN.md` remain unimplemented.

### Things that are now worse and need a decision

**The suite takes 27 minutes.** The three `_pd` arms dominate: `solo_shared_pd`
costs ~25s per run at n=100 and there are 24 such runs. I added a result cache
to `tests.py` (safe — `run()` is deterministic, which is what T4 asserts, and T4
deliberately bypasses the cache) which recovered the redundant re-runs T1/T7/T8
were doing, but it does not offset the new arms. **A 27-minute pre-commit hook
will not survive contact with a build week.** Options, none taken yet: split the
gate into a fast tier for every commit and the full suite on a tag; or drop the
S2 arms to n=60 (~18s/run) at some cost in power. Needs a decision.

**Bypass on the record.** This commit uses `--no-verify`. It edits
`sim/tests.py` and `sim/known_failures.txt`, which is exactly what the tripwire
is for. What changed and why is above; the old S2 was kept as `S2_LEGACY`
rather than deleted precisely so this is auditable. No threshold was loosened:
S1's 0.10 is untouched, and the two S2 gates that fail are left failing.

---

## 2026-08-27 — handoff prep: environment facts and stale-spec cleanup

Context: the next work (runtime optimisation + an ML baseline) is being handed
to a session with **no history of this repo**. Three things were fixed first,
because each of them is a trap a cold reader walks straight into.

**`CLAUDE.md` now has an Environment section.** Repo root is not the shell's
starting directory; `python`/`python3` on PATH are msys2 builds with no numpy
and no pip; the full suite takes ~27 minutes and looks hung under a default
timeout; large heredocs into files are unreliable in this shell. All of that
cost an hour to rediscover once and none of it was written down.

**`05_TEST_DESIGN.md` got an appended implementation-status section.** The file
is a pre-registration — it was written before the harness on purpose — so it
was NOT rewritten. What changed: one retracted number (`+5.4 points`) that
rule 6 forbids quoting was removed from the S2 rationale, and a clearly
separated section now records what was actually built versus what was
specified. M7, M9 and the T7 conservation identity are still unimplemented;
T3, T7, T1, S2 and S3 differ from the spec; and the spec names a policy
`solo_own` that has never existed.

**`04_BUILD_PLAN.md` scope change recorded.** The out-of-scope list said "no
further simulation research" and "any new policy variant". The ML baseline
needs both, so the removal is written down with its reason rather than left as
a silent contradiction a future reader would trip over. Exactly two new policy
variants are authorised (`explore`, `ml_index`); coordinated budgeting stays
cut and the agent build is still the deliverable.

Nothing in `sim/` changed in this commit.

---

## 2026-08-28 — Runtime: profile first. And the pre-registration for both workstreams.

New session, no history of this repo beyond `CLAUDE.md` and `docs/`. Two
workstreams handed over: make the suite fast without weakening it, and build an
ML baseline. The agent build is explicitly NOT started.

### Order of work, and why the reference file came first

`sim/t9_reference.json` was captured **before a single line of `sim/w3.py` or
`sim/harness.py` was touched**. A performance change that cannot be proved
inert is not a performance change, it is an unreviewed rewrite of the results.

### 1a — the profile, scored against the hypothesis in the brief

The brief stated a hypothesis and, correctly, told me to test it rather than
assume it. Scoring it honestly:

| Claim in the brief | Verdict |
|---|---|
| `BeliefPD.hyp` is 10 hypotheses, not ~15 | **right** — `[0,3,…,27]`, `len=10` |
| `NB = 90`, convolve is 90 elements with a 3-tap kernel | **right** |
| `advance()` = 10 `_step_one` per belief per day | **right** — 60,000 calls → 600,000 `_step_one` |
| `forecast()` = 12 × 10 = 120 `_step_one`, and "may well dominate advance()" | **right, and it does dominate** |
| `observe()` also loops over hypotheses | true, but **negligible** — 4% of runtime |
| the cost is interpreter overhead in tiny numpy ops | **right in kind** |

`cProfile`, one `solo_shared_pd` run, n=100, `payday_err=7`, `pop_spend=1.05`.
Unprofiled wall time 13.75s; profiled 23.7s across **39.6M function calls**.

```
ncalls   tottime  cumtime  what
4025429    4.083    4.083  numpy.ufunc.reduce          (the .sum() calls)
1699340    3.869   18.200  w3.BeliefPD._step_one
1824099    2.498    5.971  w3.BeliefPD._shift
1699340    1.951    4.341  numpy.convolve
1699340    1.504    1.504  builtins.round
   9702    0.616   12.615  w3.BeliefPD.forecast        <-- 53% of the run
  60000    0.314    6.979  w3.BeliefPD.advance         <-- 29% of the run
  12860    0.341    0.997  w3.BeliefPD.observe         <--  4% of the run
```

**Real ranking: forecast ≫ advance ≫ observe.** By `_step_one` calls:

| policy | advance | forecast | why |
|---|---|---|---|
| `solo_shared_pd` | 600,000 (35.3%) | 1,099,340 (**64.7%**) | POOLED: one forecast per decision hour |
| `solo_pop_pd` | 600,000 (18.3%) | 2,687,060 (**81.7%**) | not pooled: one forecast **per mandate** |

So `harness.py:326` is the hot line, exactly as the brief guessed.

### Where the brief's implied conclusion is wrong: vectorising is not the fix

The cost is interpreter overhead — but the right response is not to make each
call cheaper, it is to **stop making calls that recompute a number we already
have**.

`forecast(day)` computes `P1 = step(P, day+1)`, `P2 = step(P1, day+2)`, …
Tomorrow, `advance(day+1)` computes `step(P, day+1)` — that **is** `P1`, the
same function on the same array. And `forecast(day+1)` then computes `P2…P13`,
of which `P2…P12` were computed yesterday. If nothing changed the state in
between, **11 of the 12 steps are recomputations and `advance()` is free.**

How often does nothing change? `observe()` is the only invalidator:

| policy | forecast calls | observe calls | forecasts needing a full rebuild |
|---|---|---|---|
| `solo_pop_pd` | 23,690 | 2,629 | ~11% |
| `solo_shared_pd` | 9,702 | 12,860 | ~26% |

**Second redundancy, and it is worse.** For a POOLED, non-placebo policy every
mandate of a customer receives the *same* observations in the *same* order —
its own attempt via the `own` line at `harness.py:222`, every other mandate's
via the pooling loop at `harness.py:224-231`. All five beliefs start identical
and are fed identical calls. Measured across a full run:
`max|P_i − P_0| = 0.0` **exactly**, for all i. Five copies of one distribution,
each paying its own `advance()` every day.

The placebo policies are the exception and genuinely differ
(`max|P_i − P_0| = 0.94`) — there the acting mandate gets the real outcome and
the others get the donor-balance outcome.

### Measured, and bit-exact

Both changes were prototyped in a scratch directory with `sim/` untouched, and
checked against `sim/t9_reference.json`:

```
                 28 configs, incl. 20 sha256 hashes of every predicted
                 P(success) at every dispatch, raw float64 bytes
  CACHE            -> EXACT MATCH
  CACHE+COLLAPSE   -> EXACT MATCH
```

That is float-identity, not "close enough": one ulp anywhere in the filter
moves the hash.

Speed, one run each, n=100, pe=7:

| policy | before | after | |
|---|---|---|---|
| `solo_pop_pd` | 33.86s | 10.06s | 3.4× |
| `solo_shared_pd` | 21.85s | 5.31s | 4.1× |
| `portfolio_pd` | 22.59s | 6.39s | 3.5× |
| `solo_placebo_pd` | 23.14s | 12.50s | 1.9× (no collapse — beliefs differ) |
| all 13 policies | 143.19s | 60.54s | 2.4× |

**Caveat on every absolute number above.** This machine's timings drift a lot.
The same `solo_shared_pd` run measured 13.75s early on and 21.85s an hour
later, on an idle machine, unchanged code. Ratios inside one matched pass are
trustworthy; absolute seconds are not. The only number worth quoting is an
end-to-end suite run, and that has to wait until the changes are in `sim/`.

### The suite is 625s here, not the documented 1595s

Instrumented full suite: **625.3s**, 126 `harness.run` calls, and the gate
results reproduce the documented baseline exactly — 21 gates, 3 FAIL (S1, S2b,
S2_LEGACY), 1 VACUOUS (M1), 17 pass, with `+9.53`, `−14.51`, `+24.04`, `−0.40`,
`ECE=0.091` all matching `docs/02_RESULTS.md` to the decimal.

`CLAUDE.md` and `docs/02_RESULTS.md` say ~27 minutes (1595s). I measured 625s
**while two other jobs of mine were competing for CPU**, so the clean figure is
lower still. I cannot attribute the gap. Given the 60% run-to-run drift above,
machine state is a plausible explanation and so is a difference nobody
recorded. **I am not editing the 27-minute figure in the docs on the strength
of one measurement** — noted here, to be settled by the post-optimisation
end-to-end run.

Where the 625s goes: three policies are 64% of it.

```
solo_pop_pd       8 calls  183.3s  29.3%   22.92 s/call
solo_placebo_pd   8 calls  110.6s  17.7%   13.83 s/call
solo_shared_pd    8 calls  105.8s  16.9%   13.22 s/call
...everything else, 102 calls, 225.6s
```

All 24 of those are the S2 arms: n=100, 8 populations, `payday_err=7`.

### RAISED SEPARATELY, NOT FIXED: `harness.py:325` is a real defect for the placebo arms

The brief told me not to bundle this into a performance change. I am not. But
the profiling turned up the evidence, so it goes on the record.

`if fc_days is None or policy not in POOLED:` computes the forecast **once per
decision hour, from the first live mandate's belief**, and reuses it for every
mandate.

- For the five non-placebo POOLED policies (`solo_shared`, `solo_shared_pd`,
  `portfolio`, `portfolio_pd`, `myopic`) this is **exactly correct**, because
  the beliefs are provably identical — measured `max|diff| = 0.0`. It looks
  like a bug and is not one.
- For `solo_placebo` and `solo_placebo_pd`, which are also in `POOLED`, the
  beliefs **do** differ (`max|diff| = 0.94`). So mandates 2..k are scored using
  mandate 1's forecast. **That is a defect, and it is in the placebo arms** —
  the arms behind S2b (−14.51) and S2c (+24.04).

I do not know how much of S2b's non-neutrality is "the placebo injects wrong
observations" (the documented diagnosis) versus "the placebo scores four of
five mandates off the wrong belief". Both mechanisms push the same direction.
**Until this is separated, the −14.51 has two candidate causes, not one.**
Not changing it: it moves every pooled number and it is not a performance
issue. Flagged for a decision.

### PRE-REGISTRATION — runtime work

Committed before implementing, so "it worked" is checkable.

- **Prediction 1.** Incremental forecast + belief collapse alone bring the full
  suite under 300s. Basis: 2.4–4× measured per policy against a 625s suite.
  Wrong if the suite is dominated by something the per-policy benchmark missed.
- **Prediction 2.** Parallelising over (policy, seed) brings it under 90s on 32
  logical cores. The longest single run is the binding constraint, ~10s.
- **Prediction 3.** T9's paired mutant (a worker pool seeded from one shared
  RNG rather than per-run seeds) **will fire**, because a shared RNG changes
  `w3.balance_trace` draws and therefore every downstream count. If it does not
  fire, T9 is VACUOUS and must report itself as such — this project has shipped
  three gates that could not fail and this must not be the fourth.
- **Prediction 4, the one I am least sure of.** T9's five headline metrics are
  ratios of integer counts, so they are a **coarse** detector: an arithmetic
  change that flips no scheduling decision leaves them untouched. I predict
  that if the filter is ever vectorised, `cycle_rec` will still match for most
  policies while `calib_sha256` will not. That is why both are stored. If a
  future session reports "T9 passes" after a float-touching change, check
  **which half** passed.

### PRE-REGISTRATION — the ML study (written before any ML code exists)

**The structural bias, stated first.** `w3.Belief` and `w3.BeliefPD` are
hand-built to match `w3.balance_trace`: same `hourly_spend_profile`, same
payday model, same salary arrival. **The Bayes filter is the true generative
model of this world.** Any ML comparison run only in-distribution is biased
toward Bayes by construction and must not be reported as like-for-like.

The filter is not *perfectly* specified even in world A — it gets a noisy
salary estimate (±30%), a population spend rate rather than the customer's own,
and it approximates the world's hourly `uniform(0.4,1.6)` spend jitter with a
fixed drain plus a fixed 3-tap diffusion kernel. So it is right in **structure**
and wrong in **parameters**. That distinction is the whole experiment.

**Predictions, in advance:**

1. **In-distribution, `solo_shared_pd` beats `ml_index`.** If `ml_index` wins
   in world A, I will treat it as a bug — feature leak or a candidate-day
   mismatch in the 2d ablation — before believing it, per the brief.
2. **`ml_index` beats `payday_wait` in-distribution at pe=7.** If it does not,
   the ML model has not learned anything the 5-line heuristic doesn't have, and
   the misspecification study is not worth running.
3. **Under misspecification, which one degrades less depends on which
   assumption is broken, and I expect a split result:**
   - **decay 0.20 / 0.70 → Bayes still wins.** The decay constant controls how
     fast money drains, not *when it arrives*. The filter's value is the payday
     posterior, learned online from censored observations, and a success at ₹X
     still proves balance ≥ ₹X whatever the decay is. Mis-parameterised, not
     mis-structured.
   - **wider payday dispersion (0.60 → 0.30) → Bayes still wins.** This makes
     the filter's *prior* less informative but leaves its structure correct; it
     has a posterior over payday precisely so it can recover from a bad prior.
   - **`irregular_frac=0.5` → this is where I expect ML to win.** Income
     arriving 6 times a cycle on random days makes the single-payday hypothesis
     class **actively wrong**, not merely mis-tuned. There is no payday to find.
     This is the one shift that attacks structure rather than parameters.
   - **`topup_p=0.25` → both degrade, roughly together.** Neither model knows
     about a replenishment process triggered by its own failures.
4. **The counter-consideration, stated before the numbers, because it is the
   easy thing to forget:** the ML model is trained on world A *only*, so under
   shift **it is out of distribution too**. This is not a free win for ML. A
   model with no structural prior can degrade worse, and if it does, that is a
   real result in favour of the structural prior — but it must be reported with
   the caveat that ML was never allowed to retrain. The genuinely fair
   comparison, ML retrained on the shifted world, is a different experiment and
   would favour ML.
5. **A bias in the training data, declared now, that runs AGAINST ml_index.**
   `explore` picks a legal day uniformly at random. That gives an unbiased
   sample over *days*, but the *states* it visits are explore's states, not
   `ml_index`'s. Deployed, `ml_index` will visit states the training set
   under-covers — late attempts in a cycle after two failures, for instance.
   This is off-policy evaluation and it costs ml_index something. It is still
   the right choice: training on `solo_shared_pd`'s own trajectories would bias
   the ML model toward reproducing Bayes, which is worse.

**What would make me wrong:** `ml_index` winning in-distribution (→ look for a
leak); `ml_index` winning at the decay shifts (→ my "structure survives
mis-parameterisation" argument is wrong); Bayes winning at
`irregular_frac=0.5` (→ the payday posterior degrades gracefully into a
"money arrives sometimes" model, which would be a genuinely good finding for
the filter).

**The decision rule, committed now:** if `ml_index` wins anywhere, it goes in
the report as a win, plainly, without hedging. If it wins only under
misspecification, that points at the hybrid (2f) and the pitch says so.

---

## 2026-08-28 — Runtime RESULTS, scored against the pre-registration above

**Full suite: ~1600s -> 78.9s. Fast tier: 34s. Every gate number unchanged.**

### A correction I owe, before the good news

Earlier in this same session I wrote that the suite runs in **625s here, not
the documented 1595s**, and that I could not attribute the gap. **That was
wrong, and the docs were right.** The 625s reading was the outlier. The very
next full run — the pre-commit gate on the T9 reference commit — sat at 1030s
of CPU and was still going at 17 minutes, and it is the 625s measurement that
does not reproduce. Most likely I caught the machine in a boost state early in
the session.

Two things follow. `CLAUDE.md`'s "~27 minutes" needed no correction and has not
been touched. And the drift on this machine is larger than I credited: the same
unchanged run measured 13.75s and 21.85s an hour apart on an idle box. **Do not
quote a single timing measurement from this machine as a fact.** The numbers
below are all from the same session and the same code, which is the only
comparison that means anything.

### Scoring the four pre-registered predictions

| # | Predicted | Measured | Verdict |
|---|---|---|---|
| 1 | algorithmic work alone brings the suite under 300s | **458.5s** (`SIM_SERIAL=1`) | **WRONG** |
| 2 | parallel over 32 cores brings it under 90s | **78.9s** | right |
| 3 | T9's shared-RNG mutant will fire | fired, **177–180 fields differ** | right |
| 4 | if the filter were vectorised, metrics would match while `calib_sha256` would not | **not tested** | untested — we never vectorised, so this stays a live prediction for whoever tries |

**Prediction 1 was wrong, and the reason matters more than the miss.** I said
"under 300s" against a suite I believed ran in 625s. It really ran in ~1600s,
so the target was set against a baseline that did not exist. Measured serially,
the algorithmic work alone gives **~1600s -> 458.5s, a 3.5x gain** — which is
*better* than the 2.4–4x per-policy range I predicted, on a suite 2.5x larger
than I thought. So the speedup estimate was sound and the absolute number was
nonsense, because I anchored it to a bad measurement I had already been warned
by my own drift data not to trust.

The stack, isolated:

| | wall | vs before |
|---|---|---|
| before | ~1600s | — |
| + incremental forecast + belief collapse (serial) | **458.5s** | 3.5x |
| + parallel over 32 cores | **78.9s** | 20x total |
| fast tier | **34s** | — |

### What was actually done

Three changes, in the order the evidence justified them:

1. **Incremental forecast** (`w3.Belief`, `w3.BeliefPD`). The rollout is rolled
   forward one day instead of rebuilt, and `advance()` **consumes** the first
   entry rather than recomputing it. Bit-identical by construction: every array
   is produced by the same method on the same input.
2. **Belief collapse** (`harness.py`). A POOLED, non-placebo policy fed all *k*
   mandates of a customer identical observations in identical order, so it was
   maintaining *k* copies of one distribution. Measured `max|P_i − P_0| = 0.0`.
   Now one belief per customer. Placebo policies excluded — theirs genuinely
   differ (`max|diff| = 0.94`).
3. **Parallelism over (policy, seed)** (`sim/runner.py`). Identity-preserving by
   construction, not by measurement: every run derives all randomness from its
   own seed.

**We did not vectorise the filter, and on the evidence we should not.** The
brief framed a tension between vectorising (not bit-safe) and parallelising
(bit-safe). The tension dissolved: the cost was never the price of each numpy
call, it was calling numpy to recompute numbers we already had. The safe wins
were more than sufficient, so the only step that would have touched float
arithmetic was never needed.

### The proof that nothing moved

`sim/t9_reference.json` was captured and committed first (`fb99e9f`). After all
three changes:

```
T9  28 configs exact (20 float-level hashes); shared-RNG mutant caught
```

and every statistical gate reproduces its documented value to the decimal:
S2a **+9.53** (±1.81), S2b **−14.51** (±2.24), S2c **+24.04** (±2.25),
S2_LEGACY **−0.40** (±0.22), S3 headline **+25.63** (±2.86) SIG, positive
control **+76.13** (±1.61), null control **−2.60** (±6.00), S1 **ECE=0.091,
monotone=False**, M1 still VACUOUS. The suite is now **22 gates** (T9 is new):
3 FAIL, 1 VACUOUS, 18 pass — the same four red gates as before.

### ONE NUMBER DID CHANGE, and it is a mutant, not a result

**M6 moved from `leaked=90.4%` to `leaked=88.0%`.** The clean run is unchanged
at 96.9% and T9 proves the clean path is bit-exact, so this is entirely inside
the `leak_bal` mutant.

Mechanism: `mutate="leak_bal"` overwrites `b.p` for each mandate in `live`.
Before the collapse each mandate had its own belief, so only *live* mandates
were leaked into. Now they share one object per customer, so leaking any live
mandate also leaks the belief that `portfolio`'s budget line reads
(`beliefs[id(mands[0])].expected()`), even when `mands[0]` is not itself live.
The mutant leaks slightly harder than it used to.

M6 still fires (96.9% -> 88.0%), so the gate still binds, and no documented
figure quoted M6's leaked value. But it is a real semantic change to a mutant
caused by a performance change, which is exactly the kind of thing that gets
waved through, so it is written down rather than mentioned in a commit message.

### THE SUITE SEGFAULTED, TWICE, AND I HAVE NOT FIXED IT

This blocked a commit and it is unresolved.

- **Occurrence 1.** The pre-commit gate on the T9 reference commit: `sim/tests.py`
  exited **3221225477** (`0xC0000005`, access violation) having printed no gate
  lines at all. Single process, no multiprocessing involved — this was the OLD
  tests.py. The commit was correctly blocked by `gate.py` ("the test suite
  produced no gate results ... silence is not a pass"), which is the gate doing
  its job.
- **Occurrence 2.** T9's mutant pool died with `BrokenProcessPool`, same
  signature: a worker terminated abruptly.

It is intermittent — the same mutant pool then ran clean at 4, 16 and 32
workers, three times out of three.

**Best hypothesis, acted on but NOT proven:** BLAS thread oversubscription. 32
worker processes each defaulting to a 32-thread pool is ~1000 threads on a
32-core box. `runner.py` now pins every worker to one BLAS thread, set in the
parent so spawned children inherit it *before* they import numpy. That is free
here regardless — every array is 90 elements with a 3-tap kernel, far below any
size where a threaded BLAS helps.

**Why this is a hypothesis and not a diagnosis:** occurrence 1 had no
multiprocessing at all, so thread oversubscription cannot explain it. I have no
account of that one. It has not recurred in ~10 subsequent full and fast runs.

`runner.py` no longer lets a dead worker take down the suite: it prints a loud
banner, says how many jobs were lost, and re-runs them serially. It does not
retry silently. A gate that quietly does not run is precisely the failure mode
this repo exists to avoid — see the three vacuous gates in `03_ERRORS.md`.

### Tiers, and the rule that goes with them

`git commit` runs `--tier fast` (~35s): M1–M6, M8, T1–T9, S1 — every gate that
tests whether the CODE is correct. `git push` runs `--tier full` (~79s), adding
S2a/b/c, S2_LEGACY, S3 — the gates that test whether a STATISTICAL CLAIM holds.

The statistical gates are **never shrunk to fit the fast tier.** They need 8
populations at n=100 for power, and a statistical gate at low power goes green
for the wrong reason, which is rule 1 wearing a disguise. The fast tier prints
which known-failing gates it did not exercise, and `gate.py` no longer reports
a gate as "no longer failing" when that gate simply did not run — that would
have invited someone to delete a `known_failures.txt` entry on the strength of
a tier that never asked the question.

`CLAUDE.md` now carries the numbers rule: **no number reaches `docs/`, the
pitch or the architecture document except from a `--tier full` run.**

### Bypass on the record

This commit uses `--no-verify`. It rewrites `sim/tests.py`, which is what the
tripwire is for. What changed: the file was restructured so nothing executes at
import (required — Windows spawn re-imports `__main__` in every worker, and the
old module-level suite would have re-run all 21 gates inside all 32 workers),
runs are planned up front and executed in parallel, `--tier` was added, and
gate T9 was written. **No gate's logic or threshold was altered.** S1's 0.10 is
untouched, the four red gates are still red, and `known_failures.txt` was not
edited at all. T9 is an addition, not a replacement, and it is paired with a
mutant that must trip it or it reports VACUOUS.

`explore` was added to `harness.py` and to `POLS`, per the authorised scope in
`04_BUILD_PLAN.md`, **after** the T9 reference was captured and committed, so
it cannot have influenced the reference. It is in `COMPLIANT` so T1/T7/T8 prove
it is Stage-0 clean, and deliberately not in `BELIEF_POLS` or `POOLED`.

---

## 2026-08-28 — ML baseline: RESULTS, and a premise this project got wrong

**`ml_index` beats `solo_shared_pd` in world A by +4.03 pts (±2.00), significant.**
That is the outcome the brief said to treat as a bug, and my own
pre-registration said the same. I tried to kill it. It survived, and the reason
it survived is the most important thing on this page.

### Scoring the pre-registration. I got 2 of 7 right.

| # | Predicted | Measured | Verdict |
|---|---|---|---|
| 1 | in-distribution, Bayes beats ML | **ML +4.03** (±2.00) SIG | **WRONG** |
| 2 | `ml_index` beats `payday_wait` in-distribution | 86.18% vs 59.14% | right |
| 3a | decay 0.20 / 0.70 -> Bayes wins | 0.20: +1.58 n.s. · 0.70: **ML +5.54** SIG | **WRONG** |
| 3b | wider payday spread -> Bayes wins | **Bayes +4.14** SIG | right |
| 3c | `irregular_frac=0.5` -> ML wins, this is where structure breaks | −0.06, **dead heat** | **WRONG** |
| 3d | `topup_p=0.25` -> both degrade together | ML +2.26 SIG, and **neither degraded** | **WRONG** |
| 4 | ML is out-of-distribution too and may degrade worse | ML degraded less in 4 of 5 shifts | **mostly wrong** |

That is a bad scorecard and the errors were not random. They all flow from one
premise I accepted without checking.

### THE PREMISE WAS FALSE: the Bayes filter is NOT the true generative model

`docs/04_BUILD_PLAN.md` says, and the task brief repeated, and I copied into my
own pre-registration:

> `w3.Belief` and `w3.BeliefPD` are hand-built to match `w3.balance_trace` --
> same spend shape, same payday model. The Bayes filter is the TRUE GENERATIVE
> MODEL of this world.

**It matches the functional form. It does not match the parameters, and in one
respect it cannot.**

```
BeliefPD.hyp (stride 3) = [0, 3, 6, 9, 12, 15, 18, 21, 24, 27]
customers whose TRUE payday is representable          74.0%
payday == 0 alone                                     62.0%
among the 38% with a NON-ZERO payday, representable   31.7%
```

The payday posterior is a mixture over ten hypotheses on a stride-3 grid. A
customer paid on day 7 has no correct component to converge to. For roughly a
quarter of the population the filter is structurally incapable of representing
the truth, and for two-thirds of the customers who are *not* paid on day 0 it
is off by one or two days no matter how much evidence it sees.

On top of that: `est_salary` is the true salary times U(0.7, 1.3), so ±30%
wrong by construction; `est_spend` is a population rate, never the customer's
own; the balance floor at zero is not modelled; and the world's hourly
U(0.4, 1.6) spend jitter is approximated by a fixed 3-tap kernel.

So the filter is **right in shape and coarse in parameters**. Calling it "the
true generative model" overstated it, and that overstatement made the project
look better than it was -- it implied the Bayes moat was near the ceiling of
what is achievable in-world. It is not. `docs/04_BUILD_PLAN.md` has been
corrected.

### Four attempts to kill the result. All failed.

**1. Candidate-day mismatch (the brief's prime suspect).** `ml_index` must
score exactly the day set the belief branch scores, or it is a different policy
and the comparison is void. Checked all 150 decision days at two horizons:
`Belief.forecast(day, 12)`'s day list and `ml_index`'s reconstruction are
**identical everywhere**.

**2. Feature leak.** Both required checks pass -- shuffled-label AUC 0.459 and
0.509 (a leak pushes *above* 0.5, not below), and the split is by POPULATION,
so no customer and no world draw is shared. Then an ablation, because the
shuffled-label check cannot detect a between-rows leak:

```
everything                              50 feats   AUC=0.9466
no own_* history                        39 feats   AUC=0.9469
no acc_* cross-merchant                 36 feats   AUC=0.9416
no history at all                        9 feats   AUC=0.7417
ONLY timing + amount                    10 feats   AUC=0.8089
```

The model's power lives in the observation history: strip it and AUC falls from
0.947 to 0.742. If something static were carrying the answer, the bottom row
would not have collapsed. The 0.809 from timing+amount alone is not suspicious
either -- `tgt_phase_est_pay` is exactly what `payday_wait` uses, and
`payday_wait` gets 59%.

**3. The decay trap.** `spend_decay` must reach `w3.balance_trace` and never a
belief, or the filter is correctly specified again and the shifted worlds
measure nothing. Instrumented `hourly_spend_profile`: during a
`spend_decay=0.70` run it is called 20 times with **0.70** (one per customer's
balance trace) and 40 times with **0.42** (the beliefs, and `donor_bal`).
`BeliefPD.prof` and `Belief.prof` both still equal `profile(0.42)`. And
`spend_decay=None` and `spend_decay=0.42` give bit-identical results, so the
parameterisation is inert by default -- T9 agrees.

*(Noted, not fixed: `donor_bal` does not receive the shifted decay, so under a
shifted world the placebo donor stays in world A. No placebo policy appears in
this study, so no number here is affected. Flagged for whoever next touches the
placebo arms.)*

**4. So why does ML win? S1 has been telling us for two weeks.**

| engine | ECE | monotone | top decile |
|---|---|---|---|
| Bayes filter (gate S1, red since handoff) | **0.091** | **False** | predicts 0.998, achieves 0.919 |
| ML engine (held-out populations) | **0.037** | **True** | predicts 0.973, achieves 0.927 |

The index policy is a pure function of the probabilities it is fed.
`index_score` computes `value * (p_now - discount * p_later)`. Feed it better
probabilities and it makes better decisions. **S1 has been red since the
handoff saying the filter's probabilities are wrong, and this ablation is the
first thing built that exploits it.** The result is not a bug in the ablation;
it is the S1 failure finally being cashed out into points.

That reframes S1. It has been carried as debt in `known_failures.txt`. It is
not debt. It is the largest identified headroom in the system.

### The table

n=100, k=5, 8 populations, `payday_err=7`, 120-day horizon, paired 2 SE across
populations. Both models fitted and tuned on world A only; **neither is
retrained on any shifted world**, so under shift they are both out of
distribution. 192 runs, **zero Stage 0 violations**.

| world | `payday_wait` | `solo_shared_pd` | `ml_index` | `oracle` | ml − bayes |
|---|---|---|---|---|---|
| A: in-distribution | 59.14% | 82.16% | **86.18%** | 100.00% | **+4.03** ±2.00 SIG |
| decay 0.20 (world only) | 75.19% | 92.03% | 93.60% | 100.00% | +1.58 ±1.58 n.s. |
| decay 0.70 (world only) | 49.91% | 69.45% | **74.99%** | 99.97% | **+5.54** ±2.72 SIG |
| payday spread 0.60→0.30 | 57.95% | **82.08%** | 77.94% | 100.00% | **−4.14** ±1.61 SIG |
| `irregular_frac` 0.5 | 71.07% | 89.72% | 89.66% | 100.00% | −0.06 ±1.83 n.s. |
| `topup_p` 0.25 | 69.77% | 85.02% | 87.28% | 100.00% | +2.26 ±1.14 SIG |

**Read the absolute levels carefully: most of these shifts make the world
EASIER, not harder.** Slower decay, irregular income and top-ups all leave more
money in the account, and everything goes up. Only decay 0.70 is a harder
world. "Degradation" is the wrong frame for four of the five rows; the
`ml − bayes` column is the comparison that means anything.

### The one row where Bayes wins is the interesting one

**Wider payday dispersion is the only shift that hurts ML and leaves Bayes
untouched** (82.08% vs world A's 82.16% -- statistically the same). That is the
mechanism I predicted for 3b and it is the one prediction of the shifted set I
got right, for the reason I gave in advance: the filter carries a *posterior*
over payday and learns it online, so a less informative prior costs it almost
nothing. The ML model has no posterior. It learned from world A that 62% of
customers are paid on calendar day 0 -- `tgt_day_mod_cyc` is its 4th most-split
feature -- and when that drops to 30% the learned prior is simply wrong.

**Structure survives a changed population. A learned prior does not.** That is
the honest case for the hybrid, and it is a better case than the one I
pre-registered, because it comes with a named mechanism and a measured row
rather than an intuition.

### An asymmetry in ML's favour that I have to declare

The ML model learned the population payday distribution from 800 customers. The
Bayes filter was handed a hand-specified prior (`exp(-0.10 d)` around
`est_payday`) that nobody fitted to the population. Both count as aggregate,
non-identifying population knowledge -- governance Tier 2, which `solo_pop` and
`solo_shared` are explicitly allowed -- so this is not cheating. But it is not
a like-for-like contest of inference machinery either: **part of ML's +4.03 is
that its priors were fitted and Bayes's were guessed.** Fitting the filter's
payday prior to the population, and widening the stride-3 grid, are the two
obvious things to try before concluding anything about Bayes-versus-ML.

### Other caveats, stated because rule 2 requires them

- **Off-policy, and it costs ML.** Training data comes from `explore`, so the
  states are explore's, not `ml_index`'s. Deployed, `ml_index` visits states the
  training set under-covers. Declared in advance; direction is against ML.
- **These numbers are NOT gate-protected.** Every figure in this section comes
  from `sim/ml_study.py`, not from the suite. Under the numbers rule now in
  `CLAUDE.md` they may not go into `docs/`, the pitch or the architecture
  document as they stand.
- **One seed per population.** 8 populations, one run seed each.
- **`ml_index` is an ablation, not a product policy.** It has no audit story and
  no way to explain a decision, which the track explicitly asks for.

### 2f (hybrid) NOT done, and why

Feeding Bayes posterior summaries into the GBDT needs a training policy that
carries a belief -- i.e. a third policy variant. `docs/04_BUILD_PLAN.md`
authorises exactly two (`explore`, `ml_index`) and says so emphatically. Given
what the table shows I think the hybrid is now the most valuable next
experiment, but it needs a scope decision, not an assumption. Not started.

---

## 2026-08-28 — PRE-REGISTRATION: the fair fight, S1, the hybrid, and the LTV audit

**Written before any of it is measured.** Last round I scored 2 of 7 because I
accepted a premise from the docs without checking it. The point of writing this
first is that "we got the answer we wanted" stays checkable.

### What is being changed, and why it is not test-loosening

Three handicaps on the Bayes side, all hand-set, none fitted. Removing them
makes `solo_shared_pd` *better*, which makes the ML comparison *harder* to win.
Nobody is loosening a threshold; the Bayes arm is being given the same
opportunity to fit the population that the ML arm already had.

1. **`BeliefPD(stride=3)`** — a compute hack from the research phase. Leaves
   26% of customers with no representable true payday. Compute is now ~20x
   cheaper, so `stride=1`.
2. **The payday prior `exp(-0.10 d)`** — invented, never fitted.
3. **`est_spend = pop_spend * (1 + (k-1) * 0.045)`** — a hand-derived
   correction that has never been checked against anything.

**How the fit stays honest.** Everything is selected on the TRAINING
populations (seeds 600-607, the same 800 customers the GBDT trained on) and
reported on the EVALUATION populations (seeds 700-707), which neither model has
seen. Selection uses only observable quantities — `est_pay`, attempt day,
outcome — never `c["payday"]`, `c["salary"]` or `c["spend"]`. **If I end up
using a true harness parameter anywhere, it gets labelled as cheating in the
result table, not quietly folded in.**

### Predicted direction and magnitude — committed now

**(1a) stride 3 -> 1.** The biggest of the three. 26% of customers gain a
representable payday hypothesis for the first time; those are disproportionately
the non-day-0 customers the filter currently serves worst.
*Prediction: **+3 to +8 pts** on `solo_shared_pd` in-distribution.* Cost: 30
hypotheses instead of 10, so the `_pd` policies get ~3x slower.

**(1b) fitted payday prior.** The current `exp(-0.10 d)` is broad and puts real
mass at distances the estimator cannot produce. Note `est_pay - payday` is
exactly the injected noise, so at `payday_err=7` the truth is always within 7
days of `est_pay` and a prior with mass at 15 days is spending it on the
impossible.
*Prediction: **+1 to +4 pts**.*

**(1c) fitted `est_spend`.** Least confident of the three; the hand-derived
0.045 may already be about right.
*Prediction: **0 to +2 pts**, and I would not be surprised by 0.*

**(1) OVERALL: I predict the fair-fight filter BEATS `ml_index`
in-distribution, reversing the +4.03.** Combined gain predicted **+4 to +12
pts** against a gap of 4.03. This is a real prediction that can embarrass me:
if the fair filter gains only ~2 pts, ML still wins and we ship ML.
*On the payday-dispersion row Bayes already wins by +4.14; I expect the fair
filter to widen it, because that row is exactly where the learned prior fails
and a posterior does not.*

**(2) S1 on the fair filter.** *Prediction: **ECE improves to 0.04-0.07 but S1
still FAILS on monotonicity**, ~60% confidence.* Reason: the monotonicity break
and the top-decile overconfidence (predicts 0.998, achieves 0.919) come from
things stride and prior do not touch — the filter does not model the balance
floor at zero, and it approximates the world's hourly U(0.4,1.6) spend jitter
with a fixed 3-tap kernel. A sharper payday posterior should not fix either.
*If S1 goes green it comes out of `known_failures.txt` with the reasoning
written down, and that would be the first gate this project has ever fixed by
fixing the code.*

**(3) The hybrid.** Bayes posterior summaries (expected balance, `p_success`,
payday-posterior entropy, top-hypothesis weight) as extra GBDT features.
*Prediction: wins in-distribution over both, by a **small** margin — +1 to +3
pts over whichever of the two wins — and, unlike pure ML, holds up on the
payday-dispersion row, because the entropy and top-weight features carry the
filter's online payday learning into the model.* If it wins both rows it is the
probability engine for the agent.
*What would make me wrong: the summaries turn out to be redundant with the raw
history features the GBDT already has, and the hybrid ties pure ML.*

**(4) The LTV audit — a structural prediction, testable before any run.**
`index_score` computes `value * (p_now - discount * p_later)` where
`value = amount * (1 + ltv_mult * [attempts_left == 1])`. `value` is strictly
positive, so **it cannot change the sign of the score**. Non-budgeted policies
commit every mandate with `s_ > 0` regardless of order. Therefore:
*Prediction: **`ltv_mult` is a complete no-op for every non-budgeted policy** —
`solo_*`, `payday_wait`, `ml_index`, everything except `portfolio`, `myopic`
and `portfolio_pd`, which rank against a budget.* Sweeping it over {0, 1, 6,
20} should give bit-identical results for `solo_shared_pd` and different ones
for `portfolio_pd`.
*If that holds, the "invented 6x multiplier" is not on the headline result at
all — it is on three policies, two of which are already cut as harmful.*

**Also flagged, and the docs are wrong about this too:** `01_FACTS.md` and
`CLAUDE.md` say the hardcoded `0.92` discount is "gone". **It is not.** It is
still the default in `w3.index_score` and `harness.run`, and unlike `ltv_mult`
it *does* change the sign of the score, so it is live on every index policy
including the shipping one. It gets swept in the same pass.

### T9 and the deliberate re-baseline

Several of these changes alter results on purpose. T9 exists to catch
*accidental* change, so it has to be re-baselined — and that is exactly the
move that could hide a defect, so:

- the old reference stays in git history (`fb99e9f`), where it can still be
  checked out and reproduced;
- `t9_reference.py` gains a `--recapture` mode that **prints the full diff
  against the existing reference before writing it**;
- every re-baseline pastes that diff into `NOTES.md` with the reason.

A reference regenerated without its diff on the record would make T9 a gate
that cannot fail. That is the error this project has made three times.

---

## 2026-08-28 — LTV removed, placebo forecast defect fixed, T9 re-baselined

### (4) The LTV multiplier: the structural prediction was right, and then some

Pre-registered: `ltv_mult` is a no-op for every NON-budgeted policy, because
`value` is strictly positive so it cannot flip the index's sign, and
non-budgeted policies commit every positive-score mandate regardless of rank.
I predicted it WOULD change `portfolio` / `myopic` / `portfolio_pd`, which rank
against a budget.

Swept over {0, 1, 6, 20}, n=60, pe=7:

```
solo_shared_pd  83.24 83.24 83.24 83.24      portfolio_pd  78.86 x4
solo_pop_pd     69.55 x4                     portfolio     53.01 x4
payday_wait     63.42 x4                     myopic        51.59 x4
```

**It is a no-op EVERYWHERE, including the budgeted policies.** I was right about
the mechanism and wrong about the exception: reordering only ever happens on a
mandate's final attempt of a cycle, and that never coincided with a binding
budget in a way that changed an outcome.

So the "invented 6x LTV multiplier" was not sitting on the headline result at
all. It was dead code. `01_FACTS.md` and `02_RESULTS.md` said it was "no longer
used" while it was still live; the accurate statement is that it was live and
inert. **Removed**, and the removal is confirmed inert by T9: not one
non-placebo policy moved. The cycle-based metric prices mandate death on its
own — a dead mandate forfeits its remaining cycles because they still count in
`cyc_due`.

### The 0.92 discount is a different story and the docs are wrong about it too

`CLAUDE.md` and `01_FACTS.md` say the hardcoded `0.92` discount is "gone". **It
is not.** It is the default in `w3.index_score` and `harness.run`, and unlike
`ltv_mult` it multiplies `p_later` and therefore **does change the sign** of the
index. It is live on every index policy including `solo_shared_pd`.

This is item **A3** in `05_TEST_DESIGN.md`'s adversarial sweep list, declared at
the start of the project and never done. Doing it now, `solo_shared_pd` at
pe=7, n=100, 8 populations each:

| discount | 0.80 | 0.85 | 0.88 | 0.90 | **0.92** | 0.94 | 0.96 | 0.98 | 1.00 |
|---|---|---|---|---|---|---|---|---|---|
| train (600-607) | 77.11 | 78.73 | 78.65 | **82.24** | 82.06 | 81.86 | 81.49 | 80.76 | 78.41 |
| eval (700-707) | 79.41 | 79.72 | 79.10 | 80.47 | 82.16 | **83.06** | 82.15 | 81.20 | 78.68 |

**0.92 is not a tuned peak — it sits on a broad plateau.** The argmax moves
between population sets (0.90 on train, 0.94 on eval), which is what a flat
region looks like rather than a fitted constant. That is the mildest possible
version of this finding and I want to state the other half plainly: it is still
a hand-chosen number, the spread across the swept range is **78.7% to 83.1%**,
i.e. about 4.4 points, and **every headline that depends on it should be quoted
with that range, not as a point.** Kept at 0.92 rather than moved to the eval
argmax, because moving it would be fitting a constant to the evaluation set.

### The placebo forecast defect: real, and much smaller than I implied

Raised earlier as a defect at `harness.py:325` and deliberately not bundled
into the performance work. Fixed now.

`if fc_days is None or policy not in POOLED:` reused one forecast for every
mandate of a customer. Correct for the five non-placebo pooled policies, whose
beliefs are provably identical. Wrong for `solo_placebo` and `solo_placebo_pd`,
also in `POOLED`, whose beliefs genuinely diverge — those were scoring mandates
2..k off mandate 1's belief.

The fix is one token: the condition is now `not collapse`, and `collapse` is
true exactly when the mandates really do share a belief object. It is now
correct by construction rather than by coincidence.

**Effect, measured:**

| gate | before | after | moved by |
|---|---|---|---|
| S2b placebo neutrality | −14.51 (±2.24) | **−14.09** (±2.09) | 0.42 |
| S2c real vs placebo | +24.04 (±2.25) | **+23.62** (±2.14) | 0.42 |
| S2a the moat | +9.53 (±1.81) | **+9.53** (±1.81) | unchanged |

**So the defect accounted for 0.42 of the 14.51.** I flagged that S2b's
non-neutrality had "two candidate causes, not one". It now has one: the placebo
arm is damaged because it is fed observations computed against a different
customer's balance, exactly as `known_failures.txt` says. The forecast defect
was real and worth fixing, and it was 3% of the effect. The documented
diagnosis stands.

S2a is untouched because neither `solo_pop_pd` nor `solo_shared_pd` is affected
— they collapse, so the reuse was always correct for them.

### T9 re-baseline #1, with the diff

`t9_reference.py` now has `--recapture`, which prints the full field-level diff
against the existing reference before writing, and says in its own output that
the diff must be pasted here. A reference regenerated silently would make T9 a
gate that cannot fail.

**21 fields changed, all of them on the two placebo policies. Nothing else
moved at all.**

```
solo_placebo_pd|pe1  cycle_rec 72.74% -> 71.54%   approval 37.47% -> 37.94%
                     survival  78.00% -> 77.00%   att_per_cycle 207.20 -> 202.62
                     calib_n 1900 -> 1858, calib_sha256 changed
solo_placebo_pd|pe7  cycle_rec 61.83% -> 63.90%   approval 32.06% -> 33.76%
                     survival  72.00% -> 74.67%   att_per_cycle 208.18 -> 204.47
                     calib_n 1909 -> 1875, calib_sha256 changed
solo_placebo|pe1     approval 64.66% -> 64.45%    att_per_cycle 166.96 -> 167.50
                     calib_n 1531 -> 1536, calib_sha256 changed  (cycle_rec unchanged)
solo_placebo|pe7     approval 31.00% -> 31.30%    survival 77.00% -> 76.00%
                     att_per_cycle 216.68 -> 214.29
                     calib_n 1987 -> 1965, calib_sha256 changed  (cycle_rec unchanged)
```

That diff is itself the evidence for two claims: the LTV removal was inert (no
policy moved from it), and the placebo fix touched exactly the two policies it
should have and no others. The old reference remains checkoutable at `fb99e9f`.

### Crash localisation

The first segfault was reported as "printed no gate lines", which I read as
"crashed early". **That reading was wrong.** `gate.py` reads the suite through
a pipe, so stdout was block-buffered and a hard crash discarded whatever had
not been flushed — the run could have died anywhere in its 27 minutes.
`record()` now flushes per gate and `gate.py` runs the suite with `-u`, so the
next crash names the last gate that completed. That is diagnosis support, not a
fix; the crash is still unexplained.

---

## 2026-08-28 — THE FAIR FIGHT: the ML win was an artefact of an unfitted opponent

**Headline: once the Bayes filter is given the same chance to fit itself that
the ML model always had, it wins every world, by 5 to 12 points.** The
in-distribution ML win of +4.03 reported earlier today is gone. It was never a
result about ML versus Bayes; it was a result about a fitted model versus an
unfitted one.

### Scoring the pre-registration. 3 of 7, and the pattern is consistent.

| # | Predicted | Measured | Verdict |
|---|---|---|---|
| 1 | fair filter BEATS ml_index in-distribution; combined gain +4 to +12 | gain **+13.41**, and Bayes beats ML by **9.38** | **right** (magnitude just over) |
| 1a | stride 3→1 is the biggest of the three, +3 to +8 | stride alone: **+0.49**, and stride=1 was *worse* than stride=3 under the old prior | **WRONG** |
| 1b | fitted prior worth +1 to +4 | **+12.85** — it is the whole effect | **WRONG, badly** |
| 1c | fitted `spend_beta` worth 0 to +2, possibly 0 | +0.99, and the best value is **0.0** | right |
| 2 | S1 on the fair filter: ECE 0.04–0.07, still FAILS on monotonicity | ECE **0.026–0.040**, still not monotone | **right** |
| 3 | hybrid wins in-distribution by +1 to +3 and holds under dispersion | **loses by 5–10 pts in every world** | **WRONG** |
| 4 | `ltv_mult` is a no-op for non-budgeted policies, but moves the budgeted ones | no-op **everywhere** | right on mechanism, wrong on the exception |

Two rounds of pre-registration now, 2/7 then 3/7. **The pattern is that I
predict mechanisms reasonably and magnitudes badly**, and that I keep being
wrong about which of several changes will dominate. Worth knowing about my own
estimates before the next one.

### The finding that matters most is a methodological one

**The first fit was brittle and I nearly shipped it.**

Selecting on the training populations at `payday_err=7` alone produced
`prior_w=7` with a hard window — the same number as the injected payday noise.
It looked superb: **+15.37 pts (±1.06)**, 97.53% recovery, only 2.5 points off
a clairvoyant oracle. `03_ERRORS.md` says, about error 5, that *a near-zero
oracle gap is a symptom, not an achievement*. So I checked it against the one
parameter the study never varied — `payday_err` itself, which is fixed at 7 in
all six worlds:

| `payday_err` | shipped | fitted (pe=7 only) | gain |
|---|---|---|---|
| 1 | 93.61% | 98.44% | +4.83 |
| 3 | 88.62% | 97.28% | +8.66 |
| 5 | 83.57% | 97.88% | +14.31 |
| **7** | 82.16% | 97.53% | **+15.37** ← fitted here |
| 10 | 79.87% | 84.51% | +4.64 |
| **14** | 73.40% | **68.55%** | **−4.85 — WORSE than what it replaced** |

**The gain peaked exactly at the operating point it had been fitted on, and
went negative two steps away.** The mechanism is plain once seen: a hard window
gives every hypothesis outside ±7 a weight of 1e-6, so when the true payday is
14 days from `est_pay` it is excluded a priori and no amount of evidence brings
it back.

The fix was to make the window soft — a floor of 0.25 outside it rather than
1e-6 — and, more importantly, to **re-select against the mean across
`payday_err ∈ {1,3,5,7,10,14}`** rather than at a single operating point,
still on training populations only. Final configuration:

```python
FITTED_BELIEF = dict(stride=1, prior_w=12, prior_day0=8.0,
                     prior_floor=0.25, spend_beta=0.0)
```

| `payday_err` | shipped | fitted (robust) | gain |
|---|---|---|---|
| 1 | 93.61% | 95.73% | +2.12 SIG |
| 3 | 88.62% | 95.82% | +7.20 SIG |
| 5 | 83.57% | 95.82% | +12.26 SIG |
| 7 | 82.16% | 95.57% | +13.41 SIG |
| 10 | 79.87% | 95.62% | +15.75 SIG |
| 14 | 73.40% | 93.16% | +19.76 SIG |

It gives up ~2 points at `pe=7` and buys ~25 at `pe=14`. The gain now *grows*
with payday uncertainty instead of peaking where it was fitted, which is the
shape a real effect should have — the worse your payday estimate, the more a
posterior over payday is worth.

**This is worth stating as a rule rather than an anecdote: a fitted value whose
benefit peaks at the operating point it was fitted on is tuned to the harness,
not fitted to the population.** `sim/fair_audit.py` runs this check and should
keep running.

### The final table

n=100, k=5, 8 evaluation populations (700–707, never used for fitting),
`payday_err=7`, 120-day horizon, paired 2 SE. 288 runs, **zero Stage 0
violations**. `discount` is at its hardcoded 0.92 throughout, and every row
inherits the ±4.4-point range that constant carries.

| world | `payday_wait` | bayes shipped | **bayes fitted** | `ml_index` | hybrid | oracle |
|---|---|---|---|---|---|---|
| A: in-distribution | 59.14% | 82.16% | **95.57%** | 86.18% | 85.97% | 100.00% |
| decay 0.20 | 75.19% | 92.03% | **98.86%** | 93.60% | 92.94% | 100.00% |
| decay 0.70 | 49.91% | 69.45% | **81.52%** | 74.99% | 75.85% | 99.97% |
| payday spread 0.60→0.30 | 57.95% | 82.08% | **90.42%** | 77.94% | 79.94% | 100.00% |
| `irregular_frac` 0.5 | 71.07% | 89.72% | **96.69%** | 89.66% | 88.92% | 100.00% |
| `topup_p` 0.25 | 69.77% | 85.02% | **95.84%** | 87.28% | 86.83% | 100.00% |

| world | ml − fitted | hybrid − fitted | fitted − shipped |
|---|---|---|---|
| A | −9.38 ±2.09 SIG | −9.60 ±1.86 SIG | +13.41 ±1.61 SIG |
| decay 0.20 | −5.26 ±0.97 SIG | −5.93 ±1.26 SIG | +6.83 ±1.04 SIG |
| decay 0.70 | −6.52 ±2.10 SIG | −5.67 ±1.81 SIG | +12.07 ±2.56 SIG |
| payday spread | −12.48 ±1.70 SIG | −10.48 ±1.09 SIG | +8.34 ±1.86 SIG |
| irregular 0.5 | −7.02 ±1.05 SIG | −7.76 ±1.24 SIG | +6.97 ±1.51 SIG |
| topup 0.25 | −8.56 ±2.01 SIG | −9.01 ±1.96 SIG | +10.82 ±1.22 SIG |

**Bayes wins everywhere, significantly.** The answer to "should the agent's
timing brain be an ML model" is no, and it is no in every world tested,
including the ones designed to break the filter's assumptions.

### The hybrid does not help, and I think I know why

Predicted: hybrid wins by +1 to +3 and holds under payday dispersion.
Measured: it **loses to the pure filter by 5–10 points in every world**, and is
statistically indistinguishable from pure ML.

The GBDT has `bayes_p_success` as a feature — it is the single most-split
feature in the model — so it can see the filter's answer and could in principle
just repeat it. It does not, and it cannot: a tree ensemble approximates that
input with piecewise-constant splits, and the fitted filter is now accurate
enough (95.57%, 4.4 points off a clairvoyant oracle) that any smoothing of its
probability destroys more value than the residual structure the GBDT adds. On
top of that the hybrid is trained on `explore_pd` states and deployed on
`ml_index_pd` states, which is off-policy and costs it further.

**A GBDT wrapped around a good probability is worse than the probability.**
That is a real result and it argues directly against the "add ML on top"
instinct. It is also the honest answer to a question I expected to come out the
other way.

### The pooling moat survives the fair fight, and grows

The obvious worry: if a better prior is what was really missing, maybe pooling
was only ever compensating for a bad prior, and the project's central claim
dissolves. Measured on the evaluation populations:

| | own (`solo_pop_pd`) | shared (`solo_shared_pd`) | **S2a moat** |
|---|---|---|---|
| shipped belief | 73.95% | 82.16% | **+8.20** (±0.92) SIG |
| fitted belief | 85.96% | 95.57% | **+9.61** (±1.67) SIG |

**The moat is not an artefact of the bad prior.** It is slightly larger with a
good one. Cross-merchant pooling is worth ~9.6 points either way.

*(An earlier intermediate measurement using the brittle `prior_w=7` config
showed the moat shrinking to +4.57. That number came from the configuration
that was subsequently rejected for being tuned to the operating point, and it
should not be quoted.)*

### S1 has been measuring the wrong filter for the whole project

`S1` runs `portfolio`. `portfolio` does not end in `_pd`, so it carries
`w3.Belief` — the **point-estimate** payday filter. The policy this project
recommends is `solo_shared_pd`, which carries `w3.BeliefPD`.

**So the calibration gate has never measured the filter that ships.** That also
retracts something I wrote earlier today: I said "S1 has been red since the
handoff saying the filter's probabilities are wrong, and the ML ablation is the
first thing that exploits it." The premise was about the wrong object. The
conclusion happened to survive — the payday-posterior filter *is* also
miscalibrated — but I reached it by reading a gate that was pointed elsewhere.

`S1` was **not** repointed. It is a pre-registered gate whose threshold was
declared before any result was seen, and quietly aiming it at another policy
would be indistinguishable from moving a test until it says something else.
**`S1_PD` is a new gate with the identical threshold, on the real filter.** It
fails:

| filter | ECE | monotone | S1's rule |
|---|---|---|---|
| `w3.Belief` via `portfolio` (S1) | 0.091 | False | FAIL |
| `w3.BeliefPD` shipped | 0.059 | False | FAIL |
| `w3.BeliefPD` fitted (S1_PD) | **0.026–0.040** | **False** | **FAIL** |

Fitting improved ECE by more than half and did not make the curve ordered. The
break is now in the mid-range, and the two plausible causes are the things the
fit does not touch: the filter does not model the balance floor at zero, and it
approximates the world's hourly `U(0.4,1.6)` spend jitter with a fixed 3-tap
kernel. Both are structural, not parametric. Added to `known_failures.txt` with
that reasoning.

### S4: the decision number is now gated

`S4 — fitted belief beats the shipped one (>2SE)`, in the FULL tier:
**+11.66 pts (±1.61)** on the S2 populations (400–407), a third population set
that the fit never saw.

Paired with the `ignore_bcfg` mutant, which silently drops the fitted
configuration exactly as a broken plumbing change would. Under the mutant the
measured gain **collapses to +0.00** and the gate goes red. Verified, so S4 is
not vacuous.

`FITTED_BELIEF` lives in `w3.py` as a documented constant with its provenance,
not in a gitignored artifact, so the gate runs on a clean checkout.

Suite is now **24 gates, 4 FAIL, 1 VACUOUS, 19 pass, 65.6s.**

### Docs corrected

- `CLAUDE.md` rule 5 said the `0.92` discount and the `6×` LTV multiplier "are
  both gone". Neither was. LTV was live and inert and is now genuinely removed;
  the discount is live and **not** inert and now carries its swept range.
- `01_FACTS.md`'s LTV retraction said "no longer used" — corrected to "was
  still applied, swept, found inert, now removed", plus a new `[VERIFIED]`
  entry for the discount sweep.
- `04_BUILD_PLAN.md`'s "true generative model" claim was corrected earlier
  today; the fair fight sharpens it further. The filter was not wrong in
  *shape*, it was wrong in three *parameters*, and fixing them is worth more
  than the entire ML programme.

### Still open

- **M1 remains VACUOUS.** Untouched.
- **The 0.92 discount is still hand-chosen.** Swept and reported as a range,
  not fitted, because fitting it on the evaluation set is exactly the error
  this session spent its time undoing.
- **`prior_day0=8.0` encodes the population's day-0 payday spike.** Legitimate
  Tier-2 aggregate knowledge — the ML model learned the same thing from data,
  where `tgt_day_mod_cyc` was its 4th most-split feature — but it is a
  population fact baked into a prior, and if the population changes it is
  wrong. The payday-dispersion row (+8.34 for the fitted filter) is the
  evidence that it degrades gracefully; do not assume that holds further out.
- **The segfault is still unexplained.** No recurrence in this session's ~25
  further full and fast runs since BLAS threads were pinned.

### Segfault: soak-tested, not solved

Six consecutive full-suite runs after the BLAS pinning: all exit 0, all 24
gates, identical results. Together with the rest of this session that is
roughly 30 full and fast runs with no recurrence.

**That is not a fix and it should not be recorded as one.** The mitigation
(one BLAS thread per worker, set in the parent so spawned children inherit it
before importing numpy) cannot explain the FIRST occurrence, which happened in
a single process with no multiprocessing at all. What has changed is that a
crash is now survivable and diagnosable rather than silent: `runner.py` prints
a banner naming how many jobs were lost and finishes them serially, `record()`
flushes per gate, and `gate.py` runs the suite with `-u`, so the next
occurrence will name the last gate that completed. If it never recurs, the
honest description remains "unexplained, mitigated, not reproduced in ~30
runs".

---

## 2026-08-28 — Cleanup: a 97-minute process that did 0.3 seconds of work

### The runaway was not running

`sim/fair_audit.py` was alive for **97.8 minutes**. It had consumed **0.3 CPU
seconds** and had **no child processes**. It was not a large sweep and not an
infinite loop — it was hung, and it had never executed a single simulation.

**It was the leftover from the FIRST invocation of `fair_audit.py`**, the one
that hit the Windows spawn `RuntimeError` because I called `runner.run_jobs` at
module level without an `if __name__ == "__main__"` guard. After that failure
multiprocessing left non-daemon threads alive and the interpreter never shut
down. Killing it is what finally let the stale background task report
"completed" — that is the tell.

**So it consumed nothing and contaminated nothing.** The premise that it was
"competing for 32 cores" is not supported: 0.3 CPU seconds over 97.8 minutes is
an idle process. Corroborating evidence, in the wrong direction for that
theory: the verification full-suite run on the now-genuinely-idle machine took
**81.1s**, *slower* than the 64.5s measured while the hung process existed.
This machine's drift is larger than anything that process could have caused.

**Neither reported table came from it.** Both came from completed runs that
wrote files:

| table | file | written | status |
|---|---|---|---|
| brittle config (−4.85 at pe=14) | `fair_audit.out` | 13:05:36 | completed, exit 0 |
| robust config (+19.76 at pe=14) | `fair_audit2.out` | 13:29:22 | completed |

The hung process started at 13:02:09, before either file existed. It wrote its
traceback and then hung; `fair_audit.out` was overwritten three minutes later
by the fixed re-run.

**Timing, now measured rather than estimated: `fair_audit.py` takes 1m11s.**
102 runs (6 `payday_err` × 2 configs × 8 populations, plus 6 calibration runs),
all `solo_shared_pd` at n=100, roughly half at stride=1. Predicted 1–2 minutes
against the 65s/124-run suite baseline before re-running it; measured 71s. So
97.5 minutes was **~82× its real runtime**, and it did none of that work.

The script already used `runner.run_jobs` on both call sites and needed no fix
beyond the `__main__` guard it now has. **The lesson is about me, not the
script: I wrote the trap into `sim/runner.py`'s docstring and then walked into
it an hour later in a new file.** Any new script that calls `run_jobs` needs
the guard, and a Python process at ~0 CPU is hung, not busy — check
`Get-Process | Select CPU` before assuming a long-running job is working.

### Re-verification on an idle machine: everything reproduces exactly

Every headline re-run with nothing else on the box. All identical to the
figures already reported — which is what should happen, since every run is
deterministic in its seed and T9 gates exactly that, but it needed confirming
rather than assuming.

| number | reported | re-verified |
|---|---|---|
| **S4** fitted beats shipped | +11.66 (±1.61), mutant +0.00 | **+11.66 (±1.61), mutant +0.00** |
| **moat** S2a fitted | +9.61 (±1.67) | **+9.61 (±1.67)** |
| moat S2a shipped | +8.20 (±0.92) | +8.20 (±0.92) |
| S2a (suite, shipped) | +9.53 (±1.81) | +9.53 (±1.81) |
| S2b / S2c | −14.09 / +23.62 | −14.09 / +23.62 |
| S1 / S1_PD | 0.091 / 0.026 | 0.091 / 0.026 |
| S3 headline | +25.63 (±2.86) | +25.63 (±2.86) |
| six-world table | all six rows | **all six rows, digit for digit** |
| `payday_err` generalisation | +2.12 … +19.76 | **+2.12 … +19.76** |
| T9 | 28 configs exact | 28 configs exact |

**Nothing moved.** Suite: 24 gates, 4 FAIL, 1 VACUOUS, 19 pass, 81.1s.

### `prior_day0=8.0` under stress: it fails gracefully, and the margin GROWS

The last baked-in population fact. `FITTED_BELIEF` puts 8× prior weight on
payday hypothesis 0 because `make_pop` puts 62% of customers there, and it was
fitted on populations drawn with `payday_day0_frac=0.60`. The question a judge
will ask is what happens when the population is not that one.

The world's `payday_day0_frac` moves; **the prior stays at 8.0 throughout**.
n=100, 8 evaluation populations, `payday_err=7`, 160 runs, zero Stage 0
violations.

| `payday_day0_frac` | `payday_wait` | bayes shipped | **bayes fitted** | `ml_index` | oracle |
|---|---|---|---|---|---|
| 0.2 | 55.60% | 81.38% | **88.62%** | 76.59% | 100% |
| 0.4 | 58.70% | 82.50% | **93.12%** | 82.29% | 100% |
| 0.6 ← fitted here | 59.14% | 82.16% | **95.57%** | 86.18% | 100% |
| 0.8 | 58.58% | 82.93% | **96.68%** | 91.38% | 100% |

| `payday_day0_frac` | fitted − `ml_index` | fitted − `payday_wait` |
|---|---|---|
| 0.2 | **+12.03** ±2.37 SIG | +33.02 ±2.44 SIG |
| 0.4 | +10.83 ±1.49 SIG | +34.42 ±3.43 SIG |
| 0.6 | +9.38 ±2.09 SIG | +36.43 ±3.37 SIG |
| 0.8 | +5.30 ±1.83 SIG | +38.10 ±3.95 SIG |

**Three things, and the second one is the answer to the judge's question.**

1. **No cliff.** Monotone, gentle degradation: 96.68% → 88.62% across a 4×
   change in the population parameter, **6.95 points total**. It never drops
   below the *unfitted* filter (81.4–82.9%), and it beats the competitive
   `payday_wait` baseline by 33–38 points everywhere.

2. **The advantage over ML GROWS as the population moves away from the fit**,
   from +5.30 at 0.8 to **+12.03 at 0.2**. `ml_index` degrades 14.8 points
   across the sweep; the fitted filter degrades 8.1 — **ML degrades about 1.8×
   harder.** That is the opposite of the naive worry. The reason is that
   `prior_day0` is a *prior*: an 8× weight that evidence reweights away within
   a cycle or two. The GBDT learned the same population fact as hard splits on
   `tgt_day_mod_cyc` and has no mechanism to revise it.

3. So the honest framing for the pitch is not "we got away with a baked-in
   constant". It is: **a wrong prior is recoverable and a wrong learned split
   is not.** That is the same finding as the payday-dispersion row, now
   measured directly on the specific constant that was worrying me.

**What this does NOT establish.** The sweep moves the *fraction* at day 0, not
*which day* the spike is on. A population whose spike sits on day 14 rather
than day 0 is a different and harsher test, because `prior_day0` boosts a fixed
hypothesis index. Not run; the honest limit of this result.

### Frozen

`model-frozen` tagged. `CLAUDE.md` now forbids changes to `sim/w3.py`,
`sim/harness.py` or the fitted constants before 5 September without explicit
approval. The model is done. Everything after this is the agent, and its
probability engine is `w3.BeliefPD` under `w3.FITTED_BELIEF`.
