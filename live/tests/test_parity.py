"""P-gates: the simulation and the live rail share the decision layers.

THE CLAIM THIS REPOSITORY MAKES is that the architecture developed against a
simulated world runs against a real payment provider WITHOUT changing its
decision boundary. That is easy to assert in a diagram and easy to break in
code: one copied function, one "live version" of the timing rule, and the two
paths have quietly become two systems that agree by coincidence.

So these gates check identity, not resemblance. `live/service.py` and
`agent/batch.py` must reach the SAME function objects and the SAME classes --
`is`, not `==`, and not "looks similar".

WHAT THEY DO NOT CLAIM. Sharing a scheduler does not mean the two produce the
same decisions: they see different worlds, different clocks and different
customers. The shared thing is the RULE, not the answer.
"""
from __future__ import annotations

import ast
import os

import live.tests  # noqa: F401
import agent.batch as batch
from agent.constraints.stage0 import Stage0Gate
from agent.execution.sim_executor import SimExecutor  # noqa: F401
from agent.policy.belief_book import BeliefBook
from agent.policy.timing import propose
from live import service as live_service
from live.tests._harness import Bench, Results

_PKG = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

#: Modules that decide. Neither composition root may hold a private copy of
#: any of them.
SHARED = {
    "the timing rule": (propose, live_service.timing.propose),
    "the belief book": (BeliefBook, live_service.BeliefBook),
    "the constraint gate": (Stage0Gate, live_service.Stage0Gate),
}


def main() -> int:
    r = Results("SIMULATION / LIVE PARITY GATES (offline)")

    # ------------------------------------------------------------------ P1
    r.section("P1  the live service reaches the same decision objects")
    for name, (a, b) in SHARED.items():
        r.ok(f"P1  {name} is the same object", a is b,
             f"{getattr(a, '__module__', '?')} vs {getattr(b, '__module__', '?')}")
    r.ok("P1d  the batch root reaches the same gate",
         batch.Stage0Gate is Stage0Gate)
    r.ok("P1e  and the same belief book", batch.BeliefBook is BeliefBook)

    # ------------------------------------------------------------------ P2
    r.section("P2  neither root re-implements a decision")
    live_src = open(os.path.join(_PKG, "live", "service.py"),
                    encoding="utf-8").read()
    tree = ast.parse(live_src)
    defs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    # A local function named after a decision would be a second implementation
    # of it, which is the failure this whole gate exists to catch.
    forbidden = {"propose", "index_score", "observe", "advance", "posterior",
                 "check_peak", "check_cap", "check_lead", "diagnose"}
    clashes = sorted(defs & forbidden)
    r.ok("P2a  live/service.py defines no decision function of its own",
         not clashes, str(clashes))
    r.ok("P2b  it does not import w3 or harness directly",
         "\nimport w3" not in live_src and "\nimport harness" not in live_src,
         "the belief filter is reached through BeliefBook, as the loop does")

    # ------------------------------------------------------------------ P3
    r.section("P3  the executors are different and the layers above are not")
    b = Bench(seed=11)
    try:
        from agent.execution.razorpay_executor import RazorpayExecutor
        r.ok("P3a  the live service holds a RazorpayExecutor",
             isinstance(b.svc.executor, RazorpayExecutor))
        r.ok("P3b  and a Stage0Gate built from the shared class",
             type(b.svc.gate) is Stage0Gate)
        r.ok("P3c  and a BeliefBook built from the shared class",
             type(b.svc.book) is BeliefBook)
        r.ok("P3d  the gate holds the executor, and the service does not "
             "keep a second route to it",
             b.svc.gate._executor is b.svc.executor)

        # THE SAME SCHEDULER CALL, on the live service's own belief. If this
        # ever needed a different signature, the layers would have diverged.
        c, m = b.registered()
        belief = b.svc.book.belief_for(c.seq)
        decision = propose(belief, m.charge_amount_paise / 100.0, day=5,
                           now_t=5 * 24, cycle_close=30 * 24, attempts_used=0)
        r.ok("P3e  the shared scheduler accepts the live belief unchanged",
             hasattr(decision, "proposal") and hasattr(decision, "reason"),
             f"reason={decision.reason}")
    finally:
        b.close()

    # ------------------------------------------------------------------ P4
    r.section("P4  the simulation is untouched by the live work")
    batch_src = open(os.path.join(_PKG, "agent", "batch.py"),
                     encoding="utf-8").read()
    # PARSED, NOT GREPPED. The earlier form asserted the literal text of one
    # call, so it went red on a reformat that changed nothing. The claim is
    # that the simulation root builds a SimExecutor and no other backend.
    built = sorted({n.func.id for n in ast.walk(ast.parse(batch_src))
                    if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name)
                    and n.func.id.endswith("Executor")})
    r.ok("P4a  agent/batch.py builds SimExecutor and no other backend",
         built == ["SimExecutor"], str(built))
    r.ok("P4b  the composition root does not import live/",
         "\nimport live" not in batch_src and "from live" not in batch_src,
         "the core must not depend on the service that wraps it")
    for name in sorted(os.listdir(os.path.join(_PKG, "agent"))):
        if not name.endswith(".py"):
            continue
        text = open(os.path.join(_PKG, "agent", name), encoding="utf-8").read()
        if "from live" in text or "\nimport live" in text:
            r.ok(f"P4c  agent/{name} imports live/", False, "dependency inverted")
            break
    else:
        r.ok("P4c  nothing under agent/ imports live/", True)

    return r.summary()


if __name__ == "__main__":
    raise SystemExit(main())
