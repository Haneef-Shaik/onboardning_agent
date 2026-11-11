"""Where the lifecycle gets its FR-T3 validator: the stored agent registry, read on every call.

The registry changes when agents are synced or retired, so each save and each publish builds the
validator from the registry as it is then (`TemplateValidator.from_registry`: retired agents are not
in it). The tool catalog and the field → sink policy come from `config/` and are read once.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from onb_agents.registry import ToolCatalog, load_catalog, load_manifests
from onb_agents.templates import TemplateValidator
from onb_crypto import SinkPolicy, load_sink_policy

from onb_api.agents.reader import AgentRegistryReader

__all__ = ["RegistryValidatorSource", "ValidatorSource"]


class ValidatorSource(Protocol):
    async def current(self) -> TemplateValidator:
        """A validator for the registry as it is now."""


class RegistryValidatorSource:
    __slots__ = ("_catalog", "_policy", "_registry")

    def __init__(self, registry: AgentRegistryReader, catalog: ToolCatalog, sink_policy: SinkPolicy) -> None:
        self._registry = registry
        self._catalog = catalog
        self._policy = sink_policy

    @classmethod
    def from_config(cls, registry: AgentRegistryReader, config_dir: Path) -> RegistryValidatorSource:
        """The catalog and sink policy from `config_dir` (`tool_catalog.yaml`, `pii_sink_policy.yaml`)."""
        catalog = load_catalog(config_dir / "tool_catalog.yaml", load_manifests(config_dir / "agents"))
        return cls(registry, catalog, load_sink_policy(config_dir / "pii_sink_policy.yaml"))

    async def current(self) -> TemplateValidator:
        return TemplateValidator.from_registry(await self._registry.list_agents(), self._catalog, self._policy)
