# 03 — THE SIX ERRORS

Every one of these made the project look **better** than it was. Read them
before you optimise anything, because they are the shapes you will reintroduce.

They are also the strongest asset for the panel, who explicitly ask what broke
and how you recovered.

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
