# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tier-B behavioral tests for the GitHub arm of ``scripts/auto-pr`` (PR-C2).

The dormant github backend replaces the AGit ``refs/for`` push with a plain
branch push + ``create_pr`` (via ``_autopr_github_push_create``), branches the
WI-miriz PR-URL sentinel to ``/pull/N`` (via ``_autopr_pr_web_url``), reopens
(rather than AGit-re-pushes) on the timeout/hung recovery paths, plain
force-pushes the rebased head, and re-captures the true merged SHA (github
rebase-merge rewrites it). Merge itself dispatches to the lib's
``_github_do_merge`` (landed in PR-C).

Forced via ``HYPERGUMBO_FORGE_BACKEND=github``; the AGit push is bypassed with
the ``AUTO_PR_SIMULATE_GH_PUSH`` seam (parallel to the existing
``AUTO_PR_SIMULATE_DESYNC`` seam). The forgejo path stays byte-identical and is
covered by the existing ``test_autopr_*`` suites, which this PR must not
regress. Bash contributes no Python coverage.
"""

from __future__ import annotations

import json
import re
import subprocess

from _forge_github_harness import (
    REPO_ROOT,
    bindir_with_fakes,
    calls,
    fake_repo,
    run_script,
)

AUTO_PR = REPO_ROOT / "scripts" / "auto-pr"

_BASE_ENV = {
    "FORGEJO_USER": "u",
    "AUTO_PR_SKIP_MANIFEST": "1",
    "HYPERGUMBO_FORGE_BACKEND": "github",
}


class TestDoPrGitHubPush:
    def test_push_create_identifies_pr_no_vpr(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/test/repo.git", branch="feature")
        bindir = bindir_with_fakes(tmp_path)
        r, logs = run_script(
            AUTO_PR, repo, ("--title", "fix: x", "--description", "y"),
            fixtures=[
                {"match": "POST", "code": 201, "body": json.dumps({"number": 7777})},
                {"match": "GET", "code": 200, "body": "[]"},
            ],
            env=dict(_BASE_ENV, AUTO_PR_SIMULATE_GH_PUSH="ok"),
            bindir=bindir,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "github push identified PR #7777" in r.stdout
        assert not (repo / ".git" / "PR_QUEUE").exists()
        posts = [c for c in calls(logs["curl"]) if c["method"] == "POST"]
        assert posts and "api.github.com/repos/test/repo/pulls" in posts[0]["url"]

    def test_push_failure_queues_vpr(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/test/repo.git", branch="feature")
        bindir = bindir_with_fakes(tmp_path)
        r, _ = run_script(
            AUTO_PR, repo, ("--title", "fix: x", "--description", "y"),
            fixtures=[{"match": "GET", "code": 200, "body": "[]"}],
            env=dict(_BASE_ENV, AUTO_PR_SIMULATE_GH_PUSH="fail"),
            bindir=bindir,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert (repo / ".git" / "PR_QUEUE").exists()


class TestFlushQueueGitHubPush:
    def test_flush_identifies_pr_no_parse_abort(self, tmp_path):
        # Regression for the set -o pipefail abort: on github, flush's
        # identification consumed parse_pr_from_push("") — which returns
        # non-zero on no match — killing the script. The github arm must
        # consume FALLBACK_PR_NUM instead.
        repo = fake_repo(tmp_path, "https://github.com/test/repo.git", branch="feature")
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
        ).stdout.strip()
        queue = {
            "vpr": 1, "branch": "feature", "base": "dev",
            "title": "t: x", "desc": "d", "sha": head,
            "ts": "2026-01-01T00:00:00+00:00",
        }
        (repo / ".git" / "PR_QUEUE").write_text(json.dumps(queue) + "\n")
        bindir = bindir_with_fakes(tmp_path)
        r, _ = run_script(
            AUTO_PR, repo, ("flush",),
            fixtures=[
                {"match": "POST", "code": 201, "body": json.dumps({"number": 55})},
                {"match": "GET", "code": 200, "body": "[]"},
            ],
            env=dict(_BASE_ENV, AUTO_PR_SIMULATE_GH_PUSH="ok"),
            bindir=bindir,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        # Reaching this line means we got PAST the identification block.
        assert "Created PR #55 (contains 1 vPRs)" in r.stdout
        assert "flush identified PR #55" in r.stdout


def _extract_func(name: str, path=None) -> str:
    text = (path or AUTO_PR).read_text()
    m = re.search(rf"^{name}\(\) \{{.*?^\}}", text, re.M | re.S)
    assert m, f"function {name} not found in {path or AUTO_PR}"
    return m.group(0)


def _pr_web_url(*, backend="forgejo") -> str:
    # Extracted from the LIB, not from auto-pr: the helper moved to
    # scripts/lib/forgejo-api.sh so merge-pr stops printing a hardcoded
    # codeberg.org link on a GitHub remote (one fact, two homes — the wrong
    # home won). API_BASE is supplied because the non-GitHub branch now
    # derives its host from it instead of naming codeberg.org.
    fn = _extract_func("pr_web_url", path=REPO_ROOT / "scripts/lib/forgejo-api.sh")
    pre = "REPO_SLUG=o/r\n"
    pre += f"FORGE_BACKEND={backend}\n"
    pre += "API_BASE=https://codeberg.org/api/v1/repos/o/r\n"
    r = subprocess.run(
        ["bash", "-c", fn + "\n" + pre + 'pr_web_url 5\n'],
        capture_output=True, text=True,
    )
    return r.stdout.strip()


class TestPrWebUrl:
    def test_github_uses_pull_singular(self):
        assert _pr_web_url(backend="github") == "https://github.com/o/r/pull/5"

    def test_forgejo_uses_pulls_plural(self):
        assert _pr_web_url(backend="forgejo") == "https://codeberg.org/o/r/pulls/5"


class TestStructuralGates:
    """Guard the dormant github gates against accidental revert (bash has no
    coverage gate; these lock the 5 push sites + sentinel/URL branches)."""

    def test_helpers_present(self):
        text = AUTO_PR.read_text()
        assert "_autopr_github_push_create() {" in text
        # ``pr_web_url`` now lives in the SHARED lib, not here. It had two
        # homes — a correct private copy in auto-pr and a hardcoded
        # ``codeberg.org/.../pulls/N`` string in merge-pr — and the wrong one
        # won for anyone running ``merge-pr`` against a GitHub remote.
        assert "_autopr_pr_web_url" not in text, (
            "the private copy is back; it belongs in scripts/lib/forgejo-api.sh"
        )
        lib = (REPO_ROOT / "scripts" / "lib" / "forgejo-api.sh").read_text()
        assert "pr_web_url() {" in lib

    def test_no_script_hardcodes_a_forge_host_in_a_pr_link(self):
        """The defect this move exists to prevent, pinned executably.

        A display URL must be DERIVED from the detected backend. Hardcoding a
        host produces a link that is wrong in both host and path segment
        (GitHub is /pull/N, Forgejo /pulls/N) on a repo whose API calls were
        already routing correctly — the failure is silent because nothing
        checks a printed link.
        """
        for name in ("auto-pr", "merge-pr"):
            text = (REPO_ROOT / "scripts" / name).read_text()
            assert "codeberg.org/$REPO_SLUG" not in text, (
                f"scripts/{name} hardcodes a forge host in a PR link; "
                f"call pr_web_url instead"
            )

    def test_do_pr_and_flush_gate_github_push(self):
        text = AUTO_PR.read_text()
        # Both primary pushes route through the github helper.
        assert text.count('_autopr_github_push_create "$push_remote"') == 2

    def test_recovery_and_force_push_gated(self):
        text = AUTO_PR.read_text()
        # Recovery paths reopen on github rather than AGit re-push.
        assert text.count("reopen the just-closed PR") == 2
        # Rebase force-push is backend-aware.
        assert "push origin \"HEAD:refs/heads/$BRANCH\" --force-with-lease" in text

    def test_merged_sha_recaptured_for_github(self):
        text = AUTO_PR.read_text()
        assert 'GitHub rebase-merge rewrites the commit SHA' in text
        # Both do_pr AND flush_queue recapture the merged SHA (not LOCAL_SHA/tip_sha).
        assert text.count('_autopr_state_merged_sha="$_merged_sha"') == 2
