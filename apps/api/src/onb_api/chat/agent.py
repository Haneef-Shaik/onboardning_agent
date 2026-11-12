"""The production `RunAssistant`: the registered `run_assistant` agent, run through AG-1's runtime (CHAT-1).

One chat turn is one agent task. It runs under a fresh per-task hub token whose scopes are exactly the
manifest's read-only Mongo tools (`mongo__find`, `mongo__count`, `mongo__aggregate`, over the manifest's
collections); the agent has no write tool and no local tool, so it cannot change a run. The task's input
is the redacted question, the run in question and who asks; the agent answers with the validated
`RunAssistantOutput` contract. A task that fails (budget, timeout, invalid output, guardrail) raises, and
the service turns that into a 503: a broken turn is never presented as an answer.
"""

from __future__ import annotations

from typing import Final, Protocol

from onb_agents.registry import RegisteredAgent
from onb_agents.runtime import AgentRunner, AgentRunResult, AgentTask
from onb_contracts.agent_outputs import RunAssistantOutput

from onb_api.chat.assistant import AssistantAnswer, AssistantProposal, AssistantTurn

__all__ = ["AGENT_NAME", "AgentRunAssistant", "AssistantFailedError", "answer_from"]

AGENT_NAME: Final = "run_assistant"
NO_ANSWER: Final = "I could not answer that."


class AssistantFailedError(Exception):
    """The agent task did not complete. `kind` and `reason` are codes, never message text."""

    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(f"{kind}: {reason}")
        self.kind = kind
        self.reason = reason


class AgentLookup(Protocol):
    async def get_agent(self, name: str) -> RegisteredAgent | None: ...


def answer_from(result: AgentRunResult) -> AssistantAnswer:
    """The agent's validated output as an answer; a failed task raises `AssistantFailedError`."""
    if result.outcome != "completed" or result.output is None:
        failure = result.failure
        raise AssistantFailedError(failure.kind if failure else "error", failure.reason if failure else "no_output")
    output = RunAssistantOutput.model_validate(result.output)
    proposed = output.proposed_action
    return AssistantAnswer(
        status=output.status,
        answer=output.answer or NO_ANSWER,
        citations=tuple((c.run_id, c.task_id) for c in output.citations),
        proposal=None
        if proposed is None
        else AssistantProposal(type=proposed.type, run_id=proposed.run_id, task_id=proposed.task_id),
    )


class AgentRunAssistant:
    def __init__(self, *, runner: AgentRunner, agents: AgentLookup) -> None:
        self._runner = runner
        self._agents = agents

    async def answer(self, turn: AssistantTurn) -> AssistantAnswer:
        agent = await self._agents.get_agent(AGENT_NAME)
        if agent is None:
            raise AssistantFailedError("no_agent", AGENT_NAME)
        task = AgentTask(
            org_id=turn.org_id,
            run_id=turn.run_id,
            task_id=f"chat_{turn.conversation_id}",
            input={
                "message": turn.message,
                "run_id": turn.run_id,
                "asker": {"user_id": turn.user_id, "role": turn.role},
            },
        )
        return answer_from(await self._runner.run(agent, task))
