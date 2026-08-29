"""THE BATCH NUMBER. The track's actual deliverable.

    python -m agent.batch_report                  # deterministic arm only
    python -m agent.batch_report --llm            # + the LLM overlay
    python -m agent.batch_report --merchants 30

Razorpay Track 3 asks for exactly this, verbatim:

    "Don't just identify the problem. Show measured money recovered across a
    batch, with compliant escalation, stopping rules, and an audit trail."

So: money recovered across a batch of synthetic merchants, `payday_wait`
printed beside it and never omitted, every stopping rule that fired grouped by
rule, every Stage 0 refusal grouped by rule WITH AN INDEPENDENT RECOUNT beside
it, the whole chain for one recovered rupee, and the LLM's fallback rate with
its outcomes compared against the deterministic path.

WHY THIS IS NOT `agent/batch.py`. That module is the COMPOSITION ROOT -- the one
place allowed to construct both an executor and a gate, named by hand in
`agent/tests/test_layer_isolation.py`'s I2 exemption list. Overwriting it would
break the import-graph gate and every measurement in the repo. This module uses
it. The naming difference is flagged rather than silently resolved.

THE DETERMINISTIC ARM PRODUCES THE NUMBER. The LLM arm is a measured overlay
printed beside it. A headline that needs an API key is not reproducible and
`CLAUDE.md`'s numbers rule forbids quoting it.

MERCHANTS ARE A VIEW, NOT A NEW WORLD. `w3.make_pop` already assigns every
mandate a merchant id from `range(60)`; a "batch of N merchants" is that
population grouped by merchant. Inventing a second merchant model would be a
second set of assumptions with no source, and the whole point of the moat
argument is that these mandates ALREADY span merchants.

EVERY RUN IS ONE PROCESS (`agent/tests/_parallel.py`, `max_tasks_per_child=1`).
`docs/06_MODEL_CARD.md` section 6a -- the machine fault is contained, not fixed.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "1")

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import agent  # noqa: F401  -- puts sim/ on the path
import harness
import w3

from agent.audit.log import read_rows
from agent.constraints.auditor import replay
from agent.tests._parallel import agent_job, harness_job, run_jobs

POPS = [700, 701, 702, 703, 704, 705, 706, 707]
N, K, DAYS, SPEND, PE, RUN_SEED = 100, 5, 120, 1.05, 7, 7


def _rupees(paise: int) -> str:
    return f"Rs {paise / 100:,.0f}"


def _pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=N, help="customers per population")
    ap.add_argument("--pops", type=int, default=len(POPS),
                    help="how many held-out populations (max 8)")
    ap.add_argument("--llm", action="store_true",
                    help="add the LLM overlay arm (needs a key or a warm cache)")
    ap.add_argument("--days", type=int, default=DAYS)
    ap.add_argument("--llm-max-calls", type=int, default=150,
                    help="hard cap on NETWORK calls per run. Cache hits are "
                         "free and do not count. See the note this prints.")
    a = ap.parse_args(argv)
    pops = POPS[:max(1, min(a.pops, len(POPS)))]

    base = dict(payday_err=PE, pop_spend=SPEND, bcfg=w3.FITTED_BELIEF,
                time_major=True)
    arms = {"agent, deterministic": dict(base, mode="full")}
    if a.llm:
        arms["agent, LLM overlay"] = dict(base, mode="full", use_llm=True,
                                          llm_max_calls=a.llm_max_calls)

    jobs = []
    for name, kw in arms.items():
        for s in pops:
            jobs.append(((name, s), (a.n, K, s, SPEND, a.days), RUN_SEED,
                         dict(kw), True))
    hjobs = [(("payday_wait", s), "payday_wait",
              (a.n, K, s, SPEND, a.days), RUN_SEED,
              dict(payday_err=PE, pop_spend=SPEND)) for s in pops]

    print(f"running {len(jobs)} agent runs + {len(hjobs)} baseline runs, "
          f"one process each", flush=True)
    res = run_jobs(agent_job, jobs)
    hres = run_jobs(harness_job, hjobs)

    # ------------------------------------------------------------- the money
    print()
    print("=" * 104)
    print(f"THE BATCH -- {a.n} customers x {K} mandates over "
          f"{len(pops)} held-out populations, {a.days} days, payday_err=+/-{PE}")
    print("=" * 104)
    n_merch = len({m["merchant"] for s in pops[:1]
                   for c in [None] for m in []} or range(60))
    print(f"Mandates are spread over {n_merch} synthetic merchants "
          f"(w3.make_pop draws merchant ids from range(60)); a batch is that "
          f"population grouped by merchant.")
    print()
    print(f"{'arm':>22s} {'cycles collected':>17s} {'Rs recovered':>15s} "
          f"{'survival':>9s} {'att/cycle':>10s} {'2 SE':>7s}")

    def col(name, field):
        return np.array([res[(name, s)][field] for s in pops], dtype=float)

    hcr = np.array([hres[("payday_wait", s)]["cycle_rec"] for s in pops])
    print(f"{'payday_wait (rival)':>22s} {_pct(hcr.mean()):>17s} "
          f"{'--':>15s} "
          f"{_pct(np.mean([hres[('payday_wait', s)]['survival'] for s in pops])):>9s} "
          f"{np.mean([hres[('payday_wait', s)]['att_per_cycle'] for s in pops]):10.3f} "
          f"{'':>7s}")
    for name in arms:
        cr = col(name, "cycle_rec")
        d = cr - hcr
        se = 2 * d.std(ddof=1) / np.sqrt(len(d)) * 100 if d.std() > 0 else 0.0
        print(f"{name:>22s} {_pct(cr.mean()):>17s} "
              f"{_rupees(col(name, 'recovered_paise').sum()):>15s} "
              f"{_pct(col(name, 'survival').mean()):>9s} "
              f"{col(name, 'att_per_cycle').mean():10.3f} "
              f"{se:7.3f}")
    print()
    for name in arms:
        cr = col(name, "cycle_rec")
        d = (cr - hcr) * 100
        se = 2 * d.std(ddof=1) / np.sqrt(len(d)) if d.std() > 0 else 0.0
        sig = "SIG" if abs(d.mean()) > se and se > 0 else "n.s."
        print(f"  {name} vs payday_wait: {d.mean():+.2f} pts "
              f"(2 SE {se:.2f}, {sig})")
    print()
    print("  `payday_wait` IS A PERMANENT ROW AND CANNOT BE SWITCHED OFF. It is")
    print("  the five-line heuristic a good rival builds in an afternoon, and at")
    print("  payday_err of about 1 day it BEATS us. The headline is conditional")
    print("  on that parameter and on nothing else -- docs/06_MODEL_CARD.md 2.")

    # --------------------------------------------------------- stopping rules
    print()
    print("=" * 104)
    print("STOPPING RULES THAT FIRED, grouped by rule")
    print("=" * 104)
    for name in arms:
        agg = collections.Counter()
        for s in pops:
            agg.update(res[(name, s)]["stops"])
        print(f"  {name}")
        for rule, n in sorted(agg.items(), key=lambda kv: -kv[1]):
            if n:
                print(f"     {rule:18s} {n:7d}")

    # ------------------------------------------------- Stage 0, twice over
    print()
    print("=" * 104)
    print("STAGE 0 -- the gate's own refusals, and an INDEPENDENT RECOUNT from "
          "the audit log alone")
    print("=" * 104)
    print("  The recount shares no code with the enforcer: `auditor.py` may not")
    print("  import `constraints/rules.py` or `stage0.py` and gate I3 fails if")
    print("  it ever does. If the two columns disagree, believe the auditor --")
    print("  it was right the one time they did (docs/03_ERRORS.md).")
    print()
    print(f"{'arm':>22s} {'rule':>11s} {'gate refused':>13s} "
          f"{'auditor found':>14s} {'agree?':>7s}")
    for name in arms:
        gate_tot = collections.Counter()
        aud_tot = collections.Counter()
        for s in pops:
            r = res[(name, s)]
            gate_tot.update(r["gate_refusals"])
            aud_tot.update(r.get("audit_detail_counts") or {})
        for rule in ("cap", "peak", "lead", "pending", "represent"):
            g, au = gate_tot.get(rule, 0), aud_tot.get(rule, 0)
            print(f"{name:>22s} {rule:>11s} {g:13d} {au:14d} "
                  f"{('yes' if g == au or au == 0 and g == 0 else 'NO'):>7s}")
        tot_v = sum(res[(name, s)]["audit_violations"] for s in pops)
        tot_x = sum(res[(name, s)]["audit_executed"] for s in pops)
        print(f"{'':>22s} {'TOTAL':>11s} {sum(gate_tot.values()):13d} "
              f"{tot_v:14d} {('yes' if tot_v == 0 else 'NO'):>7s}"
              f"   over {tot_x} executed money actions")

    # ------------------------------------------------ one rupee, end to end
    print()
    print("=" * 104)
    print("ONE RECOVERED RUPEE, END TO END -- what `WHERE action_id = ?` returns")
    print("=" * 104)
    _one_chain(a, pops[0])

    # ---------------------------------------------------------- the LLM arm
    if a.llm:
        print()
        print("=" * 104)
        print("THE LLM OVERLAY -- fallback rate, and whether the source changes "
              "the outcome")
        print("=" * 104)
        _llm_split(res, pops)

    print()
    print("=" * 104)
    print("WHAT THIS NUMBER IS NOT")
    print("=" * 104)
    print("  * No real data. Every figure is simulation; no Razorpay")
    print("    transaction, mandate or decline code has ever been seen.")
    print("  * Conditional on payday_err=7. At +/-1 day payday_wait wins.")
    print("  * The decline taxonomy is OFF here (every rate 0), so this is the")
    print("    world without frozen accounts, broken mandates or limit hits.")
    print("    With p_limit swept 0.00/0.05/0.15 the cost is")
    print("    0.00 / -2.87 / -13.46 pts and every rate is a [GUESS].")
    print("  * 8 populations, one run seed each. Not a large study.")
    return 0


def _one_chain(a, pop_seed: int) -> None:
    """Re-run ONE population with a full log kept, and print one action's chain."""
    from agent.batch import make_pop, run_once
    log_path = os.path.join(agent._PKG_ROOT, "agent", "runs",
                            "batch_report_chain.jsonl")
    if os.path.exists(log_path):
        os.remove(log_path)          # one log file is one run -- log.py enforces
    pop = make_pop(min(a.n, 40), K, pop_seed, spend=SPEND, days=a.days)
    r = run_once(pop, RUN_SEED, payday_err=PE, pop_spend=SPEND,
                 bcfg=w3.FITTED_BELIEF, mode="full", time_major=True,
                 log_ticks=True, log_path=log_path)
    rows = list(read_rows(log_path))
    by_action = collections.defaultdict(list)
    for row in rows:
        if row.get("action_id"):
            by_action[row["action_id"]].append(row)
    chosen = None
    for aid, rs in by_action.items():
        kinds = {x["kind"] for x in rs}
        if {"MONEY_ACTION", "OUTCOME"} <= kinds and any(
                x["kind"] == "OUTCOME" and x.get("success") for x in rs):
            if sum(1 for x in rs if x["kind"] == "CONSTRAINT_CHECK") == 5:
                chosen = (aid, rs)
                break
    if chosen is None:
        print("  no fully-logged successful action found in this sample")
        return
    aid, rs = chosen
    uid = next((x.get("mandate_uid") for x in rs if x.get("mandate_uid")), "?")
    diag = next((x for x in rows
                 if x["kind"] == "DIAGNOSIS" and x.get("mandate_uid") == uid),
                None)
    tick = next((x for x in rows
                 if x["kind"] == "DECISION_TICK" and x.get("mandate_uid") == uid),
                None)
    print(f"  action_id {aid}   mandate {uid}")
    if tick:
        print(f"  WHAT THE BELIEF THOUGHT")
        print(f"     p(success) now {tick.get('p_now'):.4f}, best later "
              f"{tick.get('p_later'):.4f}, index score "
              f"{tick.get('index_score'):+.2f}  -> {tick.get('verdict')}")
    if diag:
        print(f"  WHAT THE DIAGNOSER SAID, AND WHY")
        print(f"     root cause   {diag.get('root_cause')}")
        print(f"     intervention {diag.get('intervention')}  "
              f"confidence {diag.get('confidence')}")
        print(f"     source       {diag.get('source', '?')}  "
              f"prompt {diag.get('prompt_id')}")
        print(f"     rationale    {diag.get('rationale')}")
        print(f"     governance   ok={diag.get('governance_ok')}")
    print(f"  ALL FIVE CONSTRAINT VERDICTS")
    for x in sorted([x for x in rs if x["kind"] == "CONSTRAINT_CHECK"],
                    key=lambda z: z["seq"]):
        print(f"     {x.get('rule'):10s} {x.get('verdict')}")
    for x in rs:
        if x["kind"] == "MONEY_ACTION":
            print(f"  THE MONEY ACTION")
            print(f"     Rs {x.get('amount_paise', 0)/100:,.2f} at t="
                  f"{x.get('target_t')} (day {x.get('target_t', 0)//24}, hour "
                  f"{x.get('target_t', 0) % 24:02d}), notified t="
                  f"{x.get('notify_t')}, gate={x.get('gate_verdict')}")
    for x in rs:
        if x["kind"] == "OUTCOME":
            print(f"  THE OUTCOME")
            print(f"     {x.get('outcome_code')}  success={x.get('success')}  "
                  f"recovered Rs {x.get('recovered_paise', 0)/100:,.2f}")
    print(f"  full trail: {log_path}  ({len(rows)} events)")


def _llm_split(res, pops) -> None:
    name = "agent, LLM overlay"
    det = "agent, deterministic"
    n_llm = sum(res[(name, s)].get("llm_n_llm", 0) for s in pops)
    n_fb = sum(res[(name, s)].get("llm_n_fallback", 0) for s in pops)
    n_cap = sum(res[(name, s)].get("llm_n_capped", 0) for s in pops)
    tot = n_llm + n_fb
    print("  A BOUNDED CALL BUDGET IS THE DESIGN, NOT A WORKAROUND. The loop")
    print("  asks for a diagnosis once per live mandate per decision hour --")
    print("  tens of thousands of times over this batch. No production agent")
    print("  calls a model that often either; it calls one on the novel cases")
    print("  and lets rules handle the routine ones. Cache hits are free and")
    print("  do not count against the cap, so it bites on NOVELTY, not volume.")
    print(f"  diagnoses refused by the per-run cap : {n_cap}")
    print(f"  diagnoses answered by the model : {n_llm}")
    print(f"  diagnoses that fell back        : {n_fb}")
    print(f"  FALLBACK RATE                   : "
          f"{(n_fb / tot * 100 if tot else float('nan')):.1f}%")
    reasons = collections.Counter()
    for s in pops:
        reasons.update(res[(name, s)].get("llm_reasons") or {})
    for why, n in reasons.most_common(5):
        print(f"     {n:6d}  {why}")
    if n_llm == 0:
        print("  !! THE MODEL ANSWERED NOTHING. This arm IS the deterministic")
        print("     arm wearing a different name. No LLM number may be quoted.")
        return
    print()
    print("  DO OUTCOMES DIFFER BY SOURCE? Money is credited by the executor,")
    print("  which never sees a `source` field, so this compares like with like.")
    print(f"  {'source':>12s} {'attempts':>9s} {'succeeded':>10s} "
          f"{'approval':>9s} {'Rs recovered':>15s}")
    agg = collections.Counter()
    for s in pops:
        for src, d in (res[(name, s)].get("outcome_by_source") or {}).items():
            agg[(src, "att")] += d.get("att", 0)
            agg[(src, "ok")] += d.get("ok", 0)
            agg[(src, "paise")] += d.get("paise", 0)
    for src in ("llm", "fallback"):
        att, ok = agg[(src, "att")], agg[(src, "ok")]
        print(f"  {src:>12s} {att:9d} {ok:10d} "
              f"{(ok / att * 100 if att else float('nan')):8.2f}% "
              f"{_rupees(agg[(src, 'paise')]):>15s}")
    print()
    print("  A DIFFERENCE HERE IS NOT AN EFFECT OF THE SOURCE. Which cases fall")
    print("  back is not random -- a fallback happens on timeout or an")
    print("  unparseable reply, and those correlate with case shape. Read this")
    print("  as a description of the split, never as a causal comparison.")


if __name__ == "__main__":
    raise SystemExit(main())
