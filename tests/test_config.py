"""Tests for config.yml validation and AppConfig loading."""

from pathlib import Path

import pytest
import yaml

from config import AppConfig, ConfigError, load_config

VALID_YAML = {
    "telegram": {
        "bot_token": "123456:ABC-REAL-TOKEN",
        "allowed_user_id": 987654321,
        "poll_interval_seconds": 1,
    },
    "llm": {
        "provider": "ollama",
        "claude": {
            "api_key": "sk-ant-...",
            "model": "claude-sonnet-4-6",
        },
        "ollama": {
            "base_url": "http://localhost:11434",
            "model": "qwen2.5:7b",
        },
        "openai": {
            "api_key": "sk-...",
            "model": "gpt-4o",
            "base_url": "https://api.openai.com/v1",
        },
    },
    "executor": {
        "working_directory": ".",
        "timeout_seconds": 300,
        "max_output_chars": 4000,
    },
    "safety": {
        "blocked_patterns": [r"rm\s+-rf\s+/"],
        "destructive_keywords": ["rm"],
        "read_only_commands": ["ls", "ps"],
    },
    "notifications": {
        "enabled": False,
        "cpu_threshold_percent": 85,
        "memory_threshold_percent": 90,
        "poll_every_seconds": 60,
    },
    "streaming": {
        "edit_throttle_seconds": 1.0,
    },
}


def _write_config(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "config.yml"
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


def test_missing_config_yml(tmp_path: Path):
    missing = tmp_path / "config.yml"
    with pytest.raises(ConfigError) as exc:
        load_config(missing)
    assert "config.yml not found" in str(exc.value)
    assert "config.example.yml" in str(exc.value)


def test_placeholder_bot_token(tmp_path: Path):
    data = yaml.safe_load(yaml.dump(VALID_YAML))
    data["telegram"]["bot_token"] = "YOUR_BOT_TOKEN"
    path = _write_config(tmp_path, data)
    with pytest.raises(ConfigError) as exc:
        load_config(path)
    assert str(exc.value) == (
        "telegram.bot_token is not set. Get one from @BotFather on Telegram."
    )


def test_unknown_provider(tmp_path: Path):
    data = yaml.safe_load(yaml.dump(VALID_YAML))
    data["llm"]["provider"] = "watson"
    path = _write_config(tmp_path, data)
    with pytest.raises(ConfigError) as exc:
        load_config(path)
    assert "llm.provider must be one of: claude, ollama, openai" in str(
        exc.value
    )
    assert "watson" in str(exc.value)


def test_claude_missing_api_key(tmp_path: Path):
    data = yaml.safe_load(yaml.dump(VALID_YAML))
    data["llm"]["provider"] = "claude"
    data["llm"]["claude"]["api_key"] = "sk-ant-..."
    path = _write_config(tmp_path, data)
    with pytest.raises(ConfigError) as exc:
        load_config(path)
    assert str(exc.value) == (
        "llm.claude.api_key is required when provider is claude."
    )


def test_ollama_no_api_key_passes(tmp_path: Path):
    data = yaml.safe_load(yaml.dump(VALID_YAML))
    data["llm"]["provider"] = "ollama"
    path = _write_config(tmp_path, data)
    cfg = load_config(path)
    assert isinstance(cfg, AppConfig)
    assert cfg.llm.provider == "ollama"
    assert cfg.llm.model == "qwen2.5:7b"
    assert cfg.llm.ollama.base_url == "http://localhost:11434"


def test_valid_full_config_loads(tmp_path: Path):
    path = _write_config(tmp_path, VALID_YAML)
    cfg = load_config(path)
    assert cfg.telegram.allowed_user_id == 987654321
    assert cfg.telegram.bot_token == "123456:ABC-REAL-TOKEN"
    assert cfg.llm.provider == "ollama"
    assert cfg.executor.timeout_seconds == 300
    assert cfg.safety.read_only_commands == ["ls", "ps"]
    assert cfg.notifications.enabled is False
    assert cfg.config_path == path.resolve()


def test_placeholder_allowed_user_id(tmp_path: Path):
    data = yaml.safe_load(yaml.dump(VALID_YAML))
    data["telegram"]["allowed_user_id"] = 123456789
    path = _write_config(tmp_path, data)
    with pytest.raises(ConfigError) as exc:
        load_config(path)
    assert "telegram.allowed_user_id is not set" in str(exc.value)


def test_openai_missing_api_key(tmp_path: Path):
    data = yaml.safe_load(yaml.dump(VALID_YAML))
    data["llm"]["provider"] = "openai"
    data["llm"]["openai"]["api_key"] = "sk-..."
    path = _write_config(tmp_path, data)
    with pytest.raises(ConfigError) as exc:
        load_config(path)
    assert str(exc.value) == (
        "llm.openai.api_key is required when provider is openai."
    )
