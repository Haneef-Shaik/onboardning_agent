"""Onboarding runs: the state machines, the run and task records, and how a run starts (PRD §6.2, §7.1).

    from onb_agents.runs import RunStarter, RunStore, TaskState, run_bus
    starter = RunStarter(RunStore(store), run_bus(store), auditor)
    with tenant_scope(org_id_from_token):
        started = await starter.start(template=version, hire_id=hire_id, variables=..., actor_id=uid)

The orchestrator (`apps/orchestrator`) consumes `run.started` and executes the run's tasks.
"""

from .events import (
    CLEANUP_RUN,
    FAIL_TASK,
    RESUME_RUN,
    RUN_BLOCKED_KIND,
    RUN_CANCELLED,
    RUN_PAUSED,
    RUN_RESUMED,
    RUN_STARTED,
    SINK_REJECTED,
    START_RUN,
    SUBSCRIPTIONS,
    control_key,
    run_bus,
    started_key,
)
from .records import (
    HIRE_VARIABLES,
    RUN_TASKS,
    RUNS,
    DagIntegrityError,
    RunRecords,
    hire_variables,
    new_run_id,
    run_records,
    stored_dag,
    thread_id,
)
from .starter import START_ACTION, RunStarter, StartedRun, run_target
from .states import (
    IllegalTransitionError,
    RunState,
    TaskState,
    check_run_transition,
    check_transition,
    dependencies_met,
)
from .store import RunStore, TransitionConflictError, lease_expired

__all__ = [
    "CLEANUP_RUN",
    "FAIL_TASK",
    "HIRE_VARIABLES",
    "RESUME_RUN",
    "RUNS",
    "RUN_BLOCKED_KIND",
    "RUN_CANCELLED",
    "RUN_PAUSED",
    "RUN_RESUMED",
    "RUN_STARTED",
    "RUN_TASKS",
    "SINK_REJECTED",
    "START_ACTION",
    "START_RUN",
    "SUBSCRIPTIONS",
    "DagIntegrityError",
    "IllegalTransitionError",
    "RunRecords",
    "RunStarter",
    "RunState",
    "RunStore",
    "StartedRun",
    "TaskState",
    "TransitionConflictError",
    "check_run_transition",
    "check_transition",
    "control_key",
    "dependencies_met",
    "hire_variables",
    "lease_expired",
    "new_run_id",
    "run_bus",
    "run_records",
    "run_target",
    "started_key",
    "stored_dag",
    "thread_id",
]
