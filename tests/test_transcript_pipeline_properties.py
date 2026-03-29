# SPDX-License-Identifier: AGPL-3.0-or-later
"""Property-based tests for the transcript sync pipeline (ADR-0018).

Tests invariants for:
- filter_new_lines: noise reduction, monotonic offset, idempotency
- parse_ratings: score range, preferred format priority, no false positives
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


# For parse_ratings: playbook IDs from the real registry
SAMPLE_PB_IDS = [
    "experiment-design-playbook",
    "bakeoff-broad-priorities",
    "coverage-and-test-placement",
    "ci-debug-protocol",
    "vpr-usage",
]


@st.composite
def preferred_ratings_text(draw: st.DrawFn) -> tuple[str, dict[str, int]]:
    """Generate ratings in the preferred '<id>: <score>' format with expected results."""
    lines = []
    expected: dict[str, int] = {}
    for pb_id in SAMPLE_PB_IDS:
        score = draw(st.integers(min_value=1, max_value=10))
        lines.append(f"{pb_id}: {score}")
        expected[pb_id] = score
    return "\n".join(lines), expected


@st.composite
def slash_ratings_text(draw: st.DrawFn) -> tuple[str, dict[str, int]]:
    """Generate ratings in '<score>/10 <id>' fallback format."""
    lines = []
    expected: dict[str, int] = {}
    for pb_id in SAMPLE_PB_IDS:
        score = draw(st.integers(min_value=1, max_value=10))
        lines.append(f"{score}/10 {pb_id}")
        expected[pb_id] = score
    return "\n".join(lines), expected


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
# parse_ratings tests
# ---------------------------------------------------------------------------

class TestParseRatings:
    """Property tests for on_transcript_change.py's parse_ratings."""

    @given(text_and_expected=preferred_ratings_text())
    @settings(max_examples=50)
    def test_preferred_format_parsed_correctly(
        self, text_and_expected: tuple[str, dict[str, int]], hook_mod: Any,
    ) -> None:
        """The '<id>: <score>' format is parsed with exact scores."""
        text, expected = text_and_expected
        result = hook_mod.parse_ratings(text)
        for pb_id, score in expected.items():
            assert result.get(pb_id) == score

    @given(text_and_expected=slash_ratings_text())
    @settings(max_examples=50)
    def test_slash_format_parsed(
        self, text_and_expected: tuple[str, dict[str, int]], hook_mod: Any,
    ) -> None:
        """The '<score>/10 <id>' fallback format is parsed correctly."""
        text, expected = text_and_expected
        result = hook_mod.parse_ratings(text)
        for pb_id, score in expected.items():
            assert result.get(pb_id) == score

    @given(text_and_expected=preferred_ratings_text())
    @settings(max_examples=50)
    def test_scores_always_in_valid_range(
        self, text_and_expected: tuple[str, dict[str, int]], hook_mod: Any,
    ) -> None:
        """Every parsed score is between 1 and 10 inclusive."""
        text, _ = text_and_expected
        result = hook_mod.parse_ratings(text)
        for score in result.values():
            assert 1 <= score <= 10

    def test_empty_string_returns_empty(self, hook_mod: Any) -> None:
        """Empty input yields no ratings."""
        assert hook_mod.parse_ratings("") == {}

    def test_garbage_returns_empty(self, hook_mod: Any) -> None:
        """Unrelated text yields no ratings."""
        assert hook_mod.parse_ratings("the quick brown fox jumps over the lazy dog") == {}

    def test_out_of_range_scores_rejected(self, hook_mod: Any) -> None:
        """Scores outside 1-10 are not returned."""
        text = "experiment-design-playbook: 0\nbakeoff-broad-priorities: 11"
        result = hook_mod.parse_ratings(text)
        assert "experiment-design-playbook" not in result
        assert "bakeoff-broad-priorities" not in result

    def test_numbered_list_does_not_steal_ordinal(self, hook_mod: Any) -> None:
        """A numbered list prefix must not be confused with the score.

        This was the original bug: '3. experiment-design-playbook: 8' could
        match 3 (the list ordinal) instead of 8 (the actual score).
        """
        text = (
            "1. experiment-design-playbook: 8\n"
            "2. bakeoff-broad-priorities: 3\n"
            "3. coverage-and-test-placement: 9\n"
        )
        result = hook_mod.parse_ratings(text)
        assert result.get("experiment-design-playbook") == 8
        assert result.get("bakeoff-broad-priorities") == 3
        assert result.get("coverage-and-test-placement") == 9

    def test_preferred_format_takes_priority(self, hook_mod: Any) -> None:
        """When both '<id>: <score>' and '<score>/10 <id>' appear, preferred wins."""
        text = "5/10 experiment-design-playbook: 9"
        result = hook_mod.parse_ratings(text)
        assert result.get("experiment-design-playbook") == 9


# ---------------------------------------------------------------------------
# recently_injected tests
# ---------------------------------------------------------------------------

class TestRecentlyInjected:
    """Property tests for on_transcript_change.py's recently_injected."""

    @staticmethod
    def _make_transcript(base: str, lines: list[dict]) -> str:
        """Write a JSONL transcript and return its path."""
        path = os.path.join(base, "transcript.jsonl")
        with open(path, "wb") as f:
            for obj in lines:
                f.write(_jsonl_line(obj))
        return path

    @staticmethod
    def _make_state(base: str, state: dict) -> None:
        """Write injection state to the expected location."""
        agent_dir = os.path.join(base, ".agent")
        os.makedirs(agent_dir, exist_ok=True)
        state_path = os.path.join(agent_dir, ".transcript-injection-state.json")
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
