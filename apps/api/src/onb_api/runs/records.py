"""A stored run as the API shows it (PRD §10): `RunRecord` with tasks, `RunSummary` without.

Only fields the contracts name leave the API. The run's DAG, parent span and the tasks' leases and histories
stay in the database; `error` is the error type of the last failed attempt, never a message.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from onb_contracts.runs import RunRecord, RunSummary, RunTaskRecord

__all__ = ["run_record", "run_summary", "task_record"]


_TRACEPARENT: Final = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$")


def trace_id_of(traceparent: object) -> str | None:
    """The trace id of a stored W3C `traceparent`; None when absent, malformed or the all-zero (invalid) id."""
    match = _TRACEPARENT.match(traceparent) if isinstance(traceparent, str) else None
    if match is None or set(match.group(1)) == {"0"}:
        return None
    return match.group(1)


def task_record(task: Mapping[str, Any]) -> RunTaskRecord:
    return RunTaskRecord(
        task_id=task["task_id"],
        title=task["title"],
        type=task["type"],
        agent=task["agent"],
        action=task["action"],
        depends_on=list(task["depends_on"]),
        status=task["status"],
        skip_reason=task["skip_reason"],
        due=task["due"],
        attempts=int(task.get("attempts", 0)),
        error=task.get("error"),
    )


def run_record(run: Mapping[str, Any], tasks: Sequence[Mapping[str, Any]]) -> RunRecord:
    return RunRecord(
        id=run["_id"],
        hire_id=run["hire_id"],
        template_key=run["template_key"],
        template_version=run["template_version"],
        dag_hash=run["dag_hash"],
        status=run["status"],
        created_by=run["created_by"],
        created_at=run["created_at"],
        trace_id=trace_id_of(run.get("traceparent")),
        tasks=[task_record(task) for task in tasks],
    )


def run_summary(run: Mapping[str, Any]) -> RunSummary:
    return RunSummary(
        id=run["_id"],
        hire_id=run["hire_id"],
        template_key=run["template_key"],
        template_version=run["template_version"],
        status=run["status"],
        created_by=run["created_by"],
        created_at=run["created_at"],
        updated_at=run["updated_at"],
    )
