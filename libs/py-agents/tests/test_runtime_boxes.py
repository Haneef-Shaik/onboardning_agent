"""AG-1 Done-when boxes 1-3, end to end: the agent runtime → the real MCP hub (HUB-1) in process,
with every model call made through LLM-1's runtime on the `fake` connector and GR-1's guards."""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from onb_agents.registry import AgentManifest
from onb_agents.runtime import AgentRunner, AgentRunResult, AgentTask, HubToolSource
from onb_agents.runtime.runner import ModelChoice
from onb_data import AuditOutcome, tenant_scope
from onb_data.audit import AuditEntry, AuditQuery
from pydantic import PrivateAttr

from .runtime_support import HUB_URL, MANIFESTS, TOKEN_KEY, FakeModels, HubStack, final, tool_turn

DB = MANIFESTS["db_agent"]
FIND = {"collection": "run_tasks", "filter": {"status": "open"}, "limit": 10}
INSERT = {"collection": "run_tasks", "document": {"status": "new"}}


def task(**kw: Any) -> AgentTask:
    base: dict[str, Any] = {
        "org_id": "org_acme",
        "run_id": "run_acme_001",
        "task_id": "collect_docs",
        "input": {"message": "Which tasks are open?"},
    }
    return AgentTask(**{**base, **kw})


def run(stack: HubStack, models: Any, manifest: AgentManifest, the_task: AgentTask) -> AgentRunResult:
    runner = AgentRunner(
        models=models, tools=HubToolSource(HUB_URL, client=stack.client), auditor=stack.audit, token_key=TOKEN_KEY
    )

    async def go() -> AgentRunResult:
        async with stack.running():
            return await runner.run(manifest, the_task)

    return asyncio.run(go())


def records(stack: HubStack, org: str = "org_acme") -> list[AuditEntry]:
    async def page() -> list[AuditEntry]:
        with tenant_scope(org):
            return list((await stack.audit.list(AuditQuery(), page=1, limit=200)).entries)

    return asyncio.run(page())


def widened(manifest: AgentManifest, tool: str) -> AgentManifest:
    """The manifest the runtime was handed, with one more Mongo tool than the registry holds."""
    mongo = manifest.mcp["mongo"]
    return manifest.model_copy(update={"mcp": {"mongo": mongo.model_copy(update={"tools": (*mongo.tools, tool)})}})


# --- box 1 ---------------------------------------------------------------------------------------


def test_box1_a_tool_outside_the_manifest_is_denied_by_the_hub_and_the_denial_is_in_the_trajectory(
    tmp_path: Path,
) -> None:
    """With the `fake` connector: the runtime's copy of the manifest lists `insert_one` (bound, scoped
    into the token), but the registry's db_agent does not. The hub is the authority: it denies the call."""
    stack = HubStack({"db_agent": DB})
    models = FakeModels(
        [tool_turn("mongo__insert_one", INSERT), final({"status": "ok", "answer": {"done": False}})], tmp_path
    )

    result = run(stack, models, widened(DB, "insert_one"), task())

    [step] = [s for s in result.trajectory if s.kind == "tool"]
    assert (step.name, step.outcome, step.reason) == ("mongo__insert_one", "denied", "tool_not_in_manifest")
    assert step.args == INSERT
    assert stack.downstream.sent == []  # denied before anything was forwarded
    [hub_record] = [r for r in records(stack) if r.action == "mcp.tool_call"]
    assert (hub_record.outcome, hub_record.reason) == (AuditOutcome.DENIED, "tool_not_in_manifest")
    assert hub_record.actor.id == "db_agent"
    [_, second] = models.built[0].calls()
    assert any("denied (tool_not_in_manifest)" in m.text for m in second.messages)  # the model saw the denial
    assert result.outcome == "completed"
    assert [s.kind for s in result.trajectory] == ["llm", "tool", "llm"]


class _UnboundCaller(BaseChatModel):
    """A model that names tools it was never given (as real models sometimes do). The fake connector
    refuses scripts like that, so this one stands in for it."""

    turns: list[AIMessage]
    _cursor: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "unbound-caller"

    def bind_tools(
        self, tools: Sequence[Any], *, tool_choice: str | None = None, **kwargs: Any
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self  # tools are ignored: it calls whatever its turns name

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> ChatResult:
        reply = self.turns[self._cursor]
        self._cursor += 1
        return ChatResult(generations=[ChatGeneration(message=reply)])


def test_box1_a_tool_the_agent_was_never_given_is_still_sent_to_the_hub_and_denied(tmp_path: Path) -> None:
    turns = [
        AIMessage(content="", tool_calls=[{"name": "mongo__insert_one", "args": INSERT, "id": "c1"}]),
        AIMessage(content="", tool_calls=[{"name": "mongo__drop", "args": {}, "id": "c2"}]),
        AIMessage(content=json.dumps({"status": "ok", "answer": {}})),
    ]
    model = _UnboundCaller(turns=turns)
    stack = HubStack({"db_agent": DB})

    result = run(stack, lambda _m, _t: ModelChoice(record={"connector": "test"}, model=model), DB, task())

    tools = [(s.name, s.outcome, s.reason) for s in result.trajectory if s.kind == "tool"]
    assert tools == [("mongo__insert_one", "denied", "tool_not_in_manifest"), ("mongo__drop", "denied", "unknown_tool")]
    reasons = [r.reason or "" for r in records(stack) if r.action == "mcp.tool_call"]
    assert sorted(reasons) == ["tool_not_in_manifest", "unknown_tool"]  # both reached the hub and were audited
    assert stack.downstream.sent == []


# --- box 2 ---------------------------------------------------------------------------------------


def test_box2_the_grant_and_the_agent_token_never_appear_in_captured_llm_input(tmp_path: Path) -> None:
    grant = "eyJhbGciOiJFZERTQSJ9." + secrets.token_urlsafe(48)
    stack = HubStack({"db_agent": DB}, documents=[{"_id": "t1", "status": "open", "note": "orientation slot"}])
    models = FakeModels([tool_turn("mongo__find", FIND), final({"status": "ok", "answer": {"open": ["t1"]}})], tmp_path)

    result = run(stack, models, DB, task(grant=grant))

    # The token and the grant did travel, out of band, to the hub ...
    tool_posts = [h for h in stack.headers_seen if "authorization" in h]
    assert tool_posts, "the agent reached the hub"
    token = tool_posts[0]["authorization"].removeprefix("Bearer ")
    assert len(token) > 100
    assert all(h.get("x-onb-pii-grant") == grant for h in tool_posts)
    # ... the tool result reached the model, so the capture is live ...
    sent = models.sent_texts()
    assert len(sent) == 4  # two calls, each seen by the fake's log and by the callback
    assert any("orientation slot" in text for text in sent)
    # ... and neither the token nor the grant (nor a piece of either) was ever sent to the model.
    for secret in (token, grant, *token.split("."), *grant.split(".")):
        for text in sent:
            assert secret not in text
    kept = json.dumps(result.to_dict())
    assert token not in kept
    assert grant not in kept
    assert result.outcome == "completed"


# --- box 3 ---------------------------------------------------------------------------------------


def test_box3_an_exhausted_step_budget_ends_the_run_with_a_visible_failure(tmp_path: Path) -> None:
    tight = DB.model_copy(update={"limits": DB.limits.model_copy(update={"max_steps": 2})})
    stack = HubStack({"db_agent": DB})
    script = [tool_turn("mongo__find", FIND)] * 3 + [final({"status": "ok", "answer": {}})]
    models = FakeModels(script, tmp_path)

    result = run(stack, models, tight, task())

    assert result.outcome == "failed"
    assert result.output is None
    assert result.failure is not None
    assert (result.failure.kind, result.failure.reason) == ("budget_exceeded", "steps 2/2")
    assert len(models.built[0].calls()) == 2  # the third model call was never made
    assert [s.kind for s in result.trajectory] == ["llm", "tool", "llm", "tool"]
    assert result.usage.steps == 2
    budget = [r for r in records(stack) if r.action == "guardrail.budget" and r.outcome is AuditOutcome.DENIED]
    assert [r.reason for r in budget] == ["steps 2/2"]
    assert result.to_dict()["failure"] == {"kind": "budget_exceeded", "reason": "steps 2/2"}
