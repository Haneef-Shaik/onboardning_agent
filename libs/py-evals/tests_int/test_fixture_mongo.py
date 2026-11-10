"""The fixture loader against the compose Mongo: a fresh `onb_eval_*` database per use, the seed stored
through tenant repositories, `pii_vault_plain` encrypted by the real vault, and the database dropped after."""

from __future__ import annotations

import asyncio
from typing import Any

from onb_crypto import is_token
from onb_evals.databases import DATABASE_PREFIX, ThrowawayDatabases
from onb_evals.fixture import load_fixture, load_seed
from onb_evals.scripted import find_repo_file
from pymongo import MongoClient

SEED = load_seed(find_repo_file("evals/fixtures/seed_v1.json"))


def _eval_databases(client: MongoClient[dict[str, Any]]) -> set[str]:
    return {name for name in client.list_database_names() if name.startswith(DATABASE_PREFIX)}


def test_the_seed_loads_encrypted_into_a_fresh_database_that_is_dropped_after(
    mongo_uri: str, root_client: MongoClient[dict[str, Any]]
) -> None:
    before = _eval_databases(root_client)
    seen: dict[str, Any] = {}

    async def scenario() -> None:
        async with ThrowawayDatabases(mongo_uri).fresh() as store:
            fixture = await load_fixture(store, SEED)
            [name] = _eval_databases(root_client) - before
            database = root_client[name]
            seen["name"] = name
            seen["vault"] = list(database["pii_vault"].find()) + list(database["pii_vault_aadhaar"].find())
            seen["hire"] = database["new_hires"].find_one({"_id": "h_acme_001"})
            seen["dump"] = repr([list(database[c].find()) for c in database.list_collection_names()])
            seen["indexes"] = database["pii_vault"].index_information()
            seen["bound"] = await fixture.vault.is_bound(
                "ptk_seed_acme001_mobile", org_id="org_acme", subject_id="h_acme_001", field="mobile"
            )

    asyncio.run(scenario())
    assert len(seen["vault"]) == len(SEED["pii_vault_plain"])
    assert all(is_token(doc["token"]) and doc["ciphertext"] and doc["sig"] for doc in seen["vault"])
    for record in SEED["pii_vault_plain"]:
        assert record["value"] not in seen["dump"]
        assert record["token"] not in seen["dump"]
    assert is_token(seen["hire"]["pii"]["mobile"])
    assert any(spec["key"] == [("org_id", 1), ("token", 1)] for spec in seen["indexes"].values())
    assert seen["bound"] is True
    assert seen["name"] not in root_client.list_database_names()
