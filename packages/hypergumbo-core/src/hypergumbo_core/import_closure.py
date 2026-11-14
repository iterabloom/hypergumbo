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

WHICH IMPORTS COUNT (owner ruling, 2026-11-14: module-level AND function-level).
Every ``import`` and ``from ... import`` statement anywhere in a file: at
module level, in a function or class body, under ``try`` or ``if``. A lazy
import inside a function runs the imported file's module body the first time
the function is called, and a test that calls it depends on that body exactly
as a test of a module-level importer does. Counted with them:

* the packages above an imported module (``import a.b.c`` runs
  ``a/__init__.py`` and ``a/b/__init__.py``);
* a name in ``from pkg import name`` when ``pkg.name`` is a module;
* ``importlib.import_module("a.b")`` and ``__import__("a.b")`` with a literal
  absolute name;
* for a file outside the source roots (a test, a test helper): the
  ``conftest.py`` files of its directory and the directories above it, which
  pytest loads for it, and a sibling module imported by bare name.

NOT COUNTED. The body of ``if TYPE_CHECKING:`` (it never runs; its ``else``
does). An import whose name is computed at runtime: an entry point, a registry
of dotted strings, ``import_module(prefix + name)``. A test that reaches a
file only that way is not in the answer. The coverage record is the evidence
for those (the tests seen executing a function of the file).

MODULE NAMES come from the layout smart-test selects over: a file under
``packages/<pkg>/src/`` is the module its path below ``src`` spells. Any other
dotted name is also tried as a path from the repository root, which is how
``from tests.helpers import x`` resolves.

PURE BY DESIGN. Every function takes source text; nothing here opens a file
or runs git. The caller (``scripts/coverage-select``) lists and reads the
tree. A file that does not parse contributes no edges: it cannot be imported
either.

COST, measured before this was built, over the 90 pull requests that changed
package source between 2026-10-30 and 2026-11-14: taking the closure for
import-time changes adds 18% to the selected test time (module-level imports
alone, 5%). Taking it for every code change would add 28% and put a median
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


class _ImportCollector(ast.NodeVisitor):
    """Collect ``(level, module, names)`` for every import that can run."""

    def __init__(self) -> None:
        self.found: list[tuple[int, str, tuple[str, ...]]] = []

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking(node.test):
            for child in node.orelse:
                self.visit(child)
        else:
            self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.found.append((0, alias.name, ()))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.found.append((node.level, node.module or "",
                           tuple(alias.name for alias in node.names)))

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        called = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else "")
        if called in _DYNAMIC_IMPORTERS and node.args:
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


def _imports_of(path: str, source: str, resolver: _Resolver) -> frozenset[str]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return frozenset()
    collector = _ImportCollector()
    collector.visit(tree)
    own = module_name(path)
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


def build(sources: Mapping[str, str], gone: Iterable[str] = ()) -> ImportGraph:
    """The import graph of ``sources`` (repo-relative path -> source text).

    ``gone`` names files that no longer exist but that other files may still
    import: a deleted module keeps its module name here, so the files still
    importing it are its dependents and get run, which is how the dangling
    import shows up.
    """
    resolver = _Resolver([*sources, *gone])
    return ImportGraph({path: _imports_of(path, text, resolver)
                        for path, text in sources.items()})
