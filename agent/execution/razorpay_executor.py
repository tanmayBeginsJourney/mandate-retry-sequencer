"""`RazorpayExecutor` -- the same port, a different world.

THE POINT OF THIS FILE IS THAT NOTHING ELSE CHANGES. `agent/ports.py` declares
one method:

    class Executor(Protocol):
        def attempt(self, ref, amount, t) -> AttemptOutcome: ...

`SimExecutor` implements it against the frozen simulation. This implements it
against Razorpay's live API. `agent/loop.py`, `agent/policy/`,
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

**Verified against the LIVE API, 30 August 2026** (`scripts/razorpay_ladder.py`,
transcript in `logs/razorpay_ladder.json`, no key needed):
  * DNS, TLS 1.3, and the charge URL existing and answering
  * the shape of a real API-level error envelope -- `code` and `description`
    alone, no `reason`, no `source`, no `step`, no `metadata`
  * that this file used to turn that envelope into a customer decline. Error 28.

**NOT TESTED, PENDING CREDENTIALS.** Every line marked `# UNVERIFIED` below.
No Razorpay key has been used by this project and **no request has ever been
authenticated**, so Razorpay has never read one of our request bodies.
Specifically unverified:
  * the exact request body Razorpay wants for a recurring UPI charge
  * whether test mode returns populated `error_reason` values on
    `failure@razorpay`, or a single generic one
  * whether the pre-debit notification API is required before each debit in
    test mode, and what it returns
  * whether `payment.downtime` is populated in test mode at all

The shapes come from Razorpay's public documentation, read 29 August 2026, and
are recorded in `docs/01_FACTS.md`. A doc-derived request body that has never
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
   responses happen, and error 19 in `docs/03_ERRORS.md` is this project
   already proposing a double debit once.

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
import urllib.request
from dataclasses import dataclass

# The reason -> family map lives in ports.py, not here. Gate I2 forbids a
# sibling import inside agent/execution, and rule I1 forbids agent/llm from
# reaching agent.execution at all -- so a table the narrative layer may need
# could never have lived in this package. See ports.py.
from agent.ports import (OK, AttemptOutcome, MandateRef, Rupees, bank_of,
                         code_for_reason, is_pending, to_paise)

API_BASE = "https://api.razorpay.com/v1"

#: Razorpay's OWN documented retry schedule for a failed subscription charge:
#: attempt on T, then T+1, T+2, T+3, after which the subscription moves to
#: `halted`. [VERIFIED] from their Payment Retries page, 29 August 2026.
#:
#: Recorded here rather than in a comment because it does two jobs. It is
#: independent corroboration of the NPCI attempt cap -- 1 presentation plus 3
#: retries -- which `docs/01_FACTS.md` had only from a secondary source. And it
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
    #: Bootstrap estimates. IN PRODUCTION THESE ARE THE OPEN PROBLEM: the
    #: belief filter needs a starting salary and payday guess per customer, and
    #: a real integration has no oracle for either. The honest options are a
    #: population prior, or a wide prior that the first cycle's outcomes
    #: sharpen. Neither is measured here. `docs/00_HANDOFF.md` open item 1.
    est_salary: float = 0.0
    est_payday: int = 0


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
                 max_transport_retries: int = 2):
        self.bindings = bindings
        self.currency = currency
        self.max_transport_retries = max_transport_retries
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

    # ------------------------------------------------------ the money path
    def attempt(self, ref: MandateRef, amount: Rupees, t: int,
                action_id: str = "") -> AttemptOutcome:
        """Charge one mandate. NEVER RAISES for a decline or a network fault.

        It DOES raise `RazorpayError` for a configuration fault -- a refused
        credential -- which is that exception's declared job. See
        `_is_configuration_fault` for why that is not a decline, and error 28
        in `docs/03_ERRORS.md` for what this code did before 30 August 2026.

        `action_id` is optional so the signature still satisfies the `Executor`
        protocol. Stage 0 passes it, so the idempotency key is tied to the
        audited action; a caller that omits it falls back to the mandate and
        hour, which is still deterministic but is a weaker guarantee across
        runs. Said out loud because a silently weaker guarantee is worse than a
        documented one.
        """
        b = self._binding(ref)
        key = self.idempotency_key(action_id or f"{ref.uid}@{t}", ref, t)

        # UNVERIFIED: shape taken from Razorpay's S2S recurring-payments docs.
        # An order is created, then charged against the stored token.
        body = {
            "amount": to_paise(amount),
            "currency": self.currency,
            "customer_id": b.rzp_customer_id,
            "token": b.rzp_token_id,
            "recurring": "1",
            "description": f"mandate {ref.uid}",
            "notes": {"mandate_uid": ref.uid, "action_id": action_id},
        }

        status, payload = self._post_with_retries(f"{API_BASE}/payments/create/recurring",
                                                  body, key)
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
        NPCI attempts and let the mandate die. `docs/03_ERRORS.md` error 28.

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
    def nudge(self, ref: MandateRef, amount: Rupees, t: int) -> bool:
        """Ask the customer to fund the account.

        NOT IMPLEMENTED AND DELIBERATELY NOT FAKED. A nudge is an SMS, a push
        or an email through a channel this project has not chosen, and there is
        no measured Indian UPI nudge take-up rate in `docs/01_FACTS.md` to
        model one with. Returning `False` means "no top-up resulted", which is
        the conservative reading and credits no money. `NUDGE` measures
        approximately zero in the action ablation anyway
        (`docs/02_RESULTS.md`), so this is an honest stub rather than a hole.
        """
        return False

    def notify(self, ref: MandateRef, amount: Rupees, notify_t: int,
               target_t: int) -> dict:
        """Send the pre-debit notification Razorpay requires before a debit.

        ⚠️ **WIRED TO NOTHING.** `Stage0Gate.issue_notification` records
        pendency in its own ledger and does not call the executor, and it was
        left that way ON PURPOSE: the headline claim of this file is that
        Stage 0, the loop, the belief and the audit trail are unchanged when
        the backend changes, and adding a hook to the gate for one backend's
        benefit would make that claim false. Wiring it is the one remaining
        integration step and it should be done against a live key, where the
        response can be checked, not against a docstring.
        """
        raise NotImplementedError(
            "pre-debit notification is designed but not wired -- see the "
            "docstring. Stage 0 owns notification bookkeeping today.")

    # --------------------------------------------------------- introspection
    def estimates(self, customer_id: int) -> tuple[float, int]:
        """`(est_salary, est_payday)` for the belief filter's cold start.

        `SimExecutor` can answer this because the simulation drew the noisy
        estimate itself. A real integration cannot: nobody hands you a
        customer's salary. The binding carries whatever the caller could
        supply, and zeros mean "no prior" -- which the belief filter must then
        be started wide enough to survive. THIS IS THE LARGEST UNSOLVED
        INTEGRATION PROBLEM IN THIS FILE and it is not a line of code, it is a
        missing measurement.
        """
        for b in self.bindings.values():
            if b.rzp_customer_id.endswith(f":{customer_id}"):
                return b.est_salary, b.est_payday
        return 0.0, 0
