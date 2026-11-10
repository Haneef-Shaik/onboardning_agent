"""`GuardedChatModel`: every LLM input, output and budget checked, connector agnostic (PRD §5.3, §8.1).

It wraps whatever the connector returned (a `BaseChatModel`, or one with tools bound), so the same
guards run on Bedrock, any LangChain model and the `fake` connector:

    guard = GuardedChatModel(model=chat_model.bind_tools(tools), auditor=AuditLog(store),
                             context=GuardContext(agent="db_agent", run_id=run.id, task_id=task.id),
                             budget=Budget(limits, parent=run_budget))
    reply = await guard.ainvoke(messages)                     # an agent loop step
    verdict = await guard.ainvoke_structured(messages, ApprovalVerdict)

One `ainvoke` is, in order: budget check (before), injection scan of untrusted messages, PII
redaction of every message, delimiting, the external guard on the input (if any), the model call
(bounded by the time left), budget charge and check (after), output PII scan, the external guard on
the output (if any). Each step is a recorded decision (`DecisionRecorder`); a blocking decision raises a
`GuardrailError` subclass after it is recorded. All of it runs inside a `guardrails.invoke` span.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from typing import Final

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import Runnable
from onb_data import Auditor
from opentelemetry import trace
from pydantic import BaseModel

from onb_guardrails.budget import Budget
from onb_guardrails.decision import (
    BudgetExceededError,
    ExternalGuardBlockedError,
    GuardrailCheck,
    GuardrailDecision,
    GuardrailOutcome,
    InjectionBlockedError,
    StructuredOutputError,
)
from onb_guardrails.external import ExternalGuard, Source
from onb_guardrails.messages import (
    InputScan,
    guard_input,
    output_texts,
    pii_reason,
    scan_output,
    trusted_message,
)
from onb_guardrails.recorder import DecisionRecorder, GuardContext
from onb_guardrails.structured import SchemaErrors, parse_structured, repair_prompt

INVOKE_SPAN: Final = "guardrails.invoke"
STRUCTURED_SPAN: Final = "guardrails.structured"
_CHARS_PER_TOKEN: Final = 4


def _tracer() -> trace.Tracer:
    """Looked up per call: a module-level proxy would stay bound to the first provider it resolved."""
    return trace.get_tracer("onb_guardrails")


class GuardedChatModel:
    def __init__(
        self,
        *,
        model: Runnable[LanguageModelInput, BaseMessage],
        auditor: Auditor,
        context: GuardContext,
        budget: Budget,
        external: ExternalGuard | None = None,
    ) -> None:
        self._model = model
        self._context = context
        self._budget = budget
        self._external = external
        self._recorder = DecisionRecorder(auditor, context)

    @property
    def budget(self) -> Budget:
        return self._budget

    async def ainvoke(self, messages: Sequence[BaseMessage]) -> AIMessage:
        """One guarded model call; returns the redacted reply or raises a `GuardrailError`."""
        with _tracer().start_as_current_span(INVOKE_SPAN, attributes=self._context.span_attributes()):
            await self._check_budget(before_call=True)
            scan = guard_input(messages)
            await self._check_input(scan)
            reply = await self._call(scan.messages)
            await self._check_budget(before_call=False)
            return await self._check_output(reply)

    async def ainvoke_structured[M: BaseModel](self, messages: Sequence[BaseMessage], schema: type[M]) -> M:
        """A reply validated against `schema`: one repair attempt, then `StructuredOutputError`."""
        with _tracer().start_as_current_span(STRUCTURED_SPAN, attributes=self._context.span_attributes()):
            reply = await self.ainvoke(messages)
            first = parse_structured(reply.text, schema)
            if not isinstance(first, SchemaErrors):
                await self._record(GuardrailCheck.OUTPUT_SCHEMA, GuardrailOutcome.PASSED)
                return first
            repair = [*messages, reply, trusted_message(repair_prompt(schema, first))]
            second = parse_structured((await self.ainvoke(repair)).text, schema)
            if not isinstance(second, SchemaErrors):
                reason = f"repaired; first={first.first_type}"
                await self._record(GuardrailCheck.OUTPUT_SCHEMA, GuardrailOutcome.REPAIRED, reason)
                return second
            reason = f"attempts=2; errors={len(second.errors)}; first={second.first_type}"
            await self._record(GuardrailCheck.OUTPUT_SCHEMA, GuardrailOutcome.FAILED, reason)
            raise StructuredOutputError(schema.__name__, reason)

    async def _record(self, check: GuardrailCheck, outcome: GuardrailOutcome, reason: str | None = None) -> None:
        await self._recorder.record(GuardrailDecision(check, outcome, reason))

    async def _check_budget(self, *, before_call: bool) -> None:
        reason = self._budget.exceeded(before_call=before_call)
        if reason is None:
            await self._record(GuardrailCheck.BUDGET, GuardrailOutcome.PASSED)
            return
        await self._record(GuardrailCheck.BUDGET, GuardrailOutcome.BLOCKED, reason)
        raise BudgetExceededError(reason)

    async def _check_input(self, scan: InputScan) -> None:
        if scan.injection_patterns:
            reason = f"patterns={','.join(scan.injection_patterns)}; sources={','.join(scan.injection_sources)}"
            await self._record(GuardrailCheck.INPUT_INJECTION, GuardrailOutcome.BLOCKED, reason)
            raise InjectionBlockedError(scan.injection_patterns)
        await self._record(GuardrailCheck.INPUT_INJECTION, GuardrailOutcome.PASSED)
        if scan.pii_counts:
            await self._record(GuardrailCheck.INPUT_PII, GuardrailOutcome.REDACTED, pii_reason(scan.pii_counts))
        else:
            await self._record(GuardrailCheck.INPUT_PII, GuardrailOutcome.PASSED)
        await self._external_check(scan.untrusted_texts, GuardrailCheck.EXTERNAL_INPUT, "INPUT")

    async def _call(self, messages: Sequence[BaseMessage]) -> AIMessage:
        try:
            async with asyncio.timeout(self._budget.remaining_seconds()) as deadline:
                reply = await self._model.ainvoke(list(messages))
        except TimeoutError:
            if not deadline.expired():
                self._budget.charge(_estimate(messages, None))
                raise  # the provider's own timeout (e.g. a socket read), not the time budget
            self._budget.charge(0)
            reason = self._budget.exceeded(before_call=False) or "seconds timeout"
            await self._record(GuardrailCheck.BUDGET, GuardrailOutcome.BLOCKED, reason)
            raise BudgetExceededError(reason) from None
        except BaseException:
            self._budget.charge(_estimate(messages, None))  # a failed call still used a step
            raise
        self._budget.charge(_tokens(messages, reply))
        if not isinstance(reply, AIMessage):
            raise TypeError(f"the model returned {type(reply).__name__}, not an AIMessage")
        return reply

    async def _check_output(self, reply: AIMessage) -> AIMessage:
        redacted, counts = scan_output(reply)
        if counts:
            await self._record(GuardrailCheck.OUTPUT_PII, GuardrailOutcome.REDACTED, pii_reason(counts))
        else:
            await self._record(GuardrailCheck.OUTPUT_PII, GuardrailOutcome.PASSED)
        await self._external_check(output_texts(redacted), GuardrailCheck.EXTERNAL_OUTPUT, "OUTPUT")
        return redacted

    async def _external_check(self, texts: tuple[str, ...], check: GuardrailCheck, source: Source) -> None:
        guard = self._external
        if guard is None or not texts:
            return  # nothing to check is not a decision
        try:
            async with asyncio.timeout(self._budget.remaining_seconds()):
                verdict = await guard.check(texts, source=source)
        except Exception as error:  # fail closed: an unreachable guard blocks the call
            reason = f"error={type(error).__name__}"
            await self._record(check, GuardrailOutcome.FAILED, reason)
            raise ExternalGuardBlockedError(guard.name, reason) from None
        if verdict.intervened:
            reason = verdict.reason or "intervened"
            await self._record(check, GuardrailOutcome.BLOCKED, reason)
            raise ExternalGuardBlockedError(guard.name, reason)
        await self._record(check, GuardrailOutcome.PASSED)


def _tokens(messages: Sequence[BaseMessage], reply: BaseMessage) -> int:
    """The provider's token count, never less than its input + output figures or the estimate.

    A missing, negative or implausibly small count cannot slip a call past the token budget.
    """
    usage = getattr(reply, "usage_metadata", None) or {}
    total = _count(usage.get("total_tokens"))
    parts = _count(usage.get("input_tokens")) + _count(usage.get("output_tokens"))
    return max(total, parts, _estimate(messages, reply))


def _count(value: object) -> int:
    """A reported token figure, or 0 when it is missing, not an integer or negative."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _estimate(messages: Sequence[BaseMessage], reply: BaseMessage | None) -> int:
    """About four characters per token over the input and the reply."""
    characters = sum(len(message.text) for message in messages) + (len(reply.text) if reply is not None else 0)
    return math.ceil(characters / _CHARS_PER_TOKEN)
