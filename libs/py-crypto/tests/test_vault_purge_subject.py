"""`Vault.purge_subject` (PII-5): every ciphertext of a subject goes, superseded versions included; the
consent record stays; a second call changes nothing (PRD §7.10 Retention)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from onb_crypto import (
    CONSENT_COLLECTION,
    AadhaarConsent,
    Vault,
    VaultField,
    VaultIntegrityError,
)
from onb_crypto.vault import PURGED, PurgeIncompleteError

from .fakes import ORG_A, ORG_B, in_org
from .vault_fakes import FakeVaultStore, keyring_for_two_orgs

HIRE_1 = "hire_0001"
HIRE_2 = "hire_0002"
HR = "user_hr_01"
NOW = datetime(2026, 11, 10, 6, 0, tzinfo=UTC)
CONSENT = AadhaarConsent(purpose="employment onboarding KYC")


def make_vault() -> tuple[Vault, FakeVaultStore]:
    store = FakeVaultStore()
    return Vault(keyring_for_two_orgs(), store, clock=lambda: NOW), store


def put(vault: Vault, field: VaultField, value: str, *, org: str = ORG_A, subject: str = HIRE_1) -> str:
    consent = CONSENT if field is VaultField.AADHAAR else None
    return in_org(org, vault.put(org, subject_id=subject, field=field, value=value, created_by=HR, consent=consent))


def test_purge_subject_purges_every_field_in_both_partitions() -> None:
    vault, _ = make_vault()
    mobile = put(vault, VaultField.MOBILE, "9000000001")
    aadhaar = put(vault, VaultField.AADHAAR, "999900001231")
    outcomes = in_org(ORG_A, vault.purge_subject(ORG_A, subject_id=HIRE_1))
    assert {o.token for o in outcomes} == {mobile, aadhaar}
    assert all(o.newly_purged for o in outcomes)
    for token, field in ((mobile, VaultField.MOBILE), (aadhaar, VaultField.AADHAAR)):
        value = in_org(ORG_A, vault.resolve(token, org_id=ORG_A, subject_id=HIRE_1, field=field))
        assert value == PURGED


def test_purge_subject_also_purges_superseded_versions() -> None:
    vault, store = make_vault()
    old = put(vault, VaultField.MOBILE, "9000000001")
    new = put(vault, VaultField.MOBILE, "9000000002")
    outcomes = in_org(ORG_A, vault.purge_subject(ORG_A, subject_id=HIRE_1))
    assert {o.token for o in outcomes} == {old, new}
    for doc in store.tenant("pii_vault").documents:
        assert doc["status"] == "purged"
        assert doc["ciphertext"] == b""
        assert doc["nonce"] == b""


def test_purge_subject_is_idempotent() -> None:
    vault, _ = make_vault()
    token = put(vault, VaultField.PAN, "BQVPS4821K")
    first = in_org(ORG_A, vault.purge_subject(ORG_A, subject_id=HIRE_1))
    second = in_org(ORG_A, vault.purge_subject(ORG_A, subject_id=HIRE_1))
    assert [o.newly_purged for o in first] == [True]
    assert [(o.token, o.newly_purged) for o in second] == [(token, False)]


def test_purge_subject_keeps_the_consent_record() -> None:
    vault, store = make_vault()
    put(vault, VaultField.AADHAAR, "999900001231")
    before = list(store.tenant(CONSENT_COLLECTION).documents)
    in_org(ORG_A, vault.purge_subject(ORG_A, subject_id=HIRE_1))
    assert before
    assert store.tenant(CONSENT_COLLECTION).documents == before


def test_purge_subject_leaves_other_subjects_and_orgs_alone() -> None:
    vault, _ = make_vault()
    other_hire = put(vault, VaultField.MOBILE, "9000000003", subject=HIRE_2)
    other_org = put(vault, VaultField.MOBILE, "9000000004", org=ORG_B)
    put(vault, VaultField.MOBILE, "9000000001")
    in_org(ORG_A, vault.purge_subject(ORG_A, subject_id=HIRE_1))
    assert in_org(ORG_A, vault.resolve(other_hire, org_id=ORG_A, subject_id=HIRE_2, field=VaultField.MOBILE)) == (
        "9000000003"
    )
    assert in_org(ORG_B, vault.resolve(other_org, org_id=ORG_B, subject_id=HIRE_1, field=VaultField.MOBILE)) == (
        "9000000004"
    )


def test_purge_subject_with_no_values_returns_nothing() -> None:
    vault, _ = make_vault()
    assert in_org(ORG_A, vault.purge_subject(ORG_A, subject_id=HIRE_1)) == ()


def test_one_tampered_record_does_not_stop_the_purge_of_the_others() -> None:
    vault, store = make_vault()
    put(vault, VaultField.MOBILE, "9000000001")
    pan = put(vault, VaultField.PAN, "BQVPS4821K")
    aadhaar = put(vault, VaultField.AADHAAR, "999900001231")
    mobile_doc = next(d for d in store.tenant("pii_vault").documents if d["field"] == "mobile")
    mobile_doc["created_by"] = "someone_else"
    with pytest.raises(PurgeIncompleteError) as raised:
        in_org(ORG_A, vault.purge_subject(ORG_A, subject_id=HIRE_1))
    assert isinstance(raised.value, VaultIntegrityError)
    assert {o.token for o in raised.value.outcomes} == {pan, aadhaar}
    assert len(raised.value.errors) == 1
    assert mobile_doc["ciphertext"] != b""
    assert in_org(ORG_A, vault.resolve(pan, org_id=ORG_A, subject_id=HIRE_1, field=VaultField.PAN)) == PURGED


def test_purge_subject_fails_closed_on_a_tampered_record() -> None:
    vault, store = make_vault()
    token = put(vault, VaultField.MOBILE, "9000000001")
    store.tenant("pii_vault").documents[0]["field"] = "pan"
    with pytest.raises(VaultIntegrityError):
        in_org(ORG_A, vault.purge_subject(ORG_A, subject_id=HIRE_1))
    assert token  # the tampered record was not purged by a path that skips the signature check
    assert store.tenant("pii_vault").documents[0]["ciphertext"] != b""
