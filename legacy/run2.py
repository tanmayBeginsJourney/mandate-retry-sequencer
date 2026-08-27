import numpy as np
from collections import defaultdict
import world2 as W

FIXED = {"fixed_daily3": 3, "fixed_daily4": 4}


def cap_for(p):
    if p == "baseline":
        return W.BASELINE_MAX
    if p in FIXED:
        return FIXED[p]
    return W.NPCI_MAX


def run(policy, pop, seed, ltv_mult=6.0, topup_p=0.0, topup_lag=1,
        topup_life=3, topup_mult=1.15):
    rng = np.random.default_rng(seed)
    trng = np.random.default_rng(seed + 777)
    days = pop[0]["days"]
    cyc = pop[0]["cycle_days"]
    T = days * W.SLOTS_PER_DAY
    SPD = W.SLOTS_PER_DAY

    got = billed = 0.0
    att = ok = 0
    n_rec = n_dead = n_unres = n_tot = 0
    unres_left = []
    cap = cap_for(policy)

    for c in pop:
        bal = W.balance_trace(c, rng)
        st = [dict(**m, n=0, done=False, dead=False) for m in c["mandates"]]
        n_tot += len(st)
        for m in st:
            billed += m["amount"]
        drained = 0.0
        topups = np.zeros(T + topup_lag + topup_life + 2)

        est_sal = c["salary"] * rng.uniform(0.7, 1.3)
        est_pay = int(np.clip(c["payday"] + rng.integers(-1, 2), 0, cyc - 1))

        if policy in ("solo_shared", "portfolio", "myopic"):
            shared = W.Belief(est_sal, est_pay, days, cyc)
            beliefs = {id(m): shared for m in st}
        elif policy == "solo_own":
            beliefs = {id(m): W.Belief(est_sal, est_pay, days, cyc) for m in st}
        else:
            beliefs = {}

        fc = {}
        for t in range(T):
            day, slot = divmod(t, SPD)
            for b in set(beliefs.values()):
                b.advance(day, slot)
            fc.clear()

            live = [m for m in st if not m["done"] and not m["dead"]
                    and day >= m["due_day"] and m["n"] < cap]
            if not live:
                continue

            if policy == "baseline":
                chosen = [m for m in live if t - m["due_day"] * SPD == m["n"]]
            elif policy in FIXED:
                chosen = [m for m in live
                          if slot == 0 and day - m["due_day"] == m["n"]]
            elif policy == "payday_wait":
                # next estimated payday at or after the due day, then daily
                chosen = []
                for m in live:
                    start = m["due_day"] + ((est_pay - m["due_day"]) % cyc)
                    if slot == 0 and day - start == m["n"]:
                        chosen.append(m)
            elif policy == "oracle":
                sc = []
                for m in live:
                    p_now = 1.0 if max(bal[t] - drained, 0.0) >= m["amount"] else 0.0
                    fut = bal[t + 1:min(t + 1 + W.LOOKAHEAD, T)]
                    p_lat = (1.0 if any(max(f - drained, 0) >= m["amount"]
                                        for f in fut) else 0.0) if cap - m["n"] > 1 else 0.0
                    sc.append((W.index_score(p_now, p_lat, m["amount"],
                                             cap - m["n"], ltv_mult), m))
                sc.sort(key=lambda x: -x[0])
                chosen, budget = [], max(bal[t] - drained, 0.0)
                for s_, m in sc:
                    if s_ > 0 and m["amount"] <= budget:
                        chosen.append(m)
                        budget -= m["amount"]
            else:
                sc = []
                for m in live:
                    b = beliefs[id(m)]
                    key = id(b)
                    if key not in fc:
                        fc[key] = b.forecast(day, slot)
                    p_now = b.p_success(m["amount"])
                    if policy == "myopic":
                        s_ = m["amount"] * p_now
                    else:
                        p_lat = (max([b.p_success(m["amount"], pp)
                                      for pp in fc[key]], default=0.0) * 0.92
                                 if cap - m["n"] > 1 else 0.0)
                        s_ = W.index_score(p_now, p_lat, m["amount"],
                                           cap - m["n"], ltv_mult)
                    sc.append((s_, m))
                sc.sort(key=lambda x: -x[0])
                if policy in ("portfolio", "myopic"):
                    bb = beliefs[id(st[0])]
                    exp_bal = float((bb.p * bb.centers).sum())
                    chosen = []
                    for s_, m in sc:
                        if s_ <= 0:
                            continue
                        if m["amount"] <= exp_bal:
                            chosen.append(m)
                            exp_bal -= m["amount"]
                else:
                    chosen = [m for s_, m in sc if s_ > 0]

            rng.shuffle(chosen)
            for m in chosen:
                if m["n"] >= cap:
                    continue
                m["n"] += 1
                att += 1
                avail = max(bal[t] - drained + topups[t], 0.0)
                success = avail >= m["amount"]
                if success:
                    ok += 1
                    m["done"] = True
                    drained += m["amount"]
                    got += m["amount"]
                else:
                    if m["n"] >= cap:
                        m["dead"] = True
                    if topup_p > 0 and trng.random() < topup_p:
                        credit = m["amount"] * topup_mult
                        lo, hi = min(t + topup_lag, T), min(t + topup_lag + topup_life, T)
                        topups[lo:hi] += credit
                if id(m) in beliefs:
                    beliefs[id(m)].observe(m["amount"], success)

        for m in st:
            if m["done"]:
                n_rec += 1
            elif m["dead"]:
                n_dead += 1
            else:
                n_unres += 1
                unres_left.append(cap - m["n"])

    return dict(rec_count=n_rec / n_tot, rec_rupee=got / billed,
                dead_count=n_dead / n_tot, unres_count=n_unres / n_tot,
                approval=(ok / att if att else 0.0), apm=att / n_tot,
                unres_att_left=float(np.mean(unres_left)) if unres_left else 0.0)


def calibrate(anchor, days, target=0.30, k=3, n=600, reps=3, topup_p=0.0):
    best = None
    for sp in np.arange(0.55, 1.25, 0.05):
        a = []
        for i in range(reps):
            pop = W.make_pop(n, k, np.random.default_rng(1000 + i), days, spend=float(sp))
            a.append(run(anchor, pop, 2000 + i, topup_p=topup_p)["approval"])
        am = float(np.mean(a))
        if best is None or abs(am - target) < abs(best[1] - target):
            best = (float(sp), am)
    return best


def table(days, spend, k, pols, n=400, reps=5, topup_p=0.0, label=""):
    print(f"--- {label} | horizon={days}d, k={k}, spend={spend:.2f}, "
          f"topup_p={topup_p} " + "-" * 20)
    print(f"{'policy':>13} {'rec(cnt)':>9} {'appr':>7} {'dead':>7} "
          f"{'unres':>7} {'att/man':>8} {'unres_left':>11}")
    raw, out = {}, {}
    for pol in pols:
        acc = defaultdict(list)
        for r in range(reps):
            pop = W.make_pop(n, k, np.random.default_rng(3000 + r), days, spend=spend)
            for kk, v in run(pol, pop, 4000 + r, topup_p=topup_p).items():
                acc[kk].append(v)
        raw[pol] = acc
        m = {kk: float(np.mean(v)) for kk, v in acc.items()}
        out[pol] = m
        print(f"{pol:>13} {m['rec_count']*100:>8.1f}% {m['approval']*100:>6.1f}% "
              f"{m['dead_count']*100:>6.1f}% {m['unres_count']*100:>6.1f}% "
              f"{m['apm']:>8.2f} {m['unres_att_left']:>11.2f}")

    def gap(a, b, label):
        da = np.array(raw[b]["rec_count"]) - np.array(raw[a]["rec_count"])
        mu = da.mean() * 100
        se = da.std(ddof=1) * 100 / np.sqrt(len(da))
        print(f"   {label:<44} {mu:>+6.2f} pts (+/-{2*se:.2f}) "
              f"{'SIG' if abs(mu) > 2*se else 'n.s.'}")
    return out, gap
