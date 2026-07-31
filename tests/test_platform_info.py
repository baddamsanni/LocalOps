"""Tests for host OS detection and platform system-prompt context."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_router import OllamaProvider, SYSTEM_PROMPT, _build_messages
from platform_info import (
    HostPlatform,
    build_platform_prompt,
    detect_host_platform,
)


def test_detect_host_platform_uses_platform_module():
    with patch("platform_info.platform.system", return_value="Darwin"), patch(
        "platform_info.platform.mac_ver", return_value=("15.1", ("", "", ""), "")
    ):
        host = detect_host_platform()
    assert host.os_name == "Darwin"
    assert host.os_version == "15.1"


def test_detect_linux_uses_release():
    with patch("platform_info.platform.system", return_value="Linux"), patch(
        "platform_info.platform.release", return_value="6.8.0"
    ):
        host = detect_host_platform()
    assert host.os_name == "Linux"
    assert host.os_version == "6.8.0"


def test_darwin_prompt_includes_macos_not_linux_section():
    prompt = build_platform_prompt(HostPlatform("Darwin", "15.0"))
    assert "Darwin" in prompt
    assert "15.0" in prompt
    assert "vm_stat" in prompt
    assert "sysctl" in prompt
    assert "sw_vers" in prompt
    assert "NOT `free`" in prompt
    assert "NOT `lscpu`" in prompt
    # Linux-only guidance section must not appear
    assert "this system is Linux" not in prompt
    assert "`free -h`" not in prompt
    assert "NOT `cat /etc/os-release`" in prompt  # told not to use it


def test_linux_prompt_includes_linux_not_macos_section():
    prompt = build_platform_prompt(HostPlatform("Linux", "6.8.0"))
    assert "Linux" in prompt
    assert "6.8.0" in prompt
    assert "`free -h`" in prompt
    assert "lscpu" in prompt
    assert "/etc/os-release" in prompt
    assert "this system is macOS/Darwin" not in prompt
    assert "vm_stat" not in prompt


def test_build_messages_prepends_platform_to_default_prompt():
    host = HostPlatform("Darwin", "14.5")
    messages = _build_messages(
        "how much RAM?",
        [],
        "/tmp/project",
        host_platform=host,
    )
    system = messages[0]["content"]
    assert "Darwin" in system
    assert "14.5" in system
    assert "vm_stat" in system
    assert "The user's current working directory is: /tmp/project" in system


@pytest.mark.asyncio
async def test_ollama_system_prompt_contains_os():
    mock_http = MagicMock()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value={"message": {"content": "CMD: sysctl hw.memsize"}}
    )
    mock_http.post = AsyncMock(return_value=mock_response)

    provider = OllamaProvider(
        base_url="http://ollama.test:11434",
        model="test-model",
        working_directory="/tmp/project",
        http_client=mock_http,
        host_platform=HostPlatform("Darwin", "15.2"),
    )
    await provider.respond("what is my RAM size?", context=[])
    system = mock_http.post.await_args.kwargs["json"]["messages"][0]["content"]
    assert "You are running on Darwin (15.2)" in system
    assert "vm_stat" in system
    assert SYSTEM_PROMPT in system
    assert "`free -h`" not in system
