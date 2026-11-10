"""The PII vault (PRD §7.10): AES-256-GCM with AAD binding, an HMAC `sig`, opaque tokens, versions.

Every tamper test edits the stored document directly, as someone with database access would, and then
expects the read to fail closed: an exception, never a value.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from onb_crypto import (
    PURGED,
    DecryptionError,
    KeyPurpose,
    SupersededTokenError,
    TokenBindingError,
    TokenNotFoundError,
    Vault,
    VaultField,
    VaultIntegrityError,
    VaultStatus,
    is_token,
)
from onb_crypto.vault_record import record_from_document, signing_payload
from onb_data import TenantViolationError

from .fakes import ORG_A, ORG_B, in_org
from .vault_fakes import FakeVaultStore, keyring_for_two_orgs

HIRE_1 = "hire_0001"
HIRE_2 = "hire_0002"
HR_USER = "user_hr_01"
MOBILE_1 = "9000000001"
MOBILE_2 = "9000000002"
EMAIL_1 = "priya.test@example.com"
NOW = datetime(2026, 11, 10, 6, 0, 0, 123456, tzinfo=UTC)


def make_vault() -> tuple[Vault, FakeVaultStore]:
    store = FakeVaultStore()
    return Vault(keyring_for_two_orgs(), store, clock=lambda: NOW), store


def put(vault: Vault, subject: str, field: VaultField, value: str, org: str = ORG_A) -> str:
    return in_org(org, vault.put(org, subject_id=subject, field=field, value=value, created_by=HR_USER))


def resolve(vault: Vault, token: str, subject: str, field: VaultField, org: str = ORG_A, **kw: Any) -> str:
    return in_org(org, vault.resolve(token, org_id=org, subject_id=subject, field=field, **kw))


def raw(store: FakeVaultStore, token: str, collection: str = "pii_vault") -> dict[str, Any]:
    return store.tenant(collection).raw(token)


# --- the happy path ---------------------------------------------------------------------------------


def test_put_returns_a_token_and_resolve_returns_the_value() -> None:
    vault, store = make_vault()
    token = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    assert is_token(token)
    assert resolve(vault, token, HIRE_1, VaultField.MOBILE) == MOBILE_1
    assert store.written() == {"pii_vault": 1}


def test_the_stored_record_has_exactly_the_prd_fields_and_no_raw_value() -> None:
    vault, store = make_vault()
    token = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    doc = raw(store, token)
    assert set(doc) - {"_id"} == {
        "token",
        "org_id",
        "subject",
        "field",
        "ciphertext",
        "nonce",
        "key_version",
        "version",
        "status",
        "created_by",
        "created_at",
        "sig",
    }
    assert doc["subject"] == {"type": "new_hire", "id": HIRE_1}
    assert (doc["org_id"], doc["field"], doc["version"], doc["status"]) == (ORG_A, "mobile", 1, "active")
    assert doc["created_by"] == HR_USER
    assert doc["created_at"] == NOW.replace(microsecond=123000), "milliseconds, as MongoDB keeps them"
    assert MOBILE_1.encode() not in doc["ciphertext"]
    assert MOBILE_1 not in repr(doc)
    assert len(doc["sig"]) == 32, "HMAC-SHA256"


def test_the_ciphertext_is_aes_gcm_bound_to_org_subject_field_token_and_version() -> None:
    """The AAD is `org_id | subject.id | field | token | version`: decrypting with any part changed fails."""
    vault, store = make_vault()
    token = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    record = record_from_document(raw(store, token))
    keyring = vault.keyring
    good = Vault.associated_data(ORG_A, HIRE_1, VaultField.MOBILE, token, 1)
    assert in_org(ORG_A, keyring.decrypt(ORG_A, record.sealed(), associated_data=good)) == MOBILE_1.encode()
    for wrong in (
        Vault.associated_data(ORG_A, HIRE_2, VaultField.MOBILE, token, 1),
        Vault.associated_data(ORG_A, HIRE_1, VaultField.EMAIL, token, 1),
        Vault.associated_data(ORG_A, HIRE_1, VaultField.MOBILE, token.replace("ptk_", "ptk_0", 1)[:56], 1),
        Vault.associated_data(ORG_A, HIRE_1, VaultField.MOBILE, token, 2),
    ):
        with pytest.raises(DecryptionError):
            in_org(ORG_A, keyring.decrypt(ORG_A, record.sealed(), associated_data=wrong))


def test_the_signature_covers_every_field_but_itself_with_the_signing_key() -> None:
    vault, store = make_vault()
    token = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    doc = raw(store, token)
    record = record_from_document(doc)
    keys = in_org(ORG_A, vault.keyring.key_version(ORG_A, record.key_version))
    expected = hmac.new(keys.key(KeyPurpose.SIGNING).reveal(), signing_payload(record), hashlib.sha256).digest()
    assert doc["sig"] == expected
    data_key = hmac.new(keys.key(KeyPurpose.DATA).reveal(), signing_payload(record), hashlib.sha256).digest()
    assert doc["sig"] != data_key, "the signing key is not the encryption key"


def test_each_put_gets_a_fresh_token_and_nonce() -> None:
    vault, store = make_vault()
    t1 = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    t2 = put(vault, HIRE_2, VaultField.MOBILE, MOBILE_1)
    assert t1 != t2
    assert raw(store, t1)["nonce"] != raw(store, t2)["nonce"]
    assert raw(store, t1)["ciphertext"] != raw(store, t2)["ciphertext"]


# --- Box 1: each tamper fails closed ----------------------------------------------------------------


def test_tamper_changed_subject_id_fails_closed() -> None:
    """Re-pointing a token to another hire is detected, whichever hire the reader expects."""
    vault, store = make_vault()
    token = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    raw(store, token)["subject"]["id"] = HIRE_2
    with pytest.raises(VaultIntegrityError):
        resolve(vault, token, HIRE_2, VaultField.MOBILE)
    with pytest.raises((VaultIntegrityError, TokenBindingError)):
        resolve(vault, token, HIRE_1, VaultField.MOBILE)


def test_tamper_ciphertext_swapped_between_records_fails_closed() -> None:
    vault, store = make_vault()
    victim = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    donor = put(vault, HIRE_2, VaultField.MOBILE, MOBILE_2)
    for part in ("ciphertext", "nonce", "key_version"):
        raw(store, victim)[part] = raw(store, donor)[part]
    with pytest.raises(VaultIntegrityError):
        resolve(vault, victim, HIRE_1, VaultField.MOBILE)


def test_tamper_ciphertext_swapped_and_resigned_still_fails_on_the_aad() -> None:
    """Defence in depth: even with the signing key (so `sig` passes), the AAD keeps the swap from opening."""
    vault, store = make_vault()
    victim = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    donor = put(vault, HIRE_2, VaultField.MOBILE, MOBILE_2)
    doc = raw(store, victim)
    for part in ("ciphertext", "nonce", "key_version"):
        doc[part] = raw(store, donor)[part]
    keys = in_org(ORG_A, vault.keyring.key_version(ORG_A, 1))
    payload = signing_payload(record_from_document(doc))
    doc["sig"] = hmac.new(keys.key(KeyPurpose.SIGNING).reveal(), payload, hashlib.sha256).digest()
    with pytest.raises(DecryptionError):
        resolve(vault, victim, HIRE_1, VaultField.MOBILE)


def test_tamper_changed_field_fails_closed() -> None:
    vault, store = make_vault()
    token = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    raw(store, token)["field"] = "email"
    with pytest.raises(VaultIntegrityError):
        resolve(vault, token, HIRE_1, VaultField.EMAIL)
    with pytest.raises((VaultIntegrityError, TokenBindingError)):
        resolve(vault, token, HIRE_1, VaultField.MOBILE)


def test_tamper_changed_status_fails_closed() -> None:
    vault, store = make_vault()
    old = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    new = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_2)
    raw(store, old)["status"] = "active"  # revive a superseded value
    with pytest.raises(VaultIntegrityError):
        resolve(vault, old, HIRE_1, VaultField.MOBILE)
    raw(store, new)["status"] = "purged"  # hide a live value behind "[purged]"
    with pytest.raises(VaultIntegrityError):
        resolve(vault, new, HIRE_1, VaultField.MOBILE)


def test_tamper_edited_sig_fails_closed() -> None:
    vault, store = make_vault()
    token = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    doc = raw(store, token)
    doc["sig"] = bytes([doc["sig"][0] ^ 1]) + doc["sig"][1:]
    with pytest.raises(VaultIntegrityError):
        resolve(vault, token, HIRE_1, VaultField.MOBILE)
    doc["sig"] = b""
    with pytest.raises(VaultIntegrityError):
        resolve(vault, token, HIRE_1, VaultField.MOBILE)
    del doc["sig"]
    with pytest.raises(VaultIntegrityError):
        resolve(vault, token, HIRE_1, VaultField.MOBILE)


@pytest.mark.parametrize(
    ("part", "value"),
    [
        ("token", "ptk_" + "0" * 52),
        ("version", 7),
        ("key_version", 2),
        ("created_by", "user_attacker"),
        ("created_at", datetime(2020, 1, 1, tzinfo=UTC)),
        ("nonce", b"\x00" * 12),
        ("ciphertext", b"\x00" * 26),
    ],
)
def test_tamper_with_any_other_signed_part_fails_closed(part: str, value: Any) -> None:
    vault, store = make_vault()
    token = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    doc = raw(store, token)
    doc[part] = value
    lookup = value if part == "token" else token
    with pytest.raises(VaultIntegrityError):
        resolve(vault, lookup, HIRE_1, VaultField.MOBILE)


def test_a_record_signed_by_another_org_fails_closed() -> None:
    """A record copied from org B into org A (org_id rewritten) does not verify with org A's key."""
    vault, store = make_vault()
    token_b = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1, org=ORG_B)
    copied = {**raw(store, token_b), "org_id": ORG_A, "_id": "copied"}
    repo = store.tenant("pii_vault")
    repo.documents = [*repo.documents, copied]
    with pytest.raises(VaultIntegrityError):
        resolve(vault, token_b, HIRE_1, VaultField.MOBILE, org=ORG_A)


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda d: d.update(extra="x"),
        lambda d: d.update(subject={"type": "new_hire"}),
        lambda d: d.update(subject={"type": "employee", "id": HIRE_1}),
        lambda d: d.update(subject="hire_0001"),
        lambda d: d.update(field="shoe_size"),
        lambda d: d.update(status="deleted"),
        lambda d: d.update(version="1"),
        lambda d: d.update(version=True),
        lambda d: d.update(key_version=0),
        lambda d: d.update(ciphertext="text"),
        lambda d: d.update(created_at="2026-11-10"),
        lambda d: d.update(created_by=""),
        lambda d: d.update(sig="not bytes"),
        lambda d: d.pop("nonce"),
    ],
)
def test_a_malformed_record_fails_closed(corrupt: Any) -> None:
    vault, store = make_vault()
    token = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    corrupt(raw(store, token))
    with pytest.raises(VaultIntegrityError):
        resolve(vault, token, HIRE_1, VaultField.MOBILE)


# --- binding: the reader states what it expects -----------------------------------------------------


def test_resolving_with_the_wrong_subject_or_field_is_refused() -> None:
    vault, _ = make_vault()
    token = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    with pytest.raises(TokenBindingError):
        resolve(vault, token, HIRE_2, VaultField.MOBILE)
    with pytest.raises(TokenNotFoundError):
        resolve(vault, token, HIRE_1, VaultField.AADHAAR)  # another partition: not found there


def test_resolving_with_the_wrong_field_in_the_same_partition_is_refused() -> None:
    vault, _ = make_vault()
    token = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    with pytest.raises(TokenBindingError):
        resolve(vault, token, HIRE_1, VaultField.EMAIL)


def test_another_orgs_token_is_not_found() -> None:
    vault, _ = make_vault()
    token = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1, org=ORG_B)
    with pytest.raises(TokenNotFoundError):
        resolve(vault, token, HIRE_1, VaultField.MOBILE, org=ORG_A)


def test_the_org_must_be_the_tenant_context() -> None:
    vault, _ = make_vault()
    token = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    with pytest.raises(TenantViolationError):
        in_org(ORG_A, vault.resolve(token, org_id=ORG_B, subject_id=HIRE_1, field=VaultField.MOBILE))
    with pytest.raises(TenantViolationError):
        in_org(ORG_A, vault.put(ORG_B, subject_id=HIRE_1, field=VaultField.MOBILE, value="x", created_by=HR_USER))


@pytest.mark.parametrize("token", ["", "ptk_short", "nope", "ptk_" + "I" * 52])
def test_a_malformed_token_is_not_looked_up(token: str) -> None:
    vault, _ = make_vault()
    with pytest.raises(TokenNotFoundError):
        resolve(vault, token, HIRE_1, VaultField.MOBILE)


def test_a_duplicated_token_fails_closed() -> None:
    vault, store = make_vault()
    token = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    repo = store.tenant("pii_vault")
    repo.documents = [*repo.documents, {**raw(store, token), "_id": "dup"}]
    with pytest.raises(VaultIntegrityError):
        resolve(vault, token, HIRE_1, VaultField.MOBILE)


# --- Box 2: an update makes a new token and supersedes the old record --------------------------------


def test_an_update_creates_a_new_token_supersedes_the_old_record_and_both_verify() -> None:
    vault, store = make_vault()
    old = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    new = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_2)
    assert new != old
    assert is_token(new)

    old_doc, new_doc = raw(store, old), raw(store, new)
    assert (old_doc["version"], old_doc["status"]) == (1, "superseded")
    assert (new_doc["version"], new_doc["status"]) == (2, "active")
    assert store.written() == {"pii_vault": 2}, "the old record is kept for audit"

    old_record = in_org(ORG_A, vault.verify(old, org_id=ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE))
    new_record = in_org(ORG_A, vault.verify(new, org_id=ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE))
    assert old_record.status is VaultStatus.SUPERSEDED
    assert new_record.status is VaultStatus.ACTIVE

    assert resolve(vault, new, HIRE_1, VaultField.MOBILE) == MOBILE_2
    assert resolve(vault, old, HIRE_1, VaultField.MOBILE, allow_superseded=True) == MOBILE_1


def test_a_superseded_token_does_not_resolve_by_default() -> None:
    vault, _ = make_vault()
    old = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    put(vault, HIRE_1, VaultField.MOBILE, MOBILE_2)
    with pytest.raises(SupersededTokenError):
        resolve(vault, old, HIRE_1, VaultField.MOBILE)


def test_versions_are_per_subject_and_field() -> None:
    vault, store = make_vault()
    put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    put(vault, HIRE_1, VaultField.MOBILE, MOBILE_2)
    third = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    email = put(vault, HIRE_1, VaultField.EMAIL, EMAIL_1)
    other = put(vault, HIRE_2, VaultField.MOBILE, MOBILE_1)
    assert raw(store, third)["version"] == 3
    assert raw(store, email)["version"] == 1
    assert raw(store, other)["version"] == 1
    statuses = sorted(d["status"] for d in store.tenant("pii_vault").documents if d["subject"]["id"] == HIRE_1)
    assert statuses == ["active", "active", "superseded", "superseded"]


def test_an_update_over_a_tampered_current_record_fails_closed() -> None:
    vault, store = make_vault()
    old = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    raw(store, old)["created_by"] = "user_attacker"
    with pytest.raises(VaultIntegrityError):
        put(vault, HIRE_1, VaultField.MOBILE, MOBILE_2)
    assert store.written() == {"pii_vault": 1}, "nothing written over a record that does not verify"


def test_an_update_that_loses_a_race_is_reported() -> None:
    """If the old record changed between read and supersede, the update fails loudly."""
    vault, store = make_vault()
    put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    store.tenant("pii_vault").fail_next_update = True
    with pytest.raises(VaultIntegrityError, match="superseded"):
        put(vault, HIRE_1, VaultField.MOBILE, MOBILE_2)


def test_a_concurrent_insert_of_the_same_version_is_refused() -> None:
    from onb_crypto import VaultConflictError

    vault, store = make_vault()
    first = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    assert raw(store, first)["version"] == 1
    repo = store.tenant("pii_vault")
    real_find = repo.find

    async def stale_find(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []  # this writer saw no current record, so it also tries version 1

    repo.find = stale_find  # type: ignore[method-assign]  # simulating a stale reader
    with pytest.raises(VaultConflictError):
        put(vault, HIRE_1, VaultField.MOBILE, MOBILE_2)
    repo.find = real_find  # type: ignore[method-assign]
    assert store.written() == {"pii_vault": 1}


# --- Retention status: purged resolves to "[purged]" -------------------------------------------------


def test_a_purged_record_resolves_to_the_purged_marker_and_keeps_no_ciphertext() -> None:
    vault, store = make_vault()
    token = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    in_org(ORG_A, vault.purge(token, org_id=ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE))
    doc = raw(store, token)
    assert (doc["status"], doc["ciphertext"], doc["nonce"]) == ("purged", b"", b"")
    assert PURGED == "[purged]"
    assert resolve(vault, token, HIRE_1, VaultField.MOBILE) == PURGED
    assert resolve(vault, token, HIRE_1, VaultField.MOBILE, allow_superseded=True) == PURGED
    record = in_org(ORG_A, vault.verify(token, org_id=ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE))
    assert record.status is VaultStatus.PURGED


def test_purging_twice_is_a_no_op_and_a_superseded_record_can_be_purged() -> None:
    vault, _ = make_vault()
    old = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    put(vault, HIRE_1, VaultField.MOBILE, MOBILE_2)
    for _ in range(2):
        in_org(ORG_A, vault.purge(old, org_id=ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE))
    assert resolve(vault, old, HIRE_1, VaultField.MOBILE) == PURGED


def test_a_purged_record_with_ciphertext_left_fails_closed() -> None:
    vault, store = make_vault()
    token = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    ciphertext = raw(store, token)["ciphertext"]
    in_org(ORG_A, vault.purge(token, org_id=ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE))
    raw(store, token)["ciphertext"] = ciphertext
    with pytest.raises(VaultIntegrityError):
        resolve(vault, token, HIRE_1, VaultField.MOBILE)


def test_a_purged_record_that_kept_its_ciphertext_fails_closed_even_when_signed() -> None:
    """A purge that re-signed but left the ciphertext (a buggy job) is refused by shape, not only by `sig`."""
    vault, store = make_vault()
    token = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    doc = raw(store, token)
    leftover = replace(record_from_document(doc), status=VaultStatus.PURGED)  # ciphertext kept
    doc["status"] = "purged"
    keys = in_org(ORG_A, vault.keyring.key_version(ORG_A, 1))
    payload = signing_payload(leftover)
    doc["sig"] = hmac.new(keys.key(KeyPurpose.SIGNING).reveal(), payload, hashlib.sha256).digest()
    with pytest.raises(VaultIntegrityError):
        resolve(vault, token, HIRE_1, VaultField.MOBILE)


def test_a_purge_that_loses_a_race_is_reported() -> None:
    vault, store = make_vault()
    token = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    store.tenant("pii_vault").fail_next_update = True
    with pytest.raises(VaultIntegrityError, match="purge"):
        in_org(ORG_A, vault.purge(token, org_id=ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE))


# --- input checks -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subject", "value", "created_by"),
    [
        ("", MOBILE_1, HR_USER),
        ("hire\x1f1", MOBILE_1, HR_USER),
        ("x" * 129, MOBILE_1, HR_USER),
        (HIRE_1, "", HR_USER),
        (HIRE_1, MOBILE_1, ""),
    ],
)
def test_put_refuses_bad_input_without_echoing_the_value(subject: str, value: str, created_by: str) -> None:
    vault, store = make_vault()
    with pytest.raises(ValueError, match="must be") as caught:
        in_org(ORG_A, vault.put(ORG_A, subject_id=subject, field=VaultField.MOBILE, value=value, created_by=created_by))
    if value:
        assert value not in str(caught.value)
    assert store.written() == {}


def test_put_refuses_a_field_that_is_not_a_vault_field() -> None:
    vault, _ = make_vault()
    with pytest.raises(ValueError, match="field"):
        in_org(ORG_A, vault.put(ORG_A, subject_id=HIRE_1, field="shoe_size", value="9", created_by=HR_USER))  # type: ignore[arg-type]  # the wrong type is the point


def test_the_vault_never_logs_values(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    vault, store = make_vault()
    old = put(vault, HIRE_1, VaultField.MOBILE, MOBILE_1)
    put(vault, HIRE_1, VaultField.MOBILE, MOBILE_2)
    resolve(vault, old, HIRE_1, VaultField.MOBILE, allow_superseded=True)
    raw(store, old)["status"] = "active"
    with pytest.raises(VaultIntegrityError):
        resolve(vault, old, HIRE_1, VaultField.MOBILE)
    text = "\n".join(f"{r.getMessage()} {r.args!r} {r.exc_text}" for r in caplog.records)
    assert caplog.records, "the vault logs writes and failures"
    assert MOBILE_1 not in text
    assert MOBILE_2 not in text
