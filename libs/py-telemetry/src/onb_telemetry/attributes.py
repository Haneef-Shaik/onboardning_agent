"""Span attribute keys and helpers for the run-level fields named in PRD §7.7.

Values are identifiers, never personal data: pass `ptk_` tokens or ids, not names or emails.
"""

from __future__ import annotations

from opentelemetry import trace

ORG_ID = "org_id"
RUN_ID = "run_id"
TASK_ID = "task_id"
AGENT_NAME = "agent.name"
TOOL_NAME = "tool.name"


def span_attributes(
    *,
    org_id: str | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
    agent_name: str | None = None,
    tool_name: str | None = None,
) -> dict[str, str]:
    """The attributes for the given fields, leaving out the ones not given.

    Usable at span start: `tracer.start_as_current_span(name, attributes=span_attributes(...))`.
    """
    given = {ORG_ID: org_id, RUN_ID: run_id, TASK_ID: task_id, AGENT_NAME: agent_name, TOOL_NAME: tool_name}
    result: dict[str, str] = {}
    for key, value in given.items():
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"span attribute {key} must be a non-blank string, got {value!r}")
        result[key] = value
    return result


def set_span_attributes(
    span: trace.Span | None = None,
    *,
    org_id: str | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
    agent_name: str | None = None,
    tool_name: str | None = None,
) -> None:
    """Set the given fields on `span`, or on the current span when none is passed."""
    target = span if span is not None else trace.get_current_span()
    target.set_attributes(
        span_attributes(org_id=org_id, run_id=run_id, task_id=task_id, agent_name=agent_name, tool_name=tool_name)
    )
