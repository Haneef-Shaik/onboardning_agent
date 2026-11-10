"""The `fake` connector: a deterministic, scripted chat model that can call tools."""

import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from onb_llm import LLMConfigError
from onb_llm.connectors.fake import FakeConnector, ScriptedChatModel, ScriptExhaustedError

SCRIPT: list[dict[str, Any]] = [
    {
        "content": "",
        "tool_calls": [{"name": "add_days", "args": {"date": "2026-11-10", "days": 3}}],
        "usage": {"input_tokens": 20, "output_tokens": 5},
    },
    {"content": "The start date is 2026-11-13."},
]


@tool
def add_days(date: str, days: int) -> str:
    """Add `days` to an ISO `date`."""
    year, month, day = (int(part) for part in date.split("-"))
    return f"{year:04d}-{month:02d}-{day + days:02d}"


def _run_agent_loop(script: list[dict[str, Any]]) -> tuple[list[BaseMessage], ScriptedChatModel]:
    """The smallest agent loop: call the model, run any tool calls, feed results back, repeat."""
    chat_model = FakeConnector().create_chat_model("scripted", {"script": script})
    assert isinstance(chat_model, ScriptedChatModel)
    bound = chat_model.bind_tools([add_days])
    messages: list[BaseMessage] = [HumanMessage("When does someone starting 3 days after 2026-11-10 start?")]
    for _ in range(5):
        reply = bound.invoke(messages)
        assert isinstance(reply, AIMessage)
        messages.append(reply)
        if not reply.tool_calls:
            break
        for call in reply.tool_calls:
            messages.append(ToolMessage(content=add_days.invoke(call["args"]), tool_call_id=call["id"]))
    return messages, chat_model


def test_the_fake_connector_replays_a_scripted_tool_calling_conversation() -> None:
    messages, chat_model = _run_agent_loop(SCRIPT)

    assert [type(m).__name__ for m in messages] == ["HumanMessage", "AIMessage", "ToolMessage", "AIMessage"]
    first, tool_result, final = messages[1], messages[2], messages[3]
    assert isinstance(first, AIMessage)
    assert first.tool_calls == [
        {"name": "add_days", "args": {"date": "2026-11-10", "days": 3}, "id": "call_0_0", "type": "tool_call"}
    ]
    assert first.usage_metadata == {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25}
    assert tool_result.content == "2026-11-13"
    assert final.content == "The start date is 2026-11-13."
    # The model saw the tool's result before it gave its final answer, and the bound tools each time.
    calls = chat_model.calls()
    assert len(calls) == 2
    assert calls[1].messages[-1] == tool_result
    assert {call.tool_names for call in calls} == {("add_days",)}


def test_the_replay_is_deterministic() -> None:
    first, _ = _run_agent_loop(SCRIPT)
    second, _ = _run_agent_loop(SCRIPT)
    assert first == second


def test_a_script_that_runs_out_fails_loudly() -> None:
    chat_model = FakeConnector().create_chat_model("scripted", {"script": [{"content": "only one"}]})
    chat_model.invoke("first")
    with pytest.raises(ScriptExhaustedError, match="1 scripted"):
        chat_model.invoke("second")


def test_a_scripted_call_to_a_tool_that_was_not_bound_fails() -> None:
    script = [{"content": "", "tool_calls": [{"name": "drop_database", "args": {}}]}]
    chat_model = FakeConnector().create_chat_model("scripted", {"script": script})
    with pytest.raises(LLMConfigError, match=r"drop_database.*add_days"):
        chat_model.bind_tools([add_days]).invoke("hi")


def test_usage_is_estimated_deterministically_when_the_script_gives_none() -> None:
    chat_model = FakeConnector().create_chat_model("scripted", {"script": [{"content": "four words in here"}]})
    reply = chat_model.invoke("two words")
    assert isinstance(reply, AIMessage)
    assert reply.usage_metadata == {"input_tokens": 2, "output_tokens": 4, "total_tokens": 6}


def test_common_sampling_params_are_accepted_and_ignored() -> None:
    chat_model = FakeConnector().create_chat_model("scripted", {"temperature": 0.3, "max_tokens": 9, "top_p": 1})
    assert chat_model.invoke("ping").content == "OK"


def test_the_default_script_answers_ok() -> None:
    assert FakeConnector().create_chat_model("scripted", {}).invoke("ping").content == "OK"


def test_a_script_can_come_from_a_json_file(tmp_path: Path) -> None:
    path = tmp_path / "script.json"
    path.write_text(json.dumps([{"content": "from file"}]), encoding="utf-8")
    chat_model = FakeConnector().create_chat_model("scripted", {"script_file": str(path)})
    assert chat_model.invoke("hi").content == "from file"


@pytest.mark.parametrize(
    "params",
    [
        {"script": "not a list"},
        {"script": [{"content": 3}]},
        {"script": [{"content": "x", "tool_calls": [{"args": {}}]}]},
        {"script": [{"content": "x", "surprise": True}]},
        {"script": [], "script_file": "x.json"},
        {"script_file": "/definitely/not/here.json"},
        {"unknown": 1},
    ],
)
def test_a_bad_script_is_rejected_when_the_model_is_created(params: dict[str, Any]) -> None:
    with pytest.raises(LLMConfigError):
        FakeConnector().create_chat_model("scripted", params)


def test_the_fake_connector_is_always_healthy() -> None:
    status = FakeConnector().healthcheck("scripted", {})
    assert status.ok
    assert status.detail == "OK"


def test_the_fake_connector_needs_no_environment() -> None:
    assert FakeConnector.required_env == ()
