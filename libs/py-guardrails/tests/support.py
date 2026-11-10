"""Test helpers: a scripted fake LLM (the `fake` connector), an in-memory audit log and a guarded model."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
from onb_data import tenant_scope
from onb_data.testing import InMemoryAuditLog
from onb_guardrails import Budget, BudgetLimits, ExternalGuard, GuardContext, GuardedChatModel
from onb_llm.connectors.fake import FakeConnector, ScriptedChatModel

ORG = "org_acme"
CONTEXT = GuardContext(agent="run_assistant", run_id="run_1", task_id="task_1")
# Synthetic values from the PII-3 corpus: a Verhoeff-valid Aadhaar and a well-formed PAN.
AADHAAR = "9999 0000 0125"
PAN = "ABCDE1234F"


def fake_model(*turns: dict[str, Any]) -> ScriptedChatModel:
    model = FakeConnector().create_chat_model("fake-standard", {"script": list(turns)})
    assert isinstance(model, ScriptedChatModel)
    return model


def guarded(
    model: Runnable[LanguageModelInput, BaseMessage],
    audit: InMemoryAuditLog,
    *,
    limits: BudgetLimits | None = None,
    budget: Budget | None = None,
    external: ExternalGuard | None = None,
) -> GuardedChatModel:
    return GuardedChatModel(
        model=model,
        auditor=audit,
        context=CONTEXT,
        budget=budget or Budget(limits or BudgetLimits(max_steps=10, max_tokens=10_000, max_seconds=60)),
        external=external,
    )


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run `coro` inside the test org's tenant scope (the audit log needs one)."""

    async def scoped() -> T:
        with tenant_scope(ORG):
            return await coro

    return asyncio.run(scoped())


def sent_texts(model: ScriptedChatModel) -> list[str]:
    """Every message text the fake model was sent, across all calls."""
    return [message.text for call in model.calls() for message in call.messages]


def audited(audit: InMemoryAuditLog) -> list[tuple[str, str, str | None]]:
    """(action, outcome, reason) of every audit record in the test org, oldest first."""

    async def read() -> list[tuple[str, str, str | None]]:
        from onb_data import AuditQuery

        with tenant_scope(ORG):
            page = await audit.list(AuditQuery(), page=1, limit=200)
        return [(e.action, str(e.outcome), e.reason) for e in reversed(page.entries)]

    return asyncio.run(read())


def last_messages(model: ScriptedChatModel) -> tuple[BaseMessage, ...]:
    return model.calls()[-1].messages
