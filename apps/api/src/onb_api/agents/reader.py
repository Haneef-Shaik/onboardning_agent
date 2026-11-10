"""What the API needs from the agent registry: read every agent, or one by name.

`onb_agents.registry.AgentRegistry` satisfies it, in the service and (over an in-memory store) in tests.
"""

from __future__ import annotations

from typing import Protocol

from onb_agents.registry import RegisteredAgent

__all__ = ["AgentRegistryReader"]


class AgentRegistryReader(Protocol):
    async def list_agents(self) -> list[RegisteredAgent]:
        """Every registered agent, retired ones included, by name."""

    async def get_agent(self, name: str) -> RegisteredAgent | None:
        """The agent with this name, or None."""
