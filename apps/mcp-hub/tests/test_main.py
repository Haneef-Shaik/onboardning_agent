"""Configuration from the environment only; nothing secret has a default (PRD §7.11, §12)."""

from __future__ import annotations

import secrets
from typing import Any

import pytest
from onb_mcp_hub import main as main_module
from onb_mcp_hub.settings import SettingsError, load_settings

TOKEN_KEY = secrets.token_urlsafe(40)
CONTEXT_KEY = secrets.token_urlsafe(40)
MOCKS_KEY = secrets.token_urlsafe(40)
DB_URI = f"mongodb://mcp_hub:{secrets.token_hex(12)}@mongo:27017/?authSource=admin"
ENV = {
    "AGENT_TOKEN_KEY": TOKEN_KEY,
    "MCP_CALL_CONTEXT_KEY": CONTEXT_KEY,
    "MCP_HUB_MONGO_URI": DB_URI,
    "MCP_HUB_MONGO_SERVER_URL": "http://mcp-mongo:8000/mcp",
    "MCP_MOCKS_CONTEXT_KEY": MOCKS_KEY,
    "MCP_HUB_MOCKS_SERVER_URL": "http://mcp-mocks:8200/mcp",
}


def test_settings_load_from_the_environment_with_safe_defaults() -> None:
    settings = load_settings(ENV)
    assert settings.token_key == TOKEN_KEY
    assert settings.context_key == CONTEXT_KEY
    assert settings.mongo_uri == DB_URI
    assert settings.downstream == {"mongo": "http://mcp-mongo:8000/mcp", "mocks": "http://mcp-mocks:8200/mcp"}
    assert settings.server_keys == {"mocks": MOCKS_KEY}
    assert (settings.database, settings.host, settings.port) == ("onboarding", "127.0.0.1", 8100)
    assert settings.allowed_hosts == ("127.0.0.1", "localhost", "mcp-hub")


def test_settings_take_overrides() -> None:
    env = {**ENV, "MCP_HUB_DATABASE": "onb_x", "MCP_HUB_PORT": "9100", "MCP_HUB_ALLOWED_HOSTS": "hub.internal, mcp-hub"}
    settings = load_settings(env)
    assert (settings.database, settings.port, settings.allowed_hosts) == ("onb_x", 9100, ("hub.internal", "mcp-hub"))


@pytest.mark.parametrize("missing", sorted(ENV))
def test_a_missing_required_setting_fails_fast(missing: str) -> None:
    with pytest.raises(SettingsError, match=missing):
        load_settings({k: v for k, v in ENV.items() if k != missing})


@pytest.mark.parametrize("name", ["AGENT_TOKEN_KEY", "MCP_CALL_CONTEXT_KEY", "MCP_MOCKS_CONTEXT_KEY"])
def test_a_short_key_fails_fast_without_echoing_it(name: str) -> None:
    with pytest.raises(SettingsError) as caught:
        load_settings({**ENV, name: "short-secret"})
    assert "short-secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("name", "copied"),
    [
        ("MCP_CALL_CONTEXT_KEY", "AGENT_TOKEN_KEY"),
        ("MCP_MOCKS_CONTEXT_KEY", "AGENT_TOKEN_KEY"),
        ("MCP_MOCKS_CONTEXT_KEY", "MCP_CALL_CONTEXT_KEY"),
    ],
)
def test_the_keys_must_all_differ(name: str, copied: str) -> None:
    """One key per role: a mocks key that equals mongo's would let either server mint the other's contexts."""
    with pytest.raises(SettingsError, match="differ"):
        load_settings({**ENV, name: ENV[copied]})


@pytest.mark.parametrize("port", ["0", "70000", "http"])
def test_a_bad_port_fails_fast(port: str) -> None:
    with pytest.raises(SettingsError, match="MCP_HUB_PORT"):
        load_settings({**ENV, "MCP_HUB_PORT": port})


@pytest.mark.parametrize("name", ["MCP_HUB_MONGO_SERVER_URL", "MCP_HUB_MOCKS_SERVER_URL"])
def test_a_downstream_url_must_be_http(name: str) -> None:
    with pytest.raises(SettingsError, match=name):
        load_settings({**ENV, name: "file:///etc/passwd"})


def test_the_settings_repr_hides_the_secrets() -> None:
    text = repr(load_settings(ENV))
    for secret in (TOKEN_KEY, CONTEXT_KEY, MOCKS_KEY, DB_URI):
        assert secret not in text


def test_main_wires_telemetry_the_registry_the_audit_log_and_serves_the_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(main_module, "init_telemetry", lambda name, **kw: seen.setdefault("telemetry", (name, kw)))
    monkeypatch.setattr("onb_mcp_hub.main.uvicorn.run", lambda app, **kw: seen.setdefault("served", (app, kw)))
    main_module.main(ENV)
    assert seen["telemetry"] == ("mcp-hub", {"instrument": True})
    app, options = seen["served"]
    assert options == {"host": "127.0.0.1", "port": 8100}
    assert [route.path for route in app.routes] == ["/mcp"]


def test_the_gateway_main_builds_holds_every_credential_it_must_never_return(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = main_module.build_gateway(load_settings(ENV))
    held = set(gateway.secrets)
    assert {TOKEN_KEY, CONTEXT_KEY, MOCKS_KEY, DB_URI} <= held


def test_the_gateway_main_builds_serves_the_mock_tools() -> None:
    gateway = main_module.build_gateway(load_settings(ENV))
    assert {"mocks__create_bitbucket_account", "mocks__provision_laptop", "mongo__find"} <= set(gateway.catalog)


def test_the_database_password_alone_is_a_held_credential() -> None:
    password = DB_URI.split(":")[2].split("@")[0]
    held = set(main_module.build_gateway(load_settings(ENV)).secrets)
    assert {password, f"mcp_hub:{password}"} <= held
