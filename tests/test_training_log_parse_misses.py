# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for parse-outcome sidecar logging (ADR-0018).

The training data log (`.training-data.jsonl`) records raw LLM I/O and is
written *before* the response is parsed.  A separate sidecar file
(`.parse-outcomes.jsonl`) records which playbook IDs failed to parse,
keyed by a shared ``event_id`` (UUID) so the two files can be joined
offline.

This test file covers:
- log_training_example: the ``extra`` kwarg (used for event_id)
- log_parse_outcome: sidecar file creation and content
- End-to-end: parse_ratings → miss detection → sidecar logging
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
                str(tmp_path), "relevance_rating", "prompt", "response",
                extra={"event_id": "abc-123"},
            )
        finally:
            hook_mod.TRAINING_LOG = orig_log

        entry = json.loads(log_path.read_text().strip())
        assert entry["event_id"] == "abc-123"
        assert entry["step"] == "relevance_rating"
        assert entry["messages"][0]["content"] == "prompt"

    def test_no_extra_when_none(self, hook_mod, tmp_path: Path) -> None:
        log_path = tmp_path / "training.jsonl"
        orig_log = hook_mod.TRAINING_LOG
        hook_mod.TRAINING_LOG = str(log_path)
        try:
            hook_mod.log_training_example(
                str(tmp_path), "goal_distillation", "prompt", "response",
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
                str(tmp_path), "relevance_rating", "prompt", "response",
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
    """log_parse_outcome writes parse misses to a sidecar JSONL file."""

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
# End-to-end: parse_ratings → miss detection
# ---------------------------------------------------------------------------


class TestParseMissesIntegration:
    """parse_ratings + miss computation + sidecar logging work together."""

    def test_all_parsed_no_sidecar(self, hook_mod, tmp_path: Path) -> None:
        """When every playbook parses, no sidecar entry is written."""
        sidecar = tmp_path / "outcomes.jsonl"
        orig = hook_mod.PARSE_OUTCOME_LOG
        hook_mod.PARSE_OUTCOME_LOG = str(sidecar)
        try:
            lines = [f"{pb_id}: 5" for pb_id, _, _ in hook_mod.PLAYBOOKS]
            ratings_text = "\n".join(lines)
            ratings = hook_mod.parse_ratings(ratings_text)
            all_ids = {pb_id for pb_id, _, _ in hook_mod.PLAYBOOKS}
            misses = sorted(all_ids - ratings.keys())
            assert misses == []
            if misses:  # pragma: no cover
                hook_mod.log_parse_outcome(str(tmp_path), "evt-1", misses)
        finally:
            hook_mod.PARSE_OUTCOME_LOG = orig

        assert not sidecar.exists()

    def test_some_missing_writes_sidecar(self, hook_mod, tmp_path: Path) -> None:
        """Partial parse failures produce a sidecar entry with the missed IDs."""
        sidecar = tmp_path / "outcomes.jsonl"
        orig = hook_mod.PARSE_OUTCOME_LOG
        hook_mod.PARSE_OUTCOME_LOG = str(sidecar)
        try:
            included = [pb_id for pb_id, _, _ in hook_mod.PLAYBOOKS[:3]]
            lines = [f"{pb_id}: 8" for pb_id in included]
            ratings_text = "\n".join(lines)
            ratings = hook_mod.parse_ratings(ratings_text)
            all_ids = {pb_id for pb_id, _, _ in hook_mod.PLAYBOOKS}
            misses = sorted(all_ids - ratings.keys())
            assert len(misses) == len(hook_mod.PLAYBOOKS) - 3
            if misses:
                hook_mod.log_parse_outcome(str(tmp_path), "evt-1", misses)
        finally:
            hook_mod.PARSE_OUTCOME_LOG = orig

        entry = json.loads(sidecar.read_text().strip())
        assert entry["event_id"] == "evt-1"
        assert len(entry["parse_misses"]) == len(hook_mod.PLAYBOOKS) - 3
        for pb_id in included:
            assert pb_id not in entry["parse_misses"]

    def test_empty_response_all_missing(self, hook_mod) -> None:
        """An empty LLM response means every playbook is a parse miss."""
        ratings = hook_mod.parse_ratings("")
        all_ids = {pb_id for pb_id, _, _ in hook_mod.PLAYBOOKS}
        misses = sorted(all_ids - ratings.keys())
        assert misses == sorted(all_ids)

    def test_garbled_id_is_a_miss(self, hook_mod) -> None:
        """A mangled playbook ID in the response counts as a miss."""
        first_id = hook_mod.PLAYBOOKS[0][0]
        garbled = first_id.replace("-", "_")
        lines = [f"{garbled}: 8"]
        for pb_id, _, _ in hook_mod.PLAYBOOKS[1:]:
            lines.append(f"{pb_id}: 5")
        ratings_text = "\n".join(lines)
        ratings = hook_mod.parse_ratings(ratings_text)
        all_ids = {pb_id for pb_id, _, _ in hook_mod.PLAYBOOKS}
        misses = sorted(all_ids - ratings.keys())
        assert first_id in misses
        assert len(misses) == 1

    def test_out_of_range_score_is_a_miss(self, hook_mod) -> None:
        """A score outside 1-10 fails validation, so the ID is a miss."""
        first_id = hook_mod.PLAYBOOKS[0][0]
        lines = [f"{first_id}: 0"]  # 0 is outside 1-10
        for pb_id, _, _ in hook_mod.PLAYBOOKS[1:]:
            lines.append(f"{pb_id}: 5")
        ratings_text = "\n".join(lines)
        ratings = hook_mod.parse_ratings(ratings_text)
        all_ids = {pb_id for pb_id, _, _ in hook_mod.PLAYBOOKS}
        misses = sorted(all_ids - ratings.keys())
        assert first_id in misses

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
                str(tmp_path), "relevance_rating", "prompt", "response",
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
