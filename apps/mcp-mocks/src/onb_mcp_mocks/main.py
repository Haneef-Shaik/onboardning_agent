"""Entry point: `python -m onb_mcp_mocks.main` serves the mock tools over streamable HTTP at `/mcp`."""

from __future__ import annotations

import os
from collections.abc import Mapping

import uvicorn
from onb_telemetry import init_telemetry

from .server import SERVER_NAME, build_server
from .settings import load_settings
from .store import InMemoryIdempotencyStore

__all__ = ["main"]


def main(env: Mapping[str, str] | None = None) -> None:
    settings = load_settings(os.environ if env is None else env)
    init_telemetry(SERVER_NAME, instrument=True)
    server = build_server(InMemoryIdempotencyStore(), settings.context_key, host=settings.host, port=settings.port)
    uvicorn.run(server.streamable_http_app(), host=settings.host, port=settings.port)


if __name__ == "__main__":  # pragma: no cover
    main()
