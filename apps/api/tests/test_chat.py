"""The Run Assistant chat over HTTP (CHAT-1; PRD FR-C1, FR-C2, U6, §7.10).

The app is the unit-test app (conftest): seed_v1's hires in an in-memory database, runs through the outbox
stand-in, and a scripted assistant (`StubAssistant`) in place of the agent, whose own behaviour the eval
cases ra-001…ra-007 cover. What the orchestrator does to a run (failing a task, blocking the run) is done
straight on the store. Boxes: 2 a proposed retry runs only after the confirm call; 3 a manager asking about
a run outside their reports is refused; 4 stored messages hold redaction markers, not raw PII.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from onb_agents.runs import RunState, RunStore, TaskState
from onb_api.chat import AssistantAnswer, AssistantProposal, AssistantTurn
from onb_api.chat.testing import StubAssistant
from onb_api.testing import InMemoryAuditLog, bearer, token_for
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_contracts.auth import Role
from onb_data import AuditQuery, tenant_scope

SEED_TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "engineer-india.yaml"
ADMIN = ("u_acme_hradmin", "org_acme", Role.HR_ADMIN)
HR = ("u_acme_hr1", "org_acme", Role.HR)
RAVI = ("u_acme_mgr_ravi", "org_acme", Role.MANAGER)
ANITA = ("u_acme_mgr_anita", "org_acme", Role.MANAGER)
GLOBEX_HR = ("u_globex_hr1", "org_globex", Role.HR)
NOW = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
FAILED = "collect_docs"
RAW_PAN = "ABCDE1234F"
RAW_EMAIL = "someone.private@example.com"


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def _auth(who: tuple[str, str, Role]) -> dict[str, str]:
    return bearer(token_for(*who))


def _publish(client: TestClient) -> None:
    template: dict[str, Any] = yaml.safe_load(SEED_TEMPLATE.read_text())
    draft = client.post("/templates/drafts", json={"template": template}, headers=_auth(ADMIN))
    published = client.post(f"/templates/drafts/{draft.json()['data']['id']}/publish", headers=_auth(ADMIN))
    assert published.status_code == 200, published.text


def _start(client: TestClient, hire: str = "h_acme_001") -> str:
    body = {"hire_id": hire, "template_key": "engineer-india", "template_version": 1}
    response = client.post("/runs", json=body, headers=_auth(HR))
    assert response.status_code == 201, response.text
    run_id: str = response.json()["data"]["id"]
    return run_id


def _block(memory_db: MemoryDatabase, run_id: str, task_id: str = FAILED) -> None:
    """The run is running, and the task ran and failed its last attempt: the run is blocked."""

    async def work() -> None:
        runs = RunStore(memory_store(memory_db))
        with tenant_scope("org_acme"):
            await runs.move_run(run_id, RunState.RUNNING, at=NOW)
            ready = await runs.move_task(run_id, task_id, TaskState.READY, at=NOW)
            running = await runs.move_task(run_id, task_id, TaskState.RUNNING, at=NOW, expected=ready)
            await runs.move_task(
                run_id, task_id, TaskState.FAILED, at=NOW, expected=running, fields={"error": "StubTaskError"}
            )
            await runs.move_run(run_id, RunState.BLOCKED, at=NOW)

    run(work())


def _task_status(memory_db: MemoryDatabase, run_id: str, task_id: str = FAILED) -> str:
    async def work() -> str:
        with tenant_scope("org_acme"):
            return str((await RunStore(memory_store(memory_db)).task(run_id, task_id) or {})["status"])

    return run(work())


def _events(response: Any) -> list[tuple[str, dict[str, Any]]]:
    found = []
    for block in response.text.strip().split("\n\n"):
        name, data = block.split("\n", 1)
        found.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return found


def _ask(client: TestClient, run_id: str, message: str, who: tuple[str, str, Role] = HR) -> Any:
    return client.post(f"/runs/{run_id}/chat", json={"message": message}, headers=_auth(who))


def _offering(proposed: AssistantProposal) -> Any:
    def reply(_: AssistantTurn) -> AssistantAnswer:
        return AssistantAnswer(status="ok", answer="Shall I?", proposal=proposed)

    return reply


def _propose_retry(run_id: str, task_id: str = FAILED) -> Any:
    def reply(_: AssistantTurn) -> AssistantAnswer:
        return AssistantAnswer(
            status="ok",
            answer="I can retry that task if you confirm.",
            citations=((run_id, task_id),),
            proposal=AssistantProposal(type="retry_task", run_id=run_id, task_id=task_id),
        )

    return reply


@pytest.fixture
def blocked_run(client: TestClient, memory_db: MemoryDatabase) -> str:
    _publish(client)
    run_id = _start(client)
    _block(memory_db, run_id)
    return run_id


def _reply_message(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    events = _events(response)
    assert [name for name, _ in events] == ["message", "end"]
    message: dict[str, Any] = events[0][1]["message"]
    return message


# --- asking and reading back ------------------------------------------------------------------------


def test_chat_answers_over_sse_cites_a_real_task_and_keeps_the_conversation(
    client: TestClient, assistant: StubAssistant, blocked_run: str
) -> None:
    assistant.reply = lambda _: AssistantAnswer(
        status="ok",
        answer="collect_docs failed.",
        citations=((blocked_run, FAILED), (blocked_run, "nope"), ("run_x", FAILED)),
    )
    response = _ask(client, blocked_run, "Why is this stuck?")
    assert response.headers["content-type"].startswith("text/event-stream")
    message = _reply_message(response)
    assert message["role"] == "assistant"
    assert message["citations"] == [{"run_id": blocked_run, "task_id": FAILED}]  # invented ones are dropped
    assert (assistant.turns[0].run_id, assistant.turns[0].role) == (blocked_run, "HR")

    history = client.get(f"/runs/{blocked_run}/chat", headers=_auth(HR)).json()["data"]
    assert [(m["role"], m["content"]) for m in history] == [
        ("user", "Why is this stuck?"),
        ("assistant", "collect_docs failed."),
    ]
    assert client.get(f"/runs/{blocked_run}/chat", headers=_auth(ADMIN)).json()["data"] == []  # one's own chat only


def test_an_assistant_that_fails_is_a_503_and_not_an_answer(
    client: TestClient, assistant: StubAssistant, blocked_run: str
) -> None:
    def boom(_: AssistantTurn) -> AssistantAnswer:
        raise RuntimeError("model down")

    assistant.reply = boom
    response = _ask(client, blocked_run, "hello")
    assert response.status_code == 503
    assert "model down" not in response.text
    stored = client.get(f"/runs/{blocked_run}/chat", headers=_auth(HR)).json()["data"]
    assert [m["role"] for m in stored] == ["user"]


# --- box 2: a proposed retry runs only after the confirm call -------------------------------------------


def test_a_proposed_retry_changes_nothing_until_the_confirm_call(
    client: TestClient,
    assistant: StubAssistant,
    memory_db: MemoryDatabase,
    audit_log: InMemoryAuditLog,
    blocked_run: str,
) -> None:
    assistant.reply = _propose_retry(blocked_run)
    proposal = _reply_message(_ask(client, blocked_run, "Retry the failed task."))["proposal"]
    assert proposal["status"] == "pending"
    assert (proposal["type"], proposal["task_id"], proposal["requires_confirmation"]) == ("retry_task", FAILED, True)
    # Asked, and even asked again: the task is still failed and no retry was audited.
    _ask(client, blocked_run, "Yes, do it. I confirm.")
    assert _task_status(memory_db, blocked_run) == "failed"
    assert not run(_audited(audit_log, "run.task.retry"))

    confirmed = client.post(
        f"/runs/{blocked_run}/chat/confirm", json={"proposal_id": proposal["proposal_id"]}, headers=_auth(HR)
    )
    assert confirmed.status_code == 200, confirmed.text
    assert _task_status(memory_db, blocked_run) == "ready"
    retries = run(_audited(audit_log, "run.task.retry"))
    assert len(retries) == 1
    assert retries[0].actor.id == "u_acme_hr1"
    history = client.get(f"/runs/{blocked_run}/chat", headers=_auth(HR)).json()["data"]
    assert history[1]["proposal"]["status"] == "confirmed"


async def _audited(audit_log: InMemoryAuditLog, action: str) -> list[Any]:
    with tenant_scope("org_acme"):
        return list((await audit_log.list(AuditQuery(action=action), page=1, limit=20)).entries)


def test_a_proposal_runs_once_and_only_for_the_user_it_was_offered_to(
    client: TestClient, assistant: StubAssistant, memory_db: MemoryDatabase, blocked_run: str
) -> None:
    assistant.reply = _propose_retry(blocked_run)
    proposal_id = _reply_message(_ask(client, blocked_run, "Retry it"))["proposal"]["proposal_id"]
    body = {"proposal_id": proposal_id}
    path = f"/runs/{blocked_run}/chat/confirm"

    assert client.post(path, json=body, headers=_auth(ADMIN)).status_code == 404  # another user's proposal
    assert _task_status(memory_db, blocked_run) == "failed"
    assert client.post(path, json=body, headers=_auth(HR)).status_code == 200
    assert client.post(path, json=body, headers=_auth(HR)).status_code == 409  # not twice
    ghost = {"proposal_id": "prop_" + "0" * 24}
    assert client.post(path, json=ghost, headers=_auth(HR)).status_code == 404
    assert client.post(path, json={"proposal_id": "not-a-proposal"}, headers=_auth(HR)).status_code == 422


def test_an_expired_proposal_is_refused_and_changes_nothing(
    client: TestClient, assistant: StubAssistant, memory_db: MemoryDatabase, chat: Any, blocked_run: str
) -> None:
    assistant.reply = _propose_retry(blocked_run)
    message = _reply_message(_ask(client, blocked_run, "Retry it"))
    later = datetime.now(UTC) + timedelta(hours=1)
    chat._clock = lambda: later
    response = client.post(
        f"/runs/{blocked_run}/chat/confirm", json={"proposal_id": message["proposal"]["proposal_id"]}, headers=_auth(HR)
    )
    assert response.status_code == 409
    assert _task_status(memory_db, blocked_run) == "failed"


def test_a_confirmed_retry_the_run_no_longer_allows_is_409_and_cannot_be_replayed(
    client: TestClient, assistant: StubAssistant, memory_db: MemoryDatabase, blocked_run: str
) -> None:
    assistant.reply = _propose_retry(blocked_run)
    proposal = _reply_message(_ask(client, blocked_run, "Retry it"))["proposal"]
    assert client.post(f"/runs/{blocked_run}/cancel", json={}, headers=_auth(HR)).status_code == 200
    path = f"/runs/{blocked_run}/chat/confirm"
    body = {"proposal_id": proposal["proposal_id"]}
    assert client.post(path, json=body, headers=_auth(HR)).status_code == 409
    assert client.post(path, json=body, headers=_auth(HR)).status_code == 409
    history = client.get(f"/runs/{blocked_run}/chat", headers=_auth(HR)).json()["data"]
    assert history[1]["proposal"]["status"] == "failed"


@pytest.mark.parametrize("proposal_task", ["welcome_message", "no_such_task"])
def test_a_proposal_for_a_task_that_is_not_failed_is_not_offered(
    client: TestClient, assistant: StubAssistant, blocked_run: str, proposal_task: str
) -> None:
    assistant.reply = _propose_retry(blocked_run, proposal_task)
    assert _reply_message(_ask(client, blocked_run, "Retry it"))["proposal"] is None


def test_a_proposal_naming_another_run_or_action_is_not_offered(
    client: TestClient, assistant: StubAssistant, blocked_run: str
) -> None:
    for proposed in (
        AssistantProposal(type="retry_task", run_id="run_" + "f" * 24, task_id=FAILED),
        AssistantProposal(type="cancel_run", run_id=blocked_run, task_id=None),
        AssistantProposal(type="retry_task", run_id=blocked_run, task_id=None),
    ):
        assistant.reply = _offering(proposed)
        assert _reply_message(_ask(client, blocked_run, "do it"))["proposal"] is None


def test_a_refused_answer_carries_no_proposal(client: TestClient, assistant: StubAssistant, blocked_run: str) -> None:
    assistant.reply = lambda _: AssistantAnswer(
        status="refused",
        answer="No.",
        proposal=AssistantProposal(type="retry_task", run_id=blocked_run, task_id=FAILED),
    )
    assert _reply_message(_ask(client, blocked_run, "do it"))["proposal"] is None


def test_confirming_is_for_hr_and_hr_admin_only(client: TestClient, blocked_run: str) -> None:
    response = client.post(
        f"/runs/{blocked_run}/chat/confirm", json={"proposal_id": "prop_" + "0" * 24}, headers=_auth(RAVI)
    )
    assert response.status_code == 403


# --- box 3: a manager asking about a run outside their reports is refused ---------------------------------


def test_a_manager_asking_about_a_run_outside_their_reports_is_refused_before_the_assistant_runs(
    client: TestClient, assistant: StubAssistant, blocked_run: str
) -> None:
    # h_acme_001 reports to Ravi, not Anita.
    for response in (
        _ask(client, blocked_run, "What's the status of Priya's onboarding?", ANITA),
        client.get(f"/runs/{blocked_run}/chat", headers=_auth(ANITA)),
        client.post(
            f"/runs/{blocked_run}/chat/confirm", json={"proposal_id": "prop_" + "0" * 24}, headers=_auth(ANITA)
        ),
    ):
        assert response.status_code in (403, 404), response.text
    assert _ask(client, blocked_run, "status?", ANITA).status_code == 404  # like a run nobody has
    assert assistant.turns == []
    assert client.get(f"/runs/{blocked_run}/chat", headers=_auth(RAVI)).json()["data"] == []  # nothing was stored


def test_a_manager_may_ask_about_a_report_but_is_offered_no_retry(
    client: TestClient, assistant: StubAssistant, blocked_run: str
) -> None:
    assistant.reply = _propose_retry(blocked_run)
    message = _reply_message(_ask(client, blocked_run, "Why is it stuck?", RAVI))
    assert message["proposal"] is None
    assert message["citations"] == [{"run_id": blocked_run, "task_id": FAILED}]


def test_another_orgs_run_is_404_for_every_chat_route(
    client: TestClient, assistant: StubAssistant, blocked_run: str
) -> None:
    assert _ask(client, blocked_run, "hi", GLOBEX_HR).status_code == 404
    assert client.get(f"/runs/{blocked_run}/chat", headers=_auth(GLOBEX_HR)).status_code == 404
    assert assistant.turns == []


# --- box 4: stored messages hold redaction markers, not raw PII -------------------------------------------


def test_stored_messages_and_what_the_assistant_sees_hold_markers_not_raw_values(
    client: TestClient, assistant: StubAssistant, memory_db: MemoryDatabase, blocked_run: str
) -> None:
    assistant.reply = lambda _: AssistantAnswer(status="ok", answer=f"Her PAN is {RAW_PAN}, write to {RAW_EMAIL}.")
    reply = _reply_message(_ask(client, blocked_run, f"My PAN is {RAW_PAN} and my email is {RAW_EMAIL}"))
    assert RAW_PAN not in reply["content"]
    assert RAW_EMAIL not in reply["content"]
    assert "[" in reply["content"]
    assert RAW_PAN not in assistant.turns[0].message
    assert RAW_EMAIL not in assistant.turns[0].message

    history = client.get(f"/runs/{blocked_run}/chat", headers=_auth(HR))
    assert RAW_PAN not in history.text
    assert RAW_EMAIL not in history.text
    user_message = history.json()["data"][0]["content"]
    assert user_message.count("[") >= 2  # a typed marker for each value
    stored = json.dumps(memory_db["messages"].documents, default=str)
    assert RAW_PAN not in stored
    assert RAW_EMAIL not in stored
    assert memory_db["messages"].documents  # the check above is not vacuous


def test_a_proposal_cannot_be_confirmed_through_another_runs_chat_path(
    client: TestClient, assistant: StubAssistant, memory_db: MemoryDatabase, blocked_run: str
) -> None:
    other = _start(client, "h_acme_003")  # Ravi's other report
    _block(memory_db, other)
    assistant.reply = _propose_retry(blocked_run)
    proposal_id = _reply_message(_ask(client, blocked_run, "Retry it"))["proposal"]["proposal_id"]
    response = client.post(f"/runs/{other}/chat/confirm", json={"proposal_id": proposal_id}, headers=_auth(HR))
    assert response.status_code == 404
    assert _task_status(memory_db, blocked_run) == "failed"
    assert _task_status(memory_db, other) == "failed"


def test_a_proposal_is_claimed_once_whatever_the_run_allows(memory_db: MemoryDatabase) -> None:
    from onb_api.chat.store import ChatStore

    store = ChatStore(memory_store(memory_db))

    async def work() -> tuple[bool, bool]:
        with tenant_scope("org_acme"):
            conversation = await store.conversation("run_x", "u_acme_hr1", at=NOW)
            proposal = await store.add_proposal(
                conversation, type="retry_task", task_id=FAILED, at=NOW, expires_at=NOW + timedelta(minutes=5)
            )
            return await store.claim(proposal["_id"], at=NOW), await store.claim(proposal["_id"], at=NOW)

    assert run(work()) == (True, False)
