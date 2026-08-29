# 07 — AGENT BRIEF

**START HERE.** You are building `agent/`. The simulation is finished and
frozen; you do not need to understand it, only to call it. This page tells you
what to build, what to call, and what not to touch.

Deadline **5 September 2026**. Day-by-day plan: `04_BUILD_PLAN.md`.

---

### The three things most likely to cost you the project

Added 28 August 2026 after an outside audit of `docs/` against `sim/`. Each of
these is a mistake you can make while every test still passes.

1. **ONE BELIEF PER CUSTOMER, SHARED BY ALL `k` MANDATES.** Not one per
   mandate. That sharing *is* the cross-merchant moat. Build it per-mandate and
   you have silently built `solo_pop_pd` — the arm the moat is measured
   against — and you are **9.53 points worse** with nothing to tell you. §3.
2. **Two of the five Stage 0 rules have no working test** — the attempt cap
   and the pending notification. Do not claim either in the pitch, and do not
   treat the harness's counters for them as a spec you can port. §4.
3. **Do not say "within 4.4 points of a clairvoyant oracle."** The oracle gap
   is now that small, and this project's error 5 was *exactly* a near-zero
   oracle gap produced by a broken oracle. `02_RESULTS.md` under "Other
   established results".

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
| **degenerate mode** | The agent with its action space switched off: retry-only, deterministic diagnoser, no nudge/escalate/stop, no rail monitor. It reproduces `harness.run("solo_shared_pd", ...)` **bit-exactly**, which is what makes every agent number comparable to a gated one. Added 28 Aug 2026. |
| **full mode** | Degenerate plus the action space. The difference between the two is the agent's own contribution, isolated. |
| **the rail** | The payment network itself, as opposed to a customer's account. `w3.BeliefPD` has no representation of it — that is why the context layer exists. |

### Running anything

The `python` on PATH is an msys2 build with **no numpy**. Use:

```
/c/Users/tanma/AppData/Local/Programs/Python/Python312/python.exe
```

The repo root is `/c/codeing/razorpay/razorpay_handoff/pkg`, which is *not*
where the shell starts. Full environment notes are in `CLAUDE.md`.


### Running the LLM parts — what you need, and what you do not

| you want to | you need |
|---|---|
| `run_eval.py --llm --judge --replay` | **nothing.** The response caches in `agent/eval/_cache/` are **committed**, so a clean clone reproduces the whole eval offline, byte-identical, in 0.35s, for $0.00. |
| `run_eval.py --llm --judge` (live) | a Z.ai key in **`.env` at the repo root** as `ZAI_API_KEY=...`. `.env` is gitignored and is read automatically — you do not need to export anything. Without one every call falls back to the rule engine and the harness says so in capitals. |
| `batch_report.py --llm` | the same. Without a key the LLM arm is the deterministic arm under a different label, which the report states. |
| anything in `agent/eval/` | **PyYAML.** `pip install -r requirements.txt`. **The GATED suite still needs numpy alone** — `sim/gate.py` does not import `agent/` at all. |

**Cost, if you run it live:** ~$0.15 per full eval at the prices in
`01_FACTS.md`. The whole session that produced these numbers spent **$0.26**.

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

It asserts every constant and the construction recipe, and runs in under a
second. Two doc/code contradictions survived weeks in this repo before anyone
checked; this is the check.

⚠️ **Know its limit.** For the decision recipe it does *not* call
`harness.py`. It compares §3's recipe against a **hand transcription** of the
harness branch living inside `verify_brief.py:81-88`. If `harness.py`'s belief
branch changed, this script would keep passing. It is a doc-vs-copy-of-code
check. Flagged 28 Aug 2026; making it genuine needs the decision logic factored
out of `harness.run`, which is a frozen file.

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
they are judged deliverables, not housekeeping. `03_ERRORS.md` has **twenty-three**
entries with mechanisms and guards. **Open the pitch with them.** Errors 11-13
were found by an outside reader checking `docs/` against `sim/` on 28 Aug --
all three in the measuring apparatus, all three past a suite built to stop
exactly that. That is the strongest Failure-Recovery material in the repo.

---

## 2. The three-way split — this is the pitch line, keep it true in the code

- **The LLM decides *what* to do and explains *why*.**
- **The bandit policy decides *when*.**
- **The constraint layer decides *whether it is allowed*.**

An LLM must **never** be on the path that decides whether to debit a specific
customer at a specific moment. That is a deliberate architectural choice
(**ADR-005** — there is no ADR document; it is written out in full in
`00_HANDOFF.md`) and it is defensible under the "AI Judgment" criterion — but
*only*
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
    pop_info=True,          # INERT on BeliefPD -- see below. Harmless.
    **cfg,                  # stride=1, prior_w=12, prior_day0=8.0,
)                           # prior_floor=0.25
```

With the fitted `spend_beta=0.0` the `est_spend` line reduces to `pop_spend`,
but do not hardcode that — read it from the constant so a future refit
propagates instead of silently not applying.

⚠️ **`pop_info` does nothing on `BeliefPD`.** It is in the signature
(`w3.py:325`) and the body never reads it; `self.prof` is set unconditionally
at `w3.py:373`. It *is* load-bearing on the older `Belief` (`w3.py:164-168`,
`189-195`), which is where the parameter comes from. Pass it or don't — just
don't build a "no aggregate model" arm by flipping it and expect a different
filter. Found 28 Aug 2026; not fixed because `w3.py` is frozen.

### ⚠️ ONE BELIEF PER **CUSTOMER**, NOT PER MANDATE. THIS IS THE MOAT.

**This is the single easiest way to build the wrong thing, and the brief did
not say it until 28 August 2026.**

`solo_shared_pd` does not give each mandate its own filter. All `k` mandates of
one customer **share one `BeliefPD` object**, and every mandate's attempt
outcome is folded into that one object. See `harness.py:207-215`:

```python
collapse = policy in POOLED and not policy.startswith("solo_placebo")
if collapse:
    _shared = BC(est_sal, est_pay, cyc, days, est_spend=eff_spend, ...)
    beliefs = {id(m): _shared for m in mands}      # ONE object, k references
```

That is what "pooling" *is* in this codebase. The customer is the unit of
inference; the mandate is only the unit of action.

Build one belief per *mandate*, each seeing only its own attempts, and you have
built **`solo_pop_pd`** — which is the arm the moat is measured *against*. You
would ship the architecture minus its central claim and the number would be
**9.53 points worse** (gate S2a), and nothing in the suite or this brief would
tell you, because both policies exist and both run clean.

Concretely, for a customer with mandates `m1..mk`:

```python
b = w3.BeliefPD(...)                 # ONE per customer
per_mandate_belief = {m.id: b for m in mandates}    # all the same object

# on every outcome, for ANY mandate:
b.advance(day)                       # ONCE per day per CUSTOMER, not per mandate
b.observe(m.amount, success)         # ONCE -- every mandate already sees it
```

`advance()` and `observe()` are called **once per customer**, not once per
mandate. Calling them k times ages the belief k× too fast and silently
destroys it — `advance` has no guard against being called twice for a day
(`w3.py:400-409`).

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

⚠️ **That paragraph is true for three of the five counters, not all five.**
Corrected 28 August 2026, gate **M4B**:

| counter | independent detector? | test behind it |
|---|---|---|
| `cap` | yes, `harness.py:253` | **none** — M1 is VACUOUS, the cap never binds |
| `peak` | yes, `harness.py:256` | M2, fires 1119 |
| `lead` | yes, `harness.py:258` | M3, fires 1080 |
| `pending` | **NO** | **none** — M4 is vacuous, see below |
| `represent` | yes, `harness.py:262` | M5, but double-counts |

`pending` is the bad one. Its only detector is `if m["pend"] is not None` at
`harness.py:607`, and `live` at `harness.py:349-351` has *already* filtered
`m["pend"] is None`, so it can never fire. The M4 mutant passes because the
mutation branch increments `V.pending` **itself** (`harness.py:610-612`).
Measured on an instrumented copy: 1066 counted, 1066 self-written, **0**
independent. `represent`'s mutant does the same at `harness.py:333`, but there
the independent check still fires (304 of the 608), so M5 binds.

**What this means for you.** When you build the enforcement layer, `cap` and
`pending` have **no reference implementation you can check yourself against** —
the simulation's counters for those two have never been proven to work. Build
both enforcers, build the independent counter behind both, and write your own
test that puts an illegal action through and watches your counter move. Do not
assume the harness's counter is a spec you can port.

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

⚠️ **Do not put the NPCI attempt-cap OR the pending-notification compliance
claim in the pitch or the architecture doc.** Gate M1 is VACUOUS (the cap is
never the binding constraint at either operating point) and gate M4 is
vacuous-but-green (its mutant grades itself). **Two of the five Stage 0 rules
have no working test.** Peak-hour, notification-lead and Z9 re-presentation are
genuinely tested and are safe to claim. See `06_MODEL_CARD.md` §4 and
`sim/known_failures.txt` under `M4B`.

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
3. `03_ERRORS.md` — twenty-three errors with mechanisms. Pitch material.
4. `01_FACTS.md` — every external fact with its source tag. **Nothing outside
   this file is established**, and the legality of the cross-merchant moat is
   still `[GUESS]`.

---

## 8. THE AGENT EXISTS NOW. Added 28 August 2026.

`agent/` is built through the constraint layer, the action space and the context
layer. The LLM layer is **not** built. If you are the next session, this section
plus `06_MODEL_CARD.md` §6 is your starting point, and `NOTES.md` from
28 August has the full blow-by-blow.

### What is built

```
agent/
  ports.py            shared types. Imports no layer. `Diagnosis` has NO time field.
  state.py            per-mandate bookkeeping (the POLICY's view, not the gate's)
  loop.py             detect -> diagnose -> choose -> schedule -> enforce -> execute -> log
  batch.py            composition root: the only place that builds an executor AND a gate
  policy/             belief_book.py (ONE BeliefPD per CUSTOMER), timing.py (the index)
  constraints/        rules.py + stage0.py (enforce), auditor.py (independent recount)
  context/            rail_monitor.py - outage detection. NEW.
  execution/          sim_executor.py - the world, incl. OutageSchedule
  audit/              log.py - append-only JSONL, one row per event
  llm/                caseview.py (redaction boundary), fallback.py (deterministic),
                      governance.py, client.py (Z.ai transport), prompts.py
                      (versioned), model_diagnoser.py (the LLM overlay)
  eval/               golden_cases.yaml (40 + 7 taxonomy + 3 injection),
                      cases.py, injection.py, run_eval.py, _cache/
  batch_report.py     THE TRACK DELIVERABLE. Not batch.py, the composition root.
  tests/              _parallel.py + seven gates. See 06_MODEL_CARD.md §6b.
```

### The four things most likely to cost you the project, updated

1. **ONE BELIEF PER CUSTOMER, SHARED BY ALL k MANDATES.** Unchanged, still the
   most expensive available mistake. `agent/policy/belief_book.py` enforces it
   and `test_one_belief.py` asserts it.
2. **RUN EVERY BATCH THROUGH `agent/tests/_parallel.py`.** One process per run,
   `max_tasks_per_child=1`. Long-lived processes crash on this machine and the
   root cause is unknown. `06_MODEL_CARD.md` §6a has the evidence.
3. **Two of the five Stage 0 rules still have no working test in `sim/`** (the
   attempt cap and the pending notification). `agent/` now has its own working
   tests for both, written from the rule text in `01_FACTS.md` rather than
   ported from the harness - `test_stage0_enforces.py`. Those are the only
   working tests either rule has anywhere in this repo. **The pitch ban stands
   for the `sim/` claims.**
4. **The agent's headline is a CAPABILITY claim, not a recovery number.**
   `06_MODEL_CARD.md` §6d. Do not let it drift into "money recovered".

### Two hard requirements in the agent code

**The rail monitor requires `time_major=True`.** It keeps a rolling window and a
customer-major loop restarts the clock at t=0 for every customer, so nothing
prunes and it latches OUTAGE forever - it read 1.97% recovery that way, without
crashing. It now raises `NonMonotonicTime` instead. Error 14.

**Anything that drops a pending notification must emit
`NOTIFICATION_CANCELLED`.** Otherwise `auditor.py` correctly reports `pending`
violations that did not happen. `06_MODEL_CARD.md` §6c.

### THE LLM LAYER IS BUILT AND MEASURED. 29 August 2026.

```
python -m agent.batch_report --llm                      # THE DELIVERABLE
python agent/eval/run_eval.py --llm --judge --replay    # the eval, offline, $0
```

`agent/llm/client.py` (Z.ai transport, cache, hard budget, **never raises**),
`prompts.py` (versioned - **the ID is part of the cache key**, so a prompt edit
misses the cache and shows as a diff), `model_diagnoser.py` (an OVERLAY, never a
dependency) and `agent/eval/` (case loader, 40 registered + 7 taxonomy + 3
injection cases, judge, harness).

**Diagnoser `glm-5.3-flash`. Judge `glm-5.3`.** `run_eval.py --judge` refuses to
run if the two SKU names are equal, so Flash never grades itself. **$0.26 spent.**

| | ambiguous (21) | clean (19) | terminal (4) |
|---|---|---|---|
| `RuleBasedDiagnoser` | **9/21** | 19/19 | **0/4** |
| `glm-5.3-flash` | **10/21** | 13/19 | **4/4** |

**The clean column is the floor, not the result** - it is what thirty lines of
if-else are for. Where the model earns its place is **terminal decline codes**:
a frozen account (`ZX`, `YE`) or a revoked mandate (`VI`, `VD`), where no retry
can ever succeed and `w3.index_score` has no slot for the fact. **It does not
move the batch money** - 94.33% against the deterministic 94.36%.

**Full tables and every caveat: `02_RESULTS.md`. Summary and the three things
that bound every LLM number: `06_MODEL_CARD.md` section 7.**

### THE FOUR THINGS MOST LIKELY TO MISLEAD YOU ABOUT THESE NUMBERS

1. **`reasoning_effort=low`, and it is UNSWEPT.** Thinking cannot be disabled on
   these SKUs (the API answers code `1210`); at the default the diagnoser emitted
   1,596 completion tokens for an answer whose schema holds about eighty, and
   timed out. **Every score is for that setting and 10/21 may be a floor.**
   First thing to sweep.
2. **The LLM cannot be called at every decision point.** The loop asks for a
   diagnosis once per live mandate per decision hour - **119,667 times** over a
   four-population batch. It runs under a hard per-run cap on network calls, so
   **the batch's LLM arm is 95% deterministic.** Never call it "the LLM's
   number". Error 22.
3. **`WAIT` was cut on 29 August and the premise was wrong.** It was unreachable
   in the rule engine and was the LLM's *most-used* answer; removing it moved the
   ambiguous score from 4/21 to 10/21. **An action space is part of the model,
   not part of the plumbing.** Error 20. The action space is now four: RETRY,
   NUDGE, ESCALATE, STOP.
4. **Author agreement is not accuracy.** The cases, the registered answers, the
   rubric and the deterministic baseline share one author. **19 judge-vs-author
   disagreements await human adjudication** and that is the validation step.

### Two hard requirements that have not changed

`Diagnosis` still has **no temporal field** -
`agent/eval/injection.py:diagnosis_has_temporal_field()` asserts it by
inspecting the type, so an injected "retry at 11am" has nowhere to land. And the
**judge must stay a different SKU from the diagnoser**: same-model-grading-itself
is the same-party failure this project has now hit twenty-three times.

**The deterministic fallback stays the default and produces the gated number.**
The LLM is an overlay measured against it. A headline that needs an API key is
not reproducible and the numbers rule forbids quoting it - which is why every
response is cached by `(model, prompt_id, case_hash)` and `--replay` reproduces
the whole eval offline, byte-identical, in 0.35 seconds, for **$0.00**.
