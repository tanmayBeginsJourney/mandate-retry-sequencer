"""
v2 - calibrated so the BASELINE reproduces the real world (~30% approval,
business-decline dominated), then tests whether coordination matters.

Key realism fixes over v1:
  * balance is thin: salary lands, is mostly consumed within days by rent/EMIs/
    spending that are NOT our mandates
  * mandate due dates scatter across the month, so many fire into an empty account
  * exhausting the attempt budget kills the mandate for EVERY policy, including
    the baseline (Razorpay halts the subscription after its 3rd failure)
  * successful debits drain the shared balance, so mandates compete
"""
import numpy as np
from collections import defaultdict

DAYS = 30
SLOTS_PER_DAY = 3
T = DAYS * SLOTS_PER_DAY
NPCI_MAX = 4
BASELINE_MAX = 3          # Razorpay UPI: attempt + 2 retries
LTV_MULT = 6.0


def make_population(n_cust, n_merch, rng, mandate_load=0.18, spend=0.93, kfix=None):
    pop = []
    for _ in range(n_cust):
        payday = 0 if rng.random() < 0.60 else int(rng.integers(25, 30))
        salary = float(rng.lognormal(np.log(19000), 0.55))
        k = kfix if kfix else int(rng.integers(2, min(n_merch, 6) + 1))
        k = min(k, n_merch)
        merchants = rng.choice(n_merch, size=k, replace=False)
        # total mandate burden is a realistic slice of income
        burden = salary * mandate_load * rng.uniform(0.6, 1.4)
        weights = rng.dirichlet(np.ones(k))
        mandates = []
        for m, w in zip(merchants, weights):
            amt = float(np.clip(round(burden * w, -1), 99, 15000))
            mandates.append(dict(merchant=int(m), amount=amt,
                                 due_day=int(rng.integers(0, DAYS - 8))))
        pop.append(dict(payday=payday, salary=salary,
                        spend=float(np.clip(rng.normal(spend, 0.10), 0.55, 1.15)),
                        mandates=mandates))
    return pop


def balance_trace(c, rng):
    """Salary lands then drains fast. Most of the month the account is thin."""
    bal = np.zeros(T)
    b = c["salary"] * rng.uniform(0.0, 0.06)
    outflow = c["salary"] * c["spend"]
    # heavy front-load: rent/EMI/discretionary hit within days of payday
    d_off = (np.arange(DAYS) - c["payday"]) % DAYS
    w = np.exp(-0.42 * d_off)
    daily = outflow * w / w.sum()
    for d in range(DAYS):
        if d == c["payday"]:
            b += c["salary"]
        for s in range(SLOTS_PER_DAY):
            b = max(b - daily[d] / SLOTS_PER_DAY * rng.uniform(0.4, 1.6), 0.0)
            bal[d * SLOTS_PER_DAY + s] = b
    return bal


def p_est(bal, amt):
    if bal <= 0 or amt <= 0:
        return 0.02
    return float(np.clip(1 / (1 + np.exp(-3.0 * (bal / amt - 1.0))), 0.02, 0.97))


def run(policy, pop, seed, fair=0.0):
    rng = np.random.default_rng(seed)
    got = 0.0; billed = 0.0; dead = 0; nman = 0; ltv = 0.0; att = 0
    per_m = defaultdict(lambda: [0.0, 0.0])
    cap = BASELINE_MAX if policy == "baseline" else NPCI_MAX

    for c in pop:
        bal = balance_trace(c, rng)
        st = [dict(**m, n=0, done=False, dead=False) for m in c["mandates"]]
        nman += len(st); 
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
                # fire on the due slot, then the two adjacent slots (same-day)
                chosen = [m for m in live
                          if t - m["due_day"] * SLOTS_PER_DAY == m["n"]]

            elif policy == "independent":
                # every merchant independently picks the same "best" moment:
                # the evening slot of the next payday
                nxt = pay_slot if t <= pay_slot else pay_slot + DAYS * SLOTS_PER_DAY
                tgt = min(nxt + 2, T - 1)
                chosen = [m for m in live
                          if t == min(tgt + m["n"] * SLOTS_PER_DAY, T - 1)]

            else:  # coordinated / coordinated_fair
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
                        idx += fair * val * rng.random()
                    sc.append((idx, m))
                sc.sort(key=lambda x: -x[0])
                chosen, budget = [], avail
                for idx, m in sc:
                    if idx <= 0:            # passive is genuinely better
                        continue
                    if m["amount"] <= budget:
                        chosen.append(m); budget -= m["amount"]

            rng.shuffle(chosen)             # no guaranteed execution order
            for m in chosen:
                m["n"] += 1; att += 1
                if max(bal[t] - drained, 0.0) >= m["amount"]:
                    m["done"] = True; drained += m["amount"]
                    got += m["amount"]; per_m[m["merchant"]][0] += m["amount"]
                elif m["n"] >= cap:
                    m["dead"] = True; dead += 1
                    ltv += m["amount"] * LTV_MULT

    rates = [v[0] / v[1] for v in per_m.values() if v[1] > 0]
    return dict(rec=got / billed, death=dead / nman,
                net=(got - ltv) / billed, apm=att / nman,
                worst=min(rates) if rates else 0, spread=(max(rates) - min(rates)) if rates else 0)


def calibrate(target=0.30, n_merch=8, reps=3):
    """Find the spend level where the BASELINE matches the real ~30% approval rate."""
    print("calibrating baseline to real-world ~30% approval...")
    best = None
    for spend in np.arange(0.70, 1.10, 0.05):
        vals = []
        for r in range(reps):
            pop = make_population(2500, n_merch, np.random.default_rng(200 + r), spend=spend)
            vals.append(run("baseline", pop, 300 + r)["rec"])
        v = float(np.mean(vals))
        print(f"   spend={spend:.2f} -> baseline recovery {v*100:5.1f}%")
        if best is None or abs(v - target) < abs(best[1] - target):
            best = (float(spend), v)
    print(f"   chosen spend={best[0]:.2f} (baseline {best[1]*100:.1f}%)\n")
    return best[0]


def sweep(spend, k_list=(1, 2, 4, 6), n_cust=3000, reps=5):
    print("Total amount owed per customer is held CONSTANT.")
    print("Only the number of separate mandates sharing that balance changes.\n")
    print(f"{'mandates':>10} {'policy':>18} {'recovery':>9} {'death':>7} "
          f"{'net':>8} {'att/man':>8} {'worst m':>8}")
    print("-" * 74)
    out = {}
    for nm in k_list:
        pops = [make_population(n_cust, 40, np.random.default_rng(400 + r),
                                spend=spend, kfix=nm) for r in range(reps)]
        for pol, f in [("baseline", 0), ("independent", 0),
                       ("coordinated", 0), ("coordinated_fair", 0.30)]:
            acc = defaultdict(list)
            for r, p in enumerate(pops):
                for k, v in run(pol, p, 800 + r, fair=f).items():
                    acc[k].append(v)
            out[(nm, pol)] = {k: float(np.mean(v)) for k, v in acc.items()}
            a = out[(nm, pol)]
            print(f"{nm:>10} {pol:>18} {a['rec']*100:>8.1f}% {a['death']*100:>6.1f}% "
                  f"{a['net']*100:>7.1f}% {a['apm']:>8.2f} {a['worst']*100:>7.1f}%")
        print()
    return out


if __name__ == "__main__":
    s = 0.95
    pass
