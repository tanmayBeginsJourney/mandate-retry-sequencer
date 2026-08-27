"""
B3 - FAIRNESS. Experiment design declared BEFORE running it.

HYPOTHESIS
  Ranking purely by index starves low-index mandates: some never get an attempt
  at all. A probability floor should reduce starvation, at some cost to total
  recovery. We want the size of both.

PRE-REGISTERED METRICS  (chosen before seeing any result)
  Primary fairness metric : STARVATION RATE = fraction of eligible mandates
                            that received ZERO attempts across the whole cycle.
                            Chosen over "worst merchant recovery" because a min
                            over ~60 noisy per-merchant estimates is unstable and
                            biased downward - it would let us claim an improvement
                            that is just noise shrinking.
  Secondary              : share of total attempts going to the bottom half of
                            mandates by index.
  COST metrics           : total recovery, mandate death rate.

BIAS CONTROLS  (each one closes a specific way we could fool ourselves)
  1. Paired design. Identical population and seeds across every floor value.
  2. The fair policy gets NO extra information and NO extra attempts.
  3. Merchant identity is random with respect to amount, so any systematic
     per-merchant gap is caused by the policy, not by merchant mix.
  4. We report the WHOLE floor sweep, not the best point. Picking the floor that
     looks good after seeing results is the exact trap we are trying to avoid.
  5. We report the COST alongside the benefit. A fairness result that only shows
     the fairness metric improving is cherry-picked by construction.
  6. Falsification condition stated in advance: if starvation does not fall
     materially as the floor rises, the mechanism does not work and we say so.

HONEST LABELLING
  This is a mixture-with-uniform implementation of a probability floor. It gives
  every eligible mandate a selection probability of at least floor/n_eligible.
  That is a genuine probabilistic floor but it is WEAKER than PROBFAIR, which
  optimises a per-arm bound. Do not claim we implemented PROBFAIR.
"""
import numpy as np
from collections import defaultdict
from sim4 import (make_pop, balance_trace, Belief, index_score,
                  DAYS, SLOTS_PER_DAY, T, NPCI_MAX, BASELINE_MAX, LOOKAHEAD)


def run_fair(pop, seed, floor=0.0, ltv_mult=6.0):
    rng = np.random.default_rng(seed)
    got = billed = ltv = 0.0
    dead = nman = att = ok = starved = eligible_total = 0
    bottom_half_attempts = 0
    cap = NPCI_MAX

    for c in pop:
        bal = balance_trace(c, rng)
        st = [dict(**m, n=0, done=False, dead=False, ever_eligible=False)
              for m in c["mandates"]]
        nman += len(st)
        for m in st:
            billed += m["amount"]
        drained = 0.0
        est_sal = c["salary"] * rng.uniform(0.7, 1.3)
        est_pay = int(np.clip(c["payday"] + rng.integers(-1, 2), 0, DAYS - 1))
        belief = Belief(est_sal, est_pay)

        for t in range(T):
            day, slot = divmod(t, SLOTS_PER_DAY)
            belief.advance(day, slot)
            live = [m for m in st if not m["done"] and not m["dead"]
                    and day >= m["due_day"] and m["n"] < cap]
            if not live:
                continue
            for m in live:
                m["ever_eligible"] = True

            fc = belief.forecast(day, slot)
            sc = []
            for m in live:
                p_now = belief.p_success(m["amount"])
                p_lat = (max([belief.p_success(m["amount"], pp) for pp in fc],
                             default=0.0) * 0.92 if cap - m["n"] > 1 else 0.0)
                sc.append((index_score(p_now, p_lat, m["amount"],
                                       cap - m["n"], ltv_mult), m))
            sc.sort(key=lambda x: -x[0])
            ranked = [m for _, m in sc]
            median_rank = len(ranked) // 2

            exp_bal = float((belief.p * belief.centers).sum())

            # probability floor: with probability `floor`, ignore the ranking
            # and consider mandates in a uniformly random order instead
            if floor > 0 and rng.random() < floor:
                order = list(range(len(ranked)))
                rng.shuffle(order)
                candidates = [(0.0, ranked[i], i) for i in order]
            else:
                candidates = [(s, m, i) for i, (s, m) in enumerate(sc)]

            chosen = []
            budget = exp_bal
            for s_, m, rank in candidates:
                if s_ <= 0 and floor == 0:
                    continue
                if s_ <= 0 and rng.random() > floor:
                    continue
                if m["amount"] <= budget:
                    chosen.append((m, rank))
                    budget -= m["amount"]

            rng.shuffle(chosen)
            for m, rank in chosen:
                if m["n"] >= cap:
                    continue
                m["n"] += 1
                att += 1
                if rank >= median_rank:
                    bottom_half_attempts += 1
                success = max(bal[t] - drained, 0.0) >= m["amount"]
                if success:
                    ok += 1
                    m["done"] = True
                    drained += m["amount"]
                    got += m["amount"]
                elif m["n"] >= cap:
                    m["dead"] = True
                    dead += 1
                    ltv += m["amount"] * ltv_mult
                belief.observe(m["amount"], success)

        for m in st:
            if m["ever_eligible"]:
                eligible_total += 1
                if m["n"] == 0:
                    starved += 1

    return dict(
        recovery=got / billed,
        approval=ok / att if att else 0.0,
        death=dead / nman,
        starvation=starved / eligible_total if eligible_total else 0.0,
        bottom_half_share=bottom_half_attempts / att if att else 0.0,
        att_per_mandate=att / nman,
    )


if __name__ == "__main__":
    FLOORS = [0.0, 0.05, 0.10, 0.20, 0.40]     # declared in advance
    REPS, N, K = 5, 350, 5
    pops = [make_pop(N, K, np.random.default_rng(7000 + r), spend=0.80)
            for r in range(REPS)]

    print("FAIRNESS SWEEP - full curve reported, benefit and cost together\n")
    print(f"{'floor':>7} {'starvation':>11} {'bottom-half':>12} "
          f"{'recovery':>9} {'death':>7} {'att/man':>8}")
    print("-" * 60)
    rows = {}
    for f in FLOORS:
        acc = defaultdict(list)
        for r, p in enumerate(pops):
            for k, v in run_fair(p, 8000 + r, floor=f).items():
                acc[k].append(v)
        m = {k: float(np.mean(v)) for k, v in acc.items()}
        s = {k: float(np.std(v, ddof=1)) for k, v in acc.items()}
        rows[f] = (m, s)
        print(f"{f:>7.2f} {m['starvation']*100:>10.1f}% "
              f"{m['bottom_half_share']*100:>11.1f}% "
              f"{m['recovery']*100:>8.1f}% {m['death']*100:>6.1f}% "
              f"{m['att_per_mandate']:>8.2f}")

    print("\nPaired change vs floor=0 (2 standard errors shown):")
    base = rows[0.0][0]
    for f in FLOORS[1:]:
        m, s = rows[f]
        for metric in ("starvation", "recovery", "death"):
            d = (m[metric] - base[metric]) * 100
            se = 2 * s[metric] * 100 / np.sqrt(REPS)
            flag = "sig" if abs(d) > se else "n.s."
            print(f"  floor={f:.2f} {metric:>11}: {d:>+6.2f} pts "
                  f"(+/-{se:.2f}, {flag})")
        print()
