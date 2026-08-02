# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tier-B tests for ``resolve_forge_token``'s GitHub token source (PR-C2).

PR-C landed ``resolve_forge_token`` but not github-aware: for the
github backend it fell through to ``FORGEJO_TOKEN``, which won't authenticate
against GitHub. PR-C2 closes that: github reads a dedicated local PAT
(``HG_GITHUB_TOKEN``, provisioned/documented in PR-D), falling back to
``FORGEJO_TOKEN`` so an unset env stays dormant-safe. Forgejo path stays
byte-identical.

WI-hajif removed the third arm: under the retired CI failover the self-hosted
Forgejo token used to take precedence over both. There is no failover any more,
so that precedence — and its test — are gone.
"""

from __future__ import annotations

from _forge_github_harness import fake_repo, run_lib

_SNIPPET = (
    "resolve_forge_token\n"
    'echo "TOKEN=[$FORGE_TOKEN]"\n'
)


def _resolve(repo, *, backend, env=None):
    pre = f"FORGE_BACKEND={backend}\n"
    r, _ = run_lib(repo, pre + _SNIPPET, env=env)
    return r


def test_github_prefers_hg_github_token(tmp_path):
    repo = fake_repo(tmp_path, "https://github.com/o/r.git")
    r = _resolve(repo, backend="github", env={"HG_GITHUB_TOKEN": "ghp_x"})
    assert "TOKEN=[ghp_x]" in r.stdout


def test_github_falls_back_to_forgejo_token_when_pat_unset(tmp_path):
    repo = fake_repo(tmp_path, "https://github.com/o/r.git")
    # Harness sets FORGEJO_TOKEN=tok and pops HG_GITHUB_TOKEN.
    r = _resolve(repo, backend="github")
    assert "TOKEN=[tok]" in r.stdout


def test_forgejo_uses_forgejo_token(tmp_path):
    repo = fake_repo(tmp_path, "https://codeberg.org/o/r.git")
    r = _resolve(repo, backend="forgejo")
    assert "TOKEN=[tok]" in r.stdout


