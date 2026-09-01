#!/usr/bin/env python3
"""
DOC-CODE CONTRACT CHECK for docs/07_AGENT_BRIEF.md.

The agent brief documents an interface: constant values, a construction recipe
for the belief, and a scheduling-decision recipe. If `sim/` drifts from that
prose, the next session builds on a lie. This asserts the prose.

This session found two doc/code contradictions that had survived for weeks (a
"removed" LTV multiplier that was still live, and a "gone" 0.92 discount that
was not). Both were prose claims nothing checked. This checks the claims a
reader would actually act on.

    python sim/verify_brief.py        exits 0 if the brief is accurate

Not part of the gated suite -- it runs no simulations and takes under a second.
Run it after any change to w3.py or harness.py, and before trusting the brief.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import numpy as np
import w3
import harness

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'ok ' if ok else 'FAIL'}  {label:<46} {got!r}")
    if not ok:
        fails.append(f"{label}: brief says {want!r}, code says {got!r}")


print("Constants quoted in docs/07_AGENT_BRIEF.md section 3:")
check("w3.NPCI_MAX", w3.NPCI_MAX, 4)
check("w3.HOURS", w3.HOURS, 24)
check("w3.DECISION_HOUR", w3.DECISION_HOUR, 8)
check("w3.PEAK", sorted(w3.PEAK), [10, 11, 12, 17, 18, 19, 20, 21])
check("harness.LOOKAHEAD_DAYS", harness.LOOKAHEAD_DAYS, 12)
check("harness.P_TECH", harness.P_TECH, 0.008)
check("(w3.Z9, w3.TECH, w3.OK)", (w3.Z9, w3.TECH, w3.OK), ("Z9", "TECH", "OK"))
# Re-selected on the canonical world 1 September 2026 (W24): prior_w 9 -> 5,
# prior_floor 0.5 -> 0.1. This literal is a TRANSCRIPTION of what
# docs/07_AGENT_BRIEF.md prints, so it must move in the same commit the brief
# does -- updating one without the other is what makes this checker vacuous.
check("w3.FITTED_BELIEF", w3.FITTED_BELIEF,
      dict(stride=1, prior_w=5, prior_day0=8.0, prior_floor=0.1,
           spend_beta=0.0))
check("index_score signature",
      list(w3.index_score.__code__.co_varnames[:4]),
      ["p_now", "p_later", "amount", "discount"])
check("shipping policy is a known policy",
      "solo_shared_pd" in harness.BELIEF_POLS, True)

print("\nThe brief warns that FITTED_BELIEF has a key BeliefPD rejects:")
try:
    w3.BeliefPD(20000, 3, 30, 120, **w3.FITTED_BELIEF)
    print("  FAIL  BeliefPD accepted spend_beta - the brief's warning is stale")
    fails.append("BeliefPD now accepts spend_beta; update 07_AGENT_BRIEF.md")
except TypeError:
    print("  ok    BeliefPD rejects spend_beta, as the brief says")

print("\nThe brief's construction recipe:")
cfg = dict(w3.FITTED_BELIEF)
beta = cfg.pop("spend_beta")
est_spend = 1.05 * (1 + (5 - 1) * beta)
b = w3.BeliefPD(20000, 3, 30, 120, est_spend=est_spend, pop_info=True, **cfg)
check("hypotheses under stride=1", len(b.hyp), 30)
check("posterior_summary arity", len(b.posterior_summary()), 3)

print("\nThe brief's decision recipe must equal harness.py's belief branch:")
for d in range(5):
    b.advance(d)
b.observe(900, False)
b.observe(900, True)
day, amount, cyc_close, used = 5, 900.0, 20, 1
LOOK, cap = harness.LOOKAHEAD_DAYS, w3.NPCI_MAX

# harness.py belief branch, transcribed
fc_days = b.forecast(day, LOOK)
p_now_l = [(dd, p) for dd, p in fc_days if dd >= day + 1]
h_tgt, p_tgt = p_now_l[0]
h_now = b.p_success(amount, p_tgt)
cand = [b.p_success(amount, p) for dd, p in p_now_l[1:] if dd < cyc_close]
h_lat = max(cand, default=0.0) if cap - used > 1 else 0.0
h_score = w3.index_score(h_now, h_lat, amount, 0.92)

# the recipe exactly as docs/07_AGENT_BRIEF.md prints it
fc = b.forecast(day, LOOK)
c2 = [(dd, P) for dd, P in fc if dd < cyc_close]
d_tgt, d_ptgt = c2[0]
d_now = b.p_success(amount, d_ptgt)
later = [b.p_success(amount, P) for dd, P in c2[1:]]
d_lat = max(later, default=0.0) if (cap - used) > 1 else 0.0
d_score = w3.index_score(d_now, d_lat, amount)

check("target day", d_tgt, h_tgt)
check("p_now (bit-exact)", d_now, h_now)
check("p_later (bit-exact)", d_lat, h_lat)
check("index score (bit-exact)", d_score, h_score)
check("p_later is 0.0 on the final attempt",
      (max(later, default=0.0) if (cap - 3) > 1 else 0.0), 0.0)
check("earliest_legal skips peak: (day 6, from 10:00)",
      harness.earliest_legal(6, 6 * 24 + 10) % 24, 13)

print()
if fails:
    print("=" * 70)
    print(f"BRIEF IS OUT OF DATE -- {len(fails)} mismatch(es):")
    for f in fails:
        print("   " + f)
    print("Fix docs/07_AGENT_BRIEF.md, or the code. Do not ignore this.")
    print("=" * 70)
    sys.exit(1)
print("docs/07_AGENT_BRIEF.md matches the code.")
