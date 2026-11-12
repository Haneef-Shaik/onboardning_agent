"""RUN-3 box 4: a run's spans form run → task → agent → LLM/tool, in one trace.

An in-memory exporter captures the spans of a diamond run whose agents call the model and a tool.
Spans carry ids only (org, run, task, agent, tool name), never a prompt, a grant or a hire value.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from onb_telemetry.testing import CapturedTelemetry, captured_telemetry
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan

from .agent_support import OK, Harness, Models, Tools, final, find_tool, tool_turn

SCRIPT = [tool_turn("mongo__find", {"collection": "run_tasks"}), final(OK)]


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def by_name(spans: tuple[ReadableSpan, ...], name: str) -> list[ReadableSpan]:
    return [s for s in spans if s.name == name]


def parent_of(span: ReadableSpan, spans: tuple[ReadableSpan, ...]) -> ReadableSpan | None:
    parent = span.parent
    return next((s for s in spans if parent is not None and s.context.span_id == parent.span_id), None)


def attr(span: ReadableSpan, key: str) -> Any:
    return (span.attributes or {}).get(key)


def execute_diamond(tmp_path: Path, captured: CapturedTelemetry) -> tuple[str, tuple[ReadableSpan, ...]]:
    h = Harness.create(Models(SCRIPT, tmp_path), Tools(bound=(find_tool(),)))
    run_id = run(h.start_diamond())
    run(h.execute(run_id, h.executor()))
    return run_id, captured.spans()


def ancestry(span: ReadableSpan, spans: tuple[ReadableSpan, ...]) -> list[str]:
    names: list[str] = []
    node = parent_of(span, spans)
    while node is not None:
        names.append(node.name)
        node = parent_of(node, spans)
    return names


def test_a_runs_spans_form_run_task_agent_llm_and_tool(tmp_path: Path) -> None:
    with captured_telemetry() as captured:
        run_id, spans = execute_diamond(tmp_path, captured)
    [run_span] = by_name(spans, "orchestrator.run")
    tasks = by_name(spans, "orchestrator.task")
    agents = by_name(spans, "agent.run")
    llm = by_name(spans, "llm.chat")
    tools = by_name(spans, "tool.call")
    assert attr(run_span, "run_id") == run_id
    assert {attr(t, "task_id") for t in tasks} == {"a", "b", "c", "d"}
    assert len(agents) == len(tasks) == 4
    assert len(llm) == 8  # two model turns per task: the tool call, then the answer
    assert len(tools) == 4
    for task_span in tasks:
        assert parent_of(task_span, spans) is run_span  # run → task, parallel branches included
    for agent_span in agents:
        task_span = parent_of(agent_span, spans)
        assert task_span is not None
        assert task_span.name == "orchestrator.task"
        assert attr(agent_span, "task_id") == attr(task_span, "task_id")
        assert attr(agent_span, "agent.name") == "db_agent"
    for tool_span in tools:
        parent = parent_of(tool_span, spans)
        assert parent is not None
        assert parent.name == "agent.run"  # agent → tool
        assert attr(tool_span, "tool.name") == "mongo__find"
    for llm_span in llm:  # agent → (guardrails.invoke →) llm.chat
        assert ancestry(llm_span, spans) == ["guardrails.invoke", "agent.run", "orchestrator.task", "orchestrator.run"]
    assert len({s.context.trace_id for s in [run_span, *tasks, *agents, *llm, *tools]}) == 1


def test_the_run_span_joins_the_trace_of_the_request_that_started_the_run(tmp_path: Path) -> None:
    """The run's stored traceparent (from the API request) is the parent of the run span (F-4)."""
    with captured_telemetry() as captured:
        h = Harness.create(Models(SCRIPT, tmp_path), Tools(bound=(find_tool(),)))
        with trace.get_tracer("test").start_as_current_span("POST /runs") as request:
            run_id = run(h.start_diamond())
        run(h.execute(run_id, h.executor()))
        spans = captured.spans()
    [run_span] = by_name(spans, "orchestrator.run")
    assert run_span.parent is not None
    assert run_span.parent.span_id == request.get_span_context().span_id
    assert run_span.context.trace_id == request.get_span_context().trace_id


def test_spans_carry_ids_only(tmp_path: Path) -> None:
    with captured_telemetry() as captured:
        _, spans = execute_diamond(tmp_path, captured)
    for span in by_name(spans, "agent.run") + by_name(spans, "tool.call") + by_name(spans, "orchestrator.run"):
        assert set(span.attributes or {}) <= {"org_id", "run_id", "task_id", "agent.name", "tool.name"}
