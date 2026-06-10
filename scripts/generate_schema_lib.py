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

        if origin is list:
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
    # builds a fully-populated instance for verify_round_trip
    sample_factory: Optional[Callable[[], Any]] = None


def build_def(spec: ClassSpec) -> Dict[str, Any]:
    """Build one $def from introspected structure + declared decoration."""
    hints = typing.get_type_hints(spec.cls)
    properties: Dict[str, Any] = {}
    required: List[str] = []
    composite_placed: Set[str] = set()

    field_names = {fld.name for fld in dataclasses.fields(spec.cls)}
    for f in dataclasses.fields(spec.cls):
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
    if spec.sample_factory is None:  # pragma: no cover — all four specs declare one
        return
    instance = spec.sample_factory()
    dict_keys = set(instance.to_dict())
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
    return Symbol(
        id="python:a.py:1-2:f:function",
        name="f",
        kind="function",
        language="python",
        path="a.py",
        span=_sample_span(),
        origin=["python"],
        origin_run_id="uuid:sample",
    )


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
        evidence_spans=[{"line": 1}],
        dst_ref=ExternalRef(lang="python", module_path="os", name="getcwd"),
        derived_from=["sym:1"],
    )


def _sample_analysis_run() -> AnalysisRun:
    return AnalysisRun.create(pass_id="python", version="0.0.0")


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
            "lines_of_code": {
                "oneOf": [{"type": "integer", "minimum": 1}, {"type": "null"}],
                "description": "Number of source lines in the symbol body",
            },
        },
        decorations={
            "id": {"description": "Unique identifier within analysis"},
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
                    "INV-bazij): survives body edits, NOT rename or move"
                ),
            },
            "shape_id": {"description": "Structural implementation fingerprint"},
            "fingerprint": {"description": "Content hash of source"},
            "quality": {"description": "Quality assessment"},
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
        },
        sample_factory=_sample_symbol,
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
            "evidence_spans": "meta",
            "meta": "meta",
        },
        composites={
            "meta": {
                "type": "object",
                "description": "Edge metadata including evidence",
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
                    "evidence_spans": {"type": "array"},
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
                "description": "Confidence score",
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
            "quality": {"description": "Quality assessment"},
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
                "description": "Passes skipped at dispatch time, with reasons",
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
        },
        sample_factory=_sample_analysis_run,
    )


_SPECS: Dict[str, Callable[[], ClassSpec]] = {
    "Span": _span_spec,
    "Symbol": _symbol_spec,
    "Edge": _edge_spec,
    "AnalysisRun": _analysis_run_spec,
}


def spec_for(def_name: str) -> ClassSpec:
    """Return a fresh ClassSpec for one of the introspected $defs."""
    return _SPECS[def_name]()


def build_all_defs() -> Dict[str, Dict[str, Any]]:
    """Build the four introspected $defs, drift-guarded and round-trip-verified."""
    defs: Dict[str, Dict[str, Any]] = {}
    for def_name in ("Span", "Symbol", "Edge", "AnalysisRun"):
        spec = spec_for(def_name)
        def_schema = build_def(spec)
        verify_round_trip(spec, def_schema)
        defs[def_name] = def_schema
    return defs
