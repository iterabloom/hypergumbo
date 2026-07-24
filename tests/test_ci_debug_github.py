# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tier-B behavioral tests for the GitHub arm of ``scripts/ci-debug`` (PR-C2).

Under GitHub + self-hosted Woodpecker, GitHub Actions is disabled, so
``/actions/runs`` returns an empty ``workflow_runs`` (HTTP 200) — the ``runs``
subcommand would silently print nothing. The only agent-readable CI signal is
the Woodpecker commit-STATUS at ``/commits/{sha}/status`` (context
``ci/woodpecker/pr/woodpecker``). The github arm degrades ``runs`` to render
those status entries as pseudo-runs. ``status`` / ``pr-status`` / ``logs`` are
already shape-compatible (``logs`` degrades inside the lib's
``_github_fetch_job_log``); those tests are regression guards.

Dormant (default forgejo while Codeberg is origin), forced here via
``HYPERGUMBO_FORGE_BACKEND=github``. Bash contributes no Python coverage.
"""

from __future__ import annotations

import json

from _forge_github_harness import bindir_with_fakes, calls, fake_repo, run_script

_GH = {"HYPERGUMBO_FORGE_BACKEND": "github"}

_STATUS_OK = json.dumps({
    "state": "success",
    "statuses": [
        {"state": "success", "context": "ci/woodpecker/pr/woodpecker",
         "target_url": "https://ci.example.test/build/9"},
    ],
})
_STATUS_EMPTY = json.dumps({"state": "", "statuses": [], "total_count": 0})


def _urls(logs):
    return [c["url"] or "" for c in calls(logs["curl"])]


class TestRunsGitHub:
    def test_runs_renders_commit_status_as_pseudo_runs(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = bindir_with_fakes(tmp_path)
        r, logs = run_script(
            "ci-debug", repo, ("runs",),
            fixtures=[{"match": "/commits/", "code": 200, "body": _STATUS_OK}],
            env=_GH, bindir=bindir,
        )
        assert r.returncode == 0
        assert "ci/woodpecker/pr/woodpecker" in r.stdout
        assert "success" in r.stdout
        urls = _urls(logs)
        assert any("/commits/" in u and "/status" in u for u in urls)
        # GitHub Actions is disabled — the arm must NOT hit /actions/runs.
        assert not any("/actions/runs" in u for u in urls)

    def test_runs_empty_status_is_graceful(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = bindir_with_fakes(tmp_path)
        r, _ = run_script(
            "ci-debug", repo, ("runs",),
            fixtures=[{"match": "/commits/", "code": 200, "body": _STATUS_EMPTY}],
            env=_GH, bindir=bindir,
        )
        assert r.returncode == 0
        assert "No CI status reported" in r.stdout


class TestRunsForgejoUnchanged:
    def test_forgejo_runs_hits_actions_runs(self, tmp_path):
        repo = fake_repo(tmp_path, "https://codeberg.org/o/r.git")
        bindir = bindir_with_fakes(tmp_path)
        body = json.dumps({"workflow_runs": [
            {"run_number": 5, "status": "success",
             "head_sha": "abcdef1234567890", "display_title": "CI"},
        ]})
        r, logs = run_script(
            "ci-debug", repo, ("runs",),
            fixtures=[{"match": "/actions/runs", "code": 200, "body": body}],
            bindir=bindir,
        )
        assert r.returncode == 0
        assert any("/actions/runs?limit=300" in u for u in _urls(logs))
        assert "#5" in r.stdout


class TestStatusShapeCompat:
    def test_status_github_renders_overall(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = bindir_with_fakes(tmp_path)
        r, _ = run_script(
            "ci-debug", repo, ("status",),
            fixtures=[{"match": "/commits/", "code": 200, "body": _STATUS_OK}],
            env=_GH, bindir=bindir,
        )
        assert r.returncode == 0
        assert "Overall: success" in r.stdout
        assert "ci/woodpecker/pr/woodpecker" in r.stdout

    def test_pr_status_github_renders(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = bindir_with_fakes(tmp_path)
        pr = json.dumps({
            "state": "open", "merged": False, "title": "My PR",
            "head": {"sha": "abc1234567", "ref": "feature"},
            "base": {"ref": "dev"},
        })
        r, _ = run_script(
            "ci-debug", repo, ("pr-status", "42"),
            fixtures=[
                {"match": "/pulls/42", "code": 200, "body": pr},
                {"match": "/commits/", "code": 200, "body": _STATUS_OK},
            ],
            env=_GH, bindir=bindir,
        )
        assert r.returncode == 0
        assert "My PR" in r.stdout
        assert "Overall: success" in r.stdout


class TestLogsDegradation:
    def test_logs_github_degrades_to_pointer(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = bindir_with_fakes(tmp_path)
        r, _ = run_script(
            "ci-debug", repo, ("logs",),
            fixtures=[{"match": "/commits/", "code": 200, "body": _STATUS_OK}],
            env=_GH, bindir=bindir,
        )
        assert "Could not retrieve log" in r.stdout
        assert "Cloudflare Access" in r.stderr
        assert "https://ci.example.test/build/9" in r.stderr
