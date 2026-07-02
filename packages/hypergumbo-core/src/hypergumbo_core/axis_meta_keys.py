# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical registry of ``Symbol.meta`` and ``Edge.meta`` key names.

Per WI-runod Wave 9 and the four-part axis-declaration template in
[ADR-0024](../../docs/adr/0024-axis-declaration-template.md), every
*meta key* used by producers / consumers across the codebase is
declared once here with its axis (``symbol_meta`` or ``edge_meta``)
and a short description of what the key encodes.

This module is the structural sibling of
:mod:`hypergumbo_core.symbol_kinds` and
:mod:`hypergumbo_core.evidence_types`. Same shape, different field:
``Symbol.kind`` and ``Edge.evidence_type`` are *enum-valued scalars*
on their parent dataclasses; ``Symbol.meta`` and ``Edge.meta`` are
*open dict fields* whose key names are the vocabulary being tracked.

Axis taxonomy
-------------

- ``symbol_meta`` — the key appears on ``Symbol.meta``. Example:
  ``framework_role`` (Wave 5 fold residue per
  audit-findings 0013 — the post-fold home of values that used to
  smuggle through ``Symbol.kind``).
- ``edge_meta`` — the key appears on ``Edge.meta``. Example:
  ``framework_dispatch`` (Wave 5 fold residue per audit-findings 0014
  — the post-fold home of framework-specific
  ``Edge.evidence_type`` values).
- ``entrypoint_meta`` — the key appears on ``Entrypoint.meta``
  (``entrypoints.Entrypoint``, not an ``ir.py`` dataclass). Example:
  ``evidence_type`` (WI-rukam — the Entrypoint analog of the
  ``Edge.evidence_type`` inference-pathway axis, nested under ``meta``
  so the Entrypoint record keeps a 4-field typed core). The value
  vocabularies live as single-source frozensets in ``entrypoints.py``
  (``ENTRYPOINT_SOURCES`` / ``ENTRYPOINT_EVIDENCE_TYPES``).

A handful of keys are emitted on both sides (e.g., ``language`` could
plausibly appear on either dict); convention in this codebase is that
those keys instead live on the parent dataclass as a typed field
(``Symbol.language``, ``Edge.evidence_lang``) rather than as a meta
key. The registry therefore declares each key on exactly one axis.

Why a registry rather than free-form strings
--------------------------------------------

Three structural problems the registry solves, mirroring the
``symbol_kinds`` / ``evidence_types`` rationale:

1. **Producer / consumer coherence.** When producer A writes
   ``edge.meta["framewok_role"] = ...`` (typo) and consumer B reads
   ``meta.get("framework_role")``, the consumer silently gets
   ``None``. A registry plus an AST-walking drift linter catches this
   shape at commit time.
2. **Fold-residue discoverability.** The Wave 5/6 producer
   migrations moved ~30 distinct values from ``Symbol.kind`` and
   ``Edge.evidence_type`` into meta keys. Without a registry, a
   consumer trying to "list every framework role we recognise" has
   no canonical place to look — the answer is scattered across
   ~25 linker files.
3. **Promotion gating per
   [ADR-0024 §"Fold-residue discipline"](../../docs/adr/0024-axis-declaration-template.md#fold-residue-discipline).**
   When a meta key recurs (≥3 distinct values OR ≥2 producer
   modules), the rule is to consider promoting it to a sibling typed
   field on the parent dataclass. The registry is what lets us
   notice when the threshold is met.

Drift detection
---------------

The set-literal AST walker in
:mod:`hypergumbo_core.axis_drift` is the wrong shape for meta keys —
meta keys are accessed via ``meta["..."]`` *subscripts* and
``meta.get("...")`` *method calls*, not declared as set literals
named ``*KEY*``. A subscript-access drift linter is a follow-on
work item (filed at registry-establishment time); the registry
without the linter is already useful as the canonical vocabulary
for documentation, ADR cross-references, and the audit / fold
trail.

Coverage scope
--------------

Registry seeded with every meta key empirically observed in producer
code across packages/hypergumbo-core/src and the language-analyzer
packages. New keys added by future producer migrations should be
registered here in the same PR — the property test in
``tests/test_axis_meta_keys.py`` is the structural enforcement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


AXIS_SYMBOL_META: Final[str] = "symbol_meta"
AXIS_EDGE_META: Final[str] = "edge_meta"
AXIS_ENTRYPOINT_META: Final[str] = "entrypoint_meta"

VALID_AXES: Final[frozenset[str]] = frozenset({
    AXIS_SYMBOL_META,
    AXIS_EDGE_META,
    AXIS_ENTRYPOINT_META,
})


@dataclass(frozen=True)
class MetaKeySpec:
    """A single ``Symbol.meta`` / ``Edge.meta`` key and its axis."""

    name: str
    axis: str
    description: str
    # ADR-0038 ruling 2: for an ``edge_meta`` key whose applicability varies by
    # ``Edge.edge_type``, declare the two edge-type sets — ``applicable`` (a
    # ``None`` value means "missing data, fix the emitter") and ``na`` (a
    # ``None`` value means "the question does not arise"). Both stay ``None``
    # for keys that apply uniformly. Only ``access_mode`` populates these today
    # (its 17-type census, INV-tibob); the remaining canonical edge types are
    # UNCLASSIFIED — deferred to the polyglot-census follow-up.
    applicable_edge_types: frozenset[str] | None = None
    na_edge_types: frozenset[str] | None = None


# ADR-0038 ruling 2: the ``access_mode`` per-edge-type applicability matrix,
# keyed on INV-tibob's 17-type census. APPLICABLE — a ``None`` value counts as
# missing data (fix the emitter). DECLARED-N/A — a ``None`` value means the
# question does not arise (the dataflow annotate passes skip these; a
# constructor call ``instantiates`` is not an access). The remaining canonical
# edge types (``edge_types.all_edge_type_names()`` is 54-wide) are UNCLASSIFIED,
# deferred to the polyglot-census follow-up. Two deliberate deferrals:
#   * the dataflow-DIRECTION family (``crypto_flow`` / ``data_flows_to``) — it
#     is PR-2's ``data_direction`` territory (ADR-0038 ruling 3), not access;
#   * ``script_src`` — a census structural type, but on the mid-fold
#     ``endpoint_shape`` axis (ADR-0023). Its access_mode N/A declaration is
#     deferred until that fold settles (it is behaviorally moot: a
#     ``<script src>`` include never reaches the dataflow classifier, so it is
#     never stamped regardless). Deferring it also keeps this set axis-pure for
#     the ``check-edge-type-drift`` strict linter — these are named
#     ``*_EDGE_TYPES`` on purpose so that linter DOES watch them, so every
#     member must be a relationship/pending-axis edge type.
_ACCESS_MODE_APPLICABLE_EDGE_TYPES: Final[frozenset[str]] = frozenset({
    "calls", "references", "module_attr_ref", "event_publishes",
})
_ACCESS_MODE_NA_EDGE_TYPES: Final[frozenset[str]] = frozenset({
    "contains", "decorated_by", "depends_on", "depends_on_manifest",
    "dispatches_to", "extends", "implements", "imports", "inherits",
    "overrides", "uses", "instantiates",
})


META_KEYS: Final[tuple[MetaKeySpec, ...]] = (
    # ------------------------------------------------------------------
    # Edge.meta — Wave 5 framework-dispatch fold residues (ADR-0028).
    # Per audit-findings 0014, the framework-specific evidence types
    # fold to canonical inference + one of these meta keys.
    # ------------------------------------------------------------------
    MetaKeySpec("framework_dispatch", AXIS_EDGE_META,
                "Framework whose dispatch convention produced this "
                "edge (e.g. 'django_orm', 'kafka_streams', "
                "'phoenix_event'). Fold residue per audit-findings "
                "0014 / WI-kagik Wave 5."),
    MetaKeySpec("detection_pattern", AXIS_EDGE_META,
                "Pattern-shape inference heuristic that produced "
                "this edge (e.g. 'abi_name_match', 'jni_naming', "
                "'implicit_convention'). Fold residue per "
                "audit-findings 0014 §'detection_pattern partition'."),
    MetaKeySpec("call_construct", AXIS_EDGE_META,
                "Source-language call construct collapsed under "
                "``ast_call`` apex (e.g. 'method', 'function', "
                "'pipe', 'application'). Fold residue per "
                "audit-findings 0012 / WI-nibis Wave 4."),
    MetaKeySpec("call_locality", AXIS_EDGE_META,
                "File-locality of a call edge — whether caller and "
                "callee live in the same source file ('same_file') or "
                "different files ('cross_file'). A SEPARATE axis from "
                "``call_construct`` (which names the syntactic call "
                "shape): INV-vakaf split this out after bash/objc were "
                "found smuggling 'cross_file' through ``call_construct``. "
                "Sparse by convention: set to 'cross_file' on "
                "boundary-crossing calls; ABSENT means same-file (the "
                "common case)."),
    MetaKeySpec("call_kind", AXIS_EDGE_META,
                "Specialized call type for Cluster E sub-case (a) "
                "reclassifications (e.g. 'abi', 'subprocess', "
                "'db_query'). Fold residue per audit-findings 0010."),
    MetaKeySpec("mechanism", AXIS_EDGE_META,
                "Dispatch mechanism on a ``dispatches_to`` edge — names "
                "HOW the dispatch is wired when the canonical ``edge_type`` "
                "alone is too coarse. Current value space: 'otp_call' / "
                "'otp_cast' (Elixir/Erlang GenServer synchronous call vs "
                "asynchronous cast, folded from the former otp_call/otp_cast "
                "edge_types per WI-rorul). Distinct from ``framework_dispatch`` "
                "(framework name, e.g. 'otp_genserver') and ``call_construct`` "
                "(call-edge syntactic shape)."),
    MetaKeySpec("ref_construct", AXIS_EDGE_META,
                "Source-language construct on a reference-FAMILY "
                "(non-call) edge where the canonical ``edge_type`` alone "
                "is too coarse. Current value space: 'jsx' (JSX render), "
                "'script_src' (HTML ``<script src=...>`` include), "
                "'websocket_endpoint' (WebSocket endpoint connectivity), "
                "'event_emit' (Solidity event emission), 'dispatch_table' "
                "(C/C++ dispatch-table reference) — all folded to "
                "``references`` edges (INV-vavat / ADR-0023 endpoint-shape "
                "migration). Renamed from ``construct`` per INV-lajov to "
                "disambiguate from the sibling ``call_construct`` (which "
                "names the call SHAPE on ``calls`` edges): distinct "
                "vocabularies, distinct edge families, zero overlap."),
    MetaKeySpec("receiver", AXIS_EDGE_META,
                "Call-site receiver classification "
                "(e.g. 'bare', 'external', 'typed'). "
                "Disambiguates ``ast_call`` + ``call_construct=method`` "
                "edges per audit-findings 0012."),
    MetaKeySpec("resolution_quality", AXIS_EDGE_META,
                "Pathway-quality label on ``ast_call`` edges "
                "(e.g. 'recovery', 'ambiguous'), ORTHOGONAL to "
                "``Edge.is_resolved``. It names uncertainty in the "
                "resolution *pathway* (heuristic line-proximity vs. direct "
                "binding), NOT target locality — in-repo-ness lives on "
                "``is_resolved`` (ADR-0037 ruling 1/3), and resolution "
                "certainty proper lives in ``confidence`` / ``evidence_type`` "
                "(ADR-0028). A recovery edge to a real in-repo method is "
                "correctly ``is_resolved=True`` with "
                "``resolution_quality='recovery'``."),
    # ------------------------------------------------------------------
    # Edge.meta — protocol / bridge / dispatch vocabularies (predates
    # the axis-registry pattern; PROTOCOL_KINDS and BRIDGE_KINDS in
    # edge_types.py enumerate the value vocabularies).
    # ------------------------------------------------------------------
    MetaKeySpec("protocol", AXIS_EDGE_META,
                "Wire-level protocol on ``calls`` edges that absorb "
                "the IPC family (e.g. 'abi', 'ipc', 'ipc_event'). "
                "Value vocabulary tracked at "
                "``edge_types.PROTOCOL_KINDS``; per "
                "ADR-0023 Phase 4b precedent."),
    MetaKeySpec("bridge_kind", AXIS_EDGE_META,
                "FFI / native-bridge mechanism on ``calls`` edges "
                "that absorb the bridge family (e.g. 'cgo', 'napi', "
                "'wasm', 'ffi'). Value vocabulary tracked at "
                "``edge_types.BRIDGE_KINDS``."),
    MetaKeySpec("dispatch_kind", AXIS_EDGE_META,
                "Dispatch shape on ``dispatches_to`` edges (e.g. "
                "'route', 'di_register', 'di_resolve', 'annotated'). "
                "Fold residue from ADR-0023 Phase 4b dispatch / "
                "publish family."),
    MetaKeySpec("channel_kind", AXIS_EDGE_META,
                "Channel shape on event / message edges "
                "(e.g. 'websocket', 'message_queue', 'crdt')."),
    MetaKeySpec("channel", AXIS_EDGE_META,
                "Logical dataflow channel / conduit the data flows through "
                "on a dataflow-family edge — a message topic "
                "(``event_publishes``), IPC channel, CRDT document, crypto "
                "primitive, etc. The CONDUIT referent ONLY (WI-pozom): a "
                "WebSocket route path uses ``url_path`` and a dispatch target "
                "is the edge ``dst`` — never ``channel``. The transport "
                "family is on the co-occurring ``channel_kind`` key."),
    MetaKeySpec("url_path", AXIS_EDGE_META,
                "URL / route path on a protocol-boundary edge (e.g. a "
                "WebSocket endpoint path, or a cross-language WS "
                "client→server route). Distinct from ``channel`` (the "
                "dataflow conduit/topic) per WI-pozom."),
    MetaKeySpec("ffi_mechanism", AXIS_EDGE_META,
                "FFI mechanism for cross-language bridges where "
                "``bridge_kind`` is too coarse (e.g. specific "
                "JNI / WASM-bindgen / luajit-ffi variants)."),
    # ------------------------------------------------------------------
    # Edge.meta — I/O boundary annotations.
    # ------------------------------------------------------------------
    MetaKeySpec("io_primitive", AXIS_EDGE_META,
                "Fully-qualified IO primitive matched at the "
                "boundary (e.g. 'os.open', 'socket.socket'). Set on "
                "io_boundary edges by "
                "``io_boundary.compute_boundary_map``."),
    MetaKeySpec("io_boundary", AXIS_EDGE_META,
                "Boundary classification on edges that cross an IO "
                "primitive (e.g. 'net_send', 'fs_read', "
                "'process_spawn'). Set by io_boundary catalog."),
    # ------------------------------------------------------------------
    # Edge.meta — dataflow access modes (per the WI-vehur dataflow
    # axis).
    # ------------------------------------------------------------------
    MetaKeySpec("access_mode", AXIS_EDGE_META,
                "The effect the edge's source has on its destination, from the "
                "four-cell vocabulary 'read' / 'write' / 'mutate' / 'delete' "
                "(ADR-0015 + ADR-0038 ruling 1) — derived per-edge from AST "
                "role at emission (read-evidence → read; store position "
                "→ write; augmented-assignment / receiver-mutating call "
                "→ mutate; delete target → delete), NOT from the line's "
                "statement kind. Applicability is per-edge-type (ADR-0038 ruling "
                "2 / INV-tibob): populated on calls / references / "
                "module_attr_ref / event_publishes; Declared-N/A on the "
                "structural edge types AND on instantiates (a constructor call "
                "is not an access). See ``access_mode_applicable_edge_types`` / "
                "``access_mode_na_edge_types``. Set by the ``dataflow`` "
                "annotate passes.",
                applicable_edge_types=_ACCESS_MODE_APPLICABLE_EDGE_TYPES,
                na_edge_types=_ACCESS_MODE_NA_EDGE_TYPES),
    MetaKeySpec("data_direction", AXIS_EDGE_META,
                "Dataflow DIRECTION across a cross-boundary edge — the way "
                "data moves between src and dst, from the closed vocabulary "
                "'src_to_dst' / 'dst_to_src' / 'bidirectional' "
                "(``ir.VALID_DATA_DIRECTIONS``). ADR-0038 ruling 3: the "
                "post-eviction home of the FFI-bridge / protocol-linker "
                "direction semantic that ``access_mode='write'`` + "
                "``dest_access_mode='read'`` used to smuggle (the cgo docstring "
                "said it outright — 'Go caller passes data to C'). DIRECTION, "
                "not ACCESS — a SEPARATE axis from ``access_mode`` (ADR-0038 "
                "ruling 1). ``dest_access_mode`` is removed entirely (it was "
                "observed zero-entropy — 'read' on every populated record). "
                "``event_publishes`` edges do NOT carry this key: publishing IS "
                "a genuine write, so they keep ``access_mode='write'`` "
                "(ADR-0038 §Neutral/acknowledged)."),
    # ------------------------------------------------------------------
    # Edge.meta — INV-zuhub resolution-precision provenance.
    # ------------------------------------------------------------------
    MetaKeySpec("disambiguation_fallback", AXIS_EDGE_META,
                "True when the edge's destination was resolved by "
                "simple-name fallback (no fully-qualified module / "
                "namespace / kind disambiguator available). Carries "
                "with it the contract that ``confidence <= 0.5`` on "
                "the same edge — see INV-zuhub. Consumers filter the "
                "fallback population from the precision-resolved one "
                "by checking this flag. Currently set by "
                "``linkers/inheritance.py``; per-linker conformance "
                "for the four remaining edge-type families "
                "(calls / dispatches_to / references / module_exports) "
                "tracked under INV-zuhub items 1-3."),
    # ------------------------------------------------------------------
    # Symbol.meta — Wave 5 framework-role fold residue (ADR-0027).
    # Audit-findings 0013 folded 29 framework-role Symbol.kind values
    # to canonical + this key.
    # ------------------------------------------------------------------
    MetaKeySpec("framework_role", AXIS_SYMBOL_META,
                "Framework-specific role of a symbol whose canonical "
                "``Symbol.kind`` is a generic language construct "
                "(e.g. 'event_publisher', 'route', 'graphql_resolver'). "
                "Fold residue per audit-findings 0013 / WI-habut "
                "Wave 5."),
    MetaKeySpec("reference_syntax", AXIS_SYMBOL_META,
                "Use-site reference syntax of an external boundary Symbol "
                "(e.g. 'unresolved', 'attribute', 'module', 'namespace') "
                "whose canonical ``Symbol.kind`` is 'external_symbol'. Per "
                "ADR-0036 Ruling 2 the id kind-slot is a pure copy of "
                "``Symbol.kind``, so the reference syntax that used to live "
                "there moves to this registered home. WI-pubiv."),
    # ------------------------------------------------------------------
    # Symbol.meta — Wave 6 file/build-shape fold residues.
    # Audit-findings 0005, 0006, 0007 + Wave 6 PR 1-6 introduced
    # these keys as canonical homes for fold-target metadata.
    # ------------------------------------------------------------------
    MetaKeySpec("module_system", AXIS_SYMBOL_META,
                "Module system on synthetic file-shape Symbols "
                "(e.g. 'esm', 'commonjs'). Fold residue for the "
                "``module_file`` → ``file`` migration per "
                "audit-findings 0005."),
    MetaKeySpec("component_framework", AXIS_SYMBOL_META,
                "Framework on single-file-component symbols "
                "(e.g. 'vue', 'svelte', 'astro'). Fold residue for "
                "the ``component_file`` → ``file`` migration per "
                "audit-findings 0011."),
    MetaKeySpec("package_ecosystem", AXIS_SYMBOL_META,
                "Package-manager ecosystem on package-shaped "
                "Symbols (e.g. 'npm', 'composer'). Fold residue for "
                "the ``npm_package`` / ``composer_package`` → "
                "``package`` migration per audit-findings 0005."),
    MetaKeySpec("entry_role", AXIS_SYMBOL_META,
                "Entry-point role on file-shape Symbols "
                "(e.g. 'main', 'script'). Fold residue for the "
                "``main_entry`` / ``script`` → ``file`` migration."),
    MetaKeySpec("dependency_scope", AXIS_SYMBOL_META,
                "Dependency scope on dependency Symbols "
                "(e.g. 'dev'). Fold residue for the "
                "``devDependency`` → ``dependency`` migration."),
    MetaKeySpec("install_mode", AXIS_SYMBOL_META,
                "Install mode on requirement Symbols "
                "(e.g. 'editable'). Fold residue per audit-findings "
                "0006 build-config cluster."),
    MetaKeySpec("install_source", AXIS_SYMBOL_META,
                "Install source on requirement Symbols "
                "(e.g. 'url'). Fold residue per audit-findings "
                "0006."),
    MetaKeySpec("config_format", AXIS_SYMBOL_META,
                "Configuration format on config-shape Symbols "
                "(e.g. 'tsconfig'). Fold residue per audit-findings "
                "0005."),
    MetaKeySpec("task_implementation", AXIS_SYMBOL_META,
                "Task implementation language on task Symbols "
                "(e.g. 'python'). Fold residue for the "
                "``python_task`` → ``task`` migration."),
    MetaKeySpec("test_dialect", AXIS_SYMBOL_META,
                "Test framework dialect on test Symbols "
                "(e.g. 'robot'). Fold residue per audit-findings "
                "0006."),
    MetaKeySpec("block_type", AXIS_SYMBOL_META,
                "Block subtype for generic ``block`` Symbols where "
                "the construct admits sub-classification "
                "(e.g. 'datasource', 'generator' in Prisma). Fold "
                "residue per WI-runod Wave 6 PR 4."),
    # ------------------------------------------------------------------
    # Symbol.meta — IR boundary materializer.
    # ------------------------------------------------------------------
    MetaKeySpec("external_boundary", AXIS_SYMBOL_META,
                "True on synthetic boundary Symbols emitted by "
                "``ir._canonical_external_id`` for unresolved-but-"
                "referenced names. Read by ``is_external_boundary`` "
                "helper."),
    MetaKeySpec("ecosystem", AXIS_SYMBOL_META,
                "Provenance class of an external dependency's "
                "distribution channel (ADR-0041 §3): 'stdlib' (shipped "
                "with the language runtime) vs 'third_party' (fetched "
                "from a package registry). Stamped on tier-3 boundary "
                "Symbols by ``ir.create_boundary_nodes`` from the "
                "single-source language stdlib catalog "
                "(``io_boundary.IoBoundaryCatalog.is_stdlib_module``); "
                "absent when the language has no enumerated stdlib. A "
                "SEPARATE axis from supply-chain ``tier`` (distance), "
                "``directness`` (declaration relationship), and — "
                "crucially — from ``package_ecosystem`` (which names the "
                "package-MANAGER registry, e.g. 'npm'/'composer', of "
                "package-shaped Symbols; this key names provenance "
                "CLASS, a different question)."),
    MetaKeySpec("directness", AXIS_SYMBOL_META,
                "Declaration relationship between the project's "
                "manifests and an external dependency (ADR-0041 §2): "
                "'direct' (declared in a project manifest), "
                "'transitive' (in the manifest but not declared "
                "direct), or 'undeclared' (imported but declared "
                "nowhere — a phantom dependency, and the bucket stdlib "
                "falls into). Stamped once at classification time on "
                "boundary/dependency Symbols by "
                "``ir.create_boundary_nodes`` via "
                "``DependencyManifest.classify_directness``. A SEPARATE "
                "axis from supply-chain ``tier`` (distance) and from "
                "the ``ecosystem`` provenance axis (stdlib vs "
                "third_party, ADR-0041 §3)."),
    # ------------------------------------------------------------------
    # Symbol.meta — declaration-shape annotations.
    # ------------------------------------------------------------------
    MetaKeySpec("base_classes", AXIS_SYMBOL_META,
                "List of base-class names on class / interface "
                "Symbols. Used by framework_patterns to identify "
                "Django Model, SQLAlchemy Base, etc."),
    MetaKeySpec("parent_base_classes", AXIS_SYMBOL_META,
                "Transitive ancestor base classes on class Symbols. "
                "Populated by the type_hierarchy linker."),
    MetaKeySpec("decorators", AXIS_SYMBOL_META,
                "List of decorator names on function / method / "
                "class Symbols. Used by framework_patterns to "
                "identify Flask routes, FastAPI handlers, etc."),
    MetaKeySpec("visibility_signal", AXIS_SYMBOL_META,
                "Which signal determined the canonical ``Symbol.visibility`` "
                "level (INV-jusot) — one of 'language_modifier' (an explicit "
                "``modifiers`` term or the legacy Apex/Clojure "
                "``meta['visibility']``), 'name_convention' (Python "
                "leading-underscore), or 'default' (public). Provenance for the "
                "computed visibility field; set by the finalize visibility "
                "pass. Replaces the retired ``meta['visibility']`` key, whose "
                "language-syntactic value is now folded into the typed "
                "``Symbol.visibility`` field (the meta key collided with the "
                "field under the cross-axis hygiene test)."),
    MetaKeySpec("exported", AXIS_SYMBOL_META,
                "True if the Symbol is exported from its file "
                "(e.g. ES module ``export``, Go capitalized name). "
                "Read by reachability / dead-code passes."),
    MetaKeySpec("export_scope", AXIS_SYMBOL_META,
                "Export visibility scope "
                "(e.g. 'module', 'package', 'public'). Finer-"
                "grained than the boolean ``exported`` for "
                "languages with multi-level visibility."),
    MetaKeySpec("export_source", AXIS_SYMBOL_META,
                "Originating source for re-exported symbols "
                "(e.g. ES re-export chains). Resolves "
                "``export { foo } from './bar'`` to the underlying "
                "definition site."),
    MetaKeySpec("override", AXIS_SYMBOL_META,
                "True if the method overrides a superclass / "
                "interface method. Set by analyzers that parse "
                "``override`` / ``@Override`` keywords."),
    MetaKeySpec("virtual", AXIS_SYMBOL_META,
                "True on virtual / abstract method Symbols. "
                "Distinct from ``override`` (a virtual method may "
                "or may not also override)."),
    MetaKeySpec("abstract", AXIS_SYMBOL_META,
                "True on abstract class / method Symbols. "
                "Distinguishes interface-like classes from "
                "concrete classes in languages with explicit "
                "``abstract`` keywords."),
    MetaKeySpec("static", AXIS_SYMBOL_META,
                "True on static method / field Symbols. Affects "
                "method-call resolution (no implicit ``self`` "
                "receiver)."),
    MetaKeySpec("is_local", AXIS_SYMBOL_META,
                "True on locally-scoped Symbols "
                "(e.g. nested function definitions). Read by "
                "containment / slice filters."),
    MetaKeySpec("is_recursive", AXIS_SYMBOL_META,
                "True on recursive function / method Symbols. "
                "Detected by call-graph self-loop analysis."),
    MetaKeySpec("is_native", AXIS_SYMBOL_META,
                "True on native-bridge declarations "
                "(e.g. JNI method, N-API binding). Set by FFI "
                "analyzers."),
    MetaKeySpec("is_class_based_view", AXIS_SYMBOL_META,
                "Django class-based-view marker on class Symbols. "
                "Set by the Django framework-dispatch linker."),
    # ------------------------------------------------------------------
    # Symbol.meta — language-specific annotations.
    # ------------------------------------------------------------------
    MetaKeySpec("annotations", AXIS_SYMBOL_META,
                "Java / Kotlin / Python decorator-equivalent "
                "annotations on a Symbol. Distinct from "
                "``decorators`` (Python-style) where the syntax "
                "carries different semantics."),
    MetaKeySpec("parameters", AXIS_SYMBOL_META,
                "Structured parameter list on function / method "
                "Symbols (name + type + default). Used by "
                "signature-shape matching."),
    MetaKeySpec("params", AXIS_SYMBOL_META,
                "Short-form parameter-name list on function / "
                "method Symbols. Lighter-weight alternative to "
                "``parameters`` for analyzers that don't extract "
                "types."),
    MetaKeySpec("return_type", AXIS_SYMBOL_META,
                "Declared return type on function / method Symbols. "
                "Populated by typed-language analyzers."),
    MetaKeySpec("inferred_return_type", AXIS_SYMBOL_META,
                "Inferred return type for Symbols whose declared "
                "type is missing. Set by the limited type-inference "
                "passes (Python ``typing.get_type_hints``, etc.)."),
    # ADR-0032 Phase 2 PR2: ``qualified_name`` retired from the meta axis
    # in favour of the typed ``Symbol.qualified_name`` field. See
    # :mod:`hypergumbo_core.qualified_name_axis`.
    MetaKeySpec("display_name", AXIS_SYMBOL_META,
                "Human-readable display name overriding the "
                "default. Set by analyzers where the source name "
                "is mechanical (e.g. ``__call__`` displayed as the "
                "containing class)."),
    MetaKeySpec("scope", AXIS_SYMBOL_META,
                "Lexical scope qualifier (e.g. 'workgroup' in "
                "WGSL, 'thread_local' in C++). Distinct from "
                "``visibility`` (which is access control) and "
                "``static`` (which is binding-time)."),
    MetaKeySpec("import_path", AXIS_SYMBOL_META,
                "Source-language import path on Symbols whose "
                "primary identifier doesn't match (e.g. "
                "``from foo.bar import baz`` makes ``baz``'s "
                "import_path ``foo.bar.baz``)."),
    MetaKeySpec("symbol_roles", AXIS_SYMBOL_META,
                "SCIP-style symbol-role bitset for Symbols sourced "
                "from SCIP indexes (definition / reference / "
                "import / etc.)."),
    MetaKeySpec("scip_kind", AXIS_SYMBOL_META,
                "SCIP-native kind label on Symbols sourced from "
                "SCIP indexes. Kept alongside the canonical "
                "``Symbol.kind`` for index round-trip fidelity."),
    MetaKeySpec("documentation", AXIS_SYMBOL_META,
                "Docstring / leading-comment text on documented "
                "Symbols. Extracted by analyzers that parse "
                "docstrings."),
    MetaKeySpec("tags", AXIS_SYMBOL_META,
                "Free-form tag list for analyzer-specific "
                "annotations not (yet) elevated to a named meta "
                "key. Use sparingly — sustained use of a tag is "
                "an ADR-0024 promotion signal."),
    # ------------------------------------------------------------------
    # Entrypoint.meta — provenance fields mirroring Edge's provenance
    # shape (WI-rukam). The Entrypoint record keeps a 4-field typed core
    # (symbol_id / kind / confidence / label); per the WI-rukam ruling the
    # id + producer + inference-pathway provenance nests under ``meta``
    # (rather than three new typed fields) so a consumer can interpret an
    # entrypoint's confidence the same way it interprets an edge's
    # (Edge.id / Edge.origin / Edge.evidence_type). Value vocabularies are
    # the single-source frozensets ``ENTRYPOINT_SOURCES`` /
    # ``ENTRYPOINT_EVIDENCE_TYPES`` in ``entrypoints.py``; ``Entrypoint.create``
    # validates against them.
    # ------------------------------------------------------------------
    MetaKeySpec("id", AXIS_ENTRYPOINT_META,
                "Stable content-hash identity of an Entrypoint record: "
                "``entrypoint:sha256:<16hex>`` of (kind, symbol_id, label). "
                "Auto-stamped in ``Entrypoint.__post_init__`` so every "
                "record — including test-constructed ones — carries it. The "
                "Entrypoint analog of ``Edge.id`` (WI-rukam); confidence is "
                "NOT part of the identity (it is a ranking value, not a "
                "record key)."),
    MetaKeySpec("source", AXIS_ENTRYPOINT_META,
                "Producer pass that emitted the Entrypoint — one of "
                "'concept_detector' (``_detect_from_concepts``, YAML-concept "
                "matches), 'connectivity_fallback' (``_connectivity_fallback``, "
                "the no-patterns-matched top-N-by-out-degree fallback), or "
                "'script_module_detector' (``_detect_script_modules``, the "
                "edge-set-dependent TS/JS standalone-script rule). The "
                "Entrypoint analog of ``Edge.origin`` (WI-rukam); value space "
                "is ``entrypoints.ENTRYPOINT_SOURCES``."),
    MetaKeySpec("evidence_type", AXIS_ENTRYPOINT_META,
                "Inference pathway by which the entrypoint was detected — one "
                "of 'manifest_declared' (package-manifest bin/scripts entry), "
                "'framework_pattern' (decorator/base-class/usage/reflective-"
                "dispatch YAML match), 'structural' (main-guard, shebang, "
                "filename, or import-graph shape), 'language_convention' "
                "(main()/test functions/library exports), 'naming_heuristic' "
                "(*Controller/*Handler/cmd_* name patterns), or "
                "'connectivity_heuristic' (the out-degree fallback). The "
                "Entrypoint analog of ``Edge.evidence_type`` (WI-rukam) — a "
                "SEPARATE vocabulary from the edge inference-pathway registry "
                "(``evidence_types.py``): entrypoint detection methods do not "
                "overlap edge inference pathways. Value space is "
                "``entrypoints.ENTRYPOINT_EVIDENCE_TYPES``; aligns 1:1 with "
                "the spec §8/§9 confidence tiers."),
)


def all_meta_key_names() -> frozenset[str]:
    """Return every canonical meta-key name."""
    return frozenset(spec.name for spec in META_KEYS)


def meta_keys_on_axis(axis: str) -> tuple[MetaKeySpec, ...]:
    """Return all meta-key specs whose axis equals *axis*.

    Mirrors :func:`hypergumbo_core.symbol_kinds.symbol_kinds_on_axis`
    and :func:`hypergumbo_core.evidence_types.evidence_types_on_axis`.
    Returns an empty tuple for unknown axis names (no exception) so
    callers can use it as a filter without preflight validation.
    """
    return tuple(spec for spec in META_KEYS if spec.axis == axis)


def find_meta_key(name: str) -> MetaKeySpec | None:
    """Look up a meta-key spec by name; return ``None`` if not registered."""
    for spec in META_KEYS:
        if spec.name == name:
            return spec
    return None


def access_mode_applicable_edge_types() -> frozenset[str]:
    """ADR-0038 ruling 2: edge types where ``access_mode`` APPLIES.

    A ``None`` value on one of these edges means missing data (fix the
    emitter), not "not applicable". The applicable half of INV-tibob's
    17-type census.
    """
    return _ACCESS_MODE_APPLICABLE_EDGE_TYPES


def access_mode_na_edge_types() -> frozenset[str]:
    """ADR-0038 ruling 2: edge types where ``access_mode`` is Declared-N/A.

    A ``None`` value on one of these edges means the question does not arise
    (a constructor call ``instantiates`` is not an access; the structural
    edge types carry no access semantics). The dataflow annotate passes skip
    stamping these — see :func:`is_access_mode_not_applicable`.
    """
    return _ACCESS_MODE_NA_EDGE_TYPES


def is_access_mode_not_applicable(edge_type: str) -> bool:
    """ADR-0038 ruling 2: is ``access_mode`` Declared-N/A for *edge_type*?

    The dataflow annotate passes call this to skip stamping ``access_mode`` on
    N/A edge types. Edge types OUTSIDE the 17-type census (the ~37 uncensused
    canonical types, incl. the ``crypto_flow`` / ``data_flows_to`` direction
    family) are UNCLASSIFIED and return ``False`` — their stamping behavior is
    untouched by this pass, pending the polyglot-census follow-up.
    """
    return edge_type in _ACCESS_MODE_NA_EDGE_TYPES
