# SPDX-License-Identifier: AGPL-3.0-or-later
"""Method-call recovery linker (WI-gigoz / Path B').

Solves the chained-method-call dead-end problem at the IR level so the
forward slice can traverse from a caller through ``Class().method()``
without breaking the no-fan-out invariant enforced by
``test_forward_slice_class_reaches_method_then_no_sibling_explosion``.

The Problem
-----------
For ``CliRunner().run(args)``:

  - The constructor invocation lands on the ``CliRunner`` class node
    (a ``calls`` or ``instantiates`` edge to the class).
  - The ``.run(args)`` part falls back to an ``unresolved-call`` edge
    whose dst encodes the bare method name
    (e.g. ``kotlin:external:0-0:run:unresolved``).
  - Forward slice intentionally does NOT traverse ``contains`` edges
    (slice.py blocks structural fan-out from class -> sibling methods).

Result: the slice dead-ends at the class node, missing the ~19 nodes that
slicing the method directly would yield (UAT 2026-04-13 BUG-14).

The Fix (Path B')
-----------------
A single post-analysis pass, language-agnostic. For each caller N:

  1. Index N's outgoing ``calls``/``instantiates`` edges that land on a
     class symbol (the "class hints").
  2. Index N's outgoing ``unresolved-call`` edges and parse the bare name
     out of the dst.
  3. For each (class_hint, unresolved_name) pair, look up whether the
     class has a ``contains`` child symbol whose short name matches
     ``unresolved_name``. If yes, emit a synthetic
     ``calls -> Class.method`` edge.
  4. Disambiguate by line proximity when multiple classes match.
  5. Skip if the caller already has a direct ``calls`` edge to the
     resolved method (no duplicates).

The original ``calls -> Class`` (constructor) edge stays — it's
semantically correct. The unresolved edge stays too; it's a harmless
dangling edge whose dst symbol does not exist in the graph.

Why a Linker Instead of Per-Analyzer Logic
------------------------------------------
The two input signals (class-hint edge + unresolved-call edge) are
already emitted by every analyzer that handles chained method calls
(JS/TS, Java, Kotlin, Python, Go all observed). Doing the recovery once
in linker space avoids duplicating type-inference logic per language and
naturally extends to any future analyzer that follows the same
emit-and-fall-back convention.

Priority
--------
Runs at priority 35: after ``containment`` (12), so ``contains`` edges
are populated; after ``inheritance`` (15), so we have the class
structure; before high-level rollup linkers like ``type_hierarchy``
(60) that may want to consume the rewritten edges.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import TYPE_CHECKING

from ..ir import PASS_VERSION, AnalysisRun, Edge, make_pass_id
from .registry import LinkerContext, LinkerResult, register_linker

if TYPE_CHECKING:
    from ..ir import Symbol

PASS_ID = make_pass_id("method-call-recovery")

# Edge types that signal "this caller refers to this class" — could be a
# fallback ``calls`` (Java/Kotlin/Python) or an explicit ``instantiates``
# (JS/TS ``new Foo()``).
_CLASS_HINT_EDGE_TYPES = frozenset({"calls", "instantiates"})


def _short_name(name: str) -> str:
    """Return the unqualified short name for a symbol."""
    for sep in ("#", "::", "."):
        if sep in name:
            return name.rsplit(sep, 1)[-1]
    return name


def _parse_unresolved_name(dst: str) -> str | None:
    """Extract the bare method name from an unresolved dst.

    Convention (see js_ts.py / java.py / py.py): ::

        {lang}:{module_or_package}:0-0:{name}:unresolved

    Returns None if the dst does not match this shape.
    """
    if not dst.endswith(":unresolved"):
        return None
    parts = dst.split(":")
    # Need at least lang, module, span, name, unresolved → 5 parts
    if len(parts) < 5:
        return None
    return parts[-2]


@register_linker(
    "method-call-recovery",
    priority=35,  # After containment (12) + inheritance (15); before rollups.
    description=(
        "Rewrites chained calls=Class + unresolved-call(name) pairs to a "
        "direct calls=Class.method edge so forward slice can traverse the "
        "method without fanning out through siblings (WI-gigoz / Path B')."
    ),
)
def link_method_call_recovery(ctx: LinkerContext) -> LinkerResult:
    """See module docstring for the algorithm."""
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Index symbols by ID for quick lookup; build set of class IDs.
    sym_by_id: dict[str, Symbol] = {s.id: s for s in ctx.symbols}
    class_ids: set[str] = {s.id for s in ctx.symbols if s.kind == "class"}

    # Build per-caller outgoing-edge indexes.
    class_hints: dict[str, list[Edge]] = defaultdict(list)
    unresolved: dict[str, list[tuple[str, Edge]]] = defaultdict(list)
    resolved_targets: dict[str, set[str]] = defaultdict(set)
    for e in ctx.edges:
        if e.edge_type in _CLASS_HINT_EDGE_TYPES and e.dst in class_ids:
            class_hints[e.src].append(e)
        if e.edge_type == "calls" and e.dst.endswith(":unresolved"):
            name = _parse_unresolved_name(e.dst)
            if name is not None:
                unresolved[e.src].append((name, e))
        if e.edge_type == "calls" and e.dst in sym_by_id:
            resolved_targets[e.src].add(e.dst)

    # Build per-class index of contained method short-names.
    # class_id -> { short_name -> method_symbol }
    class_methods: dict[str, dict[str, Symbol]] = defaultdict(dict)
    for e in ctx.edges:
        if e.edge_type != "contains":
            continue
        if e.src not in class_ids:
            continue
        target = sym_by_id.get(e.dst)
        if target is None:
            continue
        class_methods[e.src][_short_name(target.name)] = target

    new_edges: list[Edge] = []
    for caller_id, hints in class_hints.items():
        unresolved_for_caller = unresolved.get(caller_id)
        if not unresolved_for_caller:
            continue
        already_resolved = resolved_targets[caller_id]
        for name, ucall in unresolved_for_caller:
            # Find candidate (class_hint_edge, method_symbol) pairs.
            candidates: list[tuple[Edge, Symbol]] = []
            for hint in hints:
                method = class_methods.get(hint.dst, {}).get(name)
                if method is not None:
                    candidates.append((hint, method))
            if not candidates:
                continue
            # Pick the candidate whose hint is closest in line number to
            # the unresolved-call site (stable tiebreaker on edge id).
            chosen_hint, chosen_method = min(
                candidates,
                key=lambda pair: (abs(pair[0].line - ucall.line), pair[0].id),
            )
            if chosen_method.id in already_resolved:
                continue  # avoid duplicating an existing direct edge
            new_edges.append(Edge.create(
                src=caller_id,
                dst=chosen_method.id,
                edge_type="calls",
                line=ucall.line,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="method_call_recovery",
                confidence=min(chosen_hint.confidence, ucall.confidence),
            ))
            # Mark resolved so a second unresolved sibling on the same
            # method doesn't double-emit.
            already_resolved.add(chosen_method.id)

    run.duration_ms = int((time.time() - start_time) * 1000)
    return LinkerResult(symbols=[], edges=new_edges, run=run)
