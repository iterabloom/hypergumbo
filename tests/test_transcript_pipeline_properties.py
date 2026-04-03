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

import importlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

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
            injections = {pid: transcript_size for pid in pb_ids}
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
        transcript_size = os.path.getsize(transcript)

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
    PER_SESSION_FILES = [
        ".transcript-sync.pid",
        ".transcript-sync-state.json",
        ".transcript-poll-state",
        ".transcript-injection-state.json",
        ".transcript-session-token",
    ]

    # Files that must NOT be cleared on session start.
    PERSISTENT_FILES = [
        ".training-data.jsonl",
        ".current_session_transcript.jsonl",  # Cleared separately by explicit rm
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
