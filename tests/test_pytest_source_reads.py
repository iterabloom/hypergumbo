# SPDX-License-Identifier: AGPL-3.0-or-later
"""``--source-reads-dir``: which tests read a repository ``.py`` file as data.

smart-test's prose-only rule drops a test for a comment-or-docstring change
only when this record shows the test does not read the changed file. So a read
the plugin misses is a test wrongly dropped, and each way of reading (and each
way of NOT reading) has its own test here. Every test runs pytest on a
throwaway project in a subprocess, with and without pytest-xdist.
"""

# covers: scripts/pytest_plugins/pytest_source_reads.py
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "scripts" / "pytest_plugins"

_PROJECT = {
    "conftest.py": (
        "import pathlib, pytest\n"
        "AT_CONFTEST_IMPORT = pathlib.Path(__file__).with_name('read_by_conftest.py').read_text()\n"
        "@pytest.fixture(scope='module')\n"
        "def built():\n"
        "    return pathlib.Path(__file__).with_name('read_by_fixture.py').read_text()\n"
        "@pytest.fixture(scope='session')\n"
        "def lazy():\n"
        "    return pathlib.Path(__file__).with_name('read_by_lazy_fixture.py').read_text()\n"
    ),
    "read_by_conftest.py": "A = 1\n",
    "read_by_fixture.py": "B = 2\n",
    "read_by_lazy_fixture.py": "B2 = 2\n",
    "read_by_test.py": "C = 3\n",
    "read_at_module_import.py": "D = 4\n",
    "read_by_helper_body.py": "E = 5\n",
    "only_imported.py": "F = 6\n",
    "read_by_getsource.py": "H = 8\n",
    "helper.py": (
        "import pathlib\n"
        "CACHED = pathlib.Path(__file__).with_name('read_by_helper_body.py').read_text()\n"
    ),
    "test_a.py": (
        "import pathlib, pkgutil, subprocess, sys\n"
        "HERE = pathlib.Path(__file__).parent\n"
        "AT_IMPORT = (HERE / 'read_at_module_import.py').read_text()\n"
        "def test_reads():\n"
        "    assert 'C' in (HERE / 'read_by_test.py').read_text()\n"
        "def test_imports_only():\n"
        "    import only_imported\n"
        "    assert only_imported.F == 6\n"
        "def test_writes(tmp_path):\n"
        "    (HERE / 'written.py').write_text('G = 7\\n')\n"
        "def test_spawns():\n"
        "    subprocess.run([sys.executable, '-c', 'pass'], check=True)\n"
        "def test_uses_fixture(built):\n"
        "    assert 'B' in built\n"
        "def test_fetches_fixture(request):\n"
        "    assert request.getfixturevalue('lazy')\n"
        "def test_loader_get_data():\n"
        "    import only_imported\n"
        "    assert only_imported.__loader__.get_data(only_imported.__file__)\n"
        "def test_getsource():\n"
        "    import inspect, read_by_getsource\n"
        "    assert 'H' in inspect.getsource(read_by_getsource)\n"
        "def test_plain():\n"
        "    pass\n"
    ),
    "test_b.py": (
        "def test_uses_fixture_too(built):\n"
        "    assert built\n"
        "def test_lazy_import():\n"
        "    import helper\n"
        "    assert helper.CACHED\n"
        "def test_b_plain():\n"
        "    pass\n"
    ),
}


def _run(tmp_path: Path, *args: str) -> dict:
    project = tmp_path / "project"
    project.mkdir()
    for name, text in _PROJECT.items():
        (project / name).write_text(text)
    out = tmp_path / "reads"
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PYTEST_", "COV_CORE_", "COVERAGE_"))}
    env["PYTHONPATH"] = str(PLUGIN_DIR)
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "pytest_source_reads",
         "-o", "addopts=", "-p", "no:cacheprovider", "-q", "--rootdir", str(project),
         *args, str(project)],
        capture_output=True, text=True, cwd=project, env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    merged: dict = {"tests": {}, "fixtures": {}, "modules": {}, "expected": {},
                    "unattributed": {"reads": set(), "opaque": set()}, "files": 0}
    for part in sorted(out.glob("*.json")) if out.is_dir() else []:
        data = json.loads(part.read_text())
        merged["files"] += 1
        merged["tests"].update(data["tests"])
        for key in ("fixtures", "modules"):
            for name, bucket in data[key].items():
                slot = merged[key].setdefault(name, {"reads": set(), "opaque": set()})
                slot["reads"].update(bucket["reads"])
                slot["opaque"].update(bucket["opaque"])
        for name, ids in data["expected"].items():
            merged["expected"].setdefault(name, set()).update(ids)
        merged["unattributed"]["reads"].update(data["unattributed"]["reads"])
        merged["unattributed"]["opaque"].update(data["unattributed"]["opaque"])
    return merged


WORKERS = pytest.mark.parametrize("workers", ["0", "2"])


def _recorded(tmp_path: Path, workers: str) -> dict:
    return _run(tmp_path, f"--source-reads-dir={tmp_path / 'reads'}", "-n", workers)


@WORKERS
def test_a_read_in_a_test_is_that_tests(tmp_path: Path, workers: str) -> None:
    tests = _recorded(tmp_path, workers)["tests"]
    assert tests["test_a.py::test_reads"]["reads"] == ["read_by_test.py"]
    for quiet in ("test_a.py::test_plain", "test_b.py::test_b_plain"):
        assert tests[quiet]["reads"] == [] and tests[quiet]["opaque"] == []


@WORKERS
def test_an_import_and_a_write_are_not_reads(tmp_path: Path, workers: str) -> None:
    """The controls: a plugin that recorded every open would keep every test."""
    data = _recorded(tmp_path, workers)
    assert data["tests"]["test_a.py::test_imports_only"]["reads"] == []
    assert data["tests"]["test_a.py::test_writes"]["reads"] == []
    # only_imported.py is read on purpose by test_loader_get_data, so the
    # files checked here are the ones nothing reads as data.
    everything = {p for t in data["tests"].values() for p in t["reads"]}
    everything |= data["unattributed"]["reads"]
    assert "written.py" not in everything
    assert "helper.py" not in everything and "conftest.py" not in everything


@WORKERS
def test_a_direct_loader_read_is_a_read(tmp_path: Path, workers: str) -> None:
    """``loader.get_data()`` called by a test is the import system's code but
    not an import: one frozen frame, not two."""
    tests = _recorded(tmp_path, workers)["tests"]
    assert tests["test_a.py::test_loader_get_data"]["reads"] == ["only_imported.py"]


@WORKERS
def test_a_spawn_makes_the_test_opaque(tmp_path: Path, workers: str) -> None:
    tests = _recorded(tmp_path, workers)["tests"]
    assert tests["test_a.py::test_spawns"]["opaque"] == [
        f"subprocess.Popen:{Path(sys.executable).name}"]
    assert tests["test_a.py::test_reads"]["opaque"] == []


@WORKERS
def test_a_shared_fixtures_read_reaches_every_user(tmp_path: Path, workers: str) -> None:
    """The fixture runs once per module; its second user reads nothing itself."""
    data = _recorded(tmp_path, workers)
    assert data["fixtures"]["::built"]["reads"] == {"read_by_fixture.py"}
    for user in ("test_a.py::test_uses_fixture", "test_b.py::test_uses_fixture_too"):
        assert "::built" in data["tests"][user]["fixtures"]
    assert "::built" not in data["tests"]["test_a.py::test_plain"]["fixtures"]


@WORKERS
def test_a_fixture_fetched_by_name_is_recorded_as_used(tmp_path: Path, workers: str) -> None:
    data = _recorded(tmp_path, workers)
    assert data["fixtures"]["::lazy"]["reads"] == {"read_by_lazy_fixture.py"}
    assert "::lazy" in data["tests"]["test_a.py::test_fetches_fixture"]["fixtures"]


@WORKERS
def test_a_read_while_a_test_module_imports_is_the_modules(tmp_path: Path, workers: str) -> None:
    data = _recorded(tmp_path, workers)
    assert data["modules"]["test_a.py"]["reads"] == {"read_at_module_import.py"}
    assert data["modules"]["test_b.py"]["reads"] == set()


@WORKERS
def test_a_read_in_any_other_module_body_is_everyones(tmp_path: Path, workers: str) -> None:
    """A conftest, and a helper imported inside one test: each runs once per
    process and keeps what it read, so no single test owns the read."""
    data = _recorded(tmp_path, workers)
    assert {"read_by_conftest.py", "read_by_helper_body.py"} <= data["unattributed"]["reads"]
    assert data["tests"]["test_b.py::test_lazy_import"]["reads"] == []
    assert data["unattributed"]["opaque"] == set()


@WORKERS
def test_a_read_through_linecache_is_everyones(tmp_path: Path, workers: str) -> None:
    """inspect.getsource reads once per process and then serves its cache, so
    the first caller's read stands for every later caller."""
    data = _recorded(tmp_path, workers)
    assert "read_by_getsource.py" in data["unattributed"]["reads"]
    assert data["tests"]["test_a.py::test_getsource"]["reads"] == []


@WORKERS
def test_every_collected_test_is_listed(tmp_path: Path, workers: str) -> None:
    data = _recorded(tmp_path, workers)
    assert len(data["expected"]["test_a.py"]) == 9
    assert data["expected"]["test_b.py"] == {
        "test_b.py::test_uses_fixture_too", "test_b.py::test_lazy_import",
        "test_b.py::test_b_plain"}
    assert set(data["tests"]) == data["expected"]["test_a.py"] | data["expected"]["test_b.py"]


def test_xdist_writes_one_file_per_worker_and_none_for_the_controller(tmp_path: Path) -> None:
    assert _recorded(tmp_path, "2")["files"] == 2


def test_without_the_option_nothing_is_written(tmp_path: Path) -> None:
    assert _run(tmp_path)["files"] == 0
    assert not (tmp_path / "reads").exists()
