"""The hub in front of mcp-mocks (MOCK-1), end to end in process: agent client → hub → real mcp-mocks.

The hub signs each mocks call with the mocks' own key for the `mcp-mocks` audience; the mongo key
never reaches the mocks, and no key comes back to the agent.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import jwt
import pytest
from mcp import ClientSession
from mcp.types import CallToolResult, TextContent
from onb_agents.registry import RegisteredAgent
from onb_agents.tokens import hub_client_config, issue_agent_token
from onb_data import AuditOutcome, tenant_scope
from onb_data.audit import AuditEntry, AuditQuery
from onb_data.testing import InMemoryAuditLog
from onb_mcp_hub.catalog import MOCKS_SERVER, mocks_catalog, mongo_catalog, platform_catalog
from onb_mcp_hub.downstream import McpDownstream
from onb_mcp_hub.gateway import Gateway
from onb_mcp_mocks.contracts import CONTRACTS as MOCK_CONTRACTS
from onb_mcp_mocks.server import build_server as build_mocks
from onb_mcp_mocks.store import InMemoryIdempotencyStore
from onb_mcp_mongo.call_context import HEADER
from onb_telemetry.testing import CapturedTelemetry

from .agents import MANIFESTS, NOW, claims, registered
from .fakes import CONTEXT_KEY, TOKEN_KEY
from .harness import hub_session

MOCKS_KEY = "hub-unit-test-mocks-context-key-0123456789"
HUB_URL = "http://mcp-hub/mcp"
TASK = "65f0a1b2c3d4e5f6a7b8c9d0"
HIRE = "65f0aaaabbbbccccddddeeee"
BITBUCKET = {"task_id": TASK, "hire_id": HIRE, "workspace": "acme-eng", "groups": ["backend"]}
LAPTOP = {"task_id": "65f0a1b2c3d4e5f6a7b8c9d1", "hire_id": HIRE, "profile": "standard", "office": "bangalore"}


def text(result: CallToolResult) -> str:
    return "".join(c.text for c in result.content if isinstance(c, TextContent))


def headers_for(agent: str = "provisioning_agent", **kw: Any) -> dict[str, str]:
    config = hub_client_config(HUB_URL, issue_agent_token(claims(agent, **kw), TOKEN_KEY))
    headers: dict[str, str] = config["headers"]
    return headers


def narrowed(name: str, tools: tuple[str, ...]) -> RegisteredAgent:
    """The real provisioning manifest with fewer mock tools (validated like any manifest)."""
    manifest = MANIFESTS[name]
    mcp = {**manifest.mcp, MOCKS_SERVER: manifest.mcp[MOCKS_SERVER].model_copy(update={"tools": tools})}
    changed = manifest.model_copy(update={"mcp": mcp})
    return RegisteredAgent(name, 1, changed.sha256(), True, changed, NOW)


class Stack:
    """The hub over the full platform catalog; mocks downstream = the real mcp-mocks ASGI app."""

    def __init__(self, agents: dict[str, RegisteredAgent] | None = None) -> None:
        self.store = InMemoryIdempotencyStore()
        self.forwarded: list[httpx.Request] = []
        self._mocks_app: Any = None

        def client(headers: dict[str, str]) -> httpx.AsyncClient:
            async def record(request: httpx.Request) -> None:
                self.forwarded.append(request)

            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self._mocks_app),
                base_url="http://mcp-mocks",
                headers=headers,
                event_hooks={"request": [record]},
            )

        self.audit = InMemoryAuditLog()
        self.downstream = McpDownstream(
            {"mongo": "http://mcp-mongo/mcp", MOCKS_SERVER: "http://mcp-mocks/mcp"},
            CONTEXT_KEY,
            keys={MOCKS_SERVER: MOCKS_KEY},
            client=client,
        )
        self.gateway = Gateway(
            catalog=platform_catalog(),
            registry=_Registry(agents or {"provisioning_agent": registered("provisioning_agent")}),
            auditor=self.audit,
            downstream=self.downstream,
            token_key=TOKEN_KEY,
        )

    def run(self, fn: Callable[[ClientSession], Awaitable[Any]], headers: dict[str, str]) -> Any:
        # A FastMCP session manager runs once; each call gets a fresh server over the same store,
        # as a restarted process would (the store is what makes a replay a replay).
        mocks = build_mocks(self.store, MOCKS_KEY)
        self._mocks_app = mocks.streamable_http_app()

        async def go() -> Any:
            async with mocks.session_manager.run(), hub_session(self.gateway, headers) as session:
                return await fn(session)

        return asyncio.run(go())

    def call(self, tool: str, args: dict[str, Any], headers: dict[str, str] | None = None) -> CallToolResult:
        result: CallToolResult = self.run(lambda s: s.call_tool(tool, args), headers or headers_for())
        return result

    def records(self, org: str = "org_acme") -> list[AuditEntry]:
        async def page() -> list[AuditEntry]:
            with tenant_scope(org):
                return list((await self.audit.list(AuditQuery(), page=1, limit=100)).entries)

        return asyncio.run(page())


class _Registry:
    def __init__(self, agents: dict[str, RegisteredAgent]) -> None:
        self._agents = agents

    async def get_agent(self, name: str) -> RegisteredAgent | None:
        return self._agents.get(name)


# --- catalog -----------------------------------------------------------------------------------------


def test_the_platform_catalog_serves_the_mongo_and_mock_tools() -> None:
    catalog = platform_catalog()
    assert sorted(catalog) == sorted([*mongo_catalog(), *mocks_catalog()])
    assert sorted(mocks_catalog()) == ["mocks__create_bitbucket_account", "mocks__provision_laptop"]


@pytest.mark.parametrize("tool", sorted(MOCK_CONTRACTS))
def test_each_mock_entry_carries_the_mock_servers_own_contract(tool: str) -> None:
    entry = mocks_catalog()[f"mocks__{tool}"]
    contract = MOCK_CONTRACTS[tool]
    assert (entry.server, entry.tool, entry.audit_name) == ("mocks", tool, f"mocks.{tool}")
    assert entry.input_schema == contract.args.model_json_schema(by_alias=True)
    assert entry.output_schema == contract.result.model_json_schema(by_alias=True)
    assert entry.read_only is False


def test_the_provisioning_manifest_names_only_tools_the_hub_serves() -> None:
    """REG-1's YAML names `mocks: [create_bitbucket_account, provision_laptop]`; both must exist."""
    grant = MANIFESTS["provisioning_agent"].mcp
    served = {(t.server, t.tool) for t in platform_catalog().values()}
    assert {(server, tool) for server, g in grant.items() for tool in g.tools} <= served


# --- box 1: the provisioning agent's token reaches the mocks through the hub -------------------------


@pytest.mark.parametrize(
    ("tool", "args"), [("mocks__create_bitbucket_account", BITBUCKET), ("mocks__provision_laptop", LAPTOP)]
)
def test_the_provisioning_agent_calls_a_mock_through_the_hub(
    tool: str, args: dict[str, Any], telemetry: CapturedTelemetry
) -> None:
    stack = Stack()
    result = stack.call(tool, args)
    assert result.isError is False, text(result)
    assert result.structuredContent is not None
    assert result.structuredContent["hire_id"] == HIRE
    [record] = stack.records()
    assert (record.outcome, record.target, record.actor.id) == (
        AuditOutcome.SUCCESS,
        f"tool:mocks.{tool.removeprefix('mocks__')}",
        "provisioning_agent",
    )
    assert stack.store.count("org_acme") == 1
    [span] = [s for s in telemetry.spans() if s.name == f"mcp.hub.{tool}"]
    assert dict(span.attributes or {})["outcome"] == "ok"


def test_the_hub_signs_mocks_calls_with_the_mocks_key_for_the_mocks_audience(telemetry: CapturedTelemetry) -> None:
    stack = Stack()
    headers = headers_for()
    result = stack.call("mocks__create_bitbucket_account", BITBUCKET, headers)
    [post] = [r for r in stack.forwarded if b'"tools/call"' in r.content]
    context = post.headers[HEADER]
    claims_ = jwt.decode(context, MOCKS_KEY, algorithms=["HS256"], audience="mcp-mocks")
    assert (claims_["org_id"], claims_["tool"], claims_["agent"]) == (
        "org_acme",
        "create_bitbucket_account",
        "provisioning_agent",
    )
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(context, CONTEXT_KEY, algorithms=["HS256"], audience="mcp-mocks")
    assert "authorization" not in {k.lower() for k in post.headers}  # the agent token stops at the hub
    returned = text(result) + json.dumps(result.structuredContent)
    for secret in (MOCKS_KEY, CONTEXT_KEY, TOKEN_KEY, context):
        assert secret not in returned
    assert MOCKS_KEY in stack.downstream.secrets


# --- box 2: replay with the same idempotency key -----------------------------------------------------


def test_replaying_through_the_hub_returns_the_same_result_and_creates_nothing_new(
    telemetry: CapturedTelemetry,
) -> None:
    stack = Stack()
    first = stack.call("mocks__create_bitbucket_account", BITBUCKET)
    again = stack.call("mocks__create_bitbucket_account", BITBUCKET)
    assert again.isError is False
    assert again.structuredContent == first.structuredContent
    assert stack.store.count() == 1
    assert stack.store.creations == 1
    assert [r.outcome for r in stack.records()] == [AuditOutcome.SUCCESS, AuditOutcome.SUCCESS]


def test_a_conflicting_replay_through_the_hub_is_refused_and_audited_as_denied(telemetry: CapturedTelemetry) -> None:
    stack = Stack()
    stack.call("mocks__create_bitbucket_account", BITBUCKET)
    result = stack.call("mocks__create_bitbucket_account", {**BITBUCKET, "groups": ["admins"]})
    assert result.isError is True
    assert "downstream_denied" in text(result)
    assert stack.store.count() == 1
    outcomes = sorted((r.outcome.value, r.reason or "") for r in stack.records())
    assert outcomes == [("denied", "downstream_denied"), ("success", "")]


def test_the_same_idempotency_key_in_another_org_creates_that_orgs_own_record(telemetry: CapturedTelemetry) -> None:
    stack = Stack()
    in_acme = stack.call("mocks__provision_laptop", LAPTOP)
    in_globex = stack.call("mocks__provision_laptop", LAPTOP, headers_for(org="org_globex"))
    assert in_acme.structuredContent is not None
    assert in_globex.structuredContent is not None
    assert in_acme.structuredContent["order_id"] != in_globex.structuredContent["order_id"]
    assert (stack.store.count("org_acme"), stack.store.count("org_globex")) == (1, 1)


# --- box 3: an agent without the tool in its manifest is denied --------------------------------------


def _assert_denied_and_not_forwarded(stack: Stack, result: CallToolResult, reason: str, agent: str) -> None:
    assert result.isError is True
    assert f"denied ({reason})" in text(result)
    [record] = stack.records()
    assert (record.outcome, record.reason, record.actor.id) == (AuditOutcome.DENIED, reason, agent)
    assert stack.store.count() == 0
    assert not [r for r in stack.forwarded if b'"tools/call"' in r.content]


def test_an_agent_with_no_mock_tools_in_its_manifest_is_denied(telemetry: CapturedTelemetry) -> None:
    """db_agent's manifest grants mongo only; even a token scoped to the mock tool cannot reach it."""
    stack = Stack({"db_agent": registered("db_agent")})
    headers = headers_for("db_agent", scopes=("mocks__create_bitbucket_account",))
    result = stack.call("mocks__create_bitbucket_account", BITBUCKET, headers)
    _assert_denied_and_not_forwarded(stack, result, "server_not_in_manifest", "db_agent")


def test_an_agent_whose_manifest_lacks_that_mock_tool_is_denied(telemetry: CapturedTelemetry) -> None:
    stack = Stack({"provisioning_agent": narrowed("provisioning_agent", ("provision_laptop",))})
    headers = headers_for(scopes=("mocks__create_bitbucket_account", "mocks__provision_laptop"))
    result = stack.call("mocks__create_bitbucket_account", BITBUCKET, headers)
    _assert_denied_and_not_forwarded(stack, result, "tool_not_in_manifest", "provisioning_agent")


def test_a_token_without_the_mock_scope_is_denied(telemetry: CapturedTelemetry) -> None:
    stack = Stack()
    result = stack.call("mocks__create_bitbucket_account", BITBUCKET, headers_for(scopes=("mocks__provision_laptop",)))
    _assert_denied_and_not_forwarded(stack, result, "scope_not_granted", "provisioning_agent")


def test_an_org_id_argument_to_a_mock_is_denied_at_the_hub(telemetry: CapturedTelemetry) -> None:
    stack = Stack()
    result = stack.call("mocks__provision_laptop", {**LAPTOP, "org_id": "org_globex"})
    assert result.isError is True
    assert stack.store.count() == 0
    [record] = stack.records()
    assert record.outcome is AuditOutcome.DENIED
    assert record.reason == "invalid_arguments"  # the closed contract refuses the key before it is read
