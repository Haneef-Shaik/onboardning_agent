"""Box 3: no key material reaches the logs, nor the error messages that end up in them (PRD §7.10 Logs)."""

import asyncio
import base64
import logging

import pytest
from onb_crypto import (
    MASTER_KEY_ENV,
    BlindIndexField,
    DecryptionError,
    KeyPurpose,
    Keyring,
    KeyUnwrapError,
    MasterKey,
    MasterKeyError,
    rewrap_orgs,
    rotate_orgs,
)
from onb_data import tenant_scope

from .fakes import ORG_A, ORG_B, InMemoryOrgKeyStore, encoded_master_key

AD = b"hire_1|mobile|ptk_1|1"


def _forms(material: bytes) -> list[str]:
    """Every way the bytes could plausibly be written into a log line."""
    return [
        material.hex(),
        material.hex().upper(),
        base64.b64encode(material).decode(),
        base64.urlsafe_b64encode(material).decode().rstrip("="),
        repr(material)[2:-1],
        material.decode("latin-1"),
    ]


def test_no_key_material_appears_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    encoded_old, encoded_new = encoded_master_key(), encoded_master_key()
    old_master, new_master = MasterKey.from_encoded(encoded_old), MasterKey.from_encoded(encoded_new)
    store = InMemoryOrgKeyStore()
    keyring = Keyring(old_master, store)
    errors: list[str] = []

    async def every_operation() -> list[bytes]:
        with tenant_scope(ORG_B):
            await keyring.create(ORG_B)
        with tenant_scope(ORG_A):
            await keyring.create(ORG_A)
            v1 = await keyring.active(ORG_A)
            sealed = await keyring.encrypt(ORG_A, b"9000000001", associated_data=AD)
            await keyring.decrypt(ORG_A, sealed, associated_data=AD)
            await keyring.blind_index(ORG_A, BlindIndexField.MOBILE, "9000000001")
        with tenant_scope(ORG_B):
            try:
                await keyring.decrypt(ORG_B, sealed, associated_data=AD)
            except DecryptionError as exc:
                errors.append(f"{exc!r} {exc}")
        await rotate_orgs(keyring, [ORG_A, ORG_B])
        with tenant_scope(ORG_A):
            v2 = await keyring.active(ORG_A)
            try:
                await Keyring(new_master, store).active(ORG_A)
            except KeyUnwrapError as exc:
                errors.append(f"{exc!r} {exc}")
        await rewrap_orgs(keyring, new_master, [ORG_A, ORG_B])
        with tenant_scope(ORG_B):
            b_keys = await Keyring(new_master, store).active(ORG_B)
        return [k.key(p).reveal() for k in (v1, v2, b_keys) for p in KeyPurpose]

    key_material = asyncio.run(every_operation())
    for bad_env in ({}, {MASTER_KEY_ENV: encoded_old[:20]}):
        try:
            MasterKey.from_env(bad_env)
        except MasterKeyError as exc:
            errors.append(f"{exc!r} {exc}")

    secrets_to_find = [*key_material, old_master.key.reveal(), new_master.key.reveal()]
    logged = "\n".join(
        f"{r.getMessage()} {r.args!r} {r.exc_text or ''} {r.__dict__!r}" for r in caplog.records
    ) + "\n".join(errors)

    assert len(caplog.records) >= 6, "the keyring logs its operations, so this test is not vacuous"
    assert len(errors) == 4
    messages = [r.getMessage() for r in caplog.records]
    assert any("rotated" in m and ORG_A in m for m in messages)
    assert any("unwrap failed" in m for m in messages)
    assert any("decrypt failed" in m for m in messages)
    for material in secrets_to_find:
        for form in _forms(material):
            assert form not in logged
    for encoded in (encoded_old, encoded_new, encoded_old[:20]):
        assert encoded not in logged
