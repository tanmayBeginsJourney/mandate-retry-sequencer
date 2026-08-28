"""Stage 0 REFUSES, and the auditor can prove it independently.

TWO HALVES, AND THEY MUST BOTH BE HERE.

  Half A -- the gate refuses. Submit each of the five illegal actions and
            require a Refusal naming the right rule.

  Half B -- the independent witness works. Bypass the gate entirely, move money
            illegally, write the resulting rows to the audit log, and require
            `auditor.replay()` to find every violation from the log alone.

Half A on its own is worthless. A gate that refuses everything the test feeds
it proves nothing about whether the AUDITOR would notice if the gate had a
hole -- and an enforcement layer whose only check is its own predicates is the
vacuous-gate shape this project has shipped five times (docs/03_ERRORS.md).
Half B is what makes the enforcement claim falsifiable.

THE INJECTION TOUCHES NO COUNTER. It calls the executor and writes log rows.
It does not increment `gate.refusals`, it does not touch `IndependentCounts`,
and it does not call anything in `rules.py`. That is rule 1a, added after
error 11: a mutant may create illegal state and NOTHING else. A mutant that
writes to the scoreboard is grading itself, which is exactly how gate M4
reported PASS for the life of the suite on 1066 violations it had written.

CAP AND PENDING HAVE NO REFERENCE IMPLEMENTATION. `sim/harness.py`'s counters
for those two have never been shown to work -- M1 is VACUOUS (the cap is never
the binding constraint at either operating point) and M4's mutant increments
`V.pending` itself. So the two cases below are written from the rule text in
docs/01_FACTS.md, not ported from the harness, and they are the only working
test either rule has anywhere in this repo.
"""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

import agent  # noqa: F401
import w3

from agent.audit.log import AuditLog, EventKind, read_rows
from agent.constraints.auditor import replay
from agent.constraints.rules import AttemptLedger
from agent.constraints.stage0 import Stage0Gate
from agent.execution.sim_executor import SimExecutor
from agent.ports import (TECH, Z9, InterventionKind, MandateRef, MoneyAction,
                         Refused, to_paise)

RESULTS: list[tuple[str, bool, str]] = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def _legal_hour(day: int, h: int = 8) -> int:
    assert h not in w3.PEAK
    return day * 24 + h


def _fixture(tmp: str, tag: str):
    pop = w3.make_pop(3, 2, __import__("numpy").random.default_rng(1), days=120,
                      spend=1.05)
    ex = SimExecutor(pop, seed=7, payday_err=7)
    ledger = AttemptLedger()
    log = AuditLog(os.path.join(tmp, f"{tag}.jsonl"), tag)
    gate = Stage0Gate(ex, ledger, log)
    ref = MandateRef(0, 0, pop[0]["mandates"][0]["merchant"])
    amount = pop[0]["mandates"][0]["amount"]
    return pop, ex, ledger, log, gate, ref, amount


def _action(ref, amount, cycle, target_t, notify_t, aid="x"):
    return MoneyAction(action_id=aid, ref=ref, amount=amount, cycle=cycle,
                       target_t=target_t, notify_t=notify_t,
                       decided_at_t=target_t - 24,
                       kind=InterventionKind.RETRY)


# ===================================================================== HALF A
def half_a(tmp: str) -> None:
    """The gate refuses each of the five."""

    # --- peak
    pop, ex, ledger, log, gate, ref, amt = _fixture(tmp, "a_peak")
    peak_t = 5 * 24 + 11                        # 11:00, inside 10:00-13:00
    ledger.set_pending(ref.uid, __import__("agent.ports", fromlist=["x"])
                       .PendingNotification(peak_t - 24, peak_t, False))
    d = gate.submit(_action(ref, amt, 0, peak_t, peak_t - 24))
    ok("A/peak refused", isinstance(d, Refused) and d.refusal.rule == "peak",
       repr(d))
    ok("A/peak did not execute", ex.n_attempts == 0, f"{ex.n_attempts} attempts")
    log.close()

    # --- lead
    pop, ex, ledger, log, gate, ref, amt = _fixture(tmp, "a_lead")
    t = _legal_hour(5)
    ledger.set_pending(ref.uid, __import__("agent.ports", fromlist=["x"])
                       .PendingNotification(t - 1, t, False))
    d = gate.submit(_action(ref, amt, 0, t, t - 1))       # 1h lead, need 24h
    ok("A/lead refused", isinstance(d, Refused) and d.refusal.rule == "lead",
       repr(d))
    ok("A/lead did not execute", ex.n_attempts == 0)
    log.close()

    # --- cap  (NO REFERENCE IMPLEMENTATION -- written from the rule text)
    pop, ex, ledger, log, gate, ref, amt = _fixture(tmp, "a_cap")
    for i in range(w3.NPCI_MAX):
        t = _legal_hour(3 + i)
        gate.issue_notification(ref, 0, t - 24, t, t - 24)
        gate.submit(_action(ref, amt, 0, t, t - 24, aid=f"c{i}"))
    ok("A/cap: 4 legal attempts allowed", ex.n_attempts == w3.NPCI_MAX,
       f"{ex.n_attempts}")
    t5 = _legal_hour(3 + w3.NPCI_MAX)
    r = gate.issue_notification(ref, 0, t5 - 24, t5, t5 - 24)
    ok("A/cap: 5th notification refused", r is not None and r.rule == "cap",
       repr(r))
    ledger.set_pending(ref.uid, __import__("agent.ports", fromlist=["x"])
                       .PendingNotification(t5 - 24, t5, False))
    d = gate.submit(_action(ref, amt, 0, t5, t5 - 24, aid="c5"))
    ok("A/cap: 5th dispatch refused",
       isinstance(d, Refused) and d.refusal.rule == "cap", repr(d))
    ok("A/cap: still 4 attempts", ex.n_attempts == w3.NPCI_MAX,
       f"{ex.n_attempts}")
    log.close()

    # --- pending  (NO REFERENCE IMPLEMENTATION -- written from the rule text)
    pop, ex, ledger, log, gate, ref, amt = _fixture(tmp, "a_pending")
    t1 = _legal_hour(4)
    r1 = gate.issue_notification(ref, 0, t1 - 24, t1, t1 - 24)
    ok("A/pending: first notification allowed", r1 is None, repr(r1))
    t2 = _legal_hour(6)
    r2 = gate.issue_notification(ref, 0, t2 - 24, t2, t2 - 24)
    ok("A/pending: second while one outstanding refused",
       r2 is not None and r2.rule == "pending", repr(r2))
    log.close()

    # --- represent
    pop, ex, ledger, log, gate, ref, amt = _fixture(tmp, "a_represent")
    t = _legal_hour(4)
    gate.issue_notification(ref, 0, t - 24, t, t - 24)
    gate.submit(_action(ref, amt, 0, t, t - 24, aid="r0"))
    ledger._prev_code[(ref.uid, 0)] = Z9         # force a Z9 for the test
    t2 = _legal_hour(5)
    r = gate.issue_notification(ref, 0, None, t2, t2 - 24)   # no fresh notice
    ok("A/represent: Z9 re-presentation refused",
       r is not None and r.rule == "represent", repr(r))
    ledger._prev_code[(ref.uid, 0)] = TECH
    r = gate.issue_notification(ref, 0, None, t2, t2 - 24)
    ok("A/represent: TECH re-presentation allowed", r is None, repr(r))
    log.close()


# ===================================================================== HALF B
def half_b(tmp: str) -> None:
    """Bypass the gate, move money illegally, and require the auditor to see it.

    NOTHING BELOW TOUCHES A COUNTER. It calls the executor and appends log rows.
    """
    path = os.path.join(tmp, "b_injected.jsonl")
    pop, ex, ledger, log, gate, ref, amt = _fixture(tmp, "b_clean")

    # ---- first, a CLEAN run through the gate. The auditor must see zero.
    clean = os.path.join(tmp, "b_clean.jsonl")
    for i in range(3):
        t = _legal_hour(3 + i)
        gate.issue_notification(ref, 0, t - 24, t, t - 24)
        gate.submit(_action(ref, amt, 0, t, t - 24, aid=f"ok{i}"))
    log.close()
    c = replay(read_rows(clean))
    ok("B/clean: auditor finds 0 violations", c.total() == 0,
       f"{c.asdict()} :: {c.detail}")
    ok("B/clean: auditor counts the executions", c.executed == 3,
       f"executed={c.executed}")

    # ---- now the injection. Same executor type, no gate, no counters.
    ex2 = SimExecutor(pop, seed=7, payday_err=7)
    inj = AuditLog(path, "injected")
    ref2 = MandateRef(1, 0, pop[1]["mandates"][0]["merchant"])
    amt2 = pop[1]["mandates"][0]["amount"]

    def bypass(aid, target_t, notify_t, cycle=0, note=""):
        """Move money without asking. Writes exactly what the gate would have
        written had it allowed the action -- and nothing else."""
        outcome = ex2.attempt(ref2, amt2, target_t)
        inj.emit(EventKind.MONEY_ACTION, target_t, action_id=aid,
                 mandate_uid=ref2.uid, customer_id=ref2.customer_id,
                 merchant_id=ref2.merchant_id, cycle=cycle,
                 amount_paise=to_paise(amt2), intervention_kind="RETRY",
                 target_t=target_t, notify_t=notify_t,
                 gate_verdict="ALLOWED", injected_note=note)
        inj.emit(EventKind.OUTCOME, outcome.t, action_id=aid,
                 mandate_uid=ref2.uid, merchant_id=ref2.merchant_id,
                 cycle=cycle, outcome_code=outcome.code,
                 success=outcome.success,
                 recovered_paise=to_paise(amt2) if outcome.success else 0)
        return outcome

    # peak: 11:00 is inside 10:00-13:00
    pt = 10 * 24 + 11
    inj.emit(EventKind.NOTIFICATION_ISSUED, pt - 24, mandate_uid=ref2.uid,
             merchant_id=ref2.merchant_id, cycle=0, notify_t=pt - 24,
             target_t=pt)
    bypass("inj_peak", pt, pt - 24, note="peak hour")

    # lead: 2h between notification and execution
    lt = _legal_hour(12)
    inj.emit(EventKind.NOTIFICATION_ISSUED, lt - 2, mandate_uid=ref2.uid,
             merchant_id=ref2.merchant_id, cycle=0, notify_t=lt - 2,
             target_t=lt)
    bypass("inj_lead", lt, lt - 2, note="2h lead")

    # pending: two notifications outstanding at once
    n1 = _legal_hour(14)
    n2 = _legal_hour(15)
    inj.emit(EventKind.NOTIFICATION_ISSUED, n1 - 24, mandate_uid=ref2.uid,
             merchant_id=ref2.merchant_id, cycle=0, notify_t=n1 - 24,
             target_t=n1)
    inj.emit(EventKind.NOTIFICATION_ISSUED, n2 - 24, mandate_uid=ref2.uid,
             merchant_id=ref2.merchant_id, cycle=0, notify_t=n2 - 24,
             target_t=n2)
    bypass("inj_pend", n1, n1 - 24, note="second pending notification")

    # cap: keep going past 4 in the same cycle
    for i in range(4):
        t = _legal_hour(16 + i)
        inj.emit(EventKind.NOTIFICATION_ISSUED, t - 24, mandate_uid=ref2.uid,
                 merchant_id=ref2.merchant_id, cycle=0, notify_t=t - 24,
                 target_t=t)
        bypass(f"inj_cap{i}", t, t - 24, note="pushing past the cap")

    # represent: no fresh notification after a Z9
    rt = _legal_hour(25)
    bypass("inj_repr", rt, None, note="re-presented under the old notice")
    inj.close()

    a = replay(read_rows(path))
    ok("B/injected: peak seen", a.peak >= 1, f"peak={a.peak}")
    ok("B/injected: lead seen", a.lead >= 1, f"lead={a.lead}")
    ok("B/injected: pending seen", a.pending >= 1, f"pending={a.pending}")
    ok("B/injected: cap seen", a.cap >= 1, f"cap={a.cap}")
    ok("B/injected: represent seen", a.represent >= 1,
       f"represent={a.represent}")
    ok("B/injected: all five rules independently detected",
       all(v >= 1 for v in a.asdict().values()), str(a.asdict()))

    print("\n  auditor's own account of the injected run:")
    for line in a.detail:
        print(f"    {line}")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        half_a(tmp)
        half_b(tmp)

    print()
    print("=" * 70)
    print("STAGE 0 -- enforcement (A) and independent detection (B)")
    print("=" * 70)
    fails = 0
    for name, passed, detail in RESULTS:
        flag = "PASS" if passed else "FAIL"
        fails += 0 if passed else 1
        print(f"  {flag}  {name}" + (f"   [{detail}]" if not passed else ""))
    print()
    print(f"{len(RESULTS) - fails}/{len(RESULTS)} checks passed")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
