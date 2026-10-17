# SPDX-License-Identifier: AGPL-3.0-or-later
"""``generate-architecture --check`` ages the doc by code commits, not tracker syncs.

The staleness check measures ``current_commit_count - recorded_commit_count``
and fails past ``RECENT_COMMIT_WINDOW``. Tracker auto-sync commits
(``tracker: N files (M ops) -- ...``) and the ``tracker:`` conventional prefix
AGENTS.md mandates for tracker-only changes move no code, so they must not
count: on 2026-10-16 an afternoon of filing produced 34 of them on ``dev`` with
zero code commits in between, and a doc regenerated that morning failed the
30-commit window on the very next PR.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_generate_architecture_module():
    script_path = REPO_ROOT / "scripts" / "generate-architecture"
    loader = importlib.machinery.SourceFileLoader(
        "generate_architecture_staleness_under_test", str(script_path),
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout


def _repo_with_subjects(tmp_path: Path, subjects: list[str]) -> Path:
    """A git repo whose history is ``subjects``, oldest first."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    for i, subject in enumerate(subjects):
        (repo / f"f{i}").write_text(subject)
        _git(repo, "add", f"f{i}")
        _git(repo, "commit", "-q", "-m", subject)
    return repo


@pytest.fixture()
def mod(monkeypatch):
    return _load_generate_architecture_module()


def test_tracker_only_commits_are_counted_within_the_window(tmp_path, mod, monkeypatch):
    repo = _repo_with_subjects(tmp_path, [
        "feat: first",
        "tracker: 3 files (2 ops) — WI-a, INV-b",
        "tracker(workspace): batch status updates",
        "fix: second",
        "tracker: 1 file (1 op) — WI-c",
    ])
    monkeypatch.setattr(mod, "REPO_ROOT", repo)

    assert mod.count_tracker_only_commits(5) == 3
    # Only the newest two commits: "tracker: 1 file..." and "fix: second".
    assert mod.count_tracker_only_commits(2) == 1
    assert mod.count_tracker_only_commits(0) == 0
    assert mod.count_tracker_only_commits(-4) == 0


def test_a_subject_merely_mentioning_tracker_is_a_code_commit(tmp_path, mod, monkeypatch):
    repo = _repo_with_subjects(tmp_path, [
        "fix(tracker): reconcile no longer reports a false flush failure",
        "docs: tracker playbook",
    ])
    monkeypatch.setattr(mod, "REPO_ROOT", repo)

    assert mod.count_tracker_only_commits(2) == 0


def test_check_distance_excludes_tracker_commits(tmp_path, mod, monkeypatch):
    repo = _repo_with_subjects(tmp_path, [
        "feat: baseline",
        "tracker: 2 files (9 ops) — WI-a",
        "tracker: 2 files (9 ops) — WI-a",
        "tracker: 2 files (9 ops) — WI-a",
        "fix: a real change",
    ])
    monkeypatch.setattr(mod, "REPO_ROOT", repo)
    recorded = mod.get_commit_count() - 4  # the doc was generated right after "feat: baseline"

    raw, tracker_only, effective = mod.staleness_distance(recorded)

    assert (raw, tracker_only, effective) == (4, 3, 1)


def test_git_failure_counts_no_tracker_commits(tmp_path, mod, monkeypatch):
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path / "not-a-repo")

    assert mod.count_tracker_only_commits(3) == 0
