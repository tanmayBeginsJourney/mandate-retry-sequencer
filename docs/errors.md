# Failure analysis

Defects found in this project's own work, grouped by class. Almost every one
made the project look better than it was, which is what happens when the same
party builds the measuring instrument and the thing being measured.

Each entry states what failed, why the existing check missed it, and what
control now prevents a recurrence. The recurring shape is a **guardrail that
reported green while measuring nothing**; it appears in every class below.

Current measurements are in [results.md](results.md). The system is described in
[architecture.md](architecture.md).

These entries are curated from the project's chronological development log,
which is not part of the published tree and is preserved in the repository's
git history. Source comments that cite "the development log" or an error by
number refer to it; the entries below are grouped by failure class instead,
because the class is what a reader can reuse.

---

## Simulation and model errors

### A clairvoyant oracle that skipped opportunities

The upper bound the project was measured against used the index
`value × (p_now − p_later)` with both terms in {0, 1}. When money was available
now **and** later the index was zero, so the oracle skipped, and could defer past
the horizon. It skipped 29,161 opportunities where it had the money. Reported
headroom to optimal was +1.65 points; the real headroom was more than ten times
that. The system looked near-optimal because the bound was broken. The gate that
should have caught it — "oracle approval is about 100%" — was **vacuous**: the
oracle only fires when it already knows it will succeed.

*Guard:* gate `T1` asserts the oracle weakly dominates every policy, and
mutation gate `M8` restores the defect deliberately and requires the suite to
catch it.

### The world had no steady state

The world banked a fraction of a salary every cycle without bound, and collected
mandates were handed back at the next salary credit because the drained amount
reset each epoch. The at-risk rate decayed from 29.80% to 0.75% across four
cycles, and the due-date failure rate became a function of run length: 27.67% at
60 days against 4.24% at 360. The world's agreement with the published
first-presentation failure band was the horizon cutting a transient. Nothing
checked it, because every measurement used the same horizon.

*Guard:* burn-in of 12 whole cycles before day 0; a carry-over cap that removes
leftovers above a per-customer buffer before each credit; explicit mandate
outflow, with the double-counting path disabled in the same change. Burn-in has
no free parameter and is checked by running it longer.

### An enrichment parameter perturbed the stream it was meant to leave alone

Adding temporary account holds drew them from the generator the money path uses,
before the spend loop. At any non-zero rate **every later draw shifted and every
balance trace was re-drawn**, so each cell of the sweep was a different world
*plus* holds rather than the same world *with* holds. No gate caught it; it
surfaced while building a diagnostic that needed the set of cycles at risk *only
because of a hold*, which requires the enriched world to be the base world plus
holds. Once fixed, a pre-registered prediction moved from held to broken —
without the fix the project would have recorded a hit from a comparison that was
changing two things at once. The convention it broke was written as a comment in
a neighbouring module and enforced by nothing.

*Guard:* every per-unit draw takes its own generator, and a non-zero rate
without one raises rather than falling back silently. The balance array is
asserted bit-identical outside the affected hours. The same repair was applied
to the missed-credit parameter, which had the identical defect.

---

## Evaluation and experimental-design errors

### An ML model declared the winner because it was the only fitted arm

A gradient-boosted probability engine beat the Bayes filter in-distribution by
about 4 points, which read as an architectural finding. The ML model had been
fitted to 800 training customers; the Bayes filter was carrying three values
typed in by hand and never checked. Fitting those three on the same training
populations reversed the result. Nothing in the process had asked whether the
baseline was fairly configured.

*Guard:* gate `S4` holds the configured-versus-default difference and is paired
with a mutant that ignores the configuration, under which the gain collapses to
+0.00. `S4` proves the configuration is applied; it does not prove how the
values were selected, which is a separate control.

### A fitted constant that peaked exactly where it was fitted

The first fit of the payday prior was selected at `payday_err=7` and produced a
hard window of half-width 7 — the same number as the injected payday noise. It
measured a large gain and sat within a few points of a clairvoyant oracle.
Checked against the one parameter the study had never varied, it went
**negative** at `payday_err=14`, worse than the filter it replaced: a true payday
outside the window received prior weight 1e-6 and could never be recovered by
any amount of evidence.

*Guard:* `sim/fair_audit.py` sweeps `payday_err` and is re-run after any change
to the belief configuration. The shipping configuration's gain does not peak at
the operating point it was selected on.

### A property measured on one component and generalised to another

`WAIT` was cut from the action space on three grounds: unreachable from every
branch of the rule engine, one supporting golden case, and measured at
approximately zero by the action ablation. All three are true, and all three are
statements about **the rule engine**. They were false of the model, where `WAIT`
was the most-used answer: removing it moved the model's ambiguous-case score by
six cases and flipped the headline comparison between the two. The ablation
figure that justified the cut had been measured before a model-backed diagnoser
existed, so it could not have been about one.

*Guard:* both columns are kept side by side in
[results.md](results.md#the-diagnosis-layer), and the orphaned golden case stays
in the denominator, since dropping it would flatter every arm by removing a case
none can win. **Before removing anything from a shared vocabulary, measure it on
every consumer of that vocabulary, not on the one that made it look dead.**

### A baseline measured on a different world from the arms it was compared to

The `payday_wait` baseline ran on the pre-canonical population without burn-in or
mandate outflow while every agent arm ran on the canonical world. The difference
was reported as +24.87 points for timing; the true figure is +8.52. The rule that
a large improvement is a defect until proven otherwise should have caught a
number three times the published industry benchmark and did not, because the
number fit the expected story.

*Guard:* the baseline's world keyword arguments are guarded at the harness
boundary, and the at-risk denominator is printed beside every arm. It moves by a
factor of eleven across the three worlds involved, which is the signal this
defect lacked.

### A cross-check whose two sides can only differ in a regime the display never enters

The batch report printed the constraint gate's refusals beside the independent
auditor's count, and the text presented agreement as two-implementation
evidence. They are not the same quantity: the gate counts what it **stopped**,
the auditor counts violations that **happened**. On a clean run both are zero for
unrelated reasons. It was found while building a script that submits an illegal
action on purpose — the gate refused three times and the auditor still reported
zero, under a caption saying the two agreed.

*Guard:* the report labels the columns `gate refused` and `illegal executed` and
states that zero in both is not agreement. `scripts/prove_stage0_refuses.py`
writes money **below** the gate, touching no counter, and shows the auditor
finding it from the log alone. **Before presenting two numbers as a cross-check,
state the regime in which they could differ, and check that the display is ever
in it.**

### An unconstrained oracle used to justify a modelling decision

"The oracle collects 100%, therefore this is a pure timing problem" justified
adding insolvency to the world. That oracle ignores the four-attempt cap by
design, and the cap binds on any policy that has to search, so the inference does
not follow. The conclusion survives — a *constrained* oracle obeying the cap and
the notice rules is also 100% — but the reasoning that reached it did not.

*Guard:* the constrained oracle is the ceiling quoted in
[results.md](results.md#external-validation); the unconstrained one is not used
for inference about achievable performance.

---

### A sample size inherited from a world that no longer existed

`n=100` was chosen when every customer held five mandates. Three corrections
later the mandate count was drawn from `1 + Poisson(1)` — a mean of about two —
and the same `n` bought two and a half times fewer mandates, fewer money actions
and fewer at-risk cycles. Nothing re-derived it; it was carried forward because
it was already there. Measured against the same experiment at n=2000, the n=100
world reads +0.57 on the headline uplift, +1.32 on recovery of at-risk cycles and
−1.17 on the first-presentation failure rate. All three are in the direction that
flatters the result, and all three are larger than the interval the experiment
reported.

*Guard:* `agent/tests/test_scale_n.py` runs the canonical experiment across five
values of `n` with everything else held fixed, and `agent/tests/_canonical.py`
holds the chosen value as the single definition every script imports. Changing it
trips the pre-commit tripwire, which asks in writing which measurements are now
stale.

### An interval that measured the smaller of two variances

Every published comparison carried a paired 2 SE across the ten populations, and
that interval was presented as the uncertainty on the result. It is the
uncertainty from one source. Repeating the identical experiment on four
independent run seeds moves the headline uplift by 1.89 points — roughly three
times the interval being quoted — and the movement is almost entirely in the
baseline arm, which waits for a payday estimate the run seed draws. The
population interval is not wrong; it was the only one measured, and it was
labelled as though it were the whole thing.

*Guard:* the run-seed study is part of the sample-size script and its spread is
quoted beside every headline. The two intervals are named separately and neither
is folded into the other.

## Test and verification errors

### A mutation test that increments the counter it is graded on

A gate asked whether the harness notices a second pending notification. It ran a
mutant and required the violation counter to move. It moved — 1066 — and the gate
had reported pass for the life of the suite. The 1066 were the mutant's own
writes: the harness incremented the counter *inside the mutation branch*, and the
only independent detector was unreachable because an earlier filter had already
excluded the case. Instrumented: **1066 counted, 1066 self-written, 0
independent.** Every other vacuous gate here was a weak assertion; this one was a
compromised witness, since the mutant is the one piece of code whose job is to be
adversarial and it had write access to the scoreboard.

*Guard:* gate `M4B` parses the harness source and fails if any violation counter
is incremented inside a mutation branch, and reports vacuous if it ever flags all
five mutants. **A mutant may create illegal state and nothing else.** The mutants
were repaired to create illegal state and let independent code notice; the
detector was not narrowed.

### A gate named after a concept rather than the object it measures

The calibration gate ran a policy carrying the **point-estimate** payday filter,
while the recommended policy carries the payday **posterior** filter. For the
life of the project the gate the central claim rested on was pointed at something
that does not ship. The same mechanism produced a second defect: the byte-lock
that makes the fast/full test-tier split safe covered 14 policies at 2 operating
points, **not one of which passed the fitted configuration**, because the
configuration arrived after the reference was captured and nothing re-asked what
the lock covered.

*Guard:* `S1_PD` applies the identical, pre-registered threshold to the filter
that ships, and the byte-lock reference now includes the shipping configuration
at both operating points. The original calibration gate was **not** repointed:
quietly aiming a gate at a different subject is indistinguishable from moving it
until it agrees.

### The script that proves a constant cannot produce it

The shipping belief configuration was documented in three places as selected by a
committed fit script, so the fit would be reproducible. Two of its five values
could not come out of that script: one parameter name appeared nowhere in it, and
the objective the documentation described was not the objective the code
optimised. The guard for two earlier errors had been "the fit is reproducible and
its objective is visible". Nobody ran it again. **A committed script is not a
reproduction; only a re-run is a reproduction.**

*Guard:* `sim/fitted_belief.json` commits the full train and evaluation record,
and `sim/fit_belief.py --check` verifies whether its shipping field matches the
code. It currently records `matches_shipping=false` **with the reason**, rather
than claiming a provenance the script does not have.

### A detector that was structurally silent, and two predictions that held on the silence

Detection and response were wired through the same conditional, so when the
response was switched off — which the detection-power study did deliberately, to
keep the response from confounding detection — nothing ever asked the detector
anything. It reported a true-positive rate of 0.00 everywhere. Two pre-registered
checks passed on that output: "non-decreasing in population size" (six zeros are
non-decreasing) and "below 0.5 at small n" (zero is below 0.5). The measurement
reported 5 of 6 predictions held while measuring nothing.

*Guard:* detection is assessed whenever the monitor is enabled, regardless of
whether anything acts on the verdict, and both checks report vacuous rather than
held when the detector never fires. **State what the metric reads when the thing
being measured is absent, and check that the assertion fails on that value.**

### A metric whose time base rewarded silence

The detection benchmark scored detectors as excess loss against an oracle,
counted in detector-hours of disagreement. Its dominance gate failed: a crippled
detector that never fires scored **better than the real oracle at every
severity**. A detector that never fires accrues at most the outage windows' own
length, while one that fires correctly holds its state until the next time
anything consults it. Under an unweighted hour count, silence is cheaper than
correctness. The loss's time base was wall clock; the monitor is only consulted
when the loop has work to do.

*Guard:* the hours gate is **kept red and not repaired** — repairing a metric
after it returns an inconvenient answer is indistinguishable from moving a
threshold. A second gate counts the same claim on decision-points, the time base
at which the system acts, and that is the gate the suite verdict reads. The gate
found the defect in its own metric, in the session that wrote it.

### A normal approximation used where the expected count was 0.09

The outage detector compared observed technical declines in a window against the
base rate using a z-score. With 11 attempts in the window the expected count is
0.088, so a **single ordinary technical decline** scored z = 3.09. The exact
probability of seeing at least one is 8.5%. The detector fired 21 to 26 times on
a horizon containing 3 outages. A normal approximation to a binomial needs an
expected count of roughly 5 or more; here it ranged from 0.09 to 0.8, so it never
applied at any population size this project runs. Nothing failed, and the error
produced *more* alarms, so it looked like sensitivity.

*Guard:* the monitor computes the exact binomial tail and thresholds on a derived
false-alarm target. Transitions dropped to 6 on a 3-outage horizon, and the
false-alarm rate is now **measured** rather than assumed.

### A coverage check that could not see an invented entry

The map from Razorpay's published error reasons to internal decline families was
gated by a check that every published reason has a family. It passed. The map
contained an identifier appearing **nowhere** in Razorpay's list, typed while
writing the table, sitting inside a structure whose docstring cites a primary
source. "Every reason of theirs is covered by ours" and "every entry of ours came
from theirs" are different claims, and only the first had a check.

*Guard:* a second gate computes the reverse difference and fails on anything left
over; it found the invented key on its first run. Legitimate extras — there is
one, caused by a typo in the vendor's own list — must be declared with a written
reason, and a third gate fails if a declared extra has none. **A check that one
set covers another is not a check that the two sets are the same.**

---

### An invariant that had never been true of the tree it was written for

The import-graph gate "only the constraint layer may hold an executor" matched
every `.py` file under `agent/`, including the execution layer itself, and
carried a hand-maintained list of exempt test files. It reported eleven
violations. Four were modules inside `agent/execution/` importing their own
siblings — a layer cannot cross its own boundary — and seven were tests written
after the list was last edited. None of the eleven was the defect the rule
exists for. A central register of decisions taken elsewhere drifts, and this one
had.

*Guard:* the rule is split. `I2` covers the shipping tree only. `I2T` lets a test
hold an executor if the test says so in an `# I2-EXEMPT:` line naming why, so the
decision lives in the file that makes it and there is no list to drift. `I6` was
added in the same change, because removing `agent/execution/` from `I2`'s scope
would otherwise have left the executor free to import the layers deciding on its
behalf. Seven rules, seven mutants, and a canary proving the declaration path
still works.

### An enforcement the documentation promised and the code only printed

Three documents stated that adding a temporal field to the diagnosis type would
make a test fail, and named the function that checks it. The function was
correct. Its only caller printed `ADR-005 BROKEN` and returned zero. The claim
that a language model cannot express a debit time rested on a check that could
not fail.

*Guard:* the check moved into `sim/verify_doc_contract.py`, which the pre-commit
hook runs, and exits non-zero. It carries a canary — a synthetic field list
containing `retry_after_hours` that the same matcher must flag — so it cannot
pass by having stopped working. Adding the field to `ports.Diagnosis` was run as
a mutation and both callers went red. The eval harness now returns 3 on the same
condition, and 1 when a pre-registered prediction breaks, which is what every
other measurement script already did.

## Runtime and integration errors

### An authentication failure recorded as a statement about the customer's balance

The Razorpay executor passed every response carrying an HTTP status to the
payment-outcome parser. A real authentication failure has neither an error reason
nor a payment status, so the parser fell through to its last branch and returned
an ambiguous decline code. That is a decline, and the loop hands it to the belief,
which hard-zeroes every balance bin at or above the amount. A wrong or expired
API key would have taught the filter that the account was empty — for every one of
that customer's mandates at once, since they share one belief — burned all four
legal attempts and killed every mandate at the cap, while printing a plausible
recovery rate. Found by sending one request with no credentials. The offline gates
could not have found it: every fixture was a *payment object* from the vendor's
documentation, and none represented an *API-level* rejection, because nobody had
seen one. The module had already reasoned about the identical hazard for socket
failures; the principle was stated for one instance and not generalised to its
twin.

*Guard:* a request-level rejection raises `RazorpayError` naming the HTTP status
and is never recorded as a customer decline, because no payment was created.

### A correctness property that lived in the caller instead of the component

The rule-based diagnoser returned `RETRY` on a billing cycle that had already
collected: its first branch tested only for a technical decline, so a success fell
through to a branch that proposes a second debit. **Charging a customer twice is
the worst outcome this system can produce.** It was harmless only because the loop
filters collected mandates out before calling a diagnoser — a component with an
interface, correct under an assumption its single caller happened to satisfy and
nothing stated.

*Guard:* fixed **in the component**, guarded on attempts already used so it is
correct under both readings of the decline history's scope. The golden case that
exposed it is an anchor: any diagnoser returning `RETRY` there fails the set
outright, whatever it scores elsewhere.

### Unknown outcomes documented as never-retried, on a path that could retry them

The documentation stated that an indeterminate outcome — a timeout, a deemed
transaction — is recorded as pending so a retry cannot double-debit. The
production fallback path had no such branch and could recommend `RETRY`.

*Guard:* a first-class branch on the indeterminate and terminal code sets, on
every path including the fallback, with a regression test that fails on the
previous behaviour. A terminal code anywhere in the decline history stops further
debits.

### A language model wired into a loop that asks 119,667 times

The loop calls the diagnoser once per live mandate per decision hour. The eval
exercises fifty fixed cases, ran in seconds, and gave no hint of anything; the
batch report asks for 119,667 diagnoses. At a network call of 2–8 seconds each
that is days of wall clock and an unbounded bill, and the first attempt was killed
after twelve minutes having produced no output and about 165 unplanned paid calls.
A component validated on the harness that exercises it cheaply, deployed into the
loop that exercises it exhaustively. The two differ by three orders of magnitude
and the interface is identical, so the code gave no warning.

*Guard:* a hard per-run cap on **network** calls, with cache hits free so the cap
bites on novelty rather than volume, and every refusal logged with its reason. The
fix is not a faster model — **a bounded call budget is the design** — and the
report prints the fallback rate beside the money.

### Two clocks meeting at one field

Stage 0 reads the target timestamp as simulated hours; the live executor read the
same field as a future Unix epoch second when it created the pre-debit order. No
single value satisfies both, so **the live executor could not be driven end to
end by Stage 0 with a genuine order**. It surfaced when a script meant to
demonstrate refusal printed "allowed" with zero network calls and then died on a
missing record: the gate had swallowed the executor's exception into a log line.

*Guard:* an explicit `epoch_origin` on the executor, and one conversion —
`_epoch` — where the units change. Without an origin the executor refuses to
create an order rather than sending a timestamp in 1970, which a gate asserts.
`scripts/prove_stage0_refuses.py` now drives a legal action through the real
executor and prints the converted `payment_after` beside the simulated hour it
came from.

### An accepted submission parsed as a decline

`POST /v1/payments/create/recurring` answers `{"razorpay_payment_id": "pay_..."}`
and nothing else — no status, no error reason. The executor handed that response
to a parser expecting a payment entity, which found no status, fell through to
its last branch and returned an ambiguous decline. **Every successful submission
would have been recorded as a failed one**, and the loop hands a failure to the
belief, which hard-zeroes every balance bin at or above the amount for every
mandate that customer holds. This is the same shape as the authentication defect
above, one layer along: a response that is not a payment object, read as one.

It was found by reading the current API reference rather than by a test. No
offline fixture could have caught it, because every fixture was a payment entity
transcribed from the documentation and none was the create-recurring response —
nobody had looked at what that endpoint actually returns.

*Guard:* an accepted submission returns `pending`, not success and not failure.
The outcome arrives on `payment.captured` or `payment.failed`, or from
`GET /v1/payments/:id`, and the belief is updated there rather than at
submission. A gate asserts the documented response shape resolves to pending.

### An idempotency header the provider does not honour

The executor sent `X-Razorpay-Idempotency-Key` on the recurring charge, and the
module's own docstring called it the reason a retried request could not become a
second debit. Razorpay documents no idempotency header for that endpoint; the
documented one is for RazorpayX Payouts and a small set of explicitly idempotent
Route and Refund endpoints. The header was invented, the comment beside it read
as a guarantee, and the code was marked `UNVERIFIED` in a way that had stopped
being read.

A header the provider ignores is worse than no header, because it is a safety
claim nobody will re-examine.

*Guard:* the header is gone and the claim with it. Safety rests on two properties
Razorpay does document: an order's `receipt` is unique per account, so a
deterministic receipt makes order creation idempotent and a lost order id is
recoverable by lookup; and an order can be paid once, so one order per debit
attempt makes the debit at-most-once at the provider. That is weaker than an
idempotency key — a retried submission gets a rejection rather than a replayed
result — and the weaker claim is the one now written down.

### A reconciliation set assembled by hand

The set of attempt states worth polling the provider about was a hand-written
list. `AUTHORIZED` was not in it, so a payment that was authorised and never
captured would have sat unresolved forever, never polled and never noticed —
money the customer had committed and the merchant never counted. Found by a gate
that compared the hand-written set against "everything that is not terminal" and
found them different.

*Guard:* the set is derived rather than listed. A state that is not terminal is
polled, by construction, and adding a state to the machine cannot leave it out.

### A log replayed into a counter that was never zeroed

The constraint gate's attempt ledger lives in memory, so a service that restarts
between scheduling a debit and running it has to rebuild the ledger from the
durable attempt rows. The rebuild ran on every mandate change and replayed the
rows without clearing the counter first. Three rebuilds turned one real attempt
into four — which is the NPCI cap — so the mandate silently stopped being
chargeable for the rest of its cycle. Nothing failed loudly: the gate refused
with the correct rule, for a reason that was arithmetic rather than regulatory.

Found by asking what happens when the rebuild runs twice, which it does on any
ordinary registration.

*Guard:* the rebuild resets each cycle before replaying it. A gate calls it five
times and asserts the count does not move. Replaying a log into a counter is
idempotent only if the counter is zeroed first, and this one is now.

### A read-decide-write path with no lock, on a threaded server

The money path reads a mandate's state, decides on it and writes the result
back. The HTTP server is threaded, and nothing serialised those three steps per
mandate, so two concurrent ticks could both see no attempt in flight, both
schedule one, and both submit against the same order. Driving twelve threads at
one mandate produced integrity errors, a SQLite connection used from two places
at once, and a type error — none of which would have been diagnosable from the
symptom, which is a debit recorded as refused because the provider correctly
refused the second request for an order already paid.

*Guard:* one lock per mandate around the whole tick. The gate runs the same load
through the locked path and the unlocked one and requires the unlocked one to
fail, so the lock cannot be removed without a red gate. That mutant is
probabilistic — thread interleaving is not something a test can command — so it
is retried up to five times rather than asserted once, because a gate that is
red one run in six teaches people to re-run instead of to read.

### A flag that read the environment it had been handed

The live configuration takes an environment mapping so tests can drive every
branch, and the helper that reads the flag authorising real debits read
`os.environ` instead of the mapping. Every test asserting the flag's behaviour
was measuring the process environment, in which it was unset — so the three
gates covering the switch that permits real money all passed without exercising
it. Found by writing those gates and watching them fail against the value they
had just set.

*Guard:* the helper takes the mapping. A function handed a source of truth that
consults a different one makes the parameter a lie, and this one decided whether
a debit could be submitted.

### A refusal the sender could not read

The HTTP layer refused an over-long request body by responding 413 without
reading it. The client was still writing, so it saw a connection reset rather
than the status it had been sent — which a webhook provider reports as an outage
and retries, rather than as a rejection and stops. It showed up as a test that
failed roughly one run in six, which is the shape a real intermittent looks like
before anyone looks at it.

*Guard:* the body is drained in bounded chunks and discarded before the 413 is
sent, up to a ceiling past which the connection is closed instead. A gate
asserts that the status arrives, not merely that it is generated.

### One log file, one run — assumed by every reader, enforced by nothing

The audit log opens in append mode, deliberately, because append-only is a
property a reader can check by eye. The demo wrote to fixed paths, so every
invocation appended to the previous one's log and the auditor replayed **two
concatenated runs as one**: attempts double against the cap, and a notification
from one run reads as concurrent with one from the other. It printed compliance
violations that had never happened. A fresh clone looked clean on its first run
and lied on its second.

*Guard:* opening a non-empty audit log raises. The one legitimate append — a test
modelling a rogue writer inside a single run — passes an explicit flag.

---

### A provider refusal recorded as a customer decline

This is the same error as "An authentication failure recorded as a statement
about the customer's balance", one layer up. `RazorpayExecutor.attempt` raises
`RazorpayError` when the provider refuses the REQUEST. The service caught it and
wrote the attempt `FAILED` — a terminal state meaning "the debit did not
collect".

One refusal is `Order already paid`. A resubmission receives it after the
process dies between sending `POST /payments/create/recurring` and writing the
acknowledgement, and the order it names **holds a captured payment**. At the one
crash boundary where the money has certainly moved, the service recorded money
not collected: the cycle read as uncollected, `recovered_paise` stayed at zero,
and the NPCI attempt was spent.

The belief filter was not affected. `apply_outcome` runs only from reconciliation
and webhook processing, both of which require a terminal state reached through a
different path, so the damage was confined to the ledger and the report. Nothing
in the design produced that containment.

*Guard:* the refusal now records `UNKNOWN`, which is non-terminal, is never
retried automatically, and is what reconciliation resolves by asking the order
which payments it holds. Gate **F4b** drives the boundary end to end: it lets
the provider take a debit, drops the acknowledgement, restarts the service, and
fails if the attempt comes out `FAILED`, if a second payment reaches the
provider, or if the collected rupee is counted anywhere but once. Reverting the
repair turns four of its five checks red and puts `recovered_paise` back to 0.

---

### A precondition disarmed by the store that was meant to persist it

Nothing was visibly wrong here, and no gate was red. `RazorpayExecutor.attempt`
refuses to submit unless its journal reports the pre-debit order in
`ORDER_CREATED` or `NOTIFICATION_DELIVERED` — the executor's own last check
against submitting twice. The live service backs that journal with SQLite,
translating `AttemptState` into the executor's `PredeliveryPhase`.

The translation table had two entries and a default. `SUBMITTED`, `UNKNOWN`,
`AUTHORIZED`, `SUCCEEDED` and `FAILED` all fell through to `ORDER_CREATED`,
which `attempt()` accepts. The check held only while an in-memory copy of the
journal remained in the process. A restart rebuilt the phase from the row and
permitted a debit that had already run. The service's own guard covered the same
case, so the failure produced no symptom.

*Guard:* the table is total over `AttemptState` and an `assert` at import says
so, and the in-memory copy is gone so the row is the only record. Gate **F4b6**
calls `executor.attempt()` directly on a resolved attempt and requires
`invalid_predelivery_phase`; with the partial table it reaches the network
instead, and the gate reports that rather than crashing.

---

### A cap that bounded nothing, and a gate that graded itself

`RazorpayExecutor` carried `max_live_escalations`: read from an environment
variable, defaulting to 5, passed explicitly by a script, and read by nothing.
`escalate()` appends a row to a local file and makes no provider call, so there
was nothing to cap. A reader asking what bounds live escalations found a number
that had no effect.

Gate `A4g` asserted that the webhook route needs no operator token by passing
the literal `True`, with the claim in its detail string. It could not fail. It
now builds a second server that requires a token on every other route, posts a
signed webhook without one and requires 200, then posts a forged signature to
the same route and requires 400. Making the webhook route require the token
turns both checks red.

*Guard:* neither is a new mechanism. The first is a deletion. The second applies
the suite's existing rule — a gate no mutant can fail is not a gate — to a file
that had not been re-read.

---

## Documentation and evidence errors

### A correction that landed in one file and survived in four others

A round of corrections was recorded as having swept every copy, and had missed
three — one on the public page, where it was telling readers the project had two
untested compliance rules that had already been repaired. A second sweep found
five more. The pattern is structural: a correction lands in the file being
edited, and the same sentence survives elsewhere because nobody greps.

*Guard:* `sim/verify_docs.py` is a grep with a memory — each retracted claim is a
regex, a date, and what is true instead. Judge-facing files must not contain the
phrase at all; other files may keep it beside an explicit retraction marker, so
the record of what was believed is not deleted. Every rule has a canary and
`--selftest` proves the rule fires on it. A rule shipped without a canary was
itself found that way. It runs in the pre-commit hook.

### Number checking does not catch prose contradictions

`sim/verify_docs.py` matches figures and phrases that were explicitly retracted.
It cannot see a headline whose supporting sentence contradicts it, or a chart
caption describing an obsolete configuration. Both shipped on the public page: the
number beside the headline was regenerated from data and the sentence around it
was not.

*Guard:* `sim/verify_claims.py` encodes current-state invariants directly — which
figure is canonical and under what conditions, what is simulated and what is real,
what the language model may and may not decide, what has and has not been executed
against Razorpay, which customer the walkthrough uses, and which documents exist.
Every rule has a deliberate canary and `--selftest` proves the rule fires on it.
It runs in the pre-commit hook beside the retraction gate.

### A cache that resumed across a change to the thing it measured

A benchmark checkpoints to disk, keyed on the job tuple alone. The job tuple does
not mention the belief configuration, which arrives through a module-level
constant. After that constant changed, every re-run silently replayed the old
configuration and reported it as fresh — new timestamp, new file, same numbers.
The only thing between it and a reader was one line of stdout saying it had
resumed. Same shape as an earlier defect in a different cache, where the model's
reasoning setting was not part of the key; that one was fixed where it was found
and the twin two directories away was not looked for.

*Guard:* the cache key fingerprints the belief configuration, the grid, the
detector family, the world parameters and the job set, and a cache whose
fingerprint does not match is **discarded with a printed line** rather than
resumed. Deliberately over-broad: discarding a good cache costs half an hour,
keeping a bad one costs a published number that never happened. The fix was
exercised against the real cache file, not only written.

### A lexical filter written against the disclosures its author imagined

A governance check exists to catch merchant-facing text disclosing the customer's
financial state. A judge running on a **different model family** flagged two
rationales the check had passed: both paraphrased a boolean the case view
legitimately carries — that another of the customer's mandates recently succeeded
— as a statement that the customer has money. Restating a transaction fact as a
claim about a person is exactly what the rule forbids, and the filter had no
pattern for it. The diagnoser's own prompt contained the phrasing being missed, so
the system was coaching the language it was failing to catch.

*Guard:* patterns added for the inferential forms, and the prompt line rewritten
to forbid restating the boolean. **The fix went into the checked thing and into
the prompt, never into the judge.** Three of the judge's other flags were rejected
because they flagged the exact wording this project prescribes as compliant.

### Checkers that held their own copies of the numbers they protected

Three documentation gates and the page-data builder all guarded the batch
headline. Each held the figure as a literal: a co-occurrence rule keyed on the
agent's percentage, a retraction rule keyed on the figures it replaced, and a
transcribed dict in the page builder. Editing the headline in `docs/results.md`
to a different value passed all four, because a rule that fires on the old
literal stops firing when the literal changes and reports ok. This was
demonstrated, not suspected: the number was edited, the four checks were run, and
all four passed.

The same shape retired two rules silently. A co-occurrence rule and a
required-claim rule built on point estimates became unsatisfiable the moment
those measurements moved — the pattern matched nothing, the rule reported ok, and
the claim it protected could then be deleted without complaint.

*Guard:* the canonical run writes its own figures to `sim/canonical_result.json`.
`scripts/build_page_data.py` reads that file instead of a transcribed dict, and
`sim/verify_claims.py` checks each published headline against it through a slot
regex with a capture group — so a document quoting a different value fails, and a
document that has deleted the sentence fails too. The two co-occurrence rules
build their patterns from the same file, and the rule that was keyed on a point
estimate was rewritten to key on the claim.

### Three measurement scripts printed a world they had not run

The ablations take a `--canonical` flag that switches the population draw, the
populations and the run keywords. Their banners were f-strings over the
module-level constants, which the flag does not touch. With the flag set they ran
about two mandates per customer over ten held-out populations and printed
`k=5, 8 populations`. A fourth script printed a hardcoded `n=100` after the
canonical `n` had moved to 500. The walkthrough at the end of the batch report
built its population without the canonical keywords at all, so the one chain a
reader is shown came from a different world than every figure above it.

*Guard:* `agent/tests/_canonical.py` gained `world_line()` and `mandates()`,
which format the description from the values the run is about to use. Every
banner goes through them. A script can no longer describe a world it is not
running, because it no longer writes the description.

---

## The controls that came out of this

| Control | What it prevents |
|---|---|
| No mutant, no gate | A test no defect can trip |
| A mutant may create illegal state and nothing else | A test grading its own mutant |
| State what the metric reads when the subject is absent | A prediction satisfied by a disconnected wire |
| Every per-unit draw takes its own generator | A sweep whose cells are different worlds |
| Pre-registration before measurement, breaks recorded as breaks | Post-hoc selection of the flattering reading |
| Release-path scripts exit non-zero on a broken prediction | A break reported as a pass by automation |
| A crashed worker fails the measurement | A partial result reported as a whole one |
| Cache keys fingerprint everything that changes the answer | A replay reported as a fresh measurement |
| Two implementations of the constraint rules, sharing no code | An enforcer that is wrong in the same way twice |
| `sim/verify_docs.py` | A retracted claim outliving its retraction |
| `sim/verify_claims.py` | A public document contradicting itself in prose |
| `sim/verify_doc_contract.py` | Documentation stating constants the code has stopped carrying, and a diagnosis type that has grown a temporal field |
| `sim/canonical_result.json`, written by the run and read by the checkers | A headline edited in one document and left stale in another |
| Banners formatted from the values the run will use | A script describing a world it did not execute |
| One definition of the canonical world, imported everywhere | Two copies that agree until they do not |
| A tripwire on the whole measuring apparatus, not just the suite | A guard removed in the same commit as the thing it guards |

**The independent recount has caught a real defect once.** Pausing dispatch during
an outage dropped a pending notification without writing anything to the log, so
from the log alone a withdrawn notification was indistinguishable from a live one.
The constraint gate reported zero violations and the auditor reported dozens. The
auditor was right, and the repair went into the audit trail rather than into the
auditor.

Two controls came from outside the project. An independent reader given the
documentation and asked to check it against the code found three defects in the
measuring apparatus in half a day, at a point when the project already had a test
suite, a pre-registration habit, a mutation-testing discipline and a documentation
contract checker. A judge running on a different model family found the governance
hole on its first outing. Nothing in this document was found by re-reading code
and feeling confident.
