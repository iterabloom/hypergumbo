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
