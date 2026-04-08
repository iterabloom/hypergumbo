# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lifecycle tests for the transcript-sync watcher (ADR-0018).

Covers the bugs that allowed 13 stale `sync-transcript.sh` processes to
accumulate in production:

* `kill-transcript-sync.sh` had no fallback when the PID file was missing,
  so silent leaks were unkillable.
* `launch-transcript-sync.sh` only consulted the PID file, never scanned
  by process name, so each new session started a new watcher even when
  stale ones were still running.
* `sync-transcript.sh`'s EXIT trap removed the PID file unconditionally,
  racing with the next session's PID write.

These tests use real subprocesses (`subprocess.Popen`) and `pgrep` because
the bugs live in shell scripts. Subprocess tests don't contribute to Python
coverage, but the shell scripts aren't coverage-instrumented either — the
tests document and gate behavior.

Safety: each test uses a unique tmp_path so the kill-script's `pgrep`
fallback (which is scoped to the repo's expected destination path) cannot
accidentally kill the user's real watcher running on the host.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest


REPO_ROOT_REAL = Path(__file__).parent.parent
KILL_SCRIPT = REPO_ROOT_REAL / ".agent" / "hooks" / "_shared" / "kill-transcript-sync.sh"
LAUNCH_SCRIPT = REPO_ROOT_REAL / ".agent" / "hooks" / "_shared" / "launch-transcript-sync.sh"
SYNC_SCRIPT = REPO_ROOT_REAL / ".agent" / "hooks" / "_shared" / "sync-transcript.sh"


def _proc_alive(pid: int) -> bool:
    """Return True if the given PID is a live process we can signal."""
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _wait_dead(pid: int, timeout: float = 2.0) -> bool:
    """Poll until the process is gone or timeout expires.

    Use this only for PIDs you do NOT own as a Popen object — `os.kill(pid, 0)`
    will return success for zombie children of *some other* parent. For Popen
    objects, use `_wait_proc_dead` which calls `proc.wait()` to reap.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _proc_alive(pid):
            return True
        time.sleep(0.05)
    return False


def _wait_proc_dead(proc: subprocess.Popen, timeout: float = 2.0) -> bool:
    """Wait for a Popen child to terminate AND reap it.

    `os.kill(pid, 0)` returns success even for zombies, so checking liveness
    of a child process via PID alone is unreliable. `proc.wait()` reaps the
    zombie and returns the exit code, which is the only safe signal that
    the process is truly gone from our perspective.
    """
    try:
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def _spawn_fake_watcher(src: str, dest: str, tmp_path: Path) -> subprocess.Popen:
    """Spawn a process that looks like sync-transcript.sh to pgrep.

    The fake just sleeps so the test can exercise lifecycle behavior. We
    write a real script file named exactly 'sync-transcript.sh' so that
    `ps -o args=` reports the canonical command-line shape:

        bash /tmp/.../sync-transcript.sh <SRC> <DEST>
    """
    fake_dir = tmp_path / "fake-bin"
    fake_dir.mkdir(exist_ok=True)
    fake = fake_dir / "sync-transcript.sh"
    fake.write_text("#!/bin/bash\nsleep 60\n")
    fake.chmod(0o755)
    proc = subprocess.Popen(
        [str(fake), src, dest],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Give pgrep time to see it
    time.sleep(0.2)
    return proc


def _cleanup_proc(proc: subprocess.Popen) -> None:
    """Best-effort kill of a leftover test subprocess."""
    if _proc_alive(proc.pid):
        try:
            proc.kill()
            proc.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass


def _isolate_shared_scripts(tmp_path: Path) -> Path:
    """Copy the shared hook scripts into tmp_path so their `BASH_SOURCE`-based
    path resolution lands inside the test sandbox instead of the real repo.

    sync-transcript.sh, launch-transcript-sync.sh, and kill-transcript-sync.sh
    all expect to live at `<REPO_ROOT>/.agent/hooks/_shared/`. Without this
    isolation, the scripts would resolve REPO_ROOT to the real hypergumbo
    checkout and clobber the live watcher's PID file.

    Returns the shared dir path.
    """
    shared_dir = tmp_path / ".agent" / "hooks" / "_shared"
    shared_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "sync-transcript.sh",
        "filter-transcript.py",
        "launch-transcript-sync.sh",
        "kill-transcript-sync.sh",
    ):
        src_file = REPO_ROOT_REAL / ".agent" / "hooks" / "_shared" / name
        dest_file = shared_dir / name
        shutil.copy(src_file, dest_file)
        dest_file.chmod(0o755)
    return shared_dir


# ---------------------------------------------------------------------------
# kill-transcript-sync.sh tests
# ---------------------------------------------------------------------------

class TestKillScript:
    """Verify both the PID-file path and the pgrep fallback."""

    def test_kill_via_pid_file(self, tmp_path: Path) -> None:
        """When the PID file exists, the script kills the named process
        and removes the file. (This is the existing happy path.)"""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()

        proc = subprocess.Popen(["sleep", "60"])
        try:
            (agent_dir / ".transcript-sync.pid").write_text(str(proc.pid))

            result = subprocess.run(
                ["bash", str(KILL_SCRIPT), str(tmp_path)],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stderr
            assert _wait_proc_dead(proc), "watcher should be killed via PID file"
            assert not (agent_dir / ".transcript-sync.pid").exists()
        finally:
            _cleanup_proc(proc)

    def test_kill_via_pgrep_when_pid_missing(self, tmp_path: Path) -> None:
        """When the PID file is absent, pgrep finds the watcher whose DEST
        argument matches this repo's expected destination and kills it.

        This is the missing fallback that allowed 13 stale watchers to
        accumulate in production.
        """
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        expected_dest = str(agent_dir / ".current_session_transcript.jsonl")
        fake_src = tmp_path / "fake-src.jsonl"
        fake_src.touch()

        proc = _spawn_fake_watcher(str(fake_src), expected_dest, tmp_path)
        try:
            assert _proc_alive(proc.pid)
            assert not (agent_dir / ".transcript-sync.pid").exists()

            result = subprocess.run(
                ["bash", str(KILL_SCRIPT), str(tmp_path)],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stderr
            assert _wait_proc_dead(proc), (
                "pgrep fallback failed: watcher with matching DEST was not killed"
            )
        finally:
            _cleanup_proc(proc)

    def test_kill_skips_other_repo(self, tmp_path: Path) -> None:
        """A watcher whose DEST belongs to a DIFFERENT repo must NOT be
        killed. This is the safety property that lets concurrent Claude
        Code sessions in different repos coexist."""
        repo_a = tmp_path / "repo_a"
        (repo_a / ".agent").mkdir(parents=True)

        repo_b = tmp_path / "repo_b"
        (repo_b / ".agent").mkdir(parents=True)
        repo_b_dest = str(repo_b / ".agent" / ".current_session_transcript.jsonl")
        fake_src = tmp_path / "shared-src.jsonl"
        fake_src.touch()

        proc = _spawn_fake_watcher(str(fake_src), repo_b_dest, tmp_path)
        try:
            assert _proc_alive(proc.pid)

            result = subprocess.run(
                ["bash", str(KILL_SCRIPT), str(repo_a)],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stderr

            # Repo B's watcher must STILL be alive
            time.sleep(0.3)
            assert _proc_alive(proc.pid), (
                "kill script killed a watcher belonging to a different repo"
            )
        finally:
            _cleanup_proc(proc)

    def test_idempotent_when_nothing_to_kill(self, tmp_path: Path) -> None:
        """Running kill on a clean repo (no PID file, no processes) is a no-op."""
        (tmp_path / ".agent").mkdir()

        result = subprocess.run(
            ["bash", str(KILL_SCRIPT), str(tmp_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# launch-transcript-sync.sh tests
# ---------------------------------------------------------------------------

class TestLaunchScript:
    def test_launch_kills_stale(self, tmp_path: Path) -> None:
        """launch-transcript-sync.sh must kill any stale watcher whose DEST
        matches this repo before launching the new watcher."""
        # Isolate the shared scripts so launch-transcript-sync.sh, which
        # spawns sync-transcript.sh from its own SCRIPT_DIR, doesn't end up
        # writing to the real repo's .agent/.
        shared = _isolate_shared_scripts(tmp_path)
        launch_script = shared / "launch-transcript-sync.sh"
        kill_script = shared / "kill-transcript-sync.sh"

        agent_dir = tmp_path / ".agent"
        expected_dest = str(agent_dir / ".current_session_transcript.jsonl")

        # Stale watcher with matching DEST
        old_src = tmp_path / "old-src.jsonl"
        old_src.touch()
        stale = _spawn_fake_watcher(str(old_src), expected_dest, tmp_path)

        # Track any watcher launch script may produce
        new_src = tmp_path / "new-src.jsonl"
        new_src.touch()

        try:
            assert _proc_alive(stale.pid)

            env = os.environ.copy()
            env["REPO_ROOT"] = str(tmp_path)
            result = subprocess.run(
                ["bash", str(launch_script), str(new_src), str(tmp_path)],
                capture_output=True, text=True, env=env,
            )
            assert result.returncode == 0, result.stderr

            # Stale must be dead
            assert _wait_proc_dead(stale), (
                "launch script did not kill stale watcher with matching DEST"
            )
        finally:
            _cleanup_proc(stale)
            # Clean up any new watcher the launch script may have started
            subprocess.run(
                ["bash", str(kill_script), str(tmp_path)],
                capture_output=True,
            )
            # Also pgrep-sweep any leftover matching DEST (defensive)
            try:
                out = subprocess.run(
                    ["pgrep", "-af", "sync-transcript.sh"],
                    capture_output=True, text=True,
                ).stdout
                for line in out.splitlines():
                    if expected_dest in line:
                        pid = int(line.split()[0])
                        try:
                            os.kill(pid, 9)
                        except ProcessLookupError:
                            pass
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------------------
# sync-transcript.sh EXIT trap race-fix test
# ---------------------------------------------------------------------------

class TestExitTrapRace:
    def test_exit_trap_preserves_other_pid(self, tmp_path: Path) -> None:
        """sync-transcript.sh's EXIT trap must NOT delete the PID file when
        the file contains a PID other than the watcher's own.

        This is the race fix: previously, an exiting watcher's cleanup
        would unconditionally rm the PID file, even if a new watcher had
        already overwritten it with its own PID. The new behavior is
        conditional on `cat "$PID_FILE" == "$$"`.
        """
        # Isolate sync-transcript.sh into tmp_path so REPO_ROOT resolves
        # to the sandbox, not the real repo (which has a live watcher).
        shared = _isolate_shared_scripts(tmp_path)
        sync_script = shared / "sync-transcript.sh"
        agent_dir = tmp_path / ".agent"

        # Source file the watcher needs to exist
        src = tmp_path / "src.jsonl"
        src.write_text("")
        dest = agent_dir / ".current_session_transcript.jsonl"
        pid_file = agent_dir / ".transcript-sync.pid"

        stderr_log = tmp_path / "sync-stderr.log"
        with open(stderr_log, "wb") as err:
            proc = subprocess.Popen(
                ["bash", str(sync_script), str(src), str(dest)],
                stdout=subprocess.DEVNULL,
                stderr=err,
            )
        try:
            # Wait for the watcher to register its PID
            for _ in range(50):
                if pid_file.exists() and pid_file.read_text().strip():
                    break
                time.sleep(0.1)
            assert pid_file.exists(), (
                f"watcher never wrote PID file. stderr: {stderr_log.read_text()!r}"
            )
            assert pid_file.read_text().strip() == str(proc.pid)

            # Simulate the race: another session has overwritten the PID
            # file with its own PID before this watcher exits.
            other_pid = "99999"
            pid_file.write_text(other_pid)

            # Terminate this watcher (triggers cleanup trap)
            proc.terminate()
            proc.wait(timeout=5)

            # The other PID must survive — the cleanup trap should have
            # noticed the PID file no longer belongs to it.
            time.sleep(0.2)
            assert pid_file.exists(), (
                "EXIT trap clobbered another session's PID file"
            )
            assert pid_file.read_text().strip() == other_pid
        finally:
            _cleanup_proc(proc)
            if pid_file.exists() and pid_file.read_text().strip() == "99999":
                pid_file.unlink()
