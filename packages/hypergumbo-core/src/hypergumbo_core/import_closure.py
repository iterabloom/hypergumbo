# SPDX-License-Identifier: AGPL-3.0-or-later
"""File-level import closure: which files load a given file.

WHAT PROBLEM THIS SOLVES. smart-test's static selector is ``hypergumbo slice
--files``, a reverse slice from the SYMBOLS of the changed files. It lists a
module that imports a changed file, but it reaches a test of that module only
through a chain of call edges into the changed file. Reading a constant
imported by name, keeping a value the changed file computed at import, or
importing it inside a function leaves no call edge. (A read of an attribute of
the module object leaves none in the 9.0.0 slice; the 9.2.0 slice follows it.)
So when ``schema.STABLE_ID_SCHEME``
changed, the selection held 252 test files and not the one that asserts the
scheme's prefix through ``cli``: that test fails, and PR CI never ran it.

WHAT IT ANSWERS. A module or class body runs when the file is imported, so
every file that imports it, directly or through other files, runs the changed
code. :func:`build` reads the import statements of every Python file handed to
it and :meth:`ImportGraph.dependents` walks them backwards. smart-test asks
this for each source file whose import-time code changed and adds the test
files in the answer to the manifest (``coverage-select import-closure``).

WHICH IMPORTS COUNT. An ``import`` or ``from ... import`` statement that runs
when its file is imported: at module level or in a class body, under ``try``
or ``if`` there, and in a decorator or a parameter default of a ``def``.

An import inside a function BODY counts in a file outside the source roots
(a test, a ``conftest.py``, a test helper), and in a source module ONLY WHEN
THE FUNCTION RUNS AT IMPORT. ``haskell.py`` has ``_GATED =
_derive_haskell_gated_names()`` in its module body, and that function imports
``io_boundary`` and calls ``load_catalog``: loading ``haskell.py`` runs
``io_boundary``'s code, with no import statement at module level to say so.
Six test files of the Haskell and Jupyter analyzers were in no selection for
a change to ``load_catalog`` for that reason. Which functions run at import
is not decided here: the caller passes the ones a test run saw execute under
no test's context (``selection_index.functions_run_at_import``), by file and
qualified name, and an import inside one of them is an edge like a
module-level one.

Otherwise, for a file outside the source roots: A test that imports a module
inside its own test function, or a fixture that does, loads that module when
it runs. In a source module (a file under ``packages/<pkg>/src``) an import
inside a function that is not known to run at import does not count. The root ``conftest.py`` imports ``hypergumbo_core.analyze.registry``
inside a fixture; that runs ``hypergumbo_core/__init__.py``, which imports
``ir`` at module level; ``ir`` imports ``catalog`` inside a function and
``catalog`` imports ``cli`` inside a function. With those two counted, every
test file loaded everything ``cli`` imports, and one changed constant in
``schema.py`` selected all 1,423 test files. Without them it selects 314,
the test of the case above among them.

Counted with them:

* the packages above an imported module (``import a.b.c`` runs
  ``a/__init__.py`` and ``a/b/__init__.py``);
* a name in ``from pkg import name`` when ``pkg.name`` is a module;
* ``importlib.import_module("a.b")`` and ``__import__("a.b")`` with a literal
  absolute name, where an import statement would count;
* for a file outside the source roots (a test, a test helper): the
  ``conftest.py`` files of its directory and the directories above it, which
  pytest loads for it, and a sibling module imported by bare name.

NOT COUNTED. The body of ``if TYPE_CHECKING:`` (it never runs; its ``else``
does). An import whose name is computed at runtime: an entry point, a registry
of dotted strings, ``import_module(prefix + name)``. A test that reaches a
file only that way is not in the answer. The coverage record is the evidence
for those (the tests seen executing a function of the file).

A KNOWN MISS, by the rule above. A test that reaches the changed file only
through a function-level import in a source module (test -> ``reader.py``,
whose function imports the changed file), in a function that does NOT run at
import, is not in the answer. When such a
test reads a changed constant it is not selected for the pull request; the
twice-daily full suite is what runs it. The smart-test bed holds the shape
(``bedcore/reads_lazily.py``) and its test asserts the miss.

MODULE NAMES come from the layout smart-test selects over: a file under
``packages/<pkg>/src/`` is the module its path below ``src`` spells. Any other
dotted name is also tried as a path from the repository root, which is how
``from tests.helpers import x`` resolves.

PURE BY DESIGN. Every function takes source text; nothing here opens a file
or runs git. The caller (``scripts/coverage-select``) lists and reads the
tree. A file that does not parse contributes no edges: it cannot be imported
either.

COST, replayed with this module over the 90 pull requests that changed
package source between 2026-10-30 and 2026-11-14 (each one's own manifest plus
the closure of its import-time changes, on the tree of 2026-11-15): 9.8% more
selected test time, 44 of the 90 select more, and the median pull request goes
from 52% to 56% of the suite's test time. Counting function-level imports in
source modules too cost 25.3% and a median of 68%; module-level imports alone,
everywhere, 5.3%. Taking the closure for every code change would put a median
change at 88% of the suite, so the trigger is the import-time change and
nothing wider: a changed function body is found by the call-edge slice and by
the coverage record.
"""
from __future__ import annotations

import ast
from pathlib import PurePosixPath
from typing import Iterable, Iterator, Mapping, Optional

#: Functions whose first argument, when it is a literal, names a module to load.
_DYNAMIC_IMPORTERS = frozenset({"import_module", "__import__"})

_CONFTEST = "conftest.py"
_INIT = "__init__"


def module_name(path: str) -> Optional[str]:
    """The dotted module ``path`` is imported as, or ``None`` outside a source root.

    A source root is ``packages/<pkg>/src``; ``.../src/a/b.py`` is ``a.b`` and
    ``.../src/a/__init__.py`` is ``a``.
    """
    parts = PurePosixPath(path).parts
    if len(parts) < 4 or parts[0] != "packages" or parts[2] != "src":
        return None
    if not parts[-1].endswith(".py"):
        return None
    below = [*parts[3:-1], parts[-1][:-3]]
    if below[-1] == _INIT:
        below.pop()
    return ".".join(below) or None


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def _runs_at_definition(
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> Iterator[ast.expr]:
    """The expressions of a ``def`` or ``lambda`` evaluated where it is written:
    decorators and parameter defaults. The body runs only when it is called."""
    if not isinstance(node, ast.Lambda):
        yield from node.decorator_list
    yield from node.args.defaults
    yield from (default for default in node.args.kw_defaults if default is not None)


class _ImportCollector(ast.NodeVisitor):
    """Collect ``(level, module, names)`` for every import that counts.

    ``function_bodies`` says whether an import inside a function body counts:
    yes for a file outside the source roots, no for a source module, except
    in a function named in ``at_import`` (its qualified name, as
    ``block_hash`` spells it: ``f``, ``Class.method``, ``outer.inner``), which
    runs when the file is imported (module docstring, WHICH IMPORTS COUNT).
    """

    def __init__(self, *, function_bodies: bool,
                 at_import: frozenset[str] = frozenset()) -> None:
        self.function_bodies = function_bodies
        self.at_import = at_import
        self.found: list[tuple[int, str, tuple[str, ...]]] = []
        self._scope: list[str] = []
        #: Whether the code being visited runs when the file is imported.
        self._counts = True

    def _visit_callable(
            self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> None:
        # Decorators and defaults run where the def is written.
        for expr in _runs_at_definition(node):
            self.visit(expr)
        # A lambda has no name: its body counts only where every body does.
        name = None if isinstance(node, ast.Lambda) else node.name
        if name is not None:
            self._scope.append(name)
        outside = self._counts
        self._counts = outside and (self.function_bodies or (
            name is not None and ".".join(self._scope) in self.at_import))
        # The body is walked either way: a function defined in it has a name
        # of its own to be looked up.
        for child in (node.body if isinstance(node.body, list) else [node.body]):
            self.visit(child)
        self._counts = outside
        if name is not None:
            self._scope.pop()

    visit_FunctionDef = _visit_callable
    visit_AsyncFunctionDef = _visit_callable
    visit_Lambda = _visit_callable

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking(node.test):
            for child in node.orelse:
                self.visit(child)
        else:
            self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        if self._counts:
            for alias in node.names:
                self.found.append((0, alias.name, ()))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._counts:
            self.found.append((node.level, node.module or "",
                               tuple(alias.name for alias in node.names)))

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        called = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else "")
        if self._counts and called in _DYNAMIC_IMPORTERS and node.args:
            first = node.args[0]
            if (isinstance(first, ast.Constant) and isinstance(first.value, str)
                    and first.value and not first.value.startswith(".")):
                self.found.append((0, first.value, ()))
        self.generic_visit(node)


def _absolute(level: int, module: str, anchor: str, is_package: bool) -> Optional[str]:
    """The absolute dotted name a (possibly relative) import refers to.

    ``anchor`` is the importing file's own dotted name: its module name under
    a source root, else its path from the repository root with dots.
    """
    if level == 0:
        return module
    package = anchor.split(".") if is_package else anchor.split(".")[:-1]
    if level - 1 > len(package):
        return None
    base = package[:len(package) - (level - 1)]
    return ".".join([*base, *([module] if module else [])])


def _prefixes(dotted: str) -> Iterator[str]:
    parts = dotted.split(".")
    for end in range(1, len(parts) + 1):
        yield ".".join(parts[:end])


class _Resolver:
    """Turn a dotted name into the file it loads, among a fixed set of paths."""

    def __init__(self, paths: Iterable[str]) -> None:
        self.paths = frozenset(paths)
        self.by_module: dict[str, str] = {}
        for path in sorted(self.paths):
            name = module_name(path)
            if name is not None:
                self.by_module.setdefault(name, path)

    def file_of(self, dotted: str) -> Optional[str]:
        hit = self.by_module.get(dotted)
        if hit is not None:
            return hit
        as_path = dotted.replace(".", "/")
        for candidate in (f"{as_path}.py", f"{as_path}/{_INIT}.py"):
            if candidate in self.paths:
                return candidate
        return None

    def sibling(self, importer: str, dotted: str) -> Optional[str]:
        """A module beside ``importer`` imported by bare name (test helpers)."""
        here = PurePosixPath(importer).parent
        first = dotted.split(".")[0]
        for candidate in (here / f"{first}.py", here / first / f"{_INIT}.py"):
            if str(candidate) in self.paths:
                return str(candidate)
        return None


def _imports_of(path: str, source: str, resolver: _Resolver,
                at_import: frozenset[str] = frozenset()) -> frozenset[str]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return frozenset()
    own = module_name(path)
    collector = _ImportCollector(function_bodies=own is None, at_import=at_import)
    collector.visit(tree)
    is_package = PurePosixPath(path).stem == _INIT
    # A file outside the source roots resolves its relative imports from where
    # it stands; _Resolver.file_of reads a dotted name as a path too.
    anchor = own if own is not None else ".".join(PurePosixPath(path).with_suffix("").parts)
    if is_package and own is None:
        anchor = ".".join(PurePosixPath(path).parent.parts)
    found: set[str] = set()
    for level, module, names in collector.found:
        base = _absolute(level, module, anchor, is_package)
        if base is None:
            continue
        wanted = list(_prefixes(base)) if base else []
        wanted += [f"{base}.{name}" if base else name for name in names]
        for dotted in wanted:
            hit = resolver.file_of(dotted)
            if hit is None and own is None and level == 0:
                hit = resolver.sibling(path, dotted)
            if hit is not None:
                found.add(hit)
    if own is None:
        # pytest loads the conftest.py of a test's directory and of each
        # directory above it before the test's own module.
        for parent in PurePosixPath(path).parents:
            conftest = str(parent / _CONFTEST)
            if conftest in resolver.paths:
                found.add(conftest)
    found.discard(path)
    return frozenset(found)


class ImportGraph:
    """Which files each file imports, and the walk backwards over that."""

    def __init__(self, imports: Mapping[str, frozenset[str]]) -> None:
        self.imports = dict(imports)
        self._importers: dict[str, set[str]] = {}
        for importer, imported in self.imports.items():
            for target in imported:
                self._importers.setdefault(target, set()).add(importer)

    def dependents(self, paths: Iterable[str]) -> frozenset[str]:
        """Every file whose imports reach one of ``paths``, through any number
        of files. A path is in its own answer only when an import cycle leads
        back to it."""
        seen: set[str] = set()
        stack = list(paths)
        while stack:
            for importer in self._importers.get(stack.pop(), ()):
                if importer not in seen:
                    seen.add(importer)
                    stack.append(importer)
        return frozenset(seen)


def build(sources: Mapping[str, str], gone: Iterable[str] = (),
          at_import: Optional[Mapping[str, Iterable[str]]] = None) -> ImportGraph:
    """The import graph of ``sources`` (repo-relative path -> source text).

    ``gone`` names files that no longer exist but that other files may still
    import: a deleted module keeps its module name here, so the files still
    importing it are its dependents and get run, which is how the dangling
    import shows up.

    ``at_import`` maps a file to the qualified names of its functions that
    run when it is imported: the imports inside those count as edges (module
    docstring, WHICH IMPORTS COUNT).
    """
    resolver = _Resolver([*sources, *gone])
    marked = at_import or {}
    return ImportGraph({
        path: _imports_of(path, text, resolver, frozenset(marked.get(path, ())))
        for path, text in sources.items()})
