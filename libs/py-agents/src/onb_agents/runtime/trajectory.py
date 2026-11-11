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

__all__ = ["StepKind", "ToolOutcome", "TrajectoryStep", "llm_step", "tool_step"]

StepKind = Literal["llm", "tool"]
ToolOutcome = Literal["allowed", "denied", "error"]
# The hub answers a refused call with `denied (<reason>): <fixed text>` (HUB-1, ADR 0006).
_DENIAL: Final = re.compile(r"^denied \(([a-z][a-z0-9_]{0,63})\)")


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


def llm_step(tool_calls: tuple[str, ...]) -> TrajectoryStep:
    return TrajectoryStep(kind="llm", tool_calls=tool_calls)


def tool_step(name: str, args: Mapping[str, Any], message: ToolMessage) -> TrajectoryStep:
    """The step for one executed tool call, classified from the tool message it produced."""
    if message.status != "error":
        return TrajectoryStep(kind="tool", name=name, args=dict(args), outcome="allowed")
    denial = _DENIAL.match(message.text.strip())
    if denial is not None:
        return TrajectoryStep(kind="tool", name=name, args=dict(args), outcome="denied", reason=denial.group(1))
    return TrajectoryStep(kind="tool", name=name, args=dict(args), outcome="error")
