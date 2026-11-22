# SPDX-License-Identifier: AGPL-3.0-or-later
"""How unchanged code reaches a NEW def: the method it overrides, the decorator it runs.

WHY THIS EXISTS. :mod:`statement_select` answers a new function with "no test
of its own": nobody ran it, and a test reaches it through a caller that
changed to call it, whose change selects the test. Two kinds of new def are
reached with no caller changed, and for them that answer leaves out the test
the change can break (INV-bifih):

* a new METHOD that OVERRIDES an inherited one. ``class Tile(Square)`` gains
  ``area``; a test that calls ``tile.area()`` ran ``Square.area`` before, in
  another file, and now runs ``Tile.area``. Its only record is of
  ``Square.area``. :meth:`NewDefs.overrides` names the methods the new one
  takes the place of, so that the tests that ran them are selected;
* a new def with a DECORATOR. The decorator runs where the ``def`` statement
  runs (at import, for a module or class level def) and can put the function
  where unchanged code finds it: a registry that a test walks. No record says
  which tests read the registry. :meth:`NewDefs.registering` names the
  decorators not known to be inert, and the selector hands such a def to the
  import rule (which refuses to narrow).

WHAT IT READS. The changed file's current source (the selector reads it
already, to diff it), and, for a base class written in another file, that
file as it is on disk: the files the class statement's bases lead to through
the import statements, and nothing else. No whole-repository analysis.

HOW A BASE IS FOLLOWED TO SOURCE. A base is written as a name, a dotted name,
or either subscripted (``Base[T]``). Its first part is looked up in the class's
own file: a class defined at module level there, or a name an import
statement binds (``from .a import A``, ``from p.a import A``, ``import p.a``,
``import p.a as pa``, ``from p import a``), followed into the imported module's
file, through re-exports, to the ``class`` statement. An absolute import is
looked up under every ``packages/*/src`` beside the one the file is in; a
relative one from the file's directory. Then the base's own bases, and so on,
up every branch.

WHICH METHODS A NEW ONE OVERRIDES. On each branch, the first class that
defines the name with a ``def``: a call that reached any ancestor's method
reached the nearest one on some branch (a class precedes its bases in every
method resolution order, so the class Python picks is never behind another
definer on all branches). Every branch is followed, so a multiple-inheritance
class or a mixin yields each candidate. A class that has no definer of the
name on any branch overrides nothing, and the selector keeps its rule for a
plain new method.

WHAT IT CANNOT FOLLOW, AND SAYS SO (``Overrides.unresolved``). A base not
defined in a file of this repository (a builtin other than ``object``, the
standard library, a third-party package), a base computed by an expression, a
name bound more than once at module level or by anything but a ``class`` or
an import, a module with a ``*`` import, a class with a ``metaclass``, a class
defined inside a function, an ancestor that binds the name other than by
``def`` (``m = other``), a file that does not parse. Then what the new method
takes the place of is not known, and the selector answers that the index
cannot speak for it, so nothing is narrowed.

WHAT IS NOT COVERED. A new method on a class that a SUBCLASS lists before
another base defining the name (``class D(Mixin, Base)``, ``Mixin`` gains
``m``, ``Base.m`` existed): finding it needs every subclass of the class in
the repository, which this module does not look for. A call through
``getattr`` / ``hasattr`` on a name no class defined before. A class decorator
that replaces the class.

INERT DECORATORS. ``property`` (and ``x.setter`` / ``x.getter`` /
``x.deleter``), ``staticmethod``, ``classmethod`` when not rebound in the file,
and ``functools.cache`` / ``lru_cache`` / ``cached_property`` / ``wraps``,
``abc.abstractmethod``, ``contextlib.contextmanager`` /
``asynccontextmanager``, ``typing`` and ``typing_extensions`` ``overload`` /
``override`` / ``final``, by their imported name. Anything else may register.
"""
from __future__ import annotations

import ast
import builtins
import glob
import os
from dataclasses import dataclass
from typing import Optional, Union

_BLOCKS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_Def = Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]
#: Statements whose bodies are still the same scope.
_SAME_SCOPE = (ast.If, ast.Try, ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While)

#: Decorators that put the function nowhere (module docstring).
INERT_DECORATORS = frozenset({
    "property", "staticmethod", "classmethod",
    "functools.cache", "functools.lru_cache", "functools.cached_property", "functools.wraps",
    "abc.abstractmethod", "contextlib.contextmanager", "contextlib.asynccontextmanager",
    "typing.overload", "typing.override", "typing.final",
    "typing_extensions.overload", "typing_extensions.override", "typing_extensions.final",
})
_ACCESSORS = ("setter", "getter", "deleter")


class _Unresolved(Exception):
    """A base could not be followed to source of this repository."""


@dataclass(frozen=True)
class Overrides:
    """What a new method takes the place of."""

    #: ``(file, qualified class name)`` of each class whose method of the same
    #: name the new one overrides, nearest on each branch.
    definers: tuple[tuple[str, str], ...] = ()
    #: Why the bases could not all be followed to source; ``""`` when they were.
    unresolved: str = ""


def _blocks(tree: ast.AST) -> dict[str, _Def]:
    """Every def and class of ``tree`` by the qualified name
    :func:`block_hash.blocks_for_source` gives its block."""
    out: dict[str, _Def] = {}

    def children(node: ast.AST) -> list[_Def]:
        found: list[_Def] = []
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _BLOCKS):
                found.append(child)
            else:
                found.extend(children(child))
        return found

    def walk(node: ast.AST, prefix: str) -> None:
        seen: dict[str, int] = {}
        for child in children(node):
            seen[child.name] = seen.get(child.name, 0) + 1
            label = child.name if seen[child.name] == 1 else f"{child.name}#{seen[child.name]}"
            out[f"{prefix}{label}"] = child
            walk(child, f"{prefix}{label}.")

    walk(tree, "")
    return out


def _bindings(body: list[ast.stmt]) -> dict[str, list[ast.AST]]:
    """What binds each name in one scope: its statements, and those of the
    ``if`` / ``try`` / ``with`` / loop bodies in it, not of nested defs."""
    out: dict[str, list[ast.AST]] = {}

    def bind(name: str, node: ast.AST) -> None:
        out.setdefault(name, []).append(node)

    def targets(node: ast.AST, owner: ast.AST) -> None:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
                bind(sub.id, owner)

    def visit(statements: list[ast.stmt]) -> None:
        for stmt in statements:
            if isinstance(stmt, _BLOCKS):
                bind(stmt.name, stmt)
            elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
                for alias in stmt.names:
                    if isinstance(stmt, ast.Import) and alias.asname is None:
                        bind(alias.name.split(".")[0], stmt)
                    else:
                        bind(alias.asname or alias.name, stmt)
            elif isinstance(stmt, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete)):
                for target in (stmt.targets if isinstance(stmt, (ast.Assign, ast.Delete))
                               else [stmt.target]):
                    targets(target, stmt)
            if isinstance(stmt, (ast.For, ast.AsyncFor)):
                targets(stmt.target, stmt)
            if isinstance(stmt, (ast.With, ast.AsyncWith)):
                for item in stmt.items:
                    if item.optional_vars is not None:
                        targets(item.optional_vars, stmt)
            if isinstance(stmt, _SAME_SCOPE):
                for field in ("body", "orelse", "finalbody"):
                    visit(getattr(stmt, field, []))
                for handler in getattr(stmt, "handlers", []):
                    if handler.name:
                        bind(handler.name, handler)
                    visit(handler.body)

    visit(body)
    return out


class _Module:
    """One parsed file: its blocks and its module-level bindings."""

    def __init__(self, path: str, source: str) -> None:
        self.path = path
        tree = ast.parse(source)
        self.blocks = _blocks(tree)
        self.bindings = _bindings(tree.body)
        self.star = any(isinstance(node, ast.ImportFrom) and node.names[0].name == "*"
                        for node in ast.walk(tree))


def _dotted(node: ast.expr, outermost: bool = True) -> Optional[list[str]]:
    """``a.b.c`` as its parts, and ``a.b[T]`` as ``a.b``; ``None`` for any
    other expression."""
    if isinstance(node, ast.Subscript) and outermost:
        return _dotted(node.value, False)
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value, False)
        return None if head is None else [*head, node.attr]
    return None


def _source_roots(path: str) -> list[str]:
    """Every ``packages/*/src`` beside the one ``path`` is in."""
    parts = path.split(os.sep)
    for index in range(len(parts) - 3, -1, -1):
        if parts[index] == "packages" and parts[index + 2] == "src":
            packages = os.sep.join(parts[:index + 1]) or os.sep
            return sorted(glob.glob(os.path.join(glob.escape(packages), "*", "src")))
    return []


def _file_of(stem: str) -> Optional[str]:
    """The file of the module at ``stem`` (a path without suffix)."""
    for candidate in (f"{stem}.py", os.path.join(stem, "__init__.py")):
        if os.path.isfile(candidate):
            return candidate
    return None


def _exists(stem: str) -> bool:
    return _file_of(stem) is not None or os.path.isdir(stem)


def _binds_package(node: ast.AST, name: str) -> bool:
    """``node`` is an ``import name.x`` (no ``as``), which binds ``name``."""
    return isinstance(node, ast.Import) and any(
        alias.asname is None and alias.name.split(".")[0] == name for alias in node.names)


#: A module of the repository, by its stem (its path without ``.py``).
_Stem = str
#: What a name resolves to: a module, or a class (file, qualified name).
_Target = Union[_Stem, tuple[str, str]]


class NewDefs:
    """Questions about the new defs of one changed file (module docstring)."""

    def __init__(self, path: str, source: str) -> None:
        self._modules: dict[str, Optional[_Module]] = {}
        self._own = _Module(path, source)
        self._modules[path] = self._own
        self._roots = _source_roots(path)

    def _module(self, path: str) -> _Module:
        if path not in self._modules:
            try:
                with open(path, encoding="utf-8") as handle:
                    self._modules[path] = _Module(path, handle.read())
            except (OSError, SyntaxError, ValueError):
                self._modules[path] = None
        module = self._modules[path]
        if module is None:
            raise _Unresolved(f"{path} cannot be read or parsed")
        return module

    # ── names to modules and classes ───────────────────────────────────────

    def _absolute(self, name: str) -> _Stem:
        parts = name.split(".")
        found = [stem for root in self._roots
                 for stem in [os.path.join(root, *parts)] if _exists(stem)]
        if len(found) != 1:
            raise _Unresolved(f"module {name} is not one file of this repository"
                              if not found else f"module {name} is in {len(found)} places")
        return found[0]

    def _imported(self, module: _Module, node: ast.ImportFrom) -> _Stem:
        if not node.level:
            return self._absolute(node.module or "")
        base = os.path.dirname(module.path)
        for _ in range(node.level - 1):
            base = os.path.dirname(base)
        stem = os.path.join(base, *(node.module or "").split(".")) if node.module else base
        if not _exists(stem):
            raise _Unresolved(f"relative module {node.module} of {module.path} is not there")
        return stem

    def _name(self, module: _Module, name: str, seen: frozenset[tuple[str, str]]) -> _Target:
        """What ``name`` is at the top level of ``module``."""
        if (module.path, name) in seen:
            raise _Unresolved(f"{name} is imported in a cycle")
        seen = seen | {(module.path, name)}
        if module.star:
            raise _Unresolved(f"{module.path} has a * import")
        bound = module.bindings.get(name, [])
        if len(bound) > 1 and all(_binds_package(node, name) for node in bound):
            # ``import p.a`` and ``import p.b`` both bind ``p``: one package.
            bound = bound[:1]
        if len(bound) != 1:
            raise _Unresolved(f"{name} is not bound in {module.path}" if not bound
                              else f"{name} is bound {len(bound)} times in {module.path}")
        node = bound[0]
        if isinstance(node, ast.ClassDef):
            return (module.path, name)
        if isinstance(node, ast.Import):
            alias = next(a for a in node.names
                         if (a.asname or a.name.split(".")[0]) == name)
            return self._absolute(alias.name if alias.asname else name)
        if isinstance(node, ast.ImportFrom):
            alias = next(a for a in node.names if (a.asname or a.name) == name)
            return self._attribute(self._imported(module, node), alias.name, seen)
        raise _Unresolved(f"{name} in {module.path} is not a class or an import")

    def _attribute(self, stem: _Stem, name: str, seen: frozenset[tuple[str, str]]) -> _Target:
        """``name`` looked up in the module at ``stem``: a submodule, or what
        its file binds."""
        sub = os.path.join(stem, name)
        if _exists(sub):
            return sub
        path = _file_of(stem)
        if path is None:
            raise _Unresolved(f"{name} is not in the namespace package {stem}")
        return self._name(self._module(path), name, seen)

    def _base(self, module: _Module, owner: str, base: ast.expr) -> Optional[tuple[str, str]]:
        """The class a base expression of ``owner`` names, ``None`` for ``object``."""
        parts = _dotted(base)
        if parts is None:
            raise _Unresolved(f"a base of {owner} is computed: {ast.unparse(base)}")
        # A name of the enclosing class's body, then of the module (class
        # bodies do not see the class bodies around them).
        scope = owner.rsplit(".", 1)[0] if "." in owner else ""
        in_scope = _bindings(module.blocks[scope].body).get(parts[0], []) if scope else []
        target: _Target
        if in_scope:
            if len(in_scope) != 1 or not isinstance(in_scope[0], ast.ClassDef):
                raise _Unresolved(f"{parts[0]} in the body of {scope} is not one class")
            target = (module.path, f"{scope}.{parts[0]}")
        elif parts[0] == "object" and len(parts) == 1 and "object" not in module.bindings:
            return None
        elif parts[0] not in module.bindings and hasattr(builtins, parts[0]):
            raise _Unresolved(f"a base of {owner} is the builtin {parts[0]}")
        else:
            target = self._name(module, parts[0], frozenset())
        for part in parts[1:]:
            if isinstance(target, tuple):
                nested = f"{target[1]}.{part}"
                if not isinstance(self._module(target[0]).blocks.get(nested), ast.ClassDef):
                    raise _Unresolved(f"{nested} is not a class of {target[0]}")
                target = (target[0], nested)
            else:
                target = self._attribute(target, part, frozenset())
        if not isinstance(target, tuple):
            raise _Unresolved(f"a base of {owner} is a module: {ast.unparse(base)}")
        return target

    # ── the two questions ──────────────────────────────────────────────────

    def overrides(self, owner: str, method: str) -> Overrides:
        """What a new method ``method`` of the class ``owner`` (qualified, in
        this file) takes the place of."""
        definers: list[tuple[str, str]] = []
        done: set[tuple[str, str]] = set()

        def climb(module: _Module, name: str) -> None:
            node = module.blocks.get(name)
            if not isinstance(node, ast.ClassDef):  # pragma: no cover - a method's owner is a class
                raise _Unresolved(f"{name} is not a class of {module.path}")
            if any(isinstance(module.blocks.get(".".join(name.split(".")[:end])),
                              (ast.FunctionDef, ast.AsyncFunctionDef))
                   for end in range(1, name.count(".") + 1)):
                raise _Unresolved(f"{name} is defined inside a function")
            if any(keyword.arg == "metaclass" for keyword in node.keywords):
                raise _Unresolved(f"{name} has a metaclass")
            for base in node.bases:
                found = self._base(module, name, base)
                if found is None or found in done:
                    continue
                done.add(found)
                there = self._module(found[0])
                binds = _bindings(there.blocks[found[1]].body).get(method, [])
                if any(not isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)) for b in binds):
                    raise _Unresolved(f"{found[1]} binds {method} other than by def")
                if binds:
                    definers.append(found)
                else:
                    climb(there, found[1])

        try:
            climb(self._own, owner)
        except _Unresolved as why:
            return Overrides(unresolved=str(why))
        return Overrides(definers=tuple(definers))

    def registering(self, name: str) -> tuple[str, ...]:
        """The decorators of the new def ``name`` not known to be inert, as
        written."""
        node = self._own.blocks[name]
        return tuple(ast.unparse(d) for d in node.decorator_list if not self._inert(d))

    def _inert(self, decorator: ast.expr) -> bool:
        called = decorator.func if isinstance(decorator, ast.Call) else decorator
        parts = _dotted(called)
        if parts is None or isinstance(called, ast.Subscript):
            return False
        if len(parts) == 2 and parts[1] in _ACCESSORS:
            return True
        bound = self._own.bindings.get(parts[0], [])
        if not bound:
            return ".".join(parts) in INERT_DECORATORS
        if len(bound) != 1:
            return False
        node = bound[0]
        if isinstance(node, ast.Import):
            alias = next(a for a in node.names if (a.asname or a.name.split(".")[0]) == parts[0])
            full = [*(alias.name if alias.asname else parts[0]).split("."), *parts[1:]]
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            alias = next(a for a in node.names if (a.asname or a.name) == parts[0])
            full = [*node.module.split("."), alias.name, *parts[1:]]
        else:
            return False
        return ".".join(full) in INERT_DECORATORS
