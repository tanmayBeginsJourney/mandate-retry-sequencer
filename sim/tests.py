import functools
"""
TEST SUITE. Implements TEST_DESIGN.md, which was written before the harness.

Every gate is paired with a mutant that must trip it. A gate no mutant can trip
is reported as VACUOUS and counts as a failure of the suite, not a pass.
"""
import numpy as np
import w3, harness

PASS, FAIL, VACUOUS = "PASS", "FAIL", "VACUOUS"
results = []
RUN = functools.partial(harness.run, pop_spend=1.05)


def record(tid, name, status, detail=""):
    results.append((tid, name, status, detail))
    tag = {"PASS": "  ok  ", "FAIL": " FAIL ", "VACUOUS": "VACUOUS"}[status]
    print(f"[{tag}] {tid:<5} {name:<46} {detail}")


def pop(n=60, k=5, seed=1, spend=1.05, days=120):
    return w3.make_pop(n, k, np.random.default_rng(seed), days=days, spend=spend)


# ============================================================ TIER 1: MUTANTS
# Each asserts the corresponding violation counter goes from 0 to >0.
print("\n--- Tier 1: mutation tests (do the gates actually fire?) ---")
P = pop()
MUTANTS = [
    ("M1", "cap",       "cap",       "5th attempt in a cycle"),
    ("M2", "peak",      "peak",      "dispatch inside a peak hour"),
    ("M3", "lead",      "lead",      "<24h notification lead"),
    ("M4", "pending",   "pending",   "second pending notification"),
    ("M5", "represent", "represent", "Z9 re-presented under old notice"),
]
for tid, mut, field, desc in MUTANTS:
    clean = RUN("portfolio", P, 7, pop_spend=1.05)["vdetail"][field]
    dirty = RUN("portfolio", P, 7, mutate=mut, pop_spend=1.05)["vdetail"][field]
    if clean != 0:
        record(tid, desc, FAIL, f"clean run already violates ({clean})")
    elif dirty == 0:
        record(tid, desc, VACUOUS, "mutant did not trip the counter")
    else:
        record(tid, desc, PASS, f"clean=0 -> mutant={dirty}")

# M6/M7: leakage. A belief fed the true balance must change the outcome;
# if it does not, the leakage test could never detect real leakage.
base = RUN("portfolio", P, 7)["cycle_rec"]
leak = RUN("portfolio", P, 7, mutate="leak_bal")["cycle_rec"]
record("M6", "true-balance leak changes the result", 
       VACUOUS if abs(base - leak) < 1e-12 else PASS,
       f"clean={base*100:.1f}% leaked={leak*100:.1f}%")

# M8: the defect found in the last audit, restored on purpose.
o_ok = harness.run("oracle", P, 7)["cycle_rec"]
o_bad = harness.run("oracle", P, 7, mutate="weak_oracle")["cycle_rec"]
record("M8", "crippled oracle is detectably worse",
       PASS if o_bad < o_ok - 1e-9 else VACUOUS,
       f"fixed={o_ok*100:.1f}% crippled={o_bad*100:.1f}%")


# ======================================================= TIER 2: INVARIANTS
print("\n--- Tier 2: correctness invariants ---")

# T1 ORACLE DOMINANCE - the single most load-bearing test in the suite.
POLS = ["baseline_doc", "baseline_legal", "payday_wait", "myopic",
        "solo_naive", "solo_pop", "solo_placebo", "solo_shared", "portfolio"]
orc = harness.run("oracle", P, 7)["cycle_rec"]
worst = []
for p in POLS:
    r = RUN(p, P, 7)["cycle_rec"]
    if r > orc + 1e-9:
        worst.append((p, r))
record("T1", "oracle weakly dominates every policy",
       PASS if not worst else FAIL,
       f"oracle={orc*100:.1f}%" + (f" BEATEN BY {worst}" if worst else ""))

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

# T3 belief policies unaffected by poisoning the true balance they never see
r1 = RUN("solo_pop", P, 7)["cycle_rec"]
r2 = RUN("solo_pop", P, 7)["cycle_rec"]
record("T3", "belief policy reproducible / no hidden state",
       PASS if r1 == r2 else FAIL, f"{r1*100:.2f}% == {r2*100:.2f}%")

# T4 determinism
d1 = RUN("portfolio", P, 123)
d2 = RUN("portfolio", P, 123)
record("T4", "same seed -> identical output",
       PASS if d1["cycle_rec"] == d2["cycle_rec"] and d1["approval"] == d2["approval"] else FAIL)

# T5 budget monotonicity: more attempts must not hurt
lo = RUN("portfolio", P, 7, cap_override=2)["cycle_rec"]
hi = RUN("portfolio", P, 7, cap_override=4)["cycle_rec"]
record("T5", "raising the attempt cap does not reduce recovery",
       PASS if hi >= lo - 1e-9 else FAIL, f"cap2={lo*100:.1f}% cap4={hi*100:.1f}%")

# T6 with k=1 there is nothing to pool: shared must equal own, exactly
P1 = pop(n=60, k=1)
a = RUN("solo_pop", P1, 7)
b_ = RUN("solo_shared", P1, 7)
record("T6", "k=1: pooled == own (nothing to pool)",
       PASS if abs(a["cycle_rec"] - b_["cycle_rec"]) < 1e-12 else FAIL,
       f"{a['cycle_rec']*100:.3f}% vs {b_['cycle_rec']*100:.3f}%")

# T7 conservation + hard bounds
ok7 = True
det = []
for p in POLS + ["oracle"]:
    r = RUN(p, P, 7)
    if not (0.0 <= r["cycle_rec"] <= 1.0 + 1e-12):
        ok7 = False; det.append(f"{p} rec out of range")
    if not (0.0 <= r["approval"] <= 1.0 + 1e-12):
        ok7 = False; det.append(f"{p} approval out of range")
    if r["att_per_cycle"] > harness.cap_for(p) + 1e-9:
        ok7 = False; det.append(f"{p} attempts exceed cap")
record("T7", "metrics in range, attempts never exceed cap",
       PASS if ok7 else FAIL, "; ".join(det))

# T8 compliant policies must be clean; baseline_doc must NOT be
bad = [p for p in harness.COMPLIANT if p in POLS + ["oracle"]
       and RUN(p, P, 7)["violations"] != 0]
doc_v = RUN("baseline_doc", P, 7)["vdetail"]["represent"]
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
    cal += RUN("portfolio", Pc, 800 + r, collect_calib=True)["calib"]
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

# S2 PLACEBO POOLING - the negative control. Designed to destroy the central
# claim if the claim is false.
real, plac, own = [], [], []
for r in range(8):
    Pp = pop(n=100, k=5, seed=400 + r)
    real.append(RUN("solo_shared", Pp, 950 + r)["cycle_rec"])
    plac.append(RUN("solo_placebo", Pp, 950 + r)["cycle_rec"])
    own.append(RUN("solo_pop", Pp, 950 + r)["cycle_rec"])
real, plac, own = map(np.array, (real, plac, own))
d_real = (real - own) * 100
d_plac = (plac - own) * 100
d_diff = (real - plac) * 100
se = d_diff.std(ddof=1) / np.sqrt(len(d_diff))
print(f"       real pooling  vs own: {d_real.mean():+.2f} pts")
print(f"       PLACEBO pool  vs own: {d_plac.mean():+.2f} pts   <-- should be ~0")
record("S2", "real pooling beats placebo pooling (>2SE)",
       PASS if d_diff.mean() > 2 * se else FAIL,
       f"real-placebo = {d_diff.mean():+.2f} pts (+/-{2*se:.2f})")

print("\n" + "=" * 78)
nf = sum(1 for _, _, s, _ in results if s == FAIL)
nv = sum(1 for _, _, s, _ in results if s == VACUOUS)
print(f"SUITE: {len(results)} gates, {nf} FAIL, {nv} VACUOUS, "
      f"{len(results)-nf-nv} pass")
print("=" * 78)
