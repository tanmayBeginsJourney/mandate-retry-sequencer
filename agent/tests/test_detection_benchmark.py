"""THE DETECTION BENCHMARK. Excess loss against an oracle that knows the true
change points, decomposed, gated, and paired with four crippled oracles.

WHY THIS EXISTS. The recovery channel is measured and it saturates: outage
awareness is worth +0.256 pts at severity 0.80 (`suppress`, SIG) and pausing is
significantly NEGATIVE at severity 0.40 (-0.529, SIG). A number with a ceiling
that small cannot rank detectors -- every detector scores about the same,
because there is almost nothing to score. Detection can. So the scoreboard moves
from recovery to detection, and detection needs an upper bound to be measured
against.

The construction is borrowed, and cited rather than re-invented. arXiv
2604.10177 (piecewise-stationary restless bandits, April 2026) measures excess
regret against "an oracle that restarts the base algorithm at the true change
points", so that the stationary performance of the base solver factors out and
what remains is the cost of exploration and detection. Same shape here: our base
solver is frozen, so what factors out is the timing brain and what remains is
the context layer. Their bound decomposes into exploration cost, detection
delay, and false alarms / missed detections; the five-way partition below is
ours. [VERIFIED] from that paper's abstract and HTML full text, 29 Aug 2026.

WHAT IS PRE-REGISTERED, AND WHERE. NOTES.md, 29 August 2026, committed before
this file existed, plus a same-day amendment splitting G-1. Predictions
E-BEN-1..8 are scored at the bottom. Prior records on this project: 2/7, 3/7,
6/8, 3/6.

--------------------------------------------------------------------------
THE LOSS

Ground truth is `s*(t) = OUTAGE iff t in W`. A detector's trajectory `s(t)` is
its LATCHED state, reconstructed from its transition log. Excess loss is the
Hamming distance in detector-hours, partitioned exhaustively:

  DELAY        t in W, s=NORMAL, before that window's first detection
  MISSED       t in W, s=NORMAL, in a window never detected within [lo, hi+24)
  DROPOUT      t in W, s=NORMAL, after that same window was detected
  LATE         t not in W, s=OUTAGE, in an episode that began inside a window
  FALSE_ALARM  t not in W, s=OUTAGE, in an episode that never touched a window

`DELAY + MISSED + DROPOUT + LATE + FALSE_ALARM == L` is ASSERTED, not hoped
for. MISSED is reported separately rather than folded into DELAY, because
folding a miss into a delay is how a detector that never fires comes to look
merely slow.

NO SINGLE SCORE. The five components are never summed with weights. An hour of
false alarm is not obviously worth an hour of missed outage, no source gives an
exchange rate, and inventing one would be rule 5.

--------------------------------------------------------------------------
THE ORACLE, AND THE THING ERROR 5 TEACHES

Error 5 was a broken oracle that made the system look near-optimal, and its
guard gate -- "oracle approval ~= 100%" -- was VACUOUS: true whether the oracle
worked or not. So the gate here is split and the split is stated out loud.

  G-1a  the ANALYTIC oracle, s*(t) = 1[t in W]. Loss identically zero.
        TRUE BY CONSTRUCTION. Carries no information. Printed, never counted.

  G-1b  the oracle AS CONSULTED -- the same clairvoyant object, run through the
        real loop, graded from its transition log exactly like every other arm.
        The loop only asks the monitor anything when it has work to do: once per
        pending dispatch and once per customer at the hour-8 decision. Windows
        run [hour 8, hour 14), so the ENTRY boundary is consulted exactly and
        the EXIT boundary is not consulted at all. A perfect oracle therefore
        carries a floor of latched late-resumption hours that no detector can
        beat and none of them caused. That floor is measured here rather than
        assumed, and G-1b -- oracle-as-consulted <= every statistical detector,
        at every severity -- is a claim that CAN fail.

  G-2   at severity 0 the oracle arm is identical to the monitor-off arm.
        Witness: cycle_rec, which comes from the accounting, not the monitor.

  G-3   with pausing on, the oracle executes ZERO attempts inside any window at
        every severity > 0, and every statistical detector executes MORE THAN
        ZERO. Witness: `SimExecutor.n_attempts_in_outage`, incremented by the
        EXECUTOR from the schedule object, sharing no code with any monitor.
        This is the only check in this work whose witness is written by
        different code from the thing it checks, and it is the one that matters.

THE MUTANTS ARE WINDOW TRANSFORMS, NOT CODE BRANCHES. Rule 1a taken literally:
a crippled oracle differs from the true one only in the list of numbers it is
handed, so it executes byte-identical code and cannot write to any scoreboard,
special-case itself, or be exempted -- there is no branch to exempt. One mutant
per named loss component:

  M-BLIND    W -> []                      cripples MISSED
  M-LATE     [lo,hi) -> [lo+d, hi+d)      cripples DELAY
  M-LATCH    [lo,hi) -> [lo, T)           cripples LATE
  M-PHANTOM  W -> W + two fabricated      cripples FALSE_ALARM

EVERY MUTANT MUST BE CAUGHT BY AT LEAST ONE GATE. If any crippled oracle passes
all three, this reports VACUOUS, not PASS. That is the whole point of the
exercise and it is the lesson of error 5's guard gate.

--------------------------------------------------------------------------
HOW THIS COULD BE BIASED TOWARD THE ANSWER WE WANT

  * Window placement is WORST CASE. Every window starts at hour 8, where 99.22%
    of attempts land, so every detection figure here is an UPPER bound on both
    the damage and the detectability.
  * Severity is invented. Nothing found reports what fraction of UPI AutoPay
    executions fail during a rail incident. [GUESS], swept.
  * Detection is measured with the response OFF, because pausing suppresses the
    evidence that produces detection (that confound is error 16's shape).
    Recovery is measured separately with pausing ON. The two tables are not two
    views of one run.
  * Excess loss in hours weights a 6h window and a 24h hold equally. See
    "NO SINGLE SCORE" above.
  * n=100, 8 populations, one run seed each. Not a large study.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

import numpy as np

import agent  # noqa: F401
import w3

from agent.context.oracle_monitor import MUTANTS, crippled
from agent.tests._parallel import agent_job, run_jobs

N, K, DAYS, SPEND, PE, RUN_SEED = 100, 5, 120, 1.05, 7, 7
POPS = [700, 701, 702, 703, 704, 705, 706, 707]
SEVERITIES = [0.00, 0.15, 0.40, 0.80]
#: Mutants are measured at severity 0 too, because M-PHANTOM can only be
#: caught where there is nothing to detect -- and a gate that no mutant can
#: trip is not a gate.
MUTANT_SEVERITIES = [0.00, 0.40, 0.80]
G3_SEVERITIES = [0.00, 0.40]
G3_SEVERITY = 0.40
OUTAGE_DAYS = [20, 50, 80, 110]
DURATION_H = 6
START_HOUR = 8
GRACE_H = 24                     # evidence takes time; same as the TPR study
T_HOURS = DAYS * w3.HOURS

#: The statistical detector family. `min_attempts` is a [GUESS] flagged as the
#: obvious sweep in docs/00_HANDOFF.md item 0c, and the non-monotone TPR result
#: sits right at its cliff, so it is what the family varies.
DETECTORS = {
    "stat ma=4":  dict(min_attempts=4),
    "stat ma=8":  dict(min_attempts=8),          # ships
    "stat ma=16": dict(min_attempts=16),
}
SHIPPING = "stat ma=8"


def _outage_kw(sev):
    """severity 0 means NO WINDOWS AT ALL, not a window with nothing in it.
    Same convention as test_outage_detection.py. Ground truth W is empty, so
    the only loss a detector can incur there is a false alarm -- which is
    exactly what severity zero is in the table for."""
    if sev == 0.0:
        return None
    return dict(days=OUTAGE_DAYS, duration_h=DURATION_H, severity=sev,
                start_hour=START_HOUR)


def windows_for(sev):
    if sev == 0.0:
        return []
    return [(d * w3.HOURS + START_HOUR, d * w3.HOURS + START_HOUR + DURATION_H)
            for d in OUTAGE_DAYS]


# ------------------------------------------------------------- the grader
def trajectory(transitions, T=T_HOURS):
    """Latched state as a step function. 0 = NORMAL, 1 = OUTAGE."""
    s = np.zeros(T, dtype=np.int8)
    cur, last = 0, 0
    for tr in sorted(transitions, key=lambda x: x[0]):
        t, label = int(tr[0]), tr[1]
        t = max(0, min(t, T))
        s[last:t] = cur
        cur = 1 if label.endswith("->OUTAGE") else 0
        last = t
    s[last:T] = cur
    return s


def _episodes(s):
    """Maximal runs of s == 1, as [start, end) pairs."""
    out, i, n = [], 0, len(s)
    while i < n:
        if s[i]:
            j = i
            while j < n and s[j]:
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def decompose(s, windows, T=T_HOURS, grace=GRACE_H):
    """Excess loss against the analytic oracle, partitioned exhaustively.

    Returns a dict. The partition is ASSERTED to be exhaustive; a decomposition
    that does not add up is a decomposition that is hiding a bucket.
    """
    truth = np.zeros(T, dtype=np.int8)
    for lo, hi in windows:
        truth[lo:min(hi, T)] = 1
    fn = (truth == 1) & (s == 0)          # outage running, detector silent
    fp = (truth == 0) & (s == 1)          # detector shouting, rail is fine
    total = int(fn.sum()) + int(fp.sum())

    delay = missed = dropout = 0
    lats, detected = [], 0
    for lo, hi in windows:
        look = s[lo:min(hi + grace, T)]
        nz = np.nonzero(look)[0]
        if len(nz) == 0:
            missed += int(fn[lo:min(hi, T)].sum())
            continue
        first = lo + int(nz[0])
        detected += 1
        lats.append(first - lo)
        cut = min(first, hi)
        delay += int(fn[lo:cut].sum())
        dropout += int(fn[cut:min(hi, T)].sum())

    late = false_alarm = 0
    fa_episodes = 0
    for a, b in _episodes(s):
        # ATTRIBUTION MUST MATCH THE DETECTION RULE ABOVE, and in the first
        # version of this file it did not. Detection counted an alarm inside
        # [lo, hi+grace) as a (late) detection of that window; episode
        # attribution asked only whether the episode OVERLAPPED [lo, hi). So a
        # next-day alarm was scored simultaneously as "window detected, latency
        # 24h" and as "false alarm" -- two different answers to the same
        # question about the same episode. Fixed to use the grace window in
        # both places.
        #
        # SAID OUT LOUD BECAUSE IT MOVES A NUMBER IN OUR FAVOUR: this shrinks
        # the FALSE_ALARM column at severity > 0 and grows LATE. It cannot
        # touch severity 0, where W is empty and there is nothing to attribute
        # to, so the headline false-alarm claim is unaffected by it. Both
        # readings are printed.
        touches = any((a < hi and lo < b) or (lo <= a < hi + grace)
                      for lo, hi in windows)
        hours = int(fp[a:b].sum())
        if touches:
            late += hours
        else:
            false_alarm += hours
            fa_episodes += 1

    # THE PARTITION MUST BE EXHAUSTIVE. A bucket that does not add up is a
    # bucket that is quietly absorbing something.
    assert delay + missed + dropout + late + false_alarm == total, (
        f"decomposition does not add up: {delay}+{missed}+{dropout}+{late}"
        f"+{false_alarm} != {total}")

    # ------------------------------------------------------ decision-point loss
    # ADDED AFTER G-1b WENT RED, AND THE ORIGINAL IS KEPT RED BESIDE IT.
    #
    # The hour-count above has a defect that its own gate found. A detector
    # that fires and holds until the next hour-8 consultation accrues up to
    # 18 wrong hours per window; a detector that NEVER FIRES accrues at most 6
    # -- the window length. So under an unweighted hour count silence is
    # cheaper than correctness, the blind mutant beats the oracle, and the
    # least sensitive detector wins. That is a defect in the loss's TIME BASE,
    # not in the oracle.
    #
    # The repair is the one the bandit literature already uses: regret is
    # summed over ROUNDS AT WHICH THE ALGORITHM ACTS, not over wall clock. Our
    # rounds are the hours the monitor is actually consulted, and 99.22% of
    # attempts plus every scheduling decision land at hour 8. So the decision
    # -point loss counts one unit per DAY on which the monitor's latched state
    # was wrong at hour 8. Hours between hour 14 and the next hour 8, when
    # nobody asks the monitor anything and no dispatch is possible, cost
    # nothing -- because they change nothing.
    #
    # G-1b IS NOT REPAIRED AND NOT DELETED. Repairing a metric after it returns
    # an inconvenient answer is indistinguishable from moving a threshold
    # (CLAUDE.md rule 1), and this repo keeps S1, S2b and S2_LEGACY red on
    # exactly that principle. Both are reported; the gate reads the new one and
    # the old one stays visible with its diagnosis.
    dp = np.arange(8, T, 24)
    dp_loss = int((s[dp] != truth[dp]).sum())
    dp_missed = int(((truth[dp] == 1) & (s[dp] == 0)).sum())
    dp_false = int(((truth[dp] == 0) & (s[dp] == 1)).sum())

    return dict(loss=total, delay=delay, missed=missed, dropout=dropout,
                late=late, false_alarm=false_alarm,
                fa_episodes=fa_episodes, detected=detected,
                n_windows=len(windows),
                dp_loss=dp_loss, dp_missed=dp_missed, dp_false=dp_false,
                latency=(float(np.mean(lats)) if lats else float("nan")),
                latencies=lats)


def mean_of(dicts, field):
    vals = [d[field] for d in dicts]
    if all(isinstance(v, float) and np.isnan(v) for v in vals):
        return float("nan")
    keep = [v for v in vals if not (isinstance(v, float) and np.isnan(v))]
    return float(np.mean(keep)) if keep else float("nan")


# ---------------------------------------------------------------- the runs
def build_jobs():
    jobs = []
    base = dict(payday_err=PE, pop_spend=SPEND, bcfg=w3.FITTED_BELIEF,
                mode="degenerate", time_major=True,
                suppress_tech_updates="never")

    # --- detection, response OFF
    for sev in SEVERITIES:
        ok = _outage_kw(sev)
        for name, kw in DETECTORS.items():
            for s in POPS:
                jobs.append((("det", sev, name, s), (N, K, s, SPEND, DAYS),
                             RUN_SEED,
                             dict(base, outage_kw=ok, monitor_enabled=True,
                                  pause_on_outage=False, monitor_kw=kw),
                             False))
        for s in POPS:
            jobs.append((("det", sev, "ORACLE", s), (N, K, s, SPEND, DAYS),
                         RUN_SEED,
                         dict(base, outage_kw=ok, monitor_enabled=True,
                              pause_on_outage=False, monitor_kind="oracle"),
                         False))
        if sev in MUTANT_SEVERITIES:
            for mut in MUTANTS:
                for s in POPS:
                    jobs.append((("det", sev, f"M-{mut.upper()}", s),
                                 (N, K, s, SPEND, DAYS), RUN_SEED,
                                 dict(base, outage_kw=ok, monitor_enabled=True,
                                      pause_on_outage=False,
                                      monitor_kind="oracle",
                                      oracle_mutant=mut),
                                 False))

    # --- recovery + the behavioural witness, response ON
    for sev in SEVERITIES:
        ok = _outage_kw(sev)
        for s in POPS:
            jobs.append((("rec", sev, "monitor off", s),
                         (N, K, s, SPEND, DAYS), RUN_SEED,
                         dict(base, outage_kw=ok, monitor_enabled=False,
                              pause_on_outage=False), False))
            jobs.append((("rec", sev, SHIPPING, s), (N, K, s, SPEND, DAYS),
                         RUN_SEED,
                         dict(base, outage_kw=ok, monitor_enabled=True,
                              pause_on_outage=True,
                              monitor_kw=DETECTORS[SHIPPING]), False))
            jobs.append((("rec", sev, "ORACLE", s), (N, K, s, SPEND, DAYS),
                         RUN_SEED,
                         dict(base, outage_kw=ok, monitor_enabled=True,
                              pause_on_outage=True, monitor_kind="oracle"),
                         False))
    for sev in G3_SEVERITIES:
        for mut in MUTANTS:
            for s in POPS:
                jobs.append((("rec", sev, f"M-{mut.upper()}", s),
                             (N, K, s, SPEND, DAYS), RUN_SEED,
                             dict(base, outage_kw=_outage_kw(sev),
                                  monitor_enabled=True, pause_on_outage=True,
                                  monitor_kind="oracle", oracle_mutant=mut),
                             False))
    return jobs


CACHE = os.path.join(HERE, "_bench_cache.pkl")
CHUNK = 48
MAX_RETRIES = 4


def _cache_fingerprint(jobs):
    """What this cache is a cache OF: the belief, the world, and the job set.

    WHY THIS EXISTS. On 2 September 2026 this benchmark was re-run to give the
    detection claims a transcript. It finished in three minutes instead of
    twenty and printed `resuming: 384 run(s) already on disk`. The cache had
    been written on **29 August**, four days and one belief repair earlier
    (W24 moved `prior_w` 9 -> 5, `prior_floor` 0.5 -> 0.1 and `cycle_value`
    0 -> 0.6), and the key was the job tuple alone -- which does not mention
    the belief, because the belief arrives through `w3.FITTED_BELIEF` rather
    than through the job. So every re-run since 29 August had replayed the old
    filter and reported it as a current measurement.

    That is worse than a stale number in a document. A stale document is
    visibly old; a cache that resumes is a measurement that LOOKS fresh, and
    the only thing standing between it and the reader was one line of stdout
    nobody was reading. `NOTES.md`, error 36, records it.

    The fingerprint is deliberately over-broad: any change to the belief, the
    grid, the detector family or the world knobs invalidates everything. A
    cache is a wall-clock convenience, and the cost of discarding one wrongly
    is twenty minutes. The cost of keeping one wrongly is a published number
    that never happened.
    """
    import hashlib
    payload = repr([
        sorted(dict(w3.FITTED_BELIEF).items()),
        N, K, DAYS, SPEND, PE, RUN_SEED, POPS,
        SEVERITIES, MUTANT_SEVERITIES, G3_SEVERITIES,
        OUTAGE_DAYS, DURATION_H, START_HOUR, GRACE_H,
        sorted((k, sorted(v.items())) for k, v in DETECTORS.items()),
        sorted(MUTANTS), SHIPPING,
        sorted(j[0] for j in jobs),
    ])
    return hashlib.sha256(payload.encode()).hexdigest()


def run_chunked(jobs, cache_path=CACHE):
    """Run `jobs` in chunks, checkpointing to disk, retrying a crashed chunk.

    WHY THIS EXISTS AND WHY IT IS NOT "DROPPING A CRASHED RUN". The first
    attempt at this benchmark died after 14m38s: `BrokenProcessPool`, one
    worker gone, 384 runs lost. That is the unexplained 0xC0000005 recorded in
    NOTES.md and docs/06_MODEL_CARD.md 6a -- contained, never fixed.

    `run_jobs` raises on a dead worker on purpose: a crashed run is a FAILED
    measurement, not a missing one, and silently skipping it would change the
    sample a mean is taken over (error 4's shape). That principle is kept. What
    changes here is only WHERE the failure is absorbed: `run_once` is
    deterministic in its seeds, so re-running the identical job in a fresh
    interpreter re-measures the same thing rather than resampling it. The
    sample is unchanged; only the wall clock moves.

    Two rules keep that honest:
      * retries are COUNTED and PRINTED. A machine fault that is getting worse
        must be visible in the output, not absorbed by a loop.
      * retries are CAPPED. Past the cap this raises, because a job that will
        not complete in five fresh interpreters is a defect, not a fault.
    """
    import pickle
    done = {}
    want = _cache_fingerprint(jobs)
    if os.path.exists(cache_path):
        with open(cache_path, "rb") as fh:
            blob = pickle.load(fh)
        got = blob.get("fingerprint") if isinstance(blob, dict) else None
        if got == want:
            done = blob["runs"]
            print(f"  resuming: {len(done)} run(s) already on disk "
                  f"(fingerprint {want[:12]})")
        else:
            print(f"  DISCARDING the cache at {os.path.basename(cache_path)}: "
                  f"fingerprint {str(got)[:12]} != {want[:12]}. "
                  f"Re-measuring all {len(jobs)} runs.")
    todo = [j for j in jobs if j[0] not in done]
    retries = 0
    for i in range(0, len(todo), CHUNK):
        chunk = todo[i:i + CHUNK]
        for attempt in range(MAX_RETRIES + 1):
            try:
                done.update(run_jobs(agent_job, chunk))
                break
            except Exception as exc:
                retries += 1
                if attempt == MAX_RETRIES:
                    raise RuntimeError(
                        f"chunk starting at job {i} failed {MAX_RETRIES + 1} "
                        f"times in fresh interpreters. That is a defect, not "
                        f"the machine fault.") from exc
                print(f"  worker died ({type(exc).__name__}); re-running "
                      f"chunk {i // CHUNK} in fresh interpreters "
                      f"(retry {attempt + 1}/{MAX_RETRIES})")
        with open(cache_path, "wb") as fh:
            pickle.dump({"fingerprint": want, "runs": done}, fh)
        print(f"  {min(i + CHUNK, len(todo))}/{len(todo)} new runs complete")
    return {j[0]: done[j[0]] for j in jobs}, retries


def main() -> int:
    jobs = build_jobs()
    print(f"{len(jobs)} runs, one process each "
          f"(max_tasks_per_child=1 -- 06_MODEL_CARD.md 6a)")
    res, n_retries = run_chunked(jobs)
    print(f"  worker deaths absorbed by re-running the identical "
          f"deterministic job: {n_retries}")

    # ---------------------------------------------------- sanity on the world
    print()
    print("=" * 108)
    print("THE ORACLE'S TARGET, AND THAT THE WORLD AGREES WITH IT")
    print("=" * 108)
    for sev in SEVERITIES:
        w = windows_for(sev)
        print(f"  severity {sev:.2f}: ground truth W = {len(w)} window(s) "
              f"{[tuple(x) for x in w[:2]]}{' ...' if len(w) > 2 else ''}")
    print("  The oracle is handed `outage.windows` by the composition root, "
          "not by this file, so it cannot be graded against a target the "
          "world does not share. G-3's witness is the EXECUTOR's counter, "
          "which is independent of both.")

    # ------------------------------------------------------ detection table
    print()
    print("=" * 108)
    print("DETECTION -- excess loss in detector-hours vs the analytic oracle, "
          "per run, response OFF")
    print(f"n={N}, k={K}, {DAYS}d, payday_err={PE}, FITTED_BELIEF, "
          f"{len(OUTAGE_DAYS)}x{DURATION_H}h outages on days {OUTAGE_DAYS}, "
          f"worst-case placement (hour {START_HOUR}), {len(POPS)} populations")
    print("=" * 108)
    print("  LOSS.. are detector-HOURS (the metric G-1b found broken; kept "
          "visible). DP.. are DECISION-POINTS: one per day, at hour 8.")
    print(f"{'sev':>5s} {'detector':>12s} {'det/win':>8s} {'latency h':>10s} "
          f"{'LOSS':>7s} {'DELAY':>7s} {'MISSED':>7s} {'DROP':>6s} "
          f"{'LATE':>7s} {'FALSE':>7s} {'FA runs':>8s} | "
          f"{'DP LOSS':>8s} {'DPmiss':>7s} {'DPfalse':>8s}")

    det = {}
    arms_at = {}
    for sev in SEVERITIES:
        w = windows_for(sev)
        names = ["ORACLE (analytic)"] + list(DETECTORS) + ["ORACLE"]
        if sev in MUTANT_SEVERITIES:
            names += [f"M-{m.upper()}" for m in MUTANTS]
        arms_at[sev] = names
        for name in names:
            if name == "ORACLE (analytic)":
                truth = np.zeros(T_HOURS, dtype=np.int8)
                for lo, hi in w:
                    truth[lo:hi] = 1
                ds = [decompose(truth, w)] * len(POPS)
            else:
                ds = [decompose(trajectory(res[("det", sev, name, s)]
                                           ["rail_transitions"]), w)
                      for s in POPS]
            det[(sev, name)] = ds
            fa_runs = sum(1 for d in ds if d["fa_episodes"] > 0)
            dr = (mean_of(ds, "detected") / len(w)) if w else float("nan")
            lat = mean_of(ds, "latency")
            print(f"{sev:5.2f} {name:>12s} "
                  f"{(f'{dr:.2f}' if w else '--'):>8s} "
                  f"{(f'{lat:.1f}' if not np.isnan(lat) else '--'):>10s} "
                  f"{mean_of(ds, 'loss'):7.1f} {mean_of(ds, 'delay'):7.1f} "
                  f"{mean_of(ds, 'missed'):7.1f} {mean_of(ds, 'dropout'):6.1f} "
                  f"{mean_of(ds, 'late'):7.1f} "
                  f"{mean_of(ds, 'false_alarm'):7.1f} "
                  f"{fa_runs:5d}/{len(POPS):<3d} | "
                  f"{mean_of(ds, 'dp_loss'):8.2f} {mean_of(ds, 'dp_missed'):7.2f} "
                  f"{mean_of(ds, 'dp_false'):8.2f}")
        print()

    # --------------------------------------------------- latency histogram
    print("=" * 108)
    print("LATENCY, EVERY DETECTED WINDOW -- is it quantised? (E-BEN-2)")
    print("=" * 108)
    edges = [(0, 1), (1, 6), (6, 12), (12, 23), (23, 25), (25, 10 ** 6)]
    print(f"{'detector':>12s} {'n windows':>10s} " +
          " ".join(f"{f'[{a},{b})h':>10s}" for a, b in edges))
    lat_hist = {}
    for name in list(DETECTORS) + ["ORACLE"]:
        all_l = [x for sev in SEVERITIES if sev > 0
                 for d in det[(sev, name)] for x in d["latencies"]]
        counts = [sum(1 for x in all_l if a <= x < b) for a, b in edges]
        lat_hist[name] = (all_l, counts)
        print(f"{name:>12s} {len(all_l):10d} " +
              " ".join(f"{c:10d}" for c in counts))

    # -------------------------------------------------------- recovery table
    print()
    print("=" * 108)
    print("RECOVERY -- SECONDARY. Response ON (pause). The ceiling is +0.256 "
          "pts (suppress, sev 0.80).")
    print("=" * 108)
    print(f"{'sev':>5s} {'arm':>12s} {'cycle_rec':>10s} {'vs monitor off':>15s} "
          f"{'2SE':>6s} {'sig':>5s} {'att in outage':>14s} {'paused':>8s}")

    def rcol(sev, arm, field):
        return np.array([res[("rec", sev, arm, s)][field] for s in POPS],
                        dtype=float)

    rec_gain, att_in = {}, {}
    for sev in SEVERITIES:
        base = rcol(sev, "monitor off", "cycle_rec")
        for arm in ("monitor off", SHIPPING, "ORACLE"):
            cr = rcol(sev, arm, "cycle_rec")
            d = cr - base
            m = d.mean() * 100
            se = (2 * d.std(ddof=1) / np.sqrt(len(d)) * 100) if d.std() > 0 else 0.0
            rec_gain[(sev, arm)] = m
            ai = rcol(sev, arm, "exec_attempts_in_outage")
            att_in[(sev, arm)] = ai
            print(f"{sev:5.2f} {arm:>12s} {cr.mean()*100:10.2f} {m:+15.3f} "
                  f"{se:6.3f} "
                  f"{('SIG' if abs(m) > se and se > 0 else 'n.s.'):>5s} "
                  f"{ai.sum():14.0f} "
                  f"{rcol(sev, arm, 'paused_dispatch').sum():8.0f}")
        print()

    print(f"  the four crippled oracles, pausing on, severities "
          f"{G3_SEVERITIES}")
    print(f"{'':5s} {'arm':>12s} {'cycle_rec':>10s} {'':15s} {'':6s} {'':5s} "
          f"{'att in outage':>14s} {'paused':>8s}")
    for mut in MUTANTS:
        arm = f"M-{mut.upper()}"
        cr = rcol(G3_SEVERITY, arm, "cycle_rec")
        ai = rcol(G3_SEVERITY, arm, "exec_attempts_in_outage")
        att_in[(G3_SEVERITY, arm)] = ai
        print(f"{'':5s} {arm:>12s} {cr.mean()*100:10.2f} {'':15s} {'':6s} "
              f"{'':5s} {ai.sum():14.0f} "
              f"{rcol(G3_SEVERITY, arm, 'paused_dispatch').sum():8.0f}")

    # ----------------------------------------------------------- THE GATES
    print()
    print("=" * 108)
    print("THE GATES, AND WHETHER A CRIPPLED ORACLE CAN BE TOLD FROM A REAL ONE")
    print("=" * 108)

    def loss_of(sev, name):
        return mean_of(det[(sev, name)], "loss")

    def dp_of(sev, name):
        return mean_of(det[(sev, name)], "dp_loss")

    def dominates(cand, metric):
        """Would `cand` serve as the oracle under `metric`? It must weakly
        dominate every STATISTICAL detector at every severity it was measured
        at. Returns the list of (severity, detector) pairs where it does not."""
        bad = []
        for sev in SEVERITIES:
            if cand not in arms_at[sev]:
                continue
            for d in DETECTORS:
                if metric(sev, cand) > metric(sev, d) + 1e-9:
                    bad.append((sev, d))
        return bad

    def g2_for(arm):
        """At severity 0 the arm must be identical to monitor-off."""
        if ("rec", 0.0, arm, POPS[0]) not in res:
            return None
        a = rcol(0.0, arm, "cycle_rec")
        b = rcol(0.0, "monitor off", "cycle_rec")
        pd = rcol(0.0, arm, "paused_dispatch").sum()
        return bool(np.allclose(a, b) and pd == 0)

    def g3_for(arm):
        """With pausing on, zero attempts executed inside a window."""
        if (G3_SEVERITY, arm) not in att_in:
            return None
        return bool(att_in[(G3_SEVERITY, arm)].sum() == 0)

    print(f"{'candidate oracle':>16s} {'G-1b hours':>11s} {'G-1c decis':>11s} "
          f"{'G-2 sev0':>9s} {'G-3 zero att':>13s}  verdict")
    caught_by, g1b_state = {}, {}
    for cand in ["ORACLE"] + [f"M-{m.upper()}" for m in MUTANTS]:
        b1 = dominates(cand, loss_of)
        c1 = dominates(cand, dp_of)
        r2 = g2_for(cand)
        r3 = g3_for(cand)
        g1b_state[cand] = not b1
        gates = []
        if c1:
            gates.append("G-1c")
        if r2 is False:
            gates.append("G-2")
        if r3 is False:
            gates.append("G-3")
        caught_by[cand] = gates
        verdict = ("passes every gate" if not gates
                   else "CAUGHT by " + ", ".join(gates))
        print(f"{cand:>16s} {('yes' if not b1 else 'NO'):>11s} "
              f"{('yes' if not c1 else 'NO'):>11s} "
              f"{(('yes' if r2 else 'NO') if r2 is not None else '--'):>9s} "
              f"{(('yes' if r3 else 'NO') if r3 is not None else '--'):>13s}"
              f"  {verdict}")
    print()
    print("  G-1a: the ANALYTIC oracle's loss is "
          f"{loss_of(SEVERITIES[-1], 'ORACLE (analytic)'):.1f} at every "
          "severity -- zero BY CONSTRUCTION. Printed, never counted. This is "
          "error 5's guard-gate shape and it carries no information.")

    print()
    print("  G-1b IS RED AND STAYS RED. The oracle does not dominate on "
          "unweighted detector-hours: at severity 0.15 it scores worse than "
          "every statistical detector, and the blind mutant scores best of "
          "all. Diagnosis, not repair -- MISSED is capped at the 6h window "
          "length while LATE runs to the ~18h gap before the next hour-8 "
          "consultation, so silence is cheaper than correctness. The gate "
          "found a defect in ITS OWN METRIC's time base and is kept visible "
          "for the same reason S1, S2b and S2_LEGACY are.")
    print("  G-1c is the same statement on decision-points -- one per day, at "
          "hour 8, which is where 99.22% of attempts and every scheduling "
          "decision land. That is the gate the suite verdict reads.")

    survivors = [c for c in caught_by if c.startswith("M-") and not caught_by[c]]
    oracle_caught = bool(caught_by["ORACLE"])
    if survivors:
        gate_state = "VACUOUS"
        gate_note = (f"crippled oracle(s) {survivors} pass every gate. A gate "
                     f"no mutant can trip is not a gate.")
    elif oracle_caught:
        gate_state = "FAIL"
        gate_note = (f"the TRUE oracle is caught by {caught_by['ORACLE']} -- "
                     f"the gates flag everything and so discriminate nothing.")
    else:
        gate_state = "PASS"
        gate_note = (f"all {len(MUTANTS)} crippled oracles caught, true oracle "
                     f"clean.")
    print()
    print(f"  GATE SUITE: {gate_state} -- {gate_note}")

    # -------------------------------------------------- pre-registered checks
    print()
    print("=" * 108)
    print("PRE-REGISTERED CHECKS (NOTES.md, 29 Aug 2026, before any run)")
    print("=" * 108)
    v = []

    fires_at_high = {d: mean_of(det[(0.80, d)], "detected") > 0
                     for d in DETECTORS}
    fa0 = {d: sum(1 for x in det[(0.0, d)] if x["fa_episodes"] > 0)
           for d in DETECTORS}
    alive = any(fires_at_high.values())
    v.append(("E-BEN-1 zero false-alarm episodes at severity 0, all detectors",
              all(n == 0 for n in fa0.values()) and alive,
              ", ".join(f"{d}:{n}/{len(POPS)}" for d, n in fa0.items())
              + ("" if alive else
                 "   VACUOUS: no detector fires anywhere, and zero is what a "
                 "disconnected detector reports")))

    mid = sum(c for name in DETECTORS for c in lat_hist[name][1][1:4])
    tot = sum(len(lat_hist[name][0]) for name in DETECTORS)
    v.append(("E-BEN-2 latency is quantised: no mass in [1h, 23h)",
              tot > 0 and mid == 0,
              f"{mid} of {tot} detected windows have latency in [1,23)h"
              + ("" if tot else "   VACUOUS: nothing detected")))

    d80 = det[(0.80, SHIPPING)]
    floor = mean_of(det[(0.80, "ORACLE")], "late")
    v.append(("E-BEN-3 LATE > DELAY for the shipping detector at sev 0.80",
              mean_of(d80, "late") > mean_of(d80, "delay"),
              f"LATE {mean_of(d80, 'late'):.1f} vs DELAY "
              f"{mean_of(d80, 'delay'):.1f}   (oracle's LATE floor "
              f"{floor:.1f})"))

    # SCORED ON THE HOURS METRIC, WHICH IS WHAT WAS PRE-REGISTERED. The
    # decision-point ranking is printed beside it as a POST-HOC observation and
    # is not scored -- scoring a prediction against a metric invented after the
    # prediction broke is how you get a 8/8 that means nothing.
    seq = [loss_of(0.80, d) for d in DETECTORS]
    seqdp = [dp_of(0.80, d) for d in DETECTORS]
    v.append(("E-BEN-4 loss is monotone in min_attempts at sev 0.80",
              all(seq[i] <= seq[i + 1] + 1e-9 for i in range(len(seq) - 1)),
              " <= ".join(f"{d}:{x:.1f}" for d, x in zip(DETECTORS, seq))
              + "   [post-hoc, NOT scored: decision-points "
              + ", ".join(f"{d}:{x:.2f}" for d, x in zip(DETECTORS, seqdp))
              + "]"))

    v.append(("E-BEN-5 the oracle does NOT maximise recovery: it is below "
              "monitor-off at sev 0.40",
              rec_gain[(0.40, "ORACLE")] < 0,
              f"oracle vs monitor-off at sev 0.40 = "
              f"{rec_gain[(0.40, 'ORACLE')]:+.3f} pts"))

    worst = max(rec_gain[(sev, "ORACLE")] - rec_gain[(sev, SHIPPING)]
                for sev in SEVERITIES)
    v.append(("E-BEN-6 perfect detection buys < +0.256 pts over the shipping "
              "detector, at every severity",
              worst < 0.256,
              f"largest oracle - {SHIPPING} = {worst:+.3f} pts"))

    v.append(("E-BEN-7 every crippled oracle is caught by at least one gate",
              not survivors,
              "; ".join(f"{c}->{','.join(g) or 'SURVIVED'}"
                        for c, g in caught_by.items() if c.startswith("M-"))))

    orc_zero = all(att_in[(sev, "ORACLE")].sum() == 0
                   for sev in SEVERITIES if sev > 0)
    stat_pos = all(att_in[(sev, SHIPPING)].sum() > 0
                   for sev in SEVERITIES if sev > 0)
    v.append(("E-BEN-8 G-3 binds both ways: oracle 0 attempts in window, "
              "shipping detector > 0",
              orc_zero and stat_pos,
              f"oracle "
              f"{[int(att_in[(s, 'ORACLE')].sum()) for s in SEVERITIES if s > 0]}"
              f", {SHIPPING} "
              f"{[int(att_in[(s, SHIPPING)].sum()) for s in SEVERITIES if s > 0]}"))

    hits = 0
    for name, passed, detail in v:
        hits += 1 if passed else 0
        print(f"  {'HELD ' if passed else 'BROKE'}  {name}")
        print(f"           [{detail}]")
    print()
    print(f"Pre-registration record for this measurement: {hits}/{len(v)}")
    print(f"Gate suite: {gate_state}")
    print(f"Worker deaths re-run: {n_retries}  "
          f"(see run_chunked -- the machine fault, contained not fixed)")
    return 0 if gate_state == "PASS" and hits == len(v) else 1


if __name__ == "__main__":
    raise SystemExit(main())
