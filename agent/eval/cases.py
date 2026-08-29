"""Load `golden_cases.yaml` into real `CaseView` objects.

PyYAML IS REQUIRED FOR THIS FILE, AND THAT IS A DELIBERATE REVERSAL.

The first version of this loader carried a hand-rolled parser for the subset of
YAML the case file uses, so that a fresh clone could run the eval without a
second install step -- the gated suite needs numpy and nothing else, and that
property is worth keeping. The self-test compared the hand parser against
PyYAML on the first run and they DISAGREED: the hand parser did not produce a
`cases` key at all.

So the hand parser is gone rather than debugged. A parser that silently
mis-reads a case corrupts an eval score in a way nothing downstream can detect,
which is this project's signature failure, and shipping a second implementation
that is only exercised when the first one is missing is how you get a code path
nobody has ever run. PyYAML is added to `requirements.txt` under a heading that
says which parts of the repo need it. **The GATED suite still needs numpy
alone** -- `sim/gate.py` is untouched and this file is not on its path.

THE CASE VIEWS ARE BUILT BY `build_case_view`, NOT CONSTRUCTED DIRECTLY. That
is deliberate: `case_hash` is computed by the redaction boundary, so a case
loaded from this file hashes identically to the same case arising in a live
run, and a cached LLM response is shared between them.

"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from dataclasses import dataclass

from agent.llm.caseview import build_case_view
from agent.ports import CaseView, PaydayUncertainty

GOLDEN = os.path.join(HERE, "golden_cases.yaml")

#: `uncertainty_band` is a coarse label derived from the payday posterior's
#: top-hypothesis weight (`ports.PaydayUncertainty.band`). The case file states
#: the band directly, so we invert the mapping to a representative weight. The
#: entropy is not used by the band and is set to a fixed placeholder -- said out
#: loud because an invented entropy that LOOKED meaningful would be worse.
_BAND_WEIGHT = {"narrow": 0.80, "medium": 0.40, "wide": 0.10}


@dataclass(frozen=True)
class GoldenCase:
    id: str
    title: str
    situation: str
    view: CaseView
    correct_root_cause: str
    correct_intervention: str
    expert_agreement: float
    ambiguous: bool
    defensible_alternative: str | None
    notes: str

    @property
    def low_confidence(self) -> bool:
        """The author said in advance they expect to be wrong here."""
        return self.expert_agreement <= 0.65


def _load_raw(path: str = GOLDEN) -> dict:
    try:
        import yaml                                    # noqa: PLC0415
    except ImportError as e:                           # pragma: no cover
        raise ImportError(
            "agent/eval needs PyYAML (`pip install pyyaml`). The GATED suite "
            "does not -- sim/gate.py still runs on numpy alone. A hand-rolled "
            "parser lived here until 29 Aug 2026 and was removed because its "
            "self-test caught it disagreeing with PyYAML on the very first "
            "run; a second parser nobody exercises is worse than a dependency."
        ) from e
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh.read())


def _view_of(cv: dict) -> CaseView:
    band = cv.get("uncertainty_band", "medium")
    if band not in _BAND_WEIGHT:
        raise ValueError(f"unknown uncertainty_band {band!r}")
    unc = PaydayUncertainty(payday_entropy=0.0,
                            top_hypothesis_weight=_BAND_WEIGHT[band])
    hist = cv.get("decline_history") or []
    day = int(cv["day_in_cycle"])
    return build_case_view(
        amount=float(cv["amount"]),
        attempts_used=int(cv["attempts_used"]),
        attempts_cap=int(cv["attempts_cap"]),
        day=day, cycle_open=0,
        cycle_close=day + int(cv["days_left_in_cycle"]),
        decline_history=[str(x) for x in hist],
        peer_success_recent=bool(cv.get("peer_mandate_success_recent", False)),
        uncertainty=unc,
        merchant_note=str(cv.get("merchant_note") or ""),
        bank=str(cv.get("bank") or ""))


@dataclass(frozen=True)
class TaxonomyCase:
    """A case about what a RESPONSE CODE MEANS, not about timing.

    Graded against a SET of defensible interventions rather than one string.
    Several of these genuinely admit two answers -- a revoked mandate can be
    STOPped or ESCALATEd, and which is better depends on whether you would
    rather preserve attempts or tell a human to re-authorise -- so grading on a
    single registered answer would manufacture disagreement that is not there.
    Where only one answer is defensible the set has one member.
    """
    id: str
    title: str
    family: str
    situation: str
    view: CaseView
    ok_interventions: frozenset
    why: str

    @property
    def terminal(self) -> bool:
        """Does this case carry a code no retry can ever fix?"""
        from agent.ports import TERMINAL_CODES
        return any(c in TERMINAL_CODES for c in self.view.decline_history)


def load_taxonomy_cases(path: str = GOLDEN) -> list[TaxonomyCase]:
    raw = _load_raw(path)
    return [TaxonomyCase(
        id=c["id"], title=c.get("title", ""), family=c.get("family", ""),
        situation=c.get("situation", ""), view=_view_of(c["case_view"]),
        ok_interventions=frozenset(c["terminal_ok"]),
        why=str(c.get("why") or "")) for c in raw.get("taxonomy_cases", [])]


def load_cases(path: str = GOLDEN) -> list[GoldenCase]:
    raw = _load_raw(path)
    out = []
    for c in raw["cases"]:
        out.append(GoldenCase(
            id=c["id"], title=c.get("title", ""),
            situation=c.get("situation", ""),
            view=_view_of(c["case_view"]),
            correct_root_cause=str(c.get("correct_root_cause", "UNKNOWN")),
            correct_intervention=str(c["correct_intervention"]),
            expert_agreement=float(c.get("expert_agreement", 0.0)),
            ambiguous=bool(c.get("ambiguous", False)),
            defensible_alternative=(c.get("defensible_alternative") or None),
            notes=str(c.get("notes") or "")))
    return out


def _selftest() -> int:
    cases = load_cases()
    print(f"parsed {len(cases)} cases")
    bad = 0
    ids = [c.id for c in cases]
    if len(set(ids)) != len(ids):
        print("FAIL duplicate case ids")
        bad += 1
    if len(cases) != 40:
        print(f"FAIL expected 40 cases, got {len(cases)}")
        bad += 1
    amb = [c for c in cases if c.ambiguous]
    low = [c for c in cases if c.low_confidence]
    print(f"  ambiguous: {len(amb)}   expert_agreement<=0.65: {len(low)}")
    print(f"  low-confidence ids: {' '.join(c.id for c in low)}")
    # Every case view must hash, and two different cases must not collide.
    hashes = {c.view.case_hash for c in cases}
    if len(hashes) != len(cases):
        print(f"FAIL case_hash collision: {len(hashes)} hashes for "
              f"{len(cases)} cases")
        bad += 1
    # Every registered intervention must be one the type system can express.
    from agent.ports import InterventionKind
    vocab = {k.value for k in InterventionKind}
    unknown = sorted({c.correct_intervention for c in cases} - vocab)
    if unknown:
        print(f"FAIL registered answers outside the action space: {unknown}")
        bad += 1
    tx = load_taxonomy_cases()
    print(f"  taxonomy cases: {len(tx)} "
          f"({sum(t.terminal for t in tx)} carrying a terminal code)")
    if len({t.id for t in tx}) != len(tx):
        print("FAIL duplicate taxonomy case ids")
        bad += 1
    for t in tx:
        if not t.ok_interventions <= vocab:
            print(f"FAIL {t.id} allows an intervention outside the action "
                  f"space: {sorted(t.ok_interventions - vocab)}")
            bad += 1
    for c in cases[:3]:
        print(f"  {c.id} {c.correct_intervention:9s} hash={c.view.case_hash} "
              f"hist={list(c.view.decline_history)}")
    print("SELFTEST", "FAIL" if bad else "OK")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
