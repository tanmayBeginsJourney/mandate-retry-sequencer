"""
WORLD v2. Minimal changes to sim4's world, made ONLY to test horizon effects.

WHAT CHANGED AND WHY
  1. RECURRING PAYDAY. sim4 injects salary once, on one day in a 30-day window.
     Extending sim4's horizon to 90 days would give a customer one payday in
     three months, which is not a longer horizon - it is a different and much
     poorer world. Here salary arrives every `cycle_days` days.
  2. CONFIGURABLE HORIZON. sim4 hardcodes DAYS=30 at module scope.
  3. MANDATES STILL DUE IN CYCLE 1. So a longer horizon gives mandates time to
     RESOLVE, rather than giving policies more mandates to work with. This is
     the specific thing the 30-day window was censoring.
  4. SPEND CURVE IS PER-CYCLE. Front-loaded after each payday, same exp(-0.42d)
     shape as sim4, so within one cycle this world is equivalent to sim4's.

WHAT DID NOT CHANGE
  Salary distribution, mandate amounts, spend multiplier semantics, the
  censored-observation Belief update, the index formula, the 0.92 discount.
  Those are carried over unchanged so that horizon is the only moving part.

STILL NOT MODELLED (needs the rewrite, not this file):
  24h commit-ahead, one pending notification, peak hours, first presentation,
  decline codes, sub-day timing.
"""
import numpy as np

SLOTS_PER_DAY = 3
NB = 80
LOOKAHEAD = 15
NPCI_MAX, BASELINE_MAX = 4, 3


def make_pop(n, k, rng, days, cycle_days=30, spend=0.80, amt_frac=0.045):
    pop = []
    for _ in range(n):
        payday = 0 if rng.random() < 0.60 else int(rng.integers(25, 30))
        salary = float(rng.lognormal(np.log(19000), 0.55))
        mandates = [dict(merchant=int(m),
                         amount=float(np.clip(round(salary * amt_frac *
                                                    rng.uniform(0.7, 1.3), -1), 99, 15000)),
                         due_day=int(rng.integers(0, cycle_days - 8)))
                    for m in rng.choice(60, size=k, replace=False)]
        pop.append(dict(payday=payday, salary=salary,
                        spend=float(np.clip(rng.normal(spend, 0.10), 0.55, 1.15)),
                        mandates=mandates, days=days, cycle_days=cycle_days))
    return pop


def spend_weights(payday, days, cycle_days):
    """Front-loaded spending after each payday, repeating every cycle."""
    d = (np.arange(days) - payday) % cycle_days
    w = np.exp(-0.42 * d)
    # normalise so each CYCLE spends `spend` x salary, not each horizon
    n_cycles = days / cycle_days
    return w / w.sum() * n_cycles


def balance_trace(c, rng):
    days, cyc = c["days"], c["cycle_days"]
    T = days * SLOTS_PER_DAY
    bal = np.zeros(T)
    b = c["salary"] * rng.uniform(0.0, 0.06)
    daily = c["salary"] * c["spend"] * spend_weights(c["payday"], days, cyc)
    for d in range(days):
        if d % cyc == c["payday"] % cyc:
            b += c["salary"]
        for s in range(SLOTS_PER_DAY):
            b = max(b - daily[d] / SLOTS_PER_DAY * rng.uniform(0.4, 1.6), 0.0)
            bal[d * SLOTS_PER_DAY + s] = b
    return bal


class Belief:
    """Carried over from sim4 unchanged except for the recurring spend curve."""

    def __init__(self, est_salary, est_payday, days, cycle_days, est_spend=0.80):
        self.days, self.cyc = days, cycle_days
        self.hi = 2.5 * est_salary
        self.bw = self.hi / NB
        self.centers = (np.arange(NB) + 0.5) * self.bw
        self.p = np.zeros(NB)
        self.p[:max(1, int(0.08 * est_salary / self.bw))] = 1.0
        self.p /= self.p.sum()
        self.daily = est_salary * est_spend * spend_weights(est_payday, days, cycle_days)
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
        if day % self.cyc == self.est_payday % self.cyc and slot == 0:
            p = self._shift(p, -int(self.est_salary / self.bw))
        drain = self.daily[min(day, self.days - 1)] / SLOTS_PER_DAY
        p = self._shift(p, int(round(drain / self.bw)))
        p = np.convolve(p, self._k, mode="same")
        s = p.sum()
        return p / s if s > 0 else np.ones(NB) / NB

    def advance(self, day, slot):
        self.p = self.step(self.p, day, slot)

    def observe(self, amount, success):
        idx = int(np.ceil(amount / self.bw))
        q = self.p.copy()
        if success:
            q[:min(idx, NB)] = 0.0
            s = q.sum()
            q = q / s if s > 0 else self.p.copy()
            q = self._shift(q, idx)
        else:
            q[min(idx, NB):] = 0.0
        s = q.sum()
        self.p = q / s if s > 1e-9 else self.p

    def p_success(self, amount, p=None):
        p = self.p if p is None else p
        return float(p[min(int(np.ceil(amount / self.bw)), NB):].sum())

    def forecast(self, day, slot, horizon=LOOKAHEAD):
        out, p, d, s = [], self.p.copy(), day, slot
        for _ in range(horizon):
            s += 1
            if s >= SLOTS_PER_DAY:
                s, d = 0, d + 1
            if d >= self.days:
                break
            p = self.step(p, d, s)
            out.append(p)
        return out


def index_score(p_now, p_later, amount, attempts_left, ltv_mult):
    value = amount * (1 + ltv_mult * (1 if attempts_left == 1 else 0))
    return value * (p_now - p_later)
