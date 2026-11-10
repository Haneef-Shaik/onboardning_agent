"""Aadhaar in its own partition (`pii_vault_aadhaar`) with its own per-org key and a consent record
(PRD §7.10 Aadhaar, UIDAI Aadhaar Data Vault model)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from onb_crypto import (
    AADHAAR_VAULT_COLLECTION,
    CONSENT_COLLECTION,
    VAULT_COLLECTION,
    AadhaarConsent,
    DecryptionError,
    KeyPurpose,
    TokenBindingError,
    Vault,
    VaultField,
    VaultIntegrityError,
)
from onb_crypto.vault_record import record_from_document

from .fakes import ORG_A, in_org
from .vault_fakes import FakeVaultStore, keyring_for_two_orgs

HIRE_1 = "hire_0001"
HR_ADMIN = "user_hr_admin_01"
AADHAAR = "999900001231"  # synthetic, 9999-prefixed
AADHAAR_2 = "999900000125"
CONSENT = AadhaarConsent(purpose="employment onboarding KYC")
NOW = datetime(2026, 11, 10, 6, 0, tzinfo=UTC)


def make_vault() -> tuple[Vault, FakeVaultStore]:
    store = FakeVaultStore()
    return Vault(keyring_for_two_orgs(), store, clock=lambda: NOW), store


def put_aadhaar(vault: Vault, value: str = AADHAAR, consent: AadhaarConsent | None = CONSENT) -> str:
    return in_org(
        ORG_A,
        vault.put(
            ORG_A, subject_id=HIRE_1, field=VaultField.AADHAAR, value=value, created_by=HR_ADMIN, consent=consent
        ),
    )


def test_the_partition_names() -> None:
    assert (VAULT_COLLECTION, AADHAAR_VAULT_COLLECTION, CONSENT_COLLECTION) == (
        "pii_vault",
        "pii_vault_aadhaar",
        "pii_consent",
    )


def test_aadhaar_records_land_only_in_the_aadhaar_partition() -> None:
    vault, store = make_vault()
    token = put_aadhaar(vault)
    in_org(
        ORG_A,
        vault.put(ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE, value="9000000001", created_by=HR_ADMIN),
    )
    aadhaar_docs = store.tenant("pii_vault_aadhaar").documents
    general_docs = store.tenant("pii_vault").documents
    assert [d["token"] for d in aadhaar_docs] == [token]
    assert all(d["field"] != "aadhaar" for d in general_docs)
    assert token not in {d["token"] for d in general_docs}
    for name, repo in store.repos.items():
        if name != "pii_vault_aadhaar":
            assert all(AADHAAR not in repr(d) for d in repo.documents)
    assert in_org(ORG_A, vault.resolve(token, org_id=ORG_A, subject_id=HIRE_1, field=VaultField.AADHAAR)) == AADHAAR


def test_every_other_field_lands_only_in_the_general_partition() -> None:
    vault, store = make_vault()
    for field in VaultField:
        if field is VaultField.AADHAAR:
            continue
        in_org(ORG_A, vault.put(ORG_A, subject_id=HIRE_1, field=field, value="value-1", created_by=HR_ADMIN))
    assert store.written() == {"pii_vault": len(VaultField) - 1}


def test_aadhaar_uses_the_aadhaar_key_and_not_the_data_key() -> None:
    vault, store = make_vault()
    a_token = put_aadhaar(vault)
    m_token = in_org(
        ORG_A, vault.put(ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE, value="9000000001", created_by=HR_ADMIN)
    )
    keys = in_org(ORG_A, vault.keyring.active(ORG_A))
    assert keys.key(KeyPurpose.AADHAAR).reveal() != keys.key(KeyPurpose.DATA).reveal()

    a_record = record_from_document(store.tenant("pii_vault_aadhaar").raw(a_token))
    a_ad = Vault.associated_data(ORG_A, HIRE_1, VaultField.AADHAAR, a_token, 1)
    keyring = vault.keyring
    opened = in_org(ORG_A, keyring.decrypt(ORG_A, a_record.sealed(), associated_data=a_ad, purpose=KeyPurpose.AADHAAR))
    assert opened == AADHAAR.encode()
    with pytest.raises(DecryptionError):
        in_org(ORG_A, keyring.decrypt(ORG_A, a_record.sealed(), associated_data=a_ad, purpose=KeyPurpose.DATA))

    m_record = record_from_document(store.tenant("pii_vault").raw(m_token))
    m_ad = Vault.associated_data(ORG_A, HIRE_1, VaultField.MOBILE, m_token, 1)
    assert in_org(ORG_A, keyring.decrypt(ORG_A, m_record.sealed(), associated_data=m_ad)) == b"9000000001"
    with pytest.raises(DecryptionError):
        in_org(ORG_A, keyring.decrypt(ORG_A, m_record.sealed(), associated_data=m_ad, purpose=KeyPurpose.AADHAAR))


def test_an_aadhaar_record_moved_into_the_general_partition_is_not_resolved_there() -> None:
    vault, store = make_vault()
    token = put_aadhaar(vault)
    moved = dict(store.tenant("pii_vault_aadhaar").raw(token))
    general = store.tenant("pii_vault")
    general.documents = [*general.documents, moved]
    with pytest.raises(VaultIntegrityError, match="partition"):
        in_org(ORG_A, vault.resolve(token, org_id=ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE))


def test_a_general_record_moved_into_the_aadhaar_partition_is_refused() -> None:
    vault, store = make_vault()
    m_token = in_org(
        ORG_A, vault.put(ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE, value="9000000001", created_by=HR_ADMIN)
    )
    aadhaar = store.tenant("pii_vault_aadhaar")
    aadhaar.documents = [*aadhaar.documents, dict(store.tenant("pii_vault").raw(m_token))]
    with pytest.raises(VaultIntegrityError, match="partition"):
        in_org(ORG_A, vault.resolve(m_token, org_id=ORG_A, subject_id=HIRE_1, field=VaultField.AADHAAR))
    with pytest.raises(TokenBindingError):  # the intact original in pii_vault is still bound to mobile
        in_org(ORG_A, vault.resolve(m_token, org_id=ORG_A, subject_id=HIRE_1, field=VaultField.EMAIL))


def test_aadhaar_needs_a_consent_record() -> None:
    vault, store = make_vault()
    with pytest.raises(ValueError, match="consent"):
        put_aadhaar(vault, consent=None)
    assert store.written() == {}


def test_consent_is_only_for_aadhaar() -> None:
    vault, _ = make_vault()
    with pytest.raises(ValueError, match="consent"):
        in_org(
            ORG_A,
            vault.put(
                ORG_A,
                subject_id=HIRE_1,
                field=VaultField.MOBILE,
                value="9000000001",
                created_by=HR_ADMIN,
                consent=CONSENT,
            ),
        )


def test_the_consent_record_is_written_with_the_aadhaar_record() -> None:
    vault, store = make_vault()
    token = put_aadhaar(vault)
    consents = store.tenant("pii_consent").documents
    assert len(consents) == 1
    consent = {k: v for k, v in consents[0].items() if k != "_id"}
    assert consent == {
        "org_id": ORG_A,
        "subject": {"type": "new_hire", "id": HIRE_1},
        "field": "aadhaar",
        "token": token,
        "purpose": CONSENT.purpose,
        "captured_by": HR_ADMIN,
        "captured_at": NOW,
    }
    assert AADHAAR not in repr(consents)


def test_an_aadhaar_update_supersedes_within_the_partition_and_records_consent_again() -> None:
    vault, store = make_vault()
    old = put_aadhaar(vault)
    new = put_aadhaar(vault, AADHAAR_2)
    docs = {d["token"]: d for d in store.tenant("pii_vault_aadhaar").documents}
    assert (docs[old]["status"], docs[new]["status"]) == ("superseded", "active")
    assert store.written() == {"pii_vault_aadhaar": 2, "pii_consent": 2}


@pytest.mark.parametrize("purpose", ["", "   ", "x" * 501])
def test_a_consent_needs_a_stated_purpose(purpose: str) -> None:
    with pytest.raises(ValueError, match="purpose"):
        AadhaarConsent(purpose=purpose)


def test_an_aadhaar_value_of_the_wrong_shape_is_refused_without_echoing_it() -> None:
    vault, store = make_vault()
    with pytest.raises(ValueError, match="malformed aadhaar") as caught:
        put_aadhaar(vault, "12345")
    assert "12345" not in str(caught.value)
    assert store.written() == {}


def test_aadhaar_is_stored_as_its_twelve_digits() -> None:
    vault, store = make_vault()
    token = put_aadhaar(vault, "9999 0000 1231")
    assert in_org(ORG_A, vault.resolve(token, org_id=ORG_A, subject_id=HIRE_1, field=VaultField.AADHAAR)) == AADHAAR
    assert store.written()["pii_vault_aadhaar"] == 1
