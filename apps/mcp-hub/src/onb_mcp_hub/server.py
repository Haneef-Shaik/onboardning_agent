"""The hub's MCP server: streamable HTTP at `/mcp`, the single entry point for agent tools (PRD §7.4).

Agents connect with `Authorization: Bearer <agent token>` (`onb_agents.tokens.hub_client_config`).
`tools/list` serves the catalog; every `tools/call` goes to `Gateway.call`, unknown names included,
so even a call for a tool the hub does not serve is audited and traced. Built on the MCP SDK's
low-level server, because the hub's tools are the downstream servers' tools, not Python functions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from .catalog import HubTool
from .gateway import Gateway

__all__ = ["SERVER_NAME", "Hub", "build_hub", "hub_tools"]

SERVER_NAME = "mcp-hub"
DEFAULT_ALLOWED_HOSTS = ("127.0.0.1", "localhost", "mcp-hub")


def hub_tools(catalog: Mapping[str, HubTool]) -> list[types.Tool]:
    """What `tools/list` serves: each hub tool with its downstream contract. Nothing about where it runs."""
    return [
        types.Tool(
            name=tool.name,
            description=tool.description,
            inputSchema=dict(tool.input_schema),
            outputSchema=dict(tool.output_schema) if tool.output_schema is not None else None,
            annotations=types.ToolAnnotations(readOnlyHint=tool.read_only, destructiveHint=False),
        )
        for tool in sorted(catalog.values(), key=lambda t: t.name)
    ]


class _Endpoint:
    """The `/mcp` ASGI endpoint (no trailing-slash redirect)."""

    def __init__(self, manager: StreamableHTTPSessionManager) -> None:
        self._manager = manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._manager.handle_request(scope, receive, send)


@dataclass(frozen=True)
class Hub:
    server: Server[Any, Any]
    manager: StreamableHTTPSessionManager
    app: Starlette


def _hosts(names: Sequence[str]) -> list[str]:
    return [pattern for name in names for pattern in (name, f"{name}:*")]


def build_hub(gateway: Gateway, *, allowed_hosts: Sequence[str] = DEFAULT_ALLOWED_HOSTS) -> Hub:
    """The hub over `gateway`. Only requests whose `Host` is in `allowed_hosts` are served."""
    server: Server[Any, Any] = Server(SERVER_NAME)
    listed = hub_tools(gateway.catalog)

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[types.Tool]:
        return listed

    @server.call_tool(validate_input=False)  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        request = server.request_context.request
        headers = getattr(request, "headers", None)
        authorization = headers.get("authorization") if headers is not None else None
        return await gateway.call(name, arguments or {}, authorization)

    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=_hosts(allowed_hosts), allowed_origins=[]
    )
    manager = StreamableHTTPSessionManager(app=server, stateless=True, json_response=True, security_settings=security)
    app = Starlette(routes=[Route("/mcp", endpoint=_Endpoint(manager))], lifespan=lambda _app: manager.run())
    return Hub(server=server, manager=manager, app=app)
