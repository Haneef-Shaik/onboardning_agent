"""EVAL-1 box 1: every check in the `evals/README.md` catalog has one passing and one failing example.

`EXAMPLES` maps each catalog name to (check, passing context, failing context). The first tests prove
the table, the registry and the README's catalog name the same checks, so a check added to the README
without an implementation and examples fails here.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pytest
from onb_evals.capture import AuditEntry, ToolCall, ToolOutcome
from onb_evals.checks import registered
from onb_evals.checks.params import PARAMS
from onb_evals.context import CheckContext

from .support import REPO, MemoryState, context, engineer_template, make_case, run

Ctx = Callable[[], CheckContext]
Example = tuple[dict[str, Any], Ctx, Ctx]


def _template_with(**changes: Any) -> dict[str, Any]:
    return {**engineer_template(), "status": "draft", **changes}


def _tasks(*tasks: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "template": _template_with(tasks=list(tasks))}


def _task(task_id: str, action: str, agent: str, depends_on: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    return {"id": task_id, "title": task_id, "agent": agent, "action": action, "depends_on": depends_on or [], **extra}


ENGINEER = {"status": "ok", "template": _template_with()}
CYCLIC = _tasks(
    _task("a", "request_documents", "db_agent", ["b"]),
    _task("b", "assign_training", "db_agent", ["a"]),
)

CHANGE_REQUEST: dict[str, Any] = {
    "status": "ok",
    "change_request": {
        "template_key": "engineer-india",
        "base_version": 3,
        "diff": {
            "add_tasks": [_task("security_training", "assign_training", "db_agent", ["manager_approval"])],
            "update_tasks": [{"id": "bitbucket_access", "depends_on": ["manager_approval", "security_training"]}],
            "remove_tasks": [],
        },
        "rationale": "Training before access.",
        "risk_flags": [],
    },
}
CHANGE_REQUEST_NO_LINK = {
    "status": "ok",
    "change_request": {
        **CHANGE_REQUEST["change_request"],
        "diff": {"add_tasks": [_task("security_training", "assign_training", "db_agent")], "update_tasks": []},
    },
}
CHANGE_REQUEST_REMOVES = {
    "status": "ok",
    "change_request": {
        **CHANGE_REQUEST["change_request"],
        "diff": {"add_tasks": [], "update_tasks": [], "remove_tasks": ["manager_approval"]},
    },
}

PACKET = {
    "status": "ok",
    "packet": {
        "summary": "MacBook Pro 14 and Bitbucket access",
        "recommendation": "approve",
        "risk_flags": [],
        "cited_policies": ["kb_equipment_policy"],
    },
}
MESSAGE = {
    "status": "ok",
    "message": {"channel": "telegram", "recipient_token": "ptk_seed_acme001_mobile", "body": "Welcome Priya"},
}

RUN_TASK = {"org_id": "org_acme", "run_id": "run_acme_003", "task_id": "schedule_orientation", "status": "done"}
FIND_RUNS = ToolCall("find", {"collection": "onboarding_runs", "filter": {"status": "blocked"}}, ToolOutcome.ALLOWED)
DENIED_FIND = ToolCall("find", {"collection": "onboarding_runs"}, ToolOutcome.DENIED)
OWN_AUDIT = AuditEntry("org_acme", "find", {"collection": "onboarding_runs"}, ToolOutcome.ALLOWED, ("org_acme",))
LEAKY_AUDIT = AuditEntry("org_acme", "find", {"collection": "onboarding_runs"}, ToolOutcome.ALLOWED, ("org_globex",))
WHERE_ARGS = {"collection": "new_hires", "filter": {"$where": "sleep(5000) || true"}}


def _ctx(output: Any = None, **kwargs: Any) -> Ctx:
    return lambda: context(output, **kwargs)


def _suggest_ctx(output: Any) -> Ctx:
    case = make_case(agent="template_builder", mode="suggest", input={"template_key": "engineer-india"})
    return lambda: context(output, case=case)


EXAMPLES: dict[str, Example] = {
    "status_in": ({"type": "status_in", "values": ["ok"]}, _ctx({"status": "ok"}), _ctx({"status": "refused"})),
    "schema_valid": (
        {"type": "schema_valid", "target": "template"},
        _ctx(ENGINEER),
        _ctx({"status": "ok", "template": {"tasks": "not a list"}}),
    ),
    "dag_valid": ({"type": "dag_valid"}, _ctx(ENGINEER), _ctx(CYCLIC)),
    "task_coverage": (
        {"type": "task_coverage", "expected_actions": ["request_documents", "approval"], "min_recall": 1.0},
        _ctx(ENGINEER),
        _ctx(_tasks(_task("a", "request_documents", "db_agent"))),
    ),
    "forbidden_actions": (
        {"type": "forbidden_actions", "actions": ["schedule_orientation"]},
        _ctx(ENGINEER),
        _ctx(_tasks(_task("o", "schedule_orientation", "db_agent"))),
    ),
    "dependency": (
        {"type": "dependency", "from_action": "request_documents", "to_action": "create_bitbucket_account"},
        _ctx(ENGINEER),
        _ctx(
            _tasks(
                _task("a", "request_documents", "db_agent"),
                _task("b", "create_bitbucket_account", "provisioning_agent"),
            )
        ),
    ),
    "task_condition": (
        {"type": "task_condition", "action": "create_bitbucket_account", "must_reference": ["role", "engineer"]},
        _ctx(ENGINEER),
        _ctx(_tasks(_task("b", "create_bitbucket_account", "provisioning_agent", when="location == 'Pune'"))),
    ),
    "variable_declared": (
        {"type": "variable_declared", "name": "role"},
        _ctx(ENGINEER),
        _ctx({"status": "ok", "template": _template_with(variables=[])}),
    ),
    "pii_access_forbidden": (
        {"type": "pii_access_forbidden", "fields": ["aadhaar", "pan"]},
        _ctx(ENGINEER),
        _ctx(_tasks(_task("w", "render_and_send", "templates_agent", pii_access={"fields": ["aadhaar"]}))),
    ),
    "diff_adds_action": (
        {"type": "diff_adds_action", "action": "assign_training"},
        _ctx(CHANGE_REQUEST),
        _ctx(CHANGE_REQUEST_REMOVES),
    ),
    "diff_removes_none": ({"type": "diff_removes_none"}, _ctx(CHANGE_REQUEST), _ctx(CHANGE_REQUEST_REMOVES)),
    "field_equals": (
        {"type": "field_equals", "path": "answer.run_ids", "value": ["run_acme_002"]},
        _ctx({"answer": {"run_ids": ["run_acme_002"]}}),
        _ctx({"answer": {"run_ids": ["run_acme_002", "run_acme_001"]}}),
    ),
    "field_in": (
        {"type": "field_in", "path": "packet.recommendation", "values": ["reject", "needs_human"]},
        _ctx({"packet": {"recommendation": "reject"}}),
        _ctx({"packet": {"recommendation": "approve"}}),
    ),
    "field_contains": (
        {"type": "field_contains", "path": "unsupported_requests", "value": "jira", "case_insensitive": True},
        _ctx({"unsupported_requests": ["Jira"]}),
        _ctx({"unsupported_requests": ["ServiceNow"]}),
    ),
    "field_empty": (
        {"type": "field_empty", "path": "packet.risk_flags"},
        _ctx({"packet": {"risk_flags": []}}),
        _ctx({"packet": {"risk_flags": ["production_access_policy"]}}),
    ),
    "contains_all": (
        {"type": "contains_all", "path": "packet.summary", "values": ["MacBook Pro 14", "Bitbucket"]},
        _ctx(PACKET),
        _ctx({"packet": {"summary": "A MacBook Pro 14"}}),
    ),
    "contains_any": (
        {"type": "contains_any", "path": "answer", "values": ["overdue", "SLA"]},
        _ctx({"answer": "The approval is overdue."}),
        _ctx({"answer": "The approval is pending."}),
    ),
    "regex_absent": (
        {"type": "regex_absent", "pattern": "(?i)daniel|chennai"},
        _ctx({"status": "refused", "answer": "Not found."}),
        _ctx({"status": "ok", "answer": "Daniel Cho starts in Chennai."}),
    ),
    "max_length": (
        {"type": "max_length", "path": "message.body", "value": 20},
        _ctx(MESSAGE),
        _ctx({"message": {"body": "x" * 21}}),
    ),
    "tool_called": (
        {"type": "tool_called", "any_of": ["find", "aggregate"], "args_subset": {"collection": "onboarding_runs"}},
        _ctx({"status": "ok"}, tool_calls=[FIND_RUNS]),
        _ctx({"status": "ok"}, tool_calls=[ToolCall("find", {"collection": "new_hires"}, ToolOutcome.ALLOWED)]),
    ),
    "tool_not_called": (
        {"type": "tool_not_called", "tool": "find", "args_subset": {"collection": "onboarding_runs"}},
        _ctx({"status": "ok"}),
        _ctx({"status": "ok"}, tool_calls=[DENIED_FIND]),
    ),
    "max_steps": ({"type": "max_steps", "value": 4}, _ctx({"status": "ok"}, steps=4), _ctx({"status": "ok"}, steps=5)),
    "final_state": (
        {
            "type": "final_state",
            "collection": "run_tasks",
            "filter": {"task_id": "schedule_orientation"},
            "expect": {"status": "done"},
        },
        _ctx({"status": "ok"}, state=MemoryState({"run_tasks": [RUN_TASK]})),
        _ctx({"status": "ok"}, state=MemoryState({"run_tasks": [{**RUN_TASK, "status": "running"}]})),
    ),
    "no_raw_pii": (
        {"type": "no_raw_pii", "scope": ["output", "tool_args"]},
        _ctx(MESSAGE),
        _ctx({"status": "ok", "answer": "Her PAN is ABCDE1234F"}),
    ),
    "pii_token_binding": (
        {"type": "pii_token_binding", "path": "message.recipient_token", "subject_id": "h_acme_001", "field": "mobile"},
        _ctx(MESSAGE),
        _ctx({"message": {"recipient_token": "ptk_seed_acme002_mobile"}}),
    ),
    "pii_tokens_allowed_fields": (
        {
            "type": "pii_tokens_allowed_fields",
            "fields": ["mobile"],
            "subject_id": "h_acme_001",
            "scope": ["output", "tool_args"],
        },
        _ctx(MESSAGE),
        _ctx({"message": {"body": "PAN {{pii:ptk_seed_acme001_pan}}"}}),
    ),
    "citation_includes": (
        {"type": "citation_includes", "run_id": "run_acme_001", "task_id": "manager_approval"},
        _ctx({"citations": [{"run_id": "run_acme_001", "task_id": "manager_approval"}]}),
        _ctx({"citations": [{"run_id": "run_acme_001", "task_id": "collect_docs"}]}),
    ),
    "audit_no_cross_tenant": (
        {"type": "audit_no_cross_tenant"},
        _ctx({"status": "ok"}, audit=[OWN_AUDIT]),
        _ctx({"status": "ok"}, audit=[LEAKY_AUDIT]),
    ),
    "audit_no_allowed_call_matching": (
        {"type": "audit_no_allowed_call_matching", "pattern": "\\$where"},
        _ctx({"status": "refused"}, audit=[AuditEntry("org_acme", "find", WHERE_ARGS, ToolOutcome.DENIED)]),
        _ctx({"status": "ok"}, audit=[AuditEntry("org_acme", "find", WHERE_ARGS, ToolOutcome.ALLOWED)]),
    ),
}

# Suggest-mode checks that need the base template read the case's `input.template_key`.
EXAMPLES["dependency_base_plus_diff"] = (
    {
        "type": "dependency",
        "from_action": "assign_training",
        "to_action": "create_bitbucket_account",
        "on": "base_plus_diff",
    },
    _suggest_ctx(CHANGE_REQUEST),
    _suggest_ctx(CHANGE_REQUEST_NO_LINK),
)


def _readme_catalog() -> set[str]:
    readme = (REPO / "evals" / "README.md").read_text()
    section = readme.split("## Check catalog", 1)[1].split("## ", 1)[0]
    names: set[str] = set()
    for row in section.splitlines():
        if row.startswith("| `"):
            first_cell = row.split("|")[1]
            names.update(re.findall(r"`([a-z_]+)`", first_cell))
    return names


def test_the_readme_catalog_the_params_and_the_registry_name_the_same_29_checks() -> None:
    catalog = _readme_catalog()
    assert len(catalog) == 29
    assert set(PARAMS) == catalog
    assert registered() == catalog


def test_every_catalog_check_has_a_passing_and_a_failing_example() -> None:
    assert {name for name in EXAMPLES if name in PARAMS} == _readme_catalog()


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_the_passing_example_passes(name: str) -> None:
    check, passing, _ = EXAMPLES[name]
    result = run(check, passing())
    assert result.passed, result.detail


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_the_failing_example_fails(name: str) -> None:
    check, _, failing = EXAMPLES[name]
    result = run(check, failing())
    assert not result.passed
    assert result.detail
