# SPDX-License-Identifier: MPL-2.0
"""Recovery suppression is a LOCK, so it cannot outlive its holder (WI-gokuv).

WHAT IT GUARDS, verified by executing the guard rather than reading it.
``.githooks/reference-transaction`` and ``.githooks/post-checkout`` skip
``tracker recover`` while suppression is in force. Run against a stub tracker,
the unsuppressed arm invokes ``recover`` and the suppressed arm invokes nothing.
While it is in force, a ``git reset --hard`` or ``git checkout`` drops pending
``.ops`` and nothing restores them. The out-of-repo journal still holds the
data, so this is a degraded-safety window, not loss. One was observed open for
~10 HOURS (WI-pohir).

WHY A FLAG COULD NOT WORK, AND WHY THIS IS THE SECOND TIME THE PROJECT LEARNED
IT. ``do_sync`` clears its marker in a ``finally``; ``auto-pr`` clears its in an
EXIT trap; measured, bash runs that trap on SIGTERM and on SIGINT and NOT on
SIGKILL. So a hard kill left a ``touch``ed file behind, and a file cannot answer
"does anyone still own this?" -- a leaked marker and a healthy 45-minute
``auto-pr`` were the same observation. WI-nutin had already made exactly this
move for the marker next door: ``SyncGate``'s docstring records that the OS
releases an flock even on SIGKILL, "so the gate cannot leak across process
lifetimes". This marker simply never got the same treatment.

THE FILE'S EXISTENCE NOW MEANS NOTHING. Only a live holder suppresses anything,
so the shape that caused the original ten-hour incident -- a leftover file --
is inert, and ``test_a_leftover_file_suppresses_nothing`` pins that directly.

THE ONE REMAINING WAY IT CAN OUTLIVE ITS OWNER, measured before the design was
written rather than reasoned about afterwards: every git and curl ``auto-pr``
forks INHERITS the lock fd, and bash has no close-on-exec escape for it (tried:
``exec {var}>`` does not set it -- ``/proc/<child>/fd`` still showed the marker).
So a SIGKILLed run keeps the lock until its orphaned children exit. Normally
that is seconds and it self-heals. When it is not, the lock body -- ``pid=`` and
``started=``, the same shape ``SyncGate`` writes -- lets a reader ask a FACT
instead of starting a stopwatch: the lock is held and the process that claimed
it is gone.

WHICH IS WHY THERE IS NO LONGER A GRACE PERIOD HERE. The previous version timed
an unowned marker out at 900s, a constant that had to be measured against a live
``auto-pr``'s phases (and was wrong at 300s on its first attempt). A lock
answers "is anybody there" directly, so the constant and the measuring are both
deleted.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from hypergumbo_tracker.journal import (
    RECOVER_MARKER_NAME,
    RecoverSuppressionLock,
    _flock_is_held,
    _pid_is_alive,
    recover_suppression_status,
)


def _git_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".git"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _a_pid_that_cannot_exist() -> int:
    """``pid_max`` is the wrap point, so it is never assigned to a process.

    Chosen over "spawn something and reuse its pid after it exits", which races
    pid reuse. The caller asserts it really is dead, so the test validates its
    own instrument instead of trusting this comment.
    """
    return int(Path("/proc/sys/kernel/pid_max").read_text().strip())


class TestTheLockPrimitive:
    def test_an_unlocked_file_reads_free(self, tmp_path: Path) -> None:
        f = tmp_path / "m"
        f.touch()
        assert _flock_is_held(f) is False

    def test_a_held_file_reads_held(self, tmp_path: Path) -> None:
        lock = RecoverSuppressionLock(_git_dir(tmp_path))
        assert lock.try_acquire()
        try:
            assert _flock_is_held(lock.path) is True
        finally:
            lock.release()

    def test_releasing_frees_it_and_removes_the_file(self, tmp_path: Path) -> None:
        lock = RecoverSuppressionLock(_git_dir(tmp_path))
        assert lock.try_acquire()
        lock.release()
        assert not lock.path.exists()

    def test_a_second_acquirer_is_refused_rather_than_blocked(
        self, tmp_path: Path,
    ) -> None:
        # The nesting auto-pr actually produces: its post-merge `tracker discuss`
        # can trigger an auto-sync while auto-pr still holds the lock. The inner
        # caller must not block, and must not think it owns anything.
        git_dir = _git_dir(tmp_path)
        outer = RecoverSuppressionLock(git_dir)
        inner = RecoverSuppressionLock(git_dir)
        assert outer.try_acquire()
        try:
            assert inner.try_acquire() is False
            inner.release()  # no-op: owns nothing
            assert _flock_is_held(outer.path), (
                "the inner release must not free the OUTER holder's lock"
            )
        finally:
            outer.release()

    def test_the_os_releases_it_when_the_holder_is_SIGKILLed(
        self, tmp_path: Path,
    ) -> None:
        """THE CLAIM THE WHOLE DESIGN RESTS ON, exercised for real.

        A subprocess takes the lock and is killed with SIGKILL -- the one signal
        that runs no cleanup anywhere. If the OS did not drop the flock, this is
        the old bug with extra steps, so it is asserted rather than cited.
        """
        git_dir = _git_dir(tmp_path)
        marker = git_dir / RECOVER_MARKER_NAME
        script = textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, {str(Path(__file__).parent.parent / "src")!r})
            from pathlib import Path
            from hypergumbo_tracker.journal import RecoverSuppressionLock
            lock = RecoverSuppressionLock(Path({str(git_dir)!r}))
            assert lock.try_acquire()
            print("held", flush=True)
            time.sleep(60)
        """)
        proc = subprocess.Popen(  # nosec B603 - fixed interpreter, no shell
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE, text=True,
        )
        try:
            assert proc.stdout is not None
            assert proc.stdout.readline().strip() == "held"
            assert _flock_is_held(marker), "precondition: child must hold it"
            proc.kill()
            proc.wait(timeout=10)
        finally:
            if proc.poll() is None:  # pragma: no cover - defensive
                proc.kill()
        assert marker.exists(), "SIGKILL leaves the FILE behind, as it always did"
        assert _flock_is_held(marker) is False, (
            "the OS must drop the flock when the holder dies"
        )


class TestNothingToReport:
    def test_a_clean_repo_reports_nothing(self, tmp_path: Path) -> None:
        status = recover_suppression_status(_git_dir(tmp_path))
        assert status.present is False
        assert status.orphaned is False
        assert status.warning() is None

    def test_a_leftover_file_suppresses_nothing(self, tmp_path: Path) -> None:
        """THE ORIGINAL BUG'S SHAPE, now inert.

        This is exactly what a SIGKILLed auto-pr leaves behind: the file, with
        no holder. The hooks test the lock, so it disables nothing and there is
        nothing to warn about. Under the old flag semantics this same state
        silently switched self-healing off.
        """
        git_dir = _git_dir(tmp_path)
        (git_dir / RECOVER_MARKER_NAME).touch()

        status = recover_suppression_status(git_dir)

        assert status.present is True
        assert status.held is False
        assert status.orphaned is False
        assert status.warning() is None

    def test_a_live_holder_is_not_a_finding(self, tmp_path: Path) -> None:
        git_dir = _git_dir(tmp_path)
        lock = RecoverSuppressionLock(git_dir)
        assert lock.try_acquire()
        try:
            status = recover_suppression_status(git_dir)
        finally:
            lock.release()

        assert status.held is True
        assert status.owner_pid == os.getpid()
        assert status.owner_alive is True
        assert status.orphaned is False
        assert status.warning() is None

    def test_a_held_lock_with_no_recorded_owner_is_not_indicted(
        self, tmp_path: Path,
    ) -> None:
        # Refusing to claim an orphan on missing evidence: a holder that wrote
        # no body is one we do not know how to indict, and guessing is the
        # false-alarm direction.
        git_dir = _git_dir(tmp_path)
        lock = RecoverSuppressionLock(git_dir)
        assert lock.try_acquire()
        try:
            lock.path.write_text("")
            status = recover_suppression_status(git_dir)
        finally:
            lock.release()

        assert status.held is True
        assert status.owner_pid is None
        assert status.orphaned is False
        assert status.warning() is None


class TestTheOrphanIsTheFinding:
    def test_a_lock_held_by_a_dead_owner_is_reported(self, tmp_path: Path) -> None:
        dead = _a_pid_that_cannot_exist()
        assert _pid_is_alive(dead) is False, "instrument check: pid must be dead"

        git_dir = _git_dir(tmp_path)
        lock = RecoverSuppressionLock(git_dir)
        assert lock.try_acquire()
        try:
            # The lock stays genuinely held (this process holds it), but the
            # BODY names an owner that is gone -- the orphaned-child shape.
            lock.path.write_text(f"pid={dead} started=1 holder=auto-pr\n")
            status = recover_suppression_status(git_dir)
        finally:
            lock.release()

        assert status.held is True
        assert status.owner_pid == dead
        assert status.owner_alive is False
        assert status.orphaned is True
        warning = status.warning()
        assert warning is not None
        assert str(dead) in warning
        assert "self-healing" in warning
        assert "lslocks" in warning


class TestTheWarningActuallyReachesTheOperator:
    """A disclosure nothing reads is not a disclosure."""

    def test_an_orphan_is_printed_on_stderr(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_tracker.cli import _warn_recover_suppressed

        repo = _repo(tmp_path)
        dead = _a_pid_that_cannot_exist()
        lock = RecoverSuppressionLock(repo / ".git")
        assert lock.try_acquire()
        try:
            lock.path.write_text(f"pid={dead} started=1 holder=auto-pr\n")
            _warn_recover_suppressed(repo / ".agent")
        finally:
            lock.release()

        err = capsys.readouterr().err
        assert "ORPHANED" in err
        assert str(dead) in err

    def test_a_healthy_repo_prints_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        # THE CONTROL. Same call, same repo, no lock.
        from hypergumbo_tracker.cli import _warn_recover_suppressed

        repo = _repo(tmp_path)

        _warn_recover_suppressed(repo / ".agent")

        assert capsys.readouterr().err == ""

    def test_outside_a_git_repository_it_is_a_no_op(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_tracker.cli import _warn_recover_suppressed

        bare = tmp_path / "not-a-repo"
        bare.mkdir()

        _warn_recover_suppressed(bare)

        assert capsys.readouterr().err == ""

    def test_every_subcommand_carries_it_because_main_does(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """END TO END, through the real dispatcher.

        ``count-todos`` is picked deliberately: it is what the stop hook calls,
        it is one of the commands WI-pohir named, and before this work it
        printed the auto-sync banner and nothing else while self-healing was off
        underneath it.
        """
        from hypergumbo_tracker.cli import main

        repo = _repo(tmp_path)
        dead = _a_pid_that_cannot_exist()
        lock = RecoverSuppressionLock(repo / ".git")
        assert lock.try_acquire()
        monkeypatch.chdir(repo)
        try:
            lock.path.write_text(f"pid={dead} started=1 holder=auto-pr\n")
            with pytest.raises(SystemExit) as exc:
                main(["count-todos"])
        finally:
            lock.release()

        err = capsys.readouterr().err
        assert exc.value.code == 0
        assert "ORPHANED" in err


def _repo(tmp_path: Path) -> Path:
    """A git repo with a tracker ops dir — the shape `main` expects."""
    import shutil

    repo = tmp_path / "repo"
    (repo / ".agent" / "tracker-workspace" / ".ops").mkdir(parents=True)
    vcs = shutil.which("git") or "git"
    subprocess.run(  # nosec B603 - binary resolved via shutil.which
        [vcs, "init", "-q", "-b", "dev"], cwd=repo, check=True,
        capture_output=True, text=True,
    )
    return repo
