"""A minimal MCP client for the suite: like the hub (HUB-1), it signs one call context per tool call,
naming that tool and the caller's org (MCP-1 call-context contract)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult
from onb_mcp_mongo.call_context import AUDIENCE, HEADER, CallContext, sign_call_context

# The collections an agent of the suite may read and write through mcp-mongo.
READ = frozenset({"new_hires", "onboarding_runs", "run_tasks", "approvals"})
WRITE = frozenset({"run_tasks", "approvals"})


@dataclass(frozen=True)
class Outcome:
    """A tool call as the agent sees it: an error flag and the text or structured result."""

    error: bool
    data: dict[str, Any] | None
    text: str

    def ok(self) -> dict[str, Any]:
        assert not self.error, self.text
        assert self.data is not None
        return self.data


def _context(org_id: str, tool: str) -> CallContext:
    return CallContext(org_id=org_id, agent="xt_agent", run_id=f"run_xt_{org_id}", tool=tool, read=READ, write=WRITE)


def _outcome(result: CallToolResult) -> Outcome:
    text = " ".join(getattr(block, "text", "") for block in result.content)
    return Outcome(bool(result.isError), result.structuredContent, text)


def call(url: str, key: str, org_id: str, tool: str, args: dict[str, Any], *, audience: str = AUDIENCE) -> Outcome:
    async def sign(request: httpx.Request) -> None:
        body = json.loads(request.content or b"{}")
        if isinstance(body, dict) and body.get("method") == "tools/call":
            context = _context(org_id, body["params"]["name"])
            request.headers[HEADER] = sign_call_context(context, key, audience=audience)

    async def main() -> Outcome:
        async with (
            httpx.AsyncClient(event_hooks={"request": [sign]}, timeout=30) as http,
            streamable_http_client(url, http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            return _outcome(await session.call_tool(tool, args))

    return asyncio.run(main())
