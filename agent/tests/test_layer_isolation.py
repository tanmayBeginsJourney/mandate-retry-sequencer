"""THE IMPORT-GRAPH GATES. Five rules about who may reach whom.

These ship first because they are cheap and because they are what keeps the
architecture true under deadline pressure. An architectural boundary that
exists only in a design document is a boundary that gets crossed at 2am on
4 September by someone who needs one number.

EVERY GATE NEEDS A NAMED MUTANT THAT TRIPS IT (docs/05_TEST_DESIGN.md: "a gate
earns its place only if you can name, in advance, a concrete broken
implementation that would make it fail"). Each rule below carries its mutant in
the docstring, and `--mutants` actually RUNS them: the checker is pointed at
synthetic modules containing the forbidden import and must go red on every one.
A gate that no mutant can trip reports VACUOUS, exactly as `sim/gate.py` does,
and VACUOUS is treated as failure.

THE MUTANTS ARE SYNTHETIC SOURCE, NOT EDITS TO THE REAL TREE, and they touch no
counter. That is rule 1a, added after error 11: a mutant may create illegal
state and nothing else. A mutant that wrote to the scoreboard would be grading
itself, which is the defect this whole discipline exists to prevent.
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
     "only agent/constraints/stage0.py may hold an executor",
     lambda p: p.endswith(".py"),
     ("agent.execution",),
     ("constraints/stage0.py", "batch.py", "tests/test_parity_vs_harness.py",
      "tests/test_stage0_enforces.py", "tests/test_action_ablation.py"),
     "add `from agent.execution.sim_executor import SimExecutor` to "
     "agent/loop.py so the loop can attempt a debit without the gate"),

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


def check_tree(root: str, files: list[str] | None = None) -> list[str]:
    """Returns a list of violation strings. Empty means clean."""
    problems = []
    for path in (files if files is not None else _py_files(root)):
        rel = _rel(path)
        for rid, desc, matches, forbidden, exempt, _mutant in RULES:
            if not matches(rel) or rel in exempt:
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
    "I3": ("constraints/auditor.py",
           "from agent.constraints.rules import check_peak\n"),
    "I4": ("policy/_mutant.py", "from agent.llm.fallback import RuleBasedDiagnoser\n"),
    "I5": ("ports.py", "from agent.policy.timing import propose\n"),
}


def run_mutants() -> tuple[list[str], list[str]]:
    """Write each forbidden import to a scratch file and require the checker to
    catch it. Returns (tripped, missed)."""
    import tempfile
    tripped, missed = [], []
    for rid, (relpath, src) in MUTANT_SOURCES.items():
        with tempfile.TemporaryDirectory() as td:
            full = os.path.join(td, relpath)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write('"""synthetic mutant. touches no counter."""\n' + src)

            # Point the checker at the mutant using the REAL relative path, so
            # the rule matchers see the module where it would really live.
            saved = _rel
            problems = []
            for r, desc, matches, forbidden, exempt, _m in RULES:
                if r != rid:
                    continue
                if not matches(relpath) or relpath in exempt:
                    continue
                for mod, lineno in _imports(full):
                    if _violates(mod, forbidden):
                        problems.append(f"{r} {relpath}:{lineno} {mod}")
            (tripped if problems else missed).append(rid)
    return tripped, missed


def main() -> int:
    print("=" * 70)
    print("LAYER ISOLATION -- import-graph gates")
    print("=" * 70)
    for rid, desc, _m, forbidden, exempt, mutant in RULES:
        print(f"  {rid}  {desc}")
        print(f"      forbidden: {', '.join(forbidden)}")
        if exempt:
            print(f"      exempt:    {', '.join(exempt)}")
        print(f"      mutant:    {mutant}")
    print()

    tripped, missed = run_mutants()
    print(f"MUTANTS: {len(tripped)}/{len(MUTANT_SOURCES)} tripped the checker")
    if missed:
        print(f"  VACUOUS -- these rules cannot be tripped: {missed}")
        print("  A gate no mutant can fail is not a gate. Treated as FAIL.")
    print()

    problems = check_tree(AGENT)
    if problems:
        print(f"FAIL -- {len(problems)} isolation violation(s):")
        for p in problems:
            print(f"  {p}")
    else:
        print(f"PASS -- {len(_py_files(AGENT))} files, 0 isolation violations")

    return 1 if (problems or missed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
