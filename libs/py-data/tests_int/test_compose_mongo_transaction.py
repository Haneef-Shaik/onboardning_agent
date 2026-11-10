"""Multi-document transactions against the compose Mongo replica set (F-2 box 2; PRD §7.2 outbox)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import OperationFailure

Client = MongoClient[dict[str, object]]
# Root reads are org-scoped too: the T-2 safety net fails any unscoped command on a tenant collection.
ORG = "org_test"


@pytest.fixture
def scratch_db(root_client: Client) -> Iterator[Database[dict[str, object]]]:
    name = f"it_f2_{uuid.uuid4().hex[:12]}"
    db = root_client[name]
    db.create_collection("onboarding_runs")
    db.create_collection("events")
    try:
        yield db
    finally:
        root_client.drop_database(name)


def test_server_is_a_replica_set_primary(root_client: Client) -> None:
    hello = root_client.admin.command("hello")
    assert hello["setName"] == "rs0"
    assert hello["isWritablePrimary"] is True


def test_multi_document_transaction_commits(root_client: Client, scratch_db: Database[dict[str, object]]) -> None:
    run_id = uuid.uuid4().hex
    with root_client.start_session() as session, session.start_transaction():
        scratch_db.onboarding_runs.insert_one({"_id": run_id, "org_id": ORG, "state": "started"}, session=session)
        scratch_db.events.insert_one({"org_id": ORG, "type": "run.started", "run_id": run_id}, session=session)
    assert scratch_db.onboarding_runs.count_documents({"org_id": ORG, "_id": run_id}) == 1
    assert scratch_db.events.count_documents({"org_id": ORG, "run_id": run_id}) == 1


def test_aborted_transaction_writes_nothing(root_client: Client, scratch_db: Database[dict[str, object]]) -> None:
    run_id = uuid.uuid4().hex
    with root_client.start_session() as session:
        session.start_transaction()
        scratch_db.onboarding_runs.insert_one({"_id": run_id, "org_id": ORG}, session=session)
        scratch_db.events.insert_one({"org_id": ORG, "run_id": run_id}, session=session)
        session.abort_transaction()
    assert scratch_db.onboarding_runs.count_documents({"org_id": ORG, "_id": run_id}) == 0
    assert scratch_db.events.count_documents({"org_id": ORG, "run_id": run_id}) == 0


def test_unauthenticated_client_is_refused(anonymous_client: Client) -> None:
    with pytest.raises(OperationFailure) as err:
        anonymous_client.list_database_names()
    assert err.value.code == 13  # Unauthorized
