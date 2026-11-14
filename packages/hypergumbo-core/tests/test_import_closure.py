# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which files load a file: the import closure smart-test selects by.

The defect this answers: a test of a module that only READS something a
changed file defines (a constant, a module attribute, a value computed at
import) was not selected, because the reverse slice follows call edges. One
constant changed in ``schema.py`` and the selection lacked the test that
fails.

Every case builds a small tree as text. ``_loads`` asks the one question the
selector asks: does importing (or calling into) ``importer`` run ``target``'s
module body, through any number of files.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.import_closure import ImportGraph, build, module_name

SRC = "packages/core/src"
TESTS = "packages/core/tests"


def _loads(sources: dict[str, str], importer: str, target: str, **kwargs: object) -> bool:
    return importer in build(sources, **kwargs).dependents([target])  # type: ignore[arg-type]


@pytest.mark.parametrize(("path", "name"), [
    (f"{SRC}/a/b.py", "a.b"),
    (f"{SRC}/a/__init__.py", "a"),
    (f"{SRC}/top.py", "top"),
    (f"{SRC}/__init__.py", None),
    (f"{TESTS}/test_a.py", None),
    ("scripts/tool.py", None),
    ("packages/core/src", None),
    (f"{SRC}/a/data.json", None),
    ("other/core/src/a/b.py", None),
])
def test_module_name(path: str, name: str | None) -> None:
    assert module_name(path) == name


CONSTANT = {f"{SRC}/pkg/__init__.py": "", f"{SRC}/pkg/limits.py": "CAP = 4\n"}


@pytest.mark.parametrize("reader", [
    # The four shapes the call-edge slice misses.
    "from pkg import limits\n\ndef doubled():\n    return limits.CAP * 2\n",
    "from pkg.limits import CAP\n\ndef tripled():\n    return CAP * 3\n",
    "from pkg.limits import ceiling\nCEILING = ceiling()\n\ndef kept():\n    return CEILING\n",
    "def late():\n    from pkg import limits\n    return limits.CAP\n",
    # And every other way of writing the import.
    "import pkg.limits\n",
    "import pkg.limits as lim\n",
    "from . import limits\n",
    "from .limits import CAP\n",
    "class K:\n    from pkg.limits import CAP\n",
    "try:\n    from pkg.limits import CAP\nexcept ImportError:\n    CAP = 0\n",
    "import os\nif os.name:\n    from pkg import limits\n",
    "import importlib\nlim = importlib.import_module('pkg.limits')\n",
    "from importlib import import_module\n\ndef f():\n    return import_module('pkg.limits')\n",
    "lim = __import__('pkg.limits')\n",
    "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    pass\nelse:\n    from pkg import limits\n",
])
def test_a_test_of_a_module_that_reads_the_changed_file_is_a_dependent(reader: str) -> None:
    sources = {**CONSTANT, f"{SRC}/pkg/reader.py": reader,
               f"{TESTS}/test_reader.py": "from pkg.reader import anything\n",
               f"{TESTS}/test_other.py": "from pkg import unrelated\n"}
    graph = build(sources)
    hit = graph.dependents([f"{SRC}/pkg/limits.py"])
    assert f"{SRC}/pkg/reader.py" in hit
    assert f"{TESTS}/test_reader.py" in hit
    # Reach: a test with no path to the file is not swept in.
    assert f"{TESTS}/test_other.py" not in hit


@pytest.mark.parametrize("reader", [
    "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pkg import limits\n",
    "import typing\nif typing.TYPE_CHECKING:\n    from pkg.limits import CAP\n",
    # A name computed at runtime is not an import this module can follow.
    "import importlib\nNAME = 'pkg.' + 'limits'\nlim = importlib.import_module(NAME)\n",
    "import importlib\nlim = importlib.import_module('.limits', package='pkg')\n",
    "import importlib\nlim = importlib.import_module()\n",
    "TEXT = 'from pkg import limits'\n",
    "from pkg import unrelated\n",
    "this is not python (\n",
])
def test_what_does_not_load_the_file(reader: str) -> None:
    sources = {**CONSTANT, f"{SRC}/pkg/reader.py": reader,
               f"{TESTS}/test_reader.py": "from pkg.reader import anything\n"}
    assert not _loads(sources, f"{TESTS}/test_reader.py", f"{SRC}/pkg/limits.py")
    # Reach: the test still depends on the reader itself.
    assert _loads(sources, f"{TESTS}/test_reader.py", f"{SRC}/pkg/reader.py")


def test_importing_a_module_runs_the_packages_above_it() -> None:
    sources = {f"{SRC}/a/__init__.py": "FLAG = 1\n", f"{SRC}/a/b/__init__.py": "",
               f"{SRC}/a/b/c.py": "", f"{TESTS}/test_c.py": "import a.b.c\n",
               f"{TESTS}/test_from.py": "from a.b.c import thing\n"}
    for test in (f"{TESTS}/test_c.py", f"{TESTS}/test_from.py"):
        for package in (f"{SRC}/a/__init__.py", f"{SRC}/a/b/__init__.py", f"{SRC}/a/b/c.py"):
            assert _loads(sources, test, package), (test, package)


def test_relative_imports_resolve_from_the_importing_module() -> None:
    sources = {
        f"{SRC}/a/__init__.py": "from .inner import x\n",
        f"{SRC}/a/inner.py": "", f"{SRC}/a/up.py": "",
        f"{SRC}/a/b/__init__.py": "from . import leaf\nfrom .. import up\n",
        f"{SRC}/a/b/leaf.py": "from ..up import y\nfrom ... import nowhere\nfrom .... import beyond\n",
        f"{SRC}/solo.py": "from . import neighbour\n", f"{SRC}/neighbour.py": "",
    }
    graph = build(sources)
    assert graph.imports[f"{SRC}/a/__init__.py"] == {f"{SRC}/a/inner.py"}
    assert graph.imports[f"{SRC}/a/b/__init__.py"] == {
        f"{SRC}/a/b/leaf.py", f"{SRC}/a/up.py", f"{SRC}/a/__init__.py"}
    # ``from ... import nowhere`` names nothing here, and four dots climb past
    # the top package.
    assert graph.imports[f"{SRC}/a/b/leaf.py"] == {
        f"{SRC}/a/up.py", f"{SRC}/a/__init__.py"}
    assert graph.imports[f"{SRC}/solo.py"] == {f"{SRC}/neighbour.py"}


def test_the_walk_goes_through_any_number_of_files_and_survives_a_cycle() -> None:
    sources = {f"{SRC}/m0.py": "import m3\n", f"{SRC}/m1.py": "import m0\n",
               f"{SRC}/m2.py": "def f():\n    import m1\n", f"{SRC}/m3.py": "import m2\n",
               f"{TESTS}/test_far.py": "import m3\n"}
    hit = build(sources).dependents([f"{SRC}/m0.py"])
    assert hit == {f"{SRC}/m0.py", f"{SRC}/m1.py", f"{SRC}/m2.py", f"{SRC}/m3.py",
                   f"{TESTS}/test_far.py"}
    assert build(sources).dependents([]) == frozenset()
    assert build(sources).dependents(["no/such/file.py"]) == frozenset()


def test_a_test_loads_its_conftests_and_its_helpers() -> None:
    sources = {
        f"{SRC}/core.py": "", f"{SRC}/fixtures_src.py": "", f"{SRC}/helper_src.py": "",
        f"{SRC}/pkg_helper_src.py": "", f"{SRC}/rel_src.py": "",
        "conftest.py": "import fixtures_src\n",
        f"{TESTS}/conftest.py": "import core\n",
        f"{TESTS}/helpers.py": "import helper_src\n",
        f"{TESTS}/support/__init__.py": "import pkg_helper_src\nfrom . import inner\n",
        f"{TESTS}/support/inner.py": "from .. import relmod\n",
        f"{TESTS}/relmod.py": "import rel_src\n",
        f"{TESTS}/test_plain.py": "x = 1\n",
        f"{TESTS}/test_helped.py": "from helpers import thing\nimport support\n",
        "tests/__init__.py": "", "tests/shared.py": "import helper_src\n",
        "tests/test_root.py": "from tests.shared import thing\nfrom .shared import other\n",
    }
    graph = build(sources)
    plain, helped, root = f"{TESTS}/test_plain.py", f"{TESTS}/test_helped.py", "tests/test_root.py"
    assert graph.imports[plain] == {"conftest.py", f"{TESTS}/conftest.py"}
    assert plain in graph.dependents([f"{SRC}/core.py"])
    assert plain in graph.dependents([f"{SRC}/fixtures_src.py"])
    assert plain not in graph.dependents([f"{SRC}/helper_src.py"])
    for target in ("helper_src", "pkg_helper_src", "rel_src"):
        assert helped in graph.dependents([f"{SRC}/{target}.py"]), target
    assert root in graph.dependents([f"{SRC}/helper_src.py"])
    assert graph.imports[root] == {"conftest.py", "tests/__init__.py", "tests/shared.py"}
    # A source module does not load a conftest: pytest loads it for tests.
    assert graph.imports[f"{SRC}/core.py"] == frozenset()


def test_a_deleted_module_keeps_its_importers() -> None:
    """The files still importing a deleted module are what must run: that is
    where the dangling import fails."""
    sources = {f"{SRC}/pkg/__init__.py": "", f"{SRC}/pkg/user.py": "from pkg.gone import x\n",
               f"{TESTS}/test_user.py": "import pkg.user\n"}
    gone = f"{SRC}/pkg/gone.py"
    assert not _loads(sources, f"{TESTS}/test_user.py", gone)
    assert _loads(sources, f"{TESTS}/test_user.py", gone, gone=[gone])


def test_two_files_spelling_one_module_resolve_to_the_first_path() -> None:
    sources = {"packages/a/src/dup.py": "", "packages/b/src/dup.py": "",
               "packages/a/tests/test_dup.py": "import dup\n"}
    assert build(sources).imports["packages/a/tests/test_dup.py"] == {"packages/a/src/dup.py"}


def test_the_graph_can_be_built_from_its_edges() -> None:
    graph = ImportGraph({"t.py": frozenset({"m.py"}), "m.py": frozenset()})
    assert graph.dependents(["m.py"]) == {"t.py"}


# ── which changed files take the closure, and the helper end to end ─────────

import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

from hypergumbo_core.selection_index import import_time_changed_sources  # noqa: E402

_REPO = Path(__file__).resolve().parents[3]
_HELPER = _REPO / "scripts" / "coverage-select"

BASE = '"""Doc."""\nCAP = 4\n\n\nclass Box:\n    size = 1\n\n    def area(self):\n        return self.size\n\n\ndef ceiling():\n    return CAP\n'


def _write(root: Path, rel: str, text: str) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return str(path)


@pytest.mark.parametrize(("current", "triggers"), [
    (BASE.replace("CAP = 4", "CAP = 2 + 2"), True),             # module statement
    (BASE.replace("size = 1", "size = 2"), True),               # class body
    (BASE.replace("return CAP", "return CAP + 0"), False),      # function body
    (BASE.replace("return self.size", "return 1"), False),      # method body
    (BASE + "\n\ndef added():\n    return 1\n", False),         # a new def is a new block
    (BASE.replace("CAP = 4", "CAP = 4  # the cap"), False),     # a comment
    (BASE, False),
    ("def broken(:\n", True),                                   # no longer parses
])
def test_only_import_time_code_takes_the_closure(tmp_path: Path, current: str, triggers: bool) -> None:
    rel = "packages/core/src/pkg/limits.py"
    path = _write(tmp_path, rel, current)
    assert import_time_changed_sources({path: BASE}, tmp_path) == ((rel,) if triggers else ())


def test_which_paths_can_take_the_closure(tmp_path: Path) -> None:
    changed = BASE.replace("CAP = 4", "CAP = 5")
    source = _write(tmp_path, "packages/core/src/pkg/limits.py", changed)
    test = _write(tmp_path, "packages/core/tests/test_limits.py", changed)
    script = _write(tmp_path, "scripts/tool.py", changed)
    new = _write(tmp_path, "packages/core/src/pkg/fresh.py", changed)
    deleted = str(tmp_path / "packages/core/src/pkg/gone.py")
    got = import_time_changed_sources(
        {source: BASE, test: BASE, script: BASE, new: None, deleted: BASE}, tmp_path)
    # A test file is its own cover, a script is outside the source roots, a
    # new file had no importer at the base; a deleted one took its body away.
    assert got == ("packages/core/src/pkg/gone.py", "packages/core/src/pkg/limits.py")
    # A path outside the repo root is kept as given, and is not coverable.
    assert import_time_changed_sources({"/elsewhere/x.py": BASE}, tmp_path) == ()


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t",  # noqa: S607
                    "-c", "user.email=t@example.invalid", *args],
                   check=True, capture_output=True)


def _helper(root: Path, changed: list[str], *extra: str) -> tuple[list[str], str]:
    listing = root / "changed.txt"
    listing.write_text("".join(f"{c}\n" for c in changed))
    done = subprocess.run(
        [sys.executable, str(_HELPER), "--repo-root", str(root),
         "--index", str(root / "index.sqlite"), *extra, "import-closure",
         "--changed-files", str(listing), "--base", "HEAD"],
        capture_output=True, text=True, check=True)
    return done.stdout.split(), done.stderr


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    src, tests = "packages/core/src/pkg", "packages/core/tests"
    _write(root, f"{src}/__init__.py", "")
    _write(root, f"{src}/limits.py", BASE)
    _write(root, f"{src}/reads_name.py", "from pkg.limits import CAP\n\ndef tripled():\n    return CAP * 3\n")
    _write(root, f"{src}/reads_lazily.py", "def late():\n    from pkg import limits\n    return limits.CAP\n")
    _write(root, f"{src}/unrelated.py", "X = 1\n")
    _write(root, f"{tests}/test_limits.py", "from pkg.limits import ceiling\n")
    _write(root, f"{tests}/test_reads_name.py", "from pkg.reads_name import tripled\n")
    _write(root, f"{tests}/test_reads_lazily.py", "from pkg.reads_lazily import late\n")
    _write(root, f"{tests}/test_unrelated.py", "from pkg.unrelated import X\n")
    _write(root, f"{tests}/helper.py", "from pkg.limits import CAP\n")
    _write(root, ".gitignore", "changed.txt\nindex.sqlite*\n")
    _git(root, "init", "-q", "-b", "dev")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


LIMITS = "packages/core/src/pkg/limits.py"
READERS = ["packages/core/tests/test_limits.py", "packages/core/tests/test_reads_lazily.py",
           "packages/core/tests/test_reads_name.py"]


def test_the_helper_prints_the_tests_that_load_the_changed_file(repo: Path) -> None:
    (repo / LIMITS).write_text(BASE.replace("CAP = 4", "CAP = 2 + 2"))
    # A test file not yet committed is part of the tree too.
    _write(repo, "packages/core/tests/test_new_reader.py", "import pkg.reads_name\n")
    got, said = _helper(repo, [LIMITS, "README.md"])
    # helper.py loads the file and is not a test file; test_unrelated has no path.
    assert got == sorted([*READERS, "packages/core/tests/test_new_reader.py"])
    assert "1 file(s) with changed import-time code are loaded by 4 test file(s)" in said
    assert LIMITS not in said
    _, verbose = _helper(repo, [LIMITS], "--verbose")
    assert f"    4  {LIMITS}" in verbose


def test_the_helper_is_silent_for_a_function_body_and_for_no_python(repo: Path) -> None:
    (repo / LIMITS).write_text(BASE.replace("return CAP", "return CAP + 0"))
    assert _helper(repo, [LIMITS]) == ([], "")
    assert _helper(repo, ["README.md"]) == ([], "")
    assert _helper(repo, []) == ([], "")


def test_the_helper_names_the_tests_still_importing_a_deleted_module(repo: Path) -> None:
    (repo / LIMITS).unlink()
    got, _ = _helper(repo, [LIMITS])
    assert got == READERS
