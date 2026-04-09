# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for training data logging and parse-outcome sidecar (ADR-0018).

The training data log (`.training-data.jsonl`) records raw LLM I/O and is
written *before* the response is parsed.  A separate sidecar file
(`.parse-outcomes.jsonl`) records which playbook IDs failed to parse,
keyed by a shared ``event_id`` (UUID) so the two files can be joined
offline.

This test file covers:
- log_training_example: the ``extra`` kwarg (used for event_id)
- log_parse_outcome: sidecar file creation and content (dormant since
  migration to parse_selection)
- parse_selection integration tests
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Import the hook module
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
def hook_mod():
    """Import on_transcript_change.py as a module."""
    return _import_hook_module("on_transcript_change", "on_transcript_change.py")


# ---------------------------------------------------------------------------
# log_training_example: extra kwarg
# ---------------------------------------------------------------------------


class TestLogTrainingExampleExtra:
    """log_training_example merges extra metadata into the JSON entry."""

    def test_event_id_appears_in_output(self, hook_mod, tmp_path: Path) -> None:
        log_path = tmp_path / "training.jsonl"
        orig_log = hook_mod.TRAINING_LOG
        hook_mod.TRAINING_LOG = str(log_path)
        try:
            hook_mod.log_training_example(
                str(tmp_path), "sparse_selection", "prompt", "response",
                model="test-model",
                extra={"event_id": "abc-123"},
            )
        finally:
            hook_mod.TRAINING_LOG = orig_log

        entry = json.loads(log_path.read_text().strip())
        assert entry["event_id"] == "abc-123"
        assert entry["step"] == "sparse_selection"
        assert entry["model"] == "test-model"
        assert entry["messages"][0]["content"] == "prompt"

    def test_no_extra_when_none(self, hook_mod, tmp_path: Path) -> None:
        log_path = tmp_path / "training.jsonl"
        orig_log = hook_mod.TRAINING_LOG
        hook_mod.TRAINING_LOG = str(log_path)
        try:
            hook_mod.log_training_example(
                str(tmp_path), "goal_distillation", "prompt", "response",
                model="test-model",
            )
        finally:
            hook_mod.TRAINING_LOG = orig_log

        entry = json.loads(log_path.read_text().strip())
        assert "event_id" not in entry

    def test_no_extra_when_empty_dict(self, hook_mod, tmp_path: Path) -> None:
        log_path = tmp_path / "training.jsonl"
        orig_log = hook_mod.TRAINING_LOG
        hook_mod.TRAINING_LOG = str(log_path)
        try:
            hook_mod.log_training_example(
                str(tmp_path), "sparse_selection", "prompt", "response",
                model="test-model",
                extra={},
            )
        finally:
            hook_mod.TRAINING_LOG = orig_log

        entry = json.loads(log_path.read_text().strip())
        assert "event_id" not in entry


# ---------------------------------------------------------------------------
# log_parse_outcome: sidecar file
# ---------------------------------------------------------------------------


class TestLogParseOutcome:
    """log_parse_outcome writes parse misses to a sidecar JSONL file.

    Note: This functionality is dormant since the migration from parse_ratings
    to parse_selection. parse_selection uses exact ID matching, making parse
    misses structurally impossible. Tests are retained for backward
    compatibility and in case future changes reintroduce fragile parsing.
    """

    def test_writes_misses_with_event_id(self, hook_mod, tmp_path: Path) -> None:
        sidecar = tmp_path / "outcomes.jsonl"
        orig = hook_mod.PARSE_OUTCOME_LOG
        hook_mod.PARSE_OUTCOME_LOG = str(sidecar)
        try:
            hook_mod.log_parse_outcome(str(tmp_path), "evt-1", ["foo", "bar"])
        finally:
            hook_mod.PARSE_OUTCOME_LOG = orig

        entry = json.loads(sidecar.read_text().strip())
        assert entry["event_id"] == "evt-1"
        assert entry["parse_misses"] == ["foo", "bar"]

    def test_appends_multiple_entries(self, hook_mod, tmp_path: Path) -> None:
        sidecar = tmp_path / "outcomes.jsonl"
        orig = hook_mod.PARSE_OUTCOME_LOG
        hook_mod.PARSE_OUTCOME_LOG = str(sidecar)
        try:
            hook_mod.log_parse_outcome(str(tmp_path), "evt-1", ["a"])
            hook_mod.log_parse_outcome(str(tmp_path), "evt-2", ["b", "c"])
        finally:
            hook_mod.PARSE_OUTCOME_LOG = orig

        lines = sidecar.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["event_id"] == "evt-1"
        assert json.loads(lines[1])["parse_misses"] == ["b", "c"]

    def test_default_path(self, hook_mod, tmp_path: Path) -> None:
        """When PARSE_OUTCOME_LOG is empty, writes to .agent/.parse-outcomes.jsonl."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        orig = hook_mod.PARSE_OUTCOME_LOG
        hook_mod.PARSE_OUTCOME_LOG = ""
        try:
            hook_mod.log_parse_outcome(str(tmp_path), "evt-1", ["x"])
        finally:
            hook_mod.PARSE_OUTCOME_LOG = orig

        default_file = agent_dir / ".parse-outcomes.jsonl"
        assert default_file.exists()
        entry = json.loads(default_file.read_text().strip())
        assert entry["event_id"] == "evt-1"

    def test_oserror_is_silent(self, hook_mod) -> None:
        """OSError on write is swallowed (best-effort logging)."""
        orig = hook_mod.PARSE_OUTCOME_LOG
        hook_mod.PARSE_OUTCOME_LOG = "/nonexistent/dir/outcomes.jsonl"
        try:
            # Should not raise
            hook_mod.log_parse_outcome("/nonexistent", "evt-1", ["x"])
        finally:
            hook_mod.PARSE_OUTCOME_LOG = orig


# ---------------------------------------------------------------------------
# parse_selection integration tests
# ---------------------------------------------------------------------------


class TestParseSelectionIntegration:
    """parse_selection integration tests."""

    def test_all_ids_selected(self, hook_mod) -> None:
        """All playbook IDs mentioned in text are returned."""
        text = "\n".join(pb_id for pb_id, _, _ in hook_mod.PLAYBOOKS)
        result = hook_mod.parse_selection(text)
        assert len(result) == len(hook_mod.PLAYBOOKS)
        for pb_id, _, _ in hook_mod.PLAYBOOKS:
            assert pb_id in result

    def test_subset_selected(self, hook_mod) -> None:
        """Only mentioned playbook IDs are returned."""
        included = [pb_id for pb_id, _, _ in hook_mod.PLAYBOOKS[:3]]
        text = "\n".join(included)
        result = hook_mod.parse_selection(text)
        assert len(result) == 3
        for pb_id in included:
            assert pb_id in result
        for pb_id, _, _ in hook_mod.PLAYBOOKS[3:]:
            assert pb_id not in result

    def test_empty_response_selects_none(self, hook_mod) -> None:
        """An empty LLM response selects no playbooks."""
        assert hook_mod.parse_selection("") == []

    def test_garbled_id_not_matched(self, hook_mod) -> None:
        """A mangled playbook ID (underscores instead of hyphens) is not matched."""
        first_id = hook_mod.PLAYBOOKS[0][0]
        garbled = first_id.replace("-", "_")
        text = garbled
        result = hook_mod.parse_selection(text)
        assert first_id not in result

    def test_joinable_via_event_id(self, hook_mod, tmp_path: Path) -> None:
        """Training data entry and sidecar entry share the same event_id."""
        training_log = tmp_path / "training.jsonl"
        sidecar = tmp_path / "outcomes.jsonl"
        orig_training = hook_mod.TRAINING_LOG
        orig_outcome = hook_mod.PARSE_OUTCOME_LOG
        hook_mod.TRAINING_LOG = str(training_log)
        hook_mod.PARSE_OUTCOME_LOG = str(sidecar)
        try:
            event_id = "join-test-uuid"
            hook_mod.log_training_example(
                str(tmp_path), "sparse_selection", "prompt", "response",
                model="test-model",
                extra={"event_id": event_id},
            )
            hook_mod.log_parse_outcome(str(tmp_path), event_id, ["missed-pb"])
        finally:
            hook_mod.TRAINING_LOG = orig_training
            hook_mod.PARSE_OUTCOME_LOG = orig_outcome

        training_entry = json.loads(training_log.read_text().strip())
        outcome_entry = json.loads(sidecar.read_text().strip())
        assert training_entry["event_id"] == outcome_entry["event_id"]
        assert outcome_entry["parse_misses"] == ["missed-pb"]


# ---------------------------------------------------------------------------
# _get_file_commit_sha: git SHA resolution with caching
# ---------------------------------------------------------------------------


class TestGetFileCommitSha:
    """_get_file_commit_sha returns the last commit SHA for a file."""

    def test_returns_sha_for_tracked_file(self, hook_mod, tmp_path: Path) -> None:
        """Returns a 40-char hex SHA for a file in a git repo."""
        # Set up a minimal git repo
        subprocess.run(
            ["git", "init"], cwd=str(tmp_path),
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(tmp_path), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(tmp_path), capture_output=True, check=True,
        )
        test_file = tmp_path / "hello.py"
        test_file.write_text("print('hi')")
        subprocess.run(
            ["git", "add", "hello.py"], cwd=str(tmp_path),
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=str(tmp_path),
            capture_output=True, check=True,
        )

        # Clear the cache so this test's repo is used
        hook_mod._sha_cache.clear()
        sha = hook_mod._get_file_commit_sha(str(tmp_path), "hello.py")
        assert len(sha) == 40
        assert all(c in "0123456789abcdef" for c in sha)

    def test_returns_empty_for_untracked_file(self, hook_mod, tmp_path: Path) -> None:
        """Returns empty string when the file has no commits."""
        subprocess.run(
            ["git", "init"], cwd=str(tmp_path),
            capture_output=True, check=True,
        )
        hook_mod._sha_cache.clear()
        sha = hook_mod._get_file_commit_sha(str(tmp_path), "nonexistent.py")
        assert sha == ""

    def test_returns_empty_for_non_git_dir(self, hook_mod, tmp_path: Path) -> None:
        """Returns empty string when not in a git repo."""
        hook_mod._sha_cache.clear()
        sha = hook_mod._get_file_commit_sha(str(tmp_path), "anything.py")
        assert sha == ""

    def test_caches_result(self, hook_mod, tmp_path: Path) -> None:
        """Second call returns cached value without running git again."""
        hook_mod._sha_cache.clear()
        hook_mod._sha_cache[(str(tmp_path), "cached.py")] = "abc123"
        sha = hook_mod._get_file_commit_sha(str(tmp_path), "cached.py")
        assert sha == "abc123"

    def test_handles_oserror(self, hook_mod, tmp_path: Path) -> None:
        """OSError from subprocess is caught and returns empty string."""
        hook_mod._sha_cache.clear()
        with mock.patch("subprocess.run", side_effect=OSError("no git")):
            sha = hook_mod._get_file_commit_sha(str(tmp_path), "file.py")
        assert sha == ""

    def test_handles_timeout(self, hook_mod, tmp_path: Path) -> None:
        """TimeoutExpired from subprocess is caught and returns empty string."""
        hook_mod._sha_cache.clear()
        with mock.patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5),
        ):
            sha = hook_mod._get_file_commit_sha(str(tmp_path), "file.py")
        assert sha == ""


# ---------------------------------------------------------------------------
# _extract_transcript_metadata: main_llm and vendor_version from JSONL
# ---------------------------------------------------------------------------


class TestExtractTranscriptMetadata:
    """_extract_transcript_metadata reads main_llm and vendor_version."""

    def test_extracts_main_llm_from_assistant_message(
        self, hook_mod, tmp_path: Path,
    ) -> None:
        transcript = tmp_path / "transcript.jsonl"
        lines = [
            json.dumps({"type": "user_message", "content": "hello"}),
            json.dumps({
                "type": "assistant",
                "message": {"model": "claude-opus-4-6", "content": "hi"},
            }),
        ]
        transcript.write_text("\n".join(lines) + "\n")

        meta = hook_mod._extract_transcript_metadata(str(transcript))
        assert meta["main_llm"] == "claude-opus-4-6"

    def test_extracts_vendor_version(self, hook_mod, tmp_path: Path) -> None:
        transcript = tmp_path / "transcript.jsonl"
        lines = [
            json.dumps({"version": "1.2.3", "type": "init"}),
            json.dumps({"type": "user_message", "content": "hello"}),
        ]
        transcript.write_text("\n".join(lines) + "\n")

        meta = hook_mod._extract_transcript_metadata(str(transcript))
        assert meta["vendor_version"] == "1.2.3"

    def test_takes_most_recent_values(self, hook_mod, tmp_path: Path) -> None:
        """When multiple entries have the fields, last one wins."""
        transcript = tmp_path / "transcript.jsonl"
        lines = [
            json.dumps({"version": "1.0.0", "type": "init"}),
            json.dumps({
                "type": "assistant",
                "message": {"model": "claude-sonnet-4-5", "content": "a"},
            }),
            json.dumps({"version": "1.2.3", "type": "reconnect"}),
            json.dumps({
                "type": "assistant",
                "message": {"model": "claude-opus-4-6", "content": "b"},
            }),
        ]
        transcript.write_text("\n".join(lines) + "\n")

        meta = hook_mod._extract_transcript_metadata(str(transcript))
        assert meta["main_llm"] == "claude-opus-4-6"
        assert meta["vendor_version"] == "1.2.3"

    def test_returns_empty_for_missing_file(self, hook_mod) -> None:
        meta = hook_mod._extract_transcript_metadata("/nonexistent/path.jsonl")
        assert meta == {"main_llm": "", "vendor_version": ""}

    def test_returns_empty_for_empty_file(self, hook_mod, tmp_path: Path) -> None:
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("")
        meta = hook_mod._extract_transcript_metadata(str(transcript))
        assert meta == {"main_llm": "", "vendor_version": ""}

    def test_handles_malformed_json(self, hook_mod, tmp_path: Path) -> None:
        """Malformed lines are skipped without error."""
        transcript = tmp_path / "transcript.jsonl"
        lines = [
            "not json",
            json.dumps({
                "type": "assistant",
                "message": {"model": "claude-opus-4-6", "content": "ok"},
            }),
        ]
        transcript.write_text("\n".join(lines) + "\n")

        meta = hook_mod._extract_transcript_metadata(str(transcript))
        assert meta["main_llm"] == "claude-opus-4-6"

    def test_handles_non_dict_message(self, hook_mod, tmp_path: Path) -> None:
        """message field that is not a dict is ignored."""
        transcript = tmp_path / "transcript.jsonl"
        lines = [
            json.dumps({"type": "assistant", "message": "just a string"}),
        ]
        transcript.write_text("\n".join(lines) + "\n")

        meta = hook_mod._extract_transcript_metadata(str(transcript))
        assert meta["main_llm"] == ""


# ---------------------------------------------------------------------------
# log_training_example: cohort metadata fields (WI-tatuh / INV-gajap)
# ---------------------------------------------------------------------------


class TestLogTrainingExampleCohortMetadata:
    """log_training_example includes v1 cohort metadata in every entry."""

    def _write_and_read(
        self, hook_mod, tmp_path: Path, **kwargs,
    ) -> dict:
        """Helper: write one training example and return the parsed entry."""
        log_path = tmp_path / "training.jsonl"
        orig_log = hook_mod.TRAINING_LOG
        hook_mod.TRAINING_LOG = str(log_path)
        # Avoid hitting real git — seed the cache
        hook_mod._sha_cache.clear()
        hook_mod._sha_cache[
            (str(tmp_path), hook_mod._INFRA_REL_PATH)
        ] = "a" * 40
        try:
            hook_mod.log_training_example(
                str(tmp_path), "goal_distillation", "prompt", "response",
                model="test-scoring-model",
                **kwargs,
            )
        finally:
            hook_mod.TRAINING_LOG = orig_log
        return json.loads(log_path.read_text().strip())

    def test_pipeline_version_is_v1(self, hook_mod, tmp_path: Path) -> None:
        entry = self._write_and_read(hook_mod, tmp_path)
        assert entry["pipeline_version"] == "v1"

    def test_infra_sha_present(self, hook_mod, tmp_path: Path) -> None:
        entry = self._write_and_read(hook_mod, tmp_path)
        assert entry["infra_sha"] == "a" * 40

    def test_playbook_registry_sha_present(self, hook_mod, tmp_path: Path) -> None:
        entry = self._write_and_read(hook_mod, tmp_path)
        assert entry["playbook_registry_sha"] == "a" * 40

    def test_vendor_is_claude_code(self, hook_mod, tmp_path: Path) -> None:
        entry = self._write_and_read(hook_mod, tmp_path)
        assert entry["vendor"] == "claude-code"

    def test_scoring_model_mirrors_model(self, hook_mod, tmp_path: Path) -> None:
        entry = self._write_and_read(hook_mod, tmp_path)
        assert entry["scoring_model"] == "test-scoring-model"
        assert entry["model"] == "test-scoring-model"

    def test_main_llm_passed_through(self, hook_mod, tmp_path: Path) -> None:
        entry = self._write_and_read(
            hook_mod, tmp_path, main_llm="claude-opus-4-6",
        )
        assert entry["main_llm"] == "claude-opus-4-6"

    def test_vendor_version_passed_through(self, hook_mod, tmp_path: Path) -> None:
        entry = self._write_and_read(
            hook_mod, tmp_path, vendor_version="1.5.0",
        )
        assert entry["vendor_version"] == "1.5.0"

    def test_defaults_to_empty_when_not_provided(
        self, hook_mod, tmp_path: Path,
    ) -> None:
        entry = self._write_and_read(hook_mod, tmp_path)
        assert entry["main_llm"] == ""
        assert entry["vendor_version"] == ""

    def test_messages_array_unchanged(self, hook_mod, tmp_path: Path) -> None:
        """Cohort metadata does not leak into the messages array."""
        entry = self._write_and_read(
            hook_mod, tmp_path, main_llm="opus", vendor_version="1.0",
        )
        msgs = entry["messages"]
        assert len(msgs) == 2
        assert msgs[0] == {"role": "user", "content": "prompt"}
        assert msgs[1] == {"role": "assistant", "content": "response"}

    def test_extra_still_works_alongside_cohort(
        self, hook_mod, tmp_path: Path,
    ) -> None:
        entry = self._write_and_read(
            hook_mod, tmp_path,
            extra={"event_id": "test-uuid"},
            main_llm="opus",
        )
        assert entry["event_id"] == "test-uuid"
        assert entry["main_llm"] == "opus"
        assert entry["pipeline_version"] == "v1"

    def test_backward_compat_model_field_preserved(
        self, hook_mod, tmp_path: Path,
    ) -> None:
        """The legacy 'model' field is still present for existing data loaders."""
        entry = self._write_and_read(hook_mod, tmp_path)
        assert "model" in entry
        assert entry["model"] == entry["scoring_model"]
