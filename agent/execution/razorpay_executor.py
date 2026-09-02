"""`RazorpayExecutor` -- the same port, a different world.

THE POINT OF THIS FILE IS THAT NOTHING ELSE CHANGES. `agent/ports.py` declares
the executor port. `attempt()` is the money path. `remind()` writes a funding
notice (never a Payment Link). `backup_checkout()` is the last-attempt Payment
Link. `escalate()` appends a merchant-queue file. `SimExecutor` implements the
same methods against the simulation. `agent/loop.py`, `agent/policy/`,
`agent/constraints/` and `agent/audit/` are byte-identical either way, because
gate **I2** already forbids anything but `constraints/stage0.py` and the
composition root from holding an executor at all. The switch is one argument in
`agent/batch.py`.

That is worth being precise about rather than gestural: Stage 0 adjudicates
before `_executor.attempt` is ever called, so a peak-hour debit is refused with
**zero network traffic** against either backend.
`scripts/prove_stage0_refuses.py` demonstrates exactly that, end to end, and
needs no API key.

---------------------------------------------------------------------------
WHAT IS TESTED AND WHAT IS NOT. Read this before believing anything below.
---------------------------------------------------------------------------

**Gated offline, no key needed** (`agent/tests/test_razorpay_mapping.py`):
  * the reason -> family map over all 110 published reasons
  * `_outcome_from_payment` on recorded response shapes, including every
    terminal, limit, lien and indeterminate case
  * idempotency-key derivation is deterministic and collision-free per action
  * a transport failure never raises and never fabricates a decline
  * Stage 0 refuses a peak-hour action against this executor without calling it

**Verified against the LIVE API** (`scripts/razorpay_ladder.py`, transcript in
`logs/razorpay_ladder.json`):
  * DNS, TLS 1.3, and the charge URL existing and answering
  * the shape of a real API-level error envelope -- `code` and `description`
    alone, no `reason`, no `source`, no `step`, no `metadata`. Error 28.
  * authentication with a `rzp_test_` key: HTTP 200 on `GET /v1/payments`,
    which exercises the transport's success path

**NOT TESTED.** Every line marked `# UNVERIFIED` below. Razorpay has never read
one of our recurring-charge request bodies, because that endpoint charges a
stored token and no authorised mandate exists. Specifically unverified:
  * the exact request body Razorpay wants for a recurring UPI charge
  * whether test mode returns populated `error_reason` values on
    `failure@razorpay`, or a single generic one
  * whether the decoupled order-with-notification body is accepted in test mode
    (rung 5a; ORDER_CREATED is not delivery proof)
  * whether `payment.downtime` is populated in test mode at all

The shapes come from Razorpay's public documentation, read 29 August 2026, and
are recorded in `docs/results.md`. A doc-derived request body that has never
received a 200 is a hypothesis.

---------------------------------------------------------------------------
THREE DESIGN DECISIONS THAT ARE NOT OBVIOUS
---------------------------------------------------------------------------

1. **A DETERMINISTIC IDEMPOTENCY KEY PER MONEY ACTION.** Derived from the
   `action_id` Stage 0 already computes, which is itself a hash of
   `(run_id, mandate, cycle, target_t, attempt_no)`. So a retried HTTP request
   -- a socket timeout, a proxy hiccup, a process restart -- cannot become a
   second debit. This is not defensive habit: `deemed_transaction` and
   `duplicate_rrn_found` exist in Razorpay's error list precisely because lost
   responses happen, and `docs/errors.md`, "Unknown outcomes documented as
   never-retried, on a path that could retry them", is this project already
   proposing a double debit once.

2. **A TRANSPORT FAILURE IS `pending`, NEVER A DECLINE.** If the connection
   drops we do not know whether the debit landed. Returning `Z9` would tell the
   belief filter the account was empty -- `w3.py:432` hard-zeroes every balance
   bin at or above the amount on a failure -- which is a lie about the customer
   derived from a fact about our network. Returning `TECH` would be a smaller
   lie in the same direction. So it returns `pending=True`, and the diagnosis
   layer refuses to retry on it.

3. **NO NEW DEPENDENCY.** `urllib.request` from the standard library, not
   `razorpay` or `requests`. `requirements.txt` deliberately pins numpy alone
   for the gated suite, and adding a package so that a module nobody can run
   without a key looks tidier is a bad trade. The transport is a dozen lines
   and is injectable, which is what makes the offline gates possible.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

# The reason -> family map lives in ports.py, not here. Gate I2 forbids a
# sibling import inside agent/execution, and rule I1 forbids agent/llm from
# reaching agent.execution at all -- so a table the narrative layer may need
# could never have lived in this package. See ports.py.
from agent.audit.jsonl_queue import append_jsonl
from agent.execution.smtp_delivery import SMTP_SENT, deliver_smtp
from agent.execution.razorpay_predelivery import (DEBIT_ATTEMPTED, ORDER_CREATED,
                                                  NOTIFICATION_DELIVERED,
                                                  NOTIFICATION_FAILED,
                                                  PredeliveryOrder,
                                                  PredeliveryPhase,
                                                  apply_notification_webhook,
                                                  build_order_body,
                                                  effective_amount_paise,
                                                  envelope_record,
                                                  parse_notification_webhook,
                                                  parse_order_id)
from agent.ports import (OK, AttemptOutcome, MandateRef, Rupees, WorkflowResult,
                         bank_of, code_for_reason, is_pending, to_paise)

API_BASE = "https://api.razorpay.com/v1"

#: Razorpay's OWN documented retry schedule for a failed subscription charge:
#: attempt on T, then T+1, T+2, T+3, after which the subscription moves to
#: `halted`. [VERIFIED] from their Payment Retries page, 29 August 2026.
#:
#: Recorded here rather than in a comment because it does two jobs. It is
#: independent corroboration of the NPCI attempt cap -- 1 presentation plus 3
#: retries -- which `docs/results.md` had only from a secondary source. And it
#: means `harness.baseline_doc`, the naive comparator this project has been
#: measuring against all along, is a fair rendering of what the vendor actually
#: does rather than a strawman we drew.
VENDOR_RETRY_OFFSETS_DAYS = (0, 1, 2, 3)
VENDOR_TERMINAL_STATE = "halted"

#: HTTP statuses that mean the credential was refused, so the request never
#: reached payment processing. 401 is `[VERIFIED]` against the live API --
#: `scripts/razorpay_ladder.py`, 30 August 2026. 403 is `[GUESS]`, grouped with
#: it on the same reasoning and never observed. See
#: `RazorpayExecutor._is_configuration_fault`.
CONFIG_FAULT_STATUSES = (401, 403)


class RazorpayError(RuntimeError):
    """Raised only for CONFIGURATION faults -- a missing key, a mandate with no
    token. Never for a declined payment, and never for a transport failure:
    both of those are outcomes, and an outcome is a return value."""


@dataclass(frozen=True)
class MandateBinding:
    """What Razorpay needs to charge one of our mandates.

    Our `MandateRef` is `(customer_id, mandate_index, merchant_id)`, which is
    the simulation's identity. Razorpay's is a `customer_id` and a `token_id`
    returned when the AutoPay mandate was authorised. Nothing can derive one
    from the other, so the binding is data supplied by the caller and this
    class is where the gap is visible instead of implied.
    """
    rzp_customer_id: str
    rzp_token_id: str
    #: Contact and email are required by POST /v1/payments/create/recurring.
    #: Stage 0 passes amount=0.0 to notify(); charge_amount supplies the order.
    rzp_email: str = ""
    rzp_contact: str = ""
    charge_amount: float = 0.0
    #: Bootstrap estimates. IN PRODUCTION THESE ARE THE OPEN PROBLEM: the
    #: belief filter needs a starting salary and payday guess per customer, and
    #: a real integration has no oracle for either. The honest options are a
    #: population prior, or a wide prior that the first cycle's outcomes
    #: sharpen. Neither is measured here. `docs/architecture.md.
    est_salary: float = 0.0
    est_payday: int = 0
    #: Explicit index into the simulated population, if this binding was
    #: created from one. Ordinary Razorpay customer ids do not encode this.
    sim_customer_id: int | None = None


class _UrllibTransport:
    """Twelve lines of HTTP so that the executor has no dependency.

    Injectable, which is the whole reason it is a class: every offline gate
    passes a fake in its place and the executor under test is the real one.
    """

    def __init__(self, key_id: str, key_secret: str, timeout: float = 20.0):
        tok = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
        self._auth = f"Basic {tok}"
        self.timeout = timeout

    def post(self, url: str, body: dict, idempotency_key: str) -> tuple[int, dict]:
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", self._auth)
        req.add_header("Content-Type", "application/json")
        # UNVERIFIED: Razorpay documents idempotency on some endpoints; whether
        # this header is honoured on a recurring charge has not been confirmed
        # against a live response.
        req.add_header("X-Razorpay-Idempotency-Key", idempotency_key)
        return self._send(req)

    def get(self, url: str) -> tuple[int, dict]:
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", self._auth)
        return self._send(req)

    def _send(self, req) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:            # a 4xx/5xx WITH a body
            try:
                return e.code, json.loads(e.read().decode() or "{}")
            except Exception:
                return e.code, {}


class RazorpayExecutor:
    """Implements `agent.ports.Executor`. Only `Stage0Gate` may hold one."""

    def __init__(self, bindings: dict[str, MandateBinding],
                 key_id: str | None = None, key_secret: str | None = None,
                 transport=None, currency: str = "INR",
                 max_transport_retries: int = 2,
                 max_live_nudges: int | None = None,
                 max_live_escalations: int | None = None):
        self.bindings = bindings
        self.currency = currency
        self.max_transport_retries = max_transport_retries
        self.max_live_nudges = max_live_nudges
        if self.max_live_nudges is None:
            self.max_live_nudges = int(os.environ.get("RAZORPAY_MAX_LIVE_NUDGES", "5"))
        self.max_live_escalations = max_live_escalations
        if self.max_live_escalations is None:
            self.max_live_escalations = int(
                os.environ.get("RAZORPAY_MAX_LIVE_ESCALATIONS", "5"))
        self.n_live_nudges = 0
        self.n_live_escalations = 0
        self.n_live_backups = 0
        self.n_nudges_took = 0
        self.n_escalations = 0
        self.n_backup_links = 0
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
        self.calls = 0
        self.pending_outcomes = 0
        #: `{customer_id: handle}`, the same shape `SimExecutor` exposes, so
        #: `agent/loop.py` can put a bank on the `CaseView` either way.
        #: Derived from `ports.bank_of`, which is a stable hash of the customer
        #: index and consumes no randomness.
        #:
        #: ⚠️ IN PRODUCTION THIS IS WRONG AND MUST BE REPLACED. The real
        #: remitter handle is on the customer's VPA (`user@oksbi`) and is
        #: something the merchant can already read off their own transaction
        #: report -- which is the argument `agent/llm/caseview.py` makes for
        #: letting it cross the redaction boundary at all. Hashing an index is
        #: the simulation's stand-in and it is kept here only so the two
        #: backends have the same shape. A real integration reads the handle
        #: from the payment object.
        self.banks = {int(uid.split("m")[0][1:]): bank_of(int(uid.split("m")[0][1:]))
                      for uid in bindings}
        #: Every raw response, keyed by our action_id. The audit trail records
        #: our normalisation; this keeps the vendor's own words so the two can
        #: be reconciled against their dashboard. Bounded by the run.
        self.raw: dict[str, dict] = {}
        #: Decoupled-flow state: (mandate_uid, target_t) -> PredeliveryOrder.
        self._predelivery: dict[tuple[str, int], PredeliveryOrder] = {}
        #: Append-only proof transcript rows (sanitized).
        self.predelivery_log: list[dict] = []
        if transport is not None:
            self._t = transport
        else:
            key_id = key_id or os.environ.get("RAZORPAY_KEY_ID", "")
            key_secret = key_secret or os.environ.get("RAZORPAY_KEY_SECRET", "")
            if not key_id or not key_secret:
                raise RazorpayError(
                    "No Razorpay credentials. Set RAZORPAY_KEY_ID and "
                    "RAZORPAY_KEY_SECRET, or pass a transport. Test keys are "
                    "prefixed rzp_test_ and are functionally identical to live "
                    "keys against separate data.")
            self._t = _UrllibTransport(key_id, key_secret)

    # ------------------------------------------------------------- identity
    def _binding(self, ref: MandateRef) -> MandateBinding:
        b = self.bindings.get(ref.uid)
        if b is None:
            raise RazorpayError(
                f"no Razorpay token bound to mandate {ref.uid}. A mandate must "
                f"be authorised before it can be charged; see MandateBinding.")
        return b

    @staticmethod
    def idempotency_key(action_id: str, ref: MandateRef, t: int) -> str:
        """Stable across process restarts and across re-runs of the same run.

        Keyed on the action, NOT on wall-clock or a uuid: the whole value of an
        idempotency key is that the SAME logical debit produces the SAME key
        after a crash. `action_id` is already a hash of
        `(run_id, mandate, cycle, target_t, attempt_no)`, so the extra fields
        here only make a collision between two different actions harder to
        construct, never make the key non-deterministic.
        """
        raw = f"{action_id}|{ref.uid}|{t}"
        return "rcv_" + hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _predelivery_key(self, ref: MandateRef, target_t: int) -> tuple[str, int]:
        return (ref.uid, target_t)

    def predelivery_state(self, ref: MandateRef,
                          target_t: int) -> PredeliveryOrder | None:
        return self._predelivery.get(self._predelivery_key(ref, target_t))

    def ingest_notification_webhook(self, payload: dict) -> dict:
        """Record order.notification.delivered / .failed. Returns summary."""
        wh = parse_notification_webhook(payload)
        if wh is None:
            return {"accepted": False, "reason": "not a notification webhook"}
        matched = None
        for key, rec in self._predelivery.items():
            if rec.order_id and rec.order_id == wh.order_id:
                apply_notification_webhook(rec, wh)
                matched = rec
                break
        row = envelope_record(
            phase=(NOTIFICATION_DELIVERED if wh.event.endswith(".delivered")
                   else NOTIFICATION_FAILED),
            http_method="WEBHOOK", url="",
            request_body=None, http_status=200,
            response_body=payload,
            extra={"order_id": wh.order_id,
                   "notification_id": wh.notification_id,
                   "matched_mandate": matched.mandate_uid if matched else None})
        self.predelivery_log.append(row)
        if matched is None:
            return {"accepted": True, "matched": False,
                    "order_id": wh.order_id, "event": wh.event}
        return {"accepted": True, "matched": True,
                "mandate_uid": matched.mandate_uid,
                "target_t": matched.target_t,
                "phase": matched.phase.value,
                "event": wh.event}

    def _notify_result(self, *, executed: bool, phase: str, detail: str,
                       order_id: str = "", http_status: int | None = None,
                       error_code: str = "") -> dict:
        return {"executed": executed, "phase": phase,
                "channel": "razorpay_order" if executed else "predelivery_error",
                "order_id": order_id, "http_status": http_status,
                "error_code": error_code, "detail": detail}

    # ------------------------------------------------------ the money path
    def attempt(self, ref: MandateRef, amount: Rupees, t: int,
                action_id: str = "") -> AttemptOutcome:
        """Charge one mandate. NEVER RAISES for a decline or a network fault.

        It DOES raise `RazorpayError` for a configuration fault -- a refused
        credential -- which is that exception's declared job. See
        `_is_configuration_fault` for why that is not a decline, and error 28
        in `docs/errors.md` for what this code did before 30 August 2026.

        `action_id` is optional so the signature still satisfies the `Executor`
        protocol. Stage 0 passes it, so the idempotency key is tied to the
        audited action; a caller that omits it falls back to the mandate and
        hour, which is still deterministic but is a weaker guarantee across
        runs. Said out loud because a silently weaker guarantee is worse than a
        documented one.
        """
        b = self._binding(ref)
        pkey = self._predelivery_key(ref, t)
        pred = self._predelivery.get(pkey)
        if pred is None or not pred.order_id:
            return AttemptOutcome(
                t=t, code=code_for_reason(None), success=False,
                pending=False, raw_code="missing_predelivery_order")
        if pred.phase == PredeliveryPhase.NOTIFICATION_FAILED:
            return AttemptOutcome(
                t=t, code=code_for_reason(None), success=False,
                pending=False, raw_code="notification_failed")
        if pred.phase not in (PredeliveryPhase.ORDER_CREATED,
                              PredeliveryPhase.NOTIFICATION_DELIVERED):
            return AttemptOutcome(
                t=t, code=code_for_reason(None), success=False,
                pending=False, raw_code=f"invalid_predelivery_phase:{pred.phase.value}")

        email = (b.rzp_email or os.environ.get("RAZORPAY_DEFAULT_EMAIL", "")
                 ).strip()
        contact = (b.rzp_contact or os.environ.get("RAZORPAY_DEFAULT_CONTACT", "")
                   ).strip()
        if not email or not contact:
            raise RazorpayError(
                f"mandate {ref.uid}: email and contact required for "
                f"create/recurring; set on MandateBinding or "
                f"RAZORPAY_DEFAULT_EMAIL / RAZORPAY_DEFAULT_CONTACT")

        key = self.idempotency_key(action_id or f"{ref.uid}@{t}", ref, t)

        body = {
            "email": email,
            "contact": contact,
            "amount": pred.amount_paise,
            "currency": self.currency,
            "order_id": pred.order_id,
            "customer_id": b.rzp_customer_id,
            "token": b.rzp_token_id,
            "recurring": True,
            "description": f"mandate {ref.uid}",
            "notes": {"mandate_uid": ref.uid, "action_id": action_id},
        }

        status, payload = self._post_with_retries(
            f"{API_BASE}/payments/create/recurring", body, key)
        pred.phase = PredeliveryPhase.DEBIT_ATTEMPTED
        self.predelivery_log.append(envelope_record(
            phase=DEBIT_ATTEMPTED,
            http_method="POST",
            url=f"{API_BASE}/payments/create/recurring",
            request_body=body, http_status=status,
            response_body=payload,
            extra={"mandate_uid": ref.uid, "target_t": t,
                   "order_id": pred.order_id}))
        if action_id:
            self.raw[action_id] = {"http_status": status, "body": payload}

        if status is None:
            # Transport gave up. WE DO NOT KNOW whether the debit landed.
            self.pending_outcomes += 1
            return AttemptOutcome(t=t, code=code_for_reason("deemed_transaction"),
                                  success=False, pending=True,
                                  raw_code="transport_failure")
        if self._is_configuration_fault(status, payload):
            err = (payload.get("error") or {})
            raise RazorpayError(
                f"Razorpay refused the REQUEST, not the payment: HTTP {status}, "
                f"code={err.get('code')!r}, description={err.get('description')!r}. "
                f"No payment was created, so this is not evidence about the "
                f"customer's balance and must not be recorded as a decline. "
                f"Check RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET and the request "
                f"shape. Reproduce the envelope with scripts/razorpay_ladder.py.")
        return self._outcome_from_payment(payload, t)

    def _post_with_retries(self, url: str, body: dict, key: str):
        """Retry the TRANSPORT, never the DEBIT.

        Safe only because `key` is constant across these attempts: the same
        idempotency key means Razorpay may return the first request's result
        rather than charging again. Retrying a POST without that guarantee is
        how one debit becomes three.
        """
        last = None
        for i in range(self.max_transport_retries + 1):
            try:
                self.calls += 1
                return self._t.post(url, body, key)
            except Exception as e:              # socket, DNS, TLS, timeout
                last = e
                if i < self.max_transport_retries:
                    time.sleep(0.4 * (2 ** i))
        return None, {"transport_error": repr(last)}

    @staticmethod
    def _is_configuration_fault(status, payload: dict) -> bool:
        """Did Razorpay reject the REQUEST, or report on a PAYMENT?

        THIS DISTINCTION WAS MISSING UNTIL 30 AUGUST 2026 AND IT WAS A DEFECT
        ON THE MONEY PATH. `attempt` handed every response with a status to
        `_outcome_from_payment`, which sees no `reason` and no payment `status`
        and returns the AMBIGUOUS code `U30` with `success=False`. The loop
        then feeds that to `BeliefBook.record_outcome`, and `w3.py:432`
        hard-zeroes every balance bin at or above the amount. So a wrong or
        expired API key would have taught the belief filter that the customer's
        account was empty -- for every mandate, on every attempt, silently, and
        because one belief is shared by all `k` mandates of a customer, one bad
        response corrupts all `k`. It would also have burned all four legal
        NPCI attempts and let the mandate die. `docs/errors.md`, "An
        authentication failure recorded as a statement about the customer's
        balance".

        Two signals, and the narrow one is the verified one.

        **401 and 403 -- the credential was refused, so no payment was ever
        created.** `[VERIFIED]` 30 August 2026: an unauthenticated POST to the
        real recurring-charge endpoint returns exactly

            401 {"error": {"code": "BAD_REQUEST_ERROR",
                           "description": "Authentication failed"}}

        with no `reason`, no `source`, no `step` and no `metadata`.
        `scripts/razorpay_ladder.py` sends that request and
        `logs/razorpay_ladder.json` is the transcript. 403 is grouped with it
        on the same reasoning and has NOT been observed -- `[GUESS]`.

        **The wider signal, for other 4xx.** `[REPORTED]`, from Razorpay's
        error documentation read 29 August 2026: a payment-level failure
        carries `reason`, `source`, `step` and `metadata.payment_id`; an
        API-level rejection carries `code` and `description` alone. So a 4xx
        whose error object has neither a `reason` nor a `metadata.payment_id`,
        and no payment `status`, is treated as a request that never became a
        payment. This branch is inference, not observation, and it is the one
        to re-check the day a real key exists.

        **Why raising is the right failure here.** The two ways to be wrong are
        not symmetric. Raising when we should not stops the run with a message
        naming the status and the envelope -- loud, immediate, recoverable.
        Declining when we should not corrupts every belief in the book and
        reports a plausible-looking recovery rate -- silent, and this project
        already has an error catalogue full of that shape. A design that
        returned a new `CONFIG_FAULT` outcome for the loop to count and skip
        was considered and not taken: it puts new vocabulary in `ports.py` for
        a case that should stop the run anyway.
        """
        if status in CONFIG_FAULT_STATUSES:
            return True
        if status is None or status < 400:
            return False
        err = payload.get("error") or {}
        if not err:
            return False
        if err.get("reason") or (err.get("metadata") or {}).get("payment_id"):
            return False                     # a real payment outcome
        return bool(err.get("code")) and not payload.get("status")

    def _outcome_from_payment(self, payload: dict, t: int) -> AttemptOutcome:
        """Normalise one Razorpay payment/error object into our vocabulary.

        Pure, so every branch is reachable from a recorded response in
        `agent/tests/test_razorpay_mapping.py` without a key.
        """
        err = payload.get("error") or {}
        # A payment object carries its own error_* fields; an API-level failure
        # nests them under "error". Both shapes appear in their docs.
        reason = (err.get("reason") or payload.get("error_reason")
                  or payload.get("error_code") and None)
        state = payload.get("status")

        if not reason and state in ("captured", "authorized"):
            return AttemptOutcome(t=t, code=OK, success=True,
                                  raw_code=str(state))
        if not reason and state in ("created", "pending"):
            # No error, no terminal state: an unresolved payment is an unknown.
            self.pending_outcomes += 1
            return AttemptOutcome(t=t, code=code_for_reason("payment_pending"),
                                  success=False, pending=True,
                                  raw_code=str(state))
        if not reason:
            return AttemptOutcome(t=t, code=code_for_reason(None), success=False,
                                  raw_code=str(state or "unknown"))

        pending = is_pending(reason)
        if pending:
            self.pending_outcomes += 1
        return AttemptOutcome(t=t, code=code_for_reason(reason), success=False,
                              pending=pending, raw_code=reason)

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
            to_addr,
            email_subject or "Subscription payment reminder",
            message or "Please add funds so the next automatic debit can succeed.")
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
        return self.remind(ref, amount, t, message=message, action_id=action_id,
                           email_subject=email_subject)

    def backup_checkout(self, ref: MandateRef, amount: Rupees, t: int,
                        message: str = "", action_id: str = "") -> WorkflowResult:
        """Create a Payment Link that replaces the fourth mandate debit.

        Email notify is on when RECOVERY_NOTIFY_EMAIL is set. SMS stays off.
        """
        existing = self._backup_ids.get(ref.uid)
        if existing:
            http_st, payload = self._t.get(f"{API_BASE}/payment_links/{existing}")
            if http_st is not None and http_st < 400 and payload.get("id"):
                vid = str(payload.get("id"))
                url = str(payload.get("short_url") or "")
                st = self._map_link_status(str(payload.get("status") or "issued"))
                return WorkflowResult(
                    executed=True, channel="razorpay_payment_link",
                    vendor_id=vid, status=st, short_url=url,
                    detail=f"http replay id={vid}")
        if self.n_live_backups >= self.max_live_nudges:
            return WorkflowResult(
                executed=False, channel="local_quota",
                detail=f"live backup-link cap {self.max_live_nudges} reached")
        to_addr = self._notify_email(f"backup.{ref.uid}@example.com")
        notify_on = bool(self.notify_email)
        expire_by = int(time.time()) + 48 * 3600
        body = {
            "amount": max(to_paise(amount), 100),
            "currency": self.currency,
            "description": (message or "Pay this period's subscription. "
                            "The automatic debit is paused so you are not "
                            "charged twice.")[:2048],
            "reference_id": (action_id or f"{ref.uid}_{t}")[:40],
            "expire_by": expire_by,
            "customer": {
                "name": f"Customer {ref.customer_id}",
                "email": to_addr,
                "contact": f"+9190{ref.customer_id % 100000000:08d}",
            },
            "notify": {"sms": False, "email": notify_on},
            "notes": {"kind": "BACKUP_CHECKOUT", "mandate_uid": ref.uid,
                      "action_id": action_id},
        }
        key = self.idempotency_key(action_id or f"backup:{ref.uid}@{t}", ref, t)
        status, payload = self._post_with_retries(
            f"{API_BASE}/payment_links", body, key)
        vid = str(payload.get("id") or "")
        url = str(payload.get("short_url") or "")
        st = self._map_link_status(str(payload.get("status") or "issued"))
        ok = status is not None and status < 400 and bool(vid)
        recovered = False
        err = (payload.get("error") or {})
        if not ok:
            desc = str(err.get("description") or "")
            if "already exists" in desc.lower():
                found = self._fetch_link_by_reference(body["reference_id"])
                if found:
                    vid, url, st = found
                    ok = True
                    recovered = True
        if ok:
            prev = self._backup_ids.get(ref.uid)
            if prev != vid:
                self._backup_ids[ref.uid] = vid
                if prev is None:
                    self.n_live_backups += 1
        detail = (f"http {'recovered' if recovered else status} id={vid} "
                  f"notify_email={notify_on} short_url={bool(url)}"
                  if ok else
                  f"http {status} {err.get('code')} {err.get('description')}")
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
        q = urllib.parse.quote(reference_id, safe="")
        http_st, payload = self._t.get(
            f"{API_BASE}/payment_links/?reference_id={q}")
        if http_st is None or http_st >= 400:
            return None
        items = payload.get("items") or []
        if not items:
            return None
        p = items[0]
        vid = str(p.get("id") or "")
        if not vid:
            return None
        return (vid, str(p.get("short_url") or ""),
                self._map_link_status(str(p.get("status") or "issued")))

    def fetch_backup(self, ref: MandateRef, t: int) -> WorkflowResult:
        vid = self._backup_ids.get(ref.uid, "")
        if not vid:
            return WorkflowResult(executed=False, channel="razorpay_payment_link",
                                  detail="no backup link id")
        st, payload = self._t.get(f"{API_BASE}/payment_links/{vid}")
        raw = str(payload.get("status") or "")
        mapped = self._map_link_status(raw)
        ok = st is not None and st < 400
        credited = mapped == "paid"
        return WorkflowResult(executed=ok, credited=credited,
                              channel="razorpay_payment_link", vendor_id=vid,
                              status=mapped,
                              short_url=str(payload.get("short_url") or ""),
                              detail=f"http {st} status={raw}")

    def cancel_backup(self, ref: MandateRef, t: int) -> WorkflowResult:
        vid = self._backup_ids.get(ref.uid, "")
        if not vid:
            return WorkflowResult(executed=False, detail="no backup link id")
        key = self.idempotency_key(f"cancel:{vid}", ref, t)
        st, payload = self._post_with_retries(
            f"{API_BASE}/payment_links/{vid}/cancel", {}, key)
        raw = str(payload.get("status") or "")
        mapped = "cancelled" if raw in ("cancelled", "") and st and st < 400 \
            else raw
        ok = st is not None and st < 400
        if ok:
            mapped = "cancelled"
        return WorkflowResult(executed=ok, channel="razorpay_payment_link",
                              vendor_id=vid, status=mapped,
                              detail=f"http {st} status={raw}")

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

    def notify(self, ref: MandateRef, amount: Rupees, notify_t: int,
               target_t: int) -> dict:
        """Schedule pre-debit via POST /v1/orders (decoupled flow).

        Creates a Razorpay order with the documented ``notification`` object.
        ORDER_CREATED here is NOT proof of customer delivery — only
        ``order.notification.delivered`` webhooks advance to NOTIFICATION_DELIVERED.
        """
        b = self._binding(ref)
        if not (b.rzp_token_id or "").strip():
            return self._notify_result(
                executed=False, phase="ORDER_CREATE_FAILED",
                detail="missing token_id on MandateBinding")

        amount_paise = effective_amount_paise(amount, b.charge_amount)
        if amount_paise < 100:
            return self._notify_result(
                executed=False, phase="ORDER_CREATE_FAILED",
                detail=f"invalid amount: {amount_paise} paise "
                       "(need charge_amount on binding when Stage 0 passes 0)")

        import time as _time
        payment_after = int(target_t)
        if payment_after <= int(_time.time()):
            return self._notify_result(
                executed=False, phase="ORDER_CREATE_FAILED",
                detail=f"payment_after {payment_after} is not in the future "
                       "(target_t must be Unix epoch seconds for live Razorpay)")

        receipt = f"rcv_{ref.uid}_{target_t}"[:40]
        body = build_order_body(
            amount_paise=amount_paise,
            currency=self.currency,
            receipt=receipt,
            token_id=b.rzp_token_id,
            payment_after=payment_after,
        )
        key = self.idempotency_key(f"notify:{ref.uid}@{target_t}", ref, target_t)
        url = f"{API_BASE}/orders"
        status, payload = self._post_with_retries(url, body, key)

        self.predelivery_log.append(envelope_record(
            phase=ORDER_CREATED,
            http_method="POST", url=url,
            request_body=body, http_status=status,
            response_body=payload,
            extra={"mandate_uid": ref.uid, "target_t": target_t,
                   "notify_t": notify_t}))

        if status is None:
            return self._notify_result(
                executed=False, phase="ORDER_CREATE_FAILED",
                detail="transport failure creating order",
                http_status=None)

        if self._is_configuration_fault(status, payload):
            err = (payload.get("error") or {})
            return self._notify_result(
                executed=False, phase="ORDER_CREATE_FAILED",
                detail=str(err.get("description") or "configuration fault"),
                http_status=status,
                error_code=str(err.get("code") or ""))

        if status >= 400:
            err = (payload.get("error") or {})
            return self._notify_result(
                executed=False, phase="ORDER_CREATE_FAILED",
                detail=str(err.get("description") or f"HTTP {status}"),
                http_status=status,
                error_code=str(err.get("code") or ""))

        order_id = parse_order_id(payload)
        if not order_id:
            return self._notify_result(
                executed=False, phase="ORDER_CREATE_FAILED",
                detail="response missing order id",
                http_status=status)

        rec = PredeliveryOrder(
            mandate_uid=ref.uid, target_t=target_t,
            order_id=order_id, amount_paise=amount_paise,
            payment_after=payment_after,
            phase=PredeliveryPhase.ORDER_CREATED,
            http_status=status)
        self._predelivery[self._predelivery_key(ref, target_t)] = rec
        self.workflow_log.append({
            "kind": "NOTIFY", "mandate_uid": ref.uid,
            "notify_t": notify_t, "target_t": target_t,
            "amount": amount, "order_id": order_id,
            "phase": ORDER_CREATED})
        return self._notify_result(
            executed=True, phase=ORDER_CREATED,
            detail="razorpay order created; delivery unconfirmed until webhook",
            order_id=order_id, http_status=status)

    # --------------------------------------------------------- introspection
    def estimates(self, customer_id: int) -> tuple[float, int]:
        """`(est_salary, est_payday)` for the belief filter's cold start.

        Uses the explicit `sim_customer_id` on a binding. Ordinary Razorpay
        ids such as `cust_ABC123` do not encode a simulation index and must
        not be parsed as one.
        """
        for b in self.bindings.values():
            if b.sim_customer_id is not None and b.sim_customer_id == customer_id:
                return b.est_salary, b.est_payday
        return 0.0, 0
