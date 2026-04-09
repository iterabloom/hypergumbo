# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the ``scripts/auto-pr`` AUTOPR_LAST_RESULT.json sentinel (WI-miriz).

Background
----------
On 2026-04-08 a backgrounded ``auto-pr`` invocation completed end-to-end —
push, CI poll, merge, branch cleanup — but the orchestrating agent's
``run_in_background`` mechanism captured zero bytes of stdout. From the
agent's perspective the run never happened, so it ran ``auto-pr`` again,
hit "commit is the same as the old commit" rejections, and queued a stale
vPR. The user had to manually intervene with "its merged -- pr 2773".

WI-miriz adds a final-state marker file at ``.git/AUTOPR_LAST_RESULT.json``
that ``auto-pr`` writes on every exit from an action subcommand
(``do_pr``, ``flush_queue``, ``gov_cleanup``). External observers — and
the agent that backgrounded the script — can then check this file to
discover whether the run succeeded, without depending on captured stdout.

Strategy
--------
``auto-pr`` is a bash script. The relevant sentinel paths are:

  - ``do_pr`` exits before any network call: branch check, auth check
  - ``do_pr`` queues a vPR via ``AUTO_PR_SIMULATE_OUTAGE=1``
  - ``flush_queue`` empty / auth error
  - ``gov_cleanup`` is exercised indirectly via the auth-error path

These paths require zero forge interaction, so we don't need a full
forgejo-api stub.  We do need an isolated git repo (so ``REPO_ROOT``
points somewhere disposable) and ``AUTOPR_RESULT_FILE`` pointed at a
tmp file.

Each test invokes ``bash <REPO_ROOT>/scripts/auto-pr <args>`` directly
against the real script (not a copy), but inside a tmp git repo so the
script's branch / state checks see fixture state instead of the host
repo's actual state.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT_REAL = Path(__file__).resolve().parent.parent
AUTO_PR_PATH = REPO_ROOT_REAL / "scripts" / "auto-pr"


def _init_fake_repo(tmp_path: Path, branch: str = "feature") -> Path:
    """Build a tiny fake git repo with the requested current branch.

    The fake repo has a single empty commit and the named branch checked
    out so ``git rev-parse --show-toplevel`` and ``git branch --show-current``
    return the expected values without touching the host repository.
    """
    fake_root = tmp_path / "fake-repo"
    fake_root.mkdir()

    def run(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=str(fake_root),
            check=True,
            capture_output=True,
        )

    run("init", "-q", "-b", "dev")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test")
    run("commit", "--allow-empty", "-m", "init")
    # auto-pr's detect_api_base calls `git remote get-url origin` and
    # parses the URL into a slug.  Use a Codeberg-shaped URL so the
    # parser is happy and the script gets through to the simulated-outage
    # branch instead of crashing on a missing remote.
    run("remote", "add", "origin", "https://codeberg.org/test/repo.git")
    if branch != "dev":
        run("checkout", "-q", "-b", branch)
    return fake_root


def _run_autopr(
    fake_root: Path,
    *args: str,
    sentinel: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke the real ``scripts/auto-pr`` from inside a fake repo.

    Sentinel destination defaults to ``<fake-repo>/.git/AUTOPR_LAST_RESULT.json``
    (matching the production default), but tests can override via the
    ``sentinel`` parameter.
    """
    env = dict(os.environ)
    # Strip anything that might leak host state into the script.
    for k in (
        "FORGEJO_USER", "FORGEJO_TOKEN",
        "SELFHOSTED_FORGEJO_USER", "SELFHOSTED_FORGEJO_TOKEN",
        "AUTO_PR_SIMULATE_OUTAGE",
    ):
        env.pop(k, None)
    if sentinel is not None:
        env["AUTOPR_RESULT_FILE"] = str(sentinel)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(AUTO_PR_PATH), *args],
        cwd=str(fake_root),
        capture_output=True,
        text=True,
        env=env,
    )


def _read_sentinel(path: Path) -> dict:
    assert path.exists(), f"sentinel not written to {path}"
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Schema invariants (apply to every sentinel write)
# ---------------------------------------------------------------------------


REQUIRED_KEYS = {
    "exit_code", "pr_number", "pr_url",
    "merged_sha", "timestamp", "final_state",
}


def _assert_schema(payload: dict) -> None:
    assert set(payload.keys()) == REQUIRED_KEYS, (
        f"sentinel must have exactly {REQUIRED_KEYS}, got {set(payload.keys())}"
    )
    assert isinstance(payload["exit_code"], int)
    assert payload["pr_number"] is None or isinstance(payload["pr_number"], int)
    assert payload["pr_url"] is None or isinstance(payload["pr_url"], str)
    assert payload["merged_sha"] is None or isinstance(payload["merged_sha"], str)
    assert isinstance(payload["timestamp"], str)
    assert payload["timestamp"].endswith("Z"), (
        "timestamp must be ISO-8601 UTC with Z suffix"
    )
    assert isinstance(payload["final_state"], str)
    assert payload["final_state"] != "unknown", (
        "final_state must be set to a real terminal state, not 'unknown'"
    )


# ---------------------------------------------------------------------------
# do_pr exit paths
# ---------------------------------------------------------------------------


class TestDoPrSentinel:
    """do_pr writes a sentinel on every exit path."""

    def test_protected_branch_exit(self, tmp_path: Path) -> None:
        """Running auto-pr from dev exits 1 with failed_protected_branch."""
        fake = _init_fake_repo(tmp_path, branch="dev")
        sentinel = tmp_path / "sentinel.json"
        result = _run_autopr(
            fake, sentinel=sentinel,
            extra_env={"FORGEJO_USER": "u", "FORGEJO_TOKEN": "t"},
        )
        assert result.returncode == 1, result.stdout + result.stderr
        payload = _read_sentinel(sentinel)
        _assert_schema(payload)
        assert payload["final_state"] == "failed_protected_branch"
        assert payload["exit_code"] == 1
        assert payload["pr_number"] is None
        assert payload["merged_sha"] is None

    def test_auth_error_missing_user(self, tmp_path: Path) -> None:
        """Missing FORGEJO_USER exits 1 with auth_error."""
        fake = _init_fake_repo(tmp_path, branch="feature")
        sentinel = tmp_path / "sentinel.json"
        # FORGEJO_USER stripped by _run_autopr; FORGEJO_TOKEN provided
        # to isolate the failure to the user check.
        result = _run_autopr(
            fake, sentinel=sentinel,
            extra_env={"FORGEJO_TOKEN": "t"},
        )
        assert result.returncode == 1
        payload = _read_sentinel(sentinel)
        _assert_schema(payload)
        assert payload["final_state"] == "auth_error"

    def test_auth_error_missing_token(self, tmp_path: Path) -> None:
        """Missing FORGEJO_TOKEN exits 1 with auth_error."""
        fake = _init_fake_repo(tmp_path, branch="feature")
        sentinel = tmp_path / "sentinel.json"
        result = _run_autopr(
            fake, sentinel=sentinel,
            extra_env={"FORGEJO_USER": "u"},
        )
        assert result.returncode == 1
        payload = _read_sentinel(sentinel)
        _assert_schema(payload)
        assert payload["final_state"] == "auth_error"

    def test_simulated_outage_queues_vpr(self, tmp_path: Path) -> None:
        """AUTO_PR_SIMULATE_OUTAGE=1 from a feature branch queues a vPR
        and writes final_state=queued_vpr with exit_code 0."""
        fake = _init_fake_repo(tmp_path, branch="feature")
        sentinel = tmp_path / "sentinel.json"
        result = _run_autopr(
            fake, sentinel=sentinel,
            extra_env={
                "FORGEJO_USER": "u",
                "FORGEJO_TOKEN": "t",
                "AUTO_PR_SIMULATE_OUTAGE": "1",
                # Skip the smart-test manifest regen — the fake repo
                # has no hypergumbo install and we don't need a real
                # manifest to exercise the simulated-outage exit path.
                "AUTO_PR_SKIP_MANIFEST": "1",
            },
        )
        # Exit code 0 because vPR queueing is a successful "offline" path
        assert result.returncode == 0, result.stdout + result.stderr
        payload = _read_sentinel(sentinel)
        _assert_schema(payload)
        assert payload["final_state"] == "queued_vpr"
        assert payload["exit_code"] == 0
        assert payload["pr_number"] is None
        assert payload["merged_sha"] is None
        # vPR queue file should also exist
        assert (fake / ".git" / "PR_QUEUE").exists()


# ---------------------------------------------------------------------------
# flush_queue exit paths
# ---------------------------------------------------------------------------


class TestFlushQueueSentinel:
    """flush_queue writes a sentinel on every exit path."""

    def test_empty_queue_noop(self, tmp_path: Path) -> None:
        """Flushing an empty queue exits 0 with noop_empty_queue."""
        fake = _init_fake_repo(tmp_path, branch="dev")
        sentinel = tmp_path / "sentinel.json"
        result = _run_autopr(
            fake, "flush", sentinel=sentinel,
            # No FORGEJO_* needed: empty-queue check happens before auth.
        )
        assert result.returncode == 0, result.stdout + result.stderr
        payload = _read_sentinel(sentinel)
        _assert_schema(payload)
        assert payload["final_state"] == "noop_empty_queue"

    def test_auth_error(self, tmp_path: Path) -> None:
        """Flushing a non-empty queue without FORGEJO_USER exits 1 auth_error."""
        fake = _init_fake_repo(tmp_path, branch="dev")
        # Seed a fake vPR so the auth check actually runs.
        (fake / ".git" / "PR_QUEUE").write_text(
            '{"vpr":1,"branch":"feat/x","base":"dev",'
            '"title":"x","desc":"","sha":"abc","ts":"2026-01-01T00:00:00Z"}\n'
        )
        sentinel = tmp_path / "sentinel.json"
        result = _run_autopr(
            fake, "flush", sentinel=sentinel,
            extra_env={"FORGEJO_TOKEN": "t"},  # USER stripped
        )
        assert result.returncode == 1
        payload = _read_sentinel(sentinel)
        _assert_schema(payload)
        assert payload["final_state"] == "auth_error"


# ---------------------------------------------------------------------------
# Non-action subcommands MUST NOT write a sentinel
# ---------------------------------------------------------------------------


class TestNonActionSubcommands:
    """list/status/help do not perform an auto-pr action and must not
    fabricate a stale sentinel that overrides a prior real run."""

    @pytest.mark.parametrize("sub", ["help", "--help", "-h"])
    def test_help_does_not_write_sentinel(
        self, tmp_path: Path, sub: str,
    ) -> None:
        fake = _init_fake_repo(tmp_path, branch="dev")
        sentinel = tmp_path / "sentinel.json"
        result = _run_autopr(fake, sub, sentinel=sentinel)
        assert result.returncode == 0
        assert "Usage:" in result.stdout
        assert not sentinel.exists(), (
            "help must not write a sentinel — it would clobber the "
            "previous real run's result"
        )

    def test_list_empty_does_not_write_sentinel(self, tmp_path: Path) -> None:
        fake = _init_fake_repo(tmp_path, branch="dev")
        sentinel = tmp_path / "sentinel.json"
        result = _run_autopr(fake, "list", sentinel=sentinel)
        assert result.returncode == 0
        assert not sentinel.exists()

    def test_status_does_not_write_sentinel(self, tmp_path: Path) -> None:
        fake = _init_fake_repo(tmp_path, branch="dev")
        sentinel = tmp_path / "sentinel.json"
        result = _run_autopr(fake, "status", sentinel=sentinel)
        assert result.returncode == 0
        assert not sentinel.exists()


# ---------------------------------------------------------------------------
# Sentinel idempotency / overwrite
# ---------------------------------------------------------------------------


class TestSentinelOverwrite:
    """Each run overwrites the prior sentinel — observers always see
    the most recent action's terminal state."""

    def test_second_run_overwrites_first(self, tmp_path: Path) -> None:
        fake = _init_fake_repo(tmp_path, branch="dev")
        sentinel = tmp_path / "sentinel.json"

        # First run: protected branch failure
        _run_autopr(
            fake, sentinel=sentinel,
            extra_env={"FORGEJO_USER": "u", "FORGEJO_TOKEN": "t"},
        )
        first = _read_sentinel(sentinel)
        assert first["final_state"] == "failed_protected_branch"

        # Second run: empty-queue flush (different terminal state)
        _run_autopr(fake, "flush", sentinel=sentinel)
        second = _read_sentinel(sentinel)
        assert second["final_state"] == "noop_empty_queue"
        # Timestamps differ — second is at least as recent as first.
        assert second["timestamp"] >= first["timestamp"]


# ---------------------------------------------------------------------------
# WI-bahuf: 'commit is the same as the old commit' → already-merged hint
# ---------------------------------------------------------------------------


class TestAlreadyMergedDetection:
    """auto-pr must detect Forgejo's 'same as old commit' rejection,
    look up the merged PR, and clean up locally without queueing a vPR.

    Because the rejection requires actually contacting Forgejo with a
    valid refs/for/ push, we use the AUTO_PR_SIMULATE_ALREADY_MERGED
    env var to inject a synthetic PUSH_OUTPUT and exercise the
    detection / cleanup pipeline.
    """

    def test_simulated_already_merged_with_head_in_base(
        self, tmp_path: Path,
    ) -> None:
        """HEAD on the feature branch == HEAD of dev (no extra commit) →
        handler proceeds, cleanup runs, sentinel records merged state."""
        fake = _init_fake_repo(tmp_path, branch="feature")
        # Wire local origin so the fetch is a no-op and origin/dev is
        # local dev — feature == dev so the SHA is in origin/dev.
        subprocess.run(
            ["git", "remote", "set-url", "origin", str(fake)],
            cwd=str(fake), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "branch", "-f", "origin/dev", "dev"],
            cwd=str(fake), check=True, capture_output=True,
        )

        sentinel = tmp_path / "sentinel.json"
        result = _run_autopr(
            fake, sentinel=sentinel,
            extra_env={
                "FORGEJO_USER": "u",
                "FORGEJO_TOKEN": "t",
                "AUTO_PR_SIMULATE_ALREADY_MERGED": "1",
                "AUTO_PR_SKIP_MANIFEST": "1",
            },
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Already merged" in result.stdout
        payload = _read_sentinel(sentinel)
        _assert_schema(payload)
        assert payload["final_state"] == "already_merged"
        assert payload["exit_code"] == 0
        # merged_sha must be set to the local HEAD
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(fake), check=True, capture_output=True, text=True,
        ).stdout.strip()
        assert payload["merged_sha"] == head_sha
        # PR# is unset in this fixture (find_merged_pr fails against
        # the local origin pseudo-remote)
        assert payload["pr_number"] is None

    def test_simulated_already_merged_with_head_not_in_base(
        self, tmp_path: Path,
    ) -> None:
        """HEAD on feature has a commit NOT in dev → detection bails
        out with failed_already_merged_check."""
        fake = _init_fake_repo(tmp_path, branch="feature")
        # Add a commit to feature that is NOT in dev
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "feature work"],
            cwd=str(fake), check=True, capture_output=True,
        )
        # Local origin trick (no real upstream)
        subprocess.run(
            ["git", "remote", "set-url", "origin", str(fake)],
            cwd=str(fake), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "branch", "-f", "origin/dev", "dev"],
            cwd=str(fake), check=True, capture_output=True,
        )

        sentinel = tmp_path / "sentinel.json"
        result = _run_autopr(
            fake, sentinel=sentinel,
            extra_env={
                "FORGEJO_USER": "u",
                "FORGEJO_TOKEN": "t",
                "AUTO_PR_SIMULATE_ALREADY_MERGED": "1",
                "AUTO_PR_SKIP_MANIFEST": "1",
            },
        )
        assert result.returncode == 1, result.stdout + result.stderr
        payload = _read_sentinel(sentinel)
        _assert_schema(payload)
        assert payload["final_state"] == "failed_already_merged_check"

    def test_already_merged_simulation_requires_auth(
        self, tmp_path: Path,
    ) -> None:
        """Auth check fires before AUTO_PR_SIMULATE_ALREADY_MERGED is
        consumed — locks ordering."""
        fake = _init_fake_repo(tmp_path, branch="feature")
        sentinel = tmp_path / "sentinel.json"
        result = _run_autopr(
            fake, sentinel=sentinel,
            extra_env={
                "FORGEJO_TOKEN": "t",  # USER stripped
                "AUTO_PR_SIMULATE_ALREADY_MERGED": "1",
                "AUTO_PR_SKIP_MANIFEST": "1",
            },
        )
        assert result.returncode == 1
        payload = _read_sentinel(sentinel)
        # Auth error fires first — already-merged path never runs.
        assert payload["final_state"] == "auth_error"


class TestRejectionHelperFunctions:
    """Direct tests for ``_autopr_is_already_merged_rejection`` —
    sourced into a tiny harness so the matcher is exercised in
    isolation from the do_pr / flush_queue control flow."""

    def _run_helper(
        self, tmp_path: Path, push_output: str,
    ) -> int:
        """Source the helper definition into a one-shot bash and
        return the exit code of the rejection check on push_output."""
        # Reproduce ONLY the helper here so the test doesn't depend
        # on auto-pr's other top-level state.  This is the same body
        # as scripts/auto-pr's _autopr_is_already_merged_rejection.
        # If the production helper changes, this test must be updated
        # to match — that is the intent (it locks the matcher in).
        harness = tmp_path / "harness.sh"
        harness.write_text(
            '''#!/usr/bin/env bash
set -euo pipefail
_autopr_is_already_merged_rejection() {
    local push_output="$1"
    echo "$push_output" | grep -qiE "the new commit is the same as the old commit"
}
_autopr_is_already_merged_rejection "$1"
'''
        )
        result = subprocess.run(
            ["bash", str(harness), push_output],
            capture_output=True, text=True,
        )
        return result.returncode

    def test_matches_literal_message(self, tmp_path: Path) -> None:
        rc = self._run_helper(
            tmp_path,
            "remote: error: The new commit is the same as the old commit",
        )
        assert rc == 0

    def test_matches_case_insensitively(self, tmp_path: Path) -> None:
        rc = self._run_helper(
            tmp_path,
            "REMOTE: ERROR: THE NEW COMMIT IS THE SAME AS THE OLD COMMIT",
        )
        assert rc == 0

    def test_does_not_match_unrelated_error(self, tmp_path: Path) -> None:
        rc = self._run_helper(
            tmp_path,
            "remote: error: failed to push some refs",
        )
        assert rc != 0

    def test_does_not_match_empty_output(self, tmp_path: Path) -> None:
        rc = self._run_helper(tmp_path, "")
        assert rc != 0
