# SPDX-License-Identifier: AGPL-3.0-or-later
"""Producer-side registry gate for ``Edge.meta`` keys (WI-lijaz).

Why this exists
---------------
:mod:`hypergumbo_core.axis_meta_keys` says it declares every meta key the
codebase writes, and nothing checked the claim. The 2026-08-27 concept audit
of ``call_construct`` counted the keys on serialised ``Edge.meta`` over 42
surveys and found 35 the registry did not know (the item says 34; its own
list names 35). Two of them, ``enclosing_class`` and
``inherited_field_receiver``, are written by ``make_unresolved_edge`` itself,
on 158,375 edges. The registry's first stated purpose -- a consumer reading a
mistyped key gets ``None`` and nobody notices -- holds only for keys it
knows.

``Symbol.kind`` / ``Edge.edge_type`` / ``Edge.evidence_type`` already have a
producer-side gate (:mod:`hypergumbo_core.producer_coherence`). This is the
same gate for meta KEYS, built on the same machinery: the construction sites
come from ``producer_coherence``'s site walk (helper descent included), and
every key expression is enumerated by :mod:`hypergumbo_core.value_flow`,
asked for the ``("key",)`` projection of the dict. No second scanner.

What counts as a write
----------------------
1. **Construction.** ``Edge(...)`` / ``Edge.create(...)`` with ``meta=``, and
   each call of a module-local helper that forwards its parameter into one.
   These keys are known to land on ``Edge.meta``, so they must be registered
   ON THE ``edge_meta`` AXIS.
2. **Post-hoc writes to any ``.meta``**: ``x.meta[K] = v``, ``x.meta = V``,
   ``x.meta.update(V)``, ``x.meta.setdefault(K, ...)``,
   ``write_meta_key(M, K, ...)``, and the subscript / ``update`` /
   ``setdefault`` forms through a local alias ``m = x.meta``
   (:func:`meta_write_discipline._meta_aliases`). The receiver's type is not
   known statically, so a key written this way must be registered on SOME
   axis; that still catches a new key and a typo, but not a key written on the
   wrong axis.

The resulting map is the input to the ``Edge.meta key`` axis of the shrink-
only ratchet in ``scripts/check-producer-axis-coherence``.

How keys are read
-----------------
``meta={"a": 1, **base}`` gives ``a`` plus the keys of ``base``. A named dict
gives the keys of its own binding, plus every subscript store, ``update`` and
``setdefault`` on it (``value_flow``'s collection rules). ``value_flow`` is
told that ``Edge`` / ``Edge.create`` only READ the dict they are given
(``readers``). That is true of the constructor, which copies or stores the
dict and adds nothing to it. The dict object it stores can be written again
later through ``edge.meta[...]``, and the post-hoc arm above scans those
writes as sites of their own.

In a post-hoc write a copy of an existing meta dict
(``x.meta = {**(x.meta or {}), "k": v}``, ``m = dict(x.meta or {})``) holds
only keys that some other site wrote, and that site is checked where it
writes, so the copy contributes nothing (``value_flow``'s ``known_empty``).
At a construction site the same copy is NOT vouched for: a symbol's meta
copied onto an edge puts keys on ``Edge.meta`` that no edge writer declared,
and which ones cannot be known here, so it is reported as unresolved.

What it does not see
--------------------
- ``Symbol(...)`` / ``Symbol.create(...)`` construction. ``Symbol.meta`` is a
  separate, larger backlog, filed as its own item rather than folded into
  this gate.
- Writes through ``setattr``, ``**kwargs`` into a constructor, or a key that
  ``value_flow`` refuses (a public helper's parameter, a dict assembled in
  another module, a loop copying another record's items). Those sites are
  returned in :attr:`MetaKeyEmits.unresolved` so that a test can pin them by
  equality: a new one fails loudly instead of leaving the gate silently green.
- Whether a key is written on the right axis post-hoc (see 2. above).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Iterable, Iterator, Optional

from hypergumbo_core.axis_meta_keys import AXIS_EDGE_META, META_KEYS
from hypergumbo_core.meta_write_discipline import (
    _meta_aliases,
    _scopes,
    _walk_scope,
)
from hypergumbo_core.producer_coherence import (
    DEFAULT_EXCLUDED_PATH_SUBSTRINGS,
    DEFAULT_SEARCH_ROOTS,
    _iter_file_sites,
)
from hypergumbo_core.value_flow import flow_for

EDGE_CONSTRUCTORS: Final[frozenset[str]] = frozenset({"Edge", "Edge.create"})
"""The calls whose ``meta=`` lands on ``Edge.meta``."""

_KEYS: Final[tuple[tuple[str], ...]] = (("key",),)
"""The ``value_flow`` path that asks for a dict's keys."""


@dataclass
class MetaKeyEmits:
    """Every meta key the producer code writes, by where it is written.

    Attributes:
        constructed: ``{key: ("file:line", ...)}`` for keys passed to an
            ``Edge`` constructor's ``meta=``.
        written: the same for post-hoc writes to any record's ``.meta``.
        unresolved: ``("file", "source")`` for every write whose keys could
            not be enumerated. Keyed by source text, not line, so a pin on
            it survives edits elsewhere in the file.
    """

    constructed: dict[str, list[str]] = field(default_factory=dict)
    written: dict[str, list[str]] = field(default_factory=dict)
    unresolved: list[tuple[str, str]] = field(default_factory=list)


def _reads_a_meta(expr: ast.expr) -> bool:
    """``x.meta``: a dict whose keys were written, and checked, elsewhere."""
    return isinstance(expr, ast.Attribute) and expr.attr == "meta"


def _dict_keys(
    expr: ast.expr, tree: ast.Module, *, posthoc: bool = False,
) -> Optional[frozenset[str]]:
    """The keys the dict *expr* can hold, or ``None`` if they cannot be listed.

    *posthoc*: *expr* is written to some record's ``.meta`` after the fact, so
    a copy of an existing meta dict (``dict(x.meta or {})``,
    ``{**x.meta, ...}``) adds no key the gate has not seen at its own writer
    (see :func:`_reads_a_meta`). At a construction site it is not vouched for:
    a copy of a SYMBOL's meta onto an edge adds keys to ``Edge.meta`` that no
    edge writer checked, so there it stays unresolved.
    """
    return flow_for(tree).resolve(
        expr, _KEYS, readers=EDGE_CONSTRUCTORS,
        known_empty=_reads_a_meta if posthoc else None,
    )


def _key(expr: ast.expr, tree: ast.Module) -> Optional[frozenset[str]]:
    """The strings a key expression (``K`` in ``m[K]``) can be."""
    return flow_for(tree).resolve(expr, readers=EDGE_CONSTRUCTORS)


def _is_meta_attr(node: ast.expr) -> bool:
    """``<anything>.meta``."""
    return isinstance(node, ast.Attribute) and node.attr == "meta"


def _posthoc_writes(
    tree: ast.Module,
) -> Iterator[tuple[ast.Assign | ast.Call, ast.expr, Optional[frozenset[str]]]]:
    """Yield ``(node, written_expr, keys)`` for every post-hoc meta write."""
    aliased: dict[int, set[str]] = {}
    for scope in _scopes(tree):
        aliases = _meta_aliases(scope)
        if aliases:
            for node in _walk_scope(scope):
                aliased[id(node)] = aliases

    def is_meta(target: ast.expr, node: ast.AST) -> bool:
        if _is_meta_attr(target):
            return True
        return isinstance(target, ast.Name) and target.id in aliased.get(id(node), ())

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if _is_meta_attr(target):
                    yield node, node.value, _dict_keys(node.value, tree, posthoc=True)
                elif isinstance(target, ast.Subscript) and is_meta(target.value, node):
                    yield node, target.slice, _key(target.slice, tree)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "write_meta_key":
                if len(node.args) >= 2:
                    yield node, node.args[1], _key(node.args[1], tree)
            elif (
                isinstance(func, ast.Attribute)
                and func.attr in ("update", "setdefault")
                and is_meta(func.value, node)
                and node.args
            ):
                arg = node.args[0]
                if func.attr == "setdefault":
                    yield node, arg, _key(arg, tree)
                else:
                    yield node, arg, _dict_keys(arg, tree, posthoc=True)


def _python_files(
    repo_root: Path,
    search_roots: Iterable[str],
    excluded_path_substrings: Iterable[str],
) -> Iterator[tuple[Path, str]]:
    excluded = tuple(excluded_path_substrings)
    for root_name in search_roots:
        root = repo_root / root_name
        if not root.is_dir():
            continue
        for py_file in sorted(root.rglob("*.py")):
            if any(sub in str(py_file) for sub in excluded):
                continue
            yield py_file, str(py_file.relative_to(repo_root))


def scan_meta_key_emits(
    repo_root: Path,
    *,
    search_roots: Iterable[str] = DEFAULT_SEARCH_ROOTS,
    excluded_path_substrings: Iterable[str] = DEFAULT_EXCLUDED_PATH_SUBSTRINGS,
) -> MetaKeyEmits:
    """Enumerate every meta key the producer code writes (see the module docstring)."""
    emits = MetaKeyEmits()
    for py_file, rel in _python_files(repo_root, search_roots, excluded_path_substrings):
        try:
            source = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover
            continue
        if "meta" not in source:
            continue  # no ``meta=`` and no ``.meta``: nothing to scan
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover
            continue
        if "Edge" in source:
            for lineno, value, site_tree, _scope, forwarded in _iter_file_sites(
                py_file,
                constructor_names=EDGE_CONSTRUCTORS,
                keyword_arg="meta",
                descend_helpers=True,
            ):
                keys = _dict_keys(value, site_tree)
                if keys is None:
                    emits.unresolved.append((rel, ast.unparse(value)))
                elif not forwarded:
                    for key in keys:
                        emits.constructed.setdefault(key, []).append(f"{rel}:{lineno}")
        for node, written, keys in _posthoc_writes(tree):
            if keys is None:
                emits.unresolved.append((rel, ast.unparse(written)))
                continue
            for key in keys:
                emits.written.setdefault(key, []).append(f"{rel}:{node.lineno}")
    return emits


def unregistered_edge_meta_keys(repo_root: Path) -> dict[str, tuple[str, ...]]:
    """``{key: sites}`` for every written meta key the registry does not cover.

    A key at an ``Edge`` construction site must be registered on the
    ``edge_meta`` axis; a key written post-hoc on an untyped receiver, on any
    axis. The input to the ``Edge.meta key`` ratchet axis.
    """
    axes = {spec.name: spec.axis for spec in META_KEYS}
    emits = scan_meta_key_emits(repo_root)
    out: dict[str, list[str]] = {}
    for key, sites in emits.constructed.items():
        if axes.get(key) != AXIS_EDGE_META:
            out.setdefault(key, []).extend(sites)
    for key, sites in emits.written.items():
        if key not in axes:
            out.setdefault(key, []).extend(sites)
    return {key: tuple(sites) for key, sites in out.items()}
