"""Test plumbing for the agent runtime: the real MCP hub in process, and the `fake` connector via LLM-1.

`HubStack` is HUB-1's real `Gateway` behind its real MCP server (`build_hub`), reached over streamable
HTTP through an in-process ASGI transport. Its registry is a dict of registered agents, its audit log
is AUD-1's in-memory one, and the downstream answers allowed calls with fixed documents and records
what it was sent. Headers that reach the hub are recorded, so a test can see the token and grant
arrive there and nowhere else.

`FakeModels` builds models through `LLMRuntime.chat_model_for` on the repository's `config/llm.yaml`
with `LLM_CONNECTOR=fake` and a per-test script file, and keeps every model it built so a test can
read `ScriptedChatModel.calls()` (exactly what the model was sent). `InputCapture` is a callback that
renders every LLM input, like the eval harness's `LLMCapture`.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from mcp.types import CallToolResult, TextContent
from onb_agents.registry import AgentManifest, RegisteredAgent, load_manifests
from onb_agents.runtime.runner import ModelChoice
from onb_data.testing import InMemoryAuditLog
from onb_llm import LLMRuntime, LLMSelection, discover_connectors, load_llm_config
from onb_llm.connectors.fake import ScriptedChatModel
from onb_mcp_hub.catalog import HubTool, mongo_catalog
from onb_mcp_hub.gateway import Gateway
from onb_mcp_hub.server import build_hub
from onb_mcp_mongo.call_context import CallContext
from starlette.types import ASGIApp, Receive, Scope, Send

from .support import AGENTS_DIR, ROOT

TOKEN_KEY = secrets.token_urlsafe(40)
HUB_URL = "http://mcp-hub/mcp"
MANIFESTS: dict[str, AgentManifest] = {m.name: m for m in load_manifests(AGENTS_DIR)}
NOW = datetime(2026, 11, 11, tzinfo=UTC)


def registered(manifest: AgentManifest) -> RegisteredAgent:
    return RegisteredAgent(manifest.name, 1, manifest.sha256(), True, manifest, NOW)


@dataclass
class Registry:
    agents: dict[str, RegisteredAgent]

    async def get_agent(self, name: str) -> RegisteredAgent | None:
        return self.agents.get(name)


@dataclass
class Downstream:
    """Answers every allowed call with `documents`; holds one credential the hub must never return."""

    documents: list[dict[str, Any]] = field(default_factory=list)
    credential: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    sent: list[tuple[str, dict[str, Any], CallContext]] = field(default_factory=list)

    @property
    def secrets(self) -> tuple[str, ...]:
        return (self.credential,)

    async def call(
        self, tool: HubTool, args: Mapping[str, Any], context: CallContext, *, grant: str | None = None
    ) -> tuple[CallToolResult, tuple[str, ...]]:
        # NOTIF-1: the hub hands a sink the PII grant; these tests' Mongo tools never get one.
        self.sent.append((tool.name, dict(args), context))
        payload = {"documents": self.documents, "count": len(self.documents)}
        result = CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload))], structuredContent=payload, isError=False
        )
        return result, ()


class _HeaderRecorder:
    def __init__(self, app: ASGIApp, seen: list[dict[str, str]]) -> None:
        self._app = app
        self._seen = seen

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            self._seen.append({k.decode().lower(): v.decode() for k, v in scope["headers"]})
        await self._app(scope, receive, send)


class HubStack:
    """HUB-1's gateway and MCP server, in process. `registry` is what the hub believes."""

    def __init__(self, registry: Mapping[str, AgentManifest], documents: Sequence[dict[str, Any]] = ()) -> None:
        self.audit = InMemoryAuditLog()
        self.downstream = Downstream(list(documents))
        self.gateway = Gateway(
            catalog=mongo_catalog(),
            registry=Registry({name: registered(m) for name, m in registry.items()}),
            auditor=self.audit,
            downstream=self.downstream,
            token_key=TOKEN_KEY,
        )
        self.hub = build_hub(self.gateway, allowed_hosts=("mcp-hub",))
        self.headers_seen: list[dict[str, str]] = []
        self._app = _HeaderRecorder(self.hub.app, self.headers_seen)

    def client(self, headers: Mapping[str, str]) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self._app), base_url="http://mcp-hub", headers=dict(headers)
        )

    @asynccontextmanager
    async def running(self) -> AsyncIterator[HubStack]:
        async with self.hub.manager.run():
            yield self


def _render(message: BaseMessage) -> str:
    return f"{message.type}: {message.text} {json.dumps(getattr(message, 'tool_calls', []), default=str)}"


class InputCapture(BaseCallbackHandler):
    """Every LLM input as text, as the eval harness captures it."""

    run_inline = True

    def __init__(self) -> None:
        self.inputs: list[str] = []

    def on_chat_model_start(
        self, serialized: dict[str, Any], messages: list[list[BaseMessage]], *, run_id: UUID, **kwargs: Any
    ) -> None:
        self.inputs.extend("\n".join(_render(m) for m in batch) for batch in messages)


class FakeModels:
    """LLM-1's runtime on the `fake` connector, replaying `script` (a list of turns) for every model."""

    def __init__(self, script: Sequence[Mapping[str, Any]], tmp: Path) -> None:
        path = tmp / f"script-{secrets.token_hex(4)}.json"
        path.write_text(json.dumps(list(script)), encoding="utf-8")
        env = {
            "LLM_CONNECTOR": "fake",
            "LLM_MODEL_FAST": "fake-fast",
            "LLM_MODEL_STANDARD": "fake-standard",
            "LLM_MODEL_DEEP": "fake-deep",
            "LLM_FAKE_SCRIPT_FILE": str(path),
        }
        self.runtime = LLMRuntime.start(load_llm_config(ROOT / "config" / "llm.yaml", env), discover_connectors(), env)
        self.capture = InputCapture()
        self.built: list[ScriptedChatModel] = []

    def __call__(self, manifest: AgentManifest, task: object = None) -> ModelChoice:
        selection = LLMSelection(
            alias=manifest.llm.alias,
            connector=manifest.llm.connector,
            model=manifest.llm.model,
            params=manifest.llm.params.values(),
        )
        resolved, model = self.runtime.chat_model_for(manifest=selection)
        assert isinstance(model, ScriptedChatModel)
        existing = model.callbacks if isinstance(model.callbacks, list) else []
        model = model.model_copy(update={"callbacks": [*existing, self.capture]})
        self.built.append(model)
        return ModelChoice(record=resolved.task_record(), model=model)

    def sent_texts(self) -> list[str]:
        """Everything any model was sent: the fake's own call log and the callback's rendering."""
        from_calls = ["\n".join(_render(m) for m in call.messages) for model in self.built for call in model.calls()]
        return [*from_calls, *self.capture.inputs]


def chat_model_of(models: FakeModels) -> BaseChatModel:
    [model] = models.built
    return model


def final(payload: Mapping[str, Any]) -> dict[str, Any]:
    """A scripted final turn answering with `payload` as JSON."""
    return {"content": json.dumps(payload)}


def tool_turn(name: str, args: Mapping[str, Any]) -> dict[str, Any]:
    return {"content": "", "tool_calls": [{"name": name, "args": dict(args)}]}
