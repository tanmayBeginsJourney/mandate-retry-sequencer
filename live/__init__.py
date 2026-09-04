"""The live rail. `sim/` is the simulated world; this is the real one.

WHY THIS IS A SIBLING OF `agent/` AND NOT A PACKAGE INSIDE IT. `agent/` is the
decision architecture, and its layers are held apart by the import gates in
`agent/tests/test_layer_isolation.py`. Gate I2 permits exactly two modules in
the shipping tree to hold an `Executor`: `agent/constraints/stage0.py` and
`agent/batch.py`. A service that wires a live executor to the gate is a third
composition root, and putting it under `agent/` would mean widening that
exemption. So the service lives here and the boundary is enforced rather than
escaped: gates L1 and L2 in the same file cover this package. L1 says only
`live/service.py` may import `agent.execution`. L2 says nothing under `agent/`
may import `live` -- the core must not depend on the service that wraps it.

WHAT THIS PACKAGE OWNS: durable state, the Razorpay lifecycle, webhook
ingestion and reconciliation, and the HTTP surface.

WHAT IT DOES NOT OWN: any decision. Timing comes from `agent.policy.timing`,
legality from `agent.constraints.stage0`, diagnosis from `agent.llm` -- the
same objects the simulation uses, unchanged. That is the whole claim this
package exists to make good on, and `live/tests/test_parity.py` asserts it by
identity rather than by inspection.
"""
from __future__ import annotations

import os
import sys

_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

# Importing `agent` puts `sim/` on the path, which the belief filter needs.
import agent  # noqa: E402,F401

__all__ = ["_PKG_ROOT"]
