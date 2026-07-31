"""Command classification: blocked / destructive / write / read.

Pattern/keyword lists come from config.yml via SafetyConfig.
Overwrite detection for mv is filesystem-aware (see check_mv_overwrite).
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from config import SafetyConfig

SEVERITY = {"read": 0, "write": 1, "destructive": 2, "blocked": 3}

_MV_WORD = re.compile(r"(^|[\s;|&])mv([\s;|&]|$)", re.IGNORECASE)

# Short flags that take a separate argument (not combined with the flag letter)
_MV_FLAGS_WITH_ARG = frozenset({"t", "S"})


def check_mv_overwrite(command: str, working_directory: str) -> bool:
    """
    If the command is an mv/rename operation, check whether the
    destination path already exists as a file. If so, this mv would
    silently overwrite and destroy the destination — treat as blocked.
    Returns True if this looks like a destructive overwrite.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return True  # ambiguous → block

    if not tokens:
        return True

    # Locate the mv verb (first token, or after simple wrappers)
    mv_index = None
    for i, tok in enumerate(tokens):
        if tok.lower() == "mv":
            mv_index = i
            break
    if mv_index is None:
        return True

    no_clobber = False
    operands: list[str] = []
    i = mv_index + 1
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--":
            operands.extend(tokens[i + 1 :])
            break
        if tok.startswith("--"):
            name = tok[2:].split("=", 1)[0]
            if name == "no-clobber":
                no_clobber = True
            if "=" not in tok and name in {"target-directory", "suffix"}:
                i += 2
                continue
            i += 1
            continue
        if tok.startswith("-") and tok != "-":
            chars = tok[1:]
            if "n" in chars:
                no_clobber = True
            # Flags that consume the next token as an argument
            if any(ch in _MV_FLAGS_WITH_ARG for ch in chars):
                i += 2
                continue
            i += 1
            continue
        operands.append(tok)
        i += 1

    if no_clobber:
        return False

    if len(operands) < 2:
        # Incomplete or unparseable mv — err toward blocking
        return True

    dest = operands[-1]
    cwd = Path(working_directory).expanduser().resolve()
    dest_path = Path(dest)
    if not dest_path.is_absolute():
        dest_path = cwd / dest_path
    try:
        dest_path = dest_path.resolve(strict=False)
    except (OSError, RuntimeError):
        return True

    if dest_path.is_file():
        return True
    return False


def _looks_like_mv(command: str) -> bool:
    cmd = (command or "").strip()
    if not cmd:
        return False
    lowered = cmd.lower()
    if lowered.startswith("mv ") or lowered == "mv":
        return True
    return _MV_WORD.search(lowered) is not None


@dataclass
class SafetyResult:
    verdict: str  # blocked | destructive | write | read
    reason: str


class SafetyChecker:
    def __init__(
        self,
        safety_config: SafetyConfig,
        working_directory: str | None = None,
    ) -> None:
        self._blocked = [
            re.compile(p, re.IGNORECASE) for p in safety_config.blocked_patterns
        ]
        self._destructive = [k.lower() for k in safety_config.destructive_keywords]
        self._read_only = [c.lower() for c in safety_config.read_only_commands]
        self._working_directory = (
            Path(working_directory).resolve() if working_directory else None
        )

    def check(self, command: str) -> SafetyResult:
        cmd = (command or "").strip()
        if not cmd:
            return SafetyResult(verdict="blocked", reason="Empty command")

        # 1. Blocked patterns always win (rm, shred, mv→/dev/null, etc.)
        for pattern in self._blocked:
            if pattern.search(cmd):
                return SafetyResult(
                    verdict="blocked",
                    reason=(
                        "Blocked: this command matches a blocked pattern "
                        "and will never be executed."
                    ),
                )

        # 1b. mv overwrite of an existing file is always blocked
        if _looks_like_mv(cmd):
            cwd = (
                str(self._working_directory)
                if self._working_directory is not None
                else os.getcwd()
            )
            if check_mv_overwrite(cmd, cwd):
                return SafetyResult(
                    verdict="blocked",
                    reason=(
                        "This mv would overwrite an existing file and "
                        "destroy it. Blocked."
                    ),
                )

        # 2. Read-only / system-info commands — never path-confine.
        #    Reads cannot modify anything regardless of which path they touch.
        lowered = cmd.lower()
        for read_cmd in sorted(self._read_only, key=len, reverse=True):
            if lowered == read_cmd or lowered.startswith(read_cmd + " "):
                return SafetyResult(
                    verdict="read",
                    reason=f"Matches read-only command '{read_cmd}'",
                )

        # 3. Destructive keywords (process kill, etc.) — confirm, no path confine
        tokens = self._tokens(cmd)
        for keyword in self._destructive:
            if self._keyword_match(tokens, keyword):
                return SafetyResult(
                    verdict="destructive",
                    reason=f"Contains destructive keyword '{keyword}'",
                )

        # 4. Path confinement applies only to mutating (write) commands.
        #    Skip for mv: relocating outside cwd still needs user confirmation,
        #    but is not an automatic block (overwrite is handled above).
        if not _looks_like_mv(cmd):
            outside = self._outside_working_dir(cmd)
            if outside:
                return SafetyResult(
                    verdict="blocked",
                    reason=(
                        "Blocked: command targets a path outside the "
                        f"working directory ({outside})."
                    ),
                )

        return SafetyResult(verdict="write", reason="Default write classification")

    def merge(self, llm_level: str, safety: SafetyResult) -> SafetyResult:
        """Return the stricter of LLM classification and safety check."""
        llm = (llm_level or "write").lower()
        if llm not in SEVERITY:
            llm = "write"
        if SEVERITY[safety.verdict] >= SEVERITY[llm]:
            return safety
        return SafetyResult(
            verdict=llm,
            reason=f"LLM classified as '{llm}' (stricter than safety '{safety.verdict}')",
        )

    def _tokens(self, command: str) -> list[str]:
        try:
            return [t.lower() for t in shlex.split(command)]
        except ValueError:
            return [t.lower() for t in command.split()]

    def _keyword_match(self, tokens: list[str], keyword: str) -> bool:
        """Match keyword as a whole command token (not a substring of a path)."""
        key = keyword.lower()
        for token in tokens:
            base = Path(token).name
            if token == key or base == key:
                return True
        return False

    def _outside_working_dir(self, command: str) -> str | None:
        """Cheap check: absolute paths that resolve outside working_directory."""
        if self._working_directory is None:
            return None
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()

        for token in tokens:
            if not token.startswith("/") or token.startswith("//"):
                continue
            # Skip common non-path absolute args
            if token in {"/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr"}:
                continue
            try:
                resolved = Path(token).resolve()
            except (OSError, RuntimeError):
                continue
            try:
                resolved.relative_to(self._working_directory)
            except ValueError:
                # Allow system paths that are clearly not filesystem wipes
                # only block obvious destructive absolute targets when combined
                # with mutating verbs — keep conservative: report path.
                if token == "/" or token.startswith("/etc") or token.startswith(
                    "/usr"
                ) or token.startswith("/bin") or token.startswith("/sbin"):
                    return token
                # Relative-looking abs under home still outside cwd
                if not str(resolved).startswith(str(self._working_directory)):
                    return token
        return None
