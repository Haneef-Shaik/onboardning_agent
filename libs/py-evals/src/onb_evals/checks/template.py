"""Contract and template-graph checks (PRD §8.2 "Contract" and "Outcome" on generated templates)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from onb_evals.checks import params as p
from onb_evals.checks.base import CheckResult, failed, passed, register
from onb_evals.context import CheckContext
from onb_evals.paths import MISSING, resolve
from onb_evals.template_graph import (
    ancestors,
    apply_diff,
    find_cycle,
    placeholders,
    referenced_names,
    task_action,
    tasks_of,
)

Task = Mapping[str, Any]


def _template(context: CheckContext) -> Any:
    return resolve(context.output, "template")


def _actions(context: CheckContext, tasks: list[Task]) -> list[str]:
    return [task_action(t, context.facts.approval_actions) for t in tasks]


def _change_request(context: CheckContext) -> Mapping[str, Any] | None:
    found = resolve(context.output, "change_request")
    return found if isinstance(found, Mapping) else None


def _diff(context: CheckContext) -> Mapping[str, Any]:
    request = _change_request(context)
    diff = request.get("diff") if request else None
    return diff if isinstance(diff, Mapping) else {}


@register("schema_valid")
async def schema_valid(params: p.SchemaValid, context: CheckContext) -> CheckResult:
    path, model = context.contracts[params.target]
    value = resolve(context.output, path)
    if value is MISSING:
        return failed(f"{path} is missing")
    try:
        model.model_validate(value)
    except ValidationError as error:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()[:5])
        return failed(f"{path} does not match the {params.target} contract: {problems}")
    return passed()


def _dag_problems(context: CheckContext, template: Mapping[str, Any]) -> list[str]:
    tasks = tasks_of(template)
    if not tasks:
        return ["the template has no tasks"]
    ids = [t.get("id") for t in tasks]
    problems = [f"duplicate or invalid task id {i!r}" for i in ids if not isinstance(i, str) or ids.count(i) > 1]
    known = {i for i in ids if isinstance(i, str)}
    declared = {v.get("name") for v in template.get("variables", []) if isinstance(v, Mapping)}
    for task in tasks:
        problems.extend(_task_problems(context, task, known, declared))
    cycle = find_cycle(tasks)
    if cycle:
        problems.append(f"dependency cycle {' -> '.join(cycle)}")
    return problems


def _task_problems(context: CheckContext, task: Task, known: set[Any], declared: set[Any]) -> list[str]:
    tid, action, agent = task.get("id"), task.get("action"), task.get("agent")
    problems = [f"{tid}: depends on unknown task {d!r}" for d in task.get("depends_on", []) if d not in known]
    registered_agent = context.facts.action_agents.get(str(action))
    if registered_agent is None:
        problems.append(f"{tid}: action {action!r} is not in the registry")
    elif agent != registered_agent:
        problems.append(f"{tid}: action {action!r} belongs to {registered_agent}, not {agent!r}")
    allowed_tools = context.facts.agent_tools.get(str(agent), frozenset())
    problems.extend(
        f"{tid}: tool {tool!r} is not allowed for {agent}"
        for tool in task.get("tools") or []
        if tool not in allowed_tools
    )
    used = set(placeholders(task.get("inputs", {})))
    if isinstance(task.get("when"), str):
        used |= referenced_names(task["when"])[0]
    approver = task.get("approver")
    if isinstance(approver, Mapping) and approver.get("rule") == "variable":
        used.add(str(approver.get("ref")))
    problems.extend(f"{tid}: variable {name!r} is not declared" for name in sorted(used - declared))
    return problems


@register("dag_valid", presence_path="template")
async def dag_valid(params: p.DagValid, context: CheckContext) -> CheckResult:
    template = _template(context)
    if not isinstance(template, Mapping):
        return failed("no template object in the output")
    problems = _dag_problems(context, template)
    return failed("; ".join(problems[:6])) if problems else passed()


@register("task_coverage", presence_path="template")
async def task_coverage(params: p.TaskCoverage, context: CheckContext) -> CheckResult:
    present = set(_actions(context, tasks_of(_template(context))))
    expected = set(params.expected_actions)
    recall = len(expected & present) / len(expected)
    if recall >= params.min_recall:
        return passed(f"recall {recall:.2f}")
    return failed(f"recall {recall:.2f} < {params.min_recall}; missing {sorted(expected - present)}")


@register("forbidden_actions", presence_path="template")
async def forbidden_actions(params: p.ForbiddenActions, context: CheckContext) -> CheckResult:
    found = set(_actions(context, tasks_of(_template(context)))) & set(params.actions)
    return failed(f"forbidden actions present: {sorted(found)}") if found else passed()


def _graph_for(params: p.Dependency, context: CheckContext) -> list[Task] | str:
    if params.on == "output":
        return tasks_of(_template(context))
    request = _change_request(context)
    if request is None:
        return "no change_request in the output"
    version = request.get("base_version")
    if not isinstance(version, int) or isinstance(version, bool):
        return "change_request.base_version is not an integer"
    key = (context.case.actor.org_id, str(request.get("template_key")), version)
    base = context.facts.templates.get(key)
    if base is None:
        return f"no base template {key[1]} v{version} in the fixture"
    return tasks_of(apply_diff(base, _diff(context)))


@register("dependency")
async def dependency(params: p.Dependency, context: CheckContext) -> CheckResult:
    graph = _graph_for(params, context)
    if isinstance(graph, str):
        return failed(graph)
    approvals = context.facts.approval_actions
    targets = [t for t in graph if task_action(t, approvals) == params.to_action]
    sources = {str(t.get("id")) for t in graph if task_action(t, approvals) == params.from_action}
    if not targets or not sources:
        return failed(f"no task for {params.to_action if not targets else params.from_action}")
    unlinked = [str(t.get("id")) for t in targets if not ancestors(graph, str(t.get("id"))) & sources]
    if unlinked:
        return failed(f"{unlinked} do not depend on a {params.from_action} task")
    return passed()


@register("task_condition", presence_path="template")
async def task_condition(params: p.TaskCondition, context: CheckContext) -> CheckResult:
    tasks = [t for t in tasks_of(_template(context)) if t.get("action") == params.action]
    if not tasks:
        return failed(f"no {params.action} task")
    for task in tasks:
        when = task.get("when")
        if not isinstance(when, str):
            return failed(f"{task.get('id')} has no when condition")
        identifiers, literals = referenced_names(when)
        missing = [token for token in params.must_reference if token not in identifiers | literals]
        if missing:
            return failed(f"{task.get('id')} condition does not reference {missing}")
    return passed()


@register("variable_declared", presence_path="template")
async def variable_declared(params: p.VariableDeclared, context: CheckContext) -> CheckResult:
    template = _template(context)
    variables = template.get("variables", []) if isinstance(template, Mapping) else []
    names = {v.get("name") for v in variables if isinstance(v, Mapping)}
    return passed() if params.name in names else failed(f"variable {params.name!r} is not declared")


@register("pii_access_forbidden")
async def pii_access_forbidden(params: p.PiiAccessForbidden, context: CheckContext) -> CheckResult:
    """Every task the output proposes: the template's, and a change request's added or updated tasks."""
    diff = _diff(context)
    proposed = [
        *tasks_of(_template(context)),
        *tasks_of({"tasks": diff.get("add_tasks", [])}),
        *tasks_of({"tasks": diff.get("update_tasks", [])}),
    ]
    hits: list[str] = []
    for task in proposed:
        access = task.get("pii_access")
        fields = access.get("fields", []) if isinstance(access, Mapping) else []
        hits.extend(f"{task.get('id')}:{f}" for f in fields if f in params.fields)
    return failed(f"tasks declare forbidden pii_access {hits}") if hits else passed()


@register("diff_adds_action", presence_path="change_request")
async def diff_adds_action(params: p.DiffAddsAction, context: CheckContext) -> CheckResult:
    added = _actions(context, tasks_of({"tasks": _diff(context).get("add_tasks", [])}))
    return passed() if params.action in added else failed(f"the diff adds no {params.action} task")


@register("diff_removes_none", presence_path="change_request")
async def diff_removes_none(params: p.DiffRemovesNone, context: CheckContext) -> CheckResult:
    if _change_request(context) is None:
        return failed("no change_request in the output")
    removed = _diff(context).get("remove_tasks") or []
    return failed(f"the diff removes {removed}") if removed else passed()
