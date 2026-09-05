# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lifecycle tests for the per-session transcript-sync watcher (ADR-0018
amendment / Option 2).

Covers:

* Per-session DEST and PID file isolation: each session writes to its own
  ``.current_session_transcript.<session_id>.jsonl`` and
  ``.transcript-sync.<session_id>.pid``, so concurrent sessions in the
  same repo never race.
* ``kill-transcript-sync.sh`` matches by session_id only — a sibling
  session in the same repo with a different session_id is never killed.
* ``launch-transcript-sync.sh`` orphan sweep: walks per-session PID files,
  archives any crashed-session current files into ``.archived-transcripts/``,
  and removes their stale PID files. Live siblings are left alone.
* ``rotate-on-session-end.sh`` promotes a session's per-session current
  files into the global ``.last_*``/``.second_to_last_*`` slots with a
  flock-based critical section so concurrent end events serialize cleanly.

These tests use real subprocesses (``subprocess.Popen``) and ``pgrep``
because the bugs they guard against live in shell scripts. Subprocess
tests don't contribute to Python coverage, but the shell scripts aren't
coverage-instrumented either — the tests document and gate behavior.

Safety: each test uses a unique tmp_path so the kill-script's ``pgrep``
fallback (which is scoped by session_id) cannot accidentally kill the
user's real watcher running on the host.
"""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest


REPO_ROOT_REAL = Path(__file__).parent.parent
SHARED_DIR = REPO_ROOT_REAL / ".agent" / "hooks" / "_shared"
KILL_SCRIPT = SHARED_DIR / "kill-transcript-sync.sh"
LAUNCH_SCRIPT = SHARED_DIR / "launch-transcript-sync.sh"
SYNC_SCRIPT = SHARED_DIR / "sync-transcript.sh"
ROTATE_SCRIPT = SHARED_DIR / "rotate-on-session-end.sh"


def _proc_alive(pid: int) -> bool:
    """Return True if the given PID is a live process we can signal."""
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _wait_dead(pid: int, timeout: float = 2.0) -> bool:
    """Poll until the process is gone or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _proc_alive(pid):
            return True
        time.sleep(0.05)
    return False


def _wait_proc_dead(proc: subprocess.Popen, timeout: float = 2.0) -> bool:
    """Wait for a Popen child to terminate AND reap it."""
    try:
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def _spawn_fake_watcher(
    src: str, dest: str, session_id: str, tmp_path: Path,
) -> subprocess.Popen:
    """Spawn a process that looks like sync-transcript.sh to pgrep.

    The fake just sleeps so the test can exercise lifecycle behavior. We
    write a real script file named exactly 'sync-transcript.sh' so that
    ``ps -o args=`` reports the canonical command-line shape:

        bash /tmp/.../sync-transcript.sh <SRC> <DEST> <SESSION_ID>

    The third positional argument matters: kill-transcript-sync.sh's
    pgrep fallback matches on the SESSION_ID at offset +3 from the
    script-name field, not on the DEST.
    """
    fake_dir = tmp_path / "fake-bin"
    fake_dir.mkdir(exist_ok=True)
    fake = fake_dir / "sync-transcript.sh"
    fake.write_text("#!/bin/bash\nsleep 60\n")
    fake.chmod(0o755)
    proc = subprocess.Popen(
        [str(fake), src, dest, session_id],
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
    """Copy the shared hook scripts into tmp_path so their ``BASH_SOURCE``-based
    path resolution lands inside the test sandbox instead of the real repo.

    Returns the shared dir path.
    """
    shared_dir = tmp_path / ".agent" / "hooks" / "_shared"
    shared_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "sync-transcript.sh",
        "filter-transcript.py",
        "launch-transcript-sync.sh",
        "kill-transcript-sync.sh",
        "rotate-on-session-end.sh",
        "poll-transcript-change.sh",
        "session_id_helpers.sh",
        # Required by launch-transcript-sync.sh and rotate-on-session-end.sh,
        # which source archive_scrubbed.sh and invoke scrub_secrets.py. Omitting
        # them made this sandbox a repo where the scrubber is MISSING, which is
        # one of the demonstrated data-loss modes -- that is how
        # test_orphan_sweep_archives_dead_session_files went red.
        "archive_scrubbed.sh",
        "scrub_secrets.py",
        # Required since INV-todig: every shell writer sources the
        # permission-contract helper; a sandbox without it is a repo where
        # the scripts die at the source line under set -e.
        "transcript_perms.sh",
    ):
        src_file = SHARED_DIR / name
        dest_file = shared_dir / name
        shutil.copy(src_file, dest_file)
        dest_file.chmod(0o755)
    return shared_dir


# ---------------------------------------------------------------------------
# kill-transcript-sync.sh tests (per-session signature)
# ---------------------------------------------------------------------------


class TestKillScript:
    """Verify both the PID-file path and the pgrep-by-session-id fallback."""

    def test_kill_via_pid_file(self, tmp_path: Path) -> None:
        """When the per-session PID file exists, the script kills the named
        process and removes the file."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()

        proc = subprocess.Popen(["sleep", "60"])
        try:
            session_id = "test-session-A"
            (agent_dir / f".transcript-sync.{session_id}.pid").write_text(
                str(proc.pid)
            )

            result = subprocess.run(
                ["bash", str(KILL_SCRIPT), str(tmp_path), session_id],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stderr
            assert _wait_proc_dead(proc), "watcher should be killed via PID file"
            assert not (
                agent_dir / f".transcript-sync.{session_id}.pid"
            ).exists()
        finally:
            _cleanup_proc(proc)

    def test_kill_via_pgrep_when_pid_missing(self, tmp_path: Path) -> None:
        """When the per-session PID file is absent, pgrep finds the watcher
        whose third positional argument equals SESSION_ID and kills it."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        session_id = "test-session-pgrep-fallback"
        expected_dest = str(
            agent_dir / f".current_session_transcript.{session_id}.jsonl"
        )
        fake_src = tmp_path / "fake-src.jsonl"
        fake_src.touch()

        proc = _spawn_fake_watcher(
            str(fake_src), expected_dest, session_id, tmp_path,
        )
        try:
            assert _proc_alive(proc.pid)
            assert not (
                agent_dir / f".transcript-sync.{session_id}.pid"
            ).exists()

            result = subprocess.run(
                ["bash", str(KILL_SCRIPT), str(tmp_path), session_id],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stderr
            assert _wait_proc_dead(proc), (
                "pgrep fallback failed: watcher with matching SESSION_ID "
                "was not killed"
            )
        finally:
            _cleanup_proc(proc)

    def test_kill_skips_sibling_session_in_same_repo(
        self, tmp_path: Path,
    ) -> None:
        """A watcher in the SAME repo with a DIFFERENT session_id must NOT
        be killed. This is the structural fix for the watcher-leak bug:
        the prior DEST-matching loop killed siblings because they shared a
        global DEST. Per-session pgrep matching makes siblings invisible
        to each other's kill events.
        """
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        sibling_sid = "session-sibling"
        sibling_dest = str(
            agent_dir / f".current_session_transcript.{sibling_sid}.jsonl"
        )
        fake_src = tmp_path / "shared-src.jsonl"
        fake_src.touch()

        proc = _spawn_fake_watcher(
            str(fake_src), sibling_dest, sibling_sid, tmp_path,
        )
        try:
            assert _proc_alive(proc.pid)

            # Try to kill a DIFFERENT session in the same repo. Sibling
            # must survive.
            result = subprocess.run(
                ["bash", str(KILL_SCRIPT), str(tmp_path), "session-other"],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stderr

            time.sleep(0.3)
            assert _proc_alive(proc.pid), (
                "kill script killed a sibling session's watcher in the "
                "same repo — the watcher-leak regression"
            )
        finally:
            _cleanup_proc(proc)

    def test_kill_skips_other_repo(self, tmp_path: Path) -> None:
        """A watcher whose session_id belongs to a watcher in a DIFFERENT
        repo must not be killed when we target our own session_id.

        Concurrent Claude Code sessions across different repos are
        protected here because pgrep matches by SESSION_ID, and there is
        no chance two unrelated repos pick the same arbitrary id."""
        repo_a = tmp_path / "repo_a"
        (repo_a / ".agent").mkdir(parents=True)

        repo_b = tmp_path / "repo_b"
        (repo_b / ".agent").mkdir(parents=True)
        repo_b_sid = "repo-b-session"
        repo_b_dest = str(
            repo_b / ".agent"
            / f".current_session_transcript.{repo_b_sid}.jsonl"
        )
        fake_src = tmp_path / "shared-src.jsonl"
        fake_src.touch()

        proc = _spawn_fake_watcher(
            str(fake_src), repo_b_dest, repo_b_sid, tmp_path,
        )
        try:
            assert _proc_alive(proc.pid)

            # Repo A tries to kill its own (nonexistent) session.
            result = subprocess.run(
                [
                    "bash", str(KILL_SCRIPT),
                    str(repo_a), "repo-a-session",
                ],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stderr

            time.sleep(0.3)
            assert _proc_alive(proc.pid), (
                "kill script killed a watcher belonging to a different "
                "repo and session"
            )
        finally:
            _cleanup_proc(proc)

    def test_idempotent_when_nothing_to_kill(self, tmp_path: Path) -> None:
        """Running kill on a clean repo (no PID file, no processes) is a no-op."""
        (tmp_path / ".agent").mkdir()

        result = subprocess.run(
            ["bash", str(KILL_SCRIPT), str(tmp_path), "no-such-session"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# launch-transcript-sync.sh tests (per-session signature + orphan sweep)
# ---------------------------------------------------------------------------


class TestLaunchScript:
    def test_launch_starts_watcher_with_per_session_dest(
        self, tmp_path: Path,
    ) -> None:
        """launch-transcript-sync.sh launches sync-transcript.sh with the
        per-session DEST and PID file."""
        shared = _isolate_shared_scripts(tmp_path)
        launch_script = shared / "launch-transcript-sync.sh"
        kill_script = shared / "kill-transcript-sync.sh"

        agent_dir = tmp_path / ".agent"
        new_src = tmp_path / "new-src.jsonl"
        new_src.touch()
        session_id = "launch-test-A"

        try:
            env = os.environ.copy()
            env["REPO_ROOT"] = str(tmp_path)
            result = subprocess.run(
                [
                    "bash", str(launch_script),
                    str(new_src), session_id,
                ],
                capture_output=True, text=True, env=env,
            )
            assert result.returncode == 0, result.stderr

            # Wait for the watcher to register its per-session PID file
            pid_file = agent_dir / f".transcript-sync.{session_id}.pid"
            for _ in range(50):
                if pid_file.exists() and pid_file.read_text().strip():
                    break
                time.sleep(0.1)
            assert pid_file.exists(), "per-session PID file was not created"
        finally:
            subprocess.run(
                ["bash", str(kill_script), str(tmp_path), session_id],
                capture_output=True,
            )

    def test_launch_does_not_kill_sibling_session(
        self, tmp_path: Path,
    ) -> None:
        """launch-transcript-sync.sh starting session B must NOT kill a
        live sibling session A in the same repo. This is the structural
        fix for the watcher-leak failure documented in the 2026-04-08
        manual lifecycle test (Step 5)."""
        shared = _isolate_shared_scripts(tmp_path)
        launch_script = shared / "launch-transcript-sync.sh"
        kill_script = shared / "kill-transcript-sync.sh"

        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(exist_ok=True)

        # Session A's "live watcher" — fake but indistinguishable from a
        # real one to pgrep, with its per-session PID file recorded.
        a_sid = "session-A"
        a_dest = str(
            agent_dir / f".current_session_transcript.{a_sid}.jsonl"
        )
        a_src = tmp_path / "a-src.jsonl"
        a_src.touch()
        a_proc = _spawn_fake_watcher(str(a_src), a_dest, a_sid, tmp_path)
        (agent_dir / f".transcript-sync.{a_sid}.pid").write_text(
            str(a_proc.pid)
        )

        # Session B starts via the real launch script
        b_sid = "session-B"
        b_src = tmp_path / "b-src.jsonl"
        b_src.touch()

        try:
            assert _proc_alive(a_proc.pid)

            env = os.environ.copy()
            env["REPO_ROOT"] = str(tmp_path)
            result = subprocess.run(
                [
                    "bash", str(launch_script),
                    str(b_src), b_sid,
                ],
                capture_output=True, text=True, env=env,
            )
            assert result.returncode == 0, result.stderr

            # Session A's watcher MUST still be alive
            time.sleep(0.3)
            assert _proc_alive(a_proc.pid), (
                "session B's launch killed session A's still-live watcher "
                "— this is the watcher-leak regression"
            )

            # Session A's PID file must still be present
            assert (
                agent_dir / f".transcript-sync.{a_sid}.pid"
            ).exists(), (
                "session B's launch removed session A's PID file"
            )
        finally:
            _cleanup_proc(a_proc)
            subprocess.run(
                ["bash", str(kill_script), str(tmp_path), b_sid],
                capture_output=True,
            )

    def test_launch_kills_prior_watcher_for_same_session_id(
        self, tmp_path: Path,
    ) -> None:
        """Same-SID idempotence guard: re-running launch-transcript-sync.sh
        for a SESSION_ID that already has a live watcher must kill the prior
        watcher before launching a new one.

        Without this guard, Claude Code's SessionStart hook fires on
        every lifecycle event (startup, resume, clear, compact) and
        unconditionally calls launch-transcript-sync.sh — so a /compact
        mid-session would leave two watchers tailing the same source and
        each event would be filtered+appended twice consecutively. That
        produced the uniform 2x doubling pattern observed in fb7b3494
        and 3 other archived sessions (4/132 = ~3% HIT rate before fix).
        """
        shared = _isolate_shared_scripts(tmp_path)
        launch_script = shared / "launch-transcript-sync.sh"
        kill_script = shared / "kill-transcript-sync.sh"

        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(exist_ok=True)

        sid = "compaction-doubled-session"
        src = tmp_path / "src.jsonl"
        src.touch()
        dest_str = str(
            agent_dir / f".current_session_transcript.{sid}.jsonl"
        )
        prior = _spawn_fake_watcher(str(src), dest_str, sid, tmp_path)
        (agent_dir / f".transcript-sync.{sid}.pid").write_text(
            str(prior.pid)
        )

        try:
            assert _proc_alive(prior.pid)

            env = os.environ.copy()
            env["REPO_ROOT"] = str(tmp_path)
            result = subprocess.run(
                ["bash", str(launch_script), str(src), sid],
                capture_output=True, text=True, env=env,
            )
            assert result.returncode == 0, result.stderr

            assert _wait_proc_dead(prior, timeout=2.0), (
                "prior watcher for the same SESSION_ID must be killed "
                "before launching a replacement — otherwise both watchers "
                "tail the same source and each event lands 2x consecutively"
            )

            pid_file = agent_dir / f".transcript-sync.{sid}.pid"
            for _ in range(50):
                if pid_file.exists() and pid_file.read_text().strip():
                    break
                time.sleep(0.1)
            assert pid_file.exists(), (
                "new watcher did not register its per-session PID file"
            )
            new_pid = int(pid_file.read_text().strip())
            assert new_pid != prior.pid, (
                "PID file should reference the new watcher, not the dead one"
            )
        finally:
            _cleanup_proc(prior)
            subprocess.run(
                ["bash", str(kill_script), str(tmp_path), sid],
                capture_output=True,
            )

    def test_launch_kills_prior_via_pgrep_when_pid_file_missing(
        self, tmp_path: Path,
    ) -> None:
        """Defense-in-depth: even if the per-session PID file is missing
        (e.g., crashed mid-write, or manually clobbered), the same-SID
        guard's pgrep fallback finds prior watchers by SESSION_ID and
        kills them. Mirrors kill-transcript-sync.sh's two-phase pattern.
        """
        shared = _isolate_shared_scripts(tmp_path)
        launch_script = shared / "launch-transcript-sync.sh"
        kill_script = shared / "kill-transcript-sync.sh"

        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(exist_ok=True)

        sid = "no-pid-file-session"
        src = tmp_path / "src.jsonl"
        src.touch()
        dest_str = str(
            agent_dir / f".current_session_transcript.{sid}.jsonl"
        )
        prior = _spawn_fake_watcher(str(src), dest_str, sid, tmp_path)
        # Note: deliberately NOT writing the PID file.

        try:
            assert _proc_alive(prior.pid)

            env = os.environ.copy()
            env["REPO_ROOT"] = str(tmp_path)
            result = subprocess.run(
                ["bash", str(launch_script), str(src), sid],
                capture_output=True, text=True, env=env,
            )
            assert result.returncode == 0, result.stderr

            assert _wait_proc_dead(prior, timeout=2.0), (
                "pgrep fallback must find and kill the prior watcher by "
                "SESSION_ID even when the per-session PID file is absent"
            )
        finally:
            _cleanup_proc(prior)
            subprocess.run(
                ["bash", str(kill_script), str(tmp_path), sid],
                capture_output=True,
            )


# ---------------------------------------------------------------------------
# Crashed-session orphan sweep tests
# ---------------------------------------------------------------------------


class TestCrashedSessionOrphan:
    """The launch-time orphan sweep walks per-session PID files; for any
    PID that is dead, it archives the orphaned current file into
    ``.agent/.archived-transcripts/crashed-<stamp>-<sid>/`` and removes
    the stale PID file.

    These tests use a fake "dead session" — a PID file pointing to PID 1
    is fine because PID 1 is alive (init). Instead we use a known-dead
    PID by spawning a process and waiting for it to exit.
    """

    def _make_dead_pid(self) -> int:
        """Spawn a process, wait for it to exit, return its PID."""
        proc = subprocess.Popen(["true"])
        proc.wait(timeout=5)
        return proc.pid

    def test_orphan_sweep_archives_dead_session_files(
        self, tmp_path: Path,
    ) -> None:
        """An orphaned per-session current file with a dead PID file is
        archived to .archived-transcripts/crashed-<stamp>-<sid>/, and the
        PID file is removed."""
        shared = _isolate_shared_scripts(tmp_path)
        launch_script = shared / "launch-transcript-sync.sh"
        kill_script = shared / "kill-transcript-sync.sh"

        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(exist_ok=True)

        # Seed a "crashed" session: write a current file, an injection
        # history file, and a stale PID file pointing to a dead PID.
        crashed_sid = "crashed-session-X"
        crashed_dest = (
            agent_dir / f".current_session_transcript.{crashed_sid}.jsonl"
        )
        crashed_dest.write_text(
            '{"type":"user_message","content":"crashed session content"}\n'
        )
        crashed_inj = (
            agent_dir / f".current_injection_history.{crashed_sid}.jsonl"
        )
        crashed_inj.write_text('{"event_id":"crashed","selected":[]}\n')

        dead_pid = self._make_dead_pid()
        (agent_dir / f".transcript-sync.{crashed_sid}.pid").write_text(
            str(dead_pid)
        )

        # Now launch a different session — its orphan sweep should archive
        # the crashed session's files.
        live_sid = "live-session-Y"
        live_src = tmp_path / "live-src.jsonl"
        live_src.touch()

        try:
            env = os.environ.copy()
            env["REPO_ROOT"] = str(tmp_path)
            result = subprocess.run(
                [
                    "bash", str(launch_script),
                    str(live_src), live_sid,
                ],
                capture_output=True, text=True, env=env,
            )
            assert result.returncode == 0, result.stderr

            # Crashed session files should be gone from the live slot
            assert not crashed_dest.exists(), (
                "crashed session's current_session_transcript was not "
                "archived/removed"
            )
            assert not crashed_inj.exists(), (
                "crashed session's current_injection_history was not "
                "archived/removed"
            )
            assert not (
                agent_dir / f".transcript-sync.{crashed_sid}.pid"
            ).exists(), (
                "stale PID file was not removed"
            )

            # Archive directory should contain a crashed-* subdir with
            # the gzipped content.
            archive_dir = agent_dir / ".archived-transcripts"
            assert archive_dir.exists(), "no archive dir was created"
            crashed_subdirs = [
                d for d in archive_dir.iterdir()
                if d.name.startswith("crashed-")
            ]
            assert len(crashed_subdirs) >= 1, (
                f"no crashed-* archive subdir found in "
                f"{[d.name for d in archive_dir.iterdir()]}"
            )
            transcript_gz = crashed_subdirs[0] / "transcript.jsonl.gz"
            inj_gz = crashed_subdirs[0] / "injection_history.jsonl.gz"
            assert transcript_gz.exists()
            assert inj_gz.exists()
            with gzip.open(transcript_gz, "rt") as f:
                assert "crashed session content" in f.read()
        finally:
            subprocess.run(
                ["bash", str(kill_script), str(tmp_path), live_sid],
                capture_output=True,
            )

    def test_orphan_sweep_leaves_live_sibling_alone(
        self, tmp_path: Path,
    ) -> None:
        """A per-session PID file whose PID is ALIVE is left alone — that's
        a live sibling session, not an orphan."""
        shared = _isolate_shared_scripts(tmp_path)
        launch_script = shared / "launch-transcript-sync.sh"
        kill_script = shared / "kill-transcript-sync.sh"

        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(exist_ok=True)

        # Live sibling session
        sibling_sid = "live-sibling"
        sibling_dest = (
            agent_dir / f".current_session_transcript.{sibling_sid}.jsonl"
        )
        sibling_dest.write_text(
            '{"type":"user_message","content":"sibling alive"}\n'
        )
        sibling_proc = subprocess.Popen(["sleep", "60"])
        (
            agent_dir / f".transcript-sync.{sibling_sid}.pid"
        ).write_text(str(sibling_proc.pid))

        # Launch a different session
        new_sid = "new-session"
        new_src = tmp_path / "new-src.jsonl"
        new_src.touch()

        try:
            env = os.environ.copy()
            env["REPO_ROOT"] = str(tmp_path)
            result = subprocess.run(
                [
                    "bash", str(launch_script),
                    str(new_src), new_sid,
                ],
                capture_output=True, text=True, env=env,
            )
            assert result.returncode == 0, result.stderr

            # Sibling files must NOT have been touched
            assert sibling_dest.exists(), (
                "live sibling's current file was wrongly archived"
            )
            assert (
                agent_dir / f".transcript-sync.{sibling_sid}.pid"
            ).exists(), (
                "live sibling's PID file was wrongly removed"
            )
            # Sibling process must still be alive
            assert _proc_alive(sibling_proc.pid)
        finally:
            _cleanup_proc(sibling_proc)
            subprocess.run(
                ["bash", str(kill_script), str(tmp_path), new_sid],
                capture_output=True,
            )


# ---------------------------------------------------------------------------
# rotate-on-session-end.sh tests
# ---------------------------------------------------------------------------


class TestRotateOnSessionEnd:
    """rotate-on-session-end.sh promotes a just-ended session's per-session
    current files into the global .last_*/.second_to_last_* slots, archiving
    the displaced .second_to_last_* into .archived-transcripts/<UTC-stamp>/.
    """

    def test_rotate_promotes_current_to_last(self, tmp_path: Path) -> None:
        """First session ever to end: per-session current files become
        the global .last_* files."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()

        sid = "session-first"
        cur_tr = agent_dir / f".current_session_transcript.{sid}.jsonl"
        cur_tr.write_text('{"type":"user_message","content":"hello"}\n')
        cur_inj = agent_dir / f".current_injection_history.{sid}.jsonl"
        cur_inj.write_text('{"event_id":"e1","selected":[]}\n')

        result = subprocess.run(
            ["bash", str(ROTATE_SCRIPT), str(tmp_path), sid],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

        last_tr = agent_dir / ".last_session_transcript.jsonl"
        last_inj = agent_dir / ".last_injection_history.jsonl"
        assert last_tr.exists()
        assert last_inj.exists()
        assert "hello" in last_tr.read_text()
        assert "e1" in last_inj.read_text()
        # Per-session files should be gone after rotation
        assert not cur_tr.exists()
        assert not cur_inj.exists()
        # No second_to_last yet (no prior content to demote)
        assert not (agent_dir / ".second_to_last_transcript.jsonl").exists()
        # No archive dir yet
        assert not (agent_dir / ".archived-transcripts").exists()

    def test_rotate_demotes_old_last_to_second_to_last(
        self, tmp_path: Path,
    ) -> None:
        """When .last_* exists, rotation demotes it to .second_to_last_*
        before promoting the new session's current."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()

        # Pre-existing .last_* (from a previous session-end)
        old_last_tr = agent_dir / ".last_session_transcript.jsonl"
        old_last_tr.write_text(
            '{"type":"user_message","content":"old last"}\n'
        )
        old_last_inj = agent_dir / ".last_injection_history.jsonl"
        old_last_inj.write_text('{"event_id":"old","selected":[]}\n')

        # New session's current
        sid = "session-second"
        cur_tr = agent_dir / f".current_session_transcript.{sid}.jsonl"
        cur_tr.write_text('{"type":"user_message","content":"new"}\n')

        result = subprocess.run(
            ["bash", str(ROTATE_SCRIPT), str(tmp_path), sid],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

        # .last_* now contains the new session
        last_tr = agent_dir / ".last_session_transcript.jsonl"
        assert last_tr.exists() and "new" in last_tr.read_text()

        # .second_to_last_* now contains the old .last
        second_tr = agent_dir / ".second_to_last_transcript.jsonl"
        assert second_tr.exists() and "old last" in second_tr.read_text()

        # Old .last_injection_history was also demoted
        second_inj = agent_dir / ".second_to_last_injection_history.jsonl"
        assert second_inj.exists() and "old" in second_inj.read_text()

    def test_rotate_archives_old_second_to_last(
        self, tmp_path: Path,
    ) -> None:
        """When .second_to_last_* already exists, rotation archives it
        (gzipped, mtime preserved) before the demote/promote chain."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()

        old_second = agent_dir / ".second_to_last_transcript.jsonl"
        old_second.write_text(
            '{"type":"user_message","content":"two sessions ago"}\n'
        )
        backdate = time.time() - 7200
        os.utime(old_second, (backdate, backdate))
        original_mtime = old_second.stat().st_mtime

        old_second_inj = agent_dir / ".second_to_last_injection_history.jsonl"
        old_second_inj.write_text(
            '{"event_id":"two-ago","selected":[]}\n'
        )

        sid = "session-third"
        cur_tr = agent_dir / f".current_session_transcript.{sid}.jsonl"
        cur_tr.write_text('{"type":"user_message","content":"current"}\n')

        result = subprocess.run(
            ["bash", str(ROTATE_SCRIPT), str(tmp_path), sid],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

        archive_dir = agent_dir / ".archived-transcripts"
        assert archive_dir.exists()
        subdirs = [
            d for d in archive_dir.iterdir() if not d.name.startswith("crashed-")
        ]
        assert len(subdirs) == 1
        archive_subdir = subdirs[0]
        assert archive_subdir.name.startswith("20")

        archived_tr = archive_subdir / "transcript.jsonl.gz"
        archived_inj = archive_subdir / "injection_history.jsonl.gz"
        assert archived_tr.exists()
        assert archived_inj.exists()

        with gzip.open(archived_tr, "rt") as f:
            assert "two sessions ago" in f.read()
        with gzip.open(archived_inj, "rt") as f:
            assert "two-ago" in f.read()

        # mtime preserved (allow 1.5s slack for filesystem granularity)
        archived_mtime = archived_tr.stat().st_mtime
        assert abs(archived_mtime - original_mtime) < 1.5

    def test_rotate_skips_empty_current(self, tmp_path: Path) -> None:
        """If the per-session current file is empty (or missing), the
        rotation runs but does NOT promote an empty file into .last_*.
        The prior .last_* survives unchanged."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()

        # Pre-existing .last_*
        old_last = agent_dir / ".last_session_transcript.jsonl"
        old_last.write_text(
            '{"type":"user_message","content":"prior survives"}\n'
        )

        # Session whose current file is empty (e.g., user opened CLI but
        # never sent a message)
        sid = "empty-session"
        cur_tr = agent_dir / f".current_session_transcript.{sid}.jsonl"
        cur_tr.touch()

        result = subprocess.run(
            ["bash", str(ROTATE_SCRIPT), str(tmp_path), sid],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

        # .last_* should now hold an EMPTY file (because the empty current
        # was demoted-then-promoted)... wait, no — the empty current file
        # was rejected, so the old .last was demoted to .second_to_last
        # and .last_* is empty/missing. Verify the actual behavior:
        last_tr = agent_dir / ".last_session_transcript.jsonl"
        second_tr = agent_dir / ".second_to_last_transcript.jsonl"

        # Old last got demoted to second_to_last
        assert second_tr.exists()
        assert "prior survives" in second_tr.read_text()
        # New .last_ does NOT exist (empty current was not promoted)
        assert not last_tr.exists()
        # The empty current file is gone
        assert not cur_tr.exists()

    def test_rotate_cleans_up_per_session_state_files(
        self, tmp_path: Path,
    ) -> None:
        """Rotation removes the per-session PID, state, poll-state, and
        injection-state files for the ending session."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()

        sid = "session-cleanup"
        cur_tr = agent_dir / f".current_session_transcript.{sid}.jsonl"
        cur_tr.write_text('{"type":"x"}\n')

        # Create all the per-session state files
        state_files = [
            f".transcript-sync.{sid}.pid",
            f".transcript-sync-state.{sid}.json",
            f".transcript-poll-state.{sid}",
            f".transcript-injection-state.{sid}.json",
        ]
        for name in state_files:
            (agent_dir / name).write_text("{}")

        result = subprocess.run(
            ["bash", str(ROTATE_SCRIPT), str(tmp_path), sid],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

        for name in state_files:
            assert not (agent_dir / name).exists(), (
                f"per-session state file {name} was not cleaned up"
            )

    def test_concurrent_rotations_serialize(self, tmp_path: Path) -> None:
        """Two rotate-on-session-end.sh processes started simultaneously
        for two different sessions must both complete without corruption.
        The flock around the rotation critical section ensures the global
        slots end up in a consistent state — last writer wins .last_*."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()

        sid_a = "concurrent-A"
        sid_b = "concurrent-B"
        cur_a = agent_dir / f".current_session_transcript.{sid_a}.jsonl"
        cur_b = agent_dir / f".current_session_transcript.{sid_b}.jsonl"
        cur_a.write_text('{"type":"user_message","content":"A wins"}\n')
        cur_b.write_text('{"type":"user_message","content":"B wins"}\n')

        # Start both rotation processes simultaneously
        proc_a = subprocess.Popen(
            ["bash", str(ROTATE_SCRIPT), str(tmp_path), sid_a],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        proc_b = subprocess.Popen(
            ["bash", str(ROTATE_SCRIPT), str(tmp_path), sid_b],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        proc_a.wait(timeout=10)
        proc_b.wait(timeout=10)

        assert proc_a.returncode == 0, proc_a.stderr.read()
        assert proc_b.returncode == 0, proc_b.stderr.read()

        # Both per-session current files are gone
        assert not cur_a.exists()
        assert not cur_b.exists()

        # Exactly one session's content is in .last_*, the other in
        # .second_to_last_*. (Which one wins depends on which acquired
        # the flock first; both outcomes are valid.)
        last_tr = agent_dir / ".last_session_transcript.jsonl"
        second_tr = agent_dir / ".second_to_last_transcript.jsonl"
        assert last_tr.exists()
        assert second_tr.exists()

        last_content = last_tr.read_text()
        second_content = second_tr.read_text()
        assert ("A wins" in last_content and "B wins" in second_content) or (
            "B wins" in last_content and "A wins" in second_content
        ), (
            f"unexpected rotation outcome:\n"
            f"last={last_content!r}\nsecond={second_content!r}"
        )


# ---------------------------------------------------------------------------
# sync-transcript.sh per-session PID file tests
# ---------------------------------------------------------------------------


class TestSyncTranscript:
    def test_writes_per_session_pid_file(self, tmp_path: Path) -> None:
        """sync-transcript.sh writes its PID into a per-session PID file
        whose path encodes SESSION_ID."""
        shared = _isolate_shared_scripts(tmp_path)
        sync_script = shared / "sync-transcript.sh"
        agent_dir = tmp_path / ".agent"

        src = tmp_path / "src.jsonl"
        src.write_text("")
        sid = "sync-test-A"
        dest = agent_dir / f".current_session_transcript.{sid}.jsonl"
        pid_file = agent_dir / f".transcript-sync.{sid}.pid"

        stderr_log = tmp_path / "sync-stderr.log"
        with open(stderr_log, "wb") as err:
            proc = subprocess.Popen(
                ["bash", str(sync_script), str(src), str(dest), sid],
                stdout=subprocess.DEVNULL,
                stderr=err,
            )
        try:
            for _ in range(50):
                if pid_file.exists() and pid_file.read_text().strip():
                    break
                time.sleep(0.1)
            assert pid_file.exists(), (
                f"per-session PID file was not created. "
                f"stderr: {stderr_log.read_text()!r}"
            )
            assert pid_file.read_text().strip() == str(proc.pid)
        finally:
            _cleanup_proc(proc)

    def test_exit_trap_only_removes_own_pid_file(
        self, tmp_path: Path,
    ) -> None:
        """sync-transcript.sh's EXIT trap must NOT delete the PID file
        when the file contains a PID other than the watcher's own.

        Per-session PID files make this race vanishingly rare, but the
        guard remains as defense-in-depth.
        """
        shared = _isolate_shared_scripts(tmp_path)
        sync_script = shared / "sync-transcript.sh"
        agent_dir = tmp_path / ".agent"

        src = tmp_path / "src.jsonl"
        src.write_text("")
        sid = "sync-test-trap"
        dest = agent_dir / f".current_session_transcript.{sid}.jsonl"
        pid_file = agent_dir / f".transcript-sync.{sid}.pid"

        stderr_log = tmp_path / "sync-stderr.log"
        with open(stderr_log, "wb") as err:
            proc = subprocess.Popen(
                ["bash", str(sync_script), str(src), str(dest), sid],
                stdout=subprocess.DEVNULL,
                stderr=err,
            )
        try:
            for _ in range(50):
                if pid_file.exists() and pid_file.read_text().strip():
                    break
                time.sleep(0.1)
            assert pid_file.exists()
            assert pid_file.read_text().strip() == str(proc.pid)

            # Simulate someone clobbering our PID file with a foreign PID
            other_pid = "99999"
            pid_file.write_text(other_pid)

            proc.terminate()
            proc.wait(timeout=5)

            time.sleep(0.2)
            assert pid_file.exists(), (
                "EXIT trap clobbered another session's PID file"
            )
            assert pid_file.read_text().strip() == other_pid
        finally:
            _cleanup_proc(proc)
            if pid_file.exists() and pid_file.read_text().strip() == "99999":
                pid_file.unlink()

    def _fake_inotifywait(self, tmp_path: Path, counter: Path) -> Path:
        """A stand-in ``inotifywait`` that always fails to initialise.

        Reproduces the production failure exactly:
        ``Couldn't initialize inotify: Too many open files`` — exit 1,
        immediately, with no event and no delay.
        """
        bindir = tmp_path / "fakebin"
        bindir.mkdir(exist_ok=True)
        fake = bindir / "inotifywait"
        fake.write_text(
            "#!/bin/bash\n"
            f"echo x >> {counter}\n"
            "echo \"Couldn't initialize inotify: Too many open files\" >&2\n"
            "exit 1\n",
        )
        fake.chmod(0o755)
        return bindir

    def test_inotify_failure_backs_off_instead_of_busy_looping(
        self, tmp_path: Path,
    ) -> None:
        """When ``inotifywait`` cannot start, the watcher must BACK OFF.

        Observed in production: 128/128 ``fs.inotify.max_user_instances``
        were held by unrelated processes (369 stray ``dbus-daemon``s), so
        every ``inotifywait`` failed instantly. The watch loop's error path
        ran ``continue`` with NO delay, so watchers spun at ~16% CPU each —
        one logged 114 million voluntary context switches and 13.9 hours of
        CPU. A resource shortage has to degrade, not melt down.

        Bounded by INVOCATION COUNT rather than by CPU%, because a CPU
        threshold is a race on a loaded box; the count is the mechanism.
        """
        shared = _isolate_shared_scripts(tmp_path)
        sync_script = shared / "sync-transcript.sh"
        agent_dir = tmp_path / ".agent"
        counter = tmp_path / "invocations.txt"
        counter.write_text("")
        bindir = self._fake_inotifywait(tmp_path, counter)

        src = tmp_path / "src.jsonl"
        src.write_text('{"type":"user","message":"hello"}\n')
        sid = "sync-backoff"
        dest = agent_dir / f".current_session_transcript.{sid}.jsonl"

        env = os.environ.copy()
        env["PATH"] = f"{bindir}:{env['PATH']}"
        proc = subprocess.Popen(
            ["bash", str(sync_script), str(src), str(dest), sid],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
        )
        try:
            time.sleep(3)
            calls = len(counter.read_text().split())
            assert calls <= 20, (
                f"inotifywait invoked {calls} times in 3s — that is the "
                f"busy-loop. The error path must sleep before retrying."
            )
        finally:
            _cleanup_proc(proc)

    def test_mirror_still_updates_when_inotify_is_unavailable(
        self, tmp_path: Path,
    ) -> None:
        """Backing off must not mean going deaf.

        The pre-fix error path ``continue``d without calling ``do_sync``, so
        while inotify was exhausted the mirror silently stopped updating —
        the watcher burned CPU AND published nothing. Degraded mode has to
        keep mirroring, just less promptly.
        """
        shared = _isolate_shared_scripts(tmp_path)
        sync_script = shared / "sync-transcript.sh"
        agent_dir = tmp_path / ".agent"
        counter = tmp_path / "invocations.txt"
        counter.write_text("")
        bindir = self._fake_inotifywait(tmp_path, counter)

        src = tmp_path / "src.jsonl"
        src.write_text('{"type":"user","message":"first"}\n')
        sid = "sync-degraded"
        dest = agent_dir / f".current_session_transcript.{sid}.jsonl"

        env = os.environ.copy()
        env["PATH"] = f"{bindir}:{env['PATH']}"
        proc = subprocess.Popen(
            ["bash", str(sync_script), str(src), str(dest), sid],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
        )
        try:
            for _ in range(50):
                if dest.exists() and dest.read_text().strip():
                    break
                time.sleep(0.1)
            assert dest.exists() and "first" in dest.read_text(), (
                "initial sync did not happen"
            )

            with open(src, "a", encoding="utf-8") as fh:
                fh.write('{"type":"user","message":"second"}\n')

            for _ in range(80):
                if "second" in dest.read_text():
                    break
                time.sleep(0.25)
            assert "second" in dest.read_text(), (
                "mirror never picked up the appended line while inotify was "
                "unavailable — the watcher went deaf instead of polling"
            )
        finally:
            _cleanup_proc(proc)


# ---------------------------------------------------------------------------
# A watcher must not outlive the session that launched it (2026-09-05).
#
# Two orphans were found live on the production box after a session crash:
# an ``inotifywait -e close_write`` with PPID 1, 50 hours old, waiting on a
# transcript that would never be written again (its bash parent had been
# killed and, having no ``-t``, the child never returned); and a whole
# sync-transcript.sh, 26 hours old, looping in Phase 1 for a source file that
# never appeared, because the session-end hook that would have killed it
# never ran. Each held one of the 128 per-user inotify instances. The fix has
# three parts and each is pinned below: every watch is time-bounded, a
# trapped signal reaps the child before exiting, and the watcher checks that
# its OWNER process is still alive on every loop iteration.
# ---------------------------------------------------------------------------


def _fake_inotifywait_timeout(tmp_path: Path, log: Path) -> Path:
    """A stand-in ``inotifywait`` whose every call records its argv, waits a
    little, and exits 2 — the code real inotifywait returns when ``-t`` fires."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    fake = bindir / "inotifywait"
    fake.write_text(
        "#!/bin/bash\n"
        f"echo \"$*\" >> {log}\n"
        "sleep 0.2\n"
        "exit 2\n",
    )
    fake.chmod(0o755)
    return bindir


def _fake_inotifywait_blocking(tmp_path: Path, child_pid_file: Path) -> Path:
    """A stand-in ``inotifywait`` that records its PID and then blocks for a
    long time, like a real watch on a file nobody writes to."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    fake = bindir / "inotifywait"
    fake.write_text(
        "#!/bin/bash\n"
        f"echo $$ > {child_pid_file}\n"
        "exec sleep 30\n",
    )
    fake.chmod(0o755)
    return bindir


def _wait_for_file(path: Path, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists() and path.read_text().strip():
            return True
        time.sleep(0.05)
    return False


class TestWatcherOutlivesSession:
    def _start_watcher(
        self, tmp_path: Path, bindir: Path, *, src_exists: bool = True,
        extra_env: dict[str, str] | None = None, sid: str = "owner-test",
    ) -> tuple[subprocess.Popen, Path]:
        shared = _isolate_shared_scripts(tmp_path)
        agent_dir = tmp_path / ".agent"
        src = tmp_path / "src.jsonl"
        if src_exists:
            src.write_text('{"type":"user","message":"hello"}\n')
        dest = agent_dir / f".current_session_transcript.{sid}.jsonl"
        env = os.environ.copy()
        env["PATH"] = f"{bindir}:{env['PATH']}"
        env.pop("TRANSCRIPT_OWNER_PID", None)
        env.update(extra_env or {})
        proc = subprocess.Popen(
            ["bash", str(shared / "sync-transcript.sh"), str(src), str(dest), sid],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
        )
        return proc, src

    def test_phase2_watch_is_time_bounded(self, tmp_path: Path) -> None:
        """The close_write watch must carry ``-t``: a watcher that is SIGKILLed
        (or whose bash parent dies for any reason) then orphans a child that
        returns within the bound instead of never. The bound is configurable
        so this test does not wait a minute."""
        log = tmp_path / "argv.log"
        log.write_text("")
        bindir = _fake_inotifywait_timeout(tmp_path, log)
        proc, src = self._start_watcher(
            tmp_path, bindir, extra_env={"TRANSCRIPT_WATCH_TIMEOUT": "7"},
        )
        try:
            for _ in range(60):
                lines = [ln for ln in log.read_text().splitlines() if str(src) in ln]
                if len(lines) >= 2:
                    break
                time.sleep(0.1)
            assert lines, "Phase-2 inotifywait never invoked on the source file"
            for ln in lines:
                argv = ln.split()
                assert "-t" in argv and argv[argv.index("-t") + 1] == "7", (
                    f"close_write watch is unbounded: {ln!r}"
                )
        finally:
            _cleanup_proc(proc)

    def test_sigterm_reaps_the_child_watch(self, tmp_path: Path) -> None:
        """SIGTERM to the watcher must take the blocked ``inotifywait`` down
        with it, promptly. The 50-hour orphan was exactly a child that
        survived its parent's SIGTERM."""
        child_pid_file = tmp_path / "child.pid"
        bindir = _fake_inotifywait_blocking(tmp_path, child_pid_file)
        proc, _ = self._start_watcher(tmp_path, bindir)
        try:
            assert _wait_for_file(child_pid_file), "child watch never started"
            child_pid = int(child_pid_file.read_text().strip())
            assert _proc_alive(child_pid)
            proc.terminate()
            assert _wait_proc_dead(proc, 3.0), "watcher ignored SIGTERM"
            assert _wait_dead(child_pid, 3.0), (
                "child inotifywait outlived its parent — this is the orphan"
            )
        finally:
            _cleanup_proc(proc)
            if child_pid_file.exists():
                try:
                    os.kill(int(child_pid_file.read_text().strip()), 9)
                except (OSError, ValueError):
                    pass

    def test_exits_when_owner_dies_in_phase2(self, tmp_path: Path) -> None:
        """With TRANSCRIPT_OWNER_PID set, the watcher exits on its own within
        one watch timeout of the owner disappearing — no session-end hook
        required. This is the case the crash left behind."""
        owner = subprocess.Popen(["sleep", "30"])
        log = tmp_path / "argv.log"
        log.write_text("")
        bindir = _fake_inotifywait_timeout(tmp_path, log)
        proc, _ = self._start_watcher(
            tmp_path, bindir,
            extra_env={"TRANSCRIPT_OWNER_PID": str(owner.pid), "TRANSCRIPT_WATCH_TIMEOUT": "1"},
        )
        try:
            time.sleep(0.7)
            assert _proc_alive(proc.pid), "watcher died while its owner was alive"
            owner.kill()
            owner.wait(timeout=2)
            assert _wait_proc_dead(proc, 4.0), (
                "watcher kept running after its owning session died"
            )
            assert proc.returncode == 0
        finally:
            _cleanup_proc(proc)
            _cleanup_proc(owner)

    def test_exits_when_owner_dies_in_phase1(self, tmp_path: Path) -> None:
        """Phase 1 (source not yet created) must also watch the owner. The
        26-hour orphan was a Phase-1 loop for a file that never appeared."""
        owner = subprocess.Popen(["sleep", "30"])
        log = tmp_path / "argv.log"
        log.write_text("")
        bindir = _fake_inotifywait_timeout(tmp_path, log)
        proc, src = self._start_watcher(
            tmp_path, bindir, src_exists=False,
            extra_env={"TRANSCRIPT_OWNER_PID": str(owner.pid)},
        )
        try:
            time.sleep(0.7)
            assert not src.exists()
            assert _proc_alive(proc.pid), "watcher died while its owner was alive"
            owner.kill()
            owner.wait(timeout=2)
            assert _wait_proc_dead(proc, 4.0), (
                "Phase-1 watcher kept waiting after its owning session died"
            )
        finally:
            _cleanup_proc(proc)
            _cleanup_proc(owner)

    def test_refuses_to_start_for_a_dead_owner(self, tmp_path: Path) -> None:
        dead = subprocess.Popen(["true"])
        dead.wait(timeout=2)
        log = tmp_path / "argv.log"
        log.write_text("")
        bindir = _fake_inotifywait_timeout(tmp_path, log)
        proc, _ = self._start_watcher(
            tmp_path, bindir, extra_env={"TRANSCRIPT_OWNER_PID": str(dead.pid)},
        )
        try:
            assert _wait_proc_dead(proc, 3.0)
            assert proc.returncode == 0
        finally:
            _cleanup_proc(proc)

    def test_no_owner_means_legacy_behaviour(self, tmp_path: Path) -> None:
        """Without an owner PID the watcher must still run (other vendors'
        hooks may not be able to name one)."""
        log = tmp_path / "argv.log"
        log.write_text("")
        bindir = _fake_inotifywait_timeout(tmp_path, log)
        proc, _ = self._start_watcher(tmp_path, bindir)
        try:
            time.sleep(1.0)
            assert _proc_alive(proc.pid)
        finally:
            _cleanup_proc(proc)


class TestLauncherNamesTheOwner:
    def _launch(
        self, tmp_path: Path, env_overrides: dict[str, str], drop: tuple[str, ...],
    ) -> tuple[int, dict[str, str]]:
        shared = _isolate_shared_scripts(tmp_path)
        log = tmp_path / "argv.log"
        log.write_text("")
        bindir = _fake_inotifywait_timeout(tmp_path, log)
        src = tmp_path / "src.jsonl"
        src.write_text('{"type":"user","message":"hello"}\n')
        sid = "launch-owner"
        env = os.environ.copy()
        env["PATH"] = f"{bindir}:{env['PATH']}"
        env["REPO_ROOT"] = str(tmp_path)
        for k in drop:
            env.pop(k, None)
        env.update(env_overrides)
        result = subprocess.run(
            ["bash", str(shared / "launch-transcript-sync.sh"), str(src), sid],
            capture_output=True, text=True, env=env, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        pid_file = tmp_path / ".agent" / f".transcript-sync.{sid}.pid"
        assert _wait_for_file(pid_file), "launcher did not start a watcher"
        wpid = int(pid_file.read_text().strip())
        environ = Path(f"/proc/{wpid}/environ").read_bytes().split(b"\0")
        wenv = dict(e.decode(errors="replace").split("=", 1) for e in environ if b"=" in e)
        return wpid, wenv

    def test_owner_pid_comes_from_the_harness_variable(self, tmp_path: Path) -> None:
        """Claude Code exports CLAUDE_PID to hooks; the launcher must hand it to
        the watcher as TRANSCRIPT_OWNER_PID."""
        owner = subprocess.Popen(["sleep", "30"])
        wpid = None
        try:
            wpid, wenv = self._launch(
                tmp_path, {"CLAUDE_PID": str(owner.pid)}, drop=("TRANSCRIPT_OWNER_PID",),
            )
            assert wenv.get("TRANSCRIPT_OWNER_PID") == str(owner.pid)
        finally:
            _cleanup_proc(owner)
            if wpid and _proc_alive(wpid):
                os.kill(wpid, 15)
                _wait_dead(wpid, 3.0)

    def test_owner_pid_falls_back_to_first_non_shell_ancestor(self, tmp_path: Path) -> None:
        """Vendors that export no PID still get an owner: the first ancestor of
        the launcher that is not a shell. From this test that is the test
        process itself."""
        wpid = None
        try:
            wpid, wenv = self._launch(
                tmp_path, {}, drop=("TRANSCRIPT_OWNER_PID", "CLAUDE_PID"),
            )
            assert wenv.get("TRANSCRIPT_OWNER_PID") == str(os.getpid())
        finally:
            if wpid and _proc_alive(wpid):
                os.kill(wpid, 15)
                _wait_dead(wpid, 3.0)


class TestKillScriptReapsChildren:
    def test_kill_script_takes_the_child_watch_down_too(self, tmp_path: Path) -> None:
        """A pre-fix watcher whose bash parent is killed leaves its foreground
        ``inotifywait`` alive with PPID 1. The kill script must signal the
        watcher's children BEFORE the watcher, while they are still its
        children."""
        shared = _isolate_shared_scripts(tmp_path)
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(exist_ok=True)
        sid = "kill-children"
        child_pid_file = tmp_path / "child.pid"
        fake_dir = tmp_path / "fake-bin"
        fake_dir.mkdir(exist_ok=True)
        fake = fake_dir / "sync-transcript.sh"
        fake.write_text(
            "#!/bin/bash\n"
            "sleep 60 &\n"
            f"echo $! > {child_pid_file}\n"
            "wait\n",
        )
        fake.chmod(0o755)
        proc = subprocess.Popen(
            [str(fake), str(tmp_path / "src"), str(tmp_path / "dest"), sid],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            assert _wait_for_file(child_pid_file)
            child_pid = int(child_pid_file.read_text().strip())
            (agent_dir / f".transcript-sync.{sid}.pid").write_text(str(proc.pid))
            result = subprocess.run(
                ["bash", str(shared / "kill-transcript-sync.sh"), str(tmp_path), sid],
                capture_output=True, text=True, timeout=30,
            )
            assert result.returncode == 0, result.stderr
            assert _wait_proc_dead(proc, 3.0)
            assert _wait_dead(child_pid, 3.0), "kill script orphaned the child"
        finally:
            _cleanup_proc(proc)
            if child_pid_file.exists():
                try:
                    os.kill(int(child_pid_file.read_text().strip()), 9)
                except (OSError, ValueError):
                    pass
