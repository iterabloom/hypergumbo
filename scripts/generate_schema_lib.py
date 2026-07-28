# SPDX-License-Identifier: AGPL-3.0-or-later
"""Introspection-driven $def builder for docs/schema.json (WI-kutas).

Why this module exists: the previous generate-schema hand-coded the
Symbol / Edge / Span / AnalysisRun ``$defs`` as literal Python dicts, so
a dataclass field add / remove / retype could not propagate — that is
how ``Symbol.language`` stayed typed as a required non-nullable string
after ADR-0031 relaxed it to ``Optional[str]`` (WI-kufib, 262 violating
nodes per self-analysis), how the ADR-0032-removed ``canonical_name``
survived in the schema, and how four emitted-on-every-node axis fields
went undeclared.

How it works: each $def is produced by :func:`build_def` from a
:class:`ClassSpec` that separates the two sources of truth —

- **Structure** (property set, JSON types, nullability, required-ness)
  is introspected from ``dataclasses.fields()`` + ``get_type_hints``.
  ``Optional[X]`` becomes ``oneOf [X, null]``; a field is required iff
  it has no default and is not Optional.
- **Decoration** (descriptions, x-axis-of-values / x-deprecated maps,
  enums, numeric bounds) comes from the spec's per-JSON-key tables,
  merged onto the introspected type schema.

The serialization wrinkles ``to_dict()`` introduces are declared
explicitly: ``renames`` (``AnalysisRun.pass_id`` → ``"pass"``,
``Edge.edge_type`` → ``"type"``), ``folded`` fields that serialize into
a composite JSON property (Symbol's seven supply-chain fields →
``supply_chain``; Edge's ``evidence_*`` + ``meta`` → ``meta``) whose
schema is hand-authored in ``composites``, ``overrides`` for the few
properties whose constraints live inside a ``oneOf`` branch and cannot
be flat-merged, and ``conditional`` JSON keys that ``to_dict()`` omits
when None (``Edge.dst_ref`` / ``derived_from``).

Drift guards (the point of the rework): :func:`build_def` raises
:class:`SchemaDriftError` when a decoration / override names a JSON key
the dataclass no longer produces (the ``canonical_name`` failure mode)
and when a dataclass field has no decoration / override / composite
(the ``discovery_language`` failure mode). :func:`verify_round_trip`
additionally asserts a fully-populated instance's ``to_dict()`` key set
equals the schema property set, pinning the field→JSON mapping itself.
"""

from __future__ import annotations

import copy
import dataclasses
import typing
from typing import Any, Callable, Dict, List, Optional, Set, get_args, get_origin

from hypergumbo_core.edge_types import AXIS_ENDPOINT_SHAPE, EDGE_TYPES
from hypergumbo_core.evidence_types import EVIDENCE_TYPES
from hypergumbo_core.ir import AnalysisRun, Edge, ExternalRef, Span, Symbol
from hypergumbo_core.symbol_kinds import SYMBOL_KINDS


class SchemaDriftError(Exception):
    """A ClassSpec disagrees with its dataclass — schema drift caught at generation time."""


def python_type_to_json_schema(py_type: Any) -> Dict[str, Any]:
    """Convert a Python type annotation to a JSON Schema type fragment."""
    origin = get_origin(py_type)
    args = get_args(py_type)

    if py_type is type(None):  # pragma: no cover — Optional unwrap handles None below
        return {"type": "null"}

    if origin is not None:
        # Optional[X] (Union[X, None])
        if type(None) in args:
            non_none_args = [a for a in args if a is not type(None)]
            if len(non_none_args) == 1:
                inner_schema = python_type_to_json_schema(non_none_args[0])
                return {"oneOf": [inner_schema, {"type": "null"}]}

        # Scalar-or-list normalization union: Union[X, List[X]] accepts either a
        # scalar X or a list of X at construction and normalizes the scalar to a
        # single-element list (Symbol/Edge.origin via __post_init__, INV-jidat),
        # so it always SERIALIZES as an array of X. The union widens the *input*
        # type for mypy strict (call sites pass a scalar pass_id); the wire form
        # stays array[X], so map to the list schema, not a oneOf.
        if origin is typing.Union and len(args) == 2:
            list_arm = [a for a in args if get_origin(a) is list]
            scalar_arm = [
                a for a in args if get_origin(a) is None and a is not type(None)
            ]
            if len(list_arm) == 1 and len(scalar_arm) == 1:
                inner = get_args(list_arm[0])
                if inner and inner[0] is scalar_arm[0]:
                    return {
                        "type": "array",
                        "items": python_type_to_json_schema(inner[0]),
                    }

        if origin in (list, set, frozenset):
            # set / frozenset fields serialize as sorted JSON arrays.
            if args:
                return {"type": "array", "items": python_type_to_json_schema(args[0])}
            return {"type": "array"}  # pragma: no cover — all IR lists are parameterized

        if origin is dict:
            return {"type": "object"}

    if py_type is str:
        return {"type": "string"}
    if py_type is int:
        return {"type": "integer"}
    if py_type is float:
        return {"type": "number"}
    if py_type is bool:
        return {"type": "boolean"}

    if dataclasses.is_dataclass(py_type):
        return {"$ref": f"#/$defs/{py_type.__name__}"}

    raise SchemaDriftError(  # pragma: no cover — reached only by a new unmapped type
        f"no JSON Schema mapping for type {py_type!r}; "
        "extend python_type_to_json_schema or add an override"
    )


@dataclasses.dataclass
class ClassSpec:
    """Serialization spec binding one dataclass to its $def."""

    cls: type
    description: str
    # dataclass field name -> JSON key (identity when absent)
    renames: Dict[str, str] = dataclasses.field(default_factory=dict)
    # dataclass field name -> composite JSON key it serializes into
    folded: Dict[str, str] = dataclasses.field(default_factory=dict)
    # composite JSON key -> full hand-authored schema
    composites: Dict[str, Dict[str, Any]] = dataclasses.field(default_factory=dict)
    # JSON key -> full schema replacement (constraints inside oneOf branches)
    overrides: Dict[str, Dict[str, Any]] = dataclasses.field(default_factory=dict)
    # JSON key -> dict merged onto the introspected type schema
    decorations: Dict[str, Dict[str, Any]] = dataclasses.field(default_factory=dict)
    # JSON keys to_dict() omits when the value is None
    conditional: Set[str] = dataclasses.field(default_factory=set)
    # JSON keys required beyond the no-default-and-not-Optional rule
    extra_required: List[str] = dataclasses.field(default_factory=list)
    # JSON keys to_dict() adds that have no backing dataclass field
    # (e.g. Limits' not_captured / analyzer_version, Feature's id / name)
    extras: Dict[str, Dict[str, Any]] = dataclasses.field(default_factory=dict)
    # dataclass fields deliberately NOT serialized by to_dict()
    omitted: Set[str] = dataclasses.field(default_factory=set)
    # builds a fully-populated instance for verify_round_trip
    sample_factory: Optional[Callable[[], Any]] = None
    # serializes an instance to its JSON dict (default: to_dict())
    serializer: Callable[[Any], Dict[str, Any]] = lambda instance: instance.to_dict()


def build_def(spec: ClassSpec) -> Dict[str, Any]:
    """Build one $def from introspected structure + declared decoration."""
    hints = typing.get_type_hints(spec.cls)
    properties: Dict[str, Any] = {}
    required: List[str] = []
    composite_placed: Set[str] = set()

    field_names = {fld.name for fld in dataclasses.fields(spec.cls)}
    for f in dataclasses.fields(spec.cls):
        if f.name in spec.omitted:
            continue
        if f.name in spec.folded:
            target = spec.folded[f.name]
            if target not in spec.composites:
                raise SchemaDriftError(  # pragma: no cover — spec-authoring bug
                    f"{spec.cls.__name__}.{f.name} folds into {target!r} "
                    "but no composite schema is declared for it"
                )
            # Placement: a composite anchored by a same-named field
            # (Edge.meta -> "meta") lands at the anchor field's position;
            # an unanchored composite (Symbol's "supply_chain") lands at
            # its first folded field's position.
            is_anchor = f.name == target
            if target not in composite_placed and (
                is_anchor or target not in field_names
            ):
                properties[target] = copy.deepcopy(spec.composites[target])
                composite_placed.add(target)
            continue

        json_key = spec.renames.get(f.name, f.name)
        if json_key in spec.overrides:
            properties[json_key] = copy.deepcopy(spec.overrides[json_key])
        else:
            type_schema = python_type_to_json_schema(hints[f.name])
            if json_key not in spec.decorations:
                raise SchemaDriftError(
                    f"{spec.cls.__name__}.{f.name} (JSON key {json_key!r}) has no "
                    "decoration entry — every schema property needs at least a "
                    "description. Add it to the ClassSpec so docs/schema.json "
                    "documents the new field."
                )
            type_schema.update(copy.deepcopy(spec.decorations[json_key]))
            properties[json_key] = type_schema

        # Required iff no default and not Optional, minus conditional keys.
        origin = get_origin(hints[f.name])
        args = get_args(hints[f.name])
        is_optional = origin is not None and type(None) in args
        has_default = (
            f.default is not dataclasses.MISSING
            or f.default_factory is not dataclasses.MISSING
        )
        if not has_default and not is_optional and json_key not in spec.conditional:
            required.append(json_key)

    # to_dict()-only keys with no backing dataclass field.
    for extra_key, extra_schema in spec.extras.items():
        properties[extra_key] = copy.deepcopy(extra_schema)

    # Guard: every decorated / overridden / composite key must exist.
    produced = set(properties)
    for table_name, table in (
        ("decorations", spec.decorations),
        ("overrides", spec.overrides),
        ("composites", spec.composites),
    ):
        stale = set(table) - produced
        if stale:
            raise SchemaDriftError(
                f"{spec.cls.__name__}: {table_name} entries for JSON keys that "
                f"the dataclass no longer produces: {sorted(stale)}. Remove the "
                "stale entries (the dataclass is the source of truth)."
            )

    required.extend(k for k in spec.extra_required if k not in required)

    def_schema: Dict[str, Any] = {
        "type": "object",
        "description": spec.description,
        "properties": properties,
    }
    if required:
        def_schema["required"] = required
    return def_schema


def verify_round_trip(spec: ClassSpec, def_schema: Dict[str, Any]) -> None:
    """Assert a fully-populated instance's to_dict() keys == schema properties."""
    if spec.sample_factory is None:  # pragma: no cover — every spec declares one
        return
    instance = spec.sample_factory()
    dict_keys = set(spec.serializer(instance))
    schema_keys = set(def_schema["properties"])
    if dict_keys != schema_keys:
        raise SchemaDriftError(
            f"{spec.cls.__name__}: to_dict() and generated schema disagree. "
            f"only-in-to_dict={sorted(dict_keys - schema_keys)}, "
            f"only-in-schema={sorted(schema_keys - dict_keys)}"
        )


# ---------------------------------------------------------------------------
# Shared sample pieces
# ---------------------------------------------------------------------------

def _sample_span() -> Span:
    return Span(start_line=1, end_line=2, start_col=0, end_col=1)


def _sample_symbol() -> Symbol:
    sym = Symbol(
        id="python:a.py:1-2:f:function",
        name="f",
        kind="function",
        language="python",
        path="a.py",
        span=_sample_span(),
        origin=["python"],
        origin_run_id="uuid:sample",
    )
    # quality is a conditional key (omitted when None per INV-nuzal) — populate
    # it so the schema-drift round-trip sees a fully-populated instance.
    sym.quality = {"score": 0.9, "reason": "sample"}
    return sym


def _sample_edge() -> Edge:
    # dst_ref / derived_from are conditional keys — populate them so the
    # round-trip check sees the full key set.
    return Edge.create(
        src="a",
        dst="b",
        edge_type="calls",
        line=1,
        origin="python",
        origin_run_id="uuid:sample",
        evidence_lang="python",
        dst_ref=ExternalRef(lang="python", module_path="os", name="getcwd"),
        derived_from=["sym:1"],
    )


def _sample_analysis_run() -> AnalysisRun:
    run = AnalysisRun.create(pass_id="python", version="0.0.0")
    # Populate the conditional reporting lists so the schema-drift check sees a
    # fully-populated instance (INV-virik — these are omitted when empty).
    run.skipped_passes = [{"pass": "somepass", "reason": "not applicable"}]
    run.failed_files = [{"path": "broken.py", "reason": "SyntaxError"}]
    run.warnings = ["a warning"]
    return run


# ---------------------------------------------------------------------------
# Span
# ---------------------------------------------------------------------------

def _span_spec() -> ClassSpec:
    line_desc = (
        "{} line number (1-indexed); 0 reserved for synthetic boundary "
        "symbols (kind=external_symbol) which have no source location"
    )
    return ClassSpec(
        cls=Span,
        description="Source code location",
        decorations={
            "start_line": {"minimum": 0, "description": line_desc.format("Starting")},
            "end_line": {"minimum": 0, "description": line_desc.format("Ending")},
            "start_col": {"minimum": 0, "description": "Starting column (0-indexed)"},
            "end_col": {"minimum": 0, "description": "Ending column (0-indexed)"},
        },
        sample_factory=_sample_span,
    )


# ---------------------------------------------------------------------------
# Symbol
# ---------------------------------------------------------------------------

def _symbol_spec() -> ClassSpec:
    supply_chain_fields = (
        "supply_chain_tier",
        "supply_chain_reason",
        "is_test_file",
        "is_example_file",
        "is_config_file",
        "is_generated_file",
        "is_exported",
    )
    return ClassSpec(
        cls=Symbol,
        description="A code symbol (function, class, method, etc.)",
        folded={name: "supply_chain" for name in supply_chain_fields},
        composites={
            "supply_chain": {
                "type": "object",
                "description": "Supply chain classification",
                "properties": {
                    "tier": {
                        "type": "integer",
                        "enum": [1, 2, 3, 4],
                        "description": "1=first_party, 2=internal_dep, 3=external_dep, 4=derived",
                    },
                    "tier_name": {
                        "type": "string",
                        "enum": ["first_party", "internal_dep", "external_dep", "derived"],
                    },
                    "reason": {"type": "string", "description": "Why this tier was assigned"},
                    "is_test_file": {
                        "type": "boolean",
                        "description": "True if the file holds test code (independent of tier)",
                    },
                    "is_example_file": {
                        "type": "boolean",
                        "description": "True if the file is example/demo/sample/tutorial code",
                    },
                    "is_config_file": {
                        "type": "boolean",
                        "description": "True if the file is a dependency/build manifest",
                    },
                    "is_generated_file": {
                        "type": "boolean",
                        "description": "True if the file is generated code",
                    },
                    "is_exported": {
                        "type": "boolean",
                        "description": "True if the symbol is part of the package's public API",
                    },
                },
                "required": ["tier", "tier_name", "reason"],
            },
        },
        overrides={
            # minimum lives inside the oneOf integer branch — not flat-mergeable.
            "cyclomatic_complexity": {
                "oneOf": [{"type": "integer", "minimum": 1}, {"type": "null"}],
                "description": "McCabe cyclomatic complexity (decision points + 1)",
            },
            "line_span": {
                "oneOf": [{"type": "integer", "minimum": 1}, {"type": "null"}],
                "description": (
                    "Physical line span of the symbol body (end_line - "
                    "start_line + 1, including blank/comment lines). NOT "
                    "source-lines-of-code; file-level SLOC is "
                    "profile.languages[*].loc. Renamed from lines_of_code "
                    "(WI-bozid)."
                ),
            },
        },
        decorations={
            "id": {"description": (
                "Unique, location-addressed node identifier (ADR-0036 "
                "grammar: lang:path:span:name:kind). Because it encodes "
                "location it CHURNS on file move / rename / signature "
                "change; for an edits-surviving identity use stable_id, "
                "and for cross-run rename tracking use fingerprint."
            )},
            "name": {"description": "Symbol name"},
            "kind": {
                "description": (
                    "Symbol type. Per ADR-0027, the canonical axiom is "
                    "'Symbol.kind names the source-language syntactic "
                    "construct that the symbol represents.' The "
                    "x-axis-of-values map below documents the canonical "
                    "registry from packages/hypergumbo-core/src/"
                    "hypergumbo_core/symbol_kinds.py::SYMBOL_KINDS. The "
                    "schema enum is intentionally open (type: string "
                    "without enum constraint) until ADR-0027 Phase 4b "
                    "producer migrations land per-cluster — current "
                    "production includes dynamic kind=f\"ipc_{...}\" "
                    "emits at ipc.py:498 and phoenix_ipc.py:276 that "
                    "produce values not in the static registry. The L3 "
                    "producer-coherence linter "
                    "(scripts/check-producer-axis-coherence) gates new "
                    "literal additions against the registry; the open "
                    "enum honestly reflects the producer-side leak the "
                    "ADR is fixing."
                ),
                "x-axis-of-values": {spec.name: spec.axis for spec in SYMBOL_KINDS},
                # ADR-0027 §"Phase 4a" (additive): every endpoint_shape
                # Symbol.kind value is a deprecation candidate scheduled
                # for removal in Phase 4b. Producers either no longer
                # emit these (Phase 3 fold complete) or are scheduled to
                # fold during Phase 3 sub-PRs. Values stay valid in the
                # open schema for the deprecation window. Per-value
                # migration guidance lives in the registry's
                # SymbolKindSpec.description.
                "x-deprecated": [
                    spec.name for spec in SYMBOL_KINDS
                    if spec.axis == AXIS_ENDPOINT_SHAPE
                ],
            },
            "language": {
                "description": (
                    "Programming language of the host source file. Null for "
                    "Class B synthetic stand-ins emitted by protocol linkers "
                    "(ADR-0031) — those carry discovery_language + "
                    "protocol_origin instead."
                ),
            },
            "path": {"description": "File path"},
            "span": {"description": "Source location"},
            "origin": {
                "description": "Pass IDs that contributed to this symbol (INV-jidat)",
            },
            "origin_run_id": {"description": "Unique execution ID"},
            "stable_id": {
                "description": (
                    "Structural identity hash within a (qualified_name, "
                    "module_path) scope (ADR-0014 as amended by Phase 6 / "
                    "INV-bazij): survives body edits, NOT rename or move. "
                    "Serialized as sha256:<16hex> and shares this exact "
                    "surface with shape_id; the two are discriminated by "
                    "field name (and the top-level stable_id_scheme / "
                    "shape_id_scheme descriptors), NOT by an in-value prefix, "
                    "and their value-spaces are disjoint. Do not join on bare "
                    "hash values across the two axes (WI-tisar)."
                ),
            },
            "shape_id": {"description": (
                "Structural *skeleton* hash — the parse subtree with "
                "identifiers, literals, comments, and punctuation "
                "STRIPPED (ADR-0014 §1), so same-shape / different-name "
                "symbols collide. Within-language only. Contrast "
                "fingerprint, which KEEPS identifiers/literals; shape_id "
                "is a strict coarsening of it. Serialized as sha256:<16hex>, "
                "the same surface as stable_id (see WI-tisar). (Currently a "
                "serialized output-boundary field with no internal consumer.)"
            )},
            "fingerprint": {"description": (
                "Structural content hash of the symbol's parse subtree "
                "(shape + identifiers + literals; whitespace/comment-"
                "invariant), prefixed with the scheme tag declared in "
                "symbol_fingerprint_scheme. Null when the span has no "
                "parseable content or no grammar is available."
            )},
            "quality": {"description": (
                "Node-level quality assessment ({score, reason}). Has no "
                "producer (INV-nuzal): unlike edge.quality it is not derived "
                "from confidence, so it is omitted when null (INV-virik omit-"
                "when-empty) and appears only if a future pass populates it."
            )},
            "meta": {"description": "Language-specific metadata"},
            "signature": {
                "description": (
                    "Function/method signature string, e.g., "
                    "'(x: int, y: str) -> bool'"
                ),
            },
            "docstring": {
                "description": "First-line summary of doc comment (truncated to 80 chars)",
            },
            "modifiers": {
                "description": "Semantic modifiers (e.g., 'native', 'public', 'static')",
            },
            "discovery_language": {
                "description": (
                    "ADR-0031: host source language where a linker discovered "
                    "the pattern that produced this Class B synthetic "
                    "stand-in. Null for real-source declarations (language "
                    "already carries that information)."
                ),
            },
            "protocol_origin": {
                "description": (
                    "ADR-0031: protocol or framework family (kafka, "
                    "websocket, grpc, database_query, ...) for Class B "
                    "synthetic stand-ins. Catalog at protocol_origins.py. "
                    "Null for real-source declarations."
                ),
            },
            "display_label": {
                "description": (
                    "ADR-0032: human-readable display string for synthetic "
                    "linker stand-ins (e.g. \"invoke('save_data')\"). "
                    "Consumers display it but never branch on it. Null for "
                    "real-source declarations."
                ),
            },
            "qualified_name": {
                "description": (
                    "ADR-0032: fully-qualified name including ancestor "
                    "containers, joined with the per-language separator "
                    "declared in qualified_name_axis.py. Null when name "
                    "already encodes the qualified form or no separator "
                    "policy is declared."
                ),
            },
            "visibility": {
                "description": (
                    "INV-jusot: the single canonical visibility level of the "
                    "symbol — one of public / private / protected / internal / "
                    "package (vocabulary in visibility.py). Computed once in "
                    "finalize from the highest-priority signal (a language "
                    "modifier, else the legacy Apex/Clojure meta['visibility'], "
                    "else the Python leading-underscore name convention, else "
                    "the public default); the deciding signal is recorded in "
                    "meta['visibility_signal']. Null only on symbols that "
                    "predate the finalize visibility pass. Supersedes the "
                    "retired per-language meta['visibility'] key; is_exported "
                    "is reconciled to visibility=='public' in a follow-up."
                ),
            },
        },
        sample_factory=_sample_symbol,
        conditional={"quality"},
    )


# ---------------------------------------------------------------------------
# Edge
# ---------------------------------------------------------------------------

def _edge_spec() -> ClassSpec:
    return ClassSpec(
        cls=Edge,
        description="A relationship between two symbols",
        renames={"edge_type": "type"},
        folded={
            "evidence_type": "meta",
            "evidence_lang": "meta",
            "meta": "meta",
        },
        composites={
            "meta": {
                "type": "object",
                "description": "Edge metadata",
                "properties": {
                    "evidence_type": {
                        "type": "string",
                        "description": (
                            "Inference pathway by which the analyzer "
                            "concluded this edge exists. Per ADR-0028, "
                            "the canonical axiom is 'Edge.evidence_type "
                            "names the inference pathway by which the "
                            "analyzer concluded this edge exists.' The "
                            "x-axis-of-values map documents the canonical "
                            "registry from packages/hypergumbo-core/src/"
                            "hypergumbo_core/evidence_types.py::"
                            "EVIDENCE_TYPES. The schema enum is "
                            "intentionally open (type: string without "
                            "enum constraint) until Phase 4b producer "
                            "migrations land per-cluster — current "
                            "production includes dynamic f-string emits "
                            "(websocket.py, di_resolution.py, "
                            "inheritance.py) that produce values outside "
                            "the static registry. The L3 producer-"
                            "coherence linter gates literal additions; "
                            "the open enum honestly reflects the "
                            "producer-side leak the ADR is fixing."
                        ),
                        "x-axis-of-values": {
                            spec.name: spec.axis for spec in EVIDENCE_TYPES
                        },
                        # ADR-0028 §"Phase 4a" (additive): every
                        # endpoint_shape evidence_type value is a
                        # deprecation candidate scheduled for removal in
                        # Phase 4b. Cluster B canary fold (WI-nunal) has
                        # shipped; Clusters C/D are mid-Phase-3 with
                        # awaits_bakeoff_validation tags. Values stay
                        # valid in the open schema for the deprecation
                        # window. Per-value migration guidance lives in
                        # the registry's EvidenceTypeSpec.description.
                        "x-deprecated": [
                            spec.name for spec in EVIDENCE_TYPES
                            if spec.axis == AXIS_ENDPOINT_SHAPE
                        ],
                    },
                    "evidence_lang": {"type": "string"},
                },
            },
        },
        overrides={
            "dst_ref": {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "lang": {"type": "string"},
                            "module_path": {"type": "string"},
                            "name": {"type": "string"},
                        },
                        "required": ["lang", "module_path", "name"],
                        "additionalProperties": False,
                    },
                    {"type": "null"},
                ],
                "default": None,
                "description": (
                    "Structured identity for the dst endpoint when "
                    "it points at an external symbol (stdlib, "
                    "dependency, unresolved external). Per WI-tihup, "
                    "this is the canonical source of truth for "
                    "external-target identity — the legacy `dst` "
                    "string field is built from the same "
                    "ExternalRef and stays populated alongside for "
                    "back-compat with consumers that haven't "
                    "migrated. None when dst points at an in-repo "
                    "Symbol (whose `dst` field is a real Symbol ID)."
                ),
            },
            "derived_from": {
                "oneOf": [
                    {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    {"type": "null"},
                ],
                "default": None,
                "description": (
                    "Symbol (or Edge) IDs the producer consumed "
                    "to construct this Edge (INV-rukor). Populated "
                    "by linkers; null for analyzer-originated edges."
                ),
            },
        },
        decorations={
            "id": {"description": "Unique edge identifier"},
            "edge_key": {"description": "Canonical key for deduplication"},
            "src": {"description": "Source symbol ID"},
            "dst": {"description": "Destination symbol ID"},
            "type": {
                "description": "Relationship type",
                "enum": [spec.name for spec in EDGE_TYPES],
                "x-axis-of-values": {spec.name: spec.axis for spec in EDGE_TYPES},
                # ADR-0023 §6 Phase 4a: every endpoint_shape value is
                # a deprecation candidate scheduled for removal in a
                # future SCHEMA_VERSION minor bump. Producers no
                # longer emit these (Phase 3 / WI-mokam-jalig
                # complete); the values stay in the enum for the
                # one-minor-version dual-validity window so external
                # consumers can adapt. Per-value migration guidance
                # lives in the registry's spec.description (cited as
                # "per ADR-0023 §6 fold to <canonical> + meta[...]").
                "x-deprecated": [
                    spec.name for spec in EDGE_TYPES
                    if spec.axis == AXIS_ENDPOINT_SHAPE
                ],
            },
            "line": {
                "minimum": 1,
                "description": "Line number where relationship occurs",
            },
            "confidence": {
                "minimum": 0.0,
                "maximum": 1.0,
                "description": (
                    "Detection reliability (0.0-1.0) — the producer's "
                    "evidence-derived estimate that the relationship exists "
                    "(ADR-0039 ruling 1). NOT a ranking value; ranking "
                    "prominence lives in rank_score."
                ),
            },
            "confidence_source": {
                "enum": ["evidence_derived", "emitter_constant", "composite"],
                "description": (
                    "Provenance of the confidence value (ADR-0039 ruling 2): "
                    "evidence_derived (from the evidence_type registry base), "
                    "emitter_constant (a declared hardcoded producer value), "
                    "or composite (still fuses a ranking adjustment ruling 3 "
                    "relocates to rank_score)."
                ),
            },
            "rank_score": {
                "minimum": 0.0,
                "maximum": 1.0,
                "description": (
                    "Ranking prominence (0.0-1.0), ADR-0039 ruling 3. "
                    "Initializes from confidence and accumulates the ranking "
                    "adjustments relocated off confidence; equal to confidence "
                    "until a producer relocates its adjustment."
                ),
            },
            "origin": {
                "description": "Pass IDs that contributed to this edge (INV-jidat)",
            },
            "origin_run_id": {"description": "Unique execution ID"},
            "is_resolved": {
                "default": True,
                "description": (
                    "Whether the dst symbol was resolved at analysis "
                    "time. Per ADR-0028, this sibling field captures "
                    "what the Cluster B `*_unresolved` evidence_type "
                    "values previously smuggled into the inference "
                    "label. Default True (the ~90% case); Phase 3 "
                    "producers explicitly set False for the "
                    "Cluster B fold targets."
                ),
            },
        },
        conditional={"dst_ref", "derived_from"},
        # confidence has a producer default (0.85) but is contractually
        # always emitted; keep the pre-existing required guarantee.
        extra_required=["confidence"],
        sample_factory=_sample_edge,
    )


# ---------------------------------------------------------------------------
# AnalysisRun
# ---------------------------------------------------------------------------

def _analysis_run_spec() -> ClassSpec:
    return ClassSpec(
        cls=AnalysisRun,
        description="Provenance tracking for an analysis pass",
        renames={"pass_id": "pass"},
        decorations={
            "execution_id": {"description": "Unique run identifier (uuid)"},
            "run_signature": {"description": "Deterministic config hash"},
            "repo_fingerprint": {"description": "Git state hash for cache invalidation"},
            "pass": {"description": "Analysis pass identifier"},
            "version": {"description": "Hypergumbo version"},
            "toolchain": {
                "description": (
                    "Runtime that produced the analysis (python + "
                    "tree-sitter/grammar versions when applicable)"
                ),
                "properties": {
                    "name": {"type": "string"},
                    "version": {"type": "string"},
                },
            },
            "config_fingerprint": {
                "description": "sha256 fingerprint of the effective per-pass config",
            },
            "files_analyzed": {"description": "Number of files analyzed by this pass"},
            "files_skipped": {"description": "Number of files skipped by this pass"},
            "skipped_passes": {
                "description": (
                    "Legacy per-run mirror of limits.skipped_passes. Pass-level "
                    "skips (a pass that did not run: no files matched, missing "
                    "grammar, or crashed) have no analysis_runs[] entry and "
                    "appear only in top-level limits.skipped_passes; this "
                    "per-run field has no current producer and is omitted when "
                    "empty (INV-virik / INV-nihug). Read limits.skipped_passes "
                    "for skip provenance."
                ),
            },
            "failed_files": {
                "description": (
                    "Per-file failures recorded by this pass "
                    "({path, reason}; drained into limits.failed_files)"
                ),
            },
            "warnings": {"description": "Warnings emitted during the pass"},
            "started_at": {
                "format": "date-time",
                "description": "UTC timestamp when the pass started",
            },
            "duration_ms": {"description": "Wall-clock duration in milliseconds"},
            "pass_version": {
                "description": (
                    "Code-hash of the pass module (compute_pass_version); "
                    "distinct from `version` (the package version)"
                ),
            },
            "nodes_emitted": {
                "description": (
                    "Number of Symbols this pass contributed (INV-gizik / "
                    "INV-pitab); distinct from files_analyzed (a file count)"
                ),
            },
            "edges_emitted": {
                "description": (
                    "Number of Edges this pass contributed (INV-gizik / "
                    "INV-pitab); a pass with edges_emitted>0 must carry "
                    "duration_ms>0"
                ),
            },
        },
        # INV-virik: the per-run reporting lists are present ONLY when non-empty
        # (present-when-populated), so they are conditional keys.
        conditional={"skipped_passes", "failed_files", "warnings"},
        sample_factory=_sample_analysis_run,
    )


# ---------------------------------------------------------------------------
# Limits family (WI-kutas PR2 — the previously-opaque "limits" block)
# ---------------------------------------------------------------------------

def _failed_file_spec() -> ClassSpec:
    from hypergumbo_core.limits import FailedFile

    return ClassSpec(
        cls=FailedFile,
        description="A file that failed during analysis",
        decorations={
            "path": {"description": "Repo-relative path of the failed file"},
            "reason": {"description": "Why analysis failed (exception summary)"},
            "analyzer": {"description": "Pass ID of the analyzer that failed"},
        },
        sample_factory=lambda: __import__(
            "hypergumbo_core.limits", fromlist=["FailedFile"]
        ).FailedFile(path="a.py", reason="boom", analyzer="python"),
    )


def _classification_failure_spec() -> ClassSpec:
    from hypergumbo_core.limits import ClassificationFailure

    return ClassSpec(
        cls=ClassificationFailure,
        description="A file that failed supply chain classification",
        decorations={
            "path": {"description": "Path that could not be classified"},
            "reason": {"description": "Why classification fell through"},
        },
        sample_factory=lambda: __import__(
            "hypergumbo_core.limits", fromlist=["ClassificationFailure"]
        ).ClassificationFailure(path="a.py", reason="outside repo"),
    )


def _ambiguous_path_spec() -> ClassSpec:
    from hypergumbo_core.limits import AmbiguousPath

    return ClassSpec(
        cls=AmbiguousPath,
        description="A file with ambiguous supply chain classification",
        decorations={
            "path": {"description": "Path with ambiguous classification"},
            "assigned": {"description": "Tier that was assigned despite ambiguity"},
            "note": {"description": "Why the classification is ambiguous"},
        },
        sample_factory=lambda: __import__(
            "hypergumbo_core.limits", fromlist=["AmbiguousPath"]
        ).AmbiguousPath(path="vendor/x.py", assigned=3, note="vendored?"),
    )


def _supply_chain_limits_sample():
    from hypergumbo_core.limits import SupplyChainLimits, ClassificationFailure, AmbiguousPath

    # Populate both conditional reporting lists so the schema-drift check sees a
    # fully-populated instance (INV-virik — these are omitted when empty, so an
    # empty SupplyChainLimits serializes as {}).
    return SupplyChainLimits(
        classification_failures=[ClassificationFailure(path="weird.xyz", reason="no rule matched")],
        ambiguous_paths=[AmbiguousPath(path="edge.case", assigned=2, note="two rules matched")],
    )


def _supply_chain_limits_spec() -> ClassSpec:
    from hypergumbo_core.limits import SupplyChainLimits

    return ClassSpec(
        cls=SupplyChainLimits,
        description="Supply chain classification issues",
        decorations={
            "classification_failures": {
                "description": "Files that failed supply chain classification",
            },
            "ambiguous_paths": {
                "description": "Files whose classification was ambiguous",
            },
        },
        # INV-virik: present-when-populated (omitted when empty).
        conditional={"classification_failures", "ambiguous_paths"},
        sample_factory=_supply_chain_limits_sample,
    )


def _limits_sample():
    from hypergumbo_core.limits import Limits

    lim = Limits(
        max_tier_applied=2,
        max_files_per_analyzer=10,
        test_files_excluded=True,
        partial_results_reason="one or more passes crashed; results are partial",
        truncated_files=["big.py"],
        skipped_languages=["haskell"],
    )
    # Populate the conditional reporting lists so the schema-drift check sees a
    # fully-populated instance (INV-virik — these are omitted when empty).
    lim.add_failed_file(path="broken.py", reason="SyntaxError", analyzer="python")
    lim.add_tier_filtered_file("dist/bundle.min.js")
    return lim


def _limits_spec() -> ClassSpec:
    from hypergumbo_core.limits import Limits

    return ClassSpec(
        cls=Limits,
        description=(
            "Known gaps and limitations of this analysis — what was NOT "
            "captured, enabling honest downstream reporting"
        ),
        decorations={
            "failed_files": {
                "description": "Files that failed analysis ({path, reason, analyzer})",
            },
            "skipped_languages": {
                "description": "Languages detected but not analyzed",
            },
            "skipped_passes": {
                "description": "Passes skipped at dispatch time or crashed mid-run, with reasons (a 'crashed: ' prefix marks a contained pass crash)",
            },
            "truncated_files": {
                "description": "Files truncated or skipped due to size",
            },
            "tier_filtered_files": {
                "description": "Files whose symbols/edges the supply-chain tier filter dropped (e.g. DERIVED tier-4 excluded by default) — the 'what' behind max_tier_applied's 'why' (WI-tulit)",
            },
            "partial_results_reason": {
                "description": "Why results are partial, when they are",
            },
            "supply_chain": {
                "description": "Supply chain classification issues",
            },
            "test_files_excluded": {
                "description": "Whether test files were excluded from analysis — always emitted so the state is observable (true when excluded, false otherwise)",
            },
        },
        overrides={
            # Emitted only when set; constraints live inside oneOf branches.
            "max_tier_applied": {
                "oneOf": [{"type": "integer"}, {"type": "null"}],
                "description": "Max supply-chain tier filter applied, when any",
            },
            "max_files_per_analyzer": {
                "oneOf": [{"type": "integer"}, {"type": "null"}],
                "description": "Per-analyzer file cap applied, when any",
            },
        },
        extras={
            "not_captured": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Universal static disclaimer: the fixed categories of "
                    "constructs static analysis never captures anywhere (dynamic "
                    "imports, eval, etc.). Identical across all analyses — NOT a "
                    "per-repo measurement of constructs this repo contains-but-skipped."
                ),
            },
            "analyzer_version": {
                "type": "string",
                "description": "hypergumbo version string that produced this analysis",
            },
        },
        conditional={
            "max_tier_applied", "max_files_per_analyzer",
            # test_files_excluded is now always emitted (WI-miron); partial_results_reason
            # is emitted only when the analysis is incomplete (WI-tamop, spec §960/§994).
            "partial_results_reason",
            # INV-virik: the diagnostic reporting lists are present ONLY when
            # non-empty (present-when-populated). skipped_passes stays always-
            # emitted (the populated provenance surface, INV-nihug).
            "failed_files", "skipped_languages", "truncated_files",
            "tier_filtered_files",
        },
        sample_factory=_limits_sample,
    )


# ---------------------------------------------------------------------------
# Feature / SliceQuery (WI-kutas PR2 — the previously-untyped features[])
# ---------------------------------------------------------------------------

def _slice_query_sample():
    from hypergumbo_core.slice import SliceQuery

    return SliceQuery(
        entrypoint="main",
        max_tier=2,
        language="python",
        hub_threshold=50,
        exclude_imports=True,
        dataflow=True,
    )


def _slice_query_spec() -> ClassSpec:
    from hypergumbo_core.slice import SliceQuery

    return ClassSpec(
        cls=SliceQuery,
        description=(
            "The query that produced a feature slice (subset of SliceQuery "
            "fields that affect the stable feature id)"
        ),
        renames={"max_hops": "hops"},
        omitted={"min_confidence"},
        decorations={
            "entrypoint": {"description": "Symbol name, file path, or node ID the slice starts from"},
            "hops": {"description": "Max traversal depth (null = unlimited)"},
            "max_files": {"description": "Max files included in the slice"},
            "exclude_tests": {"description": "Whether test files were excluded"},
            "exclude_utility": {"description": "Whether utility files were excluded"},
            "method": {"description": "Traversal method (bfs)"},
            "reverse": {"description": "True = callers (backward traversal)"},
            "max_tier": {"description": "Max supply chain tier included, when set"},
            "language": {"description": "Entry-point language filter, when set"},
            "hub_threshold": {"description": "Hub-degree pruning threshold, when set"},
            "exclude_imports": {"description": "Present (true) when import edges were excluded"},
            "pass_through_kinds": {"description": "Node kinds traversed but excluded from output"},
            "dataflow": {"description": "Present (true) for dataflow-constrained slices"},
        },
        conditional={
            "max_tier", "language", "hub_threshold",
            "exclude_imports", "pass_through_kinds", "dataflow",
        },
        sample_factory=_slice_query_sample,
    )


def _feature_sample():
    from hypergumbo_core.slice import SliceResult

    return SliceResult(
        entry_nodes=["n1"],
        node_ids={"n1", "n2"},
        edge_ids={"e1"},
        query=_slice_query_sample(),
        limits_hit=["hop_limit"],
        node_depths={"n1": 0},
        node_tiers={"n1": 1},
        admission_stats={"admitted_writer_src": 1},
    )


def _feature_spec() -> ClassSpec:
    from hypergumbo_core.slice import SliceResult

    return ClassSpec(
        cls=SliceResult,
        description=(
            "A feature slice index entry (spec §9 features[]): IDs + query "
            "+ summary so consumers can discover slices via the behavior "
            "map alone and diff across commits via the stable id"
        ),
        decorations={
            "entry_nodes": {"description": "IDs of the entry point nodes"},
            "node_ids": {"description": "Sorted IDs of all nodes in the slice"},
            "edge_ids": {"description": "Sorted IDs of all edges in the slice"},
            "query": {"description": "Query that produced this slice"},
            "limits_hit": {"description": "Limits reached during traversal (e.g. hop_limit)"},
            "node_depths": {"description": "Node ID → BFS depth, when recorded"},
            "node_tiers": {"description": "Node ID → supply chain tier, when recorded"},
            "admission_stats": {
                "description": "Per-rule dataflow edge-admission counters, when dataflow=True",
            },
        },
        extras={
            "id": {
                "type": "string",
                "description": "Stable feature ID (sha256 of the canonical query JSON)",
            },
            "name": {
                "type": "string",
                "description": "Human-readable feature name (the query entrypoint)",
            },
        },
        conditional={"node_depths", "node_tiers", "admission_stats"},
        sample_factory=_feature_sample,
    )


# ---------------------------------------------------------------------------
# ValidationViolation (WI-kutas PR2 — validation_report items)
# ---------------------------------------------------------------------------

def _validation_violation_sample():
    from hypergumbo_core.spec_validator import ValidationViolation

    return ValidationViolation(
        severity="warning",
        validator_class="cross_field",
        message="sample",
    )


def _validation_violation_spec() -> ClassSpec:
    from hypergumbo_core.spec_validator import ValidationViolation

    return ClassSpec(
        cls=ValidationViolation,
        description=(
            "One structured spec-vs-data mismatch from the end-of-pipeline "
            "validator stage (ADR-0033)"
        ),
        decorations={
            "severity": {
                "enum": ["error", "warning", "info"],
                "description": "Violation severity",
            },
            "validator_class": {
                "description": (
                    "Which validator class emitted this (axis_conformance, "
                    "writer_contract, cross_field, verdict_enum, id_format)"
                ),
            },
            "message": {"description": "Human-readable description for review"},
            "axis": {"description": "Axis name (axis_conformance only)"},
            "field_name": {"description": "Dataclass field name when applicable"},
            "record_id": {
                "description": "Symbol.id / Edge.id / AnalysisRun.execution_id",
            },
            "observed": {"description": "Offending value (stringified)"},
            "expected": {"description": "Short description of what was expected"},
        },
        sample_factory=_validation_violation_sample,
        serializer=lambda instance: dataclasses.asdict(instance),
    )


_SPECS: Dict[str, Callable[[], ClassSpec]] = {
    "Span": _span_spec,
    "Symbol": _symbol_spec,
    "Edge": _edge_spec,
    "AnalysisRun": _analysis_run_spec,
    "FailedFile": _failed_file_spec,
    "ClassificationFailure": _classification_failure_spec,
    "AmbiguousPath": _ambiguous_path_spec,
    "SupplyChainLimits": _supply_chain_limits_spec,
    "Limits": _limits_spec,
    "SliceQuery": _slice_query_spec,
    "Feature": _feature_spec,
    "ValidationViolation": _validation_violation_spec,
}


def spec_for(def_name: str) -> ClassSpec:
    """Return a fresh ClassSpec for one of the introspected $defs."""
    return _SPECS[def_name]()


def build_all_defs() -> Dict[str, Dict[str, Any]]:
    """Build the introspected $defs, drift-guarded and round-trip-verified."""
    defs: Dict[str, Dict[str, Any]] = {}
    for def_name in _SPECS:
        spec = spec_for(def_name)
        def_schema = build_def(spec)
        verify_round_trip(spec, def_schema)
        defs[def_name] = def_schema
    return defs
