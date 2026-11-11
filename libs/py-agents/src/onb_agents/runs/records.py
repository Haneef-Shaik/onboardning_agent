"""The run and task records a compiled DAG becomes (PRD FR-R1, §7.1 Compilation, §7.9 Orchestrator).

A run record pins the template key and version it was compiled from and stores the compiled DAG
document with its hash. The hash is recomputed from the document here, just before it is persisted,
and a mismatch is refused (`DagIntegrityError`): compiled tasks are read-only (ADR 0008), so a DAG
changed after compiling never becomes a run. `stored_dag` checks it again whenever a run is read back
to be executed. Which tasks exist, and their edges, come only from that document; nothing adds,
removes or reorders them later (PRD §5.1).

One task record per compiled task, skipped ones included (status `skipped`, with the reason). The
LangGraph checkpoint thread of a run is `thread_id(org_id, run_id)` = `"{org_id}:{run_id}"`.

Records hold ids and template fields only: hire variables are compiled into due dates and skip
reasons that never echo a value, and no PII reaches a run.
"""

from __future__ import annotations

import copy
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from onb_agents.dag import CompiledDag

from .states import RunState

__all__ = [
    "HIRE_VARIABLES",
    "RUNS",
    "RUN_TASKS",
    "DagIntegrityError",
    "RunRecords",
    "hire_variables",
    "new_run_id",
    "run_records",
    "stored_dag",
    "thread_id",
]

RUNS: Final = "onboarding_runs"
RUN_TASKS: Final = "run_tasks"
# The hire fields a template may read as variables (TPL-1's seed declares these). Never PII.
HIRE_VARIABLES: Final = ("role", "location", "start_date", "manager_id")
_THREAD_SEPARATOR: Final = ":"


class DagIntegrityError(Exception):
    """A DAG document whose hash does not match: it changed after it was compiled."""


@dataclass(frozen=True, slots=True)
class RunRecords:
    run: dict[str, Any]
    tasks: tuple[dict[str, Any], ...]


def new_run_id() -> str:
    """A server-made run id. Never client-chosen: `_id` is unique across orgs (T-3)."""
    return "run_" + secrets.token_hex(12)


def thread_id(org_id: str, run_id: str) -> str:
    """The LangGraph checkpoint thread of a run: `"{org_id}:{run_id}"` (PRD §7.1 Durability)."""
    for part in (org_id, run_id):
        if not isinstance(part, str) or not part or _THREAD_SEPARATOR in part:
            raise ValueError("a thread id part must be a non-empty string without ':'")
    return f"{org_id}{_THREAD_SEPARATOR}{run_id}"


def hire_variables(hire: Mapping[str, Any]) -> dict[str, Any]:
    """The template variables a hire record gives: only `HIRE_VARIABLES`, never tokens or PII."""
    return {name: hire[name] for name in HIRE_VARIABLES if name in hire}


def _hash_checked(document: Mapping[str, Any], expected: object) -> None:
    try:
        actual = CompiledDag.hash_of(document)
    except (TypeError, ValueError) as error:
        raise DagIntegrityError("the DAG document cannot be hashed") from error
    if actual != expected:
        raise DagIntegrityError("the DAG document does not match its hash")


def run_records(
    *,
    org_id: str,
    run_id: str,
    hire_id: str,
    dag: CompiledDag,
    actor_id: str,
    traceparent: str | None,
    now: datetime,
) -> RunRecords:
    """The run and its task records. Raises `DagIntegrityError` if the DAG changed after compiling."""
    document = json.loads(json.dumps(dag.document(include_hash=False), allow_nan=False))
    _hash_checked(document, dag.hash)
    run = {
        "_id": run_id,
        "hire_id": hire_id,
        "template_key": dag.template_key,
        "template_version": dag.template_version,
        "dag_hash": dag.hash,
        "dag": document,
        "thread_id": thread_id(org_id, run_id),
        "status": RunState.STARTED.value,
        "traceparent": traceparent,
        "created_by": actor_id,
        "created_at": now,
        "updated_at": now,
    }
    tasks = tuple(_task_record(run_id, position, task) for position, task in enumerate(document["tasks"]))
    return RunRecords(run=run, tasks=tasks)


def _task_record(run_id: str, position: int, task: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "_id": f"{run_id}:{task['id']}",
        "run_id": run_id,
        "task_id": task["id"],
        "position": position,
        "title": task["title"],
        "type": task["type"],
        "agent": task["agent"],
        "action": task["action"],
        "depends_on": list(task["depends_on"]),
        "status": task["status"],
        "skip_reason": task["skip_reason"],
        "due": task["due"],
        "sla_hours": task.get("sla_hours"),
        "attempts": 0,
        "ready_at": None,
        "started_at": None,
        "finished_at": None,
    }


def stored_dag(run: Mapping[str, Any]) -> dict[str, Any]:
    """The run's compiled DAG document, after checking it still matches the hash it was stored with."""
    document = run.get("dag")
    if not isinstance(document, Mapping):
        raise DagIntegrityError("the run holds no DAG document")
    _hash_checked(document, run.get("dag_hash"))
    return copy.deepcopy(dict(document))
