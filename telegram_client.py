"""Telegram Bot API client (python-telegram-bot v20+)."""

from __future__ import annotations

import logging
import time
from typing import Any

from telegram import Bot, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.request import HTTPXRequest

log = logging.getLogger("localops")

TELEGRAM_SAFE_CHARS = 4000


class TelegramClient:
    def __init__(self, bot_token: str, allowed_user_id: int) -> None:
        self.allowed_user_id = allowed_user_id
        request = HTTPXRequest(connect_timeout=30.0, read_timeout=60.0)
        self.bot = Bot(token=bot_token, request=request)
        self._offset: int | None = None

    def is_authorized(self, user_id: int | None) -> bool:
        return user_id is not None and user_id == self.allowed_user_id

    async def poll_updates(self, timeout: int = 30) -> list[Update]:
        try:
            updates = await self.bot.get_updates(
                offset=self._offset,
                timeout=timeout,
                allowed_updates=["message"],
            )
        except TelegramError:
            raise

        if updates:
            self._offset = updates[-1].update_id + 1
        return list(updates)

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str | None = ParseMode.MARKDOWN,
    ) -> Any:
        return await self.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
        )

    async def send_plain(self, chat_id: int, text: str) -> Any:
        return await self.bot.send_message(
            chat_id=chat_id,
            text=text,
            disable_web_page_preview=True,
        )

    async def send_with_reply_keyboard(
        self,
        chat_id: int,
        text: str,
        buttons: list[str],
    ) -> Any:
        """
        Send a message with a ReplyKeyboardMarkup.

        Tapping a button sends its label as a normal chat message
        (same path as typing) — not a callback_query.
        """
        keyboard = [[KeyboardButton(label) for label in buttons]]
        markup = ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        return await self.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )

    async def remove_reply_keyboard(self, chat_id: int, text: str) -> Any:
        """Send a message that clears any active reply keyboard."""
        return await self.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=ReplyKeyboardRemove(),
            disable_web_page_preview=True,
        )

    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: Any | None = None,
    ) -> Any:
        try:
            return await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
        except TelegramError as exc:
            if "not modified" in str(exc).lower():
                return None
            raise

    async def send_unauthorized(self, chat_id: int) -> None:
        await self.send_plain(chat_id, "⛔ Unauthorized")


class StreamingReply:
    """Throttled in-place Telegram message updates for streaming text."""

    def __init__(
        self,
        telegram_client: TelegramClient,
        chat_id: int,
        throttle_seconds: float = 1.0,
        max_chars: int = TELEGRAM_SAFE_CHARS,
    ) -> None:
        self.telegram = telegram_client
        self.chat_id = chat_id
        self.throttle_seconds = throttle_seconds
        self.max_chars = max_chars
        self.message_id: int | None = None
        self._last_edit_at = 0.0
        self._last_sent = ""
        self._edit_count = 0

    async def start(
        self,
        placeholder: str = "…",
        message_id: int | None = None,
    ) -> None:
        if message_id is not None:
            self.message_id = message_id
            await self._safe_edit(placeholder)
        else:
            msg = await self.telegram.send_plain(self.chat_id, placeholder)
            self.message_id = msg.message_id
            self._last_sent = placeholder
        self._last_edit_at = time.monotonic()

    async def update(self, accumulated_text: str) -> None:
        text = accumulated_text if accumulated_text else "…"
        text = await self._spill_overflow(text)

        now = time.monotonic()
        if now - self._last_edit_at < self.throttle_seconds:
            return
        if text == self._last_sent:
            return
        await self._safe_edit(text)
        self._last_edit_at = now
        self._last_sent = text

    async def finish(self, final_text: str) -> None:
        text = final_text if final_text else "…"
        text = await self._spill_overflow(text, force=True)
        if text != self._last_sent:
            await self._safe_edit(text)
            self._last_sent = text
        self._last_edit_at = time.monotonic()

    async def _spill_overflow(self, text: str, force: bool = False) -> str:
        while len(text) > self.max_chars:
            head = text[: self.max_chars]
            rest = text[self.max_chars :]
            await self._safe_edit(head)
            self._last_sent = head
            msg = await self.telegram.send_plain(
                self.chat_id, rest[:1] if rest else "…"
            )
            self.message_id = msg.message_id
            self._last_sent = ""
            self._last_edit_at = 0.0 if force else time.monotonic()
            text = rest
            if force and len(text) <= self.max_chars:
                break
        return text

    async def _safe_edit(self, text: str) -> None:
        if self.message_id is None:
            msg = await self.telegram.send_plain(self.chat_id, text)
            self.message_id = msg.message_id
            self._last_sent = text
            self._edit_count += 1
            return
        try:
            await self.telegram.edit_message(
                self.chat_id, self.message_id, text
            )
            self._edit_count += 1
        except TelegramError as exc:
            msg = str(exc).lower()
            if "not modified" in msg:
                return
            log.warning(
                "Telegram edit failed (chat=%s msg=%s): %s",
                self.chat_id,
                self.message_id,
                exc,
            )
