"""Tests for LLM respond() (mocked — no real API calls)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from llm_router import (
    SYSTEM_PROMPT,
    ClaudeProvider,
    OllamaProvider,
    OpenAIProvider,
)
from platform_info import HostPlatform
from routing import parse_llm_reply

_HOST = HostPlatform("Darwin", "15.0")


@pytest.mark.asyncio
async def test_claude_respond_returns_plain_text():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_block = MagicMock()
    mock_block.type = "text"
    mock_block.text = "Hey! What can I help you with?"
    mock_response.content = [mock_block]
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    provider = ClaudeProvider(
        api_key="test-key",
        model="test-claude-model",
        working_directory="/tmp/project",
        client=mock_client,
        host_platform=_HOST,
    )
    reply = await provider.respond("hello", context=[])
    assert reply.startswith("Hey!")
    kwargs = mock_client.messages.create.await_args.kwargs
    assert SYSTEM_PROMPT in kwargs["system"]
    assert "Darwin" in kwargs["system"]
    assert "tools" not in kwargs


@pytest.mark.asyncio
async def test_ollama_respond_returns_cmd():
    mock_http = MagicMock()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value={"message": {"content": "CMD: df -h"}}
    )
    mock_http.post = AsyncMock(return_value=mock_response)

    provider = OllamaProvider(
        base_url="http://ollama.test:11434",
        model="test-ollama-model",
        working_directory="/tmp/project",
        http_client=mock_http,
        host_platform=_HOST,
    )
    reply = await provider.respond("what's my disk space?", context=[])
    assert reply == "CMD: df -h"
    parsed = parse_llm_reply(reply)
    assert parsed.kind == "command"
    assert parsed.text == "df -h"
    _args, kwargs = mock_http.post.await_args
    assert "format" not in kwargs["json"]
    assert "tools" not in kwargs["json"]
    system = kwargs["json"]["messages"][0]["content"]
    assert SYSTEM_PROMPT in system
    assert "Darwin (15.0)" in system


@pytest.mark.asyncio
async def test_openai_respond_chat_and_auth():
    mock_http = MagicMock()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value={
            "choices": [
                {"message": {"content": "A memory leak wastes RAM over time."}}
            ]
        }
    )
    mock_http.post = AsyncMock(return_value=mock_response)

    provider = OpenAIProvider(
        api_key="sk-test-key",
        model="gpt-test",
        base_url="https://api.openai.com/v1",
        working_directory="/tmp/project",
        http_client=mock_http,
        host_platform=_HOST,
    )
    reply = await provider.respond("explain memory leaks", context=[])
    assert "memory" in reply.lower()
    assert parse_llm_reply(reply).kind == "chat"
    args, kwargs = mock_http.post.await_args
    assert args[0] == "https://api.openai.com/v1/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test-key"
    assert "tools" not in kwargs["json"]
