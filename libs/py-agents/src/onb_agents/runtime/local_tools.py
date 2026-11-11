"""Local tools (PRD §7.3 `tools:`): pure functions the runtime runs in process, never through the hub.

They touch no database, network or vault, so they need no hub policy; a manifest still has to list a
tool for the agent to get it. Errors are short and fixed: they never echo an argument back to the model.

- `date_calc(date, days, business_days=False)` → `{"date": "YYYY-MM-DD"}`
- `render(template, variables)` → `{"text": ...}`: `{{ name }}` placeholders only, each one declared,
  no expressions, attribute access or filters, and substituted values are never rendered again.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date as Date  # noqa: N812 - the argument is called `date`
from datetime import timedelta
from typing import Annotated, Any, Final

from langchain_core.tools import BaseTool, StructuredTool, ToolException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, ValidationError

from onb_agents.registry import AgentManifest

__all__ = ["LOCAL_TOOLS", "date_calc", "local_tools_for", "render"]

MAX_DAYS: Final = 3650
MAX_TEMPLATE_CHARS: Final = 8000
MAX_VARIABLES: Final = 64
_ISO_DATE: Final = re.compile(r"\d{4}-\d{2}-\d{2}")
_PLACEHOLDER: Final = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_NAME: Final = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]{0,63})\s*")
_WEEKEND: Final = frozenset({5, 6})

Value = StrictStr | StrictInt | StrictFloat | StrictBool


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class DateCalcArgs(_Args):
    date: Annotated[StrictStr, Field(description="An ISO date, YYYY-MM-DD.")]
    days: Annotated[StrictInt, Field(ge=-MAX_DAYS, le=MAX_DAYS, description="Days to add; negative goes back.")]
    business_days: Annotated[StrictBool, Field(description="Count Monday to Friday only.")] = False


class RenderArgs(_Args):
    template: Annotated[StrictStr, Field(min_length=1, max_length=MAX_TEMPLATE_CHARS)]
    variables: Annotated[dict[StrictStr, Value], Field(max_length=MAX_VARIABLES)] = Field(default_factory=dict)


def _parse_date(text: str) -> Date:
    if not _ISO_DATE.fullmatch(text):
        raise ValueError("date must be YYYY-MM-DD")
    try:
        return Date.fromisoformat(text)
    except ValueError:
        raise ValueError("date is not a calendar date") from None


def _add_business_days(start: Date, days: int) -> Date:
    step = 1 if days >= 0 else -1
    current, left = start, abs(days)
    while left:
        current += timedelta(days=step)
        if current.weekday() not in _WEEKEND:
            left -= 1
    return current


def date_calc(**kwargs: Any) -> dict[str, str]:
    args = DateCalcArgs.model_validate(kwargs)
    start = _parse_date(args.date)
    result = _add_business_days(start, args.days) if args.business_days else start + timedelta(days=args.days)
    return {"date": result.isoformat()}


def _substitute(variables: Mapping[str, Any], missing: list[str]) -> Any:
    def replace(match: re.Match[str]) -> str:
        name = _NAME.fullmatch(match.group(1))
        if name is None:
            raise ValueError("a placeholder must be a plain variable name: {{ name }}")
        key = name.group(1)
        if key not in variables:
            missing.append(key)
            return ""
        return str(variables[key])

    return replace


def render(**kwargs: Any) -> dict[str, str]:
    args = RenderArgs.model_validate(kwargs)
    missing: list[str] = []
    text = _PLACEHOLDER.sub(_substitute(args.variables, missing), args.template)  # one pass: values stay literal
    if missing:
        raise ValueError(f"missing variables: {', '.join(sorted(set(missing)))}")
    return {"text": text}


def _tool(name: str, description: str, schema: type[_Args], fn: Any) -> StructuredTool:
    def run(**kwargs: Any) -> str:
        try:
            return json.dumps(fn(**kwargs), ensure_ascii=False)
        except ValidationError as error:
            fields = sorted({".".join(str(p) for p in e["loc"]) or "(root)" for e in error.errors()})
            raise ToolException(f"{name}: invalid arguments ({', '.join(fields)})") from None
        except ValueError as error:
            raise ToolException(f"{name}: {error}") from None

    return StructuredTool.from_function(
        func=run, name=name, description=description, args_schema=schema, handle_tool_error=True, infer_schema=False
    )


LOCAL_TOOLS: Final[dict[str, BaseTool]] = {
    "date_calc": _tool(
        "date_calc", "Add calendar or business days to an ISO date. Returns {date}.", DateCalcArgs, date_calc
    ),
    "render": _tool(
        "render", "Fill {{ name }} placeholders of a message template. Returns {text}.", RenderArgs, render
    ),
}


def local_tools_for(manifest: AgentManifest) -> list[BaseTool]:
    """The local tools `manifest` lists, in its order. An unknown name is refused, never skipped."""
    unknown = sorted(set(manifest.tools) - set(LOCAL_TOOLS))
    if unknown:
        raise LookupError(f"{manifest.name}: no local tool named {', '.join(unknown)}")
    return [LOCAL_TOOLS[name] for name in manifest.tools]
