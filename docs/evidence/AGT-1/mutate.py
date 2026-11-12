"""AGT-1 mutation checks: break each guard on purpose, run the tests that guard it, record which fail.

    uv run --frozen python docs/evidence/AGT-1/mutate.py > docs/evidence/AGT-1/mutation-checks.txt

Each mutation replaces one exact snippet (it must occur once), runs its suite (unit: the orchestrator's
input tests and the py-agents manifest tests; int: the real-stack tests of boxes 2 and 3, which start a
MongoDB container), records the failing test ids, then restores the file byte for byte. The run ends by
checking that no file differs from before. (Pattern of docs/evidence/RUN-3/mutate.py.)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
INPUT = REPO / "apps/orchestrator/src/onb_orchestrator/agent_input.py"
CONFIG = REPO / "config/agents"
SUITES = {
    "unit-orch": ["apps/orchestrator/tests/test_agent_input.py"],
    "unit-agents": ["libs/py-agents/tests/test_task_agent_manifests.py"],
    "int": ["apps/mcp-hub/tests_int/test_task_agents_int.py"],
}

# (id, what, file, old, new, suites)
MUTATIONS: list[tuple[str, str, Path, str, str, tuple[str, ...]]] = [
    (
        "M1",
        "the hire reference ignores the org (two orgs' hires share a reference)",
        INPUT,
        '"hire_key": opaque_ref(org_id, "hire", hire_id),',
        '"hire_key": opaque_ref("", "hire", hire_id),',
        ("unit-orch",),
    ),
    (
        "M2",
        "the task reference ignores the run (a later run's task reuses an earlier run's key)",
        INPUT,
        'opaque_ref(org_id, "task", run_id, str(template_task["id"]))',
        'opaque_ref(org_id, "task", str(template_task["id"]))',
        ("unit-orch",),
    ),
    (
        "M3",
        "reference parts are not length-prefixed (parts can be shifted into each other)",
        INPUT,
        'f"{len(p)}:{p}"',
        "p",
        ("unit-orch",),
    ),
    (
        "M4",
        "the whole hire record is given to the agent, not just its non-PII variables",
        INPUT,
        '"variables": hire_variables(hire) if hire else {},',
        '"variables": dict(hire) if hire else {},',
        ("unit-orch",),
    ),
    (
        "M5",
        "the agent is given no variables (role and location are lost)",
        INPUT,
        '"variables": hire_variables(hire) if hire else {},',
        '"variables": {},',
        ("unit-orch", "int"),
    ),
    (
        "M6",
        "every task gets the same task reference (the mocks' idempotency key collides across runs)",
        INPUT,
        'opaque_ref(org_id, "task", run_id, str(template_task["id"]))',
        '"0" * 24',
        ("unit-orch", "int"),
    ),
    (
        "M7",
        "the templates agent cannot read the hire record (name and start date unreachable)",
        CONFIG / "templates_agent.yaml",
        "read: [new_hires, run_tasks, templates]",
        "read: [run_tasks, templates]",
        ("unit-agents",),
    ),
    (
        "M8",
        "the templates agent may write run tasks (widened beyond its job)",
        CONFIG / "templates_agent.yaml",
        "    tools: [find]\n    collections:\n      read: [new_hires, run_tasks, templates]",
        "    tools: [find, update_one]\n    collections:\n      read: [new_hires, run_tasks, templates]\n      write: [run_tasks]",
        ("unit-agents",),
    ),
    (
        "M9",
        "the db agent may write the hire record (REG-1's stricter version widened)",
        CONFIG / "db_agent.yaml",
        "write: [run_tasks, documents_meta]",
        "write: [run_tasks, documents_meta, new_hires]",
        ("unit-agents",),
    ),
    (
        "M10",
        "the provisioning agent loses the laptop tool (box 2 and the seed run)",
        CONFIG / "provisioning_agent.yaml",
        "tools: [create_bitbucket_account, provision_laptop]",
        "tools: [create_bitbucket_account]",
        ("unit-agents", "int"),
    ),
    (
        "M11",
        "the templates agent loses the notification tool (welcome message cannot be sent)",
        CONFIG / "templates_agent.yaml",
        "tools: [notify_send]",
        "tools: [notify_send_unused]",
        ("int",),
    ),
]


def _pytest(paths: list[str]) -> tuple[int, list[str]]:
    result = subprocess.run(
        ["uv", "run", "--frozen", "pytest", "-q", "-p", "no:cacheprovider", "-rf", *paths],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    failed = [line.split(" ")[1] for line in result.stdout.splitlines() if line.startswith("FAILED ")]
    return result.returncode, failed


def _run(suites: tuple[str, ...]) -> tuple[int, list[str]]:
    code, failed = 0, []
    for suite in suites:
        suite_code, suite_failed = _pytest(SUITES[suite])
        code = code or suite_code
        failed += suite_failed
    return code, sorted(set(failed))


def main() -> int:
    originals = {path: path.read_bytes() for _, _, path, _, _, _ in MUTATIONS}
    code, failed = _run(tuple(SUITES))
    print(f"baseline: exit={code}, failed={len(failed)}")
    if code != 0:
        print("baseline is not green; stopping")
        return 1
    survivors: list[str] = []
    for mid, what, path, old, new, suites in MUTATIONS:
        text = originals[path].decode()
        assert text.count(old) == 1, f"{mid}: snippet must occur exactly once in {path}"
        path.write_text(text.replace(old, new))
        try:
            _, failed = _run(suites)
        finally:
            path.write_bytes(originals[path])
        if not failed:
            survivors.append(mid)
        print(f"\n{mid} {'KILLED' if failed else 'SURVIVED'}: {what}\n  file: {path.relative_to(REPO)}")
        print(f"  suites: {', '.join(suites)}\n  failing tests ({len(failed)}):")
        for test in failed[:6]:
            print(f"    {test}")
        if len(failed) > 6:
            print(f"    ... and {len(failed) - 6} more")
    differing = [str(p.relative_to(REPO)) for p, data in originals.items() if p.read_bytes() != data]
    print(f"\nsource diff after all mutations: {len(differing)} files differ {differing}")
    print(f"mutations: {len(MUTATIONS)}, killed: {len(MUTATIONS) - len(survivors)}, survivors: {survivors or 'none'}")
    return 0 if not survivors and not differing else 1


if __name__ == "__main__":
    sys.exit(main())
