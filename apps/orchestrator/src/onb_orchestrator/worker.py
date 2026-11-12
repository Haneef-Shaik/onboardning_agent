"""The orchestrator worker: `python -m onb_orchestrator` (PRD §7.1, §7.2).

It claims `run.started` jobs from the event bus and executes each run's graph. Configuration comes
from the environment and startup fails fast (`ConfigError`) when it is missing or malformed:
`MONGO_URI`, `MONGO_DATABASE`, and optionally `ORCHESTRATOR_STUB_DELAY` (seconds each stub task takes;
the stub executor stands in for the agents until RUN-3). Telemetry starts before the Mongo client, so
PyMongo's tracing listener applies to it (F-4). SIGINT/SIGTERM stop the worker after its current job.
"""

from __future__ import annotations

import asyncio
import math
import os
import signal
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from onb_agents.runs import FAIL_TASK, START_RUN, RunStore, run_bus
from onb_data import DataStore, connect
from onb_eventbus import Worker

from .agents_setup import AgentSettings, ConfigError, build_agent_executor
from .checkpointer import TenantCheckpointSaver
from .consumer import fail_task_handler, start_run_handler
from .executor import StubExecutor, TaskExecutor

__all__ = ["SERVICE_NAME", "ConfigError", "RunWorker", "Settings", "main", "prepare", "serve"]

SERVICE_NAME: Final = "orchestrator"


@dataclass(frozen=True, slots=True)
class Settings:
    mongo_uri: str = field(repr=False)
    mongo_database: str
    stub_delay: float
    agents: AgentSettings | None = None
    """Set when `ORCHESTRATOR_EXECUTOR=agents` (RUN-3); None runs RUN-1's stub executor."""

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        missing = [name for name in ("MONGO_URI", "MONGO_DATABASE") if not env.get(name)]
        if missing:
            raise ConfigError(f"missing required setting(s): {', '.join(missing)}")
        raw_delay = env.get("ORCHESTRATOR_STUB_DELAY", "0")
        try:
            delay = float(raw_delay)
        except ValueError:
            raise ConfigError("ORCHESTRATOR_STUB_DELAY must be a number of seconds") from None
        if not math.isfinite(delay) or delay < 0:
            raise ConfigError("ORCHESTRATOR_STUB_DELAY must be a number of seconds")
        return cls(
            mongo_uri=env["MONGO_URI"],
            mongo_database=env["MONGO_DATABASE"],
            stub_delay=delay,
            agents=_agent_settings(env),
        )


def _agent_settings(env: Mapping[str, str]) -> AgentSettings | None:
    chosen = env.get("ORCHESTRATOR_EXECUTOR") or "stub"
    if chosen == "stub":
        return None
    if chosen != "agents":
        raise ConfigError("ORCHESTRATOR_EXECUTOR must be 'stub' or 'agents'")
    return AgentSettings.from_env(env)


@dataclass(frozen=True, slots=True)
class RunWorker:
    worker: Worker
    consumers: tuple[str, ...]

    async def run(self, stop: asyncio.Event) -> int:
        return await self.worker.run(stop)


async def prepare(store: DataStore, *, stub_delay: float, executor: TaskExecutor | None = None) -> RunWorker:
    """Make every index the orchestrator reads by, and a worker for its consumers: `orchestrator.start_run`
    (the run's tasks go to `executor`, the stub when none is given) and `orchestrator.fail_task` (a sink
    refused a task's grant, NOTIF-1)."""
    bus = run_bus(store)
    await bus.ensure_indexes()
    await RunStore(store).ensure_indexes()
    await TenantCheckpointSaver(store).ensure_indexes()
    handlers = {
        START_RUN: start_run_handler(store, executor or StubExecutor(delay=stub_delay)),
        FAIL_TASK: fail_task_handler(store),
    }
    return RunWorker(worker=bus.worker(handlers), consumers=tuple(handlers))


async def serve(settings: Settings, stop: asyncio.Event) -> int:
    store = connect(settings.mongo_uri, settings.mongo_database)
    try:
        if settings.agents is None:
            worker = await prepare(store, stub_delay=settings.stub_delay)
        else:
            executor = build_agent_executor(store, settings.agents, os.environ)
            worker = await prepare(store, stub_delay=settings.stub_delay, executor=executor)
        return await worker.run(stop)
    finally:
        await store.close()


def main() -> None:  # pragma: no cover - process wiring; `serve` and `prepare` are tested
    from onb_telemetry import init_telemetry

    settings = Settings.from_env(os.environ)
    init_telemetry(SERVICE_NAME)

    async def run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await serve(settings, stop)

    asyncio.run(run())
