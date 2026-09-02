"""Stage 0 as ENFORCED MIDDLEWARE. It refuses illegal actions; it does not
take notes about them.

This is the difference between the simulation and the product.
`sim/harness.py` deliberately *counts* violations and dispatches anyway --
that is what makes its counters falsifiable, because a policy that filters its
own choices cannot drive a counter to zero by construction. Correct for a
measuring instrument; unshippable for a product. So here the gate refuses, and
the falsifiability is restored by `auditor.py`, which recomputes legality from
the log alone using different code.

THE CHOKEPOINT. `Stage0Gate` is the only object in `agent/` that holds an
`Executor`. Nothing else can move money, because nothing else has anything to
move it with. `agent/tests/test_layer_isolation.py` asserts that no module
outside this one imports `agent.execution`.

BOTH HALVES ARE KEPT. Enforcement here, independent recount in `auditor.py`.
An enforcement layer with no independent check is exactly the vacuous-gate
shape this project has now hit five times (docs/errors.md).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from agent.audit.log import AuditLog, EventKind
from agent.constraints.rules import (ALL_RULES, AttemptLedger, check_notification)
from agent.ports import (Allowed, AttemptOutcome, Decision, Executor, MandateRef,
                         MoneyAction, PendingNotification, Refusal, Refused,
                         WorkflowResult, to_paise)


def action_id(run_id: str, ref: MandateRef, cycle: int, target_t: int,
              attempt_no: int) -> str:
    """Deterministic, so a re-run produces a diffable log."""
    raw = f"{run_id}|{ref.uid}|{cycle}|{target_t}|{attempt_no}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class Stage0Gate:
    """Every money action passes through `submit`. Illegal ones are refused."""

    def __init__(self, executor: Executor, ledger: AttemptLedger, log: AuditLog):
        self._executor = executor          # PRIVATE. The only reference in agent/.
        self.ledger = ledger
        self.log = log
        # The gate's own tally. NOT the audit trail's -- auditor.py recomputes
        # its own from the log and the two are compared. If this tally were the
        # only count, it would be the enforcer grading itself.
        self.refusals: dict[str, int] = {r: 0 for r, _ in ALL_RULES}
        self.allowed = 0

    # ------------------------------------------------------- notification
    def issue_notification(self, ref: MandateRef, cycle: int,
                           notify_t: int | None, target_t: int,
                           decided_at_t: int) -> Refusal | None:
        """Pre-authorise. Returns a Refusal, or None having recorded pendency."""
        ref_ = check_notification(self.ledger, ref, cycle, notify_t, target_t)
        if ref_ is not None:
            self.refusals[ref_.rule] += 1
            self.log.emit(EventKind.CONSTRAINT_CHECK, decided_at_t,
                          stage="issue", mandate_uid=ref.uid, cycle=cycle,
                          rule=ref_.rule, verdict="REFUSED", detail=ref_.detail,
                          target_t=target_t, notify_t=notify_t)
            return ref_
        self.ledger.set_pending(
            ref.uid, PendingNotification(notify_t, target_t,
                                         under_previous_notice=notify_t is None))
        self.log.emit(EventKind.NOTIFICATION_ISSUED, decided_at_t,
                      mandate_uid=ref.uid, merchant_id=ref.merchant_id,
                      cycle=cycle, notify_t=notify_t, target_t=target_t)
        notify = getattr(self._executor, "notify", None)
        if notify is not None:
            try:
                notify(ref, 0.0, notify_t if notify_t is not None else decided_at_t,
                       target_t)
            except Exception as e:
                self.log.emit(EventKind.NON_MONEY_ACTION, decided_at_t,
                              mandate_uid=ref.uid, cycle=cycle,
                              action_kind="NOTIFY", executed=False,
                              detail=f"notify failed: {type(e).__name__}")
        return None

    def clear_pending(self, ref: MandateRef, cycle: int = 0, t: int = 0,
                      reason: str = "") -> None:
        """Drop an unconsumed notification, AND WRITE IT DOWN.

        The logging is not decoration. `auditor.py` rebuilds pendency from the
        log alone, so a notification that is issued and then silently dropped
        looks exactly like one still outstanding -- and the next notification
        for that mandate reads as a second concurrent one, which is a `pending`
        violation.

        That is not hypothetical. It is how this method got its logging: the
        outage-pause path dropped notifications without recording it, the gate's
        own counter said 0 violations, and the independent auditor said 8 --
        exactly the number of paused dispatches. The auditor was right. The
        audit trail was incomplete, and the fix belongs in the trail, never in
        the auditor.
        """
        if self.ledger.pending(ref.uid) is None:
            return
        self.ledger.set_pending(ref.uid, None)
        self.log.emit(EventKind.NOTIFICATION_CANCELLED, t,
                      mandate_uid=ref.uid, merchant_id=ref.merchant_id,
                      cycle=cycle, reason=reason or "cancelled")

    # ---------------------------------------------------------- non-money
    def _emit_wf(self, kind: str, ref: MandateRef, cycle: int, t: int,
                 wr: WorkflowResult, diagnosis_id: str = "",
                 action_id: str = "", **extra) -> WorkflowResult:
        self.log.emit(EventKind.NON_MONEY_ACTION, t, mandate_uid=ref.uid,
                      customer_id=ref.customer_id, merchant_id=ref.merchant_id,
                      cycle=cycle, action_kind=kind, diagnosis_id=diagnosis_id,
                      adjudicated=False, executed=wr.executed,
                      credited=wr.credited, channel=wr.channel,
                      vendor_id=wr.vendor_id or None, detail=wr.detail or None,
                      status=wr.status or None,
                      short_url=wr.short_url or None,
                      action_id=action_id or None, **extra)
        return wr

    def send_reminder(self, ref: MandateRef, cycle: int, amount: float, t: int,
                      diagnosis_id: str = "", message: str = "",
                      action_id: str = "") -> WorkflowResult:
        fn = getattr(self._executor, "remind", None) or getattr(
            self._executor, "nudge", None)
        if fn is None:
            wr = WorkflowResult(executed=False, channel="missing",
                                detail="executor has no remind()")
        else:
            wr = fn(ref, amount, t, message=message, action_id=action_id)
            if not isinstance(wr, WorkflowResult):
                wr = WorkflowResult(executed=bool(wr), channel="legacy_bool")
        return self._emit_wf("REMIND", ref, cycle, t, wr,
                             diagnosis_id=diagnosis_id, action_id=action_id)

    def send_nudge(self, ref: MandateRef, cycle: int, amount: float, t: int,
                   diagnosis_id: str = "", message: str = "",
                   action_id: str = "") -> WorkflowResult:
        """Reminders, not a Payment Link. Kept as a name the loop used."""
        return self.send_reminder(ref, cycle, amount, t,
                                  diagnosis_id=diagnosis_id, message=message,
                                  action_id=action_id)

    def issue_backup_link(self, ref: MandateRef, cycle: int, amount: float,
                          t: int, diagnosis_id: str = "", message: str = "",
                          action_id: str = "") -> WorkflowResult:
        fn = getattr(self._executor, "backup_checkout", None)
        if fn is None:
            wr = WorkflowResult(executed=False, channel="missing",
                                detail="executor has no backup_checkout()")
        else:
            wr = fn(ref, amount, t, message=message, action_id=action_id)
            if not isinstance(wr, WorkflowResult):
                wr = WorkflowResult(executed=False, detail="bad backup result")
        return self._emit_wf("BACKUP_LINK", ref, cycle, t, wr,
                             diagnosis_id=diagnosis_id, action_id=action_id)

    def poll_backup_link(self, ref: MandateRef, cycle: int, t: int) -> WorkflowResult:
        fn = getattr(self._executor, "fetch_backup", None)
        if fn is None:
            return WorkflowResult(executed=False, detail="no fetch_backup")
        wr = fn(ref, t)
        if not isinstance(wr, WorkflowResult):
            wr = WorkflowResult(executed=False, detail="bad fetch result")
        return self._emit_wf("BACKUP_POLL", ref, cycle, t, wr)

    def cancel_backup_link(self, ref: MandateRef, cycle: int, t: int) -> WorkflowResult:
        fn = getattr(self._executor, "cancel_backup", None)
        if fn is None:
            return WorkflowResult(executed=False, detail="no cancel_backup")
        wr = fn(ref, t)
        if not isinstance(wr, WorkflowResult):
            wr = WorkflowResult(executed=False)
        return self._emit_wf("BACKUP_CANCEL", ref, cycle, t, wr)

    def send_escalate(self, ref: MandateRef, cycle: int, amount: float, t: int,
                      diagnosis_id: str = "", brief: str = "",
                      action_id: str = "") -> WorkflowResult:
        fn = getattr(self._executor, "escalate", None)
        if fn is None:
            wr = WorkflowResult(executed=False, channel="missing",
                                detail="executor has no escalate()")
        else:
            wr = fn(ref, amount, t, brief=brief, action_id=action_id)
            if not isinstance(wr, WorkflowResult):
                wr = WorkflowResult(executed=True, channel="legacy")
        return self._emit_wf("ESCALATE", ref, cycle, t, wr,
                             diagnosis_id=diagnosis_id, action_id=action_id,
                             credits_money=False)

    def record_non_money(self, ref: MandateRef, cycle: int, kind: str, t: int,
                         diagnosis_id: str = "", **extra) -> None:
        """STOP and other non-executing notes."""
        self.log.emit(EventKind.NON_MONEY_ACTION, t, mandate_uid=ref.uid,
                      customer_id=ref.customer_id, merchant_id=ref.merchant_id,
                      cycle=cycle, action_kind=kind, diagnosis_id=diagnosis_id,
                      adjudicated=False, **extra)

    # -------------------------------------------------------------- money
    def submit(self, a: MoneyAction) -> Decision:
        """Adjudicate and, if every rule permits, execute.

        All five rules are evaluated even after one refuses, so the audit log
        records the complete verdict rather than the first objection. A judge
        asking "was this legal" should see five answers, not one.
        """
        verdicts: list[tuple[str, Refusal | None]] = [
            (name, fn(self.ledger, a)) for name, fn in ALL_RULES]

        for name, refusal in verdicts:
            self.log.emit(
                EventKind.CONSTRAINT_CHECK, a.target_t, action_id=a.action_id,
                stage="dispatch", mandate_uid=a.ref.uid, cycle=a.cycle,
                rule=name, verdict="REFUSED" if refusal else "PASS",
                detail=refusal.detail if refusal else None)

        first = next((r for _, r in verdicts if r is not None), None)
        if first is not None:
            for _, r in verdicts:
                if r is not None:
                    self.refusals[r.rule] += 1
            self.log.emit(EventKind.MONEY_ACTION, a.target_t,
                          action_id=a.action_id, mandate_uid=a.ref.uid,
                          customer_id=a.ref.customer_id,
                          merchant_id=a.ref.merchant_id, cycle=a.cycle,
                          attempt_no=self.ledger.attempts(a.ref.uid, a.cycle) + 1,
                          amount_paise=to_paise(a.amount),
                          intervention_kind=a.kind.value,
                          diagnosis_id=a.diagnosis_id,
                          p_now=a.p_now, p_later=a.p_later,
                          index_score=a.index_score,
                          target_t=a.target_t, notify_t=a.notify_t,
                          gate_verdict="REFUSED", refusal_rule=first.rule)
            self.ledger.set_pending(a.ref.uid, None)
            return Refused(first)

        # ---- legal. Execute.
        self.allowed += 1
        attempt_no = self.ledger.attempts(a.ref.uid, a.cycle) + 1
        # `a.action_id` is passed so a real backend's idempotency key is keyed
        # on the SAME identity this trail audits. It was not passed until
        # 30 August 2026, which silently left RazorpayExecutor on its weaker
        # `mandate@hour` fallback. SimExecutor ignores it.
        outcome = self._executor.attempt(a.ref, a.amount, a.target_t,
                                         a.action_id)
        self.ledger.record_attempt(a.ref.uid, a.cycle, outcome.code)

        self.log.emit(EventKind.MONEY_ACTION, a.target_t,
                      action_id=a.action_id, mandate_uid=a.ref.uid,
                      customer_id=a.ref.customer_id,
                      merchant_id=a.ref.merchant_id, cycle=a.cycle,
                      attempt_no=attempt_no, amount_paise=to_paise(a.amount),
                      intervention_kind=a.kind.value,
                      diagnosis_id=a.diagnosis_id,
                      p_now=a.p_now, p_later=a.p_later,
                      index_score=a.index_score,
                      target_t=a.target_t, notify_t=a.notify_t,
                      gate_verdict="ALLOWED")
        self.log.emit(EventKind.OUTCOME, outcome.t, action_id=a.action_id,
                      mandate_uid=a.ref.uid, merchant_id=a.ref.merchant_id,
                      cycle=a.cycle, outcome_code=outcome.code,
                      success=outcome.success,
                      recovered_paise=to_paise(a.amount) if outcome.success else 0)
        return Allowed(outcome)
