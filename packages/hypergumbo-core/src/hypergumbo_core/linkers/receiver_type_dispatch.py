# SPDX-License-Identifier: AGPL-3.0-or-later
"""Infrastructure linker: resolve non-hierarchy ``x.foo()`` calls via a
receiver-type-keyed candidate index (extension methods + UFCS free functions).

Why this exists (INV-vigaf)
---------------------------
Some languages let ``x.foo()`` resolve to a callable that is **not** a method
on ``x``'s type hierarchy:

* **extension methods / functions** — Kotlin ``fun T.foo()``, C# ``static
  foo(this T)``, Swift/Scala-3/Dart ``extension`` blocks, Obj-C categories,
  F#/Haxe/Pascal type-extensions. ``foo`` is declared *outside* ``T`` but
  invocable on a ``T`` receiver.
* **UFCS** (Uniform Function Call Syntax) — D/Nim ``x.foo()`` is sugar for the
  free function ``foo(x)``, matched by ``foo``'s first-parameter type.

Both are the **same search**: build an index keyed by the declared
receiver/first-parameter type name, then look up by the call's inferred
``receiver_type_hint`` and match the callee short name. Only the *analyzer-side
detection* (which meta key populates the index) and the emitted
``evidence_type`` differ; ``edge_type`` is always ``calls`` (ADR-0023 forbids an
``extension``/``ufcs`` edge_type — it is endpoint-derivable).

This is DISTINCT from ``inherited_calls.py`` (the hierarchy-walk family): that
linker walks the receiver type's ancestor chain to find an *inherited method*;
this linker searches a flat receiver-type→callable index for a target that is
not in the hierarchy at all. They share the analyzer→linker contract but not the
search, so they are sibling Infrastructure linkers rather than one module.

The analyzer/linker contract (INV-nilud analogue)
-------------------------------------------------
Analyzers do NOT resolve these calls themselves. They:

1. stamp the callable **definition** symbol's ``meta`` with a receiver-type key
   — ``extension_receiver`` (extension methods) or ``ufcs_receiver_type`` (UFCS
   free functions) — naming the type the callable attaches to; and
2. emit the call site as an unresolved ``calls`` edge carrying
   ``receiver_type_hint`` (via ``make_unresolved_edge``).

This linker owns the receiver-type-keyed search. Resolution is gated to the
call site's own ``src_lang`` (INV-milud: never cross-resolve across languages),
and an ambiguous hint (two distinct same-type/same-name candidates) withholds
rather than misbinds (the INV-fahub spirit).
"""

from __future__ import annotations

import time

from ..ir import PASS_VERSION, AnalysisRun, Edge, Symbol, make_pass_id
from .method_call_recovery import parse_unresolved_name, short_name
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerResult,
    register_linker,
)

PASS_ID = make_pass_id("receiver-type-dispatch")

# Callable-definition ``meta`` keys that declare a receiver type, paired with
# the ``evidence_type`` a resolution through that key emits. Extension members
# reuse the existing ``ast_call_extension`` seed (0.8); UFCS free functions use
# the ``ast_call_ufcs`` seed added alongside this linker (also 0.8 — both are a
# receiver-type-verified bind).
_RECEIVER_META_EVIDENCE: tuple[tuple[str, str], ...] = (
    ("extension_receiver", "ast_call_extension"),
    ("ufcs_receiver_type", "ast_call_ufcs"),
)


def _build_receiver_type_index(
    symbols: list[Symbol],
) -> dict[tuple[str, str, str], list[tuple[str, str]]]:
    """Index callable definitions by (language, receiver type, short name).

    Value is a list of ``(callable_symbol_id, evidence_type)`` — a list so the
    resolver can detect an ambiguous (multiple distinct target) hint.
    """
    index: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    for sym in symbols:
        meta = sym.meta or {}
        for meta_key, evidence in _RECEIVER_META_EVIDENCE:
            receiver_type = meta.get(meta_key)
            if not receiver_type:
                continue
            key = (sym.language, receiver_type, short_name(sym.name))
            index.setdefault(key, []).append((sym.id, evidence))
    return index


@register_linker(
    "receiver-type-dispatch",
    priority=19,  # Just after inherited-calls (18): resolve the non-hierarchy
    # receiver calls the ancestor-walk linker leaves unresolved.
    description=(
        "Resolves unresolved calls carrying receiver_type_hint to an extension "
        "method or UFCS free function whose declared receiver/first-parameter "
        "type matches, via a receiver-type-keyed index (INV-vigaf)."
    ),
    activation=LinkerActivation(always=True),
)
def link_receiver_type_dispatch(ctx: LinkerContext) -> LinkerResult:
    """See module docstring for the algorithm."""
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    index = _build_receiver_type_index(ctx.symbols)
    symbol_by_id: dict[str, Symbol] = {s.id: s for s in ctx.symbols}
    existing_call_pairs: set[tuple[str, str]] = {
        (e.src, e.dst)
        for e in ctx.edges
        if e.edge_type == "calls" and e.is_resolved
    }

    new_edges: list[Edge] = []
    for edge in ctx.edges:
        if edge.edge_type != "calls" or edge.is_resolved:
            continue
        receiver_type_hint = (edge.meta or {}).get("receiver_type_hint")
        if not receiver_type_hint:
            continue
        src_sym = symbol_by_id.get(edge.src)
        src_lang = src_sym.language if src_sym else None
        if src_lang is None:
            continue
        callee_short = parse_unresolved_name(edge.dst)
        if not callee_short:
            continue

        candidates = index.get((src_lang, receiver_type_hint, callee_short))
        if not candidates:
            continue
        # Ambiguity gate: two distinct targets for the same (lang, type, name)
        # → withhold rather than misbind (INV-fahub).
        distinct_targets = {sym_id for sym_id, _ev in candidates}
        if len(distinct_targets) > 1:
            continue
        target_id, evidence_type = candidates[0]
        if (edge.src, target_id) in existing_call_pairs:
            continue

        resolved = Edge.create(
            src=edge.src, dst=target_id, edge_type="calls",
            line=edge.line,
            origin=PASS_ID, origin_run_id=run.execution_id,
            evidence_type=evidence_type,
            is_resolved=True,
            derived_from=[edge.src, target_id],
        )
        new_edges.append(resolved)
        existing_call_pairs.add((edge.src, target_id))

    run.duration_ms = int((time.time() - start_time) * 1000)
    return LinkerResult(symbols=[], edges=new_edges, run=run)
