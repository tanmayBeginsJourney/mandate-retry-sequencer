# 07 — AGENT BRIEF

**START HERE.** You are building `agent/`. The simulation is finished and
frozen; you do not need to understand it, only to call it. This page tells you
what to build, what to call, and what not to touch.

Deadline **5 September 2026**. Day-by-day plan: `04_BUILD_PLAN.md`.

---

## 0. Vocabulary, and how to run anything

Every term below is used across `docs/` without definition. Read this once.

| term | what it means here |
|---|---|
| **mandate** | A standing authorisation to debit one customer for one merchant, e.g. a subscription. A customer has `k` of them (we use 5). |
| **billing cycle** | 30 days. A mandate is due once per cycle. A cycle *closes* when the next one opens, so every cycle resolves inside the horizon. |
| **`cycle_open` / `cycle_close`** | The day a mandate's current cycle starts / the day it ends. An attempt scheduled at or after `cycle_close` is not schedulable. |
| **attempt / presentation** | One try at debiting. NPCI permits 1 presentation + 3 retries = **4 per mandate per cycle** (`w3.NPCI_MAX`). |
| **Z9** | The decline code for **insufficient funds** — nearly all real failures. A Z9 may *not* be retried under the old pre-debit notification; a *technical* decline may. |
| **Stage 0** | Our name for the **five hard regulatory constraints** every money action must satisfy: attempt cap, peak-hour ban, ≥24h notification lead, one pending notification per mandate, and no Z9 re-presentation. Enumerated with sources in §4. |
| **`payday_err`** | How wrong our payday estimate is, in days. `payday_err=7` means the estimate is uniform in ±7 days of the truth. **The single most important knob** — the whole headline is conditional on it. See `06_MODEL_CARD.md` §2. |
| **`pop_spend`** | The *population* monthly spend rate as a fraction of salary, given to every belief policy. The suite uses **1.05**; `harness.run` defaults to 0.80, so **always pass it explicitly**. |
| **`pop`** | A population: a list of customer dicts. Built by `w3.make_pop` (below). |
| **the metric** | Billing cycles collected ÷ cycles due, over the full horizon. A dead mandate forfeits its remaining cycles, which prices mandate death without an invented LTV constant. |
| **oracle** | A clairvoyant policy with the true balance and true future. The upper bound; must weakly dominate everything (gate T1). |

### Running anything

The `python` on PATH is an msys2 build with **no numpy**. Use:

```
/c/Users/tanma/AppData/Local/Programs/Python/Python312/python.exe
```

The repo root is `/c/codeing/razorpay/razorpay_handoff/pkg`, which is *not*
where the shell starts. Full environment notes are in `CLAUDE.md`.

### Building a population

```python
import numpy as np, w3
pop = w3.make_pop(
    n=100,                    # customers
    k=5,                      # mandates per customer
    rng=np.random.default_rng(700),
    days=120,                 # horizon
    cycle_days=30,
    spend=1.05,               # per-customer spend rate, mean
    payday_day0_frac=0.60,    # fraction paid on day 0 of the cycle
    irregular_frac=0.0,       # fraction with income spread over 6 credits
)
```

`make_pop` is deterministic in its `rng`, so a population is fully described by
those arguments. `sim/runner.py` relies on that: it ships a 5-to-7 number
*spec* to worker processes rather than pickling the population.

**Verify this page against the code before trusting it:**

```bash
python sim/verify_brief.py
```

It asserts every constant, the construction recipe, and that the decision recipe
in §3 reproduces `harness.py`'s own branch bit-for-bit. It runs in under a
second. Two doc/code contradictions survived weeks in this repo before anyone
checked; this is the check.

---

## 1. What the track requires, verbatim

Razorpay's stated bar for Track 3 (AI Revenue Recovery), quoted exactly:

> "Build an agent that detects revenue at risk, determines the right
> intervention, and executes a bounded recovery workflow."

> "Don't just identify the problem. Show measured money recovered across a
> batch, with compliant escalation, stopping rules, and an audit trail."

Deliverables: a **public GitHub repo**, a **5-minute pitch video**, and an
**architecture document**. Applications close 5 September 2026.

Judged on, among other things — `[REPORTED]`, see `01_FACTS.md`:
- **AI Judgment**: whether AI tools, LLMs or agents were applied *appropriately*
  instead of forcing unnecessary tech stacks.
- **Failure Recovery**: how the applicant identified system failures at runtime
  and engineered graceful fallbacks.

Applicants are explicitly asked to **explain what broke during development and
how they recovered**. That is why `NOTES.md` and `03_ERRORS.md` exist and why
they are judged deliverables, not housekeeping. `03_ERRORS.md` has ten entries
with mechanisms and guards. **Open the pitch with them.**

---

## 2. The three-way split — this is the pitch line, keep it true in the code

- **The LLM decides *what* to do and explains *why*.**
- **The bandit policy decides *when*.**
- **The constraint layer decides *whether it is allowed*.**

An LLM must **never** be on the path that decides whether to debit a specific
customer at a specific moment. That is a deliberate architectural choice
(ADR-005) and it is defensible under the "AI Judgment" criterion — but *only*
because there is a real agent layer doing real work elsewhere. **Do not quietly
delete either half.** An agent that is only a constraint checker fails the
track; an agent that lets an LLM pick debit times fails the architecture.

Concretely:

| Layer | Owns | Implementation |
|---|---|---|
| LLM | root-cause diagnosis, intervention choice (retry / nudge / partial / escalate / stop), human-readable justification per money action | you are building this |
| Policy | which mandate, which day | `w3.BeliefPD` + `w3.index_score` — **frozen, wire it in** |
| Constraints | whether the chosen action is legal | Stage 0 — **see §4, this is the first real task** |

Governance constraint, enforced in code: merchant-facing explanations must not
disclose the customer's financial state. Say *"our model scores this window
highest"*, never *"their balance has never recovered before the 3rd"*.

---

## 3. The exact interface

Everything below is in `sim/`, importable after `sys.path.insert(0, "sim")`.

### The probability engine

⚠️ **`w3.BeliefPD(..., **w3.FITTED_BELIEF)` RAISES.** The constant carries one
key, `spend_beta`, that belongs to the harness rather than to the belief: the
harness uses it to derive `est_spend`. Split it out. This is the whole recipe:

```python
import numpy as np, w3

cfg  = dict(w3.FITTED_BELIEF)
beta = cfg.pop("spend_beta")            # 0.0 -- harness's, not BeliefPD's
est_spend = pop_spend * (1 + (n_mandates - 1) * beta)     # -> pop_spend

b = w3.BeliefPD(
    est_salary,             # float: noisy salary estimate for this customer
    est_payday,             # int:   noisy payday estimate, day-of-cycle
    30,                     # cycle_days
    days,                   # horizon, e.g. 120
    est_spend=est_spend,
    pop_info=True,
    **cfg,                  # stride=1, prior_w=12, prior_day0=8.0,
)                           # prior_floor=0.25
```

With the fitted `spend_beta=0.0` the `est_spend` line reduces to `pop_spend`,
but do not hardcode that — read it from the constant so a future refit
propagates instead of silently not applying.

**Methods you will use:**

| call | returns |
|---|---|
| `b.advance(day)` | none — steps the belief forward one day. Call once per day, at the start of the day. |
| `b.observe(amount, success)` | none — folds in one attempt outcome. This is the censored measurement: a success at ₹X proves balance ≥ X, a failure proves < X. |
| `b.forecast(day, horizon_days)` | `[(day+1, P), (day+2, P), …]` — the posterior for each future day, stopping at the horizon. Use `harness.LOOKAHEAD_DAYS` (=12). |
| `b.p_success(amount, P=None)` | float — P(a debit of `amount` succeeds). Pass a `P` from `forecast()` to ask about a **future** day; omit it for today. |
| `b.expected()` | float — expected balance. |
| `b.posterior_summary()` | `(entropy, top_hypothesis_weight, expected_balance)` — the filter's own statement of how sure it is about payday. **This is your uncertainty signal for the LLM layer and for the UI.** |

`Belief` (no `PD`) is the older point-estimate filter. **Do not use it.**

### Getting a scheduling decision

There is **no `decide()` function** — the logic lives inside `harness.run`'s
main loop. You have two options.

**Option A — call the whole simulation** (fastest way to a batch number):

```python
import harness, w3
result = harness.run("solo_shared_pd", pop, seed,
                     payday_err=7, pop_spend=1.05,
                     bcfg=w3.FITTED_BELIEF)
# -> dict with keys:
#    cycle_rec, approval, survival, att_per_cycle, starvation,
#    cycles_due, violations, vdetail, calib, ml_rows
```

`vdetail` is `{cap, peak, lead, pending, represent}` — the Stage 0 violation
counts. `cycle_rec` is the headline metric.

**Option B — reproduce the decision for one mandate** (what the agent needs).
This is `sim/harness.py`'s belief branch, reduced to its essentials. Copy the
shape exactly; the details below are the ones that are easy to get wrong.

Variables you supply: `day` (today, integer day index), `amount` (₹),
`cycle_close` (day this mandate's cycle ends), `attempts_used` (how many
attempts this mandate has already made *this cycle*), `now_t` (current absolute
hour = `day * 24 + hour`), and `b`, the belief built above.

```python
LOOK = harness.LOOKAHEAD_DAYS            # 12
cap  = w3.NPCI_MAX                       # 4

fc = b.forecast(day, LOOK)               # [(dd, P), ...], dd from day+1
cand = [(dd, P) for dd, P in fc if dd < cycle_close]
if not cand or cand[0][0] >= cycle_close:
    return None                          # nothing schedulable this cycle

tgt_day, p_tgt = cand[0]                 # always day+1
p_now  = b.p_success(amount, p_tgt)
later  = [b.p_success(amount, P) for dd, P in cand[1:]]
p_later = max(later, default=0.0) if (cap - attempts_used) > 1 else 0.0

score = w3.index_score(p_now, p_later, amount)   # discount defaults to 0.92
if score <= 0:
    return None                          # waiting beats attempting
target_time = harness.earliest_legal(tgt_day, now_t + w3.HOURS)
```

**The five things that are easy to get wrong here:**

1. **`p_success(amount, P)` takes a posterior for a FUTURE day.** You are asking
   "will this succeed on day *d*", not "would it succeed today". Passing `None`
   silently asks the wrong question.
2. **`p_later` is zero on the last attempt.** When `cap - attempts_used == 1`
   there is no later opportunity, so waiting has no option value.
3. **`score <= 0` means wait, not fail.** A negative index says the future looks
   better than now. That is the Whittle structure doing its job.
4. **`earliest_legal(day, min_t)`** returns the first non-peak hour on `day` at
   or after `min_t`, or `None`. Passing `now_t + w3.HOURS` is what enforces the
   ≥24h notification lead. Peak windows are 10:00–13:00 and 17:00–21:30.
5. **`advance(day)` once per day, `observe()` on every outcome.** Skipping
   `advance` silently freezes the belief; double-calling it silently ages it.

### Other constants you will need

| name | value | meaning |
|---|---|---|
| `w3.NPCI_MAX` | 4 | attempts per mandate per billing cycle |
| `w3.HOURS` | 24 | hours per day; time is an integer hour index |
| `w3.PEAK` | `{10,11,12,17,18,19,20,21}` | hours when execution is not permitted |
| `w3.DECISION_HOUR` | 8 | when the scheduler runs |
| `harness.LOOKAHEAD_DAYS` | 12 | forecast horizon |
| `harness.P_TECH` | 0.008 | technical decline rate |
| `w3.Z9 / TECH / OK` | strings | decline codes. **Z9 = insufficient funds** and needs a fresh notification; TECH may auto-represent. |

---

## 4. THE FIRST REAL PRODUCT TASK: Stage 0 counts, it does not prevent

**Read this carefully. It is the single most important thing on this page.**

In `sim/harness.py`, Stage 0 is a **measurement instrument, not an enforcement
layer.** At dispatch the harness independently re-derives whether the action was
legal and *increments a counter*:

```python
if ledger[lk] >= cap:
    V.cap += 1          # counts the violation. Does NOT stop the dispatch.
if hour in PEAK:
    V.peak += 1
if notif_t is not None and target_t - notif_t < HOURS:
    V.lead += 1
```

That design is deliberate and correct **for the simulation**: it is what makes
the violation counters falsifiable. A policy that filters its own choices cannot
drive the counter to zero by construction, because the counter is computed by
different code from a different source of truth. Three gates in the old suite
were vacuous precisely because they checked a condition the policy had already
guaranteed — see `03_ERRORS.md`, "Three vacuous gates".

**But a product cannot ship a constraint layer that only takes notes.** Your
first real task is to build Stage 0 as **enforced middleware**: every money
action passes through it, and it **refuses** illegal ones rather than recording
them.

The five constraints it must enforce — all `[REPORTED]`, see `01_FACTS.md`:

| | rule |
|---|---|
| `cap` | ≤ 4 attempts per mandate per billing cycle (1 presentation + 3 retries) |
| `peak` | no execution in 10:00–13:00 or 17:00–21:30 |
| `lead` | ≥ 24h between pre-debit notification and execution |
| `pending` | at most one pending notification per mandate at a time |
| `represent` | a **Z9** (insufficient funds) decline may **not** be re-presented under the old notification; a **technical** decline may |

**Keep both halves.** Enforcement in the agent, *and* the independent counter
behind it, so you can still prove the enforcement works rather than asserting
it. An enforcement layer with no independent check is exactly the vacuous-gate
shape this project has hit three times.

⚠️ **Do not put the NPCI attempt-cap compliance claim in the pitch or the
architecture doc.** Gate M1 is VACUOUS: the cap counter has no working test
behind it, because at both operating points the cap is never the binding
constraint. See `06_MODEL_CARD.md` §4.

---

## 5. The freeze

**Do not change `sim/w3.py`, `sim/harness.py`, or the fitted constants
(`w3.FITTED_BELIEF`, the `0.92` discount) before 5 September without explicit
approval from Tanmay.** Tagged `model-frozen`.

Not to tidy them, not to squeeze another point out of them, not because a better
idea turned up. The model went through four significant corrections in a single
day; each was worth making, and none is worth making on 4 September with no time
to re-run the suite.

**Still open and still allowed:** `agent/`, `docs/`, `NOTES.md`, the pitch, the
architecture doc. `sim/tests.py` may gain gates, but **no gate's threshold may
move**.

If you believe something in `sim/` is wrong, write it in `NOTES.md` and ask.
That is how the last four defects were found.

---

## 6. What "done" looks like on 5 September

- [ ] Public repo, commits visible across the whole period
- [ ] Agent runs end to end over a batch of synthetic merchants
- [ ] **One number: money recovered, with `payday_wait` printed beside it.**
      Never show our number alone — `payday_wait` is what a good rival team
      builds in an afternoon, and at ±1 day it *beats* us
- [ ] Audit log: every money action, with reason, constraint check, outcome
- [ ] Stopping rules explicit and demonstrable
- [ ] One failure handled gracefully, on camera
- [ ] Architecture doc, one page
- [ ] 5-minute pitch video, opening with the errors
- [ ] `NOTES.md` full of real mess

## 7. Read next, in this order

1. `06_MODEL_CARD.md` — what ships, what it is worth, what it was never tested
   on. Especially §3, before you quote any number to anyone.
2. `CLAUDE.md` — the ten hard rules, the environment, the numbers rule.
3. `03_ERRORS.md` — ten errors with mechanisms. Pitch material.
4. `01_FACTS.md` — every external fact with its source tag. **Nothing outside
   this file is established**, and the legality of the cross-merchant moat is
   still `[GUESS]`.
