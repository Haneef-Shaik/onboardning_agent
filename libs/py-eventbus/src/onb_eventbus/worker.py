"""A worker: claim by lease, heartbeat, retry with backoff, dead letters (PRD §7.2, §7.9, §12).

One `run_once`:
1. claims the oldest ready job of its consumers in one atomic `find_one_and_update`, which takes a
   lease (`locked_until`, a fresh `lease_token`) and counts the attempt, so two workers never hold
   the same job at once;
2. dead-letters it at once when it has no valid `org_id`, is malformed, or has used all its attempts
   (only a crashed last attempt leaves a job claimable in that state);
3. otherwise sets the tenant context from the job and runs the handler inside a span continued from
   the job's `traceparent`, renewing the lease every `heartbeat_seconds`;
4. marks it done, reschedules it with exponential backoff, or dead-letters it when exhausted.

Every write after the claim matches `{_id, lease_token}`. A worker whose lease lapsed and was taken
over finds its writes match nothing: it stops its handler (the heartbeat notices) and writes nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any

from onb_data import DataStore, TenantContext, tenant_scope
from onb_telemetry import continue_from, span_attributes
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from . import documents
from .interface import Worker
from .models import DeadLetterReason, Handler, JobContext, Processed, WorkOutcome
from .settings import QueueSettings, backoff_delay

__all__ = ["MongoWorker"]

Document = dict[str, Any]

_log = logging.getLogger("onb_eventbus")
_CRASHED = "lease expired"


class _LeaseLostError(Exception):
    """Raised inside a transaction to roll it back when the lease has passed to another worker."""


def _org_problem(job: Mapping[str, Any]) -> DeadLetterReason | None:
    if "org_id" not in job or job["org_id"] is None:
        return DeadLetterReason.MISSING_ORG_ID
    try:
        TenantContext(job["org_id"])
    except ValueError:
        return DeadLetterReason.INVALID_ORG_ID
    return None


def _malformed(job: Mapping[str, Any]) -> bool:
    texts = ("consumer", "event_id", "event_type")
    if not all(isinstance(job.get(key), str) and job[key] for key in texts):
        return True
    if not isinstance(job.get("payload"), Mapping):
        return True
    counts = (job.get("attempts"), job.get("max_attempts"))
    if not all(isinstance(n, int) and not isinstance(n, bool) for n in counts):
        return True
    run_id = job.get("run_id")
    return run_id is not None and not isinstance(run_id, str)


def _error_name(exc: BaseException) -> str:
    """Only the exception type is stored or traced: a message may carry personal data (PRD §5.5)."""
    return type(exc).__name__


class MongoWorker(Worker):
    def __init__(
        self,
        store: DataStore,
        handlers: dict[str, Handler],
        *,
        settings: QueueSettings,
        clock: Callable[[], datetime],
        worker_id: str,
    ) -> None:
        self.worker_id = worker_id
        self._store = store
        self._jobs = store.queue("jobs")
        self._dead = store.queue("dead_letters")
        self._handlers = handlers
        self._settings = settings
        self._clock = clock
        self._lease = timedelta(seconds=settings.lease_seconds)

    # --- the loop ---------------------------------------------------------------------------

    async def run(self, stop: asyncio.Event) -> int:
        processed = 0
        while not stop.is_set():
            try:
                done = await self.run_once()
            except Exception as exc:  # keep the worker alive through a database blip
                _log.warning("worker %s: cycle failed (%s); retrying", self.worker_id, _error_name(exc))
                done = None
            if done is None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self._settings.poll_seconds)
            else:
                processed += 1
        return processed

    async def run_once(self) -> Processed | None:
        lease_token = documents.new_id()
        now = self._clock()
        job = await self._jobs.claim(
            documents.claim_query(self._handlers, now),
            documents.claim_update(worker_id=self.worker_id, lease_token=lease_token, now=now, lease=self._lease),
            sort=[("available_at", 1)],
        )
        if job is None:
            return None
        job_id = job["_id"]
        org_problem = _org_problem(job)
        if org_problem is not None:
            return await self._dead_letter(job, lease_token, org_problem, None)
        if _malformed(job):
            return await self._dead_letter(job, lease_token, DeadLetterReason.MALFORMED, None)
        if job["attempts"] > job["max_attempts"]:
            return await self._dead_letter(job, lease_token, DeadLetterReason.EXHAUSTED, _CRASHED)
        return Processed(job_id, await self._execute(job, lease_token))

    # --- running a handler under a lease ----------------------------------------------------

    async def _execute(self, job: Document, lease_token: str) -> WorkOutcome:
        context = JobContext(
            job_id=job["_id"],
            org_id=job["org_id"],
            consumer=job["consumer"],
            event_id=job["event_id"],
            event_type=job["event_type"],
            payload=MappingProxyType(copy.deepcopy(dict(job["payload"]))),
            run_id=job.get("run_id"),
            attempt=job["attempts"],
        )
        attributes = {
            **span_attributes(org_id=context.org_id, run_id=context.run_id),
            "messaging.system": "mongodb",
            "messaging.operation.type": "process",
            "messaging.destination.name": context.event_type,
            "messaging.consumer.group.name": context.consumer,
            "messaging.message.id": str(context.job_id),
        }
        with (
            tenant_scope(context.org_id),
            continue_from(
                job.get("traceparent"), f"consume {context.consumer}", attributes=attributes, kind=SpanKind.CONSUMER
            ) as span,
        ):
            outcome = await self._run_handler(job, lease_token, context, span)
            span.set_attribute("onb.job.outcome", outcome.value)
            return outcome

    async def _run_handler(self, job: Document, lease_token: str, context: JobContext, span: Span) -> WorkOutcome:
        handler = self._handlers[context.consumer]

        async def call_handler() -> None:
            await handler(context)

        handler_task = asyncio.create_task(call_handler())
        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(context.job_id, lease_token, handler_task, lease_lost))
        error: str | None = None
        try:
            await handler_task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if lease_lost.is_set() and current is not None and current.cancelling() == 0:
                return WorkOutcome.LEASE_LOST
            raise  # this worker is shutting down or crashed: leave the job for its lease to lapse
        except Exception as exc:
            error = _error_name(exc)
            span.set_status(Status(StatusCode.ERROR, error))
        finally:
            heartbeat.cancel()
            handler_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        if error is not None:
            return await self._failed(job, lease_token, error)
        now = self._clock()
        if await self._jobs.update_leased(context.job_id, lease_token, documents.complete_update(now=now)):
            return WorkOutcome.COMPLETED
        return WorkOutcome.LEASE_LOST

    async def _heartbeat(
        self, job_id: Any, lease_token: str, handler_task: asyncio.Task[None], lease_lost: asyncio.Event
    ) -> None:
        while True:
            await asyncio.sleep(self._settings.heartbeat_seconds)
            try:
                held = await self._jobs.update_leased(
                    job_id, lease_token, documents.heartbeat_update(now=self._clock(), lease=self._lease)
                )
            except Exception as exc:  # the lease may still hold; try again at the next beat
                _log.warning("worker %s: heartbeat failed (%s)", self.worker_id, _error_name(exc))
                continue
            if not held:
                _log.warning("worker %s: lease on job %s lost; stopping its handler", self.worker_id, job_id)
                lease_lost.set()
                handler_task.cancel()
                return

    # --- after a failure --------------------------------------------------------------------

    async def _failed(self, job: Document, lease_token: str, error: str) -> WorkOutcome:
        attempts: int = job["attempts"]
        if attempts >= job["max_attempts"]:
            return (await self._dead_letter(job, lease_token, DeadLetterReason.EXHAUSTED, error)).outcome
        delay = backoff_delay(
            attempts,
            base_seconds=self._settings.backoff_base_seconds,
            max_seconds=self._settings.backoff_max_seconds,
        )
        update = documents.retry_update(now=self._clock(), delay=timedelta(seconds=delay), error=error)
        if await self._jobs.update_leased(job["_id"], lease_token, update):
            return WorkOutcome.RETRIED
        return WorkOutcome.LEASE_LOST

    async def _dead_letter(
        self, job: Document, lease_token: str, reason: DeadLetterReason, error: str | None
    ) -> Processed:
        """Move the job to `dead_letters` in one transaction: insert there, delete from `jobs`."""
        letter = documents.dead_letter_document(job, reason=reason.value, error=error, now=self._clock())

        async def move() -> None:
            await self._dead.insert_one(letter)
            if not await self._jobs.delete_leased(job["_id"], lease_token):
                raise _LeaseLostError

        try:
            await self._store.run_transaction(move)
        except _LeaseLostError:
            return Processed(job["_id"], WorkOutcome.LEASE_LOST)
        _log.warning("worker %s: job %s dead-lettered (%s)", self.worker_id, job["_id"], reason.value)
        return Processed(job["_id"], WorkOutcome.DEAD_LETTERED)
