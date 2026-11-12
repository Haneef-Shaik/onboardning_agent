"""Run one task of any registered agent (PRD §7.1 Sub-agents and Budgets, §5.2, §8.1).

    runner = AgentRunner(models=runtime_models(llm_runtime), tools=HubToolSource(hub_url),
                         auditor=AuditLog(store), token_key=env["AGENT_TOKEN_KEY"])
    result = await runner.run(registered_agent, AgentTask(org_id, run_id, task_id, input, grant=grant))

For each task: the output contract for the agent (and mode); the model from LLM-1's runtime (its
`task_record()` is kept on the result, so a running task keeps its model); a fresh agent token scoped
to this task; the hub's tools the manifest names plus its local tools; a step/token/time budget from
the manifest's `limits` (under the run's budget, if given); every model call through GR-1's
`GuardedChatModel`, inside the task org's `tenant_scope`. One deadline (the budget's remaining time,
so the run's too) bounds the whole task: the hub connection, every model call and every tool call.

Outcomes are always visible, never silent:
- `completed` with the contract-validated `output` (`status: ok | refused | needs_human`);
- `completed` with a contract refusal when the injection guard blocks the input (`refusal` holds the code);
- `failed` with `failure.kind` = `budget_exceeded` (steps or tokens) | `timeout` (any time limit) |
  `invalid_output` | `guardrail_blocked` | `error`, and a reason made of codes and counts (or an
  exception type), never message text. The trajectory up to the failure is kept.

A caller error (a retired agent, a mode or agent with no contract, an approval the manifest does not
require, a missing local tool or LLM connector) raises before the task starts: it is the orchestrator's
bug, not the agent's outcome.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from onb_contracts.agent_outputs import AgentOutput, contract_for
from onb_data import Auditor, tenant_scope
from onb_guardrails import (
    Budget,
    BudgetExceededError,
    BudgetLimits,
    DecisionRecorder,
    ExternalGuard,
    ExternalGuardBlockedError,
    GuardContext,
    GuardedChatModel,
    InjectionBlockedError,
    StructuredOutputError,
)
from onb_llm import LLMRuntime, LLMSelection
from onb_telemetry import span_attributes
from opentelemetry import trace

from onb_agents.registry import AgentManifest, RegisteredAgent
from onb_agents.runtime.graph import AgentState, build_agent_graph, recursion_limit
from onb_agents.runtime.local_tools import local_tools_for
from onb_agents.runtime.prompt import initial_messages
from onb_agents.runtime.task import AgentTask, issue_task_token
from onb_agents.runtime.tools import ToolSet, ToolSource
from onb_agents.runtime.trajectory import TrajectoryStep

__all__ = [
    "AgentRunResult",
    "AgentRunner",
    "Failure",
    "FailureKind",
    "ModelChoice",
    "ModelProvider",
    "Usage",
    "runtime_models",
]

FailureKind = Literal["budget_exceeded", "invalid_output", "guardrail_blocked", "timeout", "error"]
INJECTION_REFUSAL: Final = "injection_blocked"
AGENT_SPAN: Final = "agent.run"
_log = logging.getLogger("onb_agents.runtime")


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """The chat model for one task and the record to keep on the task (LLM-1 `task_record()`)."""

    record: Mapping[str, Any]
    model: BaseChatModel


ModelProvider = Callable[[AgentManifest, AgentTask], ModelChoice]
OrgOverrides = Callable[[str], LLMSelection | None]


def runtime_models(runtime: LLMRuntime, org_overrides: OrgOverrides | None = None) -> ModelProvider:
    """Models from LLM-1's runtime: manifest → the task's org override → default."""

    def provide(manifest: AgentManifest, task: AgentTask) -> ModelChoice:
        llm = manifest.llm
        selection = LLMSelection(alias=llm.alias, connector=llm.connector, model=llm.model, params=llm.params.values())
        override = org_overrides(task.org_id) if org_overrides is not None else None
        resolved, model = runtime.chat_model_for(manifest=selection, org_override=override)
        return ModelChoice(record=resolved.task_record(), model=model)

    return provide


@dataclass(frozen=True, slots=True)
class Failure:
    kind: FailureKind
    reason: str


@dataclass(frozen=True, slots=True)
class Usage:
    steps: int
    tokens: int


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    agent: str
    run_id: str
    task_id: str
    outcome: Literal["completed", "failed"]
    output: Mapping[str, Any] | None
    failure: Failure | None
    trajectory: tuple[TrajectoryStep, ...]
    llm: Mapping[str, Any]
    usage: Usage
    refusal: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Plain data for the task record and the eval capture. Holds no token, grant or tool result."""
        return {
            "agent": self.agent,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "outcome": self.outcome,
            "output": dict(self.output) if self.output is not None else None,
            "failure": {"kind": self.failure.kind, "reason": self.failure.reason} if self.failure else None,
            "refusal": self.refusal,
            "trajectory": [step.to_dict() for step in self.trajectory],
            "llm": dict(self.llm),
            "usage": {"steps": self.usage.steps, "tokens": self.usage.tokens},
        }


@dataclass(frozen=True, slots=True)
class _Ended:
    """How the graph ended: its last state, and the failure or refusal if it did not finish."""

    state: AgentState
    failure: Failure | None = None
    refusal: str | None = None


@dataclass(frozen=True, slots=True)
class _Prepared:
    """Everything one task needs, resolved before it starts."""

    manifest: AgentManifest
    contract: type[AgentOutput]
    task: AgentTask
    token: str
    choice: ModelChoice
    local: tuple[BaseTool, ...]
    budget: Budget
    context: GuardContext


class _Progress:
    """The latest graph state. Mutable on purpose: an MCP transport failure cancels the graph from
    inside the session's task group, so the state streamed so far must outlive that cancellation."""

    def __init__(self, state: AgentState) -> None:
        self.state = state


def _leaf(error: BaseException) -> BaseException:
    """The first real cause inside (nested) exception groups; cancellations only if nothing else."""
    if isinstance(error, BaseExceptionGroup):
        leaves = [_leaf(inner) for inner in error.exceptions]
        real = [leaf for leaf in leaves if not isinstance(leaf, asyncio.CancelledError)]
        return (real or leaves)[0]
    return error


def _classify(error: BaseException, budget: Budget) -> tuple[Failure | None, str | None]:
    """(failure, refusal) for an error that ended the graph."""
    if isinstance(error, InjectionBlockedError):
        return None, INJECTION_REFUSAL
    if isinstance(error, BudgetExceededError):
        kind: FailureKind = "timeout" if "seconds" in error.reason else "budget_exceeded"
        return Failure(kind, error.reason), None
    if isinstance(error, StructuredOutputError):
        return Failure("invalid_output", error.reason), None
    if isinstance(error, ExternalGuardBlockedError):
        return Failure("guardrail_blocked", error.reason), None
    if isinstance(error, TimeoutError):
        return Failure("timeout", budget.exceeded(before_call=False) or "seconds"), None
    return Failure("error", type(error).__name__), None


class AgentRunner:
    def __init__(
        self,
        *,
        models: ModelProvider,
        tools: ToolSource,
        auditor: Auditor,
        token_key: str,
        external: ExternalGuard | None = None,
    ) -> None:
        self._models = models
        self._tools = tools
        self._auditor = auditor
        self._token_key = token_key
        self._external = external

    async def run(
        self, agent: AgentManifest | RegisteredAgent, task: AgentTask, *, run_budget: Budget | None = None
    ) -> AgentRunResult:
        manifest = _manifest(agent)
        contract = contract_for(manifest.name, task.mode)
        local = tuple(local_tools_for(manifest))
        token = issue_task_token(manifest, task, self._token_key)
        choice = self._models(manifest, task)
        limits = manifest.limits
        budget = Budget(
            BudgetLimits(max_steps=limits.max_steps, max_tokens=limits.max_tokens, max_seconds=limits.timeout_s),
            parent=run_budget,
        )
        context = GuardContext(agent=manifest.name, run_id=task.run_id, task_id=task.task_id)
        prepared = _Prepared(manifest, contract, task, token, choice, local, budget, context)
        attributes = span_attributes(
            org_id=task.org_id, run_id=task.run_id, task_id=task.task_id, agent_name=manifest.name
        )
        # One span per agent task (run → task → agent → LLM/tool, PRD §7.7); ids only, never a prompt.
        with (
            tenant_scope(task.org_id),
            trace.get_tracer("onb_agents").start_as_current_span(AGENT_SPAN, attributes=attributes),
        ):
            ended = await self._run_graph(prepared)
        return _result(prepared, ended)

    async def _run_graph(self, p: _Prepared) -> _Ended:
        progress = _Progress({"messages": initial_messages(p.manifest, p.contract, p.task), "trajectory": []})
        try:
            async with asyncio.timeout(p.budget.remaining_seconds()):
                async with self._tools.open(p.manifest, p.task, p.token) as toolset:
                    await self._drive(p, toolset, progress)
        except (Exception, BaseExceptionGroup) as error:  # every ending becomes a visible result
            cause = _leaf(error)
            if isinstance(cause, asyncio.CancelledError):
                raise  # cancelled from outside: not this task's outcome
            failure, refusal = _classify(cause, p.budget)
            if failure is not None and failure.kind == "error":
                _log.warning("agent %s task %s failed: %s", p.manifest.name, p.task.task_id, failure.reason)
            return _Ended(progress.state, failure, refusal)
        schema_failure = progress.state.get("schema_failure")
        if schema_failure:
            return _Ended(progress.state, Failure("invalid_output", schema_failure))
        return _Ended(progress.state)

    async def _drive(self, p: _Prepared, toolset: ToolSet, progress: _Progress) -> None:
        bound = [*toolset.bound, *p.local]
        model = p.choice.model.bind_tools(bound) if bound else p.choice.model
        guard = GuardedChatModel(
            model=model, auditor=self._auditor, context=p.context, budget=p.budget, external=self._external
        )
        graph = build_agent_graph(
            guard=guard,
            tools=toolset,
            local=p.local,
            contract=p.contract,
            recorder=DecisionRecorder(self._auditor, p.context),
        )
        config: RunnableConfig = {"recursion_limit": recursion_limit(p.manifest.limits.max_steps)}
        async for state in graph.astream(progress.state, config, stream_mode="values"):
            progress.state = cast(AgentState, state)


def _manifest(agent: AgentManifest | RegisteredAgent) -> AgentManifest:
    if isinstance(agent, RegisteredAgent):
        if not agent.active:
            raise ValueError(f"agent {agent.name} is retired")
        return agent.manifest
    return agent


def _result(p: _Prepared, ended: _Ended) -> AgentRunResult:
    output: Mapping[str, Any] | None = ended.state.get("output")
    if ended.refusal is not None:
        output = p.contract.refusal(ended.refusal).model_dump(mode="json", exclude_none=True)
    failed = ended.failure is not None or output is None
    failure = ended.failure or (Failure("error", "no_output") if output is None else None)
    return AgentRunResult(
        agent=p.manifest.name,
        run_id=p.task.run_id,
        task_id=p.task.task_id,
        outcome="failed" if failed else "completed",
        output=None if failed else output,
        failure=failure if failed else None,
        trajectory=tuple(ended.state.get("trajectory", ())),
        llm=dict(p.choice.record),
        usage=Usage(steps=p.budget.usage.steps, tokens=p.budget.usage.tokens),
        refusal=ended.refusal,
    )
