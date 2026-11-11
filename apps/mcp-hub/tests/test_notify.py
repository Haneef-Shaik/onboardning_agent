"""The hub in front of the notify sink (NOTIF-1), end to end in process: agent → hub → real mcp-notify.

The agent's grant travels as `X-Onb-Pii-Grant` on its hub connection (AG-1); the hub forwards it to
the notify server only, never to mcp-mongo or mcp-mocks, and never back to the agent. The notify
server gets a context signed with its own key for the `mcp-notify` audience.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from mcp.types import CallToolResult, TextContent
from onb_agents.registry import RegisteredAgent
from onb_agents.runtime.task import GRANT_HEADER
from onb_agents.tokens import hub_client_config, issue_agent_token
from onb_api.testing_mongo import MemoryDatabase, memory_keyring, memory_store, seed_vault_value
from onb_contracts.notify import NotifySendArgs
from onb_crypto import (
    GrantAudit,
    GrantIssuer,
    GrantSigningKey,
    GrantVerifier,
    SinkResolver,
    VaultField,
    ensure_grant_indexes,
    load_sink_policy,
)
from onb_data import AuditOutcome, tenant_scope
from onb_data.audit import AuditEntry, AuditQuery
from onb_data.testing import InMemoryAuditLog
from onb_mcp_hub.catalog import NOTIFY_SERVER, notify_catalog, platform_catalog
from onb_mcp_hub.downstream import GRANT_SERVERS, McpDownstream
from onb_mcp_hub.gateway import Gateway
from onb_mcp_mocks.notify.outbox import TelegramMockChannel, ensure_outbox_indexes
from onb_mcp_mocks.notify.sends import ensure_send_indexes
from onb_mcp_mocks.notify.server import DESCRIPTION, NotifyArgs, build_notify_server
from onb_mcp_mocks.notify.sink import NotifySink
from onb_mcp_mocks.telegram import TelegramMockTransport
from onb_mcp_mongo.call_context import HEADER, CallContext

from .agents import MANIFESTS, claims, registered
from .fakes import CONTEXT_KEY, TOKEN_KEY, FakeDownstream, FakeRegistry, ok
from .harness import hub_session

ROOT = Path(__file__).resolve().parents[3]
POLICY = load_sink_policy(ROOT / "config" / "pii_sink_policy.yaml")
MOCKS_KEY = "hub-unit-test-mocks-context-key-0123456789"
NOTIFY_KEY = "hub-unit-test-notify-context-key-012345678"
HUB_URL = "http://mcp-hub/mcp"
MOBILE = "9876501234"


def text(result: CallToolResult) -> str:
    return "".join(c.text for c in result.content if isinstance(c, TextContent))


def headers_for(grant: str | None, agent: str = "templates_agent") -> dict[str, str]:
    config = hub_client_config(HUB_URL, issue_agent_token(claims(agent), TOKEN_KEY))
    headers: dict[str, str] = dict(config["headers"])
    if grant is not None:
        headers[GRANT_HEADER] = grant
    return headers


class _Events:
    def __init__(self) -> None:
        self.published: list[str] = []

    async def sent(self, payload: Mapping[str, Any], *, key: str, run_id: str | None, change: Any) -> None:
        await change()
        self.published.append("notification.sent")

    async def rejected(self, payload: Mapping[str, Any], *, run_id: str) -> None:
        self.published.append("task.sink_rejected")


class Stack:
    """The hub over the platform catalog, with the real notify server (over an in-memory store) behind it."""

    def __init__(self) -> None:
        self.db = MemoryDatabase()
        store = memory_store(self.db)
        keyring = memory_keyring(store)
        signing = GrantSigningKey.generate()
        verifier = GrantVerifier([signing.public_key])
        self.issuer = GrantIssuer(signing, POLICY)
        self.sink_audit = InMemoryAuditLog()
        self.events = _Events()

        def resolver(audit: GrantAudit) -> SinkResolver:
            return SinkResolver.create(keyring, store, verifier, POLICY, audit)

        async def seed() -> str:
            await ensure_grant_indexes(store)
            await ensure_outbox_indexes(store)
            await ensure_send_indexes(store)
            with tenant_scope("org_acme"):
                await keyring.create("org_acme")
            return await seed_vault_value(
                keyring, store, "org_acme", subject_id="h_acme_001", field=VaultField.MOBILE, value=MOBILE
            )

        self.token = asyncio.run(seed())
        self.sink = NotifySink(
            store,
            resolver=resolver,
            verifier=verifier,
            telegram=TelegramMockChannel(store, TelegramMockTransport()),
            events=self.events,
            auditor=self.sink_audit,
        )
        self.forwarded: list[httpx.Request] = []
        self._app: Any = None

        def client(headers: dict[str, str]) -> httpx.AsyncClient:
            async def record(request: httpx.Request) -> None:
                self.forwarded.append(request)

            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self._app),
                base_url="http://mcp-notify",
                headers=headers,
                event_hooks={"request": [record]},
            )

        self.audit = InMemoryAuditLog()
        downstream = McpDownstream(
            {"mongo": "http://mcp-mongo/mcp", "mocks": "http://mcp-mocks/mcp", NOTIFY_SERVER: "http://mcp-notify/mcp"},
            CONTEXT_KEY,
            keys={"mocks": MOCKS_KEY, NOTIFY_SERVER: NOTIFY_KEY},
            client=client,
        )
        self.gateway = Gateway(
            catalog=platform_catalog(),
            registry=FakeRegistry({"templates_agent": registered("templates_agent")}),
            auditor=self.audit,
            downstream=downstream,
            token_key=TOKEN_KEY,
        )

    def grant(self, task: str = "welcome_message") -> str:
        with tenant_scope("org_acme"):
            return self.issuer.issue_grant(
                "org_acme",
                run_id="run_1",
                task_id=task,
                subject_id="h_acme_001",
                fields=[VaultField.MOBILE],
                sink="notify.telegram",
            )

    def call(self, args: dict[str, Any], headers: dict[str, str]) -> CallToolResult:
        server = build_notify_server(self.sink, NOTIFY_KEY)
        self._app = server.streamable_http_app()

        async def go() -> CallToolResult:
            async with server.session_manager.run(), hub_session(self.gateway, headers) as session:
                return await session.call_tool("notify__notify_send", args)

        return asyncio.run(go())

    def records(self) -> list[AuditEntry]:
        async def page() -> list[AuditEntry]:
            with tenant_scope("org_acme"):
                return list((await self.audit.list(AuditQuery(), page=1, limit=100)).entries)

        return asyncio.run(page())

    def telegram(self) -> dict[str, Any]:
        return {"channel": "telegram", "recipient_token": self.token, "body": "Hi Priya, welcome aboard!"}


# --- catalog -----------------------------------------------------------------------------------------


def test_the_hub_serves_notify_send_with_the_notify_servers_own_contract() -> None:
    [entry] = notify_catalog().values()
    assert (entry.name, entry.server, entry.tool, entry.audit_name) == (
        "notify__notify_send",
        "notify",
        "notify_send",
        "notify.notify_send",
    )
    assert entry.input_schema == NotifyArgs.model_json_schema(by_alias=True)
    assert entry.description == DESCRIPTION
    assert entry.read_only is False
    assert "notify__notify_send" in platform_catalog()


def test_the_templates_manifest_names_only_notify_tools_the_hub_serves() -> None:
    """REG-1's `templates_agent.yaml` grants `notify: [notify_send]`; the hub must serve exactly that."""
    grant = MANIFESTS["templates_agent"].mcp[NOTIFY_SERVER]
    assert {f"{NOTIFY_SERVER}__{tool}" for tool in grant.tools} <= set(notify_catalog())


def test_only_the_notify_server_takes_grants() -> None:
    assert frozenset({NOTIFY_SERVER}) == GRANT_SERVERS


# --- the downstream client: the grant goes to notify only ------------------------------------------


def _first_request_headers(downstream: McpDownstream, name: str, grant: str | None) -> httpx.Headers:
    tool = platform_catalog()[name]
    seen: list[httpx.Headers] = []

    def stop(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        raise RuntimeError("stop after the first request")

    downstream._client = lambda headers: httpx.AsyncClient(transport=httpx.MockTransport(stop), headers=headers)
    ctx = CallContext(org_id="org_acme", agent="a", run_id="r", tool=tool.tool, read=frozenset(), write=frozenset())
    with pytest.raises(BaseException):  # noqa: B017, PT011 - the fake transport ends the session
        asyncio.run(downstream.call(tool, {}, ctx, grant=grant))
    return seen[0]


def _downstream() -> McpDownstream:
    urls = {"mongo": "http://m/mcp", "mocks": "http://k/mcp", NOTIFY_SERVER: "http://n/mcp"}
    return McpDownstream(urls, CONTEXT_KEY, keys={"mocks": MOCKS_KEY, NOTIFY_SERVER: NOTIFY_KEY})


def test_the_grant_header_reaches_the_notify_server_with_a_notify_context() -> None:
    headers = _first_request_headers(_downstream(), "notify__notify_send", "the.grant.jws")
    assert headers[GRANT_HEADER] == "the.grant.jws"
    claims_ = jwt.decode(headers[HEADER], NOTIFY_KEY, algorithms=["HS256"], audience="mcp-notify")
    assert claims_["tool"] == "notify_send"


@pytest.mark.parametrize("name", ["mongo__find", "mocks__provision_laptop"])
def test_the_grant_header_never_reaches_another_server(name: str) -> None:
    headers = _first_request_headers(_downstream(), name, "the.grant.jws")
    assert GRANT_HEADER.lower() not in {k.lower() for k in headers}
    assert "the.grant.jws" not in json.dumps(dict(headers))


def test_a_notify_url_without_its_own_key_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="notify"):
        McpDownstream({"notify": "http://n/mcp"}, CONTEXT_KEY, keys={})


# --- the gateway --------------------------------------------------------------------------------------


def _gateway(downstream: FakeDownstream, agent: RegisteredAgent) -> Gateway:
    return Gateway(
        catalog=platform_catalog(),
        registry=FakeRegistry({agent.name: agent}),
        auditor=InMemoryAuditLog(),
        downstream=downstream,
        token_key=TOKEN_KEY,
    )


def _bearer(agent: str) -> str:
    return f"Bearer {issue_agent_token(claims(agent), TOKEN_KEY)}"


def test_the_gateway_hands_the_grant_to_the_notify_call_only() -> None:
    downstream = FakeDownstream(
        reply=lambda sent: ok({"status": "sent", "channel": "telegram", "notification_id": "n"})
    )
    gateway = _gateway(downstream, registered("templates_agent"))
    args = {"channel": "telegram", "recipient_token": "ptk_01JD00000000000000000000000000", "body": "Hi"}
    asyncio.run(gateway.call("notify__notify_send", args, _bearer("templates_agent"), grant="g.r.ant"))
    asyncio.run(
        gateway.call(
            "mongo__find",
            {"collection": "run_tasks", "filter": {}, "limit": 5},
            _bearer("templates_agent"),
            grant="g.r.ant",
        )
    )
    assert [(s.tool.name, s.grant) for s in downstream.sent] == [
        ("notify__notify_send", "g.r.ant"),
        ("mongo__find", None),
    ]


def test_a_response_echoing_the_grant_is_withheld_from_the_agent() -> None:
    grant = "eyJhbGciOiJFZERTQSJ9.eyJqdGkiOiJ4In0.c2lnbmF0dXJlLWJ5dGVz"
    downstream = FakeDownstream(reply=lambda sent: ok({"status": "sent", "echo": grant}))
    gateway = _gateway(downstream, registered("templates_agent"))
    args = {"channel": "telegram", "recipient_token": "ptk_01JD00000000000000000000000000", "body": "Hi"}
    result = asyncio.run(gateway.call("notify__notify_send", args, _bearer("templates_agent"), grant=grant))
    assert result.isError is True
    assert grant not in text(result)


# --- end to end ---------------------------------------------------------------------------------------


def test_an_agent_sends_through_the_hub_and_the_sink_resolves_with_the_forwarded_grant() -> None:
    stack = Stack()
    result = stack.call(stack.telegram(), headers_for(stack.grant()))
    assert result.isError is False, text(result)
    assert result.structuredContent is not None
    assert result.structuredContent["status"] == "sent"
    [stored] = stack.db.raw("mock_outbox")
    assert stored["recipient"]["masked"] == "98XXXXXX34"
    assert MOBILE not in json.dumps(result.model_dump(mode="json"))
    assert [(r.target, r.outcome) for r in stack.records()] == [("tool:notify.notify_send", AuditOutcome.SUCCESS)]
    calls = [r for r in stack.forwarded if b"tools/call" in r.content]
    assert all(r.headers.get(GRANT_HEADER) is not None for r in calls)


def test_a_rejected_grant_through_the_hub_is_a_refusal_and_nothing_is_sent() -> None:
    stack = Stack()
    grant = stack.grant()
    stack.call(stack.telegram(), headers_for(grant))
    replay = stack.call({**stack.telegram(), "body": "Another body"}, headers_for(grant))  # a used grant
    assert replay.isError is True
    assert text(replay).startswith("denied (downstream_denied)")
    assert len(stack.db.raw("mock_outbox")) == 1
    assert stack.events.published == ["notification.sent", "task.sink_rejected"]
    outcomes = [(r.target, r.outcome, r.reason) for r in stack.records()]
    assert ("tool:notify.notify_send", AuditOutcome.DENIED, "downstream_denied") in outcomes


def test_an_agent_without_notify_in_its_manifest_is_denied_before_the_sink() -> None:
    downstream = FakeDownstream()
    gateway = _gateway(downstream, registered("db_agent"))
    args = {"channel": "telegram", "recipient_token": "ptk_01JD00000000000000000000000000", "body": "Hi"}
    result = asyncio.run(gateway.call("notify__notify_send", args, _bearer("db_agent"), grant="g.r.ant"))
    assert text(result).startswith("denied (server_not_in_manifest)")
    assert downstream.sent == []


def test_the_contract_the_hub_lists_matches_the_shared_one() -> None:
    entry = notify_catalog()["notify__notify_send"]
    assert set(entry.input_schema["properties"]) == set(NotifySendArgs.model_fields)
    assert dataclasses.is_dataclass(entry)
