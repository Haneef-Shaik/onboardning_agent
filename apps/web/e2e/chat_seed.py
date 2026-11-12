"""The run assistant for the Playwright tests (UI-7): the real `ChatService` (proposal rules, confirm,
redaction, storage) over the in-memory store, with a scripted `RunAssistant` instead of a model, so no
model call is made. It answers from the run's real tasks: a question names the first failed task as its
evidence, and a message containing "retry" also proposes retrying it. Test-only; synthetic data.
"""

from __future__ import annotations

from onb_agents.runs import RunStore, TaskState
from onb_api.chat.assistant import AssistantAnswer, AssistantProposal, AssistantTurn
from onb_api.chat.service import ChatService
from onb_api.chat.store import ChatStore
from onb_api.runs.control import RunControl
from onb_data import DataStore, tenant_scope


class ScriptedAssistant:
    def __init__(self, runs: RunStore) -> None:
        self._runs = runs

    async def answer(self, turn: AssistantTurn) -> AssistantAnswer:
        with tenant_scope(turn.org_id):
            tasks = await self._runs.tasks(turn.run_id)
        failed = [t["task_id"] for t in tasks if t["status"] == TaskState.FAILED.value]
        if not failed:
            return AssistantAnswer(status="ok", answer="Nothing has failed in this run.")
        first = failed[0]
        answer = f"Task {first} failed, so the run is blocked."
        citations = ((turn.run_id, first),)
        if "retry" in turn.message.lower():
            return AssistantAnswer(
                status="ok",
                answer=f"{answer} I can retry it if you confirm.",
                citations=citations,
                proposal=AssistantProposal(type="retry_task", run_id=turn.run_id, task_id=first),
            )
        return AssistantAnswer(status="ok", answer=answer, citations=citations)


def chat_service(store: DataStore, run_store: RunStore, control: RunControl) -> ChatService:
    return ChatService(store=ChatStore(store), control=control, assistant=ScriptedAssistant(run_store))
