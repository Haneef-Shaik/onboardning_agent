"""The Telegram transport stub NOTIF-1's sink sends through (PRD FR-N1, §7.10): no network, no copy kept."""

from __future__ import annotations

import gc

import pytest
from onb_mcp_mocks.telegram import (
    MAX_CALLBACK_DATA_BYTES,
    MAX_TEXT_CHARS,
    InlineButton,
    SentMessage,
    TelegramMockTransport,
    TelegramTransport,
    TransportRefusedError,
)

from .harness import run

# Synthetic values only (no real person); the stub must never hold on to them.
CHAT = 424242
TEXT = "Hi Asha, your laptop ships on Monday. Reply here if anything is missing."


def _clock() -> int:
    return 1_762_819_200


def test_sending_returns_a_bot_api_shaped_receipt_with_increasing_ids() -> None:
    transport = TelegramMockTransport(clock=_clock)
    first = run(transport.send_message(CHAT, TEXT))
    second = run(transport.send_message(CHAT, TEXT))
    assert first == SentMessage(message_id=1, date=1_762_819_200)
    assert second.message_id == 2
    assert first.to_bot_api() == {"ok": True, "result": {"message_id": 1, "date": 1_762_819_200}}
    assert transport.sent == 2


def test_the_stub_is_a_telegram_transport() -> None:
    transport: TelegramTransport = TelegramMockTransport()
    assert isinstance(transport, TelegramTransport)


def test_the_receipt_and_the_stub_hold_no_copy_of_the_text_or_recipient() -> None:
    """The sink stores a masked copy; the transport keeps nothing that a later read could leak."""
    transport = TelegramMockTransport(clock=_clock)
    receipt = run(transport.send_message(CHAT, TEXT))
    gc.collect()
    held = repr(vars(transport)) + repr(receipt) + repr(receipt.to_bot_api())
    assert all(secret not in held for secret in (TEXT, "Asha", str(CHAT)))


def test_inline_buttons_carry_callback_data_like_the_real_api() -> None:
    transport = TelegramMockTransport(clock=_clock)
    keyboard = [[InlineButton("Approve", "approve:65f0a1b2c3d4e5f6a7b8c9d0"), InlineButton("Reject", "reject:x")]]
    assert run(transport.send_message(CHAT, TEXT, inline_keyboard=keyboard)).message_id == 1


@pytest.mark.parametrize(
    ("text", "data"),
    [("", "approve"), ("Approve", ""), ("Approve", "x" * (MAX_CALLBACK_DATA_BYTES + 1)), ("Approve", "é" * 33)],
)
def test_a_button_outside_the_bot_api_limits_is_refused(text: str, data: str) -> None:
    with pytest.raises(ValueError, match=r"button needs text|callback_data"):
        InlineButton(text, data)


@pytest.mark.parametrize("text", ["", "x" * (MAX_TEXT_CHARS + 1)])
def test_a_message_outside_the_bot_api_limits_is_refused_and_not_counted(text: str) -> None:
    transport = TelegramMockTransport()
    with pytest.raises(TransportRefusedError) as caught:
        run(transport.send_message(CHAT, text))
    assert text[:20] not in str(caught.value) or not text
    assert transport.sent == 0


@pytest.mark.parametrize("chat_id", [0, -1, "", "@", True])
def test_a_bad_chat_id_is_refused_without_echoing_it(chat_id: object) -> None:
    transport = TelegramMockTransport()
    with pytest.raises(TransportRefusedError):
        run(transport.send_message(chat_id, TEXT))  # type: ignore[arg-type]
    assert transport.sent == 0


def test_a_channel_username_is_accepted() -> None:
    assert run(TelegramMockTransport().send_message("@acme_onboarding", TEXT)).message_id == 1
