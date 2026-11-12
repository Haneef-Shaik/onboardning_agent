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

from onb_agents.runs import START_RUN, RunStore, run_bus
from onb_data import DataStore, connect
from onb_eventbus import Worker

from .checkpointer import TenantCheckpointSaver
from .consumer import start_run_handler
from .executor import StubExecutor

__all__ = ["SERVICE_NAME", "ConfigError", "RunWorker", "Settings", "main", "prepare", "serve"]

SERVICE_NAME: Final = "orchestrator"


class ConfigError(Exception):
    """A required setting is missing or malformed. Names the variable, never its value."""


@dataclass(frozen=True, slots=True)
class Settings:
    mongo_uri: str = field(repr=False)
    mongo_database: str
    stub_delay: float

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
        return cls(mongo_uri=env["MONGO_URI"], mongo_database=env["MONGO_DATABASE"], stub_delay=delay)


@dataclass(frozen=True, slots=True)
class RunWorker:
    worker: Worker
    consumers: tuple[str, ...]

    async def run(self, stop: asyncio.Event) -> int:
        return await self.worker.run(stop)


async def prepare(store: DataStore, *, stub_delay: float) -> RunWorker:
    """Make every index the orchestrator reads by, and a worker for its one consumer."""
    bus = run_bus(store)
    await bus.ensure_indexes()
    await RunStore(store).ensure_indexes()
    await TenantCheckpointSaver(store).ensure_indexes()
    handlers = {START_RUN: start_run_handler(store, StubExecutor(delay=stub_delay))}
    return RunWorker(worker=bus.worker(handlers), consumers=tuple(handlers))


async def serve(settings: Settings, stop: asyncio.Event) -> int:
    store = connect(settings.mongo_uri, settings.mongo_database)
    try:
        worker = await prepare(store, stub_delay=settings.stub_delay)
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
