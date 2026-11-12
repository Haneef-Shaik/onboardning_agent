"""Entry point: `python -m onb_mcp_mocks.notify.main` serves `notify_send` at `/mcp` (port 8210) and runs
the `notify.in_app` and `notify.approval` (HIL-1) consumers on the EVT-1 bus in the same process.

Startup makes the indexes the sink relies on, before the first call: PII-4's grant indexes (the
unique `{org_id, jti}` gives concurrent replays exactly one winner), the outbox and in-app indexes,
and the bus's own.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass

import uvicorn
from mcp.server.fastmcp import FastMCP
from onb_contracts.notify import APPROVAL_NOTIFY_CONSUMER, IN_APP_CONSUMER, NOTIFY_SUBSCRIPTIONS
from onb_crypto import (
    ORG_KEYS_COLLECTION,
    GrantAudit,
    Keyring,
    RepositoryOrgKeyStore,
    SinkResolver,
    ensure_grant_indexes,
    load_sink_policy,
)
from onb_data import AuditLog, DataStore, connect
from onb_eventbus import MongoEventBus
from onb_telemetry import init_telemetry

from ..telegram import TelegramMockTransport
from .approvals import approval_notify_handler, ensure_approval_notify_indexes
from .events import BusEvents
from .in_app import ensure_in_app_indexes, in_app_handler
from .outbox import TelegramMockChannel, ensure_outbox_indexes
from .sends import ensure_send_indexes
from .server import SERVER_NAME, build_notify_server
from .settings import NotifySettings, load_notify_settings
from .sink import NotifySink

__all__ = ["NotifyService", "build_service", "ensure_indexes", "main"]


@dataclass(frozen=True)
class NotifyService:
    store: DataStore
    bus: MongoEventBus
    server: FastMCP
    telegram: TelegramMockChannel


def build_service(settings: NotifySettings, store: DataStore | None = None) -> NotifyService:
    """The sink, its bus and its MCP server over the configured database."""
    store = store or connect(settings.mongo_uri, settings.database, app_name=SERVER_NAME)
    keyring = Keyring(settings.master_key, RepositoryOrgKeyStore(store.tenant(ORG_KEYS_COLLECTION)))
    policy = load_sink_policy(settings.policy_file)
    bus = MongoEventBus(store, subscriptions=NOTIFY_SUBSCRIPTIONS)

    def resolver(audit: GrantAudit) -> SinkResolver:
        return SinkResolver.create(keyring, store, settings.verifier, policy, audit)

    telegram = TelegramMockChannel(store, TelegramMockTransport())
    sink = NotifySink(
        store,
        resolver=resolver,
        verifier=settings.verifier,
        telegram=telegram,
        events=BusEvents(bus),
        auditor=AuditLog(store),
    )
    server = build_notify_server(sink, settings.context_key, host=settings.host, port=settings.port)
    return NotifyService(store=store, bus=bus, server=server, telegram=telegram)


async def ensure_indexes(service: NotifyService) -> None:
    await ensure_grant_indexes(service.store)
    await ensure_outbox_indexes(service.store)
    await ensure_in_app_indexes(service.store)
    await ensure_send_indexes(service.store)
    await ensure_approval_notify_indexes(service.store)
    await service.bus.ensure_indexes()
    await AuditLog(service.store).ensure_indexes()


async def serve(service: NotifyService, settings: NotifySettings) -> None:  # pragma: no cover - process loop
    await ensure_indexes(service)
    stop = asyncio.Event()
    worker = service.bus.worker(
        {
            IN_APP_CONSUMER: in_app_handler(service.store),
            APPROVAL_NOTIFY_CONSUMER: approval_notify_handler(service.store, service.telegram),
        }
    )
    config = uvicorn.Config(service.server.streamable_http_app(), host=settings.host, port=settings.port)
    worker_task = asyncio.create_task(worker.run(stop))
    try:
        await uvicorn.Server(config).serve()
    finally:
        stop.set()
        await worker_task


def main(env: Mapping[str, str] | None = None) -> None:  # pragma: no cover - process entry
    settings = load_notify_settings(os.environ if env is None else env)
    init_telemetry(SERVER_NAME, instrument=True)
    asyncio.run(serve(build_service(settings), settings))


if __name__ == "__main__":  # pragma: no cover
    main()
