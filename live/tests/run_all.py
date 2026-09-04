"""Run every live gate.

    py -3.12 -m live.tests.run_all

Each gate file is its own process, for the reason `agent/tests/_parallel.py`
exists: long-lived processes that build many services on this machine have
crashed at unpredictable points, and a crashed run is a FAILED measurement
rather than a missing one. Running them separately also means one gate's
temporary database cannot outlive its gate.

EVERY GATE RUNS OFFLINE. None needs an API key, none opens a socket to
Razorpay, and none can move money. That is a property of the gates, not a flag
they are run with: `RECOVERY_MODE` defaults to offline and the harness never
sets it to anything else.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import live.tests  # noqa: F401

GATES = [
    ("config", "live.tests.test_config",
     "the mode switch fails closed in both directions"),
    ("state machine", "live.tests.test_state_machine",
     "no transition walks backwards"),
    ("webhooks", "live.tests.test_webhooks",
     "signature, duplication, ordering, malformed input"),
    ("flow", "live.tests.test_flow",
     "the lifecycle, including eight crash boundaries"),
    ("safety", "live.tests.test_safety",
     "the LLM boundary and the absence of a charge endpoint"),
    ("parity", "live.tests.test_parity",
     "the simulation and the live rail share the decision layers"),
    ("http", "live.tests.test_api",
     "the served surface, over a real socket"),
]

_PKG = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    verbose = "-v" in argv or "--verbose" in argv

    print("=" * 78)
    print("LIVE RAIL GATES")
    print("Offline, no API key, no network to Razorpay, no money.")
    print("=" * 78)

    results: list[tuple[str, bool, float, str]] = []
    for name, module, blurb in GATES:
        started = time.time()
        proc = subprocess.run([sys.executable, "-m", module],
                              cwd=_PKG, capture_output=True, text=True)
        took = time.time() - started
        passed = proc.returncode == 0
        tail = ""
        for line in reversed(proc.stdout.splitlines()):
            if "checks passed" in line:
                tail = line.strip()
                break
        results.append((name, passed, took, tail))
        print(f"\n[{'PASS' if passed else 'FAIL'}] {name:<14s} {blurb}")
        print(f"       {tail or 'no summary line'}   ({took:.1f}s)")
        if verbose or not passed:
            for line in proc.stdout.splitlines():
                if line.startswith("  FAIL") or line.startswith("  FAILED"):
                    print(f"       {line.strip()}")
            if proc.stderr.strip():
                print("       stderr:")
                for line in proc.stderr.strip().splitlines()[-12:]:
                    print(f"         {line}")

    bad = [r for r in results if not r[1]]
    print("\n" + "=" * 78)
    print(f"{len(results) - len(bad)}/{len(results)} gate files passed"
          f"   ({sum(r[2] for r in results):.1f}s)")
    if bad:
        for name, _, _, tail in bad:
            print(f"  FAILED  {name}   {tail}")
    print("=" * 78)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
