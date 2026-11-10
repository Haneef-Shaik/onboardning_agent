"""Runs cases (evals/README.md "How a case runs"): for every repetition, a fresh database with the fixture,
the case's setup, the agent, the capture, then every check. A case passes only if all checks pass.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Final, Protocol

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from onb_data import DataStore, tenant_scope
from pydantic import BaseModel

from onb_evals.capture import AuditEntry, CaseCapture
from onb_evals.cases import EvalCase
from onb_evals.checks import run_check
from onb_evals.context import CheckContext
from onb_evals.contracts import DEFAULT_CONTRACTS
from onb_evals.fixture import LoadedFixture, StoreState, apply_setup, load_fixture
from onb_evals.llm_capture import LLMCapture
from onb_evals.metrics import CaseResult, CheckOutcome, RepetitionResult
from onb_evals.spans import EvalSpans, case_span
from onb_evals.tokens import TokenMap
from onb_evals.toolbox import AUDIT_COLLECTION, ToolBox, audit_from_documents

__all__ = ["AgentAdapter", "AgentInvocation", "DatabaseProvider", "EvalRunner", "LLMProvider"]

TRAJECTORY_CHECKS: Final = frozenset({"tool_called", "tool_not_called"})
_MAX_ERROR_CHARS: Final = 300


class DatabaseProvider(Protocol):
    def fresh(self) -> AbstractAsyncContextManager[DataStore]:
        """An empty database for one repetition, dropped when the block exits."""
        ...


class LLMProvider(Protocol):
    label: str

    def chat_model(self, callbacks: Sequence[BaseCallbackHandler], tokens: TokenMap) -> BaseChatModel: ...


@dataclass(frozen=True, slots=True)
class AgentInvocation:
    case: EvalCase
    input: Mapping[str, Any]
    tools: ToolBox
    chat_model: BaseChatModel
    repetition: int


class AgentAdapter(Protocol):
    async def invoke(self, invocation: AgentInvocation) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class _Ran:
    capture: CaseCapture
    checks: tuple[CheckOutcome, ...]


class EvalRunner:
    def __init__(
        self,
        *,
        databases: DatabaseProvider,
        seed: Mapping[str, Any],
        adapter_for: Callable[[EvalCase], AgentAdapter],
        llm_for: Callable[[EvalCase], LLMProvider],
        spans: EvalSpans | None = None,
        contracts: Mapping[str, tuple[str, type[BaseModel]]] = DEFAULT_CONTRACTS,
    ) -> None:
        self._databases = databases
        self._seed = seed
        self._adapter_for = adapter_for
        self._llm_for = llm_for
        self._spans = spans
        self._contracts = contracts
        self._orgs = [str(org["_id"]) for org in seed.get("orgs", [])]

    async def run(self, cases: Sequence[EvalCase], *, repeat: int, run_id: str) -> tuple[CaseResult, ...]:
        if repeat < 1:
            raise ValueError("repeat must be at least 1")
        results = []
        for case in cases:
            reps = tuple([await self._repetition(case, rep, run_id) for rep in range(repeat)])
            results.append(CaseResult(case.id, case.agent, case.category, reps))
        return tuple(results)

    async def capture_once(self, case: EvalCase, repetition: int, run_id: str) -> CaseCapture:
        return (await self._execute(case, repetition, run_id)).capture

    async def _repetition(self, case: EvalCase, repetition: int, run_id: str) -> RepetitionResult:
        ran = await self._execute(case, repetition, run_id)
        trajectory = [c for c in ran.checks if c.type in TRAJECTORY_CHECKS]
        capture = ran.capture
        return RepetitionResult(
            repetition=repetition,
            passed=capture.error is None and all(c.passed for c in ran.checks),
            checks=ran.checks,
            steps=capture.steps,
            input_tokens=capture.input_tokens,
            output_tokens=capture.output_tokens,
            latency_ms=capture.latency_ms,
            tool_calls_ok=sum(c.passed for c in trajectory),
            tool_calls_checked=len(trajectory),
            error=capture.error,
        )

    async def _execute(self, case: EvalCase, repetition: int, run_id: str) -> _Ran:
        async with self._databases.fresh() as store:
            fixture = await load_fixture(store, self._seed)
            await apply_setup(store, case, fixture.tokens)
            capture = await self._invoke(store, fixture, case, repetition, run_id)
            context = CheckContext(
                case=case,
                capture=capture,
                state=StoreState(store, fixture.tokens),
                vault=fixture.vault,
                facts=fixture.facts,
                contracts=self._contracts,
            )
            checks = []
            for spec in case.checks:
                result = await run_check(spec, context)
                checks.append(CheckOutcome(spec.type, result.passed, result.detail))
        return _Ran(capture, tuple(checks))

    async def _invoke(
        self, store: DataStore, fixture: LoadedFixture, case: EvalCase, repetition: int, run_id: str
    ) -> CaseCapture:
        tokens = fixture.tokens
        tools = ToolBox(store, case.actor, tokens)
        llm = LLMCapture()
        key = (run_id, case.id, repetition)
        output: Mapping[str, Any] | None = None
        error: str | None = None
        started = time.perf_counter()
        with case_span(key, agent=case.agent, org_id=case.actor.org_id):
            try:
                invocation = AgentInvocation(
                    case=case,
                    input=tokens.to_real(dict(case.input)),
                    tools=tools,
                    chat_model=self._llm_for(case).chat_model([llm], tokens),
                    repetition=repetition,
                )
                returned = await self._adapter_for(case).invoke(invocation)
                if not isinstance(returned, Mapping):
                    raise TypeError("the agent's output is not a JSON object")
                output = dict(returned)
            except Exception as failure:  # the agent's failure is a failed repetition, not a crashed run
                error = f"{type(failure).__name__}: {failure}"[:_MAX_ERROR_CHARS]
        latency_ms = (time.perf_counter() - started) * 1000
        return CaseCapture(
            output=tokens.to_seed(dict(output)) if output is not None else None,
            tool_calls=tuple(type(c)(c.name, tokens.to_seed(dict(c.args)), c.outcome) for c in tools.calls),
            llm_inputs=tuple(tokens.to_seed(llm.inputs)),
            steps=llm.steps,
            input_tokens=llm.input_tokens,
            output_tokens=llm.output_tokens,
            latency_ms=latency_ms,
            audit=await self._audit(store, tokens),
            trace=tuple(tokens.to_seed(list(self._spans.collect(key)))) if self._spans else (),
            error=error,
        )

    async def _audit(self, store: DataStore, tokens: TokenMap) -> tuple[AuditEntry, ...]:
        """The MCP audit entries of this run, read back from every org's `audit_log`."""
        entries: list[AuditEntry] = []
        for org_id in self._orgs:
            with tenant_scope(org_id):
                documents = await store.tenant(AUDIT_COLLECTION).find({"kind": "mcp_call"})
            entries.extend(audit_from_documents(tokens.to_seed(documents)))
        return tuple(entries)
