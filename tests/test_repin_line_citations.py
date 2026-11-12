# SPDX-License-Identifier: AGPL-3.0-or-later
"""``scripts/repin-line-citations``: move a cited line number back onto its anchor.

A reworded docstring above a cited line moves the line and reddens the test
that checks the citation, on a file whose code nobody touched. The script
rewrites the number. It must rewrite only numbers that are off, pick the
right line when the anchor occurs more than once, and refuse to guess when
the anchor is gone.
"""

# covers: scripts/repin-line-citations
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "repin-line-citations"

TARGET = "\n".join([
    "# one", "x = needle(1)", "# three", "# four", "y = needle(2)", "z = other()",
]) + "\n"
REGISTRY = '''SITES = (
    Site(path="target.py", line={on}, anchor="other()"),
    Site(
        path="target.py",
        line={first},
        anchor="needle(",
    ),
    Site(path="target.py", line={second}, anchor="needle("),
    Site(path="target.py", line=COMPUTED, anchor="needle("),
    Site(path="target.py", line=1),
)
'''


def _project(tmp_path: Path, **lines: int) -> Path:
    (tmp_path / "target.py").write_text(TARGET)
    registry = tmp_path / "registry.py"
    registry.write_text(REGISTRY.format(**{"on": 6, "first": 2, "second": 5, **lines}))
    return registry


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(tmp_path), *args, "registry.py"],
        capture_output=True, text=True)


def test_citations_on_their_anchor_are_left_alone(tmp_path: Path) -> None:
    registry = _project(tmp_path)
    before = registry.read_text()
    done = _run(tmp_path)
    assert (done.returncode, done.stdout) == (0, "")
    assert registry.read_text() == before


def test_a_moved_citation_is_rewritten_to_the_nearest_anchor_line(tmp_path: Path) -> None:
    """``needle(`` is on lines 2 and 5. A citation that drifted to 3 belongs
    to line 2, and one that drifted to 4 belongs to line 5."""
    registry = _project(tmp_path, first=3, second=4, on=1)
    done = _run(tmp_path)
    assert done.returncode == 0, done.stderr
    assert registry.read_text() == REGISTRY.format(on=6, first=2, second=5)
    assert done.stdout.count("re-pinned: target.py:") == 3
    assert "3 -> 2" in done.stdout and "4 -> 5" in done.stdout and "1 -> 6" in done.stdout
    assert "(nearest of [2, 5])" in done.stdout


def test_check_reports_and_rewrites_nothing(tmp_path: Path) -> None:
    registry = _project(tmp_path, first=3)
    before = registry.read_text()
    done = _run(tmp_path, "--check")
    assert done.returncode == 1
    assert "off (not rewritten): target.py: 3 -> 2" in done.stdout
    assert registry.read_text() == before


def test_a_missing_anchor_is_not_guessed(tmp_path: Path) -> None:
    """The cited code changed. The other citations are still re-pinned."""
    registry = _project(tmp_path, first=3)
    (tmp_path / "target.py").write_text(TARGET.replace("z = other()", "z = renamed()"))
    done = _run(tmp_path)
    assert done.returncode == 1
    assert "CANNOT RE-PIN" in done.stderr and "'other()'" in done.stderr
    assert registry.read_text() == REGISTRY.format(on=6, first=2, second=5)


def test_a_cited_file_that_is_gone_is_reported(tmp_path: Path) -> None:
    _project(tmp_path)
    (tmp_path / "target.py").unlink()
    done = _run(tmp_path)
    assert done.returncode == 1
    assert done.stderr.count("target.py cannot be read") == 3


def test_an_unparsable_registry_is_an_error(tmp_path: Path) -> None:
    _project(tmp_path)
    (tmp_path / "registry.py").write_text("def (:\n")
    done = _run(tmp_path)
    assert done.returncode == 2 and "registry.py" in done.stderr


def test_the_repositorys_own_citations_are_found_and_on_their_anchors() -> None:
    """With no file named, the script finds the registries by their keywords.
    Reach: it must find at least the two that exist, or this passes on nothing."""
    listed = subprocess.run(
        ["git", "grep", "-l", "--all-match", "-e", "anchor=", "-e", "line=", "--",
         "packages/*/src/*.py"], cwd=REPO_ROOT, capture_output=True, text=True)
    found = set(listed.stdout.split())
    assert {"packages/hypergumbo-core/src/hypergumbo_core/module_key_axis.py",
            "packages/hypergumbo-core/src/hypergumbo_core/signature_axis.py"} <= found
    done = subprocess.run([sys.executable, str(SCRIPT), "--root", str(REPO_ROOT), "--check"],
                          capture_output=True, text=True)
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")
