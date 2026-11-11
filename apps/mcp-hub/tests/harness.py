"""In-process MCP plumbing for hub tests: an agent-side client session to the hub's ASGI app, and
mcp-mongo's real ASGI app as the hub's downstream (over a tiny fake database)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from onb_data import DataStore
from onb_mcp_hub.server import build_hub
from starlette.types import ASGIApp

Document = dict[str, Any]


@asynccontextmanager
async def agent_session(app: ASGIApp, lifespan: Any, headers: Mapping[str, str]) -> AsyncIterator[ClientSession]:
    """An MCP client session to `app` (as the agent side would open it), inside `lifespan`."""
    async with lifespan, AsyncExitStack() as stack:
        transport = httpx.ASGITransport(app=app)
        http = await stack.enter_async_context(
            httpx.AsyncClient(transport=transport, base_url="http://mcp-hub", headers=dict(headers))
        )
        read, write, _ = await stack.enter_async_context(streamable_http_client("http://mcp-hub/mcp", http_client=http))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        yield session


@asynccontextmanager
async def hub_session(gateway: Any, headers: Mapping[str, str]) -> AsyncIterator[ClientSession]:
    hub = build_hub(gateway, allowed_hosts=("mcp-hub",))
    async with agent_session(hub.app, hub.manager.run(), headers) as session:
        yield session


# --- a tiny database under mcp-mongo's real DataStore (top-level equality matching only) -----------


@dataclass
class _Cursor:
    docs: list[Document]

    async def to_list(self, length: int | None = None) -> list[Document]:
        return list(self.docs)


def _matches(doc: Document, query: Document) -> bool:
    return all(doc.get(k) == v for k, v in query.items() if not k.startswith("$"))


@dataclass
class FakeCollection:
    docs: list[Document] = field(default_factory=list)
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def find(self, query: Document, **kwargs: Any) -> _Cursor:
        self.calls.append(("find", query))
        return _Cursor([d for d in self.docs if _matches(d, query)][: kwargs.get("limit") or None])

    async def count_documents(self, query: Document) -> int:
        self.calls.append(("count", query))
        return sum(1 for d in self.docs if _matches(d, query))

    async def aggregate(self, pipeline: list[Document]) -> _Cursor:
        self.calls.append(("aggregate", pipeline))
        return _Cursor([d for d in self.docs if _matches(d, pipeline[0]["$match"])])

    async def insert_one(self, document: Document) -> SimpleNamespace:
        self.calls.append(("insert_one", document))
        self.docs.append({"_id": "6730a9f1c2d3e4f5a6b7c8d9", **document})
        return SimpleNamespace(inserted_id="6730a9f1c2d3e4f5a6b7c8d9")

    async def update_one(self, query: Document, update: Document, upsert: bool = False) -> SimpleNamespace:
        self.calls.append(("update_one", query))
        hit = next((d for d in self.docs if _matches(d, query)), None)
        return SimpleNamespace(
            matched_count=int(hit is not None), modified_count=int(hit is not None), upserted_id=None
        )


@dataclass
class FakeDatabase:
    collections: dict[str, FakeCollection] = field(default_factory=dict)

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())


def make_store(database: FakeDatabase) -> DataStore:
    return DataStore(database, None)  # type: ignore[arg-type]  # duck-typed driver stand-in
