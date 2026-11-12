"""AGT-1 integration plumbing: a run of the platform's own engine whose tasks run their registered agents
through the real hub, the real mcp-mocks and mcp-notify servers and a real MongoDB (the `stack` fixture).

Only the model is scripted (LLM-1's `fake` connector, no model call): for each task a script says what a
capable model would do given what the executor gave it (`task.input`: action, `task_key`, `hire_key`,
`variables`). Everything else (tokens, grants, hub policy, mocks' idempotency, the vault, the audit log,
the run state machine) is production code.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from onb_agents.approvals import ApprovalDecider, ApprovalStore
from onb_agents.registry import AgentManifest, AgentRegistry
from onb_agents.registry.mongo import MongoRegistryStore
from onb_agents.runs import RUN_STARTED, START_RUN, RunStarter, RunStore
from onb_agents.runs.testing import OutboxStandIn
from onb_agents.runtime import AgentTask, HubToolSource, ModelChoice
from onb_contracts.notify import Decision
from onb_contracts.templates import Template
from onb_data import AuditLog, DataStore, connect, tenant_scope
from onb_eventbus import JobContext
from onb_llm import LLMRuntime, LLMSelection, discover_connectors, load_llm_config
from onb_orchestrator.agent_executor import AgentTaskExecutor
from onb_orchestrator.consumer import resume_approval_handler, start_run_handler

from .conftest import DATABASE, ROOT, Stack

ORG = "org_acme"
HIRE = "h_acme_001"  # the hire whose mobile the stack's vault holds (conftest `_notify`)
SYNTHETIC_NAME = "Priya Synthetic"
TURN = tuple[Mapping[str, Any], ...]
Script = Callable[[AgentTask, Stack], Sequence[Mapping[str, Any]]]
PROFILE = {"engineer": "engineering", "designer": "design", "sales": "standard"}


def final(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {"content": json.dumps(payload), "usage": {"input_tokens": 50, "output_tokens": 50}}


def tool_turn(name: str, args: Mapping[str, Any]) -> dict[str, Any]:
    return {"content": "", "tool_calls": [{"name": name, "args": dict(args)}]}


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")


def seed_script(task: AgentTask, stack: Stack) -> list[dict[str, Any]]:
    """What a capable model does for each seed-template action, given the executor's input."""
    given = task.input
    action, variables = given["action"], given["variables"]
    if action == "provision_laptop":
        args = {
            "task_id": given["task_key"],
            "hire_id": given["hire_key"],
            "profile": PROFILE[variables["role"]],
            "office": slug(variables["location"]),
        }
        done = {"status": "ok", "provisioned": [{"action": action, "done": True}]}
        return [tool_turn("mocks__provision_laptop", args), final(done)]
    if action == "create_bitbucket_account":
        args = {
            "task_id": given["task_key"],
            "hire_id": given["hire_key"],
            "workspace": "acme-eng",
            "groups": ["backend"],
        }
        done = {"status": "ok", "provisioned": [{"action": action, "done": True}]}
        return [tool_turn("mocks__create_bitbucket_account", args), final(done)]
    if action == "render_and_send":
        token = stack.notify.tokens[ORG]
        body = f"Welcome, {SYNTHETIC_NAME.split()[0]}! You start on {variables['start_date']}."
        message = {"channel": "telegram", "recipient_token": token, "body": body}
        return [
            tool_turn("mongo__find", {"collection": "new_hires", "filter": {"_id": given["hire_id"]}, "limit": 1}),
            tool_turn("notify__notify_send", message),
            final({"status": "ok", "message": message}),
        ]
    if action == "approval":
        return [final({"status": "needs_human", "explanation": "A manager decides; nothing is pre-approved."})]
    # db_agent: reads this run's task records, then answers what it did.
    find = {"collection": "run_tasks", "filter": {"run_id": task.run_id}, "limit": 20}
    return [tool_turn("mongo__find", find), final({"status": "ok", "answer": {"action": action, "done": True}})]


class ScriptedModels:
    """A `ModelSource`: each task's model replays `script(task)` on LLM-1's fake connector."""

    def __init__(self, tmp: Path, stack: Stack, script: Script = seed_script) -> None:
        self._tmp, self._stack, self._script = tmp, stack, script

    def _runtime(self, script: Sequence[Mapping[str, Any]]) -> LLMRuntime:
        path = self._tmp / f"script-{secrets.token_hex(4)}.json"
        path.write_text(json.dumps(list(script)), encoding="utf-8")
        env = {
            "LLM_CONNECTOR": "fake",
            "LLM_MODEL_FAST": "fake-fast",
            "LLM_MODEL_STANDARD": "fake-standard",
            "LLM_MODEL_DEEP": "fake-deep",
            "LLM_FAKE_SCRIPT_FILE": str(path),
        }
        return LLMRuntime.start(load_llm_config(ROOT / "config" / "llm.yaml", env), discover_connectors(), env)

    @staticmethod
    def _selection(manifest: AgentManifest) -> LLMSelection:
        llm = manifest.llm
        return LLMSelection(alias=llm.alias, connector=llm.connector, model=llm.model, params=llm.params.values())

    def choose(self, manifest: AgentManifest, task: AgentTask) -> ModelChoice:
        runtime = self._runtime(self._script(task, self._stack))
        resolved, model = runtime.chat_model_for(manifest=self._selection(manifest))
        return ModelChoice(record=resolved.task_record(), model=model)

    def pinned(self, manifest: AgentManifest, record: Mapping[str, Any]) -> ModelChoice:
        raise AssertionError("no task is retried in these runs")


def seed_hire(role: str = "engineer") -> dict[str, Any]:
    return {
        "_id": HIRE,
        "name": SYNTHETIC_NAME,
        "role": role,
        "location": "Bangalore",
        "start_date": "2026-12-01",
        "manager_id": "u_acme_mgr",
    }


def one_task_template(agent: str, action: str, task_id: str = "t1", **extra: Any) -> Template:
    return template([{"id": task_id, "title": task_id, "agent": agent, "action": action, "depends_on": [], **extra}])


def template(tasks: list[dict[str, Any]], key: str = "agt1-int") -> Template:
    return Template.model_validate(
        {
            "key": key,
            "name": "AGT-1 (integration)",
            "version": 1,
            "status": "published",
            "variables": [
                {"name": "role", "type": "enum", "values": ["engineer", "designer", "sales"]},
                {"name": "location", "type": "string"},
                {"name": "start_date", "type": "date"},
                {"name": "manager_id", "type": "user_ref"},
            ],
            "tasks": tasks,
        }
    )


@dataclass
class Platform:
    """The orchestrator side of the integration stack: store, executor, and one-run drivers."""

    store: DataStore
    stack: Stack
    models: ScriptedModels
    executor: AgentTaskExecutor
    runs: RunStore
    bus: OutboxStandIn = field(default_factory=OutboxStandIn)

    @classmethod
    def create(cls, stack: Stack, tmp: Path, script: Script = seed_script) -> Platform:
        store = connect(stack.mongo_uri, DATABASE, app_name="agt1-int")
        audit = AuditLog(store)
        models = ScriptedModels(tmp, stack, script)
        executor = AgentTaskExecutor(
            store=store,
            agents=AgentRegistry(MongoRegistryStore(store), audit),
            models=models,
            tools=HubToolSource(stack.hub_url),
            auditor=audit,
            token_key=stack.token_key,
            grants=stack.notify.issuer,
            run_max_tokens=1_000_000,
        )
        return cls(store, stack, models, executor, RunStore(store))

    async def add_hire(self, document: Mapping[str, Any]) -> None:
        with tenant_scope(ORG):
            await self.store.tenant("new_hires").delete_many({"_id": document["_id"]})
            await self.store.tenant("new_hires").insert_one(dict(document))

    async def start(self, the_template: Template, role: str = "engineer") -> str:
        starter = RunStarter(self.runs, self.bus, AuditLog(self.store))
        variables = {"role": role, "location": "Bangalore", "start_date": "2026-12-01", "manager_id": "u_acme_mgr"}
        with tenant_scope(ORG):
            started = await starter.start(template=the_template, hire_id=HIRE, variables=variables, actor_id="u_hr")
        return str(started.run["_id"])

    async def drive(self, run_id: str) -> None:
        job = JobContext(
            job_id=f"job_{secrets.token_hex(4)}",
            org_id=ORG,
            consumer=START_RUN,
            event_id=f"ev_{secrets.token_hex(4)}",
            event_type=RUN_STARTED,
            payload=MappingProxyType({"run_id": run_id}),
            run_id=run_id,
            attempt=1,
        )
        with tenant_scope(ORG):
            await start_run_handler(self.store, self.executor, bus=self.bus)(job)

    async def approve_and_resume(self, run_id: str) -> None:
        """HIL-1: the run paused at its approval task. Decide the approval as its approver (the real decision
        write; the outbox stand-in takes its event, as for the start) and resume the paused graph."""
        with tenant_scope(ORG):
            approvals = ApprovalStore(self.store)
            [approval] = [a for a in await approvals.for_task(run_id, "manager_approval") if a["status"] == "pending"]
            await ApprovalDecider(approvals, self.bus, auditor=AuditLog(self.store)).decide(
                approval["_id"],
                decision=Decision.APPROVE,
                comment=None,
                channel="web",
                user_id=approval["approver_id"] or "u_hr",
                role="HR",
            )
            job = JobContext(
                job_id=f"job_{secrets.token_hex(4)}",
                org_id=ORG,
                consumer="orchestrator.resume_approval",
                event_id=f"ev_{secrets.token_hex(4)}",
                event_type="approval.decided",
                payload=MappingProxyType({"approval_id": approval["_id"]}),
                run_id=run_id,
                attempt=1,
            )
            await resume_approval_handler(self.store, self.executor, bus=self.bus)(job)

    async def tasks(self, run_id: str) -> dict[str, dict[str, Any]]:
        with tenant_scope(ORG):
            return {t["task_id"]: t for t in await self.runs.tasks(run_id)}

    async def run_status(self, run_id: str) -> str:
        with tenant_scope(ORG):
            found = await self.runs.get(run_id)
        assert found is not None
        return str(found["status"])


def on_platform[T](
    stack: Stack,
    tmp: Path,
    body: Callable[[Platform], Awaitable[T]],
    script: Script = seed_script,
    role: str = "engineer",
) -> T:
    """Run `body(platform)` on one event loop, with a hire of `role` seeded and the store closed at the end."""

    async def go() -> T:
        platform = Platform.create(stack, tmp, script)
        try:
            await platform.add_hire(seed_hire(role))
            return await body(platform)
        finally:
            await platform.store.close()

    return asyncio.run(go())


NOW = datetime(2026, 11, 12, tzinfo=UTC)
