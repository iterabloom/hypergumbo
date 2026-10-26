# SPDX-License-Identifier: AGPL-3.0-or-later
"""When anchored code runs: the one contract reachability consumers share (WI-fakuh).

THE GAP. A call outside every function is anchored on the node it runs in:
the FILE for module-level code (``REGISTRY = build_registry()``), the CLASS
for a class body or a static initializer (``class Holder: X = build_cases()``,
``static int X = Parse("1");``). Those anchors are ``calls`` sources like any
function, but nothing said when an anchor is itself reached, and four
consumers answered differently on one fixture:

* ``dead-code-maybe`` seeded functions and methods only and walked call edges
  only, so an anchor was never entered: a helper called only from module-level
  code read dead under ``--seeds entrypoints`` (and under the default
  ``production`` seeds when private), although it runs whenever its module is
  imported;
* ``test-coverage`` and ``sketch``'s coverage line seeded test FUNCTIONS only,
  so a production helper reached from a test module's own module-level code
  (``CASES = build_cases()``) or a test class body was a cold spot;
* the test-only ratchet (``test_only_reachability``) counted any caller in a
  test file -- anchors included -- which is the right answer: the test runner
  imports test modules, so their anchors run.

THE CONTRACT. Code anchored on a container runs when the container does:

* a FILE's module-level code runs when the file is loaded, and a file is loaded
  when any symbol it ``contains`` is reached (calling a function requires its
  module) or when a loaded file ``imports`` it;
* a CLASS body (Python, Ruby) or static initializer (Java, C#, Kotlin) runs
  when the class is defined or first used: modelled as "its file is loaded" or
  "any member it ``contains`` is reached". The file arm over-approximates for a
  JVM class that is defined but never used -- the direction that can only
  REMOVE a dead-code claim, never add one;
* so a TEST reaches its own test module's module-level code and its test
  class's body through the same member -> container step, and no separate
  seed is needed: the ratchet's "a caller in a test file is a test caller"
  and the walks agree without counting a test FILE as a test (a test count
  stays a count of test functions). The residual: a test file with module-level
  calls and no test function or test class is not reached.

:func:`anchor_execution_edges` turns the contract into walk-only ``(src,
dst)`` pairs from the graph's own ``contains`` and file-to-file ``imports``
edges: member -> container, file -> the type-like symbols it contains, and
importer file -> imported file. A consumer adds them to the graph IT walks,
the way INV-rolok's construction-initializer edges are added; nothing is
emitted, so no published edge and no other consumer moves. Containing is
still not calling: no edge leads from a container to a function, so a loaded
file makes its module-level code and class bodies run, never a function no
one calls.

Shape-tolerant (attribute or dict key), so the same functions serve the CLI's
deserialized maps and sketch's live ``Symbol`` / ``Edge`` objects.
"""
from __future__ import annotations

from typing import Any, Iterable, List, Tuple

from .symbol_kinds import type_like_kind_names

__all__ = ["anchor_execution_edges"]


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _edge_type(edge: Any) -> Any:
    return _get(edge, "edge_type") or _get(edge, "type")


def anchor_execution_edges(
    nodes: Iterable[Any], edges: Iterable[Any],
) -> List[Tuple[str, str]]:
    """Walk-only edges that make an anchor reached when its container runs."""
    kind_by_id = {_get(n, "id"): _get(n, "kind") for n in nodes}
    type_kinds = type_like_kind_names()
    out: List[Tuple[str, str]] = []
    for edge in edges:
        src, dst, etype = _get(edge, "src"), _get(edge, "dst"), _edge_type(edge)
        if not src or not dst:
            continue
        src_kind, dst_kind = kind_by_id.get(src), kind_by_id.get(dst)
        if etype == "contains":
            out.append((dst, src))  # a reached member means its container ran
            if src_kind == "file" and dst_kind in type_kinds:
                out.append((src, dst))  # loading a file runs its class bodies
        elif etype == "imports" and src_kind == "file" and dst_kind == "file":
            out.append((src, dst))  # importing a file loads it
    return out
