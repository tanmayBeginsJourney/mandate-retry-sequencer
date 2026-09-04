"""C-gates: the mode switch fails closed in both directions.

The two ways to get this wrong are not symmetric and both have happened in real
systems. Configuring LIVE with a missing credential and quietly falling back to
a mock means a deployment that looks healthy and collects nothing. Configuring
OFFLINE and reaching the network means a test suite that charges customers.
Every gate here is one of those two directions.
"""
from __future__ import annotations

import live.tests  # noqa: F401
from agent.execution.razorpay_api import RazorpayApi
from agent.execution.razorpay_mock import MockRazorpayApi
from live.config import ConfigError, Mode, load
from live.service import LiveService
from live.tests._harness import Results

LIVE_ENV = {"RECOVERY_MODE": "live",
            "RAZORPAY_KEY_ID": "rzp_live_XXXXXXXXXXXX",
            "RAZORPAY_KEY_SECRET": "s" * 24,
            "RAZORPAY_WEBHOOK_SECRET": "w" * 24}


def _raises(fn) -> ConfigError | None:
    try:
        fn()
    except ConfigError as e:
        return e
    return None


def main() -> int:
    r = Results("LIVE CONFIGURATION GATES (offline)")

    r.section("C1  an unset mode is offline, and a misspelled one is an error")
    r.ok("C1a  no RECOVERY_MODE means offline",
         load({}).mode is Mode.OFFLINE)
    err = _raises(lambda: load({"RECOVERY_MODE": "production"}))
    r.ok("C1b  an unrecognised mode raises rather than guessing",
         err is not None, str(err or "")[:60])
    err = _raises(lambda: load({"RECOVERY_MODE": "LIVE", **{
        k: v for k, v in LIVE_ENV.items() if k != "RECOVERY_MODE"}}))
    r.ok("C1c  case does not change the meaning of a mode", err is None)

    r.section("C2  LIVE without a credential fails closed, never demotes")
    for missing in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET",
                    "RAZORPAY_WEBHOOK_SECRET"):
        env = {k: v for k, v in LIVE_ENV.items() if k != missing}
        err = _raises(lambda e=env: load(e))
        r.ok(f"C2   missing {missing} raises", err is not None,
             (str(err) if err else "NO ERROR -- it fell back")[:70])
    err = _raises(lambda: load({**LIVE_ENV, "RAZORPAY_KEY_ID": "not-a-key"}))
    r.ok("C2d  a key that is not a Razorpay key id raises", err is not None)

    r.section("C3  the debit switch is separate from the mode switch")
    cfg = load(LIVE_ENV)
    allowed, why = cfg.may_debit()
    r.ok("C3a  live mode alone does NOT permit a debit", allowed is False, why)
    # AUTHORISING DEBITS ALSO REQUIRES AN OPERATOR TOKEN. The route that runs a
    # decision is the route that debits, so an open operator API and permitted
    # live debits together are a real debit available to anything that reaches
    # the port.
    err = _raises(lambda: load({**LIVE_ENV, "RECOVERY_LIVE_DEBIT": "yes"}))
    r.ok("C3b  the debit flag without an operator token raises",
         err is not None, str(err or "")[:70])
    ARMED = {**LIVE_ENV, "RECOVERY_LIVE_DEBIT": "yes",
             "RECOVERY_OPERATOR_TOKEN": "t0ken"}
    cfg2 = load(ARMED)
    r.ok("C3b2 live plus the flag plus a token permits one",
         cfg2.may_debit()[0] is True)
    r.ok("C3b3 and the console is told authentication is required",
         cfg2.describe()["operator_auth_required"] is True)
    # ONLY THE LITERAL WORD `yes`. `1`, `true` and `on` all arrive by
    # accident; `yes` is a word somebody typed on purpose. Checked over the
    # whole set at once so the failure names the spelling that got through.
    accidental = [v for v in ("1", "true", "on", "y", "Y", "enabled", "sure")
                  if load({**ARMED, "RECOVERY_LIVE_DEBIT": v}).may_debit()[0]]
    r.ok("C3c  no truthy-looking value but 'yes' enables live debits",
         not accidental, f"these did: {accidental}")
    r.ok("C3c2 and surrounding whitespace and case do not defeat it",
         load({**ARMED, "RECOVERY_LIVE_DEBIT": " YES "}).may_debit()[0]
         is True)
    err = _raises(lambda: load({**ARMED,
                                "RAZORPAY_KEY_ID": "rzp_test_XXXXXXXXXXXX"}))
    r.ok("C3d  authorising real debits against a TEST key raises",
         err is not None, str(err or "")[:70])
    err = _raises(lambda: load({"RECOVERY_MODE": "offline",
                                "RECOVERY_LIVE_DEBIT": "yes"}))
    r.ok("C3e  the debit flag in offline mode raises rather than being ignored",
         err is not None)

    r.section("C4  the amount ceiling is real and is validated")
    err = _raises(lambda: load({"RECOVERY_MAX_DEBIT_PAISE": "50"}))
    r.ok("C4a  a ceiling below Razorpay's Rs 1 minimum raises", err is not None)
    err = _raises(lambda: load({"RECOVERY_MAX_DEBIT_PAISE": "lots"}))
    r.ok("C4b  a non-integer ceiling raises", err is not None)

    r.section("C5  the mode chooses the rail, and nothing else does")
    off = LiveService._build_api(load({}))
    r.ok("C5a  offline builds the mock", isinstance(off, MockRazorpayApi))
    on = LiveService._build_api(load(LIVE_ENV))
    r.ok("C5b  live builds the real client", isinstance(on, RazorpayApi))
    r.ok("C5c  the mock has no generic HTTP escape hatch",
         _no_raw_http(off), "call() must refuse")

    r.section("C6  describe() shows the mode without showing the secret")
    text = repr(load(LIVE_ENV).describe())
    r.ok("C6a  the key prefix IS shown, because an operator must see it",
         "rzp_live" in text)
    r.ok("C6b  the key id itself is not", "XXXXXXXXXXXX" not in text)
    r.ok("C6c  the key secret is not", "s" * 24 not in text)
    r.ok("C6d  the webhook secret is not", "w" * 24 not in text)

    return r.summary()


def _no_raw_http(mock) -> bool:
    try:
        mock.call("POST", "https://api.razorpay.com/v1/payments")
    except NotImplementedError:
        return True
    except Exception:                                # noqa: BLE001
        return False
    return False


if __name__ == "__main__":
    raise SystemExit(main())
