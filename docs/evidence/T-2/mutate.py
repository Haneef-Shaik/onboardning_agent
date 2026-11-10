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
QA = "libs/py-data/src/onb_data/queue_allowance.py"
CONFTEST = "libs/py-data/src/onb_data/safety_net_pytest.py"  # the shared plugin every Mongo suite loads
CRYPTO_CONFTEST = "libs/py-crypto/tests_int/conftest.py"
TELEMETRY_CONFTEST = "libs/py-telemetry/tests_int/conftest.py"

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
    # --- attempt 2: the worker-queue allowance (ADR 0002) -----------------------------------------
    "Q1": (
        "queue: the allowance is removed (proves the bus needs it)",
        SRC,
        [("    if queue_command_allowed(command_name, command, str(collection)):\n", "    if False:\n")],
    ),
    "Q2": (
        "queue: the allowance applies to any tenant collection",
        QA,
        [("    if collection not in QUEUE_COLLECTIONS:\n        return False\n", "")],
    ),
    "Q3": (
        "queue: a claim is allowed on dead_letters too",
        QA,
        [("        collection == JOBS\n        and not command.get", "        True\n        and not command.get")],
    ),
    "Q4": (
        "queue: a claim may filter on any field (org_id, _id, payload)",
        QA,
        [("        elif key not in CLAIM_FILTER_FIELDS:\n", "        elif False:\n")],
    ),
    "Q5": (
        "queue: a worker update may write any field (org_id, payload, consumer)",
        QA,
        [(" or not set(fields) <= allowed:\n", ":\n")],
    ),
    "Q6": (
        "queue: a leased write may use any filter",
        QA,
        [('set(query) != {"_id", "lease_token"}', "False")],
    ),
    "Q7": (
        "queue: a leased delete may remove many",
        QA,
        [('        return statement.get("limit") == 1\n', "        return True\n")],
    ),
    "Q8": (
        "queue: a leased update may be multi or upsert",
        QA,
        [('not statement.get("multi") and not statement.get("upsert") and ', "")],
    ),
    "Q9": (
        "queue: a dead letter without org_id is allowed for any reason",
        QA,
        [
            (
                '    return document.get("status") == "dead" and isinstance(reason, str) and reason in ORPHAN_REASONS\n',
                "    return True\n",
            )
        ],
    ),
    "Q10": (
        "queue: the index exception extends to unique indexes",
        SRC,
        [("(collection in QUEUE_COLLECTIONS and not unique)", "collection in QUEUE_COLLECTIONS")],
    ),
    # --- attempt 3: every Mongo suite under the net (goal-verifier attempt 2) ----------------------
    "P1": (
        "suites: py-crypto's conftest no longer loads the plugin",
        CRYPTO_CONFTEST,
        [
            (
                "from onb_data.safety_net_pytest import (  # T-2 tenant safety net (PRD §7.9): registers these with pytest\n"
                "    pytest_configure,\n    pytest_sessionfinish,\n    tenant_safety_net,\n"
                "    tenant_safety_net_guard,\n    unscoped,\n)\n",
                "",
            )
        ],
    ),
    "P2": (
        "suites: py-telemetry's conftest loads only part of the plugin (no guard)",
        TELEMETRY_CONFTEST,
        [("    tenant_safety_net_guard,\n    unscoped,\n)", "    unscoped,\n)")],
    ),
    "P3": (
        "plugin: a violation after the last test no longer fails the run",
        CONFTEST,
        [("        session.exitstatus = pytest.ExitCode.TESTS_FAILED\n", "        pass\n")],
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
    suites = {
        "unit": ["uv", "run", "--frozen", "pytest", "libs/py-data/tests", "-q", "-p", "no:cacheprovider"],
        "int": ["uv", "run", "--frozen", "pytest", "libs/py-data/tests_int", "-q", "-p", "no:cacheprovider"],
        "bus": ["uv", "run", "--frozen", "pytest", "libs/py-eventbus/tests_int", "-q", "-p", "no:cacheprovider"],
        "cryp": ["uv", "run", "--frozen", "pytest", "libs/py-crypto/tests_int", "-q", "-p", "no:cacheprovider"],
        "tele": ["uv", "run", "--frozen", "pytest", "libs/py-telemetry/tests_int", "-q", "-p", "no:cacheprovider"],
        "tool": ["uv", "run", "--frozen", "pytest", "tools/tests/test_safety_net_suites.py", "-q", "-p", "no:cacheprovider"],
    }
    only = set(sys.argv[1:])
    for mid, (what, rel, edits) in MUTATIONS.items():
        if only and mid not in only:
            continue
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
            outputs = {suite: run(args) for suite, args in suites.items()}
        finally:
            path.write_text(original)
        print(f"## {mid} ({rel}): {what}")
        for suite, output in outputs.items():
            print(f"{suite + ':':5} {summary(output)}")
        for suite, output in outputs.items():
            for name in failures(output):
                print(f"  {suite:4} FAILED {name}")
        caught = any(failures(output) for output in outputs.values())
        print(f"caught: {'yes' if caught else 'NO'}\n")
    diff = run(["git", "status", "--porcelain", "--", SRC, QA, CONFTEST, CRYPTO_CONFTEST, TELEMETRY_CONFTEST]).strip()
    print(f"worktree after all mutations: {'clean' if not diff else diff}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
