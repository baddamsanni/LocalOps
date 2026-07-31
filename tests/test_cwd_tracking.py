"""Tests for persistent per-chat working directory (cd handling)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from executor import CommandExecutor, is_cd_command


def test_is_cd_command_detects_variants():
    assert is_cd_command("cd") == ""
    assert is_cd_command("cd ~/Desktop") == "~/Desktop"
    assert is_cd_command("cd Desktop") == "Desktop"
    assert is_cd_command("ls") is None
    # Compound forms strip to the cd target only
    assert is_cd_command("cd foo && ls") == "foo"


def test_cd_home_updates_tracked_dir(tmp_path: Path):
    executor = CommandExecutor()
    home = os.path.expanduser("~")
    msg = executor.change_directory(1, "", default=str(tmp_path))
    assert msg.startswith("📂 Now in ")
    assert executor.get_current_dir(1, str(tmp_path)) == str(Path(home).resolve())


def test_cd_tilde_desktop(tmp_path: Path):
    desktop = Path(os.path.expanduser("~/Desktop"))
    if not desktop.is_dir():
        pytest.skip("~/Desktop does not exist on this machine")
    executor = CommandExecutor()
    msg = executor.change_directory(7, "~/Desktop", default=str(tmp_path))
    assert "Now in" in msg
    assert executor.get_current_dir(7, str(tmp_path)) == str(desktop.resolve())


def test_cd_nonexistent_leaves_tracked_dir_unchanged(tmp_path: Path):
    executor = CommandExecutor()
    default = str(tmp_path)
    msg = executor.change_directory(1, "nonexistent_folder_xyz", default=default)
    assert msg.startswith("Directory not found:")
    assert executor.get_current_dir(1, default) == default


def test_cd_relative_to_current_not_project(tmp_path: Path):
    home = Path(os.path.expanduser("~"))
    desktop = home / "Desktop"
    if not desktop.is_dir():
        # Create a stand-in tree under tmp for relative resolution
        base = tmp_path / "homeish"
        desk = base / "Desktop"
        desk.mkdir(parents=True)
        executor = CommandExecutor()
        executor._current_dir[1] = str(base.resolve())
        msg = executor.change_directory(1, "Desktop", default=str(tmp_path))
        assert msg.startswith("📂 Now in ")
        assert executor.get_current_dir(1, str(tmp_path)) == str(desk.resolve())
        return

    executor = CommandExecutor()
    executor._current_dir[1] = str(home.resolve())
    msg = executor.change_directory(1, "Desktop", default=str(tmp_path))
    assert msg.startswith("📂 Now in ")
    assert executor.get_current_dir(1, str(tmp_path)) == str(desktop.resolve())


@pytest.mark.asyncio
async def test_subsequent_command_uses_tracked_cwd(tmp_path: Path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "marker.txt").write_text("hi")

    executor = CommandExecutor()
    msg = executor.change_directory(42, str(target), default=str(tmp_path))
    assert msg.startswith("📂 Now in ")

    cwd = executor.get_current_dir(42, str(tmp_path))
    assert cwd == str(target.resolve())

    with patch(
        "executor.asyncio.create_subprocess_shell", new_callable=AsyncMock
    ) as mock_shell:
        proc = AsyncMock()
        proc.stdout = AsyncMock()
        proc.stdout.readline = AsyncMock(side_effect=[b"marker.txt\n", b""])
        proc.wait = AsyncMock(return_value=0)
        proc.returncode = 0
        mock_shell.return_value = proc

        chunks: list[str] = []
        async for chunk in executor.execute("ls", cwd):
            chunks.append(chunk)

        mock_shell.assert_awaited_once()
        assert mock_shell.await_args.kwargs["cwd"] == cwd
        assert "marker.txt" in "\n".join(chunks)


def test_cd_classified_as_read():
    from config import SafetyConfig
    from safety import SafetyChecker

    checker = SafetyChecker(
        SafetyConfig(
            blocked_patterns=[],
            destructive_keywords=["kill"],
            read_only_commands=["cd", "ls"],
        )
    )
    assert checker.check("cd ~/Desktop").verdict == "read"
    assert checker.check("cd").verdict == "read"


def test_is_cd_strips_mkdir_compound():
    assert is_cd_command("mkdir -p xyz123nonexistent && cd xyz123nonexistent") == (
        "xyz123nonexistent"
    )
    assert is_cd_command("mkdir foo; cd foo") == "foo"
    assert is_cd_command("cd bar && ls") == "bar"


def test_mkdir_and_cd_never_creates_folder(tmp_path: Path):
    """Bug 2: navigating to a missing folder must not create it."""
    executor = CommandExecutor()
    name = "xyz123nonexistent"
    target = tmp_path / name
    assert not target.exists()

    # Compound command the LLM might emit — must become cd-only
    extracted = is_cd_command(f"mkdir -p {name} && cd {name}")
    assert extracted == name

    msg = executor.change_directory(1, extracted or "", default=str(tmp_path))
    assert msg.startswith("Directory not found:")
    assert name in msg
    assert not target.exists()
    assert executor.get_current_dir(1, str(tmp_path)) == str(tmp_path)


@pytest.mark.asyncio
async def test_execute_refuses_compound_cd(tmp_path: Path):
    executor = CommandExecutor()
    chunks: list[str] = []
    async for chunk in executor.execute(
        "mkdir -p nope && cd nope", cwd=str(tmp_path)
    ):
        chunks.append(chunk)
    output = "\n".join(chunks)
    assert "Refusing" in output or "directory changes" in output.lower()
    assert not (tmp_path / "nope").exists()
