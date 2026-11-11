"""The template schema (PRD §6.1 template shape, FR-T1, FR-T3).

The schema checks shape only: field types, identifiers, unique ids and the SLA grammar. The FR-T3
rules that need the registry, the tool catalog or the sink policy live in `onb_agents.templates`.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from onb_contracts.templates import (
    MAX_INPUT_STRING,
    MAX_LIST,
    RESERVED_VARIABLE_NAMES,
    Sla,
    Template,
    TemplateTask,
    Variable,
)
from pydantic import ValidationError


def prd_template() -> dict[str, Any]:
    """The PRD §6.1 example, with `action: approval` on the approval task (the tool catalog's name)."""
    return {
        "key": "engineer-india",
        "version": 3,
        "variables": [
            {"name": "role", "type": "enum", "values": ["engineer", "designer", "sales"]},
            {"name": "location", "type": "string"},
            {"name": "start_date", "type": "date"},
            {"name": "manager_id", "type": "user_ref"},
        ],
        "tasks": [
            {
                "id": "collect_docs",
                "title": "Collect ID & bank documents",
                "agent": "db_agent",
                "action": "request_documents",
                "sla": {"due": "start_date - 7d"},
            },
            {
                "id": "manager_approval",
                "title": "Manager approves equipment & access",
                "type": "approval",
                "agent": "approvals_agent",
                "action": "approval",
                "approver": {"rule": "variable", "ref": "manager_id"},
                "depends_on": ["collect_docs"],
            },
            {
                "id": "bitbucket_access",
                "title": "Create Bitbucket account (mock)",
                "agent": "provisioning_agent",
                "action": "create_bitbucket_account",
                "when": "role == 'engineer'",
                "depends_on": ["manager_approval"],
                "requires_approval": True,
            },
            {
                "id": "welcome_message",
                "title": "Send welcome message",
                "agent": "templates_agent",
                "action": "render_and_send",
                "inputs": {"channel": "telegram", "content_ref": "welcome_v2"},
                "pii_access": {"fields": ["mobile"], "sink": "notify.telegram"},
                "depends_on": ["manager_approval"],
            },
        ],
    }


def _task(data: dict[str, Any], task_id: str) -> dict[str, Any]:
    return next(task for task in data["tasks"] if task["id"] == task_id)


def test_the_prd_template_shape_is_accepted() -> None:
    template = Template.model_validate(prd_template())
    assert template.key == "engineer-india"
    assert template.status == "draft"
    assert [task.id for task in template.tasks] == [
        "collect_docs",
        "manager_approval",
        "bitbucket_access",
        "welcome_message",
    ]
    welcome = template.task("welcome_message")
    assert welcome is not None
    assert welcome.pii_access is not None
    assert welcome.pii_access.fields == ("mobile",)
    assert template.task("missing") is None
    assert template.variable("role") == Variable(name="role", type="enum", values=("engineer", "designer", "sales"))
    assert template.variable("missing") is None


def test_a_template_is_immutable() -> None:
    template = Template.model_validate(prd_template())
    with pytest.raises(ValidationError):
        template.key = "other"


def test_the_sla_due_parses_into_variable_offset_and_unit() -> None:
    assert Sla(due="start_date - 7d").parts() == ("start_date", -7, "d")
    assert Sla(due="start_date+48h").parts() == ("start_date", 48, "h")
    assert Sla(due=" start_date ").parts() == ("start_date", 0, "d")


def _rejects(data: dict[str, Any]) -> str:
    with pytest.raises(ValidationError) as caught:
        Template.model_validate(data)
    return str(caught.value)


def _mutated(path: tuple[Any, ...], value: Any) -> dict[str, Any]:
    data = copy.deepcopy(prd_template())
    node: Any = data
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return data


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("unexpected",), 1),
        (("org_id",), "org_acme"),
        (("key",), "Engineer India"),
        (("key",), ""),
        (("version",), 0),
        (("version",), "3"),
        (("status",), "live"),
        (("tasks",), []),
        (("tasks", 0, "id"), "Collect Docs"),
        (("tasks", 0, "title"), ""),
        (("tasks", 0, "agent"), "db-agent"),
        (("tasks", 0, "action"), 7),
        (("tasks", 0, "type"), "script"),
        (("tasks", 0, "surprise"), True),
        (("tasks", 0, "depends_on"), ["collect_docs", "collect_docs"]),
        (("tasks", 0, "requires_approval"), "yes"),
        (("tasks", 0, "sla"), {"due": "start_date - 7 weeks"}),
        (("tasks", 0, "sla"), {"due": "7d"}),
        (("tasks", 0, "sla"), {"due": "start_date - 7d", "extra": 1}),
        (("tasks", 0, "sla_hours"), 0),
        (("tasks", 0, "sla_hours"), 48.5),
        (("tasks", 0, "tools"), ["Find"]),
        (("tasks", 0, "tools"), ["notify.notify_send.x"]),
        (("tasks", 0, "tools"), ["find", "find"]),
        (("tasks", 0, "when"), ""),
        (("tasks", 0, "when"), 1),
        (("tasks", 0, "inputs"), {"": "x"}),
        (("tasks", 0, "inputs"), {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": 1}}}}}}}}}),
        (("tasks", 0, "inputs"), {"a": 1.5j}),
        (("tasks", 0, "inputs"), {"a": [[[[[["deep"]]]]]]}),
        (("tasks", 1, "approver"), {"rule": "variable"}),
        (("tasks", 1, "approver"), {"rule": "anyone", "ref": "x"}),
        (("tasks", 1, "approver"), {"rule": "role", "ref": "CEO"}),
        (("tasks", 3, "pii_access"), {"fields": [], "sink": "notify.telegram"}),
        (("tasks", 3, "pii_access"), {"fields": ["mobile", "mobile"], "sink": "notify.telegram"}),
        (("tasks", 3, "pii_access"), {"fields": ["mobile"], "sink": "Notify Telegram"}),
        (("variables", 0, "values"), []),
        (("variables", 0, "values"), ["engineer", "engineer"]),
        (("variables", 0, "type"), "object"),
        (("variables", 1, "values"), ["Pune"]),
        (("variables", 1, "name"), "Location"),
    ],
)
def test_a_malformed_template_is_refused(path: tuple[Any, ...], value: Any) -> None:
    _rejects(_mutated(path, value))


def test_an_enum_variable_needs_values() -> None:
    data = prd_template()
    del data["variables"][0]["values"]
    assert "enum" in _rejects(data)


def test_task_ids_are_unique() -> None:
    data = prd_template()
    data["tasks"][1]["id"] = "collect_docs"
    assert "task ids: item 1 repeats an earlier item" in _rejects(data)


def test_variable_names_are_unique() -> None:
    data = prd_template()
    data["variables"][1]["name"] = "role"
    assert "variables: item 1 repeats an earlier item" in _rejects(data)


def test_a_repeated_value_is_reported_by_position_not_echoed() -> None:
    data = prd_template()
    data["variables"][0]["values"] = ["engineer", "secret text <x>", "secret text <x>"]
    message = _rejects(data)
    assert "secret text" not in message
    assert "item 2 repeats an earlier item" in message


@pytest.mark.parametrize("length", [MAX_INPUT_STRING + 1, 40_000])
def test_an_overlong_input_string_is_refused(length: int) -> None:
    data = prd_template()
    _task(data, "welcome_message")["inputs"] = {"note": "a" * length}
    _rejects(data)


def test_inputs_larger_than_the_limit_in_total_are_refused() -> None:
    data = prd_template()
    _task(data, "welcome_message")["inputs"] = {f"k{n}": "a" * MAX_INPUT_STRING for n in range(20)}
    assert "inputs" in _rejects(data)


@pytest.mark.parametrize("name", sorted(RESERVED_VARIABLE_NAMES))
def test_a_variable_may_not_take_a_reserved_word_of_the_when_language(name: str) -> None:
    data = prd_template()
    data["variables"][1]["name"] = name
    _rejects(data)


def test_a_task_has_one_sla_form_only() -> None:
    data = prd_template()
    _task(data, "collect_docs")["sla_hours"] = 48
    assert "sla" in _rejects(data)


@pytest.mark.parametrize("role", ["HR", "HR_ADMIN", "MANAGER"])
def test_a_role_approver_names_a_platform_role(role: str) -> None:
    data = prd_template()
    _task(data, "manager_approval")["approver"] = {"rule": "role", "ref": role}
    Template.model_validate(data)


def test_too_many_tasks_are_refused() -> None:
    data = prd_template()
    base = data["tasks"][0]
    data["tasks"] = [{**base, "id": f"task_{n}"} for n in range(101)]
    _rejects(data)


def test_validation_errors_do_not_echo_the_input() -> None:
    data = prd_template()
    _task(data, "collect_docs")["title"] = 12345678
    message = _rejects(data)
    assert "12345678" not in message


def test_a_task_exposes_its_typed_fields() -> None:
    task = TemplateTask.model_validate(_task(prd_template(), "bitbucket_access"))
    assert task.type == "action"
    assert task.requires_approval is True
    assert task.depends_on == ("manager_approval",)
    assert task.tools == ()
    assert task.inputs == {}


@pytest.mark.parametrize(
    ("path", "count"),
    [
        (("variables",), MAX_LIST),
        (("variables", 0, "values"), MAX_LIST),
        (("tasks", 1, "depends_on"), MAX_LIST),
        (("tasks", 0, "tools"), MAX_LIST),
        (("tasks", 3, "pii_access", "fields"), MAX_LIST),
    ],
)
def test_lists_in_a_template_are_bounded(path: tuple[Any, ...], count: int) -> None:
    data = prd_template()
    node: Any = data
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = [
        f"v{n}" if path[-1] != "variables" else {"name": f"v{n}", "type": "string"} for n in range(count + 1)
    ]
    _rejects(data)


@pytest.mark.parametrize("key", ["Mobile.Number", "my key", "mob\u200bile", "\u0430adhaar", "1st"])
def test_input_keys_are_plain_ascii_names(key: str) -> None:
    data = prd_template()
    _task(data, "welcome_message")["inputs"] = {key: "x"}
    _rejects(data)
