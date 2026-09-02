"""The INDEPENDENT half. Recomputes Stage 0 legality from the audit log alone.

READ THIS BEFORE EDITING. This module deliberately does not import
`agent.constraints.rules` or `agent.constraints.stage0`, and
`agent/tests/test_layer_isolation.py` fails if it ever does. It is a second
implementation of the same five rules, written from the rule text rather than
from the enforcer, so that the two can disagree.

Why that matters here more than it would in most codebases: this project has
now shipped five guardrails that reported green while measuring nothing, and
the mechanism was the same every time -- the check and the thing checked shared
state. `assert violations == 0` where `live` had already filtered the
condition. A mutant that increments the counter its own gate reads (error 11).
An enforcement layer verified by calling its own predicates would be the sixth.

WHAT IT COUNTS. Violations that ACTUALLY HAPPENED: money that moved illegally.
A refused action is not a violation, it is the gate working, and it is counted
separately as `refused`. If `violations` is ever non-zero the enforcement layer
has a hole in it.

WHAT IT SHARES WITH THE ENFORCER. The regulatory constants only (peak window,
attempt cap, notification lead), imported from `w3` so the external facts in
docs/results.md have one home. The LOGIC is written twice on purpose.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import agent  # noqa: F401  -- puts sim/ on the path
import w3

_PEAK_HOURS = w3.PEAK
_CAP = w3.NPCI_MAX
_LEAD_HOURS = w3.HOURS
_TECH_CODE = "TECH"


@dataclass
class IndependentCounts:
    """Deliberately the same five field names as `harness.Violations.asdict()`
    so the two can be compared directly -- and deliberately computed from a
    different source."""
    cap: int = 0
    peak: int = 0
    lead: int = 0
    pending: int = 0
    represent: int = 0
    # context, not violations
    executed: int = 0
    refused: int = 0
    notifications: int = 0
    recovered_paise: int = 0
    detail: list[str] = field(default_factory=list)

    def total(self) -> int:
        return self.cap + self.peak + self.lead + self.pending + self.represent

    def asdict(self) -> dict:
        return dict(cap=self.cap, peak=self.peak, lead=self.lead,
                    pending=self.pending, represent=self.represent)


def replay(rows: Iterable[dict]) -> IndependentCounts:
    """Re-derive Stage 0 legality from an audit log.

    The only input is the log. This function has no access to the gate, the
    ledger, the loop, or the world -- so if it finds a violation, the violation
    is in the log, which means it happened.
    """
    c = IndependentCounts()

    # The auditor's OWN bookkeeping, rebuilt from the row stream.
    seen_attempts: dict[tuple[str, int], int] = {}
    last_code: dict[tuple[str, int], str] = {}
    outstanding: dict[str, dict | None] = {}
    pending_action: dict[str, str] = {}     # action_id -> mandate_uid awaiting outcome

    for r in sorted(rows, key=lambda x: x.get("seq", 0)):
        kind = r.get("kind")

        if kind == "NOTIFICATION_ISSUED":
            uid = r["mandate_uid"]
            c.notifications += 1
            if outstanding.get(uid) is not None:
                c.pending += 1
                c.detail.append(
                    f"pending: mandate {uid} had an outstanding notification "
                    f"targeting t={outstanding[uid]['target_t']} when a second "
                    f"was issued targeting t={r['target_t']} (seq {r['seq']})")
            outstanding[uid] = {"target_t": r["target_t"],
                                "notify_t": r.get("notify_t")}

        elif kind == "MONEY_ACTION":
            uid = r["mandate_uid"]
            if r.get("gate_verdict") != "ALLOWED":
                c.refused += 1
                outstanding[uid] = None
                continue

            c.executed += 1
            key = (uid, r["cycle"])
            n_before = seen_attempts.get(key, 0)
            target_t = r["target_t"]
            notify_t = r.get("notify_t")

            # --- cap
            if n_before >= _CAP:
                c.cap += 1
                c.detail.append(
                    f"cap: mandate {uid} cycle {r['cycle']} executed attempt "
                    f"{n_before + 1} against a cap of {_CAP} (seq {r['seq']})")
            seen_attempts[key] = n_before + 1

            # --- peak
            if (target_t % 24) in _PEAK_HOURS:
                c.peak += 1
                c.detail.append(
                    f"peak: mandate {uid} executed at hour {target_t % 24:02d}:00 "
                    f"(seq {r['seq']})")

            # --- lead
            if notify_t is not None and (target_t - notify_t) < _LEAD_HOURS:
                c.lead += 1
                c.detail.append(
                    f"lead: mandate {uid} executed {target_t - notify_t}h after "
                    f"notification, needs {_LEAD_HOURS}h (seq {r['seq']})")

            # --- represent
            if notify_t is None and last_code.get(key) != _TECH_CODE:
                c.represent += 1
                c.detail.append(
                    f"represent: mandate {uid} re-presented with no fresh "
                    f"notification after prev_code="
                    f"{last_code.get(key)!r} (seq {r['seq']})")

            outstanding[uid] = None
            pending_action[r["action_id"]] = key

        elif kind == "OUTCOME":
            key = pending_action.pop(r.get("action_id"), None)
            if key is not None:
                last_code[key] = r["outcome_code"]
            if r.get("success"):
                c.recovered_paise += int(r.get("recovered_paise", 0))

        elif kind == "NOTIFICATION_CANCELLED":
            # An issued notification that was withdrawn before dispatch. It is
            # no longer outstanding, so the next one is not a second concurrent
            # notification. Without this event the log cannot distinguish a
            # withdrawn notification from a live one.
            outstanding[r["mandate_uid"]] = None

        elif kind == "CYCLE_OPEN":
            outstanding[r["mandate_uid"]] = None

    return c
