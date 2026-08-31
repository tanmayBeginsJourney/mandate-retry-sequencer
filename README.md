# UPI AutoPay Recovery Agent

An agent that schedules retries for failed subscription debits on UPI AutoPay.
It maintains a probability distribution over the customer's bank balance and
over the day their salary arrives, and attempts the debit when the balance is
likely to cover it.

## The problem

Indian subscription payments run on UPI AutoPay. A mandate authorises a merchant
to debit a customer once per billing cycle. If the account is short at the
moment of the charge, the debit is declined.

NPCI permits four attempts per mandate per cycle: one presentation and three
retries. Razorpay's documented schedule uses them on day T, T+1, T+2 and T+3,
then halts. That spends every attempt inside four days of the due date, which is
often before the customer is paid. A mandate that fails all four attempts dies
and forfeits its remaining billing cycles.

## Approach

Each decline is treated as a measurement. A failed ₹550 debit means the balance
was below ₹550 at that moment; a successful one means it was at least ₹550.

The agent maintains a distribution over the customer's balance and payday.
Each attempt outcome updates it. Once a day the timing policy compares the
probability of clearing today against the best probability available on any
remaining day of the cycle, and either attempts or waits. Proposed actions pass
through a constraint layer that enforces NPCI's mandate rules before execution.
Every decision is appended to an audit log, including the decision to wait.

## Results

| | agent | fixed schedule | source |
|---|---|---|---|
| Billing cycles collected | 98.01% | — | `agent.batch_report` |
| Recovered across the batch | ₹6,203,060 | — | `agent.batch_report` |
| Of debits that would fail on their due date, share recovered | 90.84% | 16.35% | `test_recovery_rates` |
| Mandates alive after 120 days | 96.6% | 32.1% | `test_recovery_rates` |

The first two rows use full agent mode: 100 customers × 5 mandates, population
seeds 700–703, run seed 7, and 120 days. The last two use degenerate mode over
population seeds 700–707 with run seed 907. Both use `payday_err=7` and
`pop_spend=1.05`. In the four-population full-mode batch, the `payday_wait`
baseline collects 57.70% of cycles, 40.30 points below the agent (2 SE 2.32).

The fixed schedule's survival rate is low because it uses all four attempts
within four days of the due date and reaches the NPCI cap while the account is
still empty. The cycle-collection metric prices mandate death directly: a dead
mandate forfeits its remaining cycles, so no lifetime-value constant is needed.

All results are simulated. No Razorpay transaction, mandate or decline code has
been observed by this project.

[`docs/index.html`](docs/index.html) is an interactive walkthrough of one
customer's month, a rail outage, and the cases where the baseline wins. Static
page, no build step: `python -m http.server --directory docs`.

## External validation

No public benchmark exists for payment retry scheduling — no shared dataset,
held-out set or leaderboard. The available comparisons are aggregate statistics
published by companies selling recovery software. They are second-hand, they
aggregate customer bases that are not comparable, and one states in its
methodology note that its figures are ranges rather than laws.

The simulation is calibrated against a single anchor. The four figures below
were not used in calibration.

| | measured | published | |
|---|---|---|---|
| Share of debits failing on their due date | 13.68% | 8–15% | hit |
| Recovery under a fixed-interval retry schedule | 27.85% | 20–40% | hit |
| Recovery under smart retry timing | 96.78% | 70–85% | miss, too high |
| Share of recoveries landing inside 10 days | 42.94% | 85–95% | miss, too slow |

8 held-out populations at `pop_spend=0.80`, the calibration whose failure rate
falls inside the published band. Reproduce with
`py -3.12 agent/tests/test_recovery_rates.py`. Sources are listed in
[`docs/01_FACTS.md`](docs/01_FACTS.md). The command prints all measurements and
exits non-zero because two pre-registered predictions are marked `BROKE`; a
broken prediction is not reported to automation as a passing test.

The two hits come from different parts of the model: the first is a property of
the simulated world, the second of a baseline policy running inside it.

The two misses have separate causes, both in the world model rather than the
agent:

- Recovery is too high because no simulated customer is permanently unable to
  pay. A clairvoyant scheduler collects 100% at every calibration tested.
  Adding customers who cannot pay brings recovery into the published band.
- Recovery is too slow because a mandate's due date and its customer's payday
  are drawn independently, so the gap between them averages half a cycle. Only
  35.8% of at-risk cycles have money available inside ten days, and the agent
  recovers 42.6% of them in that window. Real billing dates cluster near
  paydays; these do not.

Temporary account holds were expected to account for the second miss. Swept
across 14 alternative worlds, they moved it by under one point. No alternative
world in that sweep scores better against the published figures than the
calibration above.

### Sensitivity to world hardness

`pop_spend` sets how much of a salary a customer spends per cycle.

| `pop_spend` | baseline per-attempt approval | agent − baseline |
|---|---|---|
| 0.60 | 93.2% | +3.52 ±0.90 |
| 0.80 | 84.6% | +6.36 ±1.43 |
| 0.90 | 66.2% | +14.93 ±1.96 |
| 1.05 | 39.7% | +36.48 ±3.20 |

Due-date failure is 13.68% at `pop_spend=0.80` and 68.71% at 1.05.

At 0.80, where the failure rate matches the published band, the agent is worth
6.36 points. The published industry benchmark for retry optimisation is a 6–8%
uplift. No parameter was fitted against either figure. Reproduce with
`py -3.12 scripts/spend_sweep.py`.

## Quickstart

Verified Windows commands:

```powershell
py -3.12 -m pip install numpy==2.4.2
py -3.12 -m agent.batch_report --pops 4
```

On macOS or Linux, use a Python 3.12 interpreter with NumPy 2.4.2 and replace
`py -3.12` with that interpreter's command.

Roughly 50 seconds. No API key, no network, no model download.

The run covers four populations of 100 customers and prints the headline beside
the baseline, then the supporting detail. Stopping rules that fired:

```
STOPPING RULES THAT FIRED, grouped by rule
  agent, deterministic
     COLLECTED             6172
     CYCLE_CLOSED           630
     ESCALATED               45
     AGENT_STOP               4
     MANDATE_DEAD             3
```

The gate counts actions it refused. An auditor that cannot import the gate
counts illegal actions that nevertheless executed. These are different
quantities; a clean run has zero in both columns for different reasons:

```
                   arm        rule  gate refused  illegal executed
  agent, deterministic         cap             0                 0
  agent, deterministic        peak             0                 0
  agent, deterministic        lead             0                 0
  agent, deterministic     pending             0                 0
  agent, deterministic   represent             0                 0
                             TOTAL             0                 0   over 8954 executed money actions
```

Querying the audit trail by `action_id` returns the full chain behind one
payment. The report chooses the first fully logged success, so its generated
action ID is run-specific. One example chain is:

```
  mandate c11m1
  WHAT THE BELIEF THOUGHT
     p(success) now 0.3114, best later 0.3081, index score +17.60  -> ok
  WHAT THE DIAGNOSER SAID, AND WHY
     root cause   INSUFFICIENT_FUNDS
     intervention RETRY  confidence 0.7
     source       fallback  prompt det-rules-v2
     rationale    Proceeding with the attempt our timing model scores highest…
     governance   ok=True
  ALL FIVE CONSTRAINT VERDICTS
     cap PASS   peak PASS   lead PASS   pending PASS   represent PASS
  THE MONEY ACTION
     Rs 630.00 at t=56 (day 2, hour 08), notified t=32, gate=ALLOWED
  THE OUTCOME
     OK  success=True  recovered Rs 630.00
```

That run writes 29,671 events to `agent/runs/batch_report_chain.jsonl`, one row
per event, append-only.

Two further offline commands:

```bash
py -3.12 scripts/prove_stage0_refuses.py
```

Runs the constraint layer against the Razorpay client with a transport that
raises if it is ever called, showing that an illegal action is refused before
any request is made. It then injects an action below the gate, which the auditor
detects from the log.

```bash
py -3.12 scripts/prove_workflows.py
```

Writes a funding reminder (no Payment Link), creates one last-attempt Payment
Link, GETs it, cancels it, and appends a merchant-queue row. Refuses to run
if the key is not `rzp_test_`. Does not charge a mandate. Set
`RECOVERY_NOTIFY_EMAIL` to have Razorpay email the backup link.

```bash
py -3.12 agent/eval/run_eval.py --llm --judge --replay
```

Replays the diagnosis eval from committed response caches. 0.5s, $0.00.

If `import numpy` fails, check the interpreter rather than the dependency list.
On this Windows machine, `python` resolves to Python 3.14 without NumPy;
`py -3.12` resolves to the verified Python 3.12 environment with NumPy 2.4.2.
`sim/gate.py` and the git hooks probe for an interpreter that can import NumPy
instead of trusting the executable name.

## How it works

Once a day, for each live mandate:

1. **Update the belief.** One distribution per customer covering the balance and
   which day of the 30-day cycle the salary lands. All of a customer's mandates
   share it, so an outcome on one subscription informs the timing of the others.
2. **Score today against later.** The belief gives the probability that a debit
   clears today and the best probability on any remaining day of the cycle. A
   negative index score means waiting has higher expected value.
3. **Diagnose the failure.** A language model assigns a root cause and selects an
   intervention: retry, nudge, escalate or stop. It falls back to a deterministic
   rule engine on any failure. For a reminder, a last-attempt checkout, and an
   escalate it also writes the customer or merchant copy.
4. **Check the action against the mandate rules.** At most four attempts per
   mandate per cycle; no execution during peak hours; at least 24 hours between
   the pre-debit notification and the debit; one pending notification per
   mandate; no re-presentation of an insufficient-funds decline under the old
   notification.
5. **Execute or refuse.** Refused actions never reach an executor. The executor
   is an interface with two implementations: the simulation and Razorpay's API.
   A retry is a debit. After the first or second insufficient-funds decline, a
   reminder is written (and emailed if SMTP is configured). After the third,
   a Payment Link replaces the fourth mandate debit: that debit is not fired
   while the link is open, and is not fired if the link expires or is
   cancelled unpaid, so the mandate survives into the next cycle. Escalate
   appends a merchant-queue file. It stops further debits only when the
   mandate itself is broken, the account is frozen, or funds are already
   claimed by another mandate.
6. **Record the outcome** in the belief, success or decline.
7. **Log the decision**, including days when nothing was attempted.

```mermaid
flowchart LR
  O["debit outcome<br/><i>success or decline</i>"] --> B["belief<br/>balance × payday<br/><b>one per customer</b>"]
  B --> T["timing<br/><i>attempt today, or wait</i>"]
  B -- "uncertainty only,<br/>never a balance" --> D["diagnosis<br/><i>which intervention, and why</i>"]
  D --> T
  T --> S{"constraint layer<br/><i>is this legal?</i>"}
  S -- refused --> A["audit log"]
  S -- allowed --> X["executor<br/>simulation · Razorpay API"]
  X --> A
  X --> O
  A -.-> R["independent recount<br/><i>shares no code with the enforcer</i>"]
```

Sharing one belief across a customer's mandates is worth **8.34 points**
(gate S2a_PD, shipping filter, ±1.36) at `pop_spend=1.05` and 3.38 points
at 0.80 (agent measurement, not gate-protected). Like the headline, it
varies with world hardness. This is the case for running the system at an
aggregator rather than at a single merchant.

Two boundaries are enforced in code, each with a test:

**The language model cannot choose when to debit.** It diagnoses why a payment
failed and selects among interventions. `Diagnosis` has no temporal field, so a
time cannot be expressed in its return type, and an import-graph test prevents
that layer from importing the belief, the timing code, the constraint layer or
the executor.

**The constraint layer refuses; a separate auditor recounts.** The gate counts
actions it stopped; the auditor counts illegal actions that occurred. The two
components share no code. `scripts/prove_stage0_refuses.py` moves money below
the gate and shows the auditor detecting it.

### Language model role

Every debit, stop, and escalate decision is deterministic. The model is invoked
only when `merchant_note` is non-empty — unstructured merchant input the rules
cannot read — and to write exception-facing copy (escalate briefs, audit
narrative). Reminder and backup-link text use templates.

| | rule engine | routed LLM (`merchant_note` only) |
|---|---|---|
| `merchant_note` cases (4 registered) | 4/4 | 2/4 |
| injection cases (3) | structural pass | structural pass |

The full 40-case table remains in the eval harness for history; it is not the
shipping path. Prompt `glm-diag-v3`, `reasoning_effort=low`. Measured 31 August
2026: `py -3.12 agent/eval/run_eval.py --llm --judge` ($0.08). Replay:
`--replay` from committed cache.

The money headline (`batch_report`) is filter plus rules only. `--demo-llm`
prints routing stats on one population; sim populations have no `merchant_note`
by default.

## Error handling

| Condition | Behaviour |
|---|---|
| The language model errors, times out, or returns unparseable output | Falls back to the rule engine on routed `merchant_note` ticks and logs `LLM_FAILURE`. |
| The model returns an instruction containing a time | Rejected by the governance check. `Diagnosis` has no field in which a time could be returned. |
| An action would breach a mandate rule | Refused before the executor is called, and the refusal is written to the audit trail. Refused actions generate no network traffic. |
| The payment rail is degraded across many customers | Detected from cross-customer outcomes; the belief update is suppressed, since a technical decline is a property of the rail rather than of one customer's balance. |
| A debit's outcome is unknown — a timeout or a deemed transaction | Recorded as pending. The rule engine stops further debits on that cycle. Retrying an unknown outcome is refused. |
| Reminder (after the 1st or 2nd insufficient-funds decline) | Writes the customer-facing copy to an outbox. Sends email only if SMTP is configured. Does not create a Payment Link and does not skip the remaining mandate attempts. |
| Last-attempt backup checkout | After a 3rd insufficient-funds decline, creates a Razorpay Payment Link. Email notify is on when `RECOVERY_NOTIFY_EMAIL` is set; SMS is off. The fourth mandate debit is not fired while the link is issued, paid, expired or cancelled. A paid link collects this cycle; an unpaid close holds the last attempt so the mandate is not killed. |
| Escalate | Appends `agent/runs/merchant_queue.jsonl`. Stops retries only for a broken mandate, a frozen account, or a lien. |
| An HTTP request is retried after a socket failure | The idempotency key is derived from the `action_id` already written to the audit trail, so the same logical debit produces the same key across a process restart. |
| Razorpay rejects the request itself — bad credentials, malformed body | Raises `RazorpayError` naming the HTTP status and response. Request-level rejections are not recorded as customer declines, since no payment was created. |
| A worker process dies during a measurement | The measurement raises. A crashed run is a failed result, not a missing one. |
| A second run appends to an existing audit log | Refused when the log is opened. |

## Timing sensitivity

Results depend on how accurately payday can be estimated in advance.
`payday_wait`, the baseline throughout, estimates the payday, waits for it, and
then attempts once a day.

*n=100, 8 held-out populations (seeds 700–707), 120 days, paired 2 SE.
Reproduce with `py -3.12 sim/headline.py`.*

| Payday known to | `payday_wait` | This agent | Difference |
|---|---|---|---|
| ±1 day | 99.24% | 96.44% | −2.81 ±0.46 |
| ±3 days | 94.65% | 96.06% | +1.41 ±1.08 |
| ±5 days | 72.18% | 95.38% | +23.20 ±2.57 |
| ±7 days | 59.14% | 95.63% | +36.48 ±3.20 |
| ±10 days | 48.11% | 94.14% | +46.03 ±3.62 |
| ±14 days | 40.01% | 91.99% | +51.98 ±2.66 |

The baseline is better when payday is known to about one day. At ±3 days the
agent is ahead by 1.41 points (2 SE 1.08). Beyond that the baseline degrades
sharply while the agent holds between 92% and 96%, because it recovers the
payday from observed outcomes instead of relying on the estimate it was given.

How accurately payday can be estimated in India is unknown; no measurement of it
was found. The agent therefore learns it online and reports its own uncertainty.

Two further results:

- The agent's action space is worth 1.371 points at 120 days, almost entirely by
  holding back a final attempt to avoid mandate death. It is 0.563 points at 60
  days and 1.790 at 180, so the value grows with the horizon.
- Outage detection works, but acting on it does not help. Pooling outcomes
  across merchants, the agent detects a degraded UPI rail with 0 false alarms in
  48 runs and a true-positive rate of 1.00 at n≥100. A single merchant sees 0.38
  attempts per 24-hour window against a floor of 8 and cannot evaluate the
  statistic. Pausing dispatch during a detected outage measured −0.529 points,
  so it is off by default.

Experimental design and bias analysis for all of the above:
[`docs/02_RESULTS.md`](docs/02_RESULTS.md).

## Decline states without a timing interpretation

Razorpay does not return NPCI decline codes. It normalises them into 110 error
reasons of its own, and mapping between the two surfaces two states that change
the correct action.

**`funds_blocked_by_mandate`** — the balance is present but another mandate has
claimed it. Retrying is wrong. A merchant seeing only its own debits cannot
distinguish this from an empty account.

**`deemed_transaction`** — the response was lost, so whether the debit went
through is unknown and the customer may already have been charged. Retrying
risks a double debit.

Neither state can be expressed as a timing decision: no combination of
"probability now" and "probability later" encodes *do not act, because the
question is unanswerable*. Both are routed by the diagnosis layer. Neither is
simulated yet; adding them to the world's decline mix at swept rates is queued
as W8 in [`docs/04_BUILD_PLAN.md`](docs/04_BUILD_PLAN.md).

## What is simulated

**Simulated.** Customer balances, salary dates and spending; debit outcomes;
outage scenarios; the merchant population; every percentage and rupee total in
this repository.

**From published sources.** NPCI's five mandate execution rules and decline code
list; Razorpay's error reasons, documented retry schedule, Payment Downtime API
shape and payment error surface. Every external claim carries a source tag in
[`docs/01_FACTS.md`](docs/01_FACTS.md).

**Unknown.** Real AutoPay decline frequencies. How accurately payday can be
predicted in India. Whether an aggregator may lawfully use one merchant's
outcomes to schedule another's debit for the same customer. Whether Razorpay
accepts the recurring-charge request body on an authorised mandate: that body
has never been submitted with one.

**Test-mode API writes.** The client authenticates. A last-attempt Payment Link
has been created, fetched and cancelled. Funding reminders do not create
Payment Links. Escalate is a local merchant-queue file. The AutoPay pre-debit
notification API is recorded locally and is not called.

## Limitations

- **No real transaction data.** There is no public dataset for payment retry
  scheduling. Every number here comes from simulation, validated against
  published aggregates rather than ground truth.
- **Decline frequencies are unpublished.** NPCI names the codes without ranking
  them, and no source gives AutoPay-specific rates. Every rate in this
  repository is swept rather than chosen, and reported as a curve. The largest
  single sensitivity: sweeping the limit-decline rate over 0.00 / 0.05 / 0.15
  costs 0.00 / −2.87 / −13.46 points.
- **The legal status of cross-merchant pooling is unresolved in Indian law.** No
  statute or RBI circular addresses it directly. The nearest instrument is the
  DPDP Act 2023, whose purpose-limitation and consent provisions were
  operationalised by the DPDP Rules notified on 14 November 2025, which points
  at consent-gating rather than prohibition. Pooling is therefore a per-customer
  permission, and the cost of withholding it is measured: running fully
  non-pooled costs 8.46 points at `pop_spend=1.05` and 3.38 at 0.80; pooling
  only for consenting customers costs 3.86 and 1.50 at half consent. Reproduce
  with `py -3.12 agent/tests/test_pooling_consent.py`.
- **Two calibration gates fail on monotonicity.** The belief filter models no
  balance floor at zero and approximates hourly spend jitter with a fixed 3-tap
  kernel. Both are structural and no parameter fixes either. Calibration error
  is inside its bound (ECE 0.026); the ordering of the reliability curve is
  what fails.
- **Sample size is bounded by compute.** n=100 × 5 mandates × 8 populations, one
  run seed each.

## Repository map

| Path | Contents |
|---|---|
| [`docs/08_ARCHITECTURE.md`](docs/08_ARCHITECTURE.md) | Architecture document: layers, interfaces, the decision rule, and what the headline is conditional on |
| [`docs/06_MODEL_CARD.md`](docs/06_MODEL_CARD.md) | What ships, what it is worth, and what it has not been tested on |
| [`docs/02_RESULTS.md`](docs/02_RESULTS.md) | Every result with its experimental design and bias analysis |
| [`docs/04_BUILD_PLAN.md`](docs/04_BUILD_PLAN.md) | Planned work and the validation suite |
| [`docs/03_ERRORS.md`](docs/03_ERRORS.md) | Defects found during development, each with its mechanism and the regression test added for it |
| [`docs/01_FACTS.md`](docs/01_FACTS.md) | External facts with source and confidence tags |
| [`docs/07_AGENT_BRIEF.md`](docs/07_AGENT_BRIEF.md) | Interface between the agent and the simulation |
| [`NOTES.md`](NOTES.md) | Append-only decision log |
| `agent/` | Policy, constraints, context, execution, LLM layer, audit trail, eval |
| `sim/` | The simulated world, the belief filters and the 25-gate suite |
| `scripts/` | Page data, constraint-layer demonstration, Razorpay connectivity ladder, test-mode workflow proof, calibration sweep, git hooks |

## Running the tests

```bash
py -3.12 sim/gate.py --tier fast
```

```bash
py -3.12 sim/gate.py --tier full
```

The fast tier (~35s idle) checks that behaviour has not changed. The full tier
(~100s idle) adds the statistical gates. Both roughly double on a busy machine;
the suite saturates eight worker processes. The full suite currently reports
23 pass and 4 known diagnostic failures, of 27 gates. The wrapper exits zero because all
four are named with reasons in `sim/known_failures.txt`; this is not a green
27/27 test result.

Individual agent gates:

```bash
py -3.12 agent/tests/test_stage0_enforces.py      # constraint layer, 20 checks
py -3.12 agent/tests/test_parity_vs_harness.py    # agent matches simulation bit-exactly
py -3.12 agent/tests/test_recovery_metric.py      # recovery metric, 5 mutants
py -3.12 agent/tests/test_workflows.py            # reminder vs last-attempt link vs queue
py -3.12 agent/tests/test_batch_ceiling.py        # batch legal ceiling holds when tripped
py -3.12 agent/tests/test_recovery_rules.py       # when to remind, when to hold the 4th debit
py -3.12 agent/tests/test_backup_loop.py          # full mode does not fire a 4th debit
py -3.12 agent/tests/test_fallback_safety.py      # unknown and terminal codes are not retried
```

Install the git hooks once per clone with `scripts/install-hooks.sh`. `git
commit` then runs the fast tier and `git push` runs the full tier.

---

Built for Razorpay's AI Buildathon, Track 3.
