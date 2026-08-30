"""THE COMPOSITION ROOT. The one place allowed to construct both an executor
and a gate, and wire them together.

Everything else in `agent/` receives what it needs. `loop.py` gets a gate and
never sees an executor; `policy/` gets a belief and never sees the world;
`llm/` gets a `CaseView` and never sees any of it. A composition root that
knows about every layer is normal and is not a hole in the isolation argument
-- it wires, it does not decide. `test_layer_isolation.py` names this module
explicitly as the exception rather than allowing the pattern generally.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import uuid

import numpy as np

import agent  # noqa: F401  -- puts sim/ on the path
import harness
import w3

from agent.audit.log import AuditLog, EventKind
from agent.constraints.rules import AttemptLedger
from agent.constraints.stage0 import Stage0Gate
from agent.context.oracle_monitor import OracleRailMonitor
from agent.context.rail_monitor import RailMonitor
from agent.execution.sim_executor import (DeclineMix, OutageSchedule,
                                          SimExecutor)
from agent.llm.fallback import RetryOnlyDiagnoser, RuleBasedDiagnoser
from agent.loop import run_agent
from agent import metrics
from agent.policy.belief_book import BeliefBook
from agent.policy.timing import DEFAULT_DISCOUNT

LOG_DIR = os.path.join(agent._PKG_ROOT, "agent", "runs")


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              cwd=agent._PKG_ROOT, capture_output=True,
                              text=True, timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def make_pop(n: int, k: int, pop_seed: int, spend: float = 1.05,
             days: int = 120, **kw):
    """Deterministic in its seed, exactly as `sim/runner.py` relies on."""
    return w3.make_pop(n, k, np.random.default_rng(pop_seed), days=days,
                       spend=spend, **kw)


def at_risk_cycles(pop, seed: int, payday_err: int = 7,
                   p_missed_credit: float = 0.0,
                   p_transient: float = 0.0, transient_h: int = 24) -> dict:
    """The world's revenue-at-risk set, without running a policy.

    Exposed HERE rather than imported from `agent.execution` by the caller,
    because gate I2 permits only this module and `constraints/stage0.py` to
    hold an executor -- and the right answer to "a test needs the world" is to
    route it through the composition root, not to widen the exempt list. The
    exempt list is for tests that must BUILD an executor to exercise the gate;
    this needs the world's opinion, which is a different thing.

    {(mandate_uid, cycle): due_day}. See `SimExecutor.at_risk_cycles`.
    """
    return SimExecutor(pop, seed, payday_err,
                       p_missed_credit=p_missed_credit,
                       p_transient=p_transient,
                       transient_h=transient_h).at_risk_cycles()


def unwinnable_cycles(pop, seed: int, payday_err: int = 7,
                      p_missed_credit: float = 0.0,
                      p_transient: float = 0.0,
                      transient_h: int = 24) -> dict:
    """The oracle's ceiling: cycles no schedule could collect. Routed through
    the composition root for the same reason `at_risk_cycles` is."""
    return SimExecutor(pop, seed, payday_err,
                       p_missed_credit=p_missed_credit,
                       p_transient=p_transient,
                       transient_h=transient_h).unwinnable_cycles()


def run_once(pop, seed: int, *, payday_err: int = 7, pop_spend: float = 1.05,
             bcfg: dict | None = None, mode: str = "degenerate",
             topup_p: float = 0.0, nudge_p: float = 0.0,
             discount: float = DEFAULT_DISCOUNT,
             allow_nudge: bool = True, allow_escalate: bool = True,
             allow_stop: bool = True,
             log_path: str | None = None, log_ticks: bool = False,
             run_id: str | None = None, provenance: dict | None = None,
             diagnoser=None, outage: OutageSchedule | None = None,
             outage_kw: dict | None = None,
             monitor_enabled: bool = False, monitor_kw: dict | None = None,
             monitor_kind: str = "statistical",
             oracle_mutant: str | None = None,
             pause_on_outage: bool = False,
             suppress_tech_updates: str = "never",
             time_major: bool = False, collect_calib: bool = False,
             per_customer_tech_rng: bool | None = None,
             declines: DeclineMix | None = None,
             decline_kw: dict | None = None,
             p_missed_credit: float = 0.0,
             p_transient: float = 0.0, transient_h: int = 24,
             use_llm: bool = False,
             llm_max_calls: int | None = 150,
             executor=None) -> dict:
    """One agent run over one population.

    `mode="degenerate"` is retry-only with the deterministic diagnoser: the
    agent reduced to the frozen policy, which is what the parity test compares
    against `harness.run("solo_shared_pd", ...)`.
    `mode="full"` turns the action space on.
    """
    run_id = run_id or uuid.uuid4().hex[:12]
    log_path = log_path or os.path.join(LOG_DIR, f"{run_id}.jsonl")

    # Built here rather than passed in, so a parallel driver ships numbers
    # across the process boundary instead of an object.
    if outage is None and outage_kw:
        outage = OutageSchedule(**outage_kw)
    # Same pattern, same reason: a parallel driver ships numbers across
    # the process boundary rather than pickling an object.
    if declines is None and decline_kw:
        declines = DeclineMix(**decline_kw)

    # Defaults to the loop order, but is separable so the order-equivalence
    # gate can hold the RNG fixed and vary ONLY the iteration order. Without
    # that separation the gate would be comparing two things at once.
    if per_customer_tech_rng is None:
        per_customer_tech_rng = time_major
    # THE BACKEND SWITCH. `executor=RazorpayExecutor(...)` and everything above
    # and below this line is unchanged: the loop, the belief, Stage 0 and the
    # audit trail never learn which world they are in, because gate I2 forbids
    # any of them from importing `agent.execution` at all. This is the ONLY
    # place in `agent/` where a second backend is even nameable.
    #
    # `scripts/prove_stage0_refuses.py` runs the constraint layer against the
    # real Razorpay client with no key and no network, which is the check that
    # this claim is structural rather than aspirational.
    if executor is None:
        executor = SimExecutor(pop, seed, payday_err, topup_p=topup_p,
                               nudge_p=nudge_p, outage=outage,
                               per_customer_tech_rng=per_customer_tech_rng,
                               declines=declines,
                               p_missed_credit=p_missed_credit,
                               p_transient=p_transient,
                               transient_h=transient_h)
    ledger = AttemptLedger()
    log = AuditLog(log_path, run_id)
    gate = Stage0Gate(executor, ledger, log)
    if diagnoser is None:
        diagnoser = (RetryOnlyDiagnoser() if mode == "degenerate"
                     else RuleBasedDiagnoser(allow_nudge=allow_nudge,
                                             allow_escalate=allow_escalate,
                                             allow_stop=allow_stop))
        if use_llm and mode != "degenerate":
            # THE LLM IS AN OVERLAY WRAPPED AROUND THE DETERMINISTIC ANSWER,
            # never a replacement for it. `ModelDiagnoser` falls back to the
            # rule engine on any failure and marks the row `source="fallback"`,
            # so a batch run cannot silently become half one thing and half
            # another without the audit trail saying which.
            #
            # The cache is the eval's cache, on purpose: a case the eval has
            # already paid for is answered here for free and IDENTICALLY, which
            # is what makes a batch number reproducible offline.
            from agent.llm.client import DIAGNOSER_MODEL, ResponseCache, ZaiClient
            from agent.llm.model_diagnoser import ModelDiagnoser
            cache_path = os.path.join(agent._PKG_ROOT, "agent", "eval",
                                      "_cache", f"{DIAGNOSER_MODEL}.json")
            diagnoser = ModelDiagnoser(
                client=ZaiClient(model=DIAGNOSER_MODEL,
                                 cache=ResponseCache(cache_path)),
                fallback=diagnoser, log=log,
                max_live_calls=llm_max_calls)

    # THE BASELINE ARM. `mode="doc_legal"` swaps the belief-driven index for a
    # fixed daily schedule. Everything else -- Stage 0, the audit trail, the
    # recovery metric -- is identical, which is the only way its recovery rate
    # is comparable to the agent's. It still builds a BeliefBook it never
    # consults, so that the RNG stream and the loop shape are unchanged and the
    # two arms differ in exactly one thing.
    scheduler = None
    if mode == "doc_legal":
        from agent.policy.fixed_schedule import propose_fixed
        scheduler = propose_fixed
        # FORCED, not defaulted. The diagnoser was already chosen above, and a
        # fixed schedule that could also nudge, escalate or stop is not a fixed
        # schedule -- it would carry part of the agent's action space into the
        # arm it is supposed to be the control for.
        diagnoser = RetryOnlyDiagnoser()

    book = BeliefBook(pop[0]["cycle_days"], pop[0]["days"], pop_spend, bcfg)
    # The monitor's base rate is the harness's P_TECH, not a number of ours.
    #
    # `monitor_kind="oracle"` swaps in the CLAIRVOYANT monitor, which is handed
    # the true outage windows and is therefore unreachable by any real
    # detector. It exists to be an upper bound, never to be a result: the
    # composition root is the only place that can build one, the windows come
    # from the schedule object itself rather than from the caller (so the
    # oracle cannot be graded against a target the world does not share), and
    # the choice is stamped into provenance below and into every verdict's
    # reason string in the audit log. `oracle_mutant` applies a named window
    # transform from `agent/context/oracle_monitor.py` -- the mutants are lists
    # of numbers, not code branches, which is rule 1a taken literally.
    if monitor_kind == "oracle":
        wins = list(outage.windows) if outage is not None else []
        if oracle_mutant:
            from agent.context.oracle_monitor import crippled
            wins = crippled(wins, pop[0]["days"] * w3.HOURS, oracle_mutant)
        monitor = OracleRailMonitor(wins, enabled=monitor_enabled,
                                    label=oracle_mutant or "oracle")
    elif monitor_kind == "statistical":
        monitor = RailMonitor(harness.P_TECH, enabled=monitor_enabled,
                              **(monitor_kw or {}))
    else:
        raise ValueError(f"unknown monitor_kind {monitor_kind!r}")

    prov = dict(
        run_id=run_id, mode=mode, policy="solo_shared_pd",
        diagnoser=type(diagnoser).__name__,
        prompt_id=getattr(diagnoser, "prompt_id", ""),
        seed=seed, payday_err=payday_err, pop_spend=pop_spend,
        discount=discount, topup_p=topup_p, nudge_p=nudge_p,
        allow_nudge=allow_nudge, allow_escalate=allow_escalate,
        allow_stop=allow_stop,
        p_missed_credit=p_missed_credit, p_transient=p_transient,
        transient_h=transient_h,
        n_customers=len(pop), k=len(pop[0]["mandates"]),
        days=pop[0]["days"], cycle_days=pop[0]["cycle_days"],
        bcfg=bcfg, bcfg_sha=hashlib.sha256(
            json.dumps(bcfg or {}, sort_keys=True).encode()).hexdigest()[:16],
        outage=outage.asdict() if outage else None,
        declines=declines.asdict() if declines else None,
        use_llm=use_llm, llm_max_calls=llm_max_calls,
        monitor_enabled=monitor_enabled, monitor_kw=monitor_kw,
        monitor_kind=monitor_kind, oracle_mutant=oracle_mutant,
        oracle_windows=(list(monitor.windows)
                        if monitor_kind == "oracle" else None),
        pause_on_outage=pause_on_outage,
        suppress_tech_updates=suppress_tech_updates, time_major=time_major,
        per_customer_tech_rng=per_customer_tech_rng,
        numpy=np.__version__, python=sys.version.split()[0],
        git_sha=_git_sha(), wall_start=time.time(),
        llm_spend_usd=0.0,
    )
    if provenance:
        prov.update(provenance)
    log.emit(EventKind.RUN_START, 0, **prov)

    t0 = time.time()
    res = run_agent(pop, seed, gate, book, log, diagnoser,
                    estimates=executor.estimates, banks=executor.banks,
                    monitor=monitor,
                    discount=discount, log_ticks=log_ticks,
                    time_major=time_major, collect_calib=collect_calib,
                    pause_on_outage=pause_on_outage,
                    suppress_tech_updates=suppress_tech_updates,
                    scheduler=scheduler)
    res["run_id"] = run_id
    res["log_path"] = log_path
    res["mode"] = mode
    res["wall_s"] = round(time.time() - t0, 2)
    res["nudges_took"] = executor.n_nudges_took
    res["exec_attempts_in_outage"] = executor.n_attempts_in_outage
    res["exec_tech_in_outage"] = executor.n_tech_in_outage
    res["exec_tech_total"] = executor.n_tech
    res["exec_code_counts"] = dict(executor.code_counts)
    res["exec_terminal_attempts"] = executor.n_terminal_attempts

    # ---- RECOVERY-RATE METRICS (docs/04_BUILD_PLAN.md W0)
    # The composition root is the only module that holds both the world and the
    # policy's result, which is exactly why the join happens here: gate I2
    # forbids anything else under `agent/` from importing `agent.execution`,
    # so `agent/metrics.py` is arithmetic over two dicts and never sees either.
    # `at_risk_cycles()` depends only on (pop, seed), so every arm on the same
    # population shares this denominator and the arms stay comparable.
    if hasattr(executor, "at_risk_cycles"):
        amounts = {f"c{ci}m{mi}": m["amount"]
                   for ci, c in enumerate(pop)
                   for mi, m in enumerate(c["mandates"])}
        rec = metrics.compute(executor.at_risk_cycles(),
                              res.pop("collected_cycles"),
                              res["cycles_due"], amounts)
        res["recovery"] = rec.as_dict()
    else:
        # A backend that is not the simulation cannot answer the counterfactual
        # "would a due-date debit have cleared" -- only the real rail knows, and
        # it was never asked. Absent, rather than zero.
        res.pop("collected_cycles", None)
        res["recovery"] = None
    st = getattr(diagnoser, "stats", None)
    if st is not None:
        res["llm_n_llm"] = st["n_llm"]
        res["llm_n_fallback"] = st["n_fallback"]
        res["llm_reasons"] = dict(st["reasons"])
        res["llm_spend_usd"] = st["spend_usd"]
        res["llm_n_capped"] = st.get("n_capped", 0)
        prov["llm_spend_usd"] = st["spend_usd"]

    _skip = ("stops", "gate_refusals", "calib", "rail_transitions")
    log.emit(EventKind.RUN_END, 0,
             **{k: v for k, v in res.items() if k not in _skip},
             stops_json=json.dumps(res["stops"]),
             gate_refusals_json=json.dumps(res["gate_refusals"]),
             rail_transitions_json=json.dumps(res["rail_transitions"]))
    log.close()
    return res
