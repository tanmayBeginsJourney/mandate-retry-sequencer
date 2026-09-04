# Architecture

The system schedules retries for failed subscription debits on UPI AutoPay. It
keeps a probability distribution over each customer's bank balance and over the
day their salary arrives, updates that distribution from debit outcomes, and
attempts the debit on the day the money is most likely to be there — inside the
mandate rules NPCI imposes.

This document describes the components, the interfaces between them, and the
invariant each boundary enforces. Measured results are in
[results.md](results.md). Defects found during development, and the controls
added for them, are in [errors.md](errors.md).

---

## The problem the design is shaped by

A UPI AutoPay mandate authorises a merchant to debit a customer once per billing
cycle. NPCI permits four attempts per mandate per cycle: one presentation and
three retries. Razorpay's documented schedule spends them on days T, T+1, T+2
and T+3, then halts. A mandate that fails all four attempts dies and forfeits
its remaining billing cycles.

Almost every failure is an empty account rather than a broken instrument. Four
attempts inside four days therefore spend the whole allowance while the account
is still empty. The scarce resource is not attempts; it is information about
when money arrives.

Each declined debit carries that information. A failed ₹550 debit proves the
balance was below ₹550 at that hour; a successful one proves it was at least
₹550. These are censored observations, and a Bayesian filter is the standard
instrument for them.

---

## Layers

Each layer owns one decision and cannot make the others.

| Layer | Decides | Code |
|---|---|---|
| Context | whether the payment rail itself is degraded | `agent/context/` |
| Policy | **when** to attempt: which mandate, which day, which hour | `agent/policy/`, wrapping `sim/w3.py` |
| Diagnosis | **what** to do and **why**: root cause, intervention, the justification attached to every money action | `agent/llm/`, an overlay over a deterministic rule engine |
| Constraints | **whether the action is allowed** | `agent/constraints/` |
| Execution | performs the action against a backend | `agent/execution/` |
| Audit | records what happened, and independently re-derives whether it was legal | `agent/audit/`, `agent/constraints/auditor.py` |

`agent/ports.py` holds the shared vocabulary — `Diagnosis`, `AttemptOutcome`,
`ScheduleProposal`, `StopRule`, the Razorpay reason-to-family map — and imports
nothing from `agent/`. `agent/batch.py` is the composition root and the only
place an executor backend is selected.

### Data flow, one decision hour

```
                       one decision hour, one customer
                                     |
   CONTEXT      rail_monitor.py      v   cross-customer outage test over a
                rolling 24h window of every merchant's outcomes. Requires
                time-major iteration; raises NonMonotonicTime otherwise.
                                     |
   POLICY       belief_book.py       v   ONE BeliefPD per CUSTOMER, shared by
                that customer's mandates. timing.py scores today against the
                best remaining day and returns (mandate, target hour) or WAIT.
                                     |
   DIAGNOSIS    caseview.py          v   redaction boundary: the diagnoser
                receives decline history and uncertainty, never a balance.
                fallback.py is the deterministic rule engine and the default;
                model_diagnoser.py is an overlay.
                -> Diagnosis {root cause, intervention, rationale}
                   NO TIME FIELD
                                     |
   STAGE 0      stage0.py            v   enforces five mandate rules against
                its own ledger (rules.py).
                -> Allowed(outcome) | Refused(rule)
                                     |
   EXECUTION    ports.Executor       v   sim_executor.py (the simulation) or
                razorpay_executor.py (Razorpay's HTTP API).
                                     |
   AUDIT        log.py               v   append-only JSONL, one row per event.
                auditor.py rebuilds legality from the log alone and may not
                import rules.py or stage0.py.
```

The outcome is written back into the belief, and the audit log records the
decision — including the days on which nothing was attempted.

---

## Timing

At each decision hour the filter produces a posterior over the balance for every
remaining day of the cycle. For a mandate with attempts left:

```
p_now    = P(a debit of `amount` succeeds tomorrow)
p_later  = max over the remaining legal days of the cycle,
           or 0 when this is the last attempt
score    = amount * (p_now - 0.92 * p_later)
score <= 0  ->  wait
```

`p_later` is zero on the last attempt because there is no later opportunity, so
waiting has no option value. The 0.92 is a hand-chosen discount on deferring;
its sensitivity is measured in [results.md](results.md#the-discount-factor).

The last attempt carries a second test. Spending it and failing kills the
mandate and forfeits its remaining cycles; reaching the end of the cycle with an
attempt unspent keeps them. The rule is

```
fire iff p / (1 - p) > cycles_left * cycle_value        cycle_value = 0.6
```

derived from that asymmetry rather than chosen. Without it the last attempt
fires whenever `p_now > 0`, because `score = amount * p_now` is positive for any
positive probability.

This is a one-step lookahead in the style of a Whittle index. There is no
exploration/exploitation trade, no learned index and no indexability proof. A
full backward-induction extension of the continuation value to every attempt was
built, measured and rejected; it ships off. See
[results.md](results.md#negative-results).

### Belief

`w3.BeliefPD` maintains a joint distribution over the balance, binned, and over
which day of the 30-day cycle the salary lands. `advance(day)` drains it by the
modelled daily spend; `observe(amount, success)` truncates it against the
outcome. The shipping configuration:

```python
FITTED_BELIEF = dict(stride=1, prior_w=5, prior_day0=8.0,
                     prior_floor=0.1, spend_beta=0.0)
```

Other constants a reader needs: `NPCI_MAX = 4` attempts per mandate per cycle,
`DECISION_HOUR = 8`, `PEAK = {10, 11, 12, 17, 18, 19, 20, 21}`,
`LOOKAHEAD_DAYS = 12`, `P_TECH = 0.008`. `sim/verify_doc_contract.py` asserts
that these values, and the decision rule above, match the code.

### One belief per customer

`BeliefBook` holds one `BeliefPD` per **customer**, not per mandate. An outcome
on any of that customer's subscriptions updates the estimate used to time all of
them. A single merchant sees one debit per customer per month; an aggregator
carrying several of that customer's subscriptions sees several, and payday
discovery is limited by observation volume.

`agent/tests/test_one_belief.py` asserts the sharing. `BeliefBook` accepts
`pooling` in `{"all", "none", "consented"}` plus a per-customer consent set, so
the non-pooled and consent-gated configurations are runnable and measurable
rather than hypothetical.

**What it is worth depends on how many mandates a customer holds, and how much
turns on that count has been measured.** In the canonical world,
where a customer holds about two, withholding pooling costs 0.16 points against
a 2 SE of 0.16 — not distinguishable from zero. At five mandates per customer it
costs 6.47. The mechanism is implemented and measured; the size of its effect is
conditional on a mandate count derived from published aggregates rather than
observed directly — [results.md](results.md#pooling).

Whether a payment aggregator may lawfully use one merchant's transaction
outcomes to schedule another merchant's debit for the same customer **has not
been established** in Indian law. That treatment is jurisdiction- and
provider-dependent and is outside the scope of this engineering evaluation. The
system is built so that either answer is a configuration rather than a rewrite.

---

## The language model, and where it is excluded

The diagnosis layer assigns a root cause, selects among interventions — retry,
nudge, escalate, stop — and writes the human-readable justification attached to
every money action. It also writes merchant-facing copy for escalations.
Reminder and backup-link text use templates.

**The language model cannot decide when to debit.** This is enforced by
construction, not by review:

- The layer's only output type is `ports.Diagnosis`, and it has **no temporal
  field**. An instruction such as "retry at 11am" — hallucinated, or injected
  through a merchant note — has nowhere to land.
  `agent/eval/injection.py:diagnosis_has_temporal_field()` inspects the type and
  fails the day someone adds one. The failure is a non-zero exit from
  `sim/verify_doc_contract.py`, which the pre-commit hook runs, and it carries a
  canary — a synthetic field list containing `retry_after_hours` that the same
  matcher must flag — so the check cannot pass by having stopped working.
- `agent/tests/test_layer_isolation.py` asserts that `agent/llm/**` does not
  import `agent.policy`, `w3` or `harness`, and that `agent/policy/**` does not
  import `agent.llm`.
- `agent/llm/governance.py` scans merchant-facing prose for times, because a
  justification that recommends an hour puts the model on the timing path
  through the merchant's eyeballs.

What the model can still do is end a cycle: `STOP` and `ESCALATE` are
interventions it may select, and both prevent further debits for that mandate in
that cycle. What it cannot do is name a day or an hour. `agent/loop.py` calls
`propose()` with a fixed `RETRY` intent and the belief; no field of the
`Diagnosis` reaches the scheduler.

Debit timing is a numerical inference from censored observations with a hard
legality boundary. A language model on that path would make every money action
unreproducible. Diagnosis, explanation and outreach copy have the opposite
shape.

The model is invoked only when `merchant_note` is non-empty — unstructured
merchant input the rules cannot read. Terminal decline codes, indeterminate
outcomes, wide uncertainty bands and technical-decline streaks stay on the rule
engine. Any model failure — error, timeout, unparseable output — falls back to
the rule engine and logs `LLM_FAILURE`. Every money figure in
[results.md](results.md) is produced with the deterministic path only.

`agent/llm/caseview.py` is the redaction boundary: the case handed to the model
carries decline history and uncertainty, never a balance or a salary. Model
calls are bounded per run by a hard cap on network calls; cache hits are free
and do not count, so the cap bites on novelty rather than on volume.

---

## Constraints

Stage 0 adjudicates every proposed money action **before** the executor is
reached, so a refused action produces no network traffic against either backend.

| Rule | Constraint |
|---|---|
| `cap` | at most 4 attempts per mandate per billing cycle |
| `peak` | no execution during 10:00–13:00 or 17:00–21:30 |
| `lead` | at least 24 hours between the pre-debit notification and the debit |
| `pending` | at most one pending notification per mandate |
| `represent` | an insufficient-funds decline may not be re-presented under the old notification; a technical decline may |

All five are second-hand summaries of NPCI requirements rather than regulation
read end to end; sources and their confidence tags are in
[results.md](results.md#sources). All five have a working mutation test in the
simulation suite.

### Enforcement, and an independent recount

`Stage0Gate` refuses illegal actions and counts what it stopped.
`agent/constraints/auditor.py` reconstructs legality **from the audit log alone**
and counts illegal actions that nevertheless occurred. The two share no code:
gate `I3` fails if `auditor.py` imports `rules.py` or `stage0.py`.

The two counts measure different quantities. On a clean run both are zero for
unrelated reasons — the gate refused nothing because nothing illegal was
proposed, and the auditor found nothing because nothing illegal happened. Two
zeros are not a cross-check. The auditor is exercised only when the enforcer has
already failed, so that regime is produced deliberately:
`scripts/prove_stage0_refuses.py` writes a debit below the gate, touching no
counter, and the auditor detects it from the log.

The split has caught a real defect once. Pausing dispatch during an outage
dropped a pending notification without writing anything to the log, so from the
log alone a withdrawn notification was indistinguishable from a live one. The
gate reported zero violations and the auditor reported dozens. The auditor was
right, and the repair went into the audit trail — a `NOTIFICATION_CANCELLED`
event — never into the auditor.

### Stopping

Stopping is explicit and counted. Nine named rules: `COLLECTED`, `CAP_REACHED`,
`CYCLE_CLOSED`, `NO_LEGAL_SLOT`, `MANDATE_DEAD`, `ESCALATED`, `AGENT_STOP` (the
diagnosis layer chose to stop), `LAST_ATTEMPT_HELD` (the fourth debit was
replaced by an unpaid backup checkout), and `BATCH_LEGAL_CEILING` — a circuit
breaker at `n_mandates × 4 × cycles in the horizon` that holds remaining live
mandates rather than exceeding the batch's legal allowance. It is expected to be
zero on a clean run and fires under a lowered ceiling in
`agent/tests/test_batch_ceiling.py`.

---

## Execution

`ports.Executor` is the interface. Two implementations exist, and the loop, the
belief, Stage 0, the auditor and the audit trail are byte-identical against
either.

| Method | Behaviour |
|---|---|
| `attempt()` | the debit |
| `notify()` | the regulatory pre-debit notification |
| `remind()` | a funding reminder after the first or second insufficient-funds decline. Writes an outbox row, and sends email through a generic SMTP path when one is configured. Does not create a Payment Link and does not consume a mandate attempt |
| `backup_checkout()` | after a third insufficient-funds decline, a Payment Link replaces the fourth mandate debit. That debit is not fired while the link is open, and not fired if the link expires or is cancelled unpaid, so the mandate survives into the next cycle |
| `escalate()` | appends a merchant-queue row and halts further debits for the cycle |

Gate `I2` permits only `constraints/stage0.py` and the composition root to hold
an executor, which is why the backend can be swapped without any other layer
knowing. `I2T` allows a test to hold one if the test says so in an
`# I2-EXEMPT:` line naming why, and `I6` keeps the execution layer from reaching
back up into the layers that decide on its behalf. `L1` and `L2` extend the same
discipline to `live/`: only `live/service.py` may hold an executor, and nothing
under `agent/` may import `live/`. The import-graph rules are listed in
[results.md](results.md#the-import-graph-gates).

`agent/execution/razorpay_api.py` owns the HTTP: URLs, authentication, request
bodies and the four things a payment API can do to a caller. It is the only
module in the repository that knows a Razorpay URL.
`agent/execution/razorpay_mock.py` implements the same surface without a socket.
`RazorpayExecutor` turns those answers into `agent/ports.py` vocabulary and
touches the network through nothing else.

**Four outcomes, not two.** `OK` — the provider acted and said so. `REJECTED` —
a 4xx naming a request problem; the provider did not act. `DENIED` — 401 or 403,
so the credential was refused and nothing reached payment processing. `LOST` —
no response at all, so the provider may have acted. Collapsing `LOST` into a
failure is what turns a dropped connection into a statement about a customer's
balance, and collapsing `DENIED` into one is
[errors.md](errors.md)'s "An authentication failure recorded as a statement
about the customer's balance".

### `SimExecutor`

Runs against the world generated by `sim/w3.py`.
`agent/tests/test_parity_vs_harness.py` asserts that the agent in degenerate
mode — retry only, deterministic diagnoser — reproduces
`harness.run("solo_shared_pd", ...)` bit-exactly, 24 of 24 runs at two operating
points. Every difference between degenerate mode and any richer arm is therefore
attributable to the agent rather than to the timing model.

### `RazorpayExecutor`

Speaks Razorpay's HTTP API over `urllib`, with no added dependency. The executor
is implemented and selectable at the composition root, and test-mode
connectivity and workflow calls have been exercised against the live API. What
is not claimed is a successful authorised recurring debit: that transaction is
not present in the evidence below, so no figure in this repository rests on one.
Each row states its evidence level rather than a pass or fail:

| | Evidence |
|---|---|
| Test-mode API authentication | **exercised** — HTTP 200 on `GET /v1/payments` with an `rzp_test_` key, transcript `logs/razorpay_ladder.json` |
| SMTP reminder delivery | **exercised** — `scripts/prove_smtp_reminder.py`, transcript `logs/smtp_reminder_proof.json` |
| Test-mode Customer creation | implemented, runs against `rzp_test_` keys via `scripts/prove_workflows.py`; **no transcript is committed**, so the repository holds no record that it ran |
| Test-mode Payment Link create, fetch, cancel | same script, same status: runnable, no committed transcript |
| Pre-debit notification order — `POST /v1/orders` carrying the `notification` object | implemented; **not demonstrated** against an authorised mandate |
| `order.notification.delivered` webhook | **not observed** from Razorpay |
| Webhook signature verification, deduplication, out-of-order handling | implemented; exercised against payloads the mock rail signs, **never against one Razorpay sent** |
| Durable state, crash recovery, reconciliation | implemented; exercised against the mock rail, `py -3.12 -m live.tests.run_all` |
| Recurring charge on an authorised mandate | implemented; **never submitted**, so not demonstrated |

The reason is an account capability, not a gap in the client. UPI AutoPay
mandate registration is not available on the test account used here: UPI and
Recurring Payments are on-demand Razorpay features and were not provisioned, so
no `token_id` exists for any request to reference. A successful `POST /v1/orders`
would not be proof of delivery in any case; only the
`order.notification.delivered` event is.

A request-level rejection — bad credentials, malformed body — raises
`RazorpayError` naming the HTTP status. It is never recorded as a customer
decline, because no payment was created. Recording it as a decline would teach
the belief filter that the account was empty, for every one of that customer's
mandates at once.

**Two clocks meet at one field.** Stage 0 reads `target_t` as simulated hours —
the peak rule is `target_t % 24` — while Razorpay wants `payment_after` as a
future Unix epoch second. No single integer is both. The conversion happens in
one place, `RazorpayExecutor._epoch`, from an `epoch_origin` the service anchors
to a local midnight so that `target_t % 24` is the real hour of the day. Without
an origin the executor refuses to create an order rather than sending a
timestamp in 1970. `scripts/prove_stage0_refuses.py` drives the constraint layer
end to end through the executor against a transport that raises if an illegal
action ever reaches it.

**Idempotency.** No idempotency header is sent on the recurring charge, because
Razorpay documents none for that endpoint. The headers they do document —
`X-Payout-Idempotency` for RazorpayX payouts and composite APIs,
`X-Refund-Idempotency` for instant refunds — cover neither this path nor
anything on it. A header the provider ignores reads, in the code and in a
review, like a guarantee that is not there.

Two documented properties stand in its place. An order's `receipt` "has to be
unique" and is treated as an idempotency key, so a deterministic receipt makes
order creation idempotent and `GET /v1/orders?receipt=` recovers an order whose
id a crash lost. And "no further payment requests are permitted once the order
moves to the `paid` state", so one order per debit attempt makes a *collected*
debit at-most-once at the provider.

That is weaker than an idempotency key in two ways, both stated rather than
glossed. A retried submission gets a rejection rather than a replayed result,
so the caller still has to go and look. And the closure is documented only for
`paid`: an order whose payment failed stays `attempted`, and the documentation
does not say a further payment against it is refused. The client sets
`payment_capture: true` so authorisation and capture are not two windows, and
the service never resubmits against an unresolved attempt. This is not
exactly-once and is not claimed to be.

---

## The live service

`live/` runs the same decision layers against Razorpay. It owns durable state,
the mandate lifecycle, webhook ingestion and the HTTP surface. It owns no
decision: timing comes from `agent/policy/timing.py`, legality from
`agent/constraints/stage0.py`, diagnosis from `agent/llm/`. Those are the same
objects `agent/batch.py` imports, and `live/tests/test_parity.py` asserts it by
object identity rather than by inspection.

`live/service.py` is the second composition root, and gate `L1` keeps it the
only module in the package that may hold an executor. Gate `L2` keeps the
dependency pointing one way: nothing under `agent/` imports `live/`, so the
simulation does not need a database to run.

### Two modes, and the direction each fails in

`RECOVERY_MODE` picks the rail. `offline` uses the mock and cannot reach the
network. `live` reaches Razorpay. An unset variable is `offline`; a misspelled
one raises rather than guessing.

`RECOVERY_LIVE_DEBIT` decides whether a debit may be submitted while in live
mode. It is separate because reading a mandate's state, replaying a webhook and
rendering the console are all things worth doing against the live rail without
charging anybody.

Live mode with a missing credential is an error, never a quiet demotion to the
mock. Offline mode cannot reach the network whatever the environment holds. Both
directions have been wrong in real systems and only one of them is loud.

### The durable model

Four entities, and deliberately not a copy of Razorpay's object model. A payment
entity carries thirty-odd fields; what is stored is what changes a decision,
closes a reconciliation, or lets a human find the transaction on Razorpay's
dashboard.

| Entity | Holds |
|---|---|
| `Customer` | our id and theirs, joined; nothing derives one from the other |
| `Mandate` | token id, provider token status, the amount ceiling the customer authorised, the registration order and payment, the cold-start estimates, the cycle |
| `PaymentAttempt` | Stage 0's `action_id` as its primary key, the order and payment ids, the deterministic receipt, the target hour and the epoch second it maps to, the outcome |
| `WebhookEvent` | the provider's event id as primary key, the raw body, whether the signature verified, and what interpreting it did |

Three constraints are enforced by the schema rather than by code.
`webhook_events.event_id` is the primary key, so a duplicate delivery is
rejected by the database and not by a check-then-insert two concurrent requests
can both pass. `attempts.receipt` is unique, so two attempts cannot claim one
provider order. `attempts.id` is Stage 0's `action_id`, so re-deriving an
attempt after a restart collides with the existing row instead of creating a
twin.

Every state change is also appended to a `transitions` table. The entity tables
hold the current answer; that one holds how it got there, which is what a
reconciliation dispute needs.

### The two state machines

A mandate is `PENDING`, `ACTIVE`, `REJECTED`, `CANCELLED` or `PAUSED`, mapped
from the provider's `recurring_details.status`. Only `ACTIVE` may be charged,
and only a `confirmed` token is `ACTIVE`. An order existing is not
authorisation, and neither is a 200. `REJECTED` and `CANCELLED` are final;
`PAUSED` is not, because a customer can resume a paused UPI mandate.

An attempt runs `INTENT → ORDER_CREATED → NOTIFIED → SUBMITTED → AUTHORIZED →
SUCCEEDED`, with `FAILED` as the other terminal and `UNKNOWN` for an outcome
nobody knows. Every transition goes through one function that compares ranks and
refuses to go backwards, because Razorpay delivers webhooks at least once and
does not guarantee order — so a redelivered `payment.authorized` would otherwise
overwrite a `payment.captured` that already landed, and a collected cycle would
become uncollected.

Two different terminal states for one payment are recorded as a conflict, not
resolved. One of them is wrong and this code cannot tell which; picking a winner
by arrival time would be inventing an answer the provider did not give.

### Webhooks

The signature is HMAC-SHA256 over the raw request body, in `X-Razorpay-Signature`.
Razorpay's documentation says in as many words not to parse or cast the body
before signing it, so `verify` takes `bytes` and not a dict — a caller
physically cannot hand it a re-serialised object, which would hash differently
and fail on every genuine event.

Ingestion does the smallest durable thing: verify, insert, return. Razorpay
allows five seconds and resends anything it does not see acknowledged, so no
model call, no provider call and no belief update happens inside the request.
Interpretation runs after the response.

A rejected signature is still persisted, with `signature_valid` false, and
answered 400. Dropping it would leave no trace of an attempt to forge an event,
which is the one delivery where the log matters most. An unhandled event type is
accepted and acknowledged rather than 4xx'd: a 4xx makes Razorpay retry for
twenty-four hours and then disable the webhook.

### Crash boundaries

| Where the process dies | What it leaves | How it recovers |
|---|---|---|
| before the intent is written | nothing | re-deciding is safe |
| after the intent, before the order | an `INTENT` row, no provider state | reconciliation reports that no request was made |
| after the order, before recording it | an order at Razorpay whose id we lost | `GET /v1/orders?receipt=` finds it; the receipt is deterministic |
| after the order, before the debit | a scheduled attempt | the next tick submits it; the gate's ledger is rebuilt from the store |
| during the debit, response lost | `UNKNOWN` — the money may have moved | never retried; the order is asked which payments it has |
| during the debit, request re-sent after a restart | the provider refuses it (`Order already paid`) and the attempt is recorded `UNKNOWN`, **not** `FAILED` | reconciliation reads the order and finds the payment that did collect |
| after the debit, before the webhook | `SUBMITTED` | `GET /v1/payments/:id` |
| after the webhook, before interpreting it | a durable event, unprocessed | replayed at startup; every write is monotonic, so a replay is a no-op |

The property this rests on is that a debit is at-most-once at the provider, and
the property it does not have is exactly-once. A retried submission after a lost
response gets a rejection, not a replayed success, so the client still has to
reconcile. Nothing here claims otherwise.

**And a provider refusal is never read as a decline.** `Order already paid` is
exactly what a resubmission after a crash mid-request receives, and the order it
names may hold a captured payment. Recording that as `FAILED` would report a
collected cycle as uncollected and spend an NPCI attempt on it, so the service
records `UNKNOWN` — non-terminal, never auto-retried, resolved by asking the
order what it holds. Gate `F4b` drives that boundary and fails if the attempt
comes out `FAILED` or if the money is counted anywhere but once.

The executor's own precondition is the second line of that defence. Its journal
is rebuilt from the attempt row on every load, so a row past `SUBMITTED` must
report a phase `attempt()` refuses; the state-to-phase table is total over
`AttemptState` and asserted to be, because a state missing from it would default
to `ORDER_CREATED` and let a restart resubmit a debit that had already run.

### The HTTP surface

There is no `POST /charge`. No route accepts an amount, and none accepts a
token. The only route that can move money runs the whole chain and reads no
request body at all: the amount comes from the mandate the customer authorised,
the hour from the belief filter, the legality from Stage 0. A generic charge
endpoint would make every guarantee in this repository conditional on nobody
calling it — including the guarantee that a language model cannot pick a debit
amount, because a model with an HTTP client and a generic endpoint has picked
one.

The webhook endpoint is the only unauthenticated write route, because Razorpay
cannot present an operator token. Its authentication is the signature. Every
other route requires `RECOVERY_OPERATOR_TOKEN` when one is configured, and the
server refuses to bind a non-loopback address without one.

Provider identifiers are shortened in responses unless the caller authenticates
and asks for them. Webhook payloads are never returned: they carry the
customer's email and contact and exist only so a signature dispute can be
settled.

### The operator console

`live/console/` is a static page served by the same process, with no framework
and no build step. It renders environment, mandate state, the decision chain,
the payment lifecycle, the webhook timeline and the diagnosis. It decides
nothing. Its content security policy permits no third-party origin, so the type
is a system stack and there are no remote assets.

---

## Audit

`agent/audit/log.py` appends one JSON object per event to a single file:
decision ticks including waits, diagnoses, all five constraint verdicts, money
actions, outcomes, non-money workflow actions, and stop rules. Querying by
`action_id` returns the full chain behind one payment — what the belief
predicted, what the diagnoser concluded and why, every constraint verdict, the
money action with its notification time, and the outcome.

`AuditLog` raises `LogFileNotEmpty` when opened on a non-empty file. One log
describes exactly one run.

---

## Runtime failure handling

Every row below is in the shipping path.

| Failure | Response |
|---|---|
| The language model errors, times out, or returns unparseable output | Falls back to the deterministic rule engine and logs `LLM_FAILURE`. The rule engine is the default path, so the fallback is exercised continuously rather than being a cold branch |
| The model returns a legal-looking instruction containing a time | `governance.py` rejects the prose, and `Diagnosis` has no field that could carry it |
| An action would breach a mandate rule | Stage 0 refuses before the executor exists to it, and the refusal is a row in the audit trail. No network traffic is generated |
| The rail is degraded across many customers at once | `rail_monitor.py` detects it from cross-customer outcomes and the belief update is suppressed, because a technical decline is a fact about the rail rather than about one customer's balance |
| A debit's outcome is unknown — a timeout, a deemed transaction | Recorded as `pending`, never rounded down to "failed". Rounding an unknown to a failure is what licenses a retry, and a retry on an unknown risks a double debit. The rule engine stops further debits on that cycle |
| The last decline code is terminal — closed account, broken mandate, lien | `STOP` or `ESCALATE`, never `RETRY`, on every path including the fallback |
| An HTTP request is retried after a socket failure | The idempotency key derives from the `action_id` Stage 0 already wrote to the audit trail, so the same logical debit produces the same key across a process restart |
| A worker process dies during a measurement | The measurement raises. A crashed run is a failed result, not a missing one |
| A second run appends to an existing audit log | Refused at open time |

---

## Decline states without a timing interpretation

Razorpay does not return NPCI decline codes; it normalises them into 110 error
reasons of its own. Mapping between the two surfaces two states that change the
correct action and cannot be expressed as a timing decision.

**`funds_blocked_by_mandate`** — the balance is present but another mandate has
claimed it. Retrying is wrong. A merchant seeing only its own debits cannot
distinguish this from an empty account.

**`deemed_transaction`** — the response was lost, so whether the debit went
through is unknown and the customer may already have been charged. Retrying
risks a double debit.

No combination of "probability now" and "probability later" encodes *do not act,
because the question is unanswerable*. Both are mapped from Razorpay's published
reasons and routed by the diagnosis layer. Neither is simulated, because no
source gives a rate for either, so the world draws neither state.

---

## Verification boundaries

| Check | What it holds |
|---|---|
| `sim/gate.py --tier full` | 27 mutation and statistical gates over the simulation. Four are red on a clean checkout with written reasons — [results.md](results.md#gate-status) |
| `agent/tests/test_layer_isolation.py` | the import graph: seven rules, each with a mutant that must trip it |
| `agent/tests/test_parity_vs_harness.py` | the agent reproduces the simulation harness bit-exactly in degenerate mode |
| `agent/tests/test_stage0_enforces.py` | Stage 0 refuses, and the auditor catches illegal actions injected below the gate |
| `sim/verify_doc_contract.py` | the constants and the decision rule stated in this document match the code, and `ports.Diagnosis` still carries no temporal field |
| `sim/verify_docs.py` | no retracted claim is live in a document |
| `sim/verify_claims.py` | the current-state claims in the public documents do not contradict each other, and every published headline equals `sim/canonical_result.json` — the canonical run's own record of itself |

Every headline figure reaches the documents through that file. The canonical
batch writes it with `--emit`, `scripts/build_page_data.py` reads it instead of
carrying a transcribed copy, and `sim/verify_claims.py` fails both when a
document quotes a different value and when the sentence carrying the figure is
deleted.
