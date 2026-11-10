"""Loads the seed into a fresh database and applies a case's setup (evals/README.md steps 1-2).

Every write goes through T-1's repositories: `orgs` through `shared()`, everything else through
`tenant()` inside `tenant_scope(<the document's org>)`. `pii_vault_plain` goes through the real vault
(`onb_evals.vault_seed`); every seed token name in the other documents is replaced by the issued token.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from onb_data import DataStore, tenant_scope

from onb_evals.cases import EvalCase
from onb_evals.context import FixtureFacts, StateReader
from onb_evals.facts import facts_from_seed
from onb_evals.tokens import TokenMap
from onb_evals.vault_seed import SeededVault, seed_vault

__all__ = ["FixtureError", "LoadedFixture", "StoreState", "apply_setup", "load_fixture", "load_seed"]

TENANT_COLLECTIONS: Final = (
    "users",
    "new_hires",
    "templates",
    "onboarding_runs",
    "run_tasks",
    "approvals",
    "documents_meta",
    "knowledge_docs",
)
_REQUIRED: Final = ("fixture_version", "orgs", "pii_vault_plain", *TENANT_COLLECTIONS)


class FixtureError(ValueError):
    """The seed is malformed or inconsistent."""


@dataclass(frozen=True, slots=True)
class LoadedFixture:
    version: str
    tokens: TokenMap
    vault: SeededVault
    facts: FixtureFacts


def load_seed(path: Path) -> dict[str, Any]:
    seed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(seed, dict):
        raise FixtureError(f"{path}: the seed must be a JSON object")
    missing = [key for key in _REQUIRED if key not in seed]
    if missing:
        raise FixtureError(f"{path}: the seed lacks {missing}")
    return seed


def _org_ids(seed: Mapping[str, Any]) -> list[str]:
    return [str(org["_id"]) for org in seed["orgs"]]


def _check_orgs(seed: Mapping[str, Any], orgs: list[str]) -> None:
    for key in ("pii_vault_plain", *TENANT_COLLECTIONS):
        unknown = {str(doc.get("org_id")) for doc in seed.get(key, [])} - set(orgs)
        if unknown:
            raise FixtureError(f"{key} names orgs the seed does not list: {sorted(unknown)}")


async def load_fixture(store: DataStore, seed: Mapping[str, Any]) -> LoadedFixture:
    """Seed a fresh database. The store must be empty; the caller owns its lifecycle."""
    orgs = _org_ids(seed)
    _check_orgs(seed, orgs)
    await store.shared("orgs").insert_many([dict(org) for org in seed["orgs"]])
    vault = await seed_vault(store, orgs, seed["pii_vault_plain"])
    for collection in TENANT_COLLECTIONS:
        for org_id in orgs:
            documents = [vault.tokens.to_real(d) for d in seed.get(collection, []) if d.get("org_id") == org_id]
            if documents:
                with tenant_scope(org_id):
                    await store.tenant(collection).insert_many(documents)
    return LoadedFixture(str(seed["fixture_version"]), vault.tokens, vault, facts_from_seed(seed))


async def apply_setup(store: DataStore, case: EvalCase, tokens: TokenMap) -> None:
    """The case's `setup` operations, in the case's org."""
    with tenant_scope(case.actor.org_id):
        for op in case.setup:
            await store.tenant(op.collection).update_many(tokens.to_real(op.filter), {"$set": tokens.to_real(op.set)})


class StoreState(StateReader):
    """`StateReader` over the case's database; documents come back with seed token names."""

    def __init__(self, store: DataStore, tokens: TokenMap) -> None:
        self._store = store
        self._tokens = tokens

    async def find(self, collection: str, query: Mapping[str, Any], *, org_id: str) -> list[dict[str, Any]]:
        with tenant_scope(org_id):
            documents = await self._store.tenant(collection).find(self._tokens.to_real(query))
        rewritten: list[dict[str, Any]] = self._tokens.to_seed(documents)
        return rewritten
