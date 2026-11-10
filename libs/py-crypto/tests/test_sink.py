"""Resolving tokens at a sink (PRD §7.10 sink flow, FR-P5, §8.1 Tool):

    verify grant → verify token binding → verify vault record signature → decrypt → log masked copy

Each rejection named on the PII-4 card has its own test. Every rejection releases nothing, writes no
`pii_access_log` entry and emits exactly one audit record with the reason.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from onb_crypto import (
    AadhaarConsent,
    GrantAuditRecord,
    GrantIssuer,
    GrantRejected,
    GrantSigningKey,
    GrantVerifier,
    RejectReason,
    SinkPolicy,
    SinkResolver,
    SinkToken,
    Vault,
    VaultField,
    load_sink_policy,
)
from onb_crypto.sink import ACCESS_LOG_COLLECTION, JTI_COLLECTION
from onb_data import tenant_scope

from .fakes import ORG_A, ORG_B, in_org
from .grant_fakes import EMAIL_SINK, HIRE_1, HIRE_2, NOW, RUN, TASK, TELEGRAM, claims, sign
from .test_sink_policy import POLICY_FILE
from .vault_fakes import FakeVaultStore, keyring_for_two_orgs

HR_USER = "user_hr_01"
MOBILE_1 = "9000000001"
MOBILE_2 = "9000000002"
EMAIL_1 = "priya.test@example.com"
AADHAAR = "999900001231"  # synthetic, 9999-prefixed
POLICY = load_sink_policy(POLICY_FILE)


class RecordingAudit:
    def __init__(self) -> None:
        self.records: list[GrantAuditRecord] = []
        self.fail = False

    async def record(self, record: GrantAuditRecord) -> None:
        if self.fail:
            raise RuntimeError("the audit store is down")
        self.records = [*self.records, record]


class World:
    """A vault for two orgs with two hires, the orchestrator's key, and a sink resolver."""

    def __init__(self, policy: SinkPolicy = POLICY, *, now_offset: timedelta = timedelta(0)) -> None:
        self.store = FakeVaultStore()
        self.vault = Vault(keyring_for_two_orgs(), self.store, clock=lambda: NOW)
        self.key = GrantSigningKey.generate()
        self.policy = policy
        self.issuer = GrantIssuer(self.key, policy, clock=lambda: NOW)
        self.audit = RecordingAudit()
        self.resolver = SinkResolver(
            self.vault,
            GrantVerifier([self.key.public_key], clock=lambda: NOW + now_offset),
            policy,
            self.store,
            self.audit,
            clock=lambda: NOW + now_offset,
        )

    def put(self, subject: str, field: VaultField, value: str, org: str = ORG_A) -> str:
        consent = AadhaarConsent(purpose="employment onboarding KYC") if field is VaultField.AADHAAR else None
        return in_org(
            org,
            self.vault.put(org, subject_id=subject, field=field, value=value, created_by=HR_USER, consent=consent),
        )

    def grant(self, org: str = ORG_A, **kw: Any) -> str:
        args: dict[str, Any] = {
            "run_id": RUN,
            "task_id": TASK,
            "subject_id": HIRE_1,
            "fields": [VaultField.MOBILE],
            "sink": TELEGRAM,
            **kw,
        }
        with tenant_scope(org):
            return self.issuer.issue_grant(org, **args)

    def resolve(self, grant: str, sink: str, tokens: Sequence[SinkToken], org: str = ORG_A) -> dict[str, str]:
        return in_org(org, self.resolver.resolve_at_sink(grant, sink, tokens))

    def access_log(self) -> list[dict[str, Any]]:
        return list(self.store.tenant(ACCESS_LOG_COLLECTION).documents)

    def jtis(self) -> list[dict[str, Any]]:
        return list(self.store.tenant(JTI_COLLECTION).documents)

    def rejection(self, grant: str, sink: str, tokens: Sequence[SinkToken], org: str = ORG_A) -> RejectReason:
        """Resolve, expect a rejection, and check it released and logged nothing but one audit record."""
        before = len(self.audit.records)
        with pytest.raises(GrantRejected) as caught:
            self.resolve(grant, sink, tokens, org)
        assert self.access_log() == []
        assert len(self.audit.records) == before + 1
        audit = self.audit.records[-1]
        assert audit.reason is caught.value.reason
        assert audit.outcome == "rejected"
        assert audit.sink == sink
        return caught.value.reason

    def rejection_after_success(
        self, grant: str, sink: str, tokens: Sequence[SinkToken], org: str = ORG_A
    ) -> RejectReason:
        """Like `rejection`, when an earlier success already wrote access-log entries."""
        logged = self.access_log()
        with pytest.raises(GrantRejected) as caught:
            self.resolve(grant, sink, tokens, org)
        assert self.access_log() == logged
        assert self.audit.records[-1].reason is caught.value.reason
        return caught.value.reason


def mobile(token: str) -> SinkToken:
    return SinkToken(VaultField.MOBILE, token)


# --- the happy path, box 2 --------------------------------------------------------------------------


def test_a_valid_grant_resolves_the_hires_mobile_at_the_telegram_sink() -> None:
    world = World()
    token = world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    assert world.resolve(world.grant(), TELEGRAM, [mobile(token)]) == {token: MOBILE_1}
    assert world.audit.records == []


def test_a_successful_resolution_writes_exactly_one_access_log_entry_holding_a_masked_value_only() -> None:
    world = World()
    token = world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    grant = world.grant()
    world.resolve(grant, TELEGRAM, [mobile(token)])
    entries = world.access_log()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["masked"] == "90XXXXXX01"
    assert MOBILE_1 not in json.dumps(entry, default=str)
    assert MOBILE_1[2:-2] not in json.dumps(entry, default=str)
    assert {k: entry[k] for k in ("org_id", "kind", "run_id", "task_id", "field", "token", "sink")} == {
        "org_id": ORG_A,
        "kind": "resolve",
        "run_id": RUN,
        "task_id": TASK,
        "field": "mobile",
        "token": token,
        "sink": TELEGRAM,
    }
    assert entry["subject"] == {"type": "new_hire", "id": HIRE_1}
    assert entry["actor"] == {"type": "service", "id": TELEGRAM}
    assert entry["at"] == NOW
    assert set(entry) - {"_id"} == {
        "org_id",
        "kind",
        "actor",
        "sink",
        "run_id",
        "task_id",
        "jti",
        "subject",
        "field",
        "token",
        "masked",
        "at",
    }


def test_an_email_resolves_at_the_email_sink_with_its_mask() -> None:
    world = World()
    token = world.put(HIRE_1, VaultField.EMAIL, EMAIL_1)
    grant = world.grant(fields=[VaultField.EMAIL], sink=EMAIL_SINK)
    assert world.resolve(grant, EMAIL_SINK, [SinkToken(VaultField.EMAIL, token)]) == {token: EMAIL_1}
    [entry] = world.access_log()
    assert entry["masked"] == "p***@example.com"
    assert EMAIL_1 not in json.dumps(entry, default=str)


def test_two_granted_tokens_resolve_together_with_one_entry_each(tmp_path: Path) -> None:
    path = tmp_path / "p.yaml"
    path.write_text("version: 1\nsinks:\n  notify.both: [mobile, email]\n")
    world = World(load_sink_policy(path))
    m = world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    e = world.put(HIRE_1, VaultField.EMAIL, EMAIL_1)
    grant = world.grant(fields=[VaultField.MOBILE, VaultField.EMAIL], sink="notify.both")
    resolved = world.resolve(grant, "notify.both", [mobile(m), SinkToken(VaultField.EMAIL, e)])
    assert resolved == {m: MOBILE_1, e: EMAIL_1}
    assert sorted(entry["field"] for entry in world.access_log()) == ["email", "mobile"]


def test_the_jti_is_kept_until_the_grant_expires() -> None:
    world = World()
    token = world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    grant = world.grant()
    world.resolve(grant, TELEGRAM, [mobile(token)])
    [kept] = world.jtis()
    assert kept["expires_at"] == NOW + timedelta(minutes=5)
    assert kept["org_id"] == ORG_A
    assert set(kept) - {"_id"} == {"org_id", "jti", "run_id", "task_id", "sink", "expires_at", "used_at"}


# --- box 1: each rejection has a named test ---------------------------------------------------------


def test_rejects_a_bad_signature() -> None:
    world = World()
    token = world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    forged = sign(GrantSigningKey.generate(), claims(ORG_A))
    assert world.rejection(forged, TELEGRAM, [mobile(token)]) is RejectReason.BAD_SIGNATURE
    assert world.jtis() == []


def test_rejects_an_expired_grant() -> None:
    world = World(now_offset=timedelta(minutes=6))
    token = world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    assert world.rejection(world.grant(), TELEGRAM, [mobile(token)]) is RejectReason.EXPIRED
    assert world.jtis() == []


def test_rejects_the_wrong_sink() -> None:
    world = World()
    token = world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    grant = world.grant()  # for notify.telegram
    assert world.rejection(grant, EMAIL_SINK, [mobile(token)]) is RejectReason.WRONG_SINK
    assert world.jtis() == []


def test_rejects_another_hires_token() -> None:
    world = World()
    world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    other = world.put(HIRE_2, VaultField.MOBILE, MOBILE_2)
    grant = world.grant()  # for HIRE_1
    assert world.rejection(grant, TELEGRAM, [mobile(other)]) is RejectReason.NOT_BOUND


def test_rejects_another_hires_token_mixed_in_with_the_right_one() -> None:
    world = World()
    own = world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    other = world.put(HIRE_2, VaultField.MOBILE, MOBILE_2)
    grant = world.grant()
    assert world.rejection(grant, TELEGRAM, [mobile(own), mobile(other)]) is RejectReason.NOT_BOUND


def test_rejects_another_orgs_token() -> None:
    world = World()
    foreign = world.put(HIRE_1, VaultField.MOBILE, MOBILE_1, org=ORG_B)  # same hire id, other org
    grant = world.grant()  # ORG_A
    assert world.rejection(grant, TELEGRAM, [mobile(foreign)]) is RejectReason.NOT_FOUND


def test_rejects_another_orgs_grant() -> None:
    world = World()
    token = world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    grant_b = world.grant(org=ORG_B)
    assert world.rejection(grant_b, TELEGRAM, [mobile(token)]) is RejectReason.WRONG_ORG
    assert world.jtis() == []


def test_rejects_a_field_not_granted(tmp_path: Path) -> None:
    path = tmp_path / "p.yaml"
    path.write_text("version: 1\nsinks:\n  notify.both: [mobile, email]\n")
    world = World(load_sink_policy(path))
    world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    email = world.put(HIRE_1, VaultField.EMAIL, EMAIL_1)
    grant = world.grant(fields=[VaultField.MOBILE], sink="notify.both")  # the policy would allow email
    reason = world.rejection(grant, "notify.both", [SinkToken(VaultField.EMAIL, email)])
    assert reason is RejectReason.FIELD_NOT_GRANTED
    assert world.jtis() == []


def test_rejects_a_field_not_granted_at_the_shipped_telegram_sink() -> None:
    world = World()
    email = world.put(HIRE_1, VaultField.EMAIL, EMAIL_1)
    reason = world.rejection(world.grant(), TELEGRAM, [SinkToken(VaultField.EMAIL, email)])
    assert reason is RejectReason.FIELD_NOT_GRANTED


def test_rejects_aadhaar_even_when_the_grant_lists_it() -> None:
    world = World()
    aadhaar = world.put(HIRE_1, VaultField.AADHAAR, AADHAAR)
    # The issuer refuses such a grant, so it is signed with the orchestrator's key directly: a
    # compromised or buggy orchestrator still cannot get Aadhaar out of a sink.
    grant = sign(world.key, claims(ORG_A, fields=["mobile", "aadhaar"]))
    reason = world.rejection(grant, TELEGRAM, [SinkToken(VaultField.AADHAAR, aadhaar)])
    assert reason is RejectReason.FIELD_FORBIDDEN_BY_POLICY
    assert world.jtis() == []


@pytest.mark.parametrize("field", [VaultField.PAN, VaultField.BANK_ACCOUNT, VaultField.IFSC])
def test_rejects_pan_and_bank_fields_even_when_the_grant_lists_them(field: VaultField) -> None:
    world = World()
    token = world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    grant = sign(world.key, claims(ORG_A, fields=[field.value]))
    reason = world.rejection(grant, TELEGRAM, [SinkToken(field, token)])
    assert reason is RejectReason.FIELD_FORBIDDEN_BY_POLICY


def test_rejects_a_replayed_jti() -> None:
    world = World()
    token = world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    grant = world.grant()
    assert world.resolve(grant, TELEGRAM, [mobile(token)]) == {token: MOBILE_1}
    log_after_first = world.access_log()
    with pytest.raises(GrantRejected) as caught:
        world.resolve(grant, TELEGRAM, [mobile(token)])
    assert caught.value.reason is RejectReason.REPLAYED
    assert world.access_log() == log_after_first
    assert [r.reason for r in world.audit.records] == [RejectReason.REPLAYED]


def test_rejects_a_replayed_jti_even_after_a_failed_first_use() -> None:
    world = World()
    world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    other = world.put(HIRE_2, VaultField.MOBILE, MOBILE_2)
    grant = world.grant()
    assert world.rejection(grant, TELEGRAM, [mobile(other)]) is RejectReason.NOT_BOUND
    own = world.put(HIRE_1, VaultField.MOBILE, "9000000003")
    assert world.rejection(grant, TELEGRAM, [mobile(own)]) is RejectReason.REPLAYED


# --- the vault's own refusals surface as rejections ---------------------------------------------------


def test_rejects_a_superseded_token() -> None:
    world = World()
    old = world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    world.put(HIRE_1, VaultField.MOBILE, "9000000003")
    assert world.rejection(world.grant(), TELEGRAM, [mobile(old)]) is RejectReason.SUPERSEDED


def test_rejects_a_purged_token() -> None:
    world = World()
    token = world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    in_org(ORG_A, world.vault.purge(token, org_id=ORG_A, subject_id=HIRE_1, field=VaultField.MOBILE))
    assert world.rejection(world.grant(), TELEGRAM, [mobile(token)]) is RejectReason.PURGED


def test_rejects_a_tampered_vault_record() -> None:
    world = World()
    token = world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    world.store.tenant("pii_vault").raw(token)["subject"]["id"] = HIRE_2
    reason = world.rejection(world.grant(subject_id=HIRE_2), TELEGRAM, [mobile(token)])
    assert reason is RejectReason.RECORD_INTEGRITY


def test_rejects_a_token_the_vault_did_not_issue() -> None:
    world = World()
    reason = world.rejection(world.grant(), TELEGRAM, [mobile("ptk_" + "0" * 52)])
    assert reason is RejectReason.NOT_FOUND


@pytest.mark.parametrize("tokens", [[], ["ptk_x"], [("mobile", "ptk_x")]])
def test_rejects_a_malformed_token_list(tokens: Any) -> None:
    world = World()
    assert world.rejection(world.grant(), TELEGRAM, tokens) is RejectReason.MALFORMED


def test_rejects_too_many_tokens() -> None:
    world = World()
    token = world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    assert world.rejection(world.grant(), TELEGRAM, [mobile(token)] * 33) is RejectReason.MALFORMED


def test_nothing_is_released_when_the_access_log_cannot_be_written() -> None:
    world = World()
    token = world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    world.store.tenant(ACCESS_LOG_COLLECTION).fail_next_insert = True
    with pytest.raises(RuntimeError, match="refused the write"):
        world.resolve(world.grant(), TELEGRAM, [mobile(token)])
    assert world.access_log() == []


def test_a_failing_audit_store_does_not_turn_a_rejection_into_a_release() -> None:
    world = World()
    world.audit.fail = True
    other = world.put(HIRE_2, VaultField.MOBILE, MOBILE_2)
    with pytest.raises(GrantRejected) as caught:
        world.resolve(world.grant(), TELEGRAM, [mobile(other)])
    assert caught.value.reason is RejectReason.NOT_BOUND
    assert world.access_log() == []


# --- the audit record -------------------------------------------------------------------------------


def test_the_audit_record_names_the_verified_grant_but_holds_no_value() -> None:
    world = World()
    world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    other = world.put(HIRE_2, VaultField.MOBILE, MOBILE_2)
    world.rejection(world.grant(), TELEGRAM, [mobile(other)])
    [record] = world.audit.records
    assert record.org_id == ORG_A
    assert record.action == "pii.grant.resolve"
    assert (record.run_id, record.task_id, record.subject_id) == (RUN, TASK, HIRE_1)
    assert record.jti is not None
    assert record.tokens == (other,)
    assert record.at == NOW
    assert MOBILE_2 not in repr(record)
    assert MOBILE_1 not in repr(record)


def test_the_audit_record_of_an_unverified_grant_carries_none_of_its_claims() -> None:
    world = World()
    token = world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    forged = sign(GrantSigningKey.generate(), claims(ORG_A, run_id="run_forged"))
    world.rejection(forged, TELEGRAM, [mobile(token)])
    [record] = world.audit.records
    assert (record.run_id, record.task_id, record.subject_id, record.jti) == (None, None, None, None)
    assert record.org_id == ORG_A


def test_the_audit_record_to_document_matches_the_audit_log_shape() -> None:
    world = World()
    other = world.put(HIRE_2, VaultField.MOBILE, MOBILE_2)
    world.rejection(world.grant(), TELEGRAM, [mobile(other)])
    document = world.audit.records[0].to_document()
    assert document["actor"] == {"type": "service", "id": TELEGRAM}
    assert document["action"] == "pii.grant.resolve"
    assert document["outcome"] == "rejected"
    assert document["reason"] == "token_not_bound"
    assert document["target"]["type"] == "pii_grant"
    assert document["target"]["run_id"] == RUN
    assert MOBILE_2 not in json.dumps(document, default=str)


def test_create_builds_a_resolver_without_the_caller_holding_a_vault() -> None:
    world = World()
    token = world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    resolver = SinkResolver.create(
        world.vault.keyring,
        world.store,
        GrantVerifier([world.key.public_key], clock=lambda: NOW),
        POLICY,
        world.audit,
        clock=lambda: NOW,
    )
    assert in_org(ORG_A, resolver.resolve_at_sink(world.grant(), TELEGRAM, [mobile(token)])) == {token: MOBILE_1}
    assert len(world.access_log()) == 1


def test_rejects_a_concurrent_replay_through_the_unique_jti_index(monkeypatch: pytest.MonkeyPatch) -> None:
    world = World()
    token = world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    grant = world.grant()
    world.resolve(grant, TELEGRAM, [mobile(token)])
    jti_repo = world.store.tenant(JTI_COLLECTION)

    async def nothing_seen(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return []  # the second use read before the first one's insert landed

    monkeypatch.setattr(jti_repo, "find", nothing_seen)
    assert world.rejection_after_success(grant, TELEGRAM, [mobile(token)]) is RejectReason.REPLAYED


def test_a_keyring_failure_is_a_vault_error_rejection() -> None:
    world = World()
    token = world.put(HIRE_1, VaultField.MOBILE, MOBILE_1)
    world.store.tenant("pii_vault").raw(token)["key_version"] = 99
    reason = world.rejection(world.grant(), TELEGRAM, [mobile(token)])
    assert reason in {RejectReason.RECORD_INTEGRITY, RejectReason.VAULT_ERROR}
