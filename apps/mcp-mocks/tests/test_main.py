"""Configuration from the environment only; the context key has no default (PRD §12)."""

from __future__ import annotations

import secrets
from typing import Any

import pytest
from onb_mcp_mocks import main as main_module
from onb_mcp_mocks.settings import SettingsError, load_settings

KEY = secrets.token_urlsafe(40)
ENV = {"MCP_MOCKS_CONTEXT_KEY": KEY}


def test_settings_load_with_safe_defaults() -> None:
    settings = load_settings(ENV)
    assert settings.context_key == KEY
    assert (settings.host, settings.port) == ("127.0.0.1", 8200)
    assert KEY not in repr(settings)


def test_settings_take_overrides() -> None:
    settings = load_settings({**ENV, "MCP_MOCKS_HOST": "0.0.0.0", "MCP_MOCKS_PORT": "9200"})  # noqa: S104
    assert (settings.host, settings.port) == ("0.0.0.0", 9200)  # noqa: S104


def test_a_missing_key_fails_fast() -> None:
    with pytest.raises(SettingsError, match="MCP_MOCKS_CONTEXT_KEY"):
        load_settings({})


def test_a_short_key_fails_fast_without_echoing_it() -> None:
    with pytest.raises(SettingsError) as caught:
        load_settings({"MCP_MOCKS_CONTEXT_KEY": "short-secret"})
    assert "short-secret" not in str(caught.value)


@pytest.mark.parametrize("port", ["0", "70000", "http"])
def test_a_bad_port_fails_fast(port: str) -> None:
    with pytest.raises(SettingsError, match="MCP_MOCKS_PORT"):
        load_settings({**ENV, "MCP_MOCKS_PORT": port})


def test_main_serves_the_server_on_the_configured_address(monkeypatch: pytest.MonkeyPatch) -> None:
    served: dict[str, Any] = {}
    monkeypatch.setattr(main_module, "init_telemetry", lambda *a, **k: None)
    monkeypatch.setattr(main_module.uvicorn, "run", lambda app, **kw: served.update(app=app, **kw))
    main_module.main({**ENV, "MCP_MOCKS_PORT": "9201"})
    assert (served["host"], served["port"]) == ("127.0.0.1", 9201)
    assert served["app"] is not None
