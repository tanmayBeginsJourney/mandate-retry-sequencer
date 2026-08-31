"""Append-only JSONL queues. Not the audit log: these are work items.

The audit log is one file per run and refuses to reopen. A merchant queue
and a customer-reminder outbox have to survive across a prove script and
a batch, so they are ordinary append files.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any


def append_jsonl(path: str, row: dict[str, Any]) -> str:
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    row = dict(row)
    row.setdefault("ts", time.time())
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")
    return os.path.abspath(path)


def read_jsonl(path: str) -> list[dict]:
    if not path or not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
