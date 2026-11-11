"""The notify sink against the compose MongoDB replica set (NOTIF-1; run by `make test-int`).

Everything is the production wiring of `onb_mcp_mocks.notify.main.build_service`: the real vault and
keyring, PII-4's resolver and grant indexes, the EVT-1 bus (so the outbox write and its event commit in
one transaction), `AuditLog`, and the MCP server reached over streamable HTTP with a hub-signed
context. Read-backs across orgs go through the root client inside the T-2 safety net's allowance.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent
from onb_api.notify.service import BusDecisions, MockChannels
from onb_api.testing_mongo import seed_vault_value
from onb_contracts.notify import Decision, TelegramUpdate
from onb_crypto import (
    ORG_KEYS_COLLECTION,
    GrantIssuer,
    GrantSigningKey,
    GrantVerifier,
    Keyring,
    MasterKey,
    RepositoryOrgKeyStore,
    SecretKey,
    VaultField,
    load_sink_policy,
)
from onb_data import DataStore, connect, tenant_scope
from onb_mcp_mocks.notify.in_app import in_app_handler
from onb_mcp_mocks.notify.main import NotifyService, build_service, ensure_indexes
from onb_mcp_mocks.notify.outbox import DecisionButton, OutboxEntry, TelegramMockChannel
from onb_mcp_mocks.notify.server import AUDIENCE, GRANT_HEADER
from onb_mcp_mocks.notify.settings import NotifySettings
from onb_mcp_mocks.telegram import TelegramMockTransport
from onb_mcp_mongo.call_context import HEADER, CallContext, sign_call_context

from .conftest import Scratch

ROOT = Path(__file__).resolve().parents[3]
POLICY_FILE = ROOT / "config" / "pii_sink_policy.yaml"
KEY = secrets.token_urlsafe(40)
A, B = "org_acme", "org_globex"
HIRES = {A: "h_acme_001", B: "h_globex_001"}
MOBILES = {A: "9876501234", B: "9988776655"}
OTHER_HIRE, OTHER_MOBILE = "h_acme_002", "9123409876"
RAW = (*MOBILES.values(), OTHER_MOBILE, *(m[2:-2] for m in (*MOBILES.values(), OTHER_MOBILE)))
NOT_SCANNED = frozenset({"pii_vault", "org_keys"})


class World:
    def __init__(self, scratch: Scratch) -> None:
        self.scratch = scratch
        self.master = MasterKey(SecretKey.generate())
        self.signing = GrantSigningKey.generate()
        self.settings = NotifySettings(
            context_key=KEY,
            mongo_uri=scratch.uri,
            master_key=self.master,
            verifier=GrantVerifier([self.signing.public_key]),
            database=scratch.name,
            policy_file=POLICY_FILE,
            host="127.0.0.1",
            port=8210,
        )
        self.issuer = GrantIssuer(self.signing, load_sink_policy(POLICY_FILE))
        self.tokens: dict[str, str] = {}

    def store(self) -> DataStore:
        return connect(self.scratch.uri, self.scratch.name, app_name="notify-int")

    async def seed(self, store: DataStore) -> None:
        keyring = Keyring(self.master, RepositoryOrgKeyStore(store.tenant(ORG_KEYS_COLLECTION)))
        for org in (A, B):
            with tenant_scope(org):
                await keyring.create(org)
                await store.tenant("users").insert_many(
                    [{"_id": f"u_{org}_hr", "role": "HR"}, {"_id": f"u_{org}_mgr", "role": "MANAGER"}]
                )
            self.tokens[org] = await seed_vault_value(
                keyring, store, org, subject_id=HIRES[org], field=VaultField.MOBILE, value=MOBILES[org]
            )
        self.tokens["other"] = await seed_vault_value(
            keyring, store, A, subject_id=OTHER_HIRE, field=VaultField.MOBILE, value=OTHER_MOBILE
        )

    def grant(self, org: str = A, task: str = "welcome_message") -> str:
        with tenant_scope(org):
            return self.issuer.issue_grant(
                org, run_id=f"run_{org}", task_id=task, subject_id=HIRES[org], fields=[VaultField.MOBILE],
                sink="notify.telegram",
            )  # fmt: skip

    def everything(self) -> list[tuple[str, dict[str, Any]]]:
        with self.scratch.root_read():
            return [
                (name, doc)
                for name in self.scratch.db.list_collection_names()
                if name not in NOT_SCANNED
                for doc in self.scratch.db[name].find()
            ]


@pytest.fixture
def world(scratch: Scratch) -> World:
    return World(scratch)


@asynccontextmanager
async def running(world: World) -> AsyncIterator[NotifyService]:
    store = world.store()
    service = build_service(world.settings, store)
    try:
        await ensure_indexes(service)
        await world.seed(store)
        yield service
    finally:
        await store.close()


async def send(
    world: World, service: NotifyService, args: dict[str, Any], grant: str | None, org: str = A
) -> CallToolResult:
    """One call over MCP, signed like the hub signs it. A fresh server each call (a FastMCP session
    manager runs once), over the same store."""
    ctx = CallContext(org_id=org, agent="templates_agent", run_id=f"run_{org}", tool="notify_send",
                      read=frozenset(), write=frozenset())  # fmt: skip
    headers = {HEADER: sign_call_context(ctx, KEY, audience=AUDIENCE)}
    if grant is not None:
        headers[GRANT_HEADER] = grant
    server = build_service(world.settings, service.store).server
    app = server.streamable_http_app()
    async with (
        server.session_manager.run(),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://n", headers=headers) as http,
        streamable_http_client("http://n/mcp", http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        return await session.call_tool("notify_send", args)


def text(result: CallToolResult) -> str:
    return "".join(c.text for c in result.content if isinstance(c, TextContent))


def telegram(world: World, which: str = A, body: str = "Hi Priya, welcome aboard!") -> dict[str, Any]:
    return {"channel": "telegram", "recipient_token": world.tokens[which], "body": body}


def run[T](world: World, scenario: Callable[[NotifyService], Awaitable[T]]) -> T:
    async def main() -> T:
        async with running(world) as service:
            result: T = await scenario(service)
            return result

    return asyncio.run(main())


# --- box 1 ---------------------------------------------------------------------------------------------


def test_box1_mock_outbox_holds_only_the_masked_mobile_never_the_raw_value_db_scan(world: World) -> None:
    async def scenario(service: NotifyService) -> CallToolResult:
        return await send(world, service, telegram(world), world.grant())

    result = run(world, scenario)
    assert result.isError is False, text(result)
    outbox = world.scratch.find("mock_outbox")
    assert [(d["org_id"], d["recipient"]) for d in outbox] == [(A, {"token": world.tokens[A], "masked": "98XXXXXX34"})]
    hits = [f"{name}: {raw}" for name, doc in world.everything() for raw in RAW if raw in json.dumps(doc, default=str)]
    assert hits == [], "a raw mobile outside the vault"
    # The outbox write and its event committed together (EVT-1 outbox pattern).
    [event] = world.scratch.find("events", {"type": "notification.sent"})
    assert (event["org_id"], event["payload"]["outbox_id"]) == (A, outbox[0]["_id"])
    [access] = world.scratch.find("pii_access_log")
    assert (access["org_id"], access["masked"], access["kind"]) == (A, "98XXXXXX34", "resolve")


# --- box 2 ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("which", "grant_org", "reason"),
    [("other", A, "token_not_bound"), (B, A, "token_not_found"), (B, B, "wrong_org"), (A, None, "malformed")],
    ids=["another-hires-token", "another-orgs-token", "another-orgs-grant", "no-grant"],
)
def test_box2_a_rejected_grant_stores_nothing_fails_the_task_and_writes_a_guardrail_event_and_an_audit_record(
    world: World, which: str, grant_org: str | None, reason: str
) -> None:
    async def scenario(service: NotifyService) -> CallToolResult:
        return await send(world, service, telegram(world, which), world.grant(grant_org) if grant_org else None)

    result = run(world, scenario)
    assert result.isError is True
    assert text(result) == f"grant_rejected ({reason}): nothing was sent"
    assert world.scratch.find("mock_outbox") == []
    assert world.scratch.find("pii_access_log") == []
    # The task fails: `task.sink_rejected` is on the bus for the orchestrator, in the caller's org.
    [failed] = world.scratch.find("events", {"type": "task.sink_rejected"})
    assert failed["org_id"] == A
    assert (failed["payload"]["run_id"], failed["payload"]["reason"]) == (f"run_{A}", reason)
    # The grant audit record and the guardrail decision, both in the caller's org.
    records = {(r["org_id"], r["action"], r["outcome"], r["reason"]) for r in world.scratch.find("audit_log")}
    assert records == {(A, "pii.grant.resolve", "denied", reason), (A, "guardrail.sink.grant", "denied", reason)}
    hits = [raw for _, doc in world.everything() for raw in RAW if raw in json.dumps(doc, default=str)]
    assert hits == []


def test_concurrent_uses_of_one_grant_send_once(world: World) -> None:
    async def scenario(service: NotifyService) -> list[CallToolResult]:
        grant = world.grant()
        bodies = [f"Message {i}" for i in range(5)]
        return list(await asyncio.gather(*(send(world, service, telegram(world, A, b), grant) for b in bodies)))

    results = run(world, scenario)
    assert sum(not r.isError for r in results) == 1
    assert {text(r) for r in results if r.isError} == {"grant_rejected (replayed): nothing was sent"}
    assert len(world.scratch.find("mock_outbox")) == 1
    assert len(world.scratch.find("pii_access_log")) == 1


def test_concurrent_retries_of_one_call_send_once_and_fail_no_task(world: World) -> None:
    async def scenario(service: NotifyService) -> list[CallToolResult]:
        grant = world.grant()
        return list(await asyncio.gather(*(send(world, service, telegram(world), grant) for _ in range(5))))

    results = run(world, scenario)
    assert all(not r.isError for r in results), [text(r) for r in results]
    assert sum(not (r.structuredContent or {}).get("replayed") for r in results) == 1
    assert len({(r.structuredContent or {}).get("notification_id") for r in results}) == 1
    assert len(world.scratch.find("mock_outbox")) == 1
    assert world.scratch.find("events", {"type": "task.sink_rejected"}) == []
    [claim] = world.scratch.find("notify_sends")
    assert claim["status"] == "sent"


# --- in-app and the webhook, on the real bus --------------------------------------------------------------


def test_a_blocked_run_reaches_the_orgs_hr_in_app_through_the_bus_once(world: World) -> None:
    async def scenario(service: NotifyService) -> None:
        with tenant_scope(A):
            for _ in range(2):  # the same event twice: one notification each
                await service.bus.publish("run.blocked", {"run_id": "run_x", "task_id": "t1"}, idempotency_key="rb-1")
        worker = service.bus.worker({"notify.in_app": in_app_handler(service.store)})
        while await worker.run_once() is not None:
            pass

    run(world, scenario)
    assert [(n["org_id"], n["user_id"], n["kind"]) for n in world.scratch.find("notifications")] == [
        (A, f"u_{A}_hr", "run.blocked")
    ]


def test_a_telegram_press_on_a_stored_button_publishes_one_decision_in_its_org(world: World) -> None:
    async def scenario(service: NotifyService) -> str:
        channel = TelegramMockChannel(service.store, TelegramMockTransport())
        with tenant_scope(B):
            entry = OutboxEntry(
                recipient_token=None, recipient_masked="99XXXXXX55", text="Approve?",
                ref={"type": "approval", "id": "ap_1"}, audience_user_id=f"u_{B}_mgr",
            )  # fmt: skip
            buttons = (DecisionButton("Approve", Decision.APPROVE), DecisionButton("Reject", Decision.REJECT))
            _, document = await channel.send(731957001, entry, buttons=buttons)
            await channel.store(document)
        channels = MockChannels(
            service.store, webhook_secret=secrets.token_urlsafe(32), decisions=BusDecisions(service.store)
        )
        update = TelegramUpdate.model_validate(
            {
                "update_id": 1,
                "callback_query": {
                    "id": "cbq-1",
                    "from": {"id": 731957001, "is_bot": False, "first_name": "M"},
                    "chat_instance": "1",
                    "data": document["buttons"][1]["callback_data"],
                },
            }
        )
        await channels.handle(update)
        await channels.handle(update)  # Telegram retries: one decision
        return str(document["_id"])

    outbox_id = run(world, scenario)
    [decision] = world.scratch.find("events", {"type": "decision.received"})
    assert decision["org_id"] == B
    assert (decision["payload"]["decision"], decision["payload"]["outbox_id"]) == ("reject", outbox_id)
    directory = world.scratch.find("telegram_callbacks")
    assert {d["org_id"] for d in directory} == {B}
    assert set(directory[0]) == {"_id", "org_id", "created_at"}
