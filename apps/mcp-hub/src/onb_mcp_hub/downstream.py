"""Forwarding to the downstream MCP servers, with the credentials only the hub holds (PRD §5.2, §7.4).

For mcp-mongo the credential is the call context: an HS256 JWS signed with `MCP_CALL_CONTEXT_KEY`
for this one call (tool, org, agent, run, collections), sent in `X-Onb-Call-Context`. The agent's
own token never leaves the hub. Each call opens its own stateless MCP session, so no context is
ever reused for another call.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult
from onb_mcp_mongo.call_context import HEADER, CallContext, check_key, sign_call_context

from .catalog import HubTool

__all__ = ["DEFAULT_TIMEOUT_S", "ClientFactory", "McpDownstream", "UnknownServerError"]

DEFAULT_TIMEOUT_S = 30.0
ClientFactory = Callable[[dict[str, str]], httpx.AsyncClient]


class UnknownServerError(LookupError):
    """The hub has no URL for the tool's server."""


def _default_client(headers: dict[str, str]) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers=headers, timeout=DEFAULT_TIMEOUT_S, follow_redirects=False)


class McpDownstream:
    def __init__(self, urls: Mapping[str, str], context_key: str, *, client: ClientFactory = _default_client) -> None:
        check_key(context_key)
        self._urls = dict(urls)
        self._key = context_key
        self._client = client

    @property
    def secrets(self) -> tuple[str, ...]:
        """Credentials this client holds: never allowed back to an agent."""
        return (self._key,)

    async def call(
        self, tool: HubTool, args: Mapping[str, Any], context: CallContext
    ) -> tuple[CallToolResult, tuple[str, ...]]:
        url = self._urls.get(tool.server)
        if url is None:
            raise UnknownServerError(f"no URL configured for the {tool.server} server")
        signed = sign_call_context(context, self._key)
        async with (
            self._client({HEADER: signed}) as http,
            streamable_http_client(url, http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool(tool.tool, dict(args))
        return result, (signed,)
