# CLAUDE.md — read this fully before your first action

You are joining a project with a finished, frozen simulation behind it. **You
do not have that context.** This file, plus `docs/`, is all of it.

**If you are building the agent — which is all that remains — read
`docs/07_AGENT_BRIEF.md` first, then `docs/06_MODEL_CARD.md`.** Between them
they carry the interface, the evidence and the limits, and you do not need to
read `sim/` to use it.

Then read `docs/00_HANDOFF.md`, `docs/01_FACTS.md`, `docs/02_RESULTS.md` and
`docs/03_ERRORS.md`. That is not optional.

The single most important thing about this project:

> **It has found six significant errors in its own work. Every single one made
> the project look BETTER than it was.** That is not coincidence. It is what
> happens when the same party builds the measuring stick and the thing being
> measured. You are now that party. Behave accordingly.

---

## THE MODEL IS FROZEN — tag `model-frozen`, 28 August 2026

**Do not change `sim/w3.py`, `sim/harness.py`, or the fitted constants
(`w3.FITTED_BELIEF`, the `0.92` discount) before 5 September without explicit
approval from Tanmay.** Not to tidy them, not to squeeze another point out of
them, not because a better idea turned up. The simulation model is done.

Everything from here is `agent/`. The probability engine is `w3.BeliefPD`
configured with `w3.FITTED_BELIEF` — wire it in, do not rewrite it.

What is still open and still allowed: `agent/`, `docs/`, `NOTES.md`, the pitch,
and the architecture document. `sim/tests.py` may gain gates but no gate's
threshold may move.

Why this rule exists: the model went through four significant corrections in a
single day (a dead LTV multiplier, a placebo forecast defect, an unfitted
belief, and a fitted-then-brittle prior that had to be refitted). Each was
worth making. None of them is worth making on 4 September with a deadline on
the 5th and no time to re-run the suite.

---

## Hard rules. These are not preferences.

### 1. Never weaken a test to make it pass
If a test fails, the code is wrong until proven otherwise. You may not delete a
gate, loosen a threshold, add a special case, or mark a test skipped. If you
believe a test is genuinely wrong, **stop and ask the human**. Write the
reasoning in `NOTES.md` first.

The test suite has already caught three defects in its own author's code. It is
the most valuable asset in the repo. Treat it as read-mostly.

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
in `05_TEST_DESIGN.md`, declared at project start and never done): it sits on a
broad plateau, and `solo_shared_pd` ranges **78.7%–83.1%** across
discount 0.80–1.00. Quote that range, not a point. Do not add new ones.

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
- **The bandit policy decides *when*.**
- **The constraint layer decides *whether it is allowed*.**

An LLM must never be on the path that decides whether to debit a specific
customer at a specific moment. That is a deliberate architectural choice
(ADR-005) and it is defensible under the "AI Judgment" judging criterion —
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
  03_ERRORS.md         TEN errors, with mechanism + guard. Pitch material.
  04_BUILD_PLAN.md     what is left, dated
  05_TEST_DESIGN.md    test philosophy, written BEFORE the harness on purpose
  06_MODEL_CARD.md     WHAT SHIPS. Read this before touching sim/ or agent/.
  07_AGENT_BRIEF.md    START HERE if you are building the agent.
sim/
  w3.py                world + belief filters + FITTED_BELIEF. FROZEN.
  harness.py           policies, Stage 0 violation counters, run(). FROZEN.
  tests.py             the 24-gate suite. Tripwired: see "Before you commit".
  gate.py              runs tests.py, decides if a commit is allowed
  runner.py            parallel driver (spawn-safe). Read its docstring first.
  t9_reference.py      captures/checks sim/t9_reference.json (gate T9)
  known_failures.txt   the gates allowed to be red, each with a written reason
  fit_belief.py        how FITTED_BELIEF was fitted. Re-runnable.
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
scripts/               install-hooks.sh, pre-commit, pre-push, day-start.sh
agent/                 (you will create this) the actual product
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

**The suite is fast now: ~81s full, ~34s fast tier.** It used to take ~27
minutes; that figure is dead. Runs are planned up front and executed across
processes by `sim/runner.py`, and the belief filter's forecast is incremental.
Individual `solo_shared_pd` runs at n=100 are ~5s unfitted and ~15s with
`FITTED_BELIEF` (30 payday hypotheses instead of 10).

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

The hook runs `sim/gate.py`, which runs the 24-gate suite and blocks the commit
on any `FAIL` or `VACUOUS` gate that is not listed in `sim/known_failures.txt`.
To run it by hand: `python sim/gate.py --tier full`.

`sim/gate.py` and `scripts/pre-commit` probe for an interpreter that can import
numpy rather than trusting the name `python`; do the same in anything new.

A `VACUOUS` result means a gate exists that no mutant can trip — that is a
failure of the suite, not a pass, and the gate treats it exactly like a `FAIL`.

### Two tiers. Which one ran is part of the result.

| Tier | Command | Gates | Answers |
|---|---|---|---|
| fast | `python sim/gate.py --tier fast` | M1-M6, M8, T1-T9, S1, S1_PD | Does the CODE still do what it did? |
| full | `python sim/gate.py --tier full` | all of the above **plus** S2a, S2b, S2c, S2_LEGACY, S3, S4 | Do the STATISTICAL CLAIMS still hold? |

`git commit` runs fast (~34s). `git push` runs full (~81s). Both are installed
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

**Five gates are red on a clean checkout.** Full reasons in
`sim/known_failures.txt`; the short version:

| Gate | State | What it means |
|---|---|---|
| **S1** calibration, point-estimate filter | FAIL | ECE 0.091, inside the 0.10 bound, but the reliability curve is **not monotone**. Note S1 runs `portfolio`, which carries `w3.Belief` — **not the filter that ships**. |
| **S1_PD** calibration, shipping filter | FAIL | Same threshold, on `w3.BeliefPD` under `FITTED_BELIEF`. ECE 0.026, also not monotone. Added 28 Aug because S1 had never measured the product. |
| **M1** attempt-cap mutant | VACUOUS | The mutant cannot trip the cap counter at either operating point, so the NPCI attempt-cap claim has **no working test behind it**. **Do not put the cap-compliance claim in the pitch.** |
| **S2b** placebo neutrality | FAIL | −14.09 pts. A finding about the control's design, not a code defect: the placebo injects *wrong* observations, not neutral extra ones. Left visible on purpose. |
| **S2_LEGACY** point-estimate pooling | FAIL | Retired architecture, kept failing on purpose so the S2 rewrite is auditable rather than looking like test-loosening. |

**The pooling claim is resolved and is not blocked by S2b.** S2a passes at
+9.53 pts (±1.81) and is the defensible moat number. S2c (+23.62) is
algebraically S2a + |S2b| and must **not** be quoted as independent evidence.

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
