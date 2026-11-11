"""TPL-1 replaces the harness's interim template contract with the real schema (EVAL-1 handoff)."""

from __future__ import annotations

from onb_contracts.templates import Template, TemplateTask
from onb_evals.contracts import DEFAULT_CONTRACTS, ChangeRequest


def test_the_template_contract_is_the_real_template_schema() -> None:
    assert DEFAULT_CONTRACTS["template"] == ("template", Template)


def test_a_change_request_adds_tasks_of_the_real_task_schema() -> None:
    request = ChangeRequest.model_validate(
        {
            "template_key": "engineer-india",
            "base_version": 3,
            "diff": {
                "add_tasks": [
                    {
                        "id": "security_training",
                        "title": "Complete security training",
                        "agent": "db_agent",
                        "action": "assign_training",
                        "depends_on": ["manager_approval"],
                    }
                ]
            },
            "rationale": "Security training before access.",
        }
    )
    assert isinstance(request.diff.add_tasks[0], TemplateTask)
