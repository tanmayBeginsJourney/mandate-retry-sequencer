"""S-gates: the two state machines, exhaustively.

`domain.advance` and `domain.advance_mandate` are pure, so every pair of states
can be enumerated rather than sampled. That matters: the property being checked
is "no reachable pair walks backwards", and a sampled version of that check
passes on the pairs it happens to try.
"""
from __future__ import annotations

import itertools

import live.tests  # noqa: F401
from live.domain import (ATTEMPT_TERMINAL, ATTEMPT_UNRESOLVED, MANDATE_TERMINAL,
                         AttemptState, MandateState, PAYMENT_STATUS_STATE,
                         TOKEN_STATUS_STATE, Transition, advance,
                         advance_mandate, from_payment_entity)
from live.tests._harness import Results

#: Rank order, written out independently of the table in `domain.py`. A gate
#: that imported `_RANK` would be checking the table against itself.
EXPECTED_ORDER = [
    AttemptState.INTENT,
    AttemptState.ORDER_CREATED,
    AttemptState.NOTIFIED,
    AttemptState.SUBMITTED,
    AttemptState.UNKNOWN,
    AttemptState.AUTHORIZED,
]


def main() -> int:
    r = Results("LIVE STATE-MACHINE GATES (offline)")

    # ------------------------------------------------------------------ S1
    r.section("S1  progress is monotonic: no pair ever goes backwards")
    backwards = []
    for i, earlier in enumerate(EXPECTED_ORDER):
        for later in EXPECTED_ORDER[i + 1:]:
            if advance(later, earlier) is not Transition.IGNORED_STALE:
                backwards.append(f"{later.value} -> {earlier.value}")
    r.ok("S1a  every backwards move is refused as stale",
         not backwards, "; ".join(backwards))

    forwards_bad = []
    for i, earlier in enumerate(EXPECTED_ORDER):
        for later in EXPECTED_ORDER[i + 1:]:
            if advance(earlier, later) is not Transition.APPLIED:
                forwards_bad.append(f"{earlier.value} -> {later.value}")
    r.ok("S1b  every forwards move is applied",
         not forwards_bad, "; ".join(forwards_bad))

    r.ok("S1c  a state never advances to itself",
         all(advance(s, s) is Transition.IGNORED_STALE
             for s in AttemptState),
         "which is what makes a redelivered webhook a no-op")

    # ------------------------------------------------------------------ S2
    r.section("S2  terminal states are final")
    non_terminal = [s for s in AttemptState if s not in ATTEMPT_TERMINAL]
    bad = [f"{t.value} -> {s.value}"
           for t in ATTEMPT_TERMINAL for s in non_terminal
           if advance(t, s) is not Transition.IGNORED_STALE]
    r.ok("S2a  nothing moves a terminal attempt back to a live one",
         not bad, "; ".join(bad))
    pairs = [(a, b) for a, b in itertools.permutations(ATTEMPT_TERMINAL, 2)]
    r.ok("S2b  one terminal state to another is a CONFLICT, not a silent write",
         all(advance(a, b) is Transition.CONFLICT for a, b in pairs),
         f"{[(a.value, b.value) for a, b in pairs]}")

    # ------------------------------------------------------------------ S3
    r.section("S3  the unresolved set is exactly the non-terminal set")
    r.ok("S3a  every non-terminal state is polled for reconciliation",
         set(ATTEMPT_UNRESOLVED) == set(AttemptState) - set(ATTEMPT_TERMINAL),
         "a state outside both sets would never be resolved and never noticed")
    r.ok("S3b  UNKNOWN is unresolved, not a failure",
         AttemptState.UNKNOWN in ATTEMPT_UNRESOLVED
         and AttemptState.UNKNOWN not in ATTEMPT_TERMINAL,
         "a lost response may still have taken the customer's money")

    # ------------------------------------------------------------------ S4
    r.section("S4  the mandate machine: paused resumes, cancelled does not")
    r.ok("S4a  PENDING becomes ACTIVE",
         advance_mandate(MandateState.PENDING, MandateState.ACTIVE)
         is Transition.APPLIED)
    r.ok("S4b  ACTIVE becomes PAUSED",
         advance_mandate(MandateState.ACTIVE, MandateState.PAUSED)
         is Transition.APPLIED)
    r.ok("S4c  PAUSED becomes ACTIVE again -- it is not terminal",
         advance_mandate(MandateState.PAUSED, MandateState.ACTIVE)
         is Transition.APPLIED,
         "a customer can resume a paused UPI mandate")
    bad = [f"{t.value} -> {s.value}"
           for t in MANDATE_TERMINAL for s in MandateState
           if s is not t and advance_mandate(t, s) is not Transition.IGNORED_STALE]
    r.ok("S4d  a cancelled or rejected mandate never comes back",
         not bad, "; ".join(bad))

    # ------------------------------------------------------------------ S5
    r.section("S5  provider vocabularies map completely and safely")
    r.ok("S5a  all five documented token statuses are mapped",
         set(TOKEN_STATUS_STATE) == {"initiated", "confirmed", "rejected",
                                     "cancelled", "paused"},
         str(sorted(TOKEN_STATUS_STATE)))
    r.ok("S5b  only `confirmed` maps to ACTIVE",
         [k for k, v in TOKEN_STATUS_STATE.items()
          if v is MandateState.ACTIVE] == ["confirmed"])
    r.ok("S5c  an unknown token status maps to nothing, and is not defaulted",
         TOKEN_STATUS_STATE.get("suspended") is None,
         "defaulting would silently stop a mandate being chargeable")
    r.ok("S5d  `refunded` is not an attempt state",
         "refunded" not in PAYMENT_STATUS_STATE,
         "a refund follows a capture; treating it as one would un-collect a cycle")

    # ------------------------------------------------------------------ S6
    r.section("S6  reading a payment entity")
    ok_entity = {"id": "pay_1", "order_id": "order_1", "status": "captured",
                 "amount": 100}
    v = from_payment_entity(ok_entity)
    r.ok("S6a  a captured payment is SUCCEEDED",
         v.state is AttemptState.SUCCEEDED and v.outcome_code == "OK")

    failed = {"id": "pay_2", "order_id": "order_2", "status": "failed",
              "amount": 100, "error_reason": "insufficient_funds"}
    v = from_payment_entity(failed)
    r.ok("S6b  a declined payment is FAILED and keeps the vendor's reason",
         v.state is AttemptState.FAILED and v.raw_reason == "insufficient_funds"
         and v.outcome_code == "Z9", f"{v.state.value}/{v.outcome_code}")

    # THE ONE THAT MATTERS MOST. Razorpay reports a deemed transaction as a
    # `failed` payment, and it means "we do not know whether the money moved".
    # Recording it as FAILED licenses a retry, and the retry is what charges
    # the customer twice.
    deemed = {"id": "pay_3", "order_id": "order_3", "status": "failed",
              "amount": 100, "error_reason": "deemed_transaction"}
    v = from_payment_entity(deemed)
    r.ok("S6c  a deemed transaction is UNKNOWN, not FAILED",
         v.state is AttemptState.UNKNOWN, v.state.value)
    r.ok("S6d  and UNKNOWN is not terminal, so it stays open for reconciliation",
         v.state not in ATTEMPT_TERMINAL)

    dup = {"id": "pay_4", "order_id": "order_4", "status": "failed",
           "amount": 100, "error_reason": "duplicate_rrn_found"}
    r.ok("S6e  so is a duplicate RRN",
         from_payment_entity(dup).state is AttemptState.UNKNOWN)

    created = {"id": "pay_5", "order_id": "order_5", "status": "created",
               "amount": 100}
    r.ok("S6f  a created payment is SUBMITTED, not a success and not a failure",
         from_payment_entity(created).state is AttemptState.SUBMITTED)

    r.ok("S6g  an entity with an unrecognised status does not become terminal",
         from_payment_entity({"id": "p", "status": "quantum"}).state
         not in ATTEMPT_TERMINAL,
         "a provider vocabulary change must not resolve a payment by accident")

    return r.summary()


if __name__ == "__main__":
    raise SystemExit(main())
