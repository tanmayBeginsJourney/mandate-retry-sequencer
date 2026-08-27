"""
Follow-up: is the 2% starvation RANDOM or SYSTEMATIC?

2% overall is harmless if it's noise. It is a real product problem if it lands
on one kind of mandate. The index is value x (p_now - p_later), so small-amount
mandates should be structurally disadvantaged. Testing that directly.

Pre-declared: we bucket mandates by amount quintile and report starvation and
recovery per bucket. If the lowest quintile is starved materially more than the
highest, the "no fairness needed" conclusion FAILS and we need the floor after all.
"""
import numpy as np
from collections import defaultdict
from sim4 import (make_pop, balance_trace, Belief, index_score,
                  DAYS, SLOTS_PER_DAY, T, NPCI_MAX)

def run(pop, seed, ltv_mult=6.0):
    rng = np.random.default_rng(seed)
    recs = []
    for c in pop:
        bal = balance_trace(c, rng)
        st = [dict(**m, n=0, done=False, dead=False, elig=False) for m in c["mandates"]]
        drained = 0.0
        est_sal = c["salary"] * rng.uniform(0.7, 1.3)
        est_pay = int(np.clip(c["payday"] + rng.integers(-1, 2), 0, DAYS - 1))
        b = Belief(est_sal, est_pay)
        for t in range(T):
            day, slot = divmod(t, SLOTS_PER_DAY)
            b.advance(day, slot)
            live = [m for m in st if not m["done"] and not m["dead"]
                    and day >= m["due_day"] and m["n"] < NPCI_MAX]
            if not live: continue
            for m in live: m["elig"] = True
            fc = b.forecast(day, slot)
            sc = []
            for m in live:
                p_now = b.p_success(m["amount"])
                p_lat = (max([b.p_success(m["amount"], pp) for pp in fc], default=0.0)*0.92
                         if NPCI_MAX - m["n"] > 1 else 0.0)
                sc.append((index_score(p_now, p_lat, m["amount"], NPCI_MAX-m["n"], ltv_mult), m))
            sc.sort(key=lambda x: -x[0])
            budget = float((b.p * b.centers).sum())
            chosen = []
            for s_, m in sc:
                if s_ <= 0: continue
                if m["amount"] <= budget:
                    chosen.append(m); budget -= m["amount"]
            rng.shuffle(chosen)
            for m in chosen:
                if m["n"] >= NPCI_MAX: continue
                m["n"] += 1
                ok = max(bal[t]-drained, 0.0) >= m["amount"]
                if ok:
                    m["done"] = True; drained += m["amount"]; got = True
                b.observe(m["amount"], ok)
        for m in st:
            if m["elig"]:
                recs.append((m["amount"], m["n"], m["done"], m["dead"]))
    return recs

REPS = 5
allrec = []
for r in range(REPS):
    pop = make_pop(350, 5, np.random.default_rng(7000+r), spend=0.80)
    allrec += run(pop, 8000+r)

amts = np.array([a for a,_,_,_ in allrec])
q = np.quantile(amts, [0.2,0.4,0.6,0.8])
print("Starvation and outcome by mandate SIZE (quintiles)\n")
print(f"{'quintile':>10} {'amount range':>20} {'n':>7} {'starved':>9} {'recovered':>10} {'died':>7}")
print("-"*68)
labels = ["Q1 smallest","Q2","Q3","Q4","Q5 largest"]
edges = [0]+list(q)+[1e18]
for i,lab in enumerate(labels):
    sel = [(a,n,d,x) for a,n,d,x in allrec if edges[i] <= a < edges[i+1]]
    if not sel: continue
    n = len(sel)
    st = sum(1 for a,k,d,x in sel if k==0)/n
    rc = sum(1 for a,k,d,x in sel if d)/n
    dd = sum(1 for a,k,d,x in sel if x)/n
    print(f"{lab:>10} {edges[i]:>9.0f}-{min(edges[i+1],99999):>9.0f} {n:>7} "
          f"{st*100:>8.1f}% {rc*100:>9.1f}% {dd*100:>6.1f}%")

lo = [(a,k,d,x) for a,k,d,x in allrec if a < q[0]]
hi = [(a,k,d,x) for a,k,d,x in allrec if a >= q[3]]
slo = sum(1 for a,k,d,x in lo if k==0)/len(lo)
shi = sum(1 for a,k,d,x in hi if k==0)/len(hi)
print(f"\nStarvation, smallest quintile vs largest: {slo*100:.1f}% vs {shi*100:.1f}%")
print("VERDICT:", "SYSTEMATIC - small mandates are starved, fairness floor needed"
      if slo > shi*2 + 0.02 else "NOT systematic by size - starvation is not size-driven")
