# SPDX-License-Identifier: AGPL-3.0-or-later
"""A construction reaches the code that runs on construction (INV-rolok, WI-satal).

THE GRAPH SHAPE. ruby resolves ``Klass.new`` to ``Klass#initialize``, and
csharp / java land a construction on a ``constructor`` symbol, so a walk over
their ``instantiates`` edges enters the initializer. py / js_ts / dart land the
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
initializer (:func:`symbol_kinds.is_initializer`).

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
        if target is not None and _node(target, "kind") == "class":
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


def initializer_hops(nodes: Iterable[Any], edges: list[Any]) -> list[tuple[str, str]]:
    """``(class, initializer)`` for every constructed class, sorted for a stable A/B."""
    return sorted(
        (owner, init)
        for owner, members in _initializers_of_constructed_classes(nodes, edges).items()
        for init in members
    )


def construction_calls(
    nodes: Iterable[Any], edges: list[Any],
) -> list[tuple[str, str, Optional[int]]]:
    """``(constructing caller, initializer, line)`` for every construction of a
    class whose initializer the graph holds. Sorted for a stable A/B."""
    inits = _initializers_of_constructed_classes(nodes, edges)
    if not inits:
        return []
    calls: set[tuple[str, str, Optional[int]]] = set()
    for edge in edges:
        if _edge(edge, "type") != "instantiates":
            continue
        for init in inits.get(_edge(edge, "dst") or "", ()):
            calls.add((_edge(edge, "src") or "", init, _edge(edge, "line")))
    return sorted(calls, key=lambda c: (c[0], c[1], c[2] or 0))
