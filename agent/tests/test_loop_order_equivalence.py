"""THE RESTRUCTURE GATE. Time-major iteration changed nothing else.

The cross-customer rail monitor needs TIME on the outside of the loop: in
customer-major order there is no such thing as "the state of the rail at hour
t", because the other 99 customers have not been simulated yet. So `loop.py`
grew a second driver.

A second driver is a second chance to be wrong. This gate holds the RNG fixed
and varies ONLY the iteration order, and requires bit-identical output.

THE MUTANT. A gate that no mutant can trip is VACUOUS and this project has
shipped five of those. Here the mutant is: turn the rail monitor ON under
time-major and require the answer to CHANGE. If enabling the monitor does not
change anything, the monitor is not reading cross-customer evidence and every
outage result in this repo is meaningless -- so Half 1's "identical" would be
identical for the trivial reason that nothing ever differs.

HALF 3 EXISTS BECAUSE THE FIRST VERSION OF THIS GATE PASSED FOR THE WRONG
REASON. The original mutant ran the monitor in BOTH orders and required
divergence. It diverged -- customer-major read 1.97% recovery against
time-major's 79.41% -- and the gate went green while I wrote "the monitor
genuinely reads cross-customer state" underneath it.

That was not the reason. In customer-major the clock RESTARTS at t=0 for every
customer, so `_prune` never drops anything, the window accumulates one
customer's entire 120-day history, OUTAGE latches permanently and dispatch
never resumes. The divergence was a time-travel bug, not evidence of anything.
The monitor now raises `NonMonotonicTime` on that misuse, and Half 3 asserts it
raises -- because a component that returns a confident wrong number is worse
than one that crashes.

All three halves are required to pass.

WHY BIT-PARITY WITH THE HARNESS SURVIVES. `test_parity_vs_harness.py` runs
customer-major with the SHARED technical-decline generator, which is the order
`harness.run` consumes it in, and still gets 24/24 exact. Time-major uses
per-customer generators instead. Those are different draws, so time-major is
NOT bit-identical to the harness and is not claimed to be -- it is bit-identical
to customer-major run with the same per-customer generators, which is what this
gate measures.
"""
from __future__ import annotations

import os
import sys

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "1")

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

import agent  # noqa: F401
import w3

from agent.context.rail_monitor import NonMonotonicTime
from agent.tests._parallel import agent_job, run_jobs

POPS = [700, 701, 702]
N, K, DAYS, SPEND, PE, RUN_SEED = 60, 5, 120, 1.05, 7, 7
FIELDS = ("cycle_rec", "approval", "survival", "att_per_cycle", "starvation",
          "cycles_due", "recovered_paise")
POP_SPEC = lambda s: (N, K, s, SPEND, DAYS)

# The outage is described by its ARGUMENTS here, not by an OutageSchedule
# object: the schedule is rebuilt inside each worker, so nothing crosses a
# process boundary except numbers.
OUTAGE_KW = dict(days=[30, 60, 90], duration_h=6, severity=0.60)


def _kw(*, time_major, monitor_enabled, outage=False):
    return dict(payday_err=PE, pop_spend=SPEND, bcfg=w3.FITTED_BELIEF,
                mode="degenerate", time_major=time_major,
                per_customer_tech_rng=True,     # HELD FIXED. See docstring.
                monitor_enabled=monitor_enabled,
                pause_on_outage=monitor_enabled,
                suppress_tech_updates=("outage_only" if monitor_enabled
                                       else "never"),
                outage_kw=OUTAGE_KW if outage else None)


def main() -> int:
    fails, mutant_tripped = [], 0

    jobs = []
    for s in POPS:
        jobs.append((("cm", s), POP_SPEC(s), RUN_SEED,
                     _kw(time_major=False, monitor_enabled=False), False))
        jobs.append((("tm", s), POP_SPEC(s), RUN_SEED,
                     _kw(time_major=True, monitor_enabled=False), False))
        jobs.append((("off", s), POP_SPEC(s), RUN_SEED,
                     _kw(time_major=True, monitor_enabled=False, outage=True),
                     False))
        jobs.append((("on", s), POP_SPEC(s), RUN_SEED,
                     _kw(time_major=True, monitor_enabled=True, outage=True),
                     False))
    res = run_jobs(agent_job, jobs)

    if True:
        print("=" * 84)
        print("LOOP ORDER EQUIVALENCE -- monitor OFF, the two orders must be "
              "bit-identical")
        print("=" * 84)
        print(f"{'pop':>5s} " + " ".join(f"{f:>14s}" for f in FIELDS[:4])
              + "   identical")
        for s in POPS:
            a, b = res[("cm", s)], res[("tm", s)]
            same = all(a[f] == b[f] for f in FIELDS)
            print(f"{s:5d} " + " ".join(f"{a[f]:14.6f}" if isinstance(a[f], float)
                                        else f"{a[f]:14d}" for f in FIELDS[:4])
                  + f"   {'YES' if same else 'NO'}")
            if not same:
                diffs = [f"{f}: {a[f]!r} vs {b[f]!r}" for f in FIELDS
                         if a[f] != b[f]]
                fails.append(f"pop {s}: orders differ with the monitor OFF -- "
                             + "; ".join(diffs))

        print()
        print("=" * 84)
        print("THE MUTANT -- time-major, monitor ON vs OFF, MUST differ")
        print("=" * 84)
        print(f"{'pop':>5s} {'monitor off':>12s} {'monitor on':>12s} "
              f"{'differs':>9s}  rail transitions")
        for s in POPS:
            off, on = res[("off", s)], res[("on", s)]
            differs = any(off[f] != on[f] for f in FIELDS)
            mutant_tripped += 1 if differs else 0
            print(f"{s:5d} {off['cycle_rec']*100:12.4f} "
                  f"{on['cycle_rec']*100:12.4f} "
                  f"{'YES' if differs else 'NO':>9s}  "
                  f"{len(on['rail_transitions'])}")

        print()
        print("=" * 84)
        print("HALF 3 -- the monitor must REFUSE customer-major, not guess")
        print("=" * 84)
        # Run IN-PROCESS: we need the exception, and a pool would hand back
        # a wrapped worker death that is indistinguishable from the machine's
        # intermittent segfault.
        import tempfile
        from agent.batch import make_pop, run_once
        pop = make_pop(N, K, POPS[0], spend=SPEND, days=DAYS)
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
                bad = run_once(pop, RUN_SEED,
                               log_path=os.path.join(td, "misuse.jsonl"),
                               **_kw(time_major=False, monitor_enabled=True,
                                     outage=True))
            print(f"  NO RAISE -- returned cycle_rec="
                  f"{bad['cycle_rec']*100:.2f}%, which is a confident number "
                  f"from a window that never prunes")
            fails.append("HALF 3: the monitor accepted a customer-major loop "
                         "and returned a number instead of raising")
        except NonMonotonicTime as exc:
            print("  raises NonMonotonicTime, as it must:")
            print(f"    {str(exc)[:72]}...")

    print()
    if mutant_tripped == 0:
        fails.append("VACUOUS: enabling the rail monitor changed nothing under "
                     "time-major. Either it never fires or it reads no "
                     "cross-customer evidence -- and every outage result in "
                     "this repo would be meaningless.")
    else:
        print(f"MUTANT: monitor changed the answer on "
              f"{mutant_tripped}/{len(POPS)} populations -- it is live")

    print()
    if fails:
        print("FAIL")
        for f in fails:
            print(f"  {f}")
    else:
        print("PASS -- the restructure is behaviour-neutral with the monitor "
              "off, the monitor")
        print("       provably does something with it on, and misusing it "
              "raises instead of guessing.")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
