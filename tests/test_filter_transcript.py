# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression tests for filter-transcript.py (queue-operation row preservation).

Per WI-mukot: verifies that filter-transcript.py preserves queue-operation
rows (real-time human interjections during long agent turns).  Without this
test, a future "let's tighten the filter" change could silently drop
interjection rows and quietly degrade the synced transcripts.

These tests import filter-transcript.py's functions directly rather than
using subprocess, which allows verifying internal behavior.  The script
lives in .agent/hooks/_shared/filter-transcript.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Import filter-transcript.py from .agent/hooks/_shared/
REPO_ROOT = Path(__file__).parent.parent
FILTER_SCRIPT = REPO_ROOT / ".agent" / "hooks" / "_shared" / "filter-transcript.py"


@pytest.fixture(autouse=True)
def _add_filter_to_path():
    """Temporarily add the filter script's directory to sys.path."""
    script_dir = str(FILTER_SCRIPT.parent)
    sys.path.insert(0, script_dir)
    yield
    sys.path.remove(script_dir)


def _import_filter():
    """Import filter-transcript module (must be called after path fixture)."""
    # Use importlib to handle the hyphenated filename
    import importlib.util
    spec = importlib.util.spec_from_file_location("filter_transcript", str(FILTER_SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_source(tmp_path: Path, rows: list[dict]) -> Path:
    """Write a JSONL source file from a list of dicts."""
    src = tmp_path / "source.jsonl"
    with open(src, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return src


# --- Fixture rows covering each type ---

USER_ROW = {"type": "user", "message": "hello", "timestamp": "2026-04-06T19:30:00Z"}
ASSISTANT_ROW = {"type": "assistant", "message": "hi", "timestamp": "2026-04-06T19:30:01Z"}
SYSTEM_ROW = {"type": "system", "content": "reminder", "timestamp": "2026-04-06T19:30:02Z"}
QUEUE_OP_ENQUEUE = {
    "type": "queue-operation",
    "operation": "enqueue",
    "timestamp": "2026-04-06T19:35:16.666Z",
    "sessionId": "test-session-id",
    "content": "user typed this mid-turn",
}
QUEUE_OP_REMOVE = {
    "type": "queue-operation",
    "operation": "remove",
    "timestamp": "2026-04-06T19:35:17.000Z",
    "sessionId": "test-session-id",
}
BASH_PROGRESS_ROW = {
    "type": "progress",
    "data": {"type": "bash_progress", "output": "some output"},
}
FILE_HISTORY_ROW = {"type": "file-history-snapshot", "files": ["a.py", "b.py"]}


class TestQueueOperationPreservation:
    """Queue-operation rows must pass through the filter unchanged."""

    def test_queue_operation_enqueue_preserved(self, tmp_path: Path) -> None:
        mod = _import_filter()
        src = _make_source(tmp_path, [USER_ROW, QUEUE_OP_ENQUEUE, ASSISTANT_ROW])
        dest = tmp_path / "dest.jsonl"
        state = {"offset": 0, "last_bash_hash": ""}

        mod.filter_new_lines(str(src), str(dest), state)

        lines = dest.read_text().strip().split("\n")
        parsed = [json.loads(line) for line in lines]
        queue_ops = [r for r in parsed if r.get("type") == "queue-operation"]
        assert len(queue_ops) == 1
        assert queue_ops[0]["operation"] == "enqueue"
        assert queue_ops[0]["content"] == "user typed this mid-turn"

    def test_queue_operation_remove_preserved(self, tmp_path: Path) -> None:
        mod = _import_filter()
        src = _make_source(tmp_path, [USER_ROW, QUEUE_OP_REMOVE, ASSISTANT_ROW])
        dest = tmp_path / "dest.jsonl"
        state = {"offset": 0, "last_bash_hash": ""}

        mod.filter_new_lines(str(src), str(dest), state)

        lines = dest.read_text().strip().split("\n")
        parsed = [json.loads(line) for line in lines]
        queue_ops = [r for r in parsed if r.get("type") == "queue-operation"]
        assert len(queue_ops) == 1
        assert queue_ops[0]["operation"] == "remove"

    def test_both_enqueue_and_remove_preserved(self, tmp_path: Path) -> None:
        mod = _import_filter()
        src = _make_source(
            tmp_path,
            [USER_ROW, QUEUE_OP_ENQUEUE, QUEUE_OP_REMOVE, ASSISTANT_ROW],
        )
        dest = tmp_path / "dest.jsonl"
        state = {"offset": 0, "last_bash_hash": ""}

        mod.filter_new_lines(str(src), str(dest), state)

        lines = dest.read_text().strip().split("\n")
        parsed = [json.loads(line) for line in lines]
        queue_ops = [r for r in parsed if r.get("type") == "queue-operation"]
        assert len(queue_ops) == 2
        assert queue_ops[0]["operation"] == "enqueue"
        assert queue_ops[1]["operation"] == "remove"


class TestFilterRulesIntact:
    """Verify bash_progress and file-history-snapshot are still filtered."""

    def test_bash_progress_dropped(self, tmp_path: Path) -> None:
        mod = _import_filter()
        src = _make_source(
            tmp_path,
            [USER_ROW, BASH_PROGRESS_ROW, ASSISTANT_ROW],
        )
        dest = tmp_path / "dest.jsonl"
        state = {"offset": 0, "last_bash_hash": ""}

        mod.filter_new_lines(str(src), str(dest), state)

        lines = dest.read_text().strip().split("\n")
        parsed = [json.loads(line) for line in lines]
        # bash_progress is buffered and flushed before the next non-bash line
        # so it WILL appear in output (Rule 4: keep last bash before non-bash)
        progress = [r for r in parsed if r.get("type") == "progress"]
        assert len(progress) == 1  # the buffered bash_progress was flushed

    def test_empty_bash_progress_dropped(self, tmp_path: Path) -> None:
        mod = _import_filter()
        empty_bash = {
            "type": "progress",
            "data": {"type": "bash_progress", "output": ""},
        }
        src = _make_source(
            tmp_path,
            [USER_ROW, empty_bash, ASSISTANT_ROW],
        )
        dest = tmp_path / "dest.jsonl"
        state = {"offset": 0, "last_bash_hash": ""}

        mod.filter_new_lines(str(src), str(dest), state)

        lines = dest.read_text().strip().split("\n")
        parsed = [json.loads(line) for line in lines]
        progress = [r for r in parsed if r.get("type") == "progress"]
        assert len(progress) == 0  # empty output → dropped entirely

    def test_file_history_snapshot_dropped(self, tmp_path: Path) -> None:
        mod = _import_filter()
        src = _make_source(
            tmp_path,
            [USER_ROW, FILE_HISTORY_ROW, ASSISTANT_ROW],
        )
        dest = tmp_path / "dest.jsonl"
        state = {"offset": 0, "last_bash_hash": ""}

        mod.filter_new_lines(str(src), str(dest), state)

        lines = dest.read_text().strip().split("\n")
        parsed = [json.loads(line) for line in lines]
        snapshots = [r for r in parsed if r.get("type") == "file-history-snapshot"]
        assert len(snapshots) == 0


class TestRowOrderPreservation:
    """Filter must preserve the relative order of kept rows."""

    def test_order_preserved(self, tmp_path: Path) -> None:
        mod = _import_filter()
        rows = [
            USER_ROW,
            QUEUE_OP_ENQUEUE,
            SYSTEM_ROW,
            QUEUE_OP_REMOVE,
            ASSISTANT_ROW,
        ]
        src = _make_source(tmp_path, rows)
        dest = tmp_path / "dest.jsonl"
        state = {"offset": 0, "last_bash_hash": ""}

        mod.filter_new_lines(str(src), str(dest), state)

        lines = dest.read_text().strip().split("\n")
        parsed = [json.loads(line) for line in lines]
        types = [r["type"] for r in parsed]
        # normalized_user_interjection follows the enqueue (WI-nadud)
        assert types == [
            "user",
            "queue-operation",
            "normalized_user_interjection",
            "system",
            "queue-operation",
            "assistant",
        ]


class TestFullFixtureMix:
    """Comprehensive test: one of each row type, verify correct filtering."""

    def test_full_mix(self, tmp_path: Path) -> None:
        mod = _import_filter()
        rows = [
            USER_ROW,
            ASSISTANT_ROW,
            SYSTEM_ROW,
            QUEUE_OP_ENQUEUE,
            QUEUE_OP_REMOVE,
            BASH_PROGRESS_ROW,  # has non-empty output → buffered
            FILE_HISTORY_ROW,   # dropped
            ASSISTANT_ROW,      # triggers flush of buffered bash
        ]
        src = _make_source(tmp_path, rows)
        dest = tmp_path / "dest.jsonl"
        state = {"offset": 0, "last_bash_hash": ""}

        mod.filter_new_lines(str(src), str(dest), state)

        lines = dest.read_text().strip().split("\n")
        parsed = [json.loads(line) for line in lines]

        # file-history-snapshot dropped
        assert not any(r.get("type") == "file-history-snapshot" for r in parsed)

        # queue-operation rows preserved
        queue_ops = [r for r in parsed if r.get("type") == "queue-operation"]
        assert len(queue_ops) == 2

        # user, assistant, system all preserved
        assert any(r.get("type") == "user" for r in parsed)
        assert any(r.get("type") == "system" for r in parsed)

        # bash_progress flushed (Rule 4: last bash before non-bash)
        progress = [r for r in parsed if r.get("type") == "progress"]
        assert len(progress) == 1


class TestNormalizationIntegration:
    """filter_new_lines emits normalized_user_interjection alongside queue-operation."""

    def test_enqueue_produces_normalized_in_output(self, tmp_path: Path) -> None:
        """Queue-operation enqueue in source -> normalized event in dest JSONL."""
        mod = _import_filter()
        src = _make_source(tmp_path, [USER_ROW, QUEUE_OP_ENQUEUE, ASSISTANT_ROW])
        dest = tmp_path / "dest.jsonl"
        state = {"offset": 0, "last_bash_hash": ""}

        mod.filter_new_lines(str(src), str(dest), state)

        lines = dest.read_text().strip().split("\n")
        parsed = [json.loads(line) for line in lines]
        normalized = [r for r in parsed if r.get("type") == "normalized_user_interjection"]
        assert len(normalized) == 1
        assert normalized[0]["vendor"] == "claude-code"
        assert normalized[0]["content"] == "user typed this mid-turn"

    def test_normalized_follows_queue_op_in_output(self, tmp_path: Path) -> None:
        """Normalized event appears right after the queue-operation in JSONL output."""
        mod = _import_filter()
        src = _make_source(tmp_path, [USER_ROW, QUEUE_OP_ENQUEUE, ASSISTANT_ROW])
        dest = tmp_path / "dest.jsonl"
        state = {"offset": 0, "last_bash_hash": ""}

        mod.filter_new_lines(str(src), str(dest), state)

        lines = dest.read_text().strip().split("\n")
        parsed = [json.loads(line) for line in lines]
        types = [r["type"] for r in parsed]
        idx_qop = types.index("queue-operation")
        idx_norm = types.index("normalized_user_interjection")
        assert idx_norm == idx_qop + 1

    def test_remove_does_not_produce_normalized(self, tmp_path: Path) -> None:
        """Queue-operation remove -> no normalized event."""
        mod = _import_filter()
        src = _make_source(tmp_path, [USER_ROW, QUEUE_OP_REMOVE, ASSISTANT_ROW])
        dest = tmp_path / "dest.jsonl"
        state = {"offset": 0, "last_bash_hash": ""}

        mod.filter_new_lines(str(src), str(dest), state)

        lines = dest.read_text().strip().split("\n")
        parsed = [json.loads(line) for line in lines]
        normalized = [r for r in parsed if r.get("type") == "normalized_user_interjection"]
        assert len(normalized) == 0

    def test_no_interjections_no_normalized(self, tmp_path: Path) -> None:
        """Normal conversation without queue-ops -> no normalized events."""
        mod = _import_filter()
        src = _make_source(tmp_path, [USER_ROW, ASSISTANT_ROW])
        dest = tmp_path / "dest.jsonl"
        state = {"offset": 0, "last_bash_hash": ""}

        mod.filter_new_lines(str(src), str(dest), state)

        lines = dest.read_text().strip().split("\n")
        parsed = [json.loads(line) for line in lines]
        normalized = [r for r in parsed if r.get("type") == "normalized_user_interjection"]
        assert len(normalized) == 0

    def test_normalization_state_persisted(self, tmp_path: Path) -> None:
        """Normalization state is included in the returned state dict."""
        mod = _import_filter()
        src = _make_source(tmp_path, [USER_ROW, QUEUE_OP_ENQUEUE, ASSISTANT_ROW])
        dest = tmp_path / "dest.jsonl"
        state = {"offset": 0, "last_bash_hash": ""}

        new_state = mod.filter_new_lines(str(src), str(dest), state)
        # State should include normalization state
        assert "norm_vendor" in new_state
        assert "norm_state" in new_state
        assert new_state["norm_vendor"] == "claude-code"
