#!/usr/bin/env python3
"""
THE ML BASELINE, AND THE MISSPECIFICATION STUDY.

Read docs/01_FACTS.md and the pre-registration in NOTES.md before the numbers.
The one thing that has to stay in front of every result on this page:

    w3.Belief and w3.BeliefPD are hand-built to match w3.balance_trace -- same
    spend shape (hourly_spend_profile), same payday model. THE BAYES FILTER IS
    THE TRUE GENERATIVE MODEL OF THIS WORLD. Any ML comparison run only
    in-distribution is biased toward Bayes by construction and must not be
    reported as a like-for-like result.

That is why the in-distribution row is not the experiment. The experiment is
what happens when the world stops obeying the filter's assumptions.

Stages:
    python sim/ml_study.py data    generate the training set from `explore`
    python sim/ml_study.py train   leakage checks, then fit LR and LightGBM
    python sim/ml_study.py eval    the misspecification table
"""
import json
import os
import pickle
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import numpy as np
import w3
import harness
import mlfeat
import runner

ART = os.path.join(HERE, "ml_artifacts")
DATA = os.path.join(ART, "train.npz")
MODEL = os.path.join(ART, "model.pkl")
RESULTS = os.path.join(ART, "misspec.json")

# Population seeds. Training and evaluation use DISJOINT populations, and the
# held-out split inside training is by population as well as by customer.
TRAIN_POPS = list(range(600, 608))     # 8 populations for fitting
HELDOUT_POPS = list(range(608, 610))   # 2 never seen during fitting
EVAL_POPS = list(range(700, 708))      # 8 more, used only for policy runs

FIT_PATH = os.path.join(ART, "belief_fit.json")


def fair_cfg():
    """The fitted belief configuration. See sim/fit_belief.py."""
    with open(FIT_PATH) as fh:
        return json.load(fh)["fitted"]


N = 100
K = 5
DAYS = 120
SPEND = 1.05
POP_SPEND = 1.05
PE = 7                                  # the contended operating point


def spec(pop_seed, **over):
    d = dict(n=N, k=K, pop_seed=pop_seed, spend=SPEND, days=DAYS)
    d.update(over)
    return d


def make_pop(s, payday_day0_frac=0.60, irregular_frac=0.0):
    return w3.make_pop(s["n"], s["k"], np.random.default_rng(s["pop_seed"]),
                       days=s["days"], spend=s["spend"],
                       payday_day0_frac=payday_day0_frac,
                       irregular_frac=irregular_frac)


# =============================================================== STAGE: data
def stage_data():
    os.makedirs(ART, exist_ok=True)
    jobs = []
    for ps in TRAIN_POPS + HELDOUT_POPS:
        jobs.append((f"explore|{ps}", "explore", (N, K, ps, SPEND, DAYS),
                     900 + ps, dict(payday_err=PE, pop_spend=POP_SPEND,
                                    collect_ml=True)))
    t0 = time.perf_counter()
    res = runner.run_jobs(jobs)
    print(f"explore runs: {len(res)} in {time.perf_counter()-t0:.1f}s")

    X, y, g_pop, g_cust = [], [], [], []
    for key, r in res.items():
        ps = int(key.split("|")[1])
        for feat, lab, ci, uid in r["ml_rows"]:
            X.append(feat)
            y.append(lab)
            g_pop.append(ps)
            g_cust.append(ps * 100000 + ci)
        print(f"  {key:<16} rows={len(r['ml_rows']):>6}  "
              f"violations={r['violations']}  rec={r['cycle_rec']*100:.2f}%")

    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.int8)
    np.savez_compressed(DATA, X=X, y=y, g_pop=np.asarray(g_pop),
                        g_cust=np.asarray(g_cust),
                        features=np.asarray(mlfeat.FEATURES))
    print(f"\nsaved {DATA}: X={X.shape} y={y.shape} "
          f"base rate={y.mean():.4f}")
    print(f"offset coverage: {np.bincount(X[:, 0].astype(int))[:15]}")


def stage_data_pd():
    """Training rows for the HYBRID, from explore_pd -- the same random day
    choice as `explore`, but carrying the fitted payday-posterior belief so
    each row is tagged with the filter's own summaries at that decision."""
    os.makedirs(ART, exist_ok=True)
    cfg = fair_cfg()
    jobs = [(f"explore_pd|{ps}", "explore_pd", (N, K, ps, SPEND, DAYS), 900 + ps,
             dict(payday_err=PE, pop_spend=POP_SPEND, collect_ml=True, bcfg=cfg))
            for ps in TRAIN_POPS + HELDOUT_POPS]
    t0 = time.perf_counter()
    res = runner.run_jobs(jobs)
    print(f"explore_pd runs: {len(res)} in {time.perf_counter()-t0:.1f}s "
          f"(belief cfg {cfg})")
    X, y, g_pop, g_cust = [], [], [], []
    for key, r in res.items():
        ps = int(key.split("|")[1])
        for feat, lab, ci, uid in r["ml_rows"]:
            X.append(feat)
            y.append(lab)
            g_pop.append(ps)
            g_cust.append(ps * 100000 + ci)
        print(f"  {key:<18} rows={len(r['ml_rows']):>6}  "
              f"violations={r['violations']}  rec={r['cycle_rec']*100:.2f}%")
    X = np.asarray(X, dtype=np.float64)
    np.savez_compressed(os.path.join(ART, "train_pd.npz"), X=X,
                        y=np.asarray(y, dtype=np.int8),
                        g_pop=np.asarray(g_pop), g_cust=np.asarray(g_cust),
                        features=np.asarray(mlfeat.FEATURES_HYBRID))
    print(f"\nsaved train_pd.npz: X={X.shape} base rate={np.mean(y):.4f}")


def stage_train_hybrid():
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score, log_loss, brier_score_loss
    d = np.load(os.path.join(ART, "train_pd.npz"), allow_pickle=True)
    X, y, g_pop = d["X"], d["y"], d["g_pop"]
    names = list(d["features"])
    tr, te = np.isin(g_pop, TRAIN_POPS), np.isin(g_pop, HELDOUT_POPS)
    print(f"hybrid train rows={tr.sum()} test rows={te.sum()} "
          f"({X.shape[1]} features)")

    rng = np.random.default_rng(0)
    sh = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.05,
                            num_leaves=31, verbose=-1, n_jobs=8)
    sh.fit(X[tr], rng.permutation(y[tr]))
    auc_sh = roc_auc_score(y[te], sh.predict_proba(X[te])[:, 1])
    print(f"[check i] shuffled-label AUC = {auc_sh:.4f} (want ~0.500)")

    gb = lgb.LGBMClassifier(n_estimators=600, learning_rate=0.05, num_leaves=63,
                            min_child_samples=40, subsample=0.9,
                            subsample_freq=1, colsample_bytree=0.9,
                            verbose=-1, n_jobs=8)
    gb.fit(X[tr], y[tr])
    p = gb.predict_proba(X[te])[:, 1]
    print(f"hybrid GBDT   AUC={roc_auc_score(y[te], p):.4f}  "
          f"logloss={log_loss(y[te], p):.4f}  "
          f"Brier={brier_score_loss(y[te], p):.4f}")
    print("top features:")
    for nm, v in sorted(zip(names, gb.feature_importances_),
                        key=lambda x: -x[1])[:12]:
        star = "   <-- Bayes summary" if nm.startswith("bayes_") else ""
        print(f"   {nm:<26} {v}{star}")

    with open(MODEL, "rb") as fh:
        blob = pickle.load(fh)
    blob["gb_hybrid"] = gb
    with open(MODEL, "wb") as fh:
        pickle.dump(blob, fh)
    print(f"saved gb_hybrid into {MODEL}")


# ============================================================== STAGE: train
def _auc(yt, p):
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(yt, p))


def stage_train():
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import log_loss, brier_score_loss
    import lightgbm as lgb

    d = np.load(DATA, allow_pickle=True)
    X, y, g_pop, g_cust = d["X"], d["y"], d["g_pop"], d["g_cust"]
    feat_names = list(d["features"])

    # GROUPED SPLIT. Split by POPULATION, which also splits by customer: five
    # mandates share one balance trace, and the cross-merchant features are
    # built across them, so a row-level split leaks the answer between train
    # and test. The shuffled-label check below would NOT catch that -- it
    # detects a leak from the label into the features, not a leak between
    # rows -- which is why both checks exist.
    tr = np.isin(g_pop, TRAIN_POPS)
    te = np.isin(g_pop, HELDOUT_POPS)
    print(f"train rows={tr.sum()} ({len(np.unique(g_cust[tr]))} customers, "
          f"{len(TRAIN_POPS)} populations)")
    print(f"test  rows={te.sum()} ({len(np.unique(g_cust[te]))} customers, "
          f"{len(HELDOUT_POPS)} populations)")
    assert not set(np.unique(g_cust[tr])) & set(np.unique(g_cust[te])), \
        "customer appears in both splits"

    out = {}

    # --- check (i): shuffled labels must be uninformative -------------------
    rng = np.random.default_rng(0)
    y_sh = rng.permutation(y[tr])
    sh = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.05,
                            num_leaves=31, verbose=-1, n_jobs=8)
    sh.fit(X[tr], y_sh)
    auc_sh = _auc(y[te], sh.predict_proba(X[te])[:, 1])
    # also score the shuffled model against shuffled test labels
    auc_sh2 = _auc(rng.permutation(y[te]), sh.predict_proba(X[te])[:, 1])
    print(f"\n[check i]  shuffled-label AUC on real test labels   = {auc_sh:.4f}")
    print(f"[check i]  shuffled-label AUC on shuffled test labels= {auc_sh2:.4f}")
    print("           both must sit near 0.500. A high value means a feature "
          "carries the label.")
    out["shuffled_auc"] = auc_sh

    # --- the real models ----------------------------------------------------
    lr = make_pipeline(StandardScaler(),
                       LogisticRegression(max_iter=2000, C=1.0))
    lr.fit(X[tr], y[tr])
    p_lr = lr.predict_proba(X[te])[:, 1]

    gb = lgb.LGBMClassifier(n_estimators=600, learning_rate=0.05,
                            num_leaves=63, min_child_samples=40,
                            subsample=0.9, subsample_freq=1,
                            colsample_bytree=0.9, verbose=-1, n_jobs=8)
    gb.fit(X[tr], y[tr])
    p_gb = gb.predict_proba(X[te])[:, 1]

    print(f"\n{'model':<22}{'AUC':>8}{'logloss':>10}{'Brier':>9}")
    for name, p in (("logistic regression", p_lr), ("LightGBM", p_gb)):
        print(f"{name:<22}{_auc(y[te], p):>8.4f}"
              f"{log_loss(y[te], p):>10.4f}{brier_score_loss(y[te], p):>9.4f}")
        out[name] = dict(auc=_auc(y[te], p),
                         logloss=float(log_loss(y[te], p)),
                         brier=float(brier_score_loss(y[te], p)))
    print(f"{'base rate (test)':<22}{'':>8}{'':>10}{y[te].mean():>9.4f}")

    imp = sorted(zip(feat_names, gb.feature_importances_),
                 key=lambda x: -x[1])[:15]
    print("\ntop LightGBM features (split count):")
    for nm, v in imp:
        print(f"   {nm:<26} {v}")

    with open(MODEL, "wb") as fh:
        pickle.dump(dict(gb=gb, lr=lr, features=feat_names), fh)
    print(f"\nsaved {MODEL}")
    with open(os.path.join(ART, "train_report.json"), "w") as fh:
        json.dump(out, fh, indent=1)


# =============================================================== STAGE: eval
# Each world changes ONE thing. `pop_tail` extends the population spec with
# (payday_day0_frac, irregular_frac); `run_kw` goes to harness.run.
#
# THE DECAY TRAP. spend_decay is threaded to w3.balance_trace and NOWHERE ELSE.
# Belief and BeliefPD keep calling hourly_spend_profile() with the default
# 0.42. If the shifted decay reached the beliefs the filter would be correctly
# specified again and this entire table would measure nothing.
WORLDS = [
    ("A: in-distribution",       dict()),
    ("decay 0.20 (world only)",  dict(run_kw=dict(spend_decay=0.20))),
    ("decay 0.70 (world only)",  dict(run_kw=dict(spend_decay=0.70))),
    ("payday spread 0.60->0.30", dict(pop_tail=(0.30, 0.0))),
    ("irregular_frac 0.5",       dict(pop_tail=(0.60, 0.5))),
    ("topup_p 0.25",             dict(run_kw=dict(topup_p=0.25))),
]
# (label, policy, extra). The fair-fight arm is the SAME policy with the fitted
# belief configuration -- only the three hand-set handicaps move.
EVAL_ARMS = [
    ("payday_wait", "payday_wait", None),
    ("bayes_shipped", "solo_shared_pd", None),
    ("bayes_fair", "solo_shared_pd", "FAIR"),
    ("ml_index", "ml_index", "ML"),
    ("hybrid", "ml_index_pd", "HYBRID"),
    ("oracle", "oracle", None),
]


def paired(a, b):
    """(mean difference in points, 2 SE) for b - a, paired across populations."""
    d = (np.asarray(b, float) - np.asarray(a, float)) * 100
    return float(d.mean()), float(2 * d.std(ddof=1) / np.sqrt(len(d)))


def stage_eval():
    import mlmodel
    cfg = fair_cfg()
    jobs = []
    for wname, w in WORLDS:
        tail = w.get("pop_tail", ())
        for ps in EVAL_POPS:
            for label, pol, extra in EVAL_ARMS:
                kw = dict(payday_err=PE, pop_spend=POP_SPEND)
                kw.update(w.get("run_kw", {}))
                if extra == "FAIR":
                    kw["bcfg"] = cfg
                elif extra == "ML":
                    kw["ml_predict"] = mlmodel.predict
                elif extra == "HYBRID":
                    kw["ml_predict"] = mlmodel.predict_hybrid
                    kw["bcfg"] = cfg
                jobs.append((f"{wname}|{ps}|{label}", pol,
                             (N, K, ps, SPEND, DAYS) + tuple(tail), 900 + ps, kw))
    print(f"{len(jobs)} runs across {len(WORLDS)} worlds x {len(EVAL_POPS)} "
          f"populations x {len(EVAL_ARMS)} arms")
    t0 = time.perf_counter()
    res = runner.run_jobs(jobs)
    print(f"done in {time.perf_counter()-t0:.1f}s\n")

    # Stage 0 compliance is not optional for ml_index: it must go through the
    # same constraint layer as everything else, and the harness re-derives
    # legality independently of the policy, so this is a real check.
    dirty = {k: res[k]["vdetail"] for k in res
             if k.split("|")[2] != "baseline_doc" and res[k]["violations"]}
    print(f"Stage 0 violations across all {len(res)} runs: "
          f"{'NONE' if not dirty else dirty}")

    labels = [l for l, _, _ in EVAL_ARMS]
    table, out = [], {}
    for wname, _ in WORLDS:
        row = {l: [res[f"{wname}|{ps}|{l}"]["cycle_rec"] for ps in EVAL_POPS]
               for l in labels}
        table.append((wname, row))
        out[wname] = {l: float(np.mean(v) * 100) for l, v in row.items()}

    w0 = max(len(w) for w, _ in WORLDS) + 2
    print(f"{'world':<{w0}}" + "".join(f"{l:>15}" for l in labels))
    for wname, row in table:
        print(f"{wname:<{w0}}"
              + "".join(f"{np.mean(row[l])*100:>14.2f}%" for l in labels))

    def fmt(m, e):
        return f"{m:+6.2f}+/-{e:.2f} {'SIG ' if abs(m) > e else 'n.s.'}"

    print("\nPaired differences, 2 SE across populations:")
    print(f"{'world':<{w0}}{'ml - fair':>19}{'hybrid - fair':>19}"
          f"{'fair - shipped':>19}")
    for wname, row in table:
        a1, e1 = paired(row["bayes_fair"], row["ml_index"])
        a2, e2 = paired(row["bayes_fair"], row["hybrid"])
        a3, e3 = paired(row["bayes_shipped"], row["bayes_fair"])
        print(f"{wname:<{w0}}{fmt(a1,e1):>19}{fmt(a2,e2):>19}{fmt(a3,e3):>19}")
        out[wname]["ml_minus_fair"] = [a1, e1]
        out[wname]["hybrid_minus_fair"] = [a2, e2]
        out[wname]["fair_minus_shipped"] = [a3, e3]

    with open(RESULTS, "w") as fh:
        json.dump(out, fh, indent=1)
    print(f"\nsaved {RESULTS}")
    print(f"\nn={N}, k={K}, {len(EVAL_POPS)} populations, payday_err={PE}, "
          f"horizon {DAYS}d. Paired 2 SE across populations.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "data"
    {"data": stage_data, "train": stage_train, "eval": stage_eval,
     "data_pd": stage_data_pd, "train_hybrid": stage_train_hybrid}[cmd]()
