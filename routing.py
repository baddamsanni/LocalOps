"""Parse LLM replies for the CMD: shell-command format."""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("localops")

CMD_MARKER = "CMD:"


@dataclass
class ParsedReply:
    kind: str  # "chat" | "command" | "empty"
    text: str  # chat text, or extracted shell command


def parse_llm_reply(raw_reply: str) -> ParsedReply:
    """
    If the reply contains CMD:, extract the shell command after the first
    occurrence. Otherwise treat the whole reply as chat.
    """
    text = (raw_reply or "").strip()
    if not text:
        return ParsedReply(kind="empty", text="")

    if text.startswith(CMD_MARKER):
        command = text[len(CMD_MARKER) :].strip()
        # Prefer the first line as the command if the model added junk after
        command = command.splitlines()[0].strip() if command else ""
        if not command:
            return ParsedReply(kind="empty", text="")
        return ParsedReply(kind="command", text=command)

    idx = text.find(CMD_MARKER)
    if idx != -1:
        before = text[:idx].strip()
        after = text[idx + len(CMD_MARKER) :].strip()
        command = after.splitlines()[0].strip() if after else ""
        log.warning(
            "Extra text around CMD: found (before=%r after_rest=%r); "
            "extracting command=%r",
            before[:120],
            after[len(command) :].strip()[:120] if command else after[:120],
            command,
        )
        if not command:
            return ParsedReply(kind="empty", text="")
        return ParsedReply(kind="command", text=command)

    return ParsedReply(kind="chat", text=text)
