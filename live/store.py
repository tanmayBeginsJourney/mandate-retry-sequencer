"""Durable state. SQLite, one file, one node.

WHY SQLITE. The requirement is that a crash cannot lose a payment intent or
replay one, and that a redelivered webhook is a no-op. Those are transaction
and unique-constraint problems, not scale problems.

WHAT THE SCHEMA ENFORCES, RATHER THAN THE CODE:

  * `webhook_events.event_id` is the PRIMARY KEY, so duplicate delivery is
    rejected by the database and not by a check-then-insert two concurrent
    requests can both pass.
  * `attempts.receipt` is UNIQUE, so two attempts cannot claim one provider
    order -- a bug in id derivation surfaces as an integrity error here rather
    than as a second debit at Razorpay.
  * `attempts.id` IS Stage 0's `action_id`, so re-deriving an attempt after a
    restart collides with the existing row instead of creating a twin.

NO SECRET IS EVER STORED. The webhook *payload* is stored verbatim because
signature verification and dispute resolution both need the exact bytes, and
Razorpay's payment entities carry an email and a contact -- so it is treated as
sensitive and is never returned by the operator API. See `api.py`.
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
import time
from dataclasses import fields
from typing import Iterator

from live.domain import (AttemptState, Customer, Mandate, MandateState,
                         PaymentAttempt, WebhookEvent)

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id                 TEXT PRIMARY KEY,
    rzp_customer_id    TEXT NOT NULL DEFAULT '',
    email              TEXT NOT NULL DEFAULT '',
    contact            TEXT NOT NULL DEFAULT '',
    name               TEXT NOT NULL DEFAULT '',
    seq                INTEGER NOT NULL DEFAULT 0,
    created_at         INTEGER NOT NULL
);

-- Process-wide settings that must survive a restart. The clock origin lives
-- here: `target_t` is a simulated hour, and an origin that moved between runs
-- would silently change what every stored hour meant.
CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mandates (
    id                       TEXT PRIMARY KEY,
    customer_id              TEXT NOT NULL REFERENCES customers(id),
    state                    TEXT NOT NULL,
    rzp_token_id             TEXT NOT NULL DEFAULT '',
    rzp_customer_id          TEXT NOT NULL DEFAULT '',
    registration_order_id    TEXT NOT NULL DEFAULT '',
    registration_payment_id  TEXT NOT NULL DEFAULT '',
    token_status             TEXT NOT NULL DEFAULT '',
    max_amount_paise         INTEGER NOT NULL DEFAULT 0,
    charge_amount_paise      INTEGER NOT NULL DEFAULT 0,
    frequency                TEXT NOT NULL DEFAULT '',
    expire_at                INTEGER NOT NULL DEFAULT 0,
    index_no                 INTEGER NOT NULL DEFAULT 0,
    merchant_id              INTEGER NOT NULL DEFAULT 1,
    est_salary               REAL NOT NULL DEFAULT 0,
    est_payday               INTEGER NOT NULL DEFAULT 1,
    cycle_days               INTEGER NOT NULL DEFAULT 30,
    cycle                    INTEGER NOT NULL DEFAULT 0,
    cycle_start_t            INTEGER NOT NULL DEFAULT 0,
    created_at               INTEGER NOT NULL,
    updated_at               INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mandates_token ON mandates(rzp_token_id);

CREATE TABLE IF NOT EXISTS attempts (
    id             TEXT PRIMARY KEY,
    mandate_id     TEXT NOT NULL REFERENCES mandates(id),
    mandate_uid    TEXT NOT NULL DEFAULT '',
    amount_paise   INTEGER NOT NULL,
    state          TEXT NOT NULL,
    order_id       TEXT NOT NULL DEFAULT '',
    payment_id     TEXT NOT NULL DEFAULT '',
    receipt        TEXT NOT NULL UNIQUE,
    outcome_code   TEXT NOT NULL DEFAULT '',
    raw_reason     TEXT NOT NULL DEFAULT '',
    target_t       INTEGER NOT NULL DEFAULT 0,
    payment_after  INTEGER NOT NULL DEFAULT 0,
    submitted_at   INTEGER NOT NULL DEFAULT 0,
    resolved_at    INTEGER NOT NULL DEFAULT 0,
    conflicted     INTEGER NOT NULL DEFAULT 0,
    cycle          INTEGER NOT NULL DEFAULT 0,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_mandate ON attempts(mandate_id);
CREATE INDEX IF NOT EXISTS idx_attempts_order   ON attempts(order_id);
CREATE INDEX IF NOT EXISTS idx_attempts_payment ON attempts(payment_id);
CREATE INDEX IF NOT EXISTS idx_attempts_state   ON attempts(state);
CREATE INDEX IF NOT EXISTS idx_attempts_target  ON attempts(mandate_uid, target_t);

CREATE TABLE IF NOT EXISTS webhook_events (
    event_id         TEXT PRIMARY KEY,
    event_type       TEXT NOT NULL,
    received_at      INTEGER NOT NULL,
    signature_valid  INTEGER NOT NULL,
    payload          TEXT NOT NULL,
    processed_at     INTEGER NOT NULL DEFAULT 0,
    result           TEXT NOT NULL DEFAULT '',
    mandate_id       TEXT NOT NULL DEFAULT '',
    attempt_id       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_received ON webhook_events(received_at);
CREATE INDEX IF NOT EXISTS idx_events_attempt  ON webhook_events(attempt_id);

-- Every state change, append-only. The tables above hold the CURRENT answer;
-- this holds how it got there, which is what a reconciliation dispute needs.
CREATE TABLE IF NOT EXISTS transitions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          INTEGER NOT NULL,
    entity      TEXT NOT NULL,      -- 'mandate' | 'attempt'
    entity_id   TEXT NOT NULL,
    from_state  TEXT NOT NULL,
    to_state    TEXT NOT NULL,
    verdict     TEXT NOT NULL,      -- domain.Transition
    source      TEXT NOT NULL,      -- 'webhook' | 'poll' | 'submit' | ...
    detail      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_transitions_entity
    ON transitions(entity, entity_id);
"""

#: Columns a second write must NEVER overwrite, per entity. Stated, rather than
#: implied by omission from a hand-written UPDATE list. `receipt` and
#: `amount_paise` fix an attempt's identity at the provider; `created_at` fixes
#: it in time; the cold-start estimates and the mandate index are what the
#: belief book and the audit trail key on. Moving any of them would silently
#: redefine a row a decision has already been made against.
#: `WebhookEvent` is deliberately absent: a delivery is INSERT-ONLY, because
#: the whole of deduplication is that a second insert of one event id fails.
#: `_upsert(WebhookEvent)` raises here rather than quietly writing one.
IMMUTABLE = {
    Customer: {"id", "seq", "created_at"},
    Mandate: {"id", "customer_id", "index_no", "merchant_id", "est_salary",
              "est_payday", "cycle_days", "created_at"},
    PaymentAttempt: {"id", "mandate_id", "mandate_uid", "amount_paise",
                     "receipt", "target_t", "cycle", "created_at"},
}

#: SQLite gives back ints and strings; these put them back into the domain's
#: own types. A field not named here round-trips unchanged.
_COERCE = {"state": {Mandate: MandateState, PaymentAttempt: AttemptState},
           "conflicted": {PaymentAttempt: bool},
           "signature_valid": {WebhookEvent: bool}}

#: The one place a dataclass is bound to a table.
TABLES = {Customer: ("customers", "id"), Mandate: ("mandates", "id"),
          PaymentAttempt: ("attempts", "id"),
          WebhookEvent: ("webhook_events", "event_id")}


def _cols(cls) -> list[str]:
    return [f.name for f in fields(cls)]


def _upsert(cls) -> str:
    """INSERT ... ON CONFLICT DO UPDATE over every mutable column.

    Generated from the dataclass, so a field added to the domain and forgotten
    in the SQL is impossible rather than silent -- which is the failure mode a
    hand-written statement has and does not announce.
    """
    table, key = TABLES[cls]
    cols = _cols(cls)
    updates = [f"{c}=excluded.{c}" for c in cols if c not in IMMUTABLE[cls]]
    return (f"INSERT INTO {table}({', '.join(cols)})"
            f" VALUES({', '.join('?' * len(cols))})"
            f" ON CONFLICT({key}) DO UPDATE SET {', '.join(updates)}")


def _values(obj) -> tuple:
    out = []
    for name in _cols(type(obj)):
        v = getattr(obj, name)
        out.append(v.value if hasattr(v, "value") else
                   int(v) if isinstance(v, bool) else v)
    return tuple(out)


def _read(cls, row: sqlite3.Row):
    kw = {}
    for name in _cols(cls):
        v = row[name]
        cast = _COERCE.get(name, {}).get(cls)
        kw[name] = cast(v) if cast is not None else v
    return cls(**kw)


class Store:
    """One SQLite file, one node.

    The connection is shared across the HTTP server's threads and guarded by
    one lock rather than pooled: a single serialised writer is both correct and
    fast enough here, and a pool would need a transaction story this does not.
    """

    def __init__(self, path: str):
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False,
                                   isolation_level=None, timeout=10.0)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        # A committed transaction has reached the disk, which is what makes
        # "the intent is durable before the request leaves" true rather than
        # intended.
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(SCHEMA)
        # The statements above are generated from the dataclasses, so a field
        # that has no column would fail at the first write with a message about
        # SQL rather than about the schema. Checked once, at open.
        for cls, (table, _) in TABLES.items():
            cols = {r["name"] for r in
                    self._db.execute(f"PRAGMA table_info({table})")}
            missing = set(_cols(cls)) - cols
            assert not missing, f"{table} has no column for {sorted(missing)}"
        self._lock = threading.RLock()
        #: Nesting depth of `tx()`. SQLite has no nested transactions, so an
        #: inner `tx()` joins the outer one instead of trying to BEGIN again.
        self._depth = 0

    def close(self) -> None:
        self._db.close()

    @contextlib.contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """One atomic unit. Everything that must not half-happen goes in one.

        REENTRANT. A caller that records a transition and then writes the row
        it describes wants both or neither, and gets it by wrapping the pair in
        its own `tx()`: the inner ones join rather than committing early.
        """
        with self._lock:
            outermost = self._depth == 0
            if outermost:
                self._db.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self._db
            except BaseException:
                self._depth -= 1
                if outermost:
                    self._db.execute("ROLLBACK")
                raise
            self._depth -= 1
            if outermost:
                self._db.execute("COMMIT")

    def _one(self, cls, sql: str, *args):
        row = self._db.execute(sql, args).fetchone()
        return _read(cls, row) if row else None

    def _many(self, cls, sql: str, *args) -> list:
        return [_read(cls, r) for r in self._db.execute(sql, args)]

    # ----------------------------------------------------------------- meta
    def meta_get(self, key: str, default: str = "") -> str:
        row = self._db.execute("SELECT value FROM meta WHERE key=?",
                               (key,)).fetchone()
        return row["value"] if row else default

    def meta_set_once(self, key: str, value: str) -> str:
        """Write only if absent, and return whatever is stored afterwards.

        The clock origin must be decided exactly once for the life of a
        database. A plain upsert would let a restart move it, and every
        `target_t` on disk would quietly start meaning a different moment.
        """
        with self.tx() as db:
            db.execute("INSERT OR IGNORE INTO meta(key, value) VALUES(?,?)",
                       (key, value))
        return self.meta_get(key, value)

    def next_customer_seq(self) -> int:
        return int(self._db.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 AS n FROM customers"
        ).fetchone()["n"])

    def next_mandate_index(self, customer_id: str) -> int:
        """Mandate index WITHIN a customer, which is what `MandateRef` means.

        A global counter would make `c3m7` the seventh mandate in the database
        rather than this customer's seventh, and the uid is what the belief
        book and the audit trail key on.
        """
        return int(self._db.execute(
            "SELECT COALESCE(MAX(index_no), -1) + 1 AS n FROM mandates"
            " WHERE customer_id=?", (customer_id,)).fetchone()["n"])

    # ------------------------------------------------------------ customers
    def put_customer(self, c: Customer) -> None:
        with self.tx() as db:
            db.execute(_upsert(Customer), _values(c))

    def customer(self, cid: str) -> Customer | None:
        return self._one(Customer, "SELECT * FROM customers WHERE id=?", cid)

    def customers(self) -> list[Customer]:
        return self._many(Customer,
                          "SELECT * FROM customers ORDER BY created_at")

    # ------------------------------------------------------------- mandates
    def put_mandate(self, m: Mandate) -> None:
        m.updated_at = int(time.time())
        with self.tx() as db:
            db.execute(_upsert(Mandate), _values(m))

    def mandate(self, mid: str) -> Mandate | None:
        return self._one(Mandate, "SELECT * FROM mandates WHERE id=?", mid)

    def mandate_by_token(self, token_id: str) -> Mandate | None:
        if not token_id:
            return None
        return self._one(Mandate, "SELECT * FROM mandates WHERE rzp_token_id=?",
                         token_id)

    def mandates(self) -> list[Mandate]:
        return self._many(Mandate,
                          "SELECT * FROM mandates ORDER BY created_at")

    # ------------------------------------------------------------- attempts
    def put_attempt(self, a: PaymentAttempt) -> None:
        a.updated_at = int(time.time())
        with self.tx() as db:
            db.execute(_upsert(PaymentAttempt), _values(a))

    def attempt(self, aid: str) -> PaymentAttempt | None:
        return self._one(PaymentAttempt, "SELECT * FROM attempts WHERE id=?",
                         aid)

    def attempt_for_target(self, mandate_uid: str,
                           target_t: int) -> PaymentAttempt | None:
        """The attempt a pre-debit order belongs to.

        The executor works in `(mandate_uid, target_t)` because that is Stage
        0's identity; the store keys on the internal mandate id. This is the
        join between them.
        """
        return self._one(
            PaymentAttempt,
            "SELECT * FROM attempts WHERE mandate_uid=? AND target_t=?"
            " ORDER BY created_at DESC LIMIT 1", mandate_uid, target_t)

    def attempt_by_payment(self, payment_id: str) -> PaymentAttempt | None:
        if not payment_id:
            return None
        return self._one(PaymentAttempt,
                         "SELECT * FROM attempts WHERE payment_id=?",
                         payment_id)

    def attempt_by_order(self, order_id: str) -> PaymentAttempt | None:
        """Correlate a webhook that names an order but not our attempt.

        `payment.failed` for a recurring charge carries `order_id`, and until
        the payment id is known that is the only join we have.
        """
        if not order_id:
            return None
        return self._one(
            PaymentAttempt,
            "SELECT * FROM attempts WHERE order_id=? ORDER BY created_at DESC"
            " LIMIT 1", order_id)

    def attempts_for(self, mandate_id: str,
                     limit: int = 50) -> list[PaymentAttempt]:
        return self._many(
            PaymentAttempt,
            "SELECT * FROM attempts WHERE mandate_id=?"
            " ORDER BY created_at DESC LIMIT ?", mandate_id, limit)

    def recent_attempts(self, limit: int = 50) -> list[PaymentAttempt]:
        return self._many(
            PaymentAttempt,
            "SELECT * FROM attempts ORDER BY created_at DESC LIMIT ?", limit)

    def unresolved_attempts(self, states: frozenset[AttemptState],
                            limit: int = 100) -> list[PaymentAttempt]:
        """Rows the provider may know more about than we do.

        This is the crash-recovery query. Everything it returns is a place
        where the process stopped between deciding and knowing.
        """
        marks = ",".join("?" * len(states))
        return self._many(
            PaymentAttempt,
            f"SELECT * FROM attempts WHERE state IN ({marks})"
            " ORDER BY created_at LIMIT ?", *[s.value for s in states], limit)

    # ------------------------------------------------------ webhook events
    def record_event(self, ev: WebhookEvent) -> bool:
        """Persist a delivery. Returns False if this event id is already known.

        THE UNIQUENESS CHECK IS THE INSERT. A SELECT followed by an INSERT
        would let two concurrent deliveries of one event both see an empty
        table and both proceed, which is the duplicate `x-razorpay-event-id`
        exists to prevent.
        """
        cols = _cols(WebhookEvent)
        try:
            with self.tx() as db:
                db.execute(f"INSERT INTO webhook_events({', '.join(cols)})"
                           f" VALUES({', '.join('?' * len(cols))})",
                           _values(ev))
            return True
        except sqlite3.IntegrityError:
            return False

    def mark_event_processed(self, event_id: str, result: str,
                             mandate_id: str = "",
                             attempt_id: str = "") -> None:
        with self.tx() as db:
            db.execute(
                "UPDATE webhook_events SET processed_at=?, result=?,"
                " mandate_id=?, attempt_id=? WHERE event_id=?",
                (int(time.time()), result, mandate_id, attempt_id, event_id))

    def event(self, event_id: str) -> WebhookEvent | None:
        return self._one(WebhookEvent,
                         "SELECT * FROM webhook_events WHERE event_id=?",
                         event_id)

    def recent_events(self, limit: int = 50) -> list[WebhookEvent]:
        return self._many(
            WebhookEvent,
            "SELECT * FROM webhook_events ORDER BY received_at DESC, rowid DESC"
            " LIMIT ?", limit)

    def unprocessed_events(self, limit: int = 100) -> list[WebhookEvent]:
        """Accepted, signature-valid, never processed. The restart queue."""
        return self._many(
            WebhookEvent,
            "SELECT * FROM webhook_events WHERE processed_at=0"
            " AND signature_valid=1 ORDER BY received_at LIMIT ?", limit)

    # --------------------------------------------------------- transitions
    def record_transition(self, entity: str, entity_id: str, from_state: str,
                          to_state: str, verdict: str, source: str,
                          detail: str = "") -> None:
        with self.tx() as db:
            db.execute(
                "INSERT INTO transitions(at, entity, entity_id, from_state,"
                " to_state, verdict, source, detail) VALUES(?,?,?,?,?,?,?,?)",
                (int(time.time()), entity, entity_id, from_state, to_state,
                 verdict, source, detail))

    def transitions_for(self, entity: str, entity_id: str,
                        limit: int = 100) -> list[dict]:
        return [dict(r) for r in self._db.execute(
            "SELECT at, from_state, to_state, verdict, source, detail"
            " FROM transitions WHERE entity=? AND entity_id=?"
            " ORDER BY id LIMIT ?", (entity, entity_id, limit))]

    # -------------------------------------------------------------- counts
    def summary(self) -> dict:
        def one(sql: str, *args) -> int:
            return int(self._db.execute(sql, args).fetchone()[0])

        return {
            "customers": one("SELECT COUNT(*) FROM customers"),
            "mandates": one("SELECT COUNT(*) FROM mandates"),
            "mandates_active": one(
                "SELECT COUNT(*) FROM mandates WHERE state=?",
                MandateState.ACTIVE.value),
            "attempts": one("SELECT COUNT(*) FROM attempts"),
            "attempts_succeeded": one(
                "SELECT COUNT(*) FROM attempts WHERE state=?",
                AttemptState.SUCCEEDED.value),
            "attempts_unresolved": one(
                "SELECT COUNT(*) FROM attempts WHERE state NOT IN (?,?)",
                AttemptState.SUCCEEDED.value, AttemptState.FAILED.value),
            "attempts_conflicted": one(
                "SELECT COUNT(*) FROM attempts WHERE conflicted=1"),
            "events": one("SELECT COUNT(*) FROM webhook_events"),
            "events_rejected": one(
                "SELECT COUNT(*) FROM webhook_events WHERE signature_valid=0"),
            "events_unprocessed": one(
                "SELECT COUNT(*) FROM webhook_events WHERE processed_at=0"
                " AND signature_valid=1"),
            "recovered_paise": one(
                "SELECT COALESCE(SUM(amount_paise),0) FROM attempts"
                " WHERE state=?", AttemptState.SUCCEEDED.value),
        }
