"""`GET /mock-outbox` and `POST /webhooks/telegram` (NOTIF-1 box 3; PRD FR-N2, §10 Mock channels).

Box 3: the webhook rejects a wrong secret token, and accepts a payload matching Telegram's documented
`callback_query` (fixture: tests/fixtures/telegram_callback_query_update.json, a Bot API `Update`).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from onb_api.testing import WEBHOOK_SECRET, RecordedDecisions, bearer, token_for
from onb_api.testing_mongo import MemoryDatabase
from onb_contracts.auth import Role
from onb_contracts.notify import TELEGRAM_AUTH_HEADER, new_callback_data

FIXTURE = Path(__file__).parent / "fixtures" / "telegram_callback_query_update.json"
TELEGRAM_HEADER = TELEGRAM_AUTH_HEADER
A, B = "org_acme", "org_globex"


def seed_message(
    db: MemoryDatabase, org: str, *, audience: str | None = "u_acme_mgr_ravi", sent_minutes_ago: int = 0
) -> dict[str, str]:
    """An outbox message with Approve/Reject buttons, as the notify sink stores it, and its directory
    entries. Returns {decision: callback data}."""
    buttons = {"approve": new_callback_data(), "reject": new_callback_data()}
    db["mock_outbox"].documents.append(
        {
            "_id": f"m_{org}_{buttons['approve'][-6:]}",
            "org_id": org,
            "channel": "telegram",
            "recipient": {"token": "ptk_x", "masked": "98XXXXXX34"},
            "text": "Approve the laptop for the new hire?",
            "buttons": [
                {"text": "Approve", "callback_data": buttons["approve"], "decision": "approve"},
                {"text": "Reject", "callback_data": buttons["reject"], "decision": "reject"},
            ],
            "ref": {"type": "approval", "id": f"ap_{org}_1"},
            "audience_user_id": audience,
            "run_id": "run_1",
            "task_id": "manager_approval",
            "agent": None,
            "message_id": 42,
            "sent_at": datetime.now(UTC) - timedelta(minutes=sent_minutes_ago),
        }
    )
    at = datetime.now(UTC)
    db["telegram_callbacks"].documents.extend(
        {"_id": data, "org_id": org, "created_at": at} for data in buttons.values()
    )
    return buttons


def update_for(data: str | None, *, query_id: str = "3143857237649841312") -> dict[str, Any]:
    update: dict[str, Any] = json.loads(FIXTURE.read_text())
    update["callback_query"]["id"] = query_id
    if data is None:
        del update["callback_query"]["data"]
    else:
        update["callback_query"]["data"] = data
        update["callback_query"]["message"]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] = data
    return update


def post(client: TestClient, body: Any, secret: str | None = WEBHOOK_SECRET) -> Any:
    headers = {TELEGRAM_HEADER: secret} if secret is not None else {}
    return client.post("/webhooks/telegram", json=body, headers=headers)


# --- box 3 --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [None, "", "wrong-secret-but-long-enough-0123456789abcdef", WEBHOOK_SECRET[:-1]],
    ids=["missing", "empty", "another-secret", "one-character-short"],
)
def test_box3_the_webhook_rejects_a_wrong_secret_token(
    client: TestClient, notify_db: MemoryDatabase, decisions: RecordedDecisions, secret: str | None
) -> None:
    buttons = seed_message(notify_db, A)
    response = post(client, update_for(buttons["approve"]), secret)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert decisions.published == []


def test_box3_a_wrong_secret_is_refused_before_the_body_is_read(client: TestClient) -> None:
    assert post(client, {"not": "an update"}, "wrong-secret-but-long-enough-0123456789abcdef").status_code == 401


def test_box3_the_webhook_accepts_telegrams_documented_callback_query_and_emits_a_decision_event(
    client: TestClient, notify_db: MemoryDatabase, decisions: RecordedDecisions
) -> None:
    buttons = seed_message(notify_db, A)
    response = post(client, update_for(buttons["approve"]))
    assert response.status_code == 200
    assert response.json()["data"] == {"ok": True}
    [(org, key, payload)] = decisions.published
    assert (org, key) == (A, "telegram:callback:3143857237649841312")
    assert payload == {
        "ref_type": "approval",
        "ref_id": "ap_org_acme_1",
        "decision": "approve",
        "channel": "telegram",
        "outbox_id": notify_db.raw("mock_outbox")[0]["_id"],
        "telegram_user_id": 731957001,
        "callback_query_id": "3143857237649841312",
    }


def test_the_decision_comes_from_the_stored_button_not_the_request(
    client: TestClient, notify_db: MemoryDatabase, decisions: RecordedDecisions
) -> None:
    buttons = seed_message(notify_db, A)
    body = update_for(buttons["reject"])
    body["callback_query"]["message"]["reply_markup"]["inline_keyboard"][0][0]["text"] = "Approve"
    post(client, body)
    assert [p["decision"] for _, _, p in decisions.published] == ["reject"]


def test_telegrams_retry_of_one_press_publishes_once(
    client: TestClient, notify_db: MemoryDatabase, decisions: RecordedDecisions
) -> None:
    buttons = seed_message(notify_db, A)
    assert post(client, update_for(buttons["approve"])).status_code == 200
    assert post(client, update_for(buttons["approve"])).status_code == 200
    assert len(decisions.published) == 1
    assert decisions.duplicates == 1


def test_a_press_on_an_org_b_button_decides_in_org_b_only(
    client: TestClient, notify_db: MemoryDatabase, decisions: RecordedDecisions
) -> None:
    seed_message(notify_db, A)
    b_buttons = seed_message(notify_db, B, audience="u_globex_mgr")
    post(client, update_for(b_buttons["approve"]))
    assert [(org, p["ref_id"]) for org, _, p in decisions.published] == [(B, "ap_org_globex_1")]


@pytest.mark.parametrize(
    "body",
    [
        {"update_id": 1, "message": {"message_id": 1, "date": 1, "chat": {"id": 1, "type": "private"}, "text": "hi"}},
        "no-data",
        "unknown-button",
        "foreign-data",
        "directory-without-message",
    ],
    ids=["not-a-callback", "no-data", "unknown-button", "foreign-data", "directory-without-message"],
)
def test_every_other_update_is_acknowledged_the_same_way_and_publishes_nothing(
    client: TestClient, notify_db: MemoryDatabase, decisions: RecordedDecisions, body: Any
) -> None:
    buttons = seed_message(notify_db, A)
    known = post(client, update_for(buttons["approve"], query_id="1")).json()
    decisions.published.clear()
    if body == "no-data":
        body = update_for(None)
    elif body == "unknown-button":
        body = update_for(new_callback_data())
    elif body == "foreign-data":
        body = update_for("approve:ap_org_acme_1")
    elif body == "directory-without-message":
        orphan = new_callback_data()
        notify_db["telegram_callbacks"].documents.append({"_id": orphan, "org_id": A, "created_at": datetime.now(UTC)})
        body = update_for(orphan)
    response = post(client, body)
    assert response.status_code == 200
    assert {k: v for k, v in response.json().items() if k != "meta"} == {k: v for k, v in known.items() if k != "meta"}
    assert decisions.published == []


def test_a_malformed_update_with_the_right_secret_is_a_validation_error(client: TestClient) -> None:
    response = post(client, {"update_id": "not-a-number", "callback_query": {"id": "1"}})
    assert response.status_code == 422


def test_the_webhook_needs_no_user_token_and_ignores_one(
    client: TestClient, notify_db: MemoryDatabase, decisions: RecordedDecisions
) -> None:
    b_buttons = seed_message(notify_db, B, audience="u_globex_mgr")
    headers = {TELEGRAM_HEADER: WEBHOOK_SECRET, **bearer(token_for("u_acme_hr1", A, Role.HR))}
    client.post("/webhooks/telegram", json=update_for(b_buttons["approve"]), headers=headers)
    assert [org for org, _, _ in decisions.published] == [B]  # the token's org changes nothing


# --- GET /mock-outbox --------------------------------------------------------------------------------


def outbox(client: TestClient, user: str, role: Role, org: str = A, **params: Any) -> Any:
    return client.get("/mock-outbox", params=params, headers=bearer(token_for(user, org, role)))


def test_hr_sees_the_orgs_outbox_masked_newest_first(client: TestClient, notify_db: MemoryDatabase) -> None:
    seed_message(notify_db, A, sent_minutes_ago=5)
    newest = seed_message(notify_db, A, audience=None)
    seed_message(notify_db, B)
    response = outbox(client, "u_acme_hr1", Role.HR)
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["page"] == {"total": 2, "page": 1, "limit": 50}
    [first, second] = body["data"]
    assert first["buttons"][0]["callback_data"] == newest["approve"]
    assert first["recipient_masked"] == "98XXXXXX34"
    assert set(first) == {
        "id",
        "channel",
        "recipient_masked",
        "text",
        "buttons",
        "run_id",
        "task_id",
        "message_id",
        "sent_at",
    }
    assert second["sent_at"] < first["sent_at"]


def test_a_manager_sees_only_messages_addressed_to_them(client: TestClient, notify_db: MemoryDatabase) -> None:
    mine = seed_message(notify_db, A, audience="u_acme_mgr_ravi")
    seed_message(notify_db, A, audience="u_acme_mgr_other")
    seed_message(notify_db, A, audience=None)
    data = outbox(client, "u_acme_mgr_ravi", Role.MANAGER).json()["data"]
    assert [m["buttons"][0]["callback_data"] for m in data] == [mine["approve"]]


def test_the_outbox_pages(client: TestClient, notify_db: MemoryDatabase) -> None:
    for minutes in range(3):
        seed_message(notify_db, A, sent_minutes_ago=minutes)
    page2 = outbox(client, "u_acme_hradmin", Role.HR_ADMIN, page=2, limit=2).json()
    assert len(page2["data"]) == 1
    assert page2["meta"]["page"] == {"total": 3, "page": 2, "limit": 2}
    assert outbox(client, "u_acme_hradmin", Role.HR_ADMIN, limit=101).status_code == 422
    assert outbox(client, "u_acme_hradmin", Role.HR_ADMIN, page=0).status_code == 422


def test_without_mock_channels_both_routes_answer_503(users: Any, registry: Any, hires: Any) -> None:
    from onb_api import create_app
    from onb_api.testing import TEST_SETTINGS, InMemoryAuditLog

    app = create_app(auth=TEST_SETTINGS, users=users, audit=InMemoryAuditLog(), agents=registry, hires=hires)
    with TestClient(app) as bare:
        assert outbox(bare, "u_acme_hr1", Role.HR).status_code == 503
        assert post(bare, update_for(new_callback_data())).status_code == 503


def test_recorded_decisions_are_per_org() -> None:
    recorded = RecordedDecisions()
    from onb_data import tenant_scope

    async def go() -> None:
        with tenant_scope(A):
            assert await recorded.publish({}, key="k")
        with tenant_scope(B):
            assert await recorded.publish({}, key="k")
        with tenant_scope(A):
            assert not await recorded.publish({}, key="k")

    asyncio.run(go())
    assert len(recorded.published) == 2
