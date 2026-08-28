"""WHY DOES STOP HELP? Rule 3: treat a large improvement as a bug until proven.

The ablation broke two pre-registered predictions, both in the direction that
flatters the agent -- which is the signature of all thirteen errors in
docs/03_ERRORS.md. Predicted ESCALATE in [-0.3, 0.0], measured +0.759 SIG.
Predicted STOP in [-1.0, +0.5], measured +1.371 SIG.

THE PROPOSED MECHANISM, from the ablation's own usage table: both actions halt
a mandate before it exhausts its attempts, and a mandate only dies by failing
AT the cap (`sim/harness.py:299-300`). A dead mandate stops accruing
`got_cycles` while `cyc_due` keeps counting (`harness.py:619-621`), so it
forfeits every remaining billing cycle. Deaths measured: degenerate 138,
+ESCALATE 102, +STOP 49, out of 4000 mandates.

That story is COHERENT, which is exactly what makes it dangerous. Error 5 in
this project was a coherent story about being near-optimal, told about a broken
oracle. So it gets a falsification test rather than a narrative.

PRE-REGISTERED, written before running (28 August 2026):

  E-MECH-1  If the mechanism is "preserved mandates collect in LATER cycles",
            STOP's value MUST grow with the horizon: a longer horizon means
            more surviving cycles to collect. Predict monotone increase across
            days = 60, 120, 180. At 60d predict < +1.371; at 180d predict
            > +1.371.
            IF IT IS FLAT OR INVERTED, THE MECHANISM STORY IS WRONG and the
            gain is coming from somewhere I have not identified.

  E-MECH-2  Across populations, the per-population cycle_rec gain must track
            the per-population deaths avoided. Predict Pearson r > 0.5.
            A near-zero correlation means deaths are not the channel.

  E-MECH-3  STOP must not reduce attempts-per-cycle much. If it works by
            simply attempting far less, it is buying survival by not billing,
            which is a different and much worse product. Predict att/cyc
            within 5% of degenerate.

If E-MECH-1 holds, STOP's value is CONDITIONAL ON THE HORIZON in the same way
the headline is conditional on `payday_err`, and it must be reported as a curve
rather than a number.
"""
from __future__ import annotations

import os
import sys

# BEFORE numpy is imported anywhere, in the PARENT. `sim/runner.py` explains
# why: multiprocessing re-imports the main module in each spawned child, and
# that import pulls in numpy -- so setting these inside a worker is too late.
# 16 workers each spinning up a 16-thread BLAS pool is ~256 threads, which this
# machine has been observed to fall over under (NOTES.md, the intermittent
# 0xC0000005 / BrokenProcessPool).
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "1")

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

import numpy as np

POPS = [700, 701, 702, 703, 704, 705, 706, 707]
N, K, SPEND, PE, RUN_SEED = 100, 5, 1.05, 7, 7
HORIZONS = [60, 120, 180]


def _job(args):
    import tempfile
    label, pop_seed, days, kw = args
    import agent  # noqa: F401
    import w3
    from agent.batch import make_pop, run_once
    pop = make_pop(N, K, pop_seed, spend=SPEND, days=days)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        r = run_once(pop, RUN_SEED, payday_err=PE, pop_spend=SPEND,
                     bcfg=w3.FITTED_BELIEF,
                     log_path=os.path.join(tmp, "a.jsonl"), **kw)
        r.pop("log_path", None)
    return (label, pop_seed, days), r


def main() -> int:
    from concurrent.futures import ProcessPoolExecutor

    arms = {
        "degenerate": dict(mode="degenerate"),
        "+STOP": dict(mode="full", allow_nudge=False, allow_escalate=False,
                      allow_stop=True),
    }
    jobs = [(lbl, s, d, kw) for lbl, kw in arms.items()
            for s in POPS for d in HORIZONS]
    res = {}
    with ProcessPoolExecutor(max_workers=min(len(jobs), os.cpu_count() or 4, 8)) as ex:
        for key, r in ex.map(_job, jobs, chunksize=1):
            res[key] = r

    print("=" * 88)
    print("STOP MECHANISM -- is the gain really preserved mandates collecting "
          "in later cycles?")
    print(f"n={N}, k={K}, 8 populations, payday_err={PE}, FITTED_BELIEF, "
          f"paired 2 SE")
    print("=" * 88)
    print(f"{'horizon':>8s} {'cycles':>7s} {'degen':>8s} {'+STOP':>8s} "
          f"{'gain':>8s} {'2SE':>7s} {'sig':>5s} {'dead d':>7s} {'dead s':>7s} "
          f"{'att/cyc d':>10s} {'att/cyc s':>10s}")

    gains = []
    for d in HORIZONS:
        dg = np.array([res[("degenerate", s, d)]["cycle_rec"] for s in POPS])
        st = np.array([res[("+STOP", s, d)]["cycle_rec"] for s in POPS])
        diff = st - dg
        m = diff.mean() * 100
        se = 2 * diff.std(ddof=1) / np.sqrt(len(diff)) * 100
        dd = sum(res[("degenerate", s, d)]["stops"]["MANDATE_DEAD"] for s in POPS)
        ds = sum(res[("+STOP", s, d)]["stops"]["MANDATE_DEAD"] for s in POPS)
        ad = np.mean([res[("degenerate", s, d)]["att_per_cycle"] for s in POPS])
        as_ = np.mean([res[("+STOP", s, d)]["att_per_cycle"] for s in POPS])
        gains.append(m)
        print(f"{d:8d} {d // 30:7d} {dg.mean()*100:8.2f} {st.mean()*100:8.2f} "
              f"{m:+8.3f} {se:7.3f} {'SIG' if abs(m) > se else 'n.s.':>5s} "
              f"{dd:7d} {ds:7d} {ad:10.3f} {as_:10.3f}")

    # ---- E-MECH-2: does the gain track deaths avoided, population by population?
    d = 120
    per_pop_gain = np.array([res[("+STOP", s, d)]["cycle_rec"]
                             - res[("degenerate", s, d)]["cycle_rec"]
                             for s in POPS])
    per_pop_saved = np.array([res[("degenerate", s, d)]["stops"]["MANDATE_DEAD"]
                              - res[("+STOP", s, d)]["stops"]["MANDATE_DEAD"]
                              for s in POPS], dtype=float)
    r = float(np.corrcoef(per_pop_gain, per_pop_saved)[0, 1])

    print()
    print("Per-population, 120d:")
    print(f"  {'pop':>5s} {'gain pts':>9s} {'deaths avoided':>15s}")
    for i, s in enumerate(POPS):
        print(f"  {s:5d} {per_pop_gain[i]*100:+9.3f} {per_pop_saved[i]:15.0f}")
    print(f"  Pearson r(gain, deaths avoided) = {r:+.3f}")

    print()
    print("=" * 88)
    print("PRE-REGISTERED CHECKS")
    print("=" * 88)
    v = []
    v.append(("E-MECH-1 STOP's gain grows monotonically with horizon",
              gains[0] < gains[1] < gains[2],
              f"60d {gains[0]:+.3f} -> 120d {gains[1]:+.3f} -> 180d {gains[2]:+.3f}"))
    v.append(("E-MECH-1b 60d below and 180d above the 120d figure",
              gains[0] < 1.371 < gains[2],
              f"{gains[0]:+.3f} < 1.371 < {gains[2]:+.3f}"))
    v.append(("E-MECH-2 gain tracks deaths avoided (r > 0.5)", r > 0.5,
              f"r = {r:+.3f}"))
    ad = np.mean([res[("degenerate", s, 120)]["att_per_cycle"] for s in POPS])
    as_ = np.mean([res[("+STOP", s, 120)]["att_per_cycle"] for s in POPS])
    v.append(("E-MECH-3 STOP does not buy survival by not billing",
              abs(as_ - ad) / ad < 0.05,
              f"att/cyc {ad:.3f} -> {as_:.3f} ({(as_-ad)/ad*100:+.1f}%)"))

    hits = 0
    for name, passed, detail in v:
        hits += 1 if passed else 0
        print(f"  {'HELD ' if passed else 'BROKE'}  {name}   [{detail}]")
    print()
    print(f"Pre-registration record for this measurement: {hits}/{len(v)}")
    if hits < len(v):
        print()
        print("A BROKEN prediction here means the mechanism story is wrong and")
        print("the +1.371 has an unidentified cause. Do not quote it until the")
        print("cause is named.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
