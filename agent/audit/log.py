"""The audit trail. Append-only JSONL is the canonical artifact.

WHY JSONL AND NOT SQLITE AS THE SOURCE OF TRUTH. "Append-only" should be a
property of the file, not a promise in a docstring. A JSONL file that is only
ever opened in "a" mode is append-only in a way a reader can check by eye; a
SQLite database is append-only only for as long as nobody runs an UPDATE. The
SQLite database in `store.py` is a DERIVED INDEX, rebuilt from the JSONL, and
it exists so that "queryable" means actual SQL rather than a grep over prints.

WHY EVENTS AND NOT JUST ACTIONS. A refusal, a decision to wait, and an LLM
failure are all auditable and none of them is a money action. Stopping rules
cannot be "demonstrable" (the track's word) unless the moments the agent chose
NOT to act are in the log next to the moments it did.

Every money action gets a stable `action_id` and every row about it carries
that id, so one `WHERE action_id = ?` returns the whole chain: what the belief
predicted, what the diagnosis said and why, all five constraint verdicts, what
was executed, and what came back.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterator


class EventKind:
    RUN_START = "RUN_START"
    RUN_END = "RUN_END"
    DECISION_TICK = "DECISION_TICK"
    DIAGNOSIS = "DIAGNOSIS"
    NOTIFICATION_ISSUED = "NOTIFICATION_ISSUED"
    NOTIFICATION_CANCELLED = "NOTIFICATION_CANCELLED"
    CONSTRAINT_CHECK = "CONSTRAINT_CHECK"
    MONEY_ACTION = "MONEY_ACTION"
    NON_MONEY_ACTION = "NON_MONEY_ACTION"
    OUTCOME = "OUTCOME"
    STOP = "STOP"
    LLM_FAILURE = "LLM_FAILURE"
    RISK_RETRY = "RISK_RETRY"
    RISK_TERMINAL = "RISK_TERMINAL"
    UNRESOLVED = "UNRESOLVED"


class LogFileNotEmpty(RuntimeError):
    """A run tried to write into a log file that already holds another run.

    FOUND 29 AUGUST 2026, and it was printing wrong compliance numbers on the
    screen a viewer sees.

    `AuditLog` opens in "a" mode, which is what makes the file append-only and
    checkable by eye. `agent/demo.py` wrote to a FIXED path. So the second
    invocation appended to the first, and `auditor.replay` -- whose only input
    is the file -- then audited two concatenated runs as if they were one. The
    same mandate's cycle appeared twice, so attempts double against the cap and
    a notification from run A reads as concurrent with one from run B. The demo
    printed `cap 24, pending 282` beside the gate's zeros. Replayed per
    `run_id` both are 0: the agent was fine and the display was not.

    ONE LOG FILE IS ONE RUN. That was always the assumption every reader of a
    log makes -- `replay` sorts by `seq`, and `seq` restarts at 1 for each run
    -- but nothing enforced it, so a fresh clone looked clean on its first run
    and lied on its second. It is now an exception at open time rather than a
    plausible number at print time, which is the lesson of error 14."""


class AuditLog:
    """Append-only writer. One JSON object per line, one line per event.

    Refuses to open a file that already has content unless the caller says
    explicitly that it means to append. See `LogFileNotEmpty`.
    """

    def __init__(self, path: str, run_id: str, *, allow_append: bool = False):
        self.path = path
        self.run_id = run_id
        self._seq = 0
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        if not allow_append and os.path.exists(path) and os.path.getsize(path):
            raise LogFileNotEmpty(
                f"{path} already holds {os.path.getsize(path)} bytes. One log "
                f"file is one run: `auditor.replay` sorts by `seq`, and `seq` "
                f"restarts at 1 for every run, so replaying a concatenated "
                f"file reports violations that did not happen. Delete it, "
                f"choose a fresh path, or pass allow_append=True and accept "
                f"that nothing downstream can audit the result.")
        self._fh = open(path, "a", encoding="utf-8", buffering=1)

    def emit(self, kind: str, ts_hour: int, **fields: Any) -> int:
        self._seq += 1
        row = {"seq": self._seq, "run_id": self.run_id,
               "kind": kind, "ts_hour": ts_hour}
        row.update({k: v for k, v in fields.items() if v is not None})
        self._fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        return self._seq

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    def __enter__(self) -> "AuditLog":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def read_rows(path: str) -> Iterator[dict]:
    """Read a log back. The auditor's ONLY input."""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)
