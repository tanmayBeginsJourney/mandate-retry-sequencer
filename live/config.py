"""Which rail this process talks to, and whether it may move money.

TWO SWITCHES, AND THEY DO DIFFERENT JOBS. `RECOVERY_MODE` picks the rail:
`offline` is `MockRazorpayApi` and cannot reach the network, `live` is
`RazorpayApi` and reaches api.razorpay.com. `RECOVERY_LIVE_DEBIT` decides
whether a debit may actually be submitted while in `live`. They are separate
because reading a mandate, replaying a webhook and rendering the console are
all things you want to do against the live rail WITHOUT charging anybody;
collapsing them would make "look at production" and "take a customer's money"
the same gesture.

THE FAILURE DIRECTION IS FIXED. `live` with a credential missing is an error,
never a quiet demotion to the mock. `offline` can never reach the network,
whatever the environment holds.

NOTHING HERE PRINTS A SECRET. `describe()` reports whether a credential is
present and which key PREFIX is in use, because `rzp_test_` versus `rzp_live_`
is the one thing an operator must see and is not itself a secret.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from enum import Enum

MODE_ENV = "RECOVERY_MODE"
DEBIT_ENV = "RECOVERY_LIVE_DEBIT"

#: Razorpay's documented minimum mandate amount, across every merchant
#: category, is Rs 1. [VERIFIED] razorpay.com UPI AutoPay, read 3 September
#: 2026. A provider floor, so it is encoded; the ceiling below is ours.
PROVIDER_MIN_DEBIT_PAISE = 100

#: Our own ceiling on one live debit, in paise. Not a Razorpay rule -- theirs
#: is the mandate's `max_amount`, which they enforce. This bounds what a bug in
#: the amount path can spend of a real balance, in one environment variable
#: rather than a literal in five files.
#:
#: A SEPARATE PROVIDER CONSTRAINT SITS ABOVE THIS AND IS NOT ENCODED: a UPI
#: AutoPay debit over Rs 15,000 requires the customer to approve it with their
#: UPI PIN, so it cannot complete unattended. [VERIFIED] razorpay.com UPI
#: AutoPay, read 3 September 2026. An operator raising this ceiling past
#: 1,500,000 paise should expect that, not a bug.
DEFAULT_MAX_DEBIT_PAISE = 500


class Mode(str, Enum):
    OFFLINE = "offline"
    LIVE = "live"


class ConfigError(RuntimeError):
    """Configuration is inconsistent. Raised at startup, never mid-request."""


@dataclass(frozen=True)
class LiveConfig:
    mode: Mode
    key_id: str
    key_secret: str
    webhook_secret: str
    debit_authorized: bool
    max_debit_paise: int
    api_base: str
    operator_token: str
    db_path: str

    @property
    def is_live(self) -> bool:
        return self.mode is Mode.LIVE

    @property
    def key_prefix(self) -> str:
        """`rzp_test` / `rzp_live` / `""`. Safe to display; not a secret."""
        parts = self.key_id.split("_")
        return "_".join(parts[:2]) if len(parts) >= 2 else ("" if not self.key_id
                                                            else "unknown")

    def may_debit(self) -> tuple[bool, str]:
        """Is a real debit permitted right now, and if not, why not.

        Returns a reason rather than raising: the console has to SHOW the
        refusal, and an exception is not a display.
        """
        if not self.is_live:
            return True, "offline mode: the mock rail moves no money"
        if not self.debit_authorized:
            return False, (f"{DEBIT_ENV} is not set to 'yes'; live mode is "
                           "read-only until it is")
        return True, "live debit authorised by operator configuration"

    def describe(self) -> dict:
        """Everything the console may know about this configuration."""
        ok, reason = self.may_debit()
        return {"mode": self.mode.value, "key_prefix": self.key_prefix,
                "has_key": bool(self.key_id and self.key_secret),
                "has_webhook_secret": bool(self.webhook_secret),
                "debit_allowed": ok, "debit_reason": reason,
                "max_debit_paise": self.max_debit_paise,
                "api_base": self.api_base,
                "operator_auth_required": bool(self.operator_token)}


def load(env: dict | None = None) -> LiveConfig:
    """Build the configuration, or raise.

    `env` is injectable so the gates can drive every branch without touching
    the process environment.
    """
    src = os.environ if env is None else env

    def get(name: str) -> str:
        return (src.get(name) or "").strip()

    raw_mode = (get(MODE_ENV) or Mode.OFFLINE.value).lower()
    try:
        mode = Mode(raw_mode)
    except ValueError:
        raise ConfigError(f"{MODE_ENV}={raw_mode!r} is not a mode. Use "
                          f"{Mode.OFFLINE.value!r} or "
                          f"{Mode.LIVE.value!r}.") from None

    key_id, key_secret = get("RAZORPAY_KEY_ID"), get("RAZORPAY_KEY_SECRET")
    webhook_secret = get("RAZORPAY_WEBHOOK_SECRET")

    # ONLY THE LITERAL WORD `yes`. Deliberately narrower than the usual truthy
    # set: `1`, `true` and `on` all arrive by accident, copied from another
    # service's compose file or left over from a shell experiment. This flag
    # authorises real debits.
    debit_authorized = get(DEBIT_ENV).lower() == "yes"

    try:
        max_debit = int(get("RECOVERY_MAX_DEBIT_PAISE")
                        or DEFAULT_MAX_DEBIT_PAISE)
    except ValueError:
        raise ConfigError("RECOVERY_MAX_DEBIT_PAISE must be an integer number "
                          "of paise") from None
    if max_debit < PROVIDER_MIN_DEBIT_PAISE:
        raise ConfigError(
            f"RECOVERY_MAX_DEBIT_PAISE={max_debit} is below Razorpay's "
            f"documented minimum of {PROVIDER_MIN_DEBIT_PAISE} paise, so no "
            f"debit could ever be legal.")

    if mode is Mode.LIVE:
        # FAIL CLOSED. Every one of these is a hard stop; none demotes to mock.
        missing = [n for n, v in (("RAZORPAY_KEY_ID", key_id),
                                  ("RAZORPAY_KEY_SECRET", key_secret),
                                  ("RAZORPAY_WEBHOOK_SECRET", webhook_secret))
                   if not v]
        if missing:
            raise ConfigError(
                f"{MODE_ENV}=live but {', '.join(missing)} is not set. Live "
                f"mode does not fall back to the mock provider -- set the "
                f"credentials or run with {MODE_ENV}=offline.")
        if not key_id.startswith(("rzp_live_", "rzp_test_")):
            raise ConfigError("RAZORPAY_KEY_ID does not look like a Razorpay "
                              "key id (expected rzp_live_ or rzp_test_).")
        if debit_authorized and not key_id.startswith("rzp_live_"):
            # An operator who set the debit flag believing they were on live
            # keys finds out here rather than from a test-mode transaction they
            # then have to explain.
            raise ConfigError(
                f"{DEBIT_ENV}=yes with a {key_id.split('_')[1]}-mode key. "
                f"Unset {DEBIT_ENV} to use live mode read-only against test "
                f"credentials.")
    else:
        if debit_authorized:
            raise ConfigError(
                f"{DEBIT_ENV}=yes with {MODE_ENV}={mode.value}. The mock rail "
                f"moves no money, so authorising live debits here means the "
                f"mode is not the one that was intended.")
        if not webhook_secret:
            # OFFLINE ONLY, and unreachable in live mode: the block above has
            # already refused a config without a configured secret. The mock
            # rail signs the webhooks it emits and this service verifies them,
            # so a secret must exist for the real HMAC path to run at all --
            # and making an operator invent one would teach them to put a
            # secret on a command line. Random per process; never leaves it.
            webhook_secret = secrets.token_hex(32)

    return LiveConfig(
        mode=mode, key_id=key_id, key_secret=key_secret,
        webhook_secret=webhook_secret, debit_authorized=debit_authorized,
        max_debit_paise=max_debit,
        api_base=(get("RAZORPAY_API_BASE")
                  or "https://api.razorpay.com/v1").rstrip("/"),
        operator_token=get("RECOVERY_OPERATOR_TOKEN"),
        # Runtime state lives in `live/data/`, which is gitignored in full.
        # Putting a database beside the source that reads it is how a demo
        # database ends up in a public repository with a customer's email in a
        # webhook payload.
        db_path=(get("RECOVERY_DB")
                 or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "data", "recovery.db")))
