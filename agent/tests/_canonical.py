"""The canonical world, in one place, for scripts that predate it.

The ablations (`test_action_ablation.py`, `test_stop_mechanism.py`,
`test_outage_ablation.py`) were all written against the OLD world:
`pop_spend=1.05`, k=5, salary-coupled amounts, a uniform payday tail, no
burn-in and no mandate outflow. Every number they print is measured on a world
that has since been shown to have three defects -- no steady state, collected
mandates handed back at every payday, and an invented mandate count -- so those
numbers are stale rather than wrong-at-the-time. See
`docs/errors.md`, "Simulation and model errors".

Passing `--canonical` to any of them switches the population and the run to the
frozen canonical world instead. Without the flag they reproduce exactly what
they always did, so the old output stays regenerable for comparison.

WHY A SHARED MODULE AND NOT THREE EDITED CONFIGS. The three scripts would
otherwise carry three copies of a nine-key dict, and a copy that drifts is a
silent mis-measurement rather than a visible one. Definition lives here; the
scripts import it.

Anchors for every value are in the development log. `pop_spend=0.93` is
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
#: CUSTOMERS PER POPULATION. Raised from 100 on 2 September 2026 by
#: `agent/tests/test_scale_n.py`, which runs the canonical experiment at
#: n = 100, 250, 500, 1000, 2000 with everything else held fixed and then
#: repeats the chosen cell across four independent run seeds.
#:
#: WHY 100 HAD TO GO. It was inherited from a world with five mandates per
#: customer, where it bought 2.5x the mandates it buys here. Measured against
#: n=2000 it is optimistic on every headline at once: uplift +0.57, recovery
#: of at-risk cycles +1.32, first-presentation failure -1.17. Each of those is
#: larger than the interval the experiment reports at n>=500.
#:
#: WHY NOT MORE THAN 500. At 500 the uplift is within 0.19 points of n=2000,
#: the agent's own collection within 0.00 and V1 within 0.02, at 3.6x the
#: speed. Past that point n is no longer what limits the number: the RUN SEED
#: is. Four independent run seeds at n=500 put the uplift at 7.38, 7.71, 8.70
#: and 9.26 -- a 1.89-point spread, five times the residual n effect. Doubling
#: the compute to n=1000 shrinks the smaller of the two errors and leaves the
#: larger one exactly where it was.
#:
#: The table and the seed study are in docs/results.md, "Sample size". Every
#: canonical script reads this constant rather than carrying its own.
N = 500
K_SEED, BUF_SEED = 4242, 9182
#: Mandates per customer: `1 + Poisson(K_MEAN - 1)`, capped at `K_MAX`. Drawn
#: over the ten held-out populations at n=100 this gives 1,986 mandates across
#: 1,000 customers -- a mean of 1.99, a maximum of 7, and 63.5% of customers
#: holding more than one. The scalar `k` a caller passes is IGNORED whenever
#: these keywords are in force, which is why `world_line()` exists.
K_MEAN, K_MAX = 2.0, 8

_POP = dict(k_mean=K_MEAN, k_max=K_MAX,
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


def n(default: int = N, argv=None) -> int:
    """Customers per population. `default` is what a non-canonical run uses."""
    return N if enabled(argv) else default


def mandates(k: int, argv=None) -> str:
    """How many mandates each customer holds IN THE WORLD THAT WILL RUN.

    Under `--canonical` the scalar `k` a script passes to `make_pop` is dead:
    `k_mean` takes over and each customer draws its own count. A script that
    printed its own `k` there was reporting a condition its run did not have,
    which is the defect this module exists to remove. Every banner goes
    through here rather than formatting `k` itself.
    """
    if not enabled(argv):
        return f"k={k}"
    return (f"k~1+Poisson({K_MEAN - 1:g}) capped at {K_MAX}, mean about "
            f"{K_MEAN:g}")


def world_line(n: int, k: int, pops_used, days, payday_err,
               spend_used=None, argv=None) -> str:
    """One line naming the world a run ACTUALLY executed.

    Every value comes from what the caller is about to run, so the line cannot
    drift from the run the way a hand-written f-string can. `days` may be an
    int, or any string for a script that sweeps the horizon.
    """
    sp = SPEND if enabled(argv) else spend_used
    ps = list(pops_used)
    horizon = f"{days}d" if isinstance(days, int) else str(days)
    return (f"n={n}, {mandates(k, argv)}, {len(ps)} populations "
            f"{ps[0]}-{ps[-1]}, {horizon}, payday_err=+/-{payday_err}"
            + (f", pop_spend={sp}" if sp is not None else ""))


def banner(argv=None) -> str:
    if not enabled(argv):
        return ("OLD WORLD (pop_spend as written, no burn-in, no mandate "
                "outflow). Numbers are stale: the world had no steady state, "
                "handed collected mandates back at every payday, and fixed the "
                "mandate count at an invented 5. See docs/errors.md, "
                "'Simulation and model errors'.")
    return (f"CANONICAL WORLD: pop_spend={SPEND}, burn {BURN}, mandate outflow "
            f"ON, {mandates(0, ['--canonical'])}, statutory payday, "
            f"salary-independent amounts, buffer lognormal(0.25, 1.0). "
            f"Held-out pops {POPS[0]}-{POPS[-1]}.")
