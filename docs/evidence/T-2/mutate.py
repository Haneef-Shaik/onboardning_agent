"""T-2 mutation checks: break each guard on purpose, run the unit and integration tests, record which
named tests failed, restore the file. Run from the repo root with the compose stack up:

    python3 docs/evidence/T-2/mutate.py > docs/evidence/T-2/mutation-checks.txt
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = "libs/py-data/src/onb_data/safety_net.py"
CONFTEST = "libs/py-data/tests_int/conftest.py"

# id: (what, file, [(old, new), ...])
MUTATIONS: dict[str, tuple[str, str, list[tuple[str, str]]]] = {
    "M1": (
        "box 1: every filter counts as scoped",
        SRC,
        [
            (
                '    """The org a scoped filter pins, or None when it pins none."""\n',
                '    """The org a scoped filter pins, or None when it pins none."""\n    return "org_any"\n',
            )
        ],
    ),
    "M2": (
        "box 1: the listener records nothing",
        SRC,
        [("        if self._allowed.get() is not None:\n            return", "        if True:\n            return")],
    ),
    "M3": (
        "box 2: the guard fixture is not autouse",
        CONFTEST,
        [
            (
                "@pytest.fixture(autouse=True)\ndef tenant_safety_net_guard",
                "@pytest.fixture\ndef tenant_safety_net_guard",
            )
        ],
    ),
    "M4": (
        "box 2: the listener is not registered globally",
        CONFTEST,
        [
            ("    install_safety_net()\n\n\ndef pytest_sessionfinish", "    return None\n\n\ndef pytest_sessionfinish"),
            ("    return install_safety_net()\n", "    return TenantCommandListener()\n"),
        ],
    ),
    "M5": (
        "box 2: the guard does not check after the test",
        SRC,
        [("        self.check()\n        yield\n        self.check()\n", "        self.check()\n        yield\n")],
    ),
    "M6": (
        "box 3: a tenant index need not start with org_id",
        SRC,
        [("    if fields[0] != ORG_FIELD:\n", "    if False:\n")],
    ),
    "M7": (
        "an org_id operator ($ne, $in, $exists) counts as scoped",
        SRC,
        [
            (
                '    if isinstance(value, Mapping) and set(value) == {"$eq"}:\n',
                '    if isinstance(value, Mapping):\n        return "org_any"\n    if False:\n',
            )
        ],
    ),
    "M10": (
        "review fix: the update body is not checked",
        SRC,
        [
            ('    if list_field == "updates":\n', "    if False:\n"),
            ('        return _update_body_violation(command["update"], org)\n', "        return None\n"),
        ],
    ),
    "M11": (
        "review fix: database-level change streams pass",
        SRC,
        [
            (
                '        if any(isinstance(stage, Mapping) and "$changeStream" in stage',
                '        if False and any(isinstance(stage, Mapping) and "$changeStream" in stage',
            )
        ],
    ),
    "M12": (
        "review fix: explain is not unwrapped",
        SRC,
        [('    if command_name == "explain":\n        return _explain_violation(command, database)\n', "")],
    ),
    "M8": (
        "cross-collection stages are not checked",
        SRC,
        [
            (
                '        return "aggregate without a pipeline list"\n    foreign = _foreign_stage(pipeline, database)\n',
                '        return "aggregate without a pipeline list"\n    foreign = None\n',
            )
        ],
    ),
    "M9": (
        "allowing_unscoped never resets",
        SRC,
        [("            self._allowed.reset(token)\n", "            pass\n")],
    ),
}


def run(args: list[str]) -> str:
    env = {**os.environ}
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, env=env, check=False)
    return result.stdout + result.stderr


def failures(output: str) -> list[str]:
    names = re.findall(r"^(?:FAILED|ERROR) (\S+)", output, flags=re.M)
    return sorted({name.split("::", 1)[-1] for name in names})


def summary(output: str) -> str:
    lines = [line for line in output.splitlines() if re.search(r"\d+ (passed|failed|error)", line)]
    return lines[-1].strip("= ") if lines else "(no summary line)"


def main() -> int:
    unit = ["uv", "run", "--frozen", "pytest", "libs/py-data/tests", "-q", "-p", "no:cacheprovider"]
    integ = ["uv", "run", "--frozen", "pytest", "libs/py-data/tests_int", "-q", "-p", "no:cacheprovider"]
    for mid, (what, rel, edits) in MUTATIONS.items():
        path = ROOT / rel
        original = path.read_text()
        mutated = original
        for old, new in edits:
            if mutated.count(old) != 1:
                print(f"{mid}: mutation target not found exactly once in {rel}: {old[:60]!r}")
                return 1
            mutated = mutated.replace(old, new)
        path.write_text(mutated)
        try:
            out_unit, out_int = run(unit), run(integ)
        finally:
            path.write_text(original)
        print(f"## {mid} ({rel}): {what}")
        print(f"unit: {summary(out_unit)}")
        print(f"int:  {summary(out_int)}")
        for name in failures(out_unit):
            print(f"  unit FAILED {name}")
        for name in failures(out_int):
            print(f"  int  FAILED {name}")
        caught = bool(failures(out_unit) or failures(out_int))
        print(f"caught: {'yes' if caught else 'NO'}\n")
    diff = run(["git", "status", "--porcelain", "--", SRC, CONFTEST]).strip()
    print(f"worktree after all mutations: {'clean' if not diff else diff}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
