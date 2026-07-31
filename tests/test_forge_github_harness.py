# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests that the GitHub-arm harness actually isolates the tools it fakes.

``tests/_forge_github_harness.py`` stubs the network by putting fake ``curl``
and (optionally) ``gh`` binaries on ``PATH``. It used to **prepend** that
directory to the inherited ``PATH`` rather than replace it, which meant the
stub only intercepted tools it remembered to fake — anything else fell through
to the real binary.

Concretely: ``bindir_with_fakes(gh=False)`` places no fake ``gh``, but
``/usr/bin/gh`` was still resolvable, so ``scripts/contribute:240``'s
``command -v gh`` probe succeeded and the class literally named
``TestGitHubFallbackWhenGhAbsent`` ran with ``gh`` present. On a machine with
``gh`` installed the real binary called ``https://api.github.com/graphql`` and
the test failed with HTTP 401; on CI runners, which have no ``gh``, it passed
for a reason that had nothing to do with the code under test.

No PATH-*prepend* trick can fix this. A shim satisfies ``command -v``; a
directory or a dangling symlink named ``gh`` is skipped and the search
continues to the real one; and ``gh`` lives in ``/usr/bin`` here, so dropping
its directory would also drop ``git``, ``sed`` and ``awk``. The harness
therefore builds a **shadow directory** — one symlink per executable found on
the inherited ``PATH``, minus ``gh`` — and runs scripts with ``PATH`` set to
the fake bindir plus that shadow. The only ``gh`` reachable from a harnessed
script is a fake one the test asked for.

These tests pin the guarantee itself rather than any single caller, because
the failure mode is silent: an un-faked tool *works*, so nothing looks wrong.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import _forge_github_harness as H


def _probe(env: dict, cmd: str) -> bool:
    """True when ``cmd`` resolves on the PATH carried by ``env``."""
    return subprocess.run(
        ["bash", "-c", f"command -v {cmd} >/dev/null 2>&1"],
        env=env,
    ).returncode == 0


class TestGhIsGenuinelyAbsent:
    """``gh=False`` must mean ``gh`` cannot be found, not merely un-faked."""

    def test_real_gh_is_not_reachable(self, tmp_path: Path) -> None:
        bindir = H.bindir_with_fakes(tmp_path)  # gh=False
        env, _ = H._base_env(bindir, tmp_path, None)
        assert not _probe(env, "gh"), (
            "a real gh is reachable from the harness PATH, so any test "
            "asserting the no-gh fallback is measuring the wrong branch"
        )

    def test_the_fake_gh_is_reachable_when_requested(self, tmp_path: Path) -> None:
        """Positive control: the isolation must not break gh=True."""
        bindir = H.bindir_with_fakes(tmp_path, gh=True)
        env, _ = H._base_env(bindir, tmp_path, None)
        assert _probe(env, "gh")

    def test_ordinary_tools_still_resolve(self, tmp_path: Path) -> None:
        """Isolation must not cost the scripts their actual dependencies.

        scripts/contribute invokes all of these; losing one would turn a
        genuine assertion into a spurious failure.
        """
        bindir = H.bindir_with_fakes(tmp_path)
        env, _ = H._base_env(bindir, tmp_path, None)
        for tool in (
            "git", "sed", "awk", "grep", "head", "tail",
            "tr", "wc", "dirname", "python3",
        ):
            assert _probe(env, tool), f"{tool} unreachable under harness PATH"

    def test_git_actually_runs(self, tmp_path: Path) -> None:
        """`command -v` resolving is not the same as the binary working."""
        bindir = H.bindir_with_fakes(tmp_path)
        env, _ = H._base_env(bindir, tmp_path, None)
        r = subprocess.run(
            ["bash", "-c", "git --version"],
            env=env, capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr
        assert "git version" in r.stdout


class TestCredentialScrub:
    """The harness must not hand a live token to a subprocess."""

    def test_gh_native_token_vars_are_scrubbed(self, tmp_path: Path) -> None:
        """``gh`` reads GH_TOKEN / GITHUB_TOKEN — neither was being removed.

        The scrub list covered FORGEJO_TOKEN and HG_GITHUB_TOKEN, which ``gh``
        does not consult, and omitted the two it does.
        """
        import os
        bindir = H.bindir_with_fakes(tmp_path)
        marker = "sentinel-not-a-real-token"
        for var in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN"):
            os.environ[var] = marker
        try:
            env, _ = H._base_env(bindir, tmp_path, None)
            leaked = [v for v in ("GH_TOKEN", "GITHUB_TOKEN",
                                  "GH_ENTERPRISE_TOKEN") if v in env]
            assert not leaked, f"harness passes {leaked} through to subprocesses"
        finally:
            for var in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN"):
                os.environ.pop(var, None)
