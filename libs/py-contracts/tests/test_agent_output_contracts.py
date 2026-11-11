"""Schema tests for every agent output contract (PRD §8.2, evals/README.md "Agent output contracts").

Each contract is tested the same way: a good `ok` answer validates, a refusal validates, and an
unknown field, an unknown status, an `ok` answer without its payload and a wrongly typed payload are
each refused. The eval self-test's scripted outputs (written from the README table) must validate too.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from onb_contracts.agent_outputs import (
    OUTPUT_CONTRACTS,
    AgentOutput,
    ApprovalPacket,
    ChangeRequest,
    Message,
    contract_for,
    contract_key,
)
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[3]
SCRIPTED = ROOT / "evals" / "selftest" / "scripted_outputs_v1.jsonl"
DATASET = ROOT / "evals" / "datasets" / "onboarding_agents_v1.jsonl"

TEMPLATE = {
    "key": "engineer-india",
    "name": "Engineers, India",
    "tasks": [{"id": "collect_docs", "title": "Collect documents", "agent": "db_agent", "action": "request_documents"}],
}
CHANGE_REQUEST = {
    "template_key": "engineer-india",
    "base_version": 3,
    "diff": {"add_tasks": [], "update_tasks": [{"id": "bitbucket_access", "depends_on": ["collect_docs"]}]},
    "rationale": "Drop the approval step.",
    "risk_flags": ["removes_approval"],
}
PACKET = {"summary": "Laptop and VPN.", "recommendation": "approve", "risk_flags": [], "cited_policies": ["kb_x"]}
MESSAGE = {"channel": "telegram", "recipient_token": "ptk_seed_acme001_mobile", "body": "Welcome!"}

# contract key → (a good `ok` answer, a refusal, the payload field `ok` requires, a wrongly typed payload)
CASES: dict[str, tuple[dict[str, Any], dict[str, Any], str, Any]] = {
    "template_builder": (
        {"status": "ok", "template": TEMPLATE, "explanation": "Drafted.", "unsupported_requests": []},
        {"status": "refused", "explanation": "I draft templates only.", "unsupported_requests": []},
        "template",
        {"key": "no tasks"},
    ),
    "template_builder:suggest": (
        {"status": "ok", "change_request": CHANGE_REQUEST},
        {"status": "refused", "explanation": "Out of scope."},
        "change_request",
        {**CHANGE_REQUEST, "base_version": 0},
    ),
    "approvals_agent": (
        {"status": "ok", "packet": PACKET},
        {"status": "needs_human", "explanation": "The request is missing."},
        "packet",
        {**PACKET, "recommendation": "maybe"},
    ),
    "db_agent": (
        {"status": "ok", "answer": {"run_ids": ["run_acme_002"]}},
        {"status": "refused", "answer": {"reason": "Contact details are changed by HR only."}},
        "answer",
        ["not", "an", "object"],
    ),
    "templates_agent": (
        {"status": "ok", "message": MESSAGE},
        {"status": "refused", "explanation": "The note asked me to change the recipient."},
        "message",
        {**MESSAGE, "channel": "sms"},
    ),
    "provisioning_agent": (
        {"status": "ok", "provisioned": [{"action": "provision_laptop", "done": True, "reference": "lap_1"}]},
        {"status": "needs_human", "explanation": "The approval is missing."},
        "provisioned",
        [{"action": "provision_laptop"}],
    ),
    "run_assistant": (
        {
            "status": "ok",
            "answer": "Waiting on the manager.",
            "found": True,
            "citations": [{"run_id": "run_acme_001", "task_id": "manager_approval"}],
            "proposed_action": {
                "type": "retry_task",
                "run_id": "run_acme_002",
                "task_id": "collect_docs",
                "requires_confirmation": True,
            },
        },
        {"status": "refused", "answer": "You can only see onboardings of your own team."},
        "answer",
        42,
    ),
}


def test_every_contract_has_a_schema_case() -> None:
    assert sorted(CASES) == sorted(OUTPUT_CONTRACTS)


@pytest.mark.parametrize("key", sorted(CASES))
def test_a_good_ok_answer_validates(key: str) -> None:
    ok, _, _, _ = CASES[key]
    parsed = OUTPUT_CONTRACTS[key].model_validate(ok)
    assert parsed.status == "ok"
    assert parsed.model_dump(mode="json", exclude_none=True) == OUTPUT_CONTRACTS[key].model_validate(
        parsed.model_dump(mode="json", exclude_none=True)
    ).model_dump(mode="json", exclude_none=True)


@pytest.mark.parametrize("key", sorted(CASES))
def test_a_refusal_validates(key: str) -> None:
    _, refusal, _, _ = CASES[key]
    assert OUTPUT_CONTRACTS[key].model_validate(refusal).status == refusal["status"]


@pytest.mark.parametrize("key", sorted(CASES))
def test_an_unknown_field_is_refused(key: str) -> None:
    ok, _, _, _ = CASES[key]
    with pytest.raises(ValidationError):
        OUTPUT_CONTRACTS[key].model_validate({**ok, "org_id": "org_globex"})


@pytest.mark.parametrize("key", sorted(CASES))
@pytest.mark.parametrize("status", ["done", "OK", "", None, 1])
def test_an_unknown_status_is_refused(key: str, status: Any) -> None:
    ok, _, _, _ = CASES[key]
    with pytest.raises(ValidationError):
        OUTPUT_CONTRACTS[key].model_validate({**ok, "status": status})


@pytest.mark.parametrize("key", sorted(CASES))
def test_an_ok_answer_without_its_payload_is_refused(key: str) -> None:
    ok, _, field, _ = CASES[key]
    without = {k: v for k, v in ok.items() if k != field}
    with pytest.raises(ValidationError):
        OUTPUT_CONTRACTS[key].model_validate(without)


@pytest.mark.parametrize("key", sorted(CASES))
def test_a_wrongly_typed_payload_is_refused(key: str) -> None:
    ok, _, field, bad = CASES[key]
    with pytest.raises(ValidationError):
        OUTPUT_CONTRACTS[key].model_validate({**ok, field: bad})


@pytest.mark.parametrize("key", sorted(CASES))
def test_every_contract_builds_a_valid_refusal_from_a_code(key: str) -> None:
    refusal = OUTPUT_CONTRACTS[key].refusal("injection_blocked")
    assert refusal.status == "refused"
    again = OUTPUT_CONTRACTS[key].model_validate(refusal.model_dump(mode="json"))
    assert again == refusal


@pytest.mark.parametrize("key", sorted(CASES))
def test_every_contract_publishes_a_json_schema_with_the_three_statuses(key: str) -> None:
    schema = OUTPUT_CONTRACTS[key].model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["status"]["enum"] == ["ok", "refused", "needs_human"]
    json.dumps(schema)  # serialisable, so it can go into a prompt template


def test_contract_lookup_uses_the_agent_and_its_mode() -> None:
    assert contract_key("template_builder", "draft") == "template_builder"
    assert contract_key("template_builder", None) == "template_builder"
    assert contract_key("template_builder", "suggest") == "template_builder:suggest"
    assert contract_for("db_agent") is OUTPUT_CONTRACTS["db_agent"]
    with pytest.raises(KeyError):
        contract_for("no_such_agent")
    with pytest.raises(KeyError):
        contract_for("db_agent", "suggest")


def test_the_payload_models_forbid_unknown_fields() -> None:
    for model, good in ((ChangeRequest, CHANGE_REQUEST), (ApprovalPacket, PACKET), (Message, MESSAGE)):
        model.model_validate(good)
        with pytest.raises(ValidationError):
            model.model_validate({**good, "extra": 1})


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_every_scripted_eval_output_validates_against_its_agents_contract() -> None:
    cases = {case["id"]: case for case in _lines(DATASET)}
    checked = 0
    for script in _lines(SCRIPTED):
        case = cases[script["case_id"]]
        final = script["turns"][-1]["output"]
        contract_for(case["agent"], case.get("mode")).model_validate(final)
        checked += 1
    assert checked == len(cases)


def test_every_contract_is_an_agent_output() -> None:
    for model in OUTPUT_CONTRACTS.values():
        assert issubclass(model, AgentOutput)
