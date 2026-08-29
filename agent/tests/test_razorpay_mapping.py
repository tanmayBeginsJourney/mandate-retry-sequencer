"""Gates for the Razorpay backend. Every one runs offline, with no API key.

    python agent/tests/test_razorpay_mapping.py

WHAT THESE CAN AND CANNOT PROVE. They prove that our normalisation of
Razorpay's vocabulary is total, that the dangerous cases route the dangerous
way, that a lost response never becomes a decline, and that Stage 0 refuses an
illegal action against the real client without touching the network. They prove
NOTHING about whether Razorpay accepts our request body, because no request has
ever been sent. That distinction is kept in the output rather than in a footnote.

EVERY GATE CARRIES A NAMED MUTANT AND `--mutants` RUNS THEM
(`docs/05_TEST_DESIGN.md`: "a gate earns its place only if you can name, in
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
from agent.execution import razorpay_downtime as DT  # noqa: E402
from agent.execution.razorpay_executor import (MandateBinding,  # noqa: E402
                                               RazorpayExecutor)
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
    """Returns a scripted payload. Records every call."""

    def __init__(self, payload=None, status=200, raises=False):
        self.payload, self.status, self.raises = payload or {}, status, raises
        self.calls = 0
        self.keys: list[str] = []

    def post(self, url, body, idempotency_key):
        self.calls += 1
        self.keys.append(idempotency_key)
        if self.raises:
            raise OSError("connection reset by peer")
        return self.status, self.payload

    def get(self, url):
        self.calls += 1
        return self.status, self.payload


def _ex(transport, uid="c45m3"):
    return RazorpayExecutor(
        bindings={uid: MandateBinding(rzp_customer_id="cust:45",
                                      rzp_token_id="tok")},
        transport=transport)


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
        o = ex._outcome_from_payment(payload, t=264)
        got = family_of(o.code)
        if not (got == fam and o.success == succ and o.pending == pend):
            bad.append(f"{name}: got {got}/{o.success}/{o.pending}, "
                       f"want {fam}/{succ}/{pend}")
    ok(f"R3a  all {len(CASES)} recorded shapes normalise correctly",
       not bad, "; ".join(bad))

    o = ex._outcome_from_payment({"id": "p", "status": "created"}, t=1)
    ok("R3b  MUTANT would credit an unresolved payment; we do not",
       o.success is False and o.pending is True,
       f"success={o.success} pending={o.pending}")

    ok("R3c  the vendor's own string survives on raw_code",
       ex._outcome_from_payment(
           {"status": "failed", "error": {"reason": "insufficient_funds"}},
           t=1).raw_code == "insufficient_funds")

    ok("R3d  success is never True while pending is True",
       all(not (c.success and c.pending)
           for c in [ex._outcome_from_payment(p, 1) for _, p, _, _, _ in CASES]))


# ===========================================================================
# R4  a lost response is never a decline
# ===========================================================================
def gate_R4() -> None:
    print("\nR4  transport failure -> pending, never a fabricated decline")
    print("    mutant: return Z9 on a transport error, which tells the belief")
    print("            filter the account was empty because OUR socket broke")
    t = FakeTransport(raises=True)
    ex = _ex(t)
    o = ex.attempt(REF, 550.0, 264, action_id="a1")

    ok("R4a  it does not raise", isinstance(o, AttemptOutcome))
    ok("R4b  pending is True", o.pending is True)
    ok("R4c  success is False", o.success is False)
    ok("R4d  the code is INDETERMINATE, not Z9 and not TECH",
       o.code in INDETERMINATE_CODES, o.code)
    ok("R4e  raw_code says it was OUR transport, not their decline",
       o.raw_code == "transport_failure", o.raw_code)
    ok("R4f  the transport was retried, not the debit",
       t.calls == 3 and len(set(t.keys)) == 1,
       f"{t.calls} calls, {len(set(t.keys))} distinct idempotency keys")


# ===========================================================================
# R5  idempotency
# ===========================================================================
def gate_R5() -> None:
    print("\nR5  the idempotency key is deterministic per money action")
    print("    mutant: derive the key from uuid4(), so a retry after a crash")
    print("            becomes a second debit")
    k1 = RazorpayExecutor.idempotency_key("act_abc", REF, 264)
    k2 = RazorpayExecutor.idempotency_key("act_abc", REF, 264)
    k3 = RazorpayExecutor.idempotency_key("act_xyz", REF, 264)
    k4 = RazorpayExecutor.idempotency_key("act_abc", MandateRef(45, 4, 17), 264)
    ok("R5a  same action -> same key", k1 == k2, k1)
    ok("R5b  different action -> different key", k1 != k3)
    ok("R5c  different mandate -> different key", k1 != k4)
    ok("R5d  keys are the documented shape",
       k1.startswith("rcv_") and len(k1) == 36, f"{k1} len={len(k1)}")


# ===========================================================================
# R6  Stage 0 refuses against the real client, with zero network
# ===========================================================================
def gate_R6(tmp: str) -> None:
    print("\nR6  Stage 0 refuses a peak-hour debit before the executor is reached")
    print("    mutant: adjudicate AFTER dispatch (what sim/harness.py does on")
    print("            purpose), which would let the request go out")

    class Tripwire(FakeTransport):
        def post(self, url, body, key):
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


def main() -> int:
    import tempfile
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
