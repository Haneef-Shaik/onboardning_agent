"""The task executor that runs each task node through its registered agent (PRD §7.1 Sub-agents, §7.9).

`AgentTaskExecutor` implements RUN-1's `TaskExecutor` seam. For one task it:

1. reads the task from the run's hash-checked DAG (never from the request or the queue): its template
   task (`inputs`, `pii_access`) and the run's hire id;
2. looks the agent up in the registry (an unregistered or retired agent fails the task);
3. issues a PII grant **only** when the template task declares `pii_access`, for exactly those fields and
   that sink, for this run's own `run_id` (the sink refuses any other: NOTIF-1 `wrong_run`). The grant
   goes to the runtime as `AgentTask.grant`, which sends it only as a header on the agent's hub
   connection; it is never stored, logged or put in a message;
4. fixes the task's model once (`RunStore.record_model`): a first attempt resolves it (manifest → org
   override → default) and records it on the task; a later attempt of the same task rebuilds the
   recorded model, so a configuration change never moves a task that has started (PRD §7.1);
5. runs AG-1's `AgentRunner` with a fresh per-task agent token under a per-run token budget that is
   shared by the run's tasks (and rebuilt from their recorded usage after a restart);
6. stores the outcome on the task before the runner moves its state: `result` (outcome, output, usage)
   and, when the agent failed, `failure = {kind, reason}` (codes and counts only). A failed outcome raises
   `AgentTaskFailedError`, so the runner marks the task `failed` and the run fails visibly.

A task never sees a hire's values: the agent's input is the action, the template's `inputs` (which the
template validator keeps free of PII), and the hire's id. Raw PII is resolved only at a sink.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import timedelta
from typing import Any, Final, Protocol

from onb_agents.registry import AgentManifest, RegisteredAgent
from onb_agents.runs import RunStore, TaskState, stored_dag
from onb_agents.runtime import AgentRunner, AgentRunResult, AgentTask, ModelChoice, ToolSource
from onb_crypto import CryptoError, GrantIssuer, VaultField
from onb_data import Auditor, DataStore, current_tenant
from onb_guardrails import Budget, BudgetLimits, ExternalGuard
from onb_llm import LLMRuntime, LLMSelection
from pymongo.errors import PyMongoError

from .executor import TaskWork

__all__ = [
    "AgentLookup",
    "AgentTaskExecutor",
    "AgentTaskFailedError",
    "ModelSource",
    "OrgOverrides",
    "RuntimeModelSource",
]

_log = logging.getLogger("onb_orchestrator")
_MAX_RUN_BUDGETS: Final = 4096
_MAX_GRANT_TTL_S: Final = 600
_GRANT_MARGIN_S: Final = 30
OrgOverrides = Callable[[str], LLMSelection | None]


class AgentTaskFailedError(Exception):
    """An agent task ended without a usable result. `kind` and `reason` are codes, never message text."""

    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(kind)
        self.kind = kind
        self.reason = reason


class AgentLookup(Protocol):
    async def get_agent(self, name: str) -> RegisteredAgent | None: ...


class ModelSource(Protocol):
    """Where a task's model comes from: `choose` resolves it, `pinned` rebuilds a recorded one."""

    def choose(self, manifest: AgentManifest, task: AgentTask) -> ModelChoice: ...

    def pinned(self, manifest: AgentManifest, record: Mapping[str, Any]) -> ModelChoice: ...


class RuntimeModelSource:
    """`ModelSource` over LLM-1's runtime. The recorded `connector` and `model` are what a later attempt
    rebuilds (sampling params come from the manifest again; they are not recorded, see `task_record`)."""

    def __init__(self, runtime: LLMRuntime, org_overrides: OrgOverrides | None = None) -> None:
        self._runtime = runtime
        self._org_overrides = org_overrides

    @staticmethod
    def _selection(manifest: AgentManifest) -> LLMSelection:
        llm = manifest.llm
        return LLMSelection(alias=llm.alias, connector=llm.connector, model=llm.model, params=llm.params.values())

    def choose(self, manifest: AgentManifest, task: AgentTask) -> ModelChoice:
        override = self._org_overrides(task.org_id) if self._org_overrides is not None else None
        resolved, model = self._runtime.chat_model_for(manifest=self._selection(manifest), org_override=override)
        return ModelChoice(record=resolved.task_record(), model=model)

    def pinned(self, manifest: AgentManifest, record: Mapping[str, Any]) -> ModelChoice:
        selection = LLMSelection(
            connector=str(record["connector"]), model=str(record["model"]), params=manifest.llm.params.values()
        )
        _, model = self._runtime.chat_model_for(manifest=selection)
        return ModelChoice(record=record, model=model)


class AgentTaskExecutor:
    """Runs a task through its registered agent. See the module docstring."""

    def __init__(
        self,
        *,
        store: DataStore,
        agents: AgentLookup,
        models: ModelSource,
        tools: ToolSource,
        auditor: Auditor,
        token_key: str,
        grants: GrantIssuer | None,
        run_max_tokens: int,
        external: ExternalGuard | None = None,
    ) -> None:
        if run_max_tokens < 1:
            raise ValueError("run_max_tokens must be a positive number of tokens")
        self._runs = RunStore(store)
        self._agents = agents
        self._models = models
        self._tools = tools
        self._auditor = auditor
        self._token_key = token_key
        self._grants = grants
        self._run_max_tokens = run_max_tokens
        self._external = external
        self._budgets: OrderedDict[tuple[str, str], Budget] = OrderedDict()

    async def execute(self, work: TaskWork) -> None:
        run = await self._runs.get(work.run_id)
        if run is None:
            raise LookupError("no such run")
        try:
            template_task = _template_task(run, work.task_id)
            registered = await self._registered(work.agent)
            task = AgentTask(
                org_id=current_tenant().org_id,
                run_id=work.run_id,
                task_id=work.task_id,
                input=_agent_input(template_task, str(run["hire_id"])),
            )
            choice = await self._model(registered.manifest, task)
            budget = await self._run_budget(work.run_id)
            # The grant is issued last, right before the agent starts, so its lifetime is the task's.
            task = replace(task, grant=self._grant(work, template_task, str(run["hire_id"]), registered.manifest))
            runner = AgentRunner(
                models=lambda _manifest, _task: choice,
                tools=self._tools,
                auditor=self._auditor,
                token_key=self._token_key,
                external=self._external,
            )
            result = await runner.run(registered, task, run_budget=budget)
        except AgentTaskFailedError as error:
            await self._record_failure(work, error)
            raise
        except PyMongoError:
            raise  # transient: the bus retries the job, the task is not failed for it
        except Exception as error:  # any other error is a visible failure too, by type only
            failed = AgentTaskFailedError("error", type(error).__name__)
            await self._record_failure(work, failed)
            raise failed from None
        await self._finish(work, result)

    async def _record_failure(self, work: TaskWork, error: AgentTaskFailedError) -> None:
        await self._runs.record_outcome(
            work.run_id,
            work.task_id,
            result=None,
            failure={"kind": error.kind, "reason": error.reason},
            only_if=TaskState.RUNNING,
        )

    async def _registered(self, name: str) -> RegisteredAgent:
        found = await self._agents.get_agent(name)
        if found is None:
            raise AgentTaskFailedError("no_agent", name)
        if not found.active:
            raise AgentTaskFailedError("retired_agent", name)
        return found

    def _grant(
        self, work: TaskWork, template_task: Mapping[str, Any], hire_id: str, manifest: AgentManifest
    ) -> str | None:
        """A grant for exactly the template task's `pii_access`, or None when it declares none."""
        access = template_task.get("pii_access")
        if not access:
            return None
        if self._grants is None:
            raise AgentTaskFailedError("grant_refused", "no_grant_issuer")
        ttl = min(_MAX_GRANT_TTL_S, int(manifest.limits.timeout_s) + _GRANT_MARGIN_S)
        try:
            return self._grants.issue_grant(
                current_tenant().org_id,
                run_id=work.run_id,
                task_id=work.task_id,
                subject_id=hire_id,
                fields=[VaultField(name) for name in access["fields"]],
                sink=access["sink"],
                ttl=timedelta(seconds=ttl),
            )
        except (ValueError, CryptoError) as error:
            raise AgentTaskFailedError("grant_refused", type(error).__name__) from None

    async def _model(self, manifest: AgentManifest, task: AgentTask) -> ModelChoice:
        """The task's model: the recorded one if it has started before, else resolved now and recorded."""
        current = await self._runs.task(task.run_id, task.task_id)
        if current is None:
            raise LookupError("no such task in this run")
        recorded = current.get("llm")
        if recorded:
            return self._models.pinned(manifest, recorded)
        choice = self._models.choose(manifest, task)
        stored = await self._runs.record_model(task.run_id, task.task_id, choice.record)
        if dict(stored) != dict(choice.record):  # another delivery recorded first: its model wins
            return self._models.pinned(manifest, stored)
        return choice

    async def _run_budget(self, run_id: str) -> Budget:
        """The run's token budget, shared by its tasks in this process and rebuilt from the tasks'
        recorded usage when this process has not seen the run yet."""
        key = (current_tenant().org_id, run_id)
        existing = self._budgets.get(key)
        if existing is not None:
            self._budgets.move_to_end(key)
            return existing
        used = await self._runs.run_tokens(run_id)
        created = Budget(BudgetLimits(max_tokens=self._run_max_tokens))
        budget = self._budgets.setdefault(key, created)
        if budget is created and used:  # only the call that made it charges the recorded usage
            budget.charge(used)
        while len(self._budgets) > _MAX_RUN_BUDGETS:
            self._budgets.popitem(last=False)
        return budget

    async def _finish(self, work: TaskWork, result: AgentRunResult) -> None:
        failure = {"kind": result.failure.kind, "reason": result.failure.reason} if result.failure else None
        await self._runs.record_outcome(
            work.run_id, work.task_id, result=_stored(result), failure=failure, only_if=TaskState.RUNNING
        )
        if result.failure is not None:
            _log.warning(
                "agent task failed",
                extra={"run_id": work.run_id, "task_id": work.task_id, "failure_kind": result.failure.kind},
            )
            raise AgentTaskFailedError(result.failure.kind, result.failure.reason)


def _template_task(run: Mapping[str, Any], task_id: str) -> Mapping[str, Any]:
    for task in stored_dag(run)["tasks"]:
        if task["id"] == task_id:
            found: Mapping[str, Any] = task
            return found
    raise LookupError("the run's DAG has no such task")


def _agent_input(template_task: Mapping[str, Any], hire_id: str) -> dict[str, Any]:
    """What the agent is asked: the action, the template's inputs and the hire's id. Never a hire value."""
    return {
        "action": template_task["action"],
        "title": template_task["title"],
        "inputs": dict(template_task.get("inputs") or {}),
        "hire_id": hire_id,
    }


def _stored(result: AgentRunResult) -> dict[str, Any]:
    """The result as kept on the task: outcome, output, usage, model and the failure. The trajectory is
    left out (its length is kept); it belongs to the eval capture and the trace, not the task record."""
    full = result.to_dict()
    return {
        "outcome": full["outcome"],
        "output": full["output"],
        "failure": full["failure"],
        "refusal": full["refusal"],
        "llm": full["llm"],
        "usage": full["usage"],
        "trajectory_steps": len(full["trajectory"]),
    }
