# SPDX-License-Identifier: AGPL-3.0-or-later
"""Integration tests for WI-gutan: bakeoff-deep session/iteration tracking.

Bakeoff session organization (init → cohort → cycle → iter-NNN) was
previously "tested in prod" rather than via unit/integration tests.
This module exercises the orchestration logic of the deep bakeoff CLI
end-to-end with ``_run_cohort_repos`` (and ``cmd_diagnose``) mocked
out so the tests do not actually invoke ``hypergumbo`` on real repos.

Covered scenarios (from the WI-gutan tracker item):
- (a) ``init`` creates a fresh timestamped session under the workdir
- (b) ``cycle`` (and ``run``) increments ``state.iteration``
- (c) ``cycle`` creates ``out/cohort-NNN/iter-NNN/`` subdirs in the
      same session as previous iterations (does not start a new session)
- (d) auto-discovery picks the latest session when no ``state.json``
      exists at the root
- (e) ``run`` without a prior cohort (or session) errors out cleanly
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
from typing import List
from unittest import mock

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


def _make_pool(pool: Path, repo_names: List[str]) -> None:
    """Create a fake repo pool with the given repo names.

    cmd_cohort with --repos only checks that each repo dir exists; it
    does not impose the MIN_FILE_COUNT filter that auto-selection uses.
    A handful of empty placeholder files is enough.
    """
    pool.mkdir(parents=True, exist_ok=True)
    for name in repo_names:
        repo_dir = pool / name
        repo_dir.mkdir(parents=True, exist_ok=True)
        (repo_dir / "main.py").write_text("print('hi')\n")


def _ns(**kwargs) -> argparse.Namespace:
    """Build an argparse.Namespace with the supplied attributes."""
    return argparse.Namespace(**kwargs)


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide an isolated bakeoff base workdir + silence the init warning.

    The workdir is the *base* directory under which init creates a new
    timestamped session subdirectory (deep-YYYYMMDD-HHMMSS).  We also
    set BAKEOFF_DEEP_SKIP_RECENT_WARNING so the recency-warning sleep
    in cmd_init never fires (its slow time.sleep would dominate test
    duration even though it is opt-out, and the recency warning is
    covered separately by test_bakeoff_deep_init_recency.py).
    """
    base = tmp_path / "bakeoff_artifacts"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("BAKEOFF_DEEP_SKIP_RECENT_WARNING", "1")
    return base


@pytest.fixture
def pool(tmp_path: Path) -> Path:
    """Provide a fake pool with three placeholder repos."""
    p = tmp_path / "pool"
    _make_pool(p, ["repo-a", "repo-b", "repo-c"])
    return p


def _find_session(base: Path, prefix: str = "deep-") -> Path:
    """Return the single session directory under *base* (or fail loudly)."""
    sessions = sorted(d for d in base.iterdir() if d.name.startswith(prefix))
    assert sessions, f"expected at least one {prefix}* session under {base}"
    return sessions[-1]


def _stub_run_cohort_repos(repos, pool_path, out_dir, workdir=""):
    """Stand-in for _run_cohort_repos that mimics the real directory layout.

    Creates one subdir per repo with a placeholder ``hg.json`` so that
    code which scans for completed runs (``cmd_run``'s "already has
    output" branch, ``cmd_diagnose``'s repo discovery, etc.) sees the
    iteration as legitimately populated.
    """
    for repo in repos:
        rd = os.path.join(out_dir, repo)
        os.makedirs(rd, exist_ok=True)
        with open(os.path.join(rd, "hg.json"), "w") as f:
            json.dump({"nodes": [], "edges": []}, f)
    return 0


# ---------------------------------------------------------------------------
# (a) init creates a fresh session
# ---------------------------------------------------------------------------


class TestInitCreatesFreshSession:
    """``init`` always creates a new timestamped session by default."""

    def test_init_creates_timestamped_session_dir(
        self, workdir: Path, pool: Path
    ) -> None:
        rc = bf.cmd_init(_ns(pool=str(pool), workdir=str(workdir), resume=False, force=False))
        assert rc == 0
        sess = _find_session(workdir)
        assert sess.name.startswith("deep-")
        # The standard subdirectories must be present.
        for sub in ("cohorts", "out", "diag"):
            assert (sess / sub).is_dir()
        # state.json is written with the user's pool path.
        state_data = json.loads((sess / "state.json").read_text())
        assert state_data["pool_path"] == str(pool)
        assert state_data["cohort_number"] == 0
        assert state_data["iteration"] == 0

    def test_init_assigns_unique_session_id(
        self, workdir: Path, pool: Path
    ) -> None:
        """Two consecutive inits produce two distinct session_ids."""
        rc1 = bf.cmd_init(_ns(pool=str(pool), workdir=str(workdir), resume=False, force=False))
        # Sleep one whole second so the timestamped dir name differs;
        # the bakeoff timestamp granularity is YYYYMMDD-HHMMSS.
        import time
        time.sleep(1.05)
        rc2 = bf.cmd_init(_ns(pool=str(pool), workdir=str(workdir), resume=False, force=False))
        assert rc1 == 0 and rc2 == 0
        sessions = sorted(d for d in workdir.iterdir() if d.name.startswith("deep-"))
        assert len(sessions) == 2
        ids = {
            json.loads((s / "state.json").read_text())["session_id"]
            for s in sessions
        }
        assert len(ids) == 2

    def test_init_rejects_missing_pool(
        self, workdir: Path, tmp_path: Path
    ) -> None:
        """A non-existent pool path is rejected with a non-zero exit."""
        bogus = tmp_path / "no-such-pool"
        rc = bf.cmd_init(_ns(pool=str(bogus), workdir=str(workdir), resume=False, force=False))
        assert rc == 1
        # No session dir was created.
        assert not any(d.name.startswith("deep-") for d in workdir.iterdir())


# ---------------------------------------------------------------------------
# (b) + (c) cycle increments iteration counter and reuses the session
# ---------------------------------------------------------------------------


class TestIterationCounterAndDirectoryLayout:
    """``cycle``/``run`` increment ``state.iteration`` and create iter-NNN/."""

    def _init_cohort(self, workdir: Path, pool: Path) -> Path:
        """Helper: init + select an explicit cohort, return the session path."""
        assert bf.cmd_init(_ns(
            pool=str(pool), workdir=str(workdir), resume=False, force=False,
        )) == 0
        sess = _find_session(workdir)
        assert bf.cmd_cohort(_ns(
            workdir=str(sess), repos="repo-a,repo-b",
            count=None, min_size=None, max_size=None, dry_run=False,
        )) == 0
        return sess

    def test_run_increments_iteration_counter(
        self, workdir: Path, pool: Path
    ) -> None:
        sess = self._init_cohort(workdir, pool)

        with mock.patch.object(bf, "_run_cohort_repos", _stub_run_cohort_repos):
            assert bf.cmd_run(_ns(workdir=str(sess), all=False, some=None)) == 0

        state_data = json.loads((sess / "state.json").read_text())
        assert state_data["iteration"] == 1
        assert state_data["cohort_number"] == 1
        # The iter-001/ directory exists and contains both repos.
        iter_dir = sess / "out" / "cohort-001" / "iter-001"
        assert iter_dir.is_dir()
        assert (iter_dir / "repo-a" / "hg.json").is_file()
        assert (iter_dir / "repo-b" / "hg.json").is_file()

    def test_second_run_creates_iter_002_in_same_session(
        self, workdir: Path, pool: Path
    ) -> None:
        """The second cycle increments iter, does NOT create a new session."""
        sess = self._init_cohort(workdir, pool)

        with mock.patch.object(bf, "_run_cohort_repos", _stub_run_cohort_repos):
            assert bf.cmd_run(_ns(workdir=str(sess), all=False, some=None)) == 0
            # Force the no-change skip to NOT trigger by tweaking the stored
            # code hash; the second run should then increment iter normally.
            state_data = json.loads((sess / "state.json").read_text())
            state_data["hypergumbo_code_hash"] = "stale-hash"
            (sess / "state.json").write_text(json.dumps(state_data))

            assert bf.cmd_run(_ns(workdir=str(sess), all=False, some=None)) == 0

        # Same session — only one deep-* dir under workdir.
        assert len([d for d in workdir.iterdir() if d.name.startswith("deep-")]) == 1
        # Both iter dirs present.
        cohort_dir = sess / "out" / "cohort-001"
        assert (cohort_dir / "iter-001").is_dir()
        assert (cohort_dir / "iter-002").is_dir()
        # Counter advanced to 2.
        state_data = json.loads((sess / "state.json").read_text())
        assert state_data["iteration"] == 2

    def test_no_change_skip_does_not_advance_iteration(
        self, workdir: Path, pool: Path
    ) -> None:
        """Same code hash + existing iter dir → skip, iter unchanged."""
        sess = self._init_cohort(workdir, pool)

        with mock.patch.object(bf, "_run_cohort_repos", _stub_run_cohort_repos):
            assert bf.cmd_run(_ns(workdir=str(sess), all=False, some=None)) == 0
            # Re-run with the same hash and an existing iter dir → skip.
            assert bf.cmd_run(_ns(workdir=str(sess), all=False, some=None)) == 0

        state_data = json.loads((sess / "state.json").read_text())
        # Still iteration 1 — the second run was a no-op skip.
        assert state_data["iteration"] == 1
        cohort_dir = sess / "out" / "cohort-001"
        assert (cohort_dir / "iter-001").is_dir()
        assert not (cohort_dir / "iter-002").exists()

    def test_cycle_with_skip_reflect_runs_run_then_diagnose(
        self, workdir: Path, pool: Path
    ) -> None:
        """``cycle --skip-reflect`` calls run + diagnose, no subprocess."""
        sess = self._init_cohort(workdir, pool)

        with mock.patch.object(bf, "_run_cohort_repos", _stub_run_cohort_repos), \
                mock.patch.object(bf, "cmd_diagnose", return_value=0) as diag_mock, \
                mock.patch("subprocess.run") as sp_mock:
            rc = bf.cmd_cycle(_ns(
                workdir=str(sess), skip_reflect=True, all=False, some=None,
            ))
            assert rc == 0
            diag_mock.assert_called_once()
            sp_mock.assert_not_called()

        # Iteration was advanced via cmd_run.
        state_data = json.loads((sess / "state.json").read_text())
        assert state_data["iteration"] == 1


# ---------------------------------------------------------------------------
# (d) auto-discovery picks the latest session
# ---------------------------------------------------------------------------


class TestAutoDiscovery:
    """``resolve_workdir`` finds the latest deep-* session under a base."""

    def test_resolve_workdir_picks_latest_when_no_root_state(
        self, workdir: Path, pool: Path
    ) -> None:
        """Multiple sessions under base — auto-discovery returns the newest."""
        # Build a deterministic ordering: sort lexically picks the lexically
        # largest, which by convention is the most recent timestamp.
        older = workdir / "deep-20260101-000000"
        newer = workdir / "deep-20260407-120000"
        for s in (older, newer):
            (s / "out").mkdir(parents=True)
            (s / "cohorts").mkdir()
            (s / "diag").mkdir()
            (s / "state.json").write_text(json.dumps({
                "session_id": s.name, "pool_path": str(pool), "workdir": str(s),
            }))

        resolved = bf.resolve_workdir(str(workdir))
        assert Path(resolved) == newer

    def test_resolve_workdir_returns_workdir_when_state_present(
        self, tmp_path: Path
    ) -> None:
        """If the resolved dir already has state.json, return it as-is."""
        sess = tmp_path / "explicit-session"
        sess.mkdir()
        (sess / "state.json").write_text("{}")
        assert bf.resolve_workdir(str(sess)) == str(sess)

    def test_resolve_workdir_no_sessions_returns_base(
        self, workdir: Path
    ) -> None:
        """Empty base directory: nothing to discover, return the base path."""
        # workdir exists but has no deep-* children
        assert bf.resolve_workdir(str(workdir)) == str(workdir)


# ---------------------------------------------------------------------------
# (e) errors when prerequisites are missing
# ---------------------------------------------------------------------------


class TestRunWithoutPrerequisites:
    """Running without a session or without a cohort errors cleanly."""

    def test_run_without_session_returns_error(
        self, workdir: Path
    ) -> None:
        """No state.json under workdir → cmd_run exits 1."""
        rc = bf.cmd_run(_ns(workdir=str(workdir), all=False, some=None))
        assert rc == 1

    def test_run_without_cohort_returns_error(
        self, workdir: Path, pool: Path
    ) -> None:
        """init was run, but no cohort selected → cmd_run exits 1."""
        assert bf.cmd_init(_ns(
            pool=str(pool), workdir=str(workdir), resume=False, force=False,
        )) == 0
        sess = _find_session(workdir)
        rc = bf.cmd_run(_ns(workdir=str(sess), all=False, some=None))
        assert rc == 1

    def test_diagnose_without_output_returns_error(
        self, workdir: Path, pool: Path
    ) -> None:
        """init + cohort but no run → cmd_diagnose exits 1 (no output)."""
        assert bf.cmd_init(_ns(
            pool=str(pool), workdir=str(workdir), resume=False, force=False,
        )) == 0
        sess = _find_session(workdir)
        assert bf.cmd_cohort(_ns(
            workdir=str(sess), repos="repo-a",
            count=None, min_size=None, max_size=None, dry_run=False,
        )) == 0
        # The session has out/ from init but it's empty — diagnose should
        # complain about no cohort output dirs.
        rc = bf.cmd_diagnose(_ns(workdir=str(sess), all=False, some=None))
        assert rc == 1
