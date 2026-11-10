"""The model-selection configuration: `config/llm.yaml` with `${ENV}` interpolation (PRD §7.1).

```yaml
default: { alias: standard }
aliases:
  standard: { connector: "${LLM_CONNECTOR:-bedrock}", model: "${LLM_MODEL_STANDARD}" }
connectors:
  bedrock: { params: { guardrail_id: "${BEDROCK_GUARDRAIL_ID:-}" } }
```
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from onb_llm.errors import LLMConfigError
from onb_llm.interpolation import interpolate_tree

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class LLMSelection(BaseModel):
    """Which model to use: a named alias, or an explicit connector and model. Same shape as a manifest's `llm:`."""

    model_config = _FROZEN

    alias: str | None = None
    connector: str | None = None
    model: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("alias", "connector", "model")
    @classmethod
    def _not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def _alias_xor_connector_and_model(self) -> Self:
        if self.alias is not None:
            if self.connector is not None or self.model is not None:
                raise ValueError("give either `alias` or `connector` and `model`, not both")
        elif self.connector is None or self.model is None:
            raise ValueError("give `alias`, or both `connector` and `model`")
        return self


class ConnectorSettings(BaseModel):
    """Params every model of one connector gets (for example Bedrock's guardrail)."""

    model_config = _FROZEN

    params: dict[str, Any] = Field(default_factory=dict)


class LLMConfig(BaseModel):
    """`default` applies when neither the agent manifest nor the org names a model."""

    model_config = _FROZEN

    default: LLMSelection
    aliases: dict[str, LLMSelection] = Field(default_factory=dict)
    connectors: dict[str, ConnectorSettings] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _aliases_are_concrete_and_default_resolves(self) -> Self:
        for name, selection in self.aliases.items():
            if selection.alias is not None:
                raise ValueError(f"alias '{name}' must name a connector and model, not another alias")
        if self.default.alias is not None and self.default.alias not in self.aliases:
            raise ValueError(f"default names alias '{self.default.alias}', which is not under `aliases`")
        return self


def load_llm_config(path: Path, env: Mapping[str, str]) -> LLMConfig:
    """Read, interpolate and validate `path`. Every problem is an `LLMConfigError` naming the file."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise LLMConfigError(f"LLM config {path} not found") from error
    try:
        tree = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise LLMConfigError(f"LLM config {path} is not valid YAML: {error}") from error
    if not isinstance(tree, Mapping):
        raise LLMConfigError(f"LLM config {path} must be a mapping with a `default` key")
    try:
        return LLMConfig.model_validate(interpolate_tree(tree, env, where=""))
    except LLMConfigError as error:
        raise LLMConfigError(f"LLM config {path}: {error}") from error
    except ValidationError as error:
        raise LLMConfigError(f"LLM config {path} is invalid:\n{_describe(error)}") from None


def _describe(error: ValidationError) -> str:
    """Where and what, never the value: by now values are interpolated and may come from secrets."""
    return "\n".join(
        f"  {'.'.join(str(part) for part in item['loc']) or '(top level)'}: {item['msg']}"
        for item in error.errors(include_input=False, include_url=False)
    )
