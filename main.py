"""localops — Telegram ↔ LLM (CMD: format) ↔ local shell bridge."""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from collections import deque

from config import load_config_or_exit
from confirmation_gate import (
    CANCELLED_TEXT,
    REPROMPT_TEXT,
    TIMEOUT_TEXT,
    ConfirmationGate,
    format_confirm_prompt,
)
from executor import CommandExecutor, is_cd_command
from explain import EXPLAIN_SYSTEM_PROMPT, build_explain_prompt
from llm_router import build_router
from platform_info import detect_host_platform
from routing import parse_llm_reply
from safety import SafetyChecker
from telegram_client import StreamingReply, TelegramClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("localops")

SEPARATOR = "─" * 23
EMPTY_FALLBACK = "I didn't get a clear response, try rephrasing"


class LocalOpsApp:
    def __init__(self) -> None:
        self.config = load_config_or_exit()
        self.host_platform = detect_host_platform()
        self.telegram = TelegramClient(
            bot_token=self.config.telegram.bot_token,
            allowed_user_id=self.config.telegram.allowed_user_id,
        )
        self.router = build_router(
            self.config.llm,
            self.config.executor.working_directory,
            host_platform=self.host_platform,
        )
        self.safety = SafetyChecker(
            self.config.safety,
            working_directory=self.config.executor.working_directory,
        )
        self.executor = CommandExecutor(
            timeout_seconds=self.config.executor.timeout_seconds,
            max_output_chars=self.config.executor.max_output_chars,
        )
        self.gate = ConfirmationGate()
        self.context: deque[dict[str, str]] = deque(maxlen=10)
        self._busy = False
        self._throttle = self.config.streaming.edit_throttle_seconds

    def _stream(self, chat_id: int) -> StreamingReply:
        return StreamingReply(
            self.telegram,
            chat_id,
            throttle_seconds=self._throttle,
        )

    async def run(self) -> None:
        log.info(
            "localops starting (cwd=%s, llm=%s/%s, os=%s %s, "
            "stream_throttle=%.1fs)",
            self.config.executor.working_directory,
            self.config.llm.provider,
            self.config.llm.model,
            self.host_platform.os_name,
            self.host_platform.os_version,
            self._throttle,
        )
        log.info(
            "Accepting messages from Telegram user_id=%s "
            "(LLM replies with CMD: to run shell commands)",
            self.config.telegram.allowed_user_id,
        )

        while True:
            try:
                updates = await self.telegram.poll_updates(timeout=30)
                for update in updates:
                    await self._handle_update(update)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.error("Poll loop error:\n%s", traceback.format_exc())
                await asyncio.sleep(
                    self.config.telegram.poll_interval_seconds
                )

    async def _handle_update(self, update) -> None:
        if update.message and update.message.text:
            await self._handle_message(update)

    async def _handle_message(self, update) -> None:
        message = update.message
        user = message.from_user
        chat_id = message.chat_id
        text = (message.text or "").strip()

        if not self.telegram.is_authorized(user.id if user else None):
            await self.telegram.send_unauthorized(chat_id)
            return

        if not text:
            return

        # 1) Pending confirmation ALWAYS wins — before busy, LLM, or anything.
        #    Timed-out pendings are cleared first so the user gets a notice.
        if self.gate.check_timeout(chat_id):
            await self.telegram.remove_reply_keyboard(chat_id, TIMEOUT_TEXT)

        if self.gate.has_pending(chat_id):
            await self._handle_confirmation_reply(chat_id, text)
            return

        if self._busy:
            await self.telegram.send_plain(
                chat_id, "⏳ Still working on the previous message…"
            )
            return

        self._busy = True
        thinking = await self.telegram.send_plain(chat_id, "🤔 Thinking...")
        try:
            await self._process_message(chat_id, text, thinking.message_id)
        except Exception:  # noqa: BLE001
            log.error("Message handling error:\n%s", traceback.format_exc())
            try:
                await self.telegram.edit_message(
                    chat_id,
                    thinking.message_id,
                    "❌ Internal error — check the localops logs.",
                )
            except Exception:  # noqa: BLE001
                pass
        finally:
            self._busy = False

    async def _handle_confirmation_reply(
        self, chat_id: int, text: str
    ) -> None:
        result = self.gate.resolve(chat_id, text)
        log.info("Confirmation resolve status=%s", result.status)

        if result.status == "confirmed" and result.command:
            # Defense in depth: re-check safety before execute
            safety = self.safety.check(result.command)
            if safety.verdict == "blocked":
                blocked = (
                    "🚫 Blocked: this command matches a blocked pattern\n"
                    "and will never be executed."
                )
                await self.telegram.remove_reply_keyboard(chat_id, blocked)
                return
            self._busy = True
            try:
                await self.telegram.remove_reply_keyboard(
                    chat_id, f"🧠 Running\n→ {result.command}"
                )
                if await self._handle_cd(chat_id, result.command):
                    self.context.append(
                        {
                            "role": "assistant",
                            "content": f"ran: {result.command}",
                        }
                    )
                    return
                raw_output, exit_code = await self._execute_and_stream(
                    chat_id, result.command
                )
                await self._send_explanation(
                    chat_id, result.command, raw_output, exit_code
                )
                self.context.append(
                    {
                        "role": "assistant",
                        "content": f"ran: {result.command}",
                    }
                )
            finally:
                self._busy = False
            return

        if result.status == "cancelled":
            await self.telegram.remove_reply_keyboard(chat_id, CANCELLED_TEXT)
            self.context.append(
                {"role": "assistant", "content": "cancelled pending command"}
            )
            return

        if result.status == "unclear":
            await self.telegram.send_with_reply_keyboard(
                chat_id, REPROMPT_TEXT, ["yes", "no"]
            )
            return

        # no_pending — fall through shouldn't happen if has_pending was True
        await self.telegram.remove_reply_keyboard(
            chat_id, "No pending command to confirm."
        )

    async def _process_message(
        self, chat_id: int, text: str, thinking_id: int
    ) -> None:
        # Defense in depth: never route through the LLM while a confirm is open
        if self.gate.has_pending(chat_id):
            await self._handle_confirmation_reply(chat_id, text)
            return

        stream = self._stream(chat_id)
        await stream.start(placeholder="…", message_id=thinking_id)

        current_dir = self.executor.get_current_dir(
            chat_id, self.config.executor.working_directory
        )
        accumulated = ""
        async for chunk in self.router.respond_stream(
            text,
            list(self.context),
            working_directory=current_dir,
        ):
            accumulated += chunk
            await stream.update(accumulated)

        parsed = parse_llm_reply(accumulated)
        log.info(
            "LLM route kind=%s preview=%r",
            parsed.kind,
            (parsed.text or "")[:80],
        )

        if parsed.kind == "empty":
            await stream.finish(EMPTY_FALLBACK)
            self.context.append({"role": "user", "content": text})
            self.context.append(
                {"role": "assistant", "content": EMPTY_FALLBACK}
            )
            return

        if parsed.kind == "chat":
            await stream.finish(parsed.text)
            self.context.append({"role": "user", "content": text})
            self.context.append({"role": "assistant", "content": parsed.text})
            return

        # Turned out to be a command
        await stream.finish("📋 Detected command, processing…")
        await self._run_command(
            chat_id, text, stream.message_id or thinking_id, parsed.text
        )

    async def _run_command(
        self,
        chat_id: int,
        user_text: str,
        thinking_id: int,
        command: str,
    ) -> None:
        self.context.append({"role": "user", "content": user_text})

        # cd (including stripped mkdir&&cd) BEFORE safety/confirm — never
        # let a compound mkdir slip into write confirmation + shell execute.
        cd_target = is_cd_command(command)
        if cd_target is not None:
            msg = self.executor.change_directory(
                chat_id,
                cd_target,
                self.config.executor.working_directory,
            )
            await self.telegram.edit_message(chat_id, thinking_id, msg)
            self.context.append(
                {"role": "assistant", "content": f"ran: {command}"}
            )
            log.info("cd handled: %s", msg)
            return

        safety = self.safety.check(command)

        if safety.verdict == "blocked":
            blocked = (
                "🚫 Blocked: this command matches a blocked pattern\n"
                "and will never be executed."
            )
            if safety.reason and not safety.reason.startswith(
                "Blocked: this"
            ):
                blocked = f"🚫 {safety.reason}"
            await self.telegram.edit_message(chat_id, thinking_id, blocked)
            self.context.append(
                {"role": "assistant", "content": f"blocked: {command}"}
            )
            return

        intent = f"Run: {command}"

        if safety.verdict in {"write", "destructive"}:
            self.gate.create_pending(
                chat_id, command, intent, safety.verdict
            )
            prompt = format_confirm_prompt(
                command, intent, safety.verdict
            )
            # Reply keyboard can only be attached on send (not edit).
            await self.telegram.edit_message(
                chat_id, thinking_id, "📋 Awaiting confirmation…"
            )
            await self.telegram.send_with_reply_keyboard(
                chat_id, prompt, ["yes", "no"]
            )
            log.info(
                "Awaiting yes/no confirmation for %r (level=%s)",
                command,
                safety.verdict,
            )
            return

        # Read — execute immediately
        await self.telegram.edit_message(
            chat_id,
            thinking_id,
            f"🧠 Running\n→ {command}",
        )
        raw_output, exit_code = await self._execute_and_stream(
            chat_id, command
        )
        await self._send_explanation(chat_id, command, raw_output, exit_code)
        self.context.append(
            {"role": "assistant", "content": f"ran: {command}"}
        )

    async def _handle_cd(self, chat_id: int, command: str) -> bool:
        """If command is cd, update tracked dir and notify. Returns True if handled."""
        target = is_cd_command(command)
        if target is None:
            return False
        msg = self.executor.change_directory(
            chat_id, target, self.config.executor.working_directory
        )
        await self.telegram.send_plain(chat_id, msg)
        log.info("cd handled: %s", msg)
        return True

    async def _execute_and_stream(
        self, chat_id: int, command: str
    ) -> tuple[str, int]:
        started = time.monotonic()
        stream = self._stream(chat_id)
        header = f"✅ {command}\n{SEPARATOR}\n"
        await stream.start(placeholder=header + "…")

        cwd = self.executor.get_current_dir(
            chat_id, self.config.executor.working_directory
        )
        lines: list[str] = []
        async for chunk in self.executor.execute(command, cwd):
            lines.append(chunk)
            body = self._format_body(lines)
            await stream.update(header + body)

        elapsed = time.monotonic() - started
        body = self._format_body(lines)
        final = (
            f"{header}"
            f"{body}\n"
            f"{SEPARATOR}\n"
            f"Done in {elapsed:.1f}s"
        )
        await stream.finish(final)
        return body, self.executor.last_exit_code

    async def _send_explanation(
        self,
        chat_id: int,
        command: str,
        raw_output: str,
        exit_code: int,
    ) -> None:
        actual_cwd = self.executor.get_current_dir(
            chat_id, self.config.executor.working_directory
        )
        prompt = build_explain_prompt(
            command, raw_output, exit_code, working_directory=actual_cwd
        )
        log.info(
            "Explaining output for %r (cwd=%s, exit=%s, chars=%d)",
            command,
            actual_cwd,
            exit_code,
            len(raw_output or ""),
        )
        stream = self._stream(chat_id)
        await stream.start(placeholder="💬 …")

        accumulated = ""
        try:
            async for chunk in self.router.respond_stream(
                prompt,
                [],
                system_prompt=EXPLAIN_SYSTEM_PROMPT,
            ):
                accumulated += chunk
                display = accumulated.strip()
                if display:
                    await stream.update("💬 " + display)
        except Exception:  # noqa: BLE001
            log.error(
                "Explain step failed:\n%s", traceback.format_exc()
            )
            await stream.finish(
                f"💬 Command `{command}` finished with exit code {exit_code}."
            )
            return

        parsed = parse_llm_reply(accumulated)
        if parsed.kind == "command":
            log.warning(
                "Explanation reply unexpectedly contained CMD:; "
                "using chat fallback"
            )
            explanation = (
                f"Command `{command}` finished with exit code {exit_code}."
            )
        elif parsed.kind == "empty":
            explanation = (
                f"Command `{command}` finished with exit code {exit_code}."
            )
        else:
            explanation = parsed.text

        await stream.finish(f"💬 {explanation.strip()}")
        log.info("Explanation sent for %r", command)

    def _format_body(self, lines: list[str]) -> str:
        body = "\n".join(lines).strip()
        if not body:
            return "(no output)"
        if len(body) > 3500:
            return body[:3500] + "\n…"
        return body


def main() -> None:
    app = LocalOpsApp()
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        print("\nlocalops stopped.")


if __name__ == "__main__":
    main()
