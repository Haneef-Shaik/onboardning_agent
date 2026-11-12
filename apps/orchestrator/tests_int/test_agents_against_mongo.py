"""RUN-3 against the compose Mongo replica set: a run started through the real outbox is claimed by a
real worker, and each task runs through its registered agent (AG-1's runtime, LLM-1's `fake` connector,
a tool double for the hub), with the real `AuditLog` (PRD §7.1, §7.9, §7.10).

Proves on real MongoDB what the unit tests prove on the in-memory driver: the model is recorded once on
the task (`$exists` update), a grant exists only for a task that declares `pii_access`, the per-run token
budget fails a task visibly, and a sink's `task.sink_rejected` fails a running task through the bus.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from onb_agents.registry import AgentManifest, RegisteredAgent, load_manifests
from onb_agents.runs import FAIL_TASK, SINK_REJECTED, START_RUN, RunStarter, RunStore, TaskState, run_bus
from onb_agents.runs.testing import hire_variables_for
from onb_agents.runtime import AgentTask, ModelChoice, ToolSet
from onb_agents.runtime.tools import ForwardResult
from onb_contracts.templates import Template
from onb_crypto import GrantIssuer, GrantSigningKey, GrantVerifier, SinkPolicy, VaultField
from onb_data import AuditLog, DataStore, connect, tenant_scope
from onb_eventbus import QueueSettings
from onb_llm import LLMRuntime, LLMSelection, discover_connectors, load_llm_config
from onb_orchestrator.agent_executor import AgentTaskExecutor
from onb_orchestrator.checkpointer import TenantCheckpointSaver
from onb_orchestrator.consumer import fail_task_handler, start_run_handler

from .conftest import ROOT, Scratch

ORG = "org_acme"
HIRE = "h_1"
OK = {"status": "ok", "answer": {}}
PII = {"fields": ["mobile"], "sink": "notify.telegram"}
TOKEN_KEY = secrets.token_urlsafe(40)
MANIFESTS: dict[str, AgentManifest] = {m.name: m for m in load_manifests(ROOT / "config" / "agents")}


class Models:
    """LLM-1's runtime on the `fake` connector; every model replays one scripted final answer."""

    def __init__(self, tmp: Path, tokens: int) -> None:
        script = tmp / f"script-{secrets.token_hex(4)}.json"
        turn = {"content": json.dumps(OK), "usage": {"input_tokens": tokens // 2, "output_tokens": tokens // 2}}
        script.write_text(json.dumps([turn]), encoding="utf-8")
        env = {
            "LLM_CONNECTOR": "fake",
            "LLM_MODEL_FAST": "fake-fast",
            "LLM_MODEL_STANDARD": "fake-standard",
            "LLM_MODEL_DEEP": "fake-deep",
            "LLM_FAKE_SCRIPT_FILE": str(script),
        }
        self.runtime = LLMRuntime.start(load_llm_config(ROOT / "config" / "llm.yaml", env), discover_connectors(), env)

    @staticmethod
    def _selection(manifest: AgentManifest) -> LLMSelection:
        llm = manifest.llm
        return LLMSelection(alias=llm.alias, connector=llm.connector, model=llm.model, params=llm.params.values())

    def choose(self, manifest: AgentManifest, task: AgentTask) -> ModelChoice:
        resolved, model = self.runtime.chat_model_for(manifest=self._selection(manifest))
        return ModelChoice(record=resolved.task_record(), model=model)

    def pinned(self, manifest: AgentManifest, record: Mapping[str, Any]) -> ModelChoice:
        selection = LLMSelection(connector=str(record["connector"]), model=str(record["model"]))
        _, model = self.runtime.chat_model_for(manifest=selection)
        return ModelChoice(record=record, model=model)


@dataclass
class Tools:
    """No tools; records the grant each task's hub connection was opened with."""

    grants: dict[str, str | None] = field(default_factory=dict)

    @asynccontextmanager
    async def open(self, manifest: AgentManifest, task: AgentTask, token: str) -> AsyncIterator[ToolSet]:
        self.grants[task.task_id] = task.grant

        async def forward(name: str, args: Mapping[str, Any]) -> ForwardResult:
            return ForwardResult("denied", True)

        yield ToolSet(bound=(), forward=forward)


class Agents:
    async def get_agent(self, name: str) -> RegisteredAgent | None:
        manifest = MANIFESTS[name]
        return RegisteredAgent(name, 1, manifest.sha256(), True, manifest, datetime(2026, 11, 12, tzinfo=UTC))


def template(*tasks: tuple[str, list[str], dict[str, Any]]) -> Template:
    return Template.model_validate(
        {
            "key": "agents-int",
            "name": "Agents (integration)",
            "version": 1,
            "status": "published",
            "variables": [
                {"name": "role", "type": "enum", "values": ["engineer", "designer", "sales"]},
                {"name": "location", "type": "string"},
                {"name": "start_date", "type": "date"},
                {"name": "manager_id", "type": "user_ref"},
            ],
            "tasks": [
                {"id": t, "title": t, "agent": "db_agent", "action": f"do_{t}", "depends_on": deps, **extra}
                for t, deps, extra in tasks
            ],
        }
    )


@dataclass
class Stack:
    store: DataStore
    key: GrantSigningKey
    models: Models
    tools: Tools

    def executor(self, run_max_tokens: int = 1_000_000) -> AgentTaskExecutor:
        policy = SinkPolicy({"notify.telegram": frozenset({VaultField.MOBILE})})
        return AgentTaskExecutor(
            store=self.store,
            agents=Agents(),
            models=self.models,
            tools=self.tools,
            auditor=AuditLog(self.store),
            token_key=TOKEN_KEY,
            grants=GrantIssuer(self.key, policy),
            run_max_tokens=run_max_tokens,
        )

    async def start(self, the_template: Template) -> str:
        bus = run_bus(self.store, QueueSettings(lease_seconds=30, heartbeat_seconds=5))
        starter = RunStarter(RunStore(self.store), bus, AuditLog(self.store))
        with tenant_scope(ORG):
            started = await starter.start(
                template=the_template, hire_id=HIRE, variables=hire_variables_for(), actor_id="u_hr"
            )
        return str(started.run["_id"])

    async def drain(self, executor: AgentTaskExecutor) -> None:
        bus = run_bus(self.store, QueueSettings(lease_seconds=30, heartbeat_seconds=5))
        worker = bus.worker(
            {START_RUN: start_run_handler(self.store, executor), FAIL_TASK: fail_task_handler(self.store)}
        )
        while await worker.run_once() is not None:
            pass

    async def task(self, run_id: str, task_id: str) -> dict[str, Any]:
        with tenant_scope(ORG):
            found = await RunStore(self.store).task(run_id, task_id)
        assert found is not None
        return found

    async def run_status(self, run_id: str) -> str:
        with tenant_scope(ORG):
            found = await RunStore(self.store).get(run_id)
        assert found is not None
        return str(found["status"])


def _with_stack(scratch: Scratch, tmp: Path, tokens: int, body: Any) -> Any:
    async def go() -> Any:
        store = connect(scratch.uri, scratch.name)
        try:
            bus = run_bus(store, QueueSettings(lease_seconds=30, heartbeat_seconds=5))
            await bus.ensure_indexes()
            await RunStore(store).ensure_indexes()
            await TenantCheckpointSaver(store).ensure_indexes()
            return await body(Stack(store, GrantSigningKey.generate(), Models(tmp, tokens), Tools()))
        finally:
            await store.close()

    return asyncio.run(go())


def test_each_task_runs_its_agent_with_its_model_recorded_and_a_grant_only_where_declared(
    scratch: Scratch, tmp_path: Path
) -> None:
    async def body(stack: Stack) -> str:
        run_id = await stack.start(template(("a", [], {}), ("b", ["a"], {"pii_access": PII})))
        await stack.drain(stack.executor())
        return run_id

    run_id = _with_stack(scratch, tmp_path, 100, body)
    tasks = {t["task_id"]: t for t in scratch.find("run_tasks", {"run_id": run_id})}
    assert {t["status"] for t in tasks.values()} == {TaskState.DONE.value}
    for task in tasks.values():
        assert task["llm"] == {"connector": "fake", "model": "fake-standard", "alias": "standard", "source": "manifest"}
        assert task["result"]["outcome"] == "completed"
    [run] = scratch.find("onboarding_runs", {"_id": run_id})
    assert run["status"] == "completed"
    stored = json.dumps([scratch.find(c) for c in ("run_tasks", "onboarding_runs", "audit_log")], default=str)
    assert _grants(stored) == {}  # no grant or agent token is stored anywhere


def _grants(stored: str) -> dict[str, str]:
    """Any JWS-shaped string in the stored documents (a grant or an agent token must never be stored)."""
    return {m: m for m in re.findall(r"[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}", stored)}


def test_only_the_task_declaring_pii_access_gets_a_grant_for_its_fields_and_sink(
    scratch: Scratch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}

    async def body(stack: Stack) -> None:
        await stack.start(template(("a", [], {}), ("b", ["a"], {"pii_access": PII})))
        await stack.drain(stack.executor())
        seen.update(stack.tools.grants)
        seen["key"] = stack.key

    _with_stack(scratch, tmp_path, 100, body)
    assert seen["a"] is None
    claims = GrantVerifier([seen["key"].public_key]).verify(seen["b"])
    assert claims.fields == frozenset({VaultField.MOBILE})
    assert (claims.sink, claims.org_id, claims.task_id, claims.subject_id) == ("notify.telegram", ORG, "b", HIRE)


def test_an_exhausted_run_token_budget_fails_the_task_and_the_run(scratch: Scratch, tmp_path: Path) -> None:
    async def body(stack: Stack) -> str:
        run_id = await stack.start(template(("a", [], {}), ("b", ["a"], {}), ("c", ["b"], {})))
        await stack.drain(stack.executor(run_max_tokens=1000))
        return run_id

    run_id = _with_stack(scratch, tmp_path, 600, body)
    by_id = {t["task_id"]: t for t in scratch.find("run_tasks", {"run_id": run_id})}
    assert by_id["a"]["status"] == "done"
    assert by_id["b"]["status"] == "failed"
    assert by_id["b"]["failure"] == {"kind": "budget_exceeded", "reason": "run tokens 1200/1000"}
    assert by_id["c"]["status"] == "cancelled"
    [run] = scratch.find("onboarding_runs", {"_id": run_id})
    assert run["status"] == "failed"


def test_a_sinks_rejection_published_on_the_bus_fails_the_running_task(scratch: Scratch, tmp_path: Path) -> None:
    async def body(stack: Stack) -> str:
        run_id = await stack.start(template(("a", [], {})))
        runs = RunStore(stack.store)
        at = datetime(2026, 11, 12, tzinfo=UTC)
        with tenant_scope(ORG):
            record = await runs.task(run_id, "a")
            assert record is not None
            ready = await runs.move_task(run_id, "a", TaskState.READY, at=at, expected=record)
            await runs.move_task(run_id, "a", TaskState.RUNNING, at=at, expected=ready)
            bus = run_bus(stack.store, QueueSettings(lease_seconds=30, heartbeat_seconds=5))
            payload = {"run_id": run_id, "task_id": "a", "agent": "db_agent", "sink": "notify.telegram"}
            await bus.publish(
                SINK_REJECTED, {**payload, "reason": "wrong_run"}, idempotency_key="int-reject", run_id=run_id
            )
        worker = run_bus(stack.store).worker({FAIL_TASK: fail_task_handler(stack.store)})
        while await worker.run_once() is not None:
            pass
        return run_id

    run_id = _with_stack(scratch, tmp_path, 100, body)
    [task] = scratch.find("run_tasks", {"run_id": run_id, "task_id": "a"})
    assert task["status"] == "failed"
    assert task["failure"] == {"kind": "sink_rejected", "reason": "wrong_run"}
