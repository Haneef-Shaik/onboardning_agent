"""The task graph of a template: dependencies, a deterministic order and the cycles that prevent one.

Edges run from a dependency to its dependent (`collect_docs -> manager_approval`), the order tasks
run in. Unknown dependencies are left out here; the validator reports them separately.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence

__all__ = ["cycles", "topological_order"]


def _dependents(tasks: Mapping[str, Sequence[str]]) -> dict[str, list[str]]:
    """`{task: [tasks that depend on it]}`, in template order, ignoring unknown dependencies."""
    dependents: dict[str, list[str]] = {task_id: [] for task_id in tasks}
    for task_id, depends_on in tasks.items():
        for dependency in depends_on:
            if dependency in dependents:
                dependents[dependency].append(task_id)
    return dependents


def topological_order(tasks: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    """Kahn's order, ties broken by template order; `()` when the graph has a cycle."""
    dependents = _dependents(tasks)
    position = {task_id: index for index, task_id in enumerate(tasks)}
    waiting = {task_id: sum(1 for d in depends_on if d in tasks) for task_id, depends_on in tasks.items()}
    ready = sorted((task_id for task_id, count in waiting.items() if count == 0), key=position.__getitem__)
    order: list[str] = []
    while ready:
        task_id = ready.pop(0)
        order.append(task_id)
        for dependent in dependents[task_id]:
            waiting[dependent] -= 1
            if waiting[dependent] == 0:
                ready.append(dependent)
                ready.sort(key=position.__getitem__)
    return tuple(order) if len(order) == len(tasks) else ()


def _reachable(start: str, dependents: Mapping[str, Sequence[str]]) -> set[str]:
    seen: set[str] = set()
    stack = list(dependents[start])
    while stack:
        task_id = stack.pop()
        if task_id not in seen:
            seen.add(task_id)
            stack.extend(dependents[task_id])
    return seen


def _shortest_loop(start: str, dependents: Mapping[str, Sequence[str]], within: set[str]) -> list[str]:
    """The shortest path from `start` back to itself, staying inside its strongly connected part."""
    previous: dict[str, str] = {}
    queue = deque([start])
    while queue:
        task_id = queue.popleft()
        for dependent in dependents[task_id]:
            if dependent == start:
                path = [task_id]
                while path[-1] != start:
                    path.append(previous[path[-1]])
                return [*reversed(path), start]
            if dependent in within and dependent not in previous:
                previous[dependent] = task_id
                queue.append(dependent)
    raise AssertionError("a task in a cycle reaches itself")  # pragma: no cover


def cycles(tasks: Mapping[str, Sequence[str]]) -> list[list[str]]:
    """One loop per group of tasks that depend on each other, each starting at its first task."""
    dependents = _dependents(tasks)
    reach = {task_id: _reachable(task_id, dependents) for task_id in tasks}
    found: list[list[str]] = []
    covered: set[str] = set()
    for task_id in tasks:
        if task_id in covered or task_id not in reach[task_id]:
            continue
        group = {other for other in reach[task_id] if task_id in reach[other]} | {task_id}
        covered |= group
        found.append(_shortest_loop(task_id, dependents, group))
    return found
