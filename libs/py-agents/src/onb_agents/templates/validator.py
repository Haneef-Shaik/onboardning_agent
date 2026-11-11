"""FR-T3 validation on save: a template becomes a safe task graph or a list of named problems.

    validator = TemplateValidator.from_config(Path("config"))       # or .from_registry(...)
    report = validator.validate(draft)                               # never raises for a bad draft
    report.ok, report.issues, report.template, report.order

The rules (PRD §6.1 FR-T3, §5.2, §7.10), each reported as a `Rule`:
- `schema`: the shape in `onb_contracts.templates` (types, identifiers, unique ids, SLA grammar).
- `cycle` / `unknown_dependency`: the `depends_on` graph is acyclic and names only its own tasks.
- `unknown_agent`: every task's agent is in the registry and active.
- `tool_not_allowed`: the tool catalog gives the task's `action` to that agent (approval actions on
  approval tasks only), every tool the task lists is in the agent's allowlist (`render`,
  `notify_send` or `notify.notify_send`), and a task with `pii_access` belongs to an agent that can
  call the sink's server.
- `undeclared_variable`: `when`, an approver `variable`, `sla.due` and `{{ … }}` placeholders in
  `inputs` read declared variables only.
- `bad_condition` / `bad_approver` / `bad_sla`: a `when` parses (no calls, attributes or imports) and
  compares each variable with values, or variables, of its type (enum values from its list); a variable approver is
  a `user_ref`; an approver sits on an approval or `requires_approval` task; `sla.due` reads a date.
- `approver_missing`: every approval task names an approver rule.
- `sensitive_input`: no sensitive field reference or raw value in LLM-visible inputs, and no raw
  value, vault token or vault name in any string of the template (see `sensitive.py`); no variable
  is named after a sensitive field, whole or in part (`personal_email`).
- `pii_access_forbidden`: each `pii_access` field is allowed at its sink by the field → sink policy;
  Aadhaar, PAN and bank fields never are.

Messages name rules, tasks, fields and positions, never submitted values.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from onb_contracts.templates import Template, TemplateTask, Variable
from onb_crypto import NEVER_SINKABLE, SinkPolicy, VaultField, load_sink_policy
from onb_guardrails.pii import redact
from pydantic import ValidationError

from onb_agents.registry import AgentManifest, RegisteredAgent, ToolCatalog, load_catalog, load_manifests

from . import graph
from .expr import ExpressionError, WhenExpression, parse_when
from .sensitive import input_findings, names_a_sensitive_field, placeholders, raw_findings, reference_findings

__all__ = ["Issue", "Rule", "TemplateInvalidError", "TemplateReport", "TemplateValidator"]

_FIELDS_BY_NAME = {member.value: member for member in VaultField}
_MAX_LOC_PARTS = 6
# Variable types that hold the same kind of value, so two variables of them may be compared.
_KIND = {"string": "text", "enum": "text", "date": "text", "user_ref": "text", "number": "number", "boolean": "bool"}
# Which literal kinds a variable of each type may be compared with (`null` always may).
_COMPARABLE: Mapping[str, tuple[type, ...]] = {
    "string": (str,),
    "enum": (str,),
    "date": (str,),
    "user_ref": (str,),
    "number": (int,),
    "boolean": (bool,),
}


class Rule(StrEnum):
    SCHEMA = "schema"
    CYCLE = "cycle"
    UNKNOWN_DEPENDENCY = "unknown_dependency"
    UNKNOWN_AGENT = "unknown_agent"
    TOOL_NOT_ALLOWED = "tool_not_allowed"
    UNDECLARED_VARIABLE = "undeclared_variable"
    BAD_CONDITION = "bad_condition"
    APPROVER_MISSING = "approver_missing"
    BAD_APPROVER = "bad_approver"
    BAD_SLA = "bad_sla"
    SENSITIVE_INPUT = "sensitive_input"
    PII_ACCESS_FORBIDDEN = "pii_access_forbidden"


@dataclass(frozen=True, slots=True)
class Issue:
    rule: Rule
    task_id: str | None
    message: str

    def document(self) -> dict[str, Any]:
        return {"rule": self.rule.value, "task_id": self.task_id, "message": self.message}


@dataclass(frozen=True, slots=True)
class TemplateReport:
    """The outcome of one validation. `template` is set when the shape parsed; `order` when it is
    acyclic; `ok` only when there is no issue at all."""

    template: Template | None
    issues: tuple[Issue, ...]
    order: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return self.template is not None and not self.issues

    @property
    def rules(self) -> frozenset[Rule]:
        return frozenset(issue.rule for issue in self.issues)


class TemplateInvalidError(ValueError):
    def __init__(self, issues: tuple[Issue, ...]) -> None:
        super().__init__("; ".join(f"{i.rule.value}: {i.message}" for i in issues) or "the template is not valid")
        self.issues = issues


class TemplateValidator:
    """Checks templates against the active agents, the tool catalog and the field → sink policy."""

    def __init__(self, agents: Mapping[str, AgentManifest], catalog: ToolCatalog, sink_policy: SinkPolicy) -> None:
        self._agents = dict(agents)
        self._catalog = {entry.action: entry for entry in catalog.actions}
        self._policy = sink_policy

    @classmethod
    def from_registry(
        cls, agents: Iterable[RegisteredAgent], catalog: ToolCatalog, sink_policy: SinkPolicy
    ) -> TemplateValidator:
        """From the stored registry: a retired agent is not in it."""
        return cls({a.name: a.manifest for a in agents if a.active}, catalog, sink_policy)

    @classmethod
    def from_config(cls, config_dir: Path) -> TemplateValidator:
        """From `config/agents/*.yaml`, `config/tool_catalog.yaml` and `config/pii_sink_policy.yaml`."""
        manifests = load_manifests(config_dir / "agents")
        catalog = load_catalog(config_dir / "tool_catalog.yaml", manifests)
        policy = load_sink_policy(config_dir / "pii_sink_policy.yaml")
        return cls({m.name: m for m in manifests}, catalog, policy)

    def validate(self, data: object) -> TemplateReport:
        if isinstance(data, Template):
            template = data
        else:
            try:
                template = Template.model_validate(data)
            except ValidationError as error:
                return TemplateReport(None, tuple(_schema_issues(error)))
        issues = [*self._template_issues(template)]
        for task in template.tasks:
            issues.extend(_TaskCheck(self, template, task).issues())
        edges = {task.id: task.depends_on for task in template.tasks}
        for loop in graph.cycles(edges):
            issues.append(Issue(Rule.CYCLE, loop[0], redact("tasks form a cycle: " + " -> ".join(loop))))
        return TemplateReport(template, tuple(issues), graph.topological_order(edges))

    def require_valid(self, data: object) -> Template:
        report = self.validate(data)
        if not report.ok or report.template is None:
            raise TemplateInvalidError(report.issues)
        return report.template

    def _template_issues(self, template: Template) -> Iterator[Issue]:
        for where, text in _strings(template.model_dump(mode="json", exclude={"tasks"}), ""):
            for finding in raw_findings(text) + (reference_findings(text) if where in {"key", "name"} else []):
                yield Issue(Rule.SENSITIVE_INPUT, None, f"template {where} holds {finding}")
        for index, variable in enumerate(template.variables):
            if names_a_sensitive_field(variable.name):
                yield Issue(Rule.SENSITIVE_INPUT, None, f"variables[{index}] is named after a sensitive field")


def _schema_issues(error: ValidationError) -> Iterator[Issue]:
    for item in error.errors(include_url=False, include_input=False, include_context=False):
        loc = [str(part) for part in item["loc"]]
        if "inputs" in loc:  # input keys are submitted text: stop at `inputs`
            loc = loc[: loc.index("inputs") + 1] + (["…"] if loc[-1] != "inputs" else [])
        where = ".".join(loc[:_MAX_LOC_PARTS]) + (".…" if len(loc) > _MAX_LOC_PARTS else "") if loc else "template"
        yield Issue(Rule.SCHEMA, None, redact(f"{where}: {item['msg']}"))


class _TaskCheck:
    """The FR-T3 checks for one task, in a fixed order."""

    def __init__(self, validator: TemplateValidator, template: Template, task: TemplateTask) -> None:
        self._validator = validator
        self._template = template
        self._task = task
        self._found: list[Issue] = []

    def issues(self) -> list[Issue]:
        task = self._task
        manifest = self._validator._agents.get(task.agent)
        if manifest is None:
            self._add(Rule.UNKNOWN_AGENT, f"agent {task.agent} is not an active agent in the registry")
        else:
            self._check_action(manifest)
            self._check_tools(manifest)
        self._check_dependencies()
        self._check_when()
        self._check_approval()
        self._check_sla()
        self._check_sensitive()
        self._check_pii_access(manifest)
        return self._found

    def _add(self, rule: Rule, message: str) -> None:
        self._found.append(Issue(rule, self._task.id, f"task {self._task.id}: {redact(message)}"))

    def _variable(self, name: str) -> Variable | None:
        return self._template.variable(name)

    def _check_action(self, manifest: AgentManifest) -> None:
        task = self._task
        entry = self._validator._catalog.get(task.action)
        if entry is None or entry.agent != manifest.name:
            self._add(Rule.TOOL_NOT_ALLOWED, f"the tool catalog does not give action {task.action} to {task.agent}")
        elif (entry.task_type == "approval") != (task.type == "approval"):
            self._add(Rule.TOOL_NOT_ALLOWED, f"action {task.action} runs only as an approval task, and vice versa")

    def _check_tools(self, manifest: AgentManifest) -> None:
        for tool in self._task.tools:
            if not _granted(manifest, tool):
                self._add(Rule.TOOL_NOT_ALLOWED, f"tool {tool} is not in {manifest.name}'s allowlist")

    def _check_dependencies(self) -> None:
        for dependency in self._task.depends_on:
            if self._template.task(dependency) is None:
                self._add(Rule.UNKNOWN_DEPENDENCY, f"depends on {dependency}, which is not a task of this template")

    def _check_when(self) -> None:
        if self._task.when is None:
            return
        try:
            condition = parse_when(self._task.when)
        except ExpressionError as error:
            self._add(Rule.BAD_CONDITION, f"when is not a safe condition ({error})")
            return
        self._check_condition_types(condition)

    def _check_condition_types(self, condition: WhenExpression) -> None:
        for name in sorted(condition.names):
            if self._variable(name) is None:
                self._add(Rule.UNDECLARED_VARIABLE, f"when reads {name}, which is not a declared variable")
        for left, right in condition.name_pairs():
            first, second = self._variable(left), self._variable(right)
            if first is not None and second is not None and _KIND[first.type] != _KIND[second.type]:
                self._add(Rule.BAD_CONDITION, f"when compares {first.type} {left} with {second.type} {right}")
        for name, literal in condition.comparisons():
            variable = self._variable(name)
            if variable is None or literal is None:
                continue
            kinds = _COMPARABLE[variable.type]
            if (isinstance(literal, bool) and bool not in kinds) or not isinstance(literal, kinds):
                self._add(Rule.BAD_CONDITION, f"when compares {variable.type} variable {name} with another type")
            elif variable.values is not None and literal not in variable.values:
                self._add(Rule.BAD_CONDITION, f"when compares enum {name} with a value outside its values")

    def _check_approval(self) -> None:
        task = self._task
        if task.type == "approval" and task.approver is None:
            self._add(Rule.APPROVER_MISSING, "an approval task needs an approver rule")
        if task.approver is None:
            return
        if task.type != "approval" and not task.requires_approval:
            self._add(Rule.BAD_APPROVER, "an approver belongs on an approval or requires_approval task")
        if task.approver.rule == "variable":
            variable = self._variable(task.approver.ref)
            if variable is None:
                self._add(Rule.UNDECLARED_VARIABLE, f"approver reads {task.approver.ref}, not a declared variable")
            elif variable.type != "user_ref":
                self._add(Rule.BAD_APPROVER, f"approver variable {variable.name} must be a user_ref")

    def _check_sla(self) -> None:
        if self._task.sla is None:
            return
        name, _, _ = self._task.sla.parts()
        variable = self._variable(name)
        if variable is None:
            self._add(Rule.UNDECLARED_VARIABLE, f"sla.due reads {name}, which is not a declared variable")
        elif variable.type != "date":
            self._add(Rule.BAD_SLA, f"sla.due reads {name}, which is not a date variable")

    def _check_sensitive(self) -> None:
        task = self._task
        for where, text in _strings(task.model_dump(mode="json", exclude={"inputs"}), ""):
            for finding in raw_findings(text) + (reference_findings(text) if where == "title" else []):
                self._add(Rule.SENSITIVE_INPUT, f"{where} holds {finding}")
        for where, finding in input_findings(task.inputs):
            self._add(Rule.SENSITIVE_INPUT, f"{where} holds {finding}")
        self._check_input_variables(task.inputs)

    def _check_input_variables(self, value: object) -> None:
        if isinstance(value, Mapping):
            for item in value.values():
                self._check_input_variables(item)
        elif isinstance(value, list):
            for item in value:
                self._check_input_variables(item)
        elif isinstance(value, str):
            for segments in placeholders(value):
                head = segments[0] if segments else ""
                if any(names_a_sensitive_field(s) for s in segments):
                    continue  # reported as sensitive_input
                if self._variable(head) is None:
                    self._add(Rule.UNDECLARED_VARIABLE, "an inputs placeholder reads an undeclared variable")

    def _check_pii_access(self, manifest: AgentManifest | None) -> None:
        access = self._task.pii_access
        if access is None:
            return
        policy = self._validator._policy
        for name in access.fields:
            vault_field = _FIELDS_BY_NAME.get(name)
            if vault_field is None:
                self._add(Rule.PII_ACCESS_FORBIDDEN, f"pii_access names {name}, which is not a vault field")
            elif vault_field in NEVER_SINKABLE:
                self._add(Rule.PII_ACCESS_FORBIDDEN, f"pii_access names {name}: no agent sink may ever resolve it")
            elif not policy.allows(vault_field, access.sink):
                self._add(Rule.PII_ACCESS_FORBIDDEN, f"the sink policy does not allow {name} at {access.sink}")
        server = access.sink.split(".", 1)[0]
        if manifest is not None and server not in manifest.mcp:
            self._add(Rule.TOOL_NOT_ALLOWED, f"{manifest.name} cannot call the {server} server of sink {access.sink}")


def _strings(value: object, where: str) -> Iterator[tuple[str, str]]:
    """Every string in a dumped task (title, when, approver.ref, tools...), with where it sits."""
    if isinstance(value, str):
        yield where, value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _strings(item, f"{where}.{key}" if where else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _strings(item, f"{where}[{index}]")


def _granted(manifest: AgentManifest, tool: str) -> bool:
    server, _, name = tool.rpartition(".")
    if server:
        grant = manifest.mcp.get(server)
        return grant is not None and name in grant.tools
    return tool in manifest.tools or any(tool in grant.tools for grant in manifest.mcp.values())
