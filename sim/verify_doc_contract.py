#!/usr/bin/env python3
"""DOC-CODE CONTRACT CHECK for the public documentation.

    py -3.12 sim/verify_doc_contract.py      exits 0 if the docs match the code

`docs/architecture.md` and `docs/results.md` state constants and a decision
rule that a reader will act on: the attempt cap, the peak windows, the decision
hour, the forecast horizon, the technical-decline base rate, the shipping
belief configuration, the index discount and the continuation value. If `sim/`
drifts from that prose, the documentation is a lie that nothing catches.

This replaces the earlier checker, which asserted the same constants against a
document that has since been removed. That checker held its expected values as
literals and never opened the file it named, so it would have kept passing after
its target disappeared -- the vacuous shape this repository has hit repeatedly.

TWO PROPERTIES, AND THE SECOND IS THE ONE THAT WAS MISSING.

  1. Every constant the documentation states must equal the code.
  2. Every constant this checker knows about must ACTUALLY APPEAR in the
     documentation. Deleting the sentence is not a way to pass. A missing
     target file is an error, not a skip.

The decision-recipe check is kept from the earlier version: it transcribes the
recipe as `docs/architecture.md` prints it and asserts bit-exact equality with
`sim/harness.py`'s belief branch. Prose can describe an algorithm correctly and
still be describing a different one.

Not part of the gated simulation suite -- it runs no simulations and takes under
a second. It runs in `scripts/pre-commit` beside the two documentation gates.
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (HERE, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: F401  -- imported for the same reason harness does
import harness
import w3

ARCH = os.path.join(ROOT, "docs", "architecture.md")
RESULTS = os.path.join(ROOT, "docs", "results.md")

fails: list[str] = []


def _read(path: str) -> str:
    if not os.path.exists(path):
        fails.append(f"{os.path.relpath(path, ROOT)} does not exist. This "
                     f"checker names it, so its absence is a failure and not "
                     f"a skip.")
        return ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def documented(label: str, text: str, pattern: str, got, want) -> None:
    """The doc must state `pattern`, and the code must equal `want`."""
    stated = re.search(pattern, text) is not None
    correct = got == want
    ok = stated and correct
    mark = "ok  " if ok else "FAIL"
    print(f"  {mark}  {label:<52} {got!r}")
    if not stated:
        fails.append(f"{label}: the documentation no longer states this. "
                     f"Removing the sentence is not a way to pass.")
    elif not correct:
        fails.append(f"{label}: the documentation says {want!r}, "
                     f"the code says {got!r}.")


def code_only(label: str, got, want) -> None:
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'}  {label:<52} {got!r}")
    if not ok:
        fails.append(f"{label}: expected {want!r}, code says {got!r}.")


arch = _read(ARCH)
results = _read(RESULTS)

print("Constants stated in docs/architecture.md:")
documented("w3.NPCI_MAX", arch, r"NPCI_MAX\s*=\s*4", w3.NPCI_MAX, 4)
documented("w3.DECISION_HOUR", arch, r"DECISION_HOUR\s*=\s*8",
           w3.DECISION_HOUR, 8)
documented("w3.PEAK", arch,
           r"PEAK\s*=\s*\{10,\s*11,\s*12,\s*17,\s*18,\s*19,\s*20,\s*21\}",
           sorted(w3.PEAK), [10, 11, 12, 17, 18, 19, 20, 21])
documented("harness.LOOKAHEAD_DAYS", arch, r"LOOKAHEAD_DAYS\s*=\s*12",
           harness.LOOKAHEAD_DAYS, 12)
documented("harness.P_TECH", arch, r"P_TECH\s*=\s*0\.008",
           harness.P_TECH, 0.008)
documented("index discount", arch, r"amount \* \(p_now - 0\.92 \* p_later\)",
           w3.index_score.__defaults__[0], 0.92)

# The peak windows appear twice in the same document, once as an hour set and
# once as clock times in the constraint table. Both are load-bearing for a
# reader and they have drifted apart before.
documented("peak windows, stated as clock times", arch,
           r"10:00.{0,3}13:00 or 17:00.{0,3}21:30",
           sorted(w3.PEAK), [10, 11, 12, 17, 18, 19, 20, 21])
documented("attempt cap, stated in the constraint table", arch,
           r"at most 4 attempts per mandate per billing cycle",
           w3.NPCI_MAX, 4)

print("\nThe continuation-value rule, stated in docs/architecture.md:")
import agent  # noqa: E402,F401  -- puts agent/ on the path for the next import
from agent.policy.timing import DEFAULT_CYCLE_VALUE  # noqa: E402

documented("cycle_value", arch,
           r"fire iff p / \(1 - p\) > cycles_left \* cycle_value",
           DEFAULT_CYCLE_VALUE, 0.6)
documented("cycle_value, the literal", arch, r"cycle_value\s*=\s*0\.6",
           DEFAULT_CYCLE_VALUE, 0.6)

print("\nThe shipping belief configuration, in BOTH documents:")
FITTED = dict(stride=1, prior_w=5, prior_day0=8.0, prior_floor=0.1,
              spend_beta=0.0)
BLOCK = (r"FITTED_BELIEF = dict\(stride=1, prior_w=5, prior_day0=8\.0,\s*"
         r"prior_floor=0\.1, spend_beta=0\.0\)")
documented("w3.FITTED_BELIEF in architecture.md", arch, BLOCK,
           w3.FITTED_BELIEF, FITTED)
documented("w3.FITTED_BELIEF in results.md", results, BLOCK,
           w3.FITTED_BELIEF, FITTED)

print("\nADR-005: the diagnosis type carries no field that could hold a time.")
# README.md, docs/architecture.md and docs/results.md all state that a
# temporal field would make the check FAIL. Until 2 September 2026 the only
# consumer printed the breach and returned 0, so the claim was unenforced.
# This is the enforcement, and the canary below proves it is not vacuous.
from agent.eval.injection import (diagnosis_has_temporal_field,  # noqa: E402
                                  temporal_fields)

_has_t, _hits = diagnosis_has_temporal_field()
code_only("ports.Diagnosis temporal fields", list(_hits), [])
if _has_t:
    fails.append(
        f"ports.Diagnosis now carries {list(_hits)}. The documentation says a "
        f"language model cannot decide when to debit BECAUSE its only output "
        f"type has no temporal field. Adding one may be right, but the claim "
        f"in README.md and docs/architecture.md is then false and must go in "
        f"the same change.")
# The canary uses a FIXED synthetic field list, not the live type plus a field.
# Built on the live type it would inherit the mutation it is meant to be
# independent of, and its own output would move when `Diagnosis` changed.
_canary = temporal_fields(["diagnosis_id", "root_cause", "intervention",
                           "confidence", "rationale", "retry_after_hours"])
code_only("canary: a `retry_after_hours` field IS detected",
          list(_canary), ["retry_after_hours"])
documented("architecture.md states the check fails on a temporal field", arch,
           r"diagnosis_has_temporal_field\(\)` inspects the type and\s*\n?\s*"
           r"fails the day someone adds one", _has_t, False)

print("\nThe brief's construction recipe still holds:")
cfg = dict(w3.FITTED_BELIEF)
beta = cfg.pop("spend_beta")
try:
    w3.BeliefPD(20000, 3, 30, 120, **w3.FITTED_BELIEF)
    print("  FAIL  BeliefPD accepted spend_beta")
    fails.append("BeliefPD now accepts spend_beta; the construction recipe in "
                 "docs/architecture.md needs revisiting.")
except TypeError:
    print("  ok    BeliefPD rejects spend_beta, so the config is unpacked")
est_spend = 1.05 * (1 + (5 - 1) * beta)
b = w3.BeliefPD(20000, 3, 30, 120, est_spend=est_spend, pop_info=True, **cfg)
code_only("hypotheses under stride=1", len(b.hyp), 30)
code_only("posterior_summary arity", len(b.posterior_summary()), 3)

print("\nThe documented decision rule must equal harness.py's belief branch:")
for d in range(5):
    b.advance(d)
b.observe(900, False)
b.observe(900, True)
day, amount, cyc_close, used = 5, 900.0, 20, 1
LOOK, cap = harness.LOOKAHEAD_DAYS, w3.NPCI_MAX

# harness.py's belief branch, transcribed
fc_days = b.forecast(day, LOOK)
p_now_l = [(dd, p) for dd, p in fc_days if dd >= day + 1]
h_tgt, p_tgt = p_now_l[0]
h_now = b.p_success(amount, p_tgt)
cand = [b.p_success(amount, p) for dd, p in p_now_l[1:] if dd < cyc_close]
h_lat = max(cand, default=0.0) if cap - used > 1 else 0.0
h_score = w3.index_score(h_now, h_lat, amount, 0.92)

# the recipe exactly as docs/architecture.md describes it
fc = b.forecast(day, LOOK)
c2 = [(dd, P) for dd, P in fc if dd < cyc_close]
d_tgt, d_ptgt = c2[0]
d_now = b.p_success(amount, d_ptgt)
later = [b.p_success(amount, P) for dd, P in c2[1:]]
d_lat = max(later, default=0.0) if (cap - used) > 1 else 0.0
d_score = w3.index_score(d_now, d_lat, amount)

code_only("target day", d_tgt, h_tgt)
code_only("p_now (bit-exact)", d_now, h_now)
code_only("p_later (bit-exact)", d_lat, h_lat)
code_only("index score (bit-exact)", d_score, h_score)
documented("p_later is 0.0 on the final attempt", arch,
           r"or 0 when this is the last attempt",
           (max(later, default=0.0) if (cap - 3) > 1 else 0.0), 0.0)
code_only("earliest_legal skips peak: (day 6, from 10:00)",
          harness.earliest_legal(6, 6 * 24 + 10) % 24, 13)

print()
if fails:
    print("=" * 70)
    print(f"DOC-CODE CONTRACT BROKEN -- {len(fails)} mismatch(es):")
    for f in fails:
        print("   " + f)
    print("Fix the document, or the code. Do not delete the sentence.")
    print("=" * 70)
    sys.exit(1)
print("docs/architecture.md and docs/results.md match the code.")
