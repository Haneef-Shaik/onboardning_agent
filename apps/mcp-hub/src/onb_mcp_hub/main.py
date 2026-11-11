"""Entry point: `python -m onb_mcp_hub.main` serves the hub over streamable HTTP at `/mcp`."""

from __future__ import annotations

import os
from collections.abc import Mapping

import uvicorn
from onb_agents.registry import AgentRegistry
from onb_agents.registry.mongo import MongoRegistryStore
from onb_data import AuditLog, connect
from onb_telemetry import init_telemetry

from .catalog import mongo_catalog
from .downstream import McpDownstream
from .gateway import Gateway
from .server import SERVER_NAME, build_hub
from .settings import Settings, load_settings

__all__ = ["build_gateway", "main"]


def build_gateway(settings: Settings) -> Gateway:
    """The gateway over the hub's database user and the configured downstream servers."""
    store = connect(settings.mongo_uri, settings.database, app_name=SERVER_NAME)
    audit = AuditLog(store)
    return Gateway(
        catalog=mongo_catalog(),
        registry=AgentRegistry(MongoRegistryStore(store), audit),
        auditor=audit,
        downstream=McpDownstream(settings.downstream, settings.context_key),
        token_key=settings.token_key,
        secrets=(settings.context_key, settings.mongo_uri),
    )


def main(env: Mapping[str, str] | None = None) -> None:
    settings = load_settings(os.environ if env is None else env)
    init_telemetry(SERVER_NAME, instrument=True)
    hub = build_hub(build_gateway(settings), allowed_hosts=settings.allowed_hosts)
    uvicorn.run(hub.app, host=settings.host, port=settings.port)


if __name__ == "__main__":  # pragma: no cover
    main()
