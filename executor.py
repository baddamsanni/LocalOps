"""Subprocess command execution with streaming output and per-chat cwd."""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import time
from collections.abc import AsyncGenerator
from pathlib import Path

# Match `cd` as a shell word (start / after ;|& whitespace)
_CD_WORD = re.compile(r"(?:^|[\s;|&])cd\b", re.IGNORECASE)


def is_cd_command(command: str) -> str | None:
    """
    If command is (or contains) a cd navigation, return the target path
    ("" means home). Otherwise return None.

    Compound forms like `mkdir -p foo && cd foo` are treated as cd-only:
    the mkdir portion is ignored and never executed.
    """
    cmd = (command or "").strip()
    if not cmd:
        return None

    lowered = cmd.lower()
    is_compound = (
        "&&" in cmd
        or ";" in cmd
        or "|" in cmd
        or "mkdir" in lowered
    )

    if is_compound:
        if not _CD_WORD.search(cmd):
            return None
        return _extract_cd_target(cmd)

    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()
    if not tokens or tokens[0] != "cd":
        return None
    if len(tokens) == 1:
        return ""
    if len(tokens) == 2:
        return tokens[1]
    # Multi-arg pure cd — still take the first path only
    return tokens[1]


def _extract_cd_target(cmd: str) -> str:
    """Extract path after the last `cd` word in a compound command."""
    matches = list(_CD_WORD.finditer(cmd))
    if not matches:
        return ""
    rest = cmd[matches[-1].end() :].strip()
    if not rest or rest.startswith(("&&", ";", "|")):
        return ""
    try:
        tokens = shlex.split(rest)
    except ValueError:
        tokens = rest.split()
    if not tokens:
        return ""
    target = tokens[0]
    if target in {"&&", ";", "|"}:
        return ""
    return target


class CommandExecutor:
    def __init__(
        self,
        timeout_seconds: int = 300,
        max_output_chars: int = 4000,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars
        self.last_exit_code: int = 0
        self._current_dir: dict[int, str] = {}

    def get_current_dir(self, chat_id: int, default: str) -> str:
        return self._current_dir.get(chat_id, default)

    def change_directory(
        self, chat_id: int, target: str, default: str
    ) -> str:
        """
        Resolve and apply a cd target for this chat (no subprocess).
        Returns a user-facing success or error message.
        Never creates directories.
        """
        current = self.get_current_dir(chat_id, default)
        if target == "":
            resolved = os.path.expanduser("~")
        else:
            expanded = os.path.expanduser(target)
            if os.path.isabs(expanded):
                resolved = expanded
            else:
                resolved = os.path.join(current, expanded)

        try:
            resolved = str(Path(resolved).resolve())
        except (OSError, RuntimeError):
            self.last_exit_code = 1
            return f"Directory not found: {resolved}"

        if not os.path.isdir(resolved):
            self.last_exit_code = 1
            return f"Directory not found: {resolved}"

        self._current_dir[chat_id] = resolved
        self.last_exit_code = 0
        return f"📂 Now in {resolved}"

    async def execute(
        self, command: str, cwd: str
    ) -> AsyncGenerator[str, None]:
        """Run command in a shell; yield stdout/stderr lines. Never raises."""
        # Defense in depth: cd / mkdir&&cd must never hit the shell
        if is_cd_command(command) is not None:
            self.last_exit_code = 1
            yield (
                "Error: directory changes are handled by localops, "
                "not the shell. Refusing to execute."
            )
            return

        started = time.monotonic()
        total_chars = 0
        truncated = False
        process: asyncio.subprocess.Process | None = None
        self.last_exit_code = 0

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
            )
            assert process.stdout is not None

            async def _readline() -> bytes:
                assert process is not None and process.stdout is not None
                return await process.stdout.readline()

            while True:
                remaining = self.timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    await self._kill(process)
                    self.last_exit_code = -1
                    yield (
                        f"\n⏱️ Timeout: command killed after "
                        f"{self.timeout_seconds}s"
                    )
                    return

                try:
                    line_bytes = await asyncio.wait_for(
                        _readline(), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    await self._kill(process)
                    self.last_exit_code = -1
                    yield (
                        f"\n⏱️ Timeout: command killed after "
                        f"{self.timeout_seconds}s"
                    )
                    return

                if not line_bytes:
                    break

                line = line_bytes.decode("utf-8", errors="replace")
                if truncated:
                    continue

                if total_chars + len(line) > self.max_output_chars:
                    room = max(0, self.max_output_chars - total_chars)
                    if room:
                        yield line[:room]
                        total_chars += room
                    truncated = True
                    yield (
                        f"\n… truncated at {self.max_output_chars} characters"
                    )
                    continue

                total_chars += len(line)
                # Yield without forcing an extra newline if the line already has one
                yield line.rstrip("\n") if line.endswith("\n") else line

            returncode = await process.wait()
            self.last_exit_code = int(returncode) if returncode is not None else 0
            if self.last_exit_code != 0:
                yield f"\n❌ Exit code {self.last_exit_code}"

        except Exception as exc:  # noqa: BLE001 — never raise to caller
            self.last_exit_code = -1
            yield f"\n❌ Error: {exc}"
        finally:
            if process is not None and process.returncode is None:
                await self._kill(process)

    async def _kill(self, process: asyncio.subprocess.Process) -> None:
        try:
            process.kill()
            await process.wait()
        except ProcessLookupError:
            pass
        except Exception:  # noqa: BLE001
            pass
