"""The script format the `fake` connector replays: a JSON/YAML list of assistant turns.

```json
[{"content": "", "tool_calls": [{"name": "add_days", "args": {"days": 3}}],
  "usage": {"input_tokens": 20, "output_tokens": 5}},
 {"content": "Done."}]
```
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.messages.tool import ToolCall
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, TypeAdapter, ValidationError

from onb_llm.errors import LLMConfigError

_FROZEN = ConfigDict(frozen=True, extra="forbid")

# Sampling params an agent may pass to any model; the fake connector accepts and ignores them.
IGNORED_PARAMS = frozenset({"temperature", "max_tokens", "top_p", "stop"})
SCRIPT_PARAMS = frozenset({"script", "script_file"})


class ScriptedToolCall(BaseModel):
    model_config = _FROZEN

    name: StrictStr
    args: dict[str, Any] = Field(default_factory=dict)
    id: StrictStr | None = None


class ScriptedUsage(BaseModel):
    model_config = _FROZEN

    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)


class ScriptedTurn(BaseModel):
    model_config = _FROZEN

    content: StrictStr = ""
    tool_calls: tuple[ScriptedToolCall, ...] = ()
    usage: ScriptedUsage | None = None

    def to_message(self, turn: int) -> AIMessage:
        """The `AIMessage` for this turn; tool-call ids default to `call_<turn>_<n>`, so replays match."""
        calls = [
            ToolCall(name=call.name, args=dict(call.args), id=call.id or f"call_{turn}_{index}", type="tool_call")
            for index, call in enumerate(self.tool_calls)
        ]
        usage = None
        if self.usage is not None:
            usage = UsageMetadata(
                input_tokens=self.usage.input_tokens,
                output_tokens=self.usage.output_tokens,
                total_tokens=self.usage.input_tokens + self.usage.output_tokens,
            )
        return AIMessage(id=f"scripted-{turn}", content=self.content, tool_calls=calls, usage_metadata=usage)


_SCRIPT = TypeAdapter(tuple[ScriptedTurn, ...])
DEFAULT_SCRIPT = (ScriptedTurn(content="OK"),)


def script_from_params(params: Mapping[str, Any]) -> tuple[ScriptedTurn, ...] | None:
    """The script named by `script` (inline) or `script_file` (JSON path); `None` when neither is given."""
    unknown = set(params) - SCRIPT_PARAMS - IGNORED_PARAMS
    if unknown:
        raise LLMConfigError(f"fake connector: unknown params {sorted(unknown)}; it takes `script` or `script_file`")
    if "script" in params and "script_file" in params:
        raise LLMConfigError("fake connector: give `script` or `script_file`, not both")
    if "script_file" in params:
        return _parse(_read(Path(str(params["script_file"]))), source=str(params["script_file"]))
    if "script" in params:
        return _parse(params["script"], source="params.script")
    return None


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LLMConfigError(f"fake connector: cannot read script {path}: {type(error).__name__}") from error


def _parse(raw: Any, *, source: str) -> tuple[ScriptedTurn, ...]:
    if not isinstance(raw, list):
        raise LLMConfigError(f"fake connector: the script in {source} must be a list of turns")
    try:
        return _SCRIPT.validate_python(raw)
    except ValidationError as error:
        raise LLMConfigError(f"fake connector: bad script in {source}:\n{error}") from error
