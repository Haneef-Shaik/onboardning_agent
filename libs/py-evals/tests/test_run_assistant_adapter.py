"""The Run Assistant adapter (CHAT-1): the registered agent runs through AG-1's runtime on the scripted `fake`
model, calls the harness's ToolBox as `mongo__find`, and returns its contract-valid output. No model call."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from onb_evals.adapters import adapter_for
from onb_evals.cases import parse_case
from onb_evals.fixture import load_fixture
from onb_evals.runner import AgentInvocation
from onb_evals.scripted import ScriptedLLM, fake_runtime
from onb_evals.toolbox import ToolBox

from .memory_store import MemoryStore, as_data_store
from .support import DATASET, SEED


def _case(case_id: str) -> Any:
    for line in DATASET.read_text().splitlines():
        if json.loads(line)["id"] == case_id:
            return parse_case(json.loads(line))
    raise AssertionError(case_id)


def _run(case_id: str, turns: list[dict[str, Any]]) -> tuple[Any, ToolBox]:
    case = _case(case_id)
    store = MemoryStore()
    fixture = asyncio.run(load_fixture(as_data_store(store), SEED))
    box = ToolBox(as_data_store(store), case.actor, fixture.tokens)
    model = ScriptedLLM(fake_runtime(), turns).chat_model([], fixture.tokens)
    invocation = AgentInvocation(case=case, input=case.input, tools=box, chat_model=model, repetition=0)
    return asyncio.run(adapter_for("run_assistant").invoke(invocation)), box


def test_the_adapter_is_registered_and_returns_the_agents_validated_output() -> None:
    turns: list[dict[str, Any]] = [
        {
            "tool_calls": [
                {"name": "mongo__find", "args": {"collection": "run_tasks", "filter": {"run_id": "run_acme_001"}}}
            ]
        },
        {
            "content": json.dumps(
                {
                    "status": "ok",
                    "found": True,
                    "answer": "It waits on Ravi's manager approval, overdue.",
                    "citations": [{"run_id": "run_acme_001", "task_id": "manager_approval"}],
                }
            )
        },
    ]
    output, box = _run("ra-001", turns)
    assert output["status"] == "ok"
    assert output["citations"] == [{"run_id": "run_acme_001", "task_id": "manager_approval"}]
    assert [c.name for c in box.calls] == ["find"]  # the call went through the harness's authority, recorded


def test_a_task_that_does_not_finish_fails_the_case_instead_of_answering() -> None:
    turns = [{"content": "I am not JSON"}] * 3
    with pytest.raises(RuntimeError, match="invalid_output"):
        _run("ra-001", turns)
