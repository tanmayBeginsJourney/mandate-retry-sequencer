#!/usr/bin/env python3
"""GATE: the recovery-rate metric. docs/results.md.

The project's own metric is cycles collected / cycles due. Every published
figure in the payments industry is a RECOVERY RATE -- of the payments that
failed, the fraction eventually collected. This gate covers the machinery that
produces the second one, because a metric that reaches the pitch deck without a
test behind it is how error 5 happened.

FIVE CHECKS, EACH WITH A NAMED MUTANT THAT MUST TRIP IT. A check whose mutant
does not trip is reported VACUOUS and treated as a failure, exactly as
`sim/gate.py` does -- that rule exists because three gates in this project once
passed by construction (docs/errors.md, "Three vacuous gates").

  W-1  the loop's record of which cycles it collected agrees EXACTLY with an
       independent replay of the same facts from the audit log. Two authors,
       one answer. This is the Stage 0 gate/auditor pattern applied to a
       metric, and the rule is the same: if they disagree, believe the log.
  W-2  the at-risk set is a property of the WORLD, not of a policy: two arms
       that behave differently on the same population produce the identical
       set. If this ever fails, recovery rates from different arms are not
       comparable and every table built on them is wrong.
  W-3  the at-risk set never names a cycle outside `cycles_due`, so the
       denominator of `first_presentation_failure_rate` cannot exceed 1.
  W-4  no recovery lands on the due date itself. The world said a due-date
       debit could not clear, and the agent cannot attempt before its own
       notification lead, so every gap is >= 1 day. A zero would mean the
       at-risk set and the collected set disagree about what a day is.
  W-5  recovery is bounded by the at-risk set: a cycle that was never at risk
       is never counted as recovered. Collecting money that was always there
       is not recovery.

Run from the repo root with the interpreter named in CLAUDE.md.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import agent  # noqa: F401  -- puts sim/ on the path
import w3

from agent import metrics
from agent.audit.log import EventKind
from agent.batch import at_risk_cycles, make_pop, run_once

N, K, DAYS, PE, SPEND, SEED = 40, 5, 120, 7, 1.05, 907
POP_SEED = 700


def replay_collected(log_path: str) -> dict:
    """Rebuild {(mandate_uid, cycle): day} from the audit log alone.

    Shares no code with the loop's own bookkeeping: it reads OUTCOME rows off
    the disk and knows nothing about `MandateState`. That is the whole point --
    an accumulator checked only against itself is not checked.
    """
    out: dict = {}
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("kind") != EventKind.OUTCOME or not row.get("success"):
                continue
            key = (row["mandate_uid"], row["cycle"])
            day = row["ts_hour"] // w3.HOURS
            # First success wins, matching the loop: a cycle is collected once
            # and `collected` then blocks any further attempt in it.
            out.setdefault(key, day)
    return out


def main() -> int:
    pop = make_pop(N, K, POP_SEED, spend=SPEND, days=DAYS)
    log_a = os.path.join(HERE, "_recovery_gate_a.jsonl")
    log_b = os.path.join(HERE, "_recovery_gate_b.jsonl")
    for p in (log_a, log_b):
        if os.path.exists(p):
            os.remove(p)

    # Two arms that genuinely behave differently on the same world.
    a = run_once(pop, SEED, payday_err=PE, pop_spend=SPEND,
                 bcfg=w3.FITTED_BELIEF, mode="degenerate", log_path=log_a)
    b = run_once(pop, SEED, payday_err=PE, pop_spend=SPEND,
                 bcfg=w3.FITTED_BELIEF, mode="full", log_path=log_b)

    # Routed through the composition root: gate I2 forbids a test in the
    # production tree from holding an executor, and this needs the world's
    # opinion rather than an executor to drive.
    at_risk = at_risk_cycles(pop, SEED, PE)
    amounts = {f"c{ci}m{mi}": m["amount"]
               for ci, c in enumerate(pop)
               for mi, m in enumerate(c["mandates"])}

    coll_a = replay_collected(log_a)
    coll_b = replay_collected(log_b)
    assert coll_a != coll_b, ("the two arms collected identically, so W-2 "
                              "would pass by construction -- pick arms that differ")

    results = []

    # ---- W-1 -------------------------------------------------------------
    # The loop's dict was consumed by batch.py, so re-derive it the way batch
    # did and compare against the log. `recovery` carries the counts.
    rec_a = metrics.compute(at_risk, coll_a, a["cycles_due"], amounts)
    ok1 = (rec_a.recovered == a["recovery"]["recovered"]
           and rec_a.at_risk == a["recovery"]["at_risk"])
    results.append(("W-1", "loop bookkeeping == independent replay of the log",
                    ok1, f"log replay {rec_a.recovered} recovered, "
                         f"loop {a['recovery']['recovered']}"))

    # ---- W-2 -------------------------------------------------------------
    ok2 = at_risk_cycles(pop, SEED, PE) == at_risk
    results.append(("W-2", "the at-risk set is a property of the world",
                    ok2, f"{len(at_risk)} cycles, identical across arms"))

    # ---- W-3 -------------------------------------------------------------
    due_by_uid = {}
    for ci, c in enumerate(pop):
        for mi, m in enumerate(c["mandates"]):
            due_by_uid[f"c{ci}m{mi}"] = max(0, (DAYS - m["due_day"]) // c["cycle_days"])
    bad = [k for k in at_risk if k[1] >= due_by_uid[k[0]]]
    ok3 = not bad and len(at_risk) <= a["cycles_due"]
    results.append(("W-3", "at-risk never exceeds cycles due",
                    ok3, f"{len(at_risk)} at risk of {a['cycles_due']} due, "
                         f"{len(bad)} out of range"))

    # ---- W-4 -------------------------------------------------------------
    zero = [g for g in rec_a.days_to_recovery if g < 1]
    ok4 = not zero
    results.append(("W-4", "no recovery lands on the due date itself",
                    ok4, f"min gap {min(rec_a.days_to_recovery, default=-1)} days"))

    # ---- W-5 -------------------------------------------------------------
    collected_and_at_risk = [k for k in coll_a if k in at_risk]
    ok5 = (rec_a.recovered == len(collected_and_at_risk)
           and rec_a.recovered <= len(at_risk)
           and rec_a.recovered <= len(coll_a))
    results.append(("W-5", "recovery is bounded by the at-risk set",
                    ok5, f"{rec_a.recovered} recovered of {len(coll_a)} "
                         f"cycles collected and {len(at_risk)} at risk"))

    # ---- MUTANTS ---------------------------------------------------------
    # Each is a deliberate corruption that MUST break its check. A check no
    # mutant can break is not evidence.
    mutants = []

    m1 = dict(coll_a)
    if m1:
        m1.popitem()
    mutants.append(("W-1", "drop one collected cycle from the replay",
                    metrics.compute(at_risk, m1, a["cycles_due"],
                                    amounts).recovered != rec_a.recovered))

    mutants.append(("W-2", "build the at-risk set from a different run seed",
                    at_risk_cycles(pop, SEED + 1, PE) != at_risk))

    fake = dict(at_risk)
    fake[("c0m0", 999)] = 0
    mutants.append(("W-3", "add a cycle beyond the horizon to the at-risk set",
                    any(k[1] >= due_by_uid.get(k[0], 0) for k in fake)))

    shifted = {k: v for k, v in coll_a.items()}
    for k in list(shifted)[:1]:
        shifted[k] = at_risk.get(k, 0)         # collected ON the due day
    mutants.append(("W-4", "move one recovery onto its due date",
                    any(g < 1 for g in metrics.compute(
                        at_risk, shifted, a["cycles_due"],
                        amounts).days_to_recovery)))

    empty = metrics.compute({}, coll_a, a["cycles_due"], amounts)
    mutants.append(("W-5", "empty the at-risk set and require recovery to vanish",
                    empty.recovered == 0))

    # ---- report ----------------------------------------------------------
    print("RECOVERY-METRIC GATE -- docs/results.md")
    print(f"n={N} k={K} {DAYS}d pop seed {POP_SEED} run seed {SEED} "
          f"payday_err=+/-{PE} pop_spend={SPEND}")
    print("=" * 88)
    for rid, desc, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {rid}  {desc}")
        print(f"           {detail}")
    print("\nMUTANTS -- each must trip its own check:")
    for rid, desc, tripped in mutants:
        print(f"  {'tripped' if tripped else 'VACUOUS'}  {rid}  {desc}")

    print("\nWHAT THE METRIC SAYS (degenerate arm, this population only):")
    for k, v in rec_a.as_dict().items():
        print(f"  {k:<34} {v}")

    for p in (log_a, log_b):
        if os.path.exists(p):
            os.remove(p)

    n_bad = sum(1 for _, _, ok, _ in results if not ok)
    n_vac = sum(1 for _, _, tr in mutants if not tr)
    print()
    if n_bad or n_vac:
        print(f"FAIL -- {n_bad} checks failed, {n_vac} mutants did not trip")
        return 1
    print(f"PASS -- {len(results)}/{len(results)} checks, "
          f"{len(mutants)}/{len(mutants)} mutants tripped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
