"""Notification contracts (NOTIF-1): the agent tool's arguments, Telegram's callback shape, callback data."""

import re

import pytest
from onb_contracts.notify import (
    CALLBACK_DATA_PATTERN,
    IN_APP_CONSUMER,
    NOTIFY_SUBSCRIPTIONS,
    Channel,
    NotifySendArgs,
    NotifySendResult,
    TelegramUpdate,
    new_callback_data,
    parse_callback_data,
)
from pydantic import ValidationError

VAULT_REF = "ptk_01JD0000000000000000000000_abcdefghijklmnopqrstuv"


def test_telegram_takes_a_vault_token_and_a_body() -> None:
    args = NotifySendArgs.model_validate({"channel": "telegram", "recipient_token": VAULT_REF, "body": "Hi Priya"})
    assert args.channel is Channel.TELEGRAM
    assert args.recipient_user_id is None


def test_in_app_takes_a_user_id_and_a_body() -> None:
    args = NotifySendArgs.model_validate({"channel": "in_app", "recipient_user_id": "u_acme_hr1", "body": "Done"})
    assert args.channel is Channel.IN_APP


@pytest.mark.parametrize(
    "args",
    [
        {"channel": "telegram", "body": "no recipient"},
        {"channel": "telegram", "recipient_token": VAULT_REF, "recipient_user_id": "u_1", "body": "both"},
        {"channel": "telegram", "recipient_token": "+919000000001", "body": "a raw mobile, not a token"},
        {"channel": "in_app", "body": "no recipient"},
        {"channel": "in_app", "recipient_user_id": "u_1", "recipient_token": VAULT_REF, "body": "both"},
        {"channel": "email", "recipient_token": VAULT_REF, "body": "no such channel in the MVP"},
        {"channel": "telegram", "recipient_token": VAULT_REF, "body": ""},
        {"channel": "telegram", "recipient_token": VAULT_REF, "body": "x" * 4001},
        {"channel": "telegram", "recipient_token": VAULT_REF, "body": "hi", "org_id": "org_b"},
        {"channel": "telegram", "recipient_token": VAULT_REF, "body": "hi", "buttons": [{"text": "Approve"}]},
    ],
    ids=[
        "telegram-no-recipient",
        "telegram-two-recipients",
        "telegram-raw-mobile",
        "in-app-no-recipient",
        "in-app-two-recipients",
        "email",
        "empty-body",
        "long-body",
        "org-id",
        "agent-made-buttons",
    ],
)
def test_bad_arguments_are_refused(args: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        NotifySendArgs.model_validate(args)


def test_the_result_names_the_notification_only() -> None:
    result = NotifySendResult(status="sent", channel=Channel.TELEGRAM, notification_id="n1")
    assert result.model_dump(mode="json") == {
        "status": "sent",
        "channel": "telegram",
        "notification_id": "n1",
        "replayed": False,
    }


def test_a_callback_query_update_parses_and_ignores_fields_telegram_adds() -> None:
    update = TelegramUpdate.model_validate(
        {
            "update_id": 10000,
            "callback_query": {
                "id": "4382bfdwdsb323b2d9",
                "from": {"id": 1111111, "is_bot": False, "first_name": "Test", "language_code": "en"},
                "chat_instance": "-8888888888888888888",
                "data": "ob1:" + "A" * 22,
                "a_field_from_a_later_bot_api": True,
            },
        }
    )
    assert update.callback_query is not None
    assert update.callback_query.from_.id == 1111111
    assert update.callback_query.message is None


def test_an_update_without_a_callback_query_parses() -> None:
    assert TelegramUpdate.model_validate({"update_id": 1, "message": {"text": "hi"}}).callback_query is None


def test_a_callback_query_needs_its_required_fields() -> None:
    with pytest.raises(ValidationError):
        TelegramUpdate.model_validate({"update_id": 1, "callback_query": {"id": "x", "chat_instance": "1"}})


def test_callback_data_is_opaque_random_and_fits_telegrams_64_bytes() -> None:
    first, second = new_callback_data(), new_callback_data()
    assert first != second
    assert re.fullmatch(CALLBACK_DATA_PATTERN, first)
    assert len(first.encode()) <= 64
    assert parse_callback_data(first) == first


@pytest.mark.parametrize("data", [None, "", "approve", "ob1:short", "ob2:" + "A" * 22, "ob1:" + "A" * 22 + "x"])
def test_foreign_callback_data_is_not_ours(data: str | None) -> None:
    assert parse_callback_data(data) is None


def test_the_in_app_consumer_is_subscribed_to_its_events() -> None:
    assert NOTIFY_SUBSCRIPTIONS == {
        "change_request.decided": [IN_APP_CONSUMER],
        "run.blocked": [IN_APP_CONSUMER],
        "task.sink_rejected": ["orchestrator.fail_task"],  # RUN-3: the orchestrator fails the task
    }
