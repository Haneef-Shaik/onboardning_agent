"""Forwarding to the downstream MCP servers, with the credentials only the hub holds (PRD §5.2, §7.4).

The credential is the call context: an HS256 JWS for this one call (tool, org, agent, run,
collections), sent in `X-Onb-Call-Context`. Each server has its own key and its own audience
(`mcp-<server>`: `mcp-mongo`, `mcp-mocks`), so a context minted for one server is refused by every
other, and one server's key cannot mint another's contexts. The agent's own token never leaves the
hub. Each call opens its own stateless MCP session, so no context is ever reused for another call.

The orchestrator's PII grant (AG-1 sends it as `X-Onb-Pii-Grant` on the agent's hub connection) is
forwarded to the servers in `GRANT_SERVERS` only, the notify sink (NOTIF-1). mcp-mongo and mcp-mocks
never see it, whatever the agent sends.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult
from onb_mcp_mongo.call_context import HEADER, CallContext, check_key, sign_call_context

from .catalog import MONGO_SERVER, NOTIFY_SERVER, HubTool

__all__ = [
    "DEFAULT_TIMEOUT_S",
    "GRANT_HEADER",
    "GRANT_SERVERS",
    "ClientFactory",
    "McpDownstream",
    "UnknownServerError",
    "audience_for",
]

DEFAULT_TIMEOUT_S = 30.0
GRANT_HEADER = "x-onb-pii-grant"  # AG-1's `onb_agents.runtime.task.GRANT_HEADER`
GRANT_SERVERS = frozenset({NOTIFY_SERVER})  # the sinks: the only servers a grant is forwarded to
ClientFactory = Callable[[dict[str, str]], httpx.AsyncClient]


class UnknownServerError(LookupError):
    """The hub has no URL for the tool's server."""


def _default_client(headers: dict[str, str]) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers=headers, timeout=DEFAULT_TIMEOUT_S, follow_redirects=False)


def audience_for(server: str) -> str:
    """The audience a server's contexts are signed for: `mcp-mongo`, `mcp-mocks`."""
    return f"mcp-{server}"


class McpDownstream:
    """`urls`: server → endpoint. `context_key` signs mcp-mongo's contexts only; `keys`: server → that
    server's own key (MOCK-1: `mocks`). Fails closed: every other server with a URL needs its own key,
    and no two servers share one."""

    def __init__(
        self,
        urls: Mapping[str, str],
        context_key: str,
        *,
        keys: Mapping[str, str] | None = None,
        client: ClientFactory = _default_client,
    ) -> None:
        own = {**dict(keys or {}), MONGO_SERVER: context_key}
        for key in own.values():
            check_key(key)
        missing = sorted(server for server in urls if server not in own)
        if missing:
            raise ValueError(f"no call-context key for the {', '.join(missing)} server(s)")
        if len(set(own.values())) != len(own):
            raise ValueError("each downstream server's call-context key must differ")
        self._urls = dict(urls)
        self._keys = own
        self._client = client

    @property
    def secrets(self) -> tuple[str, ...]:
        """Credentials this client holds: never allowed back to an agent."""
        return tuple(self._keys.values())

    async def call(
        self, tool: HubTool, args: Mapping[str, Any], context: CallContext, *, grant: str | None = None
    ) -> tuple[CallToolResult, tuple[str, ...]]:
        """Forward one call. `grant` goes along only to a server in `GRANT_SERVERS`."""
        url = self._urls.get(tool.server)
        if url is None:
            raise UnknownServerError(f"no URL configured for the {tool.server} server")
        key = self._keys[tool.server]  # present for every server with a URL (checked at construction)
        signed = sign_call_context(context, key, audience=audience_for(tool.server))
        headers = {HEADER: signed}
        if grant and tool.server in GRANT_SERVERS:
            headers[GRANT_HEADER] = grant
        async with (
            self._client(headers) as http,
            streamable_http_client(url, http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool(tool.tool, dict(args))
        return result, (signed,)
