"""THE IMPORT-GRAPH GATES. Seven rules about who may reach whom.

These ship first because they are cheap and because they are what keeps the
architecture true under deadline pressure. An architectural boundary that
exists only in a design document is a boundary that gets crossed at 2am by
someone who needs one number.

EVERY GATE NEEDS A NAMED MUTANT THAT TRIPS IT (docs/results.md: "a gate
earns its place only if you can name, in advance, a concrete broken
implementation that would make it fail"). Each rule below carries its mutant in
the docstring, and the mutants actually RUN: the checker is pointed at
synthetic modules containing the forbidden import and must go red on every one.
A gate that no mutant can trip reports VACUOUS, exactly as `sim/gate.py` does,
and VACUOUS is treated as failure.

THE MUTANTS ARE SYNTHETIC SOURCE, NOT EDITS TO THE REAL TREE, and they touch no
counter. That is the mutation rule added after error 11: a mutant may create
illegal state and nothing else. A mutant that wrote to the scoreboard would be
grading itself, which is the defect this whole discipline exists to prevent.

---------------------------------------------------------------------------
I2 WAS SPECIFIED WRONG, AND WAS RED FOR IT. REVISED 2 SEPTEMBER 2026.

As written, I2 said "only `agent/constraints/stage0.py` may hold an executor"
and applied that to EVERY `.py` file under `agent/`, with a hand-maintained
list of exempt test files. It reported eleven violations, and none of them was
the defect the rule is for:

  * FOUR were modules INSIDE `agent/execution/` importing their own siblings --
    `razorpay_executor.py` importing `smtp_delivery.py`, and so on. A layer
    cannot cross its own boundary. "Who may reach the execution layer" is a
    question about modules OUTSIDE it, and the matcher never said so.

  * SEVEN were tests that construct an executor on purpose, added after the
    exempt list was last touched. The list is a central register of decisions
    taken elsewhere, and it drifted exactly as such a register does. Widening
    it again would repair the symptom and leave the mechanism.

The rule is therefore SPLIT, because it was carrying two different invariants:

  I2   THE ARCHITECTURE. No module in the shipping tree may import
       `agent.execution`, except the constraint gate and the composition root.
       `agent/execution/**` is the layer itself and is out of scope;
       `agent/tests/**` is the measuring apparatus and is I2T's business.
       The named mutant -- `agent/loop.py` reaching for `SimExecutor` -- is a
       shipping module, so it still trips this rule.

  I2T  THE DISCIPLINE. A test MAY hold an executor, and several must in order
       to exercise the gate at all. It must say so in the file, on a line
       reading `# I2-EXEMPT: <reason>`. The declaration travels with the file
       that needs it, so adding a test cannot silently widen the boundary and
       there is no central list to drift. `agent/batch.py` already exposes
       `at_risk_cycles()` and `unwinnable_cycles()` for the tests that want the
       world's opinion rather than an executor; reach for those first.

  I6   Added in the same pass, because taking `agent/execution/**` out of I2's
       scope left it unchecked. The executor is a leaf: it may know
       `agent.ports` and `agent.audit` and nothing further up. It held on the
       day it was written and is now asserted.

This is a revision of a wrong invariant, not a relaxation of a right one. The
count of violations went from eleven to zero because eleven were not
violations of anything the architecture claims.
---------------------------------------------------------------------------
"""
from __future__ import annotations

import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.dirname(HERE)
PKG = os.path.dirname(AGENT)
if PKG not in sys.path:
    sys.path.insert(0, PKG)

# ---------------------------------------------------------------- the rules
# (rule id, description, matcher over module path, forbidden import prefixes,
#  exempt modules, the named mutant)
RULES = [
    ("I1",
     "agent/llm must not reach the belief filter, the world, the gate, or the "
     "timing layer",
     lambda p: p.startswith("llm/"),
     ("w3", "harness", "agent.policy", "agent.execution", "agent.constraints"),
     (),
     "add `import w3` to agent/llm/fallback.py so the diagnoser can read "
     "p_success and start choosing debit days"),

    ("I2",
     "in the shipping tree only constraints/stage0.py and the composition "
     "root may hold an executor",
     # Scope: shipping modules. NOT `agent/execution/**`, which is the layer
     # itself, and NOT `agent/tests/**`, which I2T covers. See the module
     # docstring for why the old `p.endswith('.py')` was wrong.
     lambda p: (p.endswith(".py")
                and not p.startswith("execution/")
                and not p.startswith("tests/")),
     ("agent.execution",),
     # Composition roots only. Nothing else in the shipping tree has ever been
     # exempted and nothing should be -- if a module under agent/ that is not
     # batch.py needs an executor, that is the rule working.
     ("constraints/stage0.py", "batch.py"),
     "add `from agent.execution.sim_executor import SimExecutor` to "
     "agent/loop.py so the loop can attempt a debit without the gate"),

    ("I2T",
     "a test that holds an executor must declare it with `# I2-EXEMPT:`",
     lambda p: p.startswith("tests/"),
     ("agent.execution",),
     (),
     "add `from agent.execution.sim_executor import SimExecutor` to a test "
     "under agent/tests/ without an `# I2-EXEMPT:` line, so the boundary "
     "widens without anyone deciding to widen it"),

    ("I6",
     "the execution layer is a leaf and may not reach back up",
     lambda p: p.startswith("execution/"),
     # `w3` and `harness` are NOT forbidden here. `SimExecutor` is the adapter
     # to the simulated world and reproduces `harness.run`'s dispatch half
     # bit-exactly; forbidding the world would forbid the backend from being a
     # backend. What this rule stops is the executor reaching UP into the
     # layers that are supposed to be deciding on its behalf.
     ("agent.policy", "agent.llm", "agent.constraints", "agent.context",
      "agent.loop", "agent.batch", "agent.recovery", "agent.metrics",
      "agent.tests"),
     (),
     "add `from agent.constraints.stage0 import Stage0Gate` to "
     "agent/execution/sim_executor.py so the executor can adjudicate its own "
     "legality"),

    ("I3",
     "the auditor must not share code with the enforcer",
     lambda p: p == "constraints/auditor.py",
     ("agent.constraints.rules", "agent.constraints.stage0"),
     (),
     "make auditor.py call rules.check_peak instead of re-deriving it, so the "
     "independent recount becomes the enforcer grading itself"),

    ("I4",
     "the timing layer must not be influenced by the narrative layer",
     lambda p: p.startswith("policy/"),
     ("agent.llm",),
     (),
     "add `from agent.llm.fallback import RuleBasedDiagnoser` to "
     "agent/policy/timing.py and let the diagnosis reorder candidate days"),

    ("I5",
     "ports.py is the shared vocabulary and must depend on no layer",
     lambda p: p == "ports.py",
     ("agent.", "w3", "harness"),
     (),
     "add `from agent.policy.timing import propose` to agent/ports.py, so the "
     "layers can reach each other through the type module"),
]


def _imports(path: str) -> list[tuple[str, int]]:
    """Every module name imported by a file, with line numbers."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.append((a.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:              # relative import
                continue
            if node.module:
                out.append((node.module, node.lineno))
    return out


def _rel(path: str) -> str:
    return os.path.relpath(path, AGENT).replace("\\", "/")


def _py_files(root: str) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", "runs", "_mutants")]
        for f in filenames:
            if f.endswith(".py"):
                out.append(os.path.join(dirpath, f))
    return sorted(out)


def _violates(mod: str, forbidden: tuple[str, ...]) -> str | None:
    for f in forbidden:
        if mod == f.rstrip(".") or mod.startswith(f if f.endswith(".") else f + "."):
            return f
        if mod == f:
            return f
    return None


#: Rules a file may opt out of BY SAYING SO IN ITSELF. The marker line replaces
#: a central exempt list, so the decision to widen a boundary lives in the file
#: that widens it and cannot drift away from it. The reason after the colon is
#: for the next reader; the checker requires only that the marker is present.
DECLARATION = {"I2T": "# I2-EXEMPT:"}


def _declares(path: str, rid: str) -> bool:
    marker = DECLARATION.get(rid)
    if marker is None:
        return False
    with open(path, encoding="utf-8", errors="replace") as fh:
        return marker in fh.read()


def check_tree(root: str, files: list[str] | None = None) -> list[str]:
    """Returns a list of violation strings. Empty means clean."""
    problems = []
    for path in (files if files is not None else _py_files(root)):
        rel = _rel(path)
        for rid, desc, matches, forbidden, exempt, _mutant in RULES:
            if not matches(rel) or rel in exempt:
                continue
            if _declares(path, rid):
                continue
            for mod, lineno in _imports(path):
                hit = _violates(mod, forbidden)
                if hit:
                    problems.append(
                        f"{rid}  {rel}:{lineno}  imports {mod!r} "
                        f"(forbidden: {hit}) -- {desc}")
    return problems


# --------------------------------------------------------------- the mutants
MUTANT_SOURCES = {
    "I1": ("llm/_mutant.py", "import w3\n"),
    "I2": ("_mutant_loop.py",
           "from agent.execution.sim_executor import SimExecutor\n"),
    "I2T": ("tests/test_mutant_undeclared.py",
            "from agent.execution.sim_executor import SimExecutor\n"),
    "I3": ("constraints/auditor.py",
           "from agent.constraints.rules import check_peak\n"),
    "I4": ("policy/_mutant.py", "from agent.llm.fallback import RuleBasedDiagnoser\n"),
    "I5": ("ports.py", "from agent.policy.timing import propose\n"),
    "I6": ("execution/_mutant.py",
           "from agent.constraints.stage0 import Stage0Gate\n"),
}

#: A rule with a declaration escape hatch needs the OPPOSITE canary too: a file
#: that declares must NOT be flagged. Without it `_declares` could be wired to
#: return False always and every mutant would still trip, which is a checker
#: that has quietly lost its exemption path.
DECLARED_SOURCES = {
    "I2T": ("tests/test_mutant_declared.py",
            "# I2-EXEMPT: builds an executor to exercise the gate.\n"
            "from agent.execution.sim_executor import SimExecutor\n"),
}


def _check_synthetic(rid: str, relpath: str, src: str) -> list[str]:
    """Write `src` at `relpath` in a scratch tree and run the REAL checker on
    it, as the module it would be if it lived under `agent/`."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        full = os.path.join(td, relpath)
        os.makedirs(os.path.dirname(full) or td, exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write('"""synthetic mutant. touches no counter."""\n' + src)
        problems = []
        for r, desc, matches, forbidden, exempt, _m in RULES:
            if r != rid or not matches(relpath) or relpath in exempt:
                continue
            if _declares(full, r):
                continue
            for mod, lineno in _imports(full):
                if _violates(mod, forbidden):
                    problems.append(f"{r} {relpath}:{lineno} {mod}")
        return problems


def run_mutants() -> tuple[list[str], list[str], list[str]]:
    """Require the checker to catch every mutant, and to stay silent on every
    declared example. Returns (tripped, missed, false_positives)."""
    tripped, missed = [], []
    for rid, (relpath, src) in MUTANT_SOURCES.items():
        (tripped if _check_synthetic(rid, relpath, src) else missed).append(rid)
    false_pos = [rid for rid, (relpath, src) in DECLARED_SOURCES.items()
                 if _check_synthetic(rid, relpath, src)]
    return tripped, missed, false_pos


def main() -> int:
    print("=" * 70)
    print("LAYER ISOLATION -- import-graph gates")
    print("=" * 70)
    for rid, desc, _m, forbidden, exempt, mutant in RULES:
        print(f"  {rid}  {desc}")
        print(f"      forbidden: {', '.join(forbidden)}")
        if exempt:
            print(f"      exempt:    {', '.join(exempt)}")
        if rid in DECLARATION:
            print(f"      declared:  a file may opt out with a "
                  f"`{DECLARATION[rid]} <reason>` line")
        print(f"      mutant:    {mutant}")
    print()

    tripped, missed, false_pos = run_mutants()
    print(f"MUTANTS: {len(tripped)}/{len(MUTANT_SOURCES)} tripped the checker")
    if missed:
        print(f"  VACUOUS -- these rules cannot be tripped: {missed}")
        print("  A gate no mutant can fail is not a gate. Treated as FAIL.")
    print(f"DECLARED EXAMPLES: {len(DECLARED_SOURCES) - len(false_pos)}"
          f"/{len(DECLARED_SOURCES)} correctly went unflagged")
    if false_pos:
        print(f"  BROKEN -- the declaration path does not work for: {false_pos}")
    print()

    # Every file whose declaration is actually DOING something is named, so the
    # set of widened boundaries is visible without a central list to maintain.
    # A file that carries the marker but imports nothing forbidden is not
    # listed -- this checker's own source mentions the marker and is not an
    # exemption.
    declared = sorted(
        _rel(p) for p in _py_files(AGENT)
        for rid, desc, matches, forbidden, exempt, _m in RULES
        if rid in DECLARATION and matches(_rel(p)) and _rel(p) not in exempt
        and _declares(p, rid)
        and any(_violates(mod, forbidden) for mod, _ln in _imports(p)))
    if declared:
        print(f"FILES DECLARING `# I2-EXEMPT:` ({len(declared)}):")
        for d in declared:
            print(f"  {d}")
        print()

    problems = check_tree(AGENT)
    if problems:
        print(f"FAIL -- {len(problems)} isolation violation(s):")
        for p in problems:
            print(f"  {p}")
    else:
        print(f"PASS -- {len(_py_files(AGENT))} files, 0 isolation violations")

    return 1 if (problems or missed or false_pos) else 0


if __name__ == "__main__":
    raise SystemExit(main())
