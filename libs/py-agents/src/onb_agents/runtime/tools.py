"""Where an agent's tools come from (PRD §7.1 Sub-agents, §5.2): the MCP hub, through langchain-mcp-adapters.

`HubToolSource` opens one MCP session to the hub per task, with the task's agent token and grant in
the HTTP headers only (`task.hub_headers`), loads the hub's tools with `load_mcp_tools`, and binds
only the ones the manifest names (`<server>__<tool>`). A call the model makes for any other name is
still sent to the hub (`ToolSet.forward`), so the hub decides and audits it; the runtime never denies
on its own, and the hub is the authority even when the runtime's manifest disagrees with the registry.

Other sources (an eval harness's stand-in) implement the same `ToolSource` protocol.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Final, Protocol

import httpx
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from onb_agents.registry import AgentManifest
from onb_agents.runtime.task import AgentTask, hub_headers, hub_tool_name

__all__ = ["ClientFactory", "ForwardResult", "HubToolSource", "ToolSet", "ToolSource"]

DEFAULT_TIMEOUT_S: Final = 30.0
ClientFactory = Callable[[Mapping[str, str]], httpx.AsyncClient]


@dataclass(frozen=True, slots=True)
class ForwardResult:
    text: str
    is_error: bool


@dataclass(frozen=True, slots=True)
class ToolSet:
    """The tools bound to the model, and how to send a call for any other name to the authority."""

    bound: tuple[BaseTool, ...]
    forward: Callable[[str, Mapping[str, Any]], Awaitable[ForwardResult]]


class ToolSource(Protocol):
    def open(self, manifest: AgentManifest, task: AgentTask, token: str) -> AbstractAsyncContextManager[ToolSet]: ...


def _default_client(headers: Mapping[str, str]) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers=dict(headers), timeout=DEFAULT_TIMEOUT_S, follow_redirects=False)


def manifest_hub_tools(manifest: AgentManifest) -> frozenset[str]:
    return frozenset(hub_tool_name(server, tool) for server, grant in manifest.mcp.items() for tool in grant.tools)


def _text(result: CallToolResult) -> str:
    return "".join(block.text for block in result.content if isinstance(block, TextContent))


class HubToolSource:
    """Tools from the MCP hub at `url` (`…/mcp`), over streamable HTTP."""

    def __init__(self, url: str, *, client: ClientFactory = _default_client) -> None:
        self._url = url
        self._client = client

    @asynccontextmanager
    async def _session(self, headers: Mapping[str, str]) -> AsyncIterator[ClientSession]:
        async with (
            self._client(headers) as http,
            streamable_http_client(self._url, http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session

    @asynccontextmanager
    async def open(self, manifest: AgentManifest, task: AgentTask, token: str) -> AsyncIterator[ToolSet]:
        allowed = manifest_hub_tools(manifest)
        async with self._session(hub_headers(token, task.grant)) as session:
            loaded = await load_mcp_tools(session)

            async def forward(name: str, args: Mapping[str, Any]) -> ForwardResult:
                result = await session.call_tool(name, dict(args))
                return ForwardResult(_text(result), bool(result.isError))

            yield ToolSet(bound=tuple(t for t in loaded if t.name in allowed), forward=forward)
