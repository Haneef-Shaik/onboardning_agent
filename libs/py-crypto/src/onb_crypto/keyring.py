"""The per-org keyring (PRD §7.10 Encryption, §7.9 Encryption, §15 Key loss / leakage).

Each org has versioned key sets in `org_keys`, every key wrapped by the master key. Unwrapped keys
exist only in this process's memory. Encryption uses the org's newest version; decryption uses the
version the ciphertext names, so ciphertext from before a rotation still opens. The AEAD associated
data binds org, purpose and version on top of the caller's own binding, so ciphertext never opens
under another org, purpose or version even if key material were shared.

Every call must run inside `tenant_scope(org_id)` for the same org (PRD §7.9: the org comes from the
signed token), cached keys included. Cached keys expire after `DEFAULT_CACHE_SECONDS`, and a
ciphertext naming a version newer than the cache triggers one reload, so a rotation reaches running
processes without a restart.

Logs carry org ids, versions, purposes and master key ids. Never key material, never values.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import ClassVar, Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from onb_data import TenantContext, TenantViolationError, current_tenant

from onb_crypto.blind_index import BlindIndexField
from onb_crypto.blind_index import blind_index as _blind_index
from onb_crypto.envelope import NONCE_BYTES, unwrap_key, wrap_key
from onb_crypto.errors import (
    DecryptionError,
    KeyRewrapConflictError,
    KeyUnwrapError,
    NoOrgKeysError,
    UnknownKeyVersionError,
)
from onb_crypto.key_store import OrgKeyRecord, OrgKeyStore
from onb_crypto.keys import KeyPurpose, MasterKey, SecretKey

__all__ = ["Keyring", "OrgKeys", "Sealed", "associated_data"]

log = logging.getLogger(__name__)

_SEP: Final = b"\x1f"
_DATA_LABEL: Final = b"onb.pii.v1"


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def associated_data(*parts: str | bytes | int) -> bytes:
    """An unambiguous AEAD binding: each part length-prefixed, so ("ab", "c") never equals ("a", "bc").
    PII-2 binds `org_id | subject.id | field | token | version` with it (PRD §7.10)."""
    encoded = []
    for part in parts:
        if isinstance(part, bytes):
            raw = part
        elif isinstance(part, str):
            raw = part.encode("utf-8")
        elif _is_int(part):
            raw = str(part).encode("ascii")
        else:
            raise TypeError("associated data parts must be str, bytes or int")
        encoded.append(len(raw).to_bytes(4, "big") + raw)
    return b"".join(encoded)


@dataclass(frozen=True, slots=True)
class Sealed:
    """AES-256-GCM output. Store all three: `key_version` picks the key on decryption."""

    key_version: int
    nonce: bytes
    ciphertext: bytes

    def __post_init__(self) -> None:
        if not _is_int(self.key_version):
            raise TypeError("key_version must be an int")
        if self.key_version < 1:
            raise ValueError("key_version must be positive")
        if not isinstance(self.nonce, bytes) or not isinstance(self.ciphertext, bytes):
            raise TypeError("nonce and ciphertext must be bytes")
        if len(self.nonce) != NONCE_BYTES:
            raise ValueError(f"nonce must be {NONCE_BYTES} bytes")


@dataclass(frozen=True, slots=True)
class OrgKeys:
    """One unwrapped key version of one org. Held in memory only, read-only; its repr shows no material."""

    org_id: str
    version: int
    keys: Mapping[KeyPurpose, SecretKey]

    def __post_init__(self) -> None:
        object.__setattr__(self, "keys", MappingProxyType(dict(self.keys)))

    def key(self, purpose: KeyPurpose) -> SecretKey:
        return self.keys[purpose]


@dataclass(frozen=True, slots=True)
class _Cached:
    loaded_at: float
    versions: tuple[OrgKeys, ...]


def _check_org(org_id: object) -> str:
    """A valid org id that is the org of the current tenant context (signed token or job)."""
    try:
        valid = TenantContext(org_id).org_id  # type: ignore[arg-type]  # validated right here
    except ValueError:
        raise ValueError("invalid org id: 1-128 letters, digits, '_' or '-'") from None
    if current_tenant().org_id != valid:
        raise TenantViolationError("org keys requested for an org other than the tenant context")
    return valid


def _check_encrypts(purpose: KeyPurpose) -> None:
    if not purpose.encrypts:
        raise ValueError(f"{purpose.value} is not an encryption key")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Keyring:
    DEFAULT_CACHE_SECONDS: ClassVar[float] = 300.0

    def __init__(
        self,
        master: MasterKey,
        store: OrgKeyStore,
        *,
        clock: Callable[[], datetime] = _utcnow,
        monotonic: Callable[[], float] = time.monotonic,
        cache_seconds: float = DEFAULT_CACHE_SECONDS,
    ) -> None:
        self._master = master
        self._store = store
        self._clock = clock
        self._monotonic = monotonic
        self._cache_seconds = cache_seconds
        self._cache: Mapping[str, _Cached] = {}
        self._generation = 0

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
        many versions changed; versions already on `new_master` are skipped, so it is resumable. Each
        new wrapping is unwrapped with `new_master` before it is written, and afterwards no version may
        remain on another master (`KeyRewrapConflictError`)."""
        org_id = _check_org(org_id)
        changed = 0
        for record in await self._store.load(org_id):
            if record.master_key_id == new_master.key_id:
                continue
            await self._rewrap_record(org_id, record, new_master)
            changed += 1
        self.forget(org_id)
        left = [r.version for r in await self._store.load(org_id) if r.master_key_id != new_master.key_id]
        if left:
            raise KeyRewrapConflictError(f"org {org_id} key versions {left} are still wrapped by another master")
        log.info("org keys re-wrapped: org=%s versions=%d master_key_id=%s", org_id, changed, new_master.key_id)
        return changed

    def forget(self, org_id: str | None = None) -> None:
        """Drop cached keys (one org, or all) so the next call reloads from the store."""
        self._generation += 1
        self._cache = {} if org_id is None else {k: v for k, v in self._cache.items() if k != org_id}

    # --- lookup ------------------------------------------------------------------------------------

    async def active(self, org_id: str) -> OrgKeys:
        """The newest key version: what new writes use."""
        return (await self._load(_check_org(org_id)))[-1]

    async def key_version(self, org_id: str, version: int) -> OrgKeys:
        """That version's keys. A version the cache does not know triggers one reload (a rotation by
        another process) before `UnknownKeyVersionError`."""
        org_id = _check_org(org_id)
        for attempt in range(2):
            if attempt:
                self.forget(org_id)
            for keys in await self._load(org_id):
                if keys.version == version:
                    return keys
        raise UnknownKeyVersionError(f"org {org_id} has no key version {version}")

    async def verify(self, org_id: str) -> int:
        """Unwrap every version (fails closed on any bad record) and return the active version."""
        org_id = _check_org(org_id)
        self.forget(org_id)
        return (await self._load(org_id))[-1].version

    async def blind_index(self, org_id: str, field: BlindIndexField, value: str) -> str:
        """The org's blind index for `value`: Aadhaar uses the Aadhaar index key, every other field the
        index key (PRD §7.10 Lookup / dedupe, Aadhaar)."""
        purpose = KeyPurpose.AADHAAR_INDEX if field is BlindIndexField.AADHAAR else KeyPurpose.INDEX
        keys = await self.active(org_id)
        return _blind_index(keys.key(purpose).reveal(), field, value)

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
        keys = await self.key_version(org_id, sealed.key_version)
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

    async def _rewrap_record(self, org_id: str, record: OrgKeyRecord, new_master: MasterKey) -> None:
        keys = self._unwrap(org_id, record, self._master).keys
        rewrapped = self._wrap(org_id, record.version, keys, new_master)
        if self._unwrap(org_id, rewrapped, new_master).keys != keys:  # prove the new wrapping opens
            raise KeyRewrapConflictError(f"org {org_id} key version {record.version} did not re-wrap cleanly")
        if not await self._store.replace(rewrapped, expected_master_key_id=record.master_key_id):
            raise KeyRewrapConflictError(f"org {org_id} key version {record.version} changed while re-wrapping")

    def _unwrap(self, org_id: str, record: OrgKeyRecord, master: MasterKey | None = None) -> OrgKeys:
        master = master or self._master
        if record.org_id != org_id:
            raise KeyUnwrapError(f"org key store returned a record of another org for {org_id}")
        try:
            if record.master_key_id != master.key_id:
                raise KeyUnwrapError(
                    f"org key {org_id} v{record.version} was wrapped by a different master key "
                    f"(record {record.master_key_id}, loaded {master.key_id})"
                )
            keys = {
                p: unwrap_key(master, record.wrapped[p], org_id=org_id, version=record.version, purpose=p)
                for p in KeyPurpose
            }
        except KeyUnwrapError:
            log.warning(
                "org key unwrap failed: org=%s version=%d master_key_id=%s", org_id, record.version, master.key_id
            )
            raise
        return OrgKeys(org_id, record.version, keys)

    async def _load(self, org_id: str) -> tuple[OrgKeys, ...]:
        cached = self._cache.get(org_id)
        if cached is not None and self._monotonic() - cached.loaded_at < self._cache_seconds:
            return cached.versions
        generation, loaded_at = self._generation, self._monotonic()
        records = sorted(await self._store.load(org_id), key=lambda r: r.version)
        if not records:
            raise NoOrgKeysError(f"org {org_id} has no keys")
        versions = [r.version for r in records]
        if len(set(versions)) != len(versions):
            raise KeyUnwrapError(f"org {org_id} has a duplicate key version in the store")
        unwrapped = tuple(self._unwrap(org_id, r) for r in records)
        if generation == self._generation:  # nothing was forgotten (e.g. a rotation) while loading
            self._cache = {**self._cache, org_id: _Cached(loaded_at, unwrapped)}
        return unwrapped
