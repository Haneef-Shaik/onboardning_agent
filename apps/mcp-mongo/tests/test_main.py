"""Configuration comes from the environment; nothing secret has a default (PRD §7.11, §12)."""

from __future__ import annotations

from typing import Any

import pytest
from onb_mcp_mongo import main as main_module
from onb_mcp_mongo.db_users import READ_ONLY, READ_WRITE, AccessSpec, PrivilegeError
from onb_mcp_mongo.server import build_server
from onb_mcp_mongo.settings import Settings, SettingsError, load_settings

from .fakes import FakeDatabase, make_store

KEY = "k" * 40
READ_URI = "mongodb://ro:rpw@mongo:27017/?authSource=admin"
WRITE_URI = "mongodb://rw:wpw@mongo:27017/?authSource=admin"
ENV = {"MCP_MONGO_URI": READ_URI, "MCP_MONGO_WRITE_URI": WRITE_URI, "MCP_CALL_CONTEXT_KEY": KEY}


def test_settings_load_from_the_environment_with_safe_defaults() -> None:
    settings = load_settings(ENV)
    assert settings == Settings(
        mongo_uri=READ_URI,
        write_uri=WRITE_URI,
        database="onboarding",
        context_key=KEY,
        host="127.0.0.1",
        port=8000,
    )


def test_settings_take_overrides() -> None:
    env = {**ENV, "MCP_MONGO_DATABASE": "onb_test", "MCP_MONGO_HOST": "0.0.0.0", "MCP_MONGO_PORT": "9001"}  # noqa: S104
    settings = load_settings(env)
    assert (settings.database, settings.host, settings.port) == ("onb_test", "0.0.0.0", 9001)  # noqa: S104


@pytest.mark.parametrize("missing", ["MCP_MONGO_URI", "MCP_MONGO_WRITE_URI", "MCP_CALL_CONTEXT_KEY"])
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
    assert "rpw" not in text
    assert "wpw" not in text


def test_the_read_and_write_uris_must_differ() -> None:
    with pytest.raises(SettingsError, match="MCP_MONGO_WRITE_URI"):
        load_settings({**ENV, "MCP_MONGO_WRITE_URI": READ_URI})


def test_build_server_refuses_a_short_key() -> None:
    with pytest.raises(ValueError, match="32"):
        build_server(make_store(FakeDatabase()), "short")


def _no_privilege_check(monkeypatch: pytest.MonkeyPatch, seen: dict[str, Any]) -> None:
    def record(uri: str, spec: AccessSpec, database: str) -> None:
        seen.setdefault("checked", []).append((uri, spec, database))

    monkeypatch.setattr(main_module, "assert_least_privilege", record)


def test_main_serves_the_app_with_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    _no_privilege_check(monkeypatch, seen)
    monkeypatch.setattr(main_module, "init_telemetry", lambda name, **kw: seen.setdefault("telemetry", (name, kw)))
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: seen.setdefault("uvicorn", (app, kw)))
    main_module.main(ENV)
    assert seen["telemetry"] == ("mcp-mongo", {"instrument": True})
    app, kwargs = seen["uvicorn"]
    assert callable(app)
    assert kwargs == {"host": "127.0.0.1", "port": 8000}


def test_main_checks_both_users_before_serving_and_routes_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    _no_privilege_check(monkeypatch, seen)
    stores: dict[str, Any] = {}

    def fake_connect(uri: str, database: str, **kw: Any) -> Any:
        stores[uri] = make_store(FakeDatabase())
        return stores[uri]

    def fake_build(store: Any, key: str, **kw: Any) -> Any:
        seen["build"] = (store, kw["write_store"])
        return build_server(store, key, **kw)

    monkeypatch.setattr(main_module, "connect", fake_connect)
    monkeypatch.setattr(main_module, "build_server", fake_build)
    monkeypatch.setattr(main_module, "init_telemetry", lambda name, **kw: None)
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: None)
    main_module.main(ENV)
    assert seen["checked"] == [(READ_URI, READ_ONLY, "onboarding"), (WRITE_URI, READ_WRITE, "onboarding")]
    assert seen["build"] == (stores[READ_URI], stores[WRITE_URI])


def test_main_refuses_to_serve_over_a_wider_user(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(uri: str, spec: AccessSpec, database: str) -> None:
        raise PrivilegeError("too wide")

    served: list[object] = []
    monkeypatch.setattr(main_module, "assert_least_privilege", refuse)
    monkeypatch.setattr(main_module, "init_telemetry", lambda name, **kw: None)
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: served.append(app))
    with pytest.raises(PrivilegeError):
        main_module.main(ENV)
    assert served == []
