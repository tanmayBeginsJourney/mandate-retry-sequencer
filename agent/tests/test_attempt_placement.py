#!/usr/bin/env python3
"""W20 -- WHERE DO THE ATTEMPTS LAND? A diagnostic, not a fix.

    py -3.12 agent/tests/test_attempt_placement.py

WHY THIS EXISTS. The agent loses to the fixed schedule `est_payday + [1, 7]` by
9.17 points at `payday_err=1` and 7.83 at 3, and NOTES.md records that residual
as UNEXPLAINED after five failed fixes. One architectural suspect survives from
the W18 write-up and has never been measured:

  `agent.policy.timing.propose` only ever targets `ahead[0]` -- TOMORROW. The
  agent's action space is "attempt tomorrow, or wait"; it cannot commit an
  attempt to a chosen future day. With the 24h notification rule on top, that
  plausibly lands its attempts LATER on the post-payday balance-decay curve
  than `[1,7]`'s payday+1, which is placed directly.

This measures placement. It does not change the agent, and passing or failing
changes no headline number. The point is that the write-up should say what the
residual IS rather than "unexplained", if one run can say it.

WHAT IT COMPARES. For every AT-RISK cycle, `collectable_days` gives the exact
set of days a legal presentation would have cleared -- clairvoyant, ignoring
sibling drain, the same upper bound `constrained_oracle` is. Each arm's real
attempts come from its own audit trail (`MONEY_ACTION`, `gate_verdict=ALLOWED`),
so this is the schedule that actually executed, not the schedule proposed.

PRE-REGISTERED, WRITTEN BEFORE THE RUN. 1 September 2026.

  P-1  The agent's median FIRST attempt lands LATER, relative to the true
       payday, than `[1,7]`'s. Predicted gap >= 2 days. THIS IS THE
       HYPOTHESIS: if the agent is earlier or level, it is dead.
  P-2  Of reachable at-risk cycles the agent fails, the largest class is
       TOO LATE -- every attempt after the last collectable day. Predicted
       over 50%.
  P-3  `[1,7]` places a higher share of its attempts inside the collectable
       set than the agent does. Predicted gap >= 10 points.
  P-4  The collectable window is NARROW: predicted median width <= 5 days.
       A wide window would mean decay is slow, and "landed late on the decay
       curve" could not be the mechanism whatever P-1 says.

ADDENDUM, PRE-REGISTERED AFTER THE payday_err=7 RUN AND BEFORE THE
payday_err=1 RUN. 1 September 2026.

P-1 to P-4 all broke at `payday_err=7`, and that run has a limitation worth
stating rather than burying: **7 is the TIE regime.** The agent is -1.34 there.
The residual to explain is -9.17, and it lives at `payday_err=1`. So the whole
prediction set was tested where there is almost nothing to explain.

Re-run at `--payday-err 1`, predictions fixed first:

  P-5  The agent's largest loss class at payday_err=1 is still NEVER
       ATTEMPTED, not TOO LATE. Predicted over 40%.
  P-6  The agent loses at least twice as many reachable cycles as `[1,7]`
       does. (At payday_err=1 `[1,7]` scores 99.75%, so it should lose
       almost none.)
  P-7  The agent's median first attempt is NOT later than `[1,7]`'s relative
       to the true payday -- i.e. lateness is dead at the losing level too,
       not only at the tie.

NOT GATE-PROTECTED. Every run is one process (`_parallel.py`).
"""
from __future__ import annotations

import os
import sys
import tempfile
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np

import agent  # noqa: F401
import w3

N, K_FIXED, DAYS, CYC = 100, 5, 120, 30
#: The noise level to diagnose. `--payday-err 1` is the regime where the agent
#: actually LOSES (-9.17); 7 is the tie. Both are reported -- see the addendum.
PE = 7
for _i, _a in enumerate(sys.argv):
    if _a == "--payday-err":
        PE = int(sys.argv[_i + 1])
#: HELD OUT from every schedule selection, matching `logs/w17_coverage.txt`.
POPS = list(range(710, 720))
SPEND = 0.93
K_SEED, BUF_SEED, BURN = 4242, 9182, 12
CANONICAL = dict(k_mean=2.0, k_seed=K_SEED, k_max=8,
                 payday_mode="statutory",
                 amount_mode="absolute", amount_median=855.0,
                 buffer_median=0.25, buffer_sigma=1.0, buffer_seed=BUF_SEED,
                 irregular_frac=0.00)
RUN_KW = dict(burn_cycles=BURN, mandate_outflow=True)
ARMS = (("agent", "degenerate"), ("[1,7]", "payday_offsets"))


def percell(pop_seed):
    kw = dict(CANONICAL)
    kw["k_seed"] = K_SEED + pop_seed
    kw["buffer_seed"] = BUF_SEED + pop_seed
    return kw


def _job(args):
    """One (population, arm). Everything is aggregated INSIDE the worker."""
    label, mode, pop_seed = args
    import agent  # noqa: F401
    import w3
    from agent.audit.log import read_rows
    from agent.batch import at_risk_cycles, collectable_days, make_pop, run_once

    pop = make_pop(N, K_FIXED, pop_seed, spend=SPEND, days=DAYS,
                   **percell(pop_seed))
    at_risk = at_risk_cycles(pop, 907, PE, **RUN_KW)
    coll = collectable_days(pop, 907, PE, **RUN_KW)
    payday = {ci: int(c["payday"]) for ci, c in enumerate(pop)}

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        path = os.path.join(tmp, "a.jsonl")
        run_once(pop, 907, payday_err=PE, pop_spend=SPEND,
                 bcfg=w3.FITTED_BELIEF, mode=mode, log_path=path,
                 **RUN_KW)
        # (uid, cycle) -> [day, ...] for attempts that ACTUALLY EXECUTED, and
        # the set of (uid, cycle) that ended up collected.
        att: dict[tuple[str, int], list[int]] = {}
        won: set[tuple[str, int]] = set()
        dead: dict[str, int] = {}
        for r in read_rows(path):
            k = r.get("kind")
            if k == "MONEY_ACTION" and r.get("gate_verdict") == "ALLOWED":
                key = (r["mandate_uid"], int(r["cycle"]))
                att.setdefault(key, []).append(int(r["ts_hour"]) // w3.HOURS)
            elif k == "OUTCOME" and r.get("success"):
                won.add((r["mandate_uid"], int(r["cycle"])))
            elif k == "STOP" and r.get("rule") == "MANDATE_DEAD":
                # W24. The cycle a mandate died in, so a later never-attempted
                # cycle of the SAME mandate can be attributed to that death
                # rather than left as "not yet attributed to a mechanism".
                dead[r["mandate_uid"]] = int(r["cycle"])

    out = dict(n_at_risk=len(at_risk), n_reach=len(coll),
               joined=0, join_mismatch=0, n_attempts=0, n_inside=0,
               first_rel_due=[], first_rel_pay=[], widths=[],
               fail=Counter(), won_reach=0,
               never_dead=0, never_other=0, n_dead=len(dead),
               n_mandates=sum(len(c["mandates"]) for c in pop))
    for (uid, cyc), due in at_risk.items():
        ci = int(uid.split("m")[0][1:])
        days = coll.get((uid, cyc))
        if days is None:
            continue                       # unreachable by ANY legal schedule
        out["widths"].append(len(days))
        rel = [d - due for d in att.get((uid, cyc), [])]
        bad = [r for r in rel if not (1 <= r < CYC)]
        out["join_mismatch"] += len(bad)
        rel = sorted(r for r in rel if 1 <= r < CYC)
        out["joined"] += 1
        out["n_attempts"] += len(rel)
        out["n_inside"] += sum(1 for r in rel if r in days)
        if rel:
            out["first_rel_due"].append(rel[0])
            # Days after the TRUE payday, wrapped into the cycle. The arm sees
            # only a noisy estimate of this; the diagnostic does not.
            out["first_rel_pay"].append((due + rel[0] - payday[ci]) % CYC)
        if (uid, cyc) in won:
            out["won_reach"] += 1
            continue
        if not rel:
            out["fail"]["never attempted"] += 1
            # W24, 1 September 2026. THE ATTRIBUTION THIS FILE PREVIOUSLY
            # LACKED. `_phase_rollover` advances a mandate's cycle only
            # `if day >= m.cycle_close and m.alive`, so a mandate that spent
            # its fourth attempt without collecting freezes at that cycle and
            # every later cycle of that mandate is never seen by the decision
            # phase at all. Measured at payday_err=1: 31 of 31.
            if uid in dead and dead[uid] < cyc:
                out["never_dead"] += 1
            else:
                out["never_other"] += 1
        elif min(rel) > max(days):
            out["fail"]["TOO LATE (all after last collectable day)"] += 1
        elif max(rel) < min(days):
            out["fail"]["too early (all before first)"] += 1
        else:
            out["fail"]["in a gap (straddles collectable days)"] += 1
    return label, pop_seed, out


def pct(a, b):
    return 100.0 * a / max(1, b)


def median(xs):
    return float(np.median(xs)) if xs else float("nan")


def main() -> int:
    from concurrent.futures import ProcessPoolExecutor

    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ.setdefault(var, "1")

    jobs = [(label, mode, ps) for label, mode in ARMS for ps in POPS]
    print("W20 ATTEMPT PLACEMENT -- where do the attempts land, and is the "
          "agent late?")
    print(f"{len(jobs)} runs, canonical world, pop_spend={SPEND}, n={N}, "
          f"payday_err={PE}, HELD-OUT pops {POPS[0]}-{POPS[-1]}.")
    print("Collectable days are CLAIRVOYANT and ignore sibling drain, exactly "
          "as constrained_oracle does.")

    agg: dict[str, dict] = {}
    with ProcessPoolExecutor(max_workers=min(len(jobs), os.cpu_count() or 4, 16),
                             max_tasks_per_child=1) as ex:
        for label, _ps, o in ex.map(_job, jobs, chunksize=1):
            a = agg.setdefault(label, dict(
                n_at_risk=0, n_reach=0, joined=0, join_mismatch=0,
                n_attempts=0, n_inside=0, won_reach=0,
                never_dead=0, never_other=0, n_dead=0, n_mandates=0,
                first_rel_due=[], first_rel_pay=[], widths=[],
                fail=Counter()))
            for f in ("n_at_risk", "n_reach", "joined", "join_mismatch",
                      "n_attempts", "n_inside", "won_reach",
                      "never_dead", "never_other", "n_dead", "n_mandates"):
                a[f] += o[f]
            for f in ("first_rel_due", "first_rel_pay", "widths"):
                a[f].extend(o[f])
            a["fail"].update(o["fail"])

    print()
    print("=" * 92)
    print(f"{'arm':>8}{'at risk':>10}{'reachable':>11}{'collected':>11}"
          f"{'attempts':>10}{'inside window':>15}{'1st att vs due':>16}"
          f"{'vs payday':>11}")
    for label, _m in ARMS:
        a = agg[label]
        print(f"{label:>8}{a['n_at_risk']:>10}{a['n_reach']:>11}"
              f"{pct(a['won_reach'], a['joined']):>10.1f}%"
              f"{a['n_attempts']:>10}"
              f"{pct(a['n_inside'], a['n_attempts']):>14.1f}%"
              f"{median(a['first_rel_due']):>16.1f}"
              f"{median(a['first_rel_pay']):>11.1f}")
    mm = sum(agg[l]["join_mismatch"] for l, _ in ARMS)
    print(f"  join self-check: {mm} attempt(s) fell outside their cycle "
          f"window. Non-zero here invalidates every row above.")

    print()
    print("WHERE THE FIRST ATTEMPT LANDS, in days after the TRUE payday. The "
          "arms see only a noisy estimate of it.")
    print("=" * 92)
    for label, _m in ARMS:
        h = Counter(agg[label]["first_rel_pay"])
        bars = " ".join(f"{d}:{h[d]}" for d in sorted(h) if h[d])
        print(f"  {label:>7}: {bars}")

    print()
    print("HOW THE REACHABLE CYCLES ARE LOST.")
    print("=" * 92)
    for label, _m in ARMS:
        a = agg[label]
        lost = a["joined"] - a["won_reach"]
        print(f"  {label:>7}: {lost} reachable cycles lost of {a['joined']}")
        for cls, n in a["fail"].most_common():
            print(f"           {n:>5} ({pct(n, lost):>5.1f}%)  {cls}")
        nv = a["never_dead"] + a["never_other"]
        if nv:
            print(f"           of the never-attempted: {a['never_dead']} "
                  f"({pct(a['never_dead'], nv):.1f}%) belong to a mandate "
                  f"that DIED in an earlier cycle,")
            print(f"           {a['never_other']} do not. "
                  f"{a['n_dead']} of {a['n_mandates']} mandates died.")

    w = agg[ARMS[0][0]]["widths"]
    print()
    print(f"COLLECTABLE WINDOW WIDTH, days per reachable cycle: "
          f"median {median(w):.1f}, mean {np.mean(w):.2f}, "
          f"p10 {np.percentile(w, 10):.1f}, p90 {np.percentile(w, 90):.1f}")

    A, B = agg["agent"], agg["[1,7]"]
    gap_pay = median(A["first_rel_pay"]) - median(B["first_rel_pay"])
    late = A["fail"]["TOO LATE (all after last collectable day)"]
    late_sh = pct(late, max(1, A["joined"] - A["won_reach"]))
    inside_gap = pct(B["n_inside"], B["n_attempts"]) - \
        pct(A["n_inside"], A["n_attempts"])
    checks = [
        ("P-1 agent's first attempt is >= 2 days later vs payday",
         gap_pay >= 2.0, f"{gap_pay:+.1f} days"),
        ("P-2 TOO LATE is over 50% of the agent's lost reachable cycles",
         late_sh > 50.0, f"{late_sh:.1f}%"),
        ("P-3 [1,7] lands inside the window >= 10 pts more often",
         inside_gap >= 10.0, f"{inside_gap:+.1f} pts"),
        ("P-4 median collectable window <= 5 days",
         median(w) <= 5.0, f"{median(w):.1f} days"),
    ]
    lostA = A["joined"] - A["won_reach"]
    lostB = B["joined"] - B["won_reach"]
    never_sh = pct(A["fail"]["never attempted"], max(1, lostA))
    checks += [
        ("P-5 NEVER ATTEMPTED is over 40% of the agent's lost cycles",
         never_sh > 40.0, f"{never_sh:.1f}%"),
        ("P-6 the agent loses >= 2x as many reachable cycles as [1,7]",
         lostA >= 2 * max(1, lostB), f"{lostA} vs {lostB}"),
        ("P-7 the agent's first attempt is NOT later than [1,7]'s",
         gap_pay <= 0.0, f"{gap_pay:+.1f} days"),
    ]

    print()
    print("PRE-REGISTERED PREDICTIONS")
    print("=" * 92)
    for name, ok, got in checks:
        print(f"  [{'HOLD' if ok else 'BROKE':>5}] {name:<62} {got}")
    print(f"  {sum(1 for _, ok, _ in checks if ok)}/{len(checks)} held.")
    print()
    print("A BROKEN PREDICTION IS THE RESULT, NOT A FAILURE. P-1 is the "
          "hypothesis; if it broke, the\nlate-placement explanation is dead "
          "and the residual stays recorded as unexplained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
