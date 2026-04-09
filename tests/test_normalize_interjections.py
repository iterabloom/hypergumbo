# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for normalize_interjections.py — multi-vendor interjection normalization.

Per WI-nadud: verifies that the normalization module correctly detects and
normalizes user interjections across Claude Code, Codex CLI, and OpenHands
transcript formats into a unified ``normalized_user_interjection`` schema.

Each vendor adapter has different detection characteristics:
- Claude Code: explicit queue-operation events (confidence 1.0)
- Codex CLI: TurnAborted(Interrupted) + user_message sequence (confidence 0.9)
- OpenHands: MessageObservation arriving before prior action closes (confidence 0.8)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Import normalize_interjections.py from .agent/hooks/_shared/
REPO_ROOT = Path(__file__).parent.parent
NORM_SCRIPT = REPO_ROOT / ".agent" / "hooks" / "_shared" / "normalize_interjections.py"


def _import_norm():
    """Import normalize_interjections module."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("normalize_interjections", str(NORM_SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixture rows for each vendor
# ---------------------------------------------------------------------------

# Claude Code: explicit queue-operation events
CC_ENQUEUE = {
    "type": "queue-operation",
    "operation": "enqueue",
    "timestamp": "2026-04-06T19:35:16.666Z",
    "sessionId": "cc-session-1",
    "content": "please also fix the tests",
}
CC_DEQUEUE = {
    "type": "queue-operation",
    "operation": "dequeue",
    "timestamp": "2026-04-06T19:35:17.000Z",
    "sessionId": "cc-session-1",
}
CC_REMOVE = {
    "type": "queue-operation",
    "operation": "remove",
    "timestamp": "2026-04-06T19:35:18.000Z",
    "sessionId": "cc-session-1",
}
CC_USER = {"type": "user", "message": "hello", "timestamp": "2026-04-06T19:30:00Z"}
CC_ASSISTANT = {"type": "assistant", "message": "hi", "timestamp": "2026-04-06T19:30:01Z"}

# Codex CLI: TurnAborted + user_message sequence
CODEX_TOOL_CALL = {
    "type": "function_call",
    "name": "shell",
    "call_id": "call_abc",
    "arguments": '{"command": "pytest"}',
    "timestamp": "2026-04-06T19:30:00Z",
}
CODEX_TURN_ABORTED = {
    "type": "turn_aborted",
    "reason": "interrupted",
    "timestamp": "2026-04-06T19:35:00Z",
}
CODEX_USER_MSG = {
    "type": "user_message",
    "content": "stop that, do this instead",
    "timestamp": "2026-04-06T19:35:01Z",
}
CODEX_ASSISTANT_MSG = {
    "type": "assistant_message",
    "content": "OK, switching to the new task",
    "timestamp": "2026-04-06T19:35:05Z",
}
CODEX_TURN_COMPLETED = {
    "type": "turn_completed",
    "timestamp": "2026-04-06T19:36:00Z",
}

# OpenHands: event-sourced with MessageObservation before action close
OH_CMD_ACTION = {
    "event_type": "CmdRunAction",
    "id": 10,
    "command": "pytest -x",
    "timestamp": "2026-04-06T19:30:00Z",
}
OH_USER_MSG = {
    "event_type": "MessageObservation",
    "id": 11,
    "source": "user",
    "content": "please also check coverage",
    "timestamp": "2026-04-06T19:35:00Z",
}
OH_CMD_OUTPUT = {
    "event_type": "CmdOutputObservation",
    "id": 12,
    "content": "5 passed",
    "cause": 10,
    "timestamp": "2026-04-06T19:36:00Z",
}
OH_AGENT_MSG = {
    "event_type": "MessageObservation",
    "id": 13,
    "source": "agent",
    "content": "I'll check coverage next",
    "timestamp": "2026-04-06T19:36:05Z",
}


# ---------------------------------------------------------------------------
# Schema validation helpers
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = {
    "type",
    "vendor",
    "timestamp",
    "content",
    "runtime_state_at_accept",
    "delivery_semantics",
    "queue_scope",
    "approval_effect",
    "observability",
}

VALID_RUNTIME_STATES = {
    "busy", "streaming", "tool_running", "waiting_approval", "idle_unknown",
}
VALID_DELIVERY_SEMANTICS = {
    "queued", "interrupt", "cancel_restart", "advisory", "unknown",
}
VALID_QUEUE_SCOPES = {
    "after_current_turn", "after_idle", "after_current_tool", "unknown",
}
VALID_APPROVAL_EFFECTS = {
    "none", "implicit_approve", "explicit_approve", "unknown",
}
VALID_SOURCE_KINDS = {
    "explicit_event", "inferred_from_event_history", "heuristic",
}


def _validate_normalized(event: dict) -> None:
    """Assert a normalized event conforms to the schema."""
    assert event["type"] == "normalized_user_interjection"
    for field in REQUIRED_FIELDS:
        assert field in event, f"Missing required field: {field}"

    assert event["vendor"] in ("claude-code", "codex-cli", "openhands")
    assert isinstance(event["timestamp"], str) and len(event["timestamp"]) > 0
    assert isinstance(event["content"], str)
    assert event["runtime_state_at_accept"] in VALID_RUNTIME_STATES
    assert event["delivery_semantics"] in VALID_DELIVERY_SEMANTICS
    assert event["queue_scope"] in VALID_QUEUE_SCOPES
    assert event["approval_effect"] in VALID_APPROVAL_EFFECTS

    obs = event["observability"]
    assert isinstance(obs, dict)
    assert obs["source_kind"] in VALID_SOURCE_KINDS
    assert isinstance(obs["confidence"], (int, float))
    assert 0.0 <= obs["confidence"] <= 1.0

    # raw_refs is optional but if present must be a list
    if "raw_refs" in event:
        assert isinstance(event["raw_refs"], list)


# ===================================================================
# Claude Code adapter tests
# ===================================================================

class TestClaudeCodeNormalization:
    """Claude Code: queue-operation enqueue -> normalized_user_interjection."""

    def test_enqueue_produces_normalized_event(self) -> None:
        mod = _import_norm()
        rows = [CC_USER, CC_ENQUEUE, CC_ASSISTANT]
        result, _state = mod.normalize_rows(rows, vendor="claude-code")

        normalized = [r for r in result if r["type"] == "normalized_user_interjection"]
        assert len(normalized) == 1
        _validate_normalized(normalized[0])

        n = normalized[0]
        assert n["vendor"] == "claude-code"
        assert n["content"] == "please also fix the tests"
        assert n["timestamp"] == "2026-04-06T19:35:16.666Z"
        assert n["delivery_semantics"] == "queued"
        assert n["runtime_state_at_accept"] == "busy"
        assert n["observability"]["source_kind"] == "explicit_event"
        assert n["observability"]["confidence"] == 1.0

    def test_dequeue_does_not_produce_normalized(self) -> None:
        """Only enqueue creates a normalized event, not dequeue."""
        mod = _import_norm()
        rows = [CC_USER, CC_DEQUEUE, CC_ASSISTANT]
        result, _state = mod.normalize_rows(rows, vendor="claude-code")

        normalized = [r for r in result if r["type"] == "normalized_user_interjection"]
        assert len(normalized) == 0

    def test_remove_does_not_produce_normalized(self) -> None:
        """Remove operations don't create normalized events."""
        mod = _import_norm()
        rows = [CC_USER, CC_REMOVE, CC_ASSISTANT]
        result, _state = mod.normalize_rows(rows, vendor="claude-code")

        normalized = [r for r in result if r["type"] == "normalized_user_interjection"]
        assert len(normalized) == 0

    def test_original_rows_preserved(self) -> None:
        """Original rows are kept alongside synthetic normalized rows."""
        mod = _import_norm()
        rows = [CC_USER, CC_ENQUEUE, CC_ASSISTANT]
        result, _state = mod.normalize_rows(rows, vendor="claude-code")

        # All original rows present
        originals = [r for r in result if r["type"] != "normalized_user_interjection"]
        assert len(originals) == 3
        assert originals[0]["type"] == "user"
        assert originals[1]["type"] == "queue-operation"
        assert originals[2]["type"] == "assistant"

    def test_normalized_follows_original(self) -> None:
        """Normalized event is emitted immediately after the original queue-op."""
        mod = _import_norm()
        rows = [CC_USER, CC_ENQUEUE, CC_ASSISTANT]
        result, _state = mod.normalize_rows(rows, vendor="claude-code")

        types = [r["type"] for r in result]
        idx_qop = types.index("queue-operation")
        idx_norm = types.index("normalized_user_interjection")
        assert idx_norm == idx_qop + 1

    def test_multiple_enqueues(self) -> None:
        """Multiple enqueues each produce a normalized event."""
        mod = _import_norm()
        enqueue2 = {
            **CC_ENQUEUE,
            "content": "second interjection",
            "timestamp": "2026-04-06T19:36:00Z",
        }
        rows = [CC_USER, CC_ENQUEUE, enqueue2, CC_ASSISTANT]
        result, _state = mod.normalize_rows(rows, vendor="claude-code")

        normalized = [r for r in result if r["type"] == "normalized_user_interjection"]
        assert len(normalized) == 2
        assert normalized[0]["content"] == "please also fix the tests"
        assert normalized[1]["content"] == "second interjection"

    def test_raw_refs_contains_original(self) -> None:
        """The raw_refs field references the original queue-operation event."""
        mod = _import_norm()
        rows = [CC_USER, CC_ENQUEUE, CC_ASSISTANT]
        result, _state = mod.normalize_rows(rows, vendor="claude-code")

        normalized = [r for r in result if r["type"] == "normalized_user_interjection"]
        assert len(normalized[0]["raw_refs"]) == 1
        assert normalized[0]["raw_refs"][0]["type"] == "queue-operation"
        assert normalized[0]["raw_refs"][0]["operation"] == "enqueue"


# ===================================================================
# Codex CLI adapter tests
# ===================================================================

class TestCodexCliNormalization:
    """Codex CLI: turn_aborted(interrupted) + user_message -> normalized."""

    def test_interrupted_turn_produces_normalized(self) -> None:
        mod = _import_norm()
        rows = [CODEX_TOOL_CALL, CODEX_TURN_ABORTED, CODEX_USER_MSG]
        result, _state = mod.normalize_rows(rows, vendor="codex-cli")

        normalized = [r for r in result if r["type"] == "normalized_user_interjection"]
        assert len(normalized) == 1
        _validate_normalized(normalized[0])

        n = normalized[0]
        assert n["vendor"] == "codex-cli"
        assert n["content"] == "stop that, do this instead"
        assert n["delivery_semantics"] == "interrupt"
        assert n["observability"]["source_kind"] == "inferred_from_event_history"
        assert n["observability"]["confidence"] == 0.9

    def test_user_message_without_abort_not_normalized(self) -> None:
        """A user_message not preceded by turn_aborted is not an interjection."""
        mod = _import_norm()
        rows = [CODEX_TURN_COMPLETED, CODEX_USER_MSG]
        result, _state = mod.normalize_rows(rows, vendor="codex-cli")

        normalized = [r for r in result if r["type"] == "normalized_user_interjection"]
        assert len(normalized) == 0

    def test_turn_aborted_without_user_msg_not_normalized(self) -> None:
        """A turn_aborted without a following user_message is not an interjection."""
        mod = _import_norm()
        rows = [CODEX_TOOL_CALL, CODEX_TURN_ABORTED, CODEX_ASSISTANT_MSG]
        result, _state = mod.normalize_rows(rows, vendor="codex-cli")

        normalized = [r for r in result if r["type"] == "normalized_user_interjection"]
        assert len(normalized) == 0

    def test_original_rows_preserved(self) -> None:
        mod = _import_norm()
        rows = [CODEX_TOOL_CALL, CODEX_TURN_ABORTED, CODEX_USER_MSG]
        result, _state = mod.normalize_rows(rows, vendor="codex-cli")

        originals = [r for r in result if r["type"] != "normalized_user_interjection"]
        assert len(originals) == 3

    def test_normalized_follows_user_message(self) -> None:
        """Normalized event emitted after the user_message (the triggering event)."""
        mod = _import_norm()
        rows = [CODEX_TOOL_CALL, CODEX_TURN_ABORTED, CODEX_USER_MSG]
        result, _state = mod.normalize_rows(rows, vendor="codex-cli")

        types = [r["type"] for r in result]
        idx_user = types.index("user_message")
        idx_norm = types.index("normalized_user_interjection")
        assert idx_norm == idx_user + 1

    def test_raw_refs_contains_both_events(self) -> None:
        """raw_refs includes both the turn_aborted and user_message events."""
        mod = _import_norm()
        rows = [CODEX_TOOL_CALL, CODEX_TURN_ABORTED, CODEX_USER_MSG]
        result, _state = mod.normalize_rows(rows, vendor="codex-cli")

        normalized = [r for r in result if r["type"] == "normalized_user_interjection"]
        refs = normalized[0]["raw_refs"]
        assert len(refs) == 2
        assert refs[0]["type"] == "turn_aborted"
        assert refs[1]["type"] == "user_message"

    def test_non_interrupted_abort_not_normalized(self) -> None:
        """turn_aborted with a reason other than 'interrupted' is not an interjection."""
        mod = _import_norm()
        non_interrupt_abort = {**CODEX_TURN_ABORTED, "reason": "timeout"}
        rows = [CODEX_TOOL_CALL, non_interrupt_abort, CODEX_USER_MSG]
        result, _state = mod.normalize_rows(rows, vendor="codex-cli")

        normalized = [r for r in result if r["type"] == "normalized_user_interjection"]
        assert len(normalized) == 0


# ===================================================================
# OpenHands adapter tests
# ===================================================================

class TestOpenHandsNormalization:
    """OpenHands: MessageObservation(source=user) before action close -> normalized."""

    def test_user_msg_during_action_produces_normalized(self) -> None:
        mod = _import_norm()
        rows = [OH_CMD_ACTION, OH_USER_MSG, OH_CMD_OUTPUT]
        result, _state = mod.normalize_rows(rows, vendor="openhands")

        normalized = [r for r in result if r.get("type") == "normalized_user_interjection"]
        assert len(normalized) == 1
        _validate_normalized(normalized[0])

        n = normalized[0]
        assert n["vendor"] == "openhands"
        assert n["content"] == "please also check coverage"
        assert n["delivery_semantics"] == "unknown"
        assert n["runtime_state_at_accept"] == "tool_running"
        assert n["observability"]["source_kind"] == "inferred_from_event_history"
        assert n["observability"]["confidence"] == 0.8

    def test_user_msg_after_action_close_not_normalized(self) -> None:
        """User message after the action's observation is not an interjection."""
        mod = _import_norm()
        rows = [OH_CMD_ACTION, OH_CMD_OUTPUT, OH_USER_MSG]
        result, _state = mod.normalize_rows(rows, vendor="openhands")

        normalized = [r for r in result if r.get("type") == "normalized_user_interjection"]
        assert len(normalized) == 0

    def test_agent_msg_during_action_not_normalized(self) -> None:
        """Only user-sourced messages are interjections, not agent messages."""
        mod = _import_norm()
        rows = [OH_CMD_ACTION, OH_AGENT_MSG, OH_CMD_OUTPUT]
        result, _state = mod.normalize_rows(rows, vendor="openhands")

        normalized = [r for r in result if r.get("type") == "normalized_user_interjection"]
        assert len(normalized) == 0

    def test_user_msg_no_open_action_not_normalized(self) -> None:
        """User message with no unclosed action is not an interjection."""
        mod = _import_norm()
        rows = [OH_USER_MSG, OH_CMD_ACTION, OH_CMD_OUTPUT]
        result, _state = mod.normalize_rows(rows, vendor="openhands")

        normalized = [r for r in result if r.get("type") == "normalized_user_interjection"]
        assert len(normalized) == 0

    def test_original_rows_preserved(self) -> None:
        mod = _import_norm()
        rows = [OH_CMD_ACTION, OH_USER_MSG, OH_CMD_OUTPUT]
        result, _state = mod.normalize_rows(rows, vendor="openhands")

        originals = [r for r in result if r.get("type") != "normalized_user_interjection"]
        assert len(originals) == 3

    def test_normalized_follows_user_msg(self) -> None:
        """Normalized event emitted after the user MessageObservation."""
        mod = _import_norm()
        rows = [OH_CMD_ACTION, OH_USER_MSG, OH_CMD_OUTPUT]
        result, _state = mod.normalize_rows(rows, vendor="openhands")

        types = [r.get("type", r.get("event_type")) for r in result]
        # Find the MessageObservation (event_type present)
        msg_idx = next(
            i for i, r in enumerate(result)
            if r.get("event_type") == "MessageObservation" and r.get("source") == "user"
        )
        norm_idx = types.index("normalized_user_interjection")
        assert norm_idx == msg_idx + 1

    def test_raw_refs_contains_action_and_message(self) -> None:
        mod = _import_norm()
        rows = [OH_CMD_ACTION, OH_USER_MSG, OH_CMD_OUTPUT]
        result, _state = mod.normalize_rows(rows, vendor="openhands")

        normalized = [r for r in result if r.get("type") == "normalized_user_interjection"]
        refs = normalized[0]["raw_refs"]
        assert len(refs) == 2
        ref_types = {r.get("event_type") for r in refs}
        assert "CmdRunAction" in ref_types
        assert "MessageObservation" in ref_types


# ===================================================================
# Vendor auto-detection tests
# ===================================================================

class TestVendorAutoDetection:
    """Vendor can be auto-detected from row content when not explicitly provided."""

    def test_detect_claude_code(self) -> None:
        mod = _import_norm()
        rows = [CC_USER, CC_ENQUEUE, CC_ASSISTANT]
        vendor = mod.detect_vendor(rows)
        assert vendor == "claude-code"

    def test_detect_codex_cli(self) -> None:
        mod = _import_norm()
        rows = [CODEX_TOOL_CALL, CODEX_TURN_ABORTED, CODEX_USER_MSG]
        vendor = mod.detect_vendor(rows)
        assert vendor == "codex-cli"

    def test_detect_openhands(self) -> None:
        mod = _import_norm()
        rows = [OH_CMD_ACTION, OH_USER_MSG, OH_CMD_OUTPUT]
        vendor = mod.detect_vendor(rows)
        assert vendor == "openhands"

    def test_unknown_vendor(self) -> None:
        mod = _import_norm()
        rows = [{"type": "something_weird", "data": "?"}]
        vendor = mod.detect_vendor(rows)
        assert vendor is None

    def test_auto_detect_used_when_vendor_none(self) -> None:
        """When vendor=None, normalize_rows auto-detects and still works."""
        mod = _import_norm()
        rows = [CC_USER, CC_ENQUEUE, CC_ASSISTANT]
        result, _state = mod.normalize_rows(rows, vendor=None)

        normalized = [r for r in result if r["type"] == "normalized_user_interjection"]
        assert len(normalized) == 1
        assert normalized[0]["vendor"] == "claude-code"


# ===================================================================
# Cross-batch state tests
# ===================================================================

class TestCrossBatchState:
    """Normalization state carries across incremental batches."""

    def test_codex_abort_in_one_batch_user_msg_in_next(self) -> None:
        """turn_aborted at end of batch 1, user_message at start of batch 2."""
        mod = _import_norm()

        # Batch 1: ends with turn_aborted
        batch1 = [CODEX_TOOL_CALL, CODEX_TURN_ABORTED]
        result1, state = mod.normalize_rows(batch1, vendor="codex-cli")
        normalized1 = [r for r in result1 if r["type"] == "normalized_user_interjection"]
        assert len(normalized1) == 0  # no user_msg yet

        # Batch 2: starts with user_message
        batch2 = [CODEX_USER_MSG, CODEX_ASSISTANT_MSG]
        result2, _state2 = mod.normalize_rows(batch2, vendor="codex-cli", state=state)
        normalized2 = [r for r in result2 if r["type"] == "normalized_user_interjection"]
        assert len(normalized2) == 1
        assert normalized2[0]["content"] == "stop that, do this instead"

    def test_openhands_action_in_one_batch_user_msg_in_next(self) -> None:
        """Open action in batch 1, user message in batch 2 before close."""
        mod = _import_norm()

        # Batch 1: opens an action
        batch1 = [OH_CMD_ACTION]
        result1, state = mod.normalize_rows(batch1, vendor="openhands")
        normalized1 = [r for r in result1 if r.get("type") == "normalized_user_interjection"]
        assert len(normalized1) == 0

        # Batch 2: user message while action still open
        batch2 = [OH_USER_MSG, OH_CMD_OUTPUT]
        result2, _state2 = mod.normalize_rows(batch2, vendor="openhands", state=state)
        normalized2 = [r for r in result2 if r.get("type") == "normalized_user_interjection"]
        assert len(normalized2) == 1
        assert normalized2[0]["content"] == "please also check coverage"

    def test_openhands_action_closed_across_batches(self) -> None:
        """Action opened and closed in batch 1 — user msg in batch 2 is NOT interjection."""
        mod = _import_norm()

        batch1 = [OH_CMD_ACTION, OH_CMD_OUTPUT]
        _result1, state = mod.normalize_rows(batch1, vendor="openhands")

        batch2 = [OH_USER_MSG]
        result2, _state2 = mod.normalize_rows(batch2, vendor="openhands", state=state)
        normalized2 = [r for r in result2 if r.get("type") == "normalized_user_interjection"]
        assert len(normalized2) == 0

    def test_state_is_serializable(self) -> None:
        """State dict is JSON-serializable for persistence in filter state file."""
        mod = _import_norm()
        rows = [OH_CMD_ACTION, OH_USER_MSG]
        _result, state = mod.normalize_rows(rows, vendor="openhands")

        # Must be JSON-serializable
        serialized = json.dumps(state)
        deserialized = json.loads(serialized)
        assert isinstance(deserialized, dict)


# ===================================================================
# Empty / edge cases
# ===================================================================

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_rows(self) -> None:
        mod = _import_norm()
        result, state = mod.normalize_rows([], vendor="claude-code")
        assert result == []
        assert isinstance(state, dict)

    def test_no_interjections(self) -> None:
        """Normal conversation without interjections produces no normalized events."""
        mod = _import_norm()
        rows = [CC_USER, CC_ASSISTANT]
        result, _state = mod.normalize_rows(rows, vendor="claude-code")

        normalized = [r for r in result if r["type"] == "normalized_user_interjection"]
        assert len(normalized) == 0
        assert len(result) == 2

    def test_unknown_vendor_passthrough(self) -> None:
        """Unknown vendor passes rows through without normalization."""
        mod = _import_norm()
        rows = [{"type": "foo", "data": "bar"}]
        result, _state = mod.normalize_rows(rows, vendor=None)
        assert result == rows

    def test_enqueue_with_empty_content(self) -> None:
        """Enqueue with empty content still produces normalized event."""
        mod = _import_norm()
        empty_enqueue = {**CC_ENQUEUE, "content": ""}
        rows = [CC_USER, empty_enqueue, CC_ASSISTANT]
        result, _state = mod.normalize_rows(rows, vendor="claude-code")

        normalized = [r for r in result if r["type"] == "normalized_user_interjection"]
        assert len(normalized) == 1
        assert normalized[0]["content"] == ""
