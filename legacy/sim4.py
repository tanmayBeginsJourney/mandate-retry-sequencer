"""
v4 - rebuilt after auditing v3 and finding the results were largely an artefact.

WHAT WAS WRONG IN v3 (all of it flattered the coordinated policy):
  1. CLAIRVOYANCE. `look = bal[t+1:t+15]` read the TRUE FUTURE balance.
     The policy knew exactly when money would arrive.
  2. ORACLE BUDGET. It read the TRUE current balance and used it as a knapsack
     budget, so it never attempted a payment it knew would fail. That alone
     explains most of the "fewer attempts, more money" headline.
  3. STRAWMAN RIVAL. `independent` had a wraparound bug: for any mandate due
     after payday, its target slot resolved to the LAST slot of the horizon.
     Hence exactly 1.00 attempts/mandate - it was handed one shot at the worst
     possible moment.
  4. WRONG CALIBRATION TARGET. I calibrated per-MANDATE recovery to ~30%.
     NPCI's ~30% is per-ATTEMPT approval. A mandate that succeeds on its 3rd
     try is 100% recovered but only 33% approval. So v3's population was far
     poorer than reality.

WHAT v4 DOES INSTEAD:
  * A real Bayes filter over the hidden balance, updated only by censored
    observations (a failure at Rs X means "balance < X"; a success means ">= X").
    No policy ever sees the true balance or the future.
  * Policies get a NOISY salary estimate and a NOISY payday estimate, and a
    population-average spend curve. They do not know the customer's real
    spend rate.
  * Clean decomposition of where any gain comes from:
        solo_own     -> solo_shared : value of POOLED OBSERVATIONS  (data effect)
        solo_shared  -> portfolio   : value of COORDINATED ACTION   (coordination effect)
        portfolio    -> oracle      : headroom left by imperfect inference
    Identical index maths in all three. Only the information and the
    action-coupling change.
  * `oracle` is reported explicitly as an unachievable upper bound, so we can
    see how much of any result is just information leakage.
  * Calibrated on per-ATTEMPT approval rate, and both metrics reported.
"""
import numpy as np
from collections import defaultdict

DAYS, SLOTS_PER_DAY = 30, 3
T = DAYS * SLOTS_PER_DAY
NPCI_MAX, BASELINE_MAX = 4, 3
NB = 80                       # balance-belief grid resolution
LOOKAHEAD = 15                # slots the policy reasons ahead (via its BELIEF)


# ----------------------------------------------------------------- world ----
def make_pop(n, k, rng, spend=0.95, amt_frac=0.045):
    pop = []
    for _ in range(n):
        payday = 0 if rng.random() < 0.60 else int(rng.integers(25, 30))
        salary = float(rng.lognormal(np.log(19000), 0.55))
        mandates = [dict(merchant=int(m),
                         amount=float(np.clip(round(salary * amt_frac *
                                                    rng.uniform(0.7, 1.3), -1), 99, 15000)),
                         due_day=int(rng.integers(0, DAYS - 8)))
                    for m in rng.choice(60, size=k, replace=False)]
        pop.append(dict(payday=payday, salary=salary,
                        spend=float(np.clip(rng.normal(spend, 0.10), 0.55, 1.15)),
                        mandates=mandates))
    return pop


def spend_weights(payday):
    d = (np.arange(DAYS) - payday) % DAYS
    w = np.exp(-0.42 * d)
    return w / w.sum()


def balance_trace(c, rng):
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


# ---------------------------------------------------------------- belief ----
class Belief:
    """Bayes filter over the hidden balance. Sees ONLY censored debit outcomes."""

    def __init__(self, est_salary, est_payday, est_spend=0.95):
        self.hi = 2.5 * est_salary
        self.bw = self.hi / NB
        self.centers = (np.arange(NB) + 0.5) * self.bw
        self.p = np.zeros(NB)
        self.p[:max(1, int(0.08 * est_salary / self.bw))] = 1.0   # starts near-empty
        self.p /= self.p.sum()
        self.daily = est_salary * est_spend * spend_weights(est_payday)
        self.est_payday, self.est_salary = est_payday, est_salary
        self._k = np.array([0.15, 0.70, 0.15])

    def _shift(self, p, bins):
        out = np.zeros(NB)
        if bins >= 0:
            if bins < NB:
                out[:NB - bins] = p[bins:]
            out[0] += p[:min(bins, NB)].sum()
        else:
            b = -bins
            if b < NB:
                out[b:] = p[:NB - b]
            out[NB - 1] += p[max(NB - b, 0):].sum()
        return out

    def step(self, p, day, slot):
        if day == self.est_payday and slot == 0:
            p = self._shift(p, -int(self.est_salary / self.bw))
        drain = self.daily[day] / SLOTS_PER_DAY
        p = self._shift(p, int(round(drain / self.bw)))
        p = np.convolve(p, self._k, mode="same")       # uncertainty grows
        s = p.sum()
        return p / s if s > 0 else np.ones(NB) / NB

    def advance(self, day, slot):
        self.p = self.step(self.p, day, slot)

    def observe(self, amount, success):
        idx = int(np.ceil(amount / self.bw))
        q = self.p.copy()
        if success:
            q[:min(idx, NB)] = 0.0                      # balance was >= amount
            s = q.sum()
            q = q / s if s > 0 else self.p.copy()
            q = self._shift(q, idx)                     # money is now gone
        else:
            q[min(idx, NB):] = 0.0                      # balance was < amount
        s = q.sum()
        # if an observation contradicts the belief entirely, don't blow up
        self.p = q / s if s > 1e-9 else self.p

    def p_success(self, amount, p=None):
        p = self.p if p is None else p
        return float(p[min(int(np.ceil(amount / self.bw)), NB):].sum())

    def forecast(self, day, slot, horizon=LOOKAHEAD):
        """Propagate the BELIEF forward. No access to the real future."""
        out, p, d, s = [], self.p.copy(), day, slot
        for _ in range(horizon):
            s += 1
            if s >= SLOTS_PER_DAY:
                s, d = 0, d + 1
            if d >= DAYS:
                break
            p = self.step(p, d, s)
            out.append(p)
        return out


# ---------------------------------------------------------------- policy ----
def index_score(p_now, p_later, amount, attempts_left, ltv_mult):
    """Whittle-flavoured: what do we lose by staying passive right now?"""
    value = amount * (1 + ltv_mult * (1 if attempts_left == 1 else 0))
    return value * (p_now - p_later)


def run(policy, pop, seed, ltv_mult=6.0, fair=0.0):
    rng = np.random.default_rng(seed)
    got = billed = ltv = 0.0
    dead = nman = att = ok = 0
    per_m = defaultdict(lambda: [0.0, 0.0])
    cap = BASELINE_MAX if policy == "baseline" else NPCI_MAX
    viol = 0

    for c in pop:
        bal = balance_trace(c, rng)
        st = [dict(**m, n=0, done=False, dead=False) for m in c["mandates"]]
        nman += len(st)
        for m in st:
            billed += m["amount"]; per_m[m["merchant"]][1] += m["amount"]
        drained = 0.0
        pay_slot = c["payday"] * SLOTS_PER_DAY

        # what the POLICY believes - noisy, not the truth
        est_sal = c["salary"] * rng.uniform(0.7, 1.3)
        est_pay = int(np.clip(c["payday"] + rng.integers(-1, 2), 0, DAYS - 1))

        if policy in ("solo_shared", "portfolio"):
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
            true_avail = max(bal[t] - drained, 0.0)

            if policy == "baseline":
                chosen = [m for m in live
                          if t - m["due_day"] * SLOTS_PER_DAY == m["n"]]

            elif policy == "oracle":
                sc = []
                for m in live:
                    p_now = 1.0 if true_avail >= m["amount"] else 0.0
                    fut = bal[t + 1:min(t + 1 + LOOKAHEAD, T)]
                    p_lat = (1.0 if any(max(f - drained, 0) >= m["amount"]
                                        for f in fut) else 0.0) if cap - m["n"] > 1 else 0.0
                    sc.append((index_score(p_now, p_lat, m["amount"],
                                           cap - m["n"], ltv_mult), m))
                sc.sort(key=lambda x: -x[0])
                chosen, budget = [], true_avail
                for s_, m in sc:
                    if s_ > 0 and m["amount"] <= budget:
                        chosen.append(m); budget -= m["amount"]

            else:
                sc = []
                for m in live:
                    b = beliefs[id(m)]
                    key = id(b)
                    if key not in fc_cache:
                        fc_cache[key] = b.forecast(day, slot)
                    p_now = b.p_success(m["amount"])
                    p_lat = (max([b.p_success(m["amount"], pp)
                                  for pp in fc_cache[key]], default=0.0) * 0.92
                             if cap - m["n"] > 1 else 0.0)
                    s_ = index_score(p_now, p_lat, m["amount"], cap - m["n"], ltv_mult)
                    if fair > 0:
                        s_ += fair * m["amount"] * (m["n"] == 0) * (day / DAYS)
                    sc.append((s_, p_now, m))
                sc.sort(key=lambda x: -x[0])

                if policy == "portfolio":
                    # ONE scheduler: knows the other mandates exist and that a
                    # success drains the shared balance. Budget from BELIEF.
                    chosen = []
                    exp_bal = float((beliefs[id(st[0])].p *
                                     beliefs[id(st[0])].centers).sum())
                    for s_, p_now, m in sc:
                        if s_ <= 0:
                            continue
                        if m["amount"] <= exp_bal:
                            chosen.append(m); exp_bal -= m["amount"]
                else:
                    # each merchant decides alone; no knowledge of the others
                    chosen = [m for s_, p_now, m in sc if s_ > 0]

            rng.shuffle(chosen)
            for m in chosen:
                if m["n"] >= cap:
                    viol += 1
                    continue
                m["n"] += 1; att += 1
                success = max(bal[t] - drained, 0.0) >= m["amount"]
                if success:
                    ok += 1
                    m["done"] = True; drained += m["amount"]
                    got += m["amount"]; per_m[m["merchant"]][0] += m["amount"]
                elif m["n"] >= cap:
                    m["dead"] = True; dead += 1; ltv += m["amount"] * ltv_mult
                if id(m) in beliefs:
                    beliefs[id(m)].observe(m["amount"], success)

    rates = [v[0] / v[1] for v in per_m.values() if v[1] > 0]
    return dict(rec=got / billed,
                approval=(ok / att if att else 0.0),
                death=dead / nman, apm=att / nman,
                net=(got - ltv) / billed,
                worst=min(rates) if rates else 0.0,
                violations=viol)
