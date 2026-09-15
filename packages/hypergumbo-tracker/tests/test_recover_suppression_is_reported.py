# SPDX-License-Identifier: MPL-2.0
"""A leaked ``tracker-recover-disabled`` marker is REPORTED, not silent (WI-pohir).

WHAT THE MARKER DOES, verified by executing the guard rather than reading it.
``.githooks/reference-transaction`` and ``.githooks/post-checkout`` both test
``-f .git/tracker-recover-disabled`` and skip ``tracker recover`` when it is
there. Run against a stub tracker, the ABSENT arm invokes ``recover`` and the
PRESENT arm invokes nothing -- so while the marker exists, a ``git reset --hard``
or ``git checkout`` drops pending ``.ops`` and nothing restores them. The
out-of-repo journal still holds the data, so this is a degraded-safety window,
not a data-loss event. It was observed open for ~10 hours.

WHY IT LEAKS. ``do_sync`` clears it in a ``finally``; ``auto-pr`` clears it in an
EXIT trap. Measured: bash runs that trap on SIGTERM and on SIGINT, and does not
on SIGKILL. So the leak path is a hard kill -- the OOM reaper, or power loss --
and nothing else.

WHY NOTHING CAUGHT IT. The marker is a bare ``touch``ed flag with no holder, so
there is no way to ask whether anyone still owns it. Its sibling has the cure
already: WI-nutin converted ``.git/TRACKER_SYNC_PENDING`` from a marker into an
fcntl lock precisely because, as ``SyncGate``'s own docstring puts it, the OS
releases a flock even on SIGKILL "so the gate cannot leak across process
lifetimes". This marker never got that treatment.

WHY AGE ALONE CANNOT BE THE TEST -- the correction that shapes this module.
WI-pohir proposed warning on any marker older than ~15 minutes, reasoning that
"no auto-pr fetch+ff window is that long". The marker's span is not the fetch+ff
window. ``do_pr`` touches it as its first act and only ``_autopr_finalize``
removes it, so it is held across the whole run INCLUDING the CI poll, whose
default timeout is 2400s with a further 300s soft-retry. A 15-minute rule fires
inside the legitimate window, on every run whose CI is slow.

SO OWNERSHIP IS ASKED FIRST, AND AGE ONLY BREAKS THE REMAINING TIE. A live
``auto-pr`` is named by ``.git/PR_PENDING``; a live ``do_sync`` is named by the
``TRACKER_SYNC_PENDING`` flock, which cannot lie because it cannot leak. Only
when neither owns the marker does age matter, and then it covers the two ends of
a run where ``PR_PENDING`` does not exist yet or does not exist any more: before
the push, and after ``cleanup_local`` drops the gate but before the process
exits. The second of those is where this bug lives -- the loud gate is already
released and the silent one is still set, which is exactly where the observed
incident died.

THE GRACE WAS WRONG ONCE ALREADY, AND THAT IS WHY IT IS PINNED HERE. The first
draft sized it at 300s against the post-merge end alone, described as
"network-bound seconds, not minutes". A live auto-pr refuted that within the
hour: on PR #975, an ordinary run with fast CI, the PRE-GATE end alone was 172s
-- 51% of the entire 336s run, and 57% of the grace it was supposed to fit
inside. ``test_the_grace_covers_the_measured_windows`` holds that measurement so
the constant cannot drift back under it by estimate.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from hypergumbo_tracker.journal import (
    RECOVER_MARKER_NAME,
    recover_suppression_status,
)

#: Resolved absolutely so the S607 partial-executable-path rule does not fire,
#: matching test_journal.py / test_reconcile.py.
_VCS = shutil.which("git") or "git"


def _git_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".git"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _marker(git_dir: Path, *, age_seconds: float = 0.0) -> Path:
    m = git_dir / RECOVER_MARKER_NAME
    m.touch()
    if age_seconds:
        past = time.time() - age_seconds
        os.utime(m, (past, past))
    return m


class TestNoMarkerIsNotAFinding:
    def test_a_clean_repo_reports_nothing(self, tmp_path: Path) -> None:
        status = recover_suppression_status(_git_dir(tmp_path))
        assert status.present is False
        assert status.leaked is False
        assert status.warning() is None


class TestALiveOwnerSilencesIt:
    """The refutation arm. These are the false alarms a 15-minute age rule
    would raise, and each one must stay quiet no matter how old the marker is."""

    def test_an_auto_pr_polling_ci_for_40_minutes_is_not_a_leak(
        self, tmp_path: Path,
    ) -> None:
        git_dir = _git_dir(tmp_path)
        _marker(git_dir, age_seconds=40 * 60)
        (git_dir / "PR_PENDING").write_text("971\n")

        status = recover_suppression_status(git_dir)

        assert status.present is True
        assert status.leaked is False
        assert status.owner is not None and "auto-pr" in status.owner
        assert status.warning() is None

    def test_a_live_do_sync_holding_the_flock_is_not_a_leak(
        self, tmp_path: Path,
    ) -> None:
        from hypergumbo_tracker.sync import SyncGate

        git_dir = _git_dir(tmp_path)
        _marker(git_dir, age_seconds=6 * 3600)
        gate = SyncGate(git_dir / "TRACKER_SYNC_PENDING")
        acquired, _ = gate.try_acquire()
        assert acquired, "test precondition: the gate must be free to take"
        try:
            status = recover_suppression_status(git_dir)
        finally:
            gate.release()

        assert status.present is True
        assert status.leaked is False
        assert status.owner is not None and "sync" in status.owner
        assert status.warning() is None

    def test_a_gate_FILE_with_no_live_holder_does_not_count_as_an_owner(
        self, tmp_path: Path,
    ) -> None:
        # THE CONTROL that makes the arm above mean something: same file, same
        # marker, only the live flock differs. A stale lock file is exactly what
        # a SIGKILL leaves, so trusting its mere existence would re-open the bug
        # one level up.
        git_dir = _git_dir(tmp_path)
        _marker(git_dir, age_seconds=6 * 3600)
        (git_dir / "TRACKER_SYNC_PENDING").write_text("pid=1 started=0\n")

        status = recover_suppression_status(git_dir)

        assert status.leaked is True


class TestTheUnownedMarkerIsTheFinding:
    def test_an_unowned_marker_past_the_grace_is_reported(
        self, tmp_path: Path,
    ) -> None:
        git_dir = _git_dir(tmp_path)
        _marker(git_dir, age_seconds=10 * 3600)

        status = recover_suppression_status(git_dir)

        assert status.present is True
        assert status.owner is None
        assert status.leaked is True
        warning = status.warning()
        assert warning is not None
        # It must name the ELAPSED TIME, what is degraded, and the remedy --
        # a warning that says only "marker present" sends the reader to grep.
        assert "10h" in warning
        assert "self-healing" in warning
        assert f"rm -f .git/{RECOVER_MARKER_NAME}" in warning

    def test_an_unowned_end_of_a_live_run_is_inside_the_grace(
        self, tmp_path: Path,
    ) -> None:
        # Both ends qualify: before auto-pr writes PR_PENDING, and after
        # cleanup_local removes it. 172s is the PRE-GATE end measured on PR #975
        # -- a real value from a healthy run, not a round number.
        git_dir = _git_dir(tmp_path)
        _marker(git_dir, age_seconds=172)

        assert recover_suppression_status(git_dir).leaked is False

    def test_the_grace_boundary_is_pinned(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.journal import _UNOWNED_GRACE_SECONDS

        git_dir = _git_dir(tmp_path)
        m = _marker(git_dir)
        for delta, expected in ((-1, False), (+1, True)):
            past = time.time() - (_UNOWNED_GRACE_SECONDS + delta)
            os.utime(m, (past, past))
            assert recover_suppression_status(git_dir).leaked is expected, delta

    def test_the_grace_covers_the_measured_windows(self) -> None:
        """THE REGRESSION GUARD ON THE CONSTANT ITSELF.

        Measured on auto-pr PR #975, an ordinary run with fast CI: the marker
        was touched at 17:35:19 and ``.git/PR_PENDING`` appeared at 17:38:11, so
        the marker sat UNOWNED for 172 seconds while a perfectly healthy auto-pr
        was pushing. The first draft of the grace was 300s -- 57% consumed by
        one ordinary run, sized by an estimate that had only considered the
        other end.

        The multiplier is the headroom a retrying push or ``cleanup_local``'s
        three-attempt pull loop needs. If someone lowers the constant, this
        fails and names the measurement rather than the opinion.
        """
        from hypergumbo_tracker.journal import _UNOWNED_GRACE_SECONDS

        measured_pre_gate_window = 172.0
        assert _UNOWNED_GRACE_SECONDS >= 5 * measured_pre_gate_window


class TestTheAgeIsLegible:
    def test_seconds_minutes_and_hours_each_render(
        self, tmp_path: Path,
    ) -> None:
        git_dir = _git_dir(tmp_path)
        m = _marker(git_dir)
        seen = []
        for age in (45, 12 * 60, 9 * 3600 + 41 * 60):
            past = time.time() - age
            os.utime(m, (past, past))
            seen.append(recover_suppression_status(git_dir).age_text)
        assert seen == ["45s", "12m", "9h41m"]


class TestTheWarningActuallyReachesTheOperator:
    """A disclosure nothing reads is not a disclosure. These pin the wiring,
    not the predicate."""

    def test_a_leak_is_printed_on_stderr(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_tracker.cli import _warn_recover_suppressed

        repo = _repo(tmp_path)
        _marker(repo / ".git", age_seconds=10 * 3600)

        _warn_recover_suppressed(repo / ".agent")

        err = capsys.readouterr().err
        assert "ops self-healing has been OFF for 10h00m" in err
        assert f"rm -f .git/{RECOVER_MARKER_NAME}" in err

    def test_a_healthy_repo_prints_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        # THE CONTROL. Same call, same repo, no marker.
        from hypergumbo_tracker.cli import _warn_recover_suppressed

        repo = _repo(tmp_path)

        _warn_recover_suppressed(repo / ".agent")

        assert capsys.readouterr().err == ""

    def test_outside_a_git_repository_it_is_a_no_op(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        bare = tmp_path / "not-a-repo"
        bare.mkdir()

        _warn_recover_suppressed_outside(bare)

        assert capsys.readouterr().err == ""

    def test_every_subcommand_carries_it_because_main_does(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """END TO END, through the real dispatcher.

        ``count-todos`` is picked deliberately: it is what the stop hook calls,
        it is one of the commands WI-pohir named, and before this change it
        printed the auto-sync banner and nothing else while self-healing was
        off underneath it.
        """
        from hypergumbo_tracker.cli import main

        repo = _repo(tmp_path)
        _marker(repo / ".git", age_seconds=10 * 3600)
        monkeypatch.chdir(repo)

        with pytest.raises(SystemExit) as exc:
            main(["count-todos"])

        err = capsys.readouterr().err
        assert exc.value.code == 0
        assert "ops self-healing has been OFF" in err


def _warn_recover_suppressed_outside(path: Path) -> None:
    from hypergumbo_tracker.cli import _warn_recover_suppressed

    _warn_recover_suppressed(path)


def _repo(tmp_path: Path) -> Path:
    """A git repo with a tracker ops dir — the shape `main` expects."""
    repo = tmp_path / "repo"
    (repo / ".agent" / "tracker-workspace" / ".ops").mkdir(parents=True)
    subprocess.run(  # nosec B603 - binary resolved via shutil.which
        [_VCS, "init", "-q", "-b", "dev"], cwd=repo, check=True,
        capture_output=True, text=True,
    )
    return repo
