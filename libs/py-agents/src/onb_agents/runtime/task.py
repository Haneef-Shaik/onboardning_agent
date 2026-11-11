"""One agent task, its per-task hub token, and its out-of-band tool context (PRD §7.4, §7.10).

The orchestrator issues one short-lived agent token per task (`issue_task_token`). Its `scopes` are
the manifest's hub tools for this task (`mongo__find` …) plus `approved:<rule>` for each
`requires_approval` rule a human has approved for this task, and nothing else (ADR 0006).

The PII grant (PII-4) and the agent token are tool context: they travel only in the HTTP headers of
the agent's hub connection (`hub_headers`), never in a message, the graph state or the trajectory, so
the model cannot read, copy or widen them. `AgentTask` keeps the grant out of its repr.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from onb_agents.registry import AgentManifest
from onb_agents.tokens import AgentClaims, issue_agent_token

__all__ = [
    "APPROVED_PREFIX",
    "GRANT_HEADER",
    "AgentTask",
    "hub_headers",
    "hub_tool_name",
    "issue_task_token",
    "task_scopes",
]

GRANT_HEADER: Final = "X-Onb-Pii-Grant"
APPROVED_PREFIX: Final = "approved:"
HUB_SEPARATOR: Final = "__"
_ID: Final = re.compile(r"[A-Za-z0-9_-]{1,128}")
_MODE: Final = re.compile(r"[a-z][a-z0-9_]{0,31}")


@dataclass(frozen=True, slots=True)
class AgentTask:
    """What one task asks of an agent. `input` is untrusted data: it reaches the model delimited."""

    org_id: str
    run_id: str
    task_id: str
    input: Mapping[str, Any]
    mode: str | None = None
    approvals: tuple[str, ...] = ()
    grant: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        for name in ("org_id", "run_id", "task_id"):
            if not _ID.fullmatch(getattr(self, name) or ""):
                raise ValueError(f"{name} must match {_ID.pattern}")
        if self.mode is not None and not _MODE.fullmatch(self.mode):
            raise ValueError("mode must be a lowercase identifier")
        if not isinstance(self.approvals, tuple):
            raise ValueError("approvals must be a tuple of approved rules")


def hub_tool_name(server: str, tool: str) -> str:
    """The hub's name for a downstream tool (ADR 0006): `<server>__<tool>`."""
    return f"{server}{HUB_SEPARATOR}{tool}"


def task_scopes(manifest: AgentManifest, approvals: tuple[str, ...]) -> tuple[str, ...]:
    """The token scopes for one task: the manifest's hub tools, then the rules a human approved.

    An approval for a rule the manifest does not require is refused: a scope never widens a manifest.
    """
    unknown = sorted(set(approvals) - set(manifest.requires_approval))
    if unknown:
        raise ValueError(f"{', '.join(unknown)} is not a rule {manifest.name} requires approval for")
    tools = [hub_tool_name(server, tool) for server, grant in manifest.mcp.items() for tool in grant.tools]
    approved = [f"{APPROVED_PREFIX}{rule}" for rule in manifest.requires_approval if rule in approvals]
    return (*tools, *approved)


def issue_task_token(manifest: AgentManifest, task: AgentTask, key: str) -> str:
    """A fresh agent token for this task (own `jti`, ≤ 15 min), scoped by `task_scopes`."""
    claims = AgentClaims(
        agent=manifest.name,
        run_id=task.run_id,
        org_id=task.org_id,
        scopes=task_scopes(manifest, task.approvals),
    )
    return issue_agent_token(claims, key)


def hub_headers(token: str, grant: str | None) -> dict[str, str]:
    """The headers of the agent's hub connection: its token, and the task's grant when it has one."""
    headers = {"Authorization": f"Bearer {token}"}
    if grant:
        headers[GRANT_HEADER] = grant
    return headers
