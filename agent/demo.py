"""Run the agent end to end over one batch and print the result.

    python -m agent.demo                 # default batch
    python -m agent.demo --n 50 --pe 3   # smaller, easier payday estimate
    python -m agent.demo --outage 0.40   # with a rail outage, monitor on

THIS IS THE ENTRY POINT. Everything else in `agent/` is a library or a gate.

`payday_wait` IS PRINTED BESIDE OUR NUMBER, ALWAYS, and it cannot be switched
off. It is the five-line heuristic a good rival team builds in an afternoon
(wait for the estimated payday, then one attempt per day), and at `payday_err`
of about 1 day it BEATS us. Showing our number alone would be dishonest by
omission -- see docs/06_MODEL_CARD.md section 2.

ONE RUN PER PROCESS. This runs a handful of configurations in a single process,
which is exactly the pattern that crashes on this machine (docs/06_MODEL_CARD.md
section 6a). A demo is small enough to get away with it; a MEASUREMENT is not.
Anything that sweeps must go through `agent/tests/_parallel.py`.
"""
from __future__ import annotations

import argparse
import collections
import os
import sys

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "1")

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent  # noqa: F401  -- puts sim/ on the path
import harness
import w3

from agent.audit.log import read_rows
from agent.batch import LOG_DIR, make_pop, run_once
from agent.constraints.auditor import replay


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=100, help="customers (default 100)")
    ap.add_argument("--k", type=int, default=5, help="mandates each (default 5)")
    ap.add_argument("--days", type=int, default=120, help="horizon (default 120)")
    ap.add_argument("--pop", type=int, default=700, help="population seed")
    ap.add_argument("--seed", type=int, default=7, help="run seed")
    ap.add_argument("--pe", type=int, default=7,
                    help="payday_err: how wrong the payday estimate is, in "
                         "days. THE HEADLINE IS CONDITIONAL ON THIS.")
    ap.add_argument("--outage", type=float, default=0.0,
                    help="outage severity 0-1. Non-zero turns the rail "
                         "monitor on and injects 6h outages.")
    a = ap.parse_args(argv)

    os.makedirs(LOG_DIR, exist_ok=True)
    pop = make_pop(a.n, a.k, a.pop, spend=1.05, days=a.days)
    outage_kw = (dict(days=list(range(20, a.days, 30)), duration_h=6,
                      severity=a.outage) if a.outage > 0 else None)

    common = dict(payday_err=a.pe, pop_spend=1.05, bcfg=w3.FITTED_BELIEF,
                  outage_kw=outage_kw, time_major=a.outage > 0,
                  monitor_enabled=a.outage > 0,
                  suppress_tech_updates="outage_only" if a.outage > 0 else "never")

    print(f"n={a.n} k={a.k} {a.days}d  payday_err=+/-{a.pe}  "
          f"population seed {a.pop}, run seed {a.seed}"
          + (f"  outage severity {a.outage}" if a.outage else ""))
    print()

    deg = run_once(pop, a.seed, mode="degenerate",
                   log_path=os.path.join(LOG_DIR, "demo_degenerate.jsonl"),
                   **common)
    full = run_once(pop, a.seed, mode="full", allow_nudge=True,
                    allow_escalate=True, allow_stop=True,
                    log_path=os.path.join(LOG_DIR, "demo_full.jsonl"),
                    **common)
    base = harness.run("payday_wait", pop, a.seed, payday_err=a.pe,
                       pop_spend=1.05)

    def money(r):
        return r["recovered_paise"] / 100.0

    print(f"{'':22s} {'cycles collected':>17s} {'Rs recovered':>14s} "
          f"{'survival':>9s} {'att/cycle':>10s}")
    print(f"{'payday_wait (rival)':22s} {base['cycle_rec']*100:16.2f}% "
          f"{'--':>14s} {base['survival']*100:8.2f}% "
          f"{base['att_per_cycle']:10.3f}")
    print(f"{'agent, degenerate':22s} {deg['cycle_rec']*100:16.2f}% "
          f"{money(deg):14,.0f} {deg['survival']*100:8.2f}% "
          f"{deg['att_per_cycle']:10.3f}")
    print(f"{'agent, full':22s} {full['cycle_rec']*100:16.2f}% "
          f"{money(full):14,.0f} {full['survival']*100:8.2f}% "
          f"{full['att_per_cycle']:10.3f}")
    print()
    print(f"vs payday_wait: {(full['cycle_rec'] - base['cycle_rec'])*100:+.2f} pts"
          f"   |   agent action space vs frozen policy: "
          f"{(full['cycle_rec'] - deg['cycle_rec'])*100:+.2f} pts")
    print()
    print("  Single run, single seed -- NO error bar, so do not quote these as")
    print("  results. The measured figures with paired 2 SE are in")
    print("  docs/02_RESULTS.md. The headline is conditional on payday_err:")
    print("  at +/-1 day payday_wait BEATS the agent.")

    # ---- compliance and stopping, from the audit log alone
    print()
    print("Stage 0 (enforced -- refused actions never reached the world):")
    print(f"  gate refusals        {full['gate_refusals']}")
    aud = replay(read_rows(full["log_path"]))
    print(f"  independent recount  {aud.asdict()}   <- different code, log only")
    print(f"  executed {aud.executed} money actions, {aud.refused} refused, "
          f"{aud.notifications} notifications issued")

    print()
    print("Stopping rules that fired:")
    for rule, n in sorted(full["stops"].items(), key=lambda kv: -kv[1]):
        if n:
            print(f"  {rule:16s} {n:6d}")
    if full["rail_transitions"]:
        print()
        print(f"Rail state changes: {len(full['rail_transitions'])}")
        for tr in full["rail_transitions"][:6]:
            print(f"  t={tr[0]:5d}  {tr[1]:16s}"
                  + (f"  window n={tr[2]}, tech={tr[3]}, "
                     f"P(by chance)={tr[4]:.2g}" if len(tr) >= 5 else ""))

    # ---- one money action, end to end
    rows = list(read_rows(full["log_path"]))
    acts = [r for r in rows if r["kind"] == "MONEY_ACTION"]
    if acts:
        aid = acts[len(acts) // 2]["action_id"]
        print()
        print(f"One money action end to end (action_id {aid}) -- this is what")
        print("`WHERE action_id = ?` returns:")
        for r in rows:
            if r.get("action_id") != aid:
                continue
            k = r["kind"]
            if k == "CONSTRAINT_CHECK":
                print(f"  {k:20s} {r['rule']:10s} {r['verdict']}")
            elif k == "MONEY_ACTION":
                print(f"  {k:20s} Rs {r['amount_paise']/100:,.2f} at t="
                      f"{r['target_t']} (day {r['target_t']//24}, hour "
                      f"{r['target_t']%24:02d}), notified t={r.get('notify_t')}, "
                      f"gate={r['gate_verdict']}")
            elif k == "OUTCOME":
                print(f"  {k:20s} {r['outcome_code']}  success={r['success']}"
                      f"  recovered Rs {r.get('recovered_paise',0)/100:,.2f}")

    print()
    print(f"Full audit trail: {full['log_path']}")
    print(f"  {len(rows)} events. Every money action, every constraint verdict,")
    print("  every stop, with its reason.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
