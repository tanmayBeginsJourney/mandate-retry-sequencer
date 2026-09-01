# CLAUDE.md — read this fully before your first action

You are joining a project with a large body of measured work behind it. **You
do not have that context.** This file, plus `docs/`, is all of it.

**`agent/` is COMPLETE** — the constraint layer, the action space, the context
layer, the LLM layer, the eval, the batch report, and a second executor backend
against Razorpay's real API are all built and all measured. Do not rebuild what
is already there.

**The live work is the WORLD.** `docs/04_BUILD_PLAN.md` opens with **THE
QUEUE** — an ordered list of what to build next. Start there. `sim/` is no
longer frozen; see below.

**Read `docs/00_HANDOFF.md` first.** It is one page and it carries the state,
the four commands that matter, and the three traps a fresh session otherwise
walks into.

⚠️ **Two things in older docs are now FALSE and you will meet them:**
"two of the five Stage 0 rules have no working test" (**fixed 30 August** —
M1 and M4B are repaired, the suite has 0 vacuous gates) and "the model is
frozen" (**lifted 30 August**).

After `00_HANDOFF.md` and the queue, read in this order:
`docs/07_AGENT_BRIEF.md` (the interface — you do not need to read `sim/` to use
it), `docs/06_MODEL_CARD.md` (what ships and what it is worth),
`docs/01_FACTS.md` (every external claim, source-tagged),
`docs/02_RESULTS.md` (every number with its bias analysis) and
`docs/03_ERRORS.md`.

✅ **`README.md` and `docs/index.html` were rewritten on 29 August 2026 and are
no longer drafts.** They are the two judge-facing artifacts. If they disagree
with `docs/`, `docs/` still wins and the disagreement is a bug in the rewrite —
fix it rather than leaving both.

⚠️ **The headline is conditional on `pop_spend` as well as on `payday_err`.**
**Restated 1 September 2026 on the canonical world; the +3.52 -> +36.48 range
and the +6.36 at `pop_spend=0.80` are superseded.** `pop_spend` is now one minus
the RBI household saving rate, scored across the region **[0.80, 0.93]** with no
point declared, and the uplift over `payday_wait` runs **+0.93 -> +9.08** across
it. Below 0.90 the world carries too few at-risk cycles to measure a difference
at all -- two of them at 0.80, across a thousand customers. The batch headline
is **99.38% / 90.29% / +9.08 pts (2 SE 1.84) / Rs 7,511,500**.
`grep -rn "40.30\|36.48\|98.01" README.md docs/` finds any survivor, and
`py -3.12 sim/verify_docs.py` fails on one that is not marked as withdrawn.

⚠️ **AND THE AGENT LOSES TO A STEELMANNED FIXED SCHEDULE BELOW +/-7 DAYS.**
`payday_wait` is not a strong baseline. Against `[1,7]` -- two attempts at
frozen offsets from the same noisy payday estimate -- the agent is behind by
9.17 points at `payday_err=1`, 7.83 at +/-3 and 6.14 at +/-5, level at +/-7, and
ahead only from between +/-7 and +/-10 upward. Real payday uncertainty in India
is unmeasured and the payroll evidence points at the losing side. Do not quote
the `payday_wait` margin without this beside it.
`py -3.12 agent/tests/test_steelman_schedule.py`.

⚠️ **SUPERSEDED 1 September 2026 (W24).** The payday prior was refitted on
the canonical world (`prior_w` 9 -> 5, `prior_floor` 0.5 -> 0.1) and the
mandate's continuation value was added to the objective (`cycle_value=0.6`).
**The crossover is now at ±5, not between ±7 and ±10.** Held-out margins
against `[1,7]`: −1.16 at ±1, −0.33 at ±3, +1.15 at ±5, +3.55 at ±7,
+23.83 at ±10, +34.41 at ±14. The figures above are the PRE-REPAIR agent and
are kept as the record. `py -3.12 agent/tests/test_steelman_schedule.py`.

⚠️ **The README's Limitations section lists ONLY what cannot be fixed from
here** — unobtainable data, unpublished decline rates, unresolved law,
structurally-unfittable calibration gates, compute-bound sample size. Open work
goes in the queue, never there. A limitation the project could fix and has not
reads as an excuse, and there were five of them until 30 August.

---

## DO NOT MANAGE TANMAY'S CALENDAR. Added 30 August 2026.

**You do not get a vote on what fits in the time available.** This project went
from nothing to a complete, measured agent in under three days. Any estimate you
form about what is "too big for the time left" is calibrated on a rate of work
you have not observed and cannot see.

Concretely, you may not:

- refuse, defer, or down-rank a piece of work because of how close the deadline is
- describe something as "a Day-1 decision, not a Day-25 one", or any variant
- pad an answer with time estimates that were not asked for
- treat the freeze as a reason to stop *thinking* about a change — it governs
  what gets committed to `sim/`, not what may be proposed or specced

**Answer the WHAT. Tanmay owns the WHEN.** If a piece of work is genuinely
large, say what it involves and what it costs in re-runs, and then let him
decide. "This is a big change and here is exactly how big" is useful. "There
isn't time for this" is not, and it has been wrong every time it has been said
here.

The question you should be optimising is **"does this make the project more
interesting to a judge?"** — not "does this fit before Friday."

The single most important thing about this project:

> **It has found thirty-two significant errors in its own work. Almost every one
> made the project look BETTER than it was.** That is not coincidence. It is
> what happens when the same party builds the measuring stick and the thing
> being measured. You are now that party. Behave accordingly.

**And the second most important thing, added 28 August 2026.** Errors 11–13
were found by an outside reader who was handed `docs/`, told to write down what
they believed, and then told to check it against `sim/`. All three were in the
*measuring* apparatus — a mutation test that graded itself, a fit script that
cannot produce the constant it documents, and a byte-lock that does not cover
the shipping configuration. Ten prior errors, a mutation-testing rule, a
pre-registration habit and a doc/code contract checker did not catch any of
them.

> **Self-audit has a floor.** You check results against tests and never check
> tests against a stranger. If you have time for one quality activity this
> week, it is another outside read of `docs/` against `sim/` — not another
> sweep.

---

## THE FREEZE IS LIFTED — 30 August 2026

**`sim/` is no longer frozen. Nothing in this repository is.** The freeze was
declared on 28 August on the assumption that there would be no time for further
model work. That assumption is withdrawn: the world model is now the main line
of work, and `docs/04_BUILD_PLAN.md` (World v2, W0-W5) is the spec.

Tag `model-frozen` still marks the 28 August state and is still the reference
point for "what the reported numbers were measured on". **When you change
`sim/w3.py`, `sim/harness.py` or a fitted constant, every number measured
against them is stale until re-run** — that is a cost to budget, not a reason
not to do it.

**What the freeze got right, and what survives it as ordinary discipline:**

- **Re-run before you re-quote.** A changed model invalidates every table that
  depended on it. `sim/t9_reference.py --recapture` is a deliberate
  re-baseline and it prints the full field-level diff before writing; paste
  that diff into `NOTES.md`.
- **One change at a time, measured.** The model went through four significant
  corrections in a single day because they were made together and untangled
  afterwards.
- **Do not fit a constant on the evaluation set.** That is error 8 and it is
  the reason the 0.92 discount was never fitted.
- **A big improvement is a defect until proven otherwise** (rule 3 below).

## Hard rules. These are not preferences.

### 1. Never weaken a test to make it pass
If a test fails, the code is wrong until proven otherwise. You may not delete a
gate, loosen a threshold, add a special case, or mark a test skipped. If you
believe a test is genuinely wrong, **stop and ask the human**. Write the
reasoning in `NOTES.md` first.

The test suite has already caught three defects in its own author's code. It is
the most valuable asset in the repo. Treat it as read-mostly.

**1a. A mutant may create illegal state and nothing else.** Added 28 August
2026 after error 11. If a `mutate == ...` branch increments a violation
counter, the gate that reads that counter is grading the mutant, not the
harness — it passes by construction. Gate **M4B** parses `sim/harness.py` and
fails if any `V.<field> += 1` sits inside a mutation branch.

✅ **FIXED 30 August 2026, and M4B is green.** `mutate="pending"` now drops the
pending filter in `live` so the mandate receives a second notification and the
harness's own check counts it; `mutate="represent"` no longer double-writes.
M1 was vacuous for a different reason — the cap never bound — and now runs at
`cap_override=2`. **Both Stage 0 rules that had no working test now have one.**
**Never make M4B green by exempting a mutant or by narrowing what it looks
at** — the only legitimate repair is the one taken: make the mutant create
state and let independent code notice.

### 2. Never report a number without stating how it could be wrong
Before presenting any simulation result, state:
- the experimental design (n, seeds, parameters, horizon)
- at least one concrete way the design could bias toward the answer we want
- the oracle / upper-bound figure alongside it

A result reported without its bias analysis is not a result.

### 3. Treat a large improvement as a bug until proven otherwise
The published industry benchmark for retry optimisation is a **6–8% uplift**.
Anything far outside that range is a defect until you have ruled out a defect.
This rule has already caught two errors. Do not explain a big number away with a
narrative — investigate it.

### 4. Every factual claim needs a source tag
Use these tags, and only these:
- `[VERIFIED]` — read directly from a primary source, link included
- `[REPORTED]` — secondary source, link included
- `[GUESS]` — our inference, no source. Must say so.
- `[RETRACTED]` — previously believed, now known false. See `docs/01_FACTS.md`.

An untagged factual claim is a rumour. Do not put rumours in code comments,
docs, the pitch, or the architecture document.

### 5. Do not invent constants
If you need a number that isn't in `docs/01_FACTS.md`, either derive it
transparently, make it a swept parameter, or ask. A previous version of this
project had a hardcoded `0.92` discount and an invented `6×` LTV multiplier
sitting directly on the headline result, with no sensitivity analysis.

**Corrected 28 August 2026 — "both are gone" was false for two years of this
file's life.** The 6× LTV multiplier was still live in `w3.index_score` until
it was swept, found to be a **complete no-op for every policy**, and removed.
The 0.92 discount is **still live** and is not a no-op — it multiplies
`p_later`, so it changes the sign of the index. It has now been swept (item A3
in `05_TEST_DESIGN.md`, declared at project start and never done).

**Corrected again 28 August 2026, later the same day.** The A3 sweep that
produced "**78.7%–83.1%**" was run on the **unfitted** filter, which is not
what ships. On the shipping configuration the spread across discount 0.80–1.00
is **88.7%–95.6%, i.e. ~7 points, not ~4**. The 0.90–0.96 plateau does survive
(94.25–95.57), so the constant is not perched on a spike — but quote the
fitted range, and say which filter it came from. Table in `02_RESULTS.md`.
Do not add new constants.

### 6. Never quote retired numbers
`41.7% → 76.3%` is **dead**. So is the `+5.4 pts` pooling figure, the `+1.5–2.1
pts` coordination figure, and everything in `legacy/`. They came from a
simulation with three vacuous gates and a broken oracle. If you see them
anywhere, they are stale text, not evidence. Current numbers live only in
`docs/02_RESULTS.md`.

### 7. `legacy/` is frozen
`legacy/` exists as a regression reference and as evidence for the pitch. **Do
not import from it, extend it, or copy patterns out of it.** It contains known
defects, documented in `docs/03_ERRORS.md`. Reading it to understand a past
error is fine. Building on it is not.

### 8. Append to `NOTES.md`, never rewrite it
`NOTES.md` is the decision log. Every non-obvious decision, every surprise,
every thing that broke goes in, with the date. **This is a judged deliverable** —
the panel explicitly asks what broke and how you recovered. Do not tidy it up.
The mess is the point.

### 9. When uncertain, ask. Do not infer and proceed.
You are missing the research conversation. If a design question feels
under-specified, it probably is, and the human has the context. Ask a short,
specific question. Do not guess and build on the guess.

### 10. Say "I don't know"
If you cannot verify something, say so plainly. Do not produce a confident
answer to compensate. This project's failure mode is confident wrongness, not
hesitancy.

---

## What we are building

**Track 3 of the Razorpay AI Buildathon: AI Revenue Recovery.** Deadline for
applications: **5 September 2026**. Deliverables: a public GitHub repo, a
5-minute pitch video, and an architecture document.

Razorpay's stated bar for this track, verbatim:

> "Build an agent that detects revenue at risk, determines the right
> intervention, and executes a bounded recovery workflow."
> "Don't just identify the problem. Show measured money recovered across a
> batch, with compliant escalation, stopping rules, and an audit trail."

**We are building an agent, not a research paper.** The research is finished and
is evidence. If a task does not move us toward a running agent with an audit
trail and a measured batch result, it is out of scope this week.

### The system in one paragraph
Indian subscription debits (UPI AutoPay) fail ~70% of the time, almost always
because the account is empty at the moment of the charge. Each failed debit is a
*measurement* of the customer's balance. We maintain a probability distribution
over the balance AND over the customer's salary credit date, updated by those
censored observations, and schedule attempts when money is likely to be there —
inside NPCI's constraints. An LLM layer diagnoses root cause, chooses among
interventions, and writes a human-readable justification for every money action.

### Division of labour — this is the pitch line, keep it true
- **The LLM decides *what* to do and explains *why*.**
- **The belief filter and its index rule decide *when*.**
- **The constraint layer decides *whether it is allowed*.**

An LLM must never be on the path that decides whether to debit a specific
customer at a specific moment. That is a deliberate architectural choice
(**ADR-005** — there is no ADR document; it is written out in full in
`docs/00_HANDOFF.md`) and it is defensible under the "AI Judgment" criterion —
but only because there IS a real agent layer elsewhere. Do not quietly delete
either half.

---

## Repo layout

```
CLAUDE.md              this file
NOTES.md               append-only decision + failure log (judged deliverable)
docs/
  00_HANDOFF.md        state of the project, what is decided, what is open
                       (start at 07_AGENT_BRIEF.md if you are building the agent)
  01_FACTS.md          every external fact, with source and confidence
  02_RESULTS.md        gated simulation results. See also 06_MODEL_CARD.md.
  03_ERRORS.md         THIRTY-TWO errors, with mechanism + guard. Pitch material.
  04_BUILD_PLAN.md     what is left, dated
  05_TEST_DESIGN.md    test philosophy, written BEFORE the harness on purpose
  06_MODEL_CARD.md     WHAT SHIPS. Read this before touching sim/ or agent/.
  07_AGENT_BRIEF.md    START HERE if you are building the agent.
  08_ARCHITECTURE.md   THE ARCHITECTURE DOCUMENT. A judged deliverable.
                       One page: layers, seams, the decision rule, and what
                       the headline is conditional on.
  index.html           the public page. Static, GitHub Pages from /docs.
  data/scenarios.json  every scenario the page shows, pre-computed.
sim/
  w3.py                world + belief filters + FITTED_BELIEF
  harness.py           policies, Stage 0 violation counters, run()
  tests.py             the 27-gate suite. Tripwired: see "Before you commit".
  gate.py              runs tests.py, decides if a commit is allowed
  runner.py            parallel driver (spawn-safe). Read its docstring first.
  t9_reference.py      captures/checks sim/t9_reference.json (gate T9)
  known_failures.txt   the gates allowed to be red, each with a written reason
  fit_belief.py        robust fit; selects a different config than shipping
  fitted_belief.json   committed record of that selection mismatch
  fair_audit.py        generalisation audit of the fitted prior (~71s)
  headline.py          the conditional headline table vs payday_wait
  verify_brief.py      asserts docs/07_AGENT_BRIEF.md matches the code
  stress_day0.py       stress test of the prior_day0 constant
  ml_study.py          ML baseline + 6-world misspecification study
  mlfeat.py            ML features. Read its leak argument before editing.
  mlmodel.py           loads the trained GBDT for ml_index / ml_index_pd
  ml_diagnose.py       leak/mismatch diagnostics for the ML arm
  ml_artifacts/        GITIGNORED. Trained models + study outputs.
  exp_main.py exp_pd.py calib2.py   older one-off experiment scripts
legacy/                FROZEN. Known-defective. Do not build on.
logs/                  raw output from prior runs
README.md              the front door. It SHOWS real command output rather
                       than describing it, so it is long on purpose. (This
                       line said "under 150 lines, on purpose" until 30 Aug
                       2026, by which point the README was 454. A line count
                       in a document is a staleness generator; it is gone.)
scripts/
  install-hooks.sh pre-commit pre-push day-start.sh
  build_page_data.py   pre-computes docs/data/scenarios.json for the page
  prove_stage0_refuses.py   Stage 0 refusing a real Razorpay debit, no key
agent/                 THE PRODUCT. Complete: every layer built and measured.
  demo.py              run it end to end: `python -m agent.demo`
  batch_report.py      THE TRACK DELIVERABLE. Not batch.py.
  ports.py             shared vocabulary. Imports NOTHING from agent/.
                       `Diagnosis` has NO time field, on purpose (ADR-005).
                       Also holds the Razorpay reason -> family map: agent/llm
                       may not import agent.execution, so it is the only
                       lawful home. See error 24.
  loop.py batch.py     the recovery loop; batch.py is the composition root
                       and the ONE place the executor backend is chosen
  policy/              ONE BeliefPD per CUSTOMER + the index. Wraps sim/.
  constraints/         Stage 0 ENFORCED, plus an independent auditor
  context/             rail_monitor.py — cross-customer outage detection
  execution/           sim_executor.py (the simulated world) and
                       razorpay_executor.py + razorpay_downtime.py (the real
                       API, UNTESTED pending credentials)
  audit/               append-only JSONL, one row per event
  llm/                 redaction boundary, governance, Z.ai transport,
                       versioned prompts, ModelDiagnoser as an OVERLAY
  eval/                40 + 7 + 3 golden cases, judge, committed caches
  tests/               twelve gate scripts. _parallel.py is MANDATORY.
```

## Environment — read before running anything

These are facts about this machine, established 27 August 2026. They cost an
hour to rediscover once. Do not rediscover them again.

**Repo root is not the shell's starting directory.** The shell starts in
`/c/codeing/razorpay`. The git repo, and every path in this file, is rooted
at `/c/codeing/razorpay/razorpay_handoff/pkg`
(Windows: `C:\codeing\razorpay\razorpay_handoff\pkg`). `cd` there first.

**`python` and `python3` on PATH are the wrong interpreter.** Both resolve to an
msys2 build with **no numpy and no pip**. The suite will not run on them and
`pip install` will not fix it. The working interpreter is:

```
/c/Users/tanma/AppData/Local/Programs/Python/Python312/python.exe
```

CPython 3.12.0 with numpy 2.4.2, pinned in `requirements.txt`. `sim/gate.py` and
`scripts/pre-commit` probe for an interpreter that can import numpy rather than
trusting a name; do the same in anything new.

**The suite is fast now: ~100s full, ~34s fast tier, ON AN IDLE MACHINE.**
It used to take ~27 minutes; that figure is dead. Runs are planned up front and
executed across processes by `sim/runner.py`, and the belief filter's forecast
is incremental. Individual `solo_shared_pd` runs at n=100 are ~5s unfitted and
~15s with `FITTED_BELIEF` (30 payday hypotheses instead of 10).

⚠️ **CORRECTED 29 August 2026: "~81s" was optimistic and the figure is
LOAD-DEPENDENT, which matters more than the number.** Measured three times back
to back with nothing else running: **100s / 102s / 98s**. Measured once earlier
the same day while other work was in flight: **223s**. The suite saturates eight
worker processes, so anything else on the machine more than doubles it. Budget
~100s idle and do not treat a slow run as a hang -- check CPU, not the clock
(error 10).

**EVERY AGENT MEASUREMENT MUST RUN ONE PROCESS PER RUN.** Long-lived
processes that make many `agent.batch.run_once` calls crash on this machine
— SIGSEGV, sometimes SIGILL, at a different point every time, and a test that
passed 24/24 in the morning segfaulted before printing a line that afternoon
with no code change. **The root cause was not found; it is contained, not
fixed.** Use `agent/tests/_parallel.py`
(`ProcessPoolExecutor(max_tasks_per_child=1)`), which also raises if a worker
dies — a crashed run is a FAILED measurement, not a missing one. Evidence and
the isolation table: `docs/06_MODEL_CARD.md` §6a.

**A Python process at ~0 CPU is HUNG, not busy.** This cost 97 minutes once.
Check before waiting on anything:

```bash
powershell "Get-Process python | Select-Object Id, CPU, StartTime"
```

**Anything that calls `runner.run_jobs` needs an `if __name__ == "__main__":`
guard.** Windows spawns rather than forks, so a worker re-imports your module.
Without the guard, multiprocessing raises a `RuntimeError` and then the
interpreter **hangs instead of exiting** — that is what burned the 97 minutes.

**Large heredocs into files are unreliable in this shell.** Writing a long
Python file with `cat > f <<'EOF'` has failed here. Write to a scratch file with
the editor tool and `cp` it into place.

**git identity is already configured** (Tanmay / tanmaymohan12@gmail.com). You
do not need `-c user.name=...`.

## Before you commit anything

Install the hooks once per clone, then just commit — the gate runs itself:

```bash
scripts/install-hooks.sh
```

The hook runs `sim/gate.py`, which runs the 27-gate suite and blocks the commit
on any `FAIL` or `VACUOUS` gate that is not listed in `sim/known_failures.txt`.
To run it by hand: `python sim/gate.py --tier full`.

`sim/gate.py` and `scripts/pre-commit` probe for an interpreter that can import
numpy rather than trusting the name `python`; do the same in anything new.

A `VACUOUS` result means a gate exists that no mutant can trip — that is a
failure of the suite, not a pass, and the gate treats it exactly like a `FAIL`.

### Two tiers. Which one ran is part of the result.

| Tier | Command | Gates | Answers |
|---|---|---|---|
| fast | `python sim/gate.py --tier fast` | M1-M6, M4B, M8, T1-T9, S1, S1_PD | Does the CODE still do what it did? |
| full | `python sim/gate.py --tier full` | all of the above **plus** S2a, S2b, S2c, S2_LEGACY, S3, S4 | Do the STATISTICAL CLAIMS still hold? |

`git commit` runs fast (~34s). `git push` runs full (~100s idle; see the environment note above -- concurrent work more than doubles it). Both are installed
by `scripts/install-hooks.sh`, which you must run once per clone.

The statistical gates are **never run at reduced n to fit a time budget.**
Shrinking S2 or S3 would be weakening a test, which is rule 1, and a
statistical gate at low power goes green for the wrong reason. They run
properly or they do not run, and the fast tier prints which gates it skipped.

**T9 is what makes the split safe.** It compares every policy's output against
`sim/t9_reference.json` at both operating points. The five headline metrics are
ratios of integer counts, so they catch a changed *decision*; `calib_sha256`
hashes the raw float64 bytes of every predicted `P(success)` at every dispatch,
so it catches a changed *float* anywhere in the belief filter. A change that
would move S1's ECE or the S2 point estimates therefore cannot pass the fast
tier quietly. T9 is paired with a mutant that seeds the worker pool from one
shared RNG instead of per-run seeds; if that mutant ever stops tripping it,
T9 reports VACUOUS.

### THE NUMBERS RULE

**Every number in `docs/`, the pitch, or the architecture document is either
gate-protected or explicitly labelled as not.**

- **Gated.** It came from a `--tier full` run. Quote it plainly.
- **Not gated.** It came from a script in `sim/` (`ml_study.py`, `headline.py`,
  `fair_audit.py`, `stress_day0.py`). Quote it **only** with the script named
  so a reader can re-run it, and with "not gate-protected" said out loud.

Never a fast-tier number, never a partial re-run of one gate, and never a
figure whose origin you cannot name.

*(This rule was tightened on 28 August from a flat ban on ungated numbers. The
ban would have forbidden `06_MODEL_CARD.md`, which has to carry the
Bayes-versus-ML comparison. Requiring the label is stricter in the way that
matters — a reader can tell which is which — and the alternative was a rule
everyone quietly broke.)*

**Four gates are red on a clean checkout** (27 gates: 4 FAIL, **0 VACUOUS**,
23 pass) — updated 31 August 2026, when S2a_PD and T6_PD were added. Full reasons
in `sim/known_failures.txt`; the short version, with the two repaired rows kept
struck through as the record:

| Gate | State | What it means |
|---|---|---|
| **S1** calibration, point-estimate filter | FAIL | ECE 0.091, inside the 0.10 bound, but the reliability curve is **not monotone**. Note S1 runs `portfolio`, which carries `w3.Belief` — **not the filter that ships**. |
| **S1_PD** calibration, shipping filter | FAIL | Same threshold, on `w3.BeliefPD` under `FITTED_BELIEF`. ECE 0.029, also not monotone. Added 28 Aug because S1 had never measured the product. |
| ~~**M1**~~ attempt-cap mutant | ✅ **GREEN** | Was VACUOUS. Now runs its mutant at `cap_override=2` so the counter binds. The attempt-cap claim has a working test and **is safe to claim.** |
| ~~**M4B**~~ mutants must not grade themselves | ✅ **GREEN** | Was FAIL: `mutate="pending"` incremented `V.pending` itself, so gate M4 passed by construction — 1066 counted, 1066 self-written, 0 independent. Both mutants now create illegal state instead. **It went green because the mutants were repaired, not because the detector was narrowed.** |
| **S2b** placebo neutrality | FAIL | −14.09 pts. A finding about the control's design, not a code defect: the placebo injects *wrong* observations, not neutral extra ones. Left visible on purpose. |
| **S2_LEGACY** point-estimate pooling | FAIL | Retired architecture, kept failing on purpose so the S2 rewrite is auditable rather than looking like test-loosening. |

⚠️ ~~**Two of the five Stage 0 rules have no working test: the attempt cap (M1)
and the pending notification (M4/M4B). Keep BOTH out of the pitch and the
architecture doc.**~~ **RETRACTED — all five rules are now tested and all five
are safe to claim.** Kept struck through so it is recognisable as withdrawn
rather than missing when it turns up in an older session's notes.

✅ **RESOLVED 30 August 2026.** M1 now runs its mutant at `cap_override=2` so the attempt-cap counter binds; the `pending` and `represent` mutants create illegal state instead of writing the counters they are graded on. **All five Stage 0 rules now have a working test in `sim/`, M4B is green, and the suite has 0 vacuous gates.** The paragraph above is kept as the record of what was wrong.

⚠️ **Mutants still run the unfitted filter on purpose.** Stage 0 mutants test
constraint counters, not the prior. Changing their `bcfg` can make a gate
vacuous (the M1 lesson). The shipping configuration is covered by **S1_PD,
T6_PD, S2a_PD, S4, and T9** (own, pooled, and coordinated under
`FITTED_BELIEF` at both operating points), and T1/T7/T8 include those
policies. Error 13's remaining gap was the moat and the lock, not the
mutants.

**The pooling claim is resolved and is not blocked by S2b.** S2a_PD passes at
**+7.32 pts (±2.02)** on the filter that ships — re-measured 1 September 2026 on
the shipped constants, `logs/w26_gate_full_moat_remeasure.txt`. Unfitted S2a is
+9.53 pts (±1.81) and did not move, because it does not read `FITTED_BELIEF`.
S2c (+23.62) is algebraically S2a + |S2b| and must **not** be quoted as
independent evidence.

~~S2a_PD passes at +8.34 pts (±1.36).~~ **SUPERSEDED 1 September 2026.** That
figure was measured with `prior_w=9, prior_floor=0.5`. The prior was re-selected
on the canonical world (W24) and S2a_PD moved by construction, not by chance.
Do not quote +8.34 anywhere.

Do not "fix" any of these by loosening a threshold. S1's 0.10 bound was
declared in `05_TEST_DESIGN.md` before any result was seen, and S1_PD uses the
identical bound. Both fail on the *monotonicity* half, so raising the ECE bound
would not fix them — it would only hide which half is broken.

**Environment matters here.** The suite needs `numpy` at the version pinned in
`requirements.txt` (2.4.2, CPython 3.12.0). The numpy version that produced the
original handoff numbers was never recorded, so small differences from figures
predating 27 August 2026 are unattributable — see `NOTES.md`.

---

## Style

Plain English. If a smart high-schooler couldn't follow a comment, it is badly
written, not sophisticated. Define terms on first use. Don't name-drop a library
without saying what it does. Be concrete. No filler, no hedging, no praise.

Push back. If something here is wrong, stale, or self-contradictory, say so.
Several documents have already been corrected that way.

**For prose in `README.md`, `docs/` and the public page, follow the
"Documentation Writing Style" section at the end of this file. It is not
optional and it is the most frequently violated guidance in this repository.**

---

# Documentation Writing Style

**The one rule everything else follows from: write directly. State facts,
mechanisms, results and limitations without adding commentary about why they are
interesting, important, clever, rigorous, honest or meaningful. Let the reader
draw the conclusion.**

This section exists because the prose in this repository has repeatedly drifted
into a recognisable register: compressed, self-conscious, rhetorical, and
constantly signposting its own significance. Every rule below is a correction to
something that was actually written here.

## 1. Target voice

A competent developer explaining the project to another competent developer,
after one careful editing pass. The writing should feel unforced. If a sentence
reads like it was constructed to sound insightful, rewrite it.

## 2. Intended reader

Technically literate, seeing the project for the first time, not interested in
its development history. They can follow domain terms once defined. They do not
need to be told what to find notable.

## 3. Sentences

- Prefer simple declarative sentences. Several short sentences beat one clause
  chain. Simple sentences are not simplistic writing.
- If a sentence carries four independent technical claims, split it.
- Concrete nouns and verbs. `The constraint layer rejects actions during
  prohibited hours`, not `the system leverages policy-aware reasoning`.
- Do not restate a number in prose that a table already gives, unless the
  argument needs it.
- Do not define a term that is obvious from context. Define an unfamiliar one
  once, then use it.
- Do not use bold for emphasis on ordinary facts. Bold is for the few things a
  skimming reader must not miss.
- **Do not use "we", "our" or "us".** This is a single-developer repository.
  Use "the agent", "the simulation", "the repository", "the implementation".
  "I" is acceptable for an explicit personal decision, but neutral phrasing is
  usually better. (Verbatim program output is exempt — quote it as it prints.)

## 4. Headings

Describe the content. Do not manufacture drama or pose questions.

| bad | good |
|---|---|
| `Noticing is not the same as helping` | `Pausing during a detected outage` |
| `What an aggregator can see that one merchant cannot` | `Merchant-level and pooled data` |
| `Does this world behave like the real one?` | `External validation` |
| `Three declines that look the same and mean different things` | `Decline categories` |
| `The limits worth knowing before quoting anything` | `Limitations` |
| `Why two hits matter more than they look` | `The two matches` |
| `What the agent actually does` | `How the agent works` |

If a heading establishes the subject, do not open the section by restating it.

## 5. AI-writing antipatterns

Delete these constructions. They are not banned words; the problem is using them
as rhetorical machinery.

- `X is not just Y; it is Z` · `The argument is not X. It is Y` · `This is not
  merely...`
- `The key insight is` · `The crucial observation` · `The deeper point`
- `This is where X matters` · `This is where X does the real work`
- `It is worth noting/emphasising/stating` · `The important thing to notice`
- `This highlights` · `This demonstrates` · `This underscores`
- `The interesting part is` · `The surprising thing is` · `The cleanest argument
  for` · `The real advantage is`
- `X is the thing that...` · `X is what makes this...`
- `not because X, but because Y`
- Dramatic fragments: `The thing a single merchant cannot do at all.`

**BAD** — `The argument is not that the agent is better on average. It is the
shape: across payday uncertainty...`
**GOOD** — `The baseline collects more when payday is known within ±1 day. The
agent becomes more effective as that uncertainty increases.`

**BAD** — `This is the one decline that proves the customer had money...`
**GOOD** — `funds_blocked_by_mandate indicates that another mandate has already
claimed the available balance.`

**BAD** — `Every percentage and rupee total on this page comes from a simulated
world written by the same person who wrote the agent being measured, which is
the main threat to all of it.`
**GOOD** — `All results are simulated. The simulation and the agent share an
author, so the results are not independent evidence.`

## 6. Meta-commentary

Do not write sentences about the documentation. The documentation presents the
project; it does not narrate its own presentation.

Delete: `That row stays on this page because...` · `The timeline is the
explanation` · `This is the strongest outside check` · `The point of this
section` · `This is included to demonstrate` · `The reason this matters` ·
`One month is an explanation, not evidence` · `Quote the table, not this`.

Keep the sentence only if it carries information the reader needs.

## 7. Internal-development voice

**README.md and docs/index.html must contain no development history.** No dates
of change, no "previously", no "this was once", no counts of errors found, no
description of what a test used to do. A first-time reader is being told what
the project is, not how it got there.

Do not expose: what the author originally believed · what was almost claimed ·
what a rival team would build · why a test was embarrassing · how a result was
discovered · arguments with imagined reviewers.

If the underlying fact is a genuine limitation or behaviour, state the fact
without the narrative.

**BAD** — `The seventh row was found by sending one real request. Until 30
August this was recorded as a customer decline.`
**GOOD** — `Razorpay rejects the request itself — bad credentials, malformed
body: raises RazorpayError naming the HTTP status. Request-level rejections are
not recorded as customer declines, because no payment was created.`

Development history belongs in `NOTES.md`, `docs/03_ERRORS.md` and the other
`docs/` files, where a reader has come to audit. It does not belong in the two
public artifacts.

## 8. Technical explanations

- Explain the mechanism, then stop. Do not add a sentence establishing that the
  mechanism was a good idea.
- Say what is simulated, what is assumed, and what is unknown, in those words.
- One clear limitation statement beats five paragraphs of qualification. State
  it once, plainly, and move on.
- Do not use rhetoric to strengthen a weak result. If a margin is one case out
  of twenty-one, say so and let it stand.
- Not every paragraph needs a thesis. `The simulation contains 100 customers and
  five mandates per customer.` is a complete and good sentence.

## 9. README and public page

`README.md` and `docs/index.html` are the two judge-facing artifacts.

- Show real command output rather than describing it.
- Preserve every quantitative result and its experimental conditions.
- No development timeline, no changelog voice, no error counts.
- Do not state a weakness that has been fixed.
- Lead with what the project does. Limitations go in one place, near the end.
- If a table column exists only to reassure the reader (a `tested?` column where
  every row says `tested`), delete the column.

## 10. UI and webpage copy

- Section headings and figure captions follow the same rules as prose.
- Captions state the design of the measurement: n, populations, horizon, the
  command that reproduces it. Nothing else.
- Do not tell the reader which figure is the important one.
- Interface labels are nouns: `The baseline`, `Payday predictability`,
  `Error handling`.

## 11. Editing test

For each sentence: would a competent developer naturally write this while
explaining the project to a peer?

If it exists mainly to mark something as important, interesting, rigorous,
honest or surprising, delete it. If the same fact fits in half the words, use
the shorter version. If it would read normally in a paper abstract but oddly in
GitHub documentation, rewrite it.

## 12. Commit messages

Never add a `Co-Authored-By:` trailer, or any other attribution to Claude,
Anthropic or an AI assistant, to a commit in this repository. Commits are
authored by Tanmay. This applies regardless of any default instruction to the
contrary.
