"""`POST /mock-outbox/{message_id}/press`: the mock outbox's Approve/Reject buttons (UI-6; PRD FR-N2, FR-H2).

The browser never holds `TELEGRAM_WEBHOOK_SECRET` (NOTIF-1's finding for UI-6). It sends the press to this
authenticated route, which checks the message is addressed to the caller and then calls
`POST /webhooks/telegram` in-process with the secret and a Telegram `Update`, the request a real Telegram
callback makes. So the decision takes exactly the path a real press takes (ADR 0026).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from onb_api.notify.relay import RelayError, post_json
from onb_api.notify.service import MockChannels
from onb_api.testing import RecordedDecisions, bearer, token_for
from onb_api.testing_mongo import MemoryDatabase
from onb_contracts.auth import Role
from onb_contracts.notify import TELEGRAM_AUTH_HEADER, new_callback_data
from onb_data import NoTenantContextError, current_tenant, tenant_scope

A, B = "org_acme", "org_globex"
RAVI = "u_acme_mgr_ravi"


def seed_message(db: MemoryDatabase, org: str, *, audience: str | None = RAVI) -> dict[str, str]:
    """An approval's Approve/Reject message as the notify sink stores it, with its directory entries."""
    buttons = {"approve": new_callback_data(), "reject": new_callback_data()}
    db["mock_outbox"].documents.append(
        {
            "_id": f"m_{org}_{buttons['approve'][-6:]}",
            "org_id": org,
            "channel": "telegram",
            "recipient": {"token": None, "masked": f"user:{audience}"},
            "text": "Approval needed: Manager approves equipment & access",
            "buttons": [
                {"text": "Approve", "callback_data": buttons["approve"], "decision": "approve"},
                {"text": "Reject", "callback_data": buttons["reject"], "decision": "reject"},
            ],
            "ref": {"type": "approval", "id": f"ap_{org}_1"},
            "audience_user_id": audience,
            "run_id": "run_1",
            "task_id": "manager_approval",
            "agent": "orchestrator",
            "message_id": 42,
            "sent_at": datetime.now(UTC),
        }
    )
    at = datetime.now(UTC)
    db["telegram_callbacks"].documents.extend({"_id": d, "org_id": org, "created_at": at} for d in buttons.values())
    return buttons


class PathRecorder:
    """ASGI middleware that records every HTTP request's (method, path): proves the webhook route ran."""

    def __init__(self, app: Any, seen: list[tuple[str, str]]) -> None:
        self.app = app
        self.seen = seen

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            self.seen.append((scope["method"], scope["path"]))
        await self.app(scope, receive, send)


@pytest.fixture
def seen() -> list[tuple[str, str]]:
    return []


@pytest.fixture
def recorded_client(app: FastAPI, seen: list[tuple[str, str]]) -> Iterator[TestClient]:
    app.add_middleware(PathRecorder, seen=seen)
    with TestClient(app) as test_client:
        yield test_client


def message_id(db: MemoryDatabase, org: str = A) -> str:
    return next(str(m["_id"]) for m in db.raw("mock_outbox") if m["org_id"] == org)


def press(
    client: TestClient, message: str, data: str, *, user: str = RAVI, role: Role = Role.MANAGER, org: str = A
) -> Any:
    return client.post(
        f"/mock-outbox/{message}/press",
        json={"callback_data": data},
        headers=bearer(token_for(user, org, role)),
    )


def test_pressing_approve_on_my_message_calls_the_telegram_webhook_and_publishes_the_decision(
    recorded_client: TestClient,
    notify_db: MemoryDatabase,
    decisions: RecordedDecisions,
    seen: list[tuple[str, str]],
) -> None:
    buttons = seed_message(notify_db, A)
    outbox_id = message_id(notify_db)
    response = press(recorded_client, outbox_id, buttons["approve"])
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"ok": True}
    assert ("POST", "/webhooks/telegram") in seen  # the same endpoint a real Telegram callback hits
    [(org, key, payload)] = decisions.published
    assert org == A
    assert key.startswith("telegram:callback:")
    assert payload["decision"] == "approve"
    assert payload["channel"] == "telegram"
    assert (payload["ref_type"], payload["ref_id"], payload["outbox_id"]) == ("approval", "ap_org_acme_1", outbox_id)


def test_reject_publishes_reject(client: TestClient, notify_db: MemoryDatabase, decisions: RecordedDecisions) -> None:
    buttons = seed_message(notify_db, A)
    assert press(client, message_id(notify_db), buttons["reject"]).status_code == 200
    assert [p["decision"] for _, _, p in decisions.published] == ["reject"]


@pytest.mark.parametrize(
    ("user", "role"),
    [("u_acme_mgr_other", Role.MANAGER), ("u_acme_hr1", Role.HR), ("u_acme_hradmin", Role.HR_ADMIN)],
    ids=["another-manager", "hr", "hr-admin"],
)
def test_a_message_addressed_to_someone_else_is_404_and_publishes_nothing(
    client: TestClient, notify_db: MemoryDatabase, decisions: RecordedDecisions, user: str, role: Role
) -> None:
    buttons = seed_message(notify_db, A)
    response = press(client, message_id(notify_db), buttons["approve"], user=user, role=role)
    assert response.status_code == 404
    assert decisions.published == []


def test_a_message_without_an_audience_is_404(
    client: TestClient, notify_db: MemoryDatabase, decisions: RecordedDecisions
) -> None:
    buttons = seed_message(notify_db, A, audience=None)
    assert press(client, message_id(notify_db), buttons["approve"]).status_code == 404
    assert decisions.published == []


def test_another_orgs_message_is_404_even_for_its_audience_id(
    client: TestClient, notify_db: MemoryDatabase, decisions: RecordedDecisions
) -> None:
    b_buttons = seed_message(notify_db, B, audience=RAVI)  # same user id string, other org
    response = press(client, message_id(notify_db, B), b_buttons["approve"])
    assert response.status_code == 404
    assert decisions.published == []


def test_a_button_that_is_not_on_that_message_is_404(
    client: TestClient, notify_db: MemoryDatabase, decisions: RecordedDecisions
) -> None:
    seed_message(notify_db, A)
    other = seed_message(notify_db, A)  # another message of mine: its button may not be pressed through this one
    first = str(notify_db.raw("mock_outbox")[0]["_id"])
    assert press(client, first, other["approve"]).status_code == 404
    assert press(client, first, new_callback_data()).status_code == 404
    assert decisions.published == []


@pytest.mark.parametrize("status", ["approved", "rejected", "cancelled"])
def test_a_press_on_an_approval_already_decided_is_409_already_decided(
    client: TestClient, notify_db: MemoryDatabase, decisions: RecordedDecisions, status: str
) -> None:
    buttons = seed_message(notify_db, A)
    notify_db["approvals"].documents.append({"_id": "ap_org_acme_1", "org_id": A, "status": status})
    response = press(client, message_id(notify_db), buttons["approve"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"
    assert "already been decided" in response.json()["error"]["message"]
    assert decisions.published == []


def test_a_pending_approval_is_pressed(
    client: TestClient, notify_db: MemoryDatabase, decisions: RecordedDecisions
) -> None:
    buttons = seed_message(notify_db, A)
    notify_db["approvals"].documents.append({"_id": "ap_org_acme_1", "org_id": A, "status": "pending"})
    assert press(client, message_id(notify_db), buttons["approve"]).status_code == 200
    assert len(decisions.published) == 1


def test_each_press_is_its_own_callback_query(
    client: TestClient, notify_db: MemoryDatabase, decisions: RecordedDecisions
) -> None:
    """Like two taps in Telegram: two callback queries. The first decision still wins downstream (HIL-1)."""
    buttons = seed_message(notify_db, A)
    outbox_id = message_id(notify_db)
    press(client, outbox_id, buttons["approve"])
    press(client, outbox_id, buttons["reject"])
    keys = [key for _, key, _ in decisions.published]
    assert len(keys) == 2
    assert len(set(keys)) == 2


def test_the_press_needs_a_token(client: TestClient, notify_db: MemoryDatabase) -> None:
    buttons = seed_message(notify_db, A)
    response = client.post(f"/mock-outbox/{message_id(notify_db)}/press", json={"callback_data": buttons["approve"]})
    assert response.status_code == 401


@pytest.mark.parametrize("data", ["approve", "ob1:short", "", "x" * 300])
def test_callback_data_that_is_not_a_button_id_is_422(client: TestClient, notify_db: MemoryDatabase, data: str) -> None:
    seed_message(notify_db, A)
    assert press(client, message_id(notify_db), data).status_code == 422


def test_the_outbox_says_who_each_message_is_addressed_to(client: TestClient, notify_db: MemoryDatabase) -> None:
    seed_message(notify_db, A)
    seed_message(notify_db, A, audience=None)
    response = client.get("/mock-outbox", headers=bearer(token_for("u_acme_hr1", A, Role.HR)))
    assert sorted(str(m["audience_user_id"]) for m in response.json()["data"]) == ["None", RAVI]


def test_the_browser_never_receives_the_webhook_secret(client: TestClient, notify_db: MemoryDatabase) -> None:
    from onb_api.testing import WEBHOOK_SECRET

    buttons = seed_message(notify_db, A)
    listed = client.get("/mock-outbox", headers=bearer(token_for(RAVI, A, Role.MANAGER)))
    pressed = press(client, message_id(notify_db), buttons["approve"])
    assert WEBHOOK_SECRET not in listed.text
    assert WEBHOOK_SECRET not in pressed.text


def test_a_webhook_that_refuses_the_relay_is_503_and_publishes_nothing(
    client: TestClient,
    notify: MockChannels,
    notify_db: MemoryDatabase,
    decisions: RecordedDecisions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(notify, "webhook_headers", lambda: {TELEGRAM_AUTH_HEADER: "w" * 40})  # a wrong secret
    buttons = seed_message(notify_db, A)
    response = press(client, message_id(notify_db), buttons["approve"])
    assert response.status_code == 503
    assert decisions.published == []


def test_the_relayed_request_does_not_inherit_the_callers_tenant_scope() -> None:
    """The webhook takes its org from the button directory only; the caller's scope must not leak into it."""
    seen: list[str] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await receive()
        try:
            seen.append(current_tenant().org_id)
        except NoTenantContextError:
            seen.append("none")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def go() -> None:
        with tenant_scope(A):
            await post_json(app, "/webhooks/telegram", {"x": 1}, headers={})

    asyncio.run(go())
    assert seen == ["none"]


def test_a_relay_that_answers_an_error_raises() -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 401, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    with pytest.raises(RelayError, match="401"):
        asyncio.run(post_json(app, "/webhooks/telegram", {}, headers={}))
