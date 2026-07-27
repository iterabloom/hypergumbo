# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tier-B behavioral tests for the GitHub arm of ``scripts/contribute`` (PR-C2).

``contribute`` is the external-contributor fork→PR path, keyed on the
``upstream`` remote (not origin). Today its create block is *dead*: it never
resolves a backend, hardcodes codeberg, and crashes at ``_saved_api_base="$API_BASE"``
under ``set -u`` (API_BASE never set). PR-C2 makes it dual-mode: detect the
backend from the upstream host, use ``gh pr create`` for github (auto-fork —
the one place gh earns its keep), keep ``api_post`` for forgejo, fix the
``set -u`` crash, and generalize the codeberg-hardcoded URLs.

``gh`` is absent in CI/sandbox, so the gh arm is exercised with a fake ``gh`` on
PATH and the fallback with none. Bash contributes no Python coverage.
"""

from __future__ import annotations

import json

from _forge_github_harness import (
    bindir_with_fakes,
    calls,
    fake_fork_repo,
    run_script,
)

# FORGEJO_USER deliberately DIFFERS from the fork owner ("myuser", the
# fake_fork_repo default) so the head-ref assertions prove the head derives from
# FORK_SLUG (FORK_OWNER) and not from $USER/FORGEJO_USER. harness sets
# FORGEJO_TOKEN=tok.
_ENV = {"FORGEJO_USER": "botuser"}


def _val_after(argv, flag):
    return argv[argv.index(flag) + 1] if flag in argv else None

_GH_UP = "https://github.com/up/r.git"
_CB_UP = "https://codeberg.org/up/r.git"


def _gh_calls(logs):
    return calls(logs["gh"])


class TestGitHubGhArm:
    def test_gh_pr_create_invoked_with_cross_fork_head(self, tmp_path):
        repo = fake_fork_repo(tmp_path, upstream=_GH_UP)
        bindir = bindir_with_fakes(tmp_path, gh=True)
        r, logs = run_script(
            "contribute", repo,
            env=dict(_ENV, GH_PR_CREATE_URL="https://github.com/up/r/pull/42"),
            bindir=bindir,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "https://github.com/up/r/pull/42" in r.stdout
        assert "codeberg.org" not in r.stdout
        create = [a for a in _gh_calls(logs) if a[:2] == ["pr", "create"]]
        assert create, _gh_calls(logs)
        argv = create[0]
        assert "--repo" in argv and "up/r" in argv
        assert "--base" in argv and "dev" in argv
        # FORK_OWNER (myuser), NOT FORGEJO_USER (botuser).
        assert "myuser:feature" in argv
        # gh pr list must use the BARE branch for --head (gh maps it to the
        # bare headRefName) and select by head repo owner in the JSON.
        lst = [a for a in _gh_calls(logs) if a[:2] == ["pr", "list"]]
        assert lst, _gh_calls(logs)
        largv = lst[0]
        assert _val_after(largv, "--head") == "feature"
        assert any("headRepositoryOwner" in a for a in largv)

    def test_gh_existing_pr_short_circuits(self, tmp_path):
        repo = fake_fork_repo(tmp_path, upstream=_GH_UP)
        bindir = bindir_with_fakes(tmp_path, gh=True)
        r, logs = run_script(
            "contribute", repo,
            env=dict(_ENV, GH_PR_LIST="7"),
            bindir=bindir,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "already exists" in r.stdout
        assert "up/r/pull/7" in r.stdout
        subs = [a[:2] for a in _gh_calls(logs)]
        assert ["pr", "list"] in subs
        assert ["pr", "create"] not in subs

    def test_gh_create_failure_prints_compare_url(self, tmp_path):
        repo = fake_fork_repo(tmp_path, upstream=_GH_UP)
        bindir = bindir_with_fakes(tmp_path, gh=True)
        r, _ = run_script(
            "contribute", repo,
            env=dict(_ENV, GH_EXIT="1"),
            bindir=bindir,
        )
        assert r.returncode == 1
        assert "Failed to create PR" in r.stdout
        assert "compare/dev...myuser:feature" in r.stdout


class TestForgejoCurlArm:
    def test_forgejo_create_uses_codeberg_host_and_pulls_path(self, tmp_path):
        repo = fake_fork_repo(tmp_path, upstream=_CB_UP)
        bindir = bindir_with_fakes(tmp_path)  # no gh
        r, logs = run_script(
            "contribute", repo,
            fixtures=[
                {"match": "pulls?state=open", "code": 200, "body": "[]"},
                {"match": "POST", "code": 201, "body": json.dumps({"number": 5})},
            ],
            env=_ENV, bindir=bindir,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        # set -u crash fix: the create block runs to completion.
        assert "unbound variable" not in r.stderr
        assert "Created PR #5" in r.stdout
        assert "https://codeberg.org/up/r/pulls/5" in r.stdout
        posts = [c for c in calls(logs["curl"]) if c["method"] == "POST"]
        assert posts and "codeberg.org/api/v1/repos/up/r/pulls" in posts[0]["url"]
        # PR head uses FORK_OWNER (myuser), not FORGEJO_USER (botuser).
        assert '"myuser:feature"' in (posts[0]["data"] or "")

    def test_forgejo_existing_pr_short_circuits(self, tmp_path):
        repo = fake_fork_repo(tmp_path, upstream=_CB_UP)
        bindir = bindir_with_fakes(tmp_path)
        existing = [{
            "number": 9,
            "head": {"repo": {"full_name": "myuser/hypergumbo"}, "ref": "feature"},
        }]
        r, logs = run_script(
            "contribute", repo,
            fixtures=[{"match": "pulls?state=open", "code": 200,
                       "body": json.dumps(existing)}],
            env=_ENV, bindir=bindir,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "already exists" in r.stdout
        assert "https://codeberg.org/up/r/pulls/9" in r.stdout
        assert not [c for c in calls(logs["curl"]) if c["method"] == "POST"]


class TestGitHubFallbackWhenGhAbsent:
    def test_github_without_gh_falls_back_to_api_post(self, tmp_path):
        repo = fake_fork_repo(tmp_path, upstream=_GH_UP)
        bindir = bindir_with_fakes(tmp_path)  # no gh
        r, logs = run_script(
            "contribute", repo,
            fixtures=[
                {"match": "pulls?state=open", "code": 200, "body": "[]"},
                {"match": "POST", "code": 201, "body": json.dumps({"number": 8})},
            ],
            env=_ENV, bindir=bindir,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "https://github.com/up/r/pull/8" in r.stdout
        posts = [c for c in calls(logs["curl"]) if c["method"] == "POST"]
        assert posts and "api.github.com/repos/up/r/pulls" in posts[0]["url"]
