"""
AUDIT HARNESS. Does not modify sim4. Imports its world + Belief unchanged so
that any difference from the published numbers is attributable to the policies
and metrics added here, not to a changed world.

WHAT THIS ADDS
  1. COUNT-BASED recovery alongside rupee-based, for every policy.
     sim4.run() returns rec = got/billed (RUPEES). decomposition_results.txt is
     therefore a rupee table. audit_metric.py returns counts. The published
     material mixes them. Here both are always reported.
  2. Three-way split (recovered / dead / unresolved) for every policy, not just
     baseline+portfolio.
  3. Competent comparators the project never tested:
       fixed_daily3 : attempts on due_day, +1d, +2d  (spacing only, 3 attempts)
       fixed_daily4 : attempts on due_day, +1d, +2d, +3d  (Razorpay's CARD model)
       payday_wait  : fire at max(due_day, est_payday), then daily. The
                      "Chargebee Revive" equivalent - payday alignment with no
                      belief filter and no index.
       myopic       : pooled belief + budget, but index = amount * p_now.
                      No forecast, no p_later, no LTV term. Isolates whether
                      the Whittle structure earns its keep over greedy.
  4. TOP-UP MECHANISM. sim4's balance is monotonically decreasing except for the
     payday injection, so a retry an hour later fails by construction. Here a
     failed attempt can trigger a customer top-up with probability `topup_p`,
     arriving `topup_lag` slots later and persisting `topup_life` slots.
     Propensity is drawn from a per-mandate stream seeded identically across
     policies, so all policies face the same customers with the same
     willingness to top up.
  5. Per-baseline recalibration, because calibrating the world on the baseline
     that is also the comparator is a feedback loop.

NOT FIXED HERE (deliberately - these need the rewrite):
  no 24h commit, no pending-notification state, no peak hours, no first
  presentation, no decline codes. This harness measures whether the published
  RESULT survives a competent comparator. It does not measure the real system.
"""
import numpy as np
from collections import defaultdict
from sim4 import (make_pop, Belief, index_score, spend_weights,
                  DAYS, SLOTS_PER_DAY, T, NPCI_MAX, BASELINE_MAX, LOOKAHEAD)

FIXED = {"fixed_daily3": 3, "fixed_daily4": 4}
BELIEF_POLS = ("solo_own", "solo_shared", "portfolio", "myopic")
SCHEDULED = ("baseline", "fixed_daily3", "fixed_daily4", "payday_wait")


def balance_trace(c, rng):
    """Identical to sim4.balance_trace. Reproduced so the audit is self-contained."""
    bal = np.zeros(T)
    b = c["salary"] * rng.uniform(0.0, 0.06)
    daily = c["salary"] * c["spend"] * spend_weights(c["payday"])
    for d in range(DAYS):
        if d == c["payday"]:
            b += c["salary"]
        for s in range(SLOTS_PER_DAY):
            b = max(b - daily[d] / SLOTS_PER_DAY * rng.uniform(0.4, 1.6), 0.0)
            bal[d * SLOTS_PER_DAY + s] = b
    return bal


def cap_for(policy):
    if policy == "baseline":
        return BASELINE_MAX
    if policy in FIXED:
        return FIXED[policy]
    return NPCI_MAX


def run(policy, pop, seed, ltv_mult=6.0, topup_p=0.0, topup_lag=1,
        topup_life=3, topup_mult=1.15):
    rng = np.random.default_rng(seed)
    # separate stream for top-up propensity so it does not desync across
    # policies when they make different numbers of attempts
    trng = np.random.default_rng(seed + 777)

    got = billed = 0.0
    att = ok = 0
    n_rec = n_dead = n_unres = n_tot = 0
    unres_left = []
    cap = cap_for(policy)

    for c in pop:
        bal = balance_trace(c, rng)
        st = [dict(**m, n=0, done=False, dead=False) for m in c["mandates"]]
        n_tot += len(st)
        for m in st:
            billed += m["amount"]
        drained = 0.0
        topups = np.zeros(T + topup_lag + topup_life + 2)  # credit arriving per slot

        est_sal = c["salary"] * rng.uniform(0.7, 1.3)
        est_pay = int(np.clip(c["payday"] + rng.integers(-1, 2), 0, DAYS - 1))

        if policy in ("solo_shared", "portfolio", "myopic"):
            shared = Belief(est_sal, est_pay)
            beliefs = {id(m): shared for m in st}
        elif policy == "solo_own":
            beliefs = {id(m): Belief(est_sal, est_pay) for m in st}
        else:
            beliefs = {}

        fc_cache = {}
        for t in range(T):
            day, slot = divmod(t, SLOTS_PER_DAY)
            for b in set(beliefs.values()):
                b.advance(day, slot)
            fc_cache.clear()

            live = [m for m in st if not m["done"] and not m["dead"]
                    and day >= m["due_day"] and m["n"] < cap]
            if not live:
                continue

            if policy == "baseline":
                # sim4's schedule: consecutive slots from the due day
                chosen = [m for m in live
                          if t - m["due_day"] * SLOTS_PER_DAY == m["n"]]
            elif policy in FIXED:
                # one attempt per DAY, at slot 0, starting on the due day
                chosen = [m for m in live
                          if slot == 0 and day - m["due_day"] == m["n"]]
            elif policy == "payday_wait":
                # wait for the estimated payday, then one attempt per day
                start = max(est_pay, 0)
                chosen = [m for m in live
                          if slot == 0
                          and day - max(m["due_day"], start) == m["n"]]
            else:
                sc = []
                for m in live:
                    b = beliefs[id(m)]
                    key = id(b)
                    if key not in fc_cache:
                        fc_cache[key] = b.forecast(day, slot)
                    p_now = b.p_success(m["amount"])
                    if policy == "myopic":
                        s_ = m["amount"] * p_now
                    else:
                        p_lat = (max([b.p_success(m["amount"], pp)
                                      for pp in fc_cache[key]], default=0.0) * 0.92
                                 if cap - m["n"] > 1 else 0.0)
                        s_ = index_score(p_now, p_lat, m["amount"],
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
                    # customer may top up after seeing the failure
                    if topup_p > 0 and trng.random() < topup_p:
                        credit = m["amount"] * topup_mult
                        lo = min(t + topup_lag, T)
                        hi = min(t + topup_lag + topup_life, T)
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

    return dict(
        rec_rupee=got / billed,
        rec_count=n_rec / n_tot,
        dead_count=n_dead / n_tot,
        unres_count=n_unres / n_tot,
        approval=(ok / att if att else 0.0),
        apm=att / n_tot,
        unres_att_left=float(np.mean(unres_left)) if unres_left else 0.0,
    )


def calibrate(anchor, target=0.30, k=3, n=600, reps=3, topup_p=0.0, quiet=False):
    """Sweep spend until `anchor` policy reproduces ~30% per-attempt approval."""
    best = None
    rows = []
    for sp in np.arange(0.55, 1.20, 0.05):
        a = []
        for i in range(reps):
            pop = make_pop(n, k, np.random.default_rng(1000 + i), spend=float(sp))
            a.append(run(anchor, pop, 2000 + i, topup_p=topup_p)["approval"])
        am = float(np.mean(a))
        rows.append((float(sp), am))
        if best is None or abs(am - target) < abs(best[1] - target):
            best = (float(sp), am)
    if not quiet:
        print(f"  calibration anchor = {anchor}, topup_p={topup_p}")
        print("   " + "  ".join(f"{s:.2f}:{a*100:.0f}%" for s, a in rows))
        print(f"   -> spend={best[0]:.2f} gives approval {best[1]*100:.1f}%")
    return best[0]
