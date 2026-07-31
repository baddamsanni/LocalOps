"""Tests for CommandExecutor."""

import pytest

from executor import CommandExecutor


@pytest.mark.asyncio
async def test_echo_hello():
    executor = CommandExecutor(timeout_seconds=10, max_output_chars=4000)
    chunks: list[str] = []
    async for chunk in executor.execute("echo hello", cwd="."):
        chunks.append(chunk)
    output = "\n".join(chunks)
    assert "hello" in output
    assert "Exit code" not in output


@pytest.mark.asyncio
async def test_exit_one_yields_error_no_raise():
    executor = CommandExecutor(timeout_seconds=10, max_output_chars=4000)
    chunks: list[str] = []
    async for chunk in executor.execute("exit 1", cwd="."):
        chunks.append(chunk)
    output = "\n".join(chunks)
    assert "Exit code 1" in output


@pytest.mark.asyncio
async def test_timeout_enforced():
    executor = CommandExecutor(timeout_seconds=1, max_output_chars=4000)
    chunks: list[str] = []
    async for chunk in executor.execute("sleep 10", cwd="."):
        chunks.append(chunk)
    output = "\n".join(chunks)
    assert "Timeout" in output


@pytest.mark.asyncio
async def test_truncates_output():
    executor = CommandExecutor(timeout_seconds=10, max_output_chars=50)
    # Generate more than 50 chars of output
    cmd = "python3 -c \"print('x' * 200)\""
    chunks: list[str] = []
    async for chunk in executor.execute(cmd, cwd="."):
        chunks.append(chunk)
    output = "".join(chunks)
    assert "truncated" in output.lower()
    # Body before truncation notice should not exceed max by much
    assert len(output) < 200
