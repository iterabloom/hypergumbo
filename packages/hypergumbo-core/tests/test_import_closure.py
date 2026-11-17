# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which files load a file: the import closure smart-test selects by.

The defect this answers: a test of a module that only READS something a
changed file defines (a constant, a module attribute, a value computed at
import) was not selected, because the reverse slice follows call edges. One
constant changed in ``schema.py`` and the selection lacked the test that
fails.

Every case builds a small tree as text. ``_loads`` asks the one question the
selector asks: does importing ``importer`` run ``target``'s module body,
through any number of files.

An import inside a function is an edge only in a file outside the source
roots (a test, a conftest.py, a helper). In a source module it is not: the
root conftest.py reaches ``cli`` through two such imports, and counting them
put ``cli``'s whole closure under every test file. The cases below pin both
halves, and the chain that has to stay.
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
    # Three shapes the call-edge slice misses.
    "from pkg import limits\n\ndef doubled():\n    return limits.CAP * 2\n",
    "from pkg.limits import CAP\n\ndef tripled():\n    return CAP * 3\n",
    "from pkg.limits import ceiling\nCEILING = ceiling()\n\ndef kept():\n    return CEILING\n",
    # And every other way of writing an import that runs when the module loads.
    "import pkg.limits\n",
    "import pkg.limits as lim\n",
    "from . import limits\n",
    "from .limits import CAP\n",
    "class K:\n    from pkg.limits import CAP\n",
    "try:\n    from pkg.limits import CAP\nexcept ImportError:\n    CAP = 0\n",
    "import os\nif os.name:\n    from pkg import limits\n",
    "import importlib\nlim = importlib.import_module('pkg.limits')\n",
    "lim = __import__('pkg.limits')\n",
    "LIMS = [__import__(name) for name in ('pkg.limits',)] and __import__('pkg.limits')\n",
    # A decorator and a parameter default run when the def statement does.
    "def f(lim=__import__('pkg.limits')):\n    return lim\n",
    "async def f(*, plain, lim=__import__('pkg.limits')):\n    return lim\n",
    "import importlib\n\n@importlib.import_module('pkg.limits').deco\ndef f():\n    return 1\n",
    "class K:\n    def m(self, lim=__import__('pkg.limits')):\n        return lim\n",
    "f = lambda lim=__import__('pkg.limits'): lim\n",
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


#: Imports that run only when the function is called.
LAZY = [
    "def late():\n    from pkg import limits\n    return limits.CAP\n",
    "async def late():\n    import pkg.limits\n",
    "class K:\n    def m(self):\n        from pkg.limits import CAP\n        return CAP\n",
    "def outer():\n    def inner():\n        from pkg import limits\n    return inner\n",
    "def outer():\n    class Local:\n        from pkg import limits\n    return Local\n",
    "from importlib import import_module\n\ndef f():\n    return import_module('pkg.limits')\n",
    "late = lambda: __import__('pkg.limits')\n",
    "def f():\n    if True:\n        try:\n            import pkg.limits\n        except ImportError:\n            pass\n",
]


@pytest.mark.parametrize("reader", LAZY)
def test_an_import_inside_a_function_of_a_source_module_is_not_an_edge(reader: str) -> None:
    sources = {**CONSTANT, f"{SRC}/pkg/reader.py": reader,
               f"{TESTS}/test_reader.py": "from pkg.reader import anything\n"}
    graph = build(sources)
    assert f"{SRC}/pkg/limits.py" not in graph.imports[f"{SRC}/pkg/reader.py"]
    assert f"{TESTS}/test_reader.py" not in graph.dependents([f"{SRC}/pkg/limits.py"])
    # Reach: the reader parses, and its test still depends on the reader.
    assert _loads(sources, f"{TESTS}/test_reader.py", f"{SRC}/pkg/reader.py")


@pytest.mark.parametrize("reader", LAZY)
@pytest.mark.parametrize("where", [
    f"{TESTS}/test_reader.py", f"{TESTS}/conftest.py", "conftest.py",
    f"{TESTS}/helpers.py", "tests/test_root.py", "scripts/tool.py",
])
def test_an_import_inside_a_function_outside_the_source_roots_is_an_edge(
        reader: str, where: str) -> None:
    sources = {**CONSTANT, where: reader}
    assert f"{SRC}/pkg/limits.py" in build(sources).imports[where]


def test_a_lazy_import_in_a_test_reaches_what_its_target_loads_at_import() -> None:
    """The half that stays: test -> (in a function) reader -> (module level) limits."""
    sources = {**CONSTANT,
               f"{SRC}/pkg/reader.py": "from pkg.limits import CAP\n",
               f"{SRC}/pkg/lazy_reader.py": "def late():\n    from pkg.limits import CAP\n",
               f"{TESTS}/test_late.py": "def test_it():\n    from pkg.reader import CAP\n",
               f"{TESTS}/test_two_hops.py": "def test_it():\n    from pkg.lazy_reader import late\n"}
    hit = build(sources).dependents([f"{SRC}/pkg/limits.py"])
    assert f"{TESTS}/test_late.py" in hit
    # The second lazy hop is in a source module, so the chain stops there.
    assert f"{TESTS}/test_two_hops.py" not in hit


def test_the_conftest_chain_stops_at_the_first_lazy_import_of_a_source_module() -> None:
    """The shape that put ``cli``'s closure under every test: a conftest fixture
    imports ``pkg.sub.registry`` in its body, which runs ``pkg/__init__.py``,
    which imports ``ir`` at module level; ``ir`` imports ``catalog`` inside a
    function. Every test loads ``__init__`` and ``ir``; none loads ``catalog``
    by that route."""
    sources = {
        "conftest.py": ("import pytest\n\n\n@pytest.fixture\ndef registry():\n"
                        "    from pkg.sub import registry\n    return registry\n"),
        f"{SRC}/pkg/__init__.py": "from pkg import ir\n",
        f"{SRC}/pkg/ir.py": "def kinds():\n    from pkg import catalog\n    return catalog.KINDS\n",
        f"{SRC}/pkg/catalog.py": "KINDS = ()\n",
        f"{SRC}/pkg/sub/__init__.py": "", f"{SRC}/pkg/sub/registry.py": "",
        f"{TESTS}/test_plain.py": "x = 1\n",
        f"{TESTS}/test_catalog.py": "from pkg import catalog\n",
    }
    graph = build(sources)
    plain = f"{TESTS}/test_plain.py"
    for loaded in ("pkg/__init__.py", "pkg/ir.py", "pkg/sub/__init__.py", "pkg/sub/registry.py"):
        assert plain in graph.dependents([f"{SRC}/{loaded}"]), loaded
    assert plain not in graph.dependents([f"{SRC}/pkg/catalog.py"])
    assert f"{TESTS}/test_catalog.py" in graph.dependents([f"{SRC}/pkg/catalog.py"])


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
               f"{SRC}/m2.py": "class K:\n    import m1\n", f"{SRC}/m3.py": "import m2\n",
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
    _write(root, f"{tests}/test_imports_late.py",
           "def test_tripled():\n    from pkg.reads_name import tripled\n    assert tripled() == 12\n")
    _write(root, f"{tests}/test_unrelated.py", "from pkg.unrelated import X\n")
    _write(root, f"{tests}/helper.py", "from pkg.limits import CAP\n")
    _write(root, ".gitignore", "changed.txt\nindex.sqlite*\n")
    _git(root, "init", "-q", "-b", "dev")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


LIMITS = "packages/core/src/pkg/limits.py"
READERS = ["packages/core/tests/test_imports_late.py", "packages/core/tests/test_limits.py",
           "packages/core/tests/test_reads_name.py"]
#: Reaches limits.py only through a function-level import in a source module.
LAZY_SOURCE_READER = "packages/core/tests/test_reads_lazily.py"


def test_the_helper_prints_the_tests_that_load_the_changed_file(repo: Path) -> None:
    (repo / LIMITS).write_text(BASE.replace("CAP = 4", "CAP = 2 + 2"))
    # A test file not yet committed is part of the tree too.
    _write(repo, "packages/core/tests/test_new_reader.py", "import pkg.reads_name\n")
    got, said = _helper(repo, [LIMITS, "README.md"])
    # helper.py loads the file and is not a test file; test_unrelated has no path.
    assert got == sorted([*READERS, "packages/core/tests/test_new_reader.py"])
    # The known miss, stated: reads_lazily.py imports limits inside a function.
    assert LAZY_SOURCE_READER not in got
    assert (repo / LAZY_SOURCE_READER).is_file()
    assert "1 file(s) with changed import-time code are loaded by 4 test file(s)" in said
    assert LIMITS not in said
    _, verbose = _helper(repo, [LIMITS], "--verbose")
    assert f"    4  {LIMITS}" in verbose


def test_the_helper_is_silent_for_a_function_body_and_for_no_python(repo: Path) -> None:
    (repo / LIMITS).write_text(BASE.replace("return CAP", "return CAP + 0"))
    assert _helper(repo, [LIMITS]) == ([], "")
    assert _helper(repo, ["README.md"]) == ([], "")
    assert _helper(repo, []) == ([], "")


AT_IMPORT_TREE = {
    "packages/core/src/pkg/__init__.py": "",
    "packages/core/src/pkg/limits.py": "CAP = 4\n",
    "packages/core/src/pkg/other.py": "X = 1\n",
    "packages/core/src/pkg/third.py": "Y = 1\n",
    # _derive runs while derived.py loads, and the import is inside it.
    "packages/core/src/pkg/derived.py": (
        "def _derive():\n    from pkg import limits\n    return limits.CAP * 2\n\n\n"
        "DOUBLE = _derive()\n\n\n"
        "def later():\n    from pkg import other\n    return other.X\n\n\n"
        "class Box:\n    def build(self):\n        from pkg import third\n        return third.Y\n\n"
        "    def unused(self):\n        from pkg import other\n        return other.X\n\n\n"
        "def outer():\n    def inner():\n        from pkg import other\n        return other.X\n"
        "    return lambda: __import__('pkg.other')\n"),
    "packages/core/tests/test_derived.py": "from pkg.derived import DOUBLE\n",
}
DERIVED = "packages/core/src/pkg/derived.py"


def test_an_import_inside_a_function_that_runs_at_import_is_an_edge() -> None:
    """``haskell.py`` loads ``io_boundary`` this way (a module-level call of a
    function that imports it), and six of its and Jupyter's test files were in
    no selection for a change to ``io_boundary.load_catalog``."""
    limits, other, third = (f"packages/core/src/pkg/{n}.py" for n in ("limits", "other", "third"))
    test = "packages/core/tests/test_derived.py"
    # Nothing known about what runs at import: no function-level import counts.
    plain = build(AT_IMPORT_TREE)
    assert plain.dependents([limits]) == frozenset()
    # _derive is known to run at import: its import is an edge, and the test
    # that loads derived.py loads limits.py.
    graph = build(AT_IMPORT_TREE, at_import={DERIVED: ["_derive"]})
    assert graph.dependents([limits]) == {DERIVED, test}
    # Only the named function's imports: later() imports other.py and is not named.
    assert graph.dependents([other]) == frozenset()
    # A method is named with its class; a function nested in a named one and a
    # lambda there are functions of their own, and neither is named.
    graph = build(AT_IMPORT_TREE, at_import={DERIVED: ["Box.build", "outer"]})
    assert graph.dependents([third]) == {DERIVED, test}
    assert graph.dependents([other]) == frozenset()
    assert graph.dependents([limits]) == frozenset()
    # A nested function is named through the one that holds it, and counts
    # only when that one runs at import as well: its body is otherwise never
    # reached while the file loads.
    assert build(AT_IMPORT_TREE, at_import={DERIVED: ["outer.inner"]}
                 ).dependents([other]) == frozenset()
    graph = build(AT_IMPORT_TREE, at_import={DERIVED: ["outer", "outer.inner"]})
    assert graph.dependents([other]) == {DERIVED, test}
    # Names of another file's functions do nothing here.
    assert build(AT_IMPORT_TREE, at_import={"packages/core/src/pkg/other.py": ["_derive"]}
                 ).dependents([limits]) == frozenset()


def test_the_helper_follows_an_import_inside_a_function_the_index_saw_run_at_import(
        repo: Path) -> None:
    """The bed's known miss, lifted for the case where it is not a guess:
    ``reads_lazily.late`` imports limits inside its body. Once a run has seen
    ``late`` execute under no test's context, loading reads_lazily.py loads
    limits.py, and its test is in the closure of a change to limits.py."""
    from hypergumbo_core import selection_index as si
    from hypergumbo_core.block_hash import blocks_for_source

    (repo / LIMITS).write_text(BASE.replace("CAP = 4", "CAP = 2 + 2"))
    got, _ = _helper(repo, [LIMITS])
    assert LAZY_SOURCE_READER not in got
    lazy = str(repo / "packages/core/src/pkg/reads_lazily.py")
    conn = si.open_index(repo / "index.sqlite")
    si.store_edges(conn, {}, digests={lazy: {
        b.name: b.digest for b in blocks_for_source(lazy, Path(lazy).read_text())}})
    si._store_at_import(conn.cursor(), [(lazy, "late")], [lazy], full_run=False)
    conn.commit()
    assert si.functions_run_at_import(conn) == {lazy: frozenset({"late"})}
    conn.close()
    got, _ = _helper(repo, [LIMITS])
    assert got == sorted([*READERS, LAZY_SOURCE_READER])


def test_the_helper_takes_a_function_the_index_saw_run_at_import(repo: Path) -> None:
    """The same function-body change the test above is silent for. Once a run
    has seen ``ceiling`` execute under no test's context (a module calls it
    while it loads), the change is import-time code and the file's loaders
    are selected."""
    from hypergumbo_core import selection_index as si
    from hypergumbo_core.block_hash import blocks_for_source

    path = str(repo / LIMITS)
    conn = si.open_index(repo / "index.sqlite")
    si.store_edges(conn, {}, digests={path: {b.name: b.digest
                                             for b in blocks_for_source(path, BASE)}})
    si._store_at_import(conn.cursor(), [(path, "ceiling")], [path], full_run=False)
    conn.commit()
    conn.close()
    (repo / LIMITS).write_text(BASE.replace("return CAP", "return CAP + 0"))
    got, said = _helper(repo, [LIMITS])
    assert got == READERS
    assert "1 file(s) with changed import-time code are loaded by 3 test file(s)" in said


def test_the_helper_names_the_tests_still_importing_a_deleted_module(repo: Path) -> None:
    (repo / LIMITS).unlink()
    got, _ = _helper(repo, [LIMITS])
    assert got == READERS
