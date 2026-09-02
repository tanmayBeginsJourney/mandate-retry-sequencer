"""`RazorpayExecutor` -- the same port, a different world.

THE POINT OF THIS FILE IS THAT NOTHING ELSE CHANGES. `agent/ports.py` declares
the executor port. `attempt()` is the money path. `remind()` writes a funding
notice (never a Payment Link). `backup_checkout()` is the last-attempt Payment
Link. `escalate()` appends a merchant-queue file. `SimExecutor` implements the
same methods against the simulation. `agent/loop.py`, `agent/policy/`,
`agent/constraints/` and `agent/audit/` are byte-identical either way, because
gate **I2** already forbids anything but `constraints/stage0.py` and a
composition root from holding an executor at all.

Stage 0 adjudicates before `_api` is ever reached, so a peak-hour debit is
refused with **zero network traffic** against either backend.
`scripts/prove_stage0_refuses.py` demonstrates that end to end with no API key.

WHERE THE HTTP LIVES. Not here. `agent/execution/razorpay_api.py` owns URLs,
authentication, request bodies and the four outcomes a payment API can return;
`agent/execution/razorpay_mock.py` implements the same surface without a
socket. This file turns those answers into `agent.ports` vocabulary and does
nothing else with the network.

---------------------------------------------------------------------------
THE TWO THINGS THIS EXECUTOR GETS RIGHT THAT A NAIVE ONE DOES NOT
---------------------------------------------------------------------------

1. **AN ACCEPTED SUBMISSION IS NOT A COLLECTED PAYMENT, AND IT IS NOT A
   DECLINE EITHER.** `POST /v1/payments/create/recurring` answers
   `{"razorpay_payment_id": "pay_..."}` -- no `status`, no `error_reason`.
   [VERIFIED] against Razorpay's create-subsequent-payments reference, read
   3 September 2026. A client that reads that as a payment entity finds no
   status, concludes "declined", and hands `success=False` to the belief
   filter, which hard-zeroes every balance bin at or above the amount
   (`w3.py:432`) for every mandate that customer holds. So a successful
   submission returns `pending=True`: the provider has the request and has not
   said what happened. The answer arrives on `payment.captured` or
   `payment.failed`, or from `GET /v1/payments/:id`.

2. **A TRANSPORT FAILURE IS `pending`, NEVER A DECLINE.** If the connection
   drops we do not know whether the debit landed. `Z9` would be a lie about
   the customer derived from a fact about our network.

---------------------------------------------------------------------------
IDEMPOTENCY, AND A HEADER THAT USED TO BE HERE
---------------------------------------------------------------------------

This executor previously sent `X-Razorpay-Idempotency-Key` on the recurring
charge. **Razorpay documents no idempotency header for that endpoint** -- the
documented one, `X-Payout-Idempotency`, is for RazorpayX Payouts and a small
set of explicitly idempotent Route and Refund endpoints. [VERIFIED]
3 September 2026. A header the provider ignores reads like a guarantee, so it
is gone.

What replaces it is documented and real: the order `receipt` is unique per
account, and an order can be paid once. `receipt_for` derives the receipt from
the `action_id` Stage 0 already computed -- itself a hash of
`(run_id, mandate, cycle, target_t, attempt_no)` -- so the SAME logical debit
produces the SAME receipt after a crash, and the provider refuses the second
order. One order per attempt makes the debit at-most-once at Razorpay. It does
NOT make it exactly-once: a retry gets a rejection rather than a replayed
result, so the caller still has to reconcile. `live/service.py` does.

---------------------------------------------------------------------------
TWO CLOCKS, RECONCILED AT ONE LINE
---------------------------------------------------------------------------

Stage 0 counts time in simulated hours -- its peak rule is `target_t % 24`.
Razorpay wants `payment_after` as a Unix epoch second. No single integer is
both, and this executor used to receive one field and read it as the other,
which is why it could never be driven end to end by the constraint layer.

`epoch_origin` is the wall-clock second that simulated hour 0 corresponds to,
and `_epoch` is the only place the conversion happens. It defaults to 0, and
at 0 the executor REFUSES to create an order rather than sending a
`payment_after` in 1970. An unset clock is a configuration error, not a
default.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass

# The reason -> family map lives in ports.py, not here. Gate I2 forbids a
# sibling import inside agent/execution, and rule I1 forbids agent/llm from
# reaching agent.execution at all -- so a table the narrative layer may need
# could never have lived in this package. See ports.py.
from agent.audit.jsonl_queue import append_jsonl
from agent.execution.razorpay_api import (API_BASE, MIN_AMOUNT_PAISE,
                                          Outcome, RazorpayApi, Transport,
                                          first_item, parse_order_id,
                                          parse_payment_id)
from agent.execution.razorpay_predelivery import (DEBIT_ATTEMPTED,
                                                  NOTIFICATION_DELIVERED,
                                                  NOTIFICATION_FAILED,
                                                  ORDER_CREATED,
                                                  PredeliveryOrder,
                                                  PredeliveryPhase,
                                                  apply_notification_webhook,
                                                  effective_amount_paise,
                                                  envelope_record,
                                                  parse_notification_webhook)
from agent.execution.smtp_delivery import SMTP_SENT, deliver_smtp
from agent.ports import (OK, AttemptOutcome, MandateRef, Rupees, WorkflowResult,
                         bank_of, code_for_reason, is_pending, to_paise)

#: Razorpay's OWN documented retry schedule for a failed subscription charge:
#: attempt on T, then T+1, T+2, T+3, after which the subscription moves to
#: `halted`. [VERIFIED] from their Payment Retries page, 29 August 2026.
#:
#: Recorded here because it does two jobs. It is independent corroboration of
#: the NPCI attempt cap -- 1 presentation plus 3 retries -- which
#: `docs/results.md` had only from a secondary source. And it means
#: `harness.baseline_doc`, the naive comparator this project measures against,
#: is a fair rendering of what the vendor actually does rather than a strawman.
VENDOR_RETRY_OFFSETS_DAYS = (0, 1, 2, 3)
VENDOR_TERMINAL_STATE = "halted"

#: What a successful submission looks like in `AttemptOutcome.raw_code`. The
#: `code` beside it is the INDETERMINATE family's canonical member, because
#: "the provider has the request and has not answered" is the same *decision*
#: as a deemed transaction: do not retry, go and find out. The raw code keeps
#: the two distinguishable in the audit trail.
SUBMITTED_RAW = "submitted_awaiting_outcome"
_UNKNOWN_CODE = code_for_reason("deemed_transaction")


class RazorpayError(RuntimeError):
    """Raised only when the provider refused the REQUEST -- a bad credential, a
    mandate with no token, an order that cannot be paid. Never for a declined
    payment and never for a transport failure: both of those are outcomes, and
    an outcome is a return value."""


@dataclass(frozen=True)
class MandateBinding:
    """What Razorpay needs to charge one of our mandates.

    Our `MandateRef` is `(customer_id, mandate_index, merchant_id)`, which is
    the simulation's identity. Razorpay's is a `customer_id` and a `token_id`
    returned when the AutoPay mandate was authorised. Nothing derives one from
    the other, so the binding is data supplied by the caller and this class is
    where the gap is visible instead of implied.
    """
    rzp_customer_id: str
    rzp_token_id: str
    #: Required by POST /v1/payments/create/recurring. Stage 0 passes
    #: amount=0.0 to notify(); `charge_amount` supplies the order amount.
    rzp_email: str = ""
    rzp_contact: str = ""
    charge_amount: float = 0.0
    #: Bootstrap estimates for the belief filter's cold start. IN PRODUCTION
    #: THESE ARE THE OPEN PROBLEM: a real integration has no oracle for a
    #: customer's salary or payday. The honest options are a population prior
    #: or a wide prior the first cycle sharpens. Neither is measured here.
    est_salary: float = 0.0
    est_payday: int = 0
    #: Explicit index into the simulated population, if this binding came from
    #: one. Ordinary Razorpay ids such as `cust_ABC123` encode no such thing.
    sim_customer_id: int | None = None


class PredeliveryJournal:
    """Where pre-debit orders survive a restart.

    The default is memory, which is right for a batch run that starts and ends
    in one process. `live/service.py` passes a SQLite-backed implementation,
    because a service that forgets it created an order will create a second one
    -- and the provider will refuse it, which is safe but leaves the debit
    stuck behind a rejection nobody expected.
    """

    def __init__(self) -> None:
        self._rows: dict[tuple[str, int], PredeliveryOrder] = {}

    def load(self, mandate_uid: str, target_t: int) -> PredeliveryOrder | None:
        return self._rows.get((mandate_uid, target_t))

    def save(self, rec: PredeliveryOrder) -> None:
        self._rows[(rec.mandate_uid, rec.target_t)] = rec

    def all(self) -> list[PredeliveryOrder]:
        return list(self._rows.values())


class RazorpayExecutor:
    """Implements `agent.ports.Executor`. Only `Stage0Gate` may hold one."""

    def __init__(self, bindings: dict[str, MandateBinding],
                 key_id: str | None = None, key_secret: str | None = None,
                 transport=None, api=None, currency: str = "INR",
                 epoch_origin: int = 0,
                 journal: PredeliveryJournal | None = None,
                 max_live_nudges: int | None = None,
                 max_live_escalations: int | None = None):
        self.bindings = bindings
        self.currency = currency
        self.epoch_origin = int(epoch_origin)
        self.journal = journal or PredeliveryJournal()
        self.max_live_nudges = max_live_nudges
        if self.max_live_nudges is None:
            self.max_live_nudges = int(
                os.environ.get("RAZORPAY_MAX_LIVE_NUDGES", "5"))
        self.max_live_escalations = max_live_escalations
        if self.max_live_escalations is None:
            self.max_live_escalations = int(
                os.environ.get("RAZORPAY_MAX_LIVE_ESCALATIONS", "5"))
        self.n_live_nudges = 0
        self.n_live_escalations = 0
        self.n_live_backups = 0
        self.n_escalations = 0
        self.notify_email = os.environ.get("RECOVERY_NOTIFY_EMAIL", "").strip()
        self._last_smtp = None
        self.outbox_path = os.environ.get(
            "RECOVERY_OUTBOX",
            os.path.join("agent", "runs", "customer_outbox.jsonl"))
        self.queue_path = os.environ.get(
            "RECOVERY_QUEUE",
            os.path.join("agent", "runs", "merchant_queue.jsonl"))
        self._backup_ids: dict[str, str] = {}
        self.workflow_log: list[dict] = []
        self.pending_outcomes = 0
        #: `{customer_id: handle}`, the same shape `SimExecutor` exposes, so
        #: `agent/loop.py` can put a bank on the `CaseView` either way.
        #:
        #: IN PRODUCTION THIS IS A STAND-IN. The real remitter handle is on the
        #: customer's VPA (`user@oksbi`) and is something the merchant can read
        #: off their own transaction report -- which is the argument
        #: `agent/llm/caseview.py` makes for letting it cross the redaction
        #: boundary at all. A real integration reads it from the payment
        #: object; hashing an index keeps the two backends the same shape.
        self.banks = {int(uid.split("m")[0][1:]): bank_of(int(uid.split("m")[0][1:]))
                      for uid in bindings}
        #: Every raw response, keyed by our action_id, so the audit trail's
        #: normalisation can be reconciled against the vendor's own words.
        #: Bounded by the run.
        self.raw: dict[str, dict] = {}
        #: Append-only sanitized proof transcript.
        self.predelivery_log: list[dict] = []
        #: The most recent `notify()` result, per mandate uid. `Stage0Gate`
        #: calls `notify` and does not return what it said, so without this a
        #: caller can only observe that no order exists and cannot say why.
        self.last_notify: dict[str, dict] = {}

        if api is not None:
            self._api = api
        else:
            if transport is None:
                key_id = key_id or os.environ.get("RAZORPAY_KEY_ID", "")
                key_secret = key_secret or os.environ.get(
                    "RAZORPAY_KEY_SECRET", "")
                if not key_id or not key_secret:
                    raise RazorpayError(
                        "No Razorpay credentials. Set RAZORPAY_KEY_ID and "
                        "RAZORPAY_KEY_SECRET, or pass an api/transport. Test "
                        "keys are prefixed rzp_test_ and are functionally "
                        "identical to live keys against separate data.")
                transport = Transport(key_id, key_secret)
            self._api = RazorpayApi(transport, API_BASE)

    @property
    def calls(self) -> int:
        """Provider calls made. Read off the api so there is one counter."""
        return self._api.calls

    # ------------------------------------------------------------- identity
    def _binding(self, ref: MandateRef) -> MandateBinding:
        b = self.bindings.get(ref.uid)
        if b is None:
            raise RazorpayError(
                f"no Razorpay token bound to mandate {ref.uid}. A mandate must "
                f"be authorised before it can be charged; see MandateBinding.")
        return b

    @staticmethod
    def receipt_for(action_id: str, ref: MandateRef, t: int) -> str:
        """The order receipt for one debit. Deterministic, and <= 40 chars.

        This is the real idempotency anchor: Razorpay rejects a second order
        carrying a receipt it has already seen, so the same logical debit
        cannot become two orders across a crash. Keyed on `action_id`, which
        Stage 0 derives from `(run_id, mandate, cycle, target_t, attempt_no)`
        -- not on wall clock and not on a fresh uuid, because a key that moves
        between runs is not a key.
        """
        raw = f"{action_id}|{ref.uid}|{t}"
        return "rcv_" + hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _epoch(self, t: int) -> int:
        """Simulated hour -> Unix second. The ONLY place the clocks meet.

        Returns 0 when no origin is configured, and every caller treats 0 as
        "refuse" rather than as a timestamp. See the module docstring.
        """
        return self.epoch_origin + int(t) * 3600 if self.epoch_origin else 0

    def predelivery_state(self, ref: MandateRef,
                          target_t: int) -> PredeliveryOrder | None:
        return self.journal.load(ref.uid, target_t)

    # -------------------------------------------------------- notification
    def notify(self, ref: MandateRef, amount: Rupees, notify_t: int,
               target_t: int) -> dict:
        """Create the pre-debit order. `POST /v1/orders` with `notification`.

        ORDER_CREATED IS NOT PROOF THE CUSTOMER WAS TOLD. It means Razorpay
        accepted the instruction to tell them. Only
        `order.notification.delivered` is evidence of delivery, and NPCI
        requires the notice 24 hours before the debit, so the distinction is
        regulatory rather than cosmetic.
        """
        b = self._binding(ref)
        if not (b.rzp_token_id or "").strip():
            return self._notify_result(
                executed=False, phase="ORDER_CREATE_FAILED", uid=ref.uid,
                detail="missing token_id on MandateBinding")

        amount_paise = effective_amount_paise(amount, b.charge_amount)
        if amount_paise < MIN_AMOUNT_PAISE:
            return self._notify_result(
                executed=False, phase="ORDER_CREATE_FAILED", uid=ref.uid,
                detail=f"invalid amount: {amount_paise} paise (Razorpay's "
                       f"documented minimum is {MIN_AMOUNT_PAISE}; set "
                       f"charge_amount on the binding when Stage 0 passes 0)")

        payment_after = self._epoch(target_t)
        if not payment_after:
            return self._notify_result(
                executed=False, phase="ORDER_CREATE_FAILED", uid=ref.uid,
                detail="no epoch_origin configured, so simulated hour "
                       f"{target_t} cannot be mapped to a Unix second for "
                       "notification.payment_after")
        if payment_after <= int(time.time()):
            return self._notify_result(
                executed=False, phase="ORDER_CREATE_FAILED", uid=ref.uid,
                detail=f"payment_after {payment_after} is not in the future")

        existing = self.journal.load(ref.uid, target_t)
        if existing is not None and existing.order_id:
            return self._notify_result(
                executed=True, phase=existing.phase.value, uid=ref.uid,
                detail="order already exists for this mandate and target",
                order_id=existing.order_id, http_status=existing.http_status)

        receipt = self.receipt_for(f"notify:{ref.uid}", ref, target_t)
        r = self._api.create_notification_order(
            amount_paise=amount_paise, receipt=receipt,
            token_id=b.rzp_token_id, payment_after=payment_after,
            currency=self.currency,
            notes={"mandate_uid": ref.uid, "target_t": str(target_t)})

        self.predelivery_log.append(envelope_record(
            phase=ORDER_CREATED, http_method="POST",
            url=self._api.url("orders"),
            request_body={"amount": amount_paise, "receipt": receipt},
            http_status=r.status, response_body=r.body,
            extra={"mandate_uid": ref.uid, "target_t": target_t,
                   "notify_t": notify_t}))

        order_id = parse_order_id(r.body) if r.ok else ""

        if not r.ok and "already exists" in (r.error_description or "").lower():
            # The crash path: we created this order on a previous run and lost
            # the record. Recover it rather than failing, and rather than
            # minting a second receipt to get around the rejection.
            found = self._api.find_order_by_receipt(receipt)
            if found.ok:
                order_id = parse_order_id(first_item(found.body))

        if not order_id:
            return self._notify_result(
                executed=False, phase="ORDER_CREATE_FAILED", uid=ref.uid,
                detail=(r.error_description or
                        ("no response from the payment provider"
                         if r.outcome is Outcome.LOST
                         else "response carried no order id")),
                http_status=r.status, error_code=r.error_code)

        rec = PredeliveryOrder(
            mandate_uid=ref.uid, target_t=target_t, order_id=order_id,
            amount_paise=amount_paise, payment_after=payment_after,
            phase=PredeliveryPhase.ORDER_CREATED, http_status=r.status)
        self.journal.save(rec)
        self.workflow_log.append({
            "kind": "NOTIFY", "mandate_uid": ref.uid, "notify_t": notify_t,
            "target_t": target_t, "amount": amount, "order_id": order_id,
            "phase": ORDER_CREATED})
        return self._notify_result(
            executed=True, phase=ORDER_CREATED, uid=ref.uid,
            detail="razorpay order created; delivery unconfirmed until webhook",
            order_id=order_id, http_status=r.status)

    def _notify_result(self, *, executed: bool, phase: str, detail: str,
                       order_id: str = "", http_status: int | None = None,
                       error_code: str = "", uid: str = "") -> dict:
        out = {"executed": executed, "phase": phase,
               "channel": "razorpay_order" if executed else "predelivery_error",
               "order_id": order_id, "http_status": http_status,
               "error_code": error_code, "detail": detail}
        if uid:
            self.last_notify[uid] = out
        return out

    def ingest_notification_webhook(self, payload: dict) -> dict:
        """Record `order.notification.delivered` / `.failed`. Returns a summary.

        Signature verification is NOT done here -- it needs the raw bytes, and
        by the time a dict exists those are gone. `live/webhooks.py` verifies
        before anything is parsed. This method exists for the batch path, which
        replays already-trusted payloads from a file.
        """
        wh = parse_notification_webhook(payload)
        if wh is None:
            return {"accepted": False, "reason": "not a notification webhook"}
        matched = None
        for rec in self.journal.all():
            if rec.order_id and rec.order_id == wh.order_id:
                apply_notification_webhook(rec, wh)
                self.journal.save(rec)
                matched = rec
                break
        self.predelivery_log.append(envelope_record(
            phase=(NOTIFICATION_DELIVERED if wh.event.endswith(".delivered")
                   else NOTIFICATION_FAILED),
            http_method="WEBHOOK", url="", request_body=None, http_status=200,
            response_body=payload,
            extra={"order_id": wh.order_id,
                   "notification_id": wh.notification_id,
                   "matched_mandate": matched.mandate_uid if matched else None}))
        if matched is None:
            return {"accepted": True, "matched": False,
                    "order_id": wh.order_id, "event": wh.event}
        return {"accepted": True, "matched": True,
                "mandate_uid": matched.mandate_uid,
                "target_t": matched.target_t, "phase": matched.phase.value,
                "event": wh.event}

    # ------------------------------------------------------ the money path
    def attempt(self, ref: MandateRef, amount: Rupees, t: int,
                action_id: str = "") -> AttemptOutcome:
        """Submit one debit. NEVER RAISES for a decline or a network fault.

        It DOES raise `RazorpayError` when the provider refused the REQUEST --
        a rejected credential, a token that is not confirmed, an order that
        cannot be paid. No payment was created in those cases, so there is no
        evidence about the customer's balance, and returning a decline would
        record one. That asymmetry is deliberate: raising stops the run with a
        message naming the status, which is loud and recoverable; declining
        corrupts every belief the customer has and reports a plausible-looking
        recovery rate, which is silent.

        THE RETURN ON SUCCESS IS `pending`, NOT SUCCESS. See the module
        docstring: an accepted submission is not a collected payment.
        """
        b = self._binding(ref)
        pred = self.journal.load(ref.uid, t)
        if pred is None or not pred.order_id:
            return AttemptOutcome(t=t, code=code_for_reason(None),
                                  success=False, pending=False,
                                  raw_code="missing_predelivery_order")
        if pred.phase == PredeliveryPhase.NOTIFICATION_FAILED:
            return AttemptOutcome(t=t, code=code_for_reason(None),
                                  success=False, pending=False,
                                  raw_code="notification_failed")
        if pred.phase not in (PredeliveryPhase.ORDER_CREATED,
                              PredeliveryPhase.NOTIFICATION_DELIVERED):
            return AttemptOutcome(
                t=t, code=code_for_reason(None), success=False, pending=False,
                raw_code=f"invalid_predelivery_phase:{pred.phase.value}")

        email = (b.rzp_email
                 or os.environ.get("RAZORPAY_DEFAULT_EMAIL", "")).strip()
        contact = (b.rzp_contact
                   or os.environ.get("RAZORPAY_DEFAULT_CONTACT", "")).strip()
        if not email or not contact:
            raise RazorpayError(
                f"mandate {ref.uid}: email and contact are required by "
                f"create/recurring; set them on MandateBinding or via "
                f"RAZORPAY_DEFAULT_EMAIL / RAZORPAY_DEFAULT_CONTACT")

        r = self._api.create_recurring_payment(
            email=email, contact=contact, amount_paise=pred.amount_paise,
            order_id=pred.order_id, customer_id=b.rzp_customer_id,
            token_id=b.rzp_token_id, currency=self.currency,
            description=f"mandate {ref.uid}",
            notes={"mandate_uid": ref.uid, "action_id": action_id})

        pred.phase = PredeliveryPhase.DEBIT_ATTEMPTED
        self.journal.save(pred)
        self.predelivery_log.append(envelope_record(
            phase=DEBIT_ATTEMPTED, http_method="POST",
            url=self._api.url("recurring"),
            request_body={"amount": pred.amount_paise,
                          "order_id": pred.order_id},
            http_status=r.status, response_body=r.body,
            extra={"mandate_uid": ref.uid, "target_t": t,
                   "order_id": pred.order_id}))
        if action_id:
            self.raw[action_id] = {"http_status": r.status, "body": r.body}

        if r.outcome is Outcome.LOST:
            # WE DO NOT KNOW whether the debit landed. Reconciliation resolves
            # it from the order; nothing here may guess.
            self.pending_outcomes += 1
            return AttemptOutcome(t=t, code=_UNKNOWN_CODE, success=False,
                                  pending=True, raw_code="transport_lost")

        if self._refuses_request(r):
            raise RazorpayError(
                f"Razorpay refused the REQUEST, not the payment: HTTP "
                f"{r.status}, code={r.error_code!r}, "
                f"description={r.error_description!r}. No payment was "
                f"created, so this is not evidence about the customer's "
                f"balance and must not be recorded as a decline. Reproduce "
                f"the envelope with scripts/razorpay_ladder.py.")

        payment_id = parse_payment_id(r.body)
        return self._outcome_from_response(r.body, t, payment_id)

    def _refuses_request(self, r) -> bool:
        """Did the provider refuse the REQUEST rather than report on a PAYMENT?

        One predicate, because it is the branch that decides whether a
        response becomes an exception or a statement about a customer's
        balance, and a decision that important should have one place to read
        and one place for a mutant to attack.
        """
        if r.outcome is Outcome.DENIED:
            return True
        if r.outcome is Outcome.REJECTED:
            return not self._is_payment_outcome(r.body)
        return False

    @staticmethod
    def _is_payment_outcome(payload: dict) -> bool:
        """Did a 4xx report on a PAYMENT, or refuse the REQUEST?

        The distinction is the difference between "the customer's account was
        empty" and "our API key is wrong", and getting it backwards was a
        defect on the money path once already -- `docs/errors.md`, "An
        authentication failure recorded as a statement about the customer's
        balance". A wrong key would otherwise teach the belief filter that
        every one of that customer's mandates faced an empty account, and burn
        all four legal NPCI attempts doing it.

        `[REPORTED]` from Razorpay's error documentation: a payment-level
        failure carries `reason`, `source`, `step` and `metadata.payment_id`;
        an API-level rejection carries `code` and `description` alone. The 401
        half is `[VERIFIED]` -- an unauthenticated POST to the real
        recurring-charge endpoint returns exactly `code` and `description`,
        transcript in `logs/razorpay_ladder.json`. The 4xx half is inference,
        and it is the branch to re-check the day a live key exists.
        """
        err = payload.get("error") or {}
        if not err:
            return False
        return bool(err.get("reason")
                    or (err.get("metadata") or {}).get("payment_id"))

    def _outcome_from_response(self, payload: dict, t: int,
                               payment_id: str) -> AttemptOutcome:
        """Normalise one accepted provider response into our vocabulary.

        Pure, so every branch is reachable from a recorded response in
        `agent/tests/test_razorpay_mapping.py` without a key.

        TWO SHAPES ARRIVE HERE. `create/recurring` answers with a payment id
        and nothing else. `GET /payments/:id` answers with a full entity
        carrying `status` and possibly `error_reason`. The first is always
        `pending`; only the second can resolve an outcome.
        """
        reason = str(payload.get("error_reason") or
                     (payload.get("error") or {}).get("reason") or "")
        state = str(payload.get("status") or "")

        if not state and payment_id:
            # The create/recurring shape. Accepted, outcome unknown.
            self.pending_outcomes += 1
            return AttemptOutcome(t=t, code=_UNKNOWN_CODE, success=False,
                                  pending=True, raw_code=SUBMITTED_RAW)
        if not reason and state in ("captured", "authorized"):
            return AttemptOutcome(t=t, code=OK, success=True, raw_code=state)
        if not reason and state in ("created", "pending"):
            self.pending_outcomes += 1
            return AttemptOutcome(t=t, code=_UNKNOWN_CODE, success=False,
                                  pending=True, raw_code=state)
        if not reason:
            return AttemptOutcome(t=t, code=code_for_reason(None),
                                  success=False,
                                  raw_code=state or "unknown")
        pending = is_pending(reason)
        if pending:
            self.pending_outcomes += 1
        return AttemptOutcome(t=t, code=code_for_reason(reason), success=False,
                              pending=pending, raw_code=reason)

    def resolve(self, payment_id: str, t: int) -> AttemptOutcome:
        """Ask the provider what happened to a submitted payment.

        The authoritative answer, for a payment whose webhook has not arrived
        or whose submission response was lost. Called by reconciliation, never
        in a loop -- Razorpay's own guidance is that webhooks are the primary
        channel and polling is the fallback.
        """
        r = self._api.fetch_payment(payment_id)
        if r.outcome is Outcome.LOST:
            self.pending_outcomes += 1
            return AttemptOutcome(t=t, code=_UNKNOWN_CODE, success=False,
                                  pending=True, raw_code="transport_lost")
        if r.outcome in (Outcome.DENIED, Outcome.REJECTED):
            raise RazorpayError(
                f"Razorpay refused the fetch for payment {payment_id}: HTTP "
                f"{r.status}, code={r.error_code!r}")
        return self._outcome_from_response(r.body, t, "")

    # -------------------------------------------------------- non-money path
    def _notify_email(self, fallback: str = "") -> str:
        return self.notify_email or fallback

    def _try_smtp(self, to_addr: str, subject: str, body: str) -> str:
        """Send if SMTP_HOST is set. Returns a short result, never raises."""
        result = deliver_smtp(to_addr, subject, body)
        self._last_smtp = result
        return result.status

    def remind(self, ref: MandateRef, amount: Rupees, t: int,
               message: str = "", action_id: str = "",
               email_subject: str = "") -> WorkflowResult:
        """Funding reminder. Must not create a Payment Link."""
        if self.n_live_nudges >= self.max_live_nudges:
            append_jsonl(self.outbox_path, {
                "kind": "REMIND", "mandate_uid": ref.uid, "capped": True,
                "message": message, "action_id": action_id})
            return WorkflowResult(
                executed=False, channel="local_quota",
                detail=f"live reminder cap {self.max_live_nudges} reached; "
                       "outbox written, email not sent")
        to_addr = self._notify_email()
        path = append_jsonl(self.outbox_path, {
            "kind": "REMIND", "mandate_uid": ref.uid, "t": t,
            "message": message, "action_id": action_id, "amount": amount,
            "to": to_addr})
        smtp = self._try_smtp(
            to_addr, email_subject or "Subscription payment reminder",
            message or "Please add funds so the next automatic debit can "
                       "succeed.")
        emailed = smtp == SMTP_SENT
        if emailed:
            self.n_live_nudges += 1
        self.workflow_log.append({"kind": "REMIND", "mandate_uid": ref.uid,
                                  "emailed": emailed, "smtp": smtp})
        return WorkflowResult(
            executed=emailed, channel="email" if emailed else "outbox",
            detail=f"{smtp} outbox={path}", status="sent" if emailed else "")

    def nudge(self, ref: MandateRef, amount: Rupees, t: int,
              message: str = "", action_id: str = "",
              email_subject: str = "") -> WorkflowResult:
        return self.remind(ref, amount, t, message=message,
                           action_id=action_id, email_subject=email_subject)

    def backup_checkout(self, ref: MandateRef, amount: Rupees, t: int,
                        message: str = "", action_id: str = "") -> WorkflowResult:
        """Create a Payment Link that replaces the fourth mandate debit.

        Email notify is on when RECOVERY_NOTIFY_EMAIL is set. SMS stays off.
        """
        existing = self._backup_ids.get(ref.uid)
        if existing:
            r = self._api.fetch_payment_link(existing)
            if r.ok and r.body.get("id"):
                return WorkflowResult(
                    executed=True, channel="razorpay_payment_link",
                    vendor_id=str(r.body["id"]),
                    status=self._map_link_status(
                        str(r.body.get("status") or "issued")),
                    short_url=str(r.body.get("short_url") or ""),
                    detail=f"http replay id={r.body['id']}")
        if self.n_live_backups >= self.max_live_nudges:
            return WorkflowResult(
                executed=False, channel="local_quota",
                detail=f"live backup-link cap {self.max_live_nudges} reached")
        to_addr = self._notify_email(f"backup.{ref.uid}@example.com")
        notify_on = bool(self.notify_email)
        body = {
            "amount": max(to_paise(amount), MIN_AMOUNT_PAISE),
            "currency": self.currency,
            "description": (message or "Pay this period's subscription. The "
                            "automatic debit is paused so you are not charged "
                            "twice.")[:2048],
            "reference_id": (action_id or f"{ref.uid}_{t}")[:40],
            "expire_by": int(time.time()) + 48 * 3600,
            "customer": {"name": f"Customer {ref.customer_id}",
                         "email": to_addr,
                         "contact": f"+9190{ref.customer_id % 100000000:08d}"},
            "notify": {"sms": False, "email": notify_on},
            "notes": {"kind": "BACKUP_CHECKOUT", "mandate_uid": ref.uid,
                      "action_id": action_id},
        }
        r = self._api.create_payment_link(body)
        vid = str(r.body.get("id") or "")
        url = str(r.body.get("short_url") or "")
        st = self._map_link_status(str(r.body.get("status") or "issued"))
        ok = r.ok and bool(vid)
        recovered = False
        if not ok and "already exists" in (r.error_description or "").lower():
            found = self._fetch_link_by_reference(body["reference_id"])
            if found:
                vid, url, st = found
                ok, recovered = True, True
        if ok:
            prev = self._backup_ids.get(ref.uid)
            if prev != vid:
                self._backup_ids[ref.uid] = vid
                if prev is None:
                    self.n_live_backups += 1
        detail = (f"http {'recovered' if recovered else r.status} id={vid} "
                  f"notify_email={notify_on} short_url={bool(url)}" if ok else
                  f"http {r.status} {r.error_code} {r.error_description}")
        self.workflow_log.append({"kind": "BACKUP_LINK", "mandate_uid": ref.uid,
                                  "vendor_id": vid, "ok": ok, "status": st,
                                  "recovered": recovered})
        return WorkflowResult(executed=ok, channel="razorpay_payment_link",
                              vendor_id=vid, detail=detail, status=st,
                              short_url=url)

    @staticmethod
    def _map_link_status(raw: str) -> str:
        return {"created": "issued", "issued": "issued", "paid": "paid",
                "cancelled": "cancelled", "expired": "expired"}.get(raw, raw)

    def _fetch_link_by_reference(self, reference_id: str):
        """Existing link for this action_id. Razorpay rejects a second create."""
        if not reference_id:
            return None
        r = self._api.find_payment_link_by_reference(reference_id)
        if not r.ok:
            return None
        p = first_item(r.body)
        vid = str(p.get("id") or "")
        if not vid:
            return None
        return (vid, str(p.get("short_url") or ""),
                self._map_link_status(str(p.get("status") or "issued")))

    def fetch_backup(self, ref: MandateRef, t: int) -> WorkflowResult:
        vid = self._backup_ids.get(ref.uid, "")
        if not vid:
            return WorkflowResult(executed=False,
                                  channel="razorpay_payment_link",
                                  detail="no backup link id")
        r = self._api.fetch_payment_link(vid)
        raw = str(r.body.get("status") or "")
        mapped = self._map_link_status(raw)
        return WorkflowResult(executed=r.ok, credited=mapped == "paid",
                              channel="razorpay_payment_link", vendor_id=vid,
                              status=mapped,
                              short_url=str(r.body.get("short_url") or ""),
                              detail=f"http {r.status} status={raw}")

    def cancel_backup(self, ref: MandateRef, t: int) -> WorkflowResult:
        vid = self._backup_ids.get(ref.uid, "")
        if not vid:
            return WorkflowResult(executed=False, detail="no backup link id")
        r = self._api.cancel_payment_link(vid)
        raw = str(r.body.get("status") or "")
        return WorkflowResult(executed=r.ok, channel="razorpay_payment_link",
                              vendor_id=vid,
                              status="cancelled" if r.ok else raw,
                              detail=f"http {r.status} status={raw}")

    def escalate(self, ref: MandateRef, amount: Rupees, t: int,
                 brief: str = "", action_id: str = "") -> WorkflowResult:
        """Append a merchant-queue row. That file is the queue."""
        self.n_escalations += 1
        path = append_jsonl(self.queue_path, {
            "kind": "ESCALATE", "mandate_uid": ref.uid, "t": t,
            "brief": brief, "action_id": action_id, "amount": amount})
        self.workflow_log.append({"kind": "ESCALATE", "mandate_uid": ref.uid,
                                  "queue": path})
        return WorkflowResult(executed=True, channel="merchant_queue",
                              vendor_id=f"ticket_{ref.uid}_{t}", detail=path)

    # --------------------------------------------------------- introspection
    def estimates(self, customer_id: int) -> tuple[float, int]:
        """`(est_salary, est_payday)` for the belief filter's cold start.

        Uses the explicit `sim_customer_id` on a binding. Ordinary Razorpay ids
        such as `cust_ABC123` do not encode a simulation index and must not be
        parsed as one.
        """
        for b in self.bindings.values():
            if b.sim_customer_id is not None and b.sim_customer_id == customer_id:
                return b.est_salary, b.est_payday
        return 0.0, 0
