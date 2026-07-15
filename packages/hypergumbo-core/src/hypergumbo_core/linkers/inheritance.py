# SPDX-License-Identifier: AGPL-3.0-or-later
"""Infrastructure linker: inheritance for creating extends/implements/includes edges.

This linker creates graph edges from base_classes metadata (extends/implements)
and from included_modules metadata (includes), providing a single implementation
that works across ALL languages instead of duplicating edge creation logic in
each analyzer.

How It Works
------------
1. Finds class/interface/struct/trait symbols with base_classes metadata (for
   extends/implements) and class/module symbols with included_modules metadata
   (for includes)
2. For each base class name, looks up the target symbol
3. Creates extends (for classes) or implements (for interfaces) edges
4. When a base resolves to no in-tree symbol (an external/stdlib base), mints an
   unresolved-external ``extends`` edge instead of dropping the relationship —
   WI-jubag Approach C, the framework-agnostic chokepoint generalization of the
   per-analyzer external-base fallbacks (py.py / js_ts) to EVERY OO language
   (Kotlin, Ruby, Java, ...) and to Python dotted bases the analyzer defers. The
   external target uses the ``external`` sentinel module
   (``{lang}:external:0-0:{name}:unresolved``), which is INV-nuzas-safe by
   construction. A name-based dedup lets this coexist with the per-analyzer
   fallbacks without double-emitting. See ``_external_base_edge``.
5. Also emits ``includes`` edges (evidence_type=ast_includes) from
   ``included_modules`` metadata for Ruby mixins (WI-hatip), resolving module
   names via a dedicated module-name map
6. Runs BEFORE type_hierarchy linker (which needs these edges)

Why a Linker Instead of Per-Analyzer Logic
------------------------------------------
- Analyzers only need to extract base_classes metadata (language-specific)
- Edge creation is identical across languages (language-agnostic)
- Single point of logic for META-001 compliance
- New language support only requires metadata extraction, not edge creation
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

from ..ir import PASS_VERSION, AnalysisRun, Edge, Symbol, make_pass_id
from .registry import LinkerContext, LinkerResult, register_linker

if TYPE_CHECKING:
    pass

PASS_ID = make_pass_id("inheritance-linker")

# WI-jubag Approach C: a base name is only externalized when its canonical
# (last) segment is a syntactically valid identifier. This rejects the raw
# expression text a dynamic superclass leaves in ``base_classes`` — e.g. Ruby
# ``class Foo < Struct.new(:a)`` records ``"Struct.new(:a)"`` — so the
# chokepoint never mints a garbage external target.
_CLEAN_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _build_symbol_maps(
    symbols: list[Symbol],
) -> tuple[
    dict[str, list[Symbol]],
    dict[str, list[Symbol]],
    dict[str, list[Symbol]],
]:
    """Build multi-value lookup maps for classes, interfaces, and modules.

    Uses list values to handle name collisions (e.g., multiple classes named
    'Model' across different files). Resolution is done by
    ``resolve_target_symbol`` using same-file preference and deterministic
    fallback.

    Returns:
        Tuple of (class_by_name, interface_by_name, module_by_name) dicts
        mapping name to list of Symbol candidates. The module map is used
        by WI-hatip's `includes`-edge emission to resolve Ruby
        ``include``/``extend`` mixin targets; modules and classes are
        independent symbol kinds so a name collision between them
        shouldn't shadow either.
    """
    class_by_name: dict[str, list[Symbol]] = {}
    interface_by_name: dict[str, list[Symbol]] = {}
    module_by_name: dict[str, list[Symbol]] = {}

    for sym in symbols:
        if sym.kind in ("class", "struct"):
            if sym.name not in class_by_name:
                class_by_name[sym.name] = []
            class_by_name[sym.name].append(sym)
        elif sym.kind in ("interface", "trait", "protocol"):
            # Traits (Scala, Groovy, Rust) and protocols (Objective-C) are
            # semantically like interfaces — index alongside interfaces so
            # the linker produces `implements` edges for conformance.
            if sym.name not in interface_by_name:
                interface_by_name[sym.name] = []
            interface_by_name[sym.name].append(sym)
        elif sym.kind == "module":
            if sym.name not in module_by_name:
                module_by_name[sym.name] = []
            module_by_name[sym.name].append(sym)

    return class_by_name, interface_by_name, module_by_name


def resolve_target_symbol(
    name: str,
    child_sym: Symbol,
    candidates_by_name: dict[str, list[Symbol]],
) -> tuple[Symbol, bool] | None:
    """Resolve a base class/interface name to a specific Symbol.

    When multiple symbols share the same name (e.g., test stubs named 'Model'),
    uses a priority cascade:

    1. Cross-language gating (WI-zozuz): drop candidates whose language differs
       from the child's. Inheritance is structurally a same-language relation;
       FFI conformance is the territory of dedicated bridge linkers (PyO3,
       cffi, wasm_bindgen, jni). Without this filter, a Python
       ``class FooModule(nn.Module)`` collapses to ``base_classes=["Module"]``
       and erroneously produces an ``implements`` edge to a Rust ``Module``
       trait — see WI-zozuz BUG-03.
    2. Same-file match: prefer the candidate defined in the same file as the child
    3. Deterministic fallback: first by sorted symbol ID

    The centralized linker does not have per-file import context (unlike
    per-analyzer resolvers), so import-based disambiguation is not available.

    Args:
        name: The base class/interface name to resolve
        child_sym: The child symbol (for file context)
        candidates_by_name: Multi-value lookup: name -> list of candidates

    Returns:
        ``(symbol, is_fallback)`` tuple where ``is_fallback`` is True iff the
        match was made by simple-name short-form without import-context
        disambiguation (the deterministic-by-sorted-ID branch). Per INV-zuhub,
        edges built from a fallback match must carry ``confidence <= 0.5`` and
        ``meta["disambiguation_fallback"] = True``. Returns None if no match.
    """
    candidates = candidates_by_name.get(name)
    if not candidates:
        return None

    # Cross-language gating: only consider same-language candidates.
    candidates = [c for c in candidates if c.language == child_sym.language]
    if not candidates:
        return None

    if len(candidates) == 1:
        return candidates[0], False

    child_path = child_sym.path or ""

    # 1. Same-file match: prefer candidate in the same file (precision)
    same_file = [c for c in candidates if c.path == child_path]
    if len(same_file) == 1:
        return same_file[0], False

    # 2. Deterministic fallback: first by symbol ID (sorted for stability).
    #    INV-zuhub flags this branch so the caller can downgrade the edge.
    candidates_sorted = sorted(candidates, key=lambda c: c.id)
    return candidates_sorted[0], True


# Backward-compat alias for callers that imported the private name before
# WI-gifar (PR-1 of INV-nilud) promoted it to the public surface.
_resolve_target_symbol = resolve_target_symbol


def _create_includes_edges(
    symbols: list[Symbol],
    module_by_name: dict[str, list[Symbol]],
    existing_edges: list[Edge],
    run: AnalysisRun,
) -> list[Edge]:
    """Create `includes` edges from ``included_modules`` metadata.

    WI-hatip (PR-2 of INV-nilud): for every class/module Symbol whose
    ``meta["included_modules"]`` lists project-internal module names,
    emit an ``edge_type="includes"`` edge with
    ``evidence_type="ast_includes"``. The Ruby analyzer extracts the
    metadata in Pass 1; this linker resolves the names to Symbols using
    the same disambiguation cascade as extends/implements.

    External module names (e.g., gem-provided ``Sidekiq::Worker``) yield
    no edge — the same "no edge for external base classes" semantics as
    ``_create_inheritance_edges`` follows for extends/implements.

    Args:
        symbols: All symbols (to find candidates with included_modules meta).
        module_by_name: Multi-value module-name lookup map.
        existing_edges: Edges already present (for dedup).
        run: AnalysisRun for provenance stamping.

    Returns:
        List of NEW `includes` edges (no duplicates).
    """
    existing_edge_keys: set[tuple[str, str, str]] = {
        (e.src, e.dst, e.edge_type)
        for e in existing_edges
        if e.edge_type == "includes"
    }

    edges: list[Edge] = []

    for sym in symbols:
        if sym.kind not in ("class", "module"):
            continue
        included = sym.meta.get("included_modules", []) if sym.meta else []
        if not included:
            continue

        for module_name in included:
            # Try qualified name first, then short segment fallback.
            lookup_names = [module_name]
            if "::" in module_name:
                lookup_names.append(module_name.split("::")[-1])
            if "." in module_name:
                lookup_names.append(module_name.split(".")[-1])

            target_sym: Symbol | None = None
            is_fallback = False
            for lookup_name in lookup_names:
                resolved = resolve_target_symbol(
                    lookup_name, sym, module_by_name,
                )
                if resolved is not None:
                    target_sym, is_fallback = resolved
                    break

            if target_sym is None:
                continue  # External module — no edge.

            if target_sym.id == sym.id:
                continue  # Self-include (defensive; Ruby permits it syntactically).

            edge_key = (sym.id, target_sym.id, "includes")
            if edge_key in existing_edge_keys:
                continue

            confidence = 0.5 if is_fallback else 0.95
            edge_meta: dict[str, object] | None = (
                {"disambiguation_fallback": True} if is_fallback else None
            )
            edges.append(Edge.create(
                src=sym.id,
                dst=target_sym.id,
                edge_type="includes",
                line=sym.span.start_line if sym.span else 0,
                confidence=confidence,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="ast_includes",
                meta=edge_meta,
                derived_from=[sym.id, target_sym.id],
            ))

    return edges


def _edge_target_base_name(edge: Edge, symbol_by_id: dict[str, Symbol]) -> str | None:
    """Recover the base-class NAME an existing extends/implements edge points at.

    Used to seed the Approach-C name-dedup set so the chokepoint does not
    re-emit an external edge a per-analyzer fallback (py.py / js_ts) already
    produced. Three cases, in priority order:

    1. ``dst_ref`` populated (imported external base) -> its canonical ``name``.
    2. dst is a resolved in-tree symbol id -> that symbol's short name.
    3. dst is an unresolved sentinel id (``{lang}:external:0-0:{name}:unresolved``,
       ``dst_ref=None`` for un-imported builtins) -> the name slot parsed from
       the id (the segment before the ``:unresolved`` kind suffix).

    Returns ``None`` when the dst is a dangling reference we cannot name (an id
    that is neither in-tree nor an unresolved sentinel) — such an edge simply
    contributes no dedup name.
    """
    if edge.dst_ref is not None:
        return edge.dst_ref.name
    target = symbol_by_id.get(edge.dst)
    if target is not None:
        return target.name
    if edge.dst.endswith(":unresolved"):
        return edge.dst[: -len(":unresolved")].rsplit(":", 1)[-1]
    return None


def _external_base_edge(
    sym: Symbol,
    base_name: str,
    seen_base_names: set[str],
    intree_names: set[str],
    src_has_analyzer_external: bool,
    run: AnalysisRun,
) -> Edge | None:
    """Mint an unresolved-external ``extends`` edge for a dropped base (or None).

    WI-jubag Approach C: when a base resolves to no in-tree symbol, the
    chokepoint represents the relationship with an unresolved-external edge
    instead of dropping it — uniformly for every OO language. The external
    target uses the ``external`` sentinel module
    (``{lang}:external:0-0:{name}:unresolved``, ``dst_ref=None`` per the WI-huzuv
    "unidentified reference" cell), which is INV-nuzas-safe by construction: an
    ``external`` module is never a workspace prefix, so no phantom
    workspace-external node can be minted.

    Returns ``None`` (drop) when:

    - the canonical name is not a clean identifier (dynamic/garbage base);
    - a per-analyzer external fallback already OWNS this class's bare external
      bases (``src_has_analyzer_external`` and the base is not dotted/qualified)
      — py.py / js_ts already emitted them (resolving aliases to their ORIGINAL
      name, which the chokepoint cannot see), so re-minting a sentinel edge under
      the alias would double them. Only the dotted/qualified bases those
      analyzers DEFER are the chokepoint's to add for such a class; languages
      with no per-analyzer fallback (Kotlin, Ruby, ...) never set this flag, so
      the chokepoint owns all of their external bases;
    - the canonical name matches ANY in-tree symbol name (``intree_names``) — a
      base whose in-tree definition simply was not extracted as a class must not
      be turned into a false external (the conservative bias py.py's
      ``_base_module_is_in_tree`` guard also takes); or
    - an edge for this base name was already emitted for this child (an analyzer
      fallback's dotted-precise edge, or an earlier same-named base) —
      ``seen_base_names`` dedup.

    Confidence is left EVIDENCE-DERIVED (omitted -> 0.95 via the ``ast_extends``
    seed, ADR-0039): the extends DETECTION is AST-certain; ``is_resolved=False``
    carries the unresolved target. External bases cannot be classified
    implements-vs-extends without a resolved target, so they default to
    ``extends`` (matching the merged py.py fallback).
    """
    # Strip generics of both grammars (``Foo<T>`` and Python ``Foo[T]``), then
    # take the last dotted/scoped segment as the canonical name.
    stripped = base_name.split("<")[0].split("[")[0]
    is_qualified = ("." in stripped) or ("::" in stripped)
    canonical = re.split(r"::|\.", stripped)[-1]

    if not _CLEAN_IDENT.fullmatch(canonical):
        return None
    if src_has_analyzer_external and not is_qualified:
        return None
    if canonical in intree_names:
        return None
    if canonical in seen_base_names:
        return None

    seen_base_names.add(canonical)
    return Edge.create(
        src=sym.id,
        dst=f"{sym.language}:external:0-0:{canonical}:unresolved",
        edge_type="extends",
        line=sym.span.start_line if sym.span else 0,
        origin=PASS_ID,
        origin_run_id=run.execution_id,
        evidence_type="ast_extends",
        is_resolved=False,
        # INV-rukor: this edge is derived solely from the subclass symbol
        # (the base is external/unresolved, not an in-tree Symbol).
        derived_from=[sym.id],
    )


def _create_inheritance_edges(
    symbols: list[Symbol],
    class_by_name: dict[str, list[Symbol]],
    interface_by_name: dict[str, list[Symbol]],
    existing_edges: list[Edge],
    run: AnalysisRun,
) -> list[Edge]:
    """Create extends/implements edges from base_classes metadata.

    For each symbol with base_classes metadata:
    - If base is an interface in our codebase -> implements edge
    - If base is a class in our codebase -> extends edge
    - If base resolves to no in-tree symbol (external/stdlib) -> an
      unresolved-external ``extends`` edge (WI-jubag Approach C); see
      ``_external_base_edge`` for the guards
    - If edge already exists (from analyzer) -> skip to avoid duplicates

    Uses ``resolve_target_symbol`` to disambiguate when multiple classes or
    interfaces share the same name.

    Args:
        symbols: All symbols to process
        class_by_name: Multi-value map of class name -> list of Symbol candidates
        interface_by_name: Multi-value map of interface name -> list of candidates
        existing_edges: Edges already created by analyzers
        run: Analysis run for provenance

    Returns:
        List of NEW extends/implements edges (not duplicates)
    """
    # Build set of existing edge keys for deduplication
    existing_edge_keys: set[tuple[str, str, str]] = {
        (e.src, e.dst, e.edge_type)
        for e in existing_edges
        if e.edge_type in ("extends", "implements")
    }

    # WI-jubag Approach C: the set of all in-tree symbol names (the in-tree
    # guard) and, per source symbol, the base names already carried by an
    # existing extends/implements edge (the name-dedup that lets the chokepoint
    # coexist with py.py's / js_ts's per-analyzer external fallbacks without
    # double-emitting).
    symbol_by_id: dict[str, Symbol] = {s.id: s for s in symbols}
    intree_names: set[str] = {s.name for s in symbols}
    seen_base_names_by_src: dict[str, set[str]] = {}
    # Sources whose external inheritance bases a per-analyzer fallback (py.py /
    # js_ts) already emitted — an existing unresolved (``is_resolved=False``)
    # extends/implements edge. The chokepoint defers this class's BARE external
    # bases to that analyzer (which resolves aliases the chokepoint can't) and
    # only adds the dotted/qualified bases the analyzer left behind.
    srcs_with_analyzer_external: set[str] = set()
    for e in existing_edges:
        if e.edge_type in ("extends", "implements"):
            name = _edge_target_base_name(e, symbol_by_id)
            if name is not None:
                seen_base_names_by_src.setdefault(e.src, set()).add(name)
            if not e.is_resolved:
                srcs_with_analyzer_external.add(e.src)

    edges: list[Edge] = []

    for sym in symbols:
        if sym.kind not in ("class", "interface", "struct", "trait"):
            continue

        base_classes = sym.meta.get("base_classes", []) if sym.meta else []
        if not base_classes:
            continue

        for base_class_name in base_classes:
            # Handle various naming patterns:
            # - Generic: List<int> -> List
            # - Qualified: Foo.Bar -> try Bar first, then Foo.Bar
            # - Scoped: Foo::Bar -> try Bar first
            base_name = base_class_name

            # Strip generic parameters
            if "<" in base_name:
                base_name = base_name.split("<")[0]

            # Try qualified name lookup first
            lookup_names = [base_name]

            # Add last segment for qualified names
            if "." in base_name:
                lookup_names.append(base_name.split(".")[-1])
            if "::" in base_name:
                lookup_names.append(base_name.split("::")[-1])

            # Try to find the target symbol.
            #
            # Rust kind discipline (WI-zozuz BUG-03 layer 1): Rust structs and
            # enums cannot extend other structs/enums — the only inheritance-
            # like relation Rust permits is ``impl Trait for Struct``. When a
            # Rust source's base_class also matches a Rust struct of the same
            # name in another crate, falling back to the struct emits a
            # spurious extends edge (the candle/LayerNorm→Module-struct case).
            # Restrict Rust struct/enum sources to trait targets.
            rust_kind_discipline = (
                sym.language == "rust" and sym.kind in ("struct", "enum")
            )

            target_sym = None
            edge_type = None
            is_fallback = False

            for lookup_name in lookup_names:
                resolved = resolve_target_symbol(
                    lookup_name, sym, interface_by_name,
                )
                if resolved is not None:
                    target_sym, is_fallback = resolved
                    edge_type = "implements"
                    break
                if rust_kind_discipline:
                    continue
                resolved = resolve_target_symbol(
                    lookup_name, sym, class_by_name,
                )
                if resolved is not None:
                    target_sym, is_fallback = resolved
                    edge_type = "extends"
                    break

            if target_sym is None:
                # WI-jubag Approach C: represent the external/stdlib base with
                # an unresolved-external edge instead of dropping it.
                external_edge = _external_base_edge(
                    sym,
                    base_name,
                    seen_base_names_by_src.setdefault(sym.id, set()),
                    intree_names,
                    sym.id in srcs_with_analyzer_external,
                    run,
                )
                if external_edge is not None:
                    edges.append(external_edge)
                continue

            # Skip self-inheritance
            if target_sym.id == sym.id:
                continue

            # Skip if edge already exists (from analyzer)
            edge_key = (sym.id, target_sym.id, edge_type)
            if edge_key in existing_edge_keys:
                continue

            # INV-zuhub: simple-name fallback edges carry conf <= 0.5 and
            # the disambiguation_fallback flag so consumers can filter the
            # fallback population from the precision-resolved one.
            confidence = 0.5 if is_fallback else 0.95
            edge_meta: dict[str, object] | None = (
                {"disambiguation_fallback": True} if is_fallback else None
            )

            edge = Edge.create(
                src=sym.id,
                dst=target_sym.id,
                edge_type=edge_type,
                line=sym.span.start_line if sym.span else 0,
                confidence=confidence,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type=f"ast_{edge_type}",
                meta=edge_meta,
                derived_from=[sym.id, target_sym.id],
            )
            edges.append(edge)

    return edges


@register_linker(
    "inheritance-linker",
    priority=15,  # Before type_hierarchy (priority 20)
    # CNF: inheritance edges come from any analyzer that populates
    # base_classes / extends / implements / includes metadata. Covers every
    # OO language with class/interface/trait/mixin constructs.
    depends_on=[["python", "javascript", "ruby", "java", "csharp", "kotlin", "scala", "rust", "swift", "dart", "php", "elixir"]],
)
def link_inheritance(ctx: LinkerContext) -> LinkerResult:
    """Create extends/implements edges from base_classes metadata.

    This linker operates on ALL symbols across all languages, creating
    inheritance edges for any symbol that has base_classes metadata.
    It runs before the type_hierarchy linker which depends on these edges.

    Args:
        ctx: Linker context with symbols and run info

    Returns:
        LinkerResult with new extends/implements edges
    """
    start_time = time.time()

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Build multi-value lookup maps (INV-015: handles name collisions)
    class_by_name, interface_by_name, module_by_name = _build_symbol_maps(
        ctx.symbols,
    )

    # Create extends/implements edges (skipping any already from analyzers)
    edges = _create_inheritance_edges(
        ctx.symbols, class_by_name, interface_by_name, ctx.edges, run,
    )

    # WI-hatip (PR-2 of INV-nilud): also emit `includes` edges from
    # `included_modules` metadata so the inherited_calls linker can
    # walk through Ruby mixins.
    edges.extend(_create_includes_edges(
        ctx.symbols, module_by_name, ctx.edges, run,
    ))

    run.duration_ms = int((time.time() - start_time) * 1000)

    return LinkerResult(
        symbols=[],  # No new symbols
        edges=edges,
        run=run,
    )
