"""Tests for StreamingReply throttling and overflow."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import TelegramError

from telegram_client import StreamingReply, TelegramClient


def _mock_telegram() -> MagicMock:
    tg = MagicMock(spec=TelegramClient)
    tg.send_plain = AsyncMock(
        side_effect=lambda chat_id, text: MagicMock(message_id=100 + len(text) % 50)
    )
    tg.edit_message = AsyncMock(return_value=None)
    return tg


@pytest.mark.asyncio
async def test_streaming_reply_accumulates_and_finishes():
    tg = _mock_telegram()
    stream = StreamingReply(tg, chat_id=1, throttle_seconds=0.0)
    await stream.start(placeholder="…")

    accumulated = ""
    for chunk in ["Hello", " world", "!"]:
        accumulated += chunk
        await stream.update(accumulated)
    await stream.finish(accumulated)

    # finish always edits with full text
    assert accumulated == "Hello world!"
    edit_texts = [c.args[2] for c in tg.edit_message.await_args_list]
    assert edit_texts[-1] == "Hello world!"


@pytest.mark.asyncio
async def test_throttling_batches_rapid_updates():
    tg = _mock_telegram()
    stream = StreamingReply(tg, chat_id=1, throttle_seconds=1.0)
    await stream.start(placeholder="…")
    # Reset edit count baseline after start
    tg.edit_message.reset_mock()

    times = [0.0, 0.1, 0.2, 0.3, 1.5]

    def fake_monotonic():
        return times.pop(0) if times else 10.0

    with patch("telegram_client.time.monotonic", side_effect=fake_monotonic):
        await stream.update("a")
        await stream.update("ab")
        await stream.update("abc")
        # still within throttle for first three if start set last_edit
        # Force a late update past throttle
        await stream.update("abcd")

    # Only updates that clear the throttle window should edit
    assert tg.edit_message.await_count <= 2
    await stream.finish("abcd")
    assert tg.edit_message.await_args.args[2] == "abcd"


@pytest.mark.asyncio
async def test_overflow_starts_new_message():
    tg = _mock_telegram()
    stream = StreamingReply(tg, chat_id=1, throttle_seconds=0.0, max_chars=20)
    await stream.start(placeholder="…")
    tg.send_plain.reset_mock()

    long_text = "x" * 45
    await stream.finish(long_text)

    # Should have spilled into at least one new send_plain
    assert tg.send_plain.await_count >= 1


@pytest.mark.asyncio
async def test_message_not_modified_is_swallowed():
    tg = _mock_telegram()
    tg.edit_message = AsyncMock(
        side_effect=TelegramError("Message is not modified")
    )
    # Bypass TelegramClient.edit_message swallow — call StreamingReply path
    # that catches TelegramError from bot layer. Simulate raise from edit_message.
    stream = StreamingReply(tg, chat_id=1, throttle_seconds=0.0)
    stream.message_id = 42
    # _safe_edit catches TelegramError only if edit_message raises it
    # Our TelegramClient.edit_message swallows "not modified" — test StreamingReply
    # path when underlying edit raises for other reasons vs not modified.

    # Directly exercise _safe_edit with a raising client
    async def raising_edit(chat_id, message_id, text, reply_markup=None):
        raise TelegramError("Message is not modified")

    tg.edit_message = raising_edit
    # Should not raise
    await stream._safe_edit("same text")


@pytest.mark.asyncio
async def test_genuine_edit_error_logs_warning_not_crash(caplog):
    import logging

    tg = _mock_telegram()

    async def boom(chat_id, message_id, text, reply_markup=None):
        raise TelegramError("Flood control exceeded")

    tg.edit_message = boom
    stream = StreamingReply(tg, chat_id=1, throttle_seconds=0.0)
    stream.message_id = 7

    with caplog.at_level(logging.WARNING):
        await stream._safe_edit("hello")

    assert any("Telegram edit failed" in r.message for r in caplog.records)
