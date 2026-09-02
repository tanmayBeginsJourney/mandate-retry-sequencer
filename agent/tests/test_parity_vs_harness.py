"""THE ACCEPTANCE GATE FOR agent/. Degenerate mode vs harness.run.

WHY THIS EXISTS. `sim/harness.py` is a monolith with no "step one hour" entry
point, so an agent that owns its own loop must own its own execution, and
`agent/execution/sim_executor.py` is therefore a SECOND IMPLEMENTATION of the
dispatch half of a frozen file. Without this test the agent's headline number
is a number from ungated code, quoted beside gated ones. That is the numbers
rule (docs/CLAUDE.md) broken in the most expensive available place.

DEGENERATE MODE is the agent reduced to the frozen policy: retry-only,
deterministic diagnoser, no nudge, no escalate, no stop. If degenerate mode
matches `harness.run("solo_shared_pd", ...)`, then every point of difference
between degenerate and full mode is attributable to the AGENT rather than to
the timing brain -- which is the number Track 3 actually asks for.

PRE-REGISTERED, written before the first run (28 Aug 2026):

  E1  Degenerate mode matches harness within a paired 2-SE band on cycle_rec.
      This is the gate. Predicted |diff| < 0.5 pts.
  E2  Exact bit-parity on cycle_rec is ACHIEVABLE at topup_p=0, because the
      RNG consumption order is reproduced deliberately (see sim_executor's
      docstring) and the customer-major loop nesting matches. Predicted:
      exact on >= 6 of 8 populations. Confidence LOW -- this is the bonus
      gate, time-boxed to 2 hours, not the acceptance gate.
  E3  Zero Stage 0 refusals. The shipping policy reports zero violations in
      every study in docs/results.md, so an ENFORCING gate should never
      have to refuse. If refusals > 0, the agent's number is NOT the gated
      number and the report must say so.
  E4  The auditor finds zero violations in the agent's own audit log.
      Different code, different source of truth, same answer.

E3 is the one to watch. Enforcement changes behaviour in a way counting does
not: a refused action is an attempt that never happened. If refusals are
non-zero, degenerate mode is not comparable to harness and this gate is
measuring two different things.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

import numpy as np

import agent  # noqa: F401
import w3

from agent.tests._parallel import agent_job, harness_job, run_jobs

POPS = [700, 701, 702, 703, 704, 705, 706, 707]
N, K, DAYS, SPEND = 100, 5, 120, 1.05
RUN_SEED = 7


def paired(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    d = a - b
    return float(d.mean()), float(2 * d.std(ddof=1) / np.sqrt(len(d)))


CONFIGS = ((7, "fitted", "pe7 fitted"), (7, None, "pe7 unfitted"),
           (1, "fitted", "pe1 fitted"))
POP_SPEC = lambda s: (N, K, s, SPEND, DAYS)


def main() -> int:
    rows = []
    fails = []

    ajobs, hjobs = [], []
    for pe, bkey, label in CONFIGS:
        bcfg = w3.FITTED_BELIEF if bkey == "fitted" else None
        for s in POPS:
            # `cycle_value=0.0` IS THE POINT OF THIS LINE, not a convenience.
            # Parity is defined against `harness.run("solo_shared_pd")`, whose
            # objective is `w3.index_score` alone and has no continuation
            # value. The agent's shipping default is
            # `timing.DEFAULT_CYCLE_VALUE = 0.6`, so an agent run at the
            # default is deliberately NOT the harness's policy and cannot be
            # bit-identical to it.
            #
            # Pinning it to zero keeps this gate measuring what it was built to
            # measure -- that the belief filter, the forecast and the index
            # arithmetic are reproduced exactly -- rather than quietly
            # redefining parity to include a term the reference does not have.
            # Zeroing it is not loosening the test: every float the gate
            # compares is still produced by the same code path.
            ajobs.append(((label, s), POP_SPEC(s), RUN_SEED,
                          dict(payday_err=pe, pop_spend=SPEND, bcfg=bcfg,
                               mode="degenerate", cycle_value=0.0), True))
            hjobs.append(((label, s), "solo_shared_pd", POP_SPEC(s), RUN_SEED,
                          dict(payday_err=pe, pop_spend=SPEND, bcfg=bcfg)))
    A = run_jobs(agent_job, ajobs)
    H = run_jobs(harness_job, hjobs)

    if True:
        for pe, bkey, label in CONFIGS:
            ag, hn, refusals, audit_v, exact = [], [], 0, 0, 0
            for s in POPS:
                r, h = A[(label, s)], H[(label, s)]
                ag.append(r["cycle_rec"])
                hn.append(h["cycle_rec"])
                refusals += sum(r["gate_refusals"].values())
                if r["cycle_rec"] == h["cycle_rec"]:
                    exact += 1
                audit_v += r["audit_violations"]

            ag, hn = np.array(ag), np.array(hn)
            mean, se2 = paired(ag, hn)
            within = abs(mean) <= max(se2, 1e-12)
            rows.append((label, ag.mean() * 100, hn.mean() * 100, mean * 100,
                         se2 * 100, within, exact, refusals, audit_v))

    print("=" * 92)
    print("PARITY GATE -- agent degenerate mode vs harness.run('solo_shared_pd')")
    print(f"n={N}, k={K}, {len(POPS)} populations {POPS[0]}-{POPS[-1]}, "
          f"{DAYS}d, run seed {RUN_SEED}, paired 2 SE")
    print("=" * 92)
    print(f"{'config':>14s} {'agent':>8s} {'harness':>8s} {'diff':>8s} "
          f"{'2SE':>7s} {'band':>6s} {'exact':>7s} {'refus':>6s} {'auditV':>7s}")
    for (label, a, h, d, se, within, exact, refus, av) in rows:
        print(f"{label:>14s} {a:8.2f} {h:8.2f} {d:+8.4f} {se:7.4f} "
              f"{'PASS' if within else 'FAIL':>6s} {exact:>4d}/{len(POPS)} "
              f"{refus:6d} {av:7d}")
        if not within:
            fails.append(f"E1 {label}: diff {d:+.4f} pts outside 2SE {se:.4f}")
        if refus:
            fails.append(f"E3 {label}: {refus} Stage 0 refusals -- the agent's "
                         f"number is NOT harness's number")
        if av:
            fails.append(f"E4 {label}: auditor found {av} violations in the "
                         f"agent's own log")

    print()
    tot_exact = sum(r[6] for r in rows)
    tot = len(rows) * len(POPS)
    print(f"E2 (bonus, not the gate): exact bit-parity on {tot_exact}/{tot} runs")
    print()
    if fails:
        print("FAIL")
        for f in fails:
            print(f"  {f}")
    else:
        print("PASS -- degenerate mode is within the paired 2-SE band of the "
              "frozen harness,")
        print("       with zero Stage 0 refusals and zero independently "
              "audited violations.")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
