"""
TEST SUITE. Implements TEST_DESIGN.md, which was written before the harness.

Every gate is paired with a mutant that must trip it. A gate no mutant can trip
is reported as VACUOUS and counts as a failure of the suite, not a pass.

Operating point matters. Gates that only bind under contention (M1, S2, T5, T7)
run at payday_err=7. At the harness default of +/-1 day the world is
uncontended -- policies hit payday nearly every time, recovery is ~97%, and a
constraint that is never reached cannot be tested. Everything else stays at the
default. See NOTES.md, 27 August 2026.
"""
import numpy as np
import w3, harness

PASS, FAIL, VACUOUS = "PASS", "FAIL", "VACUOUS"
results = []

PE_FLAT = 1     # harness default. Payday known to +/-1 day. Uncontended.
PE_CONT = 7     # contended. Payday known to +/-7 days.

_CACHE = {}


def R(policy, pop, pop_key, seed, **kw):
    """
    Cached harness.run. run() is deterministic in its arguments -- that is
    exactly what T4 asserts -- so reusing an identical call is safe, and it
    stops T1, T7 and T8 from re-running the same configurations three times.
    T4 itself deliberately bypasses this, since a cache would make it vacuous.
    """
    key = (policy, pop_key, seed, tuple(sorted(kw.items())))
    if key not in _CACHE:
        _CACHE[key] = harness.run(policy, pop, seed, pop_spend=1.05, **kw)
    return _CACHE[key]


def record(tid, name, status, detail=""):
    results.append((tid, name, status, detail))
    tag = {"PASS": "  ok  ", "FAIL": " FAIL ", "VACUOUS": "VACUOUS"}[status]
    print(f"[{tag}] {tid:<5} {name:<46} {detail}")


def pop(n=60, k=5, seed=1, spend=1.05, days=120):
    return w3.make_pop(n, k, np.random.default_rng(seed), days=days, spend=spend)


def paired(a, b):
    """(mean difference in points, 2 SE) for b - a, paired across populations."""
    d = (np.asarray(b, dtype=float) - np.asarray(a, dtype=float)) * 100
    se = d.std(ddof=1) / np.sqrt(len(d))
    return float(d.mean()), float(2 * se)


# ============================================================ TIER 1: MUTANTS
# Each asserts the corresponding violation counter goes from 0 to >0.
print("\n--- Tier 1: mutation tests (do the gates actually fire?) ---")
P = pop()
MUTANTS = [
    ("M1", "cap",       "cap",       "5th attempt in a cycle",           PE_CONT),
    ("M2", "peak",      "peak",      "dispatch inside a peak hour",      PE_FLAT),
    ("M3", "lead",      "lead",      "<24h notification lead",           PE_FLAT),
    ("M4", "pending",   "pending",   "second pending notification",      PE_FLAT),
    ("M5", "represent", "represent", "Z9 re-presented under old notice",  PE_FLAT),
]
for tid, mut, field, desc, pe in MUTANTS:
    clean = R("portfolio", P, "P", 7, payday_err=pe)["vdetail"][field]
    dirty = R("portfolio", P, "P", 7, mutate=mut, payday_err=pe)["vdetail"][field]
    if clean != 0:
        record(tid, desc, FAIL, f"pe={pe}: clean run already violates ({clean})")
    elif dirty == 0:
        record(tid, desc, VACUOUS, f"pe={pe}: mutant did not trip the counter")
    else:
        record(tid, desc, PASS, f"pe={pe}: clean=0 -> mutant={dirty}")

# M6/M7: leakage. A belief fed the true balance must change the outcome;
# if it does not, the leakage test could never detect real leakage.
# M7 (forecast reads the real future array) is specified in 05_TEST_DESIGN.md
# and is still NOT implemented. T2 covers the same property structurally.
base = R("portfolio", P, "P", 7)["cycle_rec"]
leak = R("portfolio", P, "P", 7, mutate="leak_bal")["cycle_rec"]
record("M6", "true-balance leak changes the result",
       VACUOUS if abs(base - leak) < 1e-12 else PASS,
       f"clean={base*100:.1f}% leaked={leak*100:.1f}%")

# M8: the defect found in the last audit, restored on purpose.
o_ok = R("oracle", P, "P", 7)["cycle_rec"]
o_bad = R("oracle", P, "P", 7, mutate="weak_oracle")["cycle_rec"]
record("M8", "crippled oracle is detectably worse",
       PASS if o_bad < o_ok - 1e-9 else VACUOUS,
       f"fixed={o_ok*100:.1f}% crippled={o_bad*100:.1f}%")


# ======================================================= TIER 2: INVARIANTS
print("\n--- Tier 2: correctness invariants ---")

POLS = ["baseline_doc", "baseline_legal", "payday_wait", "myopic",
        "solo_naive", "solo_pop", "solo_placebo", "solo_shared", "portfolio"]


# T1 ORACLE DOMINANCE - the single most load-bearing test in the suite.
# 05_TEST_DESIGN.md requires dominance "at every contention level", so it runs
# at both. The gate is paired with the weak_oracle mutant: with the oracle
# crippled, some policy MUST beat it, otherwise this gate is not binding.
def dominated_by(oracle_rec, pe):
    out = []
    for p in POLS:
        r = R(p, P, "P", 7, payday_err=pe)["cycle_rec"]
        if r > oracle_rec + 1e-9:
            out.append((p, round(r * 100, 1)))
    return out


t1_bad, t1_orc = [], {}
for pe in (PE_FLAT, PE_CONT):
    orc_pe = R("oracle", P, "P", 7, payday_err=pe)["cycle_rec"]
    t1_orc[pe] = orc_pe
    t1_bad += [(pe,) + tuple(b) for b in dominated_by(orc_pe, pe)]
# the mutant: a crippled oracle must be caught by this same predicate
t1_mut_fires = bool(dominated_by(
    R("oracle", P, "P", 7, mutate="weak_oracle")["cycle_rec"], PE_FLAT))
t1_margin = t1_orc[PE_FLAT] - max(
    R(p, P, "P", 7)["cycle_rec"] for p in POLS)
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
# too, by poisoning the trace after the current day and requiring bit-identity.
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
# world's balance array. Drive a belief through a fixed observation sequence
# with the world's balance trace intact, then again with it poisoned to NaN.
# A belief with a back-channel to the world diverges; a clean one cannot.
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


_probe_cust = pop(n=1, k=1)[0]


class _LeakyPD(w3.BeliefPD):
    """MUTANT for T3: a belief that peeks at the world's balance trace."""

    def p_success(self, amount, P=None):
        peek = w3.balance_trace(_probe_cust, np.random.default_rng(0))
        base = float(super().p_success(amount, P))
        return base if not np.isfinite(peek[0]) else min(1.0, base + 0.05)


def probe_pair(cls):
    clean = belief_probe(cls)
    _sv = w3.balance_trace
    w3.balance_trace = lambda c, r: np.full(c["days"] * w3.HOURS, np.nan)
    try:
        poisoned = belief_probe(cls)
    finally:
        w3.balance_trace = _sv
    return clean, poisoned


_c3, _p3 = probe_pair(w3.BeliefPD)
t3_clean_ok = all(abs(x - y) < 1e-12 for x, y in zip(_c3, _p3))
_cm, _pm = probe_pair(_LeakyPD)
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

# T4 determinism. Deliberately bypasses the cache -- the whole point is to run
# the same configuration twice for real.
d1 = harness.run("portfolio", P, 123, pop_spend=1.05)
d2 = harness.run("portfolio", P, 123, pop_spend=1.05)
record("T4", "same seed -> identical output",
       PASS if d1["cycle_rec"] == d2["cycle_rec"] and d1["approval"] == d2["approval"] else FAIL)

# T5 budget monotonicity: more attempts must not hurt. Only meaningful where
# the cap binds, so it runs contended.
lo = R("portfolio", P, "P", 7, cap_override=2, payday_err=PE_CONT)["cycle_rec"]
hi = R("portfolio", P, "P", 7, cap_override=4, payday_err=PE_CONT)["cycle_rec"]
record("T5", "raising the attempt cap does not reduce recovery",
       PASS if hi >= lo - 1e-9 else FAIL,
       f"pe=7: cap2={lo*100:.1f}% cap4={hi*100:.1f}%")

# T6 with k=1 there is nothing to pool: shared must equal own, exactly
P1 = pop(n=60, k=1)
a = R("solo_pop", P1, "P1", 7)
b_ = R("solo_shared", P1, "P1", 7)
record("T6", "k=1: pooled == own (nothing to pool)",
       PASS if abs(a["cycle_rec"] - b_["cycle_rec"]) < 1e-12 else FAIL,
       f"{a['cycle_rec']*100:.3f}% vs {b_['cycle_rec']*100:.3f}%")


# T7 HARD BOUNDS + PER-EVENT CAP.
# The cap clause used to compare att_per_cycle -- a MEAN over all cycles --
# against the cap. A mean cannot exceed 4 unless the breach is population-wide,
# so a single mandate taking a 5th attempt was invisible to it. It now reads
# vdetail["cap"], which counts every individual dispatch that was a (cap+1)th
# or later attempt within one mandate-cycle.
# NOTE: the conservation identity 05_TEST_DESIGN.md specifies
# (recovered + dead + unresolved + lapsed == 1.0 by count) is still NOT
# implemented. harness.run does not return the counts it needs, and adding
# them is a harness change held for a later pass. Do not read T7 as covering it.
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


t7_rows = [(p, R(p, P, "P", 7, payday_err=PE_CONT)) for p in POLS + ["oracle"]]
t7_real = t7_complaints(t7_rows)
# mutant: a result that breaches two clauses must be caught by the checker.
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
       and R(p, P, "P", 7)["violations"] != 0]
doc_v = R("baseline_doc", P, "P", 7)["vdetail"]["represent"]
record("T8", "compliant policies clean; documented baseline is not",
       PASS if not bad and doc_v > 0 else FAIL,
       f"dirty={bad}, baseline_doc represent-violations={doc_v}")


# ============================================ TIER 3: STATISTICAL VALIDITY
print("\n--- Tier 3: statistical validity ---")

# S1 BELIEF CALIBRATION. Never previously tested. The whole project rests on
# P(success) meaning what it says.
cal = []
for r in range(3):
    Pc = pop(n=120, k=5, seed=300 + r)
    cal += R("portfolio", Pc, f"Pc{r}", 800 + r, collect_calib=True)["calib"]
cal = np.array(cal, dtype=float)
edges = np.linspace(0, 1, 11)
rows, ece, ntot = [], 0.0, len(cal)
for i in range(10):
    sel = cal[(cal[:, 0] >= edges[i]) & (cal[:, 0] < edges[i + 1] + (1e-9 if i == 9 else 0))]
    if len(sel) < 20:
        continue
    pred, emp = sel[:, 0].mean(), sel[:, 1].mean()
    rows.append((edges[i], edges[i + 1], len(sel), pred, emp))
    ece += len(sel) / ntot * abs(pred - emp)
print(f"       reliability (n={ntot}):")
for lo_, hi_, n_, pr, em in rows:
    print(f"         P in [{lo_:.1f},{hi_:.1f})  n={n_:>6}  predicted={pr:.3f}  actual={em:.3f}"
          f"  {'over' if pr > em else 'under'}confident by {abs(pr-em):.3f}")
mono = all(rows[i][4] <= rows[i + 1][4] + 0.02 for i in range(len(rows) - 1))
record("S1", "belief calibration: ECE<0.10 and monotone",
       PASS if (ece < 0.10 and mono) else FAIL,
       f"ECE={ece:.3f}, monotone={mono}")


# ------------------------------------------------------------------ S2 ARMS
# Three arms on the PAYDAY-POSTERIOR policies, contended. The old
# point-estimate S2 is kept below as S2_LEGACY; it is not deleted, because
# keeping the retired gate visible is what distinguishes this rewrite from
# quietly loosening a test that was failing.
S2_SEEDS = range(8)
S2POPS = {r: pop(n=100, k=5, seed=400 + r) for r in S2_SEEDS}
own_pd, real_pd, plac_pd = [], [], []
for r in S2_SEEDS:
    kw = dict(payday_err=PE_CONT)
    own_pd.append(R("solo_pop_pd", S2POPS[r], f"S2P{r}", 950 + r, **kw)["cycle_rec"])
    real_pd.append(R("solo_shared_pd", S2POPS[r], f"S2P{r}", 950 + r, **kw)["cycle_rec"])
    plac_pd.append(R("solo_placebo_pd", S2POPS[r], f"S2P{r}", 950 + r, **kw)["cycle_rec"])

m_a, e_a = paired(own_pd, real_pd)     # the moat
m_b, e_b = paired(own_pd, plac_pd)     # the confound check
m_c, e_c = paired(plac_pd, real_pd)    # the doc's headline
print(f"       pe=7, n=100, {len(S2POPS)} populations, payday-posterior policies")
print(f"         own      solo_pop_pd      {np.mean(own_pd)*100:6.2f}%")
print(f"         real     solo_shared_pd   {np.mean(real_pd)*100:6.2f}%")
print(f"         placebo  solo_placebo_pd  {np.mean(plac_pd)*100:6.2f}%")

record("S2a", "moat: shared_pd beats own_pd (>2SE)",
       PASS if m_a > e_a else FAIL, f"{m_a:+.2f} pts (+/-{e_a:.2f})")

# S2b is the one that decides whether S2c means anything. If the placebo is
# not neutral against own, then it is not a clean control: it injects actively
# wrong observations rather than merely extra ones, and the real-minus-placebo
# headline is measuring placebo damage as much as pooling benefit.
record("S2b", "confound: placebo_pd is neutral vs own_pd",
       PASS if abs(m_b) < e_b else FAIL,
       f"{m_b:+.2f} pts (+/-{e_b:.2f})" + ("" if abs(m_b) < e_b else "  <-- NOT neutral"))

record("S2c", "headline: shared_pd beats placebo_pd (>2SE)",
       PASS if m_c > e_c else FAIL, f"{m_c:+.2f} pts (+/-{e_c:.2f})")

# S2_LEGACY: the original point-estimate gate, unchanged, at its original
# operating point. RETIRED ARCHITECTURE. Kept as a record, not as evidence.
real, plac, own = [], [], []
for r in S2_SEEDS:
    real.append(R("solo_shared", S2POPS[r], f"S2P{r}", 950 + r)["cycle_rec"])
    plac.append(R("solo_placebo", S2POPS[r], f"S2P{r}", 950 + r)["cycle_rec"])
    own.append(R("solo_pop", S2POPS[r], f"S2P{r}", 950 + r)["cycle_rec"])
m_l, e_l = paired(plac, real)
print(f"       LEGACY (point-estimate payday, pe=1): "
      f"real vs own {paired(own, real)[0]:+.2f}, placebo vs own {paired(own, plac)[0]:+.2f}")
record("S2_LEGACY", "retired: point-estimate pooling vs placebo",
       PASS if m_l > e_l else FAIL, f"{m_l:+.2f} pts (+/-{e_l:.2f})")


# S3 SEED STABILITY / SIGNIFICANCE MACHINERY.
# 05_TEST_DESIGN.md: report SEs across >=8 independent populations, and report
# differences smaller than 2 SE as non-significant "including differences we
# like". This gate tests the machinery that decides that, using two controls:
#   positive control - oracle vs baseline_doc, a difference so large that any
#                      working test must call it significant;
#   null control     - payday_wait against itself under a different run seed,
#                      where the true difference is zero but the variance is
#                      real. A machinery that calls this significant is broken.
# The headline is reported with its SE and its verdict, but is NOT gated -- a
# headline we like must be allowed to come back non-significant.
s3_pw, s3_pw_alt, s3_orc, s3_doc = [], [], [], []
for r in S2_SEEDS:
    kw = dict(payday_err=PE_CONT)
    s3_pw.append(R("payday_wait", S2POPS[r], f"S2P{r}", 950 + r, **kw)["cycle_rec"])
    s3_pw_alt.append(R("payday_wait", S2POPS[r], f"S2P{r}", 6950 + r, **kw)["cycle_rec"])
    s3_orc.append(R("oracle", S2POPS[r], f"S2P{r}", 950 + r, **kw)["cycle_rec"])
    s3_doc.append(R("baseline_doc", S2POPS[r], f"S2P{r}", 950 + r, **kw)["cycle_rec"])

h_m, h_e = paired(s3_pw, real_pd)        # headline: system vs the heuristic
p_m, p_e = paired(s3_doc, s3_orc)        # positive control
n_m, n_e = paired(s3_pw, s3_pw_alt)      # null control
n_pops = len(S2POPS)
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


print("\n" + "=" * 78)
nf = sum(1 for _, _, s, _ in results if s == FAIL)
nv = sum(1 for _, _, s, _ in results if s == VACUOUS)
print(f"SUITE: {len(results)} gates, {nf} FAIL, {nv} VACUOUS, "
      f"{len(results)-nf-nv} pass")
print("=" * 78)
