"""Approvals for the Playwright tests (UI-6), in memory, through the production code paths.

A run started here is driven by the **real orchestrator consumers** (`orchestrator.start_run`,
`orchestrator.resume_approval`, `orchestrator.record_decision`) and the approver is told by the **real**
`notify.approval` consumer (in-app + Telegram-mock message with Approve/Reject buttons). Decisions go through the
real `ApprovalService` (web) and the real `MockChannels` webhook (`POST /webhooks/telegram`). Only two things
stand in:

* the **event bus** (`PumpBus`): the EVT-1 outbox needs a Mongo replica set; here each published event is handed
  to its consumer at once, in the server's loop, retried while the consumer says it is transient (as the bus does);
* the **task executor** (`StubApprovalsAgent`): every task succeeds without work, and for an `approval` task a
  fixed, synthetic packet is stored where the Approvals Agent stores its masked output. No model is called.

Test-only: mounted by `serve_api.py` on 127.0.0.1. Runs are for the seeded hire (Meera Iyer, Ravi's report), so
Ravi (`u_acme_mgr_ravi`) is the approver of `manager_approval`. Synthetic data.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from fastapi import APIRouter
from onb_agents.runs import RUN_STARTED, START_RUN, RunStore
from onb_agents.runs.testing import OutboxStandIn
from onb_api.approvals import ApprovalService
from onb_api.hires.service import HireService
from onb_api.notify.service import MockChannels
from onb_api.runs import RunService
from onb_api.testing import SEED_USERS, InMemoryAuditLog
from onb_contracts.approvals import APPROVAL_DECIDED, APPROVAL_REQUESTED, APPROVALS_COLLECTION
from onb_contracts.notify import DECISION_RECEIVED
from onb_contracts.runs import RunCreate
from onb_data import DataStore, tenant_scope
from onb_eventbus import JobContext, PublishResult
from onb_mcp_mocks.notify.approvals import approval_notify_handler, ensure_approval_notify_indexes
from onb_mcp_mocks.notify.in_app import ensure_in_app_indexes
from onb_mcp_mocks.notify.outbox import TelegramMockChannel
from onb_mcp_mocks.telegram import TelegramMockTransport
from onb_orchestrator.consumer import record_decision_handler, resume_approval_handler, start_run_handler
from onb_orchestrator.executor import StubExecutor, TaskWork
from onb_orchestrator.runner import TaskStepError
from run_seed import HR, ORG, TEMPLATE_KEY

_log = logging.getLogger("e2e.approvals")
Handler = Callable[[JobContext], Awaitable[None]]
RETRY_SECONDS: Final = 0.2
MAX_ATTEMPTS: Final = 100  # 20 s: a decision can land before the start job has checkpointed its pause
# Synthetic and fixed: what the Approvals Agent would store (masked), so the inbox shows a packet.
STUB_PACKET: Final = {
    "summary": "Standard engineer kit: a laptop and Bitbucket access, within the equipment policy.",
    "recommendation": "approve",
    "risk_flags": [],
    "cited_policies": ["kb_equipment_policy"],
}


@dataclass
class StubApprovalsAgent(StubExecutor):
    """The stub executor, plus the Approvals Agent's one effect: the packet on the task's pending approval."""

    store: DataStore | None = None

    async def execute(self, work: TaskWork) -> None:
        await super().execute(work)
        if work.type == "approval" and self.store is not None:
            await self.store.tenant(APPROVALS_COLLECTION).update_one(
                {"run_id": work.run_id, "task_id": work.task_id, "status": "pending", "packet": None},
                {"$set": {"packet": dict(STUB_PACKET), "packet_failure": None}},
            )


class PumpBus(OutboxStandIn):
    """Publishes like the stand-in, then hands each new event to its consumers in the background."""

    def __init__(self) -> None:
        super().__init__()
        self.routes: dict[str, list[tuple[str, Handler]]] = {}
        self._ids = itertools.count(1)
        self._tasks: set[asyncio.Task[None]] = set()

    def route(self, event_type: str, consumer: str, handler: Handler) -> None:
        self.routes.setdefault(event_type, []).append((consumer, handler))

    async def publish(self, event_type: str, payload: Mapping[str, Any], **kwargs: Any) -> PublishResult:
        result = await super().publish(event_type, payload, **kwargs)
        if not result.duplicate:
            event = self.published[-1]
            for consumer, handler in self.routes.get(event_type, []):
                task = asyncio.get_running_loop().create_task(self._deliver(consumer, handler, event))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        return result

    async def deliver_now(self, consumer: str, handler: Handler, event: Mapping[str, Any]) -> None:
        await self._deliver(consumer, handler, event)

    async def _deliver(self, consumer: str, handler: Handler, event: Mapping[str, Any]) -> None:
        number = next(self._ids)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            job = JobContext(
                f"job_{number}",
                event["org_id"],
                consumer,
                f"ev_{number}",
                event["type"],
                MappingProxyType(dict(event["payload"])),
                event.get("run_id"),
                attempt,
            )
            try:
                with tenant_scope(event["org_id"]):
                    await handler(job)
                return
            except TaskStepError as error:
                if not error.transient:
                    _log.warning("%s: %s gave up (%s)", consumer, event["type"], error.type_name)
                    return
                await asyncio.sleep(RETRY_SECONDS)
            except Exception:
                _log.exception("%s failed on %s", consumer, event["type"])
                return
        _log.warning("%s: %s still transient after %d attempts", consumer, event["type"], MAX_ATTEMPTS)


class PumpDecisions:
    """The webhook's `DecisionPublisher` (NOTIF-1) over the pump: `decision.received` reaches the orchestrator."""

    def __init__(self, bus: PumpBus) -> None:
        self._bus = bus

    async def publish(self, payload: Mapping[str, Any], *, key: str) -> bool:
        result = await self._bus.publish(DECISION_RECEIVED, payload, idempotency_key=key)
        return not result.duplicate


@dataclass
class ApprovalWorld:
    approvals: ApprovalService
    notify: MockChannels
    start: Callable[[], Awaitable[dict[str, Any]]]


async def approval_world(
    store: DataStore, runs: RunService, hires: HireService, hire_id: str, webhook_secret: str
) -> ApprovalWorld:
    """The approval and notify services over the run store's database, wired to the real consumers."""
    bus = PumpBus()
    executor = StubApprovalsAgent(store=store)
    bus.route(
        APPROVAL_REQUESTED,
        "notify.approval",
        approval_notify_handler(store, TelegramMockChannel(store, TelegramMockTransport())),
    )
    bus.route(APPROVAL_DECIDED, "orchestrator.resume_approval", resume_approval_handler(store, executor, bus=bus))
    bus.route(DECISION_RECEIVED, "orchestrator.record_decision", record_decision_handler(store, bus))
    start_run = start_run_handler(store, executor, bus=bus)

    await ensure_approval_notify_indexes(store)
    await ensure_in_app_indexes(store)
    with tenant_scope(ORG):
        # In production the hires, users and runs share one database; here the hire and user services keep
        # their own in-memory stores. The runner reads the hire's manager (the approver rule `manager_id`) and
        # notify.approval the approver's user record from the runs' database, so both get their non-PII fields.
        hire = await hires.get(HR, hire_id)
        await store.tenant("new_hires").insert_one({"_id": hire_id, "manager_id": hire.manager_id})
        for user_id, org_id, _name, role in SEED_USERS:
            if org_id == ORG:
                await store.tenant("users").insert_one({"_id": user_id, "role": role.value})

    approvals = ApprovalService.build(store, bus, auditor=InMemoryAuditLog())
    notify = MockChannels(store, webhook_secret=webhook_secret, decisions=PumpDecisions(bus))
    run_store = RunStore(store)

    async def start() -> dict[str, Any]:
        """A run of the engineer template for the seeded hire, driven until it waits for the manager."""
        with tenant_scope(ORG):
            record = await runs.start(HR, RunCreate(hire_id=hire_id, template_key=TEMPLATE_KEY, template_version=1))
            event = {"org_id": ORG, "type": RUN_STARTED, "payload": {"run_id": record.id}, "run_id": record.id}
        await bus.deliver_now(START_RUN, start_run, event)
        with tenant_scope(ORG):
            approval = await store.tenant(APPROVALS_COLLECTION).find_one({"run_id": record.id})
            run = await run_store.get(record.id) or {}
        return {
            "run_id": record.id,
            "approval_id": str(approval["_id"]) if approval else None,
            "run_status": run.get("status"),
        }

    return ApprovalWorld(approvals=approvals, notify=notify, start=start)


def approval_router(world: ApprovalWorld) -> APIRouter:
    router = APIRouter(prefix="/e2e", include_in_schema=False)

    @router.post("/approval-runs")
    async def start() -> dict[str, Any]:
        return await world.start()

    return router


class WebhookCounter:
    """ASGI middleware: counts `POST /webhooks/telegram` requests, so a test can see the webhook was called."""

    def __init__(self, app: Any) -> None:
        self.app = app

    calls: int = 0

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["method"] == "POST" and scope["path"].endswith("/webhooks/telegram"):
            WebhookCounter.calls += 1
        await self.app(scope, receive, send)


def counter_router() -> APIRouter:
    router = APIRouter(prefix="/e2e", include_in_schema=False)

    @router.get("/webhook-calls")
    async def calls() -> dict[str, int]:
        return {"telegram": WebhookCounter.calls}

    return router
