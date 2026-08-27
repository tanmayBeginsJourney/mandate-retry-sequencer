"""
PARALLEL DRIVER for the test suite.

WHY THIS IS BIT-EXACT, not "close enough". Every `harness.run` is fully
determined by (policy, population, seed, kwargs). It builds its own generators
from `seed` -- `np.random.default_rng(seed)`, `default_rng(seed + 777)`,
`default_rng(seed + 31*ci)` -- and reads no global random state. So a run's
result cannot depend on which process it ran in, on how many workers there
were, or on the order the jobs completed. Parallelising over (policy, seed)
pairs is therefore identity-preserving BY CONSTRUCTION, not by measurement.

That claim is not taken on trust: gate T9 in sim/tests.py compares every
policy's output against sim/t9_reference.json, and is paired with a mutant
(`shared_seed_mutant` below) that seeds the pool from ONE shared RNG instead
of per-run seeds. If that mutant does not trip T9, T9 reports VACUOUS.

THE WINDOWS SPAWN TRAP, and what was done about it. There is no fork() here.
multiprocessing spawns a fresh interpreter per worker and re-imports the
parent's __main__ module inside it. sim/tests.py used to execute the entire
21-gate suite at module level, so a Pool created from it would have re-run the
whole suite in all 32 workers, recursively, forever. Two things prevent that:

  1. sim/tests.py now does its work inside functions, under
     `if __name__ == "__main__":`. A worker re-importing it as __mp_main__
     executes nothing.
  2. The worker entry point lives HERE, in a module with no side effects on
     import.

POPULATIONS ARE PASSED AS SPECS, NOT OBJECTS. A worker rebuilds the population
itself with `w3.make_pop(n, k, default_rng(seed), ...)`, which is
deterministic. That keeps the pickled payload to five numbers and removes any
question about whether a population survived serialisation intact.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import numpy as np
import w3
import harness

# spec -> population. Per process; workers each build their own once.
_POP_CACHE = {}

# One RNG shared across every job in this process. Used ONLY by the T9 mutant,
# to emulate the exact defect the gate exists to catch: a pool that draws its
# seeds from a shared stream, so results depend on execution order.
_MUTANT_RNG = None


def build_pop(spec):
    """
    spec = (n, k, pop_seed, spend, days[, payday_day0_frac[, irregular_frac]]).

    The two optional tail elements exist for the misspecification study, which
    needs populations with a wider payday spread or irregular income. They
    default to w3.make_pop's own defaults, so a 5-element spec builds exactly
    the population it always did.
    """
    spec = tuple(spec)
    if spec not in _POP_CACHE:
        n, k, pop_seed, spend, days = spec[:5]
        p0f = spec[5] if len(spec) > 5 else 0.60
        irr = spec[6] if len(spec) > 6 else 0.0
        _POP_CACHE[spec] = w3.make_pop(n, k, np.random.default_rng(pop_seed),
                                       days=days, spend=spend,
                                       payday_day0_frac=p0f,
                                       irregular_frac=irr)
    return _POP_CACHE[spec]


def _one(job):
    """job = (key, policy, pop_spec, seed, kwargs_items, shared_seed_mutant)"""
    key, policy, spec, seed, kw_items, mutant = job
    kw = dict(kw_items)
    if mutant:
        global _MUTANT_RNG
        if _MUTANT_RNG is None:
            _MUTANT_RNG = np.random.default_rng()      # OS entropy, on purpose
        seed = int(_MUTANT_RNG.integers(0, 2 ** 31 - 1))
    return key, harness.run(policy, build_pop(spec), seed, **kw)


def run_jobs(jobs, workers=None, shared_seed_mutant=False, serial=False):
    """
    jobs: iterable of (key, policy, pop_spec, seed, kwargs_dict).
    Returns {key: result}. Duplicate keys are executed once.

    `serial=True` (or SIM_SERIAL=1 in the environment) runs everything in this
    process. Useful for profiling and for debugging a worker crash, and it must
    produce identical results -- that is the whole point.
    """
    seen, plan = set(), []
    for key, policy, spec, seed, kw in jobs:
        if key in seen:
            continue
        seen.add(key)
        plan.append((key, policy, tuple(spec), seed,
                     tuple(sorted(kw.items())), shared_seed_mutant))

    if not plan:
        return {}

    if serial or os.environ.get("SIM_SERIAL") == "1":
        return dict(_one(j) for j in plan)

    if workers is None:
        workers = min(len(plan), os.cpu_count() or 4, 32)
    if workers <= 1:
        return dict(_one(j) for j in plan)

    # PIN EVERY WORKER TO ONE BLAS THREAD. Set in the parent so spawned
    # children inherit it before they import numpy -- setting it inside the
    # worker would be too late, because multiprocessing re-imports the main
    # module (and therefore numpy) first.
    #
    # Two reasons. It is free: every array here is 90 elements with a 3-tap
    # kernel, far below any threshold where a threaded BLAS helps. And 32
    # workers each spinning up a 32-thread pool is ~1000 threads, which this
    # machine has been observed to fall over under -- see NOTES.md, the
    # intermittent 0xC0000005.
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ.setdefault(var, "1")

    # ProcessPoolExecutor over spawn. chunksize=1 because run times are very
    # uneven (0.6s to 25s): a bigger chunk would leave one worker holding all
    # the slow _pd jobs while the rest idle.
    from concurrent.futures import BrokenExecutor, ProcessPoolExecutor
    out = {}
    try:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for key, res in ex.map(_one, plan, chunksize=1):
                out[key] = res
        return out
    except BrokenExecutor as exc:
        # A worker died outright (on Windows this shows up as 0xC0000005).
        # Do NOT swallow this: an intermittent crash in the simulator is a
        # finding, not noise. Say so loudly, then finish the work serially so
        # the suite still produces a verdict rather than no verdict -- a gate
        # that silently does not run is the failure mode this whole repo is
        # built around avoiding.
        left = [j for j in plan if j[0] not in out]
        print("\n" + "!" * 78)
        print(f"RUNNER: a worker process died ({type(exc).__name__}: {exc}).")
        print(f"RUNNER: {len(out)} of {len(plan)} jobs completed; re-running "
              f"the remaining {len(left)} SERIALLY.")
        print("RUNNER: results are unaffected -- every run is deterministic in "
              "its seed, which T9 checks -- but this crash is real and is "
              "logged in NOTES.md as unresolved.")
        print("!" * 78 + "\n", flush=True)
        for j in left:
            key, res = _one(j)
            out[key] = res
        return out
