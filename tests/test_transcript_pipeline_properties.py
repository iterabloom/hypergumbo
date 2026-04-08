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
    """Property tests for on_transcript_change.py's recently_injected.

    Per-session isolation (ADR-0018 amendment / Option 2): each test
    uses an explicit session_id and a per-session injection-state file
    path. The legacy global session-token mechanism has been removed
    because per-session paths already encode session identity.
    """

    SESSION_ID = "test-session-12345"

    @classmethod
    def _make_transcript(cls, base: str, lines: list[dict]) -> str:
        """Write a per-session JSONL transcript and return its path.

        The path matches the per-session naming convention so that
        on_transcript_change.py can extract the session_id from it
        (which is what main() does at runtime).
        """
        agent_dir = os.path.join(base, ".agent")
        os.makedirs(agent_dir, exist_ok=True)
        path = os.path.join(
            agent_dir,
            f".current_session_transcript.{cls.SESSION_ID}.jsonl",
        )
        with open(path, "wb") as f:
            for obj in lines:
                f.write(_jsonl_line(obj))
        return path

    @classmethod
    def _make_state(cls, base: str, state: dict) -> None:
        """Write per-session injection state for SESSION_ID."""
        agent_dir = os.path.join(base, ".agent")
        os.makedirs(agent_dir, exist_ok=True)
        state.setdefault("session_id", cls.SESSION_ID)
        state_path = os.path.join(
            agent_dir,
            f".transcript-injection-state.{cls.SESSION_ID}.json",
        )
        with open(state_path, "w") as f:
            json.dump(state, f)

    def test_no_prior_injections_returns_empty(
        self, tmp_path: Path, hook_mod: Any,
    ) -> None:
        """With no state file, nothing is marked as recently injected."""
        transcript = self._make_transcript(str(tmp_path), [
            {"type": "user_message", "content": "hello"},
        ])
        skip, state = hook_mod.recently_injected(
            transcript, ["pb-a", "pb-b"], str(tmp_path), self.SESSION_ID,
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

            skip, _ = hook_mod.recently_injected(
                transcript, pb_ids, d, self.SESSION_ID,
            )
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
            transcript, ["pb-a"], str(tmp_path), self.SESSION_ID,
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
            transcript, ["pb-a"], str(tmp_path), self.SESSION_ID,
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
            transcript, ["pb-a"], str(tmp_path), self.SESSION_ID,
        )
        assert "pb-a" not in skip

    def test_missing_transcript_returns_empty(
        self, tmp_path: Path, hook_mod: Any,
    ) -> None:
        """Non-existent transcript file yields no skips."""
        skip, _ = hook_mod.recently_injected(
            str(tmp_path / "nonexistent.jsonl"),
            ["pb-a"],
            str(tmp_path),
            self.SESSION_ID,
        )
        assert skip == set()

    def test_corrupt_state_file_resets(
        self, tmp_path: Path, hook_mod: Any,
    ) -> None:
        """Corrupt per-session state file is treated as empty state."""
        transcript = self._make_transcript(str(tmp_path), [
            {"type": "user_message", "content": "hi"},
        ])
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(exist_ok=True)
        (
            agent_dir / f".transcript-injection-state.{self.SESSION_ID}.json"
        ).write_text("{invalid json")

        skip, state = hook_mod.recently_injected(
            transcript, ["pb-a"], str(tmp_path), self.SESSION_ID,
        )
        assert skip == set()
        assert state["injections"] == {}

    def test_concurrent_sessions_have_independent_state(
        self, tmp_path: Path, hook_mod: Any,
    ) -> None:
        """Two sessions in the same repo have independent injection
        state. Session B's dedup state must not be affected by session
        A's prior injections."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(exist_ok=True)

        sid_a = "concurrent-A"
        sid_b = "concurrent-B"

        # Session A's transcript and saturated state
        path_a = os.path.join(
            agent_dir,
            f".current_session_transcript.{sid_a}.jsonl",
        )
        with open(path_a, "wb") as f:
            f.write(_jsonl_line({"type": "user_message", "content": "A"}))
        state_a_path = os.path.join(
            agent_dir,
            f".transcript-injection-state.{sid_a}.json",
        )
        with open(state_a_path, "w") as f:
            json.dump({
                "session_id": sid_a,
                "injections": {"pb-a": os.path.getsize(path_a)},
                "last_compact_offset": 0,
            }, f)

        # Session B's transcript with NO state at all
        path_b = os.path.join(
            agent_dir,
            f".current_session_transcript.{sid_b}.jsonl",
        )
        with open(path_b, "wb") as f:
            f.write(_jsonl_line({"type": "user_message", "content": "B"}))

        # Session B asking about pb-a should NOT inherit session A's
        # "already injected" state.
        skip_b, _ = hook_mod.recently_injected(
            path_b, ["pb-a"], str(tmp_path), sid_b,
        )
        assert skip_b == set(), (
            "session B inherited session A's dedup state — per-session "
            "isolation broken"
        )

        # Session A's state is still intact
        skip_a, _ = hook_mod.recently_injected(
            path_a, ["pb-a"], str(tmp_path), sid_a,
        )
        assert skip_a == {"pb-a"}


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

class TestFilterPerSessionState:
    """Tests for filter-transcript.py's per-session state file handling.

    Under per-session isolation (ADR-0018 amendment / Option 2), each
    session has its own state file path keyed by session_id, so cross-
    session state contamination is structurally impossible. The legacy
    session_token validation has been removed because the per-session
    path itself is the identity check.
    """

    def test_load_returns_state_when_present(
        self, tmp_path: Path, filter_mod: Any,
    ) -> None:
        """Filter state with offset and hash is loaded as-is."""
        state_path = str(tmp_path / "state.json")
        with open(state_path, "w") as f:
            json.dump({
                "offset": 50000,
                "last_bash_hash": "abc123",
            }, f)

        state = filter_mod.load_state(state_path)
        assert state["offset"] == 50000
        assert state["last_bash_hash"] == "abc123"

    def test_load_returns_empty_when_missing(
        self, tmp_path: Path, filter_mod: Any,
    ) -> None:
        """Missing state file yields a fresh zero-offset state."""
        state_path = str(tmp_path / "nonexistent-state.json")
        state = filter_mod.load_state(state_path)
        assert state["offset"] == 0
        assert state["last_bash_hash"] == ""

    def test_load_handles_corrupt_state_file(
        self, tmp_path: Path, filter_mod: Any,
    ) -> None:
        """A corrupt state file yields a fresh zero-offset state."""
        state_path = str(tmp_path / "state.json")
        with open(state_path, "w") as f:
            f.write("{not valid json")

        state = filter_mod.load_state(state_path)
        assert state["offset"] == 0
        assert state["last_bash_hash"] == ""

    def test_save_round_trip(
        self, tmp_path: Path, filter_mod: Any,
    ) -> None:
        """save_state writes the state, load_state reads it back."""
        state_path = str(tmp_path / "state.json")
        filter_mod.save_state(
            state_path, {"offset": 100, "last_bash_hash": "x"},
        )

        with open(state_path) as f:
            saved = json.load(f)
        assert saved["offset"] == 100
        assert saved["last_bash_hash"] == "x"


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

    Per-session isolation (ADR-0018 amendment / Option 2): the sidecar
    file path is per-session, keyed by session_id. Rotation into the
    global ``.last_injection_history.jsonl`` slot happens at session
    END via rotate-on-session-end.sh — see TestRotateOnSessionEnd in
    test_watcher_lifecycle.py.
    """

    SESSION_ID: ClassVar[str] = "test-injection-history-session"

    def _sidecar_filename(self, session_id: str | None = None) -> str:
        return (
            f".current_injection_history.{session_id or self.SESSION_ID}.jsonl"
        )

    def _read_records(
        self, repo_root: Path, session_id: str | None = None,
    ) -> list[dict]:
        path = repo_root / ".agent" / self._sidecar_filename(session_id)
        if not path.exists():
            return []
        return [
            json.loads(line) for line in path.read_text().splitlines() if line
        ]

    def test_writer_appends_record(
        self, tmp_path: Path, hook_mod: Any,
    ) -> None:
        """Basic happy path: writer creates the file and writes one well-formed record."""
        hook_mod.log_injection_history(
            str(tmp_path),
            transcript_offset=12345,
            agent_goals="The agent is implementing watcher leak fix.",
            selected=["pb-a", "pb-b"],
            injected=["pb-a"],
            skipped_dedup=["pb-b"],
            event_id="evt-001",
            session_id=self.SESSION_ID,
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
        assert rec["session_id"] == self.SESSION_ID
        assert "timestamp" in rec
        assert "distill_model" in rec
        assert "select_model" in rec

    def test_writer_appends_multiple_records(
        self, tmp_path: Path, hook_mod: Any,
    ) -> None:
        """Successive calls append, not overwrite."""
        for i in range(3):
            hook_mod.log_injection_history(
                str(tmp_path),
                transcript_offset=1000 * i,
                agent_goals=f"goal {i}",
                selected=[f"pb-{i}"],
                injected=[f"pb-{i}"],
                skipped_dedup=[],
                event_id=f"evt-{i}",
                session_id=self.SESSION_ID,
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
                session_id=self.SESSION_ID,
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
        hook_mod.log_injection_history(
            str(tmp_path),
            transcript_offset=999,
            agent_goals="The agent is reading a config file.",
            selected=[],
            injected=[],
            skipped_dedup=[],
            event_id="evt-empty",
            session_id=self.SESSION_ID,
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
        hook_mod.log_injection_history(
            str(tmp_path),
            transcript_offset=2000,
            agent_goals="goal",
            selected=["pb-a", "pb-b", "pb-c"],
            injected=["pb-a"],          # only pb-a was actually injected
            skipped_dedup=["pb-b", "pb-c"],  # pb-b and pb-c were already in context
            event_id="evt-dedup",
            session_id=self.SESSION_ID,
        )

        records = self._read_records(tmp_path)
        assert records[0]["selected"] == ["pb-a", "pb-b", "pb-c"]
        assert records[0]["injected"] == ["pb-a"]
        assert set(records[0]["skipped_dedup"]) == {"pb-b", "pb-c"}

    def test_concurrent_sessions_have_independent_sidecars(
        self, tmp_path: Path, hook_mod: Any,
    ) -> None:
        """Two concurrent sessions in the same repo write to separate
        per-session sidecar files. Session B's records never appear in
        session A's sidecar."""
        sid_a = "concurrent-history-A"
        sid_b = "concurrent-history-B"

        hook_mod.log_injection_history(
            str(tmp_path),
            transcript_offset=100,
            agent_goals="A",
            selected=["pb-a"],
            injected=["pb-a"],
            skipped_dedup=[],
            event_id="evt-A",
            session_id=sid_a,
        )
        hook_mod.log_injection_history(
            str(tmp_path),
            transcript_offset=200,
            agent_goals="B",
            selected=["pb-b"],
            injected=["pb-b"],
            skipped_dedup=[],
            event_id="evt-B",
            session_id=sid_b,
        )

        recs_a = self._read_records(tmp_path, session_id=sid_a)
        recs_b = self._read_records(tmp_path, session_id=sid_b)
        assert len(recs_a) == 1
        assert len(recs_b) == 1
        assert recs_a[0]["event_id"] == "evt-A"
        assert recs_b[0]["event_id"] == "evt-B"
        assert recs_a[0]["session_id"] == sid_a
        assert recs_b[0]["session_id"] == sid_b


# ---------------------------------------------------------------------------
# _session_id_from_transcript_path tests
# ---------------------------------------------------------------------------


class TestSessionIdFromTranscriptPath:
    """Verify on_transcript_change.py extracts session_id from per-session
    transcript filenames. The polling layer constructs the path with the
    session_id baked in, so the filename basename is the authoritative
    source of session identity inside on_transcript_change.py."""

    def test_extracts_uuid_session_id(self, hook_mod: Any) -> None:
        """A Claude Code UUID session_id is extracted correctly."""
        path = (
            "/repo/.agent/.current_session_transcript."
            "cf03d762-70bc-4a30-856f-9cf372da4c07.jsonl"
        )
        sid = hook_mod._session_id_from_transcript_path(path)
        assert sid == "cf03d762-70bc-4a30-856f-9cf372da4c07"

    def test_extracts_alphanumeric_session_id(self, hook_mod: Any) -> None:
        """An alphanumeric session_id (e.g. cursor-singleton) is extracted."""
        path = (
            "/repo/.agent/.current_session_transcript."
            "cursor-singleton.jsonl"
        )
        sid = hook_mod._session_id_from_transcript_path(path)
        assert sid == "cursor-singleton"

    def test_extracts_underscore_session_id(self, hook_mod: Any) -> None:
        """Session ids with underscores are valid."""
        path = (
            "/repo/.agent/.current_session_transcript."
            "session_id_with_underscores.jsonl"
        )
        sid = hook_mod._session_id_from_transcript_path(path)
        assert sid == "session_id_with_underscores"

    def test_returns_empty_for_non_per_session_path(
        self, hook_mod: Any,
    ) -> None:
        """A path that does not match the per-session naming convention
        yields an empty string. Callers treat this as "no session id"
        and bail out (main() exits cleanly)."""
        for bad_path in (
            "/repo/.agent/.current_session_transcript.jsonl",  # no sid
            "/repo/.agent/transcript.jsonl",                   # wrong stem
            "/repo/random.jsonl",                              # totally unrelated
            "",                                                 # empty
        ):
            assert hook_mod._session_id_from_transcript_path(bad_path) == "", (
                f"unexpectedly extracted a sid from {bad_path!r}"
            )


# ---------------------------------------------------------------------------
# Per-session naming invariants
# ---------------------------------------------------------------------------

class TestPerSessionNamingInvariants:
    """Verify that all per-session state filenames follow the convention
    that allows multiple concurrent sessions to coexist in one .agent/.

    Under the per-session model (ADR-0018 amendment / Option 2), each
    file produced by the pipeline must either:
      (a) be per-session — its name embeds <session_id> as a suffix —
          so two concurrent sessions never collide, OR
      (b) be a global slot owned by the rotation chain
          (.last_*/.second_to_last_*) or be persistent (training data,
          archive directory).

    There is NO glob-based session reset anymore: the previous global
    .transcript-* glob in sync-transcript.sh's per-session reset block
    has been removed because per-session paths make the reset
    unnecessary by construction.
    """

    # Per-session files are created with these stem patterns. Each must
    # have <session_id> embedded as a filename component.
    PER_SESSION_STEMS: ClassVar[list[str]] = [
        ".current_session_transcript",
        ".current_injection_history",
        ".transcript-sync",            # PID file: .transcript-sync.<sid>.pid
        ".transcript-sync-state",      # filter state: ...<sid>.json
        ".transcript-poll-state",      # poll state: ...<sid>
        ".transcript-injection-state", # dedup state: ...<sid>.json
    ]

    # Global slots and persistent files (no per-session suffix).
    GLOBAL_FILES: ClassVar[list[str]] = [
        ".last_session_transcript.jsonl",
        ".second_to_last_transcript.jsonl",
        ".last_injection_history.jsonl",
        ".second_to_last_injection_history.jsonl",
        ".training-data.jsonl",
        ".rotation.lock",
        ".archived-transcripts",
    ]

    def test_per_session_stem_can_be_keyed_by_sid(self) -> None:
        """For each per-session stem, the path
        ``<stem>.<session_id>.<ext>`` (or ``<stem>.<session_id>``) is
        well-formed and uniquely identifies a session."""
        for stem in self.PER_SESSION_STEMS:
            sid = "test-sid-abc123"
            # Check that no GLOBAL_FILE matches this stem-with-sid pattern
            for ext in ("", ".jsonl", ".json", ".pid"):
                candidate = f"{stem}.{sid}{ext}"
                assert candidate not in self.GLOBAL_FILES, (
                    f"per-session candidate {candidate!r} collides with a "
                    f"global slot — naming convention violated"
                )

    def test_global_files_have_no_session_suffix(self) -> None:
        """Global slot filenames don't accidentally encode a session id."""
        # A real session_id is a UUID-shaped string with hex+dashes. The
        # check is that no GLOBAL_FILE looks like it has a per-session
        # tail. We approximate by ensuring no GLOBAL_FILE name contains a
        # dot-separated component longer than 'second_to_last'.
        suspicious = []
        for name in self.GLOBAL_FILES:
            parts = name.split(".")
            for p in parts:
                if len(p) > 20 and "_" not in p and "-" in p:
                    suspicious.append((name, p))
        assert not suspicious, (
            f"global filenames look like they encode a session id: "
            f"{suspicious}"
        )
