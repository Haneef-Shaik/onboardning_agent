"""The self-test toolbox: an MCP-hub stand-in that scopes every call to the case's org, denies what
the hub would deny (MASTER-PLAN §9; MCP-2, HUB-1), and audits every call, denied ones included."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from onb_data import tenant_scope
from onb_evals.capture import ToolOutcome
from onb_evals.cases import Actor
from onb_evals.fixture import load_fixture
from onb_evals.toolbox import TOOL_NAMES, ToolBox

from .memory_store import MemoryStore, as_data_store
from .support import SEED

ACME = Actor(org_id="org_acme", role="SYSTEM")


def _box() -> tuple[MemoryStore, ToolBox]:
    store = MemoryStore()
    fixture = asyncio.run(load_fixture(as_data_store(store), SEED))
    return store, ToolBox(as_data_store(store), ACME, fixture.tokens)


def _call(box: ToolBox, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(box.call(name, args))


def test_find_is_scoped_to_the_cases_org() -> None:
    _, box = _box()
    result = _call(box, "find", {"collection": "onboarding_runs", "filter": {"_id": "run_globex_001"}})
    assert result == {"ok": True, "documents": []}
    entry = box.audit[-1]
    assert (entry.outcome, entry.result_org_ids, entry.org_id) == (ToolOutcome.ALLOWED, (), "org_acme")


def test_find_returns_documents_and_records_their_orgs() -> None:
    _, box = _box()
    result = _call(box, "find", {"collection": "onboarding_runs", "filter": {"status": "blocked"}})
    assert [d["_id"] for d in result["documents"]] == ["run_acme_002"]
    assert box.audit[-1].result_org_ids == ("org_acme",)
    assert box.calls[-1].outcome is ToolOutcome.ALLOWED


def test_count_counts_in_the_org() -> None:
    _, box = _box()
    result = _call(box, "count", {"collection": "new_hires", "filter": {"start_date": {"$gte": "2026-12-01"}}})
    assert result == {"ok": True, "count": 3}


@pytest.mark.parametrize(
    ("name", "args", "reason"),
    [
        ("find", {"collection": "new_hires", "filter": {"$where": "sleep(5000) || true"}}, "operator"),
        ("find", {"collection": "new_hires", "filter": {"a": {"$function": {}}}}, "operator"),
        ("find", {"collection": "pii_vault", "filter": {}}, "collection"),
        ("find", {"collection": "org_keys", "filter": {}}, "collection"),
        ("find", {"collection": "onboarding_runs", "filter": {"org_id": "org_globex"}}, "org_id"),
        (
            "update_one",
            {"collection": "new_hires", "filter": {"_id": "h_acme_001"}, "set": {"pii.mobile": "x"}},
            "collection",
        ),
        ("update_one", {"collection": "run_tasks", "filter": {}, "set": {"org_id": "org_globex"}}, "org_id"),
        ("notify_send", {"channel": "telegram", "recipient_token": "+91 90000 00001", "body": "hi"}, "token"),
        ("notify_send", {"channel": "fax", "recipient_token": "x", "body": "hi"}, "channel"),
        ("publish_template", {"template_key": "engineer-india"}, "unknown tool"),
        ("find", {"collection": "new_hires", "filter": "not an object"}, "arguments"),
    ],
)
def test_the_hub_stand_in_denies_and_audits(name: str, args: dict[str, Any], reason: str) -> None:
    store, box = _box()
    result = _call(box, name, args)
    assert result["ok"] is False
    assert reason in result["error"]
    assert box.calls[-1].outcome is ToolOutcome.DENIED
    assert box.audit[-1].outcome is ToolOutcome.DENIED
    stored = store.data["audit_log"][-1]
    assert stored["org_id"] == "org_acme"
    assert stored["outcome"] == "denied"
    assert json.loads(stored["args_json"]) == json.loads(json.dumps(args, default=str))


def test_update_one_writes_in_the_org_and_reports_what_matched() -> None:
    store, box = _box()
    args = {
        "collection": "run_tasks",
        "filter": {"run_id": "run_acme_003", "task_id": "schedule_orientation"},
        "set": {"status": "done"},
    }
    assert _call(box, "update_one", args) == {"ok": True, "matched": 1, "modified": 1}
    task = next(
        d for d in store.data["run_tasks"] if d["run_id"] == "run_acme_003" and d["task_id"] == "schedule_orientation"
    )
    assert task["status"] == "done"


def test_notify_send_takes_a_vault_token_and_writes_the_mock_outbox() -> None:
    store, box = _box()
    token = box.tokens.real_for("ptk_seed_acme001_mobile")
    result = _call(box, "notify_send", {"channel": "telegram", "recipient_token": token, "body": "Welcome"})
    assert result == {"ok": True, "queued": True}
    with tenant_scope("org_acme"):
        outbox = asyncio.run(as_data_store(store).tenant("mock_outbox").find({}))
    assert outbox[0]["recipient_token"] == token


def test_the_tool_schemas_name_every_tool() -> None:
    _, box = _box()
    names = [schema["function"]["name"] for schema in box.tool_schemas()]
    assert names == list(TOOL_NAMES)
    assert all(schema["type"] == "function" for schema in box.tool_schemas())
