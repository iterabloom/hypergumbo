# SPDX-License-Identifier: MPL-2.0
"""Tests for the transient-read-race forensic logger."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_tracker import race_log


@pytest.fixture(autouse=True)
def _reset_logger() -> None:
    """Ensure each test starts with the module-level path cleared."""
    race_log.configure_race_log(None)
    yield
    race_log.configure_race_log(None)


class TestConfigure:
    def test_get_returns_configured_path(self, tmp_path: Path) -> None:
        log_path = tmp_path / "race_log.jsonl"
        race_log.configure_race_log(log_path)
        assert race_log.get_race_log_path() == log_path

    def test_none_disables(self) -> None:
        race_log.configure_race_log(None)
        assert race_log.get_race_log_path() is None


class TestLogReadRace:
    def test_writes_jsonl_line(self, tmp_path: Path) -> None:
        log_path = tmp_path / "race_log.jsonl"
        race_log.configure_race_log(log_path)
        target = tmp_path / "target.ops"
        target.write_text("- op: create\n")
        race_log.log_read_race(
            target, attempt=1, max_attempts=3,
            exc=PermissionError(13, "EACCES"), final=False,
        )
        lines = log_path.read_text().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["attempt"] == 1
        assert rec["max_attempts"] == 3
        assert rec["final"] is False
        assert rec["errno"] == 13
        assert "PermissionError" in rec["error"]
        assert rec["path"] == str(target)
        assert rec["pid"] == os.getpid()
        assert rec["euid"] == os.geteuid()
        assert rec["stat"] is not None
        assert "st_mode" in rec["stat"]

    def test_stat_is_null_when_file_missing(self, tmp_path: Path) -> None:
        log_path = tmp_path / "race_log.jsonl"
        race_log.configure_race_log(log_path)
        missing = tmp_path / "vanished.ops"
        race_log.log_read_race(
            missing, attempt=1, max_attempts=3,
            exc=FileNotFoundError(2, "ENOENT"), final=True,
        )
        rec = json.loads(log_path.read_text().splitlines()[0])
        assert rec["stat"] is None
        assert rec["final"] is True

    def test_noop_when_unconfigured(self, tmp_path: Path) -> None:
        race_log.configure_race_log(None)
        # Should not raise, should not create any file.
        race_log.log_read_race(
            tmp_path / "x.ops", attempt=1, max_attempts=3,
            exc=PermissionError(13, "x"), final=True,
        )
        assert list(tmp_path.iterdir()) == []

    def test_swallows_write_errors(self, tmp_path: Path) -> None:
        """Logger must never crash the caller — e.g. if disk is full or
        the destination cannot be created."""
        log_path = tmp_path / "race_log.jsonl"
        race_log.configure_race_log(log_path)
        with patch("hypergumbo_tracker.race_log.open", side_effect=OSError("disk full")):
            # Must not raise.
            race_log.log_read_race(
                tmp_path / "x.ops", attempt=1, max_attempts=3,
                exc=PermissionError(13, "x"), final=True,
            )

    def test_appends_multiple_records(self, tmp_path: Path) -> None:
        log_path = tmp_path / "race_log.jsonl"
        race_log.configure_race_log(log_path)
        target = tmp_path / "target.ops"
        target.write_text("- op: create\n")
        for attempt in (1, 2, 3):
            race_log.log_read_race(
                target, attempt=attempt, max_attempts=3,
                exc=PermissionError(13, "EACCES"),
                final=(attempt == 3),
            )
        lines = log_path.read_text().splitlines()
        assert len(lines) == 3


class TestLogCompileSuppression:
    def test_writes_record_with_event_marker(self, tmp_path: Path) -> None:
        log_path = tmp_path / "race_log.jsonl"
        race_log.configure_race_log(log_path)
        target = tmp_path / "target.ops"
        target.write_text("- op: create\n")
        race_log.log_compile_suppression(
            target, IsADirectoryError(21, "EISDIR"),
        )
        rec = json.loads(log_path.read_text().splitlines()[0])
        assert rec["event"] == "compile_suppressed"
        assert rec["errno"] == 21
        assert "IsADirectoryError" in rec["error"]
        assert rec["path"] == str(target)

    def test_noop_when_unconfigured(self, tmp_path: Path) -> None:
        race_log.configure_race_log(None)
        race_log.log_compile_suppression(
            tmp_path / "x.ops", OSError(5, "EIO"),
        )
        assert list(tmp_path.iterdir()) == []

    def test_stat_is_null_when_file_missing(self, tmp_path: Path) -> None:
        log_path = tmp_path / "race_log.jsonl"
        race_log.configure_race_log(log_path)
        race_log.log_compile_suppression(
            tmp_path / "vanished.ops", OSError(5, "EIO"),
        )
        rec = json.loads(log_path.read_text().splitlines()[0])
        assert rec["stat"] is None

    def test_swallows_write_errors(self, tmp_path: Path) -> None:
        log_path = tmp_path / "race_log.jsonl"
        race_log.configure_race_log(log_path)
        with patch("hypergumbo_tracker.race_log.open", side_effect=OSError("disk full")):
            race_log.log_compile_suppression(
                tmp_path / "x.ops", OSError(5, "EIO"),
            )


class TestComputeDefaultPath:
    def test_uses_xdg_cache_home_when_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
        tracker_root = tmp_path / "project" / ".agent"
        tracker_root.mkdir(parents=True)
        p = race_log.compute_default_race_log_path(tracker_root)
        assert p.is_relative_to(tmp_path / "xdg" / "hypergumbo" / "tracker")
        assert p.name == "race_log.jsonl"

    def test_falls_back_to_home_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        tracker_root = tmp_path / "project" / ".agent"
        tracker_root.mkdir(parents=True)
        p = race_log.compute_default_race_log_path(tracker_root)
        assert p.is_relative_to(tmp_path / "home" / ".cache" / "hypergumbo" / "tracker")
        assert p.name == "race_log.jsonl"

    def test_fingerprint_stable_and_path_specific(self, tmp_path: Path) -> None:
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()
        p_a1 = race_log.compute_default_race_log_path(root_a)
        p_a2 = race_log.compute_default_race_log_path(root_a)
        p_b = race_log.compute_default_race_log_path(root_b)
        assert p_a1 == p_a2
        assert p_a1 != p_b
