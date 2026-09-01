"""Pre-compute every scenario the interactive page shows. Run offline, commit
the JSON, and the page looks things up instead of computing them.

WHY THIS SCRIPT EXISTS AT ALL. The page is static HTML on GitHub Pages: no
backend, no build step, has to work on a phone. So every number it can show has
to be decided here, in Python, against the frozen model -- not in JavaScript
against a re-implementation. A JS re-implementation of `w3.index_score` would
be a second implementation of a gated thing with no parity test, which is the
mistake `agent/execution/sim_executor.py` needed a whole gate to avoid making.

WHAT IS COMPUTED HERE AND WHAT IS QUOTED. Two different things, and the page
labels them differently because they have different evidence behind them:

  COMPUTED HERE  one customer's month, from a real `agent.batch.run_once` with
                 `log_ticks=True`. The arrows, the waits and the index scores
                 on the page are read out of that run's audit log. Reproducible
                 by re-running this script.

  QUOTED         every aggregate percentage. Those come from `docs/02_RESULTS.md`
                 and are NOT recomputed here, because recomputing a published
                 number and quietly shipping whatever came out is how a page
                 ends up disagreeing with its own docs. They are transcribed
                 with their source and their gate status, and `--check`
                 re-derives the ones that are cheap to re-derive.

THE HERO CUSTOMER IS CHOSEN, AND THE PAGE SAYS SO. `c45m3` was picked from
population 700 because its month is legible: payday on day 9, a 550-rupee debit
due on day 4, and a flat 215-rupee balance in between. It is not the average
customer and it is not the best one either -- plenty of customers in the same
population have months where the agent also fails, and two of them are listed
in `ALTERNATIVES` below so a sceptical reader can pull one instead. The
aggregate numbers sit next to the hero on the page for exactly this reason.

Run:  python scripts/build_page_data.py            # writes docs/data/scenarios.json
      python scripts/build_page_data.py --check    # re-derive, diff, write nothing
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import agent  # noqa: F401  -- puts sim/ on the path
import w3
from agent.batch import make_pop
from agent.execution.sim_executor import OutageSchedule, SimExecutor
from agent.tests._parallel import run_jobs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "data", "scenarios.json")
HTML = os.path.join(ROOT, "docs", "index.html")

POP_SEED = 700
RUN_SEED = 7
N, K, DAYS = 100, 5, 120
CI, MI = 45, 3                      # the hero customer and mandate
UID = f"c{CI}m{MI}"

#: Other customers in the same population whose month is worth looking at. Both
#: are cases where the agent does NOT get a clean win, listed here so that
#: "we picked a good one" is a checkable statement rather than an accusation.
ALTERNATIVES = [
    ("c34m1", "payday day 17, debit due day 6 -- the agent misses too, twice"),
    ("c51m1", "the agent burns one attempt on day 13 before landing on day 31"),
]

PAYDAY_ERRS = [1, 3, 5, 7, 10, 14]

# --------------------------------------------------------------------------
# QUOTED NUMBERS. Transcribed from docs/02_RESULTS.md. Not recomputed here.
# Each carries the script that produces it and whether a gate protects it, so
# the page can print that next to the figure instead of in a footnote nobody
# reads.
# --------------------------------------------------------------------------

#: THE CANONICAL WORLD, `logs/w27_page_sweep_repaired.txt`, produced by
#: `py -3.12 agent/tests/test_page_sweep.py`. RE-MEASURED on the shipped belief
#: 1 September 2026; the previous transcript
#: (`logs/w23_page_sweep_canonical.txt`) was the pre-W24 agent and its +/-7 row
#: had drifted a hundredth away from the re-run batch, which is the symptom of
#: a transcribed table outliving its source. Until this pass the table had NO
#: committed script behind it at all. Cycles collected,
#: n=100, 10 held-out populations 710-719, 120d, pop_spend=0.93, run seed 7,
#: mode=full, burn 12, mandate outflow ON, paired 2 SE. NOT gate-protected.
#: Same configuration as `agent.batch_report --canonical`, so the +/-7 row IS
#: the headline cell. Replaces the pre-canonical sweep, which ran on a world
#: with no steady state and an invented mandate count (NOTES.md errors 33-35).
SWEEP = [
    # err, payday_wait, agent (fitted), the DOCUMENTED difference, 2 SE, verdict.
    # The difference is transcribed, NOT computed as agent - payday_wait: the
    # two columns are rounded to 2dp and subtracting them can disagree with the
    # source by a hundredth. A page that disagrees with its own source by a
    # hundredth invites the reader to check nothing else.
    (1,  99.80, 99.81, +0.01, 0.18, "tie"),
    (3,  99.25, 99.56, +0.31, 0.46, "tie"),
    (5,  94.17, 99.58, +5.41, 1.59, "agent wins"),
    (7,  90.29, 99.38, +9.08, 1.84, "agent wins"),
    (10, 87.48, 99.25, +11.77, 1.45, "agent wins"),
    (14, 86.13, 98.83, +12.71, 1.00, "agent wins"),
]

#: docs/02_RESULTS.md, "A bank-shaped outage is 3.4x less detectable". Produced
#: by E-MIX-2 inside `py -3.12 agent/tests/test_decline_sweep.py`.
#: n=200, severity 0.80, four 6h windows, 8 populations. NOT gate-protected.
#:
#: RE-MEASURED on the shipped belief, `logs/w27_decline_sweep_repaired.txt`.
#: Until 2 September 2026 these carried 0.78 / 0.41 / 0.22 / 0.09 from a
#: pre-18:08 run that was never captured to a file -- the transcript the page
#: now cites had ALREADY superseded them and nobody had transcribed it. The
#: worst single bank also changed identity: `@upi` at 0.09 was `@oksbi` at
#: 0.06 on the current run.
POOLING_DETECTION = [
    ("every bank pooled", 200, 0.72),
    ("@okaxis -- best single bank", 30, 0.38),
    ("mean over the eight single banks", 25, 0.21),
    ("@oksbi -- worst single bank", 13, 0.06),
]

#: docs/02_RESULTS.md, "The moat's second dividend". Attempts available to a
#: detector per 24h window. `min_attempts = 8` is the floor below which the
#: monitor refuses to evaluate at all.
#:
#: RE-MEASURED on the shipped belief, `logs/w28_detection_power.txt`. The
#: superseded row read 11.4 / 22.5 / 44.5 and 0.74 at n=200. The volumes rose
#: and the verdict did not move: one merchant stays below the floor of 8 at
#: every n from 5 to 200, which is the claim the page draws.
POOLING_VOLUME = [
    (25, 5.6, 0.09), (50, 11.6, 0.19), (100, 23.1, 0.38), (200, 46.3, 0.77),
]

#: docs/02_RESULTS.md, "What outage awareness is WORTH". Paired 2 SE against
#: the monitor-off arm at the same severity. THE 0.40 ROW IS NEGATIVE AND
#: SIGNIFICANT and the page is required to show it -- see the module docstring
#: of docs/index.html.
#: RE-MEASURED on the canonical world AND the shipped belief,
#: `logs/w27_abl_outage_repaired.txt`. Nothing is significant at any severity.
#: Two superseded readings: the pre-canonical -0.273 / -0.529 (SIG) / +0.199,
#: and the pre-W24 exactly-zero row from `logs/w17_abl_outage_canonical.txt`.
#: The conclusion never changed; the literal zeros did, so the page shows the
#: intervals rather than a column of zeros a reader cannot argue with.
OUTAGE_ABLATION = [
    (0.15, 0.000, 0.000, False),
    (0.40, 0.017, 0.035, False),
    (0.80, 0.051, 0.074, False),
]

#: docs/02_RESULTS.md, "THE BATCH NUMBER". 10 held-out populations on the
#: canonical world: `py -3.12 -m agent.batch_report --pops 10 --canonical`,
#: transcript `logs/w24_headline_repaired.txt`.
#:
#: RE-MEASURED 1 September 2026 after the belief repair (prior_w 9 -> 5,
#: prior_floor 0.5 -> 0.1, cycle_value 0 -> 0.6). The uplift is unchanged at
#: +9.08 because `payday_wait` runs through the harness and carries no belief;
#: what moved is the agent's own collection (99.37 -> 99.38), the money
#: (7,496,430 -> 7,511,500), the interval (2 SE 2.01 -> 1.84) and the executed
#: money actions (8,721 -> 8,702). Previous transcript:
#: `logs/w19_headline_canonical.txt`.
BATCH = dict(agent=99.38, payday_wait=90.29, delta=9.08, two_se=1.84,
             rupees=7511500, populations=10, money_actions=8702,
             stage0_refusals=0, auditor_recount=0)


def html_batch_errors() -> list[str]:
    """Return stale static batch figures in the page.

    JavaScript hydrates these elements from scenarios.json, but the checked-in
    fallback is what readers and link previews can see before that fetch. The
    data check must therefore cover both files.
    """
    with open(HTML, encoding="utf-8") as fh:
        html = fh.read()
    expected = {
        "s-agent": f"{BATCH['agent']:.2f}%",
        "s-base": f"{BATCH['payday_wait']:.2f}%",
        "s-rupees": f"₹{BATCH['rupees'] / 100000:.1f}L",
        "b-agent": f"{BATCH['agent']:.2f}%",
        "b-base": f"{BATCH['payday_wait']:.2f}%",
        "b-delta": (f"+{BATCH['delta']:.2f} points, "
                    f"2 SE {BATCH['two_se']:.2f}"),
        "b-rupees": f"₹{BATCH['rupees']:,}",
    }
    errors = []
    for element_id, want in expected.items():
        match = re.search(
            rf'id="{re.escape(element_id)}"[^>]*>([^<]*)<', html)
        got = match.group(1).strip() if match else None
        if got != want:
            errors.append(f"{element_id}: expected {want!r}, found {got!r}")
    return errors


def _daily_balance(bal: np.ndarray, lo: int, hi: int) -> list[dict]:
    """Balance at the decision hour, one sample per day.

    Hour 8 and not a daily mean, because hour 8 is when every decision is taken
    and 99.22% of all attempts land there (docs/02_RESULTS.md). A daily average
    would draw a curve no attempt was ever evaluated against.
    """
    return [{"day": d, "bal": round(float(bal[d * w3.HOURS + w3.DECISION_HOUR]), 2)}
            for d in range(lo, hi)]


def _narrate(day: int, kind: str, ev: dict, amount: float) -> dict | None:
    """One line of the side panel, in the plain English a non-engineer reads.

    TWO STRINGS PER EVENT, ON PURPOSE. `internal` is what the agent knows.
    `merchant` is what a merchant is allowed to be told, and the difference
    between them IS the redaction boundary -- `agent/llm/governance.py` refuses
    a merchant-facing rationale that discloses the customer's financial state,
    and `agent/ports.py:CaseView` never carries a balance in the first place.
    Showing both costs one line and demonstrates a real architectural property
    that is otherwise invisible on a page.
    """
    if kind == "DECISION_TICK" and ev["verdict"] == "wait":
        return dict(
            day=day, tone="wait",
            internal=(f"Skipping today. Odds of clearing "
                      f"₹{amount:,.0f} now: {ev['p_now']:.0%}. Best later "
                      f"day: {ev['p_later']:.0%}. Waiting is worth more."),
            merchant="Holding this attempt. Our timing model scores a later "
                     "day in this cycle higher.")
    if kind == "DECISION_TICK" and ev["verdict"] == "ok":
        return dict(
            day=day, tone="decide",
            internal=(f"Now is the best remaining day. Odds {ev['p_now']:.0%} "
                      f"against {ev['p_later']:.0%} later."),
            merchant="Scheduling the attempt our timing model scores highest "
                     "in the remaining window.")
    if kind == "NOTIFICATION_ISSUED":
        tgt = ev["target_t"] // w3.HOURS
        return dict(
            day=day, tone="notify",
            internal=(f"Pre-debit notice issued. NPCI requires 24 hours' "
                      f"lead, so the debit is set for day {tgt} at 08:00 — "
                      f"outside every peak window."),
            merchant=f"Pre-debit notification sent. Debit scheduled for "
                     f"day {tgt}, 08:00.")
    if kind == "OUTCOME":
        if ev["success"]:
            return dict(day=day, tone="win",
                        internal=f"₹{amount:,.0f} collected.",
                        merchant=f"₹{amount:,.0f} collected.")
        return dict(day=day, tone="fail",
                    internal=f"Declined, {ev['outcome_code']}. "
                             f"Folded into the balance estimate.",
                    merchant=f"Attempt declined ({ev['outcome_code']}).")
    return None


def page_job(spec):
    """ONE agent run, in a FRESH PROCESS, returning only what the page needs.

    ⚠️ THIS USED TO RUN EVERY ARM IN ONE PROCESS, AND THE REASONING WAS WRONG.

    `docs/06_MODEL_CARD.md` 6a says every measurement must run one process per
    run, because long-lived processes that make many `run_once` calls crash on
    this machine (0xC0000005, a different point each time, root cause never
    found). The first version of this script argued its way out of that:
    "nothing here is a mean, the rule protects an average, there is no
    average". Seven `run_once` calls in one process. **It ran clean several
    times and then segfaulted** -- exit 3221225477 -- during a cold-read check
    of the very docs that state the rule.

    The rule has TWO halves and only one of them is about means. The other is
    that the process crashes. An exemption argued from the first half does not
    survive the second, and "it worked when I ran it" is exactly the evidence
    an intermittent fault produces. **Do not re-derive this exemption.**

    So every arm now goes through `agent/tests/_parallel.py:run_jobs`, one
    fresh interpreter each, which also raises if a worker dies rather than
    quietly returning fewer arms than were asked for.

    spec = (key, payday_err, extra_run_kwargs, want_rows)
    """
    import os as _os
    import tempfile as _tf
    key, payday_err, extra, want_rows = spec
    import agent  # noqa: F401
    import w3 as _w3
    from agent.audit.log import read_rows
    from agent.batch import make_pop as _mp, run_once as _ro

    pop = _mp(N, K, POP_SEED)
    with _tf.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        log = _os.path.join(tmp, "a.jsonl")
        res = _ro(pop, RUN_SEED, payday_err=payday_err, bcfg=_w3.FITTED_BELIEF,
                  mode="full", log_path=log, log_ticks=want_rows,
                  run_id=f"page-{key}", **extra)
        rows = ([r for r in read_rows(log)
                 if r.get("mandate_uid") == UID and r.get("cycle") == 0]
                if want_rows else [])
    return key, dict(
        cycle_rec=res["cycle_rec"],
        rail_transitions=res.get("rail_transitions", []),
        paused_dispatch=res.get("paused_dispatch", 0),
        rows=rows,
    )


def hero_arm(payday_err: int, job: dict) -> dict:
    """One arm's job result, reduced to what the page draws."""
    pop = make_pop(N, K, POP_SEED)
    amount = float(pop[CI]["mandates"][MI]["amount"])
    lo = int(pop[CI]["mandates"][MI]["due_day"])
    hi = lo + int(pop[CI]["cycle_days"])

    ticks, attempts, story = [], [], []
    for r in job["rows"]:
        day = r.get("ts_hour", 0) // w3.HOURS
        if day >= hi:
            break
        if r["kind"] == "DECISION_TICK":
            ticks.append(dict(day=day, verdict=r["verdict"],
                              p_now=round(r["p_now"], 4),
                              p_later=round(r["p_later"], 4),
                              index=round(r["index_score"], 2)))
        elif r["kind"] == "OUTCOME":
            attempts.append(dict(day=day, success=bool(r["success"]),
                                 code=r["outcome_code"]))
        line = _narrate(day, r["kind"], r, amount)
        if line:
            story.append(line)
    return dict(payday_err=payday_err, ticks=ticks, attempts=attempts,
                narration=story,
                cycle_rec=round(job["cycle_rec"] * 100, 2))


def build() -> dict:
    pop = make_pop(N, K, POP_SEED)
    ex = SimExecutor(pop, RUN_SEED, 7)
    c, m = pop[CI], pop[CI]["mandates"][MI]
    amount = float(m["amount"])
    lo = int(m["due_day"])
    hi = lo + int(c["cycle_days"])
    bal = ex.worlds[CI].bal

    # ---- the naive arm.
    #
    # THIS IS NOT A POLICY SIMULATION AND MUST NOT BE READ AS ONE. It is
    # Razorpay's OWN documented subscription retry schedule -- charge on T,
    # retry T+1, T+2, T+3, then halt ([VERIFIED], docs/01_FACTS.md) --
    # evaluated against this one customer's true balance trace. Four fixed
    # days, one lookup each. It carries no recovery percentage anywhere on the
    # page, because four attempts against one trace is an anecdote and the
    # published policy comparison is `payday_wait`, which is a real arm with
    # real error bars.
    naive = [dict(day=d, success=bool(bal[d * w3.HOURS + w3.DECISION_HOUR] >= amount))
             for d in range(lo, min(lo + 4, hi))]

    # EVERY ARM IN ITS OWN PROCESS. See `page_job`'s docstring for the
    # segfault this replaced, and do not re-derive the exemption.
    outage = OutageSchedule(days=[20, 50, 80, 110], duration_h=6, severity=0.40)
    jobs = [(f"pe{pe}", pe, {}, True) for pe in PAYDAY_ERRS]
    jobs += [(arm, 7, dict(outage_kw=dict(days=[20, 50, 80, 110], duration_h=6,
                                          severity=0.40),
                           monitor_enabled=True, pause_on_outage=pause,
                           time_major=True), False)
             for arm, pause in (("detect_only", False),
                                ("detect_and_pause", True))]
    done = run_jobs(page_job, jobs)

    arms = {str(pe): hero_arm(pe, done[f"pe{pe}"]) for pe in PAYDAY_ERRS}

    # ---- the outage scenario.
    #
    # A SEPARATE RUN, AND A DIFFERENT WORLD. The rail monitor requires
    # `time_major=True` (error 14: under customer-major the clock restarts per
    # customer, nothing prunes, and OUTAGE latches forever), and time_major
    # flips `per_customer_tech_rng`, which changes the technical-decline draw
    # order. So this is not the hero run with an outage added -- it is its own
    # run, and the page never lays its arrows over the hero timeline.
    #
    # BOTH RESPONSE ARMS ARE ON THE PAGE, because the difference between them
    # is the finding. `docs/02_RESULTS.md` measures detection with the response
    # OFF, deliberately, "so that pausing does not suppress the evidence that
    # produces detection". Running it with the response ON shows why that
    # protocol note exists: pausing removes the attempts the binomial tail is
    # computed over, so the detector starves itself. The page shows both counts
    # side by side rather than the flattering one.
    oruns = {a: done[a] for a in ("detect_only", "detect_and_pause")}

    def _covers(t: int) -> bool:
        return any(lo <= t < hi for lo, hi in outage.windows)

    def _arm(res: dict) -> dict:
        tr = [dict(t=int(t), day=int(t) // w3.HOURS, hour=int(t) % w3.HOURS,
                   edge=lbl, n_attempts=int(n), n_tech=int(k),
                   p_value=float(p), in_window=_covers(int(t)))
              for t, lbl, n, k, p in res["rail_transitions"]]
        enters = [x for x in tr if x["edge"].endswith("->OUTAGE")]
        return dict(
            transitions=tr,
            entered=len(enters),
            entered_in_window=sum(1 for x in enters if x["in_window"]),
            false_alarms=sum(1 for x in enters if not x["in_window"]),
            paused_dispatch=int(res["paused_dispatch"]),
            cycle_rec=round(res["cycle_rec"] * 100, 2),
        )

    return dict(
        meta=dict(
            generated_by="scripts/build_page_data.py",
            pop_seed=POP_SEED, run_seed=RUN_SEED, n=N, k=K, days=DAYS,
            policy="solo_shared_pd", bcfg=dict(w3.FITTED_BELIEF),
            numpy=np.__version__, python=sys.version.split()[0],
        ),
        hero=dict(
            uid=UID, payday=int(c["payday"]), due_day=lo,
            cycle_start=lo, cycle_end=hi,
            amount=amount, salary=round(float(c["salary"])),
            balance=_daily_balance(bal, lo, hi),
            naive=naive,
            arms=arms,
            alternatives=[dict(uid=u, note=n) for u, n in ALTERNATIVES],
        ),
        sweep=[dict(err=e, payday_wait=pw, agent=ag, diff=df, two_se=se,
                    verdict=v)
               for e, pw, ag, df, se, v in SWEEP],
        pooling=dict(
            detection=[dict(label=l, customers=n, rate=r)
                       for l, n, r in POOLING_DETECTION],
            volume=[dict(n=n, pooled=p, single=s) for n, p, s in POOLING_VOLUME],
            min_attempts=8,
        ),
        outage=dict(
            ablation=[dict(severity=s, delta=d, two_se=se, significant=sig)
                      for s, d, se, sig in OUTAGE_ABLATION],
            # Every state change each arm's detector made, WITH the evidence
            # that caused it: how many attempts were in the rolling window, how
            # many came back technical, and the exact binomial tail
            # probability. A detector that cannot show its working is one
            # nobody can argue with, which is worse than one that is sometimes
            # wrong.
            arms={k: _arm(v) for k, v in oruns.items()},
            windows=[dict(day=d, start_t=lo, end_t=hi)
                     for d, (lo, hi) in zip(outage.days, outage.windows)],
            total_windows=len(outage.windows),
            severity=0.40,
            #: The PUBLISHED false-alarm figure, measured at severity 0 over 48
            #: runs. It is not the same measurement as `arms[*].false_alarms`,
            #: which counts firings outside a window in ONE run that DOES
            #: contain outages. Both are on the page and the page says which is
            #: which -- conflating them would let a single unlucky run overwrite
            #: a 48-run result, or the reverse.
            #:
            #: MEASURED at last, 2 September 2026: until then this family had
            #: no transcript in logs/ and was carried as an argument. The
            #: false-alarm figure HELD; the TPR did not. It was published as
            #: "1.00 at n>=100" and the re-run scores 0.75 at n=100, reaching
            #: 1.00 only at n=200. `logs/w28_detection_power.txt`, produced by
            #: `py -3.12 agent/tests/test_outage_detection.py`.
            published_transcript="logs/w28_detection_power.txt",
            published_false_alarm_runs="0 of 48 at severity 0",
            published_tpr_at_n100="0.75 at severity 0.40, response OFF; "
                                  "1.00 at n=200",
        ),
        batch=BATCH,
    )


def main() -> int:
    check = "--check" in sys.argv
    data = build()
    blob = json.dumps(data, indent=1, sort_keys=True) + "\n"
    if check:
        if not os.path.exists(OUT):
            print("FAIL  no committed scenarios.json to check against")
            return 1
        with open(OUT, encoding="utf-8") as fh:
            old = fh.read()
        same = old == blob
        print(("PASS  " if same else "FAIL  ")
              + f"regenerated data {'matches' if same else 'DIFFERS FROM'} "
                f"the committed {os.path.relpath(OUT, ROOT)}")
        html_errors = html_batch_errors()
        if html_errors:
            print("FAIL  docs/index.html has stale batch figures:")
            for error in html_errors:
                print(f"  {error}")
        else:
            print("PASS  docs/index.html batch figures match scenarios.json")
        return 0 if same and not html_errors else 1
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(blob)
    h = data["hero"]
    print(f"wrote {os.path.relpath(OUT, ROOT)}  ({len(blob):,} bytes)")
    print(f"  hero {h['uid']}: payday day {h['payday']}, "
          f"Rs {h['amount']:,.0f} due day {h['due_day']}")
    print(f"  naive: {sum(a['success'] for a in h['naive'])}/"
          f"{len(h['naive'])} landed")
    for pe, arm in sorted(h["arms"].items(), key=lambda kv: int(kv[0])):
        att = arm["attempts"]
        print(f"  agent pe={pe:>2}: {sum(a['success'] for a in att)}/{len(att)}"
              f" landed, days {[a['day'] for a in att]}, "
              f"run cycle_rec {arm['cycle_rec']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
