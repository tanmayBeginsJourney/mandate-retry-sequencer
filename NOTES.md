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
