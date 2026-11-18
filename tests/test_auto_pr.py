# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ``scripts/auto-pr``'s amend-ownership guard (WI-libok).

``auto-pr`` amends ``HEAD`` at three sites (manifest refresh, and two
tracker-ops exclusion paths).  None of them used to check that ``HEAD`` was a
commit the *current branch* had actually created.

That matters because ``git checkout -b feature dev`` leaves ``HEAD`` pointing
at **dev's tip**.  Until the branch makes a commit of its own, an ``--amend``
does not amend "my work" — it rewrites the inherited base commit, folding
whatever is staged into it while keeping that commit's message, author and
author-date.  The work then lands on ``dev`` impersonating whatever the base
tip happened to be.

Live instance 2026-07-29: dev's tip was a tracker sync commit, so seven files
of analyzer work merged wearing the subject ``tracker: 6 files (104 ops)``.
The reflog for that branch reads ``checkout -> commit (amend) -> checkout``
with no plain ``commit:`` entry, which is the signature of the failure — every
healthy branch that day reads ``checkout -> commit -> commit (amend) ->
checkout``.  Nothing about the bug was tracker-specific; the same hole would
impersonate any dev tip.

Test strategy
-------------
The guard is a shell function, so these tests **extract its real definition**
out of ``scripts/auto-pr`` and source that into a one-shot harness.  The
sibling ``test_autopr_result_sentinel.py`` restates its helper's body inline
and carries a comment noting the copy must be hand-updated when production
changes; extracting instead means this file cannot silently drift away from
the code it pins.

``test_amend_without_a_guard_really_does_impersonate`` is the positive control
(a null result from the guard tests is only evidence once the harness is shown
capable of producing the failure), and ``test_every_amend_site_is_guarded`` is
the ratchet that makes a newly-added unguarded ``--amend`` fail CI.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT_REAL = Path(__file__).resolve().parent.parent
AUTO_PR_PATH = REPO_ROOT_REAL / "scripts" / "auto-pr"

GUARD_FN = "_assert_branch_owns_head"

#: How far above a ``git commit --amend`` the guard call may sit and still
#: count as guarding it.  Small enough that an unrelated earlier call cannot
#: be mistaken for one, large enough to tolerate a comment block.
GUARD_WINDOW = 12


def _extract_shell_function(name: str) -> str:
    """Return the production text of shell function ``name`` from auto-pr.

    Reads the real definition rather than restating it, so the behaviour these
    tests pin is by construction the behaviour that ships.
    """
    lines = AUTO_PR_PATH.read_text().splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith(f"{name}()")),
        None,
    )
    assert start is not None, f"{name}() not found in {AUTO_PR_PATH}"
    end = next(
        (j for j in range(start + 1, len(lines)) if lines[j] == "}"),
        None,
    )
    assert end is not None, f"unterminated function {name}()"
    return "\n".join(lines[start:end + 1])


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), check=True,
        capture_output=True, text=True,
    ).stdout


def _make_repo(tmp_path: Path) -> Path:
    """A repo on ``dev`` with one real commit, ready to branch from."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "dev")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "a.txt").write_text("base\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "feat: a real base commit")
    return repo


def _run_guard(repo: Path, base_ref: str = "dev") -> subprocess.CompletedProcess:
    """Invoke the extracted guard inside ``repo`` against ``base_ref``."""
    harness = repo / "harness.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        f"{_extract_shell_function(GUARD_FN)}\n"
        f'{GUARD_FN} "$1"\n'
    )
    return subprocess.run(
        ["bash", str(harness), base_ref],
        cwd=str(repo), capture_output=True, text=True,
    )


class TestGuardPredicate:
    """The guard fires exactly when HEAD is not the branch's own work."""

    def test_fires_when_branch_has_no_commits_of_its_own(
        self, tmp_path: Path,
    ) -> None:
        """The WI-libok condition: branched, staged, never committed."""
        repo = _make_repo(tmp_path)
        _git(repo, "checkout", "-q", "-b", "feature")
        (repo / "b.txt").write_text("my work\n")
        _git(repo, "add", "b.txt")

        result = _run_guard(repo)

        assert result.returncode != 0, (
            "guard did not fire on a branch with no commits of its own; "
            "an amend here rewrites the inherited base commit"
        )

    def test_silent_when_branch_has_its_own_commit(
        self, tmp_path: Path,
    ) -> None:
        """The normal flow must not be blocked."""
        repo = _make_repo(tmp_path)
        _git(repo, "checkout", "-q", "-b", "feature")
        (repo / "b.txt").write_text("my work\n")
        _git(repo, "add", "b.txt")
        _git(repo, "commit", "-qm", "fix: my own work")

        result = _run_guard(repo)

        assert result.returncode == 0, result.stdout + result.stderr

    def test_silent_when_own_commit_sits_on_a_moved_base(
        self, tmp_path: Path,
    ) -> None:
        """Rebasing onto an advanced base keeps the branch its own owner.

        ``merge-base --is-ancestor`` is chosen over counting ``base..HEAD``
        precisely so this case stays correct after auto-pr rebases.
        """
        repo = _make_repo(tmp_path)
        _git(repo, "checkout", "-q", "-b", "feature")
        (repo / "b.txt").write_text("my work\n")
        _git(repo, "add", "b.txt")
        _git(repo, "commit", "-qm", "fix: my own work")

        _git(repo, "checkout", "-q", "dev")
        (repo / "c.txt").write_text("someone else\n")
        _git(repo, "add", "c.txt")
        _git(repo, "commit", "-qm", "feat: base moved on")
        _git(repo, "checkout", "-q", "feature")
        _git(repo, "rebase", "-q", "dev")

        result = _run_guard(repo)

        assert result.returncode == 0, result.stdout + result.stderr

    def test_message_names_the_remedy(self, tmp_path: Path) -> None:
        """A refusal must tell the operator what to do instead."""
        repo = _make_repo(tmp_path)
        _git(repo, "checkout", "-q", "-b", "feature")

        result = _run_guard(repo)
        combined = result.stdout + result.stderr

        assert "git commit" in combined, combined


class TestPositiveControl:
    """Proof the harness can produce the failure the guard prevents."""

    def test_amend_without_a_guard_really_does_impersonate(
        self, tmp_path: Path,
    ) -> None:
        """Reproduce WI-libok end to end, with no tracker involved.

        Establishes that the guard tests above are measuring something real
        rather than a scenario git would never produce.
        """
        repo = _make_repo(tmp_path)
        _git(repo, "checkout", "-q", "-b", "feature")
        (repo / "b.txt").write_text("my work\n")
        _git(repo, "add", "b.txt")

        _git(repo, "commit", "-q", "--amend", "--no-edit")

        subject = _git(repo, "log", "-1", "--pretty=%s").strip()
        tracked = _git(repo, "show", "--stat", "--format=", "HEAD")

        assert subject == "feat: a real base commit", (
            "expected the amended commit to wear the BASE commit's subject"
        )
        assert "b.txt" in tracked, (
            "expected the staged work to have been folded into it"
        )


class TestAmendSitesRatchet:
    """Every ``--amend`` in auto-pr must be guarded — including future ones."""

    def test_every_amend_site_is_guarded(self) -> None:
        lines = AUTO_PR_PATH.read_text().splitlines()
        amend_sites = [
            i for i, line in enumerate(lines)
            if "git commit --amend" in line and not line.lstrip().startswith("#")
        ]

        assert len(amend_sites) >= 3, (
            f"expected auto-pr's known amend sites, found {len(amend_sites)}"
        )

        unguarded = [
            i + 1
            for i in amend_sites
            if not any(
                GUARD_FN in line
                for line in lines[max(0, i - GUARD_WINDOW):i]
            )
        ]

        assert not unguarded, (
            "unguarded `git commit --amend` at scripts/auto-pr line(s) "
            f"{unguarded}: call {GUARD_FN} first or HEAD may be a commit "
            "this branch does not own (WI-libok)"
        )


GATE_FN = "_scheduled_run_gate"


def _run_gate(tmp_path: Path, helper: str | None) -> subprocess.CompletedProcess:
    """Run the extracted gate with ``helper`` as the scheduled-gate script
    (None: no such script)."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    if helper is not None:
        (scripts / "scheduled-gate").write_text(helper)
        (scripts / "scheduled-gate").chmod(0o755)
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        f'SCRIPT_DIR="{scripts}"\n'
        f"{_extract_shell_function(GATE_FN)}\n"
        f'{GATE_FN} "dev"\n'
    )
    return subprocess.run(["bash", str(harness)], capture_output=True, text=True)


class TestScheduledRunGate:
    """auto-pr stops a merge only on the helper's own "blocked" (WI-kidop).

    The rule itself is scripts/scheduled-gate's and is tested there
    (test_scheduled_gate.py). What auto-pr adds is the wiring, and each way
    it can go wrong is silent: blocking every merge because the helper is
    missing or crashed, or passing a red run because the exit code was
    swallowed.
    """

    def test_blocked_is_the_helpers_exit_3_and_its_words_are_printed(self, tmp_path) -> None:
        done = _run_gate(tmp_path, "#!/bin/sh\necho \"scheduled gate: RED: $*\"\nexit 3\n")
        assert done.returncode == 1
        # It asks about the base branch it was given and this branch's tip.
        assert done.stdout.strip() == "scheduled gate: RED: check --base origin/dev --head HEAD"

    def test_green_passes_with_the_helpers_line(self, tmp_path) -> None:
        done = _run_gate(tmp_path, "#!/bin/sh\necho 'scheduled gate: the full suite passed'\n")
        assert done.returncode == 0
        assert done.stdout.strip() == "scheduled gate: the full suite passed"

    def test_a_missing_helper_does_not_block_and_says_so(self, tmp_path) -> None:
        done = _run_gate(tmp_path, None)
        assert done.returncode == 0
        assert "NOT KNOWN" in done.stdout and "scheduled-gate is missing" in done.stdout

    def test_a_helper_that_crashes_does_not_block_and_says_so(self, tmp_path) -> None:
        done = _run_gate(tmp_path, "#!/bin/sh\necho boom >&2\nexit 1\n")
        assert done.returncode == 0
        assert "boom" in done.stdout
        assert "scheduled gate: NOT KNOWN (scheduled-gate exited 1); not blocking" in done.stdout

    def test_it_is_asked_before_the_push_and_before_each_merge(self) -> None:
        """Before anything is pushed (no pull request and no CI spent on a
        merge that will be refused), and again before each of the two merges
        (the verdict can turn red while CI is polled). A third merge site
        added without it would merge past a red run."""
        lines = AUTO_PR_PATH.read_text().splitlines()
        calls = [i for i, line in enumerate(lines)
                 if line.strip().startswith(f"if ! {GATE_FN} ")]
        merges = [i for i, line in enumerate(lines) if "do_merge_guarded \"$PR_NUM\"" in line]
        assert len(merges) == 2, "auto-pr's merge sites changed: gate the new one too"
        for merge in merges:
            assert any(merge - 30 < call < merge for call in calls), (
                f"the merge at scripts/auto-pr line {merge + 1} is not behind {GATE_FN}")
        first_push = next(i for i, line in enumerate(lines)
                          if 'echo "preparing" >"$REPO_ROOT/.git/PR_PENDING"' in line
                          and i > next(j for j, text in enumerate(lines) if text.startswith("do_pr()")))
        assert any(first_push - 12 < call < first_push for call in calls), (
            "do_pr does not ask the gate before it creates its pending marker and pushes")
        # A block ends the run with its own recorded state, not a generic failure.
        for call in calls:
            assert any('_autopr_state_final="blocked_scheduled_red"' in line
                       for line in lines[call:call + 8]), call + 1
