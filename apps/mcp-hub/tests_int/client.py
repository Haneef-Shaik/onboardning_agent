"""The agent side of the integration tests: an MCP client to the running hub, plus read-backs of
the audit records and spans one call produced."""

from __future__ import annotations

import asyncio
import json
import secrets
import string
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent
from onb_agents.runtime.task import GRANT_HEADER
from onb_agents.tokens import AgentClaims, hub_client_config, issue_agent_token
from onb_data import AuditLog, connect, tenant_scope
from onb_data.audit import AuditEntry, AuditQuery
from onb_mcp_hub.gateway import SPAN_PREFIX
from opentelemetry.sdk.trace import ReadableSpan

from .conftest import DATABASE, Stack


def new_run() -> str:
    return f"run_{uuid.uuid4().hex[:12]}"


def agent_claims(agent: str, org: str, run_id: str, scopes: tuple[str, ...]) -> AgentClaims:
    return AgentClaims(agent=agent, run_id=run_id, org_id=org, scopes=scopes)


async def _session[T](
    stack: Stack, token: str | None, fn: Callable[[ClientSession], Awaitable[T]], grant: str | None = None
) -> T:
    headers = dict(hub_client_config(stack.hub_url, token)["headers"]) if token else {}
    if grant is not None:
        headers[GRANT_HEADER] = grant  # how the agent runtime sends the task's PII grant (AG-1)
    async with (
        httpx.AsyncClient(headers=headers, timeout=30) as http,
        streamable_http_client(stack.hub_url, http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        return await fn(session)


def call_with_token(
    stack: Stack, token: str | None, tool: str, args: dict[str, Any], grant: str | None = None
) -> CallToolResult:
    return asyncio.run(_session(stack, token, lambda s: s.call_tool(tool, args), grant))


def call(
    stack: Stack, claims: AgentClaims, tool: str, args: dict[str, Any], *, grant: str | None = None, **token_kw: Any
) -> CallToolResult:
    return call_with_token(stack, issue_agent_token(claims, stack.token_key, **token_kw), tool, args, grant)


def list_tools(stack: Stack, claims: AgentClaims) -> list[str]:
    token = issue_agent_token(claims, stack.token_key)
    listed = asyncio.run(_session(stack, token, lambda s: s.list_tools()))
    return [t.name for t in listed.tools]


def text(result: CallToolResult) -> str:
    return "".join(c.text for c in result.content if isinstance(c, TextContent))


def everything(result: CallToolResult) -> str:
    return text(result) + json.dumps(result.structuredContent)


def hub_spans(stack: Stack, run_id: str) -> list[ReadableSpan]:
    return [
        s
        for s in stack.telemetry.spans()
        if s.name.startswith(SPAN_PREFIX) and (s.attributes or {}).get("run_id") == run_id
    ]


@dataclass(frozen=True)
class Trail:
    span: ReadableSpan
    records: tuple[AuditEntry, ...]

    @property
    def attrs(self) -> dict[str, Any]:
        return dict(self.span.attributes or {})


SPAN_WAIT_SECONDS = 5.0


def new_marker() -> str:
    """A fresh 12-letter test marker. Letters only: an all-digit hex marker (about 1 in 280) reads
    as a number to the hub's PII guard and gets refused, which made these tests flaky."""
    return "".join(secrets.choice(string.ascii_lowercase) for _ in range(12))


def _one_hub_span(stack: Stack, run_id: str) -> ReadableSpan:
    """The hub can answer before its span ends and is exported; wait briefly for exactly one."""
    deadline = time.monotonic() + SPAN_WAIT_SECONDS
    spans = hub_spans(stack, run_id)
    while not spans and time.monotonic() < deadline:
        time.sleep(0.05)
        spans = hub_spans(stack, run_id)
    [span] = spans
    return span


def trail(stack: Stack, run_id: str, org: str) -> Trail:
    """The one hub span of `run_id`, and the audit records written under its trace in `org`."""
    span = _one_hub_span(stack, run_id)
    trace_id = format(span.context.trace_id, "032x")

    async def read() -> tuple[AuditEntry, ...]:
        store = connect(stack.mongo_uri, DATABASE, app_name="hub-int-reader")
        with tenant_scope(org):
            page = await AuditLog(store).list(AuditQuery(action="mcp.tool_call"), page=1, limit=100)
        return tuple(e for e in page.entries if e.trace_id == trace_id)

    return Trail(span, asyncio.run(read()))
