"""The tools the hub serves: one hub tool per downstream tool, named `<server>__<tool>` (PRD §7.4).

The name uses `__` because agent-side model APIs allow only `[A-Za-z0-9_-]` in tool names. Each
entry carries the downstream tool's JSON-schema contract, which the hub checks before forwarding.
The entries come from each server's own contracts (mcp-mongo's, mcp-mocks'), so the hub and a
server can never disagree about a schema.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from onb_mcp_mocks.contracts import CONTRACTS as MOCK_CONTRACTS
from onb_mcp_mongo.contracts import CONTRACTS

__all__ = [
    "MOCKS_SERVER",
    "MONGO_SERVER",
    "SEPARATOR",
    "HubTool",
    "hub_name",
    "mocks_catalog",
    "mongo_catalog",
    "platform_catalog",
]

MONGO_SERVER = "mongo"
MOCKS_SERVER = "mocks"
SEPARATOR = "__"


def hub_name(server: str, tool: str) -> str:
    return f"{server}{SEPARATOR}{tool}"


@dataclass(frozen=True, slots=True)
class HubTool:
    server: str
    tool: str
    name: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any] | None
    read_only: bool

    @property
    def audit_name(self) -> str:
        """`server.tool`, as audit targets name it (`tool:mongo.find/run_tasks`)."""
        return f"{self.server}.{self.tool}"


def mongo_catalog() -> dict[str, HubTool]:
    """The six mcp-mongo tools, with the schemas mcp-mongo serves."""
    tools = {}
    for name, contract in CONTRACTS.items():
        entry = HubTool(
            server=MONGO_SERVER,
            tool=name,
            name=hub_name(MONGO_SERVER, name),
            description=contract.description,
            input_schema=contract.args.model_json_schema(by_alias=True),
            output_schema=contract.result.model_json_schema(by_alias=True),
            read_only=contract.read_only,
        )
        tools[entry.name] = entry
    return tools


def mocks_catalog() -> dict[str, HubTool]:
    """The mcp-mocks tools (MOCK-1), with the schemas mcp-mocks serves."""
    return {
        hub_name(MOCKS_SERVER, name): HubTool(
            server=MOCKS_SERVER,
            tool=name,
            name=hub_name(MOCKS_SERVER, name),
            description=contract.description,
            input_schema=contract.args.model_json_schema(by_alias=True),
            output_schema=contract.result.model_json_schema(by_alias=True),
            read_only=contract.read_only,
        )
        for name, contract in MOCK_CONTRACTS.items()
    }


def platform_catalog() -> dict[str, HubTool]:
    """Every tool the deployed hub serves."""
    return {**mongo_catalog(), **mocks_catalog()}
