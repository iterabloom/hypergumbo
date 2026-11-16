# SPDX-License-Identifier: AGPL-3.0-or-later
"""A construction reaches the code that runs on construction (INV-rolok, WI-satal).

THE GRAPH SHAPE. ruby resolves ``Klass.new`` to ``Klass#initialize``, and
csharp lands a construction on a ``constructor`` symbol, so a walk over their
``instantiates`` edges enters the initializer. py / js_ts / java / dart land the
edge on the CLASS node, and every edge leaving a class node (``contains``,
``extends``, ``decorated_by``) is correctly non-traversable, so a walk arrives
at the class and stops. Whatever the initializer does -- open a file, launch a
program, send a request with the constructor's arguments -- is then unreachable
from the code that constructed the object.

INV-rolok fixed this in the dead-code walk only, and the gap was never in one
consumer's edge-type set: it is in the graph shape, so every walk that treats
``instantiates`` as reachability has it. WI-satal measured the taint arm: a
host_secret flow into a subprocess sink through a function was ``violated``;
the same flow through a constructor was ``confirmed_with_caveats`` with no
evidence, in python and in javascript.

WHY THE GRAPH IS NOT RE-POINTED. Re-pointing the emitted ``instantiates`` dst at
the initializer is WI-fagit's shape for csharp, but
``linkers/method_call_recovery`` keys its class hint on ``e.dst in class_ids``,
and minting real edges perturbs ``detect_entrypoints`` (measured on pretix: 5
route seeds). So each walk reads the SAME pairs from here, in its own terms,
after the graph is built, and no emitted edge changes:

* :func:`initializer_hops` -- ``(class, initializer)`` pairs, for a walk that
  has already arrived at the class (dead code, slice);
* :func:`construction_calls` -- ``(caller, initializer, line)`` triples, for a
  walk that reads calls (taint, io_boundary), because ``Runner(cmd)`` IS a call
  of ``Runner.__init__``.

THE LICENCE IS THE CONJUNCTION. ``contains`` alone confers nothing -- making it
traversable would make every method of every class reachable. A pair is minted
only where the class is the dst of a construction edge AND the member is its
initializer (:func:`symbol_kinds.is_initializer`), read off a ``contains`` edge
from the class. java and dart emit their initializers as ``constructor``
symbols, which the predicate accepts, but the containment linker does not root
a ``constructor`` at its class, so java and dart get no pairs yet.

C++ (``cpp``) lands ``C x(1);`` on the class too, and constructs a ``struct``
the same way, so for it both kinds are construction targets (its analyzer
emits no symbol for a ``union``, so there is no ``union`` to land on)
(``_CONSTRUCTED_TYPES_BY_LANGUAGE``); its constructor is the member named
after its class (``is_initializer``). When the argument count chose one
constructor the analyzer also draws an ``instantiates`` edge straight to it
at the same caller and line, and that construction reaches it alone
(``_CHOSEN_INITIALIZER_LANGUAGES``); otherwise it reaches every constructor.
Every other language keeps ``class`` alone and every initializer, so its
pairs are what they were. A destructor is no initializer.

Edges and nodes arrive in three shapes across the consumers (behavior-map
dicts, ``Edge`` objects, ``Symbol`` objects), read through one accessor each so
the rule has one home.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Optional

from .symbol_kinds import is_initializer

_EDGE_ATTR = {"type": "edge_type", "src": "src", "dst": "dst", "line": "line"}


def _edge(edge: Any, key: str) -> Any:
    """One field of an edge, whether it is a behavior-map dict or an ``Edge``."""
    if isinstance(edge, Mapping):
        return edge.get(key)
    return getattr(edge, _EDGE_ATTR[key], None)


def _node(node: Any, key: str) -> Any:
    """One field of a node, whether it is a behavior-map dict or a ``Symbol``."""
    if isinstance(node, Mapping):
        return node.get(key)
    return getattr(node, key, None)


#: The type kinds a construction can land on, by the constructed type's
#: language. C++ constructs a ``struct`` exactly as it does a ``class`` (a
#: ``union`` has no symbol to land on); every other language keeps ``class`` alone, so this table changes
#: nothing for it.
_CONSTRUCTED_KINDS: frozenset[str] = frozenset({"class"})
_CPP_CONSTRUCTED_KINDS: frozenset[str] = frozenset({"class", "struct"})
_CONSTRUCTED_TYPES_BY_LANGUAGE: dict[str, frozenset[str]] = {
    "cpp": _CPP_CONSTRUCTED_KINDS,
}

#: Languages whose analyzer, when a construction's argument count chooses one
#: constructor, draws that construction a second ``instantiates`` edge straight
#: to it (C++ ``C x(1)`` -> ``C::C(int)``, at the same caller and line). Such a
#: construction reaches the chosen constructor alone; one the count does not
#: choose (no companion edge) reaches every constructor of the class.
_CHOSEN_INITIALIZER_LANGUAGES: frozenset[str] = frozenset({"cpp"})


def _is_constructed_type(node: Any) -> bool:
    """Whether ``node`` is a type a construction edge can land on."""
    kinds = _CONSTRUCTED_TYPES_BY_LANGUAGE.get(
        _node(node, "language") or "", _CONSTRUCTED_KINDS,
    )
    return _node(node, "kind") in kinds


def _edge_lines(edge: Any) -> list[Optional[int]]:
    """Every call site an edge stands for: its ``meta["call_lines"]`` when a
    collapse of several sites recorded them, else its one ``line``."""
    meta = edge.get("meta") if isinstance(edge, Mapping) else getattr(edge, "meta", None)
    lines = (meta or {}).get("call_lines")
    return list(lines) if lines else [_edge(edge, "line")]


def _initializers_of_constructed_classes(
    nodes: Iterable[Any], edges: list[Any],
) -> dict[str, list[str]]:
    """``class id -> [initializer ids]`` for every class some edge constructs."""
    by_id = {_node(n, "id"): n for n in nodes}
    constructed: set[str] = set()
    for edge in edges:
        if _edge(edge, "type") != "instantiates":
            continue
        target = by_id.get(_edge(edge, "dst") or "")
        # An unresolved/external dst has no node behind it; a dst that is
        # ALREADY a callable is the ruby/csharp shape and needs no hop.
        if target is not None and _is_constructed_type(target):
            constructed.add(_node(target, "id"))
    if not constructed:
        return {}
    inits: dict[str, list[str]] = {}
    for edge in edges:
        if _edge(edge, "type") != "contains":
            continue
        owner = _edge(edge, "src") or ""
        if owner not in constructed:
            continue
        member = by_id.get(_edge(edge, "dst") or "")
        if member is None:
            continue
        if is_initializer(
            _node(member, "kind") or "", _node(member, "name") or "",
            _node(member, "language") or "",
        ):
            inits.setdefault(owner, []).append(_node(member, "id"))
    return inits


def _constructions(
    nodes: Iterable[Any], edges: list[Any],
) -> list[tuple[str, str, Optional[int], list[str]]]:
    """``(caller, class, line, initializers it reaches)`` per construction.

    A construction reaches every initializer of its class, except in a
    ``_CHOSEN_INITIALIZER_LANGUAGES`` language when the same caller has an
    ``instantiates`` edge at the same line straight to one of them: the
    analyzer chose that constructor by the argument count, and the
    construction reaches it alone.
    """
    nodes = list(nodes)
    inits = _initializers_of_constructed_classes(nodes, edges)
    if not inits:
        return []
    by_id = {_node(n, "id"): n for n in nodes}
    direct: dict[tuple[str, Optional[int]], set[str]] = {}
    for edge in edges:
        if _edge(edge, "type") == "instantiates":
            for line in _edge_lines(edge):
                direct.setdefault(
                    (_edge(edge, "src") or "", line), set(),
                ).add(_edge(edge, "dst") or "")
    out: list[tuple[str, str, Optional[int], list[str]]] = []
    for edge in edges:
        if _edge(edge, "type") != "instantiates":
            continue
        owner = _edge(edge, "dst") or ""
        members = inits.get(owner)
        if not members:
            continue
        src = _edge(edge, "src") or ""
        if _node(by_id[owner], "language") not in _CHOSEN_INITIALIZER_LANGUAGES:
            out.append((src, owner, _edge(edge, "line"), members))
            continue
        for line in _edge_lines(edge):
            chosen = [m for m in members if m in direct.get((src, line), ())]
            out.append((src, owner, line, chosen or members))
    return out


def initializer_hops(nodes: Iterable[Any], edges: list[Any]) -> list[tuple[str, str]]:
    """``(class, initializer)`` for every constructed class, sorted for a stable A/B."""
    return sorted({
        (owner, init)
        for _src, owner, _line, members in _constructions(nodes, edges)
        for init in members
    })


def construction_calls(
    nodes: Iterable[Any], edges: list[Any],
) -> list[tuple[str, str, Optional[int]]]:
    """``(constructing caller, initializer, line)`` for every construction of a
    class whose initializer the graph holds. Sorted for a stable A/B."""
    calls = {
        (src, init, line)
        for src, _owner, line, members in _constructions(nodes, edges)
        for init in members
    }
    return sorted(calls, key=lambda c: (c[0], c[1], c[2] or 0))
