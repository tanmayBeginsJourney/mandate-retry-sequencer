# 03 — THE TEN ERRORS

Every one of these made the project look **better** than it was. Read them
before you optimise anything, because they are the shapes you will reintroduce.

They are also the strongest asset for the panel, who explicitly ask what broke
and how you recovered.

Errors 1–6 are from the research phase. Errors 7–10 were found on 28 August
2026 and are the more instructive half, because by then the project already had
a test suite, a pre-registration habit and a rule about treating large
improvements as bugs — and made them anyway.

---

**1. Quoted the wrong payment rail's retry schedule.**
Used card retry timings for a UPI problem. Mechanism: pattern-matched a familiar
schedule instead of reading the rail-specific doc.
*Guard:* every constraint traces to a rail-specific source in `01_FACTS.md`.

**2. A simulation whose policy could secretly see the future.**
The scheduler had access to the true balance array. Mechanism: shared state
between world and policy.
*Guard:* `Belief.forecast()` takes no argument carrying ground truth. Test T2
asserts bit-identical output when the future is poisoned.

**3. Calibrated against the wrong real-world metric.**
Tuned the world to match a figure that measured something else.
*Guard:* calibration anchors on per-attempt approval only, and the anchor
sensitivity is a mandatory disclosure (different anchors move the fitted spend
parameter by 2×).

**4. Headline metric inflated by end-of-horizon censoring.**
Mandates unresolved at day 30 were quietly excluded, flattering the result.
*Guard:* billing cycles are counted over the full horizon whether or not the
mandate survived. A dead mandate forfeits its remaining cycles.

**5. A broken oracle that made the system look near-optimal.**
The "clairvoyant" upper bound used index `value × (p_now − p_later)` with both in
{0,1}. If money was available now **and** later, the index was 0, so it **skipped**
— and could defer past the horizon. It skipped 29,161 opportunities where it had
the money. Reported headroom was +1.65 pts; real headroom is ~+7 (old harness),
+18 to +23 (new).
The gate that should have caught it — "oracle approval ≈ 100%" — was **vacuous**:
the oracle only fires when it already knows it will succeed, so 100% approval is
guaranteed whether or not it is correct.
*Guard:* test T1 asserts the oracle weakly dominates every policy. Mutation test
M8 restores the bug deliberately and requires the suite to catch it.

**6. A belief filter that was never checked for calibration.**
Predicted P(success)=0.757 where reality was 0.172. Cause: the spend rate was
hardcoded in `Belief.step` and no longer matched the calibrated world.
**The same hardcode exists in `legacy/sim4.py`**, in the opposite direction — a
plausible mechanical cause of the 18% unresolved mandates that triggered the
previous audit.
*Guard:* test S1 bins predicted probabilities and compares to empirical
frequency. **It currently FAILS.** That is honest, not broken.

**7. Declared an ML model the winner by giving it the only fitted opponent.**
An ML probability engine (`ml_index`) beat the Bayes filter in-distribution by
**+4.03 pts**, which looked like a real architectural finding. It was not. The
GBDT had been fitted to 800 training customers; the Bayes filter was carrying
three values a human had typed in during the research phase and never checked —
a stride-3 payday grid, an invented `exp(-0.10 d)` prior, and a hand-derived
cross-mandate spend correction. Fitting those three **on the same training
populations** flipped the result: the filter now wins in all six worlds by 5–12
points, and a Bayes+ML hybrid is *worse* than the filter alone.
Mechanism: a comparison where one side had been tuned and the other had not,
with nothing in the process asking whether the baseline was fairly configured.
*Guard:* `sim/fit_belief.py` is committed, so the fit is reproducible and its
objective is visible. Gate **S4** holds the result (+11.66 pts, ±1.61) and is
paired with the `ignore_bcfg` mutant, under which the measured gain collapses
to +0.00. **Before comparing anything to a baseline, ask when the baseline was
last fitted.**

**8. A fitted constant that peaked exactly where it was fitted.**
The first fit of the payday prior was selected at `payday_err=7` and produced a
*hard* window of half-width 7 — the same number as the injected payday noise.
It measured **+15.37 pts** and 97.53% recovery, within 2.5 points of a
clairvoyant oracle. Checked against the one parameter the study never varied,
it degraded to **−4.85 at `payday_err=14` — worse than the filter it
replaced** — because a true payday outside the window got prior weight 1e-6 and
could never be recovered by any amount of evidence.
Mechanism: hyperparameter selected at a single operating point, then evaluated
at that same operating point.
*Guard:* `sim/fair_audit.py` sweeps `payday_err` and is expected to be re-run
after any change to `FITTED_BELIEF`. The rule it enforces: **a fitted value
whose benefit peaks at the operating point it was fitted on is tuned to the
harness, not fitted to the population.** The refitted version selects against
the *mean* across `payday_err` and its gain now grows with payday uncertainty
(+2.12 at ±1 to +19.76 at ±14) instead of peaking.

**9. The calibration gate measured a filter that was never the product.**
Gate S1 — "the entire project rests on the belief filter being
well-calibrated" — runs the `portfolio` policy. `portfolio` does not end in
`_pd`, so it carries `w3.Belief`, the **point-estimate** payday filter. The
recommended policy is `solo_shared_pd`, which carries `w3.BeliefPD`. For the
whole life of the project the calibration gate was pointed at something that
does not ship, and conclusions about "the filter's probabilities" were drawn
from it anyway — including one drawn on 28 August, in this repo, from this gate.
Mechanism: a gate named after a concept (`belief calibration`) rather than
after the object it instantiates, so nobody re-checked which object that was.
*Guard:* **S1_PD** applies S1's identical threshold to `w3.BeliefPD` under
`FITTED_BELIEF`. It also fails (ECE 0.026, not monotone). S1 was **not**
repointed — its threshold is pre-registered, and quietly aiming a gate at a
different subject is indistinguishable from moving it until it agrees.

**10. Waited 97 minutes for a process that had done 0.3 seconds of work.**
A script was left running while other work proceeded. It had consumed **0.3 CPU
seconds in 97.8 minutes** and had no child processes: it was hung, not
computing. It was the leftover of an invocation that hit the Windows spawn
`RuntimeError` — `runner.run_jobs` called at module level with no
`if __name__ == "__main__":` guard — after which multiprocessing left
non-daemon threads alive and the interpreter never exited. The trap was already
documented in `sim/runner.py`; it was walked into an hour later in a new file.
Mechanism: elapsed wall time treated as evidence of work.
*Guard:* every script calling `run_jobs` has the guard. Before waiting on any
long job, check CPU, not the clock:
`powershell "Get-Process python | Select-Object Id, CPU, StartTime"`.
A Python process at ~0 CPU is hung. Measured runtime of the script in question:
**71 seconds.**

---

## Three vacuous gates (all of them, in the old suite)

Worth its own section, because it is the deepest lesson.

| Old gate | Why it could never fail |
|---|---|
| `assert violations == 0` | `live` already filtered `n < cap`; `chosen ⊆ live`. A tautology. |
| peak hours never violated | 3 slots/day made peak windows unrepresentable. |
| oracle approval ≈ 100% | True by construction, correct or not. |

**Every automatic guardrail in the suite passed by construction.**

The rule that came out of it, now in `docs/05_TEST_DESIGN.md`: a gate earns its
place only if you can name, in advance, a concrete broken implementation that
would make it fail. No mutant, no gate.

While building the *new* suite under that rule, the suite immediately caught
three defects in its own author's code — including one gate that was vacuous on
first run, because the mutant reset the same counter the check was reading.
