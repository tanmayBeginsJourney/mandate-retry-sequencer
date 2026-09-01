"""The canonical world, in one place, for scripts that predate it.

The ablations (`test_action_ablation.py`, `test_stop_mechanism.py`,
`test_outage_ablation.py`) were all written against the OLD world:
`pop_spend=1.05`, k=5, salary-coupled amounts, a uniform payday tail, no
burn-in and no mandate outflow. Every number they print is measured on a world
that has since been shown to have three defects (errors 33, 34, 35), so those
numbers are stale rather than wrong-at-the-time.

Passing `--canonical` to any of them switches the population and the run to the
frozen canonical world instead. Without the flag they reproduce exactly what
they always did, so the old output stays regenerable for comparison.

WHY A SHARED MODULE AND NOT THREE EDITED CONFIGS. The three scripts would
otherwise carry three copies of a nine-key dict, and a copy that drifts is a
silent mis-measurement rather than a visible one. Definition lives here; the
scripts import it.

Anchors for every value are in NOTES.md, 1 September 2026. `pop_spend=0.93` is
one of the three published RBI household-savings readings (net financial
saving, 7% of GNDI); it is the top of the externally derived range and it is
the only point in that range where the canonical world carries enough at-risk
mass to measure anything.
"""
from __future__ import annotations

import sys

#: Held-out populations. 700-709 were used to SELECT the B-1 offset schedules,
#: so an ablation scored on them would be scored on a selection set.
POPS = list(range(710, 720))
SPEND = 0.93
BURN = 12
K_SEED, BUF_SEED = 4242, 9182

_POP = dict(k_mean=2.0, k_max=8,
            payday_mode="statutory",
            amount_mode="absolute", amount_median=855.0,
            buffer_median=0.25, buffer_sigma=1.0,
            irregular_frac=0.00)


def enabled(argv=None) -> bool:
    return "--canonical" in (sys.argv if argv is None else argv)


def pop_kwargs(pop_seed: int, argv=None) -> dict:
    """Extra `w3.make_pop` keywords. Empty unless `--canonical`.

    The per-customer seeds are PER POPULATION. A single shared seed gives every
    population the identical mandate-count and buffer vectors, which is the
    defect found in W10 and the same failure mode as error 27.
    """
    if not enabled(argv):
        return {}
    kw = dict(_POP)
    kw["k_seed"] = K_SEED + pop_seed
    kw["buffer_seed"] = BUF_SEED + pop_seed
    return kw


def run_kwargs(argv=None) -> dict:
    """Extra `run_once` keywords. Empty unless `--canonical`."""
    if not enabled(argv):
        return {}
    return dict(burn_cycles=BURN, mandate_outflow=True)


def spend(default: float, argv=None) -> float:
    return SPEND if enabled(argv) else default


def pops(default: list[int], argv=None) -> list[int]:
    return list(POPS) if enabled(argv) else list(default)


def banner(argv=None) -> str:
    if not enabled(argv):
        return ("OLD WORLD (pop_spend as written, no burn-in, no mandate "
                "outflow). Numbers are stale: see NOTES.md errors 33-35.")
    return (f"CANONICAL WORLD: pop_spend={SPEND}, burn {BURN}, mandate outflow "
            f"ON, k~1+Poisson(1), statutory payday, salary-independent "
            f"amounts, buffer lognormal(0.25, 1.0). Held-out pops "
            f"{POPS[0]}-{POPS[-1]}.")
