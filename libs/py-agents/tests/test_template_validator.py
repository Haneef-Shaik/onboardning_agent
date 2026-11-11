"""FR-T3 validation on save (TPL-1 boxes 1, 2 and 4; PRD §6.1, §5.2, §7.10).

The validator is built from the real config: `config/agents/*.yaml` (the registry's manifests),
`config/tool_catalog.yaml` and `config/pii_sink_policy.yaml`. One named test per FR-T3 rule; each
asserts the rule the report names and the task it names.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from onb_agents.registry import RegisteredAgent, load_catalog, load_manifests
from onb_agents.templates import (
    Issue,
    Rule,
    TemplateInvalidError,
    TemplateReport,
    TemplateValidator,
)
from onb_contracts.templates import Template
from onb_crypto import load_sink_policy

REPO = Path(__file__).resolve().parents[3]
SEED_TEMPLATE = REPO / "templates" / "engineer-india.yaml"
EVAL_FIXTURE = REPO / "evals" / "fixtures" / "seed_v1.json"


@pytest.fixture(scope="module")
def validator() -> TemplateValidator:
    return TemplateValidator.from_config(REPO / "config")


def base() -> dict[str, Any]:
    """The PRD §6.1 template shape (with `action: approval`, the catalog's action for approvals)."""
    return {
        "key": "engineer-india",
        "variables": [
            {"name": "role", "type": "enum", "values": ["engineer", "designer", "sales"]},
            {"name": "location", "type": "string"},
            {"name": "start_date", "type": "date"},
            {"name": "manager_id", "type": "user_ref"},
            {"name": "remote", "type": "boolean"},
            {"name": "level", "type": "number"},
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


def task(data: dict[str, Any], task_id: str) -> dict[str, Any]:
    return next(t for t in data["tasks"] if t["id"] == task_id)


def failing(report: TemplateReport, rule: Rule) -> list[Issue]:
    assert not report.ok
    found = [issue for issue in report.issues if issue.rule == rule]
    assert found, f"expected a {rule} issue, got {report.issues}"
    return found


def test_the_base_template_is_valid(validator: TemplateValidator) -> None:
    report = validator.validate(base())
    assert report.ok, report.issues
    assert report.issues == ()
    assert isinstance(report.template, Template)
    assert report.order == ("collect_docs", "manager_approval", "bitbucket_access", "welcome_message")


# --- box 1: one named failing test per FR-T3 rule -------------------------------------------------


@pytest.mark.parametrize(
    "edges",
    [
        {"collect_docs": ["welcome_message"]},
        {"collect_docs": ["collect_docs"]},
        {"collect_docs": ["bitbucket_access"], "welcome_message": ["welcome_message"]},
    ],
)
def test_a_cycle_is_rejected(validator: TemplateValidator, edges: dict[str, list[str]]) -> None:
    data = base()
    for task_id, depends_on in edges.items():
        task(data, task_id)["depends_on"] = depends_on
    issues = failing(validator.validate(data), Rule.CYCLE)
    assert all(" -> " in issue.message for issue in issues)
    assert {issue.task_id for issue in issues} <= set(edges) | {"manager_approval", "bitbucket_access"}
    assert validator.validate(data).order == ()


def test_the_cycle_message_names_the_loop_in_order(validator: TemplateValidator) -> None:
    data = base()
    task(data, "collect_docs")["depends_on"] = ["bitbucket_access"]
    (issue,) = failing(validator.validate(data), Rule.CYCLE)
    assert issue.message.endswith("collect_docs -> manager_approval -> bitbucket_access -> collect_docs")


def test_a_dependency_on_a_missing_task_is_rejected(validator: TemplateValidator) -> None:
    data = base()
    task(data, "welcome_message")["depends_on"] = ["manager_approval", "laptop"]
    (issue,) = failing(validator.validate(data), Rule.UNKNOWN_DEPENDENCY)
    assert issue.task_id == "welcome_message"
    assert "laptop" in issue.message


@pytest.mark.parametrize("agent", ["hr_bot", "db_agent_v2", "template_builder_x"])
def test_an_unknown_agent_is_rejected(validator: TemplateValidator, agent: str) -> None:
    data = base()
    task(data, "bitbucket_access")["agent"] = agent
    (issue,) = failing(validator.validate(data), Rule.UNKNOWN_AGENT)
    assert issue.task_id == "bitbucket_access"


def test_a_retired_agent_counts_as_unknown() -> None:
    manifests = load_manifests(REPO / "config" / "agents")
    now = datetime.now(UTC)
    registered = [RegisteredAgent(m.name, 2, m.sha256(), m.name != "provisioning_agent", m, now) for m in manifests]
    validator = TemplateValidator.from_registry(
        registered,
        load_catalog(REPO / "config" / "tool_catalog.yaml", manifests),
        load_sink_policy(REPO / "config" / "pii_sink_policy.yaml"),
    )
    (issue,) = failing(validator.validate(base()), Rule.UNKNOWN_AGENT)
    assert issue.task_id == "bitbucket_access"


@pytest.mark.parametrize(
    ("task_id", "tools"),
    [
        ("bitbucket_access", ["notify_send"]),
        ("bitbucket_access", ["mongo.update_one"]),
        ("welcome_message", ["mocks.create_bitbucket_account"]),
        ("welcome_message", ["render", "date_calc"]),
        ("collect_docs", ["insert_one"]),
        ("collect_docs", ["vault.read"]),
    ],
)
def test_a_tool_outside_the_agents_allowlist_is_rejected(
    validator: TemplateValidator, task_id: str, tools: list[str]
) -> None:
    data = base()
    task(data, task_id)["tools"] = tools
    (issue,) = failing(validator.validate(data), Rule.TOOL_NOT_ALLOWED)
    assert issue.task_id == task_id


@pytest.mark.parametrize(
    ("task_id", "tools"),
    [
        ("bitbucket_access", ["create_bitbucket_account", "mocks.provision_laptop", "mongo.find"]),
        ("welcome_message", ["render", "notify.notify_send", "find"]),
        ("collect_docs", ["date_calc", "update_one"]),
    ],
)
def test_tools_inside_the_agents_allowlist_are_accepted(
    validator: TemplateValidator, task_id: str, tools: list[str]
) -> None:
    data = base()
    task(data, task_id)["tools"] = tools
    assert validator.validate(data).ok


@pytest.mark.parametrize(
    ("task_id", "action"),
    [
        ("bitbucket_access", "provision_laptops"),
        ("bitbucket_access", "request_documents"),
        ("welcome_message", "approval"),
        ("collect_docs", "render_and_send"),
    ],
)
def test_an_action_the_catalog_does_not_give_the_agent_is_rejected(
    validator: TemplateValidator, task_id: str, action: str
) -> None:
    data = base()
    task(data, task_id)["action"] = action
    (issue,) = failing(validator.validate(data), Rule.TOOL_NOT_ALLOWED)
    assert issue.task_id == task_id


def test_the_approval_action_needs_an_approval_task_and_back(validator: TemplateValidator) -> None:
    data = base()
    task(data, "manager_approval")["type"] = "action"
    del task(data, "manager_approval")["approver"]
    failing(validator.validate(data), Rule.TOOL_NOT_ALLOWED)


@pytest.mark.parametrize(
    ("task_id", "field", "value"),
    [
        ("bitbucket_access", "when", "department == 'engineering'"),
        ("bitbucket_access", "when", "role == 'engineer' and team in ['core']"),
        ("manager_approval", "approver", {"rule": "variable", "ref": "skip_manager_id"}),
        ("collect_docs", "sla", {"due": "joining_date - 7d"}),
        ("welcome_message", "inputs", {"channel": "telegram", "greeting": "Welcome to {{ team }}"}),
        ("welcome_message", "inputs", {"channel": "telegram", "note": "${ office.city }"}),
    ],
)
def test_an_undeclared_variable_is_rejected(validator: TemplateValidator, task_id: str, field: str, value: Any) -> None:
    data = base()
    task(data, task_id)[field] = value
    (issue,) = failing(validator.validate(data), Rule.UNDECLARED_VARIABLE)
    assert issue.task_id == task_id


def test_declared_variables_may_be_interpolated_in_inputs(validator: TemplateValidator) -> None:
    data = base()
    task(data, "welcome_message")["inputs"] = {"channel": "telegram", "greeting": "Hello from {{ location }}"}
    assert validator.validate(data).ok


def test_an_approval_without_an_approver_is_rejected(validator: TemplateValidator) -> None:
    data = base()
    del task(data, "manager_approval")["approver"]
    (issue,) = failing(validator.validate(data), Rule.APPROVER_MISSING)
    assert issue.task_id == "manager_approval"


@pytest.mark.parametrize(
    ("task_id", "approver"),
    [
        ("manager_approval", {"rule": "variable", "ref": "location"}),
        ("collect_docs", {"rule": "role", "ref": "HR"}),
    ],
)
def test_an_approver_must_be_a_user_variable_on_an_approval(
    validator: TemplateValidator, task_id: str, approver: dict[str, str]
) -> None:
    data = base()
    task(data, task_id)["approver"] = approver
    (issue,) = failing(validator.validate(data), Rule.BAD_APPROVER)
    assert issue.task_id == task_id


@pytest.mark.parametrize("approver", [{"rule": "role", "ref": "HR_ADMIN"}, {"rule": "user", "ref": "u_acme_hradmin"}])
def test_a_requires_approval_task_may_name_an_approver(validator: TemplateValidator, approver: dict[str, str]) -> None:
    data = base()
    task(data, "bitbucket_access")["approver"] = approver
    assert validator.validate(data).ok


@pytest.mark.parametrize(
    "inputs",
    [
        {"recipient": "{{ hire.mobile }}"},
        {"to": "{{ email }}"},
        {"body": "Your Aadhaar is ${hire.pii.aadhaar}"},
        {"lookup": "new_hire.pan"},
        {"account": "hire.bank_account"},
        {"mobile": "on file"},
        {"recipient_email": "x"},
        {"Bank-Account": "x"},
        {"field": "aadhaar"},
        {"fields": ["ifsc", "pan"]},
        {"nested": {"deeper": [{"pii": "x"}]}},
        {"recipient": "ptk_seed_acme001_mobile"},
        {"note": "call +91 90000 00001"},
        {"note": "send to priya.sharma@example.com"},
        {"note": "PAN ABCDE1234F"},
        {"collection": "pii_vault_aadhaar"},
        {"channel": "aadhaar"},
    ],
)
def test_a_sensitive_field_in_llm_visible_inputs_is_rejected(
    validator: TemplateValidator, inputs: dict[str, Any]
) -> None:
    data = base()
    task(data, "welcome_message")["inputs"] = inputs
    issues = failing(validator.validate(data), Rule.SENSITIVE_INPUT)
    assert {issue.task_id for issue in issues} == {"welcome_message"}
    for issue in issues:
        for raw in ("90000 00001", "priya.sharma", "ABCDE1234F", "ptk_seed"):
            assert raw not in issue.message


@pytest.mark.parametrize(
    ("where", "value"),
    [
        (("tasks", 0, "title"), "Collect documents for +91 90000 00001"),
        (("tasks", 2, "when"), "role == 'ABCDE1234F'"),
        (("tasks", 0, "title"), "Collect {{ hire.aadhaar }}"),
        (("name",), "Template for ptk_seed_acme001_email"),
        (("variables", 0, "values"), ["engineer", "priya.sharma@example.com"]),
        (("variables", 1, "name"), "mobile"),
        (("variables", 1, "name"), "aadhaar_number"),
    ],
)
def test_raw_pii_tokens_and_sensitive_names_are_refused_anywhere_in_the_template(
    validator: TemplateValidator, where: tuple[Any, ...], value: Any
) -> None:
    data = base()
    node: Any = data
    for key in where[:-1]:
        node = node[key]
    node[where[-1]] = value
    failing(validator.validate(data), Rule.SENSITIVE_INPUT)


@pytest.mark.parametrize(
    "inputs",
    [
        {"channel": "email", "content_ref": "welcome_v2"},
        {"channel": "telegram", "documents": ["aadhaar_scan", "pan_scan"]},
        {"subject": "Welcome aboard", "count": 2, "flag": True, "none": None},
        {"greeting": "Hi {{ role }}, see you in {{ location }}"},
    ],
)
def test_ordinary_inputs_are_accepted(validator: TemplateValidator, inputs: dict[str, Any]) -> None:
    data = base()
    task(data, "welcome_message")["inputs"] = inputs
    assert validator.validate(data).ok, validator.validate(data).issues


@pytest.mark.parametrize(
    "pii_access",
    [
        {"fields": ["mobile"], "sink": "notify.email"},
        {"fields": ["email"], "sink": "notify.telegram"},
        {"fields": ["mobile", "email"], "sink": "notify.telegram"},
        {"fields": ["mobile"], "sink": "notify.sms"},
        {"fields": ["pan"], "sink": "notify.email"},
        {"fields": ["bank_account"], "sink": "notify.telegram"},
        {"fields": ["ifsc"], "sink": "notify.telegram"},
        {"fields": ["account_holder_name"], "sink": "notify.email"},
        {"fields": ["salary"], "sink": "notify.email"},
    ],
)
def test_pii_access_not_allowed_by_the_sink_policy_is_rejected(
    validator: TemplateValidator, pii_access: dict[str, Any]
) -> None:
    data = base()
    task(data, "welcome_message")["pii_access"] = pii_access
    (issue,) = failing(validator.validate(data), Rule.PII_ACCESS_FORBIDDEN)
    assert issue.task_id == "welcome_message"


def test_pii_access_on_an_agent_without_the_sinks_server_is_rejected(validator: TemplateValidator) -> None:
    data = base()
    task(data, "bitbucket_access")["pii_access"] = {"fields": ["mobile"], "sink": "notify.telegram"}
    (issue,) = failing(validator.validate(data), Rule.TOOL_NOT_ALLOWED)
    assert issue.task_id == "bitbucket_access"


def test_email_at_the_email_sink_is_allowed(validator: TemplateValidator) -> None:
    data = base()
    task(data, "welcome_message")["pii_access"] = {"fields": ["email"], "sink": "notify.email"}
    task(data, "welcome_message")["inputs"] = {"channel": "email", "content_ref": "welcome_v2"}
    assert validator.validate(data).ok


# --- box 4: pii_access for Aadhaar is rejected ---------------------------------------------------


@pytest.mark.parametrize("sink", ["notify.telegram", "notify.email", "notify.sms", "uidai.verify"])
@pytest.mark.parametrize("fields", [["aadhaar"], ["mobile", "aadhaar"]])
def test_pii_access_for_aadhaar_is_rejected(validator: TemplateValidator, sink: str, fields: list[str]) -> None:
    data = base()
    task(data, "welcome_message")["pii_access"] = {"fields": fields, "sink": sink}
    issues = failing(validator.validate(data), Rule.PII_ACCESS_FORBIDDEN)
    assert any("aadhaar" in issue.message for issue in issues)
    assert {issue.task_id for issue in issues} == {"welcome_message"}


# --- conditions and SLAs -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "when",
    [
        "role == 'enginer'",
        "role in ['engineer', 'manager']",
        "remote == 'yes'",
        "level == 'three'",
        "location == 3",
        "role.upper() == 'ENGINEER'",
        "len(role) > 3",
        "__import__('os')",
    ],
)
def test_a_bad_condition_is_rejected(validator: TemplateValidator, when: str) -> None:
    data = base()
    task(data, "bitbucket_access")["when"] = when
    (issue,) = failing(validator.validate(data), Rule.BAD_CONDITION)
    assert issue.task_id == "bitbucket_access"


@pytest.mark.parametrize(
    "when",
    ["remote == true and level >= 2", "manager_id != null", "start_date < '2027-01-01'", "not remote"],
)
def test_typed_conditions_are_accepted(validator: TemplateValidator, when: str) -> None:
    data = base()
    task(data, "bitbucket_access")["when"] = when
    assert validator.validate(data).ok


def test_an_sla_needs_a_date_variable(validator: TemplateValidator) -> None:
    data = base()
    task(data, "collect_docs")["sla"] = {"due": "location + 2d"}
    (issue,) = failing(validator.validate(data), Rule.BAD_SLA)
    assert issue.task_id == "collect_docs"


# --- schema errors and the report ----------------------------------------------------------------


def test_a_schema_error_is_reported_without_the_input(validator: TemplateValidator) -> None:
    data = base()
    task(data, "collect_docs")["title"] = 919000000001
    data["surprise"] = "ABCDE1234F"
    report = validator.validate(data)
    issues = failing(report, Rule.SCHEMA)
    assert report.template is None
    assert len(issues) == 2
    assert all("919000000001" not in i.message and "ABCDE1234F" not in i.message for i in issues)


def test_a_non_mapping_is_a_schema_error(validator: TemplateValidator) -> None:
    failing(validator.validate(["not", "a", "template"]), Rule.SCHEMA)


def test_several_problems_are_all_reported_in_template_order(validator: TemplateValidator) -> None:
    data = base()
    task(data, "collect_docs")["agent"] = "ghost_agent"
    del task(data, "manager_approval")["approver"]
    task(data, "welcome_message")["pii_access"] = {"fields": ["aadhaar"], "sink": "notify.telegram"}
    report = validator.validate(data)
    assert [issue.rule for issue in report.issues] == [
        Rule.UNKNOWN_AGENT,
        Rule.APPROVER_MISSING,
        Rule.PII_ACCESS_FORBIDDEN,
    ]
    assert report.rules == frozenset({Rule.UNKNOWN_AGENT, Rule.APPROVER_MISSING, Rule.PII_ACCESS_FORBIDDEN})


def test_require_valid_returns_the_template_or_raises(validator: TemplateValidator) -> None:
    assert validator.require_valid(base()).key == "engineer-india"
    data = base()
    del task(data, "manager_approval")["approver"]
    with pytest.raises(TemplateInvalidError) as caught:
        validator.require_valid(data)
    assert caught.value.issues[0].rule == Rule.APPROVER_MISSING
    assert "approver_missing" in str(caught.value)


def test_a_parsed_template_can_be_validated_too(validator: TemplateValidator) -> None:
    assert validator.validate(Template.model_validate(base())).ok


def test_the_issue_document_is_plain_data(validator: TemplateValidator) -> None:
    data = base()
    del task(data, "manager_approval")["approver"]
    (issue,) = validator.validate(data).issues
    assert issue.document() == {"rule": "approver_missing", "task_id": "manager_approval", "message": issue.message}


def test_the_validator_does_not_change_its_input(validator: TemplateValidator) -> None:
    data = base()
    before = copy.deepcopy(data)
    validator.validate(data)
    assert data == before


# --- box 2: the seed template and every eval fixture template validate ----------------------------


def test_the_seed_template_validates(validator: TemplateValidator) -> None:
    data = yaml.safe_load(SEED_TEMPLATE.read_text(encoding="utf-8"))
    report = validator.validate(data)
    assert report.ok, report.issues
    assert report.template is not None
    assert report.template.key == "engineer-india"
    actions = {t.action for t in report.template.tasks}
    assert {"request_documents", "approval", "create_bitbucket_account", "render_and_send"} <= actions


def _fixture_templates() -> list[dict[str, Any]]:
    return list(json.loads(EVAL_FIXTURE.read_text(encoding="utf-8"))["templates"])


@pytest.mark.parametrize("record", _fixture_templates(), ids=lambda r: f"{r['org_id']}/{r['key']}@v{r['version']}")
def test_every_template_in_the_eval_fixture_validates(validator: TemplateValidator, record: dict[str, Any]) -> None:
    # `org_id` is the storage envelope (from the signed token), not part of the template.
    template = {key: value for key, value in record.items() if key != "org_id"}
    report = validator.validate(template)
    assert report.ok, report.issues


def test_the_eval_fixture_holds_templates() -> None:
    assert len(_fixture_templates()) >= 2


# --- review fixes (code review of attempt 1) -----------------------------------------------------


@pytest.mark.parametrize(
    ("variables", "note"),
    [
        ([{"name": "personal_email", "type": "string"}], "{{ personal_email }}"),
        ([{"name": "aadhaar_no", "type": "string"}], "{{ aadhaar_no }}"),
        ([{"name": "hire", "type": "string"}], "{{ hire.emailId }}"),
        ([{"name": "hire", "type": "string"}], "{{ hire.e_mail }}"),
        ([{"name": "hire", "type": "string"}], "see hire.contact_mobile"),
        ([{"name": "hire", "type": "string"}], "see hire.panNumber"),
        ([], "${ hire.MobileNo }"),
    ],
)
def test_sensitive_names_are_found_inside_longer_names(
    validator: TemplateValidator, variables: list[dict[str, str]], note: str
) -> None:
    data = base()
    data["variables"].extend(variables)
    task(data, "welcome_message")["inputs"] = {"channel": "telegram", "note": note}
    failing(validator.validate(data), Rule.SENSITIVE_INPUT)


@pytest.mark.parametrize("name", ["personal_email", "aadhaar_no", "contact_mobile", "pan_no"])
def test_a_variable_named_after_a_sensitive_field_in_part_is_refused(validator: TemplateValidator, name: str) -> None:
    data = base()
    data["variables"].append({"name": name, "type": "string"})
    failing(validator.validate(data), Rule.SENSITIVE_INPUT)


def test_a_long_input_string_validates_quickly(validator: TemplateValidator) -> None:
    import time

    data = base()
    task(data, "welcome_message")["inputs"] = {"channel": "telegram", "note": "a" * 2000, "path": "a." * 1000}
    started = time.perf_counter()
    validator.validate(data)
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize(
    "approver", [{"rule": "user", "ref": "9876543210"}, {"rule": "user", "ref": "ptk_seed_acme001_mobile"}]
)
def test_raw_pii_in_an_approver_ref_is_refused(validator: TemplateValidator, approver: dict[str, str]) -> None:
    data = base()
    task(data, "bitbucket_access")["approver"] = approver
    issues = failing(validator.validate(data), Rule.SENSITIVE_INPUT)
    assert all("9876543210" not in issue.message for issue in issues)


def test_a_deeply_nested_schema_error_has_a_short_message(validator: TemplateValidator) -> None:
    value: Any = "x"
    for _ in range(3000):
        value = [value]
    data = base()
    task(data, "welcome_message")["inputs"] = {"deep": value}
    issues = failing(validator.validate(data), Rule.SCHEMA)
    assert all(len(issue.message) < 300 for issue in issues)


@pytest.mark.parametrize("when", ["role == level", "remote == location", "level < start_date"])
def test_two_variables_of_different_types_cannot_be_compared(validator: TemplateValidator, when: str) -> None:
    data = base()
    task(data, "bitbucket_access")["when"] = when
    (issue,) = failing(validator.validate(data), Rule.BAD_CONDITION)
    assert issue.task_id == "bitbucket_access"


def test_two_text_variables_may_be_compared(validator: TemplateValidator) -> None:
    data = base()
    task(data, "bitbucket_access")["when"] = "role == location"
    assert validator.validate(data).ok


# --- review fixes (security review of attempt 1) -------------------------------------------------


@pytest.mark.parametrize("value", [9876543210, 234123412346, 9876543210.0, [9876543210]])
def test_numeric_pii_in_inputs_is_refused(validator: TemplateValidator, value: Any) -> None:
    data = base()
    task(data, "welcome_message")["inputs"] = {"channel": "telegram", "slot": value}
    failing(validator.validate(data), Rule.SENSITIVE_INPUT)


@pytest.mark.parametrize(
    "value",
    ["mob\u200bile", "\uff4dobile", "\u0430adhaar", "pii\u200b_vault", "{{ hire.\u0430adhaar }}", "e\u00adm\u00adail"],
)
def test_sensitive_values_hidden_by_unicode_tricks_are_refused(validator: TemplateValidator, value: str) -> None:
    data = base()
    task(data, "welcome_message")["inputs"] = {"channel": "telegram", "field": value}
    failing(validator.validate(data), Rule.SENSITIVE_INPUT)


@pytest.mark.parametrize("key", ["mobileNumber", "Mobile.Number", "e_mail", "mob\u200bile", "\u0430adhaar", "my key"])
def test_input_keys_hiding_a_sensitive_name_are_refused(validator: TemplateValidator, key: str) -> None:
    data = base()
    task(data, "welcome_message")["inputs"] = {"channel": "telegram", key: "x"}
    report = validator.validate(data)
    assert not report.ok
    assert report.rules & {Rule.SENSITIVE_INPUT, Rule.SCHEMA}


@pytest.mark.parametrize("value", ['hire["aadhaar"]', "hire/aadhaar", "hire['pan_number']"])
def test_other_path_forms_naming_a_sensitive_field_are_refused(validator: TemplateValidator, value: str) -> None:
    data = base()
    task(data, "welcome_message")["inputs"] = {"channel": "telegram", "lookup": value}
    failing(validator.validate(data), Rule.SENSITIVE_INPUT)


@pytest.mark.parametrize("note", ["Please reply by email.", "Bring your PAN card and/or passport.", "e.g. laptop"])
def test_prose_mentioning_a_field_is_not_a_reference(validator: TemplateValidator, note: str) -> None:
    data = base()
    task(data, "welcome_message")["inputs"] = {"channel": "telegram", "note": note}
    assert validator.validate(data).ok, validator.validate(data).issues


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("tasks", 0, "id"), "x9876543210"),
        (("tasks", 0, "id"), "ptk_x"),
        (("variables", 1, "name"), "ptk_abc"),
        (("variables", 1, "name"), "pii_vault_x"),
        (("variables", 1, "name"), "m9876543210"),
        (("tasks", 1, "approver"), {"rule": "user", "ref": "ABCDE1234F"}),
        (("tasks", 1, "approver"), {"rule": "user", "ref": "234123412346"}),
    ],
)
def test_raw_pii_and_tokens_in_identifiers_are_refused(
    validator: TemplateValidator, path: tuple[Any, ...], value: Any
) -> None:
    data = base()
    node: Any = data
    for key in path[:-1]:
        node = node[key]
    old = node[path[-1]]
    node[path[-1]] = value
    if path[-1] == "id":
        for other in data["tasks"]:
            other["depends_on"] = [value if d == old else d for d in other.get("depends_on", [])]
    failing(validator.validate(data), Rule.SENSITIVE_INPUT)


def test_a_cycle_message_does_not_echo_raw_values(validator: TemplateValidator) -> None:
    data = base()
    task(data, "collect_docs")["id"] = "m9876543210"
    task(data, "m9876543210")["depends_on"] = ["m9876543210"]
    task(data, "manager_approval")["depends_on"] = ["m9876543210"]
    issues = failing(validator.validate(data), Rule.CYCLE)
    assert all("9876543210" not in issue.message for issue in issues)


def test_input_findings_name_the_key_by_position(validator: TemplateValidator) -> None:
    data = base()
    task(data, "welcome_message")["inputs"] = {"channel": "telegram", "ravi_kumar_dob_mobile": "x"}
    (issue,) = failing(validator.validate(data), Rule.SENSITIVE_INPUT)
    assert "ravi_kumar" not in issue.message
    assert "inputs[1]" in issue.message


def test_a_schema_error_inside_inputs_does_not_name_input_keys(validator: TemplateValidator) -> None:
    data = base()
    task(data, "welcome_message")["inputs"] = {"Ravi Kumar DOB": {"x": 1.5j}}
    issues = failing(validator.validate(data), Rule.SCHEMA)
    assert all("Ravi" not in issue.message for issue in issues)
