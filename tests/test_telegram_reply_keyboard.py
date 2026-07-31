"""Tests for ReplyKeyboardMarkup helpers (not inline / callback_query)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove

from telegram_client import TelegramClient


def _client() -> TelegramClient:
    tg = TelegramClient.__new__(TelegramClient)
    tg.allowed_user_id = 1
    tg.bot = MagicMock()
    tg.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    return tg


@pytest.mark.asyncio
async def test_send_with_reply_keyboard_markup():
    tg = _client()
    await tg.send_with_reply_keyboard(42, "Confirm?", ["yes", "no"])

    tg.bot.send_message.assert_awaited_once()
    kwargs = tg.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 42
    assert kwargs["text"] == "Confirm?"
    markup = kwargs["reply_markup"]
    assert isinstance(markup, ReplyKeyboardMarkup)
    data = markup.to_dict()
    assert data["resize_keyboard"] is True
    assert data["one_time_keyboard"] is True
    assert data["keyboard"] == [
        [{"text": "yes"}, {"text": "no"}],
    ]


@pytest.mark.asyncio
async def test_remove_reply_keyboard_markup():
    tg = _client()
    await tg.remove_reply_keyboard(7, "❌ Cancelled")

    tg.bot.send_message.assert_awaited_once()
    kwargs = tg.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 7
    assert kwargs["text"] == "❌ Cancelled"
    markup = kwargs["reply_markup"]
    assert isinstance(markup, ReplyKeyboardRemove)
    assert markup.to_dict() == {"remove_keyboard": True}
