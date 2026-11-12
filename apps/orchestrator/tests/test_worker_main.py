"""The orchestrator worker's entry point: configuration, startup, and the consumer it runs (RUN-1)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest
from onb_agents.runs import CLEANUP_RUN, RESUME_RUN, START_RUN
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_orchestrator import worker as worker_module
from onb_orchestrator.policy import RunPolicy
from onb_orchestrator.worker import ConfigError, RunWorker, Settings, prepare, serve


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


def test_prepare_makes_every_index_and_runs_the_start_resume_cleanup_and_fail_task_consumers() -> None:
    db = MemoryDatabase()
    worker = asyncio.run(prepare(memory_store(db), stub_delay=0.0))
    assert set(worker.consumers) == {START_RUN, RESUME_RUN, CLEANUP_RUN, "orchestrator.fail_task"}
    indexed = {name for name, coll in db.collections.items() if coll.indexes}
    assert {"onboarding_runs", "run_tasks", "run_checkpoints", "run_checkpoint_writes", "events", "jobs"} <= indexed


def test_serve_runs_the_worker_until_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class FakeWorker:
        consumers = (START_RUN,)

        async def run(self, stop: asyncio.Event) -> int:
            seen["stop"] = stop
            return 3

    async def fake_prepare(
        store: Any, *, stub_delay: float, policy: RunPolicy, drain_seconds: float, executor: Any
    ) -> FakeWorker:
        seen["delay"] = stub_delay
        seen["policy"] = policy
        seen["drain_seconds"] = drain_seconds
        seen["executor"] = executor
        return FakeWorker()

    class FakeStore:
        closed = False

        async def close(self) -> None:
            FakeStore.closed = True

    monkeypatch.setattr(worker_module, "prepare", fake_prepare)
    monkeypatch.setattr(worker_module, "connect", lambda uri, db: FakeStore())
    policy = RunPolicy(max_attempts=5, lease_seconds=4.0)
    settings = Settings(
        mongo_uri="mongodb://x", mongo_database="onb", stub_delay=0.25, policy=policy, drain_seconds=7.0
    )
    assert asyncio.run(serve(settings, asyncio.Event())) == 3
    assert seen["delay"] == 0.25
    assert (seen["policy"], seen["drain_seconds"]) == (policy, 7.0)
    assert FakeStore.closed


# --- RUN-2: the run policy from the environment, and the graceful drain ---------------------------


def test_the_run_policy_comes_from_the_environment() -> None:
    env = {
        "MONGO_URI": "m",
        "MONGO_DATABASE": "d",
        "ORCHESTRATOR_MAX_ATTEMPTS": "5",
        "ORCHESTRATOR_BACKOFF_BASE": "0.5",
        "ORCHESTRATOR_BACKOFF_MAX": "8",
        "ORCHESTRATOR_LEASE_SECONDS": "6",
        "ORCHESTRATOR_DRAIN_SECONDS": "12",
    }
    settings = Settings.from_env(env)
    assert settings.policy == RunPolicy(max_attempts=5, backoff_base=0.5, backoff_max=8.0, lease_seconds=6.0)
    assert settings.drain_seconds == 12.0
    assert Settings.from_env({"MONGO_URI": "m", "MONGO_DATABASE": "d"}).policy == RunPolicy()


@pytest.mark.parametrize(
    "bad",
    [
        {"ORCHESTRATOR_MAX_ATTEMPTS": "0"},
        {"ORCHESTRATOR_MAX_ATTEMPTS": "many"},
        {"ORCHESTRATOR_BACKOFF_BASE": "-1"},
        {"ORCHESTRATOR_BACKOFF_BASE": "100", "ORCHESTRATOR_BACKOFF_MAX": "1"},
        {"ORCHESTRATOR_LEASE_SECONDS": "0"},
        {"ORCHESTRATOR_DRAIN_SECONDS": "inf"},
    ],
)
def test_a_bad_run_policy_fails_startup(bad: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        Settings.from_env({"MONGO_URI": "m", "MONGO_DATABASE": "d", **bad})


class _LoopWorker:
    """A worker that runs until `stop`, then needs `finish_after` more seconds (or never ends)."""

    consumers = (START_RUN,)

    def __init__(self, drain: asyncio.Event, finish_after: float | None) -> None:
        self.drain = drain
        self.finish_after = finish_after
        self.saw_drain_before_finishing = False
        self.cancelled = False

    async def run(self, stop: asyncio.Event) -> int:
        try:
            await stop.wait()
            if self.finish_after is None:
                await asyncio.sleep(60)
            else:
                await asyncio.sleep(self.finish_after)
                self.saw_drain_before_finishing = self.drain.is_set()
            return 2
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def test_a_stopping_worker_sets_the_drain_flag_and_waits_for_the_task_in_hand() -> None:
    async def go() -> tuple[int, _LoopWorker]:
        drain = asyncio.Event()
        inner = _LoopWorker(drain, finish_after=0.1)
        worker = RunWorker(worker=inner, consumers=inner.consumers, drain=drain, drain_seconds=5.0)  # type: ignore[arg-type]
        stop = asyncio.Event()
        job = asyncio.create_task(worker.run(stop))
        await asyncio.sleep(0.05)
        assert not drain.is_set(), "nothing drains while the worker is not asked to stop"
        stop.set()
        return await job, inner

    processed, inner = asyncio.run(go())
    assert processed == 2
    assert inner.saw_drain_before_finishing
    assert not inner.cancelled


def test_a_task_that_outlasts_the_drain_is_cancelled() -> None:
    async def go() -> tuple[int, _LoopWorker]:
        drain = asyncio.Event()
        inner = _LoopWorker(drain, finish_after=None)
        worker = RunWorker(worker=inner, consumers=inner.consumers, drain=drain, drain_seconds=0.1)  # type: ignore[arg-type]
        stop = asyncio.Event()
        stop.set()
        return await asyncio.wait_for(worker.run(stop), timeout=3), inner

    processed, inner = asyncio.run(go())
    assert processed == 0
    assert inner.cancelled


def test_a_worker_that_ends_by_itself_is_not_drained() -> None:
    async def go() -> tuple[int, bool]:
        drain = asyncio.Event()

        class Done:
            consumers = (START_RUN,)

            async def run(self, stop: asyncio.Event) -> int:
                return 4

        worker = RunWorker(worker=Done(), consumers=Done.consumers, drain=drain)  # type: ignore[arg-type]
        return await worker.run(asyncio.Event()), drain.is_set()

    assert asyncio.run(go()) == (4, False)
