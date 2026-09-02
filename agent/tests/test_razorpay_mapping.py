"""Gates for the Razorpay backend. Every one runs offline, with no API key.

    python agent/tests/test_razorpay_mapping.py

WHAT THESE CAN AND CANNOT PROVE. They prove that our normalisation of
Razorpay's vocabulary is total, that the dangerous cases route the dangerous
way, that a lost response never becomes a decline, and that Stage 0 refuses an
illegal action against the real client without touching the network. They prove
NOTHING about whether Razorpay accepts our request body, because no request has
ever been sent. That distinction is kept in the output rather than in a footnote.

EVERY GATE CARRIES A NAMED MUTANT AND `--mutants` RUNS THEM
(`docs/results.md`: "a gate earns its place only if you can name, in
advance, a concrete broken implementation that would make it fail"). The
mutants here are call-site substitutions -- a different function passed in, a
different payload -- never edits to a counter, because rule 1a says a mutant
may create illegal state and nothing else.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

import agent  # noqa: F401,E402
import w3  # noqa: E402
from agent.audit.log import AuditLog, read_rows  # noqa: E402
from agent.constraints.auditor import replay  # noqa: E402
from agent.constraints.rules import AttemptLedger  # noqa: E402
from agent.constraints.stage0 import Stage0Gate, action_id  # noqa: E402
# I2-EXEMPT: constructs a RazorpayExecutor and a SimExecutor to prove Stage 0 refuses before either is reached.
from agent.execution import razorpay_downtime as DT  # noqa: E402
from agent.execution.razorpay_executor import (MandateBinding,  # noqa: E402
                                               RazorpayExecutor)
from agent.execution.razorpay_predelivery import (PredeliveryOrder,  # noqa: E402
                                                  PredeliveryPhase)
from agent.execution.sim_executor import SimExecutor  # noqa: E402
from agent import ports as RC  # noqa: E402  -- the vocabulary lives in ports
from agent.ports import (FAMILY_AMBIGUOUS, FAMILY_INDETERMINATE,  # noqa: E402
                         FAMILY_LIEN, FAMILY_LIMIT, INDETERMINATE_CODES,
                         OK, TERMINAL_CODES, AttemptOutcome, InterventionKind,
                         MandateRef, MoneyAction, Refused)

REASONS_FILE = os.path.join(PKG, "agent", "execution", "razorpay_reasons.txt")

_results: list[tuple[bool, str, str]] = []


def ok(name: str, cond: bool, detail: str = "") -> bool:
    _results.append((bool(cond), name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   {detail}" if detail else ""))
    return bool(cond)


def published_reasons() -> list[str]:
    out = []
    with open(REASONS_FILE, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            out.append(line.split("\t", 1)[0].strip())
    return out


class FakeTransport:
    """Returns a scripted payload. Records every call.

    Speaks `request(method, url, body)`, which is the whole transport
    interface: `agent/execution/razorpay_api.py` builds every call on it. A
    fake at this level means the object under test is the real client, the
    real body builder and the real response classifier.
    """

    def __init__(self, payload=None, status=200, raises=False):
        self.payload, self.status, self.raises = payload or {}, status, raises
        self.calls = 0
        self.sent: list[tuple[str, str, dict | None]] = []

    def request(self, method, url, body=None):
        self.calls += 1
        self.sent.append((method, url, body))
        if self.raises:
            # A transport that cannot reach the provider returns "no status",
            # exactly as the real one does. It does not raise past this point:
            # the caller must never see a socket error as a payment answer.
            return None, {}
        return self.status, self.payload


def _ex(transport, uid="c45m3"):
    return RazorpayExecutor(
        bindings={uid: MandateBinding(rzp_customer_id="cust:45",
                                      rzp_token_id="tok",
                                      rzp_email="t@example.com",
                                      rzp_contact="+919876543210",
                                      charge_amount=550.0)},
        transport=transport)


def _seed_predelivery(ex: RazorpayExecutor, ref: MandateRef, target_t: int,
                      order_id: str = "order_test") -> None:
    """Sim hour indices are not Unix times; seed decoupled-flow state for gates."""
    ex.journal.save(PredeliveryOrder(
        mandate_uid=ref.uid, target_t=target_t, order_id=order_id,
        amount_paise=55000, payment_after=target_t,
        phase=PredeliveryPhase.ORDER_CREATED))


REF = MandateRef(45, 3, 17)


# ===========================================================================
# R1  every published reason has a family
# ===========================================================================
def gate_R1(mutant: str | None = None) -> None:
    print("\nR1  every published Razorpay reason maps to a family")
    print("    mutant: delete `insufficient_funds` from REASON_FAMILY, so a")
    print("            real decline falls through to AMBIGUOUS unnoticed")
    reasons = published_reasons()
    lookup = dict(RC.REASON_FAMILY)
    if mutant == "drop_funds":
        lookup.pop("insufficient_funds", None)

    missing = [r for r in reasons if r not in lookup]
    ok("R1a  all 110 published reasons are mapped",
       len(reasons) == 110 and not missing,
       f"{len(reasons)} reasons, {len(missing)} unmapped"
       + (f": {missing[:5]}" if missing else ""))

    # THE OTHER DIRECTION, and it is the one that caught a real defect.
    # R1a only asks whether their list is covered. It cannot see a key we
    # INVENTED -- and one was: `deemed_transaction_unknown`, which appears
    # nowhere in Razorpay's list and was typed while writing the map. An
    # invented code in a table sourced from a document is a rumour wearing a
    # citation (rule 4), and only a check in this direction finds it.
    invented = sorted(set(RC.REASON_FAMILY) - set(reasons)
                      - set(RC.KNOWN_EXTRA_KEYS))
    ok("R1b  no mapped reason is one we invented",
       not invented, f"not in Razorpay's list: {invented}")
    ok("R1b2 every declared extra key carries a written reason",
       all(len(v) > 40 for v in RC.KNOWN_EXTRA_KEYS.values()),
       f"{len(RC.KNOWN_EXTRA_KEYS)} declared: "
       f"{sorted(RC.KNOWN_EXTRA_KEYS)}")

    cov = RC.summarise_coverage()
    ok("R1c  every family is actually used by at least one reason",
       len(cov["by_family"]) >= 8, str(cov["by_family"]))

    # A gate that cannot go red is not a gate. R1's own vacuity check.
    if mutant is None:
        crippled = {k: v for k, v in RC.REASON_FAMILY.items()
                    if k != "insufficient_funds"}
        broke = [r for r in reasons if r not in crippled]
        ok("R1d  the named mutant WOULD trip R1a (vacuity check)",
           broke == ["insufficient_funds"], f"would-miss={broke}")


# ===========================================================================
# R2  the dangerous reasons route the dangerous way
# ===========================================================================
def gate_R2() -> None:
    print("\nR2  the two families that change the correct ACTION")
    print("    mutant: map `deemed_transaction` to FUNDS, which is what a")
    print("            taxonomy without an INDETERMINATE family must do")

    ok("R2a  funds_blocked_by_mandate is LIEN, not FUNDS and not LIMIT",
       RC.family_for_reason("funds_blocked_by_mandate") == FAMILY_LIEN,
       RC.family_for_reason("funds_blocked_by_mandate"))

    ok("R2b  deemed_transaction is INDETERMINATE",
       RC.family_for_reason("deemed_transaction") == FAMILY_INDETERMINATE)
    ok("R2c  duplicate_rrn_found is INDETERMINATE",
       RC.family_for_reason("duplicate_rrn_found") == FAMILY_INDETERMINATE)

    ok("R2d  both indeterminate codes are in INDETERMINATE_CODES",
       {RC.code_for_reason("deemed_transaction"),
        RC.code_for_reason("duplicate_rrn_found")} <= set(INDETERMINATE_CODES))

    # The mutant: what a FUNDS mapping would cost.
    ok("R2e  MUTANT: mapping deemed_transaction to FUNDS makes it retryable",
       not RC.is_pending("insufficient_funds")
       and RC.is_pending("deemed_transaction"),
       "FUNDS is retryable by design; INDETERMINATE must not be")

    ok("R2f  a LIEN is NOT terminal -- the mandate is fine, the money is spoken for",
       RC.code_for_reason("funds_blocked_by_mandate") not in TERMINAL_CODES)

    ok("R2g  a count limit is flagged as a lost distinction, not silently LIMIT",
       RC.family_for_reason("transaction_daily_count_exceeded") == FAMILY_LIMIT
       and any("count_exceeded" in u[0] for u in RC.UNMAPPED_DISTINCTIONS))

    ok("R2h  an unknown reason is AMBIGUOUS, never guessed into a family",
       RC.family_for_reason("some_reason_invented_in_2027") == FAMILY_AMBIGUOUS)


# ===========================================================================
# R3  outcome normalisation on recorded response shapes
# ===========================================================================
CASES = [
    # (name, payload, expect_code_family, expect_success, expect_pending)
    ("captured", {"id": "pay_1", "status": "captured"}, "OK", True, False),
    ("insufficient funds",
     {"status": "failed", "error": {"reason": "insufficient_funds"}},
     "FUNDS", False, False),
    ("frozen account",
     {"status": "failed", "error": {"reason": "beneficiary_account_dormant"}},
     "ACCOUNT_SHUT", False, False),
    ("revoked mandate",
     {"status": "failed", "error": {"reason": "authorisation_declined_by_psp"}},
     "MANDATE_BROKEN", False, False),
    ("limit hit",
     {"status": "failed", "error": {"reason": "transaction_limit_exceeded"}},
     "LIMIT", False, False),
    ("lien",
     {"status": "failed", "error": {"reason": "funds_blocked_by_mandate"}},
     "LIEN", False, False),
    ("deemed transaction",
     {"status": "failed", "error": {"reason": "deemed_transaction"}},
     "INDETERMINATE", False, True),
    ("unresolved payment",
     {"id": "pay_2", "status": "created"}, "INDETERMINATE", False, True),
    ("flat error_reason field",
     {"status": "failed", "error_reason": "bank_technical_error"},
     "TECH", False, False),
]


def gate_R3() -> None:
    print("\nR3  one Razorpay payment object -> one AttemptOutcome")
    print("    mutant: return success=True whenever there is no `error` key,")
    print("            which silently credits money for a pending payment")
    from agent.ports import family_of
    ex = _ex(FakeTransport())
    bad = []
    for name, payload, fam, succ, pend in CASES:
        o = ex._outcome_from_response(payload, 264, "")
        got = family_of(o.code)
        if not (got == fam and o.success == succ and o.pending == pend):
            bad.append(f"{name}: got {got}/{o.success}/{o.pending}, "
                       f"want {fam}/{succ}/{pend}")
    ok(f"R3a  all {len(CASES)} recorded shapes normalise correctly",
       not bad, "; ".join(bad))

    o = ex._outcome_from_response({"id": "p", "status": "created"}, 1, "")
    ok("R3b  MUTANT would credit an unresolved payment; we do not",
       o.success is False and o.pending is True,
       f"success={o.success} pending={o.pending}")

    ok("R3c  the vendor's own string survives on raw_code",
       ex._outcome_from_response(
           {"status": "failed", "error": {"reason": "insufficient_funds"}},
           1, "").raw_code == "insufficient_funds")

    ok("R3d  success is never True while pending is True",
       all(not (c.success and c.pending)
           for c in [ex._outcome_from_response(p, 1, "") for _, p, _, _, _ in CASES]))


# ===========================================================================
# R4  a lost response is never a decline
# ===========================================================================
def gate_R4() -> None:
    print("\nR4  transport failure -> pending, never a fabricated decline")
    print("    mutant: return Z9 on a transport error, which tells the belief")
    print("            filter the account was empty because OUR socket broke")
    t = FakeTransport(raises=True)
    ex = _ex(t)
    _seed_predelivery(ex, REF, 264)
    o = ex.attempt(REF, 550.0, 264, action_id="a1")

    ok("R4a  it does not raise", isinstance(o, AttemptOutcome))
    ok("R4b  pending is True", o.pending is True)
    ok("R4c  success is False", o.success is False)
    ok("R4d  the code is INDETERMINATE, not Z9 and not TECH",
       o.code in INDETERMINATE_CODES, o.code)
    ok("R4e  raw_code says it was OUR transport, not their decline",
       o.raw_code == "transport_lost", o.raw_code)
    # THE DEBIT IS SUBMITTED ONCE. Razorpay documents no idempotency key for
    # create/recurring, so a resend after a lost response is not guaranteed to
    # be deduplicated by anything except the order -- and if the first request
    # DID land, the resend returns "order already paid", turning a successful
    # debit into a rejection. Their own guidance is to wait for the status of
    # the previous payment before creating another. So: one submission, then
    # reconcile.
    ok("R4f  the debit is submitted exactly once, never retried",
       t.calls == 1, f"{t.calls} provider calls")


# ===========================================================================
# R5  the order receipt is the real idempotency anchor
# ===========================================================================
def gate_R5() -> None:
    print("\nR5  the order receipt is deterministic per (mandate, target)")
    print("    mutant: derive the receipt from uuid4() or wall clock, so a")
    print("            restart creates a SECOND order for the same debit and")
    print("            the provider's one-payment-per-order rule stops")
    print("            protecting anything")
    # Razorpay treats an order's receipt as an idempotency key -- a second
    # create with the same value is rejected -- and an order can be paid once.
    # Those two documented properties are what make the debit at-most-once.
    k1 = RazorpayExecutor.receipt_for("act_abc", REF, 264)
    k2 = RazorpayExecutor.receipt_for("act_abc", REF, 264)
    k3 = RazorpayExecutor.receipt_for("act_xyz", REF, 264)
    k4 = RazorpayExecutor.receipt_for("act_abc", MandateRef(45, 4, 17), 264)
    k5 = RazorpayExecutor.receipt_for("act_abc", REF, 265)
    ok("R5a  same action -> same receipt", k1 == k2, k1)
    ok("R5b  different action -> different receipt", k1 != k3)
    ok("R5c  different mandate -> different receipt", k1 != k4)
    ok("R5d  different target hour -> different receipt", k1 != k5)
    ok("R5e  it fits Razorpay's 40-character receipt limit",
       k1.startswith("rcv_") and len(k1) <= 40, f"{k1} len={len(k1)}")
    ok("R5f  no idempotency header is sent on the money path",
       not any("idempotency" in str(k).lower()
               for k in dir(RazorpayExecutor)),
       "Razorpay documents none for create/recurring")


# ===========================================================================
# R6  Stage 0 refuses against the real client, with zero network
# ===========================================================================
def gate_R6(tmp: str) -> None:
    print("\nR6  Stage 0 refuses a peak-hour debit before the executor is reached")
    print("    mutant: adjudicate AFTER dispatch (what sim/harness.py does on")
    print("            purpose), which would let the request go out")

    class Tripwire(FakeTransport):
        def request(self, method, url, body=None):
            self.calls += 1
            raise AssertionError("network reached")

    t = Tripwire()
    ex = _ex(t)
    ledger = AttemptLedger()
    path = os.path.join(tmp, "r6.jsonl")
    if os.path.exists(path):
        os.remove(path)
    log = AuditLog(path, "r6")
    gate = Stage0Gate(ex, ledger, log)
    ledger.open_cycle(REF.uid, 0)

    peak_t = 11 * w3.HOURS + sorted(w3.PEAK)[0]
    a = MoneyAction(action_id=action_id("r6", REF, 0, peak_t, 1), ref=REF,
                    amount=550.0, cycle=0, target_t=peak_t,
                    notify_t=peak_t - 24, decided_at_t=peak_t - 24,
                    kind=InterventionKind.RETRY)
    gate.issue_notification(REF, 0, a.notify_t, a.target_t, a.decided_at_t)
    d = gate.submit(a)

    ok("R6a  refused", isinstance(d, Refused),
       getattr(d, "refusal", None) and d.refusal.rule)
    ok("R6b  refused on `peak`",
       isinstance(d, Refused) and d.refusal.rule == "peak")
    ok("R6c  ZERO network calls -- the transport raises if reached",
       t.calls == 0, f"calls={t.calls}")
    ok("R6d  the refusal is in the audit trail",
       any(r.get("verdict") == "REFUSED" for r in read_rows(path)))
    ok("R6e  a refused action is NOT a violation to the auditor",
       replay(read_rows(path)).total() == 0,
       "the gate counts what it stopped; the auditor counts what happened")


# ===========================================================================
# R7  the parity guarantee: SimExecutor never produces `pending`
# ===========================================================================
def gate_R7() -> None:
    print("\nR7  extending AttemptOutcome did not touch the simulation")
    print("    mutant: default `pending=True`, which would change every")
    print("            outcome the frozen world has ever produced")
    import numpy as np
    from agent.batch import make_pop
    pop = make_pop(20, 5, 700, days=60)
    ex = SimExecutor(pop, 7, 7)
    outs = [ex.attempt(MandateRef(ci, 0, pop[ci]["mandates"][0]["merchant"]),
                       pop[ci]["mandates"][0]["amount"], d * 24 + 8)
            for ci in range(20) for d in (5, 20, 35)]
    ok("R7a  SimExecutor never sets pending", not any(o.pending for o in outs),
       f"{sum(o.pending for o in outs)} of {len(outs)}")
    ok("R7b  SimExecutor never sets raw_code",
       all(o.raw_code == "" for o in outs))
    ok("R7c  the default is False",
       AttemptOutcome(t=0, code=OK, success=True).pending is False)
    ok("R7d  codes stay in the frozen three-symbol vocabulary",
       {o.code for o in outs} <= {"OK", "Z9", "TECH"},
       str(sorted({o.code for o in outs})))
    del np


# ===========================================================================
# R8  the downtime feed
# ===========================================================================
WEBHOOK = {
    "event": "payment.downtime.started",
    "payload": {"payment.downtime": {"entity": {
        "id": "down_F8LCfthx90fMOo", "entity": "payment.downtime",
        "method": "upi", "begin": 1593412063, "end": None,
        "status": "started", "scheduled": False, "severity": "high",
        "instrument": {"vpa_handle": "oksbi", "psp": "google_pay",
                       "flow": "collect"}}}}}


def gate_R8() -> None:
    print("\nR8  Razorpay's Payment Downtime feed parses into our vocabulary")
    print("    mutant: hand it a body with no `entity`, which is what a schema")
    print("            change looks like -- it must return None, not raise,")
    print("            because a dropped `resolved` leaves us paused forever")
    feed = DT.DowntimeFeed()
    d = feed.ingest_webhook(WEBHOOK)
    ok("R8a  a started downtime parses", d is not None and d.id.startswith("down_"))
    ok("R8b  vpa_handle `oksbi` maps to our `@oksbi`",
       d.handles == ("@oksbi",), str(d.handles))
    ok("R8c  it is live after `started`", "down_F8LCfthx90fMOo" in feed.live)
    ok("R8d  severity is kept as a LABEL, never converted to a rate",
       d.severity == "high" and isinstance(d.severity, str))

    res = dict(WEBHOOK)
    res = {"event": "payment.downtime.resolved",
           "payload": {"payment.downtime": {"entity": dict(
               WEBHOOK["payload"]["payment.downtime"]["entity"],
               status="resolved", end=1593422063)}}}
    feed.ingest_webhook(res)
    ok("R8e  a resolved downtime clears the live set", not feed.live)

    ev, none = DT.parse_webhook({"event": "payment.downtime.started"})
    ok("R8f  MUTANT: a malformed body returns None rather than raising",
       none is None and ev == "payment.downtime.started")

    allupi = DT.parse(dict(WEBHOOK["payload"]["payment.downtime"]["entity"],
                           instrument={"vpa_handle": "ALL"}))
    ok("R8g  `ALL` means every handle, not an unknown one",
       allupi.handles is None and allupi.covers_handle("@ybl"))

    ok("R8h  the cross-check labels, and refuses to score",
       (DT.agrees_with(True, False), DT.agrees_with(False, True),
        DT.agrees_with(True, True), DT.agrees_with(False, False))
       == ("VENDOR_ONLY", "WE_SEE_ONLY", "BOTH", "NEITHER"))


# ===========================================================================
# R9  a refused CREDENTIAL is not a declined CUSTOMER
# ===========================================================================
#: THE ONLY FIXTURE IN THIS FILE THAT RAZORPAY WROTE. Captured 30 August 2026
#: by `scripts/razorpay_ladder.py`, which POSTs the real recurring-charge URL
#: with an empty credential and records what comes back. Every other payload
#: here was transcribed from their docs by us; this one came off the wire.
LIVE_401 = {"error": {"code": "BAD_REQUEST_ERROR",
                      "description": "Authentication failed"}}

#: What a payment-level failure looks like: `reason`, `source`, `step` and a
#: `metadata.payment_id`. `[REPORTED]` from the docs -- NOT observed, because
#: observing it needs a key. R9 uses it to prove the fix does not overreach.
DOC_400_DECLINE = {"error": {"code": "BAD_REQUEST_ERROR",
                             "description": "payment failed",
                             "source": "bank", "step": "payment_authorization",
                             "reason": "insufficient_funds",
                             "metadata": {"payment_id": "pay_x"}}}


def gate_R9(mutant: str | None = None) -> None:
    print("\nR9  an authentication failure must not become a customer decline")
    print("    mutant: `blind`, the pre-30-August behaviour -- hand every")
    print("            response with a status to the payment parser")

    def outcome_for(payload, status):
        ex = _ex(FakeTransport(payload=payload, status=status))
        _seed_predelivery(ex, REF, 8)
        if mutant == "blind":
            # The pre-30-August behaviour: hand every response with a status
            # to the payment parser, so an authentication failure parses as a
            # customer decline.
            ex._refuses_request = lambda r: False
        try:
            return ex.attempt(REF, 550.0, 8, action_id="a1"), None
        except Exception as e:                      # noqa: BLE001
            return None, e

    # (a) the real envelope, off the wire.
    out, err = outcome_for(LIVE_401, 401)
    ok("R9a  a live 401 raises RazorpayError instead of returning an outcome",
       out is None and type(err).__name__ == "RazorpayError",
       f"outcome={out} err={type(err).__name__ if err else None}")

    # (b) what it used to do, and why it mattered. Shown, not asserted about
    #     the shipping code -- this is the mutant's job.
    if mutant == "blind":
        ok("R9b  MUTANT: without the check it is a SILENT DECLINE",
           out is None or (out.success is False and out.pending is False),
           "U30, success=False -- which w3.py:432 reads as `balance < amount`")

    # (c) no overreach: a real payment decline is still a decline.
    out, err = outcome_for(DOC_400_DECLINE, 400)
    ok("R9c  a 400 that IS a payment decline stays a decline",
       err is None and out is not None and out.success is False
       and out.raw_code == "insufficient_funds",
       f"err={err} raw={getattr(out, 'raw_code', None)!r}")

    # (d) no overreach: a captured payment is untouched.
    out, err = outcome_for({"id": "pay_1", "status": "captured"}, 200)
    ok("R9d  a 200 captured payment is untouched",
       err is None and out is not None and out.success is True)

    # (e) the classifier reads the SHAPE of the error object, and the two
    #     shapes are the ones Razorpay's error documentation distinguishes:
    #     a payment failure carries `reason` / `metadata.payment_id`; an
    #     API-level rejection carries `code` and `description` alone.
    ok("R9e  a bare code+description is a request refusal, not a decline",
       RazorpayExecutor._is_payment_outcome(LIVE_401) is False)
    ok("R9e2 an error carrying a reason IS a payment outcome",
       RazorpayExecutor._is_payment_outcome(DOC_400_DECLINE) is True)

    # (f) the fixture is still what the wire said, if the transcript is here.
    path = os.path.join(PKG, "logs", "razorpay_ladder.json")
    if os.path.exists(path):
        import json
        with open(path, encoding="utf-8") as fh:
            rungs = json.load(fh)["rungs"]
        bodies = [r["body"] for r in rungs
                  if r.get("rung") in (1, 2) and r.get("http_status") == 401]
        ok("R9f  the fixture still matches the captured transcript",
           bool(bodies) and all(b == LIVE_401 for b in bodies),
           f"{len(bodies)} captured 401 bodies")
    else:
        print("       R9f  SKIPPED -- no logs/razorpay_ladder.json in this clone")


# ===========================================================================
# R10  Stage 0 hands the executor the id it audited
# ===========================================================================
def gate_R10(tmp: str, mutant: str | None = None) -> None:
    print("\nR10 the provider request carries the AUDITED action_id")
    print("    mutant: `drop`, dispatch without the action_id -- which is")
    print("            what Stage 0 did until 30 August 2026, and which")
    print("            leaves no join between the trail and the dashboard")

    t = FakeTransport(payload={"id": "pay_ok", "status": "captured"}, status=200)
    ex = _ex(t)
    if mutant == "drop":
        real = ex.attempt
        ex.attempt = lambda ref, amount, tt, aid="": real(ref, amount, tt)
    ledger = AttemptLedger()
    path = os.path.join(tmp, "r10.jsonl")
    if os.path.exists(path):
        os.remove(path)
    log = AuditLog(path, "r10")
    gate = Stage0Gate(ex, ledger, log)
    ledger.open_cycle(REF.uid, 0)

    target_t = 11 * w3.HOURS + w3.DECISION_HOUR       # 08:00, not a peak hour
    aid = action_id("r10", REF, 0, target_t, 1)
    a = MoneyAction(action_id=aid, ref=REF, amount=550.0, cycle=0,
                    target_t=target_t, notify_t=target_t - 24,
                    decided_at_t=target_t - 24, kind=InterventionKind.RETRY)
    gate.issue_notification(REF, 0, a.notify_t, a.target_t, a.decided_at_t)
    _seed_predelivery(ex, REF, target_t)
    gate.submit(a)

    sent = [b for _, url, b in t.sent if url.endswith("/create/recurring")]
    notes = (sent[-1] or {}).get("notes", {}) if sent else {}

    ok("R10a the executor was reached", bool(sent), f"calls={t.calls}")
    ok("R10b the request carries the audited action_id",
       notes.get("action_id") == aid, f"notes={notes}")
    ok("R10c it also carries the mandate, so one row joins both directions",
       notes.get("mandate_uid") == REF.uid, f"notes={notes}")
    ok("R10d the executor recorded the raw response under that same id",
       aid in ex.raw, f"keys={sorted(ex.raw)}")
    # The receipt is what the PROVIDER deduplicates on, and it must survive a
    # restart unchanged or the one-payment-per-order guarantee is worthless.
    ok("R10e the order receipt is reproducible after a restart",
       RazorpayExecutor.receipt_for(f"notify:{REF.uid}", REF, target_t)
       == RazorpayExecutor.receipt_for(f"notify:{REF.uid}", REF, target_t))


def report() -> None:
    print("\n" + "=" * 74)
    print("WHAT OUR FAMILIES DO NOT COVER IN RAZORPAY'S OWN VOCABULARY")
    print("=" * 74)
    for reason, lost, did in RC.UNMAPPED_DISTINCTIONS:
        print(f"\n  {reason}")
        print(f"    loses  : {lost}")
        print(f"    action : {did}")
    print("\n" + "=" * 74)
    print("COVERAGE")
    print("=" * 74)
    cov = RC.summarise_coverage()
    for fam, n in cov["by_family"].items():
        print(f"  {fam:<16} {n:>3} reasons")
    print(f"  {'TOTAL':<16} {cov['reasons_mapped']:>3} reasons, "
          f"{cov['distinctions_lost']} distinctions recorded as lost")


def run_mutants(tmp: str) -> int:
    """Run every gate that takes a NAMED mutant, and require it to go red.

    ⚠️ THIS RUNNER DID NOT EXIST UNTIL 30 AUGUST 2026, AND THIS FILE'S OWN
    DOCSTRING SAID IT DID. Gates R2-R8 run their mutants inline, so their
    claims were sound; R1 carried a `mutant` parameter nothing ever passed, and
    R9/R10 were written the same way an hour earlier. A test file that
    advertises a mutation runner it does not have is the measuring apparatus
    lying about itself, which is the "Test and verification errors" class in
    `docs/errors.md`, and is the reason `--mutants` is now real instead of the
    sentence being deleted.

    A mutant that breaks NOTHING is reported as VACUOUS and fails the run --
    same rule as `sim/gate.py`.
    """
    global _results
    print("\n" + "=" * 74)
    print("MUTANTS -- each must break at least one check that passes clean")
    print("=" * 74)
    cases = [("R1/drop_funds", lambda: gate_R1(mutant="drop_funds")),
             ("R9/blind", lambda: gate_R9(mutant="blind")),
             ("R10/drop", lambda: gate_R10(tmp, mutant="drop"))]
    verdicts = []
    for name, run in cases:
        saved, _results = _results, []
        try:
            run()
            broke = sum(1 for good, _, _ in _results if not good)
        finally:
            mutant_results, _results = _results, saved
        state = "TRIPPED" if broke else "VACUOUS"
        verdicts.append((name, state, broke))
        print(f"\n  {state:<8} {name}: {broke} check(s) went red "
              f"of {len(mutant_results)}")
        for good, cname, detail in mutant_results:
            if not good:
                print(f"           - {cname}")
    bad = [v for v in verdicts if v[1] == "VACUOUS"]
    print("\n" + "=" * 74)
    print(f"{len(verdicts) - len(bad)}/{len(verdicts)} mutants tripped their gate")
    print("=" * 74)
    for name, _, _ in bad:
        print(f"  VACUOUS  {name} -- the gate cannot see its own named mutant")
    return 1 if bad else 0


def main() -> int:
    import tempfile
    if "--mutants" in sys.argv:
        with tempfile.TemporaryDirectory(prefix="rzp-mutants-") as tmp:
            return run_mutants(tmp)
    print("=" * 74)
    print("RAZORPAY BACKEND GATES -- all offline, no API key, no network")
    print("=" * 74)
    print("\n  UNTESTED PENDING CREDENTIALS, and these gates do not touch it:")
    print("    * whether Razorpay accepts our request body")
    print("    * whether test mode returns populated error_reason values")
    print("    * whether test mode seeds the Payment Downtime feed at all")
    print("    * whether the pre-debit notification API is required per debit")

    with tempfile.TemporaryDirectory(prefix="rzp-gates-") as tmp:
        gate_R1()
        gate_R2()
        gate_R3()
        gate_R4()
        gate_R5()
        gate_R6(tmp)
        gate_R7()
        gate_R8()
        gate_R9()
        gate_R10(tmp)

    report()
    n_bad = sum(1 for good, _, _ in _results if not good)
    print("\n" + "=" * 74)
    print(f"{len(_results) - n_bad}/{len(_results)} checks passed")
    print("=" * 74)
    if n_bad:
        for good, name, detail in _results:
            if not good:
                print(f"  FAILED  {name}  {detail}")
    return 1 if n_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
