"""Configuration comes from the environment; nothing secret has a default (PRD §7.11, §12)."""

from __future__ import annotations

from typing import Any

import pytest
from onb_mcp_mongo import main as main_module
from onb_mcp_mongo.server import build_server
from onb_mcp_mongo.settings import Settings, SettingsError, load_settings

from .fakes import FakeDatabase, make_store

KEY = "k" * 40
ENV = {"MCP_MONGO_URI": "mongodb://u:p@mongo:27017/?authSource=admin", "MCP_CALL_CONTEXT_KEY": KEY}


def test_settings_load_from_the_environment_with_safe_defaults() -> None:
    settings = load_settings(ENV)
    assert settings == Settings(
        mongo_uri=ENV["MCP_MONGO_URI"], database="onboarding", context_key=KEY, host="127.0.0.1", port=8000
    )


def test_settings_take_overrides() -> None:
    env = {**ENV, "MCP_MONGO_DATABASE": "onb_test", "MCP_MONGO_HOST": "0.0.0.0", "MCP_MONGO_PORT": "9001"}  # noqa: S104
    settings = load_settings(env)
    assert (settings.database, settings.host, settings.port) == ("onb_test", "0.0.0.0", 9001)  # noqa: S104


@pytest.mark.parametrize("missing", ["MCP_MONGO_URI", "MCP_CALL_CONTEXT_KEY"])
def test_a_missing_required_setting_fails_fast(missing: str) -> None:
    env = {k: v for k, v in ENV.items() if k != missing}
    with pytest.raises(SettingsError, match=missing):
        load_settings(env)


def test_a_short_context_key_fails_fast_without_echoing_it() -> None:
    with pytest.raises(SettingsError) as caught:
        load_settings({**ENV, "MCP_CALL_CONTEXT_KEY": "short-secret"})
    assert "short-secret" not in str(caught.value)


@pytest.mark.parametrize("port", ["0", "70000", "http"])
def test_a_bad_port_fails_fast(port: str) -> None:
    with pytest.raises(SettingsError, match="MCP_MONGO_PORT"):
        load_settings({**ENV, "MCP_MONGO_PORT": port})


def test_the_settings_repr_hides_the_secrets() -> None:
    text = repr(load_settings(ENV))
    assert KEY not in text
    assert "u:p@" not in text


def test_build_server_refuses_a_short_key() -> None:
    with pytest.raises(ValueError, match="32"):
        build_server(make_store(FakeDatabase()), "short")


def test_main_serves_the_app_with_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(main_module, "init_telemetry", lambda name, **kw: seen.setdefault("telemetry", (name, kw)))
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: seen.setdefault("uvicorn", (app, kw)))
    main_module.main(ENV)
    assert seen["telemetry"] == ("mcp-mongo", {"instrument": True})
    app, kwargs = seen["uvicorn"]
    assert callable(app)
    assert kwargs == {"host": "127.0.0.1", "port": 8000}
