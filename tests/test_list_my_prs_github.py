# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tier-B behavioral tests for the GitHub arm of ``scripts/list-my-prs`` (PR-C2).

``list-my-prs`` has two backend-divergent sites: the pulls-list query string
(Forgejo ``sort=recentupdate&limit=50`` vs GitHub
``sort=updated&direction=desc&per_page=100``) and the merged marker (Forgejo's
list objects carry a ``merged`` bool; GitHub's carry ``merged_at``). The
github arm is dormant (default forgejo while Codeberg is origin) and forced
here via ``HYPERGUMBO_FORGE_BACKEND=github``. The Forgejo path must stay
byte-identical. Bash contributes no Python coverage (Tier B).
"""

from __future__ import annotations

import json

from _forge_github_harness import bindir_with_fakes, calls, fake_repo, run_script

_GH = {"HYPERGUMBO_FORGE_BACKEND": "github"}


def _pulls_url(logs: dict) -> str:
    for c in calls(logs["curl"]):
        if c["url"] and "/pulls" in c["url"]:
            return c["url"]
    return ""


class TestQueryParams:
    def test_github_uses_github_pagination_params(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = bindir_with_fakes(tmp_path)
        _, logs = run_script(
            "list-my-prs", repo,
            fixtures=[{"match": "/pulls", "code": 200, "body": "[]"}],
            env=_GH, bindir=bindir,
        )
        url = _pulls_url(logs)
        assert "sort=updated" in url
        assert "direction=desc" in url
        assert "per_page=100" in url
        assert "recentupdate" not in url
        assert "limit=50" not in url

    def test_github_closed_adds_state(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = bindir_with_fakes(tmp_path)
        _, logs = run_script(
            "list-my-prs", repo, ("--closed",),
            fixtures=[{"match": "/pulls", "code": 200, "body": "[]"}],
            env=_GH, bindir=bindir,
        )
        url = _pulls_url(logs)
        assert "state=closed" in url

    def test_forgejo_query_byte_identical(self, tmp_path):
        repo = fake_repo(tmp_path, "https://codeberg.org/o/r.git")
        bindir = bindir_with_fakes(tmp_path)
        _, logs = run_script(
            "list-my-prs", repo,
            fixtures=[{"match": "/pulls", "code": 200, "body": "[]"}],
            bindir=bindir,
        )
        url = _pulls_url(logs)
        assert "sort=recentupdate&limit=50" in url
        assert "per_page" not in url


class TestMergedMarker:
    _PR = {
        "number": 42, "title": "T", "state": "closed",
        "head": {"ref": "b"}, "base": {"ref": "dev"},
        "user": {"login": "u"}, "updated_at": "2026-07-01",
    }

    def test_github_merged_at_renders_merged(self, tmp_path):
        # GitHub list objects carry merged_at (no 'merged' bool).
        pr = dict(self._PR, merged_at="2026-07-01T00:00:00Z")
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = bindir_with_fakes(tmp_path)
        r, _ = run_script(
            "list-my-prs", repo,
            fixtures=[{"match": "/pulls", "code": 200, "body": json.dumps([pr])}],
            env=_GH, bindir=bindir,
        )
        assert "🟣" in r.stdout
        assert "merged" in r.stdout

    def test_forgejo_merged_bool_renders_merged(self, tmp_path):
        pr = dict(self._PR, merged=True)
        repo = fake_repo(tmp_path, "https://codeberg.org/o/r.git")
        bindir = bindir_with_fakes(tmp_path)
        r, _ = run_script(
            "list-my-prs", repo,
            fixtures=[{"match": "/pulls", "code": 200, "body": json.dumps([pr])}],
            bindir=bindir,
        )
        assert "🟣" in r.stdout
        assert "merged" in r.stdout
