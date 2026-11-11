"""Compile a published template version and a hire's variables into a pruned DAG (PRD §5.1, §7.1).

    dag = compile_dag(template, {"role": "sales", "start_date": "2026-12-01", ...})
    dag.task("bitbucket_access").status        # "skipped"; .skip_reason names the condition
    dag.task("collect_docs").due               # "2026-11-24" for `start_date - 7d`
    dag.ready(done={"collect_docs"})           # FR-R3: dependencies all done or skipped
    dag.hash                                   # "sha256:…", the same for the same compile

The compiler is pure: no I/O, no clock, no randomness, and it changes neither argument. It keeps
every task of the template, in template order, with its edges: a task whose `when` is false is
`skipped` with a reason rather than removed, and a skipped task never blocks a dependent. Which tasks
exist and how they depend on each other comes only from the template; nothing here (and no LLM) adds,
removes or reorders them.

Validation against the registry and the sink policy happens when a version is published (TPL-1,
TPL-2). The compiler still refuses what it cannot compile safely: an unpublished template, a
dependency on an unknown task, a cycle, a missing or mistyped variable, and a condition or SLA that
cannot be decided. It never treats an undecidable condition as false.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from onb_contracts.templates import Template, TemplateTask
from onb_guardrails.pii import redact
from pydantic import ValidationError

from onb_agents.templates import ExpressionError, graph, parse_when

from .conditions import check_condition
from .errors import CompileError, CompileReason
from .sla import resolve_due
from .variables import HireValue, hire_values

__all__ = ["COMPILER_VERSION", "CompiledDag", "CompiledTask", "TaskStatus", "compile_dag"]

TaskStatus = Literal["pending", "skipped"]
# Part of every hash: bump it when the compiled document changes shape, so old and new hashes differ.
COMPILER_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class CompiledTask:
    """One task of a compiled DAG: a copy of the template task, its initial status and due date."""

    task: TemplateTask
    status: TaskStatus
    skip_reason: str | None
    due: str | None

    @property
    def id(self) -> str:
        return self.task.id

    @property
    def depends_on(self) -> tuple[str, ...]:
        return self.task.depends_on

    def document(self) -> dict[str, Any]:
        return {
            **self.task.model_dump(mode="json"),
            "status": self.status,
            "skip_reason": self.skip_reason,
            "due": self.due,
        }


@dataclass(frozen=True, slots=True)
class CompiledDag:
    """A compiled DAG pinned to its template version. `order` is a deterministic topological order."""

    template_key: str
    template_version: int
    tasks: tuple[CompiledTask, ...]
    order: tuple[str, ...]
    hash: str

    def task(self, task_id: str) -> CompiledTask:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(task_id)

    def ready(self, done: Collection[str]) -> tuple[str, ...]:
        """Pending tasks not yet done whose every dependency is done or skipped (FR-R3), in template order."""
        unknown = set(done) - {task.id for task in self.tasks}
        if unknown:
            raise KeyError(sorted(unknown)[0])
        finished = set(done) | {task.id for task in self.tasks if task.status == "skipped"}
        return tuple(
            task.id
            for task in self.tasks
            if task.status == "pending" and task.id not in done and all(d in finished for d in task.depends_on)
        )

    def document(self, *, include_hash: bool = True) -> dict[str, Any]:
        document: dict[str, Any] = {
            "compiler": COMPILER_VERSION,
            "template_key": self.template_key,
            "template_version": self.template_version,
            "order": list(self.order),
            "tasks": [task.document() for task in self.tasks],
        }
        return {**document, "hash": self.hash} if include_hash else document

    @staticmethod
    def hash_of(document: Mapping[str, Any]) -> str:
        """SHA-256 of the canonical JSON (sorted keys, no spaces, ASCII, no NaN)."""
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        return "sha256:" + hashlib.sha256(canonical.encode("ascii")).hexdigest()


def compile_dag(template: Template, variables: Mapping[str, object]) -> CompiledDag:
    """Compile a published template version for one hire, or raise `CompileError` naming why not."""
    version = _published_version(template)
    values = hire_values(template, variables)
    order = _order(template)
    tasks = tuple(_compile_task(template, task, values) for task in template.tasks)
    draft = CompiledDag(template.key, version, tasks, order, hash="")
    try:
        digest = CompiledDag.hash_of(draft.document(include_hash=False))
    except ValueError:
        raise CompileError(CompileReason.BAD_INPUTS, "task inputs hold a number JSON cannot carry") from None
    return CompiledDag(template.key, version, tasks, order, digest)


def _published_version(template: object) -> int:
    if not isinstance(template, Template):
        raise CompileError(CompileReason.NOT_PUBLISHED, "only a validated, published template version compiles")
    if template.status != "published" or template.version is None:
        raise CompileError(CompileReason.NOT_PUBLISHED, f"template {template.key} is not a published version")
    try:  # a Template built with `model_construct` skipped every schema check
        Template.model_validate(template.model_dump())
    except ValidationError:
        raise CompileError(CompileReason.NOT_PUBLISHED, "the template does not pass its schema") from None
    return template.version


def _order(template: Template) -> tuple[str, ...]:
    edges = {task.id: task.depends_on for task in template.tasks}
    for task in template.tasks:
        for dependency in task.depends_on:
            if dependency not in edges:
                raise CompileError(CompileReason.UNKNOWN_DEPENDENCY, f"depends on unknown task {dependency}", task.id)
    order = graph.topological_order(edges)
    if not order:
        loop = graph.cycles(edges)[0]
        raise CompileError(CompileReason.CYCLE, "tasks form a cycle: " + " -> ".join(loop), loop[0])
    return order


def _compile_task(template: Template, task: TemplateTask, values: Mapping[str, HireValue]) -> CompiledTask:
    skip_reason = _skip_reason(template, task, values)
    due = resolve_due(task.sla, template, values, task.id) if task.sla is not None else None
    return CompiledTask(
        task=task.model_copy(deep=True),
        status="skipped" if skip_reason is not None else "pending",
        skip_reason=skip_reason,
        due=due,
    )


def _skip_reason(template: Template, task: TemplateTask, values: Mapping[str, HireValue]) -> str | None:
    """`None` when the task runs; the reason when its `when` is false. Undecidable fails the compile."""
    if task.when is None:
        return None
    try:
        condition = parse_when(task.when)
    except ExpressionError as error:
        raise CompileError(CompileReason.BAD_CONDITION, f"when is not a safe condition ({error})", task.id) from None
    for name in sorted(condition.names):
        if name not in values:
            raise CompileError(CompileReason.MISSING_VARIABLE, f"when reads {name}, which the hire lacks", task.id)
    check_condition(condition, template, values, task.id)
    try:
        holds = condition.evaluate(values)
    except ExpressionError as error:
        raise CompileError(CompileReason.BAD_CONDITION, f"when cannot be decided ({error})", task.id) from None
    if holds:
        return None
    return redact(f"when condition `{task.when}` is false for this hire")
