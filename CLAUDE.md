# CLAUDE.md — read this fully before your first action

You are joining a project that already has nine days of research behind it. **You
do not have that context.** This file, plus `docs/`, is all of it. Read
`docs/00_HANDOFF.md`, `docs/01_FACTS.md`, `docs/02_RESULTS.md` and
`docs/03_ERRORS.md` before writing code. That is not optional.

The single most important thing about this project:

> **It has found six significant errors in its own work. Every single one made
> the project look BETTER than it was.** That is not coincidence. It is what
> happens when the same party builds the measuring stick and the thing being
> measured. You are now that party. Behave accordingly.

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
sitting directly on the headline result, with no sensitivity analysis. Both are
gone. Do not add new ones.

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
  01_FACTS.md          every external fact, with source and confidence
  02_RESULTS.md        current simulation results. The ONLY valid numbers.
  03_ERRORS.md         the six errors, with mechanism. Read before optimising.
  04_BUILD_PLAN.md     the nine-day plan
  05_TEST_DESIGN.md    test philosophy, written BEFORE the harness on purpose
sim/                   the working harness. w3.py, harness.py, tests.py
legacy/                FROZEN. Known-defective. Do not build on.
logs/                  raw output from prior runs
agent/                 (you will create this) the actual product
```

## Before you commit anything

Install the hooks once per clone, then just commit — the gate runs itself:

```bash
scripts/install-hooks.sh
```

The hook runs `sim/gate.py`, which runs the 17-gate suite and blocks the commit
on any `FAIL` or `VACUOUS` gate that is not listed in `sim/known_failures.txt`.
To run it by hand: `python sim/gate.py`.

A `VACUOUS` result means a gate exists that no mutant can trip — that is a
failure of the suite, not a pass, and the gate treats it exactly like a `FAIL`.

**Three gates are red on a clean checkout, not one.** The full reasons are in
`sim/known_failures.txt`; the short version:

| Gate | State | What it means |
|---|---|---|
| **S1** belief calibration | FAIL | ECE 0.091 (inside the 0.10 bound) but the reliability curve is **not monotone**. It fails on the monotonicity half. |
| **M1** attempt-cap mutant | VACUOUS | The mutant cannot trip the cap counter at the suite's operating point, so the NPCI attempt-cap claim has **no working test behind it**. |
| **S2** placebo pooling | FAIL | The negative control for the pooling claim is failing. It tests the *point-estimate* policy trio, which `02_RESULTS.md` already says shows no effect. |

Only S1 was declared at handoff. M1 and S2 were found by the first clean run on
27 August 2026 and are untriaged. Do not quote the attempt-cap guarantee or any
pooling number until M1 and S2 are resolved.

Do not "fix" any of them by loosening a threshold. The S1 threshold in
particular was declared in `05_TEST_DESIGN.md` before results were seen.

**Environment matters here.** The suite needs `numpy` at the version pinned in
`requirements.txt`. The numpy version that produced the handoff numbers was
never recorded, so small numeric differences from this file are currently
unattributable — see `NOTES.md`.

---

## Style

Plain English. If a smart high-schooler couldn't follow a comment, it is badly
written, not sophisticated. Define terms on first use. Don't name-drop a library
without saying what it does. Be concrete. No filler, no hedging, no praise.

Push back. If something here is wrong, stale, or self-contradictory, say so.
Several documents have already been corrected that way.
