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

_BOOSTER = None
_WHICH = os.environ.get("ML_MODEL", "gb")     # "gb" or "lr"


def _load():
    global _BOOSTER
    if _BOOSTER is None:
        with open(MODEL_PATH, "rb") as fh:
            _BOOSTER = pickle.load(fh)[_WHICH]
    return _BOOSTER


def predict(rows):
    """rows: list of feature vectors in mlfeat.FEATURES order -> P(success)."""
    return _load().predict_proba(np.asarray(rows, dtype=np.float64))[:, 1]
