"""`GET /runs/{id}/stream`: a run's task transitions as server-sent events (PRD §10 Live updates, FR-R2).

Every task and run keeps its own `history` (each move appended in the same write as the move), so the
stream replays what already happened and then follows new moves, and no transition is missed or
repeated however fast the orchestrator works: a poll sends the entries past the ones it already sent.

    event: task    data: {"task_id", "from", "to", "at", "note"?}   one per task transition
    event: run     data: {"from", "to", "at"}                        one per run transition
    event: end     data: {"status", "reason"?}                       the run finished, or the stream's
                                                                      lifetime is over (reconnect)

Nothing else is sent: no org id, no actor, no task or hire data. The stream ends by itself when the run
is finished and everything has been sent, and after `max_seconds` otherwise (a client reconnects and
replays). A comment line keeps idle connections open. The caller's access is checked before the stream
starts (`RunControl.visible_run`); every poll runs in the caller's org.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Mapping
from typing import Any, Final

from onb_agents.runs import RunState, RunStore
from onb_data import tenant_scope

__all__ = ["DEFAULT_MAX_SECONDS", "DEFAULT_POLL_SECONDS", "sse", "stream_run"]

DEFAULT_POLL_SECONDS: Final = 1.0
DEFAULT_MAX_SECONDS: Final = 3600.0
_KEEPALIVE_SECONDS: Final = 15.0


def sse(event: str, data: Mapping[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _moment(entry: Mapping[str, Any]) -> str:
    at = entry["at"]
    return str(at.isoformat() if hasattr(at, "isoformat") else at)


def _entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    shown = {"from": entry["from"], "to": entry["to"], "at": _moment(entry)}
    if entry.get("note"):
        shown["note"] = entry["note"]
    return shown


async def stream_run(
    runs: RunStore, org_id: str, run_id: str, *, poll_seconds: float, max_seconds: float
) -> AsyncIterator[str]:
    """The events of one run, until it finishes (or `max_seconds`). The caller has checked access."""
    started = time.monotonic()
    last_sent = started
    run_sent = 0
    task_sent: dict[str, int] = {}
    yield ": stream open\n\n"
    while True:
        with tenant_scope(org_id):
            run = await runs.get(run_id)
            tasks = await runs.tasks(run_id) if run is not None else []
        if run is None:
            yield sse("end", {"status": "gone"})
            return
        fresh: list[tuple[str, str, dict[str, Any]]] = []
        for task in tasks:
            seen = task_sent.get(task["task_id"], 0)
            history = task.get("history", [])
            fresh.extend((_moment(h), "task", {"task_id": task["task_id"], **_entry(h)}) for h in history[seen:])
            task_sent[task["task_id"]] = len(history)
        history = run.get("history", [])
        fresh.extend((_moment(h), "run", _entry(h)) for h in history[run_sent:])
        run_sent = len(history)
        for _, event, data in sorted(fresh, key=lambda item: item[0]):  # stable: ties keep task order
            yield sse(event, data)
            last_sent = time.monotonic()
        if RunState(run["status"]).active is False:
            yield sse("end", {"status": run["status"]})
            return
        if time.monotonic() - started >= max_seconds:
            yield sse("end", {"status": run["status"], "reason": "timeout"})
            return
        if time.monotonic() - last_sent >= _KEEPALIVE_SECONDS:
            yield ": keepalive\n\n"
            last_sent = time.monotonic()
        await asyncio.sleep(poll_seconds)
