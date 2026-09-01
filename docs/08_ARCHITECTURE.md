# 08 — ARCHITECTURE

**Track 3, AI Revenue Recovery. One page.** What the system is, why it is split
the way it is, and what each seam buys. Numbers here are quoted with the
command that produces them; everything is measured or labelled as not.

---

## The problem

An Indian subscription debit under UPI AutoPay fails most of the time, and
almost always for one reason: the account is empty at the moment of the charge.
NPCI permits **four attempts per mandate per billing cycle** — one presentation
and three retries. The standard vendor answer is to spend them on a fixed
schedule: T, T+1, T+2, T+3. That is Razorpay's own documented schedule
`[VERIFIED]`, so it is what a real competitor does, not a strawman.

Spending four attempts in four days empties the quiver while the account is
still empty, and a mandate that fails its last attempt dies and forfeits every
cycle after it. **The scarce resource is not attempts. It is information about
when money arrives.**

So each failed debit is treated as a **measurement**: a debit of ₹X that failed
proves the balance was below ₹X at that hour. That is a censored observation,
and censored observations are what a Bayesian filter is for.

## The answer, in one line

**Keep a probability distribution over each customer's balance and over their
salary credit date, update it from failed and successful debits, and attempt
when money is most likely to be there — inside the regulator's constraints.**

## The division of labour, and the rule that makes it real

| | decides | implemented by |
|---|---|---|
| **The language model** | *what* to do, and *why* — root cause, choice of intervention, the human-readable justification attached to every money action | `agent/llm/`, an **overlay** over a deterministic rule engine |
| **The belief filter and its index rule** | *when* — which mandate, which day, which hour | `agent/policy/`, wrapping `sim/w3.py` |
| **The constraint layer** | *whether it is allowed* | `agent/constraints/`, enforced and then independently recounted |

**ADR-005, the one architectural decision worth stating as a decision:**

> **A language model must never be on the path that decides whether to debit a
> specific customer at a specific moment.**

This is enforced by construction rather than by review. The narrative layer's
only output type is `ports.Diagnosis`, and **it has no temporal field**, so an
instruction like "retry at 11am" — whether hallucinated or injected through a
merchant note — has nowhere to land.
`agent/eval/injection.py:diagnosis_has_temporal_field()` inspects the type and
fails the day someone adds one. Separately, `agent/llm/governance.py` scans the
merchant-facing prose for times, because a justification that recommends an
hour is a model on the timing path via the merchant's eyeballs.

The reason for the rule is narrow and not ideological: debit timing is a
numerical inference from censored observations with a hard legality boundary.
That is not a task where a language model is the right instrument, and putting
it there would make every money action unreproducible. Diagnosis, explanation,
and the customer or merchant copy for reminder, last-attempt checkout, and
escalate are the opposite.

---

## The shape

```
                       one decision hour, one customer
                                    |
   +--------------------------------v---------------------------------+
   |  CONTEXT      rail_monitor.py                                     |
   |               cross-customer outage detection. Requires time-major |
   |               iteration and raises NonMonotonicTime if it does not |
   |               get it, because a customer-major loop silently       |
   |               latches OUTAGE forever (error 14).                   |
   +--------------------------------+---------------------------------+
                                    |
   +--------------------------------v---------------------------------+
   |  POLICY       belief_book.py   ONE BeliefPD per CUSTOMER,         |
   |                                shared by all k mandates           |
   |               timing.py        index = amount * (p_now - 0.92 * p_later)
   |                                -> (mandate, target hour) or WAIT   |
   +--------------------------------+---------------------------------+
                                    |
   +--------------------------------v---------------------------------+
   |  DIAGNOSIS    caseview.py      redaction boundary: the model      |
   |                                never sees a balance or a salary   |
   |               model_diagnoser  glm-5.3-flash, an OVERLAY          |
   |               fallback.py      deterministic rule engine, the     |
   |                                default, and what produces the     |
   |                                gated number                       |
   |               -> Diagnosis {root cause, intervention, rationale}  |
   |                  NO TIME FIELD                                    |
   +--------------------------------+---------------------------------+
                                    |
   +--------------------------------v---------------------------------+
   |  STAGE 0      stage0.py        ENFORCES five rules. Refuses.      |
   |               rules.py         the ledger the rules read          |
   |               -> Allowed(outcome) | Refused(rule)                 |
   +--------------------------------+---------------------------------+
                                    |
   +--------------------------------v---------------------------------+
   |  EXECUTION    ports.Executor   attempt() for debits; remind()     |
   |                                writes a funding notice (not a     |
   |                                Payment Link) and emails it when   |
   |                                SMTP is configured; backup_checkout()|
   |                                is the last-attempt Payment Link;  |
   |                                escalate() appends a merchant queue|
   |               sim_executor.py       the simulated world           |
   |               razorpay_executor.py  Razorpay's live API           |
   +--------------------------------+---------------------------------+
                                    |
   +--------------------------------v---------------------------------+
   |  AUDIT        log.py           append-only JSONL, one row per     |
   |                                event, 29,671 events in the batch  |
   |               auditor.py       INDEPENDENT recount from the log   |
   |                                alone. May not import rules.py or  |
   |                                stage0.py. Gate I3 fails if it does|
   +-------------------------------------------------------------------+
```

**`agent/ports.py` is the shared vocabulary and imports nothing from `agent/`.**
Gate **I2** enforces that only `constraints/stage0.py` and the composition root
may hold an executor at all, which is why the backend can be swapped without
any other layer knowing.

---

## The three seams that carry the weight

**1. One belief per customer, shared by all `k` mandates.** This is the claim
the project is built on: a failed debit for merchant A is evidence about the
same customer's ability to pay merchant B. Build one filter per *mandate* and
you have a system that works and is **7.3 points worse** in the hard world on
the shipping filter (gate S2a_PD) — and **1.3 points worse** at
`pop_spend=0.80` —
and nothing tells you, because both configurations run clean.
`agent/policy/belief_book.py` enforces the sharing and
`agent/tests/test_one_belief.py` asserts it.

**It is a curve, not a number, and it is reported as one.** Pooling is worth
**+7.32 points (S2a_PD, gated) at `pop_spend=1.05` and +1.30 at `pop_spend=0.80`**,
the same two calibrations the headline is reported over. The 0.80 cell is an
agent measurement, not gate-protected. Re-measured 1 September 2026 after the
payday prior was re-selected; the curve is now a factor of five end to end, not
2.5, so the seam is load-bearing in the hard world and much less so at the
gentler calibration.

**And it is a per-customer permission, not an assumption.** Sharing one
customer's outcomes across merchants is the part of this design with a live
legal question attached — mandates are structurally per-merchant, and India's
DPDP Rules 2025 operationalise consent and purpose limitation. A system that
can only run pooled cannot answer that question. This one takes `pooling` in
`{all, none, consented}`, and the cost of withholding is measured rather than
argued: **2.77 points at half consent in the hard world, 0.57 at the gentler
one.**

*(Gate S2a, `--tier full`: +9.53 pts ±1.81, on the unfitted filter. The
two-calibration figures: `python agent/tests/test_pooling_consent.py`,
not gate-protected.)*

**2. Stage 0 enforces; a separate component recounts.** The gate refuses
illegal actions. `auditor.py` then rebuilds legality **from the audit log
alone**, sharing no code with the enforcer. Two implementations of the same
rules, and they have disagreed once — the auditor was right.

The five rules, all `[REPORTED]` with sources in `01_FACTS.md`:

| rule | constraint |
|---|---|
| `cap` | ≤ 4 attempts per mandate per billing cycle |
| `peak` | no execution in 10:00–13:00 or 17:00–21:30 |
| `lead` | ≥ 24h between pre-debit notification and execution |
| `pending` | at most one pending notification per mandate |
| `represent` | an insufficient-funds decline may not be re-presented under the old notification; a technical one may |

All five have a working mutation test as of 30 August 2026, and the suite has
zero vacuous gates.

**3. One port, two worlds.** `ports.Executor` declares a single method.
`SimExecutor` implements it against the simulation; `RazorpayExecutor`
implements it against Razorpay's live API using `urllib` and no new dependency.
The loop, the belief, Stage 0, the auditor and the trail are byte-identical
either way, and the switch is one argument in `agent/batch.py`.

Stage 0 adjudicates **before** the executor is reached, so an illegal action is
refused with zero network traffic against either backend.
`scripts/prove_stage0_refuses.py` demonstrates that end to end with no API key.

---

## The decision rule, precisely

At each decision hour, for each live mandate, the filter produces a posterior
over the balance for every remaining day of the cycle. Then:

```
p_now    = P(a debit of `amount` succeeds tomorrow)
p_later  = max over the remaining legal days, or 0 if this is the last attempt
score    = amount * (p_now - 0.92 * p_later)
score <= 0  ->  wait. The future looks better than now.
```

This is a **one-step lookahead in the style of a Whittle index**, not a bandit:
there is no exploration/exploitation trade, no learned index and no
indexability proof. The 0.92 is a hand-chosen discount, swept, and it moves the
headline by about 7 points across a plausible range — the single largest
hand-set sensitivity in the system, and it is reported rather than buried.

**Stopping is explicit, not emergent.** Nine named rules, counted and logged:
`COLLECTED`, `CAP_REACHED`, `CYCLE_CLOSED`, `NO_LEGAL_SLOT`, `MANDATE_DEAD`,
`ESCALATED`, `AGENT_STOP` (the diagnosis layer chose to stop),
`LAST_ATTEMPT_HELD` (fourth debit replaced by an unpaid backup checkout),
`BATCH_LEGAL_CEILING` (n_mandates × 4 × cycles in the horizon; circuit
breaker, expected 0 on a clean run).

---

## Where the language model earns its place, and where it does not

The shipping path routes the diagnoser to the model **only when `merchant_note`
is non-empty**. Terminal codes, indeterminate outcomes, wide uncertainty bands,
and TECH streaks stay on the rule engine. The model writes escalate merchant
copy when `use_llm=True`; reminders and backup links use templates.

| | `merchant_note` registered (4) | injection (3) |
|---|---|---|
| rule engine | 4/4 | structural pass |
| routed LLM (`glm-diag-v3`) | 2/4 | structural pass |

Measured 31 August 2026, `run_eval.py --llm --judge --replay`. The full
40-case table is kept for history; do not quote 6/21 vs 9/21 as shipping
evidence.

**The money headline is filter plus rules only** — `batch_report` has no LLM
column. `--demo-llm` prints routed-call stats on one population (zero routed
calls in the default sim, which has no merchant notes).

---

## What it measures

```
python -m agent.batch_report --pops 4        # ~50s, no API key, no network
```

*100 customers × about 2 mandates each (`1 + Poisson(1)`, capped at 8), 10
held-out populations (710–719), 120 days, `payday_err=7`, `pop_spend=0.93`, 12
burn-in cycles discarded. **Not gate-protected** — an `agent/` script;
reproduce with the command above.*

| arm | cycles collected | ₹ recovered | survival | att/cycle |
|---|---|---|---|---|
| `payday_wait` (rival) | 90.29% | — | 90.52% | 1.272 |
| agent, deterministic | **99.38%** | ₹7,511,500 | **99.84%** | 1.450 |

**+9.08 pts, 2 SE 1.84.** Stage 0 refusals: **0**, with an independent recount
of **0** over **8,702 executed money actions**.

**Survival is the more interesting row.** The fixed schedule spends its four
attempts in four days, hits the cap while the account is empty, and the mandate
dies — **85.3% survival against the agent's 98.4%**. Dunning harder costs the
merchant the customer, and the cycle-based metric prices that automatically
without inventing a lifetime-value constant.

*Those two survival figures are a second experiment, over 20 populations at the
same `pop_spend`: `py -3.12 agent/tests/test_canonical_world.py --confirm`. Not
gate-protected. Named separately so it is not mistaken for the 10-population
batch above.*

### What the number is conditional on — two parameters, not one

**How well payday is known.** Measured against `[1,7]`, a steelmanned fixed
schedule: two attempts at frozen offsets from the same noisy payday estimate
the agent gets, selected once on train populations and never re-tuned.
*Recovery of at-risk cycles, 10 held-out populations, `pop_spend=0.93`.*

| `payday_err` | `[1,7]` | agent | agent − baseline |
|---|---|---|---|
| ±1 day | **99.75%** | 98.59% | **−1.16** — **the fixed schedule wins** |
| ±3 days | **98.51%** | 98.18% | **−0.33** |
| ±5 days | **96.63%** | 97.78% | **+1.15** |
| ±7 days | 90.88% | 94.43% | +3.55 |
| ±10 days | 66.93% | **90.76%** | **+23.83** |
| ±14 days | 55.47% | **89.88%** | **+34.41** |

**How hard the world is** — `pop_spend`, the share of salary spent per cycle,
set to one minus the household saving rate. India's three published FY25
readings put it in [0.80, 0.93]; no point inside is declared.
*Cycles collected, same 10 populations, `payday_err=7`.*

| `pop_spend` | `payday_wait` | agent | agent − baseline | at-risk cycles |
|---|---|---|---|---|
| 0.80 | 99.07% | 100.00% | +0.93 ±0.60 | **2** |
| 0.85 | 97.36% | 99.83% | +2.47 ±0.93 | 83 |
| 0.88 | 95.99% | 99.65% | +3.66 ±0.86 | 197 |
| 0.90 | 94.24% | 99.53% | +5.29 ±0.82 | 299 |
| **0.93** | 90.29% | 99.38% | **+9.08** ±1.84 | 557 |

*Neither table is gate-protected. Reproduce with
`py -3.12 agent/tests/test_steelman_schedule.py` and
`logs/w24_conditional_repaired.txt`.*

**Read those two tables together and the honest summary is this.** The system's
advantage is not a constant; it is a curve in two variables, and one of those
curves crosses zero inside the plausible range. It **loses** to a frozen
two-offset schedule whenever payday is known to within five days, ties at
seven, and only becomes large when payday is genuinely uncertain. At
`pop_spend=0.93`, the setting where this world's due-date failure rate lands
inside the published 8–15% band, the agent is worth **+9.08 points** against
`payday_wait` — just above the published 6–8% industry benchmark for retry
optimisation, and nothing was tuned to land there.

Below `pop_spend=0.90` the world carries too few at-risk cycles to measure a
difference: two of them at 0.80, across a thousand customers. The last column
is where the whole difference lives, since both arms collect every cycle that
was never at risk.

`payday_wait` is a permanent row in the report and cannot be switched off. It
is what a good rival builds in an afternoon, and the case for this system has
to survive being printed next to it.

### Does the simulated world resemble the real one?

There is **no public benchmark** for payment retry scheduling — no dataset, no
leaderboard, no held-out set. What exists is aggregate statistics published by
companies selling recovery software. The world was not fitted to them, and is
scored against four:

| target | measured | published | |
|---|---|---|---|
| first-presentation failure rate | 13.68% | 8–15% | **HIT** |
| fixed-interval recovery | 27.85% | 20–40% | **HIT** |
| smart-retry recovery | 97.38% | 70–85% | MISS |
| recoveries inside 10 days | 41.84% | 85–95% | MISS |

*At `pop_spend=0.80, p_missed_credit=0.00, p_transient=0.00`. Reproduce with
`python agent/tests/test_recovery_rates.py`. Not gate-protected.*

Two hits, neither fitted. **The two misses have two different causes, not one**
— that was asserted as a single cause, checked, and corrected. Calibrations
that hit a third target were found twice and **rejected both times**, because
each one broke a target it had not been tuned against. Two hits obtained by
turning two dials are a curve fit.

---

## Runtime failure recovery

The rubric asks how failures were handled **at runtime**, not only in
development. Every one of these is in the shipping path:

| failure | response |
|---|---|
| the language model errors, times out, or returns unparseable output | falls back to the deterministic rule engine and logs `LLM_FAILURE`. **94.8% of decisions take this path by design**, so the fallback is the normal case and is exercised continuously, not a cold branch |
| the model returns a legal-looking instruction with a time in it | `governance.py` rejects the prose; `Diagnosis` has no field for it in the first place |
| an action would breach a regulatory rule | Stage 0 refuses before the executor exists to it, and the refusal is a row in the audit trail |
| the rail is down across many customers at once | `rail_monitor.py` detects it from cross-customer outcomes and the belief update is suppressed, because a technical decline is not evidence about one customer's balance |
| a debit's outcome is unknown — timeout, deemed transaction | `AttemptOutcome.pending=True`. **Never** rounded down to "failed", because rounding an unknown to a failure is what licenses a retry, and a retry on an unknown is a double debit |
| an HTTP request is retried after a socket failure | the idempotency key is derived from the `action_id` Stage 0 already audited, so the same logical debit produces the same key across a crash |
| **the API key is wrong or expired** | raises, loudly. Until 30 August 2026 it was recorded as a customer decline — teaching the belief filter that the account was empty, for every mandate, silently. **Error 28**, found by sending one unauthenticated request |
| a worker process dies mid-measurement | `_parallel.py` raises. A crashed run is a **failed** measurement, not a missing one |
| a second run appends to an existing audit log | `LogFileNotEmpty` at open time. It once made the demo print violations that never happened. **Error 18** |

---

## What is not tested, stated plainly

- **No real transaction data exists in this project.** Every figure is
  simulation. No Razorpay transaction, mandate or decline code has ever been
  observed.
- **The Razorpay client authenticates in test mode.** Payment Links and
  Customer records have been created against `rzp_test_` keys
  (`scripts/prove_workflows.py`). Funding reminders are emitted through the
  executor's generic SMTP path when `SMTP_HOST` and `RECOVERY_NOTIFY_EMAIL` are
  set; the outbox JSONL row is retained as an audit backup. Live SMTP delivery
  was proven through the configured test relay (`scripts/prove_smtp_reminder.py`,
  transcript in `logs/smtp_reminder_proof.json`). A recurring-charge body has never been
  submitted on an authorised mandate.
- **Stage 0 mutants still run the unfitted filter.** They test constraint
  counters, not the prior. The moat, the calibration, the k=1 identity, and
  the byte-lock run `FITTED_BELIEF` (S1_PD, T6_PD, S2a_PD, S4, T9).
- **Four gates are red on a clean checkout, on purpose**, each with a written
  reason: two calibration gates that fail on monotonicity for structural
  reasons no parameter fixes, one retired architecture kept red so a rewrite
  stays auditable, and one negative control that turned out not to be neutral.
- **Razorpay UPI AutoPay decoupled pre-debit flow is wired in code.** Stage 0
  still records pendency and calls `notify()`; the Razorpay executor posts
  `POST /v1/orders` with the documented `notification` object. Razorpay handles
  delivery. `ORDER_CREATED` is not `NOTIFICATION_DELIVERED`; delivery proof
  requires `order.notification.delivered`. Live order creation and delivery
  webhook are **not gate-protected** until rung 5a and a real webhook are run.

**Thirty-two errors have been found in this project's own work**, catalogued with
mechanism and guard in `docs/03_ERRORS.md`. Almost every one made the project
look better than it was. That is what happens when the same party builds the
measuring stick and the thing being measured, and it is the reason the
constraint layer is recounted by a component forbidden from importing it.

---

| | |
|---|---|
| What ships and what it is worth | `docs/06_MODEL_CARD.md` |
| Every result with its bias analysis | `docs/02_RESULTS.md` |
| Every external fact with a source tag | `docs/01_FACTS.md` |
| The thirty-two errors | `docs/03_ERRORS.md` |
| The decision log | `NOTES.md` |
