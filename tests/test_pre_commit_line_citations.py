# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pre-commit hook checks line citations when a cited file is staged.

WHY. ``module_key_axis.py`` and ``signature_axis.py`` cite other source files
by path, line number and anchor text. An edit above a cited line moves it and
reddens the citation tests, which were found only about ten minutes into the
pre-PR run (PR 3: 12 citations; PR 4: 2). ``scripts/repin-line-citations
--check`` finds the same thing in about a second, so the hook runs it when a
staged file is a registry or a file a registry cites, and prints the command
that fixes it.

HOW THESE TESTS DRIVE IT. As ``test_pre_commit_could_not_run.py`` does: the
hook's ``check_line_citations`` function is extracted from the shipped file
and run alone (the whole hook would run every other gate), here in a
throw-away git repository holding a copy of the real
``scripts/repin-line-citations``, one registry and the file it cites.
"""

# covers: .githooks/pre-commit
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / ".githooks" / "pre-commit"
SCRIPT = REPO_ROOT / "scripts" / "repin-line-citations"

SRC = "packages/x/src"
TARGET = "# one\nx = needle(1)\n"
REGISTRY = f'S = Site(path="{SRC}/target.py", line=2, anchor="needle(")\n'


def _function(name: str) -> str:
    text = HOOK.read_text(encoding="utf-8")
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.MULTILINE | re.DOTALL)
    assert match, f"{name} is gone from the hook"
    return match.group(0)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository at a clean commit: one registry citing one file, plus a
    file nothing cites."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / "repin-line-citations")
    src = tmp_path / SRC
    src.mkdir(parents=True)
    (src / "target.py").write_text(TARGET)
    (src / "registry.py").write_text(REGISTRY)
    (src / "unrelated.py").write_text("y = 1\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "commit", "-q", "--no-verify", "-m", "base")
    return tmp_path


def _drive(repo: Path) -> subprocess.CompletedProcess[str]:
    script = "\n".join([
        "set -euo pipefail",
        "RED=''; GREEN=''; YELLOW=''; NC=''",
        _function("check_line_citations"),
        "check_line_citations",
        "echo AFTER",
    ])
    return subprocess.run(["bash", "-c", script], cwd=repo, capture_output=True, text=True)


def test_a_staged_cited_file_that_moved_a_citation_blocks_with_the_fix(repo: Path) -> None:
    (repo / SRC / "target.py").write_text("# inserted above the cited line\n" + TARGET)
    _git(repo, "add", f"{SRC}/target.py")
    done = _drive(repo)
    assert done.returncode == 1 and "AFTER" not in done.stdout, done.stdout
    assert "off (not rewritten)" in done.stdout and "2 -> 3" in done.stdout
    assert "Fix: ./scripts/repin-line-citations" in done.stdout
    assert f"    git add {SRC}/registry.py" in done.stdout  # the file to re-stage


def test_a_staged_registry_whose_own_line_is_off_blocks(repo: Path) -> None:
    (repo / SRC / "registry.py").write_text(REGISTRY.replace("line=2", "line=1"))
    _git(repo, "add", f"{SRC}/registry.py")
    done = _drive(repo)
    assert done.returncode == 1 and "AFTER" not in done.stdout, done.stdout
    assert "1 -> 2" in done.stdout


def test_a_staged_cited_file_with_citations_in_place_passes(repo: Path) -> None:
    (repo / SRC / "target.py").write_text(TARGET + "z = 3  # below the cited line\n")
    _git(repo, "add", f"{SRC}/target.py")
    done = _drive(repo)
    assert done.returncode == 0 and "AFTER" in done.stdout, done.stdout + done.stderr
    assert "Line citations... ✓" in done.stdout


def test_no_cited_file_staged_is_skipped(repo: Path) -> None:
    # The citation is off in the working tree, but no staged file can have
    # moved it, so this commit is not the one to stop.
    (repo / SRC / "target.py").write_text("# inserted\n" + TARGET)
    (repo / SRC / "unrelated.py").write_text("y = 2\n")
    _git(repo, "add", f"{SRC}/unrelated.py")
    done = _drive(repo)
    assert done.returncode == 0 and "AFTER" in done.stdout, done.stdout + done.stderr
    assert "skipped (no cited file staged)" in done.stdout


def test_an_anchor_gone_from_its_file_is_not_offered_the_re_pin(repo: Path) -> None:
    (repo / SRC / "target.py").write_text("# one\nx = renamed(1)\n")
    _git(repo, "add", f"{SRC}/target.py")
    done = _drive(repo)
    assert done.returncode == 1 and "AFTER" not in done.stdout, done.stdout
    assert "CANNOT RE-PIN" in done.stdout
    assert "by hand" in done.stdout


def test_a_check_that_cannot_run_says_so(repo: Path) -> None:
    (repo / SRC / "registry.py").write_text("def (:\n# line= anchor=\n")
    _git(repo, "add", f"{SRC}/registry.py")
    done = _drive(repo)
    assert done.returncode == 1 and "AFTER" not in done.stdout, done.stdout
    assert "could not run (exit 2)" in done.stdout
    assert "off (not rewritten)" not in done.stdout


def test_the_hook_runs_the_check() -> None:
    text = HOOK.read_text(encoding="utf-8")
    assert re.search(r"^check_line_citations$", text, re.MULTILINE)
    assert text.index("check_line_citations()") < text.index("\ncheck_line_citations\n")
