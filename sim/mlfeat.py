"""
FEATURES FOR THE ML BASELINE.

WHY THERE IS NO LEAK, STRUCTURALLY. A decision is taken at hour 8 of day D and
commits an attempt for a future day T >= D+1. Every feature must be knowable at
D. Excluding the outcome being predicted is NOT sufficient: other mandates on
the same customer can be dispatched between D and T, and their outcomes are
equally unknowable at D.

So features are never built by replaying a finished run. They are built INSIDE
the simulation, at the moment of the decision, from a history list that
physically cannot contain anything later -- entries are appended at dispatch,
and a dispatch that has not happened yet cannot be in the list. The same
function, called the same way, serves both training-set generation (`explore`)
and inference (`ml_index`). There is no second code path to drift.

WHAT THE MODEL IS ALLOWED TO KNOW, and why each item is fair:

  est_sal, est_pay   Noisy per-customer estimates the harness hands to EVERY
                     belief policy (salary x U(0.7,1.3); payday +/- payday_err).
                     Including them is like-for-like, not a leak. The true
                     salary, payday, spend rate and balance are never exposed.
  censored stats     Largest successful debit seen, smallest failed debit seen,
                     and how long ago -- on this mandate and across the whole
                     account. These are exactly what the Bayes filter's
                     observation update consumes: a success at X proves the
                     balance was >= X, a failure at X proves it was < X.
  cross-merchant     The same statistics computed over ALL mandates on the
                     customer. This is the moat, handed to the ML model in raw
                     form so the comparison is a real one.
  phase histogram    Counts of past account successes and failures bucketed by
                     day-of-cycle. This is the sufficient statistic the payday
                     posterior is built from. Giving it to the GBDT is the
                     difference between a fair baseline and a strawman.

NEVER INCLUDED: the true balance, bal[t], c["payday"], c["salary"],
c["spend"], anything from w3.balance_trace, donor_bal, the outcome being
predicted, or any statistic computed over a completed run.
"""

NPHASE = 6          # day-of-cycle buckets for the phase histogram
MISSING = -1.0      # sentinel for "never observed"; paired with a has_* flag
                    # so a linear model is not forced to read -1 as a distance

_CODE = {None: 0.0, "OK": 1.0, "Z9": 2.0, "TECH": 3.0}

FEATURES = [
    # --- timing -------------------------------------------------------------
    "offset",                 # target_day - decision_day. THE key feature:
                              # the model is asked P(success on day T), not
                              # P(success today).
    "tgt_phase_est_pay",      # (target_day - est_pay) % cyc -- what
                              # payday_wait uses, and nothing more
    "tgt_day_mod_cyc",
    "days_since_cycle_open",
    "days_to_cycle_close",
    # --- mandate ------------------------------------------------------------
    "amt_frac",               # amount / est_sal
    "n_before",               # attempts already made this cycle
    "attempts_left",
    "n_mandates",
    "other_amt_frac",         # other mandates' total claim / est_sal
    # --- own censored observations -----------------------------------------
    "own_has_ok", "own_max_ok_frac", "own_days_since_max_ok",
    "own_has_fail", "own_min_fail_frac", "own_days_since_min_fail",
    "own_n_att", "own_n_ok", "own_rate",
    "own_days_since_last_att", "own_last_code",
    # --- cross-merchant, same account --------------------------------------
    "acc_has_ok", "acc_max_ok_frac", "acc_days_since_max_ok",
    "acc_has_fail", "acc_min_fail_frac", "acc_days_since_min_fail",
    "acc_days_since_ok", "acc_days_since_fail",
    "acc_n_att", "acc_n_ok", "acc_rate",
    "acc_n_att_last_cyc", "acc_n_ok_last_cyc",
    "acc_n_fail_since_ok",
    # --- payday signal, raw -------------------------------------------------
    "phase_since_last_ok",    # (target_day - day of last account success) % cyc
    "ok_phase_at_target",     # how many past successes landed in the target's
    "fail_phase_at_target",   # day-of-cycle bucket, and how many failures
]
FEATURES += [f"ok_phase_{i}" for i in range(NPHASE)]
FEATURES += [f"fail_phase_{i}" for i in range(NPHASE)]

N_FEATURES = len(FEATURES)

# The hybrid's extra inputs: four summaries of the Bayes posterior, computed by
# the filter at the same decision point. These are what a model with no
# structural prior cannot produce for itself -- in particular the entropy and
# the top-hypothesis weight are the filter's own statement of how sure it is
# about payday, which is the quantity that survives a change in the population.
BAYES_FEATURES = [
    "bayes_p_success",        # filter's P(success on the CANDIDATE day)
    "bayes_expected_frac",    # E[balance] / est_salary
    "bayes_entropy",          # entropy of the payday posterior
    "bayes_top_w",            # weight on the single best payday hypothesis
]
FEATURES_HYBRID = FEATURES + BAYES_FEATURES


def bucket(day, cyc):
    return min(int(day % cyc) * NPHASE // cyc, NPHASE - 1)


def build(hist, uid, amount, n_before, cap, decision_day, target_day,
          cycle_open, cycle_close, cyc, est_sal, est_pay,
          n_mandates, sum_other_amt, bayes=None):
    """
    hist: chronological list of dicts for THIS CUSTOMER only, each
          {uid, day, amount, ok, code}, every one of them already dispatched
          at or before `decision_day`. Callers never construct it any other
          way -- see the module docstring.

    Returns a list of floats in FEATURES order.
    """
    sal = est_sal if est_sal > 0 else 1.0

    own_max_ok = own_min_fail = None
    own_max_ok_day = own_min_fail_day = None
    own_n = own_ok_n = 0
    own_last_day = own_last_code = None

    acc_max_ok = acc_min_fail = None
    acc_max_ok_day = acc_min_fail_day = None
    acc_n = acc_ok_n = 0
    acc_last_ok_day = acc_last_fail_day = None
    acc_n_last_cyc = acc_ok_last_cyc = 0
    acc_fail_since_ok = 0
    ok_phase = [0.0] * NPHASE
    fail_phase = [0.0] * NPHASE

    for e in hist:
        d, a, ok = e["day"], e["amount"], e["ok"]
        acc_n += 1
        if ok:
            acc_ok_n += 1
            acc_fail_since_ok = 0
            acc_last_ok_day = d
            ok_phase[bucket(d, cyc)] += 1
            if acc_max_ok is None or a > acc_max_ok:
                acc_max_ok, acc_max_ok_day = a, d
        else:
            acc_fail_since_ok += 1
            acc_last_fail_day = d
            fail_phase[bucket(d, cyc)] += 1
            if acc_min_fail is None or a < acc_min_fail:
                acc_min_fail, acc_min_fail_day = a, d
        if decision_day - d < cyc:
            acc_n_last_cyc += 1
            acc_ok_last_cyc += 1 if ok else 0
        if e["uid"] == uid:
            own_n += 1
            own_last_day, own_last_code = d, e["code"]
            if ok:
                own_ok_n += 1
                if own_max_ok is None or a > own_max_ok:
                    own_max_ok, own_max_ok_day = a, d
            else:
                if own_min_fail is None or a < own_min_fail:
                    own_min_fail, own_min_fail_day = a, d

    def since(d):
        return MISSING if d is None else float(decision_day - d)

    def frac(a):
        return MISSING if a is None else float(a) / sal

    f = {
        "offset": float(target_day - decision_day),
        "tgt_phase_est_pay": float((target_day - est_pay) % cyc),
        "tgt_day_mod_cyc": float(target_day % cyc),
        "days_since_cycle_open": float(target_day - cycle_open),
        "days_to_cycle_close": float(cycle_close - target_day),

        "amt_frac": float(amount) / sal,
        "n_before": float(n_before),
        "attempts_left": float(cap - n_before),
        "n_mandates": float(n_mandates),
        "other_amt_frac": float(sum_other_amt) / sal,

        "own_has_ok": 1.0 if own_max_ok is not None else 0.0,
        "own_max_ok_frac": frac(own_max_ok),
        "own_days_since_max_ok": since(own_max_ok_day),
        "own_has_fail": 1.0 if own_min_fail is not None else 0.0,
        "own_min_fail_frac": frac(own_min_fail),
        "own_days_since_min_fail": since(own_min_fail_day),
        "own_n_att": float(own_n),
        "own_n_ok": float(own_ok_n),
        "own_rate": (own_ok_n / own_n) if own_n else MISSING,
        "own_days_since_last_att": since(own_last_day),
        "own_last_code": _CODE.get(own_last_code, 0.0),

        "acc_has_ok": 1.0 if acc_max_ok is not None else 0.0,
        "acc_max_ok_frac": frac(acc_max_ok),
        "acc_days_since_max_ok": since(acc_max_ok_day),
        "acc_has_fail": 1.0 if acc_min_fail is not None else 0.0,
        "acc_min_fail_frac": frac(acc_min_fail),
        "acc_days_since_min_fail": since(acc_min_fail_day),
        "acc_days_since_ok": since(acc_last_ok_day),
        "acc_days_since_fail": since(acc_last_fail_day),
        "acc_n_att": float(acc_n),
        "acc_n_ok": float(acc_ok_n),
        "acc_rate": (acc_ok_n / acc_n) if acc_n else MISSING,
        "acc_n_att_last_cyc": float(acc_n_last_cyc),
        "acc_n_ok_last_cyc": float(acc_ok_last_cyc),
        "acc_n_fail_since_ok": float(acc_fail_since_ok),

        "phase_since_last_ok": (MISSING if acc_last_ok_day is None
                                else float((target_day - acc_last_ok_day) % cyc)),
        "ok_phase_at_target": ok_phase[bucket(target_day, cyc)],
        "fail_phase_at_target": fail_phase[bucket(target_day, cyc)],
    }
    for i in range(NPHASE):
        f[f"ok_phase_{i}"] = ok_phase[i]
        f[f"fail_phase_{i}"] = fail_phase[i]

    out = [f[k] for k in FEATURES]
    if bayes is not None:
        # (p_success_on_candidate_day, E[balance]/est_sal, entropy, top weight)
        out += [float(x) for x in bayes]
    return out
