"""The orchestrator worker's entry point: configuration, startup, and the consumer it runs (RUN-1)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest
from onb_agents.runs import START_RUN
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_orchestrator import worker as worker_module
from onb_orchestrator.worker import ConfigError, Settings, prepare, serve


def test_settings_come_from_the_environment() -> None:
    settings = Settings.from_env({"MONGO_URI": "mongodb://x", "MONGO_DATABASE": "onb"})
    assert settings == Settings(mongo_uri="mongodb://x", mongo_database="onb", stub_delay=0.0)
    assert (
        Settings.from_env({"MONGO_URI": "m", "MONGO_DATABASE": "d", "ORCHESTRATOR_STUB_DELAY": "0.5"}).stub_delay == 0.5
    )


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"MONGO_URI": "mongodb://x"},
        {"MONGO_DATABASE": "onb"},
        {"MONGO_URI": "", "MONGO_DATABASE": "onb"},
        {"MONGO_URI": "m", "MONGO_DATABASE": "d", "ORCHESTRATOR_STUB_DELAY": "soon"},
        {"MONGO_URI": "m", "MONGO_DATABASE": "d", "ORCHESTRATOR_STUB_DELAY": "-1"},
        {"MONGO_URI": "m", "MONGO_DATABASE": "d", "ORCHESTRATOR_STUB_DELAY": "nan"},
    ],
)
def test_startup_fails_fast_on_missing_or_bad_settings(env: Mapping[str, str]) -> None:
    with pytest.raises(ConfigError):
        Settings.from_env(env)


def test_the_secret_is_never_in_the_settings_repr() -> None:
    settings = Settings(mongo_uri="mongodb://user:hunter2@host", mongo_database="onb", stub_delay=0.0)
    assert "hunter2" not in repr(settings)


def test_prepare_makes_every_index_and_subscribes_the_run_consumers() -> None:
    db = MemoryDatabase()
    worker = asyncio.run(prepare(memory_store(db), stub_delay=0.0))
    assert worker.consumers == (START_RUN, "orchestrator.fail_task")  # RUN-3 adds fail_task
    indexed = {name for name, coll in db.collections.items() if coll.indexes}
    assert {"onboarding_runs", "run_tasks", "run_checkpoints", "run_checkpoint_writes", "events", "jobs"} <= indexed


def test_serve_runs_the_worker_until_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class FakeWorker:
        consumers = (START_RUN,)

        async def run(self, stop: asyncio.Event) -> int:
            seen["stop"] = stop
            return 3

    async def fake_prepare(store: Any, *, stub_delay: float) -> FakeWorker:
        seen["delay"] = stub_delay
        return FakeWorker()

    class FakeStore:
        closed = False

        async def close(self) -> None:
            FakeStore.closed = True

    monkeypatch.setattr(worker_module, "prepare", fake_prepare)
    monkeypatch.setattr(worker_module, "connect", lambda uri, db: FakeStore())
    settings = Settings(mongo_uri="mongodb://x", mongo_database="onb", stub_delay=0.25)
    assert asyncio.run(serve(settings, asyncio.Event())) == 3
    assert seen["delay"] == 0.25
    assert FakeStore.closed
