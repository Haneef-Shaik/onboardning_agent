"""Test plumbing for RUN-3: a run executed through registered agents, with every collaborator a
test double that keeps the production interfaces.

- `Models` is LLM-1's runtime on the `fake` connector (a scripted model per call). `models.runtime` can be
  swapped while a task runs, which is how a test changes the model configuration mid-run.
- `Tools` is a `ToolSource` with no tools that records every (task, token) pair the runtime opened a
  hub connection with, so a test sees the grant and the agent token exactly as the hub would.
- `Harness` starts a run with the real `RunStarter`, then drives it with the real `start_run_handler`
  over the in-memory driver. Hires, templates and credentials are synthetic.
"""

from __future__ import annotations

import inspect
import json
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from onb_agents.registry import AgentManifest, RegisteredAgent, load_manifests
from onb_agents.runs import RUN_STARTED, START_RUN, RunStarter, RunStore
from onb_agents.runs.testing import OutboxStandIn, hire_variables_for
from onb_agents.runtime import AgentTask, ModelChoice, ToolSet
from onb_agents.runtime.tools import ForwardResult
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_contracts.templates import Template
from onb_crypto import GrantIssuer, GrantSigningKey, GrantVerifier, SinkPolicy, VaultField
from onb_data import DataStore, tenant_scope
from onb_data.testing import InMemoryAuditLog
from onb_eventbus import JobContext
from onb_llm import LLMRuntime, LLMSelection, discover_connectors, load_llm_config
from onb_orchestrator.agent_executor import AgentTaskExecutor
from onb_orchestrator.consumer import start_run_handler
from onb_orchestrator.policy import RunPolicy

ROOT = Path(__file__).resolve().parents[3]
MANIFESTS: dict[str, AgentManifest] = {m.name: m for m in load_manifests(ROOT / "config" / "agents")}
AGENT_TOKEN_KEY = secrets.token_urlsafe(40)
NO_WAIT = RunPolicy(max_attempts=3, backoff_base=0.0, backoff_max=0.0)
"""Retries without waiting: a failing task is attempted `max_attempts` times, then the run blocks (RUN-2)."""
ORG = "org_a"
HIRE = "h_1"
OK = {"status": "ok", "answer": {}}
NOW = datetime(2026, 11, 12, tzinfo=UTC)


def final(payload: Mapping[str, Any], *, tokens: int | None = None) -> dict[str, Any]:
    """A scripted final turn answering with `payload`; `tokens` makes the turn's usage exact."""
    turn: dict[str, Any] = {"content": json.dumps(payload)}
    if tokens is not None:
        turn["usage"] = {"input_tokens": tokens // 2, "output_tokens": tokens - tokens // 2}
    return turn


class Models:
    """LLM-1's runtime on the `fake` connector. Every model replays `script` from its start."""

    def __init__(self, script: Sequence[Mapping[str, Any]], tmp: Path, *, standard: str = "fake-standard") -> None:
        self._script = list(script)
        self._tmp = tmp
        self.runtime = self.build(standard)
        self.chosen: list[Mapping[str, Any]] = []
        self.pinned_from: list[Mapping[str, Any]] = []

    def build(self, standard: str) -> LLMRuntime:
        path = self._tmp / f"script-{secrets.token_hex(4)}.json"
        path.write_text(json.dumps(self._script), encoding="utf-8")
        env = {
            "LLM_CONNECTOR": "fake",
            "LLM_MODEL_FAST": "fake-fast",
            "LLM_MODEL_STANDARD": standard,
            "LLM_MODEL_DEEP": "fake-deep",
            "LLM_FAKE_SCRIPT_FILE": str(path),
        }
        config = load_llm_config(ROOT / "config" / "llm.yaml", env)
        return LLMRuntime.start(config, discover_connectors(), env)

    def reconfigure(self, standard: str) -> None:
        """The deployment's model configuration changes: later resolutions see the new model."""
        self.runtime = self.build(standard)

    @staticmethod
    def _selection(manifest: AgentManifest) -> LLMSelection:
        llm = manifest.llm
        return LLMSelection(alias=llm.alias, connector=llm.connector, model=llm.model, params=llm.params.values())

    def choose(self, manifest: AgentManifest, task: AgentTask) -> ModelChoice:
        resolved, model = self.runtime.chat_model_for(manifest=self._selection(manifest))
        self.chosen.append(resolved.task_record())
        return ModelChoice(record=resolved.task_record(), model=model)

    def pinned(self, manifest: AgentManifest, record: Mapping[str, Any]) -> ModelChoice:
        self.pinned_from.append(dict(record))
        selection = LLMSelection(
            connector=str(record["connector"]), model=str(record["model"]), params=manifest.llm.params.values()
        )
        _, model = self.runtime.chat_model_for(manifest=selection)
        return ModelChoice(record=record, model=model)


@dataclass
class Tools:
    """A `ToolSource` with no tools. `on_open` runs when the runtime opens its hub connection."""

    opened: list[tuple[AgentTask, str]] = field(default_factory=list)
    on_open: Callable[[AgentTask], Awaitable[None] | None] | None = None
    bound: tuple[Any, ...] = ()

    @asynccontextmanager
    async def open(self, manifest: AgentManifest, task: AgentTask, token: str) -> AsyncIterator[ToolSet]:
        self.opened.append((task, token))
        if self.on_open is not None:
            outcome = self.on_open(task)
            if inspect.isawaitable(outcome):
                await outcome

        async def forward(name: str, args: Mapping[str, Any]) -> ForwardResult:
            return ForwardResult("denied", True)

        yield ToolSet(bound=self.bound, forward=forward)

    def for_task(self, task_id: str) -> list[tuple[AgentTask, str]]:
        return [(t, token) for t, token in self.opened if t.task_id == task_id]


@dataclass
class Agents:
    """The registry's `get_agent`, over the real manifests in config/agents."""

    retired: frozenset[str] = frozenset()
    missing: frozenset[str] = frozenset()

    async def get_agent(self, name: str) -> RegisteredAgent | None:
        if name in self.missing or name not in MANIFESTS:
            return None
        manifest = MANIFESTS[name]
        return RegisteredAgent(name, 1, manifest.sha256(), name not in self.retired, manifest, NOW)


class SpyIssuer(GrantIssuer):
    """The real issuer, counting issued grants."""

    def __init__(self, key: GrantSigningKey, policy: SinkPolicy) -> None:
        super().__init__(key, policy)
        self.issued = 0

    def issue_grant(self, org_id: str, **kwargs: Any) -> str:
        grant = super().issue_grant(org_id, **kwargs)
        self.issued += 1
        return grant


def sink_policy() -> SinkPolicy:
    return SinkPolicy(
        {"notify.telegram": frozenset({VaultField.MOBILE}), "notify.email": frozenset({VaultField.EMAIL})}
    )


def task_doc(task_id: str, *depends_on: str, agent: str = "db_agent", **extra: Any) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": f"Task {task_id}",
        "agent": agent,
        "action": f"do_{task_id}",
        "depends_on": list(depends_on),
        **extra,
    }


def template(*tasks: dict[str, Any]) -> Template:
    return Template.model_validate(
        {
            "key": "agents-test",
            "name": "Agents (test)",
            "version": 1,
            "status": "published",
            "variables": [
                {"name": "role", "type": "enum", "values": ["engineer", "designer", "sales"]},
                {"name": "location", "type": "string"},
                {"name": "start_date", "type": "date"},
                {"name": "manager_id", "type": "user_ref"},
            ],
            "tasks": list(tasks),
        }
    )


@dataclass
class Harness:
    db: MemoryDatabase
    store: DataStore
    runs: RunStore
    bus: OutboxStandIn
    audit: InMemoryAuditLog
    key: GrantSigningKey
    issuer: SpyIssuer
    models: Models
    tools: Tools
    agents: Agents

    @classmethod
    def create(cls, models: Models, tools: Tools | None = None, agents: Agents | None = None) -> Harness:
        db = MemoryDatabase()
        store = memory_store(db)
        key = GrantSigningKey.generate()
        return cls(
            db=db,
            store=store,
            runs=RunStore(store),
            bus=OutboxStandIn(),
            audit=InMemoryAuditLog(),
            key=key,
            issuer=SpyIssuer(key, sink_policy()),
            models=models,
            tools=tools or Tools(),
            agents=agents or Agents(),
        )

    def executor(
        self, *, run_max_tokens: int = 1_000_000, grants: GrantIssuer | str | None = "default"
    ) -> AgentTaskExecutor:
        return AgentTaskExecutor(
            store=self.store,
            agents=self.agents,
            models=self.models,
            tools=self.tools,
            auditor=self.audit,
            token_key=AGENT_TOKEN_KEY,
            grants=self.issuer if grants == "default" else grants,  # type: ignore[arg-type]
            run_max_tokens=run_max_tokens,
        )

    def verifier(self) -> GrantVerifier:
        return GrantVerifier([self.key.public_key])

    async def start(self, the_template: Template, org: str = ORG) -> str:
        starter = RunStarter(self.runs, self.bus, self.audit)
        with tenant_scope(org):
            started = await starter.start(
                template=the_template, hire_id=HIRE, variables=hire_variables_for(), actor_id="u_hr"
            )
        return str(started.run["_id"])

    async def start_diamond(self, org: str = ORG) -> str:
        """A run of the diamond a → (b, c) → d, every task on `db_agent`."""
        return await self.start(
            template(task_doc("a"), task_doc("b", "a"), task_doc("c", "a"), task_doc("d", "b", "c")), org
        )

    async def execute(self, run_id: str, executor: AgentTaskExecutor, org: str = ORG) -> None:
        handler = start_run_handler(self.store, executor, bus=self.bus, policy=NO_WAIT)
        job = JobContext(
            job_id="job_1",
            org_id=org,
            consumer=START_RUN,
            event_id="ev_1",
            event_type=RUN_STARTED,
            payload=MappingProxyType({"run_id": run_id}),
            run_id=run_id,
            attempt=1,
        )
        with tenant_scope(org):
            await handler(job)

    async def task(self, run_id: str, task_id: str, org: str = ORG) -> dict[str, Any]:
        with tenant_scope(org):
            found = await self.runs.task(run_id, task_id)
        assert found is not None
        return found

    async def run(self, run_id: str, org: str = ORG) -> dict[str, Any]:
        with tenant_scope(org):
            found = await self.runs.get(run_id)
        assert found is not None
        return found

    def everything_stored(self) -> str:
        """Every document of every collection as text, for scans."""
        return json.dumps([[name, doc] for name, doc in self.db.all_documents()], default=str, sort_keys=True)


def find_tool() -> Any:
    """A bound hub tool for tests: `mongo__find` answering a fixed document."""
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel

    class FindArgs(BaseModel):
        collection: str

    async def find(collection: str) -> str:
        return json.dumps({"documents": [], "count": 0})

    return StructuredTool.from_function(
        coroutine=find, name="mongo__find", description="Find documents", args_schema=FindArgs
    )


def tool_turn(name: str, args: Mapping[str, Any]) -> dict[str, Any]:
    return {"content": "", "tool_calls": [{"name": name, "args": dict(args)}]}
