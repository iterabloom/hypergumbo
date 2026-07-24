# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression guard: ``scripts/verify-tracker-pr`` is GitHub shape-compatible.

The PR-C2 scope concluded ``change_needed=false`` for this script — GitHub's
``/pulls/{n}``, ``/pulls/{n}/files``, and ``/contents`` responses match Gitea's
in every field this verifier reads, and the api_base is already resolved to
api.github.com by the lib. These tests lock that verdict in so a future edit
can't silently break the github path. Notably they use a *line-wrapped* base64
body (GitHub wraps ``/contents`` at MIME width) to prove the L95
``base64.b64decode`` tolerates the wrapping.

Forced github via ``HYPERGUMBO_FORGE_BACKEND=github``; no github-specific code
exists in the script. Bash contributes no Python coverage.
"""

from __future__ import annotations

import base64
import json

from _forge_github_harness import bindir_with_fakes, fake_repo, run_script

_GH = {"HYPERGUMBO_FORGE_BACKEND": "github"}

# 5 lines → base64.encodebytes wraps across multiple MIME lines (proves the
# decode tolerates GitHub's line-wrapped /contents body).
_PR_LINES = [f"op-line-{i}" for i in range(1, 6)]
_PR_TEXT = "".join(ln + "\n" for ln in _PR_LINES)
_CONTENT_B64 = base64.encodebytes(_PR_TEXT.encode()).decode()

_META = json.dumps({"title": "tracker sync", "state": "open",
                    "head": {"sha": "abcdef1234567890"}})
_FILES = json.dumps([{"filename": "f.ops", "status": "modified"}])
_CONTENTS = json.dumps({"content": _CONTENT_B64, "encoding": "base64"})

# Order matters: the fake curl matches the first fixture whose `match` is a
# substring of "<METHOD> <url>", so the more-specific /pulls/5/files precedes
# /pulls/5.
_FIXTURES = [
    {"match": "/pulls/5/files", "code": 200, "body": _FILES},
    {"match": "/pulls/5", "code": 200, "body": _META},
    {"match": "/contents/", "code": 200, "body": _CONTENTS},
]


def test_local_superset_is_safe_to_close(tmp_path):
    repo = fake_repo(tmp_path, "https://github.com/o/r.git")
    bindir = bindir_with_fakes(tmp_path)
    # Local file is a strict superset of the PR content.
    (repo / "f.ops").write_text(_PR_TEXT + "extra-local-line\n")
    r, _ = run_script("verify-tracker-pr", repo, ("5",),
                      fixtures=_FIXTURES, env=_GH, bindir=bindir)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "✅ OK" in r.stdout
    assert "Safe to close" in r.stdout


def test_local_missing_line_is_data_loss(tmp_path):
    repo = fake_repo(tmp_path, "https://github.com/o/r.git")
    bindir = bindir_with_fakes(tmp_path)
    # Local is missing the last PR line → data-loss risk.
    (repo / "f.ops").write_text("".join(ln + "\n" for ln in _PR_LINES[:-1]))
    r, _ = run_script("verify-tracker-pr", repo, ("5",),
                      fixtures=_FIXTURES, env=_GH, bindir=bindir)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "❌ LOSS" in r.stdout
    assert "NOT safe to close" in r.stdout
