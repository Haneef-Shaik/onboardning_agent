"""PII retention purge (PII-5; PRD FR-P6, §7.10 Retention): vault values go 90 days after the onboarding run
completes, the record stays as `purged`, the consent/purpose record stays, every purge is audited, and
running the job again changes nothing. A fake clock drives every test: nothing waits."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from onb_agents.runs import RUNS
from onb_api.retention import RETENTION_PERIOD, PiiRetentionJob, run_forever
from onb_api.testing import InMemoryAuditLog
from onb_api.testing_mongo import MemoryDatabase, memory_keyring, memory_store
from onb_crypto import (
    CONSENT_COLLECTION,
    PURGED,
    AadhaarConsent,
    Keyring,
    Vault,
    VaultField,
    VaultIntegrityError,
)
from onb_data import AuditEntry, AuditOutcome, AuditQuery, DataStore, tenant_scope

ORG = "org_acme"
OTHER_ORG = "org_globex"
HIRE = "h_0001"
COMPLETED_AT = datetime(2026, 1, 5, 9, 30, tzinfo=UTC)
MOBILE = "9000000001"
AADHAAR = "999900001231"
PURPOSE = "employment onboarding KYC"


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def set_day(self, days: int, *, seconds: int = 0) -> None:
        self.now = COMPLETED_AT + timedelta(days=days, seconds=seconds)


class FlakyAudit:
    """The audit log, failing its next write when told to (a crash between the vault purge and the audit)."""

    def __init__(self, inner: InMemoryAuditLog) -> None:
        self.inner = inner
        self.fail_next = False
        self.allow_before_failing: int | None = None

    async def audit(self, **kwargs: Any) -> str:
        if self.allow_before_failing is not None:
            if self.allow_before_failing == 0:
                self.allow_before_failing = None
                raise RuntimeError("audit write failed")
            self.allow_before_failing -= 1
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("audit write failed")
        return await self.inner.audit(**kwargs)

    async def list(self, query: AuditQuery, *, page: int, limit: int) -> Any:
        return await self.inner.list(query, page=page, limit=limit)


class Env:
    """An in-memory platform with a hire, its vault values and a run; the job under test on a fake clock."""

    def __init__(self) -> None:
        self.db = MemoryDatabase()
        self.store: DataStore = memory_store(self.db)
        self.keyring: Keyring = memory_keyring(self.store)
        self.clock = FakeClock(COMPLETED_AT)
        self.vault = Vault(self.keyring, self.store, clock=self.clock)
        self.audit = InMemoryAuditLog()
        self.flaky = FlakyAudit(self.audit)
        self.job = PiiRetentionJob(self.store, self.vault, self.flaky, clock=self.clock)

    def run(self, coro: Any) -> Any:
        return asyncio.run(coro)

    def in_org(self, org_id: str, coro: Any) -> Any:
        async def scoped() -> Any:
            with tenant_scope(org_id):
                return await coro

        return self.run(scoped())

    def add_org(self, org_id: str) -> None:
        self.run(self.store.shared("orgs").insert_one({"_id": org_id}))
        self.in_org(org_id, self.keyring.create(org_id))

    def add_hire(self, org_id: str = ORG, hire_id: str = HIRE) -> dict[str, str]:
        """A hire with a mobile (updated once, so a superseded version exists) and an Aadhaar with consent."""

        async def make() -> dict[str, str]:
            old = await self.vault.put(
                org_id, subject_id=hire_id, field=VaultField.MOBILE, value="9000000000", created_by="u_hr"
            )
            mobile = await self.vault.put(
                org_id, subject_id=hire_id, field=VaultField.MOBILE, value=MOBILE, created_by="u_hr"
            )
            aadhaar = await self.vault.put(
                org_id,
                subject_id=hire_id,
                field=VaultField.AADHAAR,
                value=AADHAAR,
                created_by="u_hr",
                consent=AadhaarConsent(purpose=PURPOSE),
            )
            await self.store.tenant("new_hires").insert_one(
                {
                    "_id": hire_id,
                    "name": "Test Hire",
                    "pii": {"mobile": mobile, "aadhaar": aadhaar},
                    "pii_masked": {"mobile": "******0001", "aadhaar": "XXXX XXXX 1231"},
                    "pii_versions": {"mobile": 2, "aadhaar": 1},
                    "mobile_hash": "hash_m",
                    "aadhaar_hash": "hash_a",
                    "email_hash": "hash_e",
                }
            )
            return {"old_mobile": old, "mobile": mobile, "aadhaar": aadhaar}

        tokens: dict[str, str] = self.in_org(org_id, make())
        return tokens

    def add_run(
        self,
        status: str = "completed",
        *,
        org_id: str = ORG,
        hire_id: str = HIRE,
        run_id: str = "run_1",
        completed_at: datetime | None = COMPLETED_AT,
        updated_at: datetime | None = None,
    ) -> None:
        history = [{"from": "started", "to": "running", "at": COMPLETED_AT - timedelta(days=3), "by": None}]
        if status == "completed" and completed_at is not None:
            history.append({"from": "running", "to": "completed", "at": completed_at, "by": None})
        document = {
            "_id": run_id,
            "hire_id": hire_id,
            "status": status,
            "dag_hash": "d" * 64,
            "history": history,
            "updated_at": updated_at or completed_at or COMPLETED_AT,
        }
        self.in_org(org_id, self.store.tenant(RUNS).insert_one(document))

    def resolve(self, token: str, field: VaultField, *, org_id: str = ORG, hire_id: str = HIRE) -> str:
        value: str = self.in_org(
            org_id, self.vault.resolve(token, org_id=org_id, subject_id=hire_id, field=field, allow_superseded=True)
        )
        return value

    def purge_audits(self, org_id: str = ORG, *, outcome: AuditOutcome | None = None) -> list[AuditEntry]:
        query = AuditQuery(action="pii.purge", outcome=outcome)
        return list(self.in_org(org_id, self.audit.list(query, page=1, limit=200)).entries)

    def hire(self, org_id: str = ORG, hire_id: str = HIRE) -> dict[str, Any]:
        found: dict[str, Any] = next(d for d in self.db.raw("new_hires") if d["_id"] == hire_id)
        return found


@pytest.fixture
def env() -> Env:
    e = Env()
    e.add_org(ORG)
    return e


# --- box 1: day 89 resolves, day 90 purges ------------------------------------------------------


def test_the_retention_period_is_ninety_days() -> None:
    assert timedelta(days=90) == RETENTION_PERIOD


def test_on_day_89_values_resolve_and_nothing_is_purged(env: Env) -> None:
    tokens = env.add_hire()
    env.add_run()
    env.clock.set_day(89, seconds=23 * 3600)
    report = env.run(env.job.run_once())
    assert report.hires_purged == 0
    assert env.resolve(tokens["mobile"], VaultField.MOBILE) == MOBILE
    assert env.resolve(tokens["aadhaar"], VaultField.AADHAAR) == AADHAAR
    assert env.purge_audits() == []


def test_on_day_90_the_job_purges_and_tokens_resolve_to_purged(env: Env) -> None:
    tokens = env.add_hire()
    env.add_run()
    env.clock.set_day(90)
    report = env.run(env.job.run_once())
    assert report.hires_purged == 1
    assert report.values_purged == 3
    for token, field in (
        (tokens["mobile"], VaultField.MOBILE),
        (tokens["old_mobile"], VaultField.MOBILE),
        (tokens["aadhaar"], VaultField.AADHAAR),
    ):
        assert env.resolve(token, field) == PURGED


def test_the_same_clock_flips_from_resolving_to_purged_between_day_89_and_day_90(env: Env) -> None:
    tokens = env.add_hire()
    env.add_run()
    env.clock.set_day(89, seconds=24 * 3600 - 1)
    env.run(env.job.run_once())
    assert env.resolve(tokens["mobile"], VaultField.MOBILE) == MOBILE
    env.clock.set_day(90)
    env.run(env.job.run_once())
    assert env.resolve(tokens["mobile"], VaultField.MOBILE) == PURGED


def test_a_purged_record_keeps_no_ciphertext_and_is_marked_purged(env: Env) -> None:
    env.add_hire()
    env.add_run()
    env.clock.set_day(90)
    env.run(env.job.run_once())
    for collection in ("pii_vault", "pii_vault_aadhaar"):
        documents = env.db.raw(collection)
        assert documents
        assert all(d["status"] == "purged" and d["ciphertext"] == b"" and d["nonce"] == b"" for d in documents)


def test_the_hire_keeps_its_tokens_but_loses_masks_blind_indexes_and_versions(env: Env) -> None:
    tokens = env.add_hire()
    env.add_run()
    env.clock.set_day(90)
    env.run(env.job.run_once())
    hire = env.hire()
    assert hire["pii"] == {"mobile": tokens["mobile"], "aadhaar": tokens["aadhaar"]}
    assert hire["pii_masked"] == {"mobile": PURGED, "aadhaar": PURGED}
    for gone in ("pii_versions", "mobile_hash", "aadhaar_hash", "email_hash"):
        assert gone not in hire
    assert hire["pii_purged_at"] == env.clock.now


def test_the_clock_starts_at_the_completion_not_at_creation_or_last_update(env: Env) -> None:
    tokens = env.add_hire()
    env.add_run(updated_at=COMPLETED_AT + timedelta(days=60))
    env.clock.set_day(90)
    env.run(env.job.run_once())
    assert env.resolve(tokens["mobile"], VaultField.MOBILE) == PURGED


@pytest.mark.parametrize("status", ["started", "running", "paused", "blocked", "failed", "cancelled"])
def test_a_run_that_did_not_complete_never_starts_the_clock(env: Env, status: str) -> None:
    tokens = env.add_hire()
    env.add_run(status)
    env.clock.set_day(400)
    report = env.run(env.job.run_once())
    assert report.hires_purged == 0
    assert env.resolve(tokens["mobile"], VaultField.MOBILE) == MOBILE


def test_a_hire_with_another_run_still_active_is_not_purged(env: Env) -> None:
    tokens = env.add_hire()
    env.add_run()
    env.add_run("running", run_id="run_2")
    env.clock.set_day(120)
    assert env.run(env.job.run_once()).hires_purged == 0
    assert env.resolve(tokens["mobile"], VaultField.MOBILE) == MOBILE


def test_a_second_completed_run_restarts_the_clock_from_the_latest(env: Env) -> None:
    tokens = env.add_hire()
    env.add_run()
    env.add_run(run_id="run_2", completed_at=COMPLETED_AT + timedelta(days=30))
    env.clock.set_day(90)
    assert env.run(env.job.run_once()).hires_purged == 0
    env.clock.set_day(120)
    assert env.run(env.job.run_once()).hires_purged == 1
    assert env.resolve(tokens["mobile"], VaultField.MOBILE) == PURGED


def test_a_completed_run_without_a_history_entry_falls_back_to_updated_at(env: Env) -> None:
    tokens = env.add_hire()
    env.add_run(completed_at=None, updated_at=COMPLETED_AT)
    env.clock.set_day(90)
    # `status` says completed but no history entry exists: the run is read as completed at `updated_at`.
    env.in_org(
        ORG, env.store.tenant(RUNS).update_one({"_id": "run_1"}, {"$set": {"status": "completed", "history": []}})
    )
    env.run(env.job.run_once())
    assert env.resolve(tokens["mobile"], VaultField.MOBILE) == PURGED


def test_a_hire_of_another_org_is_purged_by_its_own_clock_only(env: Env) -> None:
    env.add_org(OTHER_ORG)
    mine = env.add_hire()
    theirs = env.add_hire(OTHER_ORG, "h_0002")
    env.add_run()
    env.add_run(org_id=OTHER_ORG, hire_id="h_0002", run_id="run_other", completed_at=COMPLETED_AT + timedelta(days=20))
    env.clock.set_day(90)
    env.run(env.job.run_once())
    assert env.resolve(mine["mobile"], VaultField.MOBILE) == PURGED
    assert env.resolve(theirs["mobile"], VaultField.MOBILE, org_id=OTHER_ORG, hire_id="h_0002") == MOBILE


def test_every_org_with_a_due_hire_is_purged_in_one_pass(env: Env) -> None:
    env.add_org(OTHER_ORG)
    mine = env.add_hire()
    theirs = env.add_hire(OTHER_ORG, "h_0002")
    env.add_run()
    env.add_run(org_id=OTHER_ORG, hire_id="h_0002", run_id="run_other")
    env.clock.set_day(90)
    report = env.run(env.job.run_once())
    assert report.hires_purged == 2
    assert env.resolve(mine["mobile"], VaultField.MOBILE) == PURGED
    assert env.resolve(theirs["mobile"], VaultField.MOBILE, org_id=OTHER_ORG, hire_id="h_0002") == PURGED
    assert len(env.purge_audits(OTHER_ORG, outcome=AuditOutcome.SUCCESS)) == 3


def test_a_run_whose_hire_has_no_document_or_values_is_skipped_quietly(env: Env) -> None:
    env.add_run(hire_id="h_gone")
    env.clock.set_day(90)
    report = env.run(env.job.run_once())
    assert (report.hires_purged, report.values_purged, report.failures) == (0, 0, 0)


# --- box 2: consent survives --------------------------------------------------------------------


def test_the_consent_purpose_record_survives_the_purge(env: Env) -> None:
    env.add_hire()
    env.add_run()
    before = env.db.raw(CONSENT_COLLECTION)
    assert len(before) == 1
    assert before[0]["purpose"] == PURPOSE
    env.clock.set_day(90)
    env.run(env.job.run_once())
    env.clock.set_day(500)
    env.run(env.job.run_once())
    assert env.db.raw(CONSENT_COLLECTION) == before


# --- box 3: audited, idempotent -----------------------------------------------------------------


def test_each_purged_value_is_audited_with_its_token_and_no_value(env: Env) -> None:
    tokens = env.add_hire()
    env.add_run()
    env.clock.set_day(90)
    env.run(env.job.run_once())
    entries = env.purge_audits()
    assert sorted(e.target for e in entries) == sorted(f"pii:{t}" for t in tokens.values())
    assert all(e.outcome is AuditOutcome.SUCCESS for e in entries)
    assert all(e.action == "pii.purge" for e in entries)
    assert {(e.actor.type.value, e.actor.id) for e in entries} == {("agent", "job:pii_retention")}
    text = " ".join(f"{e.target} {e.reason}" for e in entries)
    assert MOBILE not in text
    assert AADHAAR not in text
    assert "retention" in text


def test_running_the_job_again_changes_nothing_and_audits_nothing_new(env: Env) -> None:
    env.add_hire()
    env.add_run()
    env.clock.set_day(90)
    env.run(env.job.run_once())
    vault_after_first = (env.db.raw("pii_vault"), env.db.raw("pii_vault_aadhaar"))
    hire_after_first = env.hire()
    audits_after_first = len(env.purge_audits(outcome=AuditOutcome.SUCCESS))
    env.clock.set_day(91)
    second = env.run(env.job.run_once())
    assert (second.hires_purged, second.values_purged, second.failures) == (0, 0, 0)
    assert (env.db.raw("pii_vault"), env.db.raw("pii_vault_aadhaar")) == vault_after_first
    assert env.hire() == hire_after_first
    assert len(env.purge_audits(outcome=AuditOutcome.SUCCESS)) == audits_after_first == 3


def test_a_crash_after_the_vault_purge_is_finished_and_audited_once_by_the_next_run(env: Env) -> None:
    env.add_hire()
    env.add_run()
    env.clock.set_day(90)
    env.flaky.fail_next = True
    first = env.run(env.job.run_once())
    assert first.failures == 1
    assert first.hires_purged == 0
    env.clock.set_day(91)
    second = env.run(env.job.run_once())
    assert second.hires_purged == 1
    assert len(env.purge_audits(outcome=AuditOutcome.SUCCESS)) == 3
    assert "pii_purged_at" in env.hire()
    third = env.run(env.job.run_once())
    assert third.hires_purged == 0
    assert len(env.purge_audits(outcome=AuditOutcome.SUCCESS)) == 3


def test_a_crash_between_two_audit_writes_does_not_audit_the_first_value_twice(env: Env) -> None:
    env.add_hire()
    env.add_run()
    env.clock.set_day(90)
    env.flaky.allow_before_failing = 1
    assert env.run(env.job.run_once()).failures == 1
    assert len(env.purge_audits(outcome=AuditOutcome.SUCCESS)) == 1
    env.clock.set_day(91)
    assert env.run(env.job.run_once()).hires_purged == 1
    targets = [e.target for e in env.purge_audits(outcome=AuditOutcome.SUCCESS)]
    assert len(targets) == len(set(targets)) == 3


def test_a_tampered_record_fails_that_hire_audits_a_failure_and_the_others_go_on(env: Env) -> None:
    env.add_hire(hire_id="h_bad")
    env.add_hire(hire_id="h_ok")
    env.add_run(hire_id="h_bad", run_id="run_bad")
    env.add_run(hire_id="h_ok", run_id="run_ok")
    documents = env.db.collections["pii_vault"].documents
    next(d for d in documents if d["subject"]["id"] == "h_bad")["field"] = "pan"
    env.clock.set_day(90)
    report = env.run(env.job.run_once())
    assert report.failures == 1
    assert report.hires_purged == 1
    assert "pii_purged_at" not in env.hire(hire_id="h_bad")
    assert "pii_purged_at" in env.hire(hire_id="h_ok")
    failed = [e for e in env.purge_audits() if e.outcome is AuditOutcome.FAILED]
    assert [e.target for e in failed] == ["hire:h_bad"]
    assert VaultIntegrityError.__name__ in (failed[0].reason or "")


# --- the scheduled loop -------------------------------------------------------------------------


def test_the_loop_runs_the_job_each_interval_on_the_fake_clock_and_stops_on_request(env: Env) -> None:
    env.add_hire()
    env.add_run()
    env.clock.set_day(89)
    sleeps: list[float] = []
    stop = asyncio.Event()

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        env.clock.now += timedelta(seconds=seconds)
        if len(sleeps) == 30:
            stop.set()

    reports = env.run(run_forever(env.job, interval=86_400.0, sleep=fake_sleep, stop=stop))
    assert sleeps[:2] == [86_400.0, 86_400.0]
    assert [r.hires_purged for r in reports].count(1) == 1
    assert sum(r.hires_purged for r in reports) == 1


def test_the_loop_survives_a_failing_pass(env: Env) -> None:
    calls = 0
    stop = asyncio.Event()

    class Flaky:
        async def run_once(self) -> Any:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("database went away")
            if calls == 3:
                stop.set()
            return None

    async def fake_sleep(_: float) -> None:
        return None

    env.run(run_forever(Flaky(), interval=1.0, sleep=fake_sleep, stop=stop))
    assert calls == 3
