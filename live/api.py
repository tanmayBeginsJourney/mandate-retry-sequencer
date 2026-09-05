"""The HTTP surface: one webhook endpoint, a small operator API, the console.

WHAT IS DELIBERATELY ABSENT IS THE MOST IMPORTANT THING ON THIS PAGE. There is
no `POST /charge`, no endpoint that takes an amount, and none that takes a
token id. The only route that can move money is
`POST /api/mandates/{id}/decide`, which runs the whole chain and READS NO BODY:
the amount is the mandate's, the timing is the belief filter's, the legality is
Stage 0's. A caller can ask the system to think; it cannot tell it what to do.

That is not defensive habit. A generic charge endpoint would make every
guarantee here conditional on nobody calling it -- including the guarantee that
an LLM cannot pick a debit amount, because an LLM with an HTTP client and a
generic endpoint has picked one.

THE WEBHOOK ENDPOINT is the only unauthenticated write route, because Razorpay
cannot present an operator token. Its authentication IS the signature. It
answers in two phases: verify, record, return -- then interpret. Razorpay
allows five seconds and resends anything it does not see acknowledged, so a
handler that updated beliefs inline would earn a duplicate for every slow tick.

IDENTIFIERS COME BACK SHORTENED (`pay_…8f2a`) unless the caller both
authenticates and asks for `reveal=1`. A console gets screenshotted; a real
customer's payment id has no reason to be legible by default, and the full
value is one query parameter away.

WEBHOOK PAYLOADS ARE NEVER RETURNED. They carry the customer's email and phone
number and are stored only so a signature dispute can be settled.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from live.config import LiveConfig
from live.domain import ATTEMPT_UNRESOLVED
from live.service import LiveError, LiveService
from live.webhooks import (EVENT_ID_HEADER, MAX_BODY_BYTES, SIGNATURE_HEADER,
                           WebhookRejected)

CONSOLE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "console")

#: Bodies larger than this are refused before being read into memory. Operator
#: requests are a few hundred bytes; the webhook cap is separate and larger.
MAX_API_BODY = 64 * 1024

#: How much of an over-long body to read and throw away before answering 413.
#: A server that refuses without draining leaves the client mid-write and it
#: sees a connection reset rather than the status -- which a webhook sender
#: reports as an outage. Past this ceiling the connection is closed instead.
DRAIN_CEILING = 8 * 1024 * 1024
_DRAIN_CHUNK = 64 * 1024

#: Razorpay's identifier shapes. Matched so redaction is a property of the
#: value rather than of the field name -- a new field carrying a payment id
#: gets redacted without anyone remembering to add it to a list.
_ID_RE = re.compile(r"^((?:pay|order|cust|token|plink|notification|evt)_)"
                    r"([A-Za-z0-9]{6,})$")


def redact(value):
    """Shorten a provider identifier, recursively, leaving everything else."""
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        mo = _ID_RE.match(value)
        if mo:
            return f"{mo.group(1)}…{mo.group(2)[-4:]}"
    return value


class Api:
    """Routing and serialisation. Holds a service; owns no business rule."""

    def __init__(self, service: LiveService):
        self.svc = service
        self.config: LiveConfig = service.config

    # ------------------------------------------------------------ security
    #
    # THE BROWSER THREAT MODEL, STATED. The operator API listens on localhost.
    # A page on any origin the operator happens to have open can send it a
    # cross-site request, and the browser attaches no token but does send the
    # request. Two things stop that, and they cover different cases:
    #
    #   * A CONFIGURED OPERATOR TOKEN. It travels in `X-Operator-Token` or
    #     `Authorization`, and a custom header makes a cross-site `fetch`
    #     preflight. No CORS headers are ever sent, so the preflight fails and
    #     the request is never made. `config.load` requires a token whenever
    #     live debits are authorised, so the one configuration that can move
    #     real money always has this.
    #   * THE ORIGIN CHECK BELOW. Not every cross-site request preflights: a
    #     form post, or `fetch` with a `text/plain` body, is a "simple request"
    #     and is delivered. Those carry an `Origin` the browser sets and the
    #     page cannot forge, so a mutating route refuses when it names anything
    #     but this server. Non-browser callers -- curl, a script, Razorpay --
    #     send no Origin and are unaffected.
    #
    # CORS is not the defence. Disabling CORS stops a page READING the reply;
    # it does not stop the request arriving, and the request is what debits.

    def authenticated(self, headers) -> bool:
        """True when the caller may use the operator API.

        With no `RECOVERY_OPERATOR_TOKEN` the API is open. That is confined to
        a read-only, offline or loopback service: `config.load` refuses to
        authorise live debits without a token, and `server.py` refuses to bind
        a non-loopback address without one.
        """
        if not self.config.operator_token:
            return True
        supplied = (headers.get("X-Operator-Token")
                    or (headers.get("Authorization") or "").removeprefix(
                        "Bearer ").strip())
        if not supplied:
            return False
        return hmac.compare_digest(supplied, self.config.operator_token)

    @staticmethod
    def same_origin(headers) -> bool:
        """Is this mutating request from our own page, or from another site?

        An absent `Origin` is allowed: browsers set it on every cross-origin
        request, so absence means the caller is not a browser. A present one
        must match `Host`.
        """
        origin = (headers.get("Origin") or "").strip()
        if not origin:
            return True
        host = (headers.get("Host") or "").strip()
        return origin.rsplit("//", 1)[-1] == host and bool(host)

    def may_reveal(self, headers) -> bool:
        """Full provider identifiers require real authentication.

        Not merely "the request got this far". With no token configured
        `authenticated` is True for everybody, and that used to be enough to
        unredact a live customer's payment and token ids. A service that has
        not been given an operator token cannot tell who is asking, so it does
        not hand out identifiers.
        """
        return bool(self.config.operator_token) and self.authenticated(headers)

    # -------------------------------------------------------------- routes
    def get(self, path: str, query: dict, headers) -> tuple[int, dict]:
        if path == "/health":
            return 200, self.svc.health()
        if path == "/ready":
            # Readiness is about THIS process serving, not about Razorpay being
            # up: a provider outage must not make a running service look dead
            # to a load balancer. It is shown on the console instead.
            counts = self.svc.store.summary()
            return 200, {"ready": True,
                         "unprocessed_events": counts["events_unprocessed"],
                         "unresolved_attempts": counts["attempts_unresolved"]}

        if not self.authenticated(headers):
            return 401, {"error": "operator token required"}
        reveal = query.get("reveal") == "1" and self.may_reveal(headers)

        # TWO READ ROUTES, NOT SIX. `/api/state` is everything the console
        # renders in one request and `/api/mandates/{id}` is the detail behind
        # a selection; separate list routes for mandates, events and decisions
        # were strict subsets of the first with no caller.
        if path == "/api/state":
            return 200, self._maybe(self._state(), reveal)
        if path.startswith("/api/mandates/"):
            m = self.svc.store.mandate(path.rsplit("/", 1)[-1])
            if m is None:
                return 404, {"error": "no such mandate"}
            return 200, self._maybe(self._mandate_detail(m), reveal)
        if path == "/api/connectivity":
            # The one read-only provider call. Charges nothing, creates
            # nothing, and is how an operator checks a live credential without
            # spending money to find out.
            return 200, self.svc.connectivity()
        return 404, {"error": "no such route"}

    def post(self, path: str, body: dict, headers) -> tuple[int, dict]:
        if not self.authenticated(headers):
            return 401, {"error": "operator token required"}
        if not self.same_origin(headers):
            return 403, {"error": "cross-site request refused"}
        try:
            if path == "/api/customers":
                c = self.svc.create_customer(
                    name=str(body.get("name") or "").strip(),
                    email=str(body.get("email") or "").strip(),
                    contact=str(body.get("contact") or "").strip())
                return 201, {"customer": self._customer(c)}

            if path == "/api/mandates":
                # `frequency` is passed through ONLY when the caller sent one.
                # Its default is the service's, so there is one place that
                # decides what an unspecified mandate registers as -- and this
                # module cannot import the provider layer to name it (gate L1).
                extra = ({"frequency": str(body["frequency"]).strip()}
                         if str(body.get("frequency") or "").strip() else {})
                m = self.svc.start_registration(
                    customer_id=str(body.get("customer_id") or ""),
                    charge_amount_paise=int(body.get("charge_amount_paise") or 0),
                    max_amount_paise=int(body.get("max_amount_paise") or 0),
                    est_salary=float(body.get("est_salary") or 0),
                    est_payday=int(body.get("est_payday") or 1),
                    cycle_days=int(body.get("cycle_days") or 30), **extra)
                return 201, {"mandate": self._mandate(m)}

            if path.startswith("/api/mandates/"):
                mid, _, action = path[len("/api/mandates/"):].partition("/")

                if action == "confirm":
                    m = self.svc.confirm_registration(
                        mid, str(body.get("payment_id") or ""))
                    return 200, {"mandate": self._mandate(m)}

                if action == "mock-authorize":
                    m = self.svc.mock_authorize(mid)
                    self.svc.deliver_mock_webhooks()
                    return 200, {"mandate": self._mandate(m)}

                if action == "decide":
                    # NO BODY IS READ. The amount is the mandate's, the time is
                    # the scheduler's, the legality is Stage 0's. There is
                    # nothing for a caller to supply and nothing to inject.
                    d = self.svc.decide(mid)
                    # The mock rail queues the webhooks a real one would send,
                    # and they go through the same verification and ingestion
                    # code here rather than being applied directly.
                    self.svc.deliver_mock_webhooks()
                    return 200, {"decision": redact(d.as_dict())}

                if action == "cancel":
                    m = self.svc.cancel_mandate(mid)
                    self.svc.deliver_mock_webhooks()
                    return 200, {"mandate": self._mandate(m)}

            if path == "/api/demo/advance":
                # OFFLINE ONLY -- `advance_clock` refuses in live mode. It lets
                # a demonstration watch a scheduler that reasons in days.
                #
                # WITH NO `hours` THE SERVICE PICKS THE STEP. It knows the next
                # hour at which a tick would do something different -- a
                # scheduled debit's target, or the day boundary the belief
                # advances at -- and a fixed step spends clicks on hours whose
                # answer is already on screen. A caller that names `hours` gets
                # exactly that many; the console names none.
                hours = (int(body["hours"]) if body.get("hours")
                         else self.svc.next_decision_hour())
                offset = self.svc.advance_clock(hours)
                decisions = []
                for m in self.svc.store.mandates():
                    if m.chargeable:
                        decisions.append(redact(
                            self.svc.decide(m.id).as_dict()))
                self.svc.deliver_mock_webhooks()
                return 200, {"clock_offset_h": offset,
                             "now_t": self.svc.now_t(),
                             "decisions": decisions}

            if path == "/api/reconcile":
                return 200, {"reconciled": redact(self.svc.reconcile())}
        except LiveError as e:
            # Every message on this path is written for an operator to read and
            # carries no credential; `LiveError` exists to mark exactly that.
            return 400, {"error": str(e)}
        except (TypeError, ValueError) as e:
            return 400, {"error": f"invalid request: {e}"}
        return 404, {"error": "no such route"}

    # -------------------------------------------------------- webhook path
    def webhook(self, raw: bytes, headers) -> tuple[int, dict]:
        """Verify, record, return. Interpretation happens after the response."""
        try:
            res = self.svc.handle_webhook(
                raw,
                headers.get(SIGNATURE_HEADER, ""),
                headers.get(EVENT_ID_HEADER, ""))
        except WebhookRejected as e:
            return e.status, {"error": e.reason}
        return 200, {"received": True, "duplicate": res.duplicate,
                     "event": res.event_type}

    # ------------------------------------------------------- serialisation
    @staticmethod
    def _maybe(payload: dict, reveal: bool) -> dict:
        return payload if reveal else redact(payload)

    def _state(self) -> dict:
        snap = self.svc.snapshot()
        snap["mandates"] = [self._mandate(m) for m in self.svc.store.mandates()]
        snap["recent_attempts"] = [self._attempt(a) for a
                                   in self.svc.store.recent_attempts(10)]
        snap["recent_events"] = self._events(10)
        # DELIVERIES THAT FAILED VERIFICATION, which `recent_events` cannot
        # show: a forged or corrupted delivery never becomes a `WebhookEvent`
        # (see `domain.WebhookEvent` for why it is refused the dedup key), so
        # until now the console could see the COUNT in `counts` and never the
        # rows. `store.recent_rejected` selects four columns and not the
        # payload -- the body an unauthenticated sender posted is kept for a
        # signature dispute and is not console material.
        snap["rejected_events"] = self.svc.store.recent_rejected(10)
        snap["decisions"] = [d.as_dict() for d
                             in reversed(self.svc.decisions[-10:])]
        return snap

    @staticmethod
    def _customer(c) -> dict:
        return {"id": c.id, "seq": c.seq, "name": c.name, "email": c.email,
                "contact": c.contact, "rzp_customer_id": c.rzp_customer_id,
                "created_at": c.created_at}

    def _mandate(self, m) -> dict:
        c = self.svc.store.customer(m.customer_id)
        attempts = self.svc.store.attempts_for(m.id, limit=50)
        return {
            "id": m.id, "state": m.state.value, "token_status": m.token_status,
            "chargeable": m.chargeable,
            # WITH THIS MANDATE'S ATTEMPTS, so an attempt recorded and never
            # sent is reported. A mandate carrying one cannot be charged --
            # `_decide`'s `open_now` guard refuses every tick on it -- and
            # `blocked_because` must never be empty on a mandate that cannot
            # actually be charged.
            "blocked_because": m.refusal_reason(attempts),
            # HOW MANY OF THIS MANDATE'S DEBITS ARE STILL IN FLIGHT. The list
            # payload otherwise carries no attempt state at all, so the console
            # cannot tell a settled mandate from one awaiting an outcome
            # without fetching every detail page.
            "unresolved_attempts": sum(1 for a in attempts
                                       if a.state in ATTEMPT_UNRESOLVED),
            "customer_id": m.customer_id,
            "customer_name": c.name if c else "",
            "uid": f"c{c.seq}m{m.index_no}" if c else "",
            "rzp_token_id": m.rzp_token_id,
            "rzp_customer_id": m.rzp_customer_id,
            "registration_order_id": m.registration_order_id,
            "registration_payment_id": m.registration_payment_id,
            "charge_amount_paise": m.charge_amount_paise,
            "max_amount_paise": m.max_amount_paise,
            "frequency": m.frequency, "expire_at": m.expire_at,
            "cycle": m.cycle, "cycle_days": m.cycle_days,
            "cycle_start_t": m.cycle_start_t,
            # THE RECOVERY LADDER'S STATE. Durable, per-cycle, and the only
            # way an operator can see that a Payment Link is standing in for
            # the fourth mandate debit. `backup_vendor_id` is a `plink_` id
            # and is shortened by `redact` like every other provider id.
            "backup_status": m.backup_status,
            "backup_vendor_id": m.backup_vendor_id,
            "reminders_sent": m.reminders_sent,
            "halted_cycle": m.halted_cycle,
            "est_salary": m.est_salary, "est_payday": m.est_payday,
            "created_at": m.created_at, "updated_at": m.updated_at,
        }

    def _mandate_detail(self, m) -> dict:
        out = self._mandate(m)
        out["attempts"] = [self._attempt(a) for a
                           in self.svc.store.attempts_for(m.id)]
        out["transitions"] = self.svc.store.transitions_for("mandate", m.id)
        return out

    def _attempt(self, a) -> dict:
        return {
            "id": a.id, "mandate_id": a.mandate_id, "mandate_uid": a.mandate_uid,
            "state": a.state.value, "amount_paise": a.amount_paise,
            "order_id": a.order_id, "payment_id": a.payment_id,
            "receipt": a.receipt, "outcome_code": a.outcome_code,
            "raw_reason": a.raw_reason, "target_t": a.target_t,
            # THE HOUR THE SCHEDULER ACTUALLY NOTIFIED AT, served rather than
            # derived. `target_t - 24` is not it -- the peak-hour rule pushes a
            # target past the first legal slot -- and the console must not
            # reconstruct an NPCI figure it can be handed.
            "notify_t": a.notify_t,
            # WHETHER THE BELIEF IN THIS PROCESS HAS READ THIS OUTCOME, which
            # `state` does not answer. The attempt row is durable and the
            # filter is not, so a restarted service serves resolved attempts it
            # has never folded in.
            "folded_in_session": self.svc.folded_in_session(a.id),
            "payment_after": a.payment_after, "submitted_at": a.submitted_at,
            "resolved_at": a.resolved_at, "conflicted": a.conflicted,
            "cycle": a.cycle, "created_at": a.created_at,
            "transitions": self.svc.store.transitions_for("attempt", a.id),
        }

    def _events(self, limit: int = 30) -> list[dict]:
        """Webhook rows WITHOUT their payloads.

        The payload is the raw body Razorpay sent; it carries the customer's
        email and contact and exists only so a signature dispute can be
        settled.
        """
        return [{"event_id": e.event_id, "event_type": e.event_type,
                 "received_at": e.received_at, "processed_at": e.processed_at,
                 "result": e.result, "mandate_id": e.mandate_id,
                 "attempt_id": e.attempt_id}
                for e in self.svc.store.recent_events(limit)]


def make_handler(api: Api):
    """A request handler class bound to one `Api`."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "recovery-agent"

        #: No stderr access log: it prints the request line, and a request line
        #: can carry an operator token if somebody ever puts one in a query.
        def log_message(self, fmt, *args):
            pass

        # ---- helpers
        def _json(self, status: int, payload) -> None:
            body = json.dumps(payload, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _static(self, rel: str) -> None:
            safe = os.path.normpath(rel).lstrip("\\/")
            full = os.path.join(CONSOLE_DIR, safe)
            if not os.path.abspath(full).startswith(os.path.abspath(CONSOLE_DIR)):
                self._json(403, {"error": "forbidden"})
                return
            if not os.path.isfile(full):
                self._json(404, {"error": "not found"})
                return
            ctype = {".html": "text/html; charset=utf-8",
                     ".css": "text/css; charset=utf-8",
                     ".js": "text/javascript; charset=utf-8",
                     ".svg": "image/svg+xml"}.get(
                         os.path.splitext(full)[1], "application/octet-stream")
            with open(full, "rb") as fh:
                data = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            # The console loads no third-party script and no remote font, so
            # the policy can be this tight.
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
                "form-action 'none'; frame-ancestors 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(data)

        def _split(self):
            path, _, raw_query = self.path.partition("?")
            query = {}
            for part in raw_query.split("&"):
                if "=" in part:
                    k, _, v = part.partition("=")
                    query[k] = v
            return path.rstrip("/") or "/", query

        def _read(self, cap: int) -> bytes | None:
            """The request body, or None if it is over `cap`.

            An over-long body is drained before the caller answers 413, so the
            client receives the status rather than a reset. See DRAIN_CEILING.
            """
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None
            if length > cap:
                remaining = min(length, DRAIN_CEILING)
                while remaining > 0:
                    chunk = self.rfile.read(min(_DRAIN_CHUNK, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                if length > DRAIN_CEILING:
                    self.close_connection = True
                return None
            return self.rfile.read(length) if length else b""

        # ---- verbs
        #
        # A ROUTE THAT RAISES MUST STILL ANSWER. An unhandled exception here
        # leaves `BaseHTTPRequestHandler` to close the socket, and the caller
        # sees a dropped connection -- which reads as the service being down,
        # not as one broken request. The wrapper answers 500 with the exception
        # TYPE and nothing else: the message can carry a provider payload, and
        # payloads carry a customer's email and contact.
        def _guarded(self, fn):
            try:
                fn()
            except Exception as e:                  # noqa: BLE001
                self._json(500, {"error": f"internal error: {type(e).__name__}"})

        def do_GET(self):
            self._guarded(self._get)

        def _get(self):
            path, query = self._split()
            if path == "/" or path == "/index.html":
                self._static("index.html")
                return
            if path in ("/app.js", "/app.css"):
                self._static(path.lstrip("/"))
                return
            status, payload = api.get(path, query, self.headers)
            self._json(status, payload)

        def do_POST(self):
            self._guarded(self._post)

        def _post(self):
            path, _ = self._split()

            if path == "/webhooks/razorpay":
                raw = self._read(MAX_BODY_BYTES)
                if raw is None:
                    self._json(413, {"error": "webhook body too large"})
                    return
                status, payload = api.webhook(raw, self.headers)
                self._json(status, payload)
                # AFTER the response. Razorpay allows five seconds and resends
                # anything unacknowledged, so interpretation -- which touches
                # the belief filter -- must not be inside the request. The
                # event is durable, so a crash here is recovered by replaying
                # `unprocessed_events` at startup.
                if status == 200:
                    try:
                        api.svc.process_webhooks()
                    except Exception:               # noqa: BLE001
                        # It stays unprocessed and is retried at the next
                        # ingest or restart. Razorpay has its 200 and must not
                        # be made to resend for our bug.
                        pass
                return

            raw = self._read(MAX_API_BODY)
            if raw is None:
                self._json(413, {"error": "request body too large"})
                return
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
                if not isinstance(body, dict):
                    raise ValueError
            except (ValueError, UnicodeDecodeError):
                self._json(400, {"error": "body must be a JSON object"})
                return
            status, payload = api.post(path, body, self.headers)
            self._json(status, payload)

    return Handler


class Server:
    """A threading HTTP server around one `Api`."""

    def __init__(self, service: LiveService, host: str = "127.0.0.1",
                 port: int = 8730):
        self.api = Api(service)
        self.httpd = ThreadingHTTPServer((host, port), make_handler(self.api))
        self.host, self.port = self.httpd.server_address[:2]
        self._thread: threading.Thread | None = None

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def start_background(self) -> "Server":
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        time.sleep(0.05)        # so an immediate caller does not race the bind
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
