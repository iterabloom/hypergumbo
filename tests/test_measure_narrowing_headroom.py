# SPDX-License-Identifier: AGPL-3.0-or-later
"""``measure-narrowing-headroom.py`` must say which ``selection_index`` it ran.

WI-tumog. The script inserted its own tree's ``hypergumbo-core/src`` at
``sys.path[0]`` unconditionally, so a PYTHONPATH naming another tree was
ignored: an A/B of two selector versions selected by PYTHONPATH measured the
script's tree twice and printed two identical, plausible reports, exit 0.

The fake ``hypergumbo_core`` below announces itself and exits 97 on import, so
which copy ran is observable from outside the child. The child runs in a fresh
``git init`` directory with a throwaway index path, so the report itself is the
empty "nothing to measure" one: these tests pin the CHOICE of code, not the
headroom numbers.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from helpers_code_tree import (
    CHOICE_ENV,
    FAKE_EXIT,
    MARKER,
    REPO_ROOT,
    fake_tree,
    own_pythonpath,
    run_script,
)

SCRIPT = REPO_ROOT / "scripts" / "measure-narrowing-headroom.py"


@pytest.fixture()
def empty_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(repo)], check=True,
    )
    return repo


def _run(repo: Path, tmp_path: Path, **kw: object) -> subprocess.CompletedProcess[str]:
    return run_script(
        SCRIPT, cwd=repo,
        extra_env={"HG_SELECTION_INDEX": str(tmp_path / "index.sqlite")},
        **kw,  # type: ignore[arg-type]
    )


def test_a_foreign_pythonpath_is_refused_not_silently_ignored(
    empty_repo: Path, tmp_path: Path,
) -> None:
    fake = fake_tree(tmp_path / "fake", ["hypergumbo_core"])
    proc = _run(empty_repo, tmp_path, pythonpath=fake)
    assert proc.returncode == 2, (proc.returncode, proc.stdout[:400])
    assert CHOICE_ENV in proc.stderr
    assert fake in proc.stderr
    assert MARKER not in proc.stderr
    assert proc.stdout == ""


def test_choosing_pythonpath_imports_the_pythonpath_copy(
    empty_repo: Path, tmp_path: Path,
) -> None:
    fake = fake_tree(tmp_path / "fake", ["hypergumbo_core"])
    proc = _run(empty_repo, tmp_path, pythonpath=fake, choice="pythonpath")
    assert proc.returncode == FAKE_EXIT, (proc.returncode, proc.stderr[-400:])
    assert f"{MARKER} hypergumbo_core" in proc.stderr


def test_choosing_script_runs_this_tree_and_says_so_first(
    empty_repo: Path, tmp_path: Path,
) -> None:
    fake = fake_tree(tmp_path / "fake", ["hypergumbo_core"])
    proc = _run(empty_repo, tmp_path, pythonpath=fake, choice="script")
    assert proc.returncode == 0, proc.stderr[-400:]
    assert MARKER not in proc.stderr
    first = proc.stdout.splitlines()[0]
    assert "code under test" in first and str(REPO_ROOT) in first
    assert "nothing to measure" in proc.stdout


def test_pythonpath_naming_this_tree_is_not_a_conflict(
    empty_repo: Path, tmp_path: Path,
) -> None:
    proc = _run(empty_repo, tmp_path, pythonpath=own_pythonpath())
    assert proc.returncode == 0, proc.stderr[-400:]
    assert str(REPO_ROOT) in proc.stdout.splitlines()[0]
