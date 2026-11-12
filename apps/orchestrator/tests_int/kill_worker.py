"""A real orchestrator worker process for the kill test (RUN-2 box 1). Test-only: `python kill_worker.py`.

It is `onb_orchestrator.worker` with one change, its executor: every task call is logged, and a task's side
effect goes to an idempotent sink on the filesystem keyed by the task's `idempotency_key` (the first apply
creates a file exclusively, a repeat is recorded as a duplicate and does nothing), like a well-behaved
external system. One task (`STALL_TASK`) stalls, once, at `STALL_STAGE`, so the test can kill the process
while that task is `running`:
- `before` (the default): stalls before applying its effect;
- `after`: applies its effect, then stalls: the worker dies after the effect and before the task is
  marked done, the case that would double the effect if nothing deduplicated it.

Environment: `MONGO_URI`, `MONGO_DATABASE`, `KILL_DIR` (where the logs and the sink go), `LEASE_SECONDS`.
SIGTERM drains (the worker exits 0 after its task in hand); SIGKILL gets no cleanup at all.
"""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

from onb_data import connect
from onb_orchestrator.executor import TaskWork
from onb_orchestrator.policy import RunPolicy
from onb_orchestrator.worker import prepare

STALL_TASK = os.environ.get("STALL_TASK", "b")
STALL_STAGE = os.environ.get("STALL_STAGE", "before")


class EffectExecutor:
    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "effects").mkdir(parents=True, exist_ok=True)

    def _log(self, name: str, line: str) -> None:
        with (self.root / name).open("a") as handle:  # O_APPEND: one short line per write
            handle.write(line + "\n")

    def _apply(self, key: str) -> None:
        try:
            descriptor = os.open(self.root / "effects" / key.replace(":", "_"), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            self._log("deduplicated.log", key)  # the sink saw this key already: it does nothing
            return
        os.close(descriptor)
        self._log("applied.log", key)

    async def _stall_once(self) -> None:
        marker = self.root / "stalled"
        if marker.exists():
            return
        marker.write_text("stalled")
        await asyncio.sleep(3600)  # the test kills this process here

    async def execute(self, work: TaskWork) -> None:
        self._log("calls.log", f"{work.task_id} {work.idempotency_key}")
        stalls = work.task_id == STALL_TASK
        if stalls and STALL_STAGE == "before":
            await self._stall_once()
        await asyncio.sleep(0.05)
        self._apply(work.idempotency_key)
        if stalls and STALL_STAGE == "after":
            await self._stall_once()


async def main() -> None:
    lease = float(os.environ["LEASE_SECONDS"])
    store = connect(os.environ["MONGO_URI"], os.environ["MONGO_DATABASE"])
    policy = RunPolicy(max_attempts=3, backoff_base=0.1, backoff_max=0.5, lease_seconds=lease)
    worker = await prepare(
        store, policy=policy, drain_seconds=10.0, executor=EffectExecutor(Path(os.environ["KILL_DIR"]))
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await worker.run(stop)
    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
