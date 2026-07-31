"""Tests for CMD: routing flow and post-execution explanation."""

from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

from explain import (
    EXPLAIN_OUTPUT_LIMIT,
    EXPLAIN_SYSTEM_PROMPT,
    build_explain_prompt,
    prepare_output_for_explain,
)
from main import LocalOpsApp
from routing import parse_llm_reply


def _async_chunks(*chunks: str):
    async def _gen(*_args, **_kwargs):
        for c in chunks:
            yield c

    return _gen


def _stub_app_cwd(app: LocalOpsApp, cwd: str = ".") -> None:
    from confirmation_gate import ConfirmationGate

    app.config = MagicMock()
    app.config.executor.working_directory = cwd
    app.config.streaming.edit_throttle_seconds = 0.0
    if not hasattr(app, "executor") or app.executor is None:
        app.executor = MagicMock()
    app.executor.get_current_dir = MagicMock(return_value=cwd)
    if not hasattr(app, "gate") or app.gate is None:
        app.gate = MagicMock()
    # Don't overwrite a real ConfirmationGate — only stub MagicMock gates
    if not isinstance(app.gate, ConfirmationGate):
        app.gate.has_pending = MagicMock(return_value=False)
    if not isinstance(app.telegram.send_with_reply_keyboard, AsyncMock):
        app.telegram.send_with_reply_keyboard = AsyncMock()
    if not isinstance(
        getattr(app.telegram, "remove_reply_keyboard", None), AsyncMock
    ):
        app.telegram.remove_reply_keyboard = AsyncMock()


def test_build_explain_prompt_exact_contract():
    prompt = build_explain_prompt("df -h", "disk usage: 64%", 0)
    assert "The command `df -h` was run and produced this output:" in prompt
    assert "disk usage: 64%" in prompt
    assert "Exit code: 0" in prompt
    assert "Do not use the CMD: format here" in prompt


def test_build_explain_prompt_includes_cwd():
    prompt = build_explain_prompt(
        "ls",
        "file.txt",
        0,
        working_directory="/Users/sunny/Desktop",
    )
    assert (
        "The command `ls` was run in directory `/Users/sunny/Desktop` "
        "and produced this output:"
    ) in prompt
    assert "file.txt" in prompt


def test_prepare_output_truncates_large_dumps():
    huge = "x" * 5000
    clipped = prepare_output_for_explain(huge)
    assert len(clipped) < len(huge)
    assert clipped.startswith("x" * EXPLAIN_OUTPUT_LIMIT)
    assert "(output truncated for explanation)" in clipped
    prompt = build_explain_prompt("cat big.log", huge, 0)
    assert "(output truncated for explanation)" in prompt
    assert "x" * 3000 not in prompt  # full dump not embedded


@pytest.mark.asyncio
async def test_chat_reply_never_calls_executor():
    app = LocalOpsApp.__new__(LocalOpsApp)
    app.context = deque()
    app._throttle = 0.0
    app.router = MagicMock()
    app.router.respond_stream = _async_chunks(
        "Hey! What can I help you with?"
    )
    app.telegram = MagicMock()
    app.telegram.edit_message = AsyncMock()
    app.telegram.send_plain = AsyncMock(
        return_value=MagicMock(message_id=99)
    )
    app.executor = MagicMock()
    app.executor.execute = MagicMock()
    app.safety = MagicMock()
    app.gate = MagicMock()
    _stub_app_cwd(app)

    await app._process_message(chat_id=1, text="hello", thinking_id=10)

    app.safety.check.assert_not_called()
    app.executor.execute.assert_not_called()
    assert any(
        "help" in str(c.args[2]).lower()
        for c in app.telegram.edit_message.await_args_list
    )


@pytest.mark.asyncio
async def test_explain_after_execute_sends_emoji_followup():
    """
    After executor returns 'disk usage: 64%', a second LLM call must
    include that exact output, and Telegram must get a 💬 message.
    """
    app = LocalOpsApp.__new__(LocalOpsApp)
    app.context = deque()
    app._throttle = 0.0

    explain_prompts: list[str] = []
    explain_system_prompts: list[str | None] = []
    calls = {"n": 0}

    async def respond_stream(msg, context, *, system_prompt=None, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            yield "CMD: df -h"
            return
        # Second call = explain step
        explain_prompts.append(msg)
        explain_system_prompts.append(system_prompt)
        yield "You're using 64% of disk — about a third still free."

    app.router = MagicMock()
    app.router.respond_stream = respond_stream
    app.safety = MagicMock()
    app.safety.check = MagicMock(
        return_value=MagicMock(verdict="read", reason="read")
    )
    app.telegram = MagicMock()
    app.telegram.edit_message = AsyncMock()
    app.telegram.send_plain = AsyncMock(
        return_value=MagicMock(message_id=55)
    )
    app.gate = MagicMock()

    async def fake_execute(command, cwd):
        assert command == "df -h"
        yield "disk usage: 64%"

    app.executor = MagicMock()
    app.executor.execute = fake_execute
    app.executor.last_exit_code = 0
    _stub_app_cwd(app)

    await app._process_message(
        chat_id=1, text="check disk space", thinking_id=10
    )

    assert calls["n"] == 2
    assert len(explain_prompts) == 1
    assert "disk usage: 64%" in explain_prompts[0]
    assert "df -h" in explain_prompts[0]
    assert "Exit code: 0" in explain_prompts[0]
    assert explain_system_prompts[0] == EXPLAIN_SYSTEM_PROMPT

    # 💬 explanation delivered (via StreamingReply → edit_message and/or send)
    emoji_msgs = [
        str(c.args[2])
        for c in app.telegram.edit_message.await_args_list
        if len(c.args) >= 3 and str(c.args[2]).startswith("💬 ")
    ] + [
        str(c.args[1])
        for c in app.telegram.send_plain.await_args_list
        if len(c.args) >= 2 and str(c.args[1]).startswith("💬 ")
    ]
    assert emoji_msgs, "expected a Telegram message starting with 💬 "
    assert any("64%" in m for m in emoji_msgs)


@pytest.mark.asyncio
async def test_write_cmd_creates_text_confirmation_pending():
    from confirmation_gate import ConfirmationGate

    app = LocalOpsApp.__new__(LocalOpsApp)
    app.context = deque()
    app._throttle = 0.0
    app.gate = ConfirmationGate()

    async def respond_stream(msg, context, *, system_prompt=None, **_kwargs):
        yield "CMD: git pull"

    app.router = MagicMock()
    app.router.respond_stream = respond_stream
    app.safety = MagicMock()
    app.safety.check = MagicMock(
        return_value=MagicMock(verdict="write", reason="write")
    )
    app.telegram = MagicMock()
    app.telegram.edit_message = AsyncMock()
    app.telegram.send_plain = AsyncMock(
        return_value=MagicMock(message_id=55)
    )
    app.telegram.send_with_reply_keyboard = AsyncMock(
        return_value=MagicMock(message_id=56)
    )
    app.executor = MagicMock()
    _stub_app_cwd(app)

    await app._process_message(chat_id=1, text="pull latest", thinking_id=10)

    assert app.gate.has_pending(1) is True
    assert app.gate.get_pending_command(1) == "git pull"
    app.executor.execute.assert_not_called()
    app.telegram.send_with_reply_keyboard.assert_awaited_once()
    prompt = app.telegram.send_with_reply_keyboard.await_args.args[1]
    buttons = app.telegram.send_with_reply_keyboard.await_args.args[2]
    assert "git pull" in prompt
    assert "yes" in prompt.lower()
    assert "no" in prompt.lower()
    assert buttons == ["yes", "no"]


@pytest.mark.asyncio
async def test_messy_cmd_reply_still_extracts_command():
    assert parse_llm_reply("Sure, here you go: CMD: ls -la").text == "ls -la"


@pytest.mark.asyncio
async def test_empty_llm_reply_sends_fallback():
    app = LocalOpsApp.__new__(LocalOpsApp)
    app.context = deque()
    app._throttle = 0.0
    app.router = MagicMock()
    app.router.respond_stream = _async_chunks("   ")
    app.telegram = MagicMock()
    app.telegram.edit_message = AsyncMock()
    app.telegram.send_plain = AsyncMock(
        return_value=MagicMock(message_id=1)
    )
    app.executor = MagicMock()
    app.safety = MagicMock()
    _stub_app_cwd(app)

    await app._process_message(chat_id=1, text="???", thinking_id=10)

    assert any(
        "rephrasing" in str(c.args[2]).lower()
        for c in app.telegram.edit_message.await_args_list
    )
    app.safety.check.assert_not_called()
