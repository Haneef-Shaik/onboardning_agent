"""`MongoRegistryStore` and the sync command, against a recording stand-in for `onb_data.DataStore`.

What reaches the repositories is asserted here; real MongoDB semantics (version-checked updates,
the transaction rolling back audit records) are proved in `apps/api/tests_int/test_mongo_registry.py`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
from onb_agents.registry import SyncChange, SyncReport
from onb_agents.registry.__main__ import main, parse_args, render
from onb_agents.registry.mongo import AGENT_REGISTRY, ORGS, TOOL_CATALOG, MongoRegistryStore
from onb_data import DataStore, UpdateOutcome
from pymongo.errors import DuplicateKeyError

ROOT = Path(__file__).resolve().parents[3]
Document = dict[str, Any]


@dataclass
class FakeRepository:
    docs: list[Document] = field(default_factory=list)
    matched: int = 1
    duplicate: bool = False
    calls: list[tuple[str, Any, Any]] = field(default_factory=list)

    async def find(self, query: Mapping[str, Any], **options: Any) -> list[Document]:
        self.calls.append(("find", dict(query), options))
        return list(self.docs)

    async def find_one(self, query: Mapping[str, Any]) -> Document | None:
        self.calls.append(("find_one", dict(query), None))
        return self.docs[0] if self.docs else None

    async def insert_one(self, document: Mapping[str, Any]) -> Any:
        self.calls.append(("insert_one", dict(document), None))
        if self.duplicate:
            raise DuplicateKeyError("E11000")
        return document["_id"]

    async def update_one(self, query: Mapping[str, Any], update: Mapping[str, Any]) -> UpdateOutcome:
        self.calls.append(("update_one", dict(query), dict(update)))
        return UpdateOutcome(matched=self.matched, modified=self.matched)


@dataclass
class FakeDataStore:
    repositories: dict[str, FakeRepository] = field(default_factory=dict)
    transactions: int = 0

    def shared(self, name: str) -> FakeRepository:
        return self.repositories.setdefault(name, FakeRepository())

    async def run_transaction(self, callback: Callable[[], Awaitable[Any]]) -> Any:
        self.transactions += 1
        return await callback()


def _store(fake: FakeDataStore) -> MongoRegistryStore:
    return MongoRegistryStore(cast(DataStore, fake))


def test_reads_go_through_the_global_repositories() -> None:
    fake = FakeDataStore()
    fake.shared(ORGS).docs = [{"_id": "org_acme"}, {"_id": "org_globex"}]
    fake.shared(AGENT_REGISTRY).docs = [{"_id": "db_agent", "version": 1}]
    store = _store(fake)
    assert asyncio.run(store.org_ids()) == ["org_acme", "org_globex"]
    assert asyncio.run(store.agents()) == [{"_id": "db_agent", "version": 1}]
    assert asyncio.run(store.agent("db_agent")) == {"_id": "db_agent", "version": 1}
    assert asyncio.run(store.catalog()) is None
    assert fake.shared(ORGS).calls == [("find", {}, {"projection": {"_id": 1}})]
    assert fake.shared(AGENT_REGISTRY).calls[1] == ("find_one", {"_id": "db_agent"}, None)
    assert fake.shared(TOOL_CATALOG).calls == [("find_one", {"_id": "tool_catalog"}, None)]


def test_a_replace_is_checked_against_the_stored_version() -> None:
    fake = FakeDataStore()
    store = _store(fake)
    document = {"_id": "db_agent", "version": 2, "sha256": "x"}
    assert asyncio.run(store.replace_agent("db_agent", 1, document))
    assert fake.shared(AGENT_REGISTRY).calls[-1] == (
        "update_one",
        {"_id": "db_agent", "version": 1},
        {"$set": {"version": 2, "sha256": "x"}},
    )
    fake.shared(AGENT_REGISTRY).matched = 0
    assert not asyncio.run(store.replace_agent("db_agent", 1, document))


def test_the_catalog_is_inserted_once_then_replaced_by_version() -> None:
    fake = FakeDataStore()
    store = _store(fake)
    document = {"_id": "tool_catalog", "version": 1}
    assert asyncio.run(store.put_catalog(None, document))
    fake.shared(TOOL_CATALOG).duplicate = True
    assert not asyncio.run(store.put_catalog(None, document))
    assert asyncio.run(store.put_catalog(1, {**document, "version": 2}))
    assert fake.shared(TOOL_CATALOG).calls[-1][1] == {"_id": "tool_catalog", "version": 1}
    fake.shared(TOOL_CATALOG).matched = 0
    assert not asyncio.run(store.put_catalog(1, document))


def test_agent_inserts_and_transactions_reach_the_store() -> None:
    fake = FakeDataStore()
    store = _store(fake)
    asyncio.run(store.insert_agent({"_id": "db_agent"}))

    async def work() -> str:
        return "done"

    assert asyncio.run(store.atomically(work)) == "done"
    assert fake.transactions == 1
    assert fake.shared(AGENT_REGISTRY).calls == [("insert_one", {"_id": "db_agent"}, None)]


# --- the command ---------------------------------------------------------------------------------


def test_the_command_needs_an_actor() -> None:
    with pytest.raises(SystemExit):
        parse_args(["sync"])
    args = parse_args(["sync", "--actor", "u_acme_hradmin"])
    assert (args.actor, args.config) == ("u_acme_hradmin", Path("config"))


def test_the_command_refuses_without_the_database_settings(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["sync", "--actor", "u_acme_hradmin", "--config", str(ROOT / "config")], env={})
    assert code == 1
    assert "MONGO_URI and MONGO_DATABASE are required" in capsys.readouterr().err


def test_the_command_refuses_an_invalid_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "agents").mkdir()
    code = main(["sync", "--actor", "u_acme_hradmin", "--config", str(tmp_path)], env={})
    assert code == 1
    assert "no agent manifests" in capsys.readouterr().err


def test_the_report_lists_each_change() -> None:
    report = SyncReport(
        changes=(SyncChange("update", "db_agent", 2, "ab" * 32),),
        unchanged=("run_assistant",),
        audited_orgs=("org_acme",),
    )
    assert render(report) == ("registry.agent_update agent:db_agent/v2\n1 changed, 1 unchanged; audited in: org_acme")
