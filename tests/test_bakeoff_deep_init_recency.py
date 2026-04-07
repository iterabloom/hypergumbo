# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for WI-kumub: bakeoff-deep init recency warning.

``init`` should warn when a recent (< 7 days) session exists with the
same pool and same hypergumbo code hash, because the user almost
certainly wants to ``cycle`` against the existing session instead of
starting a new one. The warning is opt-out (sleep + continue, not
blocking) so scripted batch use is unaffected by
``BAKEOFF_DEEP_SKIP_RECENT_WARNING=1``.

The pure ``_check_recent_session_for_init`` helper takes a base workdir,
pool path, and code hash and returns a dict describing the candidate
session (or ``None`` if no warning should fire). Tests exercise the
helper directly so they don't depend on subprocess/stdout scraping.
"""

import datetime
import importlib
import importlib.machinery
import importlib.util
import json
import os
import time
from pathlib import Path

import pytest


def _load_bakeoff_features():
    """Import scripts/bakeoff-deep as a module despite no .py extension."""
    script_path = str(
        Path(__file__).resolve().parent.parent / "scripts" / "bakeoff-deep"
    )
    loader = importlib.machinery.SourceFileLoader("bakeoff_features", script_path)
    spec = importlib.util.spec_from_loader("bakeoff_features", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


bf = _load_bakeoff_features()


def _make_session(
    base_workdir: Path,
    prefix: str,
    name: str,
    *,
    pool_path: str,
    code_hash: str,
    created_at: datetime.datetime,
    cohort_number: int = 1,
) -> Path:
    """Create a minimal session directory + state.json for tests."""
    sess_dir = base_workdir / f"{prefix}{name}"
    sess_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "session_id": "test-session",
        "pool_path": pool_path,
        "workdir": str(sess_dir),
        "created_at": created_at.isoformat(),
        "hypergumbo_code_hash": code_hash,
        "cohort_number": cohort_number,
        "cohorts": [],
        "analyzed_repos": [],
        "iteration": 1,
    }
    (sess_dir / "state.json").write_text(json.dumps(state))
    return sess_dir


class TestCheckRecentSessionForInit:
    """WI-kumub: _check_recent_session_for_init helper."""

    def test_no_sessions_returns_none(self, tmp_path: Path):
        """Empty workdir returns None (no warning to emit)."""
        result = bf._check_recent_session_for_init(
            base_workdir=str(tmp_path),
            pool_path="/pool",
            current_code_hash="abc123",
        )
        assert result is None

    def test_recent_same_pool_same_hash_warns(self, tmp_path: Path):
        """Recent session matching both pool and code hash → warn."""
        now = time.time()
        created = datetime.datetime.fromtimestamp(now - 3600)  # 1 hour ago
        _make_session(
            tmp_path, "deep-", "20260407-050000",
            pool_path="/pool",
            code_hash="abc123",
            created_at=created,
        )
        result = bf._check_recent_session_for_init(
            base_workdir=str(tmp_path),
            pool_path="/pool",
            current_code_hash="abc123",
            now=now,
        )
        assert result is not None
        assert result["session_name"] == "deep-20260407-050000"
        assert result["age_hours"] == pytest.approx(1.0, abs=0.1)
        assert result["same_pool"] is True
        assert result["same_code"] is True
        assert result["cohort_count"] == 1

    def test_old_session_does_not_warn(self, tmp_path: Path):
        """Session older than the 7-day threshold does not warn."""
        now = time.time()
        # 8 days old
        created = datetime.datetime.fromtimestamp(now - 8 * 86400)
        _make_session(
            tmp_path, "deep-", "20260330-050000",
            pool_path="/pool",
            code_hash="abc123",
            created_at=created,
        )
        result = bf._check_recent_session_for_init(
            base_workdir=str(tmp_path),
            pool_path="/pool",
            current_code_hash="abc123",
            now=now,
        )
        assert result is None

    def test_different_pool_does_not_warn(self, tmp_path: Path):
        """Same code but different pool → genuine new inquiry, no warn."""
        now = time.time()
        created = datetime.datetime.fromtimestamp(now - 3600)
        _make_session(
            tmp_path, "deep-", "20260407-050000",
            pool_path="/pool-a",
            code_hash="abc123",
            created_at=created,
        )
        result = bf._check_recent_session_for_init(
            base_workdir=str(tmp_path),
            pool_path="/pool-b",
            current_code_hash="abc123",
            now=now,
        )
        assert result is None

    def test_different_code_hash_does_not_warn(self, tmp_path: Path):
        """Same pool but different code → validating a fix, no warn."""
        now = time.time()
        created = datetime.datetime.fromtimestamp(now - 3600)
        _make_session(
            tmp_path, "deep-", "20260407-050000",
            pool_path="/pool",
            code_hash="old_hash",
            created_at=created,
        )
        result = bf._check_recent_session_for_init(
            base_workdir=str(tmp_path),
            pool_path="/pool",
            current_code_hash="new_hash",
            now=now,
        )
        assert result is None

    def test_empty_current_code_hash_does_not_warn(self, tmp_path: Path):
        """Empty current hash (unknown) never matches — no warn."""
        now = time.time()
        created = datetime.datetime.fromtimestamp(now - 3600)
        _make_session(
            tmp_path, "deep-", "20260407-050000",
            pool_path="/pool",
            code_hash="abc123",
            created_at=created,
        )
        result = bf._check_recent_session_for_init(
            base_workdir=str(tmp_path),
            pool_path="/pool",
            current_code_hash="",
            now=now,
        )
        assert result is None

    def test_missing_state_created_at_does_not_warn(self, tmp_path: Path):
        """Session with missing/invalid created_at gives up gracefully."""
        sess_dir = tmp_path / "deep-broken"
        sess_dir.mkdir()
        state = {
            "session_id": "x",
            "pool_path": "/pool",
            "workdir": str(sess_dir),
            "created_at": "not-a-date",
            "hypergumbo_code_hash": "abc123",
            "cohort_number": 1,
            "cohorts": [],
            "analyzed_repos": [],
            "iteration": 1,
        }
        (sess_dir / "state.json").write_text(json.dumps(state))
        result = bf._check_recent_session_for_init(
            base_workdir=str(tmp_path),
            pool_path="/pool",
            current_code_hash="abc123",
        )
        assert result is None

    def test_state_load_failure_does_not_warn(self, tmp_path: Path):
        """Session dir with unreadable state.json returns None."""
        sess_dir = tmp_path / "deep-corrupt"
        sess_dir.mkdir()
        (sess_dir / "state.json").write_text("this is not valid json at all")
        result = bf._check_recent_session_for_init(
            base_workdir=str(tmp_path),
            pool_path="/pool",
            current_code_hash="abc123",
        )
        assert result is None

    def test_prefix_filter(self, tmp_path: Path):
        """Only sessions with the requested prefix are considered."""
        now = time.time()
        created = datetime.datetime.fromtimestamp(now - 3600)
        # Create a broad- session; deep- check should ignore it
        _make_session(
            tmp_path, "broad-", "20260407-050000",
            pool_path="/pool",
            code_hash="abc123",
            created_at=created,
        )
        result = bf._check_recent_session_for_init(
            base_workdir=str(tmp_path),
            pool_path="/pool",
            current_code_hash="abc123",
            now=now,
            prefix="deep-",
        )
        assert result is None

    def test_nonexistent_workdir_returns_none(self, tmp_path: Path):
        """A missing base_workdir path yields None instead of raising."""
        result = bf._check_recent_session_for_init(
            base_workdir=str(tmp_path / "does_not_exist"),
            pool_path="/pool",
            current_code_hash="abc123",
        )
        assert result is None
