"""Read-only graph helpers over a template's tasks: ancestors, cycles, variable references, diffs.

Works on plain dicts (the agent's raw output), so a check can grade an output that fails the schema.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

__all__ = [
    "ancestors",
    "apply_diff",
    "find_cycle",
    "referenced_names",
    "task_action",
    "tasks_of",
]

Task = Mapping[str, Any]

_STRING = re.compile(r"'([^'\\]*)'|\"([^\"\\]*)\"")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_KEYWORDS = frozenset({"and", "or", "not", "in", "true", "false", "True", "False", "None", "null"})
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def tasks_of(template: Any) -> list[Task]:
    """The template's tasks that are objects; anything else is ignored here (`schema_valid` reports it)."""
    tasks = template.get("tasks") if isinstance(template, Mapping) else None
    if not isinstance(tasks, list):
        return []
    return [task for task in tasks if isinstance(task, Mapping)]


def task_action(task: Task, approval_actions: Iterable[str] = ("approval",)) -> str:
    """The action a task counts as: approval tasks count as `approval` (README `task_coverage`)."""
    if task.get("type") == "approval":
        return "approval"
    action = task.get("action")
    if isinstance(action, str) and action in set(approval_actions):
        return "approval"
    return str(action)


def _deps(task: Task) -> list[str]:
    deps = task.get("depends_on", [])
    return [d for d in deps if isinstance(d, str)] if isinstance(deps, list) else []


def ancestors(tasks: Sequence[Task], task_id: str) -> set[str]:
    """Every task id `task_id` depends on, directly or transitively (cycle-safe)."""
    by_id = {str(t.get("id")): t for t in tasks}
    seen: set[str] = set()
    stack = list(_deps(by_id[task_id])) if task_id in by_id else []
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        if current in by_id:
            stack.extend(_deps(by_id[current]))
    return seen


def find_cycle(tasks: Sequence[Task]) -> list[str] | None:
    """One dependency cycle as a list of ids, or `None` when the graph is acyclic."""
    by_id = {str(t.get("id")): t for t in tasks}
    state: dict[str, int] = {}  # 1 = on the current path, 2 = finished

    def visit(node: str, path: list[str]) -> list[str] | None:
        state[node] = 1
        for dep in _deps(by_id[node]):
            if dep not in by_id:
                continue
            if state.get(dep) == 1:
                return [*path[path.index(dep) :], dep] if dep in path else [node, dep]
            if dep not in state:
                found = visit(dep, [*path, dep])
                if found:
                    return found
        state[node] = 2
        return None

    for task_id in by_id:
        if task_id not in state:
            cycle = visit(task_id, [task_id])
            if cycle:
                return cycle
    return None


def referenced_names(expression: str) -> tuple[set[str], set[str]]:
    """(identifiers, string literals) referenced by a `when` expression."""
    literals = {a or b for a, b in _STRING.findall(expression)}
    bare = _STRING.sub(" ", expression)
    identifiers = {name for name in _IDENT.findall(bare) if name not in _KEYWORDS}
    return identifiers, literals


def placeholders(value: Any) -> set[str]:
    """Variable names used as `{{name}}` anywhere inside a task's inputs."""
    if isinstance(value, str):
        return set(_PLACEHOLDER.findall(value))
    if isinstance(value, Mapping):
        return set().union(*(placeholders(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(placeholders(v) for v in value)) if value else set()
    return set()


def apply_diff(base: Mapping[str, Any], diff: Mapping[str, Any]) -> dict[str, Any]:
    """A new template: `base` with the change request's removals, updates and additions applied."""
    removed = {r for r in diff.get("remove_tasks", []) if isinstance(r, str)}
    updates = {u["id"]: u for u in diff.get("update_tasks", []) if isinstance(u, Mapping) and "id" in u}
    tasks: list[dict[str, Any]] = []
    for task in tasks_of(base):
        if task.get("id") in removed:
            continue
        merged = {**task, **{k: v for k, v in updates.get(task.get("id"), {}).items() if v is not None}}
        merged["depends_on"] = [d for d in _deps(merged) if d not in removed]
        tasks.append(merged)
    tasks.extend(dict(t) for t in diff.get("add_tasks", []) if isinstance(t, Mapping))
    return {**base, "tasks": tasks}
