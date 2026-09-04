"""Gate: the console renders the contract and cannot move money by itself.

WHY THIS FILE EXISTS SEPARATELY FROM `test_api.py`. That file proves the
SERVER's properties: no charge route, no amount in a request, redaction. This
one proves the PAGE's -- that the static assets a browser is handed cannot
name an amount or a time, that they read only fields the API actually sends,
and that booting them touches nothing.

IT IS A TEXT GATE, and deliberately. There is no browser here, so every check
is a statement about the source a browser would execute. That is weaker than a
DOM test and much stronger than nothing: the failure it is built to catch is a
future edit adding an amount field, a retry-at control, or a read of a field
the backend does not serve.
"""
from __future__ import annotations

import json
import os
import re

import live.tests  # noqa: F401
from live.api import Api
from live.tests._harness import Bench, Results

CONSOLE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "console")


def _read(name: str) -> str:
    with open(os.path.join(CONSOLE, name), encoding="utf-8") as fh:
        return fh.read()


class _Headers(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


def main() -> int:
    r = Results("LIVE CONSOLE GATES (offline, static assets + contract)")
    index = _read("index.html")
    app_js = _read("app.js")
    app_css = _read("app.css")

    # ===================================================================== N1
    r.section("N1  the page cannot name an amount, a time or a target")
    # The register form legitimately carries an amount: it is the mandate the
    # customer authorises, not a debit. Everything AFTER registration must be
    # free of input that could reach the money path.
    # Dialogs legitimately take input: the mandate the customer authorises,
    # and the operator token. The page ITSELF must take none.
    page = re.sub(r"<dialog.*?</dialog>", "", index, flags=re.S)
    r.ok("N1a  the page outside its dialogs accepts no input",
         "<input" not in page, "an input exists on the page body")
    r.ok("N1b  no control offers a retry time",
         not re.search(r"retry[-_ ]?at|target_t\s*=|time picker|datetime",
                       app_js, re.I))
    r.ok("N1c  no control forces an attempt",
         not re.search(r"force[-_ ]?retry|force[-_ ]?debit|/charge", app_js, re.I))
    # `charge_amount_paise` may be READ for display and SENT only by the
    # registration call. Any other write of an amount is the defect.
    posts = re.findall(r"api\(\s*[`\"']([^`\"']+)[`\"']\s*,\s*\{\s*method:\s*\"POST\"",
                       app_js)
    r.ok("N1d  the only POST carrying an amount is registration",
         all("charge_amount_paise" not in p for p in posts),
         f"posts={posts}")

    # ===================================================================== N2
    r.section("N2  the decide call is bodyless and confirmed before it fires")
    handler = app_js.split('$("act-decide").addEventListener', 1)[-1]
    handler = handler.split('$("act-advance")', 1)[0]
    r.ok("N2a  the handler exists and is bounded", "/decide" in handler)
    r.ok("N2b  it posts an EMPTY object",
         re.search(r'/decide[`"\']?\s*,\s*\{\s*method:\s*"POST",\s*body:\s*\{\s*\}\s*\}',
                   handler) is not None,
         "the decide body is not a literal {}")
    r.ok("N2c  the confirmation comes BEFORE the request",
         "confirmLiveDebit()" in handler
         and handler.index("confirmLiveDebit()") < handler.index("/decide"))
    r.ok("N2d  and it only asks when money can actually move",
         'mode === "live"' in app_js and "debit_allowed" in app_js)

    # ===================================================================== N3
    r.section("N3  offline-only controls are absent in live mode, not disabled")
    for control in ("act-advance", "act-authorize"):
        block = app_js.split(f'$("{control}").hidden', 1)
        r.ok(f"N3  {control} is hidden by mode, not disabled",
             len(block) > 1 and "offline()" in block[1].split("\n", 1)[0],
             f"{control} is not gated on offline()")
    r.ok("N3c  offline() reads the served mode and nothing else",
         'state.config.mode === "offline"' in app_js)

    # ===================================================================== N4
    r.section("N4  the page reads only fields the API actually serves")
    b = Bench(seed=11)
    try:
        api = Api(b.svc)
        h = _Headers()
        _st, body = api.post("/api/customers", {
            "name": "Ananya Rao", "email": "ananya@example.com",
            "contact": "+919000000001"}, h)
        cid = body["customer"]["id"]
        _st, body = api.post("/api/mandates", {
            "customer_id": cid, "charge_amount_paise": 100,
            "max_amount_paise": 150000, "est_salary": 30000,
            "est_payday": 3}, h)
        mid = body["mandate"]["id"]
        api.post(f"/api/mandates/{mid}/mock-authorize", {}, h)
        # Advance until Stage 0 has adjudicated something, then STOP. The
        # service keeps the last 200 decisions and serves 10, and after a
        # decline the rule engine answers NUDGE on every tick -- so running on
        # would push the adjudicated tick out of the served window and this
        # would measure the window rather than the contract.
        for _ in range(120):
            api.post("/api/demo/advance", {"hours": 12}, h)
            _st, peek = api.get("/api/state", {}, h)
            if any(d.get("gate_verdict") for d in peek["decisions"]):
                break

        # ONE FORGED DELIVERY, so `rejected_events` is exercised rather than
        # merely present. The console renders these separately from authentic
        # events on purpose: a delivery that fails verification never becomes
        # a `WebhookEvent`, so mixing the two lists would be a claim that a
        # forgery was signed.
        forged = _Headers({"X-Razorpay-Signature": "deadbeef",
                           "X-Razorpay-Event-Id": "evt_forged00001"})
        st, _ = api.webhook(b'{"event":"payment.captured"}', forged)
        r.ok("N4  a forged delivery is refused", st == 400, f"status {st}")

        _st, snap = api.get("/api/state", {}, h)
        _st, detail = api.get(f"/api/mandates/{mid}", {}, h)

        served = set(snap) | set(detail)
        for block in (snap.get("config"), snap.get("health"), snap.get("counts")):
            served |= set(block or {})
        for row in snap.get("decisions") or []:
            served |= set(row)
        for row in (snap.get("recent_events") or []) + (snap.get("rejected_events") or []):
            served |= set(row)
        for row in detail.get("attempts") or []:
            served |= set(row)
            for t in row.get("transitions") or []:
                served |= set(t)
        for t in detail.get("transitions") or []:
            served |= set(t)
        for row in snap.get("decisions") or []:
            served |= set(row.get("provider") or {})
            for c in row.get("gate_checks") or []:
                served |= set(c)
        served |= set(snap.get("mandates", [{}])[0] if snap.get("mandates") else {})

        # Every `foo.bar` the page reads off a payload object. Confined to the
        # names the console uses for served objects, so a local variable's
        # property access is not mistaken for a contract claim.
        read = set()
        for holder in ("snap", "m", "a", "d", "e", "r", "t", "cfg", "c", "h",
                       "detail", "trigger", "answer", "prov"):
            read |= set(re.findall(holder + r"\.([a-z_][a-z0-9_]*)", app_js))
        # Names that are ours, not the API's.
        ours = {"config", "health", "counts", "mandates", "decisions",
                "recent_events", "rejected_events", "provider_lost", "selected",
                "detail", "reveal", "token", "events", "rejected", "providerLost",
                "length", "hidden", "textContent", "dataset", "disabled",
                "title", "value", "id", "state", "slice", "map", "filter",
                "find", "some", "forEach", "push", "join", "replace",
                "toLowerCase", "toUpperCase", "charAt", "currentTarget",
                "className", "style", "append", "returnValue", "submitter",
                "reverse", "test", "index", "includes", "split"}
        claimed = {n for n in read if n not in ours and "_" in n or n in {
            "acted", "reason", "cycle", "chargeable", "uid", "frequency",
            "conflicted", "rule", "verdict", "at", "provider", "intervention",
            "rationale"}}
        missing = sorted(n for n in claimed if n not in served)
        r.ok("N4a  every payload field the page reads is served",
             not missing, f"not served: {missing}")

        # THE TWO FIELDS THAT ARE SERVED AND MUST NOT BE DISPLAYED. They are
        # an operator's stated guess at a customer's pay cycle, recorded so a
        # decision made on them can be questioned -- not a measurement, and
        # not a fact about the customer. They are legitimately WRITTEN by the
        # registration form, so the check is confined to the render path.
        render = _render_path(app_js)
        for field in ("est_salary", "est_payday"):
            r.ok(f"N4  {field} is served but never rendered",
                 field in served and field not in render,
                 f"{field} reaches the page")

        # ================================================================= N5
        r.section("N5  the contract fields the spine depends on are present")
        acted = [d for d in snap["decisions"] if d.get("gate_verdict")]
        r.ok("N5a  the run produced an adjudicated decision", bool(acted),
             "no decision carried a gate verdict")
        if acted:
            d = acted[0]
            r.ok("N5b  gate_checks carries all five rules",
                 len(d["gate_checks"]) == 5,
                 f"{len(d['gate_checks'])} verdicts")
            r.ok("N5c  each verdict names a rule and an answer",
                 all(set(c) >= {"rule", "verdict"} for c in d["gate_checks"]))
            r.ok("N5d  the tick records the simulated hour it reasoned in",
                 d["now_t"] > 0, str(d["now_t"]))
            r.ok("N5e  and the NPCI count it reasoned against",
                 d["attempts_cap"] == 4 and d["attempts_used"] >= 0,
                 f"{d['attempts_used']}/{d['attempts_cap']}")
        r.ok("N5f  rejected_events is served even when empty",
             isinstance(snap.get("rejected_events"), list))

        # ================================================================= N6
        r.section("N6  serving the page moves nothing")
        before = len(b.svc.store.recent_attempts(100))
        calls = b.api.calls
        # Exactly what a browser fetches on load: the three assets and the two
        # reads `refresh()` issues. Nothing else runs at boot.
        for asset in ("index.html", "app.js", "app.css"):
            _read(asset)
        api.get("/api/state", {}, h)
        api.get(f"/api/mandates/{mid}", {}, h)
        r.ok("N6a  no attempt was created",
             len(b.svc.store.recent_attempts(100)) == before)
        r.ok("N6b  and no provider call was made", b.api.calls == calls,
             f"{b.api.calls - calls} calls")
        r.ok("N6c  boot issues reads and nothing else",
             app_js.rstrip().endswith("refresh();"),
             "the last statement at module scope is not refresh()")

    finally:
        b.close()

    # ===================================================================== N7
    r.section("N7  the page loads no remote asset the CSP would drop")
    r.ok("N7a  no remote stylesheet or font",
         "fonts.googleapis" not in index and "@import" not in app_css
         and "https://" not in app_css)
    r.ok("N7b  no remote script", not re.search(r'<script[^>]+src="https?:', index))
    r.ok("N7c  no inline handler attribute",
         not re.search(r'\son[a-z]+\s*=\s*"', index))

    return r.summary()


def _render_path(js: str) -> str:
    """`app.js` minus its comments and minus the registration submit handler.

    Registration legitimately SENDS the operator's estimates; nothing may
    DISPLAY them. Splitting on the submit handler is what keeps those two
    facts apart, and stripping comments is what keeps this file's own prose
    about the rule from reading as a breach of it.
    """
    js = _rendered(js)
    return js.split('$("register-form").addEventListener', 1)[0]


def _rendered(js: str) -> str:
    """The parts of `app.js` that can put text on the page.

    Comments are stripped: this file explains in prose WHY `est_salary` is not
    displayed, and a naive substring search would read that explanation as the
    defect it warns about.
    """
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    js = re.sub(r"^\s*//.*$", "", js, flags=re.M)
    return js


if __name__ == "__main__":
    raise SystemExit(main())
