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


class TaskExecutor(Protocol):
    async def execute(self, work: TaskWork) -> None: ...


class StubTaskError(RuntimeError):
    """A stub task told to fail (tests)."""


@dataclass
class StubExecutor:
    """Does no work: waits `delay` seconds (or `delays[task_id]`) and succeeds, unless the task is in
    `fail`. Records the calls, in the order they started, for tests and the local worker."""

    delay: float = 0.0
    fail: Collection[str] = frozenset()
    delays: Mapping[str, float] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    work: list[TaskWork] = field(default_factory=list)

    async def execute(self, work: TaskWork) -> None:
        self.calls.append(work.task_id)
        self.work.append(work)
        await asyncio.sleep(self.delays.get(work.task_id, self.delay))
        if work.task_id in self.fail:
            raise StubTaskError("the stub was told to fail this task")
