"""The per-org keyring (PRD §7.10 Encryption, §7.9 Encryption, §15 Key loss / leakage).

Each org has versioned key sets in `org_keys`, every key wrapped by the master key. Unwrapped keys
exist only in this process's memory. Encryption always uses the org's newest version; decryption uses
the version the ciphertext names, so ciphertext from before a rotation still opens. The AEAD
associated data binds org, purpose and version on top of the caller's own binding, so ciphertext
never opens under another org, purpose or version even if key material were shared.

Logs carry org ids, versions, purposes and master key ids. Never key material, never values.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from onb_data import TenantContext

from onb_crypto.envelope import NONCE_BYTES, unwrap_key, wrap_key
from onb_crypto.errors import (
    DecryptionError,
    KeyUnwrapError,
    NoOrgKeysError,
    UnknownKeyVersionError,
)
from onb_crypto.key_store import OrgKeyRecord, OrgKeyStore
from onb_crypto.keys import KeyPurpose, MasterKey, SecretKey

__all__ = ["Keyring", "OrgKeys", "Sealed"]

log = logging.getLogger(__name__)

_SEP: Final = b"\x1f"
_DATA_LABEL: Final = b"onb.pii.v1"


@dataclass(frozen=True, slots=True)
class Sealed:
    """AES-256-GCM output. Store all three: `key_version` picks the key on decryption."""

    key_version: int
    nonce: bytes
    ciphertext: bytes


@dataclass(frozen=True, slots=True)
class OrgKeys:
    """One unwrapped key version of one org. Held in memory only; its repr shows no material."""

    org_id: str
    version: int
    keys: Mapping[KeyPurpose, SecretKey]

    def key(self, purpose: KeyPurpose) -> SecretKey:
        return self.keys[purpose]


def _check_org(org_id: object) -> str:
    try:
        return TenantContext(org_id).org_id  # type: ignore[arg-type]  # validated right here
    except ValueError:
        raise ValueError("invalid org id: 1-128 letters, digits, '_' or '-'") from None


def _check_encrypts(purpose: KeyPurpose) -> None:
    if not purpose.encrypts:
        raise ValueError(f"{purpose.value} is not an encryption key")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Keyring:
    def __init__(self, master: MasterKey, store: OrgKeyStore, *, clock: Callable[[], datetime] = _utcnow) -> None:
        self._master = master
        self._store = store
        self._clock = clock
        self._cache: Mapping[str, tuple[OrgKeys, ...]] = {}

    @property
    def master(self) -> MasterKey:
        return self._master

    # --- lifecycle ---------------------------------------------------------------------------------

    async def create(self, org_id: str) -> int:
        """Version 1 of a new org's keys. `KeyVersionConflictError` if the org already has keys."""
        org_id = _check_org(org_id)
        fresh = {p: SecretKey.generate() for p in KeyPurpose}
        await self._store.add(self._wrap(org_id, 1, fresh, self._master))
        self.forget(org_id)
        log.info("org keys created: org=%s version=1 master_key_id=%s", org_id, self._master.key_id)
        return 1

    async def rotate(self, org_id: str) -> int:
        """A new version: fresh data, signing and Aadhaar keys; index keys carried forward. Old versions
        stay for decryption. A concurrent rotation of the same org raises `KeyVersionConflictError`."""
        org_id = _check_org(org_id)
        self.forget(org_id)
        current = (await self._load(org_id))[-1]
        version = current.version + 1
        keys = {p: SecretKey.generate() if p.rotates else current.key(p) for p in KeyPurpose}
        await self._store.add(self._wrap(org_id, version, keys, self._master))
        self.forget(org_id)
        log.info("org keys rotated: org=%s version=%d master_key_id=%s", org_id, version, self._master.key_id)
        return version

    async def rewrap(self, org_id: str, new_master: MasterKey) -> int:
        """Re-wrap every version still wrapped by this keyring's master under `new_master`. Returns how
        many versions changed; versions already on `new_master` are skipped, so it is resumable."""
        org_id = _check_org(org_id)
        records = await self._store.load(org_id)
        changed = 0
        for record in records:
            if record.master_key_id == new_master.key_id:
                continue
            keys = self._unwrap(org_id, record).keys
            replaced = await self._store.replace(
                self._wrap(org_id, record.version, keys, new_master), expected_master_key_id=record.master_key_id
            )
            if not replaced:
                raise RuntimeError(f"org {org_id} key version {record.version} changed while re-wrapping; run again")
            changed += 1
        self.forget(org_id)
        log.info("org keys re-wrapped: org=%s versions=%d master_key_id=%s", org_id, changed, new_master.key_id)
        return changed

    def forget(self, org_id: str | None = None) -> None:
        """Drop cached keys (one org, or all) so the next call reloads from the store."""
        self._cache = {} if org_id is None else {k: v for k, v in self._cache.items() if k != org_id}

    # --- lookup ------------------------------------------------------------------------------------

    async def active(self, org_id: str) -> OrgKeys:
        """The newest key version: what new writes use."""
        return (await self._load(_check_org(org_id)))[-1]

    async def version(self, org_id: str, version: int) -> OrgKeys:
        org_id = _check_org(org_id)
        for keys in await self._load(org_id):
            if keys.version == version:
                return keys
        raise UnknownKeyVersionError(f"org {org_id} has no key version {version}")

    async def verify(self, org_id: str) -> int:
        """Unwrap every version (fails closed on any bad record) and return the active version."""
        org_id = _check_org(org_id)
        self.forget(org_id)
        return (await self._load(org_id))[-1].version

    # --- data encryption ---------------------------------------------------------------------------

    @staticmethod
    def bound_data(org_id: str, purpose: KeyPurpose, version: int, associated_data: bytes) -> bytes:
        """The AEAD associated data actually used: the caller's binding prefixed with org, purpose, version."""
        return _SEP.join((_DATA_LABEL, org_id.encode(), purpose.value.encode(), str(version).encode(), associated_data))

    async def encrypt(
        self, org_id: str, plaintext: bytes, *, associated_data: bytes, purpose: KeyPurpose = KeyPurpose.DATA
    ) -> Sealed:
        _check_encrypts(purpose)
        keys = await self.active(org_id)
        nonce = secrets.token_bytes(NONCE_BYTES)
        bound = self.bound_data(keys.org_id, purpose, keys.version, associated_data)
        return Sealed(keys.version, nonce, AESGCM(keys.key(purpose).reveal()).encrypt(nonce, plaintext, bound))

    async def decrypt(
        self, org_id: str, sealed: Sealed, *, associated_data: bytes, purpose: KeyPurpose = KeyPurpose.DATA
    ) -> bytes:
        _check_encrypts(purpose)
        keys = await self.version(org_id, sealed.key_version)
        bound = self.bound_data(keys.org_id, purpose, keys.version, associated_data)
        try:
            return AESGCM(keys.key(purpose).reveal()).decrypt(sealed.nonce, sealed.ciphertext, bound)
        except (InvalidTag, ValueError):
            log.warning("decrypt failed: org=%s version=%d purpose=%s", keys.org_id, sealed.key_version, purpose.value)
            raise DecryptionError(
                f"ciphertext does not open for org {keys.org_id} v{sealed.key_version} {purpose.value}"
            ) from None

    # --- internals ---------------------------------------------------------------------------------

    def _wrap(self, org_id: str, version: int, keys: Mapping[KeyPurpose, SecretKey], master: MasterKey) -> OrgKeyRecord:
        wrapped = {p: wrap_key(master, keys[p], org_id=org_id, version=version, purpose=p) for p in KeyPurpose}
        return OrgKeyRecord(org_id, version, master.key_id, wrapped, self._clock())

    def _unwrap(self, org_id: str, record: OrgKeyRecord) -> OrgKeys:
        if record.org_id != org_id:
            raise KeyUnwrapError(f"org key store returned a record of another org for {org_id}")
        try:
            if record.master_key_id != self._master.key_id:
                raise KeyUnwrapError(
                    f"org key {org_id} v{record.version} was wrapped by a different master key "
                    f"(record {record.master_key_id}, loaded {self._master.key_id})"
                )
            keys = {
                p: unwrap_key(self._master, record.wrapped[p], org_id=org_id, version=record.version, purpose=p)
                for p in KeyPurpose
            }
        except KeyUnwrapError:
            log.warning(
                "org key unwrap failed: org=%s version=%d master_key_id=%s", org_id, record.version, self._master.key_id
            )
            raise
        return OrgKeys(org_id, record.version, keys)

    async def _load(self, org_id: str) -> tuple[OrgKeys, ...]:
        cached = self._cache.get(org_id)
        if cached is not None:
            return cached
        records = await self._store.load(org_id)
        if not records:
            raise NoOrgKeysError(f"org {org_id} has no keys")
        unwrapped = tuple(self._unwrap(org_id, r) for r in sorted(records, key=lambda r: r.version))
        self._cache = {**self._cache, org_id: unwrapped}
        return unwrapped
