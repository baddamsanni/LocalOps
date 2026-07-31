"""Tests for main.py yes/no confirmation routing (no LLM on resolve)."""

from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

from confirmation_gate import ConfirmationGate
from main import LocalOpsApp


def _make_app() -> LocalOpsApp:
    app = LocalOpsApp.__new__(LocalOpsApp)
    app.context = deque()
    app._throttle = 0.0
    app._busy = False
    app.gate = ConfirmationGate()
    app.router = MagicMock()
    app.router.respond_stream = MagicMock()
    app.telegram = MagicMock()
    app.telegram.edit_message = AsyncMock()
    app.telegram.send_plain = AsyncMock(
        return_value=MagicMock(message_id=55)
    )
    app.telegram.send_with_reply_keyboard = AsyncMock(
        return_value=MagicMock(message_id=56)
    )
    app.telegram.remove_reply_keyboard = AsyncMock(
        return_value=MagicMock(message_id=57)
    )
    app.safety = MagicMock()
    app.safety.check = MagicMock(
        return_value=MagicMock(verdict="write", reason="write")
    )
    app.config = MagicMock()
    app.config.executor.working_directory = "."
    app.config.streaming.edit_throttle_seconds = 0.0
    app.executor = MagicMock()
    app.executor.last_exit_code = 0
    return app


@pytest.mark.asyncio
async def test_yes_runs_pending_without_llm():
    app = _make_app()
    app.gate.create_pending(1, "git pull", "Pull latest", "write")
    app.safety.check = MagicMock(
        return_value=MagicMock(verdict="write", reason="write")
    )

    executed: list[str] = []

    async def fake_execute(command, cwd):
        executed.append(command)
        yield "Already up to date."

    app.executor.execute = fake_execute

    explain_calls = {"n": 0}

    async def respond_stream(msg, context, *, system_prompt=None):
        explain_calls["n"] += 1
        yield "Pulled successfully."

    app.router.respond_stream = respond_stream

    await app._handle_confirmation_reply(1, "yes")

    assert executed == ["git pull"]
    # LLM only used for explanation, never for routing this "yes"
    assert explain_calls["n"] == 1
    assert app.gate.has_pending(1) is False
    app.telegram.remove_reply_keyboard.assert_awaited()


@pytest.mark.asyncio
async def test_no_cancels_without_executor():
    app = _make_app()
    app.gate.create_pending(1, "rm ./dist", "Delete dist", "destructive")
    app.executor.execute = MagicMock()

    await app._handle_confirmation_reply(1, "no")

    app.executor.execute.assert_not_called()
    app.telegram.remove_reply_keyboard.assert_awaited()
    assert "Cancelled" in app.telegram.remove_reply_keyboard.await_args.args[1]
    assert app.gate.has_pending(1) is False


@pytest.mark.asyncio
async def test_unrelated_reprompts_keeps_pending():
    app = _make_app()
    app.gate.create_pending(1, "git pull", "Pull", "write")
    app.executor.execute = MagicMock()

    await app._handle_confirmation_reply(1, "what about lunch?")

    app.executor.execute.assert_not_called()
    assert app.gate.has_pending(1) is True
    app.telegram.send_with_reply_keyboard.assert_awaited()
    args = app.telegram.send_with_reply_keyboard.await_args.args
    assert "yes" in args[1].lower() and "no" in args[1].lower()
    assert args[2] == ["yes", "no"]


@pytest.mark.asyncio
async def test_pending_blocks_new_messages_via_handle_message():
    """
    Bug 3 regression: while a confirmation is pending, an unrelated
    message must NOT call the LLM or executor — only re-prompt.
    """
    app = _make_app()
    app.telegram.is_authorized = MagicMock(return_value=True)
    app.gate.create_pending(42, "npm install", "Install deps", "write")

    llm_calls = {"n": 0}

    async def respond_stream(*_a, **_k):
        llm_calls["n"] += 1
        yield "CMD: git status"

    app.router.respond_stream = respond_stream
    app.router.respond = AsyncMock(
        side_effect=AssertionError("respond must not be called")
    )
    app.executor.execute = MagicMock(
        side_effect=AssertionError("execute must not be called")
    )
    app.executor.get_current_dir = MagicMock(return_value=".")

    update = MagicMock()
    update.message.chat_id = 42
    update.message.text = "show me git status"
    update.message.from_user.id = 99

    await app._handle_message(update)

    assert llm_calls["n"] == 0
    app.executor.execute.assert_not_called()
    assert app.gate.has_pending(42) is True
    app.telegram.send_with_reply_keyboard.assert_awaited()
    prompt = app.telegram.send_with_reply_keyboard.await_args.args[1]
    assert "yes" in prompt.lower() and "no" in prompt.lower()


@pytest.mark.asyncio
async def test_mkdir_cd_compound_never_creates_pending_or_folder(tmp_path):
    """Bug 2/4: mkdir&&cd is handled as cd-only — no confirm, no mkdir."""
    from pathlib import Path

    from executor import CommandExecutor

    app = _make_app()
    app.executor = CommandExecutor()
    app.config.executor.working_directory = str(tmp_path)
    name = "xyz123nonexistent"
    assert not (Path(tmp_path) / name).exists()

    await app._run_command(
        chat_id=1,
        user_text="go to xyz123nonexistent",
        thinking_id=10,
        command=f"mkdir -p {name} && cd {name}",
    )

    assert not app.gate.has_pending(1)
    assert not (Path(tmp_path) / name).exists()
    edited = app.telegram.edit_message.await_args.args[2]
    assert "Directory not found" in edited
    assert name in edited
