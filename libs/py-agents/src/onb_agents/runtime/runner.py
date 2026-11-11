"""Run one task of any registered agent (PRD §7.1 Sub-agents and Budgets, §5.2, §8.1).

    runner = AgentRunner(models=runtime_models(llm_runtime), tools=HubToolSource(hub_url),
                         auditor=AuditLog(store), token_key=env["AGENT_TOKEN_KEY"])
    result = await runner.run(registered_agent, AgentTask(org_id, run_id, task_id, input, grant=grant))

For each task: the output contract for the agent (and mode); the model from LLM-1's runtime (its
`task_record()` is kept on the result, so a running task keeps its model); a fresh agent token scoped
to this task; the hub's tools the manifest names plus its local tools; a step/token/time budget from
the manifest's `limits` (under the run's budget, if given); every model call through GR-1's
`GuardedChatModel`, inside the task org's `tenant_scope`.

Outcomes are always visible, never silent:
- `completed` with the contract-validated `output` (`status: ok | refused | needs_human`);
- `completed` with a contract refusal when the injection guard blocks the input (`refusal` holds the code);
- `failed` with `failure.kind` = `budget_exceeded` | `invalid_output` | `guardrail_blocked` | `timeout`
  | `error`, and a reason made of codes and counts (or an exception type), never message text.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
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
_log = logging.getLogger("onb_agents.runtime")


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """The chat model for one task and the record to keep on the task (LLM-1 `task_record()`)."""

    record: Mapping[str, Any]
    model: BaseChatModel


ModelProvider = Callable[[AgentManifest], ModelChoice]


def runtime_models(runtime: LLMRuntime, org_override: LLMSelection | None = None) -> ModelProvider:
    """Models from LLM-1's runtime: manifest → org override → default."""

    def provide(manifest: AgentManifest) -> ModelChoice:
        llm = manifest.llm
        selection = LLMSelection(alias=llm.alias, connector=llm.connector, model=llm.model, params=llm.params.values())
        resolved, model = runtime.chat_model_for(manifest=selection, org_override=org_override)
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

    state: AgentState = field(default_factory=lambda: AgentState())
    failure: Failure | None = None
    refusal: str | None = None


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
        local = local_tools_for(manifest)
        choice = self._models(manifest)
        token = issue_task_token(manifest, task, self._token_key)
        limits = manifest.limits
        budget = Budget(
            BudgetLimits(max_steps=limits.max_steps, max_tokens=limits.max_tokens, max_seconds=limits.timeout_s),
            parent=run_budget,
        )
        context = GuardContext(agent=manifest.name, run_id=task.run_id, task_id=task.task_id)
        with tenant_scope(task.org_id):
            ended = await self._run_graph(manifest, contract, task, token, choice, local, budget, context)
        return _result(manifest, task, choice, budget, ended, contract)

    async def _run_graph(
        self,
        manifest: AgentManifest,
        contract: type[AgentOutput],
        task: AgentTask,
        token: str,
        choice: ModelChoice,
        local: Sequence[Any],
        budget: Budget,
        context: GuardContext,
    ) -> _Ended:
        try:
            async with self._tools.open(manifest, task, token) as toolset:
                return await self._drive(manifest, contract, task, toolset, choice, local, budget, context)
        except Exception as error:  # the hub session itself failed: a visible failure, never a silent one
            _log.warning("agent %s task %s: tool source failed: %s", manifest.name, task.task_id, type(error).__name__)
            return _Ended(failure=Failure("error", type(error).__name__))

    async def _drive(
        self,
        manifest: AgentManifest,
        contract: type[AgentOutput],
        task: AgentTask,
        toolset: ToolSet,
        choice: ModelChoice,
        local: Sequence[Any],
        budget: Budget,
        context: GuardContext,
    ) -> _Ended:
        bound = [*toolset.bound, *local]
        model = choice.model.bind_tools(bound) if bound else choice.model
        guard = GuardedChatModel(
            model=model, auditor=self._auditor, context=context, budget=budget, external=self._external
        )
        graph = build_agent_graph(
            guard=guard,
            tools=toolset,
            local=local,
            contract=contract,
            recorder=DecisionRecorder(self._auditor, context),
        )
        initial: AgentState = {"messages": initial_messages(manifest, contract, task), "trajectory": []}
        config: RunnableConfig = {"recursion_limit": recursion_limit(manifest.limits.max_steps)}
        last: AgentState = initial
        try:
            async with asyncio.timeout(manifest.limits.timeout_s):
                async for state in graph.astream(initial, config, stream_mode="values"):
                    last = cast(AgentState, state)
        except InjectionBlockedError:
            return _Ended(state=last, refusal=INJECTION_REFUSAL)
        except BudgetExceededError as error:
            return _Ended(state=last, failure=Failure("budget_exceeded", error.reason))
        except StructuredOutputError as error:
            return _Ended(state=last, failure=Failure("invalid_output", error.reason))
        except ExternalGuardBlockedError as error:
            return _Ended(state=last, failure=Failure("guardrail_blocked", error.reason))
        except TimeoutError:
            return _Ended(state=last, failure=Failure("timeout", f"seconds {manifest.limits.timeout_s}"))
        except Exception as error:
            _log.warning("agent %s task %s failed: %s", manifest.name, task.task_id, type(error).__name__)
            return _Ended(state=last, failure=Failure("error", type(error).__name__))
        return _Ended(state=last)


def _manifest(agent: AgentManifest | RegisteredAgent) -> AgentManifest:
    if isinstance(agent, RegisteredAgent):
        if not agent.active:
            raise ValueError(f"agent {agent.name} is retired")
        return agent.manifest
    return agent


def _result(
    manifest: AgentManifest,
    task: AgentTask,
    choice: ModelChoice,
    budget: Budget,
    ended: _Ended,
    contract: type[AgentOutput],
) -> AgentRunResult:
    output: Mapping[str, Any] | None = ended.state.get("output")
    if ended.refusal is not None:
        output = contract.refusal(ended.refusal).model_dump(mode="json", exclude_none=True)
    failed = ended.failure is not None or output is None
    failure = ended.failure or (Failure("error", "no_output") if output is None else None)
    return AgentRunResult(
        agent=manifest.name,
        run_id=task.run_id,
        task_id=task.task_id,
        outcome="failed" if failed else "completed",
        output=None if failed else output,
        failure=failure if failed else None,
        trajectory=tuple(ended.state.get("trajectory", ())),
        llm=dict(choice.record),
        usage=Usage(steps=budget.usage.steps, tokens=budget.usage.tokens),
        refusal=ended.refusal,
    )
