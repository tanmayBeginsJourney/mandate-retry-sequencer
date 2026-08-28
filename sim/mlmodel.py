"""
The probability engine for the `ml_index` ablation.

`predict` is a module-level function on purpose: multiprocessing on Windows
pickles it by qualified name, so a worker imports this module and loads the
model once rather than receiving a copy of the booster with every job.

harness.py never imports this. It takes `ml_predict` as a plain callable, so
the simulator has no dependency on any model library.
"""
import os
import pickle

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(HERE, "ml_artifacts", "model.pkl")

_CACHE = {}


def _load(which):
    if which not in _CACHE:
        with open(MODEL_PATH, "rb") as fh:
            _CACHE[which] = pickle.load(fh)[which]
    return _CACHE[which]


def predict(rows):
    """rows: feature vectors in mlfeat.FEATURES order -> P(success)."""
    return _load(os.environ.get("ML_MODEL", "gb")).predict_proba(
        np.asarray(rows, dtype=np.float64))[:, 1]


def predict_hybrid(rows):
    """rows: feature vectors in mlfeat.FEATURES_HYBRID order -> P(success).

    Same GBDT, four extra inputs: the Bayes filter's P(success) for the
    candidate day, its expected balance, and the entropy and top weight of its
    payday posterior.
    """
    return _load("gb_hybrid").predict_proba(
        np.asarray(rows, dtype=np.float64))[:, 1]
