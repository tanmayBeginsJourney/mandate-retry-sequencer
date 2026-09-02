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
back up into the layers that decide on its behalf. Seven import-graph rules are
listed in [results.md](results.md#the-import-graph-gates).

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
| `order.notification.delivered` webhook | **not observed** |
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

**Two clocks meet at one field and disagree.** Stage 0 reads `target_t` as
simulated hours — the peak rule is `target_t % 24` — while
`RazorpayExecutor.notify` reads the same field as a future Unix epoch second
when it creates the pre-debit order. No single value satisfies both, so the live
executor has never been driven end to end by Stage 0 with a genuine order. The
disagreement is detected rather than papered over:
`scripts/prove_stage0_refuses.py` prints it and asserts the executor refuses for
exactly that reason. Reconciling the two clocks is an open piece of work in the
executor, not in the constraint layer, whose behaviour is measured against the
simulation executor and independently recounted from the audit log.

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
