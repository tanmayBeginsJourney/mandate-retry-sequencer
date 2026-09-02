"""Prove the constraint layer refuses an illegal debit against the REAL
Razorpay client, without an API key and without a single network call.

    python scripts/prove_stage0_refuses.py

WHAT THIS DEMONSTRATES, AND WHY IT NEEDS NO KEY.

`Stage0Gate` is the only object in `agent/` that holds an executor. It
adjudicates all five NPCI rules and only then calls `_executor.attempt`. So
when an action is illegal, the executor is never reached -- which means the
refusal is a property of the CONSTRAINT LAYER and not of the backend behind it,
and can be shown against the live Razorpay client with the network unplugged.

That is the claim worth making. Compliance that only holds because the
simulation happened to be well behaved is not compliance. Here the gate is
handed the real `RazorpayExecutor`, wrapped in a transport that RAISES if
anything touches it, and the illegal action is still refused.

Three things are shown, in order:

  1. A legal debit goes through, and reaches the executor. The gate is not
     simply refusing everything.
  2. The SAME debit moved into an NPCI peak window (10:00-13:00) is refused,
     and the network is provably untouched -- the transport counted zero calls
     and would have raised had it been called.
  3. `auditor.py` REPLAYS THE LOG and finds that refusal independently, using
     code that shares nothing with the enforcer. Gate I3 fails the build if
     `auditor.py` ever imports `constraints/rules.py` or `constraints/stage0.py`,
     so "independent" is enforced by the import graph, not asserted here.

The third is the one that matters. An enforcement layer with no independent
check is the vacuous-gate shape this project has now hit seven times
(`docs/errors.md`), and the auditor has already caught one real hole that
the gate's own counter missed.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent  # noqa: F401  -- puts sim/ on the path
import w3
from agent.audit.log import AuditLog, read_rows
from agent.constraints.auditor import replay
from agent.constraints.rules import AttemptLedger
from agent.constraints.stage0 import Stage0Gate, action_id
from agent.execution.razorpay_executor import MandateBinding, RazorpayExecutor
from agent.ports import (Allowed, InterventionKind, MandateRef, MoneyAction,
                         Refused)

REF = MandateRef(customer_id=45, mandate_index=3, merchant_id=17)
AMOUNT = 550.0
RUN = "prove-stage0"


class TripwireTransport:
    """A transport that fails the demo if anything sends a request.

    This is the whole trick. A test that asserts "no network happened" by
    checking a counter can be fooled by a call that resets it; a transport that
    RAISES cannot be reached quietly. If Stage 0 ever regressed into
    adjudicating after dispatch, this script would not print a softer message
    -- it would blow up.
    """

    def __init__(self) -> None:
        self.calls = 0

    def post(self, url, body, idempotency_key):
        self.calls += 1
        raise AssertionError(
            f"NETWORK REACHED. Stage 0 let an action through to {url}. "
            f"That is the failure this script exists to detect.")

    def get(self, url):
        self.calls += 1
        raise AssertionError(f"NETWORK REACHED on GET {url}")


class CountingTransport(TripwireTransport):
    """For the legal case only: records the call and returns a captured
    payment instead of raising, so step 1 can show the executor IS reached."""

    def post(self, url, body, idempotency_key):
        self.calls += 1
        self.last = dict(url=url, body=body, idempotency_key=idempotency_key)
        return 200, {"id": "pay_TESTONLY", "status": "captured"}


def hdr(s: str) -> None:
    print()
    print("=" * 78)
    print(s)
    print("=" * 78)


def build(transport, log_path: str):
    # `charge_amount`, `rzp_email` and `rzp_contact` are all REQUIRED by the
    # decoupled predelivery path. `Stage0Gate.issue_notification` calls
    # `executor.notify()` to create the Razorpay order, and `execute()` refuses
    # with `missing_predelivery_order` when that order does not exist. Stage 0
    # passes `amount=0.0` to `notify()` by design, so the order's amount has to
    # come from `charge_amount` on the binding; email and contact are required
    # by `POST /v1/payments/create/recurring`.
    #
    # WITHOUT THEM THIS SCRIPT PROVED NOTHING AND SAID IT PASSED. `notify()`
    # returned `ORDER_CREATE_FAILED` ("invalid amount: 0 paise"), the gate
    # swallowed it into a log line, `execute()` never reached the transport,
    # and step 1 printed ALLOWED with `network calls 0` before crashing on the
    # missing record. That is the shape this repository calls a vacuous gate.
    # Found and fixed 1 September 2026; the predelivery path shipped in commit
    # 91b1fc1 and this script was not updated with it.
    ex = RazorpayExecutor(
        bindings={REF.uid: MandateBinding(rzp_customer_id="cust_demo:45",
                                          rzp_token_id="token_demo",
                                          rzp_email="demo@example.com",
                                          rzp_contact="+919999999999",
                                          charge_amount=AMOUNT)},
        transport=transport)
    ledger = AttemptLedger()
    log = AuditLog(log_path, RUN)
    return ex, ledger, log, Stage0Gate(ex, ledger, log)


def money_action(day: int, hour: int, notify_hour_offset: int = 24,
                 attempt_no: int = 1) -> MoneyAction:
    target_t = day * w3.HOURS + hour
    notify_t = target_t - notify_hour_offset
    return MoneyAction(
        action_id=action_id(RUN, REF, 0, target_t, attempt_no),
        ref=REF, amount=AMOUNT, cycle=0, target_t=target_t, notify_t=notify_t,
        decided_at_t=notify_t, kind=InterventionKind.RETRY,
        p_now=0.31, p_later=0.30, index_score=2.31)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="stage0-proof-")
    peak_hours = sorted(w3.PEAK)

    # ---------------------------------------------------------------- step 1
    hdr("1.  A LEGAL DEBIT.  Hour 08 -- outside every peak window.")
    t1 = CountingTransport()
    ex, ledger, log, gate = build(t1, os.path.join(tmp, "legal.jsonl"))
    a = money_action(day=11, hour=8)
    ledger.open_cycle(REF.uid, 0)
    ref_ = gate.issue_notification(REF, 0, a.notify_t, a.target_t, a.decided_at_t)
    assert ref_ is None, ref_
    d = gate.submit(a)
    print(f"    executor            RazorpayExecutor (real class)")
    print(f"    target              day {a.target_t // 24}, "
          f"hour {a.target_t % 24:02d}")
    print(f"    gate verdict        {type(d).__name__.upper()}")
    print(f"    network calls       {t1.calls}")
    assert isinstance(d, Allowed), "a legal action must reach the executor"
    if t1.calls:
        print(f"    idempotency key     {t1.last['idempotency_key']}")
        print(f"    request body        amount={t1.last['body']['amount']} paise, "
              f"recurring={t1.last['body']['recurring']!r}")
    print(f"    outcome             {d.outcome.code}  "
          f"success={d.outcome.success}  pending={d.outcome.pending}  "
          f"raw={d.outcome.raw_code!r}")
    pred = ex.predelivery_state(REF, a.target_t)
    print(f"    predelivery order   {pred.order_id if pred else 'NOT CREATED'}")

    # WHY THERE IS NO NETWORK CALL HERE, SAID OUT LOUD RATHER THAN ASSERTED
    # AWAY. Stage 0 allows the action and hands it to the real executor. The
    # executor then refuses to fabricate a debit without a predelivery order,
    # and the order cannot be created from this script's clock:
    # `RazorpayExecutor.notify` sets `payment_after = int(target_t)` and
    # requires it to be a FUTURE UNIX EPOCH SECOND, while Stage 0's rules read
    # the same field as SIMULATED HOURS (`check_peak` is `target_t % 24`).
    # One field, two unit systems, and they meet exactly here.
    #
    # That is a real limitation and it is recorded as one: the live
    # `RazorpayExecutor` has never been driven end-to-end by Stage 0 with a
    # genuine order, because no single value of `target_t` satisfies both
    # clocks. What this step DOES prove is unchanged and is worth stating: the
    # gate allows a legal action, the executor is the real class, and the
    # executor's own precondition binds rather than being bypassed.
    #
    # Until 1 September 2026 this step asserted `t1.calls == 1` and crashed
    # before reaching the assert, so the script exited non-zero while printing
    # nothing about why.
    if t1.calls == 0:
        assert d.outcome.raw_code == "missing_predelivery_order", d.outcome.raw_code
        print("    LIMITATION          no network call: Stage 0's clock is "
              "simulated hours and the")
        print("                        live predelivery order needs future "
              "epoch seconds. The")
        print("                        executor refused rather than "
              "fabricating a debit.")
    else:
        assert t1.calls == 1

    # ---------------------------------------------------------------- step 2
    hdr(f"2.  THE SAME DEBIT AT {peak_hours[0]:02d}:00 -- inside an NPCI peak "
        f"window.")
    t2 = TripwireTransport()          # raises if anything is sent
    ex2, ledger2, log2, gate2 = build(t2, os.path.join(tmp, "peak.jsonl"))
    b = money_action(day=11, hour=peak_hours[0])
    ledger2.open_cycle(REF.uid, 0)
    ref2 = gate2.issue_notification(REF, 0, b.notify_t, b.target_t, b.decided_at_t)
    print(f"    notification        "
          f"{'REFUSED at issue: ' + ref2.rule if ref2 else 'issued'}")
    if ref2 is None:
        d2 = gate2.submit(b)
    else:
        # The gate refuses a peak target at ISSUE time, before a notification
        # is even recorded. Submit anyway, to show dispatch refuses too --
        # both halves are adjudicated and the demo should show both.
        d2 = gate2.submit(b)
    print(f"    target              day {b.target_t // 24}, "
          f"hour {b.target_t % 24:02d}   (peak = "
          f"{peak_hours[0]:02d}:00-13:00, 17:00-21:30)")
    print(f"    gate verdict        {type(d2).__name__.upper()}")
    if isinstance(d2, Refused):
        print(f"    refused on          {d2.refusal.rule}")
        print(f"    detail              {d2.refusal.detail}")
    print(f"    network calls       {t2.calls}   "
          f"<- the transport RAISES if reached; it was not reached")
    print(f"    gate's own tally    {dict(gate2.refusals)}")
    assert isinstance(d2, Refused), "a peak-hour debit must be refused"
    assert t2.calls == 0, "the executor must never be reached"

    # ---------------------------------------------------------------- step 3
    hdr("3.  AN INDEPENDENT RECOUNT, FROM THE LOG ALONE.")
    v = replay(read_rows(os.path.join(tmp, "peak.jsonl")))
    counts = v.asdict()
    print("    auditor.py replays the JSONL and re-derives legality using code")
    print("    that may not import rules.py or stage0.py -- gate I3 enforces it.")
    print()
    print(f"    gate REFUSED        {dict(gate2.refusals)}   "
          f"(3 refusals: peak at issue, peak + pending at dispatch)")
    print(f"    auditor VIOLATIONS  {counts}   (0)")
    print()
    print("    THESE ARE NOT THE SAME QUANTITY, and the difference is the")
    print("    point. The gate counts what it STOPPED. The auditor counts what")
    print("    ILLEGALLY HAPPENED. Three refusals and zero violations is the")
    print("    correct pair: the gate refused, so nothing illegal reached the")
    print("    world, so there is nothing for the auditor to find.")
    print()
    print("    Worth saying plainly, because in a clean batch report both")
    print("    columns read 0 and it is tempting to call that agreement. It is")
    print("    not. They agree at zero because nothing illegal happened. The")
    print("    auditor only bites when the GATE fails -- which is step 4.")
    assert v.total() == 0, "a refused action is not a violation"

    # ---------------------------------------------------------------- step 4
    hdr("4.  NOW BREAK IT.  Move money below the gate and see if it is caught.")
    from agent.audit.log import EventKind
    from agent.ports import to_paise
    rogue_path = os.path.join(tmp, "rogue.jsonl")
    rogue = AuditLog(rogue_path, RUN)
    pt = 11 * w3.HOURS + peak_hours[0]          # 10:00 -- illegal
    rogue.emit(EventKind.NOTIFICATION_ISSUED, pt - 24, mandate_uid=REF.uid,
               merchant_id=REF.merchant_id, cycle=0, notify_t=pt - 24,
               target_t=pt)
    # Exactly what the gate would have written HAD IT ALLOWED the action, and
    # nothing else. No refusal row, no counter touched: this is what a rogue
    # caller that held its own executor would leave behind.
    rogue.emit(EventKind.MONEY_ACTION, pt, action_id="rogue_peak",
               mandate_uid=REF.uid, customer_id=REF.customer_id,
               merchant_id=REF.merchant_id, cycle=0,
               amount_paise=to_paise(AMOUNT), intervention_kind="RETRY",
               target_t=pt, notify_t=pt - 24, gate_verdict="ALLOWED")
    rogue.emit(EventKind.OUTCOME, pt, action_id="rogue_peak",
               mandate_uid=REF.uid, merchant_id=REF.merchant_id, cycle=0,
               outcome_code="OK", success=True,
               recovered_paise=to_paise(AMOUNT))

    rv = replay(read_rows(rogue_path))
    print("    A caller that bypassed Stage 0 debited at 10:00 and wrote the")
    print("    same rows the gate writes on success. No counter was touched.")
    print()
    print(f"    gate's tally        (this run never saw it)")
    print(f"    auditor VIOLATIONS  {rv.asdict()}")
    for d in rv.detail[:3]:
        print(f"      -> {d}")
    print()
    print("    Caught, from the log alone, by code that cannot import the")
    print("    enforcer. THAT is the cross-check -- and it is the one that")
    print("    found a real hole once, when the gate said 0 and the auditor")
    print("    said 8, and the auditor was right.")
    assert rv.peak == 1, f"the auditor must catch the bypass, got {rv.asdict()}"

    hdr("WHAT THIS DID NOT NEED")
    print("    * no RAZORPAY_KEY_ID, no RAZORPAY_KEY_SECRET")
    print("    * no network -- zero calls in every step; the step 1 transport")
    print("      is a fake that records, the step 2 transport RAISES if reached")
    print("    * no simulation -- SimExecutor was never imported")
    print()
    print("    Stage 0 adjudicates before the executor exists to it. That is")
    print("    why the constraint layer is backend-independent, and why this")
    print("    is a proof rather than an assertion.")
    print()
    print(f"    audit trail: {os.path.join(tmp, 'peak.jsonl')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
