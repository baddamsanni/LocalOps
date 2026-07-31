"""Load and validate config.yml — single source of truth for all integrations."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PLACEHOLDER_BOT_TOKENS = {"", "YOUR_BOT_TOKEN", "your_bot_token"}
PLACEHOLDER_API_KEYS = {
    "",
    "sk-...",
    "sk-ant-...",
    "YOUR_API_KEY",
    "your_api_key",
}
VALID_PROVIDERS = ("claude", "ollama", "openai")


@dataclass
class TelegramConfig:
    bot_token: str
    allowed_user_id: int
    poll_interval_seconds: float


@dataclass
class ClaudeProviderConfig:
    api_key: str
    model: str


@dataclass
class OllamaProviderConfig:
    base_url: str
    model: str


@dataclass
class OpenAIProviderConfig:
    api_key: str
    model: str
    base_url: str


@dataclass
class LLMConfig:
    provider: str
    claude: ClaudeProviderConfig
    ollama: OllamaProviderConfig
    openai: OpenAIProviderConfig

    @property
    def model(self) -> str:
        if self.provider == "claude":
            return self.claude.model
        if self.provider == "openai":
            return self.openai.model
        return self.ollama.model


@dataclass
class ExecutorConfig:
    working_directory: str
    timeout_seconds: int
    max_output_chars: int


@dataclass
class SafetyConfig:
    blocked_patterns: list[str] = field(default_factory=list)
    destructive_keywords: list[str] = field(default_factory=list)
    read_only_commands: list[str] = field(default_factory=list)


@dataclass
class NotificationsConfig:
    enabled: bool
    cpu_threshold_percent: float
    memory_threshold_percent: float
    poll_every_seconds: float


@dataclass
class StreamingConfig:
    edit_throttle_seconds: float = 1.0


@dataclass
class AppConfig:
    telegram: TelegramConfig
    llm: LLMConfig
    executor: ExecutorConfig
    safety: SafetyConfig
    notifications: NotificationsConfig
    streaming: StreamingConfig
    config_path: Path


# Back-compat alias used by older imports in the codebase
Config = AppConfig


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


def _require_dict(data: Any, key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"config.yml: missing or invalid '{key}' section")
    return value


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"config.yml: '{key}' must be a mapping")
    return value


def _load_notifications(notif_raw: dict[str, Any]) -> NotificationsConfig:
    if not notif_raw:
        return NotificationsConfig(
            enabled=False,
            cpu_threshold_percent=0,
            memory_threshold_percent=0,
            poll_every_seconds=0,
        )
    for key in (
        "enabled",
        "cpu_threshold_percent",
        "memory_threshold_percent",
        "poll_every_seconds",
    ):
        if key not in notif_raw:
            raise ConfigError(f"notifications.{key} is required")
    return NotificationsConfig(
        enabled=bool(notif_raw["enabled"]),
        cpu_threshold_percent=float(notif_raw["cpu_threshold_percent"]),
        memory_threshold_percent=float(notif_raw["memory_threshold_percent"]),
        poll_every_seconds=float(notif_raw["poll_every_seconds"]),
    )


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load config.yml from path (default: ./config.yml)."""
    config_path = Path(path) if path else Path("config.yml")
    if not config_path.is_file():
        raise ConfigError(
            "config.yml not found. Copy config.example.yml to config.yml "
            "and fill in your values."
        )

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path} must contain a YAML mapping at the root")

    tg_raw = _require_dict(raw, "telegram")
    llm_raw = _require_dict(raw, "llm")
    exec_raw = _require_dict(raw, "executor")
    safety_raw = _require_dict(raw, "safety")
    notif_raw = _section(raw, "notifications")
    streaming_raw = _section(raw, "streaming")

    bot_token = str(tg_raw.get("bot_token") or "").strip()
    if bot_token in PLACEHOLDER_BOT_TOKENS:
        raise ConfigError(
            "telegram.bot_token is not set. Get one from @BotFather on Telegram."
        )

    allowed_user_id = tg_raw.get("allowed_user_id")
    if allowed_user_id is None or allowed_user_id == 123456789:
        raise ConfigError(
            "telegram.allowed_user_id is not set. Message @userinfobot "
            "on Telegram to find yours."
        )
    try:
        allowed_user_id = int(allowed_user_id)
    except (TypeError, ValueError) as exc:
        raise ConfigError("telegram.allowed_user_id must be an integer") from exc

    if "poll_interval_seconds" not in tg_raw:
        raise ConfigError("telegram.poll_interval_seconds is required")

    provider = str(llm_raw.get("provider") or "").lower().strip()
    if provider not in VALID_PROVIDERS:
        raise ConfigError(
            f"llm.provider must be one of: claude, ollama, openai. Got: {provider!r}"
        )

    claude_raw = _section(llm_raw, "claude")
    ollama_raw = _section(llm_raw, "ollama")
    openai_raw = _section(llm_raw, "openai")

    claude = ClaudeProviderConfig(
        api_key=str(claude_raw.get("api_key") or "").strip(),
        model=str(claude_raw.get("model") or "").strip(),
    )
    ollama = OllamaProviderConfig(
        base_url=str(ollama_raw.get("base_url") or "").strip().rstrip("/"),
        model=str(ollama_raw.get("model") or "").strip(),
    )
    openai = OpenAIProviderConfig(
        api_key=str(openai_raw.get("api_key") or "").strip(),
        model=str(openai_raw.get("model") or "").strip(),
        base_url=str(openai_raw.get("base_url") or "").strip().rstrip("/"),
    )

    if provider == "claude":
        if claude.api_key in PLACEHOLDER_API_KEYS:
            raise ConfigError(
                "llm.claude.api_key is required when provider is claude."
            )
        if not claude.model:
            raise ConfigError(
                "llm.claude.model is required when provider is claude."
            )
    elif provider == "openai":
        if openai.api_key in PLACEHOLDER_API_KEYS:
            raise ConfigError(
                "llm.openai.api_key is required when provider is openai."
            )
        if not openai.model:
            raise ConfigError(
                "llm.openai.model is required when provider is openai."
            )
        if not openai.base_url:
            raise ConfigError(
                "llm.openai.base_url is required when provider is openai."
            )
    else:  # ollama
        if not ollama.base_url:
            raise ConfigError(
                "llm.ollama.base_url is required when provider is ollama."
            )
        if not ollama.model:
            raise ConfigError(
                "llm.ollama.model is required when provider is ollama."
            )

    for key in ("working_directory", "timeout_seconds", "max_output_chars"):
        if key not in exec_raw:
            raise ConfigError(f"executor.{key} is required")

    working_directory = str(exec_raw["working_directory"])
    resolved_cwd = Path(working_directory).expanduser().resolve()
    if not resolved_cwd.is_dir():
        raise ConfigError(
            f"executor.working_directory does not exist: {resolved_cwd}"
        )

    return AppConfig(
        telegram=TelegramConfig(
            bot_token=bot_token,
            allowed_user_id=allowed_user_id,
            poll_interval_seconds=float(tg_raw["poll_interval_seconds"]),
        ),
        llm=LLMConfig(
            provider=provider,
            claude=claude,
            ollama=ollama,
            openai=openai,
        ),
        executor=ExecutorConfig(
            working_directory=str(resolved_cwd),
            timeout_seconds=int(exec_raw["timeout_seconds"]),
            max_output_chars=int(exec_raw["max_output_chars"]),
        ),
        safety=SafetyConfig(
            blocked_patterns=list(safety_raw.get("blocked_patterns") or []),
            destructive_keywords=list(
                safety_raw.get("destructive_keywords") or []
            ),
            read_only_commands=list(safety_raw.get("read_only_commands") or []),
        ),
        notifications=_load_notifications(notif_raw),
        streaming=StreamingConfig(
            edit_throttle_seconds=float(
                streaming_raw.get("edit_throttle_seconds", 1.0)
            ),
        ),
        config_path=config_path.resolve(),
    )


def load_config_or_exit(path: str | Path | None = None) -> AppConfig:
    """Load config or print a clear error and exit."""
    try:
        return load_config(path)
    except ConfigError as exc:
        print(f"Configuration error:\n{exc}", file=sys.stderr)
        sys.exit(1)
