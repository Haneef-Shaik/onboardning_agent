"""Connector discovery: every package in the `onboarding.llm_connectors` entry-point group.

A new connector is a new package with an entry point, not a change here (PRD §5.6, §7.1). Broken or
clashing packages fail discovery loudly instead of being skipped.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from importlib import metadata
from types import MappingProxyType

from onb_llm.connector import LLMConnector
from onb_llm.errors import ConnectorNotFoundError, ConnectorRegistryError

ENTRY_POINT_GROUP = "onboarding.llm_connectors"


class ConnectorRegistry:
    """An immutable name → connector map."""

    def __init__(self, connectors: Mapping[str, LLMConnector]) -> None:
        self._connectors = MappingProxyType(dict(connectors))

    @classmethod
    def of(cls, *connectors: LLMConnector) -> ConnectorRegistry:
        by_name: dict[str, LLMConnector] = {}
        for connector in connectors:
            if connector.name in by_name:
                raise ConnectorRegistryError(f"connector '{connector.name}' is registered more than once")
            by_name[connector.name] = connector
        return cls(by_name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._connectors))

    def get(self, name: str) -> LLMConnector:
        connector = self._connectors.get(name)
        if connector is None:
            installed = ", ".join(self.names()) or "none"
            raise ConnectorNotFoundError(f"no LLM connector named '{name}' is installed; installed: {installed}")
        return connector


def discover_connectors(entry_points: Iterable[metadata.EntryPoint] | None = None) -> ConnectorRegistry:
    """Load every connector registered under `ENTRY_POINT_GROUP` (or the given entry points)."""
    found = metadata.entry_points(group=ENTRY_POINT_GROUP) if entry_points is None else entry_points
    connectors: dict[str, LLMConnector] = {}
    for entry_point in found:
        if entry_point.name in connectors:
            raise ConnectorRegistryError(
                f"LLM connector '{entry_point.name}' is registered more than once in {ENTRY_POINT_GROUP}"
            )
        connectors[entry_point.name] = _load(entry_point)
    return ConnectorRegistry(connectors)


def _load(entry_point: metadata.EntryPoint) -> LLMConnector:
    try:
        factory = entry_point.load()
        connector = factory()
    except Exception as error:
        raise ConnectorRegistryError(
            f"LLM connector '{entry_point.name}' ({entry_point.value}) failed to load: {type(error).__name__}: {error}"
        ) from error
    if not isinstance(connector, LLMConnector):
        raise ConnectorRegistryError(
            f"LLM connector '{entry_point.name}' ({entry_point.value}) does not implement LLMConnector"
        )
    if connector.name != entry_point.name:
        raise ConnectorRegistryError(
            f"entry point '{entry_point.name}' loads a connector named '{connector.name}'; the names must match"
        )
    return connector
