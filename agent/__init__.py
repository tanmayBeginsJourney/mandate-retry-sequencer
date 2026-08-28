"""The recovery agent.

Importing `agent` puts `sim/` on the path so that `w3` and `harness` are
importable by the layers that are allowed to use them. Which layers those are
is not a matter of taste -- see `agent/tests/test_layer_isolation.py`, which
parses the import graph and fails if, for example, `agent/llm` learns how to
reach the belief filter.

`sim/` is FROZEN at tag `model-frozen`. Nothing under `agent/` writes to it.
"""
from __future__ import annotations

import os
import sys

_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SIM = os.path.join(_PKG_ROOT, "sim")
if _SIM not in sys.path:
    sys.path.insert(0, _SIM)

__all__ = ["_PKG_ROOT", "_SIM"]
