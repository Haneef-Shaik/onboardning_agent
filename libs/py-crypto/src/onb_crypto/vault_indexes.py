"""Indexes the vault relies on. Run `await ensure_vault_indexes(store)` once at startup.

In each partition, a token is unique per org (lookups find at most one record) and so is
`{subject.id, field, version}`, which turns two concurrent updates of one field into one winner and
one `VaultConflictError`.
"""

from __future__ import annotations

from onb_data import DataStore

from onb_crypto.vault import CONSENT_COLLECTION
from onb_crypto.vault_record import AADHAAR_VAULT_COLLECTION, VAULT_COLLECTION

__all__ = ["ensure_vault_indexes"]


async def ensure_vault_indexes(store: DataStore) -> None:
    for collection in (VAULT_COLLECTION, AADHAAR_VAULT_COLLECTION):
        await store.ensure_index(collection, [("org_id", 1), ("token", 1)], unique=True)
        await store.ensure_index(
            collection, [("org_id", 1), ("subject.id", 1), ("field", 1), ("version", 1)], unique=True
        )
    await store.ensure_index(CONSENT_COLLECTION, [("org_id", 1), ("subject.id", 1)])
