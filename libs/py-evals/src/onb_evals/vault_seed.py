"""Seeds a throwaway database's PII vault through the real `Vault` (evals/README.md step 1).

This is the only module in the harness that holds a `Vault` (ADR 0003: PII-2's vault-import boundary
allows this one file). It runs only against a fresh eval database: each fixture load gets a new random
master key, creates org keys, and `put`s every `pii_vault_plain` record, so the stored records are
encrypted, signed and token-bound exactly as the API would store them. The plain values exist only in
the synthetic seed file.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from onb_crypto import (
    ORG_KEYS_COLLECTION,
    AadhaarConsent,
    CryptoError,
    Keyring,
    MasterKey,
    RepositoryOrgKeyStore,
    SecretKey,
    Vault,
    VaultField,
    ensure_org_keys_index,
    ensure_vault_indexes,
)
from onb_data import DataStore, tenant_scope

from onb_evals.tokens import TokenMap

__all__ = ["SeededVault", "seed_vault"]

FIXTURE_WRITER = "eval_fixture_loader"
_CONSENT = AadhaarConsent(purpose="synthetic eval fixture")


@dataclass(frozen=True, slots=True)
class SeedRecord:
    seed_token: str
    org_id: str
    subject_id: str
    field: str
    value: str


class SeededVault:
    """`VaultProbe` over the seeded vault: answers in seed names, verifies through `Vault.verify`."""

    def __init__(self, vault: Vault, tokens: TokenMap, owners: Mapping[str, tuple[str, str, str]]) -> None:
        self._vault = vault
        self._tokens = tokens
        self._owners = dict(owners)

    @property
    def tokens(self) -> TokenMap:
        return self._tokens

    def owner(self, token: str) -> tuple[str, str, str] | None:
        return self._owners.get(token)

    async def is_bound(self, token: str, *, org_id: str, subject_id: str, field: str) -> bool:
        real = self._tokens.real_for(token)
        if real is None or field not in {f.value for f in VaultField}:
            return False
        try:
            with tenant_scope(org_id):
                await self._vault.verify(real, org_id=org_id, subject_id=subject_id, field=VaultField(field))
        except (CryptoError, ValueError):
            return False
        return True


def _records(plain: Iterable[Mapping[str, Any]]) -> list[SeedRecord]:
    return [
        SeedRecord(str(r["token"]), str(r["org_id"]), str(r["subject_id"]), str(r["field"]), str(r["value"]))
        for r in plain
    ]


async def seed_vault(store: DataStore, org_ids: Iterable[str], plain: Iterable[Mapping[str, Any]]) -> SeededVault:
    """Create org keys under a fresh master key, then `put` every plain record; returns the probe."""
    keyring = Keyring(MasterKey(SecretKey.generate()), RepositoryOrgKeyStore(store.tenant(ORG_KEYS_COLLECTION)))
    vault = Vault(keyring, store)
    await ensure_org_keys_index(store)
    await ensure_vault_indexes(store)
    for org_id in org_ids:
        with tenant_scope(org_id):
            await keyring.create(org_id)
    seed_to_real: dict[str, str] = {}
    owners: dict[str, tuple[str, str, str]] = {}
    for record in _records(plain):
        field = VaultField(record.field)
        consent = _CONSENT if field is VaultField.AADHAAR else None
        with tenant_scope(record.org_id):
            token = await vault.put(
                record.org_id,
                subject_id=record.subject_id,
                field=field,
                value=record.value,
                created_by=FIXTURE_WRITER,
                consent=consent,
            )
        seed_to_real[record.seed_token] = token
        owners[record.seed_token] = (record.org_id, record.subject_id, record.field)
    return SeededVault(vault, TokenMap(seed_to_real), owners)
