"""Resolution order (PRD §7.1): agent manifest → org override → global default.

The first of those that names a model wins. Params layer from the connector's settings, then the
alias, then the winning selection. The result is recorded on the task, so a running task keeps the
model it started with when the configuration changes.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from onb_llm.config import LLMConfig, LLMSelection
from onb_llm.errors import LLMConfigError

Source = Literal["manifest", "org_override", "default"]


class ResolvedLLM(BaseModel):
    """A concrete connector and model, and where the choice came from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    connector: str
    model: str
    params: dict[str, Any]
    source: Source
    alias: str | None


def resolve_llm(
    config: LLMConfig,
    *,
    manifest: LLMSelection | None = None,
    org_override: LLMSelection | None = None,
) -> ResolvedLLM:
    """The model for one agent. `manifest` is the agent manifest's `llm:`; `org_override` is the org's choice."""
    source: Source
    if manifest is not None:
        selection, source = manifest, "manifest"
    elif org_override is not None:
        selection, source = org_override, "org_override"
    else:
        selection, source = config.default, "default"
    return _concrete(config, selection, source)


def _concrete(config: LLMConfig, selection: LLMSelection, source: Source) -> ResolvedLLM:
    alias_params: dict[str, Any] = {}
    connector, model = selection.connector, selection.model
    if selection.alias is not None:
        target = config.aliases.get(selection.alias)
        if target is None:
            known = ", ".join(sorted(config.aliases)) or "none"
            raise LLMConfigError(f"unknown model alias '{selection.alias}' (from {source}); known aliases: {known}")
        connector, model, alias_params = target.connector, target.model, target.params
    if connector is None or model is None:  # LLMSelection's validator rules this out
        raise LLMConfigError(f"selection from {source} names no connector and model")
    settings = config.connectors.get(connector)
    params = {**(settings.params if settings else {}), **alias_params, **selection.params}
    return ResolvedLLM(connector=connector, model=model, params=params, source=source, alias=selection.alias)
