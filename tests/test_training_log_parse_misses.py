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
from pathlib import Path

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
