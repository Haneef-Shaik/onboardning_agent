"""Box 4: every step of docs/runbooks/key-rotation.md, run in order (PRD §15 Key loss / leakage).

The same steps run against the compose MongoDB in `tests_int/test_org_keys_mongo.py`. Each step is
marked with a `# <step>.` comment; `test_the_runbook_documents_exactly_the_exercised_steps` checks
that every step heading in the runbook has exactly that marker here.
"""

import asyncio
import re
from pathlib import Path

import pytest
from onb_crypto import (
    KeyPurpose,
    KeyRewrapConflictError,
    Keyring,
    KeyUnwrapError,
    MasterKey,
    OrgKeyRecord,
    Sealed,
    rewrap_orgs,
    rotate_orgs,
    verify_orgs,
)
from onb_data import tenant_scope

from .fakes import ORG_A, ORG_B, InMemoryOrgKeyStore, encoded_master_key

RUNBOOK = Path(__file__).resolve().parents[3] / "docs" / "runbooks" / "key-rotation.md"
AD = b"hire_1|mobile|ptk_1|1"
ORGS = [ORG_A, ORG_B]


class Clock:
    """A monotonic clock the test moves by hand, for the keyring's cache age."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def provision(keyring: Keyring) -> dict[str, Sealed]:
    sealed: dict[str, Sealed] = {}
    for org in ORGS:
        with tenant_scope(org):
            await keyring.create(org)
            sealed[org] = await keyring.encrypt(org, b"value-" + org.encode(), associated_data=AD)
    return sealed


def test_org_key_rotation_runbook_procedure_a() -> None:
    async def procedure() -> None:
        store = InMemoryOrgKeyStore()
        clock = Clock()
        keyring = Keyring(MasterKey.from_encoded(encoded_master_key()), store)
        running = Keyring(keyring.master, store, monotonic=clock)  # services that keep running
        idle = Keyring(keyring.master, store, monotonic=clock)
        before = await provision(keyring)
        for org in ORGS:
            with tenant_scope(org):
                await running.active(org)  # their caches now hold v1
                await idle.active(org)

        # A1.
        assert await verify_orgs(keyring, ORGS) == {ORG_A: 1, ORG_B: 1}
        # A2.
        assert await rotate_orgs(keyring, ORGS) == {ORG_A: 2, ORG_B: 2}
        # A3. Within the cache age a running service still writes v1 but already reads v2.
        for org in ORGS:
            with tenant_scope(org):
                fresh = await keyring.encrypt(org, b"after", associated_data=AD)
                assert await running.decrypt(org, fresh, associated_data=AD) == b"after"
        with tenant_scope(ORG_A):
            assert (await idle.active(ORG_A)).version == 1, "within the cache age it still writes v1"
        clock.now += Keyring.DEFAULT_CACHE_SECONDS + 1
        with tenant_scope(ORG_A):
            assert (await idle.active(ORG_A)).version == 2, "after the cache age it writes v2"
        # A4.
        restarted = Keyring(keyring.master, store)
        assert await verify_orgs(restarted, ORGS) == {ORG_A: 2, ORG_B: 2}
        for org in ORGS:
            with tenant_scope(org):
                assert (await restarted.encrypt(org, b"x", associated_data=AD)).key_version == 2
                assert await restarted.decrypt(org, before[org], associated_data=AD) == b"value-" + org.encode()
        # A5.
        assert sorted(store.records) == [(ORG_A, 1), (ORG_A, 2), (ORG_B, 1), (ORG_B, 2)]

    asyncio.run(procedure())


def test_master_key_rotation_runbook_procedure_b() -> None:
    async def procedure() -> None:
        store = InMemoryOrgKeyStore()
        old_master = MasterKey.from_encoded(encoded_master_key())
        keyring = Keyring(old_master, store)
        sealed = await provision(keyring)
        await rotate_orgs(keyring, [ORG_A])
        with tenant_scope(ORG_A):
            index_before = (await keyring.active(ORG_A)).key(KeyPurpose.INDEX)

        # B1.
        new_master = MasterKey.from_encoded(encoded_master_key())
        # B2. (the writers are stopped: nothing else touches the store below)
        # B3.
        assert await rewrap_orgs(keyring, new_master, ORGS) == {ORG_A: 2, ORG_B: 1}
        assert await rewrap_orgs(keyring, new_master, ORGS) == {ORG_A: 0, ORG_B: 0}, "resumable"
        # B4.
        new_keyring = Keyring(new_master, store)
        assert await verify_orgs(new_keyring, ORGS) == {ORG_A: 2, ORG_B: 1}
        for org in ORGS:
            with tenant_scope(org):
                assert await new_keyring.decrypt(org, sealed[org], associated_data=AD) == b"value-" + org.encode()
        with tenant_scope(ORG_A):
            assert (await new_keyring.active(ORG_A)).key(KeyPurpose.INDEX) == index_before
        # B5.
        with pytest.raises(KeyUnwrapError):
            await verify_orgs(Keyring(old_master, store), ORGS)
        # B6. After an exposure, Procedure A under the new master: new writes stop using exposed keys.
        assert await rotate_orgs(new_keyring, ORGS) == {ORG_A: 3, ORG_B: 2}
        with tenant_scope(ORG_B):
            assert (await new_keyring.encrypt(ORG_B, b"y", associated_data=AD)).key_version == 2

    asyncio.run(procedure())


def test_rewrap_refuses_records_wrapped_by_an_unknown_master() -> None:
    async def procedure() -> None:
        store = InMemoryOrgKeyStore()
        with tenant_scope(ORG_A):
            await Keyring(MasterKey.from_encoded(encoded_master_key()), store).create(ORG_A)
        stranger = Keyring(MasterKey.from_encoded(encoded_master_key()), store)
        with pytest.raises(KeyUnwrapError):
            await rewrap_orgs(stranger, MasterKey.from_encoded(encoded_master_key()), [ORG_A])

    asyncio.run(procedure())


def test_rewrap_detects_a_concurrent_change() -> None:
    async def procedure() -> None:
        store = InMemoryOrgKeyStore()
        keyring = Keyring(MasterKey.from_encoded(encoded_master_key()), store)
        with tenant_scope(ORG_A):
            await keyring.create(ORG_A)

        async def lost_race(record: OrgKeyRecord, *, expected_master_key_id: str) -> bool:
            return False

        store.replace = lost_race  # type: ignore[method-assign]  # another operator re-wrapped first
        with pytest.raises(KeyRewrapConflictError, match="changed while re-wrapping"):
            await rewrap_orgs(keyring, MasterKey.from_encoded(encoded_master_key()), [ORG_A])

    asyncio.run(procedure())


def test_the_runbook_documents_exactly_the_exercised_steps() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    steps = re.findall(r"^### ([AB]\d)\. ", text, flags=re.MULTILINE)
    assert steps == ["A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3", "B4", "B5", "B6"]
    tests = Path(__file__).read_text(encoding="utf-8")
    markers = re.findall(r"^\s+# ([AB]\d)\.", tests, flags=re.MULTILINE)
    assert markers == steps, "each runbook step is exercised once, in order"
    for name in ("verify_orgs", "rotate_orgs", "rewrap_orgs", "ensure_org_keys_index", "PII_MASTER_KEY"):
        assert name in text
    assert "openssl rand -base64 32" in text
