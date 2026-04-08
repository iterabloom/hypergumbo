# SPDX-License-Identifier: AGPL-3.0-or-later
"""Property-based tests for the transcript sync pipeline (ADR-0018).

Tests invariants for:
- filter_new_lines: noise reduction, monotonic offset, idempotency
- parse_selection: exact ID matching, hyphen-to-space fallback, "none" keyword
- recently_injected: compaction invalidation, token-distance eviction
- select_recent_entries: token budget compliance, order preservation

Property tests use tempfile.mkdtemp() for isolation (hypothesis reuses
function-scoped fixtures across examples). Non-property tests use tmp_path.
"""

from __future__ import annotations

import gzip
import importlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, ClassVar

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Import the two hook scripts as modules (they lack .py-friendly names/paths)
# ---------------------------------------------------------------------------

def _import_hook_module(name: str, filename: str):
    """Import a Python file from .agent/hooks/_shared/ as a module."""
    script_path = str(
        Path(__file__).parent.parent / ".agent" / "hooks" / "_shared" / filename
    )
    loader = importlib.machinery.SourceFileLoader(name, script_path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def filter_mod():
    """Import filter-transcript.py as a module."""
    return _import_hook_module("filter_transcript", "filter-transcript.py")


@pytest.fixture(scope="module")
def hook_mod():
    """Import on_transcript_change.py as a module."""
    return _import_hook_module("on_transcript_change", "on_transcript_change.py")


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

def _jsonl_line(obj: dict) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode() + b"\n"


@st.composite
def bash_progress_line(draw: st.DrawFn) -> dict:
    """Generate a transcript bash_progress entry."""
    output = draw(st.text(min_size=0, max_size=200))
    return {
        "type": "progress",
        "data": {"type": "bash_progress", "output": output},
    }


@st.composite
def non_progress_line(draw: st.DrawFn) -> dict:
    """Generate a non-progress transcript entry."""
    line_type = draw(st.sampled_from([
        "user_message", "assistant_message", "tool_result", "system",
    ]))
    return {"type": line_type, "content": draw(st.text(max_size=100))}


@st.composite
def snapshot_line(draw: st.DrawFn) -> dict:
    """Generate a file-history-snapshot entry."""
    return {"type": "file-history-snapshot", "files": [draw(st.text(max_size=30))]}


@st.composite
def transcript_line(draw: st.DrawFn) -> dict:
    """Generate any transcript line type."""
    return draw(st.one_of(bash_progress_line(), non_progress_line(), snapshot_line()))


@st.composite
def transcript_sequence(draw: st.DrawFn) -> list[dict]:
    """Generate a sequence of transcript entries."""
    return draw(st.lists(transcript_line(), min_size=1, max_size=50))




# ---------------------------------------------------------------------------
# filter_new_lines tests
# ---------------------------------------------------------------------------

class TestFilterNewLines:
    """Property tests for filter-transcript.py's filter_new_lines."""

    @given(data=transcript_sequence())
    @settings(max_examples=50)
    def test_output_never_exceeds_input(
        self, data: list[dict], filter_mod: Any,
    ) -> None:
        """Filtered output has at most as many lines as the input."""
        d = tempfile.mkdtemp()
        try:
            src = os.path.join(d, "src.jsonl")
            dest = os.path.join(d, "dest.jsonl")
            with open(src, "wb") as f:
                for obj in data:
                    f.write(_jsonl_line(obj))

            state = {"offset": 0, "last_bash_hash": ""}
            filter_mod.filter_new_lines(src, dest, state)

            src_count = len(data)
            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                with open(dest, "rb") as f:
                    dest_count = len(f.read().strip().split(b"\n"))
            else:
                dest_count = 0
            assert dest_count <= src_count
        finally:
            shutil.rmtree(d)

    @given(data=transcript_sequence())
    @settings(max_examples=50)
    def test_offset_advances_monotonically(
        self, data: list[dict], filter_mod: Any,
    ) -> None:
        """The byte offset in state always advances or stays the same."""
        d = tempfile.mkdtemp()
        try:
            src = os.path.join(d, "src.jsonl")
            dest = os.path.join(d, "dest.jsonl")
            with open(src, "wb") as f:
                for obj in data:
                    f.write(_jsonl_line(obj))

            state = {"offset": 0, "last_bash_hash": ""}
            new_state = filter_mod.filter_new_lines(src, dest, state)
            assert new_state["offset"] >= state["offset"]
        finally:
            shutil.rmtree(d)

    @given(data=transcript_sequence())
    @settings(max_examples=50)
    def test_idempotent_on_second_call(
        self, data: list[dict], filter_mod: Any,
    ) -> None:
        """Running the filter twice with no new data appends nothing."""
        d = tempfile.mkdtemp()
        try:
            src = os.path.join(d, "src.jsonl")
            dest = os.path.join(d, "dest.jsonl")
            with open(src, "wb") as f:
                for obj in data:
                    f.write(_jsonl_line(obj))

            state = {"offset": 0, "last_bash_hash": ""}
            state = filter_mod.filter_new_lines(src, dest, state)
            size_after_first = os.path.getsize(dest) if os.path.exists(dest) else 0

            state = filter_mod.filter_new_lines(src, dest, state)
            size_after_second = os.path.getsize(dest) if os.path.exists(dest) else 0

            assert size_after_second == size_after_first
        finally:
            shutil.rmtree(d)

    @given(data=transcript_sequence())
    @settings(max_examples=50)
    def test_snapshots_never_in_output(
        self, data: list[dict], filter_mod: Any,
    ) -> None:
        """file-history-snapshot lines are always dropped."""
        d = tempfile.mkdtemp()
        try:
            src = os.path.join(d, "src.jsonl")
            dest = os.path.join(d, "dest.jsonl")
            with open(src, "wb") as f:
                for obj in data:
                    f.write(_jsonl_line(obj))

            state = {"offset": 0, "last_bash_hash": ""}
            filter_mod.filter_new_lines(src, dest, state)

            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                with open(dest, "rb") as f:
                    for raw_line in f.read().strip().split(b"\n"):
                        obj = json.loads(raw_line)
                        assert obj.get("type") != "file-history-snapshot"
        finally:
            shutil.rmtree(d)

    @given(data=transcript_sequence())
    @settings(max_examples=50)
    def test_non_progress_lines_preserved(
        self, data: list[dict], filter_mod: Any,
    ) -> None:
        """Non-progress, non-snapshot lines always survive filtering."""
        d = tempfile.mkdtemp()
        try:
            src = os.path.join(d, "src.jsonl")
            dest = os.path.join(d, "dest.jsonl")
            with open(src, "wb") as f:
                for obj in data:
                    f.write(_jsonl_line(obj))

            state = {"offset": 0, "last_bash_hash": ""}
            filter_mod.filter_new_lines(src, dest, state)

            kept_types = set()
            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                with open(dest, "rb") as f:
                    for raw_line in f.read().strip().split(b"\n"):
                        obj = json.loads(raw_line)
                        kept_types.add(obj.get("type"))

            for entry in data:
                t = entry.get("type")
                if t not in ("progress", "file-history-snapshot"):
                    assert t in kept_types
        finally:
            shutil.rmtree(d)

    @given(data=st.lists(bash_progress_line(), min_size=2, max_size=20))
    @settings(max_examples=50)
    def test_empty_bash_progress_dropped(
        self, data: list[dict], filter_mod: Any,
    ) -> None:
        """bash_progress with empty output is never in the output."""
        d = tempfile.mkdtemp()
        try:
            src = os.path.join(d, "src.jsonl")
            dest = os.path.join(d, "dest.jsonl")
            with open(src, "wb") as f:
                for obj in data:
                    f.write(_jsonl_line(obj))

            state = {"offset": 0, "last_bash_hash": ""}
            filter_mod.filter_new_lines(src, dest, state)

            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                with open(dest, "rb") as f:
                    for raw_line in f.read().strip().split(b"\n"):
                        obj = json.loads(raw_line)
                        if obj.get("type") == "progress":
                            output = obj.get("data", {}).get("output", "")
                            assert output != ""
        finally:
            shutil.rmtree(d)

    def test_incremental_append(
        self, tmp_path: Path, filter_mod: Any,
    ) -> None:
        """Filter processes only new bytes when called incrementally."""
        src = tmp_path / "src.jsonl"
        dest = tmp_path / "dest.jsonl"

        batch1 = [
            {"type": "user_message", "content": "hello"},
            {"type": "user_message", "content": "world"},
        ]
        src.write_bytes(b"".join(_jsonl_line(obj) for obj in batch1))
        state = {"offset": 0, "last_bash_hash": ""}
        state = filter_mod.filter_new_lines(str(src), str(dest), state)
        count1 = len(dest.read_bytes().strip().split(b"\n"))

        batch2 = [{"type": "assistant_message", "content": "hi"}]
        with open(src, "ab") as f:
            for obj in batch2:
                f.write(_jsonl_line(obj))

        state = filter_mod.filter_new_lines(str(src), str(dest), state)
        count2 = len(dest.read_bytes().strip().split(b"\n"))

        assert count2 == count1 + 1


# ---------------------------------------------------------------------------
# parse_selection tests
# ---------------------------------------------------------------------------

class TestParseSelection:
    """Tests for on_transcript_change.py's parse_selection."""

    def test_exact_id_match(self, hook_mod: Any) -> None:
        """Exact playbook ID in text is selected."""
        text = "experiment-design-playbook\ncoverage-and-test-placement"
        result = hook_mod.parse_selection(text)
        assert "experiment-design-playbook" in result
        assert "coverage-and-test-placement" in result

    def test_hyphen_to_space_match(self, hook_mod: Any) -> None:
        """Playbook ID with hyphens replaced by spaces is matched."""
        text = "experiment design playbook"
        result = hook_mod.parse_selection(text)
        assert "experiment-design-playbook" in result

    def test_none_keyword_empty_list(self, hook_mod: Any) -> None:
        """The word 'none' with no IDs yields empty list."""
        assert hook_mod.parse_selection("none") == []

    def test_none_with_ids_still_returns_ids(self, hook_mod: Any) -> None:
        """'none' in text alongside actual IDs returns those IDs."""
        text = "none of the others, but experiment-design-playbook is relevant"
        result = hook_mod.parse_selection(text)
        assert "experiment-design-playbook" in result

    def test_empty_string_returns_empty(self, hook_mod: Any) -> None:
        """Empty input yields empty list."""
        assert hook_mod.parse_selection("") == []

    def test_whitespace_only_returns_empty(self, hook_mod: Any) -> None:
        """Whitespace-only input yields empty list."""
        assert hook_mod.parse_selection("   \n  ") == []

    def test_garbage_returns_empty(self, hook_mod: Any) -> None:
        """Unrelated text yields empty list."""
        assert hook_mod.parse_selection("the quick brown fox jumps over the lazy dog") == []

    def test_returns_list(self, hook_mod: Any) -> None:
        """Return type is list[str]."""
        result = hook_mod.parse_selection("experiment-design-playbook")
        assert isinstance(result, list)

    def test_all_playbooks_selectable(self, hook_mod: Any) -> None:
        """Every registered playbook can be selected."""
        text = "\n".join(pb_id for pb_id, _, _ in hook_mod.PLAYBOOKS)
        result = hook_mod.parse_selection(text)
        assert len(result) == len(hook_mod.PLAYBOOKS)

    def test_more_than_three_still_parsed(self, hook_mod: Any) -> None:
        """Response with >3 playbooks still parses all of them."""
        ids = [pb_id for pb_id, _, _ in hook_mod.PLAYBOOKS[:5]]
        text = "\n".join(ids)
        result = hook_mod.parse_selection(text)
        assert len(result) == 5
        for pb_id in ids:
            assert pb_id in result


# ---------------------------------------------------------------------------
# recently_injected tests
# ---------------------------------------------------------------------------

class TestRecentlyInjected:
    """Property tests for on_transcript_change.py's recently_injected."""

    SESSION_TOKEN = "test-session-12345"

    @staticmethod
    def _make_transcript(base: str, lines: list[dict]) -> str:
        """Write a JSONL transcript and return its path."""
        path = os.path.join(base, "transcript.jsonl")
        with open(path, "wb") as f:
            for obj in lines:
                f.write(_jsonl_line(obj))
        return path

    @classmethod
    def _write_session_token(cls, base: str, token: str | None = None) -> None:
        """Write a session token file so state validation passes."""
        agent_dir = os.path.join(base, ".agent")
        os.makedirs(agent_dir, exist_ok=True)
        with open(os.path.join(agent_dir, ".transcript-session-token"), "w") as f:
            f.write(token or cls.SESSION_TOKEN)

    @classmethod
    def _make_state(cls, base: str, state: dict) -> None:
        """Write injection state with matching session token."""
        agent_dir = os.path.join(base, ".agent")
        os.makedirs(agent_dir, exist_ok=True)
        state.setdefault("session_token", cls.SESSION_TOKEN)
        state_path = os.path.join(agent_dir, ".transcript-injection-state.json")
        with open(state_path, "w") as f:
            json.dump(state, f)
        cls._write_session_token(base)

    def test_no_prior_injections_returns_empty(
        self, tmp_path: Path, hook_mod: Any,
    ) -> None:
        """With no state file, nothing is marked as recently injected."""
        transcript = self._make_transcript(str(tmp_path), [
            {"type": "user_message", "content": "hello"},
        ])
        skip, state = hook_mod.recently_injected(
            transcript, ["pb-a", "pb-b"], str(tmp_path),
        )
        assert skip == set()

    @given(
        pb_count=st.integers(min_value=1, max_value=5),
        transcript_growth=st.integers(min_value=0, max_value=100),
    )
    @settings(max_examples=50)
    def test_recent_injections_within_window_are_skipped(
        self, pb_count: int, transcript_growth: int, hook_mod: Any,
    ) -> None:
        """Playbooks injected within the dedup window are returned as 'skip'."""
        d = tempfile.mkdtemp()
        try:
            lines = [{"type": "user_message", "content": f"msg{i}"}
                     for i in range(10 + transcript_growth)]
            transcript = self._make_transcript(d, lines)
            transcript_size = os.path.getsize(transcript)

            pb_ids = [f"pb-{i}" for i in range(pb_count)]
            injections = dict.fromkeys(pb_ids, transcript_size)
            self._make_state(d, {
                "injections": injections,
                "last_compact_offset": 0,
            })

            skip, _ = hook_mod.recently_injected(transcript, pb_ids, d)
            assert skip == set(pb_ids)
        finally:
            shutil.rmtree(d)

    def test_compaction_invalidates_old_injections(
        self, tmp_path: Path, hook_mod: Any,
    ) -> None:
        """Injections before a compact_boundary are evicted."""
        lines = [
            {"type": "user_message", "content": "before"},
            {"type": "system", "subtype": "compact_boundary"},
            {"type": "user_message", "content": "after"},
        ]
        transcript = self._make_transcript(str(tmp_path), lines)

        self._make_state(str(tmp_path), {
            "injections": {"pb-a": 0},
            "last_compact_offset": 0,
        })

        skip, state = hook_mod.recently_injected(
            transcript, ["pb-a"], str(tmp_path),
        )
        assert "pb-a" not in skip
        assert state["last_compact_offset"] > 0

    def test_compaction_preserves_post_compaction_injections(
        self, tmp_path: Path, hook_mod: Any,
    ) -> None:
        """Injections after a compact_boundary survive eviction."""
        lines = [
            {"type": "system", "subtype": "compact_boundary"},
            {"type": "user_message", "content": "after"},
        ]
        transcript = self._make_transcript(str(tmp_path), lines)
        transcript_size = os.path.getsize(transcript)

        self._make_state(str(tmp_path), {
            "injections": {"pb-a": transcript_size},
            "last_compact_offset": 0,
        })

        skip, _ = hook_mod.recently_injected(
            transcript, ["pb-a"], str(tmp_path),
        )
        assert "pb-a" in skip

    def test_token_distance_evicts_old_injections(
        self, tmp_path: Path, hook_mod: Any,
    ) -> None:
        """Injections beyond DEDUP_TOKENS characters are evicted."""
        dedup_chars = int(hook_mod.DEDUP_TOKENS * hook_mod.CHARS_PER_TOKEN)
        big_content = "x" * (dedup_chars + 1000)
        lines = [{"type": "user_message", "content": big_content}]
        transcript = self._make_transcript(str(tmp_path), lines)

        self._make_state(str(tmp_path), {
            "injections": {"pb-a": 0},
            "last_compact_offset": 0,
        })

        skip, _ = hook_mod.recently_injected(
            transcript, ["pb-a"], str(tmp_path),
        )
        assert "pb-a" not in skip

    def test_missing_transcript_returns_empty(
        self, tmp_path: Path, hook_mod: Any,
    ) -> None:
        """Non-existent transcript file yields no skips."""
        skip, _ = hook_mod.recently_injected(
            str(tmp_path / "nonexistent.jsonl"), ["pb-a"], str(tmp_path),
        )
        assert skip == set()

    def test_corrupt_state_file_resets(
        self, tmp_path: Path, hook_mod: Any,
    ) -> None:
        """Corrupt state file is treated as empty state."""
        transcript = self._make_transcript(str(tmp_path), [
            {"type": "user_message", "content": "hi"},
        ])
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(exist_ok=True)
        (agent_dir / ".transcript-injection-state.json").write_text("{invalid json")

        skip, state = hook_mod.recently_injected(
            transcript, ["pb-a"], str(tmp_path),
        )
        assert skip == set()
        assert state["injections"] == {}

    def test_stale_session_token_invalidates_state(
        self, tmp_path: Path, hook_mod: Any,
    ) -> None:
        """Injection state from a prior session (different token) is discarded.

        This is the bug that ADR-0018's session token mechanism fixes:
        byte offsets from an old session's transcript are meaningless
        against the new session's transcript.
        """
        transcript = self._make_transcript(str(tmp_path), [
            {"type": "user_message", "content": "new session"},
        ])

        # Write state with OLD session token and large byte offsets
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(exist_ok=True)
        old_state = {
            "session_token": "old-session-999",
            "injections": {
                "pb-a": 1_200_000,  # Offset larger than new transcript
                "pb-b": 1_100_000,
            },
            "last_compact_offset": 0,
        }
        (agent_dir / ".transcript-injection-state.json").write_text(
            json.dumps(old_state),
        )
        # Write CURRENT session token (different from the state's token)
        (agent_dir / ".transcript-session-token").write_text("new-session-123")

        skip, state = hook_mod.recently_injected(
            transcript, ["pb-a", "pb-b"], str(tmp_path),
        )
        # Stale state should be discarded — nothing skipped
        assert skip == set()
        assert state["injections"] == {}
        assert state["session_token"] == "new-session-123"


# ---------------------------------------------------------------------------
# select_recent_entries tests
# ---------------------------------------------------------------------------

class TestSelectRecentEntries:
    """Property tests for on_transcript_change.py's select_recent_entries."""

    @given(
        line_count=st.integers(min_value=1, max_value=100),
        content_size=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=50)
    def test_output_within_token_budget(
        self, line_count: int, content_size: int, hook_mod: Any,
    ) -> None:
        """Selected text never exceeds MAX_TOKENS * CHARS_PER_TOKEN bytes."""
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "transcript.jsonl")
            with open(path, "wb") as f:
                for _ in range(line_count):
                    f.write(_jsonl_line({"type": "user_message", "content": "a" * content_size}))

            result = hook_mod.select_recent_entries(path)
            max_chars = int(hook_mod.MAX_TOKENS * hook_mod.CHARS_PER_TOKEN)
            # The function selects whole lines, so it may include one line
            # that pushes slightly over the budget. Allow one max-line overshoot.
            max_line_size = len(_jsonl_line({"type": "user_message", "content": "a" * content_size}))
            assert len(result.encode("utf-8")) <= max_chars + max_line_size
        finally:
            shutil.rmtree(d)

    @given(data=st.lists(
        st.integers(min_value=0, max_value=999), min_size=2, max_size=30,
    ))
    @settings(max_examples=50)
    def test_preserves_line_order(
        self, data: list[int], hook_mod: Any,
    ) -> None:
        """Selected lines maintain their original order from the transcript."""
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "transcript.jsonl")
            with open(path, "wb") as f:
                for i in data:
                    f.write(_jsonl_line({"type": "user_message", "seq": i}))

            result = hook_mod.select_recent_entries(path)
            if not result:
                return

            seq_values = []
            for raw_line in result.strip().split("\n"):
                if raw_line.strip():
                    obj = json.loads(raw_line)
                    seq_values.append(obj["seq"])

            # Selected lines must be a contiguous suffix in original order
            assert seq_values == data[-len(seq_values):]
        finally:
            shutil.rmtree(d)

    def test_selects_from_tail(
        self, tmp_path: Path, hook_mod: Any,
    ) -> None:
        """When transcript exceeds budget, the most recent lines are selected."""
        max_chars = int(hook_mod.MAX_TOKENS * hook_mod.CHARS_PER_TOKEN)
        big_line = {"type": "user_message", "content": "x" * max_chars}
        small_line = {"type": "assistant_message", "content": "last"}

        path = tmp_path / "transcript.jsonl"
        path.write_bytes(_jsonl_line(big_line) + _jsonl_line(small_line))

        result = hook_mod.select_recent_entries(str(path))
        assert "last" in result

    def test_missing_file_returns_empty(
        self, tmp_path: Path, hook_mod: Any,
    ) -> None:
        """Non-existent file returns empty string."""
        result = hook_mod.select_recent_entries(str(tmp_path / "nope.jsonl"))
        assert result == ""

    def test_empty_file_returns_empty(
        self, tmp_path: Path, hook_mod: Any,
    ) -> None:
        """Empty file returns empty string."""
        path = tmp_path / "empty.jsonl"
        path.write_bytes(b"")
        result = hook_mod.select_recent_entries(str(path))
        assert result == ""


# ---------------------------------------------------------------------------
# _truncate_to_budget tests
# ---------------------------------------------------------------------------

class TestTruncateToBudget:
    """Tests for on_transcript_change.py's _truncate_to_budget."""

    def test_within_budget_unchanged(self, hook_mod: Any) -> None:
        """Short inputs are returned unchanged."""
        p, r = hook_mod._truncate_to_budget("hello", "world", max_tokens=1000)
        assert p == "hello"
        assert r == "world"

    def test_truncates_longer_field(self, hook_mod: Any) -> None:
        """The longer field is truncated, the shorter one is preserved."""
        long_prompt = "A" * 10000
        short_response = "B" * 100
        max_tokens = 1000
        p, r = hook_mod._truncate_to_budget(long_prompt, short_response, max_tokens)
        assert r == short_response  # short field untouched
        assert len(p) + len(r) <= int(max_tokens * hook_mod.CHARS_PER_TOKEN)

    def test_truncates_from_front(self, hook_mod: Any) -> None:
        """Truncation removes the beginning, preserving the end."""
        prompt = "AAAA_IMPORTANT_END"
        response = "ok"
        # Budget that forces truncation of prompt
        budget_chars = len(response) + 10
        max_tokens = budget_chars / hook_mod.CHARS_PER_TOKEN
        p, r = hook_mod._truncate_to_budget(prompt, response, max_tokens)
        assert p.endswith("IMPORTANT_END") or p.endswith("_END") or p.endswith("END")

    def test_truncates_response_when_longer(self, hook_mod: Any) -> None:
        """When response is longer than prompt, response gets truncated."""
        prompt = "short"
        response = "X" * 10000
        p, r = hook_mod._truncate_to_budget(prompt, response, max_tokens=500)
        assert p == "short"
        assert len(r) < len(response)

    def test_exact_budget_unchanged(self, hook_mod: Any) -> None:
        """Input exactly at budget is not truncated."""
        max_tokens = 100
        max_chars = int(max_tokens * hook_mod.CHARS_PER_TOKEN)
        half = max_chars // 2
        p, r = hook_mod._truncate_to_budget("A" * half, "B" * half, max_tokens)
        assert len(p) == half
        assert len(r) == half


# ---------------------------------------------------------------------------
# Session token tests for filter-transcript.py
# ---------------------------------------------------------------------------

class TestFilterSessionToken:
    """Tests for filter-transcript.py's session token validation."""

    def test_stale_token_resets_filter_state(
        self, tmp_path: Path, filter_mod: Any,
    ) -> None:
        """Filter state from a prior session (different token) resets to zero offset."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()

        # Write state with old token and non-zero offset
        state_path = str(agent_dir / "state.json")
        with open(state_path, "w") as f:
            json.dump({
                "offset": 50000,
                "last_bash_hash": "abc123",
                "session_token": "old-session",
            }, f)

        # Write current session token (different)
        (agent_dir / ".transcript-session-token").write_text("new-session")

        # State path is in .agent/, so _read_session_token will find the token
        state = filter_mod.load_state(state_path)
        assert state["offset"] == 0
        assert state["last_bash_hash"] == ""

    def test_matching_token_preserves_state(
        self, tmp_path: Path, filter_mod: Any,
    ) -> None:
        """Filter state with matching session token is preserved."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()

        state_path = str(agent_dir / "state.json")
        with open(state_path, "w") as f:
            json.dump({
                "offset": 50000,
                "last_bash_hash": "abc123",
                "session_token": "current-session",
            }, f)

        (agent_dir / ".transcript-session-token").write_text("current-session")

        state = filter_mod.load_state(state_path)
        assert state["offset"] == 50000
        assert state["last_bash_hash"] == "abc123"

    def test_no_token_file_preserves_state(
        self, tmp_path: Path, filter_mod: Any,
    ) -> None:
        """Without a session token file, state is preserved (backward compat)."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()

        state_path = str(agent_dir / "state.json")
        with open(state_path, "w") as f:
            json.dump({
                "offset": 50000,
                "last_bash_hash": "abc123",
            }, f)

        # No .transcript-session-token file written
        state = filter_mod.load_state(state_path)
        assert state["offset"] == 50000

    def test_save_embeds_session_token(
        self, tmp_path: Path, filter_mod: Any,
    ) -> None:
        """save_state embeds the current session token in the state file."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()

        (agent_dir / ".transcript-session-token").write_text("my-token")

        state_path = str(agent_dir / "state.json")
        filter_mod.save_state(state_path, {"offset": 100, "last_bash_hash": "x"})

        with open(state_path) as f:
            saved = json.load(f)
        assert saved["session_token"] == "my-token"


# ---------------------------------------------------------------------------
# log_injection_history tests
# ---------------------------------------------------------------------------

class TestInjectionHistory:
    """Verify on_transcript_change.py's log_injection_history sidecar writer.

    The sidecar fixes ADR-0018's "retrospective blindness" gap: previously,
    Claude Code's `additionalContext` injection mechanism never round-tripped
    playbook content into the session transcript JSONL, so a retrospective on
    `.last_session_transcript.jsonl` could not see which playbooks were
    injected, when, or why. The sidecar records every poll's metadata
    (including dedup-skipped and zero-selection polls) so that retrospective
    analysis has a real signal to compute precision against.
    """

    SIDECAR_FILENAME: ClassVar[str] = ".current_injection_history.jsonl"

    def _read_records(self, repo_root: Path) -> list[dict]:
        path = repo_root / ".agent" / self.SIDECAR_FILENAME
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    def _write_session_token(self, repo_root: Path, token: str = "test-token") -> None:
        agent_dir = repo_root / ".agent"
        agent_dir.mkdir(exist_ok=True)
        (agent_dir / ".transcript-session-token").write_text(token)

    def test_writer_appends_record(
        self, tmp_path: Path, hook_mod: Any,
    ) -> None:
        """Basic happy path: writer creates the file and writes one well-formed record."""
        self._write_session_token(tmp_path, "session-A")

        hook_mod.log_injection_history(
            str(tmp_path),
            transcript_offset=12345,
            agent_goals="The agent is implementing watcher leak fix.",
            selected=["pb-a", "pb-b"],
            injected=["pb-a"],
            skipped_dedup=["pb-b"],
            event_id="evt-001",
        )

        records = self._read_records(tmp_path)
        assert len(records) == 1
        rec = records[0]
        assert rec["transcript_offset"] == 12345
        assert rec["agent_goals"] == "The agent is implementing watcher leak fix."
        assert rec["selected"] == ["pb-a", "pb-b"]
        assert rec["injected"] == ["pb-a"]
        assert rec["skipped_dedup"] == ["pb-b"]
        assert rec["event_id"] == "evt-001"
        assert rec["session_token"] == "session-A"
        assert "timestamp" in rec
        assert "distill_model" in rec
        assert "select_model" in rec

    def test_writer_appends_multiple_records(
        self, tmp_path: Path, hook_mod: Any,
    ) -> None:
        """Successive calls append, not overwrite."""
        self._write_session_token(tmp_path)

        for i in range(3):
            hook_mod.log_injection_history(
                str(tmp_path),
                transcript_offset=1000 * i,
                agent_goals=f"goal {i}",
                selected=[f"pb-{i}"],
                injected=[f"pb-{i}"],
                skipped_dedup=[],
                event_id=f"evt-{i}",
            )

        records = self._read_records(tmp_path)
        assert len(records) == 3
        assert [r["event_id"] for r in records] == ["evt-0", "evt-1", "evt-2"]

    def test_writer_handles_oserror_silently(
        self, tmp_path: Path, hook_mod: Any,
    ) -> None:
        """Writer must be best-effort: an unwritable directory does not raise.

        This matches log_training_example's resilience pattern — the pipeline
        should never crash because of a logging hiccup.
        """
        # Create a read-only .agent dir so the writer can't create the file
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        agent_dir.chmod(0o555)  # r-x only
        try:
            # Must NOT raise
            hook_mod.log_injection_history(
                str(tmp_path),
                transcript_offset=0,
                agent_goals="goal",
                selected=[],
                injected=[],
                skipped_dedup=[],
                event_id="evt",
            )
        finally:
            agent_dir.chmod(0o755)  # restore so pytest cleanup works

    def test_records_zero_selection_polls(
        self, tmp_path: Path, hook_mod: Any,
    ) -> None:
        """A poll where the selector returned [] still writes a record.

        This is the precision-measurement case: zero-selection polls are
        actually the correct outcome most of the time (per the selector
        prompt), and we need to count them to compute precision/recall.
        """
        self._write_session_token(tmp_path)

        hook_mod.log_injection_history(
            str(tmp_path),
            transcript_offset=999,
            agent_goals="The agent is reading a config file.",
            selected=[],
            injected=[],
            skipped_dedup=[],
            event_id="evt-empty",
        )

        records = self._read_records(tmp_path)
        assert len(records) == 1
        assert records[0]["selected"] == []
        assert records[0]["injected"] == []
        assert records[0]["skipped_dedup"] == []

    def test_records_dedup_skipped(
        self, tmp_path: Path, hook_mod: Any,
    ) -> None:
        """When dedup suppresses a selected playbook, it lands in skipped_dedup."""
        self._write_session_token(tmp_path)

        hook_mod.log_injection_history(
            str(tmp_path),
            transcript_offset=2000,
            agent_goals="goal",
            selected=["pb-a", "pb-b", "pb-c"],
            injected=["pb-a"],          # only pb-a was actually injected
            skipped_dedup=["pb-b", "pb-c"],  # pb-b and pb-c were already in context
            event_id="evt-dedup",
        )

        records = self._read_records(tmp_path)
        assert records[0]["selected"] == ["pb-a", "pb-b", "pb-c"]
        assert records[0]["injected"] == ["pb-a"]
        assert set(records[0]["skipped_dedup"]) == {"pb-b", "pb-c"}

    def test_sidecar_filename_does_not_match_transcript_glob(self) -> None:
        """The sidecar filename must NOT match `.transcript-*` because that
        glob is used by sync-transcript.sh's per-session reset to wipe
        transient state. The sidecar is supposed to ROTATE (parallel to the
        transcript), not be wiped."""
        import fnmatch

        assert not fnmatch.fnmatch(self.SIDECAR_FILENAME, ".transcript-*"), (
            f"{self.SIDECAR_FILENAME} matches `.transcript-*` glob — it would "
            "be deleted by sync-transcript.sh's session reset, defeating "
            "rotation."
        )


# ---------------------------------------------------------------------------
# sync-transcript.sh archive + sidecar rotation tests (B3)
# ---------------------------------------------------------------------------

def _isolate_shared_scripts(tmp_path: Path) -> Path:
    """Copy sync-transcript.sh + filter-transcript.py into tmp_path so the
    script's `BASH_SOURCE`-based path resolution lands inside the test
    sandbox instead of the real repo.

    Returns the .agent/hooks/_shared/ dir inside tmp_path.
    """
    real_shared = (
        Path(__file__).parent.parent / ".agent" / "hooks" / "_shared"
    )
    shared_dir = tmp_path / ".agent" / "hooks" / "_shared"
    shared_dir.mkdir(parents=True, exist_ok=True)
    for name in ("sync-transcript.sh", "filter-transcript.py"):
        src_file = real_shared / name
        dest_file = shared_dir / name
        shutil.copy(src_file, dest_file)
        dest_file.chmod(0o755)
    return shared_dir


def _run_rotation(tmp_path: Path) -> subprocess.Popen:
    """Spawn sync-transcript.sh in tmp_path so it runs its rotation block.

    Returns the Popen handle so the test can poll for the PID file (which
    signals "rotation done") and then terminate the watcher.
    """
    shared = _isolate_shared_scripts(tmp_path)
    sync_script = shared / "sync-transcript.sh"

    src = tmp_path / "src.jsonl"
    src.write_text("")
    dest = tmp_path / ".agent" / ".current_session_transcript.jsonl"

    proc = subprocess.Popen(
        ["bash", str(sync_script), str(src), str(dest)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Wait for the rotation to complete (PID file appears AFTER rotation)
    pid_file = tmp_path / ".agent" / ".transcript-sync.pid"
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if pid_file.exists() and pid_file.read_text().strip():
            break
        time.sleep(0.05)
    return proc


def _terminate_proc(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


class TestSidecarRotation:
    """Verify sync-transcript.sh's archive-on-rotation logic for the
    transcript and the new injection-history sidecar.

    Each session start should:
      1. Archive the about-to-be-clobbered .second_to_last_* pair into a
         timestamped subdir under .agent/.archived-transcripts/, gzipped,
         with mtime preserved via `touch -r`.
      2. Rotate .last_* → .second_to_last_*.
      3. Rotate .current_* → .last_*.

    These tests cover both the transcript pair AND the parallel injection
    history sidecar pair.
    """

    def test_rotation_archives_second_to_last_pair(
        self, tmp_path: Path,
    ) -> None:
        """When .second_to_last_transcript.jsonl exists at session start,
        it gets gzipped into a timestamped archive subdir."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(parents=True, exist_ok=True)

        # Seed the .second_to_last pair (these should be archived)
        second_tr = agent_dir / ".second_to_last_transcript.jsonl"
        second_tr.write_text('{"type":"user_message","content":"two sessions ago"}\n')
        second_inj = agent_dir / ".second_to_last_injection_history.jsonl"
        second_inj.write_text('{"event_id":"old","selected":["pb-x"]}\n')

        # Also seed .last_* so the rotation has something to demote
        last_tr = agent_dir / ".last_session_transcript.jsonl"
        last_tr.write_text('{"type":"user_message","content":"last session"}\n')
        last_inj = agent_dir / ".last_injection_history.jsonl"
        last_inj.write_text('{"event_id":"recent","selected":[]}\n')

        proc = _run_rotation(tmp_path)
        try:
            archive_dir = agent_dir / ".archived-transcripts"
            assert archive_dir.exists(), "no archive dir was created"

            subdirs = sorted(archive_dir.iterdir())
            assert len(subdirs) == 1, f"expected 1 archive subdir, got {len(subdirs)}"
            archive_subdir = subdirs[0]
            assert archive_subdir.name.startswith("20"), (
                f"archive subdir should be timestamped (20YYMMDD...): {archive_subdir.name}"
            )

            transcript_gz = archive_subdir / "transcript.jsonl.gz"
            inj_gz = archive_subdir / "injection_history.jsonl.gz"
            assert transcript_gz.exists(), "transcript.jsonl.gz missing in archive"
            assert inj_gz.exists(), "injection_history.jsonl.gz missing in archive"

            # Verify content survives the gzip round-trip
            with gzip.open(transcript_gz, "rt") as f:
                assert "two sessions ago" in f.read()
            with gzip.open(inj_gz, "rt") as f:
                assert '"event_id":"old"' in f.read()
        finally:
            _terminate_proc(proc)

    def test_rotation_rotates_sidecar_in_parallel(
        self, tmp_path: Path,
    ) -> None:
        """The injection-history sidecar rotates in parallel with the
        transcript: .current → .last, .last → .second_to_last."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(parents=True, exist_ok=True)

        # Seed only .current_injection_history.jsonl (a fresh first session
        # with no prior history). After rotation, it should appear at
        # .last_injection_history.jsonl.
        current_inj = agent_dir / ".current_injection_history.jsonl"
        current_inj.write_text('{"event_id":"current"}\n')

        proc = _run_rotation(tmp_path)
        try:
            last_inj = agent_dir / ".last_injection_history.jsonl"
            second_inj = agent_dir / ".second_to_last_injection_history.jsonl"

            assert last_inj.exists(), "current_injection_history.jsonl was not rotated to .last"
            assert '"event_id":"current"' in last_inj.read_text()
            assert not second_inj.exists(), (
                ".second_to_last should not exist on first rotation with no prior .last"
            )
            # No archive needed because nothing was at .second_to_last
            archive_dir = agent_dir / ".archived-transcripts"
            assert not archive_dir.exists(), (
                "first-session rotation should NOT create an archive subdir"
            )
        finally:
            _terminate_proc(proc)

    def test_archive_preserves_mtime(self, tmp_path: Path) -> None:
        """The gzipped archive file's mtime equals the source file's mtime
        (validates `touch -r`). The user can read the original session-end
        time via `ls -la` on the archive."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(parents=True, exist_ok=True)

        second_tr = agent_dir / ".second_to_last_transcript.jsonl"
        second_tr.write_text('{"line":1}\n')
        # Backdate the file by 2 hours so we can detect mtime preservation
        backdate = time.time() - 7200
        os.utime(second_tr, (backdate, backdate))
        original_mtime = second_tr.stat().st_mtime

        proc = _run_rotation(tmp_path)
        try:
            archive_dir = agent_dir / ".archived-transcripts"
            subdir = next(archive_dir.iterdir())
            archived = subdir / "transcript.jsonl.gz"
            assert archived.exists()

            archived_mtime = archived.stat().st_mtime
            # Allow 1s slack for filesystem timestamp granularity
            assert abs(archived_mtime - original_mtime) < 1.5, (
                f"archived mtime {archived_mtime} differs from original "
                f"{original_mtime} by more than 1s"
            )
        finally:
            _terminate_proc(proc)

    def test_no_archive_when_nothing_to_archive(
        self, tmp_path: Path,
    ) -> None:
        """First session of a clean repo: only .current_* files exist (or
        none). The rotation must NOT create an empty .archived-transcripts/
        subdir."""
        # Run rotation against a fresh tmp_path with no prior state at all
        proc = _run_rotation(tmp_path)
        try:
            archive_dir = tmp_path / ".agent" / ".archived-transcripts"
            assert not archive_dir.exists(), (
                "fresh repo with no .second_to_last_* files should not "
                "create an archive subdir"
            )
        finally:
            _terminate_proc(proc)


# ---------------------------------------------------------------------------
# Session reset invariant test
# ---------------------------------------------------------------------------

class TestSessionResetInvariant:
    """Verify that session start clears all per-session state files.

    Convention: any file in .agent/ matching .transcript-* is per-session
    transient state.  The glob reset in sync-transcript.sh must catch all
    of them.  This test simulates a pipeline run (creating all known state
    files), then verifies the glob pattern would remove them.
    """

    # All per-session state files that the pipeline creates.
    # If you add a new .transcript-* file, add it here — the test will
    # verify the glob catches it.
    PER_SESSION_FILES: ClassVar[list[str]] = [
        ".transcript-sync.pid",
        ".transcript-sync-state.json",
        ".transcript-poll-state",
        ".transcript-injection-state.json",
        ".transcript-session-token",
    ]

    # Files that must NOT be cleared on session start.
    PERSISTENT_FILES: ClassVar[list[str]] = [
        ".training-data.jsonl",
        ".current_session_transcript.jsonl",  # Cleared separately by explicit rm
        # Injection-history sidecar (B1+B3): rotates parallel to the
        # transcript and must NOT be wiped by the .transcript-* glob.
        ".current_injection_history.jsonl",
        ".last_injection_history.jsonl",
        ".second_to_last_injection_history.jsonl",
        # Archive dir for rotated-out sessions.
        ".archived-transcripts",
    ]

    def test_glob_pattern_catches_all_per_session_files(
        self, tmp_path: Path,
    ) -> None:
        """The glob '.transcript-*' matches every per-session state file."""
        import fnmatch

        for name in self.PER_SESSION_FILES:
            assert fnmatch.fnmatch(name, ".transcript-*"), (
                f"{name} does not match '.transcript-*' — it will survive "
                f"session reset.  Rename it to start with '.transcript-'."
            )

    def test_glob_pattern_spares_persistent_files(
        self, tmp_path: Path,
    ) -> None:
        """The glob '.transcript-*' does NOT match persistent files."""
        import fnmatch

        for name in self.PERSISTENT_FILES:
            assert not fnmatch.fnmatch(name, ".transcript-*"), (
                f"{name} matches '.transcript-*' — it would be deleted on "
                f"session start.  Rename it if it should persist."
            )
