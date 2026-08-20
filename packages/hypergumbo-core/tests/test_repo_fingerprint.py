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

from hypergumbo_core.repo_fingerprint import (
    compute_repo_fingerprint,
    compute_repo_fingerprint_field,
)


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


class TestFieldRendering:
    """WI-bosog: the AnalysisRun ``repo_fingerprint`` FIELD carries the
    ``sha256:`` scheme prefix (matching the sibling ``run_signature`` /
    ``config_fingerprint`` identity fields), while the bare digest is reserved
    for the colon-free cache-dir path segment."""

    def test_field_is_scheme_prefixed_full_digest(self, git_repo: Path) -> None:
        field = compute_repo_fingerprint_field(git_repo)
        assert field.startswith("sha256:")
        hex_part = field[len("sha256:"):]
        assert len(hex_part) == 64
        assert all(c in "0123456789abcdef" for c in hex_part)

    def test_field_wraps_the_bare_digest(self, git_repo: Path) -> None:
        # The field is exactly the bare cache-dir digest with the scheme prefix,
        # so the two never diverge in the underlying hash.
        assert (
            compute_repo_fingerprint_field(git_repo)
            == "sha256:" + compute_repo_fingerprint(git_repo)
        )

    def test_field_works_for_non_git(self, non_git_repo: Path) -> None:
        field = compute_repo_fingerprint_field(non_git_repo)
        assert field.startswith("sha256:")
        assert len(field[len("sha256:"):]) == 64


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

    def test_rename_handled_without_double_counting(
        self, git_repo: Path,
    ) -> None:
        """`git status --porcelain -z` emits renames as a status code
        plus *two* path records (new then old). The dirty-files parser
        must skip the second record to avoid hashing a missing path."""
        _git(git_repo, "mv", "a.py", "renamed.py")
        fp = compute_repo_fingerprint(git_repo)
        assert isinstance(fp, str) and len(fp) == 64

    def test_deleted_file_does_not_break_fingerprint(
        self, git_repo: Path,
    ) -> None:
        """Deleted files (status code `D`) have no content to hash; the
        parser must skip them rather than trying to read the missing
        path. Without this guard the function would crash on any repo
        with staged or working-tree deletions."""
        (git_repo / "a.py").unlink()
        fp = compute_repo_fingerprint(git_repo)
        assert isinstance(fp, str) and len(fp) == 64


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

    def test_non_source_files_excluded(self, tmp_path: Path) -> None:
        """Files whose suffix isn't in _SOURCE_EXTENSIONS (e.g. .txt,
        .json, hypergumbo's own output) must not affect the fingerprint
        — otherwise running hypergumbo into a sibling output directory
        re-pollutes the cache key on every invocation."""
        (tmp_path / "src.py").write_text("print('src')\n")
        fp_before = compute_repo_fingerprint(tmp_path)
        (tmp_path / "output.json").write_text('{"x": 1}\n')
        (tmp_path / "readme.txt").write_text("notes\n")
        fp_after = compute_repo_fingerprint(tmp_path)
        assert fp_before == fp_after

    def test_excluded_directories_skipped(self, tmp_path: Path) -> None:
        """Files inside excluded directories (.git, __pycache__,
        node_modules, .venv, venv) must not contribute to the
        fingerprint — these are cache/build artifacts, not analyzer
        inputs."""
        (tmp_path / "src.py").write_text("print('src')\n")
        fp_before = compute_repo_fingerprint(tmp_path)
        # Drop a source-extension file inside an excluded dir.
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "junk.py").write_text("print('junk')\n")
        fp_after = compute_repo_fingerprint(tmp_path)
        assert fp_before == fp_after

    def test_subdirectories_walked_without_directory_being_hashed(
        self, tmp_path: Path,
    ) -> None:
        """The walk must recurse into subdirectories (so nested source
        files contribute) without trying to read the directory itself
        as a file (``not p.is_file()`` branch)."""
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "nested.py").write_text("print('nested')\n")
        fp = compute_repo_fingerprint(tmp_path)
        # The fingerprint changes when nested file content changes —
        # proves the walk found it inside the subdir.
        (sub / "nested.py").write_text("print('changed')\n")
        fp_changed = compute_repo_fingerprint(tmp_path)
        assert fp != fp_changed


# ---------------------------------------------------------------------------
# Git branch — empty-repo edge case (no commits yet)
# ---------------------------------------------------------------------------


class TestGitBranchEmptyRepo:
    def test_no_commits_yet_does_not_crash(self, tmp_path: Path) -> None:
        """``git rev-parse HEAD`` fails on a freshly-initialized repo
        with no commits. ``_git_head`` returns an empty string in that
        case; the fingerprint is computed without a HEAD component but
        must still be a valid 64-char hex digest."""
        _git(tmp_path, "init", "-q")
        (tmp_path / "untracked.py").write_text("print('hi')\n")
        fp = compute_repo_fingerprint(tmp_path)
        assert isinstance(fp, str) and len(fp) == 64


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


# ---------------------------------------------------------------------------
# Unreadable-file toleration (WI-madal / §17 fail-open)
# ---------------------------------------------------------------------------


class TestUnreadableFileToleration:
    """A file that exists but cannot be read must not abort the fingerprint.

    ``_hash_file_content`` is the single chokepoint where both the git and
    non-git branches read file bytes, so guarding it there covers both with
    one fail-open ``except OSError``. Tests force the read to fail via
    monkeypatch rather than ``chmod(0o000)`` — ``chmod`` does not deny root
    and CI may run as root, so a real-permission test would be euid-flaky.
    """

    def test_hash_file_content_returns_sentinel_when_unreadable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hypergumbo_core.repo_fingerprint import (
            _UNREADABLE_CONTENT_SENTINEL,
            _hash_file_content,
        )

        f = tmp_path / "secret.py"
        f.write_text("print('x')\n")

        def boom(self: Path) -> bytes:
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(Path, "read_bytes", boom)
        assert _hash_file_content(f) == _UNREADABLE_CONTENT_SENTINEL

    def test_non_git_fingerprint_tolerates_unreadable_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "ok.py").write_text("print('ok')\n")
        (tmp_path / "bad.py").write_text("print('bad')\n")

        real_read_bytes = Path.read_bytes

        def deny_bad(self: Path) -> bytes:
            if self.name == "bad.py":
                raise PermissionError(13, "Permission denied")
            return real_read_bytes(self)

        monkeypatch.setattr(Path, "read_bytes", deny_bad)
        fp = compute_repo_fingerprint(tmp_path)
        assert isinstance(fp, str) and len(fp) == 64

    def test_non_git_unreadable_fingerprint_is_deterministic(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two reads of the same unreadable-set produce the same fingerprint
        — the sentinel is a fixed digest, so determinism is preserved."""
        (tmp_path / "ok.py").write_text("print('ok')\n")
        (tmp_path / "bad.py").write_text("print('bad')\n")

        real_read_bytes = Path.read_bytes

        def deny_bad(self: Path) -> bytes:
            if self.name == "bad.py":
                raise PermissionError(13, "Permission denied")
            return real_read_bytes(self)

        monkeypatch.setattr(Path, "read_bytes", deny_bad)
        assert compute_repo_fingerprint(tmp_path) == compute_repo_fingerprint(tmp_path)

    def test_git_fingerprint_tolerates_unreadable_dirty_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The git branch hashes dirty-file bytes through the same
        ``_hash_file_content`` chokepoint, so the guard covers it too."""
        _git(tmp_path, "init", "-q")
        (tmp_path / "tracked.py").write_text("print('v1')\n")
        _git(tmp_path, "add", "tracked.py")
        _git(tmp_path, "commit", "-q", "-m", "init")
        # Dirty the tracked file so it enters the dirty-file hashing path.
        (tmp_path / "tracked.py").write_text("print('v2')\n")

        real_read_bytes = Path.read_bytes

        def deny_tracked(self: Path) -> bytes:
            if self.name == "tracked.py":
                raise PermissionError(13, "Permission denied")
            return real_read_bytes(self)

        monkeypatch.setattr(Path, "read_bytes", deny_tracked)
        fp = compute_repo_fingerprint(tmp_path)
        assert isinstance(fp, str) and len(fp) == 64

    def test_sentinel_digest_is_pinned_to_scheme(self) -> None:
        """Pin the sentinel's literal digest.

        The sentinel feeds the repo_fingerprint, which is itself a cache-key
        segment. Silently changing the sentinel byte-string would invalidate
        every cache for a repo that contains an unreadable file — with zero
        test failure, because the other tests compare against the imported
        constant, not its value. Per spec line 392 a value-altering change to
        the fingerprint must bump ``repo_fingerprint_scheme``
        (``hypergumbo-repofp-v2``); this literal pin forces that coupling to be
        a conscious, reviewed edit rather than an accidental cache wipe.
        """
        from hypergumbo_core.repo_fingerprint import _UNREADABLE_CONTENT_SENTINEL

        assert _UNREADABLE_CONTENT_SENTINEL == (
            "67d7acff712bc6477b6319b001dd00f0daf551b2c7c75705fc4fdc222becb4c2"
        )


class TestNoCodeExecutionFromTargetRepo:
    """A hostile target repo must not be able to run code during analysis.

    VERIFIED VULNERABILITY (canary, exit 0, silent): ``hypergumbo
    io-boundaries <repo>`` executed an attacker-supplied program 6 times,
    because fingerprinting shelled out to ``git status`` with cwd inside the
    target repo. Three independent vectors were demonstrated on that one
    command:

    * ``core.fsmonitor`` — a program path in the repo's own ``.git/config``.
    * ``.git/hooks/post-index-change`` — fires with **no config keys set at
      all**, so auditing ``.git/config`` is not a mitigation.
    * ``filter.<driver>.clean`` armed by an in-tree ``.gitattributes`` —
      measured to survive ``-c core.fsmonitor=false -c core.hooksPath=/dev/null
      -c core.attributesFile=/dev/null --literal-pathspecs
      --no-optional-locks`` simultaneously. Suppressing it requires naming the
      driver, and the driver name is chosen by the attacker.

    Because the third vector cannot be closed by hardening, the fix is to not
    run ``git status`` at all. Its result was never load-bearing — it fed only
    a cache-key/provenance digest, and the content-hash path already computes
    the same class of value with no subprocess (measured cost: 0.12s on pretix,
    1.7s on this repo, against analyses that take minutes).

    This test pins the absence rather than the hardening, because a hardening
    flag list silently rots as git gains new exec-capable keys.
    """

    def test_fingerprinting_never_runs_git_status(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No subprocess invoked while fingerprinting may be ``git status``.

        SEAM MOVED, INTENT UNCHANGED (WI-fasuv). This used to patch
        ``rf.subprocess.run``; ``repo_fingerprint`` no longer imports
        ``subprocess`` at all, because every git invocation now routes through
        ``safety_zones.repo_inspect_git``. Patching the wrapper is if anything
        a tighter seam than patching the module's ``subprocess``: the wrapper
        is the single declared chokepoint for this module's process
        execution, so a future git call added anywhere in it is recorded here
        by construction rather than by remembering to import the right thing.
        """
        from hypergumbo_core import repo_fingerprint as rf

        seen: list[list[str]] = []
        real_run = subprocess.run

        def recording_run(argv, *a, **kw):  # type: ignore[no-untyped-def]
            seen.append([str(x) for x in argv])
            return real_run(argv, *a, **kw)

        monkeypatch.setattr(rf, "repo_inspect_git", recording_run)
        compute_repo_fingerprint(git_repo)

        offending = [c for c in seen if "status" in c]
        assert not offending, (
            f"fingerprinting shelled out to git status: {offending}. That "
            f"command executes repo-controlled programs via core.fsmonitor, "
            f".git/hooks/post-index-change and filter.*.clean, and the last "
            f"of those survives every hardening flag tested."
        )

    def test_dirty_content_still_changes_the_fingerprint(
        self, git_repo: Path,
    ) -> None:
        """CONTROL: removing ``git status`` must not cost content sensitivity.

        Without this, the security pin above could be satisfied by a
        fingerprint that ignores the working tree entirely — which would make
        every cached analysis stale-but-accepted, a correctness regression
        dressed up as a security fix.
        """
        before = compute_repo_fingerprint(git_repo)
        (git_repo / "tracked.py").write_text("print('changed after commit')\n")
        after = compute_repo_fingerprint(git_repo)
        assert before != after
