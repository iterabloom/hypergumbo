# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tier-B behavioral tests for auto-pr's vPR reconciliation (WI-buniz).

Three failure modes, all observed live 2026-08-01 while landing PR #144:

1. A push rejected NON-FAST-FORWARD (the remote branch already held the
   change from auto-pr's own hung-run re-push cycle) was classified as
   "remote unavailable" and queued a duplicate vPR while the real PR sat
   open on the remote.
2. ``prune`` judged staleness by sha-ancestry only. A flush REBUILDS
   commits, so a merged change's queue entry carries a sha that never
   appears in the base branch — measured: the queued commit differed from
   the merged one ONLY in ``.ci/affected-tests.txt``'s timestamp header,
   and prune called it "live" forever.
3. A queue entry whose sha/branch became unresolvable (merged branch
   deleted) made ``flush`` die with git's raw ``Needed a single
   revision`` and made ``prune`` call the entry live.

Bash contributes no Python coverage (Tier B); these drive the real script.
"""

from __future__ import annotations

import json
import subprocess

from _forge_github_harness import (
    REPO_ROOT,
    bindir_with_fakes,
    fake_repo,
    run_script,
)

AUTO_PR = REPO_ROOT / "scripts" / "auto-pr"

_BASE_ENV = {
    "FORGEJO_USER": "u",
    "AUTO_PR_SKIP_MANIFEST": "1",
    "HYPERGUMBO_FORGE_BACKEND": "github",
}


def _git(repo, *a):
    return subprocess.run(
        ["git", *a], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


class TestPushRejectionClassifier:
    """Rejection is not unavailability: only network/auth failures queue."""

    def test_rejected_diverged_does_not_queue_a_vpr(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/test/repo.git", branch="feature")
        bindir = bindir_with_fakes(tmp_path)
        r, _logs = run_script(
            AUTO_PR, repo, ("--title", "fix: x", "--description", "y"),
            fixtures=[{"match": "GET", "code": 200, "body": "[]"}],
            env=dict(_BASE_ENV, AUTO_PR_SIMULATE_GH_PUSH="reject-diverged"),
            bindir=bindir,
        )
        assert r.returncode != 0, r.stdout + r.stderr
        assert "remote unavailable" not in r.stdout, (
            "a non-fast-forward rejection is not an offline condition:\n"
            + r.stdout
        )
        assert "NOT queueing" in r.stdout, r.stdout
        assert not (repo / ".git" / "PR_QUEUE").exists(), (
            "queued a duplicate vPR for a branch that exists on the remote"
        )

    def test_rejected_but_remote_already_at_local_sha_proceeds(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/test/repo.git", branch="feature")
        bindir = bindir_with_fakes(tmp_path)
        r, _logs = run_script(
            AUTO_PR, repo, ("--title", "fix: x", "--description", "y"),
            fixtures=[
                {"match": "POST", "code": 201, "body": json.dumps({"number": 7878})},
                {"match": "GET", "code": 200, "body": "[]"},
            ],
            env=dict(_BASE_ENV, AUTO_PR_SIMULATE_GH_PUSH="reject-same"),
            bindir=bindir,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "github push identified PR #7878" in r.stdout
        assert not (repo / ".git" / "PR_QUEUE").exists()


def _queue_entry(repo, sha, vpr=1, branch="q-branch", base="dev"):
    entry = {
        "vpr": vpr, "branch": branch, "base": base,
        "title": "t", "desc": "d", "sha": sha,
        "ts": "2026-08-01T00:00:00-04:00",
    }
    (repo / ".git" / "PR_QUEUE").write_text(json.dumps(entry) + "\n")


class TestPruneStaleness:
    def _repo_with_squash_merged_content(self, tmp_path):
        """Queue sha whose tree differs from origin/dev ONLY in the
        regenerated-every-run manifest — the measured PR #144 shape."""
        repo = fake_repo(tmp_path, "https://github.com/test/repo.git")
        (repo / ".ci").mkdir(exist_ok=True)
        (repo / "src.py").write_text("v1\n")
        (repo / ".ci" / "affected-tests.txt").write_text("# at t0\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base")

        # The queued commit: substantive change + manifest at t1.
        _git(repo, "checkout", "-q", "-b", "q-branch")
        (repo / "src.py").write_text("v2\n")
        (repo / ".ci" / "affected-tests.txt").write_text("# at t1\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "feat: x")
        queued_sha = _git(repo, "rev-parse", "HEAD")

        # dev gets the SAME substantive change, manifest at t2 (the
        # rebase-merge/flush-rebuild shape: different sha, different
        # patch-id, identical modulo the manifest).
        _git(repo, "checkout", "-q", "dev")
        (repo / "src.py").write_text("v2\n")
        (repo / ".ci" / "affected-tests.txt").write_text("# at t2\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "feat: x (merged)")
        _git(repo, "update-ref", "refs/remotes/origin/dev", "HEAD")
        return repo, queued_sha

    def test_prune_drops_manifest_noise_equivalent_entry(self, tmp_path):
        repo, sha = self._repo_with_squash_merged_content(tmp_path)
        _queue_entry(repo, sha)
        r, _ = run_script(AUTO_PR, repo, ("prune",), env=dict(_BASE_ENV))
        assert r.returncode == 0, r.stdout + r.stderr
        assert not (repo / ".git" / "PR_QUEUE").exists(), (
            "entry whose content is merged (modulo the timestamped manifest) "
            "must prune:\n" + r.stdout
        )

    def test_prune_keeps_genuinely_unmerged_entry(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/test/repo.git")
        (repo / "src.py").write_text("v1\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base")
        _git(repo, "update-ref", "refs/remotes/origin/dev", "HEAD")
        _git(repo, "checkout", "-q", "-b", "q-branch")
        (repo / "src.py").write_text("UNMERGED\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "feat: unmerged")
        _queue_entry(repo, _git(repo, "rev-parse", "HEAD"))
        r, _ = run_script(AUTO_PR, repo, ("prune",), env=dict(_BASE_ENV))
        assert r.returncode == 0, r.stdout + r.stderr
        assert (repo / ".git" / "PR_QUEUE").exists(), (
            "genuinely unmerged content must stay queued:\n" + r.stdout
        )

    def test_prune_drops_unresolvable_sha_with_warning(self, tmp_path):
        """The deleted-branch shape: the sha is gone; prune must drop the
        entry loudly instead of calling it live (and leaving flush to die
        on 'Needed a single revision')."""
        repo = fake_repo(tmp_path, "https://github.com/test/repo.git")
        (repo / "f").write_text("x")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base")
        _git(repo, "update-ref", "refs/remotes/origin/dev", "HEAD")
        _queue_entry(repo, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")
        r, _ = run_script(AUTO_PR, repo, ("prune",), env=dict(_BASE_ENV))
        assert r.returncode == 0, r.stdout + r.stderr
        assert not (repo / ".git" / "PR_QUEUE").exists(), r.stdout
        assert "unresolvable" in (r.stdout + r.stderr).lower(), (
            "dropping unverifiable content must be LOUD:\n" + r.stdout
        )
