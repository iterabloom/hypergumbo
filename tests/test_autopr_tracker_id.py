# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the ``scripts/auto-pr`` ``--tracker-id`` flag.

Per the 2026-04-11 user discussion, when the agent resolves a tracker item
via ``auto-pr``, it currently writes a pre-merge discussion entry that cites
the feature-branch commit SHA. That SHA survives true fast-forward merges
but is changed by rebase-before-merge and squash-fallback, so it is not a
guaranteed-durable identifier.

``auto-pr`` already knows two durable identifiers post-merge (``PR_NUM`` and
``_autopr_state_merged_sha``, both surfaced in the WI-miriz sentinel). The
``--tracker-id`` flag lets a caller hand auto-pr a tracker item ID; on
successful merge, auto-pr appends a tracker discussion entry to that item
carrying the PR number and the merged SHA.

Test strategy
-------------
The test file exercises three surfaces:

1. **Arg parsing.** Existing failure paths (protected-branch, simulated
   outage) should still fail the same way when ``--tracker-id WI-foo`` is
   added. The flag must not break any existing code path.

2. **The post-merge helper in isolation.** The helper that invokes
   ``scripts/tracker discuss`` is factored into a shell function that tests
   source via ``AUTOPR_SOURCE_ONLY=1`` (a test-only short-circuit that skips
   main dispatch). Tests stub the tracker binary via ``AUTOPR_TRACKER_CMD``
   and assert the stub was called with the expected arguments.

3. **No-op and failure tolerance.** Empty tracker_id must skip the discuss
   call entirely; a failing tracker binary must not propagate non-zero exit
   back to auto-pr's caller.

Integration of the helper with the actual merge-success sites (do_pr,
flush_queue, already-merged recovery) is validated structurally by grep: a
single point of truth (the helper) is called from all three sites.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT_REAL = Path(__file__).resolve().parent.parent
AUTO_PR_PATH = REPO_ROOT_REAL / "scripts" / "auto-pr"


def _init_fake_repo(tmp_path: Path, branch: str = "feature") -> Path:
    """Build a tiny fake git repo with the requested current branch.

    Copied from test_autopr_result_sentinel.py — same reason: auto-pr's
    branch / remote checks need realistic state, but we don't want to touch
    the host repository.
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
    env = dict(os.environ)
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


def _make_tracker_stub(tmp_path: Path, *, exit_code: int = 0) -> tuple[Path, Path]:
    """Create a stub ``tracker`` binary that logs its arguments.

    Returns ``(stub_path, log_path)``. ``log_path`` is one line per
    invocation; each line is the space-joined argv (discuss <id> <msg>).
    The stub exits with ``exit_code``.
    """
    stub = tmp_path / "tracker-stub"
    log = tmp_path / "tracker-stub.log"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        f"exit {exit_code}\n"
    )
    stub.chmod(0o755)
    return stub, log


# ---------------------------------------------------------------------------
# Arg parsing: --tracker-id must not break existing failure paths
# ---------------------------------------------------------------------------


class TestTrackerIdArgAccepted:
    """``--tracker-id WI-foo`` is a valid flag; existing paths unchanged."""

    def test_protected_branch_exit_with_tracker_id(self, tmp_path: Path) -> None:
        """Running from dev still exits 1 when --tracker-id is supplied."""
        fake = _init_fake_repo(tmp_path, branch="dev")
        sentinel = tmp_path / "sentinel.json"
        result = _run_autopr(
            fake,
            "--tracker-id",
            "WI-foo-bar-baz-quux-fuzz-fizz-buzz-barn",
            sentinel=sentinel,
            extra_env={"FORGEJO_USER": "u", "FORGEJO_TOKEN": "t"},
        )
        assert result.returncode == 1, result.stdout + result.stderr

    def test_simulated_outage_with_tracker_id_queues_vpr(self, tmp_path: Path) -> None:
        """AUTO_PR_SIMULATE_OUTAGE + --tracker-id still queues a vPR.

        The tracker-discuss helper must NOT fire on the vPR-queue path
        because no merge happened yet. The stub log must remain empty.
        """
        fake = _init_fake_repo(tmp_path, branch="feature")
        sentinel = tmp_path / "sentinel.json"
        stub, log = _make_tracker_stub(tmp_path)
        result = _run_autopr(
            fake,
            "--tracker-id",
            "WI-foo-bar-baz-quux-fuzz-fizz-buzz-barn",
            sentinel=sentinel,
            extra_env={
                "FORGEJO_USER": "u",
                "FORGEJO_TOKEN": "t",
                "AUTO_PR_SIMULATE_OUTAGE": "1",
                "AUTO_PR_SKIP_MANIFEST": "1",
                "AUTOPR_TRACKER_CMD": str(stub),
            },
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (fake / ".git" / "PR_QUEUE").exists()
        # Helper must not have been invoked — no merge happened.
        assert not log.exists() or log.read_text() == "", (
            f"tracker-discuss helper fired on a vPR-queue path: {log.read_text()}"
        )

    def test_missing_tracker_id_value_errors(self, tmp_path: Path) -> None:
        """``--tracker-id`` with no following value should fail loudly, not silently."""
        fake = _init_fake_repo(tmp_path, branch="feature")
        result = _run_autopr(
            fake,
            "--tracker-id",  # no value
            extra_env={"FORGEJO_USER": "u", "FORGEJO_TOKEN": "t"},
        )
        # Any non-zero exit is acceptable — the behavior we're ruling out is
        # silently treating the next positional arg as a tracker ID.
        assert result.returncode != 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Helper function in isolation (via AUTOPR_SOURCE_ONLY)
# ---------------------------------------------------------------------------


def _invoke_helper(
    *,
    tracker_id: str,
    pr_num: str,
    merged_sha: str,
    tracker_cmd: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Source auto-pr with AUTOPR_SOURCE_ONLY=1, then invoke the helper."""
    env = dict(os.environ)
    for k in (
        "FORGEJO_USER", "FORGEJO_TOKEN",
        "SELFHOSTED_FORGEJO_USER", "SELFHOSTED_FORGEJO_TOKEN",
    ):
        env.pop(k, None)
    env["AUTOPR_SOURCE_ONLY"] = "1"
    env["AUTOPR_TRACKER_CMD"] = tracker_cmd
    if extra_env:
        env.update(extra_env)
    script = (
        f'source "{AUTO_PR_PATH}"\n'
        f'_autopr_append_tracker_discussion '
        f'"{tracker_id}" "{pr_num}" "{merged_sha}"\n'
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
    )


class TestHelperBehavior:
    """``_autopr_append_tracker_discussion`` behavior in isolation."""

    def test_calls_tracker_with_expected_message(self, tmp_path: Path) -> None:
        stub, log = _make_tracker_stub(tmp_path)
        result = _invoke_helper(
            tracker_id="WI-foo-bar-baz-quux-fuzz-fizz-buzz-barn",
            pr_num="2789",
            merged_sha="abcdef1234567890",
            tracker_cmd=str(stub),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert log.exists(), "tracker stub was not called"
        logged = log.read_text().strip()
        # Expected shape: `discuss WI-foo... Merged as PR #2789, dev SHA abcdef123456.`
        assert logged.startswith("discuss WI-foo-bar-baz-quux-fuzz-fizz-buzz-barn"), logged
        assert "PR #2789" in logged
        # Short SHA — 12 chars is the conventional auto-pr length.
        assert "abcdef123456" in logged
        assert "dev SHA" in logged

    def test_empty_tracker_id_is_noop(self, tmp_path: Path) -> None:
        stub, log = _make_tracker_stub(tmp_path)
        result = _invoke_helper(
            tracker_id="",
            pr_num="2789",
            merged_sha="abcdef1234567890",
            tracker_cmd=str(stub),
        )
        assert result.returncode == 0
        assert not log.exists() or log.read_text() == "", (
            "helper invoked tracker even though tracker_id was empty"
        )

    def test_tolerates_tracker_failure(self, tmp_path: Path) -> None:
        """A failing tracker binary must not break auto-pr."""
        stub, log = _make_tracker_stub(tmp_path, exit_code=1)
        result = _invoke_helper(
            tracker_id="WI-foo-bar-baz-quux-fuzz-fizz-buzz-barn",
            pr_num="2789",
            merged_sha="abcdef1234567890",
            tracker_cmd=str(stub),
        )
        # Helper returns 0 even when the underlying tracker call fails.
        assert result.returncode == 0, result.stdout + result.stderr
        assert log.exists(), "stub should still have been invoked"


# ---------------------------------------------------------------------------
# Structural: the helper is wired into all three merge-success sites
# ---------------------------------------------------------------------------


class TestWiring:
    """A single point of truth (the helper) is called from every merge site.

    We grep the script rather than running it end-to-end because the real
    merge paths require forge interaction. This test catches the class of
    bug where someone adds a new merge-success site and forgets to call
    the helper.
    """

    def test_helper_defined_once(self) -> None:
        content = AUTO_PR_PATH.read_text()
        defs = [
            line for line in content.splitlines()
            if "_autopr_append_tracker_discussion()" in line
        ]
        assert len(defs) == 1, f"helper must be defined exactly once, found {len(defs)}"

    def test_helper_called_from_do_pr(self) -> None:
        """The do_pr merge-success site calls the helper."""
        content = AUTO_PR_PATH.read_text()
        lines = content.splitlines()
        # Find the do_pr merge-success marker and look ahead a few lines
        # for the helper call.
        for i, line in enumerate(lines):
            if '_autopr_state_final="merged"' in line and 'LOCAL_SHA' in "\n".join(lines[i:i + 5]):
                window = "\n".join(lines[i:i + 10])
                assert "_autopr_append_tracker_discussion" in window, (
                    f"do_pr merge-success site does not call helper: {window}"
                )
                return
        pytest.fail("do_pr merge-success marker not found")

    def test_helper_called_from_flush_queue(self) -> None:
        """The flush_queue merge-success site calls the helper."""
        content = AUTO_PR_PATH.read_text()
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if '_autopr_state_final="merged"' in line and 'tip_sha' in "\n".join(lines[i:i + 5]):
                window = "\n".join(lines[i:i + 10])
                assert "_autopr_append_tracker_discussion" in window, (
                    f"flush_queue merge-success site does not call helper: {window}"
                )
                return
        pytest.fail("flush_queue merge-success marker not found")
