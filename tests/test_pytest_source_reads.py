# SPDX-License-Identifier: AGPL-3.0-or-later
"""``--source-reads-dir``: how each test can see the text of a repository ``.py`` file.

smart-test's prose-only rule leaves a test out of a comment-or-docstring
change only when this record shows the test sees none of the changed text. So
an event the plugin misses is a test wrongly left out, and each way of seeing
(and each way of NOT seeing) has its own test here. pytest runs on a throwaway
project in a subprocess, once without pytest-xdist and once with it, and every
test below reads one of those two records.
"""

# covers: scripts/pytest_plugins/pytest_source_reads.py
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "scripts" / "pytest_plugins"
KINDS = ("reads", "opaque", "docs", "lines")

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
    "hashed.py": "I = 9\n",
    "helper.py": (
        "import pathlib\n"
        "CACHED = pathlib.Path(__file__).with_name('read_by_helper_body.py').read_text()\n"
    ),
    "documented.py": (
        '"""Module doc."""\n'
        "class K:\n"
        '    """K doc."""\n'
        "    def m(self):\n"
        '        """m doc."""\n'
        "    @property\n"
        "    def p(self):\n"
        '        """p doc."""\n'
        "def f():\n"
        '    """f doc."""\n'
    ),
    "observer.py": (
        '"""Observer doc."""\n'
        "import hashlib, inspect, sys, traceback\n"
        "from inspect import getdoc\n"            # an import is not a site
        "def doc_of(obj):\n"
        "    return obj.__doc__\n"
        "def own():\n"
        "    return __doc__\n"
        "def wholesale(obj):\n"
        "    return getdoc(obj)\n"
        "def each(objs):\n"
        "    return [o.__doc__ for o in objs]\n"   # `o` is unbound when the line starts
        "def first_line(fn):\n"
        "    return fn.__code__.co_firstlineno\n"
        "def here():\n"
        "    return sys._getframe().f_lineno\n"
        "def digest(module):\n"
        "    return hashlib.sha256(inspect.getsource(module).encode()).hexdigest()\n"
        "def failure():\n"
        "    try:\n"
        "        raise ValueError('x')\n"
        "    except ValueError:\n"
        "        return traceback.format_exc()\n"
    ),
    "own_at_import.py": '"""Own."""\nTOP = __doc__\n',
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
    "test_c.py": (
        "import inspect, os, subprocess\n"
        "import documented, hashed, observer, read_by_getsource\n"
        "def test_doc_of_a_function():\n"
        "    assert observer.doc_of(documented.f)\n"
        "def test_doc_of_a_class_a_method_and_a_property():\n"
        "    assert observer.doc_of(documented.K) and observer.doc_of(documented.K().m)\n"
        "    assert observer.doc_of(documented.K.p)\n"
        "def test_doc_of_an_instance():\n"
        "    assert observer.doc_of(documented.K())\n"
        "def test_doc_of_a_module():\n"
        "    assert observer.doc_of(documented)\n"
        "def test_doc_of_a_builtin():\n"
        "    assert observer.doc_of(int)\n"
        "def test_own_doc():\n"
        "    assert observer.own()\n"
        "def test_docs_wholesale():\n"
        "    assert observer.wholesale(documented.f)\n"
        "def test_docs_unevaluable():\n"
        "    assert observer.each([documented.f])\n"
        "def test_own_doc_at_import():\n"
        "    import own_at_import\n"
        "    assert own_at_import.TOP\n"
        "def test_line_of_a_function():\n"
        "    assert observer.first_line(documented.f)\n"
        "def test_line_untied():\n"
        "    assert observer.here()\n"
        "def test_getsource_first():\n"
        "    assert 'H' in inspect.getsource(read_by_getsource)\n"
        "def test_getsource_again():\n"
        "    assert 'H' in inspect.getsource(read_by_getsource)\n"
        "def test_hash_only():\n"
        "    assert observer.digest(hashed)\n"
        "def test_traceback():\n"
        "    assert 'ValueError' in observer.failure()\n"
        "def test_git_elsewhere(tmp_path):\n"
        "    subprocess.run(['git', 'init', '-q'], cwd=tmp_path, check=True)\n"
        "def test_git_here():\n"
        "    subprocess.run(['git', '--version'], check=True)\n"
        "def test_git_pointed_here(tmp_path):\n"
        "    subprocess.run(['git', '-C', os.getcwd(), '--version'], cwd=tmp_path, check=True)\n"
        "def test_git_dir_equals_here(tmp_path):\n"
        "    subprocess.run(['git', '--git-dir=' + os.getcwd(), '--version'], cwd=tmp_path, check=True)\n"
        "def test_git_env_here(tmp_path):\n"
        "    subprocess.run(['git', '--version'], cwd=tmp_path, check=True,\n"
        "                   env={**os.environ, 'GIT_WORK_TREE': os.getcwd()})\n"
        "def test_git_as_a_shell_string(tmp_path):\n"
        "    subprocess.run('git --version', shell=True, cwd=tmp_path, check=True)\n"
    ),
}


def _run(tmp_path: Path, *args: str) -> dict[str, Any]:
    project = tmp_path / "project"
    project.mkdir()
    for name, text in _PROJECT.items():
        (project / name).write_text(text)
    out = tmp_path / "reads"
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PYTEST_", "COV_CORE_", "COVERAGE_", "GIT_"))}
    env["PYTHONPATH"] = str(PLUGIN_DIR)
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "pytest_source_reads",
         "-o", "addopts=", "-p", "no:cacheprovider", "-q", "--rootdir", str(project),
         *args, str(project)],
        capture_output=True, text=True, cwd=project, env=env)
    assert done.returncode == 0, done.stdout + done.stderr

    def empty() -> dict[str, set[str]]:
        return {kind: set() for kind in KINDS}

    merged: dict[str, Any] = {
        "tests": {}, "fixtures": {}, "modules": {}, "expected": {},
        "unattributed": empty(), "files": 0, "versions": set(), "observed": set()}
    for part in sorted(out.glob("*.json")) if out.is_dir() else []:
        data = json.loads(part.read_text())
        merged["files"] += 1
        merged["versions"].add(data["version"])
        merged["observed"].add(data["observed"])
        merged["tests"].update(data["tests"])
        for key in ("fixtures", "modules"):
            for name, bucket in data[key].items():
                slot = merged[key].setdefault(name, empty())
                for kind in KINDS:
                    slot[kind].update(bucket[kind])
        for name, ids in data["expected"].items():
            merged["expected"].setdefault(name, set()).update(ids)
        for kind in KINDS:
            merged["unattributed"][kind].update(data["unattributed"][kind])
    return merged


@pytest.fixture(scope="module", params=["0", "2"])
def recorded(request: pytest.FixtureRequest,
             tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """The record of one run of the project, without xdist and with it."""
    tmp_path = tmp_path_factory.mktemp("recorded")
    return _run(tmp_path, f"--source-reads-dir={tmp_path / 'reads'}",
                "--source-reads-hash-only=observer:digest", "-n", request.param)


def _seen(recorded: dict[str, Any], test: str) -> dict[str, list[str]]:
    return {kind: recorded["tests"][test][kind] for kind in KINDS
            if recorded["tests"][test][kind]}


# ── reads ───────────────────────────────────────────────────────────────────


def test_a_read_in_a_test_is_that_tests(recorded: dict[str, Any]) -> None:
    assert _seen(recorded, "test_a.py::test_reads") == {"reads": ["read_by_test.py"]}
    for quiet in ("test_a.py::test_plain", "test_b.py::test_b_plain"):
        assert _seen(recorded, quiet) == {}


def test_an_import_and_a_write_are_not_reads(recorded: dict[str, Any]) -> None:
    """The controls: a plugin that recorded every open would keep every test."""
    assert _seen(recorded, "test_a.py::test_imports_only") == {}
    assert _seen(recorded, "test_a.py::test_writes") == {}
    # only_imported.py is read on purpose by test_loader_get_data, so the
    # files checked here are the ones nothing reads as data.
    everything = {p for t in recorded["tests"].values() for p in t["reads"]}
    everything |= recorded["unattributed"]["reads"]
    assert "written.py" not in everything
    assert "helper.py" not in everything and "conftest.py" not in everything


def test_a_direct_loader_read_is_a_read(recorded: dict[str, Any]) -> None:
    """``loader.get_data()`` called by a test is the import system's code but
    not an import: one frozen frame, not two."""
    assert _seen(recorded, "test_a.py::test_loader_get_data") == {
        "reads": ["only_imported.py"]}


def test_a_shared_fixtures_read_reaches_every_user(recorded: dict[str, Any]) -> None:
    """The fixture runs once per module; its second user reads nothing itself."""
    assert recorded["fixtures"]["::built"]["reads"] == {"read_by_fixture.py"}
    for user in ("test_a.py::test_uses_fixture", "test_b.py::test_uses_fixture_too"):
        assert "::built" in recorded["tests"][user]["fixtures"]
    assert "::built" not in recorded["tests"]["test_a.py::test_plain"]["fixtures"]


def test_a_fixture_fetched_by_name_is_recorded_as_used(recorded: dict[str, Any]) -> None:
    assert recorded["fixtures"]["::lazy"]["reads"] == {"read_by_lazy_fixture.py"}
    assert "::lazy" in recorded["tests"]["test_a.py::test_fetches_fixture"]["fixtures"]


def test_a_read_while_a_test_module_imports_is_the_modules(recorded: dict[str, Any]) -> None:
    assert recorded["modules"]["test_a.py"]["reads"] == {"read_at_module_import.py"}
    assert recorded["modules"]["test_b.py"]["reads"] == set()


def test_a_read_in_any_other_module_body_is_everyones(recorded: dict[str, Any]) -> None:
    """A conftest, and a helper imported inside one test: each runs once per
    process and keeps what it read, so no single test owns the read."""
    assert recorded["unattributed"]["reads"] == {
        "read_by_conftest.py", "read_by_helper_body.py"}
    assert _seen(recorded, "test_b.py::test_lazy_import") == {}
    assert recorded["unattributed"]["opaque"] == set()


def test_every_reader_through_linecache_is_recorded(recorded: dict[str, Any]) -> None:
    """``inspect.getsource`` opens the file once per process and then serves
    a cache. The second caller opens nothing and is a reader all the same."""
    for test in ("test_c.py::test_getsource_first", "test_c.py::test_getsource_again"):
        assert _seen(recorded, test) == {"reads": ["read_by_getsource.py"]}


def test_a_formatted_traceback_reads_the_files_it_shows(recorded: dict[str, Any]) -> None:
    assert _seen(recorded, "test_c.py::test_traceback") == {"reads": ["observer.py"]}


def test_a_hash_only_function_is_not_a_reader(recorded: dict[str, Any]) -> None:
    assert _seen(recorded, "test_c.py::test_hash_only") == {}


def test_without_the_option_the_same_function_is_a_reader(tmp_path: Path) -> None:
    """The control for the test above."""
    plain = _run(tmp_path, f"--source-reads-dir={tmp_path / 'reads'}", "-n", "0")
    assert _seen(plain, "test_c.py::test_hash_only") == {"reads": ["hashed.py"]}


# ── spawns ──────────────────────────────────────────────────────────────────


def test_a_spawn_makes_the_test_opaque(recorded: dict[str, Any]) -> None:
    assert _seen(recorded, "test_a.py::test_spawns") == {
        "opaque": [f"subprocess.Popen:{Path(sys.executable).name}"]}


def test_git_run_elsewhere_is_not_recorded(recorded: dict[str, Any]) -> None:
    assert _seen(recorded, "test_c.py::test_git_elsewhere") == {}


@pytest.mark.parametrize("test", [
    "test_git_here", "test_git_pointed_here", "test_git_dir_equals_here",
    "test_git_env_here"])
def test_git_that_can_reach_the_repository_is_opaque(
        recorded: dict[str, Any], test: str) -> None:
    """Its directory, an argument, or a ``GIT_*`` variable names the rootdir."""
    assert _seen(recorded, f"test_c.py::{test}") == {"opaque": ["subprocess.Popen:git"]}


def test_git_inside_a_shell_command_is_the_shells_spawn(recorded: dict[str, Any]) -> None:
    assert _seen(recorded, "test_c.py::test_git_as_a_shell_string") == {
        "opaque": ["subprocess.Popen:sh"]}


# ── docstrings and line numbers read at runtime ─────────────────────────────


def test_the_record_says_it_watched(recorded: dict[str, Any]) -> None:
    assert recorded["versions"] == {2}
    assert recorded["observed"] == {sys.version_info >= (3, 12)}


needs_monitoring = pytest.mark.skipif(
    sys.version_info < (3, 12), reason="sys.monitoring is Python 3.12+")


@needs_monitoring
def test_a_docstring_read_names_its_definition(recorded: dict[str, Any]) -> None:
    assert _seen(recorded, "test_c.py::test_doc_of_a_function") == {
        "docs": ["documented.py::f"]}
    assert _seen(recorded, "test_c.py::test_doc_of_a_class_a_method_and_a_property") == {
        "docs": ["documented.py::K", "documented.py::K.m", "documented.py::K.p"]}
    assert _seen(recorded, "test_c.py::test_doc_of_an_instance") == {
        "docs": ["documented.py::K"]}
    assert _seen(recorded, "test_c.py::test_doc_of_a_module") == {
        "docs": ["documented.py::<module>"]}


@needs_monitoring
def test_a_docstring_outside_the_repository_is_not_recorded(recorded: dict[str, Any]) -> None:
    assert _seen(recorded, "test_c.py::test_doc_of_a_builtin") == {}


@needs_monitoring
def test_a_bare_doc_name_is_any_docstring_of_its_file(recorded: dict[str, Any]) -> None:
    assert _seen(recorded, "test_c.py::test_own_doc") == {"docs": ["observer.py::*"]}


@needs_monitoring
def test_a_read_that_cannot_be_tied_down_is_any(recorded: dict[str, Any]) -> None:
    assert _seen(recorded, "test_c.py::test_docs_wholesale") == {"docs": ["*"]}
    assert _seen(recorded, "test_c.py::test_docs_unevaluable") == {"docs": ["*"]}
    assert _seen(recorded, "test_c.py::test_line_untied") == {"lines": ["*"]}


@needs_monitoring
def test_a_line_number_read_names_its_file(recorded: dict[str, Any]) -> None:
    assert _seen(recorded, "test_c.py::test_line_of_a_function") == {
        "lines": ["documented.py"]}


@needs_monitoring
def test_a_site_in_a_module_body_is_everyones_and_an_import_is_no_site(
        recorded: dict[str, Any]) -> None:
    """``observer.py`` imports ``getdoc`` at its top: importing reads nothing."""
    assert recorded["unattributed"]["docs"] == {"own_at_import.py::*"}
    assert recorded["unattributed"]["lines"] == set()
    assert _seen(recorded, "test_c.py::test_own_doc_at_import") == {}


# ── the record itself ───────────────────────────────────────────────────────


def test_every_collected_test_is_listed(recorded: dict[str, Any]) -> None:
    assert len(recorded["expected"]["test_a.py"]) == 8
    assert recorded["expected"]["test_b.py"] == {
        "test_b.py::test_uses_fixture_too", "test_b.py::test_lazy_import",
        "test_b.py::test_b_plain"}
    assert set(recorded["tests"]) == set().union(*recorded["expected"].values())


def test_xdist_writes_one_file_per_worker_and_none_for_the_controller(
        recorded: dict[str, Any], request: pytest.FixtureRequest) -> None:
    workers = request.node.callspec.params["recorded"]
    assert recorded["files"] == (2 if workers == "2" else 1)


def test_without_the_option_nothing_is_written(tmp_path: Path) -> None:
    assert _run(tmp_path)["files"] == 0
    assert not (tmp_path / "reads").exists()
