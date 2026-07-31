"""Tests for CMD: format parsing."""

import logging

from routing import parse_llm_reply


def test_clean_cmd_prefix():
    parsed = parse_llm_reply("CMD: df -h")
    assert parsed.kind == "command"
    assert parsed.text == "df -h"


def test_plain_chat_no_cmd():
    parsed = parse_llm_reply("Hey! What can I help you with?")
    assert parsed.kind == "chat"
    assert "help" in parsed.text.lower()


def test_empty_reply():
    parsed = parse_llm_reply("   ")
    assert parsed.kind == "empty"


def test_cmd_with_extra_text_before(caplog):
    with caplog.at_level(logging.WARNING):
        parsed = parse_llm_reply("Sure, here you go: CMD: ls -la")
    assert parsed.kind == "command"
    assert parsed.text == "ls -la"
    assert any("Extra text around CMD:" in r.message for r in caplog.records)


def test_cmd_with_trailing_commentary():
    parsed = parse_llm_reply("CMD: echo hello\nHope that helps!")
    assert parsed.kind == "command"
    assert parsed.text == "echo hello"
