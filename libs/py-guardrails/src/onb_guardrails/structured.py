"""Structured-output validation and the single repair prompt (PRD §8.1 Output).

The reply text is parsed as JSON (one surrounding Markdown code fence is allowed) and validated with
the Pydantic schema. Error summaries come from `ValidationError.errors(include_input=False)`: field
location, error type and Pydantic's message, never the model's text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, ValidationError

_FENCE: Final = re.compile(r"^\s*```(?:json)?\s*\n(?P<body>.*?)\n\s*```\s*$", re.DOTALL | re.IGNORECASE)
_MAX_LISTED_ERRORS: Final = 10


@dataclass(frozen=True, slots=True)
class SchemaErrors:
    """What was wrong with one reply: `(location, type, message)` per error."""

    errors: tuple[tuple[str, str, str], ...]

    @property
    def first_type(self) -> str:
        return self.errors[0][1]

    def summary(self) -> str:
        lines = [f"- {loc or '(root)'}: {msg} ({kind})" for loc, kind, msg in self.errors[:_MAX_LISTED_ERRORS]]
        if len(self.errors) > _MAX_LISTED_ERRORS:
            lines.append(f"- ... and {len(self.errors) - _MAX_LISTED_ERRORS} more")
        return "\n".join(lines)


def parse_structured[M: BaseModel](text: str, schema: type[M]) -> M | SchemaErrors:
    """The validated model, or the errors that stopped it."""
    match = _FENCE.match(text)
    body = match.group("body") if match else text
    try:
        return schema.model_validate_json(body)
    except ValidationError as error:
        details = error.errors(include_url=False, include_input=False, include_context=False)
        return SchemaErrors(
            tuple((".".join(str(part) for part in item["loc"]), item["type"], item["msg"]) for item in details)
        )


def repair_prompt(schema: type[BaseModel], errors: SchemaErrors) -> str:
    return (
        f"Your previous reply did not match the required schema {schema.__name__}.\n"
        f"Errors:\n{errors.summary()}\n"
        "Reply again with only one JSON object that matches this JSON schema, and nothing else:\n"
        f"{json.dumps(schema.model_json_schema(), separators=(',', ':'))}"
    )
