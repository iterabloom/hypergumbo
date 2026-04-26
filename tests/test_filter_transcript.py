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


class TestConcurrentInvocationSafety:
    """Defense-in-depth: filter-transcript.py must be safe under concurrent
    invocation against the same state file and dest.

    Even if launch-transcript-sync.sh's same-SID idempotence guard fails
    (or is bypassed), running ``main()`` twice concurrently against the
    same SRC / DEST / STATE_FILE must NOT produce duplicate output rows.
    The mechanism is an ``flock`` against the state file that serializes
    the read-modify-write of (offset, dest-append, state-update).

    This is the second layer of the fix for the transcript-doubling bug
    diagnosed against archived session fb7b3494 — see PLAN.md and the
    2026-04-26 retrospective.
    """

    def test_concurrent_subprocess_invocations_no_duplicates(
        self, tmp_path: Path,
    ) -> None:
        """Two ``filter-transcript.py`` subprocesses launched in tight
        succession against the same state file must not double-write the
        same source bytes to the dest.

        Without the ``flock`` guard, both processes read offset 0, both
        filter+append the same N rows, both update state to offset N —
        the dest ends up with 2N rows. With the guard, the second
        process blocks until the first releases the lock, then sees the
        already-advanced offset and writes nothing new.
        """
        import subprocess as _sub

        rows = [
            {"type": "user", "message": f"row-{i}",
             "timestamp": f"2026-04-26T20:{i:02d}:00Z"}
            for i in range(50)
        ]
        src = _make_source(tmp_path, rows)
        dest = tmp_path / "dest.jsonl"
        state_file = tmp_path / "state.json"

        # Run multiple iterations to make race exposure reliable.
        for trial in range(3):
            dest.write_text("")
            state_file.unlink(missing_ok=True)

            procs = [
                _sub.Popen(
                    [sys.executable, str(FILTER_SCRIPT),
                     str(src), str(dest), str(state_file)],
                    stdout=_sub.PIPE, stderr=_sub.PIPE,
                )
                for _ in range(2)
            ]
            for p in procs:
                p.wait(timeout=15)
                assert p.returncode == 0, p.stderr.read().decode()

            written = [
                line for line in dest.read_text().splitlines() if line.strip()
            ]
            user_rows = [
                json.loads(line) for line in written
                if json.loads(line).get("type") == "user"
            ]
            assert len(user_rows) == len(rows), (
                f"trial {trial}: dest contains {len(user_rows)} 'user' rows "
                f"but source has {len(rows)} — concurrent filter invocations "
                f"produced duplicates (the transcript-doubling bug)"
            )

    def test_state_file_lock_serializes_concurrent_writers(
        self, tmp_path: Path,
    ) -> None:
        """Deterministic flock test: while one process holds an exclusive
        ``flock`` on the state file, a second ``filter-transcript.py``
        invocation must block (not race-write) until the lock is released.

        This is the structural guarantee — the empirical test above
        confirms the user-visible behavior, but this test pins the
        mechanism so a future "let's drop the flock" change cannot
        silently regress it.
        """
        import fcntl
        import subprocess as _sub

        rows = [
            {"type": "user", "message": f"row-{i}",
             "timestamp": f"2026-04-26T22:{i:02d}:00Z"}
            for i in range(20)
        ]
        src = _make_source(tmp_path, rows)
        dest = tmp_path / "dest.jsonl"
        state_file = tmp_path / "state.json"
        state_file.write_text('{"offset": 0, "last_bash_hash": ""}')

        # Acquire the flock externally — filter-transcript.py uses the
        # state file path itself as the lock target.
        lock_fd = open(state_file, "r+")
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)

        proc = _sub.Popen(
            [sys.executable, str(FILTER_SCRIPT),
             str(src), str(dest), str(state_file)],
            stdout=_sub.PIPE, stderr=_sub.PIPE,
        )
        try:
            import time as _time
            _time.sleep(0.5)
            assert proc.poll() is None, (
                "filter-transcript.py must block on flock while another "
                "writer holds the lock; instead it ran to completion "
                "(no flock guard or wrong lock target)"
            )
        finally:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            lock_fd.close()

        proc.wait(timeout=10)
        assert proc.returncode == 0, proc.stderr.read().decode()

        # Source rows should be exactly mirrored in dest, no doubles.
        written = [
            line for line in dest.read_text().splitlines() if line.strip()
        ]
        user_rows = [
            json.loads(line) for line in written
            if json.loads(line).get("type") == "user"
        ]
        assert len(user_rows) == len(rows)
