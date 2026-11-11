"""The agent's trajectory: one step per model turn and per tool call, in order (PRD §8.2 Trajectory).

A tool step records the call's name, its arguments (as the model sent them, after GR-1's output
redaction), and its outcome: `allowed`, `denied` (the hub's or a local tool's refusal, with the hub's
reason code) or `error`. Attempts the hub denied are steps too ("attempts denied by the hub still
count as calls"). Steps never hold the agent token, the grant or tool results.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from langchain_core.messages import ToolMessage

__all__ = ["StepKind", "ToolOutcome", "TrajectoryStep", "capped_step", "llm_step", "safe_name", "tool_step"]

StepKind = Literal["llm", "tool"]
ToolOutcome = Literal["allowed", "denied", "error"]
# The hub answers a refused call with `denied (<reason>): <fixed text>` (HUB-1, ADR 0006).
_DENIAL: Final = re.compile(r"^denied \(([a-z][a-z0-9_]{0,63})\)")
_NAME: Final = re.compile(r"[A-Za-z0-9_-]{1,128}")
INVALID_NAME: Final = "<invalid>"
TOO_MANY_TOOL_CALLS: Final = "too_many_tool_calls"


@dataclass(frozen=True, slots=True)
class TrajectoryStep:
    kind: StepKind
    name: str | None = None
    args: Mapping[str, Any] = field(default_factory=dict)
    outcome: ToolOutcome | None = None
    reason: str | None = None
    tool_calls: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        if self.kind == "llm":
            return {"kind": "llm", "tool_calls": list(self.tool_calls)}
        return {
            "kind": "tool",
            "name": self.name,
            "args": dict(self.args),
            "outcome": self.outcome,
            "reason": self.reason,
        }


def safe_name(name: str) -> str:
    """A tool name as recorded: the model chose it, so anything outside a plain identifier is `<invalid>`."""
    return name if _NAME.fullmatch(name) else INVALID_NAME


def llm_step(tool_calls: tuple[str, ...]) -> TrajectoryStep:
    return TrajectoryStep(kind="llm", tool_calls=tuple(safe_name(n) for n in tool_calls))


def _step(name: str, args: Mapping[str, Any], outcome: ToolOutcome, reason: str | None = None) -> TrajectoryStep:
    return TrajectoryStep(kind="tool", name=safe_name(name), args=dict(args), outcome=outcome, reason=reason)


def tool_step(name: str, args: Mapping[str, Any], message: ToolMessage) -> TrajectoryStep:
    """The step for one executed tool call, classified from the tool message it produced."""
    if message.status != "error":
        return _step(name, args, "allowed")
    denial = _DENIAL.match(message.text.strip())
    if denial is not None:
        return _step(name, args, "denied", denial.group(1))
    return _step(name, args, "error")


def capped_step(name: str, args: Mapping[str, Any]) -> TrajectoryStep:
    """A call past the per-turn cap: answered with an error, never run."""
    return _step(name, args, "error", TOO_MANY_TOOL_CALLS)
