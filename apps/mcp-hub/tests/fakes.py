"""Test doubles for the gateway: a scripted downstream and a registry lookup."""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from mcp.types import CallToolResult, TextContent
from onb_agents.registry import ManifestError, RegisteredAgent
from onb_mcp_hub.catalog import HubTool
from onb_mcp_mongo.call_context import CallContext

TOKEN_KEY = secrets.token_urlsafe(40)
CONTEXT_KEY = secrets.token_urlsafe(40)


def ok(payload: Mapping[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))], structuredContent=dict(payload), isError=False
    )


@dataclass
class Sent:
    tool: HubTool
    args: Mapping[str, Any]
    context: CallContext


@dataclass
class FakeDownstream:
    """Answers with `reply(sent)`; records what the hub forwarded. Holds one credential of its own."""

    reply: Callable[[Sent], CallToolResult] = lambda sent: ok({"documents": [], "count": 0})
    credential: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    sent: list[Sent] = field(default_factory=list)
    fail: Exception | None = None

    @property
    def secrets(self) -> tuple[str, ...]:
        return (self.credential,)

    async def call(
        self, tool: HubTool, args: Mapping[str, Any], context: CallContext
    ) -> tuple[CallToolResult, tuple[str, ...]]:
        if self.fail is not None:
            raise self.fail
        sent = Sent(tool, dict(args), context)
        self.sent.append(sent)
        return self.reply(sent), (f"signed-context-for-{context.tool}-{self.credential}",)


@dataclass
class FakeRegistry:
    agents: dict[str, RegisteredAgent | ManifestError] = field(default_factory=dict)

    async def get_agent(self, name: str) -> RegisteredAgent | None:
        found = self.agents.get(name)
        if isinstance(found, ManifestError):
            raise found
        return found
