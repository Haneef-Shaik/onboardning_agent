"""mcp-notify settings and wiring: secrets from the environment only, fail fast, the indexes at startup."""

from __future__ import annotations

import asyncio
import base64
import secrets
from pathlib import Path

import pytest
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_crypto import GrantSigningKey
from onb_mcp_mocks.notify.main import build_service, ensure_indexes
from onb_mcp_mocks.notify.settings import SettingsError, load_notify_settings

POLICY = Path(__file__).resolve().parents[3] / "config" / "pii_sink_policy.yaml"


def env(**overrides: str) -> dict[str, str]:
    base = {
        "MCP_NOTIFY_CONTEXT_KEY": "notify-context-key-0123456789abcdef-xyz",
        "MCP_NOTIFY_MONGO_URI": "mongodb://notify:secret@localhost:27017/?replicaSet=rs0",
        "PII_MASTER_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
        "PII_GRANT_VERIFY_KEYS": GrantSigningKey.generate().public_key.encoded(),
        "PII_SINK_POLICY_FILE": str(POLICY),
    }
    return {**base, **overrides}


def test_settings_load_with_defaults() -> None:
    settings = load_notify_settings(env())
    assert (settings.host, settings.port, settings.database) == ("127.0.0.1", 8210, "onboarding")
    assert "secret" not in repr(settings)
    assert "notify-context-key" not in repr(settings)


@pytest.mark.parametrize(
    ("overrides", "named"),
    [
        ({"MCP_NOTIFY_CONTEXT_KEY": ""}, "MCP_NOTIFY_CONTEXT_KEY"),
        ({"MCP_NOTIFY_CONTEXT_KEY": "short"}, "MCP_NOTIFY_CONTEXT_KEY"),
        ({"MCP_NOTIFY_MONGO_URI": ""}, "MCP_NOTIFY_MONGO_URI"),
        ({"PII_MASTER_KEY": ""}, "PII_MASTER_KEY"),
        ({"PII_MASTER_KEY": "bm90LTMyLWJ5dGVz"}, "PII_MASTER_KEY"),
        ({"PII_GRANT_VERIFY_KEYS": ""}, "PII_GRANT_VERIFY_KEYS"),
        ({"PII_SINK_POLICY_FILE": "/nonexistent/policy.yaml"}, "PII_SINK_POLICY_FILE"),
        ({"MCP_NOTIFY_PORT": "0"}, "MCP_NOTIFY_PORT"),
        ({"MCP_NOTIFY_PORT": "http"}, "MCP_NOTIFY_PORT"),
    ],
)
def test_a_missing_or_malformed_setting_fails_at_startup_naming_the_variable(
    overrides: dict[str, str], named: str
) -> None:
    with pytest.raises(SettingsError, match=named) as raised:
        load_notify_settings(env(**overrides))
    assert "secret" not in str(raised.value)


def test_the_service_makes_the_grant_outbox_in_app_and_bus_indexes_at_startup() -> None:
    db = MemoryDatabase()
    service = build_service(load_notify_settings(env()), memory_store(db))
    asyncio.run(ensure_indexes(service))
    unique = {
        name: [index.keys for index in db[name].indexes if index.unique]
        for name in ("pii_grant_jti", "mock_outbox", "notifications", "events")
    }
    assert ("org_id", "jti") in unique["pii_grant_jti"]
    assert ("org_id", "grant_digest") in unique["mock_outbox"]
    assert ("org_id", "dedupe_key") in unique["notifications"]
    assert unique["events"], "the bus's idempotency index"


def test_the_service_serves_notify_send_and_subscribes_the_in_app_consumer() -> None:
    service = build_service(load_notify_settings(env()), memory_store(MemoryDatabase()))
    assert service.bus.consumers() == ("notify.in_app", "orchestrator.fail_task")  # RUN-3 subscribes fail_task
    tools = asyncio.run(service.server.list_tools())
    assert [t.name for t in tools] == ["notify_send"]
