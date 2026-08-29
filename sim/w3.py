"""
WORLD v3.

Changes from sim4, each made because a test in TEST_DESIGN.md could not
otherwise be written:

  SLOTS_PER_DAY 3 -> 24. Peak windows (10:00-13:00, 17:00-21:30) are now
      representable, so M2 can actually trip. At 3 slots/day the peak-hour
      gate was unfalsifiable.
  Recurring payday every `cycle_days`. sim4 injected salary once in 30 days,
      so extending the horizon produced a poorer world rather than a longer
      one, and the horizon dominated every result.
  Multi-cycle mandates. A mandate bills every cycle. A cycle CLOSES when the
      next one opens, so cycles resolve inside the window and the primary
      metric is not censored. This replaces the 30-day censoring bodge.
  Top-up process. A failed attempt may prompt the customer to top up, with
      probability `topup_p`. sim4 had no top-up at any timescale, which made
      the baseline's rapid retries fail by construction (A1).
  Decline codes. success / Z9 (no funds) / technical (U28,U30). Technical
      declines may auto-represent under the existing notification; Z9 may not.
      Without this the notification constraint has nothing to bite on.
  `drained` resets each cycle. In sim4 it accumulated for the whole horizon, so
      a collection on day 2 suppressed the balance on day 28, after payday.

Deliberately still out of scope, and stated so no one mistakes it for modelled:
  hour-of-day bank success curves (salary lands at hour 0, so within a day
  earlier is always weakly better and hour choice carries no information);
  merchant heterogeneity beyond amount; mandate abuse.
"""
import numpy as np

HOURS = 24
PEAK = set(range(10, 13)) | set(range(17, 22))     # 10:00-13:00, 17:00-21:30
LEGAL_HOURS = [h for h in range(HOURS) if h not in PEAK]
DECISION_HOUR = 8
NB = 90
NPCI_MAX = 4

Z9, TECH, OK = "Z9", "TECH", "OK"

# The fitted BeliefPD configuration. NOT an invented constant: every value was
# selected by sim/fit_belief.py, by outcome, on TRAINING populations 600-607 --
# the same 800 customers the ML baseline was allowed to fit itself to -- and
# reported on populations it had never seen. The three values replace three
# hand-set ones that had never been checked against anything:
#
#   stride       3 -> 1        the old grid [0,3,...,27] left 26% of
#                              customers with no representable true payday
#   prior        exp(-0.10d) -> a window of half-width 12 around est_payday
#                              with 8x weight on hypothesis 0 and a SOFT
#                              floor of 0.25 outside it
#   spend_beta   0.045 -> 0.0   the hand-derived cross-mandate correction
#                              turns out to be worth less than nothing
#
# THE FLOOR IS THE IMPORTANT PART, and it is there because the first fit was
# wrong. Selecting at payday_err=7 alone gave a hard window of half-width 7,
# which looked superb (+15.37 pts) and was BRITTLE: at payday_err=14 the true
# payday falls outside the window, gets weight 1e-6, and can never be
# recovered -- that configuration measured -4.85 pts, i.e. actively WORSE than
# the filter it replaced. The gain peaked exactly at the operating point it
# had been fitted on. Re-selected on training populations against the MEAN
# across payday_err in {1,3,5,7,10,14}, because the true operating point is
# not known. See NOTES.md, 28 August 2026.
#
# Gate S4 holds this, paired with the `ignore_bcfg` mutant so the gate goes
# red if the plumbing that applies it is ever broken.
FITTED_BELIEF = dict(stride=1, prior_w=12, prior_day0=8.0,
                     prior_floor=0.25, spend_beta=0.0)


def make_pop(n, k, rng, days=120, cycle_days=30, spend=0.80, amt_frac=0.045,
             payday_day0_frac=0.60, irregular_frac=0.0, n_credits=6):
    pop = []
    for _ in range(n):
        payday = 0 if rng.random() < payday_day0_frac else int(rng.integers(1, cycle_days))
        salary = float(rng.lognormal(np.log(19000), 0.55))
        mandates = [dict(merchant=int(m),
                         amount=float(np.clip(round(salary * amt_frac *
                                                    rng.uniform(0.7, 1.3), -1), 99, 15000)),
                         due_day=int(rng.integers(0, cycle_days)))
                    for m in rng.choice(60, size=k, replace=False)]
        irregular = bool(rng.random() < irregular_frac)
        credit_days = (sorted(int(x) for x in rng.integers(0, cycle_days, n_credits))
                       if irregular else [payday])
        pop.append(dict(payday=payday, irregular=irregular,
                        credit_days=credit_days, n_credits=n_credits,
                        salary=salary,
                        spend=float(np.clip(rng.normal(spend, 0.10 * spend), 0.4 * spend, 1.6 * spend)),
                        mandates=mandates, days=days, cycle_days=cycle_days))
    return pop


DEFAULT_DECAY = 0.42


def hourly_spend_profile(cycle_days, decay=DEFAULT_DECAY):
    """Front-loaded after payday, same exp(-0.42d) shape as sim4, per cycle.

    `decay` used to be the literal 0.42, hardcoded here. It is now a parameter
    so the misspecification study can move the WORLD off it.

    *** THE BELIEFS MUST NEVER BE GIVEN A SHIFTED VALUE. *** Both Belief and
    BeliefPD call this with the default on purpose. The experiment asks what
    happens when the filter's assumption about the world is wrong; handing the
    filter the new value makes it correctly specified again and turns the whole
    study into a no-op. Only balance_trace takes an override.
    """
    d = np.arange(cycle_days)
    w = np.exp(-decay * d)
    return w / w.sum()


def balance_trace(c, rng, decay=None, p_missed_credit=0.0):
    """True balance at every hour. Salary lands at hour 0 of payday.

    `decay` shifts the WORLD's spend curve away from what the beliefs assume.
    None means "unchanged", and every existing caller passes nothing, so this
    is inert by default -- gate T9 confirms it.

    `p_missed_credit` is W2 (docs/04_BUILD_PLAN.md): the per-customer,
    per-cycle probability that the salary credit does not arrive at all. It is
    what makes a billing cycle genuinely UNCOLLECTABLE, and until it existed
    every cycle in this world was winnable on some day -- the oracle scored
    100% at every calibration tested, so the agent was solving a pure timing
    problem and never a collectability one.

    ⚠️ **INERT AT 0.0 AND THAT IS LOAD-BEARING.** The draw is guarded, so at
    the default this function consumes nothing extra from `rng` and every
    number measured before W2 is unchanged. Removing the guard would shift the
    shared RNG stream and silently move every historical result; gate T9
    catches that, and it should never have to.
    """
    days, cyc = c["days"], c["cycle_days"]
    T = days * HOURS
    bal = np.zeros(T)
    b = c["salary"] * rng.uniform(0.0, 0.06)
    prof = hourly_spend_profile(cyc, DEFAULT_DECAY if decay is None else decay)
    # One draw per cycle, taken up front so the stream position does not depend
    # on which days happen to be paydays.
    missed = (rng.random(days // cyc + 2) < p_missed_credit
              if p_missed_credit > 0 else None)
    for d in range(days):
        phase = (d - c["payday"]) % cyc
        # Which cycle of THIS customer's salary calendar d falls in. Used only
        # to index `missed`; >= 0 for every d at which a credit can land.
        ci = max(0, (d - c["payday"]) // cyc)
        skip = missed is not None and missed[ci]
        if c.get("irregular"):
            share = c["salary"] / len(c["credit_days"])
            if not skip:
                b += share * sum(1 for cd in c["credit_days"] if cd == d % cyc)
        elif phase == 0 and not skip:
            b += c["salary"]
        day_spend = c["salary"] * c["spend"] * prof[phase]
        for h in range(HOURS):
            b = max(b - day_spend / HOURS * rng.uniform(0.4, 1.6), 0.0)
            bal[d * HOURS + h] = b
    return bal


# ------------------------------------------------------------------ belief ---
class Belief:
    """
    Bayes filter over the hidden balance. Advances once per DAY; observations
    arrive at any hour. Daily granularity is deliberate: the scheduler commits
    24h ahead, so it can only ever choose a DAY, never an hour.

    `pop_info` controls what aggregate knowledge the policy is allowed:
      True  - population-fitted spend curve and a payday estimate (Tier 2 of
              the data-governance ladder: aggregate, non-identifying)
      False - no aggregate model. Uniform spend, salary arrival smeared
              uniformly over the cycle. What a merchant with no scale sees.
    """

    def __init__(self, est_salary, est_payday, cycle_days, days,
                 est_spend=0.80, pop_info=True):
        self.cyc, self.days = cycle_days, days
        self.hi = 2.5 * est_salary
        self.bw = self.hi / NB
        self.centers = (np.arange(NB) + 0.5) * self.bw
        self.p = np.zeros(NB)
        self.p[:max(1, int(0.08 * est_salary / self.bw))] = 1.0
        self.p /= self.p.sum()
        self.est_salary, self.est_payday = est_salary, est_payday
        self.est_spend = est_spend
        self.pop_info = pop_info
        if pop_info:
            self.prof = hourly_spend_profile(cycle_days)
        else:
            self.prof = np.ones(cycle_days) / cycle_days
        self._k = np.array([0.12, 0.76, 0.12])
        # Rolling forecast; see forecast(). Consecutive days starting at
        # (last advanced day + 1). Empty means "no valid rollout".
        self._fc = []

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

    def step(self, p, day):
        """Advance the belief one day. Never sees the true balance."""
        if self.pop_info:
            if (day - self.est_payday) % self.cyc == 0:
                p = self._shift(p, -int(self.est_salary / self.bw))
        else:
            # payday unknown: smear 1/cycle of the salary in every day
            p = self._shift(p, -int(self.est_salary / self.cyc / self.bw))
        phase = (day - self.est_payday) % self.cyc if self.pop_info else day % self.cyc
        drain = self.est_salary * self.est_spend * self.prof[phase]
        p = self._shift(p, int(round(drain / self.bw)))
        p = np.convolve(p, self._k, mode="same")
        s = p.sum()
        return p / s if s > 0 else np.ones(NB) / NB

    def advance(self, day):
        # If yesterday's forecast already computed step(self.p, day), and
        # nothing has touched self.p since, reuse that array. This is not an
        # approximation of the recompute -- it IS the recompute: same method,
        # same input array, same floats, so the result is bit-identical.
        # observe() clears the rollout precisely because it changes self.p.
        if self._fc and self._fc[0][0] == day:
            self.p = self._fc[0][1]
            self._fc = self._fc[1:]
            return
        self._fc = []
        self.p = self.step(self.p, day)

    def observe(self, amount, success):
        self._fc = []          # state changes here, so the rollout is stale
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

    def forecast(self, day, horizon_days):
        """Propagate belief forward in DAYS. Structurally cannot see the future:
        takes no argument that carries ground truth.

        INCREMENTAL, AND BIT-IDENTICAL TO THE NON-INCREMENTAL VERSION.
        The rollout from `day` is step(p, day+1), then step of that at day+2,
        and so on. Tomorrow's rollout from day+1 is the same chain with its
        first element removed and one new step appended -- provided self.p has
        not changed, which advance() guarantees by CONSUMING the first element
        rather than recomputing it, and which observe() breaks by clearing the
        cache. So every array handed back was produced by exactly the same
        sequence of step() calls as before, on exactly the same inputs. The
        floats are identical by construction, not by luck. Gate T9 checks this
        against a reference captured before any of it existed.

        Measured effect: for solo_pop_pd, 89% of forecast calls follow no new
        observation, so 11 of their 12 steps disappear and advance() is free.
        """
        out = self._fc
        if out and out[0][0] != day + 1:
            out = []                       # not aligned with this day: rebuild
        keep = min(horizon_days, max(0, self.days - (day + 1)))
        if len(out) > keep:
            out = out[:keep]
        p = out[-1][1] if out else self.p
        for i in range(len(out) + 1, horizon_days + 1):
            if day + i >= self.days:
                break
            p = self.step(p, day + i)
            out = out + [(day + i, p)]
        self._fc = out
        return list(out)

    def expected(self):
        return float((self.p * self.centers).sum())


def index_score(p_now, p_later, amount, discount=0.92):
    """Whittle-style index: value now, minus discounted value of waiting.

    THE 6x LTV MULTIPLIER WAS REMOVED 28 August 2026, after being swept.
    It used to read
        value = amount * (1 + ltv_mult * (1 if attempts_left == 1 else 0))
    and `01_FACTS.md` and `02_RESULTS.md` both already described it as "no
    longer used" while it was still live -- an invented constant sitting on the
    index with no sensitivity analysis, breaking rules 5 and 6.

    Sweeping ltv_mult over {0, 1, 6, 20} changed NOTHING, for any policy:

        solo_shared_pd 83.24 83.24 83.24 83.24    portfolio_pd 78.86 x4
        solo_pop_pd    69.55 x4                   portfolio    53.01 x4
        payday_wait    63.42 x4                   myopic       51.59 x4

    The reason is structural. `value` is strictly positive, so it cannot change
    the SIGN of the index, and non-budgeted policies commit every mandate whose
    score is positive regardless of rank. It can only reorder, and only on the
    final attempt of a cycle -- which never changed a budgeted policy's outcome
    either. So the multiplier was dead code on the headline result, and the
    cycle-based metric prices mandate death on its own: a dead mandate forfeits
    its remaining cycles because they still count in `cyc_due`.

    `discount` is a DIFFERENT matter and is NOT dead -- see the sweep in
    NOTES.md, 28 August. It changes the sign, so it is live on every index
    policy. It sits on a broad plateau (0.90-0.96) rather than a tuned peak,
    and the argmax moves between population sets, but it remains a
    hand-chosen constant and every headline is reported as a range over it.
    """
    return amount * (p_now - discount * p_later)


class BeliefPD:
    """
    Belief with a POSTERIOR OVER PAYDAY, not a point estimate.

    Motivation, from the payday_err sweep: every policy was handed the payday
    to +/-1 day for free, and the whole result collapsed when that was taken
    away. The original Belief keeps a distribution over the balance but a
    single fixed number for the payday - so it cannot learn the one variable
    that turns out to dominate.

    Structure: a mixture over `cycle_days` payday hypotheses. Each component
    carries its own balance distribution and is advanced under its own payday
    assumption. Each observation reweights the components by how well they
    predicted it. A failure at Rs X is evidence against every hypothesis that
    expected the customer to be flush today.

    This is why pooling should matter: one merchant sees one debit per month
    and can barely move this posterior. An aggregator sees several per month
    on the same account, so the payday posterior sharpens far faster.
    """

    def __init__(self, est_salary, est_payday, cycle_days, days,
                 est_spend=0.80, pop_info=True, stride=3,
                 prior_w=None, prior_day0=1.0, prior_floor=1e-6):
        """
        THE THREE FITTABLE HANDICAPS (all default to the original values, so an
        unconfigured BeliefPD is bit-identical and gate T9 stays exact).

        `stride`   was 3 -- a compute hack from the research phase. On a
                   30-day cycle it yields hypotheses [0,3,...,27], so only 74%
                   of customers have a representable true payday and only 31.7%
                   of those NOT paid on day 0 do. stride=1 removes that.
        `prior_w`  None keeps the original invented prior exp(-0.10 d). An
                   integer switches to a uniform window of that half-width
                   around est_payday, which is the shape the estimator's own
                   noise implies: est_pay - payday IS the injected noise, so
                   mass beyond the noise width is spent on the impossible.
        `prior_day0` extra weight on hypothesis 0. The population puts a large
                   spike at day 0, so P(payday=0 | est_pay) is far above
                   uniform even a few days away.

        These are fitted on TRAINING populations by outcome only -- never from
        c["payday"], c["salary"] or c["spend"]. See NOTES.md, 28 August.
        """
        self.cyc, self.days = cycle_days, days
        self.hi = 2.5 * est_salary
        self.bw = self.hi / NB
        self.centers = (np.arange(NB) + 0.5) * self.bw
        self.est_salary, self.est_spend = est_salary, est_spend
        self.hyp = list(range(0, cycle_days, stride))
        # prior: broad, gently centred on the population's payday guess
        d = np.array([min(abs(h - est_payday), cycle_days - abs(h - est_payday))
                      for h in self.hyp], dtype=float)
        if prior_w is None:
            self.w = np.exp(-0.10 * d)          # the original, unfitted prior
        else:
            # `prior_floor` is the weight kept OUTSIDE the window. At 1e-6
            # the window is effectively hard, which is why a prior fitted at
            # payday_err=7 goes actively harmful at payday_err=14: the true
            # payday falls outside and can never be recovered. A soft floor
            # lets evidence pull it back. See NOTES.md, 28 August.
            self.w = np.where(d <= prior_w, 1.0, prior_floor)
            if prior_day0 != 1.0:
                self.w = self.w * np.where(np.asarray(self.hyp) == 0,
                                           prior_day0, 1.0)
        self.w /= self.w.sum()
        p0 = np.zeros(NB)
        p0[:max(1, int(0.08 * est_salary / self.bw))] = 1.0
        p0 /= p0.sum()
        self.P = np.tile(p0, (len(self.hyp), 1))
        self.prof = hourly_spend_profile(cycle_days)
        self._k = np.array([0.12, 0.76, 0.12])
        self._fc = []          # rolling forecast; see Belief.forecast()

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

    def _step_one(self, p, day, h):
        if (day - h) % self.cyc == 0:
            p = self._shift(p, -int(self.est_salary / self.bw))
        phase = (day - h) % self.cyc
        drain = self.est_salary * self.est_spend * self.prof[phase]
        p = self._shift(p, int(round(drain / self.bw)))
        p = np.convolve(p, self._k, mode="same")
        s = p.sum()
        return p / s if s > 0 else np.ones(NB) / NB

    def advance(self, day):
        # See Belief.advance -- the first entry of yesterday's rollout IS
        # step(self.P, day), so reuse it rather than recomputing it.
        if self._fc and self._fc[0][0] == day:
            self.P = self._fc[0][1]
            self._fc = self._fc[1:]
            return
        self._fc = []
        self.P = np.array([self._step_one(self.P[i], day, h)
                           for i, h in enumerate(self.hyp)])

    def _pj(self, amount, P=None):
        P = self.P if P is None else P
        i = min(int(np.ceil(amount / self.bw)), NB)
        return P[:, i:].sum(axis=1)

    def observe(self, amount, success):
        self._fc = []          # state changes here, so the rollout is stale
        pj = self._pj(amount)
        lik = pj if success else (1.0 - pj)
        w = self.w * np.maximum(lik, 1e-6)
        s = w.sum()
        if s > 1e-12:
            self.w = w / s                    # reweight payday hypotheses
        idx = min(int(np.ceil(amount / self.bw)), NB)
        newP = []
        for i in range(len(self.hyp)):
            q = self.P[i].copy()
            if success:
                q[:idx] = 0.0
                t = q.sum()
                q = q / t if t > 0 else self.P[i].copy()
                q = self._shift(q, idx)
            else:
                q[idx:] = 0.0
            t = q.sum()
            newP.append(q / t if t > 1e-9 else self.P[i])
        self.P = np.array(newP)

    def p_success(self, amount, P=None):
        return float(np.dot(self.w, self._pj(amount, P)))

    def forecast(self, day, horizon_days):
        """Incremental. See Belief.forecast for why this is bit-identical.

        This is the hot path of the whole suite: profiling one solo_shared_pd
        run showed forecast at 53% of runtime against advance at 29%, and
        1,099,340 of 1,699,340 _step_one calls (81.7% for solo_pop_pd, where
        the forecast is recomputed per mandate rather than once per hour).
        """
        out = self._fc
        if out and out[0][0] != day + 1:
            out = []
        keep = min(horizon_days, max(0, self.days - (day + 1)))
        if len(out) > keep:
            out = out[:keep]
        P = out[-1][1] if out else self.P
        for i in range(len(out) + 1, horizon_days + 1):
            if day + i >= self.days:
                break
            P = np.array([self._step_one(P[j], day + i, h)
                          for j, h in enumerate(self.hyp)])
            out = out + [(day + i, P)]
        self._fc = out
        return list(out)

    def expected(self):
        return float(np.dot(self.w, (self.P * self.centers).sum(axis=1)))

    def posterior_summary(self):
        """(entropy of the payday posterior, top hypothesis weight, E[balance]).

        These are what the hybrid hands to the GBDT. Entropy and top-weight are
        the filter's own statement of how sure it is about payday -- the thing
        a model with no structural prior cannot compute for itself, and the
        thing that survives a change in the population.
        """
        w = np.clip(self.w, 1e-12, None)
        ent = float(-(w * np.log(w)).sum())
        return ent, float(self.w.max()), self.expected()
