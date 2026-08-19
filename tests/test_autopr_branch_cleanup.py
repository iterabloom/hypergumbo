# SPDX-License-Identifier: AGPL-3.0-or-later
"""``cleanup_local`` must actually delete the branch it says it cleaned up.

The failure is silent and total, not intermittent. ``auto-pr`` merges via
**rebase** (``scripts/lib/github-api.sh`` tries ``methods=(rebase merge)``,
rebase first), so the PR's commits land on the base under *new* SHAs and the
local branch tip is never an ancestor of the updated base. ``git branch -d``'s
safety check is exactly that ancestry test, so it refuses every time:

    error: the branch '<name>' is not fully merged.

Both that command and the ``git push --delete`` beside it were written
``2>/dev/null || true``, so the script printed "🧹 Cleaning up local branch..."
and reported nothing while deleting nothing. Seven merged branches accumulated
in a single day before a branch count surfaced it; an earlier session found 97
local branches and 436 remote-tracking refs from the same cause.

Two further facts shape the fix:

* The remote branch is already gone by this point —
  ``_github_delete_pr_head_branch`` deletes it via the API during the merge —
  so the ``git push --delete`` could only ever fail with "remote ref does not
  exist". What is actually left behind is the stale *remote-tracking* ref, and
  nothing prunes it (``git pull`` does not, and ``fetch.prune`` is unset).
* ``git cherry`` is the right safety check under rebase because it compares
  patch-ids rather than ancestry, so a rebased-and-merged commit registers as
  upstream. This is the same instrument the repo already prescribes in place of
  ``git branch --merged``.

The test drives the real function against a real repo with a real bare origin,
simulating exactly what GitHub does: land the patch on the base under a new
SHA, then delete the remote head branch.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AUTO_PR = REPO_ROOT / "scripts" / "auto-pr"


def _extract_cleanup_local() -> str:
    """Pull ``cleanup_local`` out of auto-pr; sourcing the script runs do_pr."""
    text = AUTO_PR.read_text(encoding="utf-8")
    m = re.search(r"^cleanup_local\(\) \{\n.*?^\}$", text, re.S | re.M)
    assert m, "cleanup_local() not found in scripts/auto-pr"
    return m.group(0)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True, timeout=20,
    )


def _build_rebase_merged_repo(tmp_path: Path) -> Path:
    """A clone whose branch was rebase-merged and remote-deleted upstream."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(origin)],
                   check=True, capture_output=True, timeout=20)
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", str(origin), str(repo)],
                   check=True, capture_output=True, timeout=20)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"),
                 ("commit.gpgsign", "false"), ("pull.rebase", "false")):
        _git(repo, "config", k, v)

    (repo / "base.txt").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    _git(repo, "push", "origin", "dev")

    # The feature branch, pushed as a PR head would be.
    _git(repo, "checkout", "-b", "feat/x")
    (repo / "feature.txt").write_text("feature\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feature work")
    # Pushed exactly as auto-pr pushes: an explicit refspec, which updates the
    # remote-tracking ref but sets NO upstream config. That matters — `git
    # branch -d` also accepts "merged into its upstream", so a test that used
    # `push -u` would let the broken code pass.
    _git(repo, "push", "origin", "HEAD:refs/heads/feat/x")
    feature_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    # What GitHub's rebase merge does: replay the patch onto dev as a NEW
    # commit, then delete the head branch server-side.
    server = tmp_path / "server-side"
    subprocess.run(["git", "clone", str(origin), str(server)],
                   check=True, capture_output=True, timeout=20)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"),
                 ("commit.gpgsign", "false")):
        _git(server, "config", k, v)
    _git(server, "checkout", "dev")
    # dev must advance first. Cherry-picking onto the SAME parent reproduces a
    # byte-identical commit object — same tree, message, author, timestamps —
    # so the "rebased" commit would keep feat/x's SHA and the branch would be a
    # plain ancestor of dev. That is a fast-forward, not a rebase, and `git
    # branch -d` accepts it. Advancing dev is what forces a new SHA, and it is
    # also what really happens: other PRs land between branch and merge.
    (server / "other.txt").write_text("another PR landed meanwhile\n")
    _git(server, "add", "-A")
    _git(server, "commit", "-m", "unrelated work from another PR")
    _git(server, "cherry-pick", feature_sha)
    _git(server, "push", "origin", "dev")
    _git(server, "push", "origin", "--delete", "feat/x")

    # Back on the branch, as auto-pr is when cleanup_local is called.
    _git(repo, "checkout", "feat/x")
    # Refresh before asserting: origin/dev in this clone is stale until fetched,
    # and comparing against a stale ref makes the check pass vacuously.
    _git(repo, "fetch", "origin")
    rebased = _git(repo, "rev-parse", "origin/dev").stdout.strip()
    assert rebased != feature_sha, "test must model a NEW sha, not a fast-forward"
    ancestor = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", "feat/x", "origin/dev"],
        capture_output=True, timeout=20,
    )
    assert ancestor.returncode != 0, (
        "feat/x is an ancestor of origin/dev, so this models a fast-forward and "
        "`git branch -d` would succeed for the wrong reason — the defect under "
        "test only appears when the merge rewrote the commits"
    )
    upstream = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "feat/x@{upstream}"],
        capture_output=True, text=True, timeout=20,
    )
    assert upstream.returncode != 0, (
        "the harness gave feat/x an upstream; auto-pr never does (it pushes a "
        "bare refspec), and `git branch -d` accepts merged-into-upstream, so an "
        "upstream here would mask the defect under test"
    )
    return repo


def _run_cleanup_local(repo: Path) -> subprocess.CompletedProcess[str]:
    script = (
        f"set -uo pipefail\n"
        f"REPO_ROOT='{repo}'\n"
        f"cd '{repo}'\n"
        f"{_extract_cleanup_local()}\n"
        f"cleanup_local 'feat/x' 'dev'\n"
    )
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=60,
        stdin=subprocess.DEVNULL,
    )


def test_rebase_merged_branch_is_deleted(tmp_path: Path) -> None:
    """The whole point: after cleanup, the local branch is gone."""
    repo = _build_rebase_merged_repo(tmp_path)
    _run_cleanup_local(repo)
    branches = _git(repo, "branch", "--format=%(refname:short)").stdout.split()
    assert "feat/x" not in branches, (
        "cleanup_local left the merged branch behind. Under a rebase merge the "
        "branch tip is not an ancestor of dev, so `git branch -d` refuses; the "
        "safety check has to be patch-equivalence (git cherry), not ancestry."
    )


def test_stale_remote_tracking_ref_is_pruned(tmp_path: Path) -> None:
    """The remote branch is already gone; what is left is the stale ref."""
    repo = _build_rebase_merged_repo(tmp_path)
    _run_cleanup_local(repo)
    remotes = _git(repo, "branch", "-r", "--format=%(refname:short)").stdout.split()
    assert "origin/feat/x" not in remotes, (
        "the stale remote-tracking ref survived. `git pull` does not prune and "
        "fetch.prune is unset, so these accumulate one per merge."
    )


def test_branch_with_unmerged_work_is_kept(tmp_path: Path) -> None:
    """The safety property `-d` was there for must survive the fix.

    A forced delete that skips the check would pass the two tests above and
    still be wrong, so this pins the other side: a commit that never landed
    upstream means the branch stays.
    """
    repo = _build_rebase_merged_repo(tmp_path)
    (repo / "unmerged.txt").write_text("never pushed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "work that never landed upstream")
    _run_cleanup_local(repo)
    branches = _git(repo, "branch", "--format=%(refname:short)").stdout.split()
    assert "feat/x" in branches, (
        "cleanup_local deleted a branch carrying a commit that is not upstream "
        "— that is data loss, not cleanup."
    )
