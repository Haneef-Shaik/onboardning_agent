"""Test support for runs: synthetic published templates and hire variables. Test-only, never imported
by production code (like `onb_agents.testing`). No real person's data: every value is made up.

`diamond_template()` is the FR-R3 shape: `a` fans out to `b` and `c`, which fan in to `d`; `e` depends
on `a` and runs only for engineers, and `f` depends on `e`, so a sales hire shows a skipped dependency
that must not block.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from onb_contracts.templates import Template
from onb_data import current_tenant
from onb_eventbus import EventBus, Handler, PublishResult, Worker

from .events import SUBSCRIPTIONS

__all__ = ["DIAMOND_KEY", "OutboxStandIn", "diamond_template", "hire_variables_for", "uneven_diamond_template"]

DIAMOND_KEY = "diamond-test"


def _task(task_id: str, *depends_on: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": f"Task {task_id.upper()}",
        "agent": "provisioning_agent",
        "action": f"do_{task_id}",
        "depends_on": list(depends_on),
        **extra,
    }


def diamond_template(*, version: int = 1, key: str = DIAMOND_KEY, extra_task: bool = False) -> Template:
    """A published diamond template. `extra_task` adds `g` after `d` (a later version's change)."""
    tasks = [
        _task("a", sla={"due": "start_date - 7d"}),
        _task("b", "a"),
        _task("c", "a"),
        _task("d", "b", "c"),
        _task("e", "a", when="role == 'engineer'"),
        _task("f", "e"),
    ]
    if extra_task:
        tasks.append(_task("g", "d"))
    return Template.model_validate(
        {
            "key": key,
            "name": "Diamond (test)",
            "version": version,
            "status": "published",
            "variables": [
                {"name": "role", "type": "enum", "values": ["engineer", "designer", "sales"]},
                {"name": "location", "type": "string"},
                {"name": "start_date", "type": "date"},
                {"name": "manager_id", "type": "user_ref"},
            ],
            "tasks": tasks,
        }
    )


def uneven_diamond_template(*, version: int = 1) -> Template:
    """`a` fans out to a long branch (`b` → `b2`) and a short one (`c`); `d` joins both, so it must
    wait for `b2` even though `c` finished a step earlier."""
    base = diamond_template(version=version).model_dump(mode="json")
    tasks = [_task("a"), _task("b", "a"), _task("b2", "b"), _task("c", "a"), _task("d", "b2", "c")]
    return Template.model_validate({**base, "key": "uneven-test", "tasks": tasks})


def hire_variables_for(role: str = "engineer") -> dict[str, str]:
    return {"role": role, "location": "Testville", "start_date": "2026-12-01", "manager_id": "u_test_manager"}


@dataclass
class OutboxStandIn(EventBus):
    """An event bus for unit tests: runs `change`, then keeps the event, without a transaction (the
    real outbox, one transaction on a replica set, is EVT-1's and the integration tests'). A key the
    org already published is a duplicate and writes nothing, `change` included, like the real bus.
    `fail_after_change` makes the event write fail after `change` ran."""

    published: list[dict[str, Any]] = field(default_factory=list)
    fail_after_change: bool = False

    async def publish(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
        run_id: str | None = None,
        change: Callable[[], Awaitable[None]] | None = None,
    ) -> PublishResult:
        org_id = current_tenant().org_id
        if any(p["org_id"] == org_id and p["idempotency_key"] == idempotency_key for p in self.published):
            return PublishResult(event_id="ev_duplicate", duplicate=True)
        if change is not None:
            await change()
        if self.fail_after_change:
            raise RuntimeError("the event write failed")
        self.published.append(
            {
                "org_id": org_id,
                "type": event_type,
                "payload": dict(payload),
                "idempotency_key": idempotency_key,
                "run_id": run_id,
            }
        )
        return PublishResult(event_id=f"ev_{len(self.published)}", duplicate=False, job_ids=("job_1",))

    def worker(self, handlers: Mapping[str, Handler], *, worker_id: str | None = None) -> Worker:
        raise NotImplementedError("the stand-in has no queue; integration tests use the real bus")

    def consumers(self) -> tuple[str, ...]:
        return tuple(sorted({c for names in SUBSCRIPTIONS.values() for c in names}))
