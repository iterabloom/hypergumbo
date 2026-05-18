# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for hypergumbo_core.repo_fingerprint.

INV-tofur: AnalysisRun.repo_fingerprint must be populated by the
producer pipeline. The spec (docs/hypergumbo-spec.md:378-384) defines
the algorithm:

- Git repos: sha256(git_head + sorted([(path, sha256(content_bytes))
  for each dirty file])).
- Non-git: sha256(sorted([(path, content_hash) for all files])).

Property tests assert:
  - determinism (same state → same fingerprint),
  - content sensitivity (dirty-file content change → fingerprint changes),
  - mtime insensitivity (touch with same content → fingerprint stable),
  - git/non-git divergence (the two branches use different inputs).
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - tests need git
import time
from pathlib import Path

import pytest

from hypergumbo_core.repo_fingerprint import compute_repo_fingerprint


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    """Run a git command in cwd, return stdout. Fail loudly on non-zero."""
    env = os.environ.copy()
    env.update(
        GIT_AUTHOR_NAME="test",
        GIT_AUTHOR_EMAIL="test@example.com",
        GIT_COMMITTER_NAME="test",
        GIT_COMMITTER_EMAIL="test@example.com",
    )
    git_path = shutil.which("git") or "git"
    out = subprocess.run(  # nosec B603 - git_path resolved via shutil.which
        [git_path, *args],
        cwd=cwd, env=env, check=True,
        capture_output=True, text=True,
    )
    return out.stdout.strip()


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """A clean git repo with one tracked file."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text("print('a')\n")
    _git(tmp_path, "add", "a.py")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


@pytest.fixture()
def non_git_repo(tmp_path: Path) -> Path:
    """A plain directory with a few source files, no .git."""
    (tmp_path / "a.py").write_text("print('a')\n")
    (tmp_path / "b.py").write_text("print('b')\n")
    return tmp_path


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


class TestOutputShape:
    def test_returns_64_char_hex(self, git_repo: Path) -> None:
        fp = compute_repo_fingerprint(git_repo)
        assert isinstance(fp, str)
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)

    def test_returns_64_char_hex_for_non_git(self, non_git_repo: Path) -> None:
        fp = compute_repo_fingerprint(non_git_repo)
        assert isinstance(fp, str)
        assert len(fp) == 64


# ---------------------------------------------------------------------------
# Git branch — determinism, content sensitivity, mtime insensitivity
# ---------------------------------------------------------------------------


class TestGitBranch:
    def test_clean_repo_deterministic(self, git_repo: Path) -> None:
        """Two reads of the same clean repo state → same fingerprint."""
        assert compute_repo_fingerprint(git_repo) == compute_repo_fingerprint(git_repo)

    def test_dirty_file_content_change_changes_fingerprint(
        self, git_repo: Path,
    ) -> None:
        """Spec line 382 explicit: 'ensures repo_fingerprint changes when
        dirty file contents change'."""
        before = compute_repo_fingerprint(git_repo)
        (git_repo / "a.py").write_text("print('modified')\n")
        after = compute_repo_fingerprint(git_repo)
        assert before != after

    def test_mtime_only_change_does_not_change_fingerprint(
        self, git_repo: Path,
    ) -> None:
        """Dirty the file, then touch it to bump mtime while preserving
        content — fingerprint must stay stable. This is the property the
        existing _get_repo_state_hash (path:size:mtime) violates, which
        is INV-magul's cache-pollution mechanism."""
        (git_repo / "a.py").write_text("print('dirty')\n")
        fp1 = compute_repo_fingerprint(git_repo)
        time.sleep(0.05)
        os.utime(git_repo / "a.py", None)
        fp2 = compute_repo_fingerprint(git_repo)
        assert fp1 == fp2

    def test_new_commit_changes_fingerprint(self, git_repo: Path) -> None:
        """A second commit moves HEAD → fingerprint must differ."""
        before = compute_repo_fingerprint(git_repo)
        (git_repo / "b.py").write_text("print('b')\n")
        _git(git_repo, "add", "b.py")
        _git(git_repo, "commit", "-q", "-m", "add b")
        after = compute_repo_fingerprint(git_repo)
        assert before != after

    def test_untracked_file_changes_fingerprint(self, git_repo: Path) -> None:
        """Spec line 381: 'untracked file included in analysis' counts
        as dirty."""
        before = compute_repo_fingerprint(git_repo)
        (git_repo / "untracked.py").write_text("print('new')\n")
        after = compute_repo_fingerprint(git_repo)
        assert before != after

    def test_untracked_file_path_order_irrelevant(self, git_repo: Path) -> None:
        """Sorted([(path, hash) ...]) means file-creation order can't
        change the fingerprint."""
        (git_repo / "x.py").write_text("print('x')\n")
        (git_repo / "y.py").write_text("print('y')\n")
        fp_xy = compute_repo_fingerprint(git_repo)
        (git_repo / "x.py").unlink()
        (git_repo / "y.py").unlink()
        (git_repo / "y.py").write_text("print('y')\n")
        (git_repo / "x.py").write_text("print('x')\n")
        fp_yx = compute_repo_fingerprint(git_repo)
        assert fp_xy == fp_yx


# ---------------------------------------------------------------------------
# Non-git branch
# ---------------------------------------------------------------------------


class TestNonGitBranch:
    def test_clean_dir_deterministic(self, non_git_repo: Path) -> None:
        assert (
            compute_repo_fingerprint(non_git_repo)
            == compute_repo_fingerprint(non_git_repo)
        )

    def test_content_change_changes_fingerprint(self, non_git_repo: Path) -> None:
        before = compute_repo_fingerprint(non_git_repo)
        (non_git_repo / "a.py").write_text("print('modified')\n")
        after = compute_repo_fingerprint(non_git_repo)
        assert before != after

    def test_mtime_only_change_does_not_change_fingerprint(
        self, non_git_repo: Path,
    ) -> None:
        fp1 = compute_repo_fingerprint(non_git_repo)
        time.sleep(0.05)
        os.utime(non_git_repo / "a.py", None)
        fp2 = compute_repo_fingerprint(non_git_repo)
        assert fp1 == fp2

    def test_new_file_changes_fingerprint(self, non_git_repo: Path) -> None:
        before = compute_repo_fingerprint(non_git_repo)
        (non_git_repo / "c.py").write_text("print('c')\n")
        after = compute_repo_fingerprint(non_git_repo)
        assert before != after

    def test_empty_dir_returns_stable_fingerprint(self, tmp_path: Path) -> None:
        """An empty directory must produce a deterministic fingerprint
        (not raise) so the caller can still stamp AnalysisRun.repo_fingerprint."""
        fp1 = compute_repo_fingerprint(tmp_path)
        fp2 = compute_repo_fingerprint(tmp_path)
        assert fp1 == fp2
        assert len(fp1) == 64


# ---------------------------------------------------------------------------
# Cross-branch — git and non-git must produce different inputs
# ---------------------------------------------------------------------------


class TestGitVsNonGitDivergence:
    def test_same_files_different_branches_different_fingerprints(
        self, tmp_path: Path,
    ) -> None:
        """A directory analyzed first as non-git, then as a git repo with
        the same file contents, should produce different fingerprints —
        the git branch incorporates git_head (a commit SHA) which the
        non-git branch lacks. This proves the two branches are not
        accidentally collapsed to one algorithm."""
        d_non_git = tmp_path / "ng"
        d_non_git.mkdir()
        (d_non_git / "a.py").write_text("print('a')\n")
        fp_non_git = compute_repo_fingerprint(d_non_git)

        d_git = tmp_path / "g"
        d_git.mkdir()
        (d_git / "a.py").write_text("print('a')\n")
        _git(d_git, "init", "-q")
        _git(d_git, "add", "a.py")
        _git(d_git, "commit", "-q", "-m", "init")
        fp_git = compute_repo_fingerprint(d_git)

        assert fp_non_git != fp_git
