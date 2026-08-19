# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical registry of edge types in hypergumbo's behavior map.

Per ADR-0023, every value in the canonical registry should have
``axis="relationship"`` — ``edge_type`` names the relationship that
produced the edge, with endpoint properties queried from the
endpoint nodes themselves rather than smuggled into the type label.

This module is the single source of truth: ``scripts/generate-schema``
imports ``EDGE_TYPES`` to emit both the JSON Schema enum and per-value
axis annotations (under the ``x-axis-of-values`` extension keyword).
Consumers that need a subset of edge types use the curated constants
this module exports (``IMPORT_EDGE_TYPES``, ``INHERITANCE_EDGE_TYPES``)
rather than their own literals; the property test in
``tests/test_edge_types.py`` enforces that every hardcoded set in the
codebase is a subset of this registry.

Axis taxonomy:

- ``relationship`` — ADR-0023 compliant. The value names the
  relationship between src and dst. **Every registered value now sits
  on this axis.**
- ``endpoint_shape`` — **empty.** The ADR-0023 §6 fold retired every
  value that occupied it (see the note above ``EDGE_TYPES``).
- ``pending_classification`` — **empty.** The dispatch and publish
  family audits issued per-value verdicts and promoted or folded every
  member (audit-findings 0001 onward).

Because exactly one axis is occupied, ``edge_types_on_axis(...)`` is
degenerate on current data and has no caller outside this module. It is
kept as the query API for the day a second axis is declared.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final


AXIS_RELATIONSHIP: Final[str] = "relationship"
AXIS_ENDPOINT_SHAPE: Final[str] = "endpoint_shape"
AXIS_PENDING: Final[str] = "pending_classification"

VALID_AXES: Final[frozenset[str]] = frozenset({
    AXIS_RELATIONSHIP,
    AXIS_ENDPOINT_SHAPE,
    AXIS_PENDING,
})


@dataclass(frozen=True)
class EdgeTypeSpec:
    """A single edge type and its axis classification."""

    name: str
    axis: str
    description: str


EDGE_TYPES: Final[tuple[EdgeTypeSpec, ...]] = (
    # ADR-0023 compliant — the value names the relationship.
    EdgeTypeSpec(
        "calls", AXIS_RELATIONSHIP,
        "Caller invokes callee.",
    ),
    EdgeTypeSpec(
        "imports", AXIS_RELATIONSHIP,
        "Module imports another module or symbol.",
    ),
    EdgeTypeSpec(
        "instantiates", AXIS_RELATIONSHIP,
        "Constructor or factory creates an instance.",
    ),
    EdgeTypeSpec(
        "extends", AXIS_RELATIONSHIP,
        "Class extends a superclass.",
    ),
    EdgeTypeSpec(
        "implements", AXIS_RELATIONSHIP,
        "Class implements an interface.",
    ),
    EdgeTypeSpec(
        "contains", AXIS_RELATIONSHIP,
        "Container symbol holds member symbol.",
    ),
    EdgeTypeSpec(
        "uses", AXIS_RELATIONSHIP,
        "Generic symbol-usage relationship.",
    ),
    EdgeTypeSpec(
        "references", AXIS_RELATIONSHIP,
        "Symbol references another by name without invocation.",
    ),
    EdgeTypeSpec(
        "depends_on", AXIS_RELATIONSHIP,
        "A package/build manifest DECLARES a dependency on another "
        "(package.json, Dockerfile, Makefile, meson, ...): a declaration "
        "edge from the manifest/project to its dependency. Distinct from "
        "depends_on_manifest (the import-resolution bridge) -- see WI-dinih.",
    ),
    EdgeTypeSpec(
        "depends_on_manifest", AXIS_RELATIONSHIP,
        "An importing source file RESOLVED to a manifest-declared "
        "dependency (the dependency linker's import->declared-dep bridge; "
        "evidence_type=import_to_manifest): a resolution edge from the file "
        "to the dependency. Distinct from depends_on (the manifest's own "
        "declaration) -- see WI-dinih.",
    ),
    EdgeTypeSpec(
        "sources", AXIS_RELATIONSHIP,
        "Sources another file (e.g., shell ``source``).",
    ),
    EdgeTypeSpec(
        "subprocess_calls", AXIS_RELATIONSHIP,
        "Symbol invokes another symbol via a subprocess.",
    ),
    EdgeTypeSpec(
        "links", AXIS_RELATIONSHIP,
        "Generic linkage relationship.",
    ),
    EdgeTypeSpec(
        "wraps", AXIS_RELATIONSHIP,
        "Decorator or middleware wraps the target symbol.",
    ),
    EdgeTypeSpec(
        "module_attr_ref", AXIS_RELATIONSHIP,
        "Reads an attribute on an imported module (e.g., os.environ).",
    ),
    # Promoted from pending_classification by audit-findings 0001 (dispatch family
    # audit): apex of single-target dispatch via runtime indirection.
    EdgeTypeSpec(
        "dispatches_to", AXIS_RELATIONSHIP,
        "Caller dispatches to callee via runtime indirection "
        "(virtual method, function pointer, DI resolution, etc.).",
    ),
    # Promoted from pending_classification by audit-findings 0001 (publish family
    # audit): apex of producer→consumer over an async channel.
    EdgeTypeSpec(
        "event_publishes", AXIS_RELATIONSHIP,
        "Producer publishes an event/message that the consumer "
        "receives via an async channel (event bus, queue, CRDT, etc.).",
    ),
    # Registry-completeness fills per WI-tavas-voror sweep —
    # canonical relationship-axis values per ADR-0023's
    # 'Edge types that stay' list (and a couple of obvious-canonical
    # additions the original list didn't enumerate). Producers
    # already emitted these; the registry was just missing entries.
    EdgeTypeSpec(
        "inherits", AXIS_RELATIONSHIP,
        "Class/contract inherits from a parent (used by languages "
        "where 'inherits' reads more naturally than 'extends').",
    ),
    EdgeTypeSpec(
        "decorated_by", AXIS_RELATIONSHIP,
        "Symbol is decorated/annotated by another (e.g., Python "
        "decorator, Java annotation, C# attribute, Rust derive).",
    ),
    EdgeTypeSpec(
        "includes", AXIS_RELATIONSHIP,
        "File or class includes / sources / mixes-in another unit's "
        "content (LaTeX \\include, RST .. include::, Meson subdir, "
        "Ruby `include`/`extend` mixin — WI-hatip).",
    ),
    EdgeTypeSpec(
        "constrains", AXIS_RELATIONSHIP,
        "pip ``-c`` / ``--constraint`` file reference — constrains "
        "version selection without forcing install. Peer of "
        "``includes`` (which models ``-r`` / ``--requirement``). "
        "Surfaced by WI-nubuv ext A in linkers/requirements.py.",
    ),
    EdgeTypeSpec(
        "defines_target", AXIS_RELATIONSHIP,
        "Config file defines a build/run/deploy target (Makefile "
        "rule, package.json script, pyproject entry point, "
        "Compose service, etc.).",
    ),
    EdgeTypeSpec(
        "data_flows_to", AXIS_RELATIONSHIP,
        "Data flow edge per ADR-0015 — value computed at src "
        "reaches dst.",
    ),
    EdgeTypeSpec(
        "module_exports", AXIS_RELATIONSHIP,
        "Module exposes a symbol as part of its public surface "
        "(JS export, Python __all__, Rust pub, etc.).",
    ),
    EdgeTypeSpec(
        "overrides", AXIS_RELATIONSHIP,
        "Method overrides a parent's same-named method (parallel "
        "to extends/implements; the override declaration itself).",
    ),

    # ADR-0023 Phase 4b' COMPLETE (WI-pumav): all 21 long-tail
    # endpoint_shape values were producer-migrated to canonical relationship
    # types + meta discriminators (audit-findings 0017, Batches 1a-7) and
    # their dead registry entries pruned. Together with the earlier
    # dst-kind / bridge / IPC / publish / dispatch closures and the
    # protocol-call (WI-hirud) + script_src (Batch 0) prunes, the
    # endpoint_shape axis of Edge.edge_type is now EMPTY — the fold declared
    # by ADR-0023 §6 is complete.
    #
    # The pending_classification axis is now ALSO empty (WI-sumik / WI-pusuv
    # Option B): the resolver/OpenAPI/RPC family (resolver_implements,
    # resolver_for_type, openapi_implements, implements_rpc) was producer-
    # migrated to canonical implements/references + meta per audit-findings
    # 0016 (Batches A/B) and its dead registry entries pruned here. The
    # AXIS_PENDING constant is retained (a future edge type may land pending
    # its per-family audit per ADR-0023 §5), but no value occupies it today.
)


def all_edge_type_names() -> frozenset[str]:
    """Return every canonical edge type name."""
    return frozenset(spec.name for spec in EDGE_TYPES)


def edge_types_on_axis(axis: str) -> tuple[EdgeTypeSpec, ...]:
    """Return all edge type specs whose axis equals *axis*.

    Use this in place of hardcoded sets like
    ``_STRUCTURAL_EDGE_TYPES = {"extends", "implements"}``: query by
    axis (or by another property) instead of enumerating values, so
    new specs that match the axis are picked up automatically.
    """
    return tuple(spec for spec in EDGE_TYPES if spec.axis == axis)


def find_edge_type(name: str) -> EdgeTypeSpec | None:
    """Look up an edge type by name; return None if not registered."""
    for spec in EDGE_TYPES:
        if spec.name == name:
            return spec
    return None


def is_grpc_rpc_implementation(
    edge_type: str, meta: Mapping[str, object] | None
) -> bool:
    """True for a folded gRPC RPC-implementation edge (audit-findings 0016).

    ``implements_rpc`` folded to canonical ``implements`` +
    ``meta['protocol']='grpc'`` (a Go method satisfying a proto RPC IS a
    Go interface implementation). But ``implements_rpc`` carried call-like
    consumer coupling — traceable for taint (``taint.TAINT_CALL_EDGE_TYPES``),
    I/O-boundary reachability (``io_boundary._TRACEABLE_EDGE_TYPES`` /
    ``verify_claims._COVERAGE_CALL_EDGE_TYPES``), ranked at call weight
    (``ranking`` weight 1.0), and forward-traversable in slices — whereas
    canonical ``implements`` is a *structural* (reverse-only, weight-0.5)
    edge. This predicate lets those consumers keep matching the folded
    form WITHOUT wholesale-including every structural ``implements`` edge,
    so gRPC reachability / taint / ranking / slice coupling is preserved
    (audit-findings 0016 finding 3). It is the single source of truth for
    "is this the folded gRPC edge", shared by every consumer so they can
    never drift. Callers pass primitives so it works on both dict-shaped
    edges (``edge['type']`` / ``edge['meta']``) and ``Edge`` objects
    (``edge.edge_type`` / ``edge.meta``).
    """
    return (
        edge_type == "implements"
        and meta is not None
        and meta.get("protocol") == "grpc"
    )


# ---------------------------------------------------------------------------
# Phase-2 migration helpers (per ADR-0023 §6 Phase 2)
# ---------------------------------------------------------------------------
#
# Centralized constants that consumers use in place of hand-rolled
# ``*_EDGE_TYPES`` literals. Each covers (a) the canonical
# relationship-axis name(s) the registry keeps through Phase 4, plus
# (b) the deprecated endpoint_shape variants Phase 3 will fold back.
# After Phase 4's deprecation removal, the deprecated entries vanish
# and the constant simplifies; consumer call sites stay correct
# throughout the migration.
#
# The drift linter catches future analyzers that introduce
# ``imports_widget`` without registering it (subset coherence with
# ``EDGE_TYPES``); WI-tavas-voror's triage of emitted-but-unregistered
# values is the complementary completeness check that catches
# additions to the registry which should also enter these sets.

IMPORT_EDGE_TYPES: Final[frozenset[str]] = frozenset({
    "imports",
})
"""Edges representing import-shaped relationships, regardless of dst.kind.

Per ADR-0023 §6 Phase 2: replaces ad-hoc consumer-side sets like
``_IMPORT_EDGE_TYPES = {"imports", "imports_module"}`` (the silent
bug from ADR-0023 §1 case 1 — Vue/Svelte/Astro/React component
imports were silently miscategorized because ``imports_component``
was missing). After Phase 4b (this PR), the deprecated
``imports_module`` and ``imports_component`` entries are removed
from both the registry and this set; consumers querying by
``dst.kind`` (e.g., ``dst.kind == 'component'``) get the
component-import-specific behavior directly."""


INHERITANCE_EDGE_TYPES: Final[frozenset[str]] = frozenset({
    "extends",
    "inherits",
    "implements",
})
"""Class is-a (structural inheritance) edges, regardless of the language's
spelling. ``extends`` (Python/Java/TypeScript/...) and ``inherits`` (Solidity,
and any language where "inherits" reads more naturally) are the *same*
relationship; ``implements`` covers interface is-a. All three are
``AXIS_RELATIONSHIP``.

Replaces hand-rolled consumer literals like
``_STRUCTURAL_EDGE_TYPES = {"extends", "implements"}`` (WI-lobif) that silently
omitted ``inherits`` — making Solidity / ``inherits``-language inheritance
invisible to slice forward-BFS structural handling and ranking's
structural-edge preservation. Method-level ``overrides`` is deliberately NOT
included: it is a distinct method→parent-method relationship, and neither
consumer treated it as structural before."""


PROTOCOL_KINDS: Final[frozenset[str]] = frozenset({
    "ipc",      # Tauri-style command bus, OS-level inter-process channels
    "http",     # HTTP / HTTPS client-server calls
    "grpc",     # gRPC over HTTP/2
    "graphql",  # GraphQL operations against a schema
})
"""Closed enumeration of values for ``edge.meta['protocol']``.

Per ADR-0023 §6 (WI-vumum-juvil Phase 3 + WI-hirud Phase 4b'): the
protocol-call family (``http_calls``, ``grpc_calls``, ``graphql_calls``)
was folded into the canonical ``calls`` relationship plus
``meta['protocol']`` carrying the wire protocol, and its ``endpoint_shape``
registry entries were then pruned.
``ipc`` is already in flight (audit-findings 0002 / WI-hahap-farid:
the Tauri IPC linker emits ``calls`` + ``meta['protocol']='ipc'``).
The enumeration is closed: adding a new wire protocol requires an
ADR amendment plus an entry here.

The choice of *closed* enumeration mirrors :data:`BRIDGE_KINDS`
(and ADR-0015's ``access_mode``): a small finite vocabulary keeps
consumers' filter logic stable, and any analyzer wanting a new
value has a forced governance moment. The closure is currently
documented-but-unenforced — a property test would need to walk
the producer-side ``meta={...}`` literals, which the current
static drift linter does not do.

Why protocol is genuinely orthogonal to ``edge_type``: the
relationship is the same (a caller invokes a callee); the
protocol picks the wire encoding / dispatch fabric. Two sites
making an HTTP call and a gRPC call to functionally-equivalent
endpoints share the relationship, not the wire shape — so the
protocol belongs in ``meta``, not in the relationship name."""


BRIDGE_KINDS: Final[frozenset[str]] = frozenset({
    "cgo",            # Go's cgo FFI
    "ffi",            # generic FFI (Python ctypes, Lua FFI, Ruby FFI, etc.)
    "napi",           # Node-API (N-API)
    "wasm",           # WebAssembly bridge
    "native",         # JNI and other native interfaces
    "context_bridge", # Electron contextBridge
})
"""Closed enumeration of values for ``edge.meta['bridge_kind']``.

Per ADR-0023 §6 Phase 3 (WI-mifor-vabul): Phase 3 folds the
bridge-family endpoint_shape values (``cgo_bridge``, ``ffi_bridge``,
``napi_bridge``, ``wasm_bridge``, ``native_bridge``,
``bridge_invokes``) into the canonical ``calls`` relationship plus
``meta['bridge_kind']`` carrying the bridge mechanism. The
enumeration is closed: adding a new bridge mechanism requires an
ADR amendment plus an entry here.

The choice of *closed* enumeration is the same shape ADR-0015 used
for ``access_mode``: a small finite vocabulary keeps consumers'
filter logic stable, and any analyzer wanting a new value has a
forced governance moment (a property test enforces the closure once
the value-set property test for `meta['bridge_kind']` lands;
currently the closure is documented-but-unenforced because the
existing static drift linter only walks set literals, not nested
dict-literal kwargs).

The mechanism is genuinely orthogonal to ``src.language`` /
``dst.language`` — multiple language pairs can use the same bridge
mechanism (e.g., both Lua FFI and Python ctypes use ``ffi``), and
the language-pair information is recoverable from
``src.language``/``dst.language``."""


# ---------------------------------------------------------------------------
# Axis-coherence drift detection (Edge.edge_type wrapper)
# ---------------------------------------------------------------------------
#
# The drift detector catches the silent-bug shape from ADR-0023: consumer-
# side hardcoded sets of edge_type values that drift from the canonical
# registry (either by missing values that runtime emits, or by including
# values that runtime never emits — see the audit playbook's Step 4).
#
# The implementation is field-agnostic and lives in
# ``hypergumbo_core.axis_drift``; this wrapper supplies the Edge.edge_type
# defaults (the ``EDGE_TYPE`` name filter and the ``EDGE_TYPES`` registry).
# Other axis-bearing fields call ``axis_drift.find_drift`` directly.


def find_axis_drift(
    repo_root: Path,
    *,
    strict: bool = False,
) -> list[str]:
    """Return drift offenders for ``Edge.edge_type`` consumer-side sets.

    Thin wrapper over :func:`hypergumbo_core.axis_drift.find_drift`
    with Edge.edge_type defaults: ``name_filter="EDGE_TYPE"`` and the
    canonical registry (``EDGE_TYPES``). Other axis-bearing fields
    (``Symbol.kind``, ``evidence_type``, ...) call ``find_drift``
    directly with their own ``name_filter`` and registry per
    ADR-0024's enforcement template.

    The default search scope (``packages/``, ``scripts/``, ``.agent/``)
    is the parameterized helper's
    :data:`~hypergumbo_core.axis_drift.DEFAULT_SEARCH_ROOTS`; pass
    ``search_roots=`` to ``find_drift`` directly if a narrower or
    wider scope is needed.

    When ``strict=True`` (per WI-variv-lujug, post-Phase-4b), the
    linter additionally enforces axis-principle membership: consumer
    sets are flagged if they contain any registry value whose axis is
    not in ``{relationship, pending_classification}``. Pending values
    stay allowed by design — they are real edges produced by GraphQL/
    OpenAPI/RPC analyzers awaiting per-family audit, and a consumer set
    may legitimately reference one. (The resolver/OpenAPI/RPC family —
    the last pending values — has since been folded to canonical
    relationships per audit-findings 0016: the folded gRPC edge is now
    matched by ``is_grpc_rpc_implementation`` rather than by a set
    membership, so no consumer set names a pending value today; the
    registry prune that drains the axis follows.) When the per-family
    audits move pending values to canonical-relationship, the existing
    membership check catches any consumer that didn't follow the
    rename.

    Used by the property test in ``tests/test_edge_types.py`` and the
    pre-commit linter at ``scripts/check-edge-type-drift``.
    """
    from hypergumbo_core.axis_drift import find_drift
    allowed_axis_names: frozenset[str] | None = None
    name_to_axis: dict[str, str] | None = None
    if strict:
        allowed_axis_names = frozenset(
            {AXIS_RELATIONSHIP, AXIS_PENDING},
        )
        name_to_axis = {spec.name: spec.axis for spec in EDGE_TYPES}
    return find_drift(
        repo_root,
        name_filter="EDGE_TYPE",
        registry_names=all_edge_type_names(),
        allowed_axis_names=allowed_axis_names,
        name_to_axis=name_to_axis,
    )
