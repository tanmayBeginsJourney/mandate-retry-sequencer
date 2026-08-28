"""
TEST SUITE. Implements TEST_DESIGN.md, which was written before the harness.

Every gate is paired with a mutant that must trip it. A gate no mutant can trip
is reported as VACUOUS and counts as a failure of the suite, not a pass.

Operating point matters. Gates that only bind under contention (M1, S2, T5, T7)
run at payday_err=7. At the harness default of +/-1 day the world is
uncontended -- policies hit payday nearly every time, recovery is ~97%, and a
constraint that is never reached cannot be tested. Everything else stays at the
default. See NOTES.md, 27 August 2026.

-----------------------------------------------------------------------------
RESTRUCTURED 28 August 2026. Three changes, none of them to a gate's logic or
threshold:

1. NOTHING RUNS AT IMPORT any more. Every gate lives in a function and the
   suite runs under `if __name__ == "__main__":`. This is not tidiness -- it is
   required. Windows has no fork(), so multiprocessing re-imports the parent's
   __main__ in every worker. With the suite at module level, creating a pool
   would have re-run all 21 gates inside all 32 workers, recursively.

2. RUNS ARE EXECUTED UP FRONT, IN PARALLEL. `plan_jobs()` enumerates every
   harness.run the selected tier needs; `sim/runner.py` executes them across
   processes; the gates then read results out of _CACHE and do no work. Each
   run is independent and fully determined by its seed, so this is
   identity-preserving by construction -- and gate T9 proves it rather than
   asserting it. A gate asking for something the plan missed still works: R()
   falls back to running it inline, and the miss count is printed, so a
   forgotten job costs time and is visible, never coverage.

3. TWO TIERS. `--tier fast` runs the gates that test whether the CODE is
   correct (all mutants, all invariants, plus S1, which is cheap). `--tier
   full` adds the gates that test whether a STATISTICAL CLAIM holds -- S2a/b/c,
   S2_LEGACY, S3 -- which need 8 populations at n=100 to have any power.
   Those are never run at reduced n to fit a time budget: shrinking them would
   be weakening a test, which is CLAUDE.md rule 1. They run properly or they
   do not run, and a tier that did not run them says so.
"""
import argparse
import ast
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import numpy as np
import w3
import harness
import runner
import t9_reference

PASS, FAIL, VACUOUS = "PASS", "FAIL", "VACUOUS"
results = []

PE_FLAT = 1     # harness default. Payday known to +/-1 day. Uncontended.
PE_CONT = 7     # contended. Payday known to +/-7 days.

POLS = ["baseline_doc", "baseline_legal", "payday_wait", "myopic",
        "solo_naive", "solo_pop", "solo_placebo", "solo_shared", "portfolio",
        "explore"]

S2_SEEDS = range(8)

# Populations as SPECS: (n, k, pop_seed, spend, days). A spec is what gets sent
# to a worker; w3.make_pop is deterministic, so the worker rebuilds the exact
# same population from five numbers instead of unpickling one.
POP_SPECS = {
    "P": (60, 5, 1, 1.05, 120),
    "P1": (60, 1, 1, 1.05, 120),
    "PROBE": (1, 1, 1, 1.05, 120),
}
POP_SPECS.update({f"Pc{r}": (120, 5, 300 + r, 1.05, 120) for r in range(3)})
POP_SPECS.update({f"S2P{r}": (100, 5, 400 + r, 1.05, 120) for r in S2_SEEDS})

FAST_GATES = ("M1", "M2", "M3", "M4", "M4B", "M5", "M6", "M8",
              "T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9",
              "S1", "S1_PD")
FULL_ONLY = ("S2a", "S2b", "S2c", "S2_LEGACY", "S3", "S4")

_POPS = {}
_CACHE = {}
_MISSES = []


def get_pop(key):
    if key not in _POPS:
        _POPS[key] = runner.build_pop(POP_SPECS[key])
    return _POPS[key]


def norm(kw):
    """
    Canonical kwargs for a suite run.

    `payday_err` is made explicit because harness.run defaults it to 1: without
    this, R(p, "P", 7) and R(p, "P", 7, payday_err=1) are the same run under two
    different cache keys and get executed twice.

    `collect_calib` is forced on for every run. It is numerically inert -- it
    appends the belief's own p_success at each dispatch and touches no state --
    and it is what lets T9 compare the FLOATS rather than only the decisions,
    while sharing runs T1/T7/T8 were doing anyway.
    """
    kw = dict(kw)
    kw.setdefault("payday_err", PE_FLAT)
    kw["pop_spend"] = 1.05
    kw["collect_calib"] = True
    return kw


def _hashable(v):
    # bcfg is a dict, and a dict cannot go in a cache key. Only the key needs
    # flattening -- harness.run still receives the real dict.
    return tuple(sorted(v.items())) if isinstance(v, dict) else v


def ckey(policy, pop_key, seed, kw):
    return (policy, pop_key, seed,
            tuple(sorted((k, _hashable(v)) for k, v in kw.items())))


def R(policy, pop_key, seed, **kw):
    """
    Cached harness.run. run() is deterministic in its arguments -- that is
    exactly what T4 asserts -- so reusing an identical call is safe. Normally
    the result is already there, put in by the parallel prefetch. A miss is
    executed inline and recorded, so a job the plan forgot is slow and loud
    rather than silently skipped. T4 deliberately bypasses this, since a cache
    would make it vacuous.
    """
    kw = norm(kw)
    k = ckey(policy, pop_key, seed, kw)
    if k not in _CACHE:
        _MISSES.append(k)
        _CACHE[k] = harness.run(policy, get_pop(pop_key), seed, **kw)
    return _CACHE[k]


def record(tid, name, status, detail=""):
    results.append((tid, name, status, detail))
    tag = {"PASS": "  ok  ", "FAIL": " FAIL ", "VACUOUS": "VACUOUS"}[status]
    # flush=True is not cosmetic. gate.py reads this through a pipe, so stdout
    # is block-buffered, and a hard crash (this suite has segfaulted twice with
    # 0xC0000005) loses whatever is still in the buffer. That is why the first
    # crash looked like "printed no gate lines" and was misread as crashing
    # early -- it could have died anywhere. Flushing per gate means the next
    # crash names the last gate that completed.
    print(f"[{tag}] {tid:<5} {name:<46} {detail}", flush=True)


def paired(a, b):
    """(mean difference in points, 2 SE) for b - a, paired across populations."""
    d = (np.asarray(b, dtype=float) - np.asarray(a, dtype=float)) * 100
    se = d.std(ddof=1) / np.sqrt(len(d))
    return float(d.mean()), float(2 * se)


MUTANTS = [
    ("M1", "cap", "cap", "5th attempt in a cycle", PE_CONT),
    ("M2", "peak", "peak", "dispatch inside a peak hour", PE_FLAT),
    ("M3", "lead", "lead", "<24h notification lead", PE_FLAT),
    ("M4", "pending", "pending", "second pending notification", PE_FLAT),
    ("M5", "represent", "represent", "Z9 re-presented under old notice", PE_FLAT),
]

T9_POLICIES = t9_reference.POLICIES


# ============================================================== THE JOB PLAN
def plan_jobs(tier):
    """Every harness.run the selected tier will ask for, enumerated up front."""
    jobs = []

    def J(policy, pop_key, seed, **kw):
        kw = norm(kw)
        jobs.append((ckey(policy, pop_key, seed, kw), policy,
                     POP_SPECS[pop_key], seed, kw))

    # Tier 1 mutants
    for _tid, mut, _f, _d, pe in MUTANTS:
        J("portfolio", "P", 7, payday_err=pe)
        J("portfolio", "P", 7, mutate=mut, payday_err=pe)
    J("portfolio", "P", 7)
    J("portfolio", "P", 7, mutate="leak_bal")
    J("oracle", "P", 7)
    J("oracle", "P", 7, mutate="weak_oracle")

    # T1 / T7 / T8
    for pe in (PE_FLAT, PE_CONT):
        J("oracle", "P", 7, payday_err=pe)
        for p in POLS:
            J(p, "P", 7, payday_err=pe)

    # T5
    J("portfolio", "P", 7, cap_override=2, payday_err=PE_CONT)
    J("portfolio", "P", 7, cap_override=4, payday_err=PE_CONT)

    # T6
    J("solo_pop", "P1", 7)
    J("solo_shared", "P1", 7)

    # T9
    for pe in (PE_FLAT, PE_CONT):
        for pol in T9_POLICIES:
            J(pol, "P", 7, payday_err=pe)

    # S1 (point-estimate belief) and S1_PD (the belief that ships)
    for r in range(3):
        J("portfolio", f"Pc{r}", 800 + r)
        J("solo_shared_pd", f"Pc{r}", 800 + r, payday_err=PE_CONT,
          bcfg=w3.FITTED_BELIEF)

    if tier == "full":
        for r in S2_SEEDS:
            for pol in ("solo_pop_pd", "solo_shared_pd", "solo_placebo_pd"):
                J(pol, f"S2P{r}", 950 + r, payday_err=PE_CONT)
            for pol in ("solo_shared", "solo_placebo", "solo_pop"):
                J(pol, f"S2P{r}", 950 + r)
            J("payday_wait", f"S2P{r}", 950 + r, payday_err=PE_CONT)
            J("payday_wait", f"S2P{r}", 6950 + r, payday_err=PE_CONT)
            J("oracle", f"S2P{r}", 950 + r, payday_err=PE_CONT)
            J("baseline_doc", f"S2P{r}", 950 + r, payday_err=PE_CONT)
            # S4: the fitted belief, and its mutant
            J("solo_shared_pd", f"S2P{r}", 950 + r, payday_err=PE_CONT,
              bcfg=w3.FITTED_BELIEF)
            J("solo_shared_pd", f"S2P{r}", 950 + r, payday_err=PE_CONT,
              bcfg=w3.FITTED_BELIEF, mutate="ignore_bcfg")
    return jobs


# ============================================================ TIER 1: MUTANTS
def tier1():
    print("\n--- Tier 1: mutation tests (do the gates actually fire?) ---")
    for tid, mut, field, desc, pe in MUTANTS:
        clean = R("portfolio", "P", 7, payday_err=pe)["vdetail"][field]
        dirty = R("portfolio", "P", 7, mutate=mut, payday_err=pe)["vdetail"][field]
        if clean != 0:
            record(tid, desc, FAIL, f"pe={pe}: clean run already violates ({clean})")
        elif dirty == 0:
            record(tid, desc, VACUOUS, f"pe={pe}: mutant did not trip the counter")
        else:
            record(tid, desc, PASS, f"pe={pe}: clean=0 -> mutant={dirty}")

    # M6/M7: leakage. A belief fed the true balance must change the outcome;
    # if it does not, the leakage test could never detect real leakage.
    # M7 (forecast reads the real future array) is specified in
    # 05_TEST_DESIGN.md and is still NOT implemented. T2 covers the same
    # property structurally.
    base = R("portfolio", "P", 7)["cycle_rec"]
    leak = R("portfolio", "P", 7, mutate="leak_bal")["cycle_rec"]
    record("M6", "true-balance leak changes the result",
           VACUOUS if abs(base - leak) < 1e-12 else PASS,
           f"clean={base*100:.1f}% leaked={leak*100:.1f}%")

    # M8: the defect found in the last audit, restored on purpose.
    o_ok = R("oracle", "P", 7)["cycle_rec"]
    o_bad = R("oracle", "P", 7, mutate="weak_oracle")["cycle_rec"]
    record("M8", "crippled oracle is detectably worse",
           PASS if o_bad < o_ok - 1e-9 else VACUOUS,
           f"fixed={o_ok*100:.1f}% crippled={o_bad*100:.1f}%")

    gate_m4b()


# ============================ M4B: THE MUTANT MUST NOT GRADE ITSELF
def mutant_written_counters():
    """
    {mutation name: {violation counters it increments itself}}.

    Static read of sim/harness.py: every `V.<field> += 1` that sits inside a
    branch guarded by `mutate == "<name>"`. Returns only self-writes, so an
    empty dict means every mutant creates an illegal STATE and lets the
    independent dispatch-time re-check find it.
    """
    with open(os.path.join(HERE, "harness.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    out = {}

    def mutate_names(test):
        names = set()
        for n in ast.walk(test):
            if (isinstance(n, ast.Compare) and isinstance(n.left, ast.Name)
                    and n.left.id == "mutate"
                    and len(n.ops) == 1 and isinstance(n.ops[0], ast.Eq)
                    and isinstance(n.comparators[0], ast.Constant)
                    and isinstance(n.comparators[0].value, str)):
                names.add(n.comparators[0].value)
        return names

    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        names = mutate_names(node.test)
        if not names:
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.AugAssign)
                    and isinstance(sub.op, ast.Add)
                    and isinstance(sub.target, ast.Attribute)
                    and isinstance(sub.target.value, ast.Name)
                    and sub.target.value.id == "V"):
                for nm in names:
                    out.setdefault(nm, set()).add(sub.target.attr)
    return out


def gate_m4b():
    """
    M1-M5 grade a mutant by reading vdetail[field] and requiring it to move.
    That is evidence ONLY if the mutant creates an illegal STATE and a
    different piece of code notices. If the mutation branch increments
    V.<field> itself, the gate passes by construction -- the same shape as the
    three vacuous gates in docs/03_ERRORS.md, and one level worse, because the
    mutant is the thing that is supposed to be adversarial.

    Measured 28 August 2026 on an instrumented copy of the harness:

        mutate='pending'    1066 counted, 1066 written by the mutant itself,
                               0 from the independent re-check.  M4 IS VACUOUS.
        mutate='represent'   608 counted,  304 written by the mutant itself,
                             304 from the independent re-check.  M5 double-counts
                               but does still bind.

    Why this gate is STATIC. The harness returns only the counter, so from the
    outside a self-written violation and an independently-detected one are the
    same integer. There is no behavioural probe that separates them without
    editing sim/harness.py, which is frozen. So the gate reads the source.

    FALSIFIABILITY. The detector must DISCRIMINATE, not just flag. `cap`,
    `peak` and `lead` create state and are detected independently; if this gate
    ever flags all five it is broken, not the harness, and it reports VACUOUS.

    THE FIX IS IN harness.py AND IS BLOCKED BY THE FREEZE (CLAUDE.md, tag
    `model-frozen`). Listed in sim/known_failures.txt until 5 September.
    """
    self_written = mutant_written_counters()
    fields = {tid: field for tid, _mut, field, _d, _pe in MUTANTS}
    flagged, clean = [], []
    for tid, mut, field, _desc, _pe in MUTANTS:
        if field in self_written.get(mut, set()):
            flagged.append(f"{tid}({mut}->V.{field})")
        else:
            clean.append(tid)
    if not flagged:
        record("M4B", "no mutant writes the counter it is graded on", PASS,
               f"all {len(fields)} Stage-0 mutants create state only")
    elif not clean:
        record("M4B", "no mutant writes the counter it is graded on", VACUOUS,
               "detector flagged every mutant - it cannot discriminate")
    else:
        record("M4B", "no mutant writes the counter it is graded on", FAIL,
               f"self-graded: {', '.join(flagged)}; "
               f"independent: {', '.join(clean)}")


# ======================================================= TIER 2: INVARIANTS
_PROBE_OBS = [(900, False), (900, False), (900, True), (1200, False), (1200, True)]


def belief_probe(cls):
    bb = cls(20000, 3, 30, 120)
    out = []
    for day, (amt, ok) in enumerate(_PROBE_OBS):
        bb.advance(day)
        out.append(float(bb.p_success(amt)))
        bb.observe(amt, ok)
    out.append(float(bb.expected()))
    return out


def make_leaky(probe_cust):
    class _LeakyPD(w3.BeliefPD):
        """MUTANT for T3: a belief that peeks at the world's balance trace."""

        def p_success(self, amount, P=None):
            peek = w3.balance_trace(probe_cust, np.random.default_rng(0))
            base = float(super().p_success(amount, P))
            return base if not np.isfinite(peek[0]) else min(1.0, base + 0.05)
    return _LeakyPD


def probe_pair(cls):
    clean = belief_probe(cls)
    _sv = w3.balance_trace
    w3.balance_trace = lambda c, r: np.full(c["days"] * w3.HOURS, np.nan)
    try:
        poisoned = belief_probe(cls)
    finally:
        w3.balance_trace = _sv
    return clean, poisoned


def tier2():
    print("\n--- Tier 2: correctness invariants ---")

    # T1 ORACLE DOMINANCE - the single most load-bearing test in the suite.
    # 05_TEST_DESIGN.md requires dominance "at every contention level", so it
    # runs at both. The gate is paired with the weak_oracle mutant: with the
    # oracle crippled, some policy MUST beat it, otherwise this gate is not
    # binding.
    def dominated_by(oracle_rec, pe):
        out = []
        for p in POLS:
            r = R(p, "P", 7, payday_err=pe)["cycle_rec"]
            if r > oracle_rec + 1e-9:
                out.append((p, round(r * 100, 1)))
        return out

    t1_bad, t1_orc = [], {}
    for pe in (PE_FLAT, PE_CONT):
        orc_pe = R("oracle", "P", 7, payday_err=pe)["cycle_rec"]
        t1_orc[pe] = orc_pe
        t1_bad += [(pe,) + tuple(b) for b in dominated_by(orc_pe, pe)]
    t1_mut_fires = bool(dominated_by(
        R("oracle", "P", 7, mutate="weak_oracle")["cycle_rec"], PE_FLAT))
    t1_margin = t1_orc[PE_FLAT] - max(
        R(p, "P", 7)["cycle_rec"] for p in POLS)
    if t1_bad:
        record("T1", "oracle weakly dominates every policy", FAIL,
               f"BEATEN BY {t1_bad}")
    elif not t1_mut_fires:
        record("T1", "oracle weakly dominates every policy", VACUOUS,
               "crippled oracle was still not beaten - gate does not bind")
    else:
        record("T1", "oracle weakly dominates every policy", PASS,
               f"pe1={t1_orc[PE_FLAT]*100:.1f}% pe7={t1_orc[PE_CONT]*100:.1f}%, "
               f"margin={t1_margin*100:.1f}pts, mutant caught")

    # T2 NO FUTURE LEAKAGE. Belief.forecast takes no argument carrying ground
    # truth, so leakage is structurally impossible - but assert it behaviourally
    # too, by poisoning the trace after the current day and requiring
    # bit-identity.
    b = w3.Belief(20000, 3, 30, 120)
    f1 = b.forecast(10, 12)
    _saved = w3.balance_trace
    w3.balance_trace = lambda c, r: np.full(c["days"] * w3.HOURS, np.nan)
    f2 = b.forecast(10, 12)
    w3.balance_trace = _saved
    same = all(np.array_equal(a[1], c[1]) for a, c in zip(f1, f2)) and len(f1) == len(f2)
    record("T2", "forecast is bit-identical with future poisoned",
           PASS if same else FAIL)

    # T3 NO TRUE-BALANCE BACK-CHANNEL.
    # Previously this ran solo_pop twice and compared - a determinism check that
    # T4 already performs, testing nothing about leakage despite its name.
    # Rewritten to the property 05_TEST_DESIGN.md actually specifies: a belief's
    # predictions must depend only on the observations it is given, never on the
    # world's balance array.
    probe_cust = get_pop("PROBE")[0]
    _c3, _p3 = probe_pair(w3.BeliefPD)
    t3_clean_ok = all(abs(x - y) < 1e-12 for x, y in zip(_c3, _p3))
    _cm, _pm = probe_pair(make_leaky(probe_cust))
    t3_mut_fires = any(abs(x - y) > 1e-12 for x, y in zip(_cm, _pm))
    if not t3_clean_ok:
        record("T3", "belief has no back-channel to true balance", FAIL,
               "BeliefPD predictions changed when the world was poisoned")
    elif not t3_mut_fires:
        record("T3", "belief has no back-channel to true balance", VACUOUS,
               "leaky-belief mutant was not detected")
    else:
        record("T3", "belief has no back-channel to true balance", PASS,
               "clean identical; leaky mutant caught")

    # T4 determinism. Deliberately bypasses the cache -- the whole point is to
    # run the same configuration twice for real.
    Pobj = get_pop("P")
    d1 = harness.run("portfolio", Pobj, 123, pop_spend=1.05)
    d2 = harness.run("portfolio", Pobj, 123, pop_spend=1.05)
    record("T4", "same seed -> identical output",
           PASS if d1["cycle_rec"] == d2["cycle_rec"] and d1["approval"] == d2["approval"] else FAIL)

    # T5 budget monotonicity: more attempts must not hurt. Only meaningful
    # where the cap binds, so it runs contended.
    lo = R("portfolio", "P", 7, cap_override=2, payday_err=PE_CONT)["cycle_rec"]
    hi = R("portfolio", "P", 7, cap_override=4, payday_err=PE_CONT)["cycle_rec"]
    record("T5", "raising the attempt cap does not reduce recovery",
           PASS if hi >= lo - 1e-9 else FAIL,
           f"pe=7: cap2={lo*100:.1f}% cap4={hi*100:.1f}%")

    # T6 with k=1 there is nothing to pool: shared must equal own, exactly
    a = R("solo_pop", "P1", 7)
    b_ = R("solo_shared", "P1", 7)
    record("T6", "k=1: pooled == own (nothing to pool)",
           PASS if abs(a["cycle_rec"] - b_["cycle_rec"]) < 1e-12 else FAIL,
           f"{a['cycle_rec']*100:.3f}% vs {b_['cycle_rec']*100:.3f}%")

    # T7 HARD BOUNDS + PER-EVENT CAP.
    # The cap clause used to compare att_per_cycle -- a MEAN over all cycles --
    # against the cap. A mean cannot exceed 4 unless the breach is
    # population-wide, so a single mandate taking a 5th attempt was invisible to
    # it. It now reads vdetail["cap"], which counts every individual dispatch
    # that was a (cap+1)th or later attempt within one mandate-cycle.
    # NOTE: the conservation identity 05_TEST_DESIGN.md specifies
    # (recovered + dead + unresolved + lapsed == 1.0 by count) is still NOT
    # implemented. harness.run does not return the counts it needs. Do not read
    # T7 as covering it.
    def t7_complaints(rows):
        bad = []
        for p, r in rows:
            if not (0.0 <= r["cycle_rec"] <= 1.0 + 1e-12):
                bad.append(f"{p} rec out of range")
            if not (0.0 <= r["approval"] <= 1.0 + 1e-12):
                bad.append(f"{p} approval out of range")
            if not (0.0 <= r["survival"] <= 1.0 + 1e-12):
                bad.append(f"{p} survival out of range")
            if r["vdetail"]["cap"] != 0:
                bad.append(f"{p} cap violations={r['vdetail']['cap']}")
        return bad

    t7_rows = [(p, R(p, "P", 7, payday_err=PE_CONT)) for p in POLS + ["oracle"]]
    t7_real = t7_complaints(t7_rows)
    _poison = dict(t7_rows[0][1])
    _poison["cycle_rec"] = 1.5
    _poison["vdetail"] = dict(_poison["vdetail"], cap=1)
    t7_mut = t7_complaints([("POISONED", _poison)])
    if t7_real:
        record("T7", "bounds hold; no mandate exceeds the cap", FAIL, "; ".join(t7_real))
    elif len(t7_mut) < 2:
        record("T7", "bounds hold; no mandate exceeds the cap", VACUOUS,
               f"checker missed the poisoned result ({t7_mut})")
    else:
        record("T7", "bounds hold; no mandate exceeds the cap", PASS,
               f"pe=7: {len(t7_rows)} policies clean, poison caught ({len(t7_mut)} clauses)")

    # T8 compliant policies must be clean; baseline_doc must NOT be
    bad = [p for p in harness.COMPLIANT if p in POLS + ["oracle"]
           and R(p, "P", 7)["violations"] != 0]
    doc_v = R("baseline_doc", "P", 7)["vdetail"]["represent"]
    record("T8", "compliant policies clean; documented baseline is not",
           PASS if not bad and doc_v > 0 else FAIL,
           f"dirty={bad}, baseline_doc represent-violations={doc_v}")


# ================================================== T9: EXACT-OUTPUT LOCK
def gate_t9(workers):
    """
    Every policy's output must equal sim/t9_reference.json EXACTLY, at both
    operating points. The reference was captured before any performance work.

    Two levels of resolution, and it matters which one moves:
      - the five headline metrics are ratios of integer counts, so they detect
        a changed DECISION;
      - calib_sha256 hashes the raw float64 bytes of every predicted
        P(success) at every dispatch, so it detects a changed FLOAT.
    A change that keeps the metrics and breaks the hash perturbed the
    arithmetic without (yet) flipping a schedule. That is the signature of
    vectorising the filter.

    THE MUTANT. The defect this gate exists to catch is a parallel runner whose
    workers draw seeds from one shared RNG instead of using each run's own
    seed, which makes results depend on execution order. The mutant does
    exactly that. If it does not produce a mismatch, this gate is not
    protecting anything and reports VACUOUS.
    """
    with open(t9_reference.REF_PATH, encoding="utf-8") as fh:
        ref = json.load(fh)["runs"]

    def diff(fp, key):
        want = ref.get(key)
        if want is None:
            return [f"{key}: missing from reference"]
        return [f"{key}.{f}" for f in sorted(set(want) | set(fp))
                if want.get(f) != fp.get(f)]

    real_bad, n_hash = [], 0
    for pe in t9_reference.PAYDAY_ERRS:
        for pol in T9_POLICIES:
            key = f"{pol}|pe{pe}"
            fp = t9_reference.fingerprint(R(pol, "P", 7, payday_err=pe))
            if fp.get("calib_sha256"):
                n_hash += 1
            real_bad += diff(fp, key)

    # the mutant, run through the real parallel driver
    mut_jobs = []
    for pe in t9_reference.PAYDAY_ERRS:
        for pol in T9_POLICIES:
            kw = norm(dict(payday_err=pe))
            mut_jobs.append((f"{pol}|pe{pe}", pol, POP_SPECS["P"], 7, kw))
    mut_res = runner.run_jobs(mut_jobs, workers=workers, shared_seed_mutant=True)
    mut_bad = []
    for key, res in mut_res.items():
        mut_bad += diff(t9_reference.fingerprint(res), key)

    n_cfg = len(T9_POLICIES) * len(t9_reference.PAYDAY_ERRS)
    if real_bad:
        record("T9", "output identical to the pre-optimisation reference", FAIL,
               f"{len(real_bad)} field(s) differ: {real_bad[:6]}")
    elif not mut_bad:
        record("T9", "output identical to the pre-optimisation reference", VACUOUS,
               "shared-RNG mutant produced the reference output - gate does not bind")
    else:
        record("T9", "output identical to the pre-optimisation reference", PASS,
               f"{n_cfg} configs exact ({n_hash} float-level hashes); "
               f"shared-RNG mutant caught ({len(mut_bad)} fields)")


# ============================================ TIER 3: STATISTICAL VALIDITY
def gate_s1():
    # S1 BELIEF CALIBRATION. Never previously tested. The whole project rests
    # on P(success) meaning what it says.
    cal = []
    for r in range(3):
        cal += R("portfolio", f"Pc{r}", 800 + r)["calib"]
    ece, mono = reliability(cal, "w3.Belief via portfolio (POINT-ESTIMATE "
                                 "payday -- NOT the shipping filter)")
    record("S1", "belief calibration: ECE<0.10 and monotone",
           PASS if (ece < 0.10 and mono) else FAIL,
           f"ECE={ece:.3f}, monotone={mono}")


def reliability(cal, label, extra=""):
    """S1's binning and S1's threshold, factored out so S1_PD cannot drift
    from it. ECE < 0.10 AND monotone, both declared in 05_TEST_DESIGN.md
    before any result was seen."""
    cal = np.array(cal, dtype=float)
    edges = np.linspace(0, 1, 11)
    rows, ece, ntot = [], 0.0, len(cal)
    for i in range(10):
        sel = cal[(cal[:, 0] >= edges[i])
                  & (cal[:, 0] < edges[i + 1] + (1e-9 if i == 9 else 0))]
        if len(sel) < 20:
            continue
        pred, emp = sel[:, 0].mean(), sel[:, 1].mean()
        rows.append((edges[i], edges[i + 1], len(sel), pred, emp))
        ece += len(sel) / ntot * abs(pred - emp)
    print(f"       reliability, {label} (n={ntot}){extra}:")
    for lo_, hi_, n_, pr, em in rows:
        print(f"         P in [{lo_:.1f},{hi_:.1f})  n={n_:>6}  predicted={pr:.3f}"
              f"  actual={em:.3f}  {'over' if pr > em else 'under'}confident "
              f"by {abs(pr-em):.3f}")
    mono = all(rows[i][4] <= rows[i + 1][4] + 0.02 for i in range(len(rows) - 1))
    return ece, mono


def gate_s1_pd():
    """
    S1_PD -- THE SAME GATE, ON THE FILTER THAT ACTUALLY SHIPS.

    S1 runs `portfolio`, which does not end in "_pd" and therefore carries
    w3.Belief: the POINT-ESTIMATE payday filter. The policy this project
    recommends is `solo_shared_pd`, which carries w3.BeliefPD. So for the whole
    life of the project the calibration gate has been measuring a filter that
    is not the product, and the conclusions drawn from S1 -- including one I
    drew myself earlier in this session, that "S1 says the shipping filter's
    probabilities are wrong" -- were about the wrong object.

    S1 is left exactly as it is. It is a pre-registered gate with a threshold
    declared before results, and quietly repointing it at another policy would
    be indistinguishable from moving a test until it says something else.
    S1_PD is an ADDITION, with the identical threshold, on the real filter.
    """
    cal = []
    for r in range(3):
        cal += R("solo_shared_pd", f"Pc{r}", 800 + r, payday_err=PE_CONT,
                 bcfg=w3.FITTED_BELIEF)["calib"]
    ece, mono = reliability(cal, "BeliefPD, fitted config (the shipping filter)",
                            "  <-- S1_PD")
    record("S1_PD", "SHIPPING belief calibration: ECE<0.10 and monotone",
           PASS if (ece < 0.10 and mono) else FAIL,
           f"ECE={ece:.3f}, monotone={mono}")


def tier3_stats():
    # ------------------------------------------------------------- S2 ARMS
    # Three arms on the PAYDAY-POSTERIOR policies, contended. The old
    # point-estimate S2 is kept below as S2_LEGACY; it is not deleted, because
    # keeping the retired gate visible is what distinguishes this rewrite from
    # quietly loosening a test that was failing.
    own_pd, real_pd, plac_pd = [], [], []
    for r in S2_SEEDS:
        kw = dict(payday_err=PE_CONT)
        own_pd.append(R("solo_pop_pd", f"S2P{r}", 950 + r, **kw)["cycle_rec"])
        real_pd.append(R("solo_shared_pd", f"S2P{r}", 950 + r, **kw)["cycle_rec"])
        plac_pd.append(R("solo_placebo_pd", f"S2P{r}", 950 + r, **kw)["cycle_rec"])

    m_a, e_a = paired(own_pd, real_pd)     # the moat
    m_b, e_b = paired(own_pd, plac_pd)     # the confound check
    m_c, e_c = paired(plac_pd, real_pd)    # the doc's headline
    print(f"       pe=7, n=100, {len(S2_SEEDS)} populations, payday-posterior policies")
    print(f"         own      solo_pop_pd      {np.mean(own_pd)*100:6.2f}%")
    print(f"         real     solo_shared_pd   {np.mean(real_pd)*100:6.2f}%")
    print(f"         placebo  solo_placebo_pd  {np.mean(plac_pd)*100:6.2f}%")

    record("S2a", "moat: shared_pd beats own_pd (>2SE)",
           PASS if m_a > e_a else FAIL, f"{m_a:+.2f} pts (+/-{e_a:.2f})")

    # S2b is the one that decides whether S2c means anything. If the placebo is
    # not neutral against own, then it is not a clean control: it injects
    # actively wrong observations rather than merely extra ones, and the
    # real-minus-placebo headline is measuring placebo damage as much as
    # pooling benefit.
    record("S2b", "confound: placebo_pd is neutral vs own_pd",
           PASS if abs(m_b) < e_b else FAIL,
           f"{m_b:+.2f} pts (+/-{e_b:.2f})" + ("" if abs(m_b) < e_b else "  <-- NOT neutral"))

    record("S2c", "headline: shared_pd beats placebo_pd (>2SE)",
           PASS if m_c > e_c else FAIL, f"{m_c:+.2f} pts (+/-{e_c:.2f})")

    # S2_LEGACY: the original point-estimate gate, unchanged, at its original
    # operating point. RETIRED ARCHITECTURE. Kept as a record, not as evidence.
    real, plac, own = [], [], []
    for r in S2_SEEDS:
        real.append(R("solo_shared", f"S2P{r}", 950 + r)["cycle_rec"])
        plac.append(R("solo_placebo", f"S2P{r}", 950 + r)["cycle_rec"])
        own.append(R("solo_pop", f"S2P{r}", 950 + r)["cycle_rec"])
    m_l, e_l = paired(plac, real)
    print(f"       LEGACY (point-estimate payday, pe=1): "
          f"real vs own {paired(own, real)[0]:+.2f}, placebo vs own {paired(own, plac)[0]:+.2f}")
    record("S2_LEGACY", "retired: point-estimate pooling vs placebo",
           PASS if m_l > e_l else FAIL, f"{m_l:+.2f} pts (+/-{e_l:.2f})")

    # ------------------------------------------------------------------ S4
    # THE DECISION NUMBER. Which probability engine the agent ships with.
    #
    # `solo_shared_pd` carried three hand-set values that had never been
    # checked -- a stride-3 payday grid, an invented exp(-0.10 d) prior, and a
    # hand-derived cross-mandate spend correction. Fitting them on TRAINING
    # populations (600-607, the same customers the ML baseline was allowed to
    # fit itself to) and reporting here on the S2 populations (400-407, which
    # the fit never saw) is what makes the Bayes-versus-ML comparison
    # like-for-like. Before this, a fitted model was being compared against an
    # unfitted one and unsurprisingly won.
    #
    # MUTANT: `ignore_bcfg` drops the fitted configuration on the floor, which
    # is exactly what a broken plumbing change would do. The mutant arm then
    # reproduces the shipped filter bit-for-bit, the measured gain collapses to
    # zero, and this gate goes red. Verified: it does.
    ship, fair, mut = [], [], []
    for r in S2_SEEDS:
        kw = dict(payday_err=PE_CONT)
        ship.append(R("solo_shared_pd", f"S2P{r}", 950 + r, **kw)["cycle_rec"])
        fair.append(R("solo_shared_pd", f"S2P{r}", 950 + r,
                      bcfg=w3.FITTED_BELIEF, **kw)["cycle_rec"])
        mut.append(R("solo_shared_pd", f"S2P{r}", 950 + r,
                     bcfg=w3.FITTED_BELIEF, mutate="ignore_bcfg",
                     **kw)["cycle_rec"])
    s4_m, s4_e = paired(ship, fair)
    s4_mm, s4_me = paired(ship, mut)
    print(f"       shipped belief   {np.mean(ship)*100:6.2f}%")
    print(f"       fitted  belief   {np.mean(fair)*100:6.2f}%   "
          f"{w3.FITTED_BELIEF}")
    if not (s4_m > s4_e):
        record("S4", "fitted belief beats the shipped one (>2SE)", FAIL,
               f"{s4_m:+.2f} pts (+/-{s4_e:.2f})")
    elif s4_mm > s4_me:
        record("S4", "fitted belief beats the shipped one (>2SE)", VACUOUS,
               f"ignore_bcfg mutant still 'won' ({s4_mm:+.2f}) - gate does not bind")
    else:
        record("S4", "fitted belief beats the shipped one (>2SE)", PASS,
               f"{s4_m:+.2f} pts (+/-{s4_e:.2f}); mutant collapses to "
               f"{s4_mm:+.2f}")

    # S3 SEED STABILITY / SIGNIFICANCE MACHINERY.
    s3_pw, s3_pw_alt, s3_orc, s3_doc = [], [], [], []
    for r in S2_SEEDS:
        kw = dict(payday_err=PE_CONT)
        s3_pw.append(R("payday_wait", f"S2P{r}", 950 + r, **kw)["cycle_rec"])
        s3_pw_alt.append(R("payday_wait", f"S2P{r}", 6950 + r, **kw)["cycle_rec"])
        s3_orc.append(R("oracle", f"S2P{r}", 950 + r, **kw)["cycle_rec"])
        s3_doc.append(R("baseline_doc", f"S2P{r}", 950 + r, **kw)["cycle_rec"])

    h_m, h_e = paired(s3_pw, real_pd)        # headline: system vs the heuristic
    p_m, p_e = paired(s3_doc, s3_orc)        # positive control
    n_m, n_e = paired(s3_pw, s3_pw_alt)      # null control
    n_pops = len(S2_SEEDS)
    print(f"       headline  solo_shared_pd - payday_wait : {h_m:+.2f} pts (+/-{h_e:.2f})"
          f"  {'SIG' if abs(h_m) > h_e else 'NOT SIG'}")
    print(f"       pos ctrl  oracle - baseline_doc        : {p_m:+.2f} pts (+/-{p_e:.2f})")
    print(f"       null ctrl payday_wait - itself, reseed : {n_m:+.2f} pts (+/-{n_e:.2f})")
    s3_pos_ok = p_m > p_e
    s3_null_ok = abs(n_m) < n_e
    if n_pops < 8:
        record("S3", "significance machinery (>=8 populations)", FAIL,
               f"only {n_pops} populations")
    elif not s3_pos_ok:
        record("S3", "significance machinery (>=8 populations)", VACUOUS,
               f"positive control not significant ({p_m:+.2f}+/-{p_e:.2f})")
    elif not s3_null_ok:
        record("S3", "significance machinery (>=8 populations)", FAIL,
               f"null control came back significant ({n_m:+.2f}+/-{n_e:.2f})")
    else:
        record("S3", "significance machinery (>=8 populations)", PASS,
               f"n={n_pops}; headline {h_m:+.2f}+/-{h_e:.2f} "
               f"{'SIG' if abs(h_m) > h_e else 'NOT SIG'}")


# ============================================================ ENTRY POINT
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=("fast", "full"), default="full",
                    help="fast: code-correctness gates only. full: everything.")
    ap.add_argument("--workers", type=int, default=None,
                    help="parallel worker processes (default: min(jobs, cores, 32))")
    args = ap.parse_args(argv)

    t0 = time.perf_counter()
    jobs = plan_jobs(args.tier)
    print(f"TIER={args.tier}   planned runs={len(jobs)}   "
          f"workers={args.workers or 'auto'}")
    _CACHE.update(runner.run_jobs(jobs, workers=args.workers))
    print(f"prefetch done in {time.perf_counter()-t0:.1f}s")

    tier1()
    tier2()
    gate_t9(args.workers)
    print("\n--- Tier 3: statistical validity ---")
    gate_s1()
    gate_s1_pd()
    if args.tier == "full":
        tier3_stats()
    else:
        print("       S2a / S2b / S2c / S2_LEGACY / S3 NOT RUN in the fast tier.")
        print("       They need 8 populations at n=100 to have power and are")
        print("       never shrunk to fit a time budget. Run --tier full.")

    print("\n" + "=" * 78)
    nf = sum(1 for _, _, s, _ in results if s == FAIL)
    nv = sum(1 for _, _, s, _ in results if s == VACUOUS)
    print(f"SUITE: {len(results)} gates, {nf} FAIL, {nv} VACUOUS, "
          f"{len(results)-nf-nv} pass   [tier={args.tier}]")
    if _MISSES:
        print(f"NOTE: {len(_MISSES)} run(s) were not in the job plan and ran "
              f"serially. Add them to plan_jobs() to keep the suite fast.")
        for k in _MISSES[:8]:
            print(f"      miss: {k[0]} pop={k[1]} seed={k[2]} {dict(k[3])}")
    print(f"wall time: {time.perf_counter()-t0:.1f}s")
    print("=" * 78)


if __name__ == "__main__":
    main()
