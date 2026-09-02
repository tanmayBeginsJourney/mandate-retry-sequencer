"""Which rail is this process talking to, and is it allowed to move money.

TWO SWITCHES, NOT ONE, AND THEY DO DIFFERENT JOBS.

`RECOVERY_MODE` picks the rail. `offline` uses `MockRazorpayApi` and cannot
reach the network. `live` uses `RazorpayApi` and reaches api.razorpay.com. There
is no third value and no default-by-inference: an unset variable is `offline`,
and a misspelled one raises rather than guessing.

`RECOVERY_LIVE_DEBIT` decides whether a debit may actually be submitted while
in `live`. It is separate because reading a mandate's state, replaying a
webhook and rendering the console are all things you want to do against the
live rail *without* charging anybody. Collapsing the two into one flag would
make "look at production" and "take a customer's money" the same gesture.

THE FAILURE DIRECTION IS FIXED. Configured for `live` with a credential
missing is an error, never a quiet demotion to the mock. Configured for
`offline` can never reach the network, whatever the environment holds. Both
directions have been wrong in real systems and only one of them is loud.

NOTHING HERE PRINTS A SECRET. `describe()` reports whether a credential is
present and which key *prefix* is in use, because `rzp_test_` versus
`rzp_live_` is the single most important thing an operator needs to see and it
is not itself a secret. The key id's remainder and the secret never leave this
module.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

MODE_ENV = "RECOVERY_MODE"
DEBIT_ENV = "RECOVERY_LIVE_DEBIT"

#: Razorpay's documented minimum mandate amount across every merchant category
#: is Rs 1. [VERIFIED] razorpay.com UPI AutoPay docs, read 3 September 2026.
#: It is a floor the provider enforces, so it is encoded; the *ceiling* below
#: is ours and is configuration, because no provider rule sets it.
PROVIDER_MIN_DEBIT_PAISE = 100

#: Our own ceiling on a single live debit, in paise. Not a Razorpay rule --
#: their limit is the mandate's `max_amount`, which the provider enforces on
#: its own. This exists so that a bug in the amount path cannot spend more of
#: a real balance than the operator agreed to expose, and so the number is one
#: environment variable rather than a literal in five files.
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

    # ------------------------------------------------------------ predicates
    @property
    def is_live(self) -> bool:
        return self.mode is Mode.LIVE

    @property
    def key_prefix(self) -> str:
        """`rzp_test` / `rzp_live` / `""`. Safe to display; not a secret."""
        if not self.key_id:
            return ""
        parts = self.key_id.split("_")
        return "_".join(parts[:2]) if len(parts) >= 2 else "unknown"

    def may_debit(self) -> tuple[bool, str]:
        """Is a real debit permitted right now, and if not, why not.

        Returns a reason string rather than raising, because the console needs
        to *show* the refusal and an exception is not a display.
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
        return {
            "mode": self.mode.value,
            "key_prefix": self.key_prefix,
            "has_key": bool(self.key_id and self.key_secret),
            "has_webhook_secret": bool(self.webhook_secret),
            "debit_allowed": ok,
            "debit_reason": reason,
            "max_debit_paise": self.max_debit_paise,
            "api_base": self.api_base,
            "operator_auth_required": bool(self.operator_token),
        }


def _flag(src, name: str) -> bool:
    """Only the literal string `yes` enables a flag.

    Deliberately narrower than the usual truthy set. `1`, `true` and `on` all
    arrive by accident -- copied from another service's compose file, left over
    from a shell experiment. `yes` is a word somebody typed on purpose.

    It reads `src`, not `os.environ`. Reading the process environment from
    inside a function that has been handed a mapping makes the mapping a lie,
    and the flag it decides is the one that authorises real debits.
    """
    return (src.get(name) or "").strip().lower() == "yes"


def load(env: dict | None = None) -> LiveConfig:
    """Build the configuration, or raise.

    `env` is injectable so the tests can drive every branch without touching
    the process environment.
    """
    src = os.environ if env is None else env

    raw_mode = (src.get(MODE_ENV) or Mode.OFFLINE.value).strip().lower()
    try:
        mode = Mode(raw_mode)
    except ValueError:
        raise ConfigError(
            f"{MODE_ENV}={raw_mode!r} is not a mode. Use "
            f"{Mode.OFFLINE.value!r} or {Mode.LIVE.value!r}.") from None

    key_id = (src.get("RAZORPAY_KEY_ID") or "").strip()
    key_secret = (src.get("RAZORPAY_KEY_SECRET") or "").strip()
    webhook_secret = (src.get("RAZORPAY_WEBHOOK_SECRET") or "").strip()
    operator_token = (src.get("RECOVERY_OPERATOR_TOKEN") or "").strip()

    try:
        max_debit = int(src.get("RECOVERY_MAX_DEBIT_PAISE")
                        or DEFAULT_MAX_DEBIT_PAISE)
    except ValueError:
        raise ConfigError("RECOVERY_MAX_DEBIT_PAISE must be an integer number "
                          "of paise") from None
    if max_debit < PROVIDER_MIN_DEBIT_PAISE:
        raise ConfigError(
            f"RECOVERY_MAX_DEBIT_PAISE={max_debit} is below Razorpay's "
            f"documented minimum of {PROVIDER_MIN_DEBIT_PAISE} paise, so no "
            f"debit could ever be legal.")

    debit_authorized = _flag(src, DEBIT_ENV)

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
            raise ConfigError(
                "RAZORPAY_KEY_ID does not look like a Razorpay key id "
                "(expected an rzp_live_ or rzp_test_ prefix).")
        if debit_authorized and not key_id.startswith("rzp_live_"):
            # Refusing this combination costs nothing and removes a whole class
            # of confusion: an operator who set the debit flag believing they
            # were on live keys finds out here rather than from a test-mode
            # transaction they then have to explain.
            raise ConfigError(
                f"{DEBIT_ENV}=yes with a {key_id.split('_')[1]}-mode key. "
                f"Authorising real debits against test credentials is almost "
                f"always a mistake; unset {DEBIT_ENV} to use live mode "
                f"read-only against test keys.")
    else:
        if not webhook_secret:
            # OFFLINE ONLY. The mock rail signs the webhooks it emits and this
            # service verifies them, so a secret has to exist for the real HMAC
            # path to be exercised at all -- and requiring an operator to
            # invent one to run the demo would teach them to put a secret in a
            # command line. It is random per process and never leaves it.
            #
            # In LIVE mode this branch is unreachable: the block above has
            # already refused to build a config without a configured secret,
            # because there the secret is Razorpay's and cannot be guessed.
            import secrets
            webhook_secret = secrets.token_hex(32)
        if debit_authorized:
            raise ConfigError(
                f"{DEBIT_ENV}=yes with {MODE_ENV}={mode.value}. The mock rail "
                f"moves no money, so authorising live debits here means the "
                f"mode is not the one that was intended.")

    return LiveConfig(
        mode=mode,
        key_id=key_id,
        key_secret=key_secret,
        webhook_secret=webhook_secret,
        debit_authorized=debit_authorized,
        max_debit_paise=max_debit,
        api_base=(src.get("RAZORPAY_API_BASE")
                  or "https://api.razorpay.com/v1").rstrip("/"),
        operator_token=operator_token,
        # Runtime state lives in `live/data/`, which is gitignored in full.
        # Putting a database beside the source that reads it is how a demo
        # database ends up in a public repository with a customer's email in
        # a webhook payload.
        db_path=(src.get("RECOVERY_DB")
                 or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "data", "recovery.db")),
    )
