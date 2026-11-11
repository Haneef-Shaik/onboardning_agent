"""A notification sink over an in-memory database with the real vault, grants, resolver and audit.

Only the MongoDB driver is replaced (`onb_api.testing_mongo`); tenant scoping, the vault's
encryption and record signatures, PII-4's grant checks and the `pii_access_log` writes all run their
production code. Events are recorded instead of going through the transactional bus (the bus runs
for real in `tests_int`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from onb_api.testing_mongo import MemoryDatabase, memory_keyring, memory_store, seed_vault_value
from onb_contracts.notify import TELEGRAM_SINK, NotifySendArgs, NotifySendResult
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
from onb_data import DataStore, tenant_scope
from onb_data.testing import InMemoryAuditLog
from onb_mcp_mocks.notify.audit import Caller
from onb_mcp_mocks.notify.outbox import TelegramMockChannel, ensure_outbox_indexes
from onb_mcp_mocks.notify.sink import NotifySink
from onb_mcp_mocks.telegram import TelegramMockTransport
from opentelemetry import trace

POLICY = load_sink_policy(Path(__file__).resolve().parents[3] / "config" / "pii_sink_policy.yaml")
ORG_A, ORG_B = "org_acme", "org_globex"
HIRE, OTHER_HIRE, B_HIRE = "h_acme_001", "h_acme_002", "h_globex_001"
MOBILE, OTHER_MOBILE, B_MOBILE = "9876501234", "9123409876", "9988776655"
EMAIL = "priya.k@example.com"
CALLER = Caller(org_id=ORG_A, agent="templates_agent", run_id="run_acme_001")


@dataclass
class RecordedEvents:
    """`NotifyEvents` that runs the change and records the event (the bus's transaction is tested live)."""

    published: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def sent(
        self, payload: Mapping[str, Any], *, key: str, run_id: str | None, change: Callable[[], Awaitable[None]]
    ) -> None:
        await change()
        self.published.append(("notification.sent", {**payload, "_key": key}))

    async def rejected(self, payload: Mapping[str, Any], *, run_id: str) -> None:
        self.published.append(("task.sink_rejected", dict(payload)))


@dataclass
class World:
    db: MemoryDatabase
    store: DataStore
    sink: NotifySink
    issuer: GrantIssuer
    other_key: GrantSigningKey
    audit: InMemoryAuditLog
    events: RecordedEvents
    transport: TelegramMockTransport
    tokens: dict[str, str]

    def grant(self, *, subject: str = HIRE, org: str = ORG_A, task: str = "welcome_message", **kw: Any) -> str:
        with tenant_scope(org):
            return self.issuer.issue_grant(
                org,
                run_id=kw.pop("run_id", CALLER.run_id),
                task_id=task,
                subject_id=subject,
                fields=kw.pop("fields", [VaultField.MOBILE]),
                sink=kw.pop("sink", TELEGRAM_SINK),
                **kw,
            )

    def send(self, grant: str | None, args: Mapping[str, Any], caller: Caller = CALLER) -> NotifySendResult:
        async def go() -> NotifySendResult:
            # The server runs every call inside its own span; the guardrail event lands on it.
            tracer = trace.get_tracer("notify-tests")
            with tracer.start_as_current_span("mcp.notify.notify_send"), tenant_scope(caller.org_id):
                return await self.sink.send(caller, grant, NotifySendArgs.model_validate(args))

        return asyncio.run(go())

    def telegram(
        self, which: str = "mobile", body: str = "Hi Priya, welcome aboard on 1 December 2026!"
    ) -> dict[str, Any]:
        return {"channel": "telegram", "recipient_token": self.tokens[which], "body": body}

    def records(self, org: str = ORG_A) -> list[Any]:
        async def go() -> list[Any]:
            with tenant_scope(org):
                from onb_data import AuditQuery

                return list((await self.audit.list(AuditQuery(), page=1, limit=100)).entries)

        return asyncio.run(go())


def build_world(*, clock: Callable[[], datetime] | None = None) -> World:
    db = MemoryDatabase()
    store = memory_store(db)
    keyring = memory_keyring(store)
    signing = GrantSigningKey.generate()
    verifier = GrantVerifier([signing.public_key])
    audit = InMemoryAuditLog()
    events = RecordedEvents()
    transport = TelegramMockTransport()

    def resolver(hook: GrantAudit) -> SinkResolver:
        return SinkResolver.create(keyring, store, verifier, POLICY, hook)

    async def seed() -> dict[str, str]:
        await ensure_grant_indexes(store)
        await ensure_outbox_indexes(store)
        for org in (ORG_A, ORG_B):
            with tenant_scope(org):
                await keyring.create(org)

        async def put(org: str, subject: str, field: VaultField, value: str) -> str:
            return await seed_vault_value(keyring, store, org, subject_id=subject, field=field, value=value)

        tokens = {
            "mobile": await put(ORG_A, HIRE, VaultField.MOBILE, MOBILE),
            "email": await put(ORG_A, HIRE, VaultField.EMAIL, EMAIL),
            "other": await put(ORG_A, OTHER_HIRE, VaultField.MOBILE, OTHER_MOBILE),
            "b_mobile": await put(ORG_B, B_HIRE, VaultField.MOBILE, B_MOBILE),
        }
        with tenant_scope(ORG_A):
            await store.tenant("users").insert_many(
                [{"_id": "u_acme_hr1", "role": "HR"}, {"_id": "u_acme_mgr_ravi", "role": "MANAGER"}]
            )
        with tenant_scope(ORG_B):
            await store.tenant("users").insert_one({"_id": "u_globex_hr1", "role": "HR"})
        return tokens

    tokens = asyncio.run(seed())
    sink = NotifySink(
        store,
        resolver=resolver,
        verifier=verifier,
        telegram=TelegramMockChannel(store, transport),
        events=events,
        auditor=audit,
        **({"clock": clock} if clock else {}),
    )
    return World(
        db=db,
        store=store,
        sink=sink,
        issuer=GrantIssuer(signing, POLICY),
        other_key=GrantSigningKey.generate(),
        audit=audit,
        events=events,
        transport=transport,
        tokens=tokens,
    )


class MovableClock:
    """Real time plus an offset the test moves forward."""

    def __init__(self) -> None:
        self._offset = timedelta()

    def advance(self, by: timedelta) -> None:
        self._offset += by

    def __call__(self) -> datetime:
        return datetime.now(UTC) + self._offset
