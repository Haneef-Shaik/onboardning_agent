"""The agent runtime (AG-1): any registered agent as a LangGraph subgraph with only its allowed tools.

    runner = AgentRunner(models=runtime_models(llm_runtime), tools=HubToolSource(hub_url),
                         auditor=AuditLog(store), token_key=env["AGENT_TOKEN_KEY"])
    result = await runner.run(registered, AgentTask(org_id, run_id, task_id, input, grant=grant))

See `runner` for outcomes, `tools` for the hub connection, `task` for tokens and the grant header.
"""

from onb_agents.runtime.graph import build_agent_graph
from onb_agents.runtime.local_tools import LOCAL_TOOLS, local_tools_for
from onb_agents.runtime.runner import (
    AgentRunner,
    AgentRunResult,
    Failure,
    ModelChoice,
    ModelProvider,
    Usage,
    runtime_models,
)
from onb_agents.runtime.task import GRANT_HEADER, AgentTask, hub_headers, issue_task_token, task_scopes
from onb_agents.runtime.tools import HubToolSource, ToolSet, ToolSource
from onb_agents.runtime.trajectory import TrajectoryStep

__all__ = [
    "GRANT_HEADER",
    "LOCAL_TOOLS",
    "AgentRunResult",
    "AgentRunner",
    "AgentTask",
    "Failure",
    "HubToolSource",
    "ModelChoice",
    "ModelProvider",
    "ToolSet",
    "ToolSource",
    "TrajectoryStep",
    "Usage",
    "build_agent_graph",
    "hub_headers",
    "issue_task_token",
    "local_tools_for",
    "runtime_models",
    "task_scopes",
]
