#!/usr/bin/env python3
"""
Test gate. Runs sim/tests.py, prints its full output, and decides whether the
current state of the tree is allowed to be committed.

The rule this enforces is CLAUDE.md rule 1: a failing gate means the code is
wrong until proven otherwise. You do not get to loosen a test to go green.

A gate is "bad" if it came back FAIL or VACUOUS. VACUOUS means the gate exists
but no mutant could trip it, so it is not actually protecting anything -- that
is a failure of the suite, not a pass, and it is treated exactly like a FAIL.

Bad gates listed in sim/known_failures.txt are tolerated (they are the debt we
have chosen to carry, with a written reason). Anything else blocks the commit.

Exit codes:  0 = allowed to commit,  1 = blocked.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TESTS = os.path.join(HERE, "tests.py")
KNOWN = os.path.join(HERE, "known_failures.txt")

# tests.py record() prints:  f"[{tag}] {tid:<5} {name:<46} {detail}"
# where tag is one of "  ok  ", " FAIL ", "VACUOUS" -- note the widths differ,
# so match the bracket contents loosely and strip, rather than by column.
LINE = re.compile(r"^\[\s*(ok|FAIL|VACUOUS)\s*\]\s+(\S+)\s*(.*?)\s*$")

BAD = ("FAIL", "VACUOUS")


def run_tests(tier):
    """Run the suite and return (combined_output, exit_code)."""
    proc = subprocess.run(
        [sys.executable, TESTS, "--tier", tier],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout, proc.returncode


def parse(output):
    """Return {gate_id: (status, detail)} for every gate line in the output."""
    seen = {}
    for line in output.splitlines():
        m = LINE.match(line)
        if m:
            status, tid, detail = m.group(1), m.group(2), m.group(3)
            seen[tid] = (status, detail)
    return seen


def load_known():
    """Read known_failures.txt: one gate ID per line, '#' starts a comment."""
    if not os.path.exists(KNOWN):
        return {}
    known = {}
    with open(KNOWN, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].strip()
            if line:
                known[line.split()[0]] = raw.strip()
    return known


def block(title, lines):
    bar = "!" * 78
    print("\n" + bar)
    print(title)
    print(bar)
    for line in lines:
        print(line)
    print(bar + "\n")


def main():
    tier = "full"
    if "--tier" in sys.argv:
        tier = sys.argv[sys.argv.index("--tier") + 1]
    if tier not in ("fast", "full"):
        print(f"gate: unknown tier {tier!r}; use fast or full")
        return 1

    output, code = run_tests(tier)
    print(output, end="" if output.endswith("\n") else "\n")

    gates = parse(output)

    # The suite failing to run at all is a block. Silence is not a pass.
    if not gates:
        block("GATE ERROR: the test suite produced no gate results.",
              [f"sim/tests.py exited {code} and printed no parsable gate lines.",
               "The suite must run before anything can be committed.",
               "",
               "Fix the code. Do NOT loosen the test."])
        return 1

    bad = {t: s for t, (s, _) in gates.items() if s in BAD}
    known = load_known()

    unexpected = sorted(t for t in bad if t not in known)
    # A gate that did NOT RUN in this tier has not been "fixed" -- it has not
    # been asked. Reporting it as fixed would invite someone to delete its
    # known_failures entry on the strength of a tier that never exercised it.
    fixed = sorted(t for t in known if t in gates and t not in bad)
    skipped = sorted(t for t in known if t not in gates)
    still = sorted(t for t in known if t in bad)

    if unexpected:
        lines = [
            f"{len(unexpected)} gate(s) are failing that are NOT in "
            "sim/known_failures.txt:",
            "",
        ]
        for tid in unexpected:
            status, detail = gates[tid]
            lines.append(f"    {tid:<5} {status:<8} {detail}")
        lines += [
            "",
            "A VACUOUS gate counts as a failure: it means no mutant can trip",
            "the gate, so the gate is not protecting anything.",
            "",
            "Fix the code. Do NOT loosen the test.",
        ]
        block("COMMIT BLOCKED: NEW TEST FAILURES", lines)
        return 1

    if fixed:
        print("\nNOTE: these gates are in sim/known_failures.txt but are no "
              "longer failing:")
        for tid in fixed:
            status = gates.get(tid, ("MISSING", ""))[0]
            print(f"    {tid:<5} now {status}")
        print("\n  If you genuinely fixed them, remove them from "
              "sim/known_failures.txt.")
        print("  If they went green because a threshold was loosened, put the")
        print("  threshold back. Getting to green by moving the bar is the")
        print("  failure mode this whole gate exists to catch.")
        print("  (Not blocking.)")

    if still:
        print("\nKnown failures still present (tolerated, documented in "
              "sim/known_failures.txt):")
        for tid in still:
            status, detail = gates[tid]
            print(f"    {tid:<5} {status:<8} {detail}")

    if skipped:
        print(f"\nNOT EXERCISED by the '{tier}' tier (still owed a full run):")
        for tid in skipped:
            print(f"    {tid}")

    total_bad = len(bad)
    print(f"\nGATE PASS [tier={tier}]: {len(gates)} gates, {total_bad} bad, "
          f"all {total_bad} known. Commit allowed.")
    if tier != "full":
        print("Reminder: no number reaches docs/, the pitch or the "
              "architecture doc from anything but a --tier full run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
