"""Box 4: every step of docs/runbooks/key-rotation.md, run in order (PRD §15 Key loss / leakage).

The same steps run against the compose MongoDB in `tests_int/test_org_keys_mongo.py`.
"""

import asyncio
import re
from pathlib import Path

import pytest
from onb_crypto import (
    KeyPurpose,
    Keyring,
    KeyUnwrapError,
    MasterKey,
    rewrap_orgs,
    rotate_orgs,
    verify_orgs,
)

from .fakes import ORG_A, ORG_B, InMemoryOrgKeyStore, encoded_master_key

RUNBOOK = Path(__file__).resolve().parents[3] / "docs" / "runbooks" / "key-rotation.md"
AD = b"hire_1|mobile|ptk_1|1"
ORGS = [ORG_A, ORG_B]


def test_org_key_rotation_runbook_procedure_a() -> None:
    async def procedure() -> None:
        store = InMemoryOrgKeyStore()
        keyring = Keyring(MasterKey.from_encoded(encoded_master_key()), store)
        for org in ORGS:
            await keyring.create(org)
        before = {org: await keyring.encrypt(org, b"value-" + org.encode(), associated_data=AD) for org in ORGS}

        # A1. Record the active versions.
        assert await verify_orgs(keyring, ORGS) == {ORG_A: 1, ORG_B: 1}
        # A2. Rotate every org.
        assert await rotate_orgs(keyring, ORGS) == {ORG_A: 2, ORG_B: 2}
        # A3. Restart the services that hold keys: a running process keeps its cached version until then.
        running = Keyring(keyring.master, store)
        restarted = Keyring(keyring.master, store)
        # A4. Verify: every version of every org unwraps, new writes use the new version, old data reads.
        assert await verify_orgs(restarted, ORGS) == {ORG_A: 2, ORG_B: 2}
        for org in ORGS:
            fresh = await restarted.encrypt(org, b"after", associated_data=AD)
            assert fresh.key_version == 2
            assert await restarted.decrypt(org, before[org], associated_data=AD) == b"value-" + org.encode()
            assert await running.decrypt(org, fresh, associated_data=AD) == b"after"
        # A5. Old versions stay: nothing was deleted.
        assert sorted(store.records) == [(ORG_A, 1), (ORG_A, 2), (ORG_B, 1), (ORG_B, 2)]

    asyncio.run(procedure())


def test_master_key_rotation_runbook_procedure_b() -> None:
    async def procedure() -> None:
        store = InMemoryOrgKeyStore()
        old_master = MasterKey.from_encoded(encoded_master_key())
        keyring = Keyring(old_master, store)
        for org in ORGS:
            await keyring.create(org)
        await rotate_orgs(keyring, [ORG_A])
        sealed = {org: await keyring.encrypt(org, b"v", associated_data=AD) for org in ORGS}
        index_before = (await keyring.active(ORG_A)).key(KeyPurpose.INDEX)

        # B1. Generate the new master key (`openssl rand -base64 32`).
        new_master = MasterKey.from_encoded(encoded_master_key())
        # B2. Stop the writers. B3. Re-wrap every org's keys: old master in, new master out.
        assert await rewrap_orgs(keyring, new_master, ORGS) == {ORG_A: 2, ORG_B: 1}
        # The step is resumable: a second run finds nothing left to re-wrap.
        assert await rewrap_orgs(keyring, new_master, ORGS) == {ORG_A: 0, ORG_B: 0}
        # B4. Verify with the new master key only.
        new_keyring = Keyring(new_master, store)
        assert await verify_orgs(new_keyring, ORGS) == {ORG_A: 2, ORG_B: 1}
        for org in ORGS:
            assert await new_keyring.decrypt(org, sealed[org], associated_data=AD) == b"v"
        assert (await new_keyring.active(ORG_A)).key(KeyPurpose.INDEX) == index_before
        # B5. The old master key no longer opens anything.
        with pytest.raises(KeyUnwrapError):
            await verify_orgs(Keyring(old_master, store), ORGS)

    asyncio.run(procedure())


def test_rewrap_refuses_records_wrapped_by_an_unknown_master() -> None:
    async def procedure() -> None:
        store = InMemoryOrgKeyStore()
        await Keyring(MasterKey.from_encoded(encoded_master_key()), store).create(ORG_A)
        stranger = Keyring(MasterKey.from_encoded(encoded_master_key()), store)
        with pytest.raises(KeyUnwrapError):
            await rewrap_orgs(stranger, MasterKey.from_encoded(encoded_master_key()), [ORG_A])

    asyncio.run(procedure())


def test_rewrap_detects_a_concurrent_change() -> None:
    async def procedure() -> None:
        store = InMemoryOrgKeyStore()
        keyring = Keyring(MasterKey.from_encoded(encoded_master_key()), store)
        await keyring.create(ORG_A)

        async def lost_race(record: object, *, expected_master_key_id: str) -> bool:
            return False

        store.replace = lost_race  # type: ignore[method-assign]  # another operator re-wrapped first
        with pytest.raises(RuntimeError, match="changed while re-wrapping"):
            await rewrap_orgs(keyring, MasterKey.from_encoded(encoded_master_key()), [ORG_A])

    asyncio.run(procedure())


def test_the_runbook_documents_exactly_the_exercised_steps() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    steps = re.findall(r"^### ([AB]\d)\. ", text, flags=re.MULTILINE)
    assert steps == ["A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3", "B4", "B5"]
    tests = Path(__file__).read_text(encoding="utf-8")
    for step in steps:
        assert f"# {step}." in tests or f"{step}." in tests, f"runbook step {step} is not exercised"
    for name in ("verify_orgs", "rotate_orgs", "rewrap_orgs", "PII_MASTER_KEY", "openssl rand -base64 32"):
        assert name in text
