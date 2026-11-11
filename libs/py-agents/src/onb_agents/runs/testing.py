"""Test support for runs: synthetic published templates and hire variables. Test-only, never imported
by production code (like `onb_agents.testing`). No real person's data: every value is made up.

`diamond_template()` is the FR-R3 shape: `a` fans out to `b` and `c`, which fan in to `d`; `e` depends
on `a` and runs only for engineers, and `f` depends on `e`, so a sales hire shows a skipped dependency
that must not block.
"""

from __future__ import annotations

from typing import Any

from onb_contracts.templates import Template

__all__ = ["DIAMOND_KEY", "diamond_template", "hire_variables_for"]

DIAMOND_KEY = "diamond-test"


def _task(task_id: str, *depends_on: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": f"Task {task_id.upper()}",
        "agent": "provisioning_agent",
        "action": f"do_{task_id}",
        "depends_on": list(depends_on),
        **extra,
    }


def diamond_template(*, version: int = 1, key: str = DIAMOND_KEY, extra_task: bool = False) -> Template:
    """A published diamond template. `extra_task` adds `g` after `d` (a later version's change)."""
    tasks = [
        _task("a", sla={"due": "start_date - 7d"}),
        _task("b", "a"),
        _task("c", "a"),
        _task("d", "b", "c"),
        _task("e", "a", when="role == 'engineer'"),
        _task("f", "e"),
    ]
    if extra_task:
        tasks.append(_task("g", "d"))
    return Template.model_validate(
        {
            "key": key,
            "name": "Diamond (test)",
            "version": version,
            "status": "published",
            "variables": [
                {"name": "role", "type": "enum", "values": ["engineer", "designer", "sales"]},
                {"name": "location", "type": "string"},
                {"name": "start_date", "type": "date"},
                {"name": "manager_id", "type": "user_ref"},
            ],
            "tasks": tasks,
        }
    )


def hire_variables_for(role: str = "engineer") -> dict[str, str]:
    return {"role": role, "location": "Testville", "start_date": "2026-12-01", "manager_id": "u_test_manager"}
