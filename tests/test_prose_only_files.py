# SPDX-License-Identifier: AGPL-3.0-or-later
"""``scripts/prose-only-files``: which changed files CI may leave out of its coverage gate.

The CI pytest step enforces 100% coverage on changed source files, and takes
out the files this script prints. A file printed by mistake is a code change
that escapes the gate, so the script is run on a throwaway repository holding
one file of each kind of change, and the two CI definitions are checked for
how they use the answer.
"""

# covers: scripts/prose-only-files
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "prose-only-files"

BASE = {
    "docstring.py": 'def f():\n    """Old."""\n    return 1\n',
    "comment.py": "x = 1  # old words\n",
    "code.py": "x = 1\n",
    "pragma.py": "x = 1  # pragma: no cover\n",
    "deleted.py": "x = 1\n",
    "unparsable.py": "x = 1\n",
    "same.py": "x = 1\n",
    "notes.txt": "old\n",
}
HEAD = {
    "docstring.py": 'def f():\n    """New."""\n    return 1\n',
    "comment.py": "x = 1  # new words\n",
    "code.py": "x = 2\n",
    "pragma.py": "x = 1\n",
    "unparsable.py": "x = (\n",
    "same.py": "x = 1\n",
    "notes.txt": "new\n",
    "added.py": '"""Only prose, but new."""\n',
}


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    commits = []
    for files in (BASE, HEAD):
        for old in repo.iterdir():
            if old.is_file():
                old.unlink()
        for name, text in files.items():
            (repo / name).write_text(text)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "state")
        commits.append(_git(repo, "rev-parse", "HEAD"))
    return repo, commits[0], commits[1]


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=repo,
                          capture_output=True, text=True)


def test_only_comment_and_docstring_changes_are_printed(tmp_path: Path) -> None:
    repo, base, head = _repo(tmp_path)
    done = _run(repo, base, head)
    assert done.returncode == 0, done.stderr
    # Not printed: a code edit, a removed pragma, a new file, a deleted file,
    # a file that no longer parses, an unchanged file, and a non-Python file.
    assert done.stdout.split() == ["comment.py", "docstring.py"]


def test_an_unreadable_commit_prints_nothing_and_fails(tmp_path: Path) -> None:
    """The CI step ignores the exit status, so nothing printed is what makes
    a failure exempt nothing."""
    repo, _base, head = _repo(tmp_path)
    done = _run(repo, "no-such-commit", head)
    assert (done.returncode, done.stdout) == (2, "")
    assert "cannot diff" in done.stderr


def test_wrong_arguments_print_nothing_and_fail(tmp_path: Path) -> None:
    repo, base, _head = _repo(tmp_path)
    done = _run(repo, base)
    assert (done.returncode, done.stdout) == (2, "")


# ── how CI uses the answer ───────────────────────────────────────────────────

CALL = 'python3 scripts/prose-only-files "$BASE" "$HEAD" > "$PROSE_ONLY_LIST" 2>/dev/null || : > "$PROSE_ONLY_LIST"'
FILTER = '| grep -vxF -f "$PROSE_ONLY_LIST" |'


def _gate_lines(path: str) -> list[str]:
    return [line.strip() for line in (REPO_ROOT / path).read_text().splitlines()
            if line.strip().startswith("ACTUAL_SOURCE_FILES=$(")]


def test_the_live_ci_step_takes_the_files_out_of_its_coverage_gate() -> None:
    text = (REPO_ROOT / ".woodpecker" / "woodpecker.yml").read_text()
    gates = _gate_lines(".woodpecker/woodpecker.yml")
    assert len(gates) == 1 and FILTER in gates[0]
    # Computed from the diff before the gate's file list is built, and a
    # failing helper leaves an empty list.
    assert text.count(CALL) == 1
    assert text.index(CALL) < text.index("ACTUAL_SOURCE_FILES=$(")


def test_the_github_twin_does_the_same_at_both_of_its_gates() -> None:
    text = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    gates = _gate_lines(".github/workflows/ci.yml")
    assert len(gates) == 2 and all(FILTER in gate for gate in gates)
    assert text.count(CALL) == 2
