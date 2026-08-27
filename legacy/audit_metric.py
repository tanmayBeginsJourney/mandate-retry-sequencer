"""
Challenge: is the 58% -> 5% mandate-death headline real, or an artefact?

SUSPICION: 'death' is only recorded when a mandate EXHAUSTS its attempts.
The baseline gets 3 attempts and burns them all same-day, so it hits that
condition fast. Our policy gets 4 and deliberately spends fewer - so at the end
of a 30-day horizon many of its mandates still have attempts LEFT and are
therefore never counted as dead, even though the money was never collected.

If so, we are partly measuring "did you spend your budget", not "did you lose
the mandate". Reporting a three-way split settles it.

ALSO: 'recovery' is rupees-based and 'death' is mandate-count-based. They are
not complements and must never be presented as if they were.
"""
import numpy as np
from collections import defaultdict
from sim4 import (make_pop, balance_trace, Belief, index_score,
                  DAYS, SLOTS_PER_DAY, T, NPCI_MAX, BASELINE_MAX)

def run(policy, pop, seed, ltv=6.0):
    rng = np.random.default_rng(seed)
    cap = BASELINE_MAX if policy == "baseline" else NPCI_MAX
    n_rec = n_dead = n_unres = n_tot = 0
    unres_attempts_left = []
    for c in pop:
        bal = balance_trace(c, rng)
        st = [dict(**m, n=0, done=False, dead=False) for m in c["mandates"]]
        n_tot += len(st)
        drained = 0.0
        est_sal = c["salary"] * rng.uniform(0.7, 1.3)
        est_pay = int(np.clip(c["payday"] + rng.integers(-1, 2), 0, DAYS-1))
        b = Belief(est_sal, est_pay) if policy != "baseline" else None
        for t in range(T):
            day, slot = divmod(t, SLOTS_PER_DAY)
            if b: b.advance(day, slot)
            live = [m for m in st if not m["done"] and not m["dead"]
                    and day >= m["due_day"] and m["n"] < cap]
            if not live: continue
            if policy == "baseline":
                chosen = [m for m in live if t - m["due_day"]*SLOTS_PER_DAY == m["n"]]
            else:
                fc = b.forecast(day, slot)
                sc = []
                for m in live:
                    p_now = b.p_success(m["amount"])
                    p_lat = (max([b.p_success(m["amount"], pp) for pp in fc], default=0.0)*0.92
                             if cap-m["n"] > 1 else 0.0)
                    sc.append((index_score(p_now,p_lat,m["amount"],cap-m["n"],ltv), m))
                sc.sort(key=lambda x:-x[0])
                budget = float((b.p*b.centers).sum()); chosen=[]
                for s_,m in sc:
                    if s_<=0: continue
                    if m["amount"]<=budget: chosen.append(m); budget-=m["amount"]
            rng.shuffle(chosen)
            for m in chosen:
                if m["n"]>=cap: continue
                m["n"]+=1
                ok = max(bal[t]-drained,0.0) >= m["amount"]
                if ok: m["done"]=True; drained+=m["amount"]
                elif m["n"]>=cap: m["dead"]=True
                if b: b.observe(m["amount"], ok)
        for m in st:
            if m["done"]: n_rec+=1
            elif m["dead"]: n_dead+=1
            else:
                n_unres+=1; unres_attempts_left.append(cap-m["n"])
    return (n_rec/n_tot, n_dead/n_tot, n_unres/n_tot,
            float(np.mean(unres_attempts_left)) if unres_attempts_left else 0.0)

print("MANDATE OUTCOMES BY COUNT (not rupees) - three-way split\n")
print(f"{'k':>3} {'policy':>10} {'recovered':>10} {'dead':>8} {'UNRESOLVED':>11} {'att left':>9}")
print("-"*56)
for k in (3,5,7):
    for pol in ("baseline","portfolio"):
        acc=[]
        for r in range(4):
            pop = make_pop(300,k,np.random.default_rng(9000+r),spend=0.80)
            acc.append(run(pol,pop,9500+r))
        a=np.mean(acc,axis=0)
        print(f"{k:>3} {pol:>10} {a[0]*100:>9.1f}% {a[1]*100:>7.1f}% "
              f"{a[2]*100:>10.1f}% {a[3]:>9.2f}")
    print()
