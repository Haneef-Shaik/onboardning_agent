"""Onboarding runs: the state machines, the run and task records, and how a run starts (PRD §6.2, §7.1).

    from onb_agents.runs import RunStarter, RunStore, TaskState, run_bus
    starter = RunStarter(RunStore(store), run_bus(store), auditor)
    with tenant_scope(org_id_from_token):
        run = await starter.start(template=version, hire_id=hire["_id"], variables=..., actor_id=uid)

The orchestrator (`apps/orchestrator`) consumes `run.started` and executes the run's tasks.
"""

from .states import (
    IllegalTransitionError,
    RunState,
    TaskState,
    check_run_transition,
    check_transition,
    dependencies_met,
)

__all__ = [
    "IllegalTransitionError",
    "RunState",
    "TaskState",
    "check_run_transition",
    "check_transition",
    "dependencies_met",
]
