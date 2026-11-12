"""What runs a task. RUN-1 ships a stub; the registered agents arrive with RUN-3 (PRD §7.1 Sub-agents).

An executor gets the task's ids, agent and action, never PII or the hire's variables. It returns when
the task's work is done and raises when it failed; the runner owns every state change.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Protocol

__all__ = ["StubExecutor", "StubTaskError", "TaskExecutor", "TaskWork"]


@dataclass(frozen=True, slots=True)
class TaskWork:
    run_id: str
    task_id: str
    agent: str
    action: str
    type: str
    attempt: int
    idempotency_key: str = ""
    """`<run_id>:<task_id>`, the same for every attempt of the task. A task's side effect must be idempotent
    on it: an attempt killed after its effect but before the task was marked done is run again (a
    takeover) and must not repeat the effect. Tools reached through the MCP hub pass it on (RUN-3)."""


class TaskExecutor(Protocol):
    async def execute(self, work: TaskWork) -> None: ...


class StubTaskError(RuntimeError):
    """A stub task told to fail (tests)."""


@dataclass
class StubExecutor:
    """Does no work: waits `delay` seconds (or `delays[task_id]`) and succeeds, unless the task is in
    `fail` (always) or its first `fail_times[task_id]` attempts. Records the calls, in the order they
    started, for tests and the local worker."""

    delay: float = 0.0
    fail: Collection[str] = frozenset()
    fail_times: Mapping[str, int] = field(default_factory=dict)
    delays: Mapping[str, float] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    work: list[TaskWork] = field(default_factory=list)

    async def execute(self, work: TaskWork) -> None:
        self.calls.append(work.task_id)
        self.work.append(work)
        await asyncio.sleep(self.delays.get(work.task_id, self.delay))
        if self.calls.count(work.task_id) <= self.fail_times.get(work.task_id, 0):
            raise StubTaskError("the stub was told to fail this task for now")
        if work.task_id in self.fail:
            raise StubTaskError("the stub was told to fail this task")
