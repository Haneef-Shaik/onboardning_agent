"""Entry point: `python -m onb_mcp_mongo.main` serves streamable HTTP at `/mcp`."""

from __future__ import annotations

import os
from collections.abc import Mapping

import uvicorn
from onb_data import connect
from onb_telemetry import init_telemetry

from .db_users import READ_ONLY, READ_WRITE, assert_least_privilege
from .server import SERVER_NAME, build_server
from .settings import load_settings

__all__ = ["main"]


def main(env: Mapping[str, str] | None = None) -> None:
    settings = load_settings(os.environ if env is None else env)
    # Refuse to serve over a user wider than its role (MCP-3): the roles bound any policy bug.
    assert_least_privilege(settings.mongo_uri, READ_ONLY, settings.database)
    assert_least_privilege(settings.write_uri, READ_WRITE, settings.database)
    init_telemetry(SERVER_NAME, instrument=True)
    store = connect(settings.mongo_uri, settings.database, app_name=SERVER_NAME)
    write_store = connect(settings.write_uri, settings.database, app_name=SERVER_NAME)
    server = build_server(store, settings.context_key, write_store=write_store, host=settings.host, port=settings.port)
    uvicorn.run(server.streamable_http_app(), host=settings.host, port=settings.port)


if __name__ == "__main__":  # pragma: no cover
    main()
