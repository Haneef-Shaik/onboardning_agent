"""The PII retention purge (PRD FR-P6, §7.10 Retention): vault values go 90 days after onboarding completes.

For every org, a hire is due when its newest completed run finished at least `RETENTION_PERIOD` ago and it
has no run still moving. The job then purges every vault record of the hire (`Vault.purge_subject`: the
ciphertext goes, the signed record stays as `purged`, superseded versions included), writes one `pii.purge`
audit record per value, and finally clears what else of the values the hire document keeps: masks become
`[purged]`, the blind indexes and stored versions are removed. The consent/purpose record is never touched.

Order and idempotency. The vault is purged first, then each value is audited (unless its success record
exists already), then the hire is marked `pii_purged_at`. A crash anywhere leaves the hire unmarked, so the
next pass finishes the work and audits every value exactly once; a marked hire is skipped, so a repeat pass
changes and audits nothing. One hire's failure (a tampered record, a failed audit write) is audited as
`failed` where possible, counted, and does not stop the others.

The job runs as its own process (`python -m onb_api.retention`) with no HTTP surface, so it takes no input
from a caller. The orgs come from the `orgs` collection and the tenant scope is set per org.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol

from onb_agents.runs import RUNS, RunState
from onb_crypto import PURGED, BlindIndexField, PurgeIncompleteError, PurgeOutcome, Vault
from onb_data import Actor, AuditOutcome, AuditPage, AuditQuery, DataStore, tenant_scope

from onb_api.hires.store import HIRES_COLLECTION

__all__ = ["ACTION", "ACTOR_ID", "RETENTION_PERIOD", "PiiRetentionJob", "PurgeReport", "completed_at"]

log = logging.getLogger(__name__)

RETENTION_PERIOD: Final = timedelta(days=90)
ACTION: Final = "pii.purge"
ACTOR_ID: Final = "job:pii_retention"
_REASON: Final = "retention: 90 days after the onboarding run completed"
_PURGED_AT: Final = "pii_purged_at"
_ACTIVE: Final = [state.value for state in RunState if state.active]
_BLIND_INDEX_KEYS: Final = tuple(field.index_name for field in BlindIndexField)


class PurgeAuditor(Protocol):
    """An audit log the job can write to and read back (`AuditLog`)."""

    async def audit(
        self, *, actor: Actor, action: str, target: str, outcome: AuditOutcome, reason: str | None = None
    ) -> str: ...

    async def list(self, query: AuditQuery, *, page: int, limit: int) -> AuditPage: ...


@dataclass(frozen=True, slots=True)
class PurgeReport:
    """What one pass did: hires finished, values newly purged, hires that failed (and will be retried)."""

    hires_purged: int = 0
    values_purged: int = 0
    failures: int = 0

    def plus(self, other: PurgeReport) -> PurgeReport:
        return PurgeReport(
            self.hires_purged + other.hires_purged,
            self.values_purged + other.values_purged,
            self.failures + other.failures,
        )


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def completed_at(run: Mapping[str, Any]) -> datetime | None:
    """When the run completed: its last move to `completed`, else its last update."""
    for entry in reversed(list(run.get("history") or ())):
        if isinstance(entry, Mapping) and entry.get("to") == RunState.COMPLETED.value:
            at = entry.get("at")
            if isinstance(at, datetime):
                return _aware(at)
    updated = run.get("updated_at")
    return _aware(updated) if isinstance(updated, datetime) else None


def _already_purged(hire: Mapping[str, Any], finished: datetime) -> bool:
    """Purged for this onboarding: marked at or after the newest run completed. A hire onboarded again
    (a newer completed run) is purged again once that run's 90 days are up."""
    marked = hire.get(_PURGED_AT)
    return isinstance(marked, datetime) and _aware(marked) >= finished


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PiiRetentionJob:
    def __init__(
        self,
        store: DataStore,
        vault: Vault,
        auditor: PurgeAuditor,
        *,
        clock: Callable[[], datetime] = _utc_now,
        retention: timedelta = RETENTION_PERIOD,
    ) -> None:
        self._store = store
        self._vault = vault
        self._auditor = auditor
        self._clock = clock
        self._retention = retention
        self._actor = Actor.agent(ACTOR_ID)

    async def run_once(self) -> PurgeReport:
        """One pass over every org; one org's failure is counted and does not skip the others."""
        report = PurgeReport()
        for org in await self._store.shared("orgs").find({}, projection={"_id": 1}):
            try:
                report = report.plus(await self.run_org(str(org["_id"])))
            except Exception as error:
                log.error("pii retention failed for an org: error=%s", type(error).__name__)
                report = report.plus(PurgeReport(failures=1))
        return report

    async def run_org(self, org_id: str) -> PurgeReport:
        """One pass over one org's due hires."""
        now = self._clock()
        report = PurgeReport()
        with tenant_scope(org_id):
            for hire_id, finished in await self._due_hires(now):
                report = report.plus(await self._purge_hire(org_id, hire_id, finished))
        return report

    async def _due_hires(self, now: datetime) -> list[tuple[str, datetime]]:
        """(hire id, when its newest run completed) for hires whose retention is up and that have no live run."""
        runs = await self._store.tenant(RUNS).find(
            {"status": RunState.COMPLETED.value, "dag_hash": {"$exists": True}},
            projection={"hire_id": 1, "history": 1, "updated_at": 1},
        )
        newest: dict[str, datetime] = {}
        for run in runs:
            finished = completed_at(run)
            hire_id = run.get("hire_id")
            if finished is not None and isinstance(hire_id, str):
                newest[hire_id] = max(finished, newest.get(hire_id, finished))
        due = [(hire_id, finished) for hire_id, finished in sorted(newest.items()) if now - finished >= self._retention]
        return [(hire_id, finished) for hire_id, finished in due if not await self._has_active_run(hire_id)]

    async def _has_active_run(self, hire_id: str) -> bool:
        return bool(await self._store.tenant(RUNS).count({"hire_id": hire_id, "status": {"$in": _ACTIVE}}))

    async def _purge_hire(self, org_id: str, hire_id: str, finished: datetime) -> PurgeReport:
        hire = await self._store.tenant(HIRES_COLLECTION).find_one({"_id": hire_id})
        if hire is None or _already_purged(hire, finished):
            return PurgeReport()
        outcomes: tuple[PurgeOutcome, ...] = ()
        try:
            try:
                outcomes = await self._vault.purge_subject(org_id, subject_id=hire_id)
            except PurgeIncompleteError as incomplete:
                outcomes = incomplete.outcomes  # what was purged is still audited, then the hire fails
                for outcome in outcomes:
                    await self._audit_value(outcome)
                raise
            for outcome in outcomes:
                await self._audit_value(outcome)
            await self._clear_hire(hire_id, hire)
        except Exception as error:  # one hire's failure must not stop the pass; the next pass retries it
            log.error("pii purge failed: org=%s hire=%s error=%s", org_id, hire_id, type(error).__name__)
            await self._audit_failure(hire_id, error)
            return PurgeReport(failures=1)
        newly = sum(1 for outcome in outcomes if outcome.newly_purged)
        log.info("pii purged: org=%s hire=%s values=%d", org_id, hire_id, newly)
        return PurgeReport(hires_purged=1, values_purged=newly)

    async def _audit_value(self, outcome: PurgeOutcome) -> None:
        """One success record per purged value, unless an earlier pass wrote it before it stopped."""
        target = f"pii:{outcome.token}"
        query = AuditQuery(action=ACTION, target=target, outcome=AuditOutcome.SUCCESS)
        if (await self._auditor.list(query, page=1, limit=1)).total:
            return
        await self._auditor.audit(
            actor=self._actor, action=ACTION, target=target, outcome=AuditOutcome.SUCCESS, reason=_REASON
        )

    async def _audit_failure(self, hire_id: str, error: BaseException) -> None:
        try:
            await self._auditor.audit(
                actor=self._actor,
                action=ACTION,
                target=f"hire:{hire_id}",
                outcome=AuditOutcome.FAILED,
                reason=f"{_REASON}; stopped on {type(error).__name__}",
            )
        except Exception as audit_error:
            log.error("pii purge failure not audited: hire=%s error=%s", hire_id, type(audit_error).__name__)

    async def _clear_hire(self, hire_id: str, hire: Mapping[str, Any]) -> None:
        """What the hire document keeps of its values: masks say `[purged]`, indexes and versions go."""
        fields = list((hire.get("pii") or {}).keys())
        await self._store.tenant(HIRES_COLLECTION).update_one(
            {"_id": hire_id},
            {
                "$set": {**{f"pii_masked.{field}": PURGED for field in fields}, _PURGED_AT: self._clock()},
                "$unset": {key: "" for key in ("pii_versions", *_BLIND_INDEX_KEYS)},
            },
        )
