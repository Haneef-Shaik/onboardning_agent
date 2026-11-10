"""What one case run left behind (evals/README.md "How a case runs", step 4).

The runner fills a `CaseCapture` per repetition: the agent's structured output, every tool/MCP call
(denied ones included), the text of every LLM input, steps, tokens, latency, the MCP audit entries and
the case's spans. Checks read only this, the database state and the vault.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

__all__ = ["AuditEntry", "CaseCapture", "ToolCall", "ToolOutcome", "to_text"]

ScopeName = Literal["output", "tool_args", "llm_input", "trace"]


class ToolOutcome(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    args: Mapping[str, Any]
    outcome: ToolOutcome


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One MCP audit record: who called what, whether it was allowed, and which orgs' data came back."""

    org_id: str
    tool: str
    args: Mapping[str, Any]
    outcome: ToolOutcome
    result_org_ids: tuple[str, ...] = ()


def to_text(value: Any) -> str:
    """A stable serialisation for text scans: strings as-is, anything else as sorted JSON."""
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


@dataclass(frozen=True, slots=True)
class CaseCapture:
    output: Mapping[str, Any] | None
    tool_calls: tuple[ToolCall, ...] = ()
    llm_inputs: tuple[str, ...] = ()
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    audit: tuple[AuditEntry, ...] = ()
    trace: tuple[str, ...] = ()
    error: str | None = None

    def scope_texts(self, scope: ScopeName) -> tuple[str, ...]:
        """The texts a PII or token scan reads for one scope."""
        if scope == "output":
            return () if self.output is None else (to_text(self.output),)
        if scope == "tool_args":
            return tuple(to_text(call.args) for call in self.tool_calls)
        if scope == "llm_input":
            return self.llm_inputs
        return self.trace
