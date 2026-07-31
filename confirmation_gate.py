"""Plain-text yes/no confirmation for write/destructive commands."""

from __future__ import annotations

import time
from dataclasses import dataclass

CONFIRM_TIMEOUT_SECONDS = 60

YES_REPLIES = frozenset({"yes", "y"})
NO_REPLIES = frozenset({"no", "n", "cancel"})


@dataclass
class PendingConfirmation:
    command: str
    intent: str
    safety_level: str
    created_at: float


@dataclass
class ConfirmationResult:
    status: str  # confirmed | cancelled | unclear | no_pending
    command: str | None = None
    intent: str | None = None
    safety_level: str | None = None


class ConfirmationGate:
    def __init__(self, timeout_seconds: float = CONFIRM_TIMEOUT_SECONDS) -> None:
        self.timeout_seconds = timeout_seconds
        self._pending: dict[int, PendingConfirmation] = {}

    def create_pending(
        self,
        chat_id: int,
        command: str,
        intent: str,
        safety_level: str,
    ) -> None:
        self._pending[chat_id] = PendingConfirmation(
            command=command,
            intent=intent,
            safety_level=safety_level,
            created_at=time.monotonic(),
        )

    def _expired(self, pending: PendingConfirmation) -> bool:
        return time.monotonic() - pending.created_at > self.timeout_seconds

    def has_pending(self, chat_id: int) -> bool:
        """True if unresolved confirmation exists. Timed-out entries are cleared."""
        pending = self._pending.get(chat_id)
        if pending is None:
            return False
        if self._expired(pending):
            self._pending.pop(chat_id, None)
            return False
        return True

    def check_timeout(self, chat_id: int) -> bool:
        """
        If a pending confirmation has timed out, clear it and return True.
        Call this before has_pending to notify the user of timeout.
        """
        pending = self._pending.get(chat_id)
        if pending is None:
            return False
        if self._expired(pending):
            self._pending.pop(chat_id, None)
            return True
        return False

    def resolve(self, chat_id: int, user_reply: str) -> ConfirmationResult:
        pending = self._pending.get(chat_id)
        if pending is None:
            return ConfirmationResult(status="no_pending")
        if self._expired(pending):
            self._pending.pop(chat_id, None)
            return ConfirmationResult(status="no_pending")

        reply = (user_reply or "").strip().lower()
        if reply in YES_REPLIES:
            self._pending.pop(chat_id, None)
            return ConfirmationResult(
                status="confirmed",
                command=pending.command,
                intent=pending.intent,
                safety_level=pending.safety_level,
            )
        if reply in NO_REPLIES:
            self._pending.pop(chat_id, None)
            return ConfirmationResult(status="cancelled")
        return ConfirmationResult(
            status="unclear",
            command=pending.command,
            intent=pending.intent,
            safety_level=pending.safety_level,
        )

    def get_pending_command(self, chat_id: int) -> str | None:
        pending = self._pending.get(chat_id)
        if pending is None or self._expired(pending):
            return None
        return pending.command

    def clear(self, chat_id: int) -> None:
        self._pending.pop(chat_id, None)


def format_confirm_prompt(
    command: str, intent: str, safety_level: str
) -> str:
    if safety_level == "destructive":
        return (
            f"⚠️ {command}\n"
            f"{intent}\n"
            "This is destructive and may be hard to reverse.\n"
            "Tap yes to run it, or no to cancel."
        )
    return (
        f"📋 {command}\n"
        f"{intent}\n"
        "Tap yes to run it, or no to cancel."
    )


REPROMPT_TEXT = (
    "Please tap yes or no to confirm the pending command, or "
    "say 'cancel' to abandon it and start a new request."
)

TIMEOUT_TEXT = "⏱ Confirmation timed out, cancelled."
CANCELLED_TEXT = "❌ Cancelled"
