"""A run's compiled DAG as a LangGraph `StateGraph` (PRD §7.1 Compilation, FR-R3).

One node per task, edges from `depends_on`: a task without dependencies starts from `START`; a task
with one dependency follows it; a task with several waits for all of them (a fan-in join, LangGraph's
`add_edge([deps...], task)`), and a task nothing depends on leads to `END`. Tasks whose dependencies
are finished in the same step run in parallel (fan-out).

The graph's shape comes only from the run's stored DAG document, checked against its hash first
(`stored_dag`): nothing here, and no LLM, adds, removes or reorders tasks (PRD §5.1). Skipped tasks
keep their node, so the graph has the template's shape; their step does nothing and lets dependents
go on. The graph checkpoints on the thread `"{org_id}:{run_id}"` (`TenantCheckpointSaver`).

A task that waits for an approval (HIL-1) pauses its node with LangGraph's `interrupt()`: the graph
checkpoints, the run stays `running`, and nothing after that task starts. `orchestrator.resume_approval`
resumes the node with `Command(resume=...)`; the node runs again from its top, finds the task
`waiting_approval`, gets the resume value from `interrupt()` and `settle`s the task from the stored
decision (the resume value carries the approval id only, never the decision itself). An approval already
decided when the run is driven on again (it was decided while the run was paused or blocked, or before the pause was
checkpointed) is resumed by the driver before the graph is driven on (`_Driver.drive`).
"""

from __future__ import annotations

import operator
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Final, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from onb_agents.runs import stored_dag, thread_id
from onb_data import current_tenant

from .checkpointer import TenantCheckpointSaver
from .runner import (
    DrainRequestedError,
    RunExecutor,
    RunHaltedError,
    TaskAwaitsApprovalError,
    TaskExhaustedError,
    TaskFailedError,
    TaskStepError,
    step_error,
)

__all__ = ["RunGraph", "RunGraphState", "build_run_graph"]

# Graph steps beyond one per task (a chain of n tasks takes n steps).
_STEP_MARGIN: Final = 5


class RunGraphState(TypedDict):
    finished: Annotated[list[str], operator.add]


@dataclass(frozen=True, slots=True)
class RunGraph:
    graph: CompiledStateGraph[RunGraphState, None, RunGraphState, RunGraphState]
    config: RunnableConfig


@dataclass(frozen=True, slots=True)
class _TaskStep:
    """One task's node: run the task's step, then record it finished in the graph state."""

    runner: RunExecutor
    run_id: str
    task_id: str
    traceparent: str | None

    async def __call__(self, state: RunGraphState) -> RunGraphState:
        approval_id = await self._step(
            lambda: self.runner.run_task(self.run_id, self.task_id, traceparent=self.traceparent)
        )
        while approval_id is not None:
            # The task waits for a human. The first pass raises `GraphInterrupt` here (the graph pauses) and a
            # resumed pass gets the resume value and settles the task from the stored decision. Settling can
            # run the task again (an approved tool call) and meet the next approval: pause again (at most two
            # per task, ADR 0020).
            interrupt({"approval_id": approval_id})
            approval_id = await self._step(
                lambda: self.runner.settle(self.run_id, self.task_id, traceparent=self.traceparent)
            )
        return {"finished": [self.task_id]}

    @staticmethod
    async def _step(work: Callable[[], Awaitable[None]]) -> str | None:
        """Run one step of the task: None when it finished, the approval id when it waits for one."""
        try:
            await work()
        except TaskAwaitsApprovalError as waiting:
            return waiting.approval_id
        except (TaskFailedError, TaskExhaustedError, RunHaltedError, DrainRequestedError, TaskStepError):
            raise
        except Exception as error:
            # LangGraph stores a node's error in the checkpoint: only its type goes there.
            raise step_error(error) from None
        return None


def build_run_graph(
    run: Mapping[str, Any], runner: RunExecutor, checkpointer: BaseCheckpointSaver[Any] | None = None
) -> RunGraph:
    """The run's graph and the config to invoke it with. Raises `DagIntegrityError` for a changed DAG."""
    dag = stored_dag(run)
    run_id = str(run["_id"])
    traceparent = run.get("traceparent")
    tasks: list[Mapping[str, Any]] = dag["tasks"]
    builder = StateGraph(RunGraphState)
    for task in tasks:
        task_id = str(task["id"])
        builder.add_node(task_id, _TaskStep(runner, run_id, task_id, traceparent))
    has_dependents = {dependency for task in tasks for dependency in task["depends_on"]}
    for task in tasks:
        depends_on = list(task["depends_on"])
        if not depends_on:
            builder.add_edge(START, task["id"])
        elif len(depends_on) == 1:
            builder.add_edge(depends_on[0], task["id"])
        else:
            builder.add_edge(depends_on, task["id"])
        if task["id"] not in has_dependents:
            builder.add_edge(task["id"], END)
    saver = checkpointer if checkpointer is not None else TenantCheckpointSaver(runner.store)
    config: RunnableConfig = {
        "configurable": {"thread_id": thread_id(current_tenant().org_id, run_id)},
        "recursion_limit": len(tasks) + _STEP_MARGIN,
    }
    return RunGraph(graph=builder.compile(checkpointer=saver), config=config)
