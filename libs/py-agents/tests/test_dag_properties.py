"""DAG-1 box 2: every compiled graph is acyclic, and every dependent of a skipped task stays schedulable.

Hypothesis generates templates with arbitrary `depends_on` edges (cycles and unknown ids included) and
arbitrary `when` conditions, and hires with arbitrary variables. A graph with an unknown dependency or a
cycle (found here independently of the compiler) must be refused with that reason; every other graph
must compile, and then:
- the compiled graph is acyclic, checked here independently of the compiler (Kahn's algorithm);
- `order` lists each task once, after all its dependencies;
- a simulated scheduler that only ever runs `dag.ready(done)` (FR-R3: ready when every dependency is
  done or skipped) runs every pending task to completion, so no skipped task blocks a dependent;
- a task whose dependencies are all skipped is ready at once.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

import pytest
from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st
from onb_agents.dag import CompiledDag, CompileError, CompileReason, compile_dag
from onb_contracts.templates import Template

ROLES = ("engineer", "designer", "sales")
CONDITIONS = (
    None,
    "role == 'engineer'",
    "role != 'engineer'",
    "role in ['engineer', 'designer']",
    "role not in ['sales']",
    "remote",
    "not remote",
    "remote and role == 'sales'",
    "level >= 3",
    "level < 2 or remote",
)
VARIABLES = (
    {"name": "role", "type": "enum", "values": list(ROLES)},
    {"name": "remote", "type": "boolean"},
    {"name": "level", "type": "number"},
    {"name": "start_date", "type": "date"},
)


def _has_cycle(edges: Mapping[str, Sequence[str]]) -> bool:
    """Independent of the compiler: Kahn's algorithm over known dependencies."""
    waiting = {task: sum(1 for d in deps if d in edges) for task, deps in edges.items()}
    ready = [task for task, count in waiting.items() if count == 0]
    seen = 0
    while ready:
        task = ready.pop()
        seen += 1
        for other, deps in edges.items():
            for dependency in deps:
                if dependency == task:
                    waiting[other] -= 1
                    if waiting[other] == 0:
                        ready.append(other)
    return seen != len(edges)


@st.composite
def templates(draw: st.DrawFn) -> dict[str, Any]:
    size = draw(st.integers(min_value=1, max_value=12))
    ids = [f"t{index}" for index in range(size)]
    allow_cycles = draw(st.integers(0, 3)) == 0
    ghost_at = draw(st.integers(0, 10 * size))  # an unknown dependency in about one template in ten
    tasks = []
    for index, task_id in enumerate(ids):
        pool = ids if allow_cycles else ids[:index]
        depends_on = draw(st.lists(st.sampled_from(pool), unique=True, max_size=4)) if pool else []
        if index == ghost_at:
            depends_on = [*depends_on, "ghost"]
        task: dict[str, Any] = {
            "id": task_id,
            "title": f"Task {index}",
            "agent": "db_agent",
            "action": "assign_training",
            "depends_on": depends_on,
        }
        condition = draw(st.sampled_from(CONDITIONS))
        if condition is not None:
            task["when"] = condition
        if draw(st.booleans()):
            task["sla"] = {"due": f"start_date - {draw(st.integers(0, 30))}d"}
        tasks.append(task)
    return {"key": "generated", "version": 1, "status": "published", "variables": list(VARIABLES), "tasks": tasks}


hires = st.fixed_dictionaries(
    {
        "role": st.sampled_from(ROLES),
        "remote": st.booleans(),
        "level": st.integers(0, 5),
        "start_date": st.dates(min_value=date(1000, 1, 1)).map(date.isoformat),
    }
)


def _run_to_completion(dag: CompiledDag) -> list[str]:
    """Run only what `ready` offers, one task at a time, until nothing is ready."""
    done: set[str] = set()
    ran: list[str] = []
    while ready := dag.ready(done=done):
        task_id = ready[0]
        task = dag.task(task_id)
        assert task.status == "pending"
        for dependency in task.depends_on:
            assert dependency in done or dag.task(dependency).status == "skipped"
        done.add(task_id)
        ran.append(task_id)
    return ran


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(data=templates(), variables=hires)
def test_every_compiled_graph_is_acyclic_and_skipped_tasks_never_block(
    data: dict[str, Any], variables: dict[str, object]
) -> None:
    template = Template.model_validate(data)
    edges = {task.id: task.depends_on for task in template.tasks}
    ids = set(edges)
    unknown = any(d not in ids for deps in edges.values() for d in deps)
    if unknown or _has_cycle(edges):
        with pytest.raises(CompileError) as caught:
            compile_dag(template, variables)
        assert caught.value.reason is (CompileReason.UNKNOWN_DEPENDENCY if unknown else CompileReason.CYCLE)
        event("refused: unknown dependency" if unknown else "refused: cycle")
        return

    dag = compile_dag(template, variables)  # every acyclic graph with valid variables compiles

    compiled = {task.id: task.depends_on for task in dag.tasks}
    assert compiled == edges
    assert not _has_cycle(compiled)

    position = {task_id: index for index, task_id in enumerate(dag.order)}
    assert len(dag.order) == len(ids) == len(position)
    for task_id, deps in compiled.items():
        assert all(position[d] < position[task_id] for d in deps)

    pending = {task.id for task in dag.tasks if task.status == "pending"}
    skipped = {task.id for task in dag.tasks if task.status == "skipped"}
    assert pending | skipped == ids
    for task in dag.tasks:
        assert (task.skip_reason is not None) == (task.id in skipped)

    for task_id in pending:
        if any(d in skipped for d in compiled[task_id]):
            event("compiled: a pending task depends on a skipped task")
        if compiled[task_id] and all(d in skipped for d in compiled[task_id]):
            event("compiled: every dependency of a pending task is skipped")
            assert task_id in dag.ready(done=set())

    ran = _run_to_completion(dag)
    assert set(ran) == pending
    assert len(ran) == len(pending)


@settings(max_examples=100, deadline=None)
@given(data=templates(), variables=hires)
def test_compiling_any_template_twice_gives_the_same_hash(data: dict[str, Any], variables: dict[str, object]) -> None:
    template = Template.model_validate(data)
    try:
        first = compile_dag(template, variables)
    except CompileError:
        return
    assert compile_dag(Template.model_validate(data), dict(variables)).hash == first.hash
