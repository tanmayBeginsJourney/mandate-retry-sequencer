"""
HARNESS v3.

Constraint checking is INDEPENDENT of policy selection. Policies propose
commitments; the harness re-derives legality at dispatch from the recorded
state and counts violations. That is what makes the counters falsifiable: a
policy that filters its own choices cannot make the counter zero by
construction, because the counter is computed by a different piece of code
from a different source of truth.

INFORMATION CONDITIONS (all use identical index maths; only information moves)

  baseline_doc   Razorpay's documented UPI schedule: attempt, then again ~1h
                 and ~2h later, same day, 3 attempts. Issues no fresh
                 notification per retry. Reported WITH its violation count -
                 under the one-notification-per-business-decline rule this
                 schedule is not executable for Z9 declines.
  baseline_legal Same 3 attempts, one per day, each properly notified.
  payday_wait    Wait for the estimated payday, then one attempt per day.
                 No belief filter, no index, no pooling. The competitive
                 baseline: what a good rival team builds in an afternoon.
  explore        Uniformly random legal day inside the remaining cycle
                 window, under the SAME Stage 0 constraints as every other
                 policy. NOT a candidate policy and NOT a baseline: it exists
                 only to generate an unbiased training set for the ML
                 ablation, which is why it is in COMPLIANT (so T1/T7/T8 prove
                 it is Stage-0 clean) but deliberately NOT in BELIEF_POLS or
                 POOLED. It carries no belief at all.
  myopic         Pooled belief + budget, index = amount * p_now. No forecast,
                 no passive action. Isolates whether Whittle structure earns
                 its keep over greedy.
  solo_naive     Own observations, NO aggregate model (uniform spend, payday
                 unknown). A merchant with no scale.
  solo_pop       Own observations + population-fitted spend/payday curves.
                 Governance Tier 2: aggregate, non-identifying.
  solo_placebo   NEGATIVE CONTROL. Same observation schedule as solo_shared,
                 but cross-mandate observations carry outcomes computed
                 against a DIFFERENT customer's balance. Identical mechanics,
                 identical count, wrong information.
  solo_shared    Own + other mandates' real observations. Governance Tier 1.
  portfolio      Pooled observations AND coordinated budget.
  oracle         True balance and true future. Must weakly dominate all.

  solo_pop -> solo_shared  : value of the per-customer cross-merchant join
  solo_shared vs solo_placebo : whether that value is information or artefact
  solo_naive -> solo_pop   : value of aggregate population learning (Tier 2)
"""
import numpy as np
from collections import defaultdict
import w3
import mlfeat
from w3 import HOURS, PEAK, DECISION_HOUR, NPCI_MAX, Z9, TECH, OK

POOLED = ("solo_shared", "solo_placebo", "portfolio", "myopic",
          "solo_shared_pd", "portfolio_pd", "solo_placebo_pd")
BELIEF_POLS = ("solo_naive", "solo_pop", "solo_shared", "solo_placebo",
               "portfolio", "myopic",
               "solo_pop_pd", "solo_shared_pd", "portfolio_pd", "solo_placebo_pd")
COMPLIANT = ("baseline_legal", "payday_wait", "explore", "ml_index",
             "myopic", "solo_naive", "solo_pop", "solo_shared",
             "solo_placebo", "portfolio", "oracle", "solo_pop_pd",
             "solo_shared_pd", "portfolio_pd", "solo_placebo_pd")

P_TECH = 0.008          # technical declines <1% (NPCI CEO, via wire coverage)
LOOKAHEAD_DAYS = 12


def cap_for(pol):
    return 3 if pol.startswith("baseline") else NPCI_MAX


def earliest_legal(day, min_t):
    """First legal (non-peak) hour on `day` at or after absolute time min_t."""
    for h in w3.LEGAL_HOURS:
        t = day * HOURS + h
        if t >= min_t:
            return t
    return None


class Violations:
    __slots__ = ("cap", "peak", "lead", "pending", "represent")

    def __init__(self):
        self.cap = self.peak = self.lead = self.pending = self.represent = 0

    def total(self):
        return self.cap + self.peak + self.lead + self.pending + self.represent

    def asdict(self):
        return dict(cap=self.cap, peak=self.peak, lead=self.lead,
                    pending=self.pending, represent=self.represent)


def run(policy, pop, seed, topup_p=0.0, topup_lag=2,
        topup_life=48, topup_mult=1.15, discount=0.92, payday_err=1,
        cap_override=None, collect_calib=False, mutate=None, pop_spend=0.80,
        n_mandates_hint=None, collect_ml=False, ml_predict=None,
        spend_decay=None):
    """
    `mutate` injects a deliberate defect, used only by the mutation tests:
      'cap'       -> attempt a 5th time in a cycle
      'peak'      -> dispatch inside a peak hour
      'lead'      -> dispatch <24h after notification
      'pending'   -> issue a second pending notification
      'represent' -> re-present a Z9 decline under the old notification
      'leak_bal'  -> feed the belief the true balance
      'weak_oracle' -> restore the deferral bug found in the audit

    `collect_ml` records one training row per dispatched attempt: the feature
    vector as it was AT THE MOMENT OF THE DECISION, paired with the outcome.
    Features are never reconstructed after the fact -- see sim/mlfeat.py.

    `ml_predict` is a callable(list_of_feature_rows) -> probabilities, used by
    the `ml_index` policy. The harness never imports a model library.

    `spend_decay` overrides the WORLD's spend-curve decay (w3 default 0.42).
    It is passed to w3.balance_trace ONLY, never to a belief: the whole point
    of the misspecification study is that the filter keeps believing 0.42
    while the world does something else. Passing it to the beliefs would make
    the filter correctly specified again and the experiment a no-op.
    """
    rng = np.random.default_rng(seed)
    trng = np.random.default_rng(seed + 777)
    # `explore` gets its OWN generator so that adding the policy cannot shift
    # a single draw taken by any existing policy. Created unconditionally and
    # never touched unless policy == "explore".
    erng = np.random.default_rng(seed + 4242)
    days, cyc = pop[0]["days"], pop[0]["cycle_days"]
    T = days * HOURS
    cap = cap_override or cap_for(policy)
    V = Violations()

    ml_rows = []
    cyc_due = cyc_got = 0
    ledger = defaultdict(int)      # (mandate identity, cycle) -> attempts.
                                   # Written only by the harness at dispatch.
    n_att = n_ok = 0
    n_mand = n_alive = n_starved = 0
    open_at_end = 0
    calib = []

    donors = list(range(len(pop)))
    rng.shuffle(donors)

    for ci, c in enumerate(pop):
        bal = w3.balance_trace(c, rng, decay=spend_decay)
        donor_bal = w3.balance_trace(pop[donors[ci]], np.random.default_rng(seed + 31 * ci))
        topups = np.zeros(T + topup_lag + topup_life + 2)

        est_sal = c["salary"] * rng.uniform(0.7, 1.3)
        est_pay = int((c["payday"] + rng.integers(-payday_err, payday_err + 1)) % cyc)

        mands = []
        for _mi, m in enumerate(c["mandates"]):
            mands.append(dict(
                amount=m["amount"], due_day=m["due_day"], merchant=m["merchant"],
                uid=(ci, _mi), cycle=0, n=0, alive=True, collected=False,
                pend=None,          # (notif_t, target_t, prev_code_ok)
                prev_code=None, total_att=0, got_cycles=0))
        n_mand += len(mands)
        # This customer's dispatched attempts, append-only. It physically
        # cannot contain an attempt that has not happened yet, which is what
        # makes the ML features leak-free by construction rather than by
        # inspection. See sim/mlfeat.py.
        hist = []
        sum_amt = sum(m["amount"] for m in mands)

        pop_info = policy != "solo_naive"
        if policy in BELIEF_POLS:
            # a pop_info policy knows the POPULATION spend rate and how many
            # mandates compete for the balance; it never knows this customer's
            # own spend rate, balance, or future.
            eff_spend = pop_spend * (1 + (len(mands) - 1) * 0.045) if pop_info else 0.80
            BC = w3.BeliefPD if policy.endswith("_pd") else w3.Belief
            # A POOLED, non-placebo policy feeds EVERY mandate of a customer
            # the same observations in the same order: its own attempt on the
            # line below, every other mandate's through the pooling loop. The
            # beliefs start identical and are driven identically, so they stay
            # identical -- measured max|P_i - P_0| = 0.0 exactly across a full
            # run. Keeping k copies of one distribution costs k times the
            # advance() work for nothing, so keep one.
            #
            # The placebo policies are excluded and MUST stay excluded: there
            # the acting mandate gets the real outcome while the others get an
            # outcome computed against a different customer's balance, so the
            # beliefs genuinely diverge (measured max|diff| = 0.94).
            collapse = policy in POOLED and not policy.startswith("solo_placebo")
            if collapse:
                _shared = BC(est_sal, est_pay, cyc, days,
                             est_spend=eff_spend, pop_info=pop_info)
                beliefs = {id(m): _shared for m in mands}
                bobjs = [_shared]
            else:
                beliefs = {id(m): BC(est_sal, est_pay, cyc, days,
                                     est_spend=eff_spend,
                                     pop_info=pop_info) for m in mands}
                bobjs = list(beliefs.values())
        else:
            beliefs = {}
            bobjs = []
            collapse = False

        drained = defaultdict(float)     # per (mandate cycle window) -> reset each cycle

        def cycle_open(m):
            return m["due_day"] + m["cycle"] * cyc

        def cycle_close(m):
            return m["due_day"] + (m["cycle"] + 1) * cyc

        for t in range(T):
            day, hour = divmod(t, HOURS)

            if hour == 0:
                for b in bobjs:
                    b.advance(day)      # distinct objects only, never twice
                if (day - c["payday"]) % cyc == 0:
                    drained.clear()          # balance replenished at payday

            # ---- dispatch any attempt scheduled for exactly now -------------
            for m in mands:
                if not m["alive"] or m["pend"] is None:
                    continue
                notif_t, target_t, auto_ok = m["pend"]
                if target_t != t:
                    continue

                # INDEPENDENT Stage-0 re-check. Uses the harness's own ledger,
                # NOT m["n"], so a policy that corrupts its own counter is
                # still caught.
                lk = (m["uid"], m["cycle"])
                if ledger[lk] >= cap:
                    V.cap += 1
                ledger[lk] += 1
                if hour in PEAK:
                    V.peak += 1
                if notif_t is not None and target_t - notif_t < HOURS:
                    V.lead += 1
                if notif_t is None and m["prev_code"] != TECH:
                    # re-presentation without a fresh notification is only
                    # permitted for technical declines, never for Z9
                    V.represent += 1

                m["pend"] = None
                m["n"] += 1
                m["total_att"] += 1
                n_att += 1
                avail = max(bal[t] - drained["b"] + topups[t], 0.0)

                if trng.random() < P_TECH:
                    code = TECH
                    success = False
                elif avail >= m["amount"]:
                    code, success = OK, True
                else:
                    code, success = Z9, False

                if collect_calib and id(m) in beliefs:
                    calib.append((beliefs[id(m)].p_success(m["amount"]), success))

                if collect_ml and m.get("_mlf") is not None:
                    ml_rows.append((m["_mlf"], 1 if success else 0,
                                    ci, m["uid"]))
                    m["_mlf"] = None
                hist.append(dict(uid=m["uid"], day=day, amount=m["amount"],
                                 ok=bool(success), code=code))

                m["prev_code"] = code
                if success:
                    n_ok += 1
                    m["collected"] = True
                    m["got_cycles"] += 1
                    drained["b"] += m["amount"]
                else:
                    if topup_p > 0 and trng.random() < topup_p:
                        cr = m["amount"] * topup_mult
                        lo, hi = min(t + topup_lag, T), min(t + topup_lag + topup_life, T)
                        topups[lo:hi] += cr
                    if m["n"] >= cap:
                        m["alive"] = False

                # belief updates
                if policy in BELIEF_POLS:
                    beliefs[id(m)].observe(m["amount"], success)
                    # When collapsed, the line above already delivered this
                    # observation to every mandate: they share one object.
                    if policy in POOLED and not collapse:
                        for other in mands:
                            if other is m:
                                continue
                            if policy.startswith("solo_placebo"):
                                o = max(donor_bal[t], 0.0) >= m["amount"]
                            else:
                                o = success
                            beliefs[id(other)].observe(m["amount"], o)

                # technical decline may auto-represent under same notification
                if code == TECH and m["alive"] and m["n"] < cap:
                    nt = earliest_legal(day, t + 1)
                    if nt is not None and nt < cycle_close(m) * HOURS:
                        m["pend"] = (notif_t, nt, True)
                elif (policy == "baseline_doc" and not success
                      and m["alive"] and m["n"] < cap):
                    # documented UPI schedule: retry ~1h later, same day,
                    # under no fresh notification. Illegal for Z9 - counted.
                    nt = earliest_legal(day, t + 1)
                    if nt is not None and nt < cycle_close(m) * HOURS:
                        m["pend"] = (None, nt, True)
                if mutate == "represent" and code == Z9 and m["alive"] and m["n"] < cap:
                    nt = earliest_legal(day, t + 1)
                    if nt is not None:
                        m["pend"] = (None, nt, True)   # no fresh notification: illegal
                        V.represent += 1

            # ---- cycle rollover ---------------------------------------------
            if hour == 0:
                for m in mands:
                    if day >= cycle_close(m) and m["alive"]:
                        m["cycle"] += 1
                        m["n"] = 0
                        m["collected"] = False
                        m["pend"] = None
                        m["prev_code"] = None

            # ---- scheduling decision ----------------------------------------
            if hour != DECISION_HOUR:
                continue

            live = [m for m in mands
                    if m["alive"] and not m["collected"] and m["pend"] is None
                    and cycle_open(m) <= day < cycle_close(m) and m["n"] < cap]
            if not live:
                continue

            commits = []            # (mandate, target_day, notif_t or None)

            if policy == "baseline_doc":
                # documented UPI behaviour: same-day, ~1h apart, no fresh notice
                for m in live:
                    tt = earliest_legal(day, t + 1 + m["n"])
                    if tt is not None and tt < cycle_close(m) * HOURS:
                        commits.append((m, tt, None))
            elif policy == "baseline_legal":
                for m in live:
                    tt = earliest_legal(day + 1, t + HOURS)
                    if tt is not None and tt < cycle_close(m) * HOURS:
                        commits.append((m, tt, t))
            elif policy == "payday_wait":
                for m in live:
                    tgt = day + 1 + ((est_pay - (day + 1)) % cyc) if m["n"] == 0 else day + 1
                    if tgt >= cycle_close(m):
                        tgt = day + 1
                    tt = earliest_legal(tgt, t + HOURS)
                    if tt is not None and tt < cycle_close(m) * HOURS:
                        commits.append((m, tt, t))
            elif policy == "explore":
                # Uniform over the legal days still available in this cycle.
                # Same Stage 0 path as every other policy: earliest_legal()
                # supplies the non-peak hour and the >=24h notification lead
                # comes from the t + HOURS floor, exactly as in payday_wait.
                for m in live:
                    lo_d, hi_d = day + 1, cycle_close(m) - 1
                    if hi_d < lo_d:
                        continue
                    tgt = int(erng.integers(lo_d, hi_d + 1))
                    tt = earliest_legal(tgt, t + HOURS)
                    if tt is not None and tt < cycle_close(m) * HOURS:
                        if collect_ml:
                            m["_mlf"] = mlfeat.build(
                                hist, m["uid"], m["amount"], m["n"], cap,
                                day, tt // HOURS, cycle_open(m),
                                cycle_close(m), cyc, est_sal, est_pay,
                                len(mands), sum_amt - m["amount"])
                        commits.append((m, tt, t))
            elif policy == "ml_index":
                # THE CLEAN ABLATION. Identical index maths, identical
                # constraint layer, identical metric. ONLY the probability
                # engine changes.
                #
                # The candidate-day set is reproduced exactly rather than
                # approximately: Belief.forecast yields day+1 .. day+LOOKAHEAD
                # and stops at `days`, and the belief branch then drops any
                # day at or past cycle_close. Getting this wrong would make
                # ml_index a different policy and void the comparison.
                #
                # Note what is being predicted: P(success ON CANDIDATE DAY dd
                # | information at decision time) -- not P(success today).
                # mlfeat carries `offset` precisely so the model can express
                # that, mirroring p_success(amount, P) taking a FUTURE
                # posterior.
                cand_days = [day + i for i in range(1, LOOKAHEAD_DAYS + 1)
                             if day + i < days]
                rows, spans = [], []
                for m in live:
                    dd_use = [dd for dd in cand_days if dd < cycle_close(m)]
                    if not dd_use or cand_days[0] >= cycle_close(m):
                        continue
                    spans.append((m, dd_use, len(rows)))
                    for dd in dd_use:
                        rows.append(mlfeat.build(
                            hist, m["uid"], m["amount"], m["n"], cap,
                            day, dd, cycle_open(m), cycle_close(m), cyc,
                            est_sal, est_pay, len(mands),
                            sum_amt - m["amount"]))
                sc = []
                if rows:
                    allp = ml_predict(rows)
                    for m, dd_use, off in spans:
                        ps = allp[off:off + len(dd_use)]
                        p_now = float(ps[0])
                        p_lat = 0.0
                        if cap - m["n"] > 1 and len(ps) > 1:
                            p_lat = max(float(x) for x in ps[1:])
                        s_ = w3.index_score(p_now, p_lat, m["amount"], discount)
                        sc.append((s_, p_now, m, dd_use[0]))
                sc.sort(key=lambda x: -x[0])
                for s_, p_now, m, tgt_day in sc:
                    if s_ <= 0:
                        continue
                    tt = earliest_legal(tgt_day, t + HOURS)
                    if tt is not None and tt < cycle_close(m) * HOURS:
                        commits.append((m, tt, t))
            elif policy == "oracle":
                weak = (mutate == "weak_oracle")
                sc = []
                for m in live:
                    best = None
                    for dd in range(day + 1, min(cycle_close(m), days)):
                        tt = earliest_legal(dd, t + HOURS)
                        if tt is None or tt >= T:
                            continue
                        if max(bal[tt] - drained["b"], 0.0) >= m["amount"]:
                            best = tt
                            break
                    if best is None:
                        continue
                    if weak:
                        later = any(max(bal[earliest_legal(dd, t + HOURS) or 0]
                                        - drained["b"], 0.0) >= m["amount"]
                                    for dd in range(day + 2, min(cycle_close(m), days))
                                    if earliest_legal(dd, t + HOURS) is not None)
                        if later and cap - m["n"] > 1:
                            continue          # the deferral bug, restored
                    sc.append((m["amount"], m, best))
                sc.sort(key=lambda x: -x[0])
                for _, m, tt in sc:
                    commits.append((m, tt, t))
            else:
                fc_days = None
                sc = []
                for m in live:
                    b = beliefs[id(m)]
                    if mutate == "leak_bal":
                        b.p = np.zeros(w3.NB)
                        b.p[min(int(bal[t] / b.bw), w3.NB - 1)] = 1.0
                    # Reuse one forecast for every mandate ONLY when they
                    # genuinely share a belief. `collapse` is true exactly when
                    # they do, so this is now correct by construction rather
                    # than by coincidence.
                    #
                    # It used to read `policy not in POOLED`, which was right
                    # for the five non-placebo pooled policies (their beliefs
                    # are provably identical, measured max|diff| = 0.0) and
                    # WRONG for solo_placebo and solo_placebo_pd, which are
                    # also in POOLED but whose beliefs genuinely diverge
                    # (measured max|diff| = 0.94). Those two were scoring
                    # mandates 2..k off mandate 1's belief. Fixed 28 Aug 2026;
                    # it moves the placebo arms, which is why S2b and S2c
                    # change. See NOTES.md.
                    if fc_days is None or not collapse:
                        fc_days = b.forecast(day, LOOKAHEAD_DAYS)
                    p_now_l = [(dd, p) for dd, p in fc_days if dd >= day + 1]
                    if not p_now_l:
                        continue
                    tgt_day, p_tgt = p_now_l[0]
                    if tgt_day >= cycle_close(m):
                        continue
                    p_now = b.p_success(m["amount"], p_tgt)
                    if policy == "myopic":
                        s_ = m["amount"] * p_now
                    else:
                        cand = [b.p_success(m["amount"], p) for dd, p in p_now_l[1:]
                                if dd < cycle_close(m)]
                        p_lat = max(cand, default=0.0) if cap - m["n"] > 1 else 0.0
                        s_ = w3.index_score(p_now, p_lat, m["amount"], discount)
                    sc.append((s_, p_now, m, tgt_day))
                sc.sort(key=lambda x: -x[0])

                if policy in ("portfolio", "myopic", "portfolio_pd"):
                    budget = beliefs[id(mands[0])].expected() if mands else 0.0
                    for s_, p_now, m, tgt_day in sc:
                        if s_ <= 0:
                            continue
                        if m["amount"] <= budget:
                            tt = earliest_legal(tgt_day, t + HOURS)
                            if tt is not None and tt < cycle_close(m) * HOURS:
                                commits.append((m, tt, t))
                                budget -= m["amount"]
                else:
                    for s_, p_now, m, tgt_day in sc:
                        if s_ <= 0:
                            continue
                        tt = earliest_legal(tgt_day, t + HOURS)
                        if tt is not None and tt < cycle_close(m) * HOURS:
                            commits.append((m, tt, t))

            # ---- apply mutations, then commit -------------------------------
            for m, tt, notif in commits:
                if mutate == "peak":
                    tt = (tt // HOURS) * HOURS + 11        # force a peak hour
                if mutate == "lead":
                    notif = tt - 1                          # 1h lead, not 24h
                if mutate == "cap":
                    m["n"] = 0                              # reset the counter
                if m["pend"] is not None:
                    V.pending += 1
                m["pend"] = (notif, tt, False)
                if mutate == "pending":
                    m["pend"] = (notif, tt, False)
                    V.pending += 1

        for m in mands:
            # every cycle window that CLOSED inside the horizon is due, whether
            # or not the mandate survived to see it. A dead mandate forfeits all
            # its remaining cycles, which prices mandate death directly and
            # removes the need for an invented LTV multiplier.
            closed = max(0, (days - m["due_day"]) // cyc)
            cyc_due += closed
            cyc_got += min(m["got_cycles"], closed)
            if m["alive"]:
                n_alive += 1
            if m["total_att"] == 0:
                n_starved += 1

    return dict(
        cycle_rec=cyc_got / cyc_due if cyc_due else 0.0,
        approval=n_ok / n_att if n_att else 0.0,
        survival=n_alive / n_mand,
        att_per_cycle=n_att / cyc_due if cyc_due else 0.0,
        starvation=n_starved / n_mand,
        cycles_due=cyc_due,
        violations=V.total(),
        vdetail=V.asdict(),
        calib=calib,
        ml_rows=ml_rows,
    )
