# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static string-value flow inside one module, for the registry gates.

Why this exists
---------------
Every registry gate in this tree answers "which axis values can this producer
emit?" by reading source. Their first versions read only a literal keyword
argument (``Edge.create(edge_type="extends")``), and every later extension
(function-local assignments, ternaries, ``MAP[k]`` dict indirection, f-string
expansion, helper descent) added ONE more shape. Values that reach the
constructor through data kept escaping, and each escape was found by accident:

- ``scip/edges.py`` minted ``has_type`` / ``defined_by`` through
  ``for flag, edge_type in _RELATION_EDGE_TYPES`` -- a loop over a list of
  tuples (INV-dahuh);
- ``linkers/lua_ffi.py`` and ``linkers/napi.py`` minted four evidence types
  through a helper that APPENDS tuples to a list and returns it, unpacked by
  the caller's loop (WI-pubin);
- the Dart analyzer chooses ``extends`` / ``implements`` / ``includes`` by
  clause, carries it through a dataclass field and emits
  ``edge_type=ref.edge_type``, so the ``depends_on`` producer-set scanner
  could not see Dart at all (WI-nakur).

So instead of a seventh shape, this module resolves an expression to the set
of string literals it can evaluate to by following the value backwards through
the module: bindings, loop and comprehension targets, tuple positions,
collection elements, dict keys and values, module-local function returns and
parameters, and record-class fields. The producer gates
(:mod:`hypergumbo_core.producer_coherence`) call it as the fallback for any
keyword value their own shapes leave unresolved. The meta-key gate
(:mod:`hypergumbo_core.meta_key_coherence`) calls it to list the keys a
``meta`` dict can hold and the strings a ``meta[K]`` key can be.

How it works
------------
:meth:`ModuleValueFlow.resolve` takes an expression and a *path* of
projections still to apply to its value: ``("pos", i)`` a tuple position,
``("elem",)`` an element of an iterable, ``("key",)`` / ``("val",)`` a
mapping's keys / values, ``("sub", i)`` a subscript, ``("attr", f)`` a field.
``for a, b in X`` asks for ``X`` under ``(elem, pos 1)``; ``refs`` built by
``refs.append((x, "lit"))`` answers ``elem`` with the appended tuples, which
answer ``pos 1`` with ``"lit"``. A tuple is never evaluated whole -- only the
position that was asked for -- so an unresolvable neighbour (a ``Symbol``
next to the label) does not poison the answer.

Names resolve by Python's scoping rules (comprehension, enclosing functions,
module; class bodies skipped), from the use site's position in the tree.

The answer is ``None`` -- "cannot say" -- unless EVERY way the value can be
produced was resolved. That is what lets a gate trust a ``frozenset``:

- a name with any binding this module does not model (``import``, ``with``,
  ``except``, ``global``, a ``match`` capture) is unresolvable;
- a collection that ESCAPES -- passed to a call that is not a known
  non-mutating builtin, aliased, stored on an attribute -- is unresolvable,
  since whatever it was passed to may add elements this walk cannot see;
- a parameter resolves only for a nested function or a module-private
  (``_``-prefixed) function, never a method, never a decorated function other
  than ``lru_cache`` / ``cache``, and only when EVERY reference to the
  function in the module is a direct call: a function passed as a callback,
  or called from another module, has callers this walk cannot enumerate;
- a record field resolves only for a module-local ``@dataclass`` /
  ``NamedTuple`` whose constructions are all direct calls, and never when the
  module assigns to an attribute of that name.

Two caller overrides relax these rules, as claims the CALLER makes rather
than this module: ``readers`` names callees that add nothing to a collection
passed to them, so such a call is not an escape, and ``known_empty`` marks
expressions that contribute the empty set. ``meta_key_coherence`` passes
``readers=EDGE_CONSTRUCTORS``, plus a ``known_empty`` for post-hoc ``.meta``
writes. Answers are memoized per override pair, never shared across pairs.

Recognized non-``None`` results that hold no string (``None``, numbers,
``return`` with no value) contribute the empty set; a cycle (a recursive
function, ``x = f(x)``) contributes the empty set at the point it closes,
which is the least fixpoint. A depth cap returns ``None``.

What it deliberately does NOT do
--------------------------------
- Cross-module flow. A helper imported from another module is opaque, which
  is why ``producer_coherence`` scans every module that hosts a constructor
  call rather than following imports.
- Attribute reads on ``self`` / ``cls``. Instance state is filled across
  methods and runs; tracking it is type inference, not value flow.
- Typing the object of an attribute read. ``ref.edge_type`` resolves against
  every module-local record class that declares ``edge_type``, whatever
  ``ref`` is. An object of an IMPORTED class with a same-named field is the
  documented unsoundness; the realistic case (re-emitting an existing
  ``Edge``'s ``edge_type``) has no module-local class to match and stays
  ``None``.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from itertools import pairwise
from typing import Callable, Final, Iterator, Optional

Step = tuple[object, ...]
"""One projection: ``("pos", i)``, ``("elem",)``, ``("key",)``, ``("val",)``,
``("sub", i)`` or ``("attr", name)``."""

Path = tuple[Step, ...]

_ELEM: Final[Step] = ("elem",)
_KEY: Final[Step] = ("key",)
_VAL: Final[Step] = ("val",)
_SUB_ANY: Final[Step] = ("sub", None)

_COLLECTION_HEADS: Final[frozenset[str]] = frozenset(
    {"pos", "elem", "key", "val", "sub"},
)

_MAX_DEPTH: Final[int] = 48
"""Recursion budget per top-level :meth:`ModuleValueFlow.resolve` call."""

_FSTRING_CAP: Final[int] = 32
"""Most candidate strings an f-string may expand to (mirrors producer_coherence)."""

_SCOPE_TYPES: Final[tuple[type[ast.AST], ...]] = (
    ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
)
_FUNC_TYPES: Final[tuple[type[ast.AST], ...]] = (
    ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
)
_COMP_TYPES: Final[tuple[type[ast.AST], ...]] = (
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
)

_PASSTHROUGH_BUILTINS: Final[frozenset[str]] = frozenset(
    {"list", "tuple", "sorted", "reversed", "set", "frozenset", "iter"},
)
"""Builtins whose result's elements are the argument's elements."""

_SAFE_CALLEES: Final[frozenset[str]] = frozenset({
    "len", "sorted", "list", "tuple", "set", "frozenset", "iter", "enumerate",
    "reversed", "any", "all", "bool", "min", "max", "sum", "isinstance",
    "print", "repr", "str", "id", "hash", "zip", "dict", "next",
})
"""Builtins a collection may be passed to without escaping (none mutates it)."""

_READ_METHODS: Final[frozenset[str]] = frozenset({
    "get", "items", "keys", "values", "copy", "index", "count", "pop",
    "sort", "clear", "remove", "discard", "popitem", "__contains__",
})
"""Methods that read or shrink a collection, so add no element to it."""

_ADD_METHODS: Final[frozenset[str]] = frozenset(
    {"append", "add", "insert", "extend", "update", "setdefault"},
)
"""Methods that add elements; their arguments are contributions."""

_TRANSPARENT_DECORATORS: Final[frozenset[str]] = frozenset({"lru_cache", "cache"})

_EMPTY_CONSTRUCTORS: Final[frozenset[str]] = frozenset(
    {"list", "tuple", "set", "frozenset", "dict"},
)
"""Builtins that, called with no argument, build an empty collection."""


@dataclass(frozen=True)
class _Binding:
    """One way a name gets a value in its binding scope.

    ``kind`` is ``"assign"`` (``value`` taken at ``positions``), ``"aug"``
    (``x += value``), ``"for"`` (an element of ``value`` at ``positions``),
    ``"param"`` (``func``'s parameter), or ``"opaque"`` (any binding this
    module does not model -- it makes the name unresolvable).
    """

    kind: str
    value: Optional[ast.expr] = None
    positions: tuple[int, ...] = ()
    func: Optional[ast.AST] = None


class _Unresolvable(Exception):
    """Internal: unwinds a resolution that hit something it cannot model."""


def _callee_name(node: ast.expr) -> Optional[str]:
    """The bare name a call or decorator invokes (``f`` / ``m.f`` / ``f(...)``)."""
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _dotted_name(node: ast.expr) -> Optional[str]:
    """``f`` -> ``"f"``, ``Mod.f`` -> ``"Mod.f"``; anything else -> None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return None


def _target_positions(target: ast.expr, name: str) -> Optional[tuple[int, ...]]:
    """The tuple-position path at which *target* binds *name*, if it does.

    ``a`` -> ``()``; ``(x, a)`` -> ``(1,)``; ``(x, (y, a))`` -> ``(1, 1)``.
    A starred element anywhere on the way makes later positions ambiguous,
    so it raises (the binding is then opaque).
    """
    if isinstance(target, ast.Name):
        return () if target.id == name else None
    if isinstance(target, (ast.Tuple, ast.List)):
        for index, element in enumerate(target.elts):
            if isinstance(element, ast.Starred):
                if _mentions(element, name) or any(
                    _mentions(e, name) for e in target.elts[index + 1:]
                ):
                    raise _Unresolvable
                continue
            inner = _target_positions(element, name)
            if inner is not None:
                return (index,) + inner
    if isinstance(target, ast.Starred):  # pragma: no cover - handled by the tuple arm
        raise _Unresolvable
    return None


def _nests_collections(path: Path) -> bool:
    """True when *path* reads an element of an element as a general collection.

    ``(elem, pos 1)`` -- a position of each tuple in a list -- is fine: a
    tuple cannot gain an element. ``(sub, elem)`` -- iterating the list stored
    under a dict key -- is not: the inner list can be filled through an alias
    (``index.setdefault(k, []).append(v)``, ``for row in rows: row.append(v)``)
    that a walk of the OUTER name's uses never sees. Refusing the shape is
    the sound answer.
    """
    for outer, inner in pairwise(path):
        if outer[0] in _COLLECTION_HEADS and inner[0] in _COLLECTION_HEADS:
            if inner[0] == "pos" or (inner[0] == "sub" and isinstance(inner[1], int)):
                continue
            return True
    return False


def _mentions(node: ast.AST, name: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node))


def _own_nodes(scope: ast.AST) -> Iterator[ast.AST]:
    """Nodes evaluated in *scope* itself, not in a scope nested inside it.

    For a function that is its body; for a comprehension its element(s),
    conditions and every generator but the first one's iterable (which is
    evaluated in the enclosing scope); for a module or class its body.
    """
    roots: list[ast.AST]
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        roots = list(scope.body)
    elif isinstance(scope, ast.Lambda):
        roots = [scope.body]
    elif isinstance(scope, _COMP_TYPES):
        generators = scope.generators  # type: ignore[attr-defined]
        roots = []
        for index, gen in enumerate(generators):
            roots.append(gen.target)
            if index:
                roots.append(gen.iter)
            roots.extend(gen.ifs)
        if isinstance(scope, ast.DictComp):
            roots.extend([scope.key, scope.value])
        else:
            roots.append(scope.elt)  # type: ignore[attr-defined]
    else:
        roots = list(getattr(scope, "body", []))
    stack = list(reversed(roots))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, _SCOPE_TYPES):
            # A nested scope's own header parts still belong here: defaults,
            # decorators, a class's bases, a comprehension's first iterable.
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                stack.extend(node.decorator_list)
                stack.extend(node.args.defaults)
                stack.extend(d for d in node.args.kw_defaults if d is not None)
            elif isinstance(node, ast.Lambda):
                stack.extend(node.args.defaults)
                stack.extend(d for d in node.args.kw_defaults if d is not None)
            elif isinstance(node, ast.ClassDef):
                stack.extend(node.decorator_list)
                stack.extend(node.bases)
            else:
                stack.append(node.generators[0].iter)  # type: ignore[attr-defined]
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


class ModuleValueFlow:
    """Resolve expressions of one parsed module to the strings they can be.

    Build once per module (it indexes parents and caches scope lookups) and
    call :meth:`resolve` for each producer site. See the module docstring for
    the soundness rules.
    """

    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self._parent: dict[int, ast.AST] = {}
        self._field: dict[int, str] = {}
        for node in ast.walk(tree):
            for field_name, value in ast.iter_fields(node):
                children = value if isinstance(value, list) else [value]
                for child in children:
                    if isinstance(child, ast.AST):
                        self._parent[id(child)] = node
                        self._field[id(child)] = field_name
        self._memos: dict[
            tuple[frozenset[str], Optional[Callable[[ast.expr], bool]]],
            dict[tuple[int, Path], Optional[frozenset[str]]],
        ] = {}
        self._memo: dict[tuple[int, Path], Optional[frozenset[str]]] = {}
        self._readers: frozenset[str] = frozenset()
        self._known_empty: Optional[Callable[[ast.expr], bool]] = None
        self._active: set[tuple[int, Path]] = set()
        self._depth = 0
        self._cycles = 0
        self._own_cache: dict[int, list[ast.AST]] = {}
        self._binds_cache: dict[tuple[int, str], bool] = {}
        self._bindings_cache: dict[tuple[int, str], list[_Binding]] = {}
        self._scope_cache: dict[tuple[int, str], Optional[ast.AST]] = {}

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def resolve(
        self,
        expr: ast.expr,
        path: Path = (),
        *,
        readers: frozenset[str] = frozenset(),
        known_empty: Optional[Callable[[ast.expr], bool]] = None,
    ) -> Optional[frozenset[str]]:
        """The string literals *expr* (projected through *path*) can be.

        ``None`` when any producing route is outside what this module models.

        *readers* names callees (``f`` or ``Mod.f``, as written at the call)
        that the ASKER vouches add nothing to a collection passed to them as
        an argument, directly or as an arm of ``or`` / ``a if t else b``.
        Such a use is then not an escape. It is the asker's claim, not this
        module's: the meta-key gate makes it for ``Edge.create`` because it
        scans every ``edge.meta[...]`` write as a site of its own (WI-lijaz).
        *known_empty* is the same kind of claim about expressions: one it
        accepts contributes nothing to the answer. The meta-key gate passes
        "reads some record's ``.meta``" for a post-hoc write, because a copy
        of an existing meta dict holds only keys whose own writers it checks.

        Answers are kept per ``(readers, known_empty)``, never shared across
        them.
        """
        self._depth = 0
        self._readers = readers
        self._known_empty = known_empty
        self._memo = self._memos.setdefault((readers, known_empty), {})
        try:
            return self._resolve(expr, path)
        except _Unresolvable:
            return None

    # ------------------------------------------------------------------
    # scopes
    # ------------------------------------------------------------------

    def _parent_of(self, node: ast.AST) -> Optional[ast.AST]:
        return self._parent.get(id(node))

    def _own(self, scope: ast.AST) -> list[ast.AST]:
        cached = self._own_cache.get(id(scope))
        if cached is None:
            cached = list(_own_nodes(scope))
            self._own_cache[id(scope)] = cached
        return cached

    def _scope_of(self, node: ast.AST) -> ast.AST:
        """The scope in which *node* is evaluated."""
        child: ast.AST = node
        parent = self._parent_of(node)
        while parent is not None:
            if isinstance(parent, _SCOPE_TYPES):
                field_name = self._field[id(child)]
                if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef, ast.Lambda)):
                    if field_name == "body":
                        return parent
                elif field_name != "generators" or not self._in_first_iter(node, child):
                    return parent
            child = parent
            parent = self._parent_of(parent)
        return self.tree

    def _in_first_iter(self, node: ast.AST, generator: ast.AST) -> bool:
        """True iff *node* sits in the ``iter`` of the FIRST generator *generator*.

        That one expression is evaluated in the enclosing scope; every other
        part of a comprehension is evaluated in the comprehension's own.
        """
        comp = self._parent_of(generator)
        if comp is None or comp.generators[0] is not generator:  # type: ignore[attr-defined]
            return False
        child: ast.AST = node
        parent = self._parent_of(node)
        while parent is not None and parent is not generator:
            child = parent
            parent = self._parent_of(parent)
        return self._field.get(id(child)) == "iter"

    def _binding_scope(self, use: ast.AST, name: str) -> Optional[ast.AST]:
        """The scope whose binding of *name* the use at *use* reads (None: builtin)."""
        key = (id(use), name)
        if key not in self._scope_cache:
            self._scope_cache[key] = self._find_binding_scope(use, name)
        return self._scope_cache[key]

    def _find_binding_scope(self, use: ast.AST, name: str) -> Optional[ast.AST]:
        scope: Optional[ast.AST] = self._scope_of(use)
        first = True
        while scope is not None:
            if isinstance(scope, ast.ClassDef) and not first:
                scope = self._enclosing_scope(scope)
                continue
            first = False
            declared = self._declaration(scope, name)
            if declared == "global":
                return self.tree if self._binds(self.tree, name) else None
            if declared == "nonlocal":
                scope = self._enclosing_scope(scope)
                continue
            if self._binds(scope, name):
                return scope
            scope = self._enclosing_scope(scope)
        return None

    def _enclosing_scope(self, scope: ast.AST) -> Optional[ast.AST]:
        if scope is self.tree:
            return None
        return self._scope_of(scope)

    def _declaration(self, scope: ast.AST, name: str) -> Optional[str]:
        if scope is self.tree:
            return None
        for node in self._own(scope):
            if isinstance(node, ast.Global) and name in node.names:
                return "global"
            if isinstance(node, ast.Nonlocal) and name in node.names:
                return "nonlocal"
        return None

    @staticmethod
    def _params(func: ast.AST) -> list[ast.arg]:
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return []
        args = func.args
        out = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
        if args.vararg is not None:
            out.append(args.vararg)
        if args.kwarg is not None:
            out.append(args.kwarg)
        return out

    def _binds(self, scope: ast.AST, name: str) -> bool:
        key = (id(scope), name)
        if key not in self._binds_cache:
            self._binds_cache[key] = bool(self._bindings(scope, name))
        return self._binds_cache[key]

    def _walrus_scope(self, node: ast.NamedExpr) -> ast.AST:
        scope = self._scope_of(node)
        while isinstance(scope, _COMP_TYPES):
            scope = self._scope_of(scope)
        return scope

    def _bindings(self, scope: ast.AST, name: str) -> list[_Binding]:
        key = (id(scope), name)
        cached = self._bindings_cache.get(key)
        if cached is None:
            try:
                cached = self._collect_bindings(scope, name)
            except _Unresolvable:
                cached = [_Binding("opaque")]
            self._bindings_cache[key] = cached
        return cached

    def _collect_bindings(self, scope: ast.AST, name: str) -> list[_Binding]:
        out: list[_Binding] = []
        for param in self._params(scope):
            if param.arg == name:
                out.append(_Binding("param", func=scope))
        if isinstance(scope, _COMP_TYPES):
            for gen in scope.generators:  # type: ignore[attr-defined]
                positions = _target_positions(gen.target, name)
                if positions is not None:
                    out.append(_Binding("for", gen.iter, positions))
            return out
        for node in self._own(scope):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    positions = _target_positions(target, name)
                    if positions is not None:
                        out.append(_Binding("assign", node.value, positions))
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and node.target.id == name:
                    if node.value is not None:
                        out.append(_Binding("assign", node.value))
            elif isinstance(node, ast.AugAssign):
                if _target_positions(node.target, name) is not None:
                    out.append(_Binding("aug", node.value))
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                positions = _target_positions(node.target, name)
                if positions is not None:
                    out.append(_Binding("for", node.iter, positions))
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                if any(
                    item.optional_vars is not None and _mentions(item.optional_vars, name)
                    for item in node.items
                ):
                    out.append(_Binding("opaque"))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name == name:
                    out.append(_Binding("def", func=node))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                if any((a.asname or a.name.split(".")[0]) == name for a in node.names):
                    out.append(_Binding("opaque"))
            elif isinstance(node, ast.ExceptHandler) and node.name == name:
                out.append(_Binding("opaque"))
            elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == name:
                out.append(_Binding("opaque"))
            elif isinstance(node, ast.MatchMapping) and node.rest == name:
                out.append(_Binding("opaque"))
            elif isinstance(node, ast.Delete):
                if any(_target_positions(t, name) is not None for t in node.targets):
                    out.append(_Binding("opaque"))
        for node in ast.walk(scope):
            if isinstance(node, ast.NamedExpr) and node.target.id == name:
                if self._walrus_scope(node) is scope:
                    out.append(_Binding("assign", node.value))
            elif isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
                # A nested scope re-binding this name from inside: its writes
                # are bindings of this scope that the walk above never reads.
                declaring = self._scope_of(node)
                if declaring is not scope and out and any(
                    isinstance(n, ast.Name) and n.id == name
                    and isinstance(n.ctx, (ast.Store, ast.Del))
                    for n in self._own(declaring)
                ):
                    out.append(_Binding("opaque"))
        return out

    def _uses(self, scope: ast.AST, name: str) -> Iterator[ast.Name]:
        """Every load of *name* anywhere that reads *scope*'s binding."""
        for node in ast.walk(scope):
            if (
                isinstance(node, ast.Name)
                and node.id == name
                and isinstance(node.ctx, ast.Load)
                and self._binding_scope(node, name) is scope
            ):
                yield node

    # ------------------------------------------------------------------
    # resolution
    # ------------------------------------------------------------------

    def _resolve(self, expr: ast.AST, path: Path) -> frozenset[str]:
        key = (id(expr), path)
        if key in self._memo:
            cached = self._memo[key]
            if cached is None:
                raise _Unresolvable
            return cached
        if key in self._active:
            self._cycles += 1
            return frozenset()
        if self._depth >= _MAX_DEPTH or _nests_collections(path):
            raise _Unresolvable
        self._active.add(key)
        self._depth += 1
        cycles_before = self._cycles
        try:
            result = self._dispatch(expr, path)
        except _Unresolvable:
            if self._cycles == cycles_before:
                self._memo[key] = None
            raise
        finally:
            self._active.discard(key)
            self._depth -= 1
        if self._cycles == cycles_before:
            self._memo[key] = result
        return result

    def _element(self, expr: ast.AST, rest: Path) -> frozenset[str]:
        """Resolve an element taken OUT of a collection, under *rest*.

        A mutable element (a list / dict / set) read further as a collection
        can be filled through whatever alias extracted it, so it is refused;
        see :func:`_nests_collections` for the general-collection half.
        """
        if rest and rest[0][0] in _COLLECTION_HEADS and self._is_mutable(expr):
            raise _Unresolvable
        return self._resolve(expr, rest)

    def _is_mutable(self, expr: ast.AST) -> bool:
        if isinstance(expr, (ast.List, ast.Dict, ast.Set, ast.ListComp,
                             ast.DictComp, ast.SetComp)):
            return True
        if isinstance(expr, ast.Call):
            return _callee_name(expr) in _MUTABLE_FACTORIES
        if isinstance(expr, ast.Name):
            scope = self._binding_scope(expr, expr.id)
            if scope is None:
                return False
            return any(
                b.value is not None and not b.positions and b.kind == "assign"
                and (
                    isinstance(b.value, (ast.List, ast.Dict, ast.Set, ast.ListComp,
                                         ast.DictComp, ast.SetComp))
                    or (isinstance(b.value, ast.Call)
                        and _callee_name(b.value) in _MUTABLE_FACTORIES)
                )
                for b in self._bindings(scope, expr.id)
            )
        return False

    def _dispatch(self, expr: ast.AST, path: Path) -> frozenset[str]:
        if (
            self._known_empty is not None
            and isinstance(expr, ast.expr)
            and self._known_empty(expr)
        ):
            return frozenset()
        if isinstance(expr, ast.Constant):
            if isinstance(expr.value, str):
                if path:
                    raise _Unresolvable
                return frozenset({expr.value})
            return frozenset()
        if isinstance(expr, ast.JoinedStr):
            if path:
                raise _Unresolvable
            return self._fstring(expr)
        if isinstance(expr, ast.IfExp):
            return (
                self._narrowed(expr.body, expr.test, True, path)
                | self._narrowed(expr.orelse, expr.test, False, path)
            )
        if isinstance(expr, ast.BoolOp):
            out: frozenset[str] = frozenset()
            for value in expr.values:
                out |= self._resolve(value, path)
            return out
        if isinstance(expr, ast.NamedExpr):
            return self._resolve(expr.value, path)
        if isinstance(expr, (ast.Tuple, ast.List, ast.Set)):
            return self._sequence(expr.elts, path)
        if isinstance(expr, ast.Dict):
            return self._dict(expr, path)
        if isinstance(expr, _COMP_TYPES):
            return self._comprehension(expr, path)
        if isinstance(expr, ast.Name):
            return self._name(expr, path)
        if isinstance(expr, ast.Subscript):
            index = expr.slice
            step: Step = (
                ("sub", index.value)
                if isinstance(index, ast.Constant) and type(index.value) is int
                else _SUB_ANY
            )
            return self._resolve(expr.value, (step,) + path)
        if isinstance(expr, ast.Attribute):
            return self._attribute(expr, path)
        if isinstance(expr, ast.Call):
            return self._call(expr, path)
        if isinstance(expr, ast.Starred):
            return self._resolve(expr.value, (_ELEM,) + path)
        raise _Unresolvable

    def _narrowed(
        self, branch: ast.expr, test: ast.expr, truth: bool, path: Path,
    ) -> frozenset[str]:
        """Resolve one arm of ``a if test else b``, filtered by what *test* proves.

        ``"dependency" if dep_type == "devDependency" else dep_type`` can never
        yield ``"devDependency"`` from its else-arm, and resolving that arm
        without the test says it can. Only the arm that IS the tested name is
        narrowed (see :func:`_narrow`).
        """
        values = self._resolve(branch, path)
        if path or not isinstance(branch, ast.Name):
            return values
        return _narrow(values, branch.id, test, truth)

    def _fstring(self, expr: ast.JoinedStr) -> frozenset[str]:
        candidates: set[str] = {""}
        for part in expr.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                pieces = frozenset({part.value})
            elif (
                isinstance(part, ast.FormattedValue)
                and part.format_spec is None
                and part.conversion == -1
            ):
                pieces = self._resolve(part.value, ())
                if not pieces:
                    raise _Unresolvable
            else:
                raise _Unresolvable
            candidates = {c + p for c in candidates for p in pieces}
            if len(candidates) > _FSTRING_CAP:
                raise _Unresolvable
        return frozenset(candidates)

    def _sequence(self, elts: list[ast.expr], path: Path) -> frozenset[str]:
        if not path or path[0][0] == "attr":
            raise _Unresolvable
        head, rest = path[0], path[1:]
        index = head[1] if head[0] in ("pos", "sub") else None
        if isinstance(index, int) and not any(isinstance(e, ast.Starred) for e in elts):
            if -len(elts) <= index < len(elts):
                return self._element(elts[index], rest)
            return frozenset()
        out: frozenset[str] = frozenset()
        for element in elts:
            if isinstance(element, ast.Starred):
                out |= self._resolve(element.value, (_ELEM,) + rest)
            else:
                out |= self._element(element, rest)
        return out

    def _dict(self, expr: ast.Dict, path: Path) -> frozenset[str]:
        if not path or path[0][0] == "attr":
            raise _Unresolvable
        head, rest = path[0], path[1:]
        want_values = head[0] in ("val", "sub")
        out: frozenset[str] = frozenset()
        for key, value in zip(expr.keys, expr.values, strict=True):
            if key is None:  # ``**spread``
                out |= self._resolve(value, ((_VAL if want_values else _KEY),) + rest)
            elif want_values:
                out |= self._element(value, rest)
            else:
                out |= self._resolve(key, rest)
        return out

    def _comprehension(self, expr: ast.AST, path: Path) -> frozenset[str]:
        if not path or path[0][0] == "attr":
            raise _Unresolvable
        head, rest = path[0], path[1:]
        produced: ast.expr
        if isinstance(expr, ast.DictComp):
            if head[0] in ("val", "sub"):
                produced = expr.value
                values = self._element(produced, rest)
            else:
                produced = expr.key
                values = self._resolve(produced, rest)
        else:
            produced = expr.elt  # type: ignore[attr-defined]
            values = self._element(produced, rest)
        return self._filtered(expr, produced, values, rest)

    @staticmethod
    def _filtered(
        expr: ast.AST, produced: ast.expr, values: frozenset[str], rest: Path,
    ) -> frozenset[str]:
        """Narrow a comprehension's produced name by its ``if`` filters.

        ``{k: v for k, v in ref.items() if k != "type"}`` never has the key
        ``"type"`` (``route_handler``'s ``handler_meta``, WI-lijaz). Only an
        ITERATION variable is narrowed: the walrus cannot rebind one, so no
        later filter or the element itself can change it after the test.
        """
        if rest or not isinstance(produced, ast.Name):
            return values
        generators: list[ast.comprehension] = expr.generators  # type: ignore[attr-defined]
        if not any(_mentions(gen.target, produced.id) for gen in generators):
            return values
        for gen in generators:
            for test in gen.ifs:
                values = _narrow(values, produced.id, test, True)
        return values

    # -- names ---------------------------------------------------------

    def _name(self, expr: ast.Name, path: Path) -> frozenset[str]:
        scope = self._binding_scope(expr, expr.id)
        if scope is None:
            raise _Unresolvable
        return self._binding_values(scope, expr.id, path, answering=expr)

    def _binding_values(
        self, scope: ast.AST, name: str, path: Path,
        answering: Optional[ast.Name] = None,
    ) -> frozenset[str]:
        bindings = self._bindings(scope, name)
        out: frozenset[str] = frozenset()
        for binding in bindings:
            if binding.kind in ("opaque", "def"):
                raise _Unresolvable
            if binding.kind == "assign":
                assert binding.value is not None
                steps: Path = tuple(("pos", p) for p in binding.positions)
                out |= self._resolve(binding.value, steps + path)
            elif binding.kind == "aug":
                assert binding.value is not None
                if not path or path[0][0] not in _COLLECTION_HEADS:
                    raise _Unresolvable
                out |= self._resolve(binding.value, path)
            elif binding.kind == "for":
                assert binding.value is not None
                steps = (_ELEM,) + tuple(("pos", p) for p in binding.positions)
                out |= self._resolve(binding.value, steps + path)
            else:  # param
                assert binding.func is not None
                out |= self._param(binding.func, name, path)
        if path and path[0][0] in _COLLECTION_HEADS and not self._all_tuples(bindings):
            out |= self._mutations(scope, name, bindings, path, answering)
        return out

    def _all_tuples(self, bindings: list[_Binding]) -> bool:
        """True when every binding is a fresh tuple, which nothing can extend."""
        return all(
            b.kind == "assign" and not b.positions and b.value is not None
            and self._arity(b.value) is not None
            for b in bindings
        )

    @staticmethod
    def _is_dict_like(bindings: list[_Binding]) -> bool:
        for binding in bindings:
            value = binding.value
            if binding.kind == "assign" and not binding.positions and value is not None:
                if isinstance(value, (ast.Dict, ast.DictComp)):
                    return True
                if isinstance(value, ast.Call) and _callee_name(value) in _DICT_FACTORIES:
                    return True
        return False

    def _mutations(
        self, scope: ast.AST, name: str, bindings: list[_Binding], path: Path,
        answering: Optional[ast.Name] = None,
    ) -> frozenset[str]:
        """Elements added to *name*'s collection after binding; raise if it escapes.

        *answering* is the use whose value is being asked for. It is not
        checked for an escape: it IS the read, and where it goes next is the
        asker's business, not a route by which it gains elements (WI-lijaz --
        ``Edge.create(meta=edge_meta)`` otherwise refuses ``edge_meta``
        because of the very call that asked). Every other use still is.
        """
        head, rest = path[0], path[1:]
        dict_like = self._is_dict_like(bindings)
        out: frozenset[str] = frozenset()
        for use in self._uses(scope, name):
            if use is answering:
                continue
            parent = self._parent_of(use)
            field_name = self._field.get(id(use))
            if isinstance(parent, ast.Attribute) and field_name == "value":
                call = self._parent_of(parent)
                if not (isinstance(call, ast.Call) and call.func is parent):
                    raise _Unresolvable  # ``x.attr`` read or stored: not a collection op
                if parent.attr in _READ_METHODS:
                    self._refuse_element_mutation(call)
                    continue
                if parent.attr not in _ADD_METHODS:
                    raise _Unresolvable
                self._refuse_element_mutation(call)
                out |= self._added(call, parent.attr, head, rest, dict_like)
            elif isinstance(parent, ast.Subscript) and field_name == "value":
                if isinstance(parent.ctx, ast.Store):
                    store = self._parent_of(parent)
                    if not (isinstance(store, ast.Assign) and parent in store.targets):
                        raise _Unresolvable
                    if head[0] in ("val", "sub") or (
                        head[0] in ("elem", "pos") and not dict_like
                    ):
                        out |= self._element(store.value, rest)
                    if head[0] == "key" or (head[0] in ("elem", "pos") and dict_like):
                        out |= self._resolve(parent.slice, rest)
                elif isinstance(parent.ctx, ast.Load):
                    self._refuse_element_mutation(parent)
            elif not self._is_harmless_read(use, parent, field_name):
                raise _Unresolvable
        return out

    def _refuse_element_mutation(self, extracted: ast.AST) -> None:
        """Raise when an element pulled out of a collection is mutated in place.

        ``d[k].append(v)``, ``d.setdefault(k, []).append(v)`` and
        ``d.get(k)[i] = v`` all change what the OUTER collection holds without
        a binding or a call on the outer name that the use walk can read.
        """
        parent = self._parent_of(extracted)
        field_name = self._field.get(id(extracted))
        if isinstance(parent, ast.Attribute) and field_name == "value":
            if parent.attr in _ADD_METHODS:
                raise _Unresolvable
        if isinstance(parent, ast.Subscript) and field_name == "value":
            if not isinstance(parent.ctx, ast.Load):
                raise _Unresolvable

    def _added(
        self, call: ast.Call, method: str, head: Step, rest: Path, dict_like: bool,
    ) -> frozenset[str]:
        if any(isinstance(a, ast.Starred) for a in call.args) or call.keywords:
            raise _Unresolvable
        if method in ("append", "add"):
            if dict_like or len(call.args) != 1:
                raise _Unresolvable
            return self._element(call.args[0], rest)
        if method == "insert":
            if dict_like or len(call.args) != 2:
                raise _Unresolvable
            return self._element(call.args[1], rest)
        if method == "extend":
            if dict_like or len(call.args) != 1:
                raise _Unresolvable
            return self._resolve(call.args[0], (_ELEM,) + rest)
        if method == "setdefault":
            if not dict_like or not call.args:
                raise _Unresolvable
            out: frozenset[str] = frozenset()
            if head[0] in ("val", "sub") and len(call.args) > 1:
                out |= self._element(call.args[1], rest)
            if head[0] in ("key", "elem", "pos"):
                out |= self._resolve(call.args[0], rest)
            return out
        # update
        if len(call.args) != 1:
            raise _Unresolvable
        if not dict_like:
            return self._resolve(call.args[0], (_ELEM,) + rest)
        step = _VAL if head[0] in ("val", "sub") else _KEY
        return self._resolve(call.args[0], (step,) + rest)

    def _is_harmless_read(
        self, use: ast.Name, parent: Optional[ast.AST], field_name: Optional[str],
    ) -> bool:
        """A load of a collection that cannot add an element to it."""
        if self._readers and self._passed_to_reader(use):
            return True
        if isinstance(parent, (ast.For, ast.AsyncFor, ast.comprehension)):
            return field_name == "iter"
        if isinstance(parent, (ast.Compare, ast.Return, ast.Yield, ast.YieldFrom,
                               ast.FormattedValue, ast.Assert, ast.If, ast.While,
                               ast.UnaryOp)):
            return True
        if isinstance(parent, ast.Subscript):
            return field_name == "slice"
        if isinstance(parent, ast.IfExp):
            return field_name == "test"
        if isinstance(parent, ast.BoolOp):
            grand = self._parent_of(parent)
            return isinstance(grand, (ast.If, ast.While, ast.Assert, ast.UnaryOp)) or (
                isinstance(grand, ast.IfExp) and self._field.get(id(parent)) == "test"
            )
        if isinstance(parent, ast.Dict) and field_name == "values":
            # ``{**x}`` copies x's items; ``{k: x}`` would store x itself.
            index = next(i for i, v in enumerate(parent.values) if v is use)
            return parent.keys[index] is None
        if isinstance(parent, (ast.Starred, ast.keyword)):
            grand = self._parent_of(parent)
            if isinstance(parent, ast.Starred) and isinstance(
                grand, (ast.List, ast.Tuple, ast.Set),
            ):
                return True  # ``[*x]`` copies x's elements
            return isinstance(grand, ast.Call) and self._is_safe_callee(grand)
        if isinstance(parent, ast.Call) and field_name == "args":
            # ``other.extend(x)`` / ``other.update(x)`` copy x's elements and
            # keep no reference to x; ``append``/``add`` would keep one.
            func = parent.func
            if isinstance(func, ast.Attribute) and func.attr in ("extend", "update"):
                return True
            return self._is_safe_callee(parent)
        return False

    def _passed_to_reader(self, use: ast.Name) -> bool:
        """True when *use* is an argument of a declared reader (see :meth:`resolve`)."""
        node: ast.AST = use
        parent = self._parent_of(node)
        while isinstance(parent, ast.BoolOp) or (
            isinstance(parent, ast.IfExp) and self._field.get(id(node)) != "test"
        ):
            node, parent = parent, self._parent_of(parent)
        if isinstance(parent, ast.keyword):
            node, parent = parent, self._parent_of(parent)
        elif self._field.get(id(node)) != "args":
            return False
        if not isinstance(parent, ast.Call):
            return False  # ``class C(metaclass=M)``: a keyword of a ClassDef
        return _dotted_name(parent.func) in self._readers

    def _is_safe_callee(self, call: ast.Call) -> bool:
        func = call.func
        return (
            isinstance(func, ast.Name)
            and func.id in _SAFE_CALLEES
            and self._binding_scope(func, func.id) is None
        )

    # -- parameters ------------------------------------------------------

    def _call_sites(self, definition: ast.AST, name: str) -> list[ast.Call]:
        """Every call of the def/class *definition*; raise if any use is not one.

        Annotations and ``isinstance`` checks are references that call
        nothing, so they may appear too.
        """
        home = self._scope_of(definition)
        if len(self._bindings(home, name)) != 1:
            raise _Unresolvable
        calls: list[ast.Call] = []
        for use in self._uses(home, name):
            parent = self._parent_of(use)
            if isinstance(parent, ast.Call) and parent.func is use:
                calls.append(parent)
            elif not (self._in_annotation(use) or self._is_isinstance_arg(use)):
                raise _Unresolvable
        if not calls:
            raise _Unresolvable
        return calls

    def _in_annotation(self, node: ast.AST) -> bool:
        child: ast.AST = node
        parent = self._parent_of(node)
        while parent is not None:
            if self._field.get(id(child)) in ("annotation", "returns"):
                return True
            if isinstance(parent, ast.stmt):
                return False
            child = parent
            parent = self._parent_of(parent)
        return False  # pragma: no cover - every expression sits under a statement

    def _is_isinstance_arg(self, node: ast.AST) -> bool:
        parent = self._parent_of(node)
        if isinstance(parent, ast.Tuple):
            parent = self._parent_of(parent)
        return (
            isinstance(parent, ast.Call)
            and isinstance(parent.func, ast.Name)
            and parent.func.id in ("isinstance", "issubclass")
        )

    def _param(self, func: ast.AST, name: str, path: Path) -> frozenset[str]:
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            raise _Unresolvable  # a lambda's callers are not enumerable here
        if not _transparent(func):
            raise _Unresolvable
        home = self._scope_of(func)
        if isinstance(home, ast.ClassDef):
            raise _Unresolvable
        if home is self.tree and not func.name.startswith("_"):
            raise _Unresolvable
        args = func.args
        if name in (getattr(args.vararg, "arg", None), getattr(args.kwarg, "arg", None)):
            raise _Unresolvable
        positional = [a.arg for a in args.posonlyargs] + [a.arg for a in args.args]
        defaults: dict[str, ast.expr] = dict(
            zip(reversed(positional), reversed(args.defaults), strict=False),
        )
        for kwonly, kw_default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
            if kw_default is not None:
                defaults[kwonly.arg] = kw_default
        index = positional.index(name) if name in positional else None
        out: frozenset[str] = frozenset()
        for call in self._call_sites(func, func.name):
            out |= self._argument(call, name, index, defaults.get(name), path)
        return out

    def _argument(
        self,
        call: ast.Call,
        name: str,
        index: Optional[int],
        default: Optional[ast.expr],
        path: Path,
    ) -> frozenset[str]:
        """The value *call* binds to parameter *name* (positional *index*)."""
        for keyword in call.keywords:
            if keyword.arg == name:
                return self._resolve(keyword.value, path)
            if keyword.arg is None:
                raise _Unresolvable
        if index is not None:
            position = 0
            for arg in call.args:
                if isinstance(arg, ast.Starred):
                    arity = self._arity(arg.value)
                    if arity is None:
                        raise _Unresolvable
                    if position <= index < position + arity:
                        return self._resolve(
                            arg.value, (("pos", index - position),) + path,
                        )
                    position += arity
                    continue
                if position == index:
                    return self._resolve(arg, path)
                position += 1
        if default is None:
            raise _Unresolvable
        return self._resolve(default, path)

    def _arity(self, expr: ast.expr, depth: int = 0) -> Optional[int]:
        """The fixed length of the tuple *expr* always evaluates to, if any.

        Only a tuple display, a name every binding of which is one, or a
        module-local function whose every non-``None`` return is one -- the
        shapes a ``*splat`` into a helper is built from.
        """
        if depth > 8:
            return None
        lengths: set[int] = set()
        if isinstance(expr, ast.Tuple):
            if any(isinstance(e, ast.Starred) for e in expr.elts):
                return None
            return len(expr.elts)
        if isinstance(expr, ast.Name):
            scope = self._binding_scope(expr, expr.id)
            if scope is None:
                return None
            for binding in self._bindings(scope, expr.id):
                if binding.kind != "assign" or binding.positions or binding.value is None:
                    return None
                arity = self._arity(binding.value, depth + 1)
                if arity is None:
                    return None
                lengths.add(arity)
        elif isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
            func = self._local_function(expr.func)
            if func is None:
                return None
            for ret in self._returns(func):
                value = ret.value
                if value is None or (isinstance(value, ast.Constant) and value.value is None):
                    continue
                arity = self._arity(value, depth + 1)
                if arity is None:
                    return None
                lengths.add(arity)
        else:
            return None
        return lengths.pop() if len(lengths) == 1 else None

    # -- calls -----------------------------------------------------------

    def _local_definition(self, name_node: ast.Name) -> Optional[ast.AST]:
        """The single module-local def/class *name_node* names, if that is all it is."""
        scope = self._binding_scope(name_node, name_node.id)
        if scope is None:
            return None
        bindings = self._bindings(scope, name_node.id)
        if len(bindings) != 1 or bindings[0].kind != "def":
            return None
        return bindings[0].func

    def _local_function(
        self, name_node: ast.Name,
    ) -> Optional[ast.FunctionDef | ast.AsyncFunctionDef]:
        definition = self._local_definition(name_node)
        if isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)) and _transparent(
            definition,
        ):
            return definition
        return None

    def _returns(self, func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Return]:
        return [n for n in self._own(func) if isinstance(n, ast.Return)]

    def _call(self, expr: ast.Call, path: Path) -> frozenset[str]:
        func = expr.func
        if isinstance(func, ast.Attribute):
            return self._method_call(expr, func, path)
        if not isinstance(func, ast.Name):
            raise _Unresolvable
        if self._binding_scope(func, func.id) is None:
            return self._builtin_call(expr, func.id, path)
        definition = self._local_definition(func)
        if isinstance(definition, ast.ClassDef):
            return self._construction(definition, expr, path)
        function = self._local_function(func)
        if function is None:
            raise _Unresolvable
        own = self._own(function)
        out: frozenset[str] = frozenset()
        if any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in own):
            if not path or path[0][0] not in ("elem", "pos"):
                raise _Unresolvable
            for node in own:
                if isinstance(node, ast.Yield) and node.value is not None:
                    out |= self._element(node.value, path[1:])
                elif isinstance(node, ast.YieldFrom):
                    out |= self._resolve(node.value, (_ELEM,) + path[1:])
            return out
        for ret in self._returns(function):
            if ret.value is not None:
                out |= self._resolve(ret.value, path)
        return out

    def _builtin_call(self, expr: ast.Call, name: str, path: Path) -> frozenset[str]:
        if any(isinstance(a, ast.Starred) for a in expr.args):
            raise _Unresolvable
        if expr.keywords and name != "sorted":
            raise _Unresolvable
        if (
            name == "dict" and len(expr.args) == 1 and path
            and path[0][0] in ("key", "val", "sub")
        ):
            return self._resolve(expr.args[0], path)  # a copy of a mapping
        if not path or path[0][0] not in ("elem", "pos", "sub"):
            raise _Unresolvable
        rest = path[1:]
        if name in _EMPTY_CONSTRUCTORS and not expr.args and not expr.keywords:
            return frozenset()  # ``list()`` / ``dict()``: no element yet
        if name in _PASSTHROUGH_BUILTINS and len(expr.args) == 1:
            return self._resolve(expr.args[0], (_ELEM,) + rest)
        if name in ("enumerate", "zip") and rest and rest[0][0] == "pos":
            position = rest[0][1]
            if name == "enumerate" and len(expr.args) == 1:
                if position == 0:
                    return frozenset()
                return self._resolve(expr.args[0], (_ELEM,) + rest[1:])
            if name == "zip" and isinstance(position, int) and 0 <= position < len(expr.args):
                return self._resolve(expr.args[position], (_ELEM,) + rest[1:])
        raise _Unresolvable

    def _method_call(
        self, expr: ast.Call, func: ast.Attribute, path: Path,
    ) -> frozenset[str]:
        receiver = func.value
        method = func.attr
        if isinstance(receiver, ast.Name) and receiver.id in ("self", "cls"):
            raise _Unresolvable
        if any(isinstance(a, ast.Starred) for a in expr.args) or expr.keywords:
            raise _Unresolvable
        if method == "items" and not expr.args:
            if len(path) >= 2 and path[0][0] == "elem" and path[1][0] == "pos":
                step = _KEY if path[1][1] == 0 else _VAL
                return self._resolve(receiver, (step,) + path[2:])
            raise _Unresolvable
        if method in ("values", "keys") and not expr.args:
            if not path or path[0][0] not in ("elem", "pos"):
                raise _Unresolvable
            return self._resolve(receiver, ((_VAL if method == "values" else _KEY),) + path[1:])
        if method in ("get", "pop") and len(expr.args) <= 2:
            out = self._resolve(receiver, (_SUB_ANY,) + path)
            if len(expr.args) == 2:
                out |= self._resolve(expr.args[1], path)
            return out
        if method == "copy" and not expr.args:
            return self._resolve(receiver, path)
        raise _Unresolvable

    # -- record classes --------------------------------------------------

    @staticmethod
    def _record_fields(
        cls: ast.ClassDef,
    ) -> Optional[tuple[list[tuple[str, Optional[ast.expr]]], bool]]:
        """``([(field, default)], is_namedtuple)`` or None if not a record class.

        Fields are the class body's annotated names, in order, minus
        ``ClassVar``s; a ``field(default=...)`` default is unwrapped, and a
        ``field()`` with none (or a ``default_factory``) has no default.
        """
        is_dataclass = any(_callee_name(d) == "dataclass" for d in cls.decorator_list)
        is_namedtuple = any(
            (isinstance(b, ast.Name) and b.id == "NamedTuple")
            or (isinstance(b, ast.Attribute) and b.attr == "NamedTuple")
            for b in cls.bases
        )
        if not (is_dataclass or is_namedtuple):
            return None
        fields: list[tuple[str, Optional[ast.expr]]] = []
        for stmt in cls.body:
            if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
                continue
            if "ClassVar" in ast.dump(stmt.annotation):
                continue
            default = stmt.value
            if isinstance(default, ast.Call) and _callee_name(default) == "field":
                default = next(
                    (k.value for k in default.keywords if k.arg == "default"), None,
                )
            fields.append((stmt.target.id, default))
        return fields, is_namedtuple

    def _construction(self, cls: ast.ClassDef, call: ast.Call, path: Path) -> frozenset[str]:
        record = self._record_fields(cls)
        if record is None or not path:
            raise _Unresolvable
        fields, is_namedtuple = record
        names = [f for f, _ in fields]
        head, rest = path[0], path[1:]
        if head[0] == "attr":
            field = str(head[1])
        elif (
            is_namedtuple and head[0] in ("pos", "sub")
            and isinstance(head[1], int) and 0 <= head[1] < len(names)
        ):
            field = names[head[1]]
        else:
            raise _Unresolvable
        for keyword in call.keywords:
            if keyword.arg is None:
                raise _Unresolvable
            if keyword.arg == field:
                return self._resolve(keyword.value, rest)
        index = names.index(field)
        for position, arg in enumerate(call.args):
            if isinstance(arg, ast.Starred):
                raise _Unresolvable
            if position == index:
                return self._resolve(arg, rest)
        default = dict(fields)[field]
        if default is None:
            raise _Unresolvable
        return self._resolve(default, rest)

    def _attribute(self, expr: ast.Attribute, path: Path) -> frozenset[str]:
        receiver = expr.value
        if isinstance(receiver, ast.Name) and receiver.id in ("self", "cls"):
            raise _Unresolvable
        field = expr.attr
        classes: list[ast.ClassDef] = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef):
                record = self._record_fields(node)
                if record is not None and any(f == field for f, _ in record[0]):
                    classes.append(node)
            elif (
                isinstance(node, ast.Attribute)
                and node.attr == field
                and isinstance(node.ctx, (ast.Store, ast.Del))
            ):
                raise _Unresolvable
        if not classes:
            raise _Unresolvable
        out: frozenset[str] = frozenset()
        step: Path = (("attr", field),) + path
        for cls in classes:
            for call in self._call_sites(cls, cls.name):
                out |= self._construction(cls, call, step)
        return out | self._replacements(field, path)

    def _replacements(self, field: str, path: Path) -> frozenset[str]:
        """Values ``dataclasses.replace(x, field=...)`` / ``x._replace(...)`` set."""
        out: frozenset[str] = frozenset()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and _callee_name(node) in ("replace", "_replace"):
                for keyword in node.keywords:
                    if keyword.arg is None:
                        raise _Unresolvable
                    if keyword.arg == field:
                        out |= self._resolve(keyword.value, path)
        return out


_MUTABLE_FACTORIES: Final[frozenset[str]] = frozenset(
    {"list", "dict", "set", "defaultdict", "OrderedDict", "deque"},
)
_DICT_FACTORIES: Final[frozenset[str]] = frozenset({"dict", "defaultdict", "OrderedDict"})


def _narrow(
    values: frozenset[str], name: str, test: ast.expr, truth: bool,
) -> frozenset[str]:
    """*values* of the name *name*, restricted by *test* having been *truth*.

    Only ``==`` / ``!=`` / ``in`` / ``not in`` against string literals (and
    their ``not``) narrow -- the shapes whose meaning is a set of strings.
    Any other test leaves *values* as they are, which over-approximates and
    so stays sound.
    """
    positive = truth
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        test, positive = test.operand, not truth
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1):
        return values
    left, op, right = test.left, test.ops[0], test.comparators[0]
    if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(right, ast.Name):
        left, right = right, left  # ``"lit" == x``
    if not (isinstance(left, ast.Name) and left.id == name):
        return values
    literals: frozenset[str]
    if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(right, ast.Constant) and (
        isinstance(right.value, str)
    ):
        literals = frozenset({right.value})
    elif (
        isinstance(op, (ast.In, ast.NotIn))
        and isinstance(right, (ast.Tuple, ast.List, ast.Set))
        and all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                for e in right.elts)
    ):
        literals = frozenset(
            e.value for e in right.elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
        )
    else:
        return values
    if isinstance(op, (ast.NotEq, ast.NotIn)):
        positive = not positive
    return values & literals if positive else values - literals


def _transparent(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """No decorator, or only ones that leave calls and returns as written."""
    return all(_callee_name(d) in _TRANSPARENT_DECORATORS for d in func.decorator_list)


_CACHE: dict[int, tuple[ast.Module, ModuleValueFlow]] = {}


def flow_for(tree: ast.Module) -> ModuleValueFlow:
    """The :class:`ModuleValueFlow` for *tree*, reused across its sites.

    Producer scans visit every site of one parsed module before moving to the
    next, so one cached entry is enough; holding the tree keeps ``id`` stable.
    """
    cached = _CACHE.get(id(tree))
    if cached is not None and cached[0] is tree:
        return cached[1]
    _CACHE.clear()
    flow = ModuleValueFlow(tree)
    _CACHE[id(tree)] = (tree, flow)
    return flow


def resolve_strings(expr: ast.expr, tree: ast.Module) -> Optional[frozenset[str]]:
    """The string literals *expr* (a node of *tree*) can evaluate to, or None."""
    return flow_for(tree).resolve(expr)
