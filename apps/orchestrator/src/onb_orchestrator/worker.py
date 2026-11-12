"""The orchestrator worker: `python -m onb_orchestrator` (PRD §7.1, §7.2, FR-R6).

It claims `run.started` and `run.resumed` jobs from the event bus and executes each run's graph, and
deletes the checkpoints of cancelled runs (`run.cancelled`). Configuration comes from the environment
and startup fails fast (`ConfigError`) when it is missing or malformed:
- `MONGO_URI`, `MONGO_DATABASE`;
- optionally `ORCHESTRATOR_STUB_DELAY` (seconds each stub task takes; the stub executor stands in for the
  agents until RUN-3);
- optionally the run policy (`RunPolicy`): `ORCHESTRATOR_MAX_ATTEMPTS` (3), `ORCHESTRATOR_BACKOFF_BASE`
  (2 s), `ORCHESTRATOR_BACKOFF_MAX` (60 s), `ORCHESTRATOR_LEASE_SECONDS` (30 s: how long a task's, and
  a job's, lease holds without a heartbeat, so how soon a killed worker's work is taken over);
- optionally `ORCHESTRATOR_DRAIN_SECONDS` (30 s: how long a stopping worker waits for its task in hand);
- optionally `APPROVAL_SLA_CONFIG` (`config/approval_sla.yaml`): the approval SLA thresholds (HIL-2). The worker
  runs the SLA sweep (`approval_sla.py`) beside its consumers, every `check_interval_seconds`, until it stops.

Telemetry starts before the Mongo client, so PyMongo's tracing listener applies to it (F-4).

**Graceful drain (FR-R6).** SIGINT/SIGTERM set `stop`. `RunWorker.run` then sets the drain flag: the
task in hand finishes, no new task starts, the run's job goes back to the queue at once (`ReleaseJob`,
no attempt used) and the worker exits. A task that outlasts `drain_seconds` is cancelled; its lease is
released, so the next worker takes it over. A worker killed outright (SIGKILL) gets no drain at all: its
leases run out and the next worker takes over the same way.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import signal
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from onb_agents.approvals import ApprovalStore
from onb_agents.approvals.sla import DEFAULT_SLA_CONFIG, SlaConfigError, SlaPolicy, load_sla_policy
from onb_agents.runs import CLEANUP_RUN, FAIL_TASK, RESUME_APPROVAL, RESUME_RUN, START_RUN, RunStore, run_bus
from onb_contracts.notify import RECORD_DECISION_CONSUMER
from onb_data import DataStore, connect
from onb_eventbus import QueueSettings, Worker

from .agents_setup import AgentSettings, ConfigError, build_agent_executor
from .approval_sla import ApprovalSlaJob, run_sla_forever
from .checkpointer import TenantCheckpointSaver
from .consumer import (
    cleanup_run_handler,
    fail_task_handler,
    record_decision_handler,
    resume_approval_handler,
    start_run_handler,
)
from .executor import StubExecutor, TaskExecutor
from .policy import RunPolicy

__all__ = ["SERVICE_NAME", "ConfigError", "RunWorker", "Settings", "main", "prepare", "serve"]

SERVICE_NAME: Final = "orchestrator"
_DEFAULT_DRAIN_SECONDS: Final = 30.0


def _number(env: Mapping[str, str], name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = env.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number") from None
    if not math.isfinite(value) or value < minimum:
        raise ConfigError(f"{name} must be a number of at least {minimum}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    mongo_uri: str = field(repr=False)
    mongo_database: str
    stub_delay: float
    agents: AgentSettings | None = None
    """Set when `ORCHESTRATOR_EXECUTOR=agents` (RUN-3); None runs RUN-1's stub executor."""
    policy: RunPolicy = field(default_factory=RunPolicy)
    drain_seconds: float = _DEFAULT_DRAIN_SECONDS
    approval_sla: SlaPolicy = field(default_factory=SlaPolicy)
    """HIL-2: the approval SLA thresholds, from `APPROVAL_SLA_CONFIG` (default `config/approval_sla.yaml`)."""

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        missing = [name for name in ("MONGO_URI", "MONGO_DATABASE") if not env.get(name)]
        if missing:
            raise ConfigError(f"missing required setting(s): {', '.join(missing)}")
        defaults = RunPolicy()
        try:
            policy = RunPolicy(
                max_attempts=int(_number(env, "ORCHESTRATOR_MAX_ATTEMPTS", defaults.max_attempts, minimum=1)),
                backoff_base=_number(env, "ORCHESTRATOR_BACKOFF_BASE", defaults.backoff_base),
                backoff_max=_number(env, "ORCHESTRATOR_BACKOFF_MAX", defaults.backoff_max),
                lease_seconds=_number(env, "ORCHESTRATOR_LEASE_SECONDS", defaults.lease_seconds, minimum=0.05),
            )
        except ValueError as error:
            raise ConfigError(f"invalid run policy: {error}") from None
        return cls(
            mongo_uri=env["MONGO_URI"],
            mongo_database=env["MONGO_DATABASE"],
            stub_delay=_number(env, "ORCHESTRATOR_STUB_DELAY", 0.0),
            policy=policy,
            drain_seconds=_number(env, "ORCHESTRATOR_DRAIN_SECONDS", _DEFAULT_DRAIN_SECONDS),
            agents=_agent_settings(env),
            approval_sla=_approval_sla(env),
        )


def _approval_sla(env: Mapping[str, str]) -> SlaPolicy:
    try:
        return load_sla_policy(Path(env.get("APPROVAL_SLA_CONFIG") or DEFAULT_SLA_CONFIG))
    except SlaConfigError as error:
        raise ConfigError(f"invalid approval SLA config: {error}") from None


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
    drain: asyncio.Event = field(default_factory=asyncio.Event)
    drain_seconds: float = _DEFAULT_DRAIN_SECONDS

    async def run(self, stop: asyncio.Event) -> int:
        """Work until `stop`, then drain: the task in hand finishes (up to `drain_seconds`), nothing new
        starts and the run's job is handed back. Returns how many jobs were processed."""
        working = asyncio.ensure_future(self.worker.run(stop))
        stopping = asyncio.ensure_future(stop.wait())
        try:
            await asyncio.wait({working, stopping}, return_when=asyncio.FIRST_COMPLETED)
            if not working.done():
                self.drain.set()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(working), timeout=self.drain_seconds)
            if not working.done():
                working.cancel()  # a task that outlasted the drain: its lease is released, the next worker takes over
                with contextlib.suppress(asyncio.CancelledError):
                    await working
                return 0
            return working.result()
        finally:
            stopping.cancel()


async def prepare(
    store: DataStore,
    *,
    stub_delay: float = 0.0,
    policy: RunPolicy | None = None,
    drain_seconds: float = _DEFAULT_DRAIN_SECONDS,
    executor: TaskExecutor | None = None,
) -> RunWorker:
    """Make every index the orchestrator reads by, and a worker for its consumers: `orchestrator.start_run`
    and `orchestrator.resume_run` (the run's tasks go to `executor`, the stub when none is given),
    `orchestrator.cleanup_run` (a cancelled run's checkpoints) and `orchestrator.fail_task` (a sink refused a
    task's grant, NOTIF-1), `orchestrator.resume_approval` (an approval was decided) and
    `orchestrator.record_decision` (a Telegram press on an approval's buttons, HIL-1).

    The bus's job lease follows the policy's lease, so a killed worker's job and its task become claimable
    together."""
    policy = policy or RunPolicy()
    queue = QueueSettings(lease_seconds=policy.lease_seconds, heartbeat_seconds=policy.heartbeat_seconds)
    bus = run_bus(store, queue)
    await bus.ensure_indexes()
    await RunStore(store).ensure_indexes()
    await ApprovalStore(store).ensure_indexes()
    await TenantCheckpointSaver(store).ensure_indexes()
    drain = asyncio.Event()
    tasks = executor or StubExecutor(delay=stub_delay)
    run_handler = start_run_handler(store, tasks, bus=bus, policy=policy, drain=drain)
    handlers = {
        START_RUN: run_handler,
        RESUME_RUN: run_handler,
        CLEANUP_RUN: cleanup_run_handler(store),
        FAIL_TASK: fail_task_handler(store),
        RESUME_APPROVAL: resume_approval_handler(  # HIL-1: an approval was decided
            store, tasks, bus=bus, policy=policy, drain=drain
        ),
        RECORD_DECISION_CONSUMER: record_decision_handler(store, bus),  # HIL-1: a Telegram press
    }
    return RunWorker(worker=bus.worker(handlers), consumers=tuple(handlers), drain=drain, drain_seconds=drain_seconds)


async def _with_sla_sweep(store: DataStore, settings: Settings, worker: RunWorker, stop: asyncio.Event) -> int:
    """Run the worker with the approval SLA sweep beside it (HIL-2); the sweep stops when the worker ends."""
    sweep_stop = asyncio.Event()
    job = ApprovalSlaJob(store, run_bus(store), settings.approval_sla)
    sweeping = asyncio.ensure_future(
        run_sla_forever(job, interval=settings.approval_sla.check_interval_seconds, stop=sweep_stop)
    )
    try:
        return await worker.run(stop)
    finally:
        sweep_stop.set()
        await sweeping


async def serve(settings: Settings, stop: asyncio.Event) -> int:
    store = connect(settings.mongo_uri, settings.mongo_database)
    try:
        executor = None if settings.agents is None else build_agent_executor(store, settings.agents, os.environ)
        worker = await prepare(
            store,
            stub_delay=settings.stub_delay,
            policy=settings.policy,
            drain_seconds=settings.drain_seconds,
            executor=executor,
        )
        return await _with_sla_sweep(store, settings, worker, stop)
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
