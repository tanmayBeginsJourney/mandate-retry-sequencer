"""Durable state. SQLite, one file, one node.

WHY SQLITE AND NOT SOMETHING LARGER. The requirement is that a crash cannot
lose a payment intent and cannot replay one, and that a redelivered webhook is
a no-op. Those are transaction and unique-constraint problems, not scale
problems. A single-node demonstration that reached for a networked database
would add an operational dependency, a second failure mode and a migration
story, and would answer none of the three questions above any better.

WHAT THE SCHEMA ENFORCES, RATHER THAN THE CODE:

  * `webhook_events.event_id` is the PRIMARY KEY. Duplicate delivery is
    rejected by the database, not by a check-then-insert in Python that two
    concurrent requests can both pass.
  * `attempts.receipt` is UNIQUE. Two attempts cannot claim one provider
    order, so a bug in id derivation surfaces as an integrity error here
    rather than as a second debit at Razorpay.
  * `attempts.id` is Stage 0's `action_id`, so re-deriving an attempt after a
    restart collides with the existing row instead of creating a twin.

NO SECRET IS EVER STORED. Not the API key, not the webhook secret, not a UPI
credential. The webhook *payload* is stored verbatim because signature
verification and dispute resolution both need the exact bytes, and Razorpay's
payment entities carry an email and a contact -- so `payload` is treated as
sensitive: it is never returned by the operator API. See `api.py`.
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
import time
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
        # `check_same_thread=False` with an explicit lock rather than a
        # connection pool: the HTTP server is threaded, and one serialised
        # writer is both correct and fast enough for a single-node service.
        self._db = sqlite3.connect(path, check_same_thread=False,
                                   isolation_level=None, timeout=10.0)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(SCHEMA)
        import threading
        self._lock = threading.RLock()
        #: Nesting depth of `tx()`. SQLite has no nested transactions, so an
        #: inner `tx()` joins the outer one instead of trying to BEGIN again.
        #: Without this, any method that calls two writing methods would either
        #: raise or silently commit half its work.
        self._depth = 0

    def close(self) -> None:
        self._db.close()

    @contextlib.contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """One atomic unit. Everything that must not half-happen goes in one.

        `synchronous=FULL` above means a committed transaction has reached the
        disk, which is what makes "the intent is durable before the request
        leaves" a true statement rather than an intention.

        REENTRANT. A caller that records a transition and then writes the row
        it describes wants both or neither, and it gets that by wrapping the
        pair in its own `tx()`: the inner ones join it rather than committing
        early. SQLite has no nested transactions, so the depth counter is what
        makes that work.
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

    # ----------------------------------------------------------------- meta
    def meta_get(self, key: str, default: str = "") -> str:
        row = self._db.execute("SELECT value FROM meta WHERE key=?",
                               (key,)).fetchone()
        return row["value"] if row else default

    def meta_set_once(self, key: str, value: str) -> str:
        """Write only if absent, and return whatever is stored afterwards.

        Used for the clock origin, which must be decided exactly once for the
        life of a database. A plain upsert would let a restart move it, and
        every `target_t` already on disk would quietly start meaning a
        different wall-clock moment.
        """
        with self.tx() as db:
            db.execute("INSERT OR IGNORE INTO meta(key, value) VALUES(?,?)",
                       (key, value))
        return self.meta_get(key, value)

    def next_customer_seq(self) -> int:
        row = self._db.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 AS n FROM customers").fetchone()
        return int(row["n"])

    def next_mandate_index(self, customer_id: str) -> int:
        """Mandate index WITHIN a customer, which is what `MandateRef` means.

        A global counter here would make `c3m7` the seventh mandate in the
        database rather than this customer's seventh, and the uid is what the
        belief book and the audit trail key on.
        """
        row = self._db.execute(
            "SELECT COALESCE(MAX(index_no), -1) + 1 AS n FROM mandates"
            " WHERE customer_id=?", (customer_id,)).fetchone()
        return int(row["n"])

    # ------------------------------------------------------------ customers
    def put_customer(self, c: Customer) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT INTO customers(id, rzp_customer_id, email, contact,"
                " name, seq, created_at) VALUES(?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET"
                " rzp_customer_id=excluded.rzp_customer_id,"
                " email=excluded.email, contact=excluded.contact,"
                " name=excluded.name",
                (c.id, c.rzp_customer_id, c.email, c.contact, c.name, c.seq,
                 c.created_at))

    def customer(self, cid: str) -> Customer | None:
        row = self._db.execute("SELECT * FROM customers WHERE id=?",
                               (cid,)).fetchone()
        return _customer(row) if row else None

    def customers(self) -> list[Customer]:
        return [_customer(r) for r in
                self._db.execute("SELECT * FROM customers ORDER BY created_at")]

    # ------------------------------------------------------------- mandates
    def put_mandate(self, m: Mandate) -> None:
        m.updated_at = int(time.time())
        with self.tx() as db:
            db.execute(
                "INSERT INTO mandates(id, customer_id, state, rzp_token_id,"
                " rzp_customer_id, registration_order_id,"
                " registration_payment_id, token_status, max_amount_paise,"
                " charge_amount_paise, frequency, expire_at, index_no,"
                " merchant_id, est_salary, est_payday, cycle_days, cycle,"
                " cycle_start_t, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET"
                " state=excluded.state, rzp_token_id=excluded.rzp_token_id,"
                " rzp_customer_id=excluded.rzp_customer_id,"
                " registration_order_id=excluded.registration_order_id,"
                " registration_payment_id=excluded.registration_payment_id,"
                " token_status=excluded.token_status,"
                " max_amount_paise=excluded.max_amount_paise,"
                " charge_amount_paise=excluded.charge_amount_paise,"
                " frequency=excluded.frequency, expire_at=excluded.expire_at,"
                " cycle=excluded.cycle,"
                " cycle_start_t=excluded.cycle_start_t,"
                " updated_at=excluded.updated_at",
                (m.id, m.customer_id, m.state.value, m.rzp_token_id,
                 m.rzp_customer_id, m.registration_order_id,
                 m.registration_payment_id, m.token_status, m.max_amount_paise,
                 m.charge_amount_paise, m.frequency, m.expire_at, m.index_no,
                 m.merchant_id, m.est_salary, m.est_payday, m.cycle_days,
                 m.cycle, m.cycle_start_t, m.created_at, m.updated_at))

    def mandate(self, mid: str) -> Mandate | None:
        row = self._db.execute("SELECT * FROM mandates WHERE id=?",
                               (mid,)).fetchone()
        return _mandate(row) if row else None

    def mandate_by_token(self, token_id: str) -> Mandate | None:
        if not token_id:
            return None
        row = self._db.execute("SELECT * FROM mandates WHERE rzp_token_id=?",
                               (token_id,)).fetchone()
        return _mandate(row) if row else None

    def mandates(self) -> list[Mandate]:
        return [_mandate(r) for r in
                self._db.execute("SELECT * FROM mandates ORDER BY created_at")]

    # ------------------------------------------------------------- attempts
    def put_attempt(self, a: PaymentAttempt) -> None:
        a.updated_at = int(time.time())
        with self.tx() as db:
            db.execute(
                "INSERT INTO attempts(id, mandate_id, mandate_uid,"
                " amount_paise, state, order_id, payment_id, receipt,"
                " outcome_code, raw_reason, target_t, payment_after,"
                " submitted_at, resolved_at, conflicted, cycle, created_at,"
                " updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET"
                " state=excluded.state, order_id=excluded.order_id,"
                " payment_id=excluded.payment_id,"
                " outcome_code=excluded.outcome_code,"
                " raw_reason=excluded.raw_reason,"
                " payment_after=excluded.payment_after,"
                " submitted_at=excluded.submitted_at,"
                " resolved_at=excluded.resolved_at,"
                " conflicted=excluded.conflicted,"
                " updated_at=excluded.updated_at",
                (a.id, a.mandate_id, a.mandate_uid, a.amount_paise,
                 a.state.value, a.order_id, a.payment_id, a.receipt,
                 a.outcome_code, a.raw_reason, a.target_t, a.payment_after,
                 a.submitted_at, a.resolved_at, int(a.conflicted), a.cycle,
                 a.created_at, a.updated_at))

    def attempt(self, aid: str) -> PaymentAttempt | None:
        row = self._db.execute("SELECT * FROM attempts WHERE id=?",
                               (aid,)).fetchone()
        return _attempt(row) if row else None

    def attempt_for_target(self, mandate_uid: str,
                           target_t: int) -> PaymentAttempt | None:
        """The attempt a pre-debit order belongs to.

        The executor's journal is keyed on `(mandate_uid, target_t)` because
        that is the identity Stage 0 works in; the store is keyed on the
        internal mandate id. This is the join between them.
        """
        row = self._db.execute(
            "SELECT * FROM attempts WHERE mandate_uid=? AND target_t=?"
            " ORDER BY created_at DESC LIMIT 1",
            (mandate_uid, target_t)).fetchone()
        return _attempt(row) if row else None

    def attempt_by_payment(self, payment_id: str) -> PaymentAttempt | None:
        if not payment_id:
            return None
        row = self._db.execute("SELECT * FROM attempts WHERE payment_id=?",
                               (payment_id,)).fetchone()
        return _attempt(row) if row else None

    def attempt_by_order(self, order_id: str) -> PaymentAttempt | None:
        """Correlate a webhook that names an order but not our attempt.

        `payment.failed` for a recurring charge carries `order_id`, and until
        the payment id is known that is the only join we have.
        """
        if not order_id:
            return None
        row = self._db.execute(
            "SELECT * FROM attempts WHERE order_id=? ORDER BY created_at DESC"
            " LIMIT 1", (order_id,)).fetchone()
        return _attempt(row) if row else None

    def attempts_for(self, mandate_id: str, limit: int = 50
                     ) -> list[PaymentAttempt]:
        return [_attempt(r) for r in self._db.execute(
            "SELECT * FROM attempts WHERE mandate_id=?"
            " ORDER BY created_at DESC LIMIT ?", (mandate_id, limit))]

    def recent_attempts(self, limit: int = 50) -> list[PaymentAttempt]:
        return [_attempt(r) for r in self._db.execute(
            "SELECT * FROM attempts ORDER BY created_at DESC LIMIT ?",
            (limit,))]

    def unresolved_attempts(self, states: frozenset[AttemptState],
                            limit: int = 100) -> list[PaymentAttempt]:
        """Rows the provider may know more about than we do.

        This is the crash-recovery query. Everything it returns is a place
        where the process stopped between deciding and knowing.
        """
        marks = ",".join("?" * len(states))
        return [_attempt(r) for r in self._db.execute(
            f"SELECT * FROM attempts WHERE state IN ({marks})"
            " ORDER BY created_at LIMIT ?",
            (*[s.value for s in states], limit))]

    # ------------------------------------------------------ webhook events
    def record_event(self, ev: WebhookEvent) -> bool:
        """Persist a delivery. Returns False if this event id is already known.

        The uniqueness check IS the insert. Doing it as a SELECT followed by an
        INSERT would let two concurrent deliveries of the same event both see
        an empty table and both proceed, which is precisely the duplicate the
        `x-razorpay-event-id` header exists to prevent.
        """
        try:
            with self.tx() as db:
                db.execute(
                    "INSERT INTO webhook_events(event_id, event_type,"
                    " received_at, signature_valid, payload, processed_at,"
                    " result, mandate_id, attempt_id)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (ev.event_id, ev.event_type, ev.received_at,
                     int(ev.signature_valid), ev.payload, ev.processed_at,
                     ev.result, ev.mandate_id, ev.attempt_id))
            return True
        except sqlite3.IntegrityError:
            return False

    def mark_event_processed(self, event_id: str, result: str,
                             mandate_id: str = "", attempt_id: str = "") -> None:
        with self.tx() as db:
            db.execute(
                "UPDATE webhook_events SET processed_at=?, result=?,"
                " mandate_id=?, attempt_id=? WHERE event_id=?",
                (int(time.time()), result, mandate_id, attempt_id, event_id))

    def event(self, event_id: str) -> WebhookEvent | None:
        row = self._db.execute(
            "SELECT * FROM webhook_events WHERE event_id=?",
            (event_id,)).fetchone()
        return _event(row) if row else None

    def recent_events(self, limit: int = 50) -> list[WebhookEvent]:
        return [_event(r) for r in self._db.execute(
            "SELECT * FROM webhook_events ORDER BY received_at DESC, rowid DESC"
            " LIMIT ?", (limit,))]

    def unprocessed_events(self, limit: int = 100) -> list[WebhookEvent]:
        """Accepted, signature-valid, never processed. The restart queue."""
        return [_event(r) for r in self._db.execute(
            "SELECT * FROM webhook_events WHERE processed_at=0"
            " AND signature_valid=1 ORDER BY received_at LIMIT ?", (limit,))]

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


# ---------------------------------------------------------------- row -> obj
def _customer(r: sqlite3.Row) -> Customer:
    return Customer(id=r["id"], rzp_customer_id=r["rzp_customer_id"],
                    email=r["email"], contact=r["contact"], name=r["name"],
                    seq=r["seq"], created_at=r["created_at"])


def _mandate(r: sqlite3.Row) -> Mandate:
    return Mandate(
        id=r["id"], customer_id=r["customer_id"],
        state=MandateState(r["state"]), rzp_token_id=r["rzp_token_id"],
        rzp_customer_id=r["rzp_customer_id"],
        registration_order_id=r["registration_order_id"],
        registration_payment_id=r["registration_payment_id"],
        token_status=r["token_status"],
        max_amount_paise=r["max_amount_paise"],
        charge_amount_paise=r["charge_amount_paise"],
        frequency=r["frequency"], expire_at=r["expire_at"],
        index_no=r["index_no"], merchant_id=r["merchant_id"],
        est_salary=r["est_salary"], est_payday=r["est_payday"],
        cycle_days=r["cycle_days"], cycle=r["cycle"],
        cycle_start_t=r["cycle_start_t"],
        created_at=r["created_at"], updated_at=r["updated_at"])


def _attempt(r: sqlite3.Row) -> PaymentAttempt:
    return PaymentAttempt(
        id=r["id"], mandate_id=r["mandate_id"], mandate_uid=r["mandate_uid"],
        amount_paise=r["amount_paise"], state=AttemptState(r["state"]),
        order_id=r["order_id"], payment_id=r["payment_id"],
        receipt=r["receipt"], outcome_code=r["outcome_code"],
        raw_reason=r["raw_reason"], target_t=r["target_t"],
        payment_after=r["payment_after"], submitted_at=r["submitted_at"],
        resolved_at=r["resolved_at"], conflicted=bool(r["conflicted"]),
        cycle=r["cycle"], created_at=r["created_at"],
        updated_at=r["updated_at"])


def _event(r: sqlite3.Row) -> WebhookEvent:
    return WebhookEvent(
        event_id=r["event_id"], event_type=r["event_type"],
        received_at=r["received_at"],
        signature_valid=bool(r["signature_valid"]), payload=r["payload"],
        processed_at=r["processed_at"], result=r["result"],
        mandate_id=r["mandate_id"], attempt_id=r["attempt_id"])
