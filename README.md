# UPI AutoPay Recovery Agent

An agent that schedules retries for failed subscription debits on UPI AutoPay.
It maintains a probability distribution over the customer's bank balance and
over the day their salary arrives, and attempts the debit when the balance is
likely to cover it.

All results below are simulated. No Razorpay transaction, mandate or decline
code has been observed by this project.

## Summary

Ten held-out populations of 500 simulated customers, 120 days: **99.12% of
billing cycles collected, against `payday_wait`'s 90.41%** — a difference of
+8.70 points, 2 SE 0.68 — measured at `payday_err=±7` and `pop_spend=0.93`, with
₹37,164,850 recovered across the batch. Zero constraint refusals, and an
independent recount from the audit log finds zero illegal actions, over 44,271
executed money actions. The headline is conditional on that payday-error regime:
a frozen two-attempt schedule collects more when payday is already known to
within three days.

Debit timing is a deterministic inference from censored payment outcomes. The
language model diagnoses the failure, selects the intervention and writes the
justification; it cannot choose when to debit, because the type it returns has
no field for a time and an import-graph test keeps it away from the timing code.
`n=500` was selected by measuring the same experiment at five sample sizes, not
inherited. One command reproduces the headline, and every figure in this
repository is checked against that run's own record.

## The problem

Indian subscription payments run on UPI AutoPay. A mandate authorises a merchant
to debit a customer once per billing cycle. If the account is short at the moment
of the charge, the debit is declined — and almost every decline is an empty
account rather than a broken instrument.

NPCI permits four attempts per mandate per cycle: one presentation and three
retries. Razorpay's documented schedule uses them on days T, T+1, T+2 and T+3,
then halts. That spends every attempt inside four days of the due date, which is
often before the customer is paid. A mandate that fails all four attempts dies
and forfeits its remaining billing cycles, so dunning harder costs the merchant
the customer.

The scarce resource is not attempts. It is information about when money arrives.

## Approach

Each decline is a measurement. A failed ₹550 debit proves the balance was below
₹550 at that hour; a successful one proves it was at least ₹550. These are
censored observations, and a Bayesian filter is the standard instrument for them.

Once a day, for each live mandate:

1. **Update the belief.** One distribution per customer covering the balance and
   which day of the 30-day cycle the salary lands. All of a customer's mandates
   share it, so an outcome on one subscription informs the timing of the others.
2. **Score today against later.** The belief gives the probability a debit clears
   tomorrow and the best probability on any remaining day of the cycle. A
   negative index score means waiting has higher expected value. On the last
   attempt a second test applies: spending it and failing kills the mandate, so
   it fires only when the odds cover the mandate's remaining cycles.
3. **Diagnose the failure.** A language model assigns a root cause and selects an
   intervention — retry, nudge, escalate or stop — and writes the justification
   attached to the money action. It falls back to a deterministic rule engine on
   any failure.
4. **Check the action against the mandate rules.** At most four attempts per
   mandate per cycle; no execution during peak hours; at least 24 hours between
   the pre-debit notification and the debit; one pending notification per
   mandate; no re-presentation of an insufficient-funds decline under the old
   notification.
5. **Execute or refuse.** Refused actions never reach an executor. After the
   first or second insufficient-funds decline a funding reminder is sent. After
   the third, a Payment Link replaces the fourth mandate debit, and that debit is
   not fired while the link is open or after it closes unpaid, so the mandate
   survives into the next cycle. Escalate appends a merchant-queue row.
6. **Record the outcome** in the belief, and **log the decision** — including the
   days on which nothing was attempted.

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

## What the language model does, and does not do

**It does:** assign a root cause, select among interventions, write the
human-readable justification attached to every money action, and write
merchant-facing copy for escalations. It is invoked only when `merchant_note` is
non-empty — unstructured merchant input the rules cannot read. Terminal decline
codes, indeterminate outcomes and technical-decline streaks stay on the rule
engine.

**The language model cannot decide when to debit.** That is enforced by
construction, not by review. Its only output type has **no temporal field**, so
an instruction like "retry at 11am" — hallucinated or injected through a merchant
note — has nowhere to land; `sim/verify_doc_contract.py` inspects the type and
exits non-zero the day someone adds one. An import-graph test prevents that layer
from importing the belief, the timing code, the constraint layer or the executor.
A separate check scans the merchant-facing prose for times, because a
justification recommending an hour puts the model on the timing path through the
merchant's eyeballs.

The model can still stop a cycle: `STOP` and `ESCALATE` are interventions it may
select, and both prevent further debits that cycle. What it cannot do is choose a
day or an hour for one. The scheduler is called with a fixed `RETRY` intent and
reads the belief, never the diagnosis.

Debit timing is a numerical inference from censored observations with a hard
legality boundary, and a language model on that path would make every money
action unreproducible. On the four registered cases the routed path actually
sees, the rule engine scores 4/4 and the model 2/4, so the model does not
currently beat the rules on its own path either. Every money figure in this
repository is produced with the deterministic path.

## What is implemented

Every layer is built and measured. Each row below states its evidence level,
because "the code exists", "a mock exercises it", "it would work against a real
key" and "it has run against a real key" are four different claims and only the
last one is a live integration.

| Level | Means |
|---|---|
| **IMPLEMENTED** | the code exists and is reachable; nothing exercises it end to end |
| **MOCK-VERIFIED** | a gate drives it against `MockRazorpayApi`, which answers the way Razorpay's documentation says Razorpay answers |
| **LIVE-READY** | the request shape is [VERIFIED] against current Razorpay documentation and the path has been driven with a real client against a stub transport; no live call has been made |
| **LIVE-DEMONSTRATED** | it has run against `api.razorpay.com` and a transcript is committed |

| | Level | Evidence |
|---|---|---|
| Belief filter, timing rule, constraint layer, audit trail, independent auditor | IMPLEMENTED | and measured — `sim/gate.py --tier full` |
| Simulation executor | IMPLEMENTED | reproduces the simulation harness bit-exactly, 24 of 24 runs |
| Live service: durable state, webhook ingestion, reconciliation, crash recovery, operator console | MOCK-VERIFIED | 7 gate files, 225 checks, `py -3.12 -m live.tests.run_all` |
| Razorpay test-mode API authentication | LIVE-DEMONSTRATED | HTTP 200 on `GET /v1/payments` with an `rzp_test_` key, transcript `logs/razorpay_ladder.json` |
| Funding reminders over SMTP | LIVE-DEMONSTRATED | delivered through a live test relay, transcript `logs/smtp_reminder_proof.json` |
| Razorpay test-mode Customer and Payment Link create / fetch / cancel | LIVE-READY | runnable against `rzp_test_` keys via `scripts/prove_workflows.py`; no transcript is committed |
| Mandate registration order (`POST /v1/orders`, `method: upi`) | LIVE-READY | body [VERIFIED] against Razorpay's authorisation-transaction reference and asserted by `agent/tests/test_razorpay_registration.py` on the request the client actually builds |
| Pre-debit notification order (`notification.token_id` / `payment_after`) | LIVE-READY | body [VERIFIED] against create-subsequent-payments; never accepted by Razorpay for a real mandate |
| `order.notification.delivered` webhook | IMPLEMENTED | never observed from Razorpay |
| Webhook signature verification, deduplication, out-of-order handling | MOCK-VERIFIED | signed payloads the mock rail produces, never one Razorpay sent |
| Recurring charge on an authorised mandate | IMPLEMENTED | **never submitted.** No mandate token exists to charge |

**Nothing in this repository is LIVE-DEMONSTRATED for the money path.** The two
rows that are demonstrated are an authentication probe and an email.

The last rows are limited by an account capability rather than by the client.
UPI AutoPay mandate registration is not available on the test account used here:
UPI and Recurring Payments are on-demand Razorpay features and were not
provisioned, so no mandate token exists for a request to reference.
Authenticating against the API is not the same as executing a payment, and a
successful order creation would not be proof of notification delivery. No result
in this repository depends on a live debit.

A passing mock suite and a live integration are different claims. The mock rail
answers the way Razorpay's documentation says Razorpay answers, and it declines,
loses responses, redelivers webhooks and delivers them out of order on purpose —
which is enough to test a state machine and not evidence about the real one.

## The live service

`live/` runs the same decision layers against Razorpay instead of the
simulation. The scheduler, the belief filter, the diagnosis layer and the
constraint gate are the same objects the batch run imports, not copies of them;
`live/tests/test_parity.py` asserts that by object identity. What differs is the
executor and the fact that state is durable.

```
Razorpay ──webhook──▶ verify signature ─▶ persist ─▶ 2xx
                                            │
                                            ▼
                              belief ─▶ scheduler ─▶ diagnosis
                                            │
                                            ▼
                                        Stage 0 ──refused──▶ audit
                                            │ allowed
                                            ▼
                                   RazorpayExecutor ─▶ Razorpay ─▶ UPI
```

Two switches decide what it can do, and they are separate on purpose.
`RECOVERY_MODE` picks the rail — `offline` uses a deterministic mock and cannot
reach the network, `live` reaches Razorpay. `RECOVERY_LIVE_DEBIT` decides
whether a debit may actually be submitted while in live mode, so that reading
production state and taking a customer's money are not the same gesture. Live
mode with a missing credential is an error; it never falls back to the mock.

There is no endpoint that accepts an amount, a token or a time. The only route
that can move money runs the whole chain and takes no request body at all: the
amount comes from the mandate the customer authorised, the hour from the belief
filter, and the legality from Stage 0.

## Results

Two quantities are reported, with different denominators. **Cycle collection**
counts every billing cycle due, including those that never failed, and is this
project's metric because it prices mandate death. **Recovery** counts only the
debits that would have failed on their due date, and is what published industry
figures measure.

Both run the same world: 500 customers, mandates per customer drawn from
`1 + Poisson(1)` capped at 8, 120 days, `payday_err=±7`, `pop_spend=0.93`, and 12
burn-in cycles discarded before measurement.

**Cycles collected.** *Full agent mode, 10 held-out populations (seeds 710–719),
run seed 7.* `py -3.12 -m agent.batch_report --pops 10 --canonical`, transcript
`logs/w30_headline_n500.txt`.

| | agent | `payday_wait` |
|---|---|---|
| Billing cycles collected | 99.12% | 90.41% |
| Mandates alive after 120 days | 99.95% | 90.70% |
| Recovered across the batch | ₹37,164,850 | — |

**+8.70 points, 2 SE 0.68.** Zero constraint refusals, and an independent recount
of zero illegal actions, over 44,271 executed money actions.

**That interval is the spread across populations at one run seed, and it is not
the whole uncertainty.** Repeating the same experiment on four independent run
seeds puts the uplift at 7.38, 7.71, 8.70 and 9.26 — a 1.89-point spread against
a within-seed interval of about 0.7. Almost all of it is the baseline: the
agent's own collection moves 0.18 points across those seeds and `payday_wait`
moves 2.06. `py -3.12 agent/tests/test_scale_n.py --seeds`, transcript
`logs/w29_scale_n.txt`.

**Recovery of debits that would fail on their due date.** Of 3,160 at-risk cycles
in the same run, 3,000 were collected — 94.94%, with 44.20% inside ten days and a
median of 11.7 days. First-presentation failure is 10.43%.

## Under what conditions those numbers hold

**The advantage depends on how well payday can be estimated.** The strongest
baseline is `[1,7]`: two attempts at frozen offsets from the same noisy payday
estimate the agent is given, selected once on training populations and then
frozen.

*Recovery of at-risk cycles, 10 held-out populations, `pop_spend=0.93`, run seed
907.* `py -3.12 agent/tests/test_steelman_schedule.py`, transcript
`logs/w30_steelman_n500.txt`.

| Payday known to | `[1,7]` | this agent | agent − `[1,7]` | paired 2 SE |
|---|---|---|---|---|
| ±1 day | 99.89% | 97.90% | −2.00 | 0.73 |
| ±3 days | 98.76% | 97.14% | −1.62 | 1.02 |
| ±5 days | 95.97% | 96.89% | +0.91 | 0.96 |
| ±7 days | 91.86% | 94.02% | +2.16 | 1.27 |
| ±10 days | 71.35% | 91.40% | +20.05 | 3.25 |
| ±14 days | 55.69% | 89.59% | +33.90 | 3.45 |

The agent is behind at ±1 and ±3 by more than the measurement error, level at ±5,
and ahead from ±7 upward. It is nearly flat across the range because it recovers
payday from observed outcomes; the frozen schedule collapses because it cannot.
The frozen schedule also loses no mandates at any level tested — 100.0%
survival against the agent's 99.0–99.5%. **How accurately payday can be estimated
in India is unmeasured**, and what payroll evidence exists points at the
predictable end, where the frozen schedule wins.

**The advantage also depends on how hard the world is.** `pop_spend` is the share
of a salary a customer spends per cycle, set to one minus India's household
saving rate. Three published readings put it between 0.80 and 0.93, and no point
inside that range is declared.

*Cycles collected, same 10 populations, `payday_err=±7`, run seed 7.*
`py -3.12 agent/tests/test_conditional_headline.py`, transcript
`logs/w30_conditional_n500.txt`.

| `pop_spend` | `payday_wait` | agent | difference | at-risk cycles |
|---|---|---|---|---|
| 0.80 | 99.06% | 99.96% | +0.89 ±0.24 | 49 |
| 0.85 | 97.40% | 99.78% | +2.38 ±0.57 | 493 |
| 0.88 | 95.77% | 99.64% | +3.88 ±0.74 | 1,133 |
| 0.90 | 94.21% | 99.42% | +5.21 ±0.74 | 1,766 |
| 0.93 | 90.41% | 99.12% | +8.70 ±0.68 | 3,160 |

Both arms collect every cycle that was never at risk, so the last column carries
the entire difference. At `pop_spend=0.80` there are 49 such cycles across five
thousand customers, so that row is a thin measurement rather than a
representative one. 0.93 is the end of the range where the world's due-date
failure rate falls inside the published 8–15% band.

**External validation is 2 of 4.** There is no public benchmark for payment retry
scheduling, so the world is scored against aggregate statistics published by
companies that sell recovery software.

*`pop_spend=0.93`, run seed 907.* Transcripts `logs/w30_v3_power_n500.txt` and
`logs/w30_canonical_n500.txt`.

| | measured | populations | published | |
|---|---|---|---|---|
| Share of debits failing on their due date | 10.62% ±0.24 | 100 | 8–15% | hit |
| Recovery under a fixed-interval retry schedule | 21.80% ±0.71 | 100 | 20–40% | hit |
| Recovery under smart retry timing | 94.19% ±0.72 | 20 | 70–85% | miss, too high |
| Share of recoveries landing inside 10 days | 42.90% | 20 | 85–95% | miss, too slow |

The first two rows are properties of the world and of a policy this project did
not write; the second two measure the agent. Row two landing inside its band, by
more than its measurement error, is the argument that the excess in row three
belongs to the agent rather than to an easy world.

Both misses are attributed. Recovery is too high because no simulated customer is
permanently unable to pay: a clairvoyant schedule obeying the same four-attempt
cap and notice rules collects 100%, so that row measures the agent rather than
the world. Recovery is too slow because due dates and paydays are drawn
independently; the same clairvoyant schedule reaches only 51.9% inside ten days,
below the published band's floor, and the agent reaches 82.6% of what is
available. The published band is drawn from card dunning, where a customer can
fix the instrument on demand.

Full experimental design, external validation, uncertainty treatment and negative
results: [`docs/results.md`](docs/results.md).

## Limitations

- **No real transaction data.** Every number here is simulation, validated
  against published aggregates rather than ground truth. The simulation and the
  agent share an author.
- **A frozen two-offset schedule beats the agent when payday is predictable**,
  and Indian payroll practice points at that end of the range. The agent's value
  there is that collection does not depend on the payday estimate being good.
  The headline is measured under a ±7-day payday-error regime and should be read
  as conditional on it; the full curve from ±1 to ±14 is above.
- **The published interval understates the uncertainty.** Every headline is one
  run seed. The measured spread across four seeds is 1.89 points on an uplift of
  about 8.7, and it is carried almost entirely by the baseline arm.
- **Decline frequencies are unpublished.** Every rate is swept rather than
  chosen. The largest single sensitivity is the limit-decline rate.
- **Pooling is measured, and in the canonical world its incremental effect is
  not distinguishable from zero.** One belief per customer, shared across their
  mandates, is implemented and exercised by every run; it is the reason the
  design argues for an aggregator rather than a merchant. Running fully
  non-pooled costs 0.16 points against a 2 SE of 0.16 here, and 6.47 points in a
  world where every customer holds five mandates. Published aggregates put the
  mean mandate count in the range 1 to 3 and the canonical world uses 2; the
  exact distribution is a modelling approximation. The aggregator argument is
  conditional on that count, and how much turns on it is measured rather than
  assumed.
- **The legal treatment of cross-merchant pooling has not been established in
  Indian law**, is jurisdiction- and provider-dependent, and is outside the
  scope of this engineering evaluation. No statute or RBI circular addresses it
  directly, and the nearest instrument points at consent-gating rather than
  prohibition. Pooling is a per-customer permission for that reason, and the
  cost of withholding it is measured. **Engineering consent is not a legal
  conclusion.**
- **The belief is not well calibrated.** Two gates fail on monotonicity because
  the filter's diffusion leaks through the balance floor it does model. A repair
  exists, is measured, and is off because it kills more mandates at the shipping
  horizon.
- **The live path has never met Razorpay.** Every part of it — the pre-debit
  order, the recurring charge, the webhook handler, the state machine, the
  reconciliation — is exercised only against a mock rail written from
  Razorpay's documentation. Their real API has read none of these request
  bodies, so the request shapes are a hypothesis with a citation, not a
  verified contract.
- **The customer's income and payday are supplied, not inferred.** The belief
  filter needs a starting salary and salary date, and a live integration has no
  oracle for either: the merchant knows the subscription price, not the
  customer's pay cycle. The live service takes both as an operator estimate and
  records them as such. Nothing here measures how wrong they are on real
  customers, and every live timing decision inherits that.
- **Sample size is bounded by compute**: 500 customers, about 2 mandates each,
  10 populations, one run seed per published table. `n` was selected by
  measuring the experiment at 100, 250, 500, 1,000 and 2,000; n=500 sits within
  0.19 points of n=2,000 on the uplift.

## Running it

Verified Windows commands. On macOS or Linux, replace `py -3.12` with a Python
3.12 interpreter carrying NumPy 2.4.2.

```powershell
py -3.12 -m pip install numpy==2.4.2
py -3.12 -m agent.batch_report --pops 10 --canonical
```

Roughly three minutes. No API key, no network, no model download. It runs ten
populations of 500 customers and prints the headline beside the baseline:

```
                   arm  cycles collected    Rs recovered  survival  att/cycle    2 SE
   payday_wait (rival)            90.41%              --    90.70%      1.276
  agent, deterministic            99.12%   Rs 37,164,850    99.95%      1.461   0.681

  agent, deterministic vs payday_wait: +8.70 pts (2 SE 0.68, SIG)
```

then the stopping rules that fired, the constraint gate's refusals beside an
independent recount from the audit log, and the full chain behind one payment —
what the belief predicted, what the diagnoser concluded, all five constraint
verdicts, the money action and its outcome.

Adding `--emit` writes `sim/canonical_result.json`. Every headline figure in this
README, in `docs/results.md` and on the page is checked against that file by
`sim/verify_claims.py`, so a number cannot be edited in one place and left
elsewhere.

Three further offline proofs:

```bash
py -3.12 scripts/prove_stage0_refuses.py
```

Runs the constraint layer against the Razorpay client with a transport that
raises if it is ever called, showing that an illegal action is refused before any
request is made. It then injects an action below the gate, which the auditor
detects from the log alone. No key, no network, no simulation.

```bash
py -3.12 scripts/prove_workflows.py
```

Writes a funding reminder, creates one last-attempt Payment Link, fetches it,
cancels it, and appends a merchant-queue row against `rzp_test_` keys. It refuses
to run on any other key and does not charge a mandate. It needs live test-mode
credentials and writes no committed transcript.

```bash
py -3.12 agent/eval/run_eval.py --llm --judge --replay
```

Replays the diagnosis eval from committed response caches. 0.5s, $0.00.

### The live service and its console

```bash
py -3.12 -m live.server
```

Starts on `127.0.0.1:8730` in offline mode against the mock rail. The console is
at the root; `/health` and `/ready` need no token. Registering a mandate,
authorising it and running decision ticks all work there without a key, without
the network and without money — the mock declines, loses responses and
redelivers webhooks on purpose, and every one of them goes through the same
signature verification and the same state machine the live path uses.

Live mode needs `RECOVERY_MODE=live` and three credentials by name —
`RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` — and
refuses to start without them rather than falling back. Submitting a real debit
needs `RECOVERY_LIVE_DEBIT=yes` as well. Binding anything but loopback needs
`RECOVERY_OPERATOR_TOKEN`. Values belong in `.env`, which is gitignored.

```bash
py -3.12 -m live.tests.run_all
```

Seven gate files, 225 checks, about four seconds. Configuration, the state
machine, webhooks, the full lifecycle across seven crash boundaries, the safety
boundaries, simulation/live parity, and the HTTP surface over a real socket.
Every one runs offline and none can move money.

If `import numpy` fails, check the interpreter rather than the dependency list.
`sim/gate.py` and the git hooks probe for an interpreter that can import NumPy
instead of trusting the executable name.

### Tests

```bash
py -3.12 sim/gate.py --tier fast
py -3.12 sim/gate.py --tier full
```

The fast tier checks that behaviour has not changed; the full tier adds the
statistical gates. The full suite reports **23 pass and 4 known diagnostic
failures, of 27 gates**. The wrapper exits zero because all four are named with
written reasons in `sim/known_failures.txt`; this is not a green 27/27 result.
The four, and why each is left red, are in
[`docs/results.md`](docs/results.md#gate-status); the full roster with each
gate's state is printed by the suite itself.

Install the git hooks once per clone with `scripts/install-hooks.sh`. `git commit`
then runs the fast tier, the documentation checks and their self-tests, and
`git push` runs the full tier.

## Reading further

| | |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Components, interfaces, the decision rule, and the invariant each boundary enforces |
| [`docs/results.md`](docs/results.md) | Every result with its experimental design, the model configuration, uncertainty, negative results and sources |
| [`docs/errors.md`](docs/errors.md) | Defects found in this project's own work, by class, each with the control added for it |
| [`docs/index.html`](docs/index.html) | Interactive walkthrough of one customer's month. Static page: `python -m http.server --directory docs` |

| Path | Contents |
|---|---|
| `agent/` | Policy, constraints, context, execution, LLM layer, audit trail, eval |
| `sim/` | The simulated world, the belief filters, the 27-gate suite, the documentation checkers |
| `live/` | The service that runs the same decision layers against Razorpay: durable state, webhook ingestion, reconciliation, the operator console, and its gates |
| `scripts/` | Page data, constraint-layer proof, Razorpay connectivity ladder, test-mode workflow proof, sweeps, git hooks |
| `logs/` | Committed transcripts for every figure quoted above |

---

Built for Razorpay's AI Buildathon, Track 3.
