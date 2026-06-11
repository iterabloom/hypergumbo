# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the ``_pr_landed_in_base`` git-ground-truth merge check in
``scripts/lib/forgejo-api.sh``.

Background
----------
Codeberg's merge API intermittently returns a transient failure (observed as
HTTP 000→405) for a merge that actually lands server-side. ``do_merge``'s
post-rebase poll loop relies on the API-based ``_check_pr_merged`` to confirm
the merge; when the API flakes, the loop gives up and ``do_merge`` returns
failure — so ``auto-pr``'s ``do_pr`` skips ``cleanup_local`` and never advances
local dev. A stale local dev is exactly what let a later ``git checkout dev``
drop op-logs tracked on origin/dev but absent locally, then a sync committed
their deletion (INV-lovih / the WI-vojik/WI-durub loss).

``_pr_landed_in_base SHA BASE`` is the git-ground-truth fallback: it fetches
``origin/BASE`` and returns 0 iff ``SHA`` is an ancestor of it — i.e. the PR's
commit is in the base branch regardless of what the merge API reported. When it
confirms the merge, ``do_merge`` returns success and ``cleanup_local`` runs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FORGEJO_LIB = REPO_ROOT / "scripts" / "lib" / "forgejo-api.sh"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True, timeout=15,
    )


def _setup_repo_with_origin(tmp_path: Path) -> tuple[Path, str]:
    """Create a repo with a bare ``origin`` whose ``dev`` branch has one commit.

    Returns (repo_path, base_branch_name).
    """
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", str(origin)],
        check=True, capture_output=True, text=True,
    )
    repo = tmp_path / "repo"
    subprocess.run(
        ["git", "init", "-q", str(repo)],
        check=True, capture_output=True, text=True,
    )
    _git(repo, "config", "user.email", "a@b.c")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "remote", "add", "origin", str(origin))
    (repo / "f.txt").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "push", "-q", "origin", "HEAD:dev")
    return repo, "dev"


def _run_helper(repo: Path, sha: str, base: str) -> subprocess.CompletedProcess[str]:
    """Invoke ``_pr_landed_in_base`` via a sourced sub-shell, cwd = repo."""
    return subprocess.run(
        [
            "bash",
            "-c",
            (
                f"source '{FORGEJO_LIB}' >/dev/null 2>&1; "
                f"cd '{repo}'; _pr_landed_in_base '{sha}' '{base}'"
            ),
        ],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_helper_is_sourceable(tmp_path: Path) -> None:
    """The helper must exist and be callable after sourcing the lib."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source '{FORGEJO_LIB}' >/dev/null 2>&1; "
            "declare -F _pr_landed_in_base > /dev/null && echo OK",
        ],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_returns_true_when_sha_is_in_origin_base(tmp_path: Path) -> None:
    """The transient-405 case: the merge landed (the commit is in origin/dev),
    even though the API never cleanly confirmed it — the helper must return 0."""
    repo, base = _setup_repo_with_origin(tmp_path)
    _git(repo, "checkout", "-qb", "feature")
    (repo / "f.txt").write_text("feat\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "feat")
    feature_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    # Simulate the PR landing server-side: ff origin/dev to include the commit.
    _git(repo, "push", "-q", "origin", "HEAD:dev")

    result = _run_helper(repo, feature_sha, base)
    assert result.returncode == 0, (
        f"sha is in origin/{base}; helper should confirm the merge. {result.stderr}"
    )


def test_returns_false_when_sha_absent_from_origin_base(tmp_path: Path) -> None:
    """A genuinely-unmerged commit (never pushed to origin/dev) must NOT be
    reported as merged — otherwise auto-pr would falsely claim success."""
    repo, base = _setup_repo_with_origin(tmp_path)
    _git(repo, "checkout", "-qb", "orphan")
    (repo / "f.txt").write_text("orphan\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "orphan")
    orphan_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    # Do NOT push to origin/dev.

    result = _run_helper(repo, orphan_sha, base)
    assert result.returncode != 0, (
        "sha is absent from origin/dev; helper must not claim it merged"
    )
