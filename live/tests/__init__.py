"""Gates for the live rail. Every one runs offline, with no API key.

    py -3.12 -m live.tests.run_all

WHAT THESE CAN AND CANNOT PROVE. They prove that the state machine cannot be
walked backwards, that a duplicate webhook is a no-op, that a forged signature
is rejected and recorded, that a crash between submitting and hearing back is
recoverable, and that no route can charge an arbitrary amount. They prove
NOTHING about whether Razorpay accepts these request bodies against a real
account -- that needs a live key and a real mandate, and the distinction is
kept in the output rather than in a footnote.
"""
from __future__ import annotations

import os
import sys

_PKG = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)
