# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical registry of edge types in hypergumbo's behavior map.

Per ADR-0023, every value in the canonical registry should have
``axis="relationship"`` — ``edge_type`` names the relationship that
produced the edge, with endpoint properties queried from the
endpoint nodes themselves rather than smuggled into the type label.

This module is the single source of truth: ``scripts/generate-schema``
imports ``EDGE_TYPES`` to emit both the JSON Schema enum and per-value
axis annotations (under the ``x-axis-of-values`` extension keyword).

It also exports the meta-discriminator vocabulary consumers need once
``edge_type`` alone stopped answering their question: ``IMPORT_EDGE_TYPES``
and ``INHERITANCE_EDGE_TYPES`` (curated subsets), ``PROTOCOL_KINDS`` and
``BRIDGE_KINDS`` (the ADR-3bbb linker subcategories a mechanism belongs to),
``is_grpc_rpc_implementation`` (a predicate that reads ``meta`` because the
fold moved the distinction there), and ``find_axis_drift`` — the linter that
fails the build when a registry value stops matching its declared axis.
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

import ast
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ._literal_scan import (
    declared_linker_languages,
    language_guarded_line_spans,
    string_literal_members,
)


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
consumer treated it as structural before.

``includes`` is deliberately NOT included either, and that exclusion is
load-bearing — see :func:`is_inheritance_edge`, which is the predicate a
consumer should call. Membership in *this* set means "an edge of this type is
an is-a edge no matter what produced it", which is true of these three and
false of ``includes``."""


CONCRETE_INHERITANCE_EDGE_TYPES: Final[frozenset[str]] = frozenset({
    "extends",
    "inherits",
})
"""Inheritance spellings where the child is-a *concrete* parent type.

Split out from :data:`INHERITANCE_EDGE_TYPES` because virtual dispatch
through concrete inheritance is **language-dependent** — Go, C++, Rust and
C# do not dispatch through it, so ``type_hierarchy`` gates these edges on
``_extends_admits_dispatch`` before treating a parent method as dispatchable
to a child's same-named method.

``inherits`` is placed here rather than being split further by the
destination's kind. Solidity's ``is`` spells both relations — measured on
openzeppelin-contracts, 412 of 516 ``inherits`` edges point at a ``contract``
and 104 at an ``interface`` — so a dst-kind refinement is *expressible*. It
is not implemented because it would buy nothing today: Solidity is not in the
no-virtual-dispatch set, so both arms reach the same verdict for every
``inherits``-emitting language currently in the tree. Revisit if an
``inherits``-emitting language is ever added to that set."""


INTERFACE_SATISFACTION_EDGE_TYPES: Final[frozenset[str]] = frozenset({
    "implements",
})
"""Inheritance spellings where dispatch is virtual in **every** language.

Interface satisfaction always dispatches, so ``type_hierarchy`` applies no
language gate to these. Ruby mixin ``includes`` edges join this role at the
predicate level (:func:`inheritance_dispatch_role`) without joining the type
set, for the reason given on :data:`MIXIN_INCLUDE_EVIDENCE_TYPES`."""


MIXIN_INCLUDE_EVIDENCE_TYPES: Final[frozenset[str]] = frozenset({
    "ast_includes",
})
"""Evidence types that make an ``includes`` edge a *mixin*, not a file read.

``includes`` is a registered two-meaning value — its own registry entry reads
"File or class includes / sources / mixes-in another unit's ...". Nine
producers emit it and eight of them mean file inclusion (latex, make, meson,
puppet, requirements, rst, scss, twig); exactly one, the inheritance linker's
``_create_includes_edges``, means the Ruby ``include`` / ``extend`` mixin.

Measured on the Rails app *postal*: 41 ``includes`` edges — 39 ``class ->
module`` mixins from the inheritance linker, and 2 ``file ->
external_symbol`` edges from an SCSS ``@include`` of a Sass mixin. Adding the
bare string ``"includes"`` to :data:`INHERITANCE_EDGE_TYPES` would have
enrolled a stylesheet ``@include``, a Makefile ``include`` and a LaTeX
``\\include`` as class is-a edges in slice expansion, ranking centrality and
the dead-code ancestor walk — the mirror image of the hand-rolled copies this
registry exists to replace.

The discriminator is ``evidence_type`` (ADR-0028's inference-pathway axis),
which is the right axis for the question: it distinguishes "I saw a mixin
declaration in a class body" from "I saw an include directive". Absence is
read conservatively as *not* a mixin, because eight of nine producers mean
file inclusion."""


def is_inheritance_edge(
    edge_type: str,
    *,
    evidence_type: str | None = None,
) -> bool:
    """Is this edge a class is-a edge?

    The one answer every consumer should use. Prefer it over
    ``edge_type in INHERITANCE_EDGE_TYPES``: the set alone cannot express
    the Ruby-mixin half of the overloaded ``includes`` value, and six
    consumers previously each guessed at the family and disagreed
    (INV-nosoz).

    Args:
        edge_type: the edge's type. Note that a *serialized* edge carries
            this under the ``"type"`` key, not ``"edge_type"``.
        evidence_type: the edge's inference pathway. Only consulted for
            ``includes``, where it is the discriminator between a mixin and
            a file read. On a serialized edge this lives in
            ``meta["evidence_type"]``; see :func:`inheritance_edge_fields`.

    Returns:
        True for ``extends`` / ``inherits`` / ``implements`` unconditionally,
        and for ``includes`` only when *evidence_type* names a mixin
        declaration.
    """
    if edge_type in INHERITANCE_EDGE_TYPES:
        return True
    if edge_type == "includes":
        return evidence_type in MIXIN_INCLUDE_EVIDENCE_TYPES
    return False


DISPATCH_ROLE_CONCRETE: Final[str] = "concrete"
DISPATCH_ROLE_INTERFACE: Final[str] = "interface"


def inheritance_dispatch_role(
    edge_type: str,
    *,
    evidence_type: str | None = None,
) -> str | None:
    """Which dispatch rule applies to this inheritance edge?

    Returns :data:`DISPATCH_ROLE_INTERFACE` when dispatch is virtual in every
    language (interface satisfaction, and Ruby mixin inclusion — a module's
    instance methods enter the includer's method resolution order
    unconditionally), :data:`DISPATCH_ROLE_CONCRETE` when it depends on the
    language, and ``None`` when the edge is not an inheritance edge at all.

    The two roles exist only to decide whether the per-language
    concrete-dispatch gate applies. Consumers that just need "is this an is-a
    edge?" should call :func:`is_inheritance_edge`.
    """
    if not is_inheritance_edge(edge_type, evidence_type=evidence_type):
        return None
    if edge_type in CONCRETE_INHERITANCE_EDGE_TYPES:
        return DISPATCH_ROLE_CONCRETE
    return DISPATCH_ROLE_INTERFACE


def inheritance_edge_fields(edge: object) -> tuple[str, str | None]:
    """Read ``(edge_type, evidence_type)`` off an ``Edge`` **or** a dict.

    The two shapes diverge in two ways that have each cost a debugging
    session: a serialized edge names its type ``"type"`` rather than
    ``"edge_type"``, and its ``evidence_type`` is nested under ``meta``
    rather than sitting at the top level. Consumers read whichever shape
    their stage of the pipeline hands them — linkers get ``Edge`` objects,
    the CLI read views get dicts — so the family predicate has to accept
    both or it will be silently wrong in half the tree.
    """
    if isinstance(edge, Mapping):
        edge_type = edge.get("edge_type") or edge.get("type") or ""
        evidence = edge.get("evidence_type")
        if evidence is None:
            meta = edge.get("meta") or {}
            if isinstance(meta, Mapping):
                evidence = meta.get("evidence_type")
        return str(edge_type), evidence
    return (
        str(getattr(edge, "edge_type", "") or ""),
        getattr(edge, "evidence_type", None),
    )


def is_inheritance_edge_record(edge: object) -> bool:
    """:func:`is_inheritance_edge`, reading both edge shapes."""
    edge_type, evidence = inheritance_edge_fields(edge)
    return is_inheritance_edge(edge_type, evidence_type=evidence)


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


# Languages that can emit each inheritance spelling, measured from the
# producer sites rather than assumed. Used to decide whether a linker that
# declares ``depends_on`` is *entitled* to omit a spelling: a Java-only
# linker omitting ``inherits`` is correct, ``type_hierarchy`` omitting it is
# the INV-nosoz defect.
INHERITANCE_EDGE_TYPE_LANGUAGES: Final[dict[str, frozenset[str]]] = {
    "extends": frozenset({
        "blade", "java", "javascript", "python", "ruby", "twig", "typescript",
    }),
    "inherits": frozenset({"bitbake", "solidity"}),
    "implements": frozenset({
        "graphql", "haskell", "java", "javascript", "rust", "typescript",
        "vhdl",
    }),
}


def find_partial_inheritance_family_literals(repo_root: Path) -> list[str]:
    """Find literals that enumerate PART of the inheritance family by hand.

    INV-nosoz measured six consumers each answering "is this an inheritance
    edge?" with their own literal, four of which omitted ``inherits`` — so
    Solidity's 516 ``inherits`` edges on openzeppelin-contracts produced zero
    dispatch. The rule enforced here is the same one audit-findings 0018 set
    for ``Symbol.kind``: **enumerate all or none**. A complete literal is
    allowed (the resolver is preferred, but a complete set is not the bug);
    a partial one is not.

    Three exemptions, each principled rather than an escape hatch:

    * ``packages/hypergumbo-lang-*`` — a per-language analyzer's incomplete
      set is *correct*: Solidity has no ``implements``, Java no ``inherits``.
    * an expression guarded by a ``language`` comparison in the same boolean
      expression, which is a per-language predicate that happens to live in
      core.
    * a module whose ``@register_linker(depends_on=...)`` languages cannot
      produce the missing spelling — see
      :data:`INHERITANCE_EDGE_TYPE_LANGUAGES`.

    Two structural filters keep the signal honest rather than merely quiet:
    this module is skipped (the registry that *defines* the family is
    entitled to enumerate it, and to publish decompositions like
    :data:`CONCRETE_INHERITANCE_EDGE_TYPES`), and a literal naming only ONE
    family member is not a partial enumeration. The second filter is what
    separates a vocabulary from a lookup-table row: ``scip/edges.py`` maps
    ``("is_implementation", "implements")`` — SCIP's field name to ours —
    which mentions the family without claiming to enumerate it.

    Not exempted, deliberately: a set that names what the *enclosing function
    itself emits* rather than the family (``inheritance.py``'s two dedup
    sets). Those are scoped correctly, and they read as partial family
    literals, so they carry an inline ``# not-the-family`` marker instead —
    a marker a reviewer can see, rather than an allow-list entry in a file
    nobody opens.
    """
    full = frozenset(INHERITANCE_EDGE_TYPES)
    offenders: list[str] = []
    for pkg_src in sorted((repo_root / "packages").glob("*/src")):
        if not pkg_src.parent.name.startswith("hypergumbo-core"):
            continue
        for path in sorted(pkg_src.rglob("*.py")):
            if path.name == "edge_types.py":
                continue
            source = path.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(source)
            except SyntaxError:  # pragma: no cover - core source is valid
                continue
            lines = source.splitlines()
            guarded = language_guarded_line_spans(tree)
            declared = declared_linker_languages(tree)
            seen: set[int] = set()
            for node in ast.walk(tree):
                names = string_literal_members(node)
                lineno: int | None = getattr(node, "lineno", None)
                if names is None or lineno is None or lineno in seen:
                    continue
                present = names & full
                if len(present) < 2 or present == full:
                    continue
                if any(lo <= lineno <= hi for lo, hi in guarded):
                    continue
                if "not-the-family" in lines[lineno - 1]:
                    continue
                missing = sorted(
                    t for t in full - present
                    if declared is None
                    or (
                        INHERITANCE_EDGE_TYPE_LANGUAGES.get(t, frozenset())
                        & declared
                    )
                )
                if not missing:
                    continue
                seen.add(lineno)
                offenders.append(
                    f"{path.relative_to(repo_root)}:{lineno}: "
                    f"{sorted(names)} omits {missing} — call "
                    f"is_inheritance_edge() or INHERITANCE_EDGE_TYPES",
                )
            offenders.extend(
                _equality_chain_offenders(tree, full, declared, path, repo_root)
            )
    return offenders


def _equality_chain_offenders(
    tree: ast.AST,
    full: frozenset[str],
    declared: frozenset[str] | None,
    path: Path,
    repo_root: Path,
) -> list[str]:
    """Flag a function that branches on family members via ``==`` chains.

    A literal-collection scanner cannot see ``if e.edge_type == "extends":
    ... elif e.edge_type == "implements":`` — and that shape, in
    ``type_hierarchy.build_inheritance_maps``, is precisely what cost
    Solidity its dispatch on all 516 ``inherits`` edges. A guard blind to
    the site that caused the defect it is named after is not a guard.

    Scoped per enclosing function, since two functions legitimately
    handling different halves of the family are not one partial
    enumeration.
    """
    offenders: list[str] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        present: set[str] = set()
        for node in ast.walk(func):
            if not isinstance(node, ast.Compare):
                continue
            if not all(isinstance(op, ast.Eq) for op in node.ops):
                continue
            for side in [node.left, *node.comparators]:
                if isinstance(side, ast.Constant) and side.value in full:
                    present.add(side.value)
        if len(present) < 2 or present == full:
            continue
        missing = sorted(
            t for t in full - present
            if declared is None
            or (INHERITANCE_EDGE_TYPE_LANGUAGES.get(t, frozenset()) & declared)
        )
        if not missing:
            continue
        offenders.append(
            f"{path.relative_to(repo_root)}:{func.lineno}: "
            f"{func.name}() branches on {sorted(present)} via == and omits "
            f"{missing} — call is_inheritance_edge() or "
            f"inheritance_dispatch_role()",
        )
    return offenders
