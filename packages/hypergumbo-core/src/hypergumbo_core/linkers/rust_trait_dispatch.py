# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: Rust trait-impl method dispatch (WI-kivut).

How It Works
------------
Rust dispatches trait methods two ways:

* **Static dispatch** via generic bounds (``fn f<T: Trait>(x: T)``) — the
  compiler monomorphizes to a concrete impl at compile time. The call
  graph sees an unbound call on the generic parameter.
* **Dynamic dispatch** via trait objects (``&dyn Trait``) — the call
  dispatches through a vtable at runtime. The call graph sees a call on
  the trait itself.

Either way, the static call graph treats the concrete ``impl Trait for
MyStruct { fn method() { … } }`` bodies as unreachable because the user
code calls ``x.method()`` on a generic / trait-object receiver rather
than on a concrete ``MyStruct`` value. WI-tubot's 2026-04-11 aggregate-
v5 prospector run pinned ``rust_trait_impl + rust_instruction_descriptor``
at ~3004 dead-code-maybe candidates for this reason.

This linker consumes the ``implements`` edges that the Rust analyzer's
existing inheritance pass already emits (WI-tulid) — one edge per
``impl Trait for Struct`` block, connecting the struct symbol to the
trait symbol — and, for each such edge, emits ``dispatches_to`` edges
from the trait symbol to every concrete method named
``"{Struct}::{method}"`` in the same file as the struct. With those
edges in place, any path that reaches the trait (a call site on a trait
object, a trait bound on a generic function, or even a ``use Trait``
import at a reachable module) transitively reaches the concrete
implementation bodies.

Why a Linker (Not Per-Analyzer Logic)
-------------------------------------
Identical reasoning to the Airflow / Jackson / Django framework-dispatch
linkers: the per-language inheritance detection (the ``implements``
edges) is already done. The trait-dispatch synthesis is graph-structural
shuffling — read implements edges, write dispatches_to edges — and
belongs in a single place, not spread across the Rust analyzer.

Scope
-----
Rust-internal. This linker is a Phase-1 approximation of the full
static- and dynamic-dispatch handling the tracker item describes: it
connects the trait to its concrete implementations' methods, but does
NOT parse call sites for generic bounds or trait-object receivers.
That second phase requires call-site type information the Rust analyzer
does not currently expose. Phase 1's edges are sufficient to make the
impl methods reachable in forward slices from ``main`` whenever the
trait is itself reachable, which is the empirically common case.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import TYPE_CHECKING

from ..ir import PASS_VERSION, AnalysisRun, Edge, make_pass_id
from .registry import LinkerContext, LinkerResult, register_linker

if TYPE_CHECKING:
    from ..ir import Symbol

PASS_ID = make_pass_id("rust-trait-dispatch-linker")


def _rust_method_owner(name: str) -> str | None:
    """Return the impl target of a Rust method name, or None.

    Rust methods are emitted with name ``"{ImplTarget}::{method_name}"``.
    For ``"MyStruct::foo"``, returns ``"MyStruct"``. Names without the
    ``::`` separator are not impl methods and return None.
    """
    if "::" not in name:
        return None
    return name.rsplit("::", 1)[0]


def _build_struct_method_index(
    symbols: list["Symbol"],
) -> dict[tuple[str, str], list["Symbol"]]:
    """Group Rust method symbols by ``(file_path, owning_struct_name)``.

    The Rust analyzer qualifies method symbols with the impl target's
    short name (``"MyStruct::foo"``), so ``MyStruct``'s methods are
    recovered by stripping the suffix. Keying by file path disambiguates
    structs of the same name in different modules — Rust permits
    ``mod a { struct Config {…} }`` and ``mod b { struct Config {…} }``
    in separate files, each with their own impls.
    """
    index: dict[tuple[str, str], list[Symbol]] = defaultdict(list)
    for sym in symbols:
        if sym.language != "rust":
            continue
        if sym.kind != "method":
            continue
        owner = _rust_method_owner(sym.name)
        if owner is None:
            continue
        index[(sym.path or "", owner)].append(sym)
    return index


def _symbol_index_by_id(symbols: list["Symbol"]) -> dict[str, "Symbol"]:
    """Build an id → Symbol lookup for edge-endpoint resolution."""
    return {sym.id: sym for sym in symbols}


def _iter_implements_edges(edges: list[Edge]) -> list[Edge]:
    """Return only the ``implements`` edges from the edge list."""
    return [e for e in edges if e.edge_type == "implements"]


@register_linker(
    "rust-trait-dispatch",
    priority=23,
    description="Emit dispatches_to edges from Rust trait symbols to the concrete methods of impl blocks that implement them (WI-kivut)",
)
def link_rust_trait_dispatch(ctx: LinkerContext) -> LinkerResult:
    """Fan dispatches_to edges out from each trait to its concrete impl methods.

    For every ``implements`` edge ``struct_sym → trait_sym``, walks the
    Rust method symbols under ``struct_sym``'s name and file and emits a
    ``dispatches_to`` edge ``trait_sym → method_sym``. Methods that
    aren't part of the trait's declared interface still receive edges —
    hypergumbo's Rust analyzer does not emit the trait's required-method
    list, so distinguishing trait-required methods from inherent
    methods would require SCIP-level information (which is WI-duzul
    territory, not this linker's). The resulting edge set may include
    a few inherent methods that weren't actually dispatched through the
    trait; the false-positive cost is well below the false-negative
    cost of leaving every trait-required method looking dead.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    implements_edges = _iter_implements_edges(ctx.edges)
    if not implements_edges:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return LinkerResult(symbols=[], edges=[], run=run)

    sym_by_id = _symbol_index_by_id(ctx.symbols)
    method_index = _build_struct_method_index(ctx.symbols)

    # Dedupe against any prior dispatches_to edges already in the graph.
    existing_keys: set[tuple[str, str, str]] = {
        (e.src, e.dst, e.edge_type)
        for e in ctx.edges
        if e.edge_type == "dispatches_to"
    }

    edges: list[Edge] = []
    for impl_edge in implements_edges:
        struct_sym = sym_by_id.get(impl_edge.src)
        trait_sym = sym_by_id.get(impl_edge.dst)
        if struct_sym is None or trait_sym is None:
            # Unresolved implements edge (pointing to an external trait)
            continue
        if struct_sym.language != "rust":
            continue
        if trait_sym.language != "rust":
            continue
        methods = method_index.get((struct_sym.path or "", struct_sym.name), [])
        for method in methods:
            key = (trait_sym.id, method.id, "dispatches_to")
            if key in existing_keys:
                continue
            existing_keys.add(key)
            edges.append(
                Edge.create(
                    src=trait_sym.id,
                    dst=method.id,
                    edge_type="dispatches_to",
                    line=trait_sym.span.start_line if trait_sym.span else 0,
                    confidence=0.85,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="rust_trait_dispatch",
                ),
            )

    run.duration_ms = int((time.time() - start_time) * 1000)
    return LinkerResult(symbols=[], edges=edges, run=run)
