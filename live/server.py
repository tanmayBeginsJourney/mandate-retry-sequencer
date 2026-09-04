"""Start the recovery service.

    py -3.12 -m live.server                    offline, mock rail, localhost
    py -3.12 -m live.server --host 0.0.0.0     needs RECOVERY_OPERATOR_TOKEN

Environment variables, BY NAME ONLY -- no value belongs in a document:

    RECOVERY_MODE                offline | live      (default offline)
    RECOVERY_LIVE_DEBIT          yes, to permit a real debit in live mode
    RECOVERY_DB                  path to the SQLite file
    RECOVERY_OPERATOR_TOKEN      required to bind anything but loopback
    RECOVERY_MAX_DEBIT_PAISE     our own ceiling on one debit
    RAZORPAY_KEY_ID              required in live mode
    RAZORPAY_KEY_SECRET          required in live mode
    RAZORPAY_WEBHOOK_SECRET      required in live mode

STARTUP IS FAIL-CLOSED AND IT NEVER MOVES MONEY. Configuration is validated
before the socket is opened, a LIVE mode missing a credential is an error
rather than a quiet demotion to the mock, and nothing on this path submits a
payment: the first thing that can is an operator asking a specific mandate to
decide. Importing this module, running the tests, hitting the health endpoint
and loading the console are all incapable of debiting anybody.
"""
from __future__ import annotations

import argparse
import sys

from agent.llm.client import _load_dotenv
from live.api import Server
from live.config import ConfigError, Mode, load
from live.service import LiveService
from live.webhooks import process_pending


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8730)
    args = ap.parse_args(argv)

    # Credentials live in `.env` at the repository root, gitignored. One
    # loader, one precedence rule: an environment variable set by the caller
    # always wins. It never prints a key.
    _load_dotenv()

    try:
        config = load()
    except ConfigError as e:
        print(f"CONFIGURATION REFUSED: {e}", file=sys.stderr)
        return 2

    loopback = args.host in ("127.0.0.1", "localhost", "::1")
    if not loopback and not config.operator_token:
        # The operator API is open with no token set -- right for a machine
        # only its owner can reach, wrong for anything else. Refusing here is
        # what keeps that from being a comment.
        print("REFUSED: binding a non-loopback address without "
              "RECOVERY_OPERATOR_TOKEN would expose the operator API.",
              file=sys.stderr)
        return 2

    service = LiveService(config)

    # CRASH RECOVERY, before the first request. Any webhook accepted and
    # acknowledged but not interpreted is replayed now; every transition is
    # monotonic, so replaying one that did land is a no-op.
    replayed = process_pending(service.store)
    if replayed:
        print(f"replayed {len(replayed)} unprocessed webhook event(s)")

    desc = config.describe()
    print(f"mode           {desc['mode']}"
          + (f"  (key {desc['key_prefix']}_…)" if desc["key_prefix"] else ""))
    print(f"live debits    {'ALLOWED' if desc['debit_allowed'] else 'BLOCKED'}"
          f"  -- {desc['debit_reason']}")
    print(f"database       {service.store.path}")
    print(f"clock origin   {service.epoch_origin}  (simulated hour 0)")
    print(f"operator auth  {'required' if desc['operator_auth_required'] else 'OPEN (loopback only)'}")
    if config.mode is Mode.LIVE:
        print("webhook URL    POST /webhooks/razorpay  -- must be HTTPS and "
              "reachable by Razorpay")

    server = Server(service, host=args.host, port=args.port)
    print(f"\nconsole        http://{args.host}:{server.port}/")
    print(f"health         http://{args.host}:{server.port}/health")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
