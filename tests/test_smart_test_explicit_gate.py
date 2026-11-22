# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-birog: an explicit-path smart-test run says what it did not gate, and can gate.

THE BUG. ``scripts/smart-test <test files>`` ran pytest and exited. It never
asked which source files the branch changed against the merge base, so it never
applied the 100% gate that the self-selected run and PR CI apply to them (CI:
``coverage report --include=<changed source files> --fail-under=100``). The
pre-PR recipe's explicit runs returned 0 with a changed file at 95% or 98%,
committed or not, and PR CI then failed on the line.

THE RULE pinned here. After a GREEN explicit run:

- without ``--gate-changed``: a ``NOT GATED (explicit run)`` notice names the
  merge base and lists each changed source file under ``packages/*/src`` with
  its percent IN THIS RUN and its missing lines; a file the run did not measure
  is said to be unmeasured, never shown as 0% or 100%. Exit 0: it is a notice,
  not a failure, because an explicit run of a few test files misses lines other
  tests cover.
- with ``--gate-changed``: the same files are gated at 100%, as the self-selected
  run gates them; below it the run exits non-zero naming the file and the lines.
- a change with no package source says there is nothing to gate (not silence).
- a RED run keeps its verdict and makes no coverage claim.

THE HARNESS is ``test_smart_test_zero_collect``'s: the shipped script copied
into a throwaway git repo, real pytest and pytest-cov, a stub stable
hypergumbo and a stub mypy ratchet. The fixture's source lives under
``packages/hypergumbo-core/src`` because that is one of the paths the script
measures (``COV_PATHS``; the fixture is not target mode, so the list is this
repository's). ``packages/hypergumbo-tracker/src`` is never measured, which is
how the unmeasured case is built.
"""

# covers: scripts/smart-test
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from test_smart_test_zero_collect import _commit, _fixture_repo, _git, _smart_test

SRC = "packages/hypergumbo-core/src/fxpkg/mod.py"
UNMEASURED_SRC = "packages/hypergumbo-tracker/src/fxtrk/mod.py"

CONFTEST = (
    "import sys\n"
    "sys.path.insert(0, 'packages/hypergumbo-core/src')\n"
    "sys.path.insert(0, 'packages/hypergumbo-tracker/src')\n"
)
BASE_MOD = "def covered(x):\n    return x + 1\n"
# Line 6, the body of `uncovered`, is what no test runs.
GAP_MOD = BASE_MOD + "\n\ndef uncovered(x):\n    return x * 2\n"
FULL_MOD = "def covered(x):\n    return x + 2\n"
TEST = (
    "from fxpkg.mod import covered\n\n"
    "def test_covered():\n    assert covered(1) >= 2\n"
)


def _repo(tmp_path: Path) -> Path:
    repo = _fixture_repo(tmp_path, {
        "conftest.py": CONFTEST,
        "packages/hypergumbo-core/src/fxpkg/__init__.py": "",
        SRC: BASE_MOD,
        "tests/test_mod.py": TEST,
    })
    # A merge base to name: origin/dev at the base commit.
    _git(repo, "update-ref", "refs/remotes/origin/dev", "HEAD")
    return repo


def _base_short(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "--short", "origin/dev"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _run(repo: Path, tmp_path: Path, *args: str) -> tuple[int, str]:
    result = _smart_test(repo, tmp_path, "tests/test_mod.py", *args)
    return result.returncode, result.stdout + result.stderr


def _notice(out: str) -> str:
    assert "NOT GATED (explicit run)" in out, out
    return out.split("NOT GATED (explicit run)", 1)[1]


def test_a_committed_gap_is_named_and_not_gated(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / SRC).write_text(GAP_MOD)
    _commit(repo)

    rc, out = _run(repo, tmp_path)

    assert rc == 0, f"the notice must not fail the run\n{out}"
    notice = _notice(out)
    assert _base_short(repo) in notice, f"the merge base is not named\n{out}"
    row = next((ln for ln in notice.splitlines() if SRC in ln), "")
    assert re.search(r"\b([0-9]|[1-9][0-9])%", row), (
        f"the changed file's percent in this run is not shown\n{out}"
    )
    assert "missing: 6" in row, f"the missing line is not named\n{out}"
    assert "❌" not in out, f"a notice was worded as a failure\n{out}"
    assert "--gate-changed" in notice, out


def test_a_committed_gap_fails_with_the_flag(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / SRC).write_text(GAP_MOD)
    _commit(repo)

    rc, out = _run(repo, tmp_path, "--gate-changed")

    assert rc != 0, f"--gate-changed passed a changed file below 100%\n{out}"
    assert "❌" in out, out
    after = out.split("❌", 1)[1]
    assert SRC in after and re.search(r"\b6\b", after), (
        f"the failure must name the file and its missing line\n{out}"
    )
    assert "NOT GATED" not in out, out


def test_an_uncommitted_gap_is_named_and_fails_with_the_flag(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    (repo / SRC).write_text(GAP_MOD)  # left unstaged

    rc, out = _run(repo, tmp_path)
    assert rc == 0, out
    row = next((ln for ln in _notice(out).splitlines() if SRC in ln), "")
    assert "missing: 6" in row, out

    rc, out = _run(repo, tmp_path, "--gate-changed")
    assert rc != 0, out
    assert SRC in out.split("❌", 1)[1], out


def test_a_fully_covered_change_passes_with_and_without_the_flag(
    tmp_path: Path,
) -> None:
    """The control: without it, a gate that always failed would pass above."""
    repo = _repo(tmp_path)
    (repo / SRC).write_text(FULL_MOD)
    _commit(repo)

    rc, out = _run(repo, tmp_path)
    assert rc == 0, out
    row = next((ln for ln in _notice(out).splitlines() if SRC in ln), "")
    assert "100%" in row and "missing" not in row, out

    rc, out = _run(repo, tmp_path, "--gate-changed")
    assert rc == 0, out
    assert "✅ All changed source files are at 100% coverage" in out, out


def test_a_change_with_no_package_source_says_nothing_to_gate(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    (repo / "tests" / "test_mod.py").write_text(TEST + "\n# edited\n")
    _commit(repo)

    for flag in ((), ("--gate-changed",)):
        rc, out = _run(repo, tmp_path, *flag)
        assert rc == 0, out
        assert "nothing to gate" in out, (
            f"a change with no package source must say so, not stay silent\n{out}"
        )
        assert _base_short(repo) in out, out


def test_an_unmeasured_file_is_said_to_be_unmeasured(tmp_path: Path) -> None:
    """Its package is not under --cov: not 0%, not 100%, not measured."""
    repo = _repo(tmp_path)
    (repo / UNMEASURED_SRC).parent.mkdir(parents=True)
    (repo / UNMEASURED_SRC).write_text("VALUE = 1\n")
    _commit(repo)

    rc, out = _run(repo, tmp_path)
    assert rc == 0, out
    row = next((ln for ln in _notice(out).splitlines() if UNMEASURED_SRC in ln), "")
    assert "not measured in this run" in row, out
    assert "%" not in row, out


def test_a_run_without_coverage_measures_nothing_and_cannot_gate(
    tmp_path: Path,
) -> None:
    """--no-cov: whatever .coverage a previous run left is not this run's."""
    repo = _repo(tmp_path)
    (repo / SRC).write_text(GAP_MOD)
    _commit(repo)
    _run(repo, tmp_path)  # leaves a .coverage from a run WITH coverage

    rc, out = _run(repo, tmp_path, "--no-cov")
    assert rc == 0, out
    row = next((ln for ln in _notice(out).splitlines() if SRC in ln), "")
    assert "not measured in this run" in row and "%" not in row, (
        f"a previous run's coverage data answered for this run\n{out}"
    )

    rc, out = _run(repo, tmp_path, "--no-cov", "--gate-changed")
    assert rc != 0, f"--gate-changed passed with nothing measured\n{out}"
    assert "cannot be gated" in out, out


def test_a_red_run_keeps_its_verdict_and_makes_no_coverage_claim(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    (repo / SRC).write_text(GAP_MOD)
    (repo / "tests" / "test_mod.py").write_text(TEST + "\ndef test_red():\n    assert False\n")
    _commit(repo)

    for flag in ((), ("--gate-changed",)):
        rc, out = _run(repo, tmp_path, *flag)
        assert rc != 0, out
        assert "test_red" in out, out
        assert "GATED" not in out, (
            f"a red run made a coverage claim\n{out}"
        )
