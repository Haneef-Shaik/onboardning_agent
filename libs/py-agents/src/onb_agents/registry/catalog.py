"""The tool catalog: which agent carries out each template action (PRD §7.3, §9 `tool_catalog`).

`config/tool_catalog.yaml` lists `actions: [{action, agent, task_type?}]`. It is the same list as
the eval fixture's `action_catalog` (a test keeps them equal), so templates, evals and the
registry agree on the actions. Every action names an agent that has a manifest.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from .loader import read_yaml
from .manifest import AgentManifest, ManifestError, names_a_forbidden_collection

__all__ = ["CatalogEntry", "ToolCatalog", "load_catalog", "parse_catalog"]

_FROZEN = ConfigDict(frozen=True, extra="forbid")
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class CatalogEntry(BaseModel):
    model_config = _FROZEN

    action: Identifier
    agent: Identifier
    task_type: Identifier | None = None


class ToolCatalog(BaseModel):
    model_config = _FROZEN

    actions: tuple[CatalogEntry, ...] = Field(min_length=1)

    def entries(self) -> list[dict[str, Any]]:
        """The actions as plain data, without unset fields (the fixture's `action_catalog` shape)."""
        return [entry.model_dump(mode="json", exclude_none=True) for entry in self.actions]

    def sha256(self) -> str:
        canonical = json.dumps(self.entries(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def agent_for(self, action: str) -> str | None:
        return next((entry.agent for entry in self.actions if entry.action == action), None)


def parse_catalog(data: object, manifests: Iterable[AgentManifest]) -> ToolCatalog:
    """Validate the catalog and check every action names a known agent, or raise `ManifestError`."""
    if not isinstance(data, Mapping):
        raise ManifestError("the tool catalog must be a mapping with `actions`")
    if any(names_a_forbidden_collection(str(value)) for value in _values(data)):
        raise ManifestError("the tool catalog may not name a vault collection")
    try:
        catalog = ToolCatalog.model_validate(dict(data))
    except ValidationError as error:
        first = error.errors(include_url=False, include_input=False)[0]
        where = ".".join(str(p) for p in first["loc"])
        raise ManifestError(f"tool catalog {where}: {first['msg']}") from None
    actions = [entry.action for entry in catalog.actions]
    repeated = sorted({a for a in actions if actions.count(a) > 1})
    if repeated:
        raise ManifestError(f"tool catalog repeats {', '.join(repeated)}")
    known = {manifest.name for manifest in manifests}
    unknown = sorted({entry.agent for entry in catalog.actions} - known)
    if unknown:
        raise ManifestError(f"tool catalog names agents without a manifest: {', '.join(unknown)}")
    return catalog


def _values(data: Any) -> Iterable[Any]:
    if isinstance(data, Mapping):
        for item in data.values():
            yield from _values(item)
    elif isinstance(data, list | tuple):
        for item in data:
            yield from _values(item)
    else:
        yield data


def load_catalog(path: Path, manifests: Iterable[AgentManifest]) -> ToolCatalog:
    try:
        return parse_catalog(read_yaml(path), manifests)
    except ManifestError as error:
        message = str(error)
        raise ManifestError(message if message.startswith(path.name) else f"{path.name}: {message}") from None
