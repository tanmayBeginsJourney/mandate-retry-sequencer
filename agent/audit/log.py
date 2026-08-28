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


class AuditLog:
    """Append-only writer. One JSON object per line, one line per event."""

    def __init__(self, path: str, run_id: str):
        self.path = path
        self.run_id = run_id
        self._seq = 0
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
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
