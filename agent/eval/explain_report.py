"""Compact side-by-side of the explanation arms, read back from the caches.

    py -3.12 agent/eval/explain_report.py            all arms, all cases
    py -3.12 agent/eval/explain_report.py --arms template,template2,v3

`run_explain_eval.py` prints everything; this prints the part a reader
compares. It makes no calls and spends nothing -- if a response is not in the
cache it says so rather than fetching it, so this script cannot quietly turn a
replay into a paid run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from agent.eval.explain_cases import CASES                     # noqa: E402
from agent.eval.run_explain_eval import (ARMS, EXPLAIN_CACHE,  # noqa: E402
                                         JUDGE_CACHE, _wrap, score)
from agent.llm.client import DIAGNOSER_MODEL                   # noqa: E402


def _load(path: str) -> dict:
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}


def bodies(arm: str, cache: dict) -> dict[str, str | None]:
    """case id -> the arm's draw-0 text, or None if it fell back / is absent."""
    name, pid, renderer, det_fn = next(a for a in ARMS if a[0] == arm)
    out: dict[str, str | None] = {}
    for cid, _q, view in CASES:
        if renderer is None:
            out[cid] = det_fn(view)
            continue
        hit = cache.get(f"{DIAGNOSER_MODEL}|{pid}|{view.explain_hash}")
        p = (hit or {}).get("parsed")
        out[cid] = (str(p.get("explanation")).strip()
                    if isinstance(p, dict) and p.get("explanation") else None)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default=",".join(a[0] for a in ARMS))
    ap.add_argument("--width", type=int, default=96)
    a = ap.parse_args(argv)

    want = [x.strip() for x in a.arms.split(",") if x.strip()]
    cache = _load(EXPLAIN_CACHE)
    texts = {arm: bodies(arm, cache) for arm in want}

    jraw = _load(JUDGE_CACHE)
    # The judge cache is keyed by a hash of (view hash, text), so a row is
    # found by rebuilding that key rather than by remembering which arm it was.
    from agent.llm.client import case_key
    from agent.eval.run_explain_eval import JUDGE_ID

    def verdict(view, text):
        if text is None:
            return None
        k = f"glm-5.3|{JUDGE_ID}|" + case_key(JUDGE_ID, {"h": view.explain_hash,
                                                         "t": text})
        p = (jraw.get(k) or {}).get("parsed")
        return p if isinstance(p, dict) else None

    for cid, question, view in CASES:
        print("=" * (a.width + 6))
        print(f"{cid}  {question}")
        print(f"      mandate={view.mandate_state} attempt="
              f"{view.attempt_state or '-'} codes="
              f"{','.join(view.decline_history) or '-'} "
              f"used={view.attempts_used}/{view.attempts_cap} "
              f"gate={view.gate_verdict or '-'} act={view.intervention or '-'}")
        print("=" * (a.width + 6))
        for arm in want:
            t = texts[arm][cid]
            if t is None:
                print(f"\n  [{arm}]  NO MODEL OUTPUT -- fell back to the "
                      f"template. The operator sees the template arm's text.")
                continue
            s = score(view, t)
            v = verdict(view, t)
            jt = (f"  judge f={v['faithfulness']} e={v['explanatory_value']} "
                  f"u={v['operator_usefulness']}"
                  + (" RESTATES" if v["merely_restates"] else "")
                  + (" MISATTRIB" if v["misattributes"] else "")
                  + (" RECOMMENDS" if v["recommends_action"] else "")
                  + (" LEAKS" if v["leaks_state"] else "")) if v else "  judge -"
            print(f"\n  [{arm}]  {s['words']}w causal={s['causal']} "
                  f"attrib={len(s['attrib_got'])}/{len(s['attrib_want'])}"
                  f"{'' if s['gov_ok'] else '  GOV-FAIL'}"
                  f"{'' if not s['unfaithful'] else '  UNFAITHFUL'}{jt}")
            for line in _wrap(t, a.width):
                print(f"      {line}")
            for b in s["unfaithful"]:
                print(f"      !! {b}")
            for g in s["gov_reasons"]:
                print(f"      !! governance: {g}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
