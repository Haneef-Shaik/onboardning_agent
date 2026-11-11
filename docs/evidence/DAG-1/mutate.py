"""DAG-1 mutation checks: break each guard on purpose, run the DAG tests, record which named tests fail.

    uv run --frozen python docs/evidence/DAG-1/mutate.py > docs/evidence/DAG-1/mutation-checks.txt

Each mutation replaces one exact snippet (it must occur once), runs the two DAG test files, records the
failing test ids, then restores the file. The run ends by checking that no source file differs.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SRC = REPO / "libs/py-agents/src/onb_agents/dag"
TESTS = ["libs/py-agents/tests/test_dag_compiler.py", "libs/py-agents/tests/test_dag_properties.py"]

C, V, S = SRC / "compiler.py", SRC / "variables.py", SRC / "sla.py"

MUTATIONS: list[tuple[str, str, Path, str, str]] = [
    (
        "M1",
        "a false `when` never skips (box 1)",
        C,
        "    if holds:\n        return None",
        "    if True:\n        return None",
    ),
    (
        "M2",
        "skipped tasks block their dependents (box 2)",
        C,
        'finished = set(done) | {task.id for task in self.tasks if task.status == "skipped"}',
        "finished = set(done)",
    ),
    (
        "M3",
        "skipped tasks are offered as ready (boxes 1, 2)",
        C,
        'if task.status == "pending" and task.id not in done',
        "if task.id not in done",
    ),
    ("M4", "cycles are not refused (box 2)", C, "    if not order:\n", "    if False:\n"),
    (
        "M5",
        "unknown dependencies are not refused (box 2)",
        C,
        '                raise CompileError(CompileReason.UNKNOWN_DEPENDENCY, f"depends on unknown task {dependency}", task.id)',
        "                pass",
    ),
    (
        "M6",
        "the hash depends on the call, not the compile (box 3)",
        C,
        'return "sha256:" + hashlib.sha256(canonical.encode("ascii")).hexdigest()',
        'return "sha256:" + hashlib.sha256((canonical + str(id(document))).encode("ascii")).hexdigest()',
    ),
    (
        "M7",
        "the hash covers only the template key and version (box 3)",
        C,
        "        digest = CompiledDag.hash_of(draft.document(include_hash=False))",
        '        digest = CompiledDag.hash_of({"k": template.key, "v": version})',
    ),
    (
        "M8",
        "the canonical form is not key-sorted (box 3, pinned hash)",
        C,
        "json.dumps(document, sort_keys=True,",
        "json.dumps(document, sort_keys=False,",
    ),
    ("M9", "the SLA offset sign is ignored (box 4)", S, "timedelta(days=offset)", "timedelta(days=abs(offset))"),
    ("M10", "an hour offset counts as days (box 4)", S, "timedelta(hours=offset)", "timedelta(days=offset)"),
    (
        "M11",
        "an hour offset is not anchored to UTC (box 4)",
        S,
        "datetime.combine(day, time(), tzinfo=UTC)",
        "datetime.combine(day, time())",
    ),
    (
        "M12",
        "an SLA on a non-date variable is not refused",
        S,
        'if variable is None or variable.type != "date":',
        "if variable is None:",
    ),
    (
        "M13",
        "an SLA date outside the calendar crashes instead of failing closed",
        S,
        "    except OverflowError:\n",
        "    except ZeroDivisionError:\n",
    ),
    (
        "M14",
        "an undecidable condition reads as false",
        C,
        '        raise CompileError(CompileReason.BAD_CONDITION, f"when cannot be decided ({error})", task.id) from None',
        "        holds = False",
    ),
    (
        "M15",
        "a missing variable is not named as missing",
        C,
        '            raise CompileError(CompileReason.MISSING_VARIABLE, f"when reads {name}, which the hire lacks", task.id)',
        "            pass",
    ),
    (
        "M16",
        "an unpublished template compiles",
        C,
        'if template.status != "published" or template.version is None:',
        "if template.version is None:",
    ),
    ("M17", "a datetime passes as a date", V, "if type(value) is date:", "if isinstance(value, date):"),
    (
        "M18",
        "enum membership is not checked",
        V,
        "value in variable.values else None",
        "True else None",
    ),
    (
        "M19",
        "a bool passes as a number",
        V,
        "if isinstance(value, bool) or not isinstance(value, int | float):",
        "if not isinstance(value, int | float):",
    ),
    ("M20", "a non-finite number passes", V, "return value if math.isfinite(value) else None", "return value"),
    (
        "M21",
        "a bool check accepts anything",
        V,
        "return value if isinstance(value, bool) else None",
        "return value  # type: ignore[return-value]",
    ),
    (
        "M22",
        "a text check accepts anything",
        V,
        "return value if isinstance(value, str) and 0 < len(value) <= _MAX_TEXT else None",
        "return value  # type: ignore[return-value]",
    ),
    (
        "M23",
        "a non-mapping of variables is accepted",
        V,
        "    if not isinstance(variables, Mapping):\n",
        "    if False:\n",
    ),
    (
        "M24",
        "compiled tasks share the template's inputs dict",
        C,
        "task=task.model_copy(deep=True),",
        "task=task,",
    ),
    (
        "M25",
        "ready ignores unknown done ids",
        C,
        "        if unknown:\n            raise KeyError",
        "        if False:\n            raise KeyError",
    ),
    (
        "M26",
        "inputs that JSON cannot carry are hashed anyway",
        C,
        "ensure_ascii=True, allow_nan=False)",
        "ensure_ascii=True, allow_nan=True)",
    ),
    ("M27", "a non-Template is accepted", C, "    if not isinstance(template, Template):\n", "    if False:\n"),
    (
        "M28",
        "the skip reason drops the condition",
        C,
        'return redact(f"when condition `{task.when}` is false for this hire")',
        'return "skipped"',
    ),
    # `\d` in place of `[0-9]` is an equivalent mutant: `date.fromisoformat` refuses non-ASCII digits too.
    (
        "M29",
        "the YYYY-MM-DD pattern is not enforced",
        V,
        "if isinstance(value, str) and _ISO_DATE.fullmatch(value):",
        "if isinstance(value, str):",
    ),
    (
        "M30",
        "a missing SLA variable is not named as missing",
        S,
        '        raise CompileError(CompileReason.MISSING_VARIABLE, f"sla.due reads {name}, which the hire lacks", task_id)',
        "        pass",
    ),
]


def run_tests() -> tuple[int, list[str]]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *TESTS, "-q", "-p", "no:cacheprovider", "--no-header", "-rf"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    failed = [
        line.split(" ", 1)[1].split(" - ")[0] for line in result.stdout.splitlines() if line.startswith("FAILED ")
    ]
    return result.returncode, failed


def main() -> int:
    survivors: list[str] = []
    originals = {path: path.read_text() for path in (C, V, S)}
    for ident, what, path, old, new in MUTATIONS:
        source = originals[path]
        if source.count(old) != 1:
            print(f"{ident} SNIPPET NOT FOUND EXACTLY ONCE in {path.name}: {what}")
            survivors.append(ident)
            continue
        path.write_text(source.replace(old, new))
        try:
            code, failed = run_tests()
        finally:
            path.write_text(source)
        if code == 0:
            survivors.append(ident)
            print(f"{ident} SURVIVED  {what}")
        else:
            shown = ", ".join(name.split("::", 1)[1] for name in failed[:4])
            more = f" (+{len(failed) - 4} more)" if len(failed) > 4 else ""
            print(f"{ident} killed    {what}\n    failed: {shown}{more}")
    differ = sum(1 for path, text in originals.items() if path.read_text() != text)
    print(f"\nsource diff after all mutations: {differ} files differ")
    print(
        f"mutations: {len(MUTATIONS)}, killed: {len(MUTATIONS) - len(survivors)}, survivors: {', '.join(survivors) or 'none'}"
    )
    return 1 if survivors or differ else 0


if __name__ == "__main__":
    raise SystemExit(main())
