#!/usr/bin/env python3
"""
T9 REFERENCE CAPTURE.

Writes sim/t9_reference.json: the exact output of every policy at both
operating points, captured BEFORE any performance work, so that any later
optimisation can be proved to have changed nothing.

WHY THE BITS MATTER. Every scalar is stored as `float.hex()`, which
round-trips exactly. Storing rounded decimals would make T9 a gate that
cannot fail -- a comparison at 3 decimal places passes for any change that
does not move the result by 0.1%, which is most changes. This project has
shipped three gates that could not fail; this is not going to be the fourth.

WHAT EACH FIELD ACTUALLY TESTS -- read this before trusting T9.

  The five headline metrics (cycle_rec, approval, survival, att_per_cycle,
  starvation) are all RATIOS OF INTEGER COUNTS. Their float.hex() is exact,
  but they are COARSE: a last-ulp change deep in the belief filter only moves
  them if it flips a scheduling decision. So the metrics test
  DECISION-IDENTITY, not float-identity.

  `calib_sha256` is the sha256 of the raw float64 bytes of every predicted
  P(success) recorded at every dispatch, in order. That IS float-identity: a
  single ulp anywhere in BeliefPD.p_success changes it. It is only populated
  for policies that carry a belief (harness.BELIEF_POLS).

  A change that keeps the metrics and breaks calib_sha256 is a change that
  perturbed the arithmetic but not (yet) any decision. That is exactly the
  signature of vectorising the filter, and it is why the two are separate.

Run:  python sim/t9_reference.py            (writes the file)
      python sim/t9_reference.py --check    (compares, does not write)
"""
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np
import w3
import harness

REF_PATH = os.path.join(HERE, "t9_reference.json")

# The population the suite itself uses for most gates: tests.pop() defaults.
POP_SPEC = dict(n=60, k=5, seed=1, spend=1.05, days=120)
RUN_SEED = 7
POP_SPEND = 1.05
PAYDAY_ERRS = (1, 7)

POLICIES = ("baseline_doc", "baseline_legal", "payday_wait", "myopic",
            "solo_naive", "solo_pop", "solo_placebo", "solo_shared",
            "portfolio", "solo_pop_pd", "solo_shared_pd", "portfolio_pd",
            "solo_placebo_pd", "oracle")

SCALARS = ("cycle_rec", "approval", "survival", "att_per_cycle", "starvation")


def build_pop():
    s = POP_SPEC
    return w3.make_pop(s["n"], s["k"], np.random.default_rng(s["seed"]),
                       days=s["days"], spend=s["spend"])


def fingerprint(res):
    """Exact, round-trippable fingerprint of one harness.run result."""
    out = {k: float(res[k]).hex() for k in SCALARS}
    out["cycles_due"] = int(res["cycles_due"])
    out["violations"] = int(res["violations"])
    out["vdetail"] = {k: int(v) for k, v in sorted(res["vdetail"].items())}
    cal = res["calib"]
    out["calib_n"] = len(cal)
    if cal:
        arr = np.asarray([c[0] for c in cal], dtype=np.float64)
        ok = np.asarray([1 if c[1] else 0 for c in cal], dtype=np.int8)
        h = hashlib.sha256()
        h.update(arr.tobytes())
        h.update(ok.tobytes())
        out["calib_sha256"] = h.hexdigest()
        # a few raw values, in hex, so a mismatch is debuggable rather than
        # just a hash that differs
        out["calib_head"] = [float(x).hex() for x in arr[:3]]
        out["calib_tail"] = [float(x).hex() for x in arr[-3:]]
    else:
        out["calib_sha256"] = None
    return out


def capture():
    pop = build_pop()
    runs = {}
    for pe in PAYDAY_ERRS:
        for pol in POLICIES:
            t0 = time.perf_counter()
            res = harness.run(pol, pop, RUN_SEED, payday_err=pe,
                              pop_spend=POP_SPEND, collect_calib=True)
            dt = time.perf_counter() - t0
            key = f"{pol}|pe{pe}"
            runs[key] = fingerprint(res)
            print(f"  {key:<28} rec={res['cycle_rec']*100:6.2f}%  "
                  f"calib_n={runs[key]['calib_n']:>6}  {dt:6.2f}s", flush=True)
    return runs


def meta():
    return dict(
        note="Captured before any performance work. See sim/t9_reference.py.",
        pop_spec=POP_SPEC, run_seed=RUN_SEED, pop_spend=POP_SPEND,
        payday_errs=list(PAYDAY_ERRS), policies=list(POLICIES),
        collect_calib=True,
        numpy=np.__version__,
        python=sys.version.split()[0],
    )


def diff_against(runs, ref):
    """Every field that differs, oldest-first. Used by --check and --recapture."""
    out = []
    for key in sorted(set(runs) | set(ref)):
        got, want = runs.get(key), ref.get(key)
        if want is None:
            out.append(f"{key}: NEW (not in reference)")
            continue
        if got is None:
            out.append(f"{key}: GONE (in reference, not captured)")
            continue
        for field in sorted(set(want) | set(got)):
            if want.get(field) != got.get(field):
                a, b = want.get(field), got.get(field)
                extra = ""
                if field in SCALARS:
                    try:
                        extra = (f"   ({float.fromhex(a)*100:.2f}% -> "
                                 f"{float.fromhex(b)*100:.2f}%)")
                    except Exception:
                        pass
                out.append(f"{key}.{field}: {a} -> {b}{extra}")
    return out


def main():
    check = "--check" in sys.argv
    recapture = "--recapture" in sys.argv
    t0 = time.perf_counter()
    runs = capture()
    print(f"\ntotal capture time: {time.perf_counter()-t0:.1f}s")

    if recapture:
        # A DELIBERATE re-baseline. The whole risk of this mode is that someone
        # regenerates the reference to make T9 go green and never looks at what
        # moved -- which would turn T9 into a gate that cannot fail, the exact
        # error this project has made three times. So the diff is printed in
        # full, unconditionally, and the instruction to paste it into NOTES.md
        # is part of the output rather than a convention someone can forget.
        with open(REF_PATH, encoding="utf-8") as fh:
            old = json.load(fh)["runs"]
        d = diff_against(runs, old)
        print("\n" + "=" * 78)
        print(f"RE-BASELINE DIFF against the existing reference: "
              f"{len(d)} field(s) changed")
        print("=" * 78)
        for line in d:
            print("   " + line)
        if not d:
            print("   (nothing changed -- a re-baseline was not needed)")
        print("=" * 78)
        print("PASTE THIS DIFF INTO NOTES.md WITH THE REASON FOR EACH CHANGE.")
        print("A reference regenerated without its diff on the record makes T9")
        print("a gate that cannot fail.")
        print("=" * 78)

    if check:
        with open(REF_PATH, encoding="utf-8") as fh:
            ref = json.load(fh)
        bad = []
        for key, got in runs.items():
            want = ref["runs"].get(key)
            if want is None:
                bad.append(f"{key}: missing from reference")
                continue
            for field in sorted(set(want) | set(got)):
                if want.get(field) != got.get(field):
                    bad.append(f"{key}.{field}: ref={want.get(field)} "
                               f"got={got.get(field)}")
        if bad:
            print(f"\nMISMATCH ({len(bad)}):")
            for b in bad[:40]:
                print("   ", b)
            return 1
        print("\nEXACT MATCH against sim/t9_reference.json")
        return 0

    with open(REF_PATH, "w", encoding="utf-8") as fh:
        json.dump(dict(meta=meta(), runs=runs), fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(f"wrote {REF_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
