"""THE ACTION ABLATION. What is the agent's action space actually worth?

Degenerate mode reproduces `harness.run("solo_shared_pd", ...)` EXACTLY (24/24
runs bit-identical, `test_parity_vs_harness.py`). So every point of difference
between degenerate and any other arm here is attributable to the AGENT, not to
the timing brain. That is the isolation the whole build order was for.

THE ACTION SPACE -- what each costs, what it credits, and where that comes from
-----------------------------------------------------------------------------
RETRY     costs one attempt against the NPCI cap of 4, and on failure at the
          cap it kills the mandate, forfeiting its remaining billing cycles.
          Credits the debit amount on success.
          Source: sim/harness.py dispatch + w3.balance_trace. [REPORTED] cap.

WAIT      costs one day of the cycle. Credits nothing directly; it buys the
          option on a better day. Already inside the frozen policy as
          `index_score <= 0` -- it is not an agent action and is not ablated.
          Source: w3.index_score. Its 0.92 discount is hand-chosen and every
          number here inherits that ~7-point band (docs/06_MODEL_CARD.md §3).

NUDGE     costs one decision day: no attempt is scheduled that day. Credits,
          with probability `nudge_p`, amount x1.15 available for 48h from t+2.
          Source: `harness.run`'s `topup_p` mechanism, made CONDITIONAL on the
          agent acting. NO VALUE IS PICKED -- `nudge_p` is swept and the result
          is a curve, because there is no measured Indian UPI nudge take-up
          rate in docs/01_FACTS.md and inventing one would break rule 5.

ESCALATE  costs every remaining attempt in the cycle. Credits NOTHING that is
          modelled anywhere in this world. It is pure cost here, and that is a
          limit of the simulation, not a claim that escalation is worthless in
          reality. Source: none. Said out loud rather than hidden.

STOP      costs every remaining attempt in the cycle. Credits the mandate's
          survival: a mandate that dies forfeits its remaining cycles because
          `cyc_due` keeps counting while `got_cycles` stops
          (sim/harness.py:619-621), so holding an attempt back preserves
          future revenue with no new constant.
          Source: harness death rule + the cycle-based metric.

PRE-REGISTERED, written before the first run (28 August 2026)
-------------------------------------------------------------
E-ABL-1  `rules_none` (RuleBasedDiagnoser, every action disabled) equals
         `degenerate` EXACTLY on every metric. This is the consistency check:
         if it fails, the ablation is not isolating what it claims to.

E-ABL-2  NUDGE is worth approximately ZERO and is not significant at any
         `nudge_p`. Predicted |effect| < 0.6 pts at 0.10, 0.25 and 0.50, and
         possibly NEGATIVE because a nudge consumes a decision day.
         Reasoning: sweeping the UNCONDITIONAL `topup_p` on the shipping
         config gives +0.02 pts (2SE 0.59) at 0.25 -- and that fires on EVERY
         failure, so it is a strict upper bound on a nudge that fires on a
         subset. The same sweep moves `payday_wait` by +11.4 pts, so the
         mechanism is live; it is the SHIPPING POLICY that has nothing left
         for a nudge to recover, because it already collects 95.3%.

E-ABL-3  ESCALATE <= 0. Predicted -0.3 to 0.0. No modelled benefit, real cost.

E-ABL-4  STOP between -1.0 and +0.5. Survival on the shipping config is
         96.55%, so only 3.45% of mandates die; preserving ALL of them caps
         the gain near +1.7 pts, and STOP also forfeits the current cycle.

E-ABL-5  full <= degenerate. Predicted -0.5 to 0.0.

E-ABL-6  Zero Stage 0 refusals and zero independently audited violations in
         every arm.

THE HEADLINE CLAIM BEING TESTED: that the agent's action space is worth <= 0
on this metric, and that the honest deliverable is to say so and cut what does
not pay. Being wrong here would be good news and would need explaining, not
celebrating -- docs/CLAUDE.md rule 3.

`payday_wait` IS A PERMANENT ROW. Never show our number without it.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

import numpy as np

POPS = [700, 701, 702, 703, 704, 705, 706, 707]
N, K, DAYS, SPEND, PE = 100, 5, 120, 1.05, 7
RUN_SEED = 7

# (arm label, kwargs to run_once)
ARMS = [
    ("degenerate",     dict(mode="degenerate")),
    ("rules_none",     dict(mode="full", allow_nudge=False,
                            allow_escalate=False, allow_stop=False)),
    ("+NUDGE p=0.10",  dict(mode="full", allow_nudge=True, allow_escalate=False,
                            allow_stop=False, nudge_p=0.10)),
    ("+NUDGE p=0.25",  dict(mode="full", allow_nudge=True, allow_escalate=False,
                            allow_stop=False, nudge_p=0.25)),
    ("+NUDGE p=0.50",  dict(mode="full", allow_nudge=True, allow_escalate=False,
                            allow_stop=False, nudge_p=0.50)),
    ("+ESCALATE",      dict(mode="full", allow_nudge=False, allow_escalate=True,
                            allow_stop=False)),
    ("+STOP",          dict(mode="full", allow_nudge=False, allow_escalate=False,
                            allow_stop=True)),
    ("full p=0.25",    dict(mode="full", allow_nudge=True, allow_escalate=True,
                            allow_stop=True, nudge_p=0.25)),
]


def _job(args):
    """Worker. Module level so Windows spawn can re-import it safely."""
    import tempfile
    label, pop_seed, kw = args
    import agent  # noqa: F401
    import w3
    from agent.audit.log import read_rows
    from agent.batch import make_pop, run_once
    from agent.constraints.auditor import replay

    pop = make_pop(N, K, pop_seed, spend=SPEND, days=DAYS)
    # ignore_cleanup_errors: a Windows rmtree failure inside __exit__ REPLACES
    # whatever exception actually happened in the block, which cost a debugging
    # cycle once already. The masking is the bug, not the leftover file.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        r = run_once(pop, RUN_SEED, payday_err=PE, pop_spend=SPEND,
                     bcfg=w3.FITTED_BELIEF,
                     log_path=os.path.join(tmp, "a.jsonl"), **kw)
        a = replay(read_rows(r["log_path"]))
        r["audit_violations"] = a.total()
        r["audit_recovered_paise"] = a.recovered_paise
        r["audit_executed"] = a.executed
        r.pop("log_path", None)
    return (label, pop_seed), r


def _baseline(pop_seed):
    import agent  # noqa: F401
    import harness
    from agent.batch import make_pop
    pop = make_pop(N, K, pop_seed, spend=SPEND, days=DAYS)
    return pop_seed, harness.run("payday_wait", pop, RUN_SEED,
                                 payday_err=PE, pop_spend=SPEND)


def main() -> int:
    from concurrent.futures import ProcessPoolExecutor

    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ.setdefault(var, "1")

    jobs = [(label, s, kw) for label, kw in ARMS for s in POPS]
    res: dict[tuple[str, int], dict] = {}
    workers = min(len(jobs), os.cpu_count() or 4, 16)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for key, r in ex.map(_job, jobs, chunksize=1):
            res[key] = r
        base = dict(ex.map(_baseline, POPS))

    def col(label, field):
        return np.array([res[(label, s)][field] for s in POPS], dtype=float)

    deg = col("degenerate", "cycle_rec")
    pw = np.array([base[s]["cycle_rec"] for s in POPS])

    print("=" * 100)
    print("ACTION ABLATION -- degenerate -> each action added -> full")
    print(f"n={N}, k={K}, 8 populations {POPS[0]}-{POPS[-1]}, {DAYS}d, "
          f"payday_err={PE}, FITTED_BELIEF, paired 2 SE vs degenerate")
    print("=" * 100)
    print(f"{'arm':>15s} {'cycle_rec':>10s} {'vs degen':>10s} {'2SE':>7s} "
          f"{'sig':>5s} {'Rs recovered':>14s} {'surv':>7s} {'att/cyc':>8s} "
          f"{'refus':>6s} {'auditV':>7s}")

    rows = []
    for label, _kw in ARMS:
        cr = col(label, "cycle_rec")
        d = cr - deg
        m, se = d.mean(), 2 * d.std(ddof=1) / np.sqrt(len(d))
        sig = "SIG" if abs(m) > se and se > 0 else "n.s."
        rupees = col(label, "recovered_paise").sum() / 100.0
        refus = sum(sum(res[(label, s)]["gate_refusals"].values()) for s in POPS)
        av = sum(res[(label, s)]["audit_violations"] for s in POPS)
        rows.append((label, cr.mean(), m, se, sig, rupees, refus, av))
        print(f"{label:>15s} {cr.mean()*100:10.2f} {m*100:+10.3f} {se*100:7.3f} "
              f"{sig:>5s} {rupees:14,.0f} {col(label,'survival').mean()*100:7.2f} "
              f"{col(label,'att_per_cycle').mean():8.3f} {refus:6d} {av:7d}")

    print(f"{'payday_wait':>15s} {pw.mean()*100:10.2f} "
          f"{(pw-deg).mean()*100:+10.3f} "
          f"{2*(pw-deg).std(ddof=1)/np.sqrt(len(pw))*100:7.3f}  "
          f"<- the permanent baseline row. Never omit it.")

    # ---- action-usage counts, so a zero effect can be told from a zero usage
    print()
    print("Action usage (summed over 8 populations) -- a zero EFFECT and a zero"
          " USAGE are different findings:")
    print(f"{'arm':>15s} {'nudges':>8s} {'took':>7s} {'escal':>7s} "
          f"{'stops':>7s} {'waits':>8s} {'dead':>7s}")
    for label, _kw in ARMS:
        g = lambda f: int(sum(res[(label, s)][f] for s in POPS))
        dead = int(sum(res[(label, s)]["stops"]["MANDATE_DEAD"] for s in POPS))
        print(f"{label:>15s} {g('nudges'):8d} {g('nudges_took'):7d} "
              f"{g('escalations'):7d} {g('agent_stops'):7d} {g('waits'):8d} "
              f"{dead:7d}")

    # ---- pre-registered checks
    print()
    print("=" * 100)
    print("PRE-REGISTERED CHECKS")
    print("=" * 100)
    verdicts = []

    same = all(res[("rules_none", s)]["cycle_rec"] ==
               res[("degenerate", s)]["cycle_rec"] for s in POPS)
    verdicts.append(("E-ABL-1 rules_none == degenerate exactly", same,
                     "consistency: the ablation isolates what it claims to"))

    for p in ("0.10", "0.25", "0.50"):
        label = f"+NUDGE p={p}"
        m = (col(label, "cycle_rec") - deg).mean() * 100
        verdicts.append((f"E-ABL-2 NUDGE@{p} |effect| < 0.6 pts", abs(m) < 0.6,
                         f"measured {m:+.3f}"))

    m_esc = (col("+ESCALATE", "cycle_rec") - deg).mean() * 100
    verdicts.append(("E-ABL-3 ESCALATE in [-0.3, 0.0]", -0.3 <= m_esc <= 0.0,
                     f"measured {m_esc:+.3f}"))

    m_stop = (col("+STOP", "cycle_rec") - deg).mean() * 100
    verdicts.append(("E-ABL-4 STOP in [-1.0, +0.5]", -1.0 <= m_stop <= 0.5,
                     f"measured {m_stop:+.3f}"))

    m_full = (col("full p=0.25", "cycle_rec") - deg).mean() * 100
    verdicts.append(("E-ABL-5 full <= degenerate", m_full <= 0.0,
                     f"measured {m_full:+.3f}"))

    tot_ref = sum(r[6] for r in rows)
    tot_av = sum(r[7] for r in rows)
    verdicts.append(("E-ABL-6 zero refusals and zero audited violations",
                     tot_ref == 0 and tot_av == 0,
                     f"refusals={tot_ref} auditV={tot_av}"))

    hits = 0
    for name, passed, detail in verdicts:
        hits += 1 if passed else 0
        print(f"  {'HELD ' if passed else 'BROKE'}  {name}   [{detail}]")
    print()
    print(f"Pre-registration record for this measurement: {hits}/{len(verdicts)}")
    print()
    print("A BROKEN prediction is a finding and goes in NOTES.md with its")
    print("mechanism. The non-zero exit prevents automation calling it a pass.")
    return 0 if hits == len(verdicts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
