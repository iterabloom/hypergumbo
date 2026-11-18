# SPDX-License-Identifier: AGPL-3.0-or-later
"""``--statement-deselect=FILE``: the tests a change does not need are left out.

smart-test passes the by-statement selector's left-out node ids in a file
(``coverage-select statement-narrow``). Each test here runs pytest on a
throwaway project in a subprocess, with and without pytest-xdist and its
``loadgroup`` renaming, and checks which tests ran.
"""

# covers: scripts/pytest_plugins/pytest_statement_deselect.py
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "scripts" / "pytest_plugins"

_PROJECT = {
    "test_a.py": (
        "import pytest\n\n"
        "def test_keep():\n    pass\n\n"
        "def test_drop():\n    pass\n\n"
        "@pytest.mark.parametrize('i', [1, 2])\n"
        "def test_param(i):\n    pass\n\n"
        "@pytest.mark.xdist_group('g')\n"
        "def test_grouped():\n    pass\n"
    ),
}


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    for name, text in _PROJECT.items():
        (tmp_path / name).write_text(text)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PYTEST_", "COV_CORE_", "COVERAGE_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(PLUGIN_DIR), *sys.path])
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "pytest_statement_deselect",
         "-o", "addopts=", "-p", "no:cacheprovider", "-q", "-rA", "--rootdir", str(tmp_path),
         *args, str(tmp_path)],
        capture_output=True, text=True, cwd=tmp_path, env=env)


def _passed(done: subprocess.CompletedProcess) -> set[str]:
    return set(re.findall(r"^PASSED (\S+)", done.stdout, re.MULTILINE))


@pytest.mark.parametrize("workers", [["-n", "0"], ["-n", "2", "--dist", "loadgroup"]])
def test_the_listed_tests_are_deselected_and_the_rest_run(
        tmp_path: Path, workers: list[str]) -> None:
    listed = tmp_path / "left_out.txt"
    listed.write_text("test_a.py::test_drop\ntest_a.py::test_param[2]\ntest_a.py::test_grouped\n\n")
    done = _run(tmp_path, f"--statement-deselect={listed}", *workers)
    assert done.returncode == 0, done.stdout + done.stderr
    assert _passed(done) == {"test_a.py::test_keep", "test_a.py::test_param[1]"}
    if workers[1] == "0":
        # Under xdist the workers deselect, and the controller's summary line
        # does not count them; smart-test prints its own count.
        assert "3 deselected" in done.stdout


@pytest.mark.parametrize("content", [None, "", "test_a.py::test_not_there\n"])
def test_no_file_an_empty_one_or_unknown_ids_deselect_nothing(
        tmp_path: Path, content: str | None) -> None:
    listed = tmp_path / "left_out.txt"
    if content is not None:
        listed.write_text(content)
    done = _run(tmp_path, f"--statement-deselect={listed}", "-n", "0")
    assert done.returncode == 0, done.stdout + done.stderr
    assert len(_passed(done)) == 5
    assert "deselected" not in done.stdout


def test_without_the_option_everything_runs(tmp_path: Path) -> None:
    done = _run(tmp_path, "-n", "0")
    assert len(_passed(done)) == 5
