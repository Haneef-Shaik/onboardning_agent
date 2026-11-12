"""The Run Assistant under the harness (CHAT-1; evals/README.md "How a case runs").

Runs the registered `run_assistant` manifest through AG-1's `AgentRunner` with the case's model
(`invocation.chat_model`, the configured connector: `fake` in the self-test, Bedrock for the real gate) and
the harness's `ToolBox` as the tool authority, so every call is recorded and audited the way the checks
read it. The case's actor supplies the asker; the task is `chat_<case id>` in the actor's org. The agent's
validated `RunAssistantOutput` is what the checks see. A task that does not complete raises, so the case
fails with the failure kind instead of passing on a made-up answer.

`onb_agents` is imported here only: the harness itself does not depend on the agent runtime.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Final, cast

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel

from onb_evals.runner import AgentInvocation
from onb_evals.scripted import find_repo_file
from onb_evals.toolbox import CountArgs, FindArgs, ToolBox

__all__ = ["RunAssistantAdapter"]

AGENT: Final = "run_assistant"
HUB_PREFIX: Final = "mongo__"
_ARGS: Final = {"find": FindArgs, "count": CountArgs}


class _DiscardAuditor:
    """The runner's guard decisions are not part of any check: the ToolBox keeps the audit the checks read."""

    async def audit(self, **_: Any) -> None:
        return None


def _tools(box: ToolBox) -> tuple[BaseTool, ...]:
    def bind(name: str) -> BaseTool:
        async def call(**args: Any) -> str:
            return json.dumps(await box.call(name, args), default=str)

        return StructuredTool.from_function(
            coroutine=call,
            name=f"{HUB_PREFIX}{name}",
            description=f"Read-only {name} over this org's run records.",
            args_schema=cast(type[BaseModel], _ARGS[name]),
        )

    return tuple(bind(name) for name in _ARGS)


class _BoxSource:
    """A `ToolSource` whose authority is the harness's ToolBox (it denies and records what it must)."""

    def __init__(self, box: ToolBox) -> None:
        self._box = box

    @asynccontextmanager
    async def open(self, manifest: Any, task: Any, token: str) -> AsyncIterator[Any]:
        from onb_agents.runtime.tools import ForwardResult, ToolSet

        async def forward(name: str, args: Mapping[str, Any]) -> Any:
            result = await self._box.call(name.removeprefix(HUB_PREFIX), args)
            return ForwardResult(json.dumps(result, default=str), not result.get("ok", True))

        yield ToolSet(bound=_tools(self._box), forward=forward)


class RunAssistantAdapter:
    def __init__(self, config_dir: Path | None = None) -> None:
        self._config_dir = config_dir or find_repo_file("config/agents")

    async def invoke(self, invocation: AgentInvocation) -> Mapping[str, Any]:
        from onb_agents.registry import load_manifests
        from onb_agents.runtime import AgentRunner, AgentTask, ModelChoice

        manifest = next(m for m in load_manifests(self._config_dir) if m.name == AGENT)
        actor = invocation.case.actor
        runner = AgentRunner(
            models=lambda *_: ModelChoice(record={}, model=invocation.chat_model),
            tools=_BoxSource(invocation.tools),
            auditor=_DiscardAuditor(),  # type: ignore[arg-type]
            token_key=secrets.token_urlsafe(48),
        )
        task = AgentTask(
            org_id=actor.org_id,
            run_id=actor.run_id or "run_eval",
            task_id=f"chat_{invocation.case.id}",
            input={**invocation.input, "asker": {"user_id": actor.user_id, "role": actor.role}},
        )
        result = await runner.run(manifest, task)
        if result.outcome != "completed" or result.output is None:
            kind = result.failure.kind if result.failure else "no_output"
            raise RuntimeError(f"run_assistant task failed: {kind}")
        return result.output
