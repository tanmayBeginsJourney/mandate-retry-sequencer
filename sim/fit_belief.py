#!/usr/bin/env python3
"""Fit the Bayes filter on training populations.

Selection maximises mean cycle collection across
``payday_err in {1, 3, 5, 7, 10, 14}`` on population seeds 600-607. Evaluation
seeds 700-707 are reported only after selection. The objective reads outcomes,
not hidden payday, salary or spending values.

The search is coordinate-wise: stride, then the payday prior (window, day-zero
weight and soft floor), then the spend correction, repeated for two passes. It
can miss interactions and is not an exhaustive grid.

The previous version only scored ``payday_err=7`` and had no ``prior_floor``
candidate, so it could not produce the then-shipping constant. This version
includes both parts of the stated robust-fit procedure. On 31 August 2026
the search selected ``prior_w=9, prior_floor=0.5`` and that configuration
was adopted as ``w3.FITTED_BELIEF``. A rerun that selects something else
still refuses to overwrite the shipping record.
"""
import itertools
import ast
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import numpy as np
import runner
import w3

TRAIN = list(range(600, 608))
EVAL = list(range(700, 708))
PAYDAY_ERRS = (1, 3, 5, 7, 10, 14)
N, K, DAYS, SPEND, POP_SPEND = 100, 5, 120, 1.05, 1.05
OUT = os.path.join(HERE, "fitted_belief.json")
CACHE = os.path.join(HERE, "ml_artifacts", "belief_fit_cache_v2.json")
WORKERS = int(os.environ.get(
    "FIT_WORKERS", min(os.cpu_count() or 4, 24)))
# Cache fingerprints that remain valid because only comments, the shipping
# constant, or fitter reporting code changed — not BeliefPD maths or the
# harness. Accept once; the next checkpoint rewrites the current fingerprint.
LEGACY_CACHE_FINGERPRINTS = {
    "67b074304fd7afe91265844bee18200d41bfc4bf93b65307a385a71435f2d38c",
    # Cache written before FITTED_BELIEF was adopted from this search.
    "b552fea7c03a52abbc2c1a6e32982e6ff40cf544b8840a9e2fddd37d2591af44",
}

BASE = dict(stride=3, prior_w=None, prior_day0=1.0,
            prior_floor=1e-6, spend_beta=0.045)
# The configuration that shipped before the 31 August 2026 adoption.
# Kept so the committed record still shows the train/eval comparison.
FORMER_SHIPPING = dict(stride=1, prior_w=12, prior_day0=8.0,
                       prior_floor=0.25, spend_beta=0.0)


def model_fingerprint() -> str:
    h = hashlib.sha256()
    for name in ("w3.py", "harness.py"):
        with open(os.path.join(HERE, name), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=name)
        h.update(ast.dump(tree, include_attributes=False).encode("utf-8"))
    return h.hexdigest()


def load_cache() -> dict:
    if not os.path.exists(CACHE):
        return {}
    with open(CACHE, encoding="utf-8") as fh:
        saved = json.load(fh)
    saved_fingerprint = saved.get("model_fingerprint")
    if (saved_fingerprint != model_fingerprint()
            and saved_fingerprint not in LEGACY_CACHE_FINGERPRINTS):
        print("Ignoring fit cache from different model or fitter code.")
        return {}
    if saved_fingerprint in LEGACY_CACHE_FINGERPRINTS:
        print("Using compatible pre-record cache; model files are unchanged.")
    return saved.get("cycle_rec", {})


def save_cache(values: dict) -> None:
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({
            "model_fingerprint": model_fingerprint(),
            "numpy": np.__version__,
            "python": sys.version.split()[0],
            "cycle_rec": values,
        }, fh, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, CACHE)


def case_key(pol, cfg, pe, population):
    return json.dumps({
        "policy": pol,
        "config": cfg,
        "payday_err": pe,
        "population": population,
        "run_seed": 900 + population,
        "n": N, "k": K, "days": DAYS,
        "spend": SPEND, "pop_spend": POP_SPEND,
    }, sort_keys=True, separators=(",", ":"))


def score(cfgs, seeds, pol="solo_shared_pd", payday_errs=PAYDAY_ERRS):
    """Return mean cycle collection, checkpointing after every candidate."""
    cached = load_cache()
    scores = {}
    for index, (label, cfg) in enumerate(cfgs.items(), 1):
        jobs = []
        for pe in payday_errs:
            for s in seeds:
                key = case_key(pol, cfg, pe, s)
                if key in cached:
                    continue
                jobs.append((
                    key, pol,
                    (N, K, s, SPEND, DAYS), 900 + s,
                    dict(payday_err=pe, pop_spend=POP_SPEND, bcfg=cfg)))
        if jobs:
            res = runner.run_jobs(jobs, workers=WORKERS)
            cached.update({key: float(row["cycle_rec"])
                           for key, row in res.items()})
            save_cache(cached)
        scores[label] = float(np.mean([
            cached[case_key(pol, cfg, pe, s)]
            for pe in payday_errs for s in seeds
        ]))
        print(f"  candidate {index:>2}/{len(cfgs)}  {label:<52} "
              f"{scores[label]*100:6.2f}%  "
              f"({len(jobs)} new runs)", flush=True)
    return scores


def stage(name, cfgs, seeds=TRAIN):
    t0 = time.perf_counter()
    sc = score(cfgs, seeds)
    best = max(sc, key=sc.get)
    print(f"\n--- {name}  ({len(cfgs)} configs x {len(seeds)} populations "
          f"x {len(PAYDAY_ERRS)} payday-error cells, "
          f"{time.perf_counter()-t0:.0f}s)")
    for label in sorted(sc, key=lambda x: -sc[x]):
        mark = "  <-- best" if label == best else ""
        print(f"    {label:<40} {sc[label]*100:6.2f}%{mark}")
    return cfgs[best], sc


def sweep_stride(cfg, tag):
    return stage(f"{tag}: stride", {
        f"stride={s}": dict(cfg, stride=s) for s in (1, 2, 3)
    })[0]


def sweep_prior(cfg, tag):
    grid = {"prior=exp(-0.10d) [base]": dict(
        cfg, prior_w=None, prior_day0=1.0, prior_floor=1e-6)}
    for w, d0, floor in itertools.product(
            (5, 7, 9, 12, 15), (1.0, 2.0, 4.0, 8.0),
            (1e-6, 0.10, 0.25, 0.50)):
        grid[f"prior=uniform(w={w}) day0x{d0:g} floor={floor:g}"] = dict(
            cfg, prior_w=w, prior_day0=d0, prior_floor=floor)
    return stage(f"{tag}: payday prior", grid)[0]


def sweep_beta(cfg, tag):
    return stage(f"{tag}: spend_beta", {
        f"spend_beta={b:g}": dict(cfg, spend_beta=b)
        for b in (0.0, 0.02, 0.045, 0.07, 0.10, 0.14)
    })[0]


def score_by_pe(cfgs, seeds):
    return {
        str(pe): score(cfgs, seeds, payday_errs=(pe,))
        for pe in PAYDAY_ERRS
    }


def check_record() -> int:
    if not os.path.exists(OUT):
        print(f"FAIL  missing {OUT}")
        return 1
    with open(OUT, encoding="utf-8") as fh:
        record = json.load(fh)
    if record.get("model_fingerprint") != model_fingerprint():
        print("FAIL  fitted_belief.json was produced by different model code")
        return 1
    got = record.get("shipping")
    want = dict(w3.FITTED_BELIEF)
    if got != want:
        print(f"FAIL  record shipping={got}; w3.FITTED_BELIEF={want}")
        return 1
    selected = record.get("selected")
    matches = selected == want
    if record.get("matches_shipping") != matches:
        print("FAIL  matches_shipping disagrees with the recorded configs")
        return 1
    print("PASS  fitted_belief.json matches the shipping constant and records "
          f"selection match={matches}")
    return 0


def main():
    if "--check" in sys.argv:
        return check_record()

    print("Selecting on training populations 600-607 by mean cycle_rec across "
          f"payday_err={PAYDAY_ERRS}, n={N}.")
    print(f"Unfitted baseline (old handicaps, not FITTED_BELIEF): {BASE}")

    # TWO PASSES, and the second one is not ceremony. The first run of this
    # search picked stride=2 in pass 1 -- chosen against the OLD prior -- and
    # once the prior was fitted, stride=1 became better on both train and eval.
    # Coordinate-wise search is only honest if you go round twice and say what
    # moved. It can still miss an interaction the second pass does not surface.
    cfg = dict(BASE)
    for tag in ("pass 1", "pass 2"):
        cfg = sweep_stride(cfg, tag)
        cfg = sweep_prior(cfg, tag)
        cfg = sweep_beta(cfg, tag)
        print(f"\n  after {tag}: {cfg}")

    print("\n" + "=" * 78)
    print(f"FITTED CONFIG: {cfg}")
    print("=" * 78)

    # --- report on held-out evaluation populations --------------------------
    shipping = dict(w3.FITTED_BELIEF)
    both = {"base": BASE, "selected": cfg, "shipping": shipping}
    if FORMER_SHIPPING != shipping:
        both["former"] = dict(FORMER_SHIPPING)
    tr_by_pe = score_by_pe(both, TRAIN)
    ev_by_pe = score_by_pe(both, EVAL)
    tr = {label: float(np.mean([tr_by_pe[str(pe)][label]
                                for pe in PAYDAY_ERRS]))
          for label in both}
    ev = {label: float(np.mean([ev_by_pe[str(pe)][label]
                                for pe in PAYDAY_ERRS]))
          for label in both}
    print(f"\n{'':<14}{'train 600-607':>16}{'eval 700-707':>16}")
    for label in both:
        print(f"{label:<14}{tr[label]*100:>15.2f}%{ev[label]*100:>15.2f}%")

    record = dict(
        schema=1,
        status=("reproduced" if cfg == shipping
                else "shipping configuration not selected"),
        procedure="two-pass coordinate search",
        model_fingerprint=model_fingerprint(),
        objective="mean cycle_rec across payday_err cells on training populations",
        payday_errs=list(PAYDAY_ERRS),
        train_populations=TRAIN,
        evaluation_populations=EVAL,
        n=N, k=K, days=DAYS, spend=SPEND, pop_spend=POP_SPEND,
        selected=cfg,
        shipping=shipping,
        former_shipping=dict(FORMER_SHIPPING),
        matches_shipping=cfg == shipping,
        base=BASE,
        selection_margin_points=(tr["selected"] - tr["shipping"]) * 100,
        train_mean=tr,
        evaluation_mean=ev,
        train_by_payday_err=tr_by_pe,
        evaluation_by_payday_err=ev_by_pe,
        numpy=np.__version__,
        python=sys.version.split()[0],
    )
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(f"\nsaved {OUT}")
    if cfg != shipping:
        print("\nFAIL: the rerun selected a different configuration.")
        print(f"  selected: {cfg}")
        print(f"  shipping: {shipping}")
        print("The mismatch is preserved in fitted_belief.json. The shipping "
              "constant and published numbers are unchanged.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
