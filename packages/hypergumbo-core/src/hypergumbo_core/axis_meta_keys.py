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

VALID_AXES: Final[frozenset[str]] = frozenset({
    AXIS_SYMBOL_META,
    AXIS_EDGE_META,
})


@dataclass(frozen=True)
class MetaKeySpec:
    """A single ``Symbol.meta`` / ``Edge.meta`` key and its axis."""

    name: str
    axis: str
    description: str


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
                "Dataflow access mode at the edge source "
                "(e.g. 'read', 'write', 'read_write'). Set by "
                "``dataflow.apply_access_modes``."),
    MetaKeySpec("dest_access_mode", AXIS_EDGE_META,
                "Dataflow access mode at the edge destination. "
                "Sibling of ``access_mode``; populated when the "
                "destination's mode is distinct from the source's."),
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
    MetaKeySpec("visibility", AXIS_SYMBOL_META,
                "Source-language visibility modifier "
                "(e.g. 'public', 'private', 'protected', "
                "'package-private'). Set by analyzers whose source "
                "language has explicit visibility keywords."),
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
