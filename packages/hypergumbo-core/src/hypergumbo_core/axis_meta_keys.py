# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical registry of ``Symbol.meta`` and ``Edge.meta`` key names.

Per WI-runod Wave 9 and the four-part axis-declaration template in
[ADR-0024](../../docs/adr/0024-axis-declaration-template.md), every
*meta key* used by producers / consumers across the codebase is
declared once here with its axis (``symbol_meta``, ``edge_meta``, or
``entrypoint_meta``) and a short description of what the key encodes.

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

from collections.abc import Callable
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


# ----------------------------------------------------------------------
# INV-hazov: write discipline — how many writers may reach this key, and
# what happens when a second one does.
#
# The axis above answers "which keys may exist" and "what vocabulary do
# their values draw from". It is blind to ARITY by construction, which is
# why it could not catch any of the seven last-writer-wins instances: the
# offending write in INV-virat was a correctly-spelled assignment of a
# registered key. This second declaration is the arity answer.
# ----------------------------------------------------------------------

#: Exactly one writer can reach this key on any given record. A second
#: writer with a DIFFERENT value is a violation of the declaration, so the
#: chokepoint raises rather than silently picking a winner. Writers that
#: partition (per-language analyzers each stamping their own symbols) are
#: single-writer per record even though many modules assign the key — say
#: so in ``discipline_note``.
DISCIPLINE_SINGLE_WRITER: Final[str] = "single_writer"

#: Several writers contribute independently true facts. The chokepoint
#: folds by key and keeps the UNION. Under-reporting is the failure that
#: matters here; over-reporting is merely noisy.
DISCIPLINE_MERGE_UNION: Final[str] = "merge_union"

#: A producer stamps a fact a later consumer-time pass must not displace
#: (INV-virat: a catalogue row erasing bash's ``command_launch`` opacity
#: stamp). First writer wins; the chokepoint refuses the overwrite.
DISCIPLINE_PRODUCER_PRIMARY: Final[str] = "producer_primary"

#: No claim has been made. This is the DEFAULT on purpose: declaring
#: ``single_writer`` wrongly buys a false assurance, which is worse than
#: no assurance (the INV-faput lesson — a mislabelled row buys a
#: ``confirmed``). ``unaudited`` keys are counted as visible debt by
#: ``check-meta-write-discipline`` rather than hidden behind a permissive
#: default, and a key that a second writer can DEMONSTRABLY reach is
#: refused this state by the static check.
DISCIPLINE_UNAUDITED: Final[str] = "unaudited"

#: A later writer DELIBERATELY replaces an earlier one because its value is
#: strictly better, and the earlier writer's safety-relevant fact survives
#: on a sibling key. Behaviourally identical to an unguarded assignment —
#: the difference is epistemic, and that is the whole point: somebody looked
#: and concluded the overwrite is correct. Mirrors the
#: ``# axis: free-text — <justification>`` precedent, and carries the same
#: mandatory-justification friction so it cannot become a drive-by escape
#: hatch from a real audit.
DISCIPLINE_REFINES: Final[str] = "refines"

WRITE_DISCIPLINES: Final[frozenset[str]] = frozenset({
    DISCIPLINE_SINGLE_WRITER,
    DISCIPLINE_MERGE_UNION,
    DISCIPLINE_PRODUCER_PRIMARY,
    DISCIPLINE_REFINES,
    DISCIPLINE_UNAUDITED,
})

#: The declarations that assert something and therefore owe a justification.
DISCIPLINES_REQUIRING_A_NOTE: Final[frozenset[str]] = frozenset({
    DISCIPLINE_MERGE_UNION,
    DISCIPLINE_PRODUCER_PRIMARY,
    DISCIPLINE_REFINES,
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
    # INV-hazov: the arity answer. Defaults to ``unaudited`` so an omitted
    # declaration never reads as "only one writer" — see the constants above.
    write_discipline: str = DISCIPLINE_UNAUDITED
    # Mandatory for ``merge_union`` / ``producer_primary``: name the writers
    # and say which one is authoritative. A discipline nobody can audit is a
    # pinky-swear with a field name.
    discipline_note: str = ""
    # INV-vukiv: does this key's value VARY BY CALL SITE? ``deduplicate_edges``
    # keeps one edge per ``(src, dst, edge_type)``; a per-site key on the
    # survivor would otherwise report one arbitrary site's value as the whole
    # relationship's. Declared keys collapse under the ``call_arg_shape`` rule
    # generalized: the singular key survives ONLY if every collapsed site
    # agreed, and the distinct values move to ``<name>_values`` when they did
    # not. ``write_discipline`` answers a DIFFERENT question — how many
    # PRODUCERS write the key — and the two are orthogonal: ``io_mode`` has one
    # producer and many sites.
    per_call_site: bool = False


# WI-toruz / INV-lalad: the CALL FAMILY — the edge types on which a call
# construct can appear, i.e. the edge types that carry an invocation.
# ``instantiates`` is a member because a constructor call IS a call: data
# passed to it crosses into a callable exactly as through ``calls``. Declared
# ONCE here and read by both ``call_construct``'s ``applicable_edge_types``
# and :func:`call_family_edge_types`, so consumers stop keeping private copies
# — the disagreeing-copies defect INV-nosoz names for the inheritance family.
#
# NOT a general-purpose "taint cares about this" set: consumers legitimately
# add their own extras (taint carries ``module_attr_ref`` per WI-lokuv and
# ``dispatches_to`` per INV-zuhig, neither of which is a call construct).
# Union with those; never replace them.
_CALL_FAMILY_EDGE_TYPES: Final[frozenset[str]] = frozenset({
    "calls",
    "instantiates",
})


# ADR-0038 ruling 2: the ``access_mode`` per-edge-type applicability matrix.
# APPLICABLE — a ``None`` value counts as missing data (fix the emitter).
# DECLARED-N/A — a ``None`` value means the question does not arise (the
# dataflow annotate passes skip these; a constructor call ``instantiates`` is
# not an access).
#
# CENSUS COMPLETE (WI-pusuv, 2026-07-21). The matrix originally covered only
# INV-tibob's 17-type census; the ADR-0023 endpoint_shape fold-tail (WI-pumav)
# AND the resolver/OpenAPI/RPC pending_classification fold (WI-sumik) drained
# both non-relationship axes to empty, so ``edge_types.all_edge_type_names()``
# is now a 25-wide single ``relationship`` axis and EVERY value is classified
# here (applicable XOR N/A) — enforced by
# ``test_access_mode_matrix_total_census_complete``. Per the 2026-07-08 owner
# ruling the census resolved N/A-now for the residual (no new applicable type
# is silently 0%-populated, honoring the WI-nibis close-check discipline):
#   * ``subprocess_calls`` — a subprocess invocation IS a call whose access the
#     question COULD arise for, but no emitter stamps access_mode on it today,
#     so it is N/A-now. RE-EVAL TRIGGER: reclassify to APPLICABLE if/when the
#     subprocess linker stamps access_mode on these edges (the ADR-0038
#     "per-analyzer as dataflow support matures" precedent).
#   * ``data_flows_to`` — the ADR-0015 dataflow relationship carries value
#     PROPAGATION + DIRECTION (the ``data_direction`` key, ADR-0038 ruling 3),
#     not state access; the access question does not arise → N/A. (``crypto_flow``
#     folded into it in Batch 7 and is no longer a registry type.)
#   * ``constrains`` / ``defines_target`` / ``includes`` / ``links`` /
#     ``module_exports`` / ``sources`` / ``wraps`` — structural / dependency /
#     export / inclusion relationships, not accesses → N/A (same shape as the
#     original 12 structural N/A types).
# Keeping these sets axis-pure still matters for the ``check-edge-type-drift``
# strict linter — they are named ``*_EDGE_TYPES`` on purpose so the linter DOES
# watch them, so every member must be a relationship (or pending) axis value.
_ACCESS_MODE_APPLICABLE_EDGE_TYPES: Final[frozenset[str]] = frozenset({
    "calls", "references", "module_attr_ref", "event_publishes",
})
_ACCESS_MODE_NA_EDGE_TYPES: Final[frozenset[str]] = frozenset({
    # INV-tibob's original 17-type census (ADR-0038 ruling 2):
    "contains", "decorated_by", "depends_on", "depends_on_manifest",
    "dispatches_to", "extends", "implements", "imports", "inherits",
    "overrides", "uses", "instantiates",
    # WI-pusuv census completion (2026-07-21): the residual relationship tail,
    # all N/A-now (subprocess_calls carries a re-eval trigger; see above):
    "constrains", "data_flows_to", "defines_target", "includes", "links",
    "module_exports", "sources", "subprocess_calls", "wraps",
})


_BASE_META_KEYS: Final[tuple[MetaKeySpec, ...]] = (
    # ------------------------------------------------------------------
    # Edge.meta — Wave 5 framework-dispatch fold residues (ADR-0028).
    # Per audit-findings 0014, the framework-specific evidence types
    # fold to canonical inference + one of these meta keys.
    # ------------------------------------------------------------------
    MetaKeySpec("framework_dispatch", AXIS_EDGE_META,
                "The framework-or-generic dispatch convention that "
                "produced this edge — ONE coherent axis ('which dispatch "
                "convention recovered this edge'), per ADR-0028 Cluster 28C "
                "('framework-dispatch convention') and spec §9. Values are "
                "convention identifiers: framework-specific compounds "
                "('django_orm', 'kafka_streams', 'vue_component', "
                "'tauri_invoke') AND framework-agnostic dispatch mechanisms "
                "('registry_dispatch', 'middleware_chain', 'npm_package'). "
                "NOT a bare-framework-name axis — that reading misreads "
                "audit-findings 0014's '<framework_name>' shorthand (INV-junid). "
                "Distinct from 'detection_pattern' (pattern-shape match "
                "heuristics — HOW an edge was string/name/URL-matched, not "
                "which convention dispatches it). Pure provenance: zero "
                "production consumers read this key; promote to a typed "
                "Edge.framework field ONLY if a consumer materializes "
                "(ADR-0028 OQ2, declined 2026-07-05; ADR-0024 rule 3 threshold "
                "= ≥3 distinct values OR ≥2 producers). Fold residue per "
                "audit-findings 0014 / WI-kagik Wave 5."),
    MetaKeySpec("detection_pattern", AXIS_EDGE_META,
                "Pattern-shape inference heuristic that produced "
                "this edge (e.g. 'abi_name_match', 'jni_naming', "
                "'implicit_convention') — names HOW the edge was "
                "string/name/URL/regex-matched. Distinct from "
                "'framework_dispatch' (which dispatch convention produced "
                "the edge, not how it was matched). Fold residue per "
                "audit-findings 0014 §'detection_pattern partition'."),
    MetaKeySpec("call_construct", AXIS_EDGE_META,
                "Source-language call construct collapsed under "
                "``ast_call`` apex (e.g. 'method', 'function', "
                "'pipe', 'application'). Fold residue per "
                "audit-findings 0012 / WI-nibis Wave 4. Scoped to the CALL "
                "FAMILY -- ``calls`` and ``instantiates`` -- declared on "
                "``applicable_edge_types`` below rather than asserted in this "
                "sentence, because a scope that lives only in prose cannot be "
                "checked and this one drifted (WI-toruz). ``instantiates`` is "
                "in scope deliberately: ``edge_type`` is NOT a function of the "
                "construct, since ruby emits ``calls`` for the same source "
                "construct that dart/csharp emit ``instantiates`` for "
                "(INV-kahig), so 'constructor' is the only cross-language "
                "invariant for object creation and is NOT redundant with "
                "``edge_type``.",
                applicable_edge_types=_CALL_FAMILY_EDGE_TYPES),
    MetaKeySpec("callee_name", AXIS_EDGE_META,
                "The callee's name at FULL FIDELITY, as the producer saw it "
                "at the call site -- the lossless home ADR-0036 Ruling 1 "
                "designates. The id's name slot is deliberately LOSSY (names "
                "containing ':' fold to '.') because the id is a "
                "location-addressed key, not a fidelity surface, and Ruling 1 "
                "says in as many words that consumers needing the exact name "
                "MUST read it from elsewhere and never re-derive it from the "
                "id. Until this key existed there was no elsewhere for an "
                "unresolved external: an Objective-C selector "
                "(writeToFile:atomically:) ENDS in a colon, so the id's "
                "second-to-last token was the EMPTY STRING, the boundary node "
                "synthesised from that id had name='', and the selector "
                "existed nowhere in the output (INV-divuf / WI-nakut). "
                "Stamped on EVERY edge make_unresolved_edge builds, including "
                "the cell where WI-huzuv correctly withholds dst_ref because "
                "the module is unknown -- carrying the name in dst_ref "
                "sometimes and here otherwise would give one fact two homes.",
                applicable_edge_types=_CALL_FAMILY_EDGE_TYPES,
                write_discipline=DISCIPLINE_SINGLE_WRITER,
                discipline_note=(
                    "Sole writer: analyze.base.make_unresolved_edge, the "
                    "factory all 57 unresolved-external call sites route "
                    "through. Nothing refines or displaces it -- it records "
                    "what the producer saw, so a later pass 'improving' it "
                    "would be recording a different fact under one name.")),
    MetaKeySpec("call_arg_shape", AXIS_EDGE_META,
                "What the arguments at this call site CAN carry. One value "
                "today: 'literal_only' -- every positional and keyword "
                "argument is a literal constant, or there are no arguments at "
                "all. INV-fubag, and it exists to be a PROOF rather than a "
                "heuristic: taint models a flow as the tainted value being an "
                "argument to the sink call or its receiver, so a call passing "
                "only literals has nothing that could be the tainted value. "
                "Measured cost of not having it (docs/measurements/0003): 24 "
                "of 34 adjudicated false positives were sink calls with no "
                "arguments at all -- tempfile.TemporaryDirectory(), "
                "TemporaryFile(), NamedTemporaryFile(delete=True). "
                "ABSENCE IS THE CONSERVATIVE READING and is load-bearing: "
                "producers stamp this ONLY when they can prove the argument "
                "list is all-literal, so every un-stamped call -- including "
                "every edge in every behavior map written before this key "
                "existed -- keeps flowing. A default that silenced findings "
                "would make this a false-negative generator on a security "
                "analysis. Sparse by convention for the same reason: stamping "
                "'dynamic' on every call edge would say nothing absence does "
                "not already say, at the cost of a key on every edge of every "
                "map. Scoped to the CALL FAMILY -- the question does not "
                "arise for an edge that is not an invocation.",
                applicable_edge_types=_CALL_FAMILY_EDGE_TYPES,
                per_call_site=True),
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
                "Dispatch/call mechanism on a ``dispatches_to`` or ``calls`` "
                "edge — names HOW the dispatch/call is wired when the canonical "
                "``edge_type`` alone is too coarse. Current value space: "
                "'otp_call' / 'otp_cast' (Elixir/Erlang GenServer synchronous "
                "call vs asynchronous cast, folded from the former "
                "otp_call/otp_cast edge_types per WI-rorul); 'callback' "
                "(Elixir/Erlang behaviour + Rails callbacks, folded from "
                "``invokes_callback`` per audit-findings 0017); 'kernel_launch' "
                "(CUDA kernel launch, folded from ``kernel_launch``); 'template' "
                "(Vue template→method call, folded from ``template_calls``). "
                "Distinct from ``framework_dispatch`` (framework name, e.g. "
                "'otp_genserver') and ``call_construct`` (call-edge syntactic "
                "shape)."),
    MetaKeySpec("ref_construct", AXIS_EDGE_META,
                "Source-language construct on a reference-FAMILY (non-call) "
                "edge (``references`` / ``includes`` / ``extends`` / "
                "``depends_on`` / ``contains`` / ``data_flows_to``) where the "
                "canonical ``edge_type`` alone is too coarse. Value space: "
                "'jsx' (JSX render), 'script_src' (HTML ``<script src=...>``), "
                "'websocket_endpoint' (WebSocket endpoint connectivity), "
                "'event_emit' (Solidity event emission), 'dispatch_table' "
                "(C/C++ dispatch-table reference); and — from the ADR-0023 "
                "endpoint_shape fold-tail (audit-findings 0017, WI-pumav) — "
                "'markdown_link', 'rdf_vocabulary', 'association' (Ruby "
                "ActiveRecord), 'build_tag_alternative' (Go build tags), "
                "'view_render' (controller→template), 'template' (Twig/Blade "
                "extends/includes), 'puppet_class', 'sass_mixin', "
                "'dockerfile_stage' (Docker FROM..AS), 'puppet_require', "
                "'puppet_notify', 'crypto' (crypto write→read on "
                "``data_flows_to``); and — from the resolver/OpenAPI/RPC "
                "family fold (audit-findings 0016, WI-sumik) — "
                "'graphql_resolver_type' (a GraphQL ``@Resolver(() => Type)`` "
                "declaration-time association) and 'openapi_operation' (an "
                "OpenAPI spec operation referencing its realizing route "
                "handler, direction-preserving); and -- per WI-diruk -- "
                "'method_group' (a C# method group, ``Action h = Handle;`` or "
                "``items.ForEach(Process)``, which REFERENCES a method without "
                "invoking it; it was emitted on ``call_construct`` until the "
                "concept audit found it on ``references`` edges, where a call "
                "construct cannot belong). Renamed from ``construct`` "
                "per INV-lajov "
                "to disambiguate from the sibling ``call_construct`` (which "
                "names the call SHAPE on the CALL family -- ``calls`` and "
                "``instantiates``, see that key's ``applicable_edge_types``): "
                "distinct vocabularies, distinct edge families. INV-lajov "
                "originally wrote 'zero overlap' here on the strength of a "
                "corpus measurement in which every observed ``call_construct`` "
                "sat on a ``calls`` edge; that described the sample rather "
                "than the field, and was already false by five "
                "``constructor``-on-``instantiates`` sites (WI-toruz)."),
    MetaKeySpec("refresh", AXIS_EDGE_META,
                "Boolean flag on a ``depends_on`` edge marking an ordering "
                "dependency that ALSO triggers a refresh-on-change of the dst "
                "(Puppet ``notify``, folded from the former ``notifies_resource`` "
                "edge_type per audit-findings 0017). Present-and-true only on "
                "the refresh subset; a plain ordering ``depends_on`` (e.g. "
                "Puppet ``require``, ``ref_construct='puppet_require'``) omits "
                "it. Makes the require-vs-notify distinction queryable without "
                "a distinct edge type; no consumer branches on it yet."),
    MetaKeySpec("receiver", AXIS_EDGE_META,
                "Call-site receiver classification: a per-language "
                "fold-residue label (audit-findings 0012) emitted only by "
                "analyzers whose call syntax carries receiver flavor "
                "(Ruby / Go / C# / Rust / C++), hence ABSENT on corpora that "
                "lack those languages (e.g. a pure-Python tree). Complete "
                "current producer vocabulary (no consumer branches on it yet): "
                "'bare' (implicit / self receiver), 'external' / "
                "'constant_external' / 'stdlib' (receiver resolves outside the "
                "module), 'typed_field' / 'typed_var' (receiver has a known "
                "type), 'field_chain' (chained field-access receiver), "
                "'generic' (receiver present but unclassified). NOTE: this key "
                "mixes a resolution CLASS (bare / external / typed / generic) "
                "with an expression SHAPE (field_chain); kept as one key "
                "because no consumer distinguishes them — re-evaluate (split "
                "into receiver_class + receiver_shape) if one ever needs to "
                "query them independently. Disambiguates ``ast_call`` + "
                "``call_construct=method`` edges."),
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
                "``resolution_quality='recovery'``. INV-tadup added "
                "'chained_return_type' (Go: the receiver's type came from a "
                "chained call's return type) — it named a resolution MECHANISM "
                "and was being smuggled through ``call_construct``, which is "
                "the syntactic-construct axis."),
    # ------------------------------------------------------------------
    # Edge.meta — protocol / bridge / dispatch vocabularies (predates
    # the axis-registry pattern; PROTOCOL_KINDS and BRIDGE_KINDS in
    # edge_types.py enumerate the value vocabularies).
    # ------------------------------------------------------------------
    MetaKeySpec("protocol", AXIS_EDGE_META,
                "Wire-level protocol on ``calls`` edges that absorb "
                "the IPC family (e.g. 'abi', 'ipc', 'ipc_event'), and on "
                "``implements`` edges for protocol-contract implementations "
                "folded from the resolver/OpenAPI/RPC family (audit-findings "
                "0016, WI-sumik): a GraphQL resolver satisfying a schema field "
                "carries 'graphql'; a Go method satisfying a proto RPC carries "
                "'grpc'. Value vocabulary tracked at "
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
                "``io_boundary.compute_boundary_map``.",
                write_discipline=DISCIPLINE_REFINES,
                discipline_note=(
                    "TWO writers. bash.py stamps a bare command name at "
                    "analysis time; io_boundary.tag_io_boundaries later "
                    "replaces it with the catalogue's qualified name, which "
                    "is strictly more precise. The overwrite is INTENDED and "
                    "loses nothing safety-relevant: the producer's OPACITY "
                    "fact does not live here, it lives on io_boundary "
                    "(producer_primary) and io_boundaries (merge_union), "
                    "both of which retain command_launch. Re-evaluate if any "
                    "consumer ever branches on io_primitive to decide "
                    "whether a crossing is opaque."
                )),
    # ------------------------------------------------------------------
    # Edge.meta — bash's synthesized redirection / expansion edges.
    # Registered as part of INV-vukiv: all three vary PER CALL SITE, and two
    # of them were emitted by bash.py while unregistered, so nothing could
    # have known to union them on collapse.
    # ------------------------------------------------------------------
    MetaKeySpec("io_target_kind", AXIS_EDGE_META,
                "What KIND of thing a call site's I/O target is, where the "
                "catalogue row cannot tell: 'host_path' (a place in a "
                "filesystem), 'null_device' (a kernel sink that discards), "
                "'std_stream' (/dev/stdout, /dev/stderr, /dev/fd/N — a real "
                "crossing, but a logging one rather than a filesystem one), "
                "'in_memory' (WI-lipis: the bytes never left the process — a "
                "`bufio.NewScanner(strings.NewReader(s))` wraps a buffer, not "
                "a channel), or "
                "'unresolved' (a variable target; a real write to a place the "
                "analyzer cannot name). INV-nular: `redirect.>` is ONE "
                "catalogue row whose boundary depends on the target at the "
                "call site, exactly as `builtins.open`'s depends on the mode, "
                "so the discriminator is stamped by the analyzer and read by "
                "`io_boundary.classify_call`. Measured: without it, "
                "`echo \"$API_KEY\" > /dev/null` returned `violated` against "
                "a fs_write must-not-exist claim. PER CALL SITE for the same "
                "reason `redirect_target` is.",
                per_call_site=True,
                write_discipline=DISCIPLINE_SINGLE_WRITER,
                discipline_note=(
                    "TWO writers over DISJOINT edges, which is what this "
                    "discipline permits and io_mode's two analyzer families "
                    "already do. bash.py's _redirect_edge stamps it on "
                    "redirect edges, in the same dict literal as "
                    "redirect_target. go.py's _go_wrapped_handle_kind stamps "
                    "it on handle-wrapper call edges (WI-lipis) — a "
                    "`bufio.NewScanner` edge is never a bash redirect, so no "
                    "edge can receive both. The note previously read 'sole "
                    "writer: bash.py' and anticipated exactly this second "
                    "writer; it went stale when one arrived."
                )),
    MetaKeySpec("redirect_target", AXIS_EDGE_META,
                "The path (or `<unresolved>`) a shell redirection writes to "
                "or reads from — the `/etc/cron.d/pwned` in "
                "`echo x > /etc/cron.d/pwned`. Written by bash.py's "
                "file_redirect branch. PER CALL SITE, and the measurement "
                "that filed INV-vukiv: one function redirecting to /dev/null "
                "on one line and to /etc/cron.d/pwned on the next collapsed "
                "to a single edge reading '/dev/null', so the cron-dropper "
                "was reported as a write to the bit bucket.",
                per_call_site=True,
                write_discipline=DISCIPLINE_SINGLE_WRITER,
                discipline_note=(
                    "Sole writer: bash.py's _redirect_edge. No later pass "
                    "refines it — the DDG is where a `> \"$OUT\"` target "
                    "would be resolved, and that answer would be a different "
                    "fact under a different name."
                )),
    MetaKeySpec("redirect_target_resolved", AXIS_EDGE_META,
                "Whether ``redirect_target`` names a literal path rather than "
                "an unexpanded variable. Companion boolean so a consumer can "
                "tell 'wrote /tmp/x' from 'wrote somewhere I cannot name' "
                "without re-parsing the target. PER CALL SITE for the same "
                "reason ``redirect_target`` is.",
                per_call_site=True,
                write_discipline=DISCIPLINE_SINGLE_WRITER,
                discipline_note=(
                    "Sole writer: bash.py's _redirect_edge, stamped in the "
                    "same dict literal as ``redirect_target``."
                )),
    MetaKeySpec("redirect_origin_names", AXIS_EDGE_META,
                "The externally-derived variable names that can reach what "
                "the SHELL ITSELF contributes at a redirect — its target "
                "operand, a heredoc body the shell expands, and every "
                "producing stage's arguments. WI-zovuz: bash carries no "
                "dataflow, so a redirect-sink taint finding rested on "
                "reachability alone ('this file reads the environment "
                "somewhere AND reaches a function that writes somewhere'). "
                "Measured over 15 cohort repos, 28 of the 186 environment "
                "names read in the 69 files that also carry a write redirect "
                "can reach one; 48 of those files have none. An EMPTY list is "
                "a PROOF that no value this program holds crossed here, and "
                "`_sink_call_can_carry_taint` consumes it as such — an ABSENT "
                "key keeps the finding, because absence is the state of every "
                "map written before this key existed. PER CALL SITE for the "
                "same reason `redirect_target` is (INV-vukiv): two redirects "
                "in one function reach different names.",
                per_call_site=True,
                write_discipline=DISCIPLINE_SINGLE_WRITER,
                discipline_note=(
                    "Sole writer: bash.py's _redirect_edge, stamped in the "
                    "same dict literal as ``redirect_target`` from the "
                    "whole-file closure ``_redirect_origin_names`` computes "
                    "once per file. No later pass refines it."
                )),
    MetaKeySpec("env_var", AXIS_EDGE_META,
                "The variable name behind a synthesized environment read — "
                "the `API_KEY` in `$API_KEY`. Carried for the READER rather "
                "than for matching: bash's env_read catalogue is ONE row on "
                "the os.environ precedent, so the name never participates in "
                "the lookup. PER CALL SITE (INV-vukiv): `$HOME` then "
                "`$API_KEY` in one function collapsed to `env_var='HOME'`, "
                "which named the harmless read and dropped the secret one.",
                per_call_site=True,
                write_discipline=DISCIPLINE_SINGLE_WRITER,
                discipline_note=(
                    "Sole writer: bash.py's simple_expansion/expansion branch."
                )),
    MetaKeySpec("io_boundary", AXIS_EDGE_META,
                "Boundary classification on edges that cross an IO "
                "primitive (e.g. 'net_send', 'fs_read', "
                "'process_spawn'). Two-source field per the WI-fakuv / "
                "WI-puvun io-boundary REFRAME: the full value-space is "
                "CONSUMER-TIME-derived by ``io_boundary.compute_boundary_map`` "
                "from the ``io_primitives`` YAML catalogs and is NOT persisted "
                "(it is stamped in memory only by the ``io-boundaries`` / "
                "``verify-claims`` / ``slice --io-boundary`` paths). What "
                "survives into a persisted behavior map is only the analyzer "
                "producer-hints — currently just bash's ``command_launch`` "
                "(``bash.py``; bash has no ``io_primitives`` catalog so the "
                "analyzer stamps it directly), which is why the persisted "
                "field reads as ``command_launch``-only. Consumers that need "
                "the full classification must run ``compute_boundary_map`` "
                "rather than read the persisted meta key.",
                write_discipline=DISCIPLINE_PRODUCER_PRIMARY,
                discipline_note=(
                    "INV-virat verbatim. bash.py stamps command_launch "
                    "because a launched program's I/O is UNKNOWABLE to "
                    "static analysis; io_boundary.tag_io_boundaries then "
                    "matched a catalogue row and overwrote it, downgrading "
                    "an opaque crossing to an examined one and buying a "
                    "false confirm. The producer wins: opacity is a fact "
                    "about what the analyzer CANNOT see, so no amount of "
                    "catalogue knowledge refutes it. The INV-larol gate "
                    "survived this defect only because it reads raw_edges "
                    "while the tagger mutated an accidental shallow copy — "
                    "a safety property resting on an accidental copy is not "
                    "a safety property, which is what this declaration "
                    "replaces."
                )),
    MetaKeySpec("io_boundaries", AXIS_EDGE_META,
                "The FULL set of boundaries a single call crosses, for "
                "primitives that cross more than one simultaneously "
                "(``simultaneous: true`` in the io_primitives YAML — e.g. "
                "scala's ``Process.run``, which spawns AND pipes). The "
                "scalar ``io_boundary`` names one of them; this names all "
                "of them. Written by ``io_boundary.tag_io_boundaries``.",
                write_discipline=DISCIPLINE_MERGE_UNION,
                discipline_note=(
                    "INV-zumin. A one-slot scalar let ROW ORDER in the YAML "
                    "decide which of several simultaneously-true boundaries "
                    "survived, and INV-virat then showed the same slot "
                    "erasing a producer stamp. Every writer contributes an "
                    "independently TRUE crossing, so the union is the only "
                    "sound fold. Under-reporting crossings is the failure "
                    "that matters; over-reporting is merely noisy."
                )),
    MetaKeySpec("io_mode", AXIS_EDGE_META,
                "Read/write disambiguation for a mode-parameterised IO "
                "primitive — the ``'w'`` in ``fopen(path, 'w')`` — resolving "
                "one catalogue row to a directional boundary. PER CALL SITE "
                "(INV-vukiv): one function may open the same path 'r' at one "
                "line and 'w' at another, and those two calls collapse to ONE "
                "edge. Measured before the flag existed: adding a preceding "
                "open(p,'r') deleted a real truncating open(p,'w') from the "
                "boundary map outright, because the survivor carried the "
                "first site's mode and the gate eliminated the fs_write row.",
                per_call_site=True,
                write_discipline=DISCIPLINE_SINGLE_WRITER,
                discipline_note=(
                    "Two writers tree-wide, one per analyzer family and "
                    "neither reachable from the other's edges: py.py's "
                    "_stamp_io_mode (ast) and analyze.base's "
                    "stamp_io_mode_from_call (tree-sitter, consumed by c.py "
                    "and cpp.py). Both stamp once, over the edges a single "
                    "call produced, so the single-writer ARITY holds per edge. "
                    "INV-kaduh was the COVERAGE gap behind this key — c/cpp "
                    "declared stdio.fopen mode-disambiguated with no producer, "
                    "so every fopen(p,'w') tagged fs_read — and arity was "
                    "always silent about it. The two are different questions "
                    "and this field answers only the first; which languages "
                    "can produce a mode is asserted by the parity test over "
                    "io_boundary.mode_discriminated_primitives."
                )),
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
    MetaKeySpec("constructed_from", AXIS_SYMBOL_META,
                "The callee whose result a variable is bound to — the "
                "``FastAPI`` in ``app = FastAPI()``, the "
                "``sqlalchemy.orm.declarative_base`` in "
                "``Base = declarative_base()``. Recorded verbatim, keeping "
                "any attribute qualification, so a framework YAML can "
                "distinguish a namespaced callee from a same-named local "
                "one. WI-nopod: a whole class of frameworks is configured "
                "by CONSTRUCTING an object rather than decorating or "
                "subclassing one, and nothing recorded that binding — "
                "``instantiates`` is anchored at the file (\"this file "
                "instantiated FastAPI somewhere\"), never at the variable. "
                "Sibling of ``base_classes``: both record what type a "
                "symbol relates to, as metadata a linker can later promote "
                "to edges. Static analysis cannot distinguish a constructor "
                "from a factory and the key does not try to — it names the "
                "callee, which is what the AST supports."),
    MetaKeySpec("framework_role", AXIS_SYMBOL_META,
                "Framework-specific role of a symbol whose canonical "
                "``Symbol.kind`` is a generic language construct "
                "(e.g. 'event_publisher', 'route', 'graphql_resolver'). "
                "Fold residue per audit-findings 0013 / WI-habut "
                "Wave 5."),
    MetaKeySpec("concepts", AXIS_SYMBOL_META,
                "Framework/language concepts attached to a symbol, as a list "
                "of ``{concept, framework, ...}`` dicts. (A literal example "
                "is deliberately NOT spelled out here: generate-concepts "
                "scrapes source for that dict shape, and an example in prose "
                "is indistinguishable from a declaration — it listed this "
                "module as a web framework beside django and rails.) Written "
                "BOTH by analyzers at construction (bash's ``shell_script``, "
                "python's ``main_guard``, html's ``html_entry``) and by "
                "``framework_patterns.enrich_symbols`` at consumer time.",
                write_discipline=DISCIPLINE_MERGE_UNION,
                discipline_note=(
                    "INV-hazov instance 7, found live while filing the class "
                    "item and confirmed with a positive control: "
                    "enrich_symbols assigned the pattern matches over the "
                    "top of whatever the analyzer had stamped, so a symbol "
                    "carrying a producer concept AND matching a framework "
                    "pattern lost the producer's. Both are independently "
                    "true — a function can be a main_guard AND a route — so "
                    "the union is the sound fold. Dicts are deduplicated by "
                    "their sorted items, so an identical re-stamp does not "
                    "double the list (the INV-virat inverse: append fixes "
                    "erasure and invites duplication)."
                )),
    MetaKeySpec("fields", AXIS_SYMBOL_META,
                "Declared fields of a class-like symbol, as a mapping of "
                "field name to type information. Written by the java and "
                "python analyzers on their own class symbols.",
                write_discipline=DISCIPLINE_SINGLE_WRITER,
                discipline_note=(
                    "THREE modules assign this key (java.py, py.py, "
                    "racket.py) but they PARTITION by language and can never "
                    "meet on one Symbol, so the arity per record is one. "
                    "Recorded explicitly because the collision-capable "
                    "detector flags it on shape — it is written both at "
                    "construction and by post-hoc mutation — and a reader "
                    "who does not know about the partition would otherwise "
                    "read the silence as nobody having checked."
                )),
    # ------------------------------------------------------------------
    # Symbol.meta — ADR-0027 route-marker payload (WI-tosul target-D).
    # These sit on a ``framework_role == 'route'`` marker; the canonical
    # accessor is ``routes.route_of`` / ``routes.is_route``. The 2026-07-07
    # route-surfacing concept audit found route_path / http_method read as
    # the authoritative payload at ~12 consumer sites yet unregistered —
    # closed here; route_framework / route_protocol are the additive homes
    # target-D(ii) mandated — producer emission has since landed (route_framework
    # via the framework-pattern route enrichment, INV-vokak; route_protocol via
    # ``routes.transport_meta``, INV-tibap).
    # ------------------------------------------------------------------
    MetaKeySpec("route_path", AXIS_SYMBOL_META,
                "URL path a ``framework_role == 'route'`` marker handler is "
                "registered at (e.g. '/users', '/api/v1/orders'). Analyzer-"
                "level (fires with no manifest); normalized by "
                "``routes.route_of``."),
    MetaKeySpec("http_method", AXIS_SYMBOL_META,
                "HTTP method a route marker handles (e.g. 'GET', 'POST', "
                "'ANY'). Older producers also smuggle transport sentinels "
                "('WS', 'LIVE', 'RPC') into this key — a mechanism-vs-category "
                "leak ``routes.route_of`` lifts 'WS' out into ``route_protocol`` "
                "and INV-tibap folds LIVE/RPC out of."),
    MetaKeySpec("route_framework", AXIS_SYMBOL_META,
                "Framework name uniform across a route marker (e.g. 'flask', "
                "'rails'). The canonical additive home for the route marker's "
                "framework: stamped by the framework-pattern route enrichment "
                "(first-wins, never clobbering an existing value) and read by "
                "``routes.route_of``, which unions it with the legacy "
                "``concept == 'route'`` dict and Starlette's "
                "``meta['framework']`` (INV-vokak)."),
    MetaKeySpec("route_protocol", AXIS_SYMBOL_META,
                "Transport protocol of a route endpoint ('http' | 'websocket' "
                "| …), split out of the ``http_method`` verb field. Producers "
                "persist it by spreading ``routes.transport_meta(method)`` into "
                "the route marker's meta (the py/elixir analyzers, the grpc "
                "linker, and framework-pattern enrichment); a persisted value "
                "wins in ``routes.route_of`` over the 'WS'-sentinel derivation "
                "(INV-tibap)."),
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
    # ------------------------------------------------------------------
    # Edge.meta — cross-language / framework linker vocabularies (WI-mulub).
    # WebSocket / message-queue / database-query / route linkers plus the
    # analyzer receiver-inference and the ir.py edge-dedup pass stamp these;
    # they were emitted but unregistered, so the writer-contract and
    # registry-drift checks could not police them.
    # ------------------------------------------------------------------
    MetaKeySpec("client_framework", AXIS_EDGE_META,
                "Client-side framework of a WebSocket edge, from the websocket "
                "linker's pattern→framework map (e.g. 'socketio', 'ws', "
                "'native_websocket', 'django_channels', 'fastapi', "
                "'starlette')."),
    MetaKeySpec("server_framework", AXIS_EDGE_META,
                "Server-side framework of a WebSocket edge — same vocabulary as "
                "``client_framework``, naming the endpoint that accepts the "
                "connection."),
    MetaKeySpec("topic", AXIS_EDGE_META,
                "Message-queue / pub-sub topic (or channel) an event_publishes "
                "edge with meta['channel_kind']='queue' targets — the literal "
                "topic string, or "
                "the variable's identifier name when the topic is dynamic (see "
                "``topic_type``)."),
    MetaKeySpec("topic_type", AXIS_EDGE_META,
                "Whether the message-queue ``topic`` was a string 'literal' or "
                "a 'variable' reference resolved to its identifier name."),
    MetaKeySpec("queue_type", AXIS_EDGE_META,
                "Message-queue family/pattern that produced a publish/subscribe "
                "edge, from the message_queue linker's queue classification."),
    MetaKeySpec("query_type", AXIS_EDGE_META,
                "SQL operation of a database-query edge: 'SELECT', 'INSERT', "
                "'UPDATE', or 'DELETE' (database_query linker)."),
    MetaKeySpec("table_name", AXIS_EDGE_META,
                "Database table a database-query edge reads or writes, extracted "
                "from the query by the database_query linker."),
    MetaKeySpec("handler_name", AXIS_EDGE_META,
                "Handler function name a route/command edge dispatches to "
                "(route_handler and go_cobra linkers) — the callable servicing "
                "the route or CLI subcommand."),
    MetaKeySpec("receiver_type_hint", AXIS_EDGE_META,
                "Inferred type of a call edge's receiver, stamped by the "
                "analyzer receiver-type inference to aid cross-file method "
                "resolution (e.g. an external constructor's return type)."),
    MetaKeySpec("referring_paths", AXIS_EDGE_META,
                "The list of source file paths that referred to a deduplicated "
                "edge — the only list-of-paths meta value, minted at the "
                "edge-dedup merge in ir.py so one canonical edge retains every "
                "referring site."),
    MetaKeySpec("call_lines", AXIS_EDGE_META,
                "Sorted union of every call-site line that collapsed into this "
                "edge, its own ``line`` included — the sibling of "
                "``referring_paths``, minted at the same edge-dedup merge in "
                "ir.py. Present only when two or more sites collapsed, so its "
                "absence means the single site is ``line``. The call graph "
                "keeps one edge per (src, dst, type); this retains the sites "
                "that model discards, which ADR-0017 §4 needs to map a "
                "dataflow use at line U onto the callee invoked at line U. "
                "Capped at ``ir._CALL_LINES_CAP`` (lowest N retained)."),
)


def _per_call_site_values_specs() -> tuple[MetaKeySpec, ...]:
    """The ``<key>_values`` companion each per-call-site key gains on collapse.

    DERIVED rather than hand-listed, so a key declared ``per_call_site`` tomorrow
    is documented in ``docs/schema.json`` the same day. Hand-listing them would
    be the second home for one fact that this module exists to prevent — and the
    failure mode is quiet: an undeclared companion is simply missing from the
    schema, so a consumer reading it out of a behavior map cannot learn it
    exists (the WI-zisig defect, one convention over).

    They are NOT themselves ``per_call_site``: the companion is already the
    site-wise answer, and flagging it would ask ``ir._absorb_call_site`` to
    collapse the collapse.
    """
    return tuple(
        MetaKeySpec(
            f"{spec.name}_values",
            spec.axis,
            f"Every distinct ``{spec.name}`` among the call sites that "
            f"collapsed into this edge, sorted, capped at "
            f"``ir._CALL_LINES_CAP``. Written by ``ir._absorb_call_site`` "
            f"ONLY when the sites disagreed, in which case the singular "
            f"``{spec.name}`` is removed — its presence would state one "
            f"site's value of the whole relationship (INV-vukiv). Absence of "
            f"this key therefore means every collapsed site agreed, exactly "
            f"as absence of ``call_lines`` means there was one site.",
            write_discipline=DISCIPLINE_SINGLE_WRITER,
            discipline_note=(
                "Sole writer: ir._absorb_call_site, at both collapse sites "
                "(deduplicate_edges and apply_external_id_remap, which route "
                "through the same function)."
            ),
        )
        for spec in _BASE_META_KEYS
        if spec.per_call_site
    )


META_KEYS: Final[tuple[MetaKeySpec, ...]] = (
    _BASE_META_KEYS + _per_call_site_values_specs()
)


def all_meta_key_names() -> frozenset[str]:
    """Return every canonical meta-key name."""
    return frozenset(spec.name for spec in META_KEYS)


def per_call_site_keys() -> frozenset[str]:
    """Meta keys whose value varies BY CALL SITE (INV-vukiv).

    ``ir.deduplicate_edges`` keeps one edge per ``(src, dst, edge_type)``. For
    every key named here the survivor may carry the value only if EVERY
    collapsed site agreed on it; otherwise the singular key is dropped and the
    distinct values move to ``<name>_values``. The rule is not new — INV-fubag
    gave ``call_arg_shape`` exactly this treatment, hardcoded, because a
    "every argument here is a literal" proof asserted from one of several sites
    silences real flows. What was missing was any way to DECLARE a second key,
    which is why ``io_mode`` (one producer, many sites) quietly deleted real
    ``fs_write`` boundaries for as long as it did.

    Read by ir.py rather than owned by it: the registry is where a key's
    contract lives, and putting the list in the dedup function would make the
    next per-site key someone adds invisible to it — the same "second home for
    one fact" shape this module exists to prevent.
    """
    return frozenset(spec.name for spec in META_KEYS if spec.per_call_site)


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


def call_family_edge_types() -> frozenset[str]:
    """The edge types that carry an invocation — the CALL FAMILY.

    The single source for "is this edge a call?", read from
    ``call_construct``'s declared ``applicable_edge_types`` (WI-toruz).
    ``instantiates`` is a member: a constructor call is a call, and data
    passed to it crosses into a callable exactly as through ``calls``.

    Consumers that need the call family PLUS their own extras must UNION,
    never replace: ``taint.TAINT_CALL_EDGE_TYPES`` also carries
    ``module_attr_ref`` (WI-lokuv) and ``dispatches_to`` (INV-zuhig), and
    neither is a call construct. Replacing rather than unioning deletes them
    — the mistake INV-lalad's fix design flags because it reads as a cleanup.
    """
    return _CALL_FAMILY_EDGE_TYPES


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
    N/A edge types. Edge types OUTSIDE the 17-type census (the uncensused
    canonical types, incl. the ``data_flows_to`` dataflow-direction relationship
    — crypto write→read flows fold here after the ADR-0023 ``crypto_flow`` prune)
    are UNCLASSIFIED and return ``False`` — their stamping behavior is untouched
    by this pass, pending the polyglot-census follow-up (WI-pusuv).
    """
    return edge_type in _ACCESS_MODE_NA_EDGE_TYPES


def _union_preserving_order(existing: object, incoming: object) -> list[object]:
    """Fold two contributions into one list, order-stable, no duplicates.

    Accepts a scalar on either side so a caller need not know whether the
    key is list-shaped yet — the first writer of a ``merge_union`` key
    usually has a single value in hand.

    Dicts are unhashable, so membership is tested on a canonical form
    (sorted items) rather than on the object itself; ``concepts`` is a list
    of dicts and would otherwise re-append an identical concept on every
    pass.
    """
    def as_list(value: object) -> list[object]:
        if value is None:
            return []
        if isinstance(value, (list, tuple, set, frozenset)):
            return list(value)
        return [value]

    def canonical(value: object) -> object:
        if isinstance(value, dict):
            return tuple(sorted((str(k), str(v)) for k, v in value.items()))
        if isinstance(value, (list, tuple)):
            return tuple(str(v) for v in value)
        return value

    out: list[object] = []
    seen: set[object] = set()
    for value in [*as_list(existing), *as_list(incoming)]:
        marker = canonical(value)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(value)
    return out


def write_meta_key(meta: dict[str, object], key: str, value: object) -> None:
    """Write *key* into *meta*, enforcing the key's declared discipline.

    INV-hazov. This is the single place a meta key with more than one
    writer may be written; ``check-meta-write-discipline`` fails the build
    on a direct subscript assignment that bypasses it. The point is that
    the fold lives ONE place rather than being re-derived (and re-forgotten)
    at each call site — the previous seven instances of this class were
    each fixed correctly and locally, which is precisely why the eighth was
    still possible.

    Raises:
        ValueError: if *key* is not registered, or if a ``single_writer``
            key already holds a different value. Both are declaration
            violations rather than data conditions, so they are loud: a
            wrong declaration that failed quietly would be exactly the
            false assurance ``unaudited`` exists to avoid.
    """
    spec = find_meta_key(key)
    if spec is None:
        raise ValueError(
            f"meta key {key!r} is not registered in axis_meta_keys.META_KEYS; "
            "declare it (with a write_discipline) before writing it"
        )

    if spec.write_discipline == DISCIPLINE_MERGE_UNION:
        meta[key] = _union_preserving_order(meta.get(key), value)
        return

    if spec.write_discipline == DISCIPLINE_PRODUCER_PRIMARY:
        # First writer wins. The producer's fact is about what the analyzer
        # CANNOT see, so a later, better-informed pass does not refute it.
        if meta.get(key) is None:
            meta[key] = value
        return

    if spec.write_discipline == DISCIPLINE_SINGLE_WRITER:
        existing = meta.get(key)
        if existing is not None and existing != value:
            raise ValueError(
                f"meta key {key!r} is declared single_writer but a second "
                f"writer supplied {value!r} over {existing!r}. Either the "
                "declaration is wrong (two writers really can reach this "
                "key — pick merge_union / producer_primary / refines) or "
                "the new writer is."
            )
        meta[key] = value
        return

    # refines / unaudited: plain assignment. For ``refines`` that is the
    # audited answer; for ``unaudited`` it is the honest one — no claim has
    # been made, so no enforcement is applied.
    meta[key] = value


def filter_meta_key(
    meta: dict[str, object],
    key: str,
    keep: Callable[[object], bool],
) -> int:
    """Narrow a registered meta key in place, keeping only entries ``keep`` admits.

    Returns the number of entries removed.

    INV-hazov (b). The write-discipline vocabulary had add / refine / replace
    and NO REMOVE, and the gap was not academic: ``strip_test_file_only_concepts``
    removes concepts a later-computed fact invalidates, and it could not be
    routed through :func:`write_meta_key` because ``concepts`` is declared
    ``merge_union`` — the chokepoint would have UNIONED the stripped entries
    straight back in. So the one legitimate remover in the tree had to bypass
    the chokepoint, and the linter could not tell that bypass from a careless
    direct assignment.

    WHY THIS IS AN OPERATION AND NOT A NEW DISCIPLINE, which is the actual
    question the residual left open. A ``write_discipline`` describes how
    several WRITERS COMBINE, and ``concepts`` genuinely is ``merge_union`` for
    its writers — two enrichment passes each contributing concepts must keep
    both. Declaring the key "removal-shaped" would be false about those writers
    and would forfeit the union guarantee INV-virat and INV-zumin were fixed to
    get. A curator is not a competing writer: it runs after the writers and
    narrows their agreed result. That is a second verb on the same key, not a
    different discipline for it.

    The two rejected alternatives, recorded so this is not re-litigated:
    adding a ``removal`` discipline value (false about the writers, as above);
    and declaring the site an audited exception in the module (leaves the
    bypass invisible to the linter, which is exactly the "silent bypass" the
    residual objected to — an exception list cannot distinguish "removes
    deliberately" from "assigned directly and nobody noticed").

    Removal is restricted to ``merge_union`` keys because that is the only
    discipline where narrowing is meaningful: a ``single_writer`` or
    ``producer_primary`` key holds one authoritative value, and silently
    dropping it would be the erasure this whole class is about.

    Raises:
        ValueError: if *key* is unregistered, or is not declared
            ``merge_union``. Both are declaration violations, so both are
            loud — a quiet failure here would buy exactly the false assurance
            ``unaudited`` exists to refuse.
    """
    spec = find_meta_key(key)
    if spec is None:
        raise ValueError(
            f"meta key {key!r} is not registered in axis_meta_keys.META_KEYS; "
            "declare it (with a write_discipline) before filtering it"
        )
    if spec.write_discipline != DISCIPLINE_MERGE_UNION:
        raise ValueError(
            f"meta key {key!r} is declared {spec.write_discipline}; only a "
            f"{DISCIPLINE_MERGE_UNION} key may be narrowed. A single-valued "
            "key holds one authoritative value and dropping it is the "
            "erasure INV-hazov is about."
        )
    current = meta.get(key)
    if not isinstance(current, list):
        return 0
    kept = [entry for entry in current if keep(entry)]
    removed = len(current) - len(kept)
    if removed:
        meta[key] = kept
    return removed
