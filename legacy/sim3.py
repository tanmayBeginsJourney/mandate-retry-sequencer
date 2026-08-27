"""
v3 - two fixes to make the comparison fair, then a clean test of the
contention hypothesis.

Fix 1: 'independent' now uses its full 4-attempt budget (in v2 it effectively
       got one shot, which flattered every other policy).
Fix 2: per-mandate amount is held CONSTANT and the count varies, so growing
       the number of mandates genuinely grows the claims on one balance.
       (v2 held total debt constant, which meant more mandates just meant
       smaller, individually-easier mandates - a confound.)
"""
import numpy as np
from collections import defaultdict
from sim2 import balance_trace, p_est, DAYS, SLOTS_PER_DAY, T, NPCI_MAX, BASELINE_MAX, LTV_MULT


def make_pop(n_cust, k, rng, spend=0.95, amt_frac=0.045):
    """k mandates per customer, each a FIXED fraction of salary."""
    pop = []
    for _ in range(n_cust):
        payday = 0 if rng.random() < 0.60 else int(rng.integers(25, 30))
        salary = float(rng.lognormal(np.log(19000), 0.55))
        mandates = [dict(merchant=int(m),
                         amount=float(np.clip(round(salary * amt_frac *
                                                    rng.uniform(0.7, 1.3), -1), 99, 15000)),
                         due_day=int(rng.integers(0, DAYS - 8)))
                    for m in rng.choice(40, size=k, replace=False)]
        pop.append(dict(payday=payday, salary=salary,
                        spend=float(np.clip(rng.normal(spend, 0.10), 0.55, 1.15)),
                        mandates=mandates))
    return pop


def run(policy, pop, seed, fair=0.0):
    rng = np.random.default_rng(seed)
    got = billed = ltv = 0.0
    dead = nman = att = 0
    per_m = defaultdict(lambda: [0.0, 0.0])
    cap = BASELINE_MAX if policy == "baseline" else NPCI_MAX

    for c in pop:
        bal = balance_trace(c, rng)
        st = [dict(**m, n=0, done=False, dead=False) for m in c["mandates"]]
        nman += len(st)
        for m in st:
            billed += m["amount"]; per_m[m["merchant"]][1] += m["amount"]
        drained = 0.0
        pay_slot = c["payday"] * SLOTS_PER_DAY

        for t in range(T):
            day = t // SLOTS_PER_DAY
            live = [m for m in st if not m["done"] and not m["dead"]
                    and day >= m["due_day"] and m["n"] < cap]
            if not live:
                continue
            avail = max(bal[t] - drained, 0.0)

            if policy == "baseline":
                chosen = [m for m in live
                          if t - m["due_day"] * SLOTS_PER_DAY == m["n"]]

            elif policy == "independent":
                # FIXED: every merchant independently targets the payday evening
                # slot, and keeps retrying on following evenings until budget spent
                chosen = []
                for m in live:
                    nxt = pay_slot if t <= pay_slot else pay_slot + DAYS * SLOTS_PER_DAY
                    tgt = min(nxt + 2, T - 1)
                    if t >= tgt and t % SLOTS_PER_DAY == 2 and (t - tgt) // SLOTS_PER_DAY == m["n"]:
                        chosen.append(m)
                    elif t > tgt + 6 * SLOTS_PER_DAY and t % SLOTS_PER_DAY == 2 and m["n"] == 0:
                        chosen.append(m)

            else:
                sc = []
                for m in live:
                    p_now = p_est(avail, m["amount"])
                    look = bal[t + 1:min(t + 15, T)]
                    p_lat = (max([p_est(max(f - drained, 0), m["amount"])
                                  for f in look], default=0.0) * 0.92
                             if cap - m["n"] > 1 else 0.0)
                    val = m["amount"] * (1 + LTV_MULT * (1 if cap - m["n"] == 1 else 0))
                    idx = val * (p_now - p_lat)
                    if policy == "coordinated_fair":
                        # deterministic bonus for mandates that keep losing out
                        idx += fair * val * (m["n"] == 0) * (day / DAYS)
                    sc.append((idx, m))
                sc.sort(key=lambda x: -x[0])
                chosen, budget = [], avail
                for idx, m in sc:
                    if idx <= 0:
                        continue
                    if m["amount"] <= budget:
                        chosen.append(m); budget -= m["amount"]

            rng.shuffle(chosen)
            for m in chosen:
                m["n"] += 1; att += 1
                if max(bal[t] - drained, 0.0) >= m["amount"]:
                    m["done"] = True; drained += m["amount"]
                    got += m["amount"]; per_m[m["merchant"]][0] += m["amount"]
                elif m["n"] >= cap:
                    m["dead"] = True; dead += 1; ltv += m["amount"] * LTV_MULT

    rates = [v[0] / v[1] for v in per_m.values() if v[1] > 0]
    return dict(rec=got / billed, death=dead / nman, apm=att / nman,
                worst=min(rates) if rates else 0.0,
                spread=(max(rates) - min(rates)) if rates else 0.0)


if __name__ == "__main__":
    print("Per-mandate amount held CONSTANT (~4.5% of salary each).")
    print("More mandates = genuinely more claims competing for one balance.\n")
    print(f"{'mandates':>9} {'policy':>18} {'recovery':>9} {'death':>7} "
          f"{'att/man':>8} {'worst m':>8} {'spread':>8}")
    print("-" * 72)
    res = {}
    for k in (1, 2, 3, 5, 7):
        pops = [make_pop(1500, k, np.random.default_rng(600 + r)) for r in range(3)]
        for pol, f in [("baseline", 0), ("independent", 0),
                       ("coordinated", 0), ("coordinated_fair", 0.35)]:
            acc = defaultdict(list)
            for r, p in enumerate(pops):
                for kk, v in run(pol, p, 900 + r, fair=f).items():
                    acc[kk].append(v)
            a = {kk: float(np.mean(v)) for kk, v in acc.items()}
            res[(k, pol)] = a
            print(f"{k:>9} {pol:>18} {a['rec']*100:>8.1f}% {a['death']*100:>6.1f}% "
                  f"{a['apm']:>8.2f} {a['worst']*100:>7.1f}% {a['spread']*100:>7.1f}%")
        gap = res[(k, 'coordinated')]['rec'] - res[(k, 'independent')]['rec']
        print(f"{'':>9} {'--> coord advantage':>18} {gap*100:>+8.1f} pts\n")
