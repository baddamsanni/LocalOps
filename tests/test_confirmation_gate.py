"""Tests for plain-text yes/no ConfirmationGate."""

from unittest.mock import patch

from confirmation_gate import ConfirmationGate


def test_create_pending_then_has_pending():
    gate = ConfirmationGate()
    gate.create_pending(1, "git pull", "Pull latest", "write")
    assert gate.has_pending(1) is True
    assert gate.get_pending_command(1) == "git pull"


def test_resolve_yes_confirmed_and_clears():
    gate = ConfirmationGate()
    gate.create_pending(1, "git pull", "Pull", "write")
    result = gate.resolve(1, "yes")
    assert result.status == "confirmed"
    assert result.command == "git pull"
    assert gate.has_pending(1) is False


def test_resolve_yes_case_insensitive():
    for reply in ("Yes", "YES", "y", "Y"):
        gate = ConfirmationGate()
        gate.create_pending(1, "echo hi", "say hi", "write")
        result = gate.resolve(1, reply)
        assert result.status == "confirmed", reply
        assert result.command == "echo hi"


def test_resolve_no_and_cancel():
    for reply in ("no", "n", "cancel", "NO"):
        gate = ConfirmationGate()
        gate.create_pending(1, "rm ./x", "delete", "destructive")
        result = gate.resolve(1, reply)
        assert result.status == "cancelled", reply
        assert gate.has_pending(1) is False


def test_resolve_unclear_keeps_pending():
    gate = ConfirmationGate()
    gate.create_pending(1, "git pull", "Pull", "write")
    result = gate.resolve(1, "maybe")
    assert result.status == "unclear"
    assert gate.has_pending(1) is True
    assert gate.get_pending_command(1) == "git pull"


def test_has_pending_false_after_timeout():
    gate = ConfirmationGate(timeout_seconds=60)
    gate.create_pending(1, "git pull", "Pull", "write")
    start = gate._pending[1].created_at
    with patch(
        "confirmation_gate.time.monotonic",
        return_value=start + 61,
    ):
        assert gate.has_pending(1) is False
    assert 1 not in gate._pending


def test_resolve_no_pending():
    gate = ConfirmationGate()
    result = gate.resolve(99, "yes")
    assert result.status == "no_pending"


def test_check_timeout_clears_and_reports():
    gate = ConfirmationGate(timeout_seconds=60)
    gate.create_pending(1, "git pull", "Pull", "write")
    start = gate._pending[1].created_at
    with patch(
        "confirmation_gate.time.monotonic",
        return_value=start + 61,
    ):
        assert gate.check_timeout(1) is True
    assert gate.has_pending(1) is False
