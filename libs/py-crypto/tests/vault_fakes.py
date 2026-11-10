"""An in-memory stand-in for `DataStore.tenant(...)` that keeps raw documents, so tests can tamper with
them the way someone with database access would. It scopes by the current tenant like T-1's
`TenantRepository` and enforces the vault's unique indexes."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from onb_crypto import Keyring
from onb_data import TenantViolationError, UpdateOutcome, current_tenant
from pymongo.errors import DuplicateKeyError

from .fakes import ORG_A, ORG_B, InMemoryOrgKeyStore, in_org, new_master_key

_UNIQUE: dict[str, tuple[tuple[str, ...], ...]] = {
    "pii_vault": (("token",), ("subject.id", "field", "version")),
    "pii_vault_aadhaar": (("token",), ("subject.id", "field", "version")),
    "pii_grant_jti": (("jti",),),
}


def _get(document: Mapping[str, Any], path: str) -> Any:
    value: Any = document
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def _sort_key(path: str) -> Callable[[Mapping[str, Any]], Any]:
    return lambda document: _get(document, path)


def _matches(document: Mapping[str, Any], query: Mapping[str, Any]) -> bool:
    return all(_get(document, k) == v for k, v in query.items())


class FakeVaultRepository:
    def __init__(self, name: str) -> None:
        self.name = name
        self.documents: list[dict[str, Any]] = []
        self.fail_next_update = False
        self.fail_next_insert = False

    def _scoped(self, query: Mapping[str, Any] | None) -> dict[str, Any]:
        org = current_tenant().org_id
        query = dict(query or {})
        if query.get("org_id", org) != org:
            raise TenantViolationError("filter names another org")
        return {**query, "org_id": org}

    async def find(
        self,
        query: Mapping[str, Any] | None = None,
        *,
        sort: Sequence[tuple[str, int]] | None = None,
        limit: int = 0,
        **_: Any,
    ) -> list[dict[str, Any]]:
        found = [copy.deepcopy(d) for d in self.documents if _matches(d, self._scoped(query))]
        for key, direction in reversed(list(sort or [])):
            found.sort(key=_sort_key(key), reverse=direction < 0)
        return found[:limit] if limit else found

    async def find_one(self, query: Mapping[str, Any] | None = None, **_: Any) -> dict[str, Any] | None:
        found = await self.find(query)
        return found[0] if found else None

    async def insert_one(self, document: Mapping[str, Any]) -> Any:
        org = current_tenant().org_id
        if document.get("org_id", org) != org:
            raise TenantViolationError("document names another org")
        stored = {**copy.deepcopy(dict(document)), "org_id": org, "_id": f"{self.name}-{len(self.documents) + 1}"}
        for keys in _UNIQUE.get(self.name, ()):
            for existing in self.documents:
                if existing["org_id"] == org and all(_get(existing, k) == _get(stored, k) for k in keys):
                    raise DuplicateKeyError(f"E11000 duplicate key {keys}")
        self.documents = [*self.documents, stored]
        return stored["_id"]

    async def insert_many(self, documents: Sequence[Mapping[str, Any]]) -> list[Any]:
        if self.fail_next_insert:
            self.fail_next_insert = False
            raise RuntimeError("the database refused the write")
        return [await self.insert_one(document) for document in documents]

    async def update_one(self, query: Mapping[str, Any] | None, update: Mapping[str, Any], **_: Any) -> UpdateOutcome:
        if self.fail_next_update:
            self.fail_next_update = False
            return UpdateOutcome(0, 0)
        if set(update) != {"$set"} or any("." in k or k == "org_id" for k in update["$set"]):
            raise ValueError("the fake supports top-level $set only (not org_id)")
        scoped = self._scoped(query)
        for index, existing in enumerate(self.documents):
            if _matches(existing, scoped):
                changed = {**existing, **copy.deepcopy(dict(update["$set"]))}
                self.documents = [*self.documents[:index], changed, *self.documents[index + 1 :]]
                return UpdateOutcome(1, 1)
        return UpdateOutcome(0, 0)

    async def delete_one(self, query: Mapping[str, Any] | None) -> int:
        scoped = self._scoped(query)
        kept = [d for d in self.documents if not _matches(d, scoped)]
        deleted = len(self.documents) - len(kept)
        self.documents = kept
        return deleted

    # --- tampering, as someone with direct database access -------------------------------------------

    def raw(self, token: str) -> dict[str, Any]:
        """The stored document itself (not a copy): edit it to tamper."""
        return next(d for d in self.documents if d.get("token") == token)


class FakeVaultStore:
    def __init__(self) -> None:
        self.repos: dict[str, FakeVaultRepository] = {}

    def tenant(self, collection: str) -> FakeVaultRepository:
        if collection not in self.repos:
            self.repos = {**self.repos, collection: FakeVaultRepository(collection)}
        return self.repos[collection]

    def written(self) -> dict[str, int]:
        return {name: len(repo.documents) for name, repo in self.repos.items() if repo.documents}


def keyring_for_two_orgs() -> Keyring:
    keyring = Keyring(new_master_key(), InMemoryOrgKeyStore())
    in_org(ORG_A, keyring.create(ORG_A))
    in_org(ORG_B, keyring.create(ORG_B))
    return keyring
