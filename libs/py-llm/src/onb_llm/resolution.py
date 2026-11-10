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

    def task_record(self) -> dict[str, str | None]:
        """What to store on the task: the model and where it came from. Params stay out of the store,
        because llm.yaml may route provider settings through them."""
        return {"connector": self.connector, "model": self.model, "alias": self.alias, "source": self.source}


# The only params an agent manifest or an org override may set. Provider wiring (guardrail, region,
# endpoint, credentials, fake scripts) comes only from llm.yaml, so a tenant's override cannot switch
# Bedrock Guardrails off or send prompts to another host.
SELECTION_PARAMS = frozenset({"temperature", "max_tokens", "top_p", "stop"})


def _check_selection_params(selection: LLMSelection, source: Source) -> None:
    for key in sorted(set(selection.params) - SELECTION_PARAMS):
        allowed = ", ".join(sorted(SELECTION_PARAMS))
        raise LLMConfigError(
            f"{source} sets param '{key}'; a manifest or org override may only set {allowed} "
            "(provider settings belong in llm.yaml)"
        )


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
    if source != "default":
        _check_selection_params(selection, source)
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
