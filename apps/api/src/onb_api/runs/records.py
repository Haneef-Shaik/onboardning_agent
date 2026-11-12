"""A stored run as the API shows it (PRD §10): `RunRecord` with tasks, `RunSummary` without.

Only fields the contracts name leave the API. The run's DAG, trace and the tasks' leases and histories
stay in the database; `error` is the error type of the last failed attempt, never a message.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from onb_contracts.runs import RunRecord, RunSummary, RunTaskRecord

__all__ = ["run_record", "run_summary", "task_record"]


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
