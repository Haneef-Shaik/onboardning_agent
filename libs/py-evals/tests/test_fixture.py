"""The fixture loader (evals/README.md "How a case runs", steps 1-2): seed data goes into a fresh store
through tenant repositories, and `pii_vault_plain` goes through the real vault, so the store holds
ciphertext and issued `ptk_` tokens, never the plain values or the seed's readable token names."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from onb_crypto import is_token
from onb_evals.cases import parse_case
from onb_evals.fixture import FixtureError, LoadedFixture, apply_setup, load_fixture, load_seed
from onb_evals.tokens import TokenMap

from .memory_store import MemoryStore, as_data_store
from .support import SEED, SEED_PATH


def _loaded() -> tuple[MemoryStore, LoadedFixture]:
    store = MemoryStore()
    fixture = asyncio.run(load_fixture(as_data_store(store), SEED))
    return store, fixture


def test_every_plain_value_is_encrypted_and_no_plain_value_or_seed_token_is_stored() -> None:
    store, _ = _loaded()
    stored = store.dump()
    for record in SEED["pii_vault_plain"]:
        assert record["value"] not in stored
        assert record["token"] not in stored
    vault_docs = store.data["pii_vault"] + store.data["pii_vault_aadhaar"]
    assert len(vault_docs) == len(SEED["pii_vault_plain"])
    assert all(is_token(doc["token"]) and doc["ciphertext"] for doc in vault_docs)
    assert len(store.data["pii_vault_aadhaar"]) == 1
    assert {doc["org_id"] for doc in store.data["org_keys"]} == {"org_acme", "org_globex"}


def test_hire_documents_carry_the_issued_tokens() -> None:
    store, fixture = _loaded()
    priya = next(d for d in store.data["new_hires"] if d["_id"] == "h_acme_001")
    assert priya["pii"]["mobile"] == fixture.tokens.to_real("ptk_seed_acme001_mobile")
    assert is_token(priya["pii"]["mobile"])


def test_tenant_documents_are_stored_under_their_own_org() -> None:
    store, _ = _loaded()
    assert len(store.data["new_hires"]) == 5
    assert {d["org_id"] for d in store.data["run_tasks"]} == {"org_acme", "org_globex"}
    assert [d["_id"] for d in store.data["orgs"]] == ["org_acme", "org_globex"]
    assert len(store.data["knowledge_docs"]) == 4


def test_the_vault_probe_binds_seed_names_to_the_right_subject_and_field() -> None:
    _, fixture = _loaded()
    probe = fixture.vault

    async def bound(token: str, org: str, subject: str, field: str) -> bool:
        return await probe.is_bound(token, org_id=org, subject_id=subject, field=field)

    assert asyncio.run(bound("ptk_seed_acme001_mobile", "org_acme", "h_acme_001", "mobile"))
    assert not asyncio.run(bound("ptk_seed_acme002_mobile", "org_acme", "h_acme_001", "mobile"))
    assert not asyncio.run(bound("ptk_seed_acme001_mobile", "org_acme", "h_acme_001", "email"))
    assert not asyncio.run(bound("ptk_seed_globex001_mobile", "org_acme", "h_globex_001", "mobile"))
    assert not asyncio.run(bound("ptk_unknown", "org_acme", "h_acme_001", "mobile"))
    assert not asyncio.run(bound("ptk_seed_acme001_mobile", "org_acme", "h_acme_001", "not_a_field"))
    assert probe.owner("ptk_seed_acme001_pan") == ("org_acme", "h_acme_001", "pan")
    assert probe.owner("ptk_nope") is None


def test_the_facts_come_from_the_seed() -> None:
    _, fixture = _loaded()
    assert fixture.facts.action_agents["approval"] == "approvals_agent"
    assert ("org_acme", "engineer-india", 3) in fixture.facts.templates


def test_setup_updates_run_in_the_cases_org_only() -> None:
    store, fixture = _loaded()
    case = parse_case(
        {
            "id": "s-1",
            "agent": "templates_agent",
            "category": "capability",
            "actor": {"org_id": "org_acme", "role": "SYSTEM"},
            "input": {},
            "setup": [
                {
                    "op": "update",
                    "collection": "run_tasks",
                    "filter": {"task_id": "manager_approval"},
                    "set": {"status": "done"},
                }
            ],
            "checks": [{"type": "status_in", "values": ["ok"]}],
        }
    )
    asyncio.run(apply_setup(as_data_store(store), case, fixture.tokens))
    approvals = [d for d in store.data["run_tasks"] if d["task_id"] == "manager_approval"]
    assert {d["status"] for d in approvals if d["org_id"] == "org_acme"} == {"done"}
    assert {d["status"] for d in approvals if d["org_id"] == "org_globex"} == {"running"}


def test_the_seed_file_loads_and_a_bad_seed_is_refused(tmp_path: Any) -> None:
    assert load_seed(SEED_PATH)["fixture_version"] == "seed_v1"
    bad = tmp_path / "seed.json"
    bad.write_text(json.dumps({"fixture_version": "x"}))
    with pytest.raises(FixtureError, match="orgs"):
        load_seed(bad)
    bad.write_text("[]")
    with pytest.raises(FixtureError, match="object"):
        load_seed(bad)


def test_a_record_for_an_org_the_seed_does_not_list_is_refused() -> None:
    seed = {**SEED, "pii_vault_plain": [{**SEED["pii_vault_plain"][0], "org_id": "org_ghost"}]}
    with pytest.raises(FixtureError, match="org_ghost"):
        asyncio.run(load_fixture(as_data_store(MemoryStore()), seed))


# --- token map -----------------------------------------------------------------------------------


def test_the_token_map_rewrites_strings_deeply_in_both_directions() -> None:
    tokens = TokenMap({"ptk_seed_a_mobile": "ptk_REAL1", "ptk_seed_a_mobile_2": "ptk_REAL2"})
    value = {"to": "ptk_seed_a_mobile", "body": ["x {{pii:ptk_seed_a_mobile_2}}"], "n": 3, "ptk_seed_a_mobile": 1}
    real = tokens.to_real(value)
    assert real == {"to": "ptk_REAL1", "body": ["x {{pii:ptk_REAL2}}"], "n": 3, "ptk_seed_a_mobile": 1}
    assert tokens.to_seed(real) == value
    assert value["to"] == "ptk_seed_a_mobile"


def test_an_empty_token_map_returns_values_unchanged() -> None:
    assert TokenMap({}).to_real({"a": ("x",)}) == {"a": ["x"]}
