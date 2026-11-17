# SPDX-License-Identifier: AGPL-3.0-or-later
"""A session hook returns before the slow transcript work, not after it.

The defect these tests pin: a vendor hook runs under a short budget (Claude
Code: 5 s) and is killed when it overruns. Rotation and the start-time sweeps
compress, validate and secret-scrub whole transcripts, which reach hundreds
of megabytes. Measured on a 395 MB slot, the session-end hook was cut off at
5.0 s at two consecutive session ends ("SessionEnd hook ... failed: Hook
cancelled"), so the ``.last_*`` slots stopped rotating; the next session
start then tried to archive the un-rotated transcript BEFORE launching its
watcher, was cut off too, and ran with no watcher at all.

The cure under test: a hook does O(1) work (queue entry, kill or start the
watcher) and hands the rest to a detached worker that runs under the
rotation lock (``transcript_rotation_queue.sh``).

How slowness is produced: the sandbox's ``scrub_secrets.py`` is replaced by
a stub that sleeps before passing the content through, so "slow archive" is
a property of the fixture and not of the machine. The ordering assertions
(hook returned WHILE the stub is still sleeping) are what fail on the
synchronous design; no assertion depends on an absolute wall-clock bound
shorter than the stub's sleep.

Safety: session ids are unique per test (the kill script's ``pgrep`` fallback
matches any ``sync-transcript.sh`` on the machine by session id).
"""

# covers: .agent/hooks/_shared/*transcript*, .agent/hooks/_shared/rotate-on-session-end.sh, .agent/hooks/*/session-end.sh

from __future__ import annotations

import fcntl
import gzip
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Callable

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = REPO_ROOT / ".agent" / "hooks"
VENDORS = ("claude-code", "codex-cli", "gemini-cli", "cursor")

# Seconds the stub scrubber sleeps per archive. Long enough that a hook
# returning "while it sleeps" is unambiguous under a loaded test run.
SLOW_SECONDS = 4

_SLOW_SCRUB = f'''#!/usr/bin/env python3
"""Test stub for scrub_secrets.py: slow in archive (stdout) mode."""
import sys
import time
from pathlib import Path

args = sys.argv[1:]
if "--in-place" in args:
    sys.exit(0)
marker = Path(__file__).with_name("slow-scrub-started")
marker.write_text("started")
time.sleep({SLOW_SECONDS})
sys.stdout.write(Path(args[-1]).read_text())
'''


def _sid(base: str) -> str:
    return f"{base}-{uuid.uuid4().hex[:12]}"


def _line(text: str) -> str:
    return json.dumps({"type": "user_message", "content": text}) + "\n"


def _sandbox(tmp_path: Path, *, slow: bool = False) -> Path:
    """A repo-shaped sandbox holding a copy of every hook script."""
    repo = tmp_path / "repo"
    shutil.copytree(
        HOOKS_DIR, repo / ".agent" / "hooks",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    if slow:
        (repo / ".agent" / "hooks" / "_shared" / "scrub_secrets.py").write_text(
            _SLOW_SCRUB
        )
    return repo


def _shared(repo: Path) -> Path:
    return repo / ".agent" / "hooks" / "_shared"


def _run(script: Path, *args: str, repo: Path, stdin: str = "",
         env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), *args],
        input=stdin, capture_output=True, text=True, cwd=str(repo),
        env={**os.environ, "REPO_ROOT": str(repo), **(env or {})},
        timeout=60,
    )


def _wait_for(predicate: Callable[[], bool], timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _queue(repo: Path) -> list[str]:
    qdir = repo / ".agent" / ".rotation-queue"
    return sorted(p.name for p in qdir.iterdir()) if qdir.is_dir() else []


def _sweep_done(repo: Path, sid: str) -> bool:
    log = repo / ".agent" / ".transcript-rotation.log"
    return log.is_file() and f"sweep done session={sid}" in log.read_text()


def _kill_watcher(repo: Path, sid: str) -> None:
    _run(_shared(repo) / "kill-transcript-sync.sh", str(repo), sid, repo=repo)


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait(timeout=5)
    return proc.pid


# --- session end ----------------------------------------------------------


class TestSessionEndHookBudget:
    def test_hook_returns_while_the_archive_is_still_running(
        self, tmp_path: Path,
    ) -> None:
        """The production path: the Claude Code session-end hook, with a
        ``.second_to_last`` slot whose archive is slow. The hook must return
        before that archive finishes, and the rotation must still complete."""
        repo = _sandbox(tmp_path, slow=True)
        agent = repo / ".agent"
        sid = _sid("ending")
        (agent / ".second_to_last_transcript.jsonl").write_text(_line("oldest"))
        (agent / ".last_session_transcript.jsonl").write_text(_line("older"))
        current = agent / f".current_session_transcript.{sid}.jsonl"
        current.write_text(_line("just ended"))
        started = _shared(repo) / "slow-scrub-started"

        began = time.time()
        proc = _run(
            repo / ".agent" / "hooks" / "claude-code" / "session-end.sh",
            repo=repo, stdin=json.dumps({"session_id": sid}),
        )
        elapsed = time.time() - began
        assert proc.returncode == 0, proc.stderr

        # Reach first: the slow archive really is part of this rotation.
        assert _wait_for(started.exists, timeout=10), (
            "the slow archive never started: the fixture does not reach the "
            "code under test"
        )
        # The hook is back while the archive is still asleep: the rotation
        # is not finished, so the session's file has not been promoted yet.
        assert elapsed < SLOW_SECONDS, (
            f"session-end hook took {elapsed:.1f}s: it waited for the archive"
        )
        assert current.exists(), (
            "rotation had already completed when the hook returned; the "
            "archive ran inside the hook"
        )

        last = agent / ".last_session_transcript.jsonl"
        assert _wait_for(
            lambda: last.exists() and "just ended" in last.read_text()
        ), "the detached worker never promoted the ended session to .last"
        assert _wait_for(lambda: _queue(repo) == [])
        assert "older" in (agent / ".second_to_last_transcript.jsonl").read_text()
        archives = list((agent / ".archived-transcripts").glob("*/transcript.jsonl.gz"))
        assert len(archives) == 1
        with gzip.open(archives[0], "rt") as fh:
            assert "oldest" in fh.read()
        assert not current.exists()

    @pytest.mark.parametrize("vendor", VENDORS)
    def test_every_vendor_hook_uses_the_one_entry_point(self, vendor: str) -> None:
        """A vendor hook that calls the rotation itself runs it inside the
        hook budget again."""
        text = (HOOKS_DIR / vendor / "session-end.sh").read_text()
        code = "\n".join(
            ln for ln in text.splitlines() if not ln.lstrip().startswith("#")
        )
        assert "end-session-transcript.sh" in code, (
            f"{vendor}/session-end.sh does not use end-session-transcript.sh"
        )
        assert "rotate-on-session-end.sh" not in code, (
            f"{vendor}/session-end.sh calls the rotation synchronously"
        )
        assert "kill-transcript-sync.sh" not in code, (
            f"{vendor}/session-end.sh kills the watcher before the session "
            f"is queued; the entry point does both in the safe order"
        )

    def test_entry_point_queues_before_it_kills(self, tmp_path: Path) -> None:
        """The queue entry must exist before the PID file goes: a sweep that
        runs in between otherwise sees neither and archives the ending
        session as a stray."""
        repo = _sandbox(tmp_path)
        agent = repo / ".agent"
        sid = _sid("ordered")
        witness = agent / "queue-at-kill-time"
        # Replace the kill script by one that records the queue it sees.
        (_shared(repo) / "kill-transcript-sync.sh").write_text(
            "#!/bin/bash\n"
            f'ls "{agent}/.rotation-queue" > "{witness}" 2>/dev/null\n'
        )
        (agent / f".current_session_transcript.{sid}.jsonl").write_text(_line("x"))

        proc = _run(_shared(repo) / "end-session-transcript.sh", str(repo), sid,
                    repo=repo)
        assert proc.returncode == 0, proc.stderr
        assert witness.exists(), "the entry point never ran the kill script"
        assert witness.read_text().strip().endswith(f".{sid}"), (
            "the session was not queued when its watcher was killed"
        )
        assert _wait_for(lambda: _queue(repo) == [])


# --- the queue ------------------------------------------------------------


class TestRotationQueue:
    def _rotate(self, repo: Path, *args: str,
                env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return _run(_shared(repo) / "rotate-on-session-end.sh", *args,
                    repo=repo, env=env)

    def test_drain_rotates_in_end_order(self, tmp_path: Path) -> None:
        """Entries drain oldest first, so the session that ended last owns
        ``.last`` however long the worker waited."""
        repo = _sandbox(tmp_path)
        agent = repo / ".agent"
        qdir = agent / ".rotation-queue"
        qdir.mkdir(parents=True)
        first, second = _sid("zz-ended-first"), _sid("aa-ended-second")
        (agent / f".current_session_transcript.{first}.jsonl").write_text(_line("first"))
        (agent / f".current_session_transcript.{second}.jsonl").write_text(_line("second"))
        (qdir / f"20260101T000000Z.{first}").touch()
        (qdir / f"20260101T000005Z.{second}").touch()

        proc = self._rotate(repo, "--drain", str(repo))
        assert proc.returncode == 0, proc.stderr
        assert "second" in (agent / ".last_session_transcript.jsonl").read_text()
        assert "first" in (agent / ".second_to_last_transcript.jsonl").read_text()
        assert _queue(repo) == []

    def test_an_entry_with_no_transcript_leaves_the_slots_alone(
        self, tmp_path: Path,
    ) -> None:
        """A drain that repeats (its predecessor died after the renames)
        finds the entry but no current file. Rotating again would demote the
        transcript it just promoted and leave ``.last`` absent."""
        repo = _sandbox(tmp_path)
        agent = repo / ".agent"
        qdir = agent / ".rotation-queue"
        qdir.mkdir(parents=True)
        sid = _sid("already-rotated")
        (agent / ".last_session_transcript.jsonl").write_text(_line("promoted"))
        (qdir / f"20260101T000000Z.{sid}").touch()

        proc = self._rotate(repo, "--drain", str(repo))
        assert proc.returncode == 0, proc.stderr
        assert "promoted" in (agent / ".last_session_transcript.jsonl").read_text()
        assert not (agent / ".second_to_last_transcript.jsonl").exists()
        assert _queue(repo) == []

    def test_a_resumed_session_is_not_rotated(self, tmp_path: Path) -> None:
        """A session resumed before its entry drained has a live watcher
        again. Its transcript belongs to a running session, and nothing is
        archived on its behalf: ``.second_to_last`` is not about to be
        displaced, and archiving it at every such drain would pile up
        copies of one transcript."""
        repo = _sandbox(tmp_path)
        agent = repo / ".agent"
        qdir = agent / ".rotation-queue"
        qdir.mkdir(parents=True)
        sid = _sid("resumed")
        (agent / ".second_to_last_transcript.jsonl").write_text(_line("oldest"))
        current = agent / f".current_session_transcript.{sid}.jsonl"
        current.write_text(_line("still running"))
        pid_file = agent / f".transcript-sync.{sid}.pid"
        (qdir / f"20260101T000000Z.{sid}").touch()
        watcher = subprocess.Popen(["sleep", "60"])
        try:
            pid_file.write_text(str(watcher.pid))
            proc = self._rotate(repo, "--drain", str(repo))
            assert proc.returncode == 0, proc.stderr
            assert current.exists(), "a live session's transcript was rotated away"
            assert pid_file.exists(), "a live session's PID file was removed"
            assert not (agent / ".last_session_transcript.jsonl").exists()
            assert _queue(repo) == []
            assert not (agent / ".archived-transcripts").exists(), (
                "a slot was archived for a session that did not rotate"
            )
        finally:
            watcher.kill()
            watcher.wait(timeout=5)

    def test_a_session_resumed_during_the_archive_is_not_rotated(
        self, tmp_path: Path,
    ) -> None:
        """The archive of ``.second_to_last`` takes seconds to minutes. A
        session resumed in that window must find its transcript where its
        new watcher writes it: the renames come after a second check."""
        repo = _sandbox(tmp_path, slow=True)
        agent = repo / ".agent"
        qdir = agent / ".rotation-queue"
        qdir.mkdir(parents=True)
        sid = _sid("resumed-mid-archive")
        (agent / ".second_to_last_transcript.jsonl").write_text(_line("oldest"))
        last = agent / ".last_session_transcript.jsonl"
        last.write_text(_line("older"))
        current = agent / f".current_session_transcript.{sid}.jsonl"
        current.write_text(_line("resumed"))
        pid_file = agent / f".transcript-sync.{sid}.pid"
        (qdir / f"20260101T000000Z.{sid}").touch()
        started = _shared(repo) / "slow-scrub-started"

        drain = subprocess.Popen(
            ["bash", str(_shared(repo) / "rotate-on-session-end.sh"),
             "--drain", str(repo)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        watcher = subprocess.Popen(["sleep", "60"])
        try:
            assert _wait_for(started.exists, timeout=10), (
                "the slow archive never started: the fixture does not reach "
                "the window under test"
            )
            pid_file.write_text(str(watcher.pid))
            assert drain.wait(timeout=30) == 0
            assert current.exists(), "a session resumed mid-archive was rotated away"
            assert "older" in last.read_text(), ".last was demoted for a live session"
            assert pid_file.exists()
            assert _queue(repo) == []
        finally:
            watcher.kill()
            watcher.wait(timeout=5)
            if drain.poll() is None:
                drain.kill()
                drain.wait(timeout=5)

    def test_a_busy_lock_leaves_the_session_queued(self, tmp_path: Path) -> None:
        """A worker that cannot get the lock must not rotate unsynchronized:
        the holder may be scrubbing a slot in place. The entry stays, and
        the next drain rotates it."""
        repo = _sandbox(tmp_path)
        agent = repo / ".agent"
        agent.mkdir(parents=True, exist_ok=True)
        sid = _sid("blocked")
        current = agent / f".current_session_transcript.{sid}.jsonl"
        current.write_text(_line("waiting"))

        with open(agent / ".rotation.lock", "w") as holder:
            fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
            proc = self._rotate(repo, str(repo), sid,
                                env={"TRANSCRIPT_ROTATION_LOCK_WAIT": "1"})
            assert proc.returncode == 0, proc.stderr
            assert current.exists(), "rotated without the lock"
            assert not (agent / ".last_session_transcript.jsonl").exists()
            assert [n.split(".", 1)[1] for n in _queue(repo)] == [sid]
            # A second end event for the same session, at least a second
            # later (so under a new stamp), adds no second entry.
            proc = self._rotate(repo, str(repo), sid,
                                env={"TRANSCRIPT_ROTATION_LOCK_WAIT": "1"})
            assert proc.returncode == 0, proc.stderr
            assert [n.split(".", 1)[1] for n in _queue(repo)] == [sid]
            fcntl.flock(holder.fileno(), fcntl.LOCK_UN)

        proc = self._rotate(repo, "--drain", str(repo))
        assert proc.returncode == 0, proc.stderr
        assert "waiting" in (agent / ".last_session_transcript.jsonl").read_text()
        assert _queue(repo) == []

    def test_launch_drops_its_own_queue_entry(self, tmp_path: Path) -> None:
        """Session start for a queued session id means the session was
        resumed: its pending rotation is void."""
        repo = _sandbox(tmp_path)
        agent = repo / ".agent"
        qdir = agent / ".rotation-queue"
        qdir.mkdir(parents=True)
        sid = _sid("relaunched")
        other = _sid("other")
        (qdir / f"20260101T000000Z.{sid}").touch()
        (qdir / f"20260101T000001Z.{other}").touch()
        # Keep the detached sweep from draining the other entry, so the
        # assertion below is about the launcher alone.
        (_shared(repo) / "sweep-transcript-orphans.sh").write_text("#!/bin/bash\n")
        src = tmp_path / "src.jsonl"
        src.touch()
        try:
            proc = _run(_shared(repo) / "launch-transcript-sync.sh", str(src), sid,
                        repo=repo)
            assert proc.returncode == 0, proc.stderr
            assert [n.split(".", 1)[1] for n in _queue(repo)] == [other]
        finally:
            _kill_watcher(repo, sid)


# --- session start --------------------------------------------------------


class TestSessionStartBudget:
    def test_watcher_starts_while_the_sweep_is_still_archiving(
        self, tmp_path: Path,
    ) -> None:
        """A stray transcript whose archive is slow must not delay, let
        alone prevent, this session's watcher."""
        repo = _sandbox(tmp_path, slow=True)
        agent = repo / ".agent"
        agent.mkdir(parents=True, exist_ok=True)
        stray_sid, sid = _sid("stray"), _sid("starting")
        stray = agent / f".current_session_transcript.{stray_sid}.jsonl"
        stray.write_text(_line("left behind"))
        src = tmp_path / "src.jsonl"
        src.touch()
        started = _shared(repo) / "slow-scrub-started"
        pid_file = agent / f".transcript-sync.{sid}.pid"
        try:
            began = time.time()
            proc = _run(_shared(repo) / "launch-transcript-sync.sh", str(src), sid,
                        repo=repo)
            elapsed = time.time() - began
            assert proc.returncode == 0, proc.stderr
            assert _wait_for(started.exists, timeout=10), (
                "the slow stray archive never started: the fixture does not "
                "reach the code under test"
            )
            assert elapsed < SLOW_SECONDS, (
                f"launch took {elapsed:.1f}s: it waited for the sweep"
            )
            assert _wait_for(pid_file.exists, timeout=3), (
                "no watcher started while the sweep was archiving"
            )
            assert stray.exists(), (
                "the stray was already archived when the watcher started; "
                "the sweep ran ahead of the launch"
            )
            assert _wait_for(lambda: _sweep_done(repo, sid))
            assert not stray.exists()
            archives = list(
                (agent / ".archived-transcripts").glob("stray-*/transcript.jsonl.gz")
            )
            assert len(archives) == 1
            with gzip.open(archives[0], "rt") as fh:
                assert "left behind" in fh.read()
        finally:
            _kill_watcher(repo, sid)

    def test_sweep_skips_a_session_queued_for_rotation(self, tmp_path: Path) -> None:
        """A session that has ended and is waiting for its rotation has no
        PID file. It is not a stray."""
        repo = _sandbox(tmp_path)
        agent = repo / ".agent"
        qdir = agent / ".rotation-queue"
        qdir.mkdir(parents=True)
        ending = _sid("ending")
        current = agent / f".current_session_transcript.{ending}.jsonl"
        current.write_text(_line("awaiting rotation"))
        crashed = _sid("ending-crashed-watcher")
        crashed_cur = agent / f".current_session_transcript.{crashed}.jsonl"
        crashed_cur.write_text(_line("awaiting rotation too"))
        (agent / f".transcript-sync.{crashed}.pid").write_text(str(_dead_pid()))
        (qdir / f"20260101T000000Z.{ending}").touch()
        (qdir / f"20260101T000001Z.{crashed}").touch()
        # The entries stay queued for the whole sweep (as when a session
        # ends while the sweep runs): make the sweep's own drain a no-op.
        (_shared(repo) / "rotate-on-session-end.sh").write_text("#!/bin/bash\n")

        proc = _run(_shared(repo) / "sweep-transcript-orphans.sh", str(repo),
                    _sid("sweeper"), repo=repo)
        assert proc.returncode == 0, proc.stderr
        assert current.exists(), "a session queued for rotation was swept as a stray"
        assert crashed_cur.exists(), (
            "a session queued for rotation was swept as a crashed orphan"
        )
        archive = agent / ".archived-transcripts"
        assert not archive.exists() or list(archive.iterdir()) == []

    def test_sweep_rotates_a_queued_session(self, tmp_path: Path) -> None:
        """Workers are interchangeable: a sweep drains the queue first, so a
        session whose own worker died still reaches ``.last`` and is never
        archived as a stray."""
        repo = _sandbox(tmp_path)
        agent = repo / ".agent"
        qdir = agent / ".rotation-queue"
        qdir.mkdir(parents=True)
        ended = _sid("worker-died")
        (agent / f".current_session_transcript.{ended}.jsonl").write_text(
            _line("ended earlier")
        )
        (qdir / f"20260101T000000Z.{ended}").touch()

        proc = _run(_shared(repo) / "sweep-transcript-orphans.sh", str(repo),
                    _sid("sweeper"), repo=repo)
        assert proc.returncode == 0, proc.stderr
        assert "ended earlier" in (agent / ".last_session_transcript.jsonl").read_text()
        assert _queue(repo) == []
        assert list((agent / ".archived-transcripts").glob("stray-*")) == [] \
            if (agent / ".archived-transcripts").exists() else True

    def test_sweep_removes_a_dead_writers_partial(self, tmp_path: Path) -> None:
        """A writer killed mid-archive leaves ``*.partial.<pid>`` behind
        (three were found, 204 MB). The sweep removes those whose writer is
        gone, the directory they emptied, and nothing else."""
        repo = _sandbox(tmp_path)
        agent = repo / ".agent"
        archive = agent / ".archived-transcripts"
        dead_dir = archive / "20260101T000000Z"
        live_dir = archive / "20260101T000001Z"
        kept_dir = archive / "20260101T000002Z"
        for d in (dead_dir, live_dir, kept_dir):
            d.mkdir(parents=True)
        dead = dead_dir / f"transcript.jsonl.gz.partial.{_dead_pid()}"
        dead.write_bytes(b"truncated")
        good = kept_dir / "transcript.jsonl.gz"
        good.write_bytes(gzip.compress(b"{}\n"))
        dead_beside_good = kept_dir / f"injection_history.jsonl.gz.partial.{_dead_pid()}"
        dead_beside_good.write_bytes(b"truncated")
        writer = subprocess.Popen(["sleep", "60"])
        try:
            live = live_dir / f"transcript.jsonl.gz.partial.{writer.pid}"
            live.write_bytes(b"in progress")
            proc = _run(_shared(repo) / "sweep-transcript-orphans.sh", str(repo),
                        _sid("sweeper"), repo=repo)
            assert proc.returncode == 0, proc.stderr
            assert not dead.exists(), "a dead writer's partial survived the sweep"
            assert not dead_dir.exists(), "the directory it emptied was left behind"
            assert not dead_beside_good.exists()
            assert good.exists(), "a finished archive was removed"
            assert live.exists(), "a live writer's partial was removed"
        finally:
            writer.kill()
            writer.wait(timeout=5)
