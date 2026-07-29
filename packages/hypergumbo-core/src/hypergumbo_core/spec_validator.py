# SPDX-License-Identifier: AGPL-3.0-or-later
"""Spec-vs-data validator stage (ADR-0033, INV-sugat).

Runtime counterpart to ``multi_value_field_axis.py``'s static-AST validator.
The static walker checks source code — that every ``str``-typed dataclass
field carries a ``# axis: <category>`` annotation. This module checks the
*emitted IR at runtime* — that every value populated into those axis-tagged
fields conforms to the axis the annotation declares.

The pipeline (``cli.run_behavior_map``) constructs ``Symbol`` / ``Edge`` /
``AnalysisRun`` records through ordered passes (analyzers → linkers →
fingerprint stamping → supply-chain classification → boundary synthesis →
route promotion → tier filtering → metrics → write). Each pass is a writer;
no pass is a reader. Until this module, nothing read the emitted records
and checked them against the schema axioms, axis catalogs, or producer
contracts. That gap is INV-sugat — "no spec-vs-data validator stage exists
in the pipeline" — the super-META this ADR closes.

The validator classes (ADR-0033 §"Validator classes") shipped incrementally:
the Phase-0 module landed the scaffolding (the ``ValidationViolation``
dataclass, the public ``validate_ir`` entry point, the pipeline wire-up at
``cli.py``'s end-of-pipeline post-pass slot) with every class off, and the
campaign's Phase-3/5/6 PRs then turned each on. All of the classes below are
now ACTIVE — ``validate_ir`` runs each, and the corpus surfaces real
violations (see "The gate's realized form" below for how the CI ratchet
holds those counts shrink-only):

1. **Axis-conformance** — every axis-tagged ``str`` field's values must be
   in the catalog (union ``{None}`` for ``Optional`` fields). The four
   ADR-0024 categories interpret differently at runtime:
   *known-axis-name* uses the registry accessor; *identity* is a
   uniqueness invariant; *bounded-enum* is a fixed-list check;
   *free-text* skips value-level checks (the justification was the gate
   at source-write time).
2. **Writer-contract** — for each ``(producer-class, axis-tagged field)``
   pair, verify records from that producer populate the field. Generalizes
   the four sub-patterns in INV-luhur's META description and folds in
   WI-rolol sub-task B.
3. **Cross-field coherence** — documented field-pair invariants:
   ``Edge.dst_ref ↔ Edge.dst``, ``Symbol.language is None ↔
   Symbol.protocol_origin is not None`` for synthetic stand-ins,
   ``Symbol.display_label`` populated on synthetic stand-ins only. The
   ``cross_field`` class also carries several other wired checks: the
   ADR-0037 ``is_resolved ↔ dst`` FK, ``origin_run_id`` FK integrity,
   dangling-endpoint detection, edge ``confidence`` range, route-marker
   single-home, and the receiver-blind-magnet gate.
4. **Verdict-enum completeness** — verdict-emitting code paths must enumerate
   an ``inconclusive`` (or equivalent) branch for missing-data cases.
   Folds in WI-rolol sub-task A (``ClaimVerdict.inconclusive``).
5. **ID-format conformance** — every ``Symbol.id`` matches the canonical
   ``<language>:<path>:<start>-<end>:<name>:<kind>`` schema with single-colon
   separators. Lands in Phase 5 PR1 alongside the INV-sadiv six-site migration
   to ``make_symbol_id(...)``. Phase 6 PR1 (INV-hunup closure) extends the
   same ``id_format`` validator class with a ``Symbol.stable_id`` sub-check
   pinning the canonical ``sha256:<16hex>`` shape that every
   ``make_*_stable_id`` factory produces. See ADR-0034 for the discipline
   rationale. id-format:F3 (``_check_id_roundtrip``) extends the class once
   more with the ADR-0036 Ruling-2 *round-trip* canary: the id kind-slot must
   be a registered symbol-kind and equal ``Symbol.kind`` (advisory ``warning``
   until the v6 kind-slot folds clear the WI-pubiv/WI-kugaj/tsconfig backlog),
   the name-slot must be non-empty (advisory), and the span must satisfy
   ``start <= end`` (``error``).

Why scaffold first
------------------
The stage was landed with no checks enabled first (Phase 0), by design. The
module's presence and the pipeline wire-up established (a) the artifact's
``validation_report`` section as a stable surface that consumers and CI
can rely on, (b) the ``ValidationViolation`` dataclass as the structured
shape that every check class emits, and (c) the warn-not-fail default
behavior the campaign committed to. Subsequent PRs then turned on one check
class each on that already-smoke-tested wire-up; all five are now live.

Default failure behavior
------------------------
The validator does NOT fail ``hypergumbo run`` by default. Violations are:

* Written into the artifact's ``validation_report`` section.
* Summarized to stderr, one line per non-empty validator class
  (``"[warn] N <class> validation violation(s); see validation_report in
  artifact"``).
* CI-gated by a separate test (``tests/test_validation_report_empty.py``).

The gate's realized form (validator:F1 / G1, WI-kafar + WI-himoj). The
file is named "…_empty" for its aspiration, but the corpus is not at zero
violations and an "assert empty" gate would be permanently red, so the
gate is a SHRINK-ONLY per-substrate RATCHET over a four-substrate matrix
(default / ``--frameworks all`` / ``--include-docs`` / ``--max-tier 4``)
run against the multi-language ``schema-coverage-corpus`` fixture tree.
Each substrate's violation count may shrink below its committed baseline
(ratchet it down when it does) but never grow — a regression that adds a
violation trips CI. ``--include-docs`` exercises the flag-gated writer
paths a default-only gate would let escape (WI-himoj). The gate also
co-ratchets the ADR-0023 §3 ``runtime_coherence`` offender count per
substrate as a separate dimension. The heavy self-analysis variant of the
same matrix is future work for full-suite (the strategy's "fixture corpus
per-PR; self-tree in full-suite").

This soft-introduction posture lets the validator land cleanly: users see
violations as informational warnings; CI catches regressions; downstream
producer fixes ratchet each substrate's baseline toward zero.

WI-niluv denominator disclosure: the cross-field collision/degeneracy
umbrellas both disclose their keyless cohort so a clean non-null rate can
never silently hide a large no-key cohort, but they treat the denominator
oppositely. The stable_id collision umbrella INCLUDES the no-stable_id cohort
in its denominator (the rate is over ALL Symbols) and discloses the count as
``none_cohort=N/population``. The fingerprint degeneracy twin keeps a non-null
denominator (a rate is undefined without a key) and discloses
``denominator_scope=non_null`` plus the excluded cohort. Either way the
encoding is biconditional.

See ADR-0033 for the full architectural decision.
"""
from __future__ import annotations

import re
import sys
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable, Optional

from .receiver_blind_magnets import find_harmful_magnets

VALIDATION_REPORT_SCHEMA_VERSION = "0.3"  # 0.3: validator:F2 (WI-moriz) added wired_checks disclosure; 0.2: ADR-0035 §5 added stable_id_stats

# Stable enum-like sets. Mirrored in the ADR-0033 §"Output format" table
# and in `ValidationViolation.severity` / `validator_class` axis annotations.
_SEVERITIES = ("error", "warning", "info")
_VALIDATOR_CLASSES = (
    "axis_conformance",
    "writer_contract",
    "cross_field",
    "verdict_enum",
    "id_format",
)

# validator:F2 (WI-moriz) — the wired-checks disclosure manifest.
#
# A class count of 0 in ``violations_by_class`` is ambiguous between "the
# substrate is clean on that dimension" and "no wired predicate covers that
# dimension" — and the counter alone cannot tell them apart. That ambiguity
# IS the WI-moriz false-all-clear: a reader treats 0 as a clean bill of health
# when whole defect classes may simply be unchecked. ``build_validation_report``
# embeds this manifest so a consumer can map each class count to the named
# predicates that produced it; an un-listed defect class is, by absence, NOT
# yet validated. The ``check`` stems here are pinned to the ``_check_*``
# functions actually wired into ``validate_ir`` by
# ``test_wired_checks_manifest_matches_validate_ir`` — you cannot wire a new
# check without disclosing it, nor disclose one you did not wire, so the
# manifest can never drift back into a silent false-all-clear.
_WIRED_CHECKS: tuple[dict[str, str], ...] = (
    {"check": "axis_conformance", "validator_class": "axis_conformance",
     "description": "Every axis-tagged Symbol/Edge/AnalysisRun field value is "
                    "in its registry/catalog (ADR-0024)."},
    {"check": "writer_contract", "validator_class": "writer_contract",
     "description": "Declared-and-writable fields are populated, not left at a "
                    "default sentinel across the whole corpus."},
    {"check": "cross_field_coherence", "validator_class": "cross_field",
     "description": "Field-pair invariants: Class-B stamping canary, ADR-0031 "
                    "language/protocol_origin, ADR-0032 display_label scope, "
                    "ADR-0037 is_resolved<->dst FK, dst_ref<->dst back-compat, "
                    "stable_id collision + fingerprint degeneracy umbrellas."},
    {"check": "stable_id_per_file_uniqueness", "validator_class": "cross_field",
     "description": "No two symbols in the same file share a stable_id "
                    "(ADR-0035 §5, zero-tolerance error)."},
    {"check": "verdict_enum_completeness", "validator_class": "verdict_enum",
     "description": "ClaimVerdict enum completeness."},
    {"check": "id_format", "validator_class": "id_format",
     "description": "Symbol.id matches the canonical lang:path:span:name:kind "
                    "grammar."},
    {"check": "stable_id_format", "validator_class": "id_format",
     "description": "Symbol.stable_id matches the sha256:<16hex> scheme."},
    {"check": "id_roundtrip", "validator_class": "id_format",
     "description": "Symbol.id kind-slot matches Symbol.kind and is a "
                    "registered kind; span/name well-formed."},
    {"check": "origin_run_id_fk", "validator_class": "cross_field",
     "description": "Symbol/Edge.origin_run_id is non-empty and references an "
                    "existing AnalysisRun.execution_id (content-gated on a "
                    "non-empty run set; WI-mosil + synthetic:F1 regression "
                    "guard)."},
    {"check": "dangling_endpoint", "validator_class": "cross_field",
     "description": "Every non-empty Edge.src/Edge.dst references a node id "
                    "present in the symbol set (content-gated on a non-empty "
                    "run set; WI-mujor endpoint-integrity guard — the dst-absent "
                    "half deferred from the ADR-0037 is_resolved<->dst FK)."},
    {"check": "fingerprint_format", "validator_class": "id_format",
     "description": "Every non-null Symbol.fingerprint on a real source node "
                    "(language is not None) carries the canonical hgfp2: scheme "
                    "prefix (content-gated on a non-empty run set; WI-vudul "
                    "output-boundary format guard — Class-B language=None "
                    "identity-hash stand-ins are exempt)."},
    {"check": "confidence_range", "validator_class": "cross_field",
     "description": "Edge.confidence sits within the derived [low, "
                    "base_confidence] band for its evidence_type — a WI-nurun "
                    "step-4 forward regression guard against off-band / "
                    "reserved-ceiling (1.0) per-emitter values (advisory info; "
                    "unregistered/unseeded pathways carry no band)."},
    {"check": "route_marker_single_home", "validator_class": "cross_field",
     "description": "An ADR-0027 route marker (meta.framework_role=='route') "
                    "carries no redundant path-less concept=route alongside it "
                    "(INV-vokak dual-carry root; the framework belongs on "
                    "route_framework, not orphaned in a second home)."},
    {"check": "no_harmful_receiver_blind_magnets", "validator_class": "cross_field",
     "description": "No un-demoted CLEANLY-harmful receiver-blind method magnet "
                    "survives finalization (INV-fahub): a high-confidence calls "
                    "edge that bound an unresolvable-receiver call to an arbitrary "
                    "same-named internal method that is a production->test-helper "
                    "misbind or a stdlib-interface-method shadow. finalize's "
                    "demotion sub-step redirects each to external; a survivor is a "
                    "demotion-ordering/coverage regression. The correct-but-"
                    "unprovable trait-dispatch residual is ADR-0012 scope and is "
                    "NOT flagged."},
)


@dataclass(frozen=True)
class ValidationViolation:
    """One structured record of a spec-vs-data mismatch.

    The fields mirror ADR-0033 §"Output format" exactly. Frozen because
    a violation is observation data, not a mutable workspace object:
    once the validator emits one, it should not be edited downstream
    (rewrite the validator if the observation is wrong).
    """

    severity: str  # axis: bounded-enum {"error", "warning", "info"}
    validator_class: str  # axis: bounded-enum {"axis_conformance", "writer_contract", "cross_field", "verdict_enum", "id_format"}
    message: str  # axis: free-text — human-readable description for review
    axis: Optional[str] = None  # axis: free-text — axis name (axis_conformance only)
    field_name: Optional[str] = None  # axis: free-text — dataclass field name when applicable
    record_id: Optional[str] = None  # axis: free-text — Symbol.id / Edge.id / AnalysisRun.execution_id
    observed: Optional[str] = None  # axis: free-text — offending value (stringified)
    expected: Optional[str] = None  # axis: free-text — short description of what was expected


def validate_ir(
    symbols: Iterable[Any],
    edges: Iterable[Any],
    analysis_runs: Iterable[Any],
) -> list[ValidationViolation]:
    """Run all enabled validator classes against the emitted IR.

    Each Phase-3 PR adds one class's checks to this function. The
    argument types are deliberately ``Iterable[Any]`` rather than typed
    `Symbol`/`Edge`/`AnalysisRun` to keep this module decoupled from
    the IR module — `validate_ir` reads attributes by name and is
    therefore tolerant of dataclass-shape evolution.
    """
    violations: list[ValidationViolation] = []
    # The argument may be a one-shot iterable; the writer-contract
    # validator below scans the same lists multiple times, so we
    # materialise once. (Axis-conformance also benefits but it's
    # single-pass — materialising upstream is the safer composition.)
    symbols = list(symbols)
    edges = list(edges)
    analysis_runs = list(analysis_runs)
    violations.extend(_check_axis_conformance(symbols, edges, analysis_runs))
    violations.extend(_check_writer_contract(symbols, edges, analysis_runs))
    violations.extend(_check_cross_field_coherence(symbols, edges, analysis_runs))
    violations.extend(_check_stable_id_per_file_uniqueness(symbols))
    violations.extend(_check_verdict_enum_completeness())
    violations.extend(_check_id_format(symbols))
    violations.extend(_check_stable_id_format(symbols))
    violations.extend(_check_id_roundtrip(symbols))
    violations.extend(_check_origin_run_id_fk(symbols, edges, analysis_runs))
    violations.extend(_check_dangling_endpoint(symbols, edges, analysis_runs))
    violations.extend(_check_fingerprint_format(symbols, edges, analysis_runs))
    violations.extend(_check_confidence_range(edges))
    violations.extend(_check_route_marker_single_home(symbols))
    violations.extend(_check_no_harmful_receiver_blind_magnets(symbols, edges))
    return violations


def _check_route_marker_single_home(
    symbols: Iterable[Any],
) -> list[ValidationViolation]:
    """INV-vokak: a route symbol records its route fact in exactly ONE home.

    A symbol carrying the ADR-0027 route marker (``meta['framework_role'] ==
    'route'``) must not ALSO carry a redundant *path-less* ``concept ==
    'route'`` entry in ``meta['concepts']``. That dual-carry state orphans the
    concept's framework from the marker — ``routes.route_of`` merely tolerates
    it by unioning the framework at read time (WI-tosul Phase-1b-alpha), but
    the emitted data is incoherent (one route fact in two homes). The
    producer-side fix (``framework_patterns._dedup_route_marker_concepts``)
    lifts such a concept's framework onto the marker's ``route_framework`` home
    and drops the concept; this predicate is the standing corpus-wide guard
    that the fix holds — a regression here, or a new route producer that
    re-introduces the shape, re-fires it and the ratchet gate blocks.
    """
    violations: list[ValidationViolation] = []
    for sym in symbols:
        meta = getattr(sym, "meta", None) or {}
        if meta.get("framework_role") != "route":
            continue
        for concept in meta.get("concepts", []) or []:
            if (
                isinstance(concept, dict)
                and concept.get("concept") == "route"
                and not concept.get("path")
            ):
                violations.append(ValidationViolation(
                    severity="error",
                    validator_class="cross_field",
                    message=(
                        "route symbol carries a redundant path-less "
                        "concept=route alongside its framework_role=='route' "
                        "marker (INV-vokak dual-carry): the route fact must "
                        "live in one home and the framework belongs on "
                        "route_framework"
                    ),
                    field_name="meta.concepts",
                    record_id=getattr(sym, "id", None),
                    observed=str(concept.get("framework")),
                    expected="single-homed route marker (no redundant concept)",
                ))
                break
    return violations


def _check_no_harmful_receiver_blind_magnets(
    symbols: Iterable[Any], edges: Iterable[Any],
) -> list[ValidationViolation]:
    """INV-fahub: no un-demoted CLEANLY-harmful receiver-blind magnet survives.

    A receiver-blind magnet is a high-confidence ``calls`` edge that bound an
    unresolvable-receiver call to an arbitrary same-named internal ``method``
    (``d.Val()`` → an unrelated ``Dispenser.Val``). The finalize demotion
    sub-step (6c) redirects the two cleanly-harmful sub-classes — a
    production→test-helper misbind and a stdlib-interface-method shadow — to an
    ``external:unresolved`` id BEFORE the ADR-0037 edge-resolution verdict, so on
    the finalized graph ``find_harmful_magnets`` should return nothing. A survivor
    means the demotion ran out of order, a new producer path emitted one the pass
    missed, or the shared detector has a gap — this corpus-wide gate is the
    standing durable teeth that keeps the flip honest (it fires and the ratchet
    blocks). The refined criterion is deliberate: the correct-but-unprovable
    trait-dispatch residual (Rust ``x.next()`` → ``Red::next``) needs real type
    resolution (ADR-0012) and is a KEPT bind, so it is NOT flagged here.
    """
    violations: list[ValidationViolation] = []
    symbols = list(symbols)
    edges = list(edges)
    for edge in find_harmful_magnets(symbols, edges):
        violations.append(ValidationViolation(
            severity="error",
            validator_class="cross_field",
            message=(
                "harmful receiver-blind method magnet survived finalization "
                "(INV-fahub): an unresolvable-receiver call bound at high "
                "confidence to an arbitrary internal method that is a "
                "test-helper misbind or a stdlib-interface shadow; the finalize "
                "demotion sub-step should have redirected it to external"
            ),
            field_name="dst",
            record_id=getattr(edge, "id", None),
            observed=str(getattr(edge, "dst", None)),
            expected="external:unresolved (harmful magnet demoted at finalize)",
        ))
    return violations


def _check_confidence_range(edges: Iterable[Any]) -> list[ValidationViolation]:
    """WI-nurun step 4: advisory range-validation of `Edge.confidence`.

    Per ADR-0039, an analyzer edge's confidence is *derived* from its
    inference pathway (`evidence_type`). This check flags any edge whose
    confidence falls outside the derived ``[low, base_confidence]`` band for
    its pathway — a per-emitter value that no longer tracks the pathway (a
    derivation regression, or an over-claim such as the reserved-ceiling 1.0).
    It is a forward regression guard: the post-migration corpus is fully
    in-band (0 violations), and edges whose evidence_type is unregistered or
    not-yet-seeded carry no band (treated in-band). Emitted as advisory
    ``info`` under the existing ``cross_field`` class (confidence cohering with
    evidence_type is a cross-field property).
    """
    from hypergumbo_core.confidence import confidence_within_band
    from hypergumbo_core.evidence_types import find_evidence_type

    violations: list[ValidationViolation] = []
    for edge in edges:
        ev = getattr(edge, "evidence_type", None)
        conf = getattr(edge, "confidence", None)
        if ev is None or conf is None:
            continue
        if confidence_within_band(ev, conf):
            continue
        spec = find_evidence_type(ev)
        # find_evidence_type is non-None here: confidence_within_band only
        # returns False for a seeded (hence registered) pathway.
        assert spec is not None
        low = (
            spec.base_confidence_unresolved
            if spec.base_confidence_unresolved is not None
            else 0.30
        )
        violations.append(ValidationViolation(
            severity="info",
            validator_class="cross_field",
            message=(
                f"Edge.confidence {conf} for evidence_type '{ev}' is outside "
                f"the derived band [{low}, {spec.base_confidence}] — the "
                f"per-emitter value no longer tracks the inference pathway "
                f"(WI-nurun range validation)."
            ),
            field_name="confidence",
            record_id=getattr(edge, "id", None),
            observed=str(conf),
            expected=f"[{low}, {spec.base_confidence}]",
        ))
    return violations


# ----------------------------------------------------------------------
# Phase 3 PR1 — Axis-conformance validator class
# ----------------------------------------------------------------------
#
# For every (record_class, field, axis) tuple in _AXIS_TAGGED_FIELDS,
# verify the emitted value is in the axis catalog (or None for Optional
# fields). The (record, field, axis) mapping is hand-maintained: when
# a new axis-tagged field lands on Symbol / Edge / AnalysisRun in
# ir.py, the developer adding it MUST extend this table — the same
# forcing function the static-AST validator at
# multi_value_field_axis.py has at PR-review time.
#
# Categories from ADR-0024 are NOT all checked here:
#   - "identity" — uniqueness invariant; checked by cross-field
#     coherence (Phase 3 PR3) or by ID-format validator (Phase 5 PR1).
#   - "bounded-enum" — small fixed list; checked here ad-hoc when the
#     field's enum is documented in this module's bounded-enum dict.
#   - "free-text" — no value check (the justification was the gate at
#     source-write time).
#   - "qualified-name" — structural per-language separator check; the
#     axis catalog returns the set of LANGUAGES with declared
#     separators (not a value set). Verified separately: the value
#     must use the separator declared for the Symbol's language.
#
# The "edge_lang" / "discovery_language" fields share the language
# axis with Symbol.language. The "origin" lists carry pass-id values
# per element; that's a per-element membership check.

_BOUNDED_ENUMS: dict[tuple[str, str], frozenset[str]] = {
    # (record_class, field) -> legal value set documented in the
    # dataclass docstring. Mirrors ADR-0024 "bounded-enum" category.
    # ADR-0039 ruling 2: Edge.confidence_source provenance discriminator.
    # Kept in lockstep with ir.VALID_CONFIDENCE_SOURCES by
    # test_spec_validator's drift guard.
    ("Edge", "confidence_source"): frozenset({
        "evidence_derived", "emitter_constant", "composite",
    }),
}


def _check_axis_conformance(
    symbols: Iterable[Any],
    edges: Iterable[Any],
    analysis_runs: Iterable[Any],
) -> list[ValidationViolation]:
    """Axis-conformance class: every axis-tagged value in the catalog.

    See ADR-0033 §"Validator classes" #1.
    """
    # Deferred imports keep this module importable without the catalog
    # machinery initialised (mirrors multi_value_field_axis._known_axes).
    from .catalog import all_known_languages, all_known_pass_ids
    from .edge_types import all_edge_type_names
    from .evidence_types import all_evidence_type_names
    from .protocol_origins import all_protocol_origin_names
    from .qualified_name_axis import separator_for_language
    from .symbol_kinds import all_symbol_kind_names

    languages = all_known_languages()
    pass_ids = all_known_pass_ids()
    symbol_kinds = all_symbol_kind_names()
    evidence_types = all_evidence_type_names()
    edge_types = all_edge_type_names()
    protocol_origins = all_protocol_origin_names()

    violations: list[ValidationViolation] = []

    # ---- Symbol-side checks ----
    for sym in symbols:
        sym_id = getattr(sym, "id", None)
        violations.extend(_check_value(
            sym_id, "Symbol.kind", "symbol-kind",
            getattr(sym, "kind", None), symbol_kinds, allow_none=False,
        ))
        violations.extend(_check_value(
            sym_id, "Symbol.language", "language",
            getattr(sym, "language", None), languages, allow_none=True,
        ))
        violations.extend(_check_value(
            sym_id, "Symbol.discovery_language", "language",
            getattr(sym, "discovery_language", None), languages,
            allow_none=True,
        ))
        violations.extend(_check_value(
            sym_id, "Symbol.protocol_origin", "protocol-origin",
            getattr(sym, "protocol_origin", None), protocol_origins,
            allow_none=True,
        ))
        for v in _check_list(
            sym_id, "Symbol.origin", "pass-id",
            getattr(sym, "origin", None), pass_ids,
        ):
            violations.append(v)
        # qualified-name structural check: when populated, value must
        # use the language's declared separator.
        violations.extend(_check_qualified_name_separator(
            sym_id,
            getattr(sym, "qualified_name", None),
            getattr(sym, "language", None),
            separator_for_language,
        ))

    # ---- Edge-side checks ----
    for edge in edges:
        edge_id = getattr(edge, "id", None)
        violations.extend(_check_value(
            edge_id, "Edge.edge_type", "edge-type",
            getattr(edge, "edge_type", None), edge_types, allow_none=False,
        ))
        violations.extend(_check_value(
            edge_id, "Edge.evidence_type", "evidence-type",
            getattr(edge, "evidence_type", None), evidence_types,
            allow_none=False,
        ))
        violations.extend(_check_value(
            edge_id, "Edge.evidence_lang", "language",
            getattr(edge, "evidence_lang", None), languages,
            allow_none=True,
        ))
        violations.extend(_check_value(
            edge_id, "Edge.confidence_source", "bounded-enum",
            getattr(edge, "confidence_source", None),
            _BOUNDED_ENUMS[("Edge", "confidence_source")], allow_none=False,
        ))
        for v in _check_list(
            edge_id, "Edge.origin", "pass-id",
            getattr(edge, "origin", None), pass_ids,
        ):
            violations.append(v)

    # ---- AnalysisRun-side checks ----
    # AnalysisRuns reach validate_ir as dicts (cli.py accumulates
    # ``linker_result.run.to_dict()`` rather than the dataclass), so
    # use ``_read`` to handle both shapes. The serialized form names
    # ``pass_id`` as ``"pass"`` (ir.py:to_dict line 275) — check both.
    for run in analysis_runs:
        run_id = _read(run, "execution_id", None)
        pass_id_value = _read(run, "pass_id", None)
        if pass_id_value is None:
            pass_id_value = _read(run, "pass", None)
        violations.extend(_check_value(
            run_id, "AnalysisRun.pass_id", "pass-id",
            pass_id_value, pass_ids, allow_none=False,
        ))

    return violations


def _read(obj: Any, attr: str, default: Any = None) -> Any:
    """Read an attribute or dict-key from a record, whichever shape it has.

    AnalysisRuns frequently reach the validator as already-serialized
    dicts (the orchestrator at ``cli.py`` accumulates
    ``run.to_dict()`` instances). Symbols and Edges typically reach
    as dataclass instances. This helper lets the same axis-conformance
    code branch handle both without per-call ``isinstance`` checks.
    """
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _check_value(
    record_id: Optional[str],
    field_name: str,
    axis: str,
    observed: Optional[str],
    catalog: frozenset[str] | set[str],
    *,
    allow_none: bool,
) -> list[ValidationViolation]:
    """Single-value membership check. Empty list = pass.

    Allowance rules:
    - allow_none=True + observed=None → pass (Optional field, not populated).
    - allow_none=False + observed=None → violation (required field missing).
    - observed in catalog → pass.
    - observed not in catalog → violation.
    """
    if observed is None:
        if allow_none:
            return []
        return [ValidationViolation(
            severity="error",
            validator_class="axis_conformance",
            axis=axis,
            field_name=field_name,
            record_id=record_id,
            observed=None,
            expected=f"non-None value in {axis} catalog",
            message=(
                f"{field_name} on record {record_id!r} is None but the "
                f"axis ({axis}) is non-Optional in the IR spec."
            ),
        )]
    if observed in catalog:
        return []
    return [ValidationViolation(
        severity="error",
        validator_class="axis_conformance",
        axis=axis,
        field_name=field_name,
        record_id=record_id,
        observed=observed,
        expected=f"value in {axis} catalog ({len(catalog)} known values)",
        message=(
            f"{field_name} on record {record_id!r} has value "
            f"{observed!r} which is not in the {axis} catalog."
        ),
    )]


def _check_list(
    record_id: Optional[str],
    field_name: str,
    axis: str,
    observed: Optional[Iterable[str]],
    catalog: frozenset[str] | set[str],
) -> list[ValidationViolation]:
    """Per-element membership check for list-of-str fields (e.g., origin)."""
    if observed is None:
        return []
    violations: list[ValidationViolation] = []
    for elem in observed:
        if elem not in catalog:
            violations.append(ValidationViolation(
                severity="error",
                validator_class="axis_conformance",
                axis=axis,
                field_name=field_name,
                record_id=record_id,
                observed=elem,
                expected=(
                    f"each list element in {axis} catalog "
                    f"({len(catalog)} known values)"
                ),
                message=(
                    f"{field_name} on record {record_id!r} contains "
                    f"element {elem!r} not in the {axis} catalog."
                ),
            ))
    return violations


def _check_qualified_name_separator(
    record_id: Optional[str],
    qualified_name: Optional[str],
    language: Optional[str],
    separator_for_language,  # callable: str -> Optional[str]
) -> list[ValidationViolation]:
    """Structural check: ``qualified_name`` uses the language's separator.

    ADR-0032 §"Per-language separator policy". When both fields are
    populated, the qualified_name must contain the separator declared
    for that language (or be unqualified — a single segment is legal).
    """
    if qualified_name is None or language is None:
        return []
    separator = separator_for_language(language)
    if separator is None:
        # No declared policy yet for this language; can't check format.
        return []
    # Reject the WRONG separators only — finding the right separator
    # in the value is a sufficient condition for conformance, but a
    # value with no separator at all is also legal (unqualified name).
    wrong = {".", "::", "\\"} - {separator}
    for w in wrong:
        if w in qualified_name and separator not in qualified_name:
            return [ValidationViolation(
                severity="warning",
                validator_class="axis_conformance",
                axis="qualified-name",
                field_name="Symbol.qualified_name",
                record_id=record_id,
                observed=qualified_name,
                expected=(
                    f"separator {separator!r} for language {language!r} "
                    f"(per qualified_name_axis policy)"
                ),
                message=(
                    f"Symbol {record_id!r} has qualified_name={qualified_name!r} "
                    f"with separator {w!r} but the policy for language "
                    f"{language!r} declares separator {separator!r}."
                ),
            )]
    return []


# ----------------------------------------------------------------------
# Phase 3 PR2 — Writer-contract validator class (folds in WI-rolol sub-task B)
# ----------------------------------------------------------------------
#
# Per ADR-0033 §"Validator classes" #2 and INV-luhur META, four
# symptom sub-patterns of the writer-contract gap are checked:
#
#   1. **Schema-declares-no-writer.** Field declared on the dataclass +
#      serialized via to_dict(), but no producer populates it — value
#      stays at default across every record.
#   2. **Default-only initializer.** Writer populates with a default-
#      constant (e.g., AnalysisRun.config_fingerprint defaulting to
#      `sha256(b'{}')`), so every record carries the same low-entropy
#      value when the field is supposed to be evidence-derived.
#   3. **Same-name two-definitions.** Two metrics with the same name
#      disagree across serialization paths (e.g., `total_io_edges`
#      meaning `tagged_count` in one place and `sum(len(.chains))` in
#      another). Cross-checked via a small explicit allowlist of
#      "names that should agree."
#   4. **Writer-writes-constant.** Schema reserves an evidence-derived
#      slot but the writer ships a constant. Detected like #2 — single-
#      value-across-corpus signature — but flagged with a different
#      severity since the field IS populated.
#
# Phase 3 PR2 lands the framework + ONE concrete sub-pattern-2 check
# (AnalysisRun.config_fingerprint constant-default detection). Each of
# the 10 INV-luhur member items closes via a downstream PR that adds
# its specific assertion to the table below (per WI-rolol sub-task B's
# trial procedure). The validator framework is the gate; each writer-
# side fix is its own small per-producer PR.


# (record_class_name, field_name) -> default-sentinel callable.
# When ALL records of that class have the field set to the sentinel
# value, the writer-contract validator flags it as a "default-only
# initializer" sub-pattern-2 violation.
def _all_runs_default_config_fingerprint() -> str:
    """Compute the literal default config_fingerprint at runtime.

    Mirrors `ir._default_config_fingerprint` exactly. Computed lazily so
    this module stays decoupled from ir.py at import time.
    """
    from .ir import _default_config_fingerprint

    return _default_config_fingerprint()


# Each entry: (record_class_name, field_name) -> lazy-sentinel callable.
_WRITER_CONTRACT_DEFAULT_SENTINELS: dict[
    tuple[str, str],
    "Callable[[], str]",
] = {
    ("AnalysisRun", "config_fingerprint"):
        _all_runs_default_config_fingerprint,
}


# Phase 6 PR2 sub-pattern-1 "schema-declares-no-writer" table.
#
# Each entry: (record_class_name, field_name, getter, expected_message).
# When ALL records of that class have the field empty/None/default-list,
# the validator emits a single umbrella violation. ``getter`` is a
# callable that returns the field's truthy presence (False = empty).
# Distinct from sub-pattern-2: this is about a field NEVER being
# populated, not about being populated to a literal default value.
def _is_truthy(record: Any, field_name: str) -> bool:
    val = _read(record, field_name, None)
    if val is None:
        return False
    if isinstance(val, (list, dict, str)):
        return len(val) > 0
    return True


# (record_class_name, field_name, sub-pattern-1 contract description)
#
# WI-lonoz (Edge.quality) is closed at construction time by
# ``Edge.__post_init__`` (ir.py): ``quality`` is auto-derived from
# evidence signals when the producer doesn't set it. The Phase 6 PR2
# closure left this table EMPTY because every previously-unpopulated
# field on the corpus is now wired by a producer; future
# sub-pattern-1 candidates register here.
_WRITER_CONTRACT_NEVER_POPULATED: tuple[tuple[str, str, str], ...] = ()


# Phase 6 PR2 sub-pattern-3 "same-name two-definitions" cross-checks
# (documentation-only).
#
# The two members closed in Phase 6 PR2 (INV-pubom: total_io_edges;
# INV-mozaf: total_files) were resolved at the *producer* layer by
# codifying ONE canonical definition for each name and updating every
# write site to use it. The validator does not run a runtime
# cross-check because the boundary/metrics-builder layer doesn't have
# a record stream to inspect — but the contract is recorded here:
#
# - total_io_edges canonical = sum(len(e.chains) for k, e in entries.items()
#   if k != "external_potential") — the real/verified I/O surface, EXCLUDING
#   the external_potential bucket (disclosed separately as
#   external_potential_edges). INV-pubom amended 2026-06-30 (WI-huhit/WI-foduh);
#   see io_boundary.py.
# - total_files canonical = len({n.path for n in nodes if n.path})
#   (node-distinct-path count); see metrics.py:compute_metrics.


def _check_writer_contract(
    symbols: Iterable[Any],
    edges: Iterable[Any],
    analysis_runs: Iterable[Any],
) -> list[ValidationViolation]:
    """Writer-contract class: writers populate axis-tagged fields with
    evidence-derived values, not initialization defaults.

    See ADR-0033 §"Validator classes" #2 and INV-luhur META.

    Phase 3 PR2 scope: sub-pattern 2 (default-only initializer) for
    ``AnalysisRun.config_fingerprint``.

    Phase 6 PR2 extension: sub-pattern 1 (schema-declares-no-writer)
    table. The sub-pattern-1 checks fire when ALL records of a class
    leave a field empty/None, signalling a structural gap (no producer
    writes to that slot) rather than a missing value on one record.
    Sub-patterns 3 (same-name two-definitions) and 4 (writer-writes-
    constant) are codified at the producer side rather than detected
    by the validator — the cross-checks would fire at the boundary
    (orchestrator) layer, which doesn't have a record stream to inspect.

    WI-libib/WI-hudug extension: a **kind-conditioned population contract**.
    The sub-patterns above partition only by ``(record_class, field)``, so a
    field populated on one Symbol *kind* masks a 100%-NULL partition on
    another kind (``qualified_name`` on ``function`` symbols hides a fully-
    NULL ``method`` partition behind the shared Symbol class). A registered
    per-``(language, kind, field)`` contract catches that regression class;
    see ``_check_kind_conditioned_population``.
    """
    violations: list[ValidationViolation] = []
    runs_list = list(analysis_runs)
    symbols_list = list(symbols)
    edges_list = list(edges)
    if not runs_list:
        return violations

    # Sub-pattern 2: default-only initializer. For each registered
    # (record_class, field) sentinel, check if every record of that
    # class has the field set to exactly the sentinel value. If so,
    # the writer side is wiring the field but never overriding the
    # default — INV-luhur §"Default-only initializer never overridden".
    for (record_class, field_name), sentinel_fn in (
        _WRITER_CONTRACT_DEFAULT_SENTINELS.items()
    ):
        records: list[Any]
        if record_class == "AnalysisRun":
            records = runs_list
        elif record_class == "Symbol":  # pragma: no cover — no Symbol sentinels yet
            records = symbols_list
        elif record_class == "Edge":  # pragma: no cover — no Edge sentinels yet
            records = edges_list
        else:  # pragma: no cover — unknown record class is an internal bug
            continue
        if not records:  # pragma: no cover — guarded by outer truthiness
            continue
        sentinel = sentinel_fn()
        violating_records = [
            r for r in records
            if _read(r, field_name, None) == sentinel
        ]
        if len(violating_records) == len(records) and len(records) >= 2:
            # Every record has the literal default; the writer side
            # never overrode it. Emit ONE umbrella violation rather
            # than N per-record violations — the issue is structural,
            # not per-record.
            example_id = _read(
                violating_records[0],
                "execution_id",
                _read(violating_records[0], "id", None),
            )
            violations.append(ValidationViolation(
                severity="warning",
                validator_class="writer_contract",
                axis=None,
                field_name=f"{record_class}.{field_name}",
                record_id=example_id,
                observed=sentinel,
                expected=(
                    "evidence-derived value (writer-contract sub-pattern 2: "
                    "default-only initializer never overridden)"
                ),
                message=(
                    f"All {len(records)} {record_class} records have "
                    f"{field_name}={sentinel!r} (the literal default). "
                    "Some pass should be populating it with an evidence-"
                    "derived value. See INV-luhur §\"Default-only "
                    "initializer never overridden\"."
                ),
            ))

    # Sub-pattern 1 (Phase 6 PR2): schema-declares-no-writer. For each
    # registered (record_class, field) pair, check if every record of
    # that class has the field empty/None/default-list. If so, no
    # producer is wiring the field at all. Distinguishes from the
    # axis_conformance "required field missing" check — sub-pattern-1
    # targets Optional fields where the schema reserves a slot but no
    # producer ever ships a value, leaving the slot dead.
    violations.extend(
        _check_sub_pattern_1_never_populated(symbols_list, edges_list, runs_list)
    )

    # Kind-conditioned population contract (WI-libib engine, WI-hudug entry).
    # Partitions symbols by (language, kind) so a field populated on one kind
    # cannot mask a 100%-NULL partition on another; complements the
    # (record_class, field)-level sub-patterns above.
    violations.extend(_check_kind_conditioned_population(symbols_list))

    return violations


def _check_sub_pattern_1_never_populated(
    symbols_list: list[Any],
    edges_list: list[Any],
    runs_list: list[Any],
) -> list[ValidationViolation]:
    """Sub-pattern 1 helper extracted for explicit testability.

    The ``_WRITER_CONTRACT_NEVER_POPULATED`` table is currently empty
    (Phase 6 PR2 closes every previously-registered case at the producer
    layer). When the table is empty the loop body is unreachable; future
    registrations re-exercise it.
    """
    violations: list[ValidationViolation] = []
    for record_class, field_name, contract_msg in _WRITER_CONTRACT_NEVER_POPULATED:  # pragma: no cover — table currently empty
        if record_class == "Edge":
            records = edges_list
        elif record_class == "Symbol":
            records = symbols_list
        elif record_class == "AnalysisRun":
            records = runs_list
        else:
            continue
        if len(records) < 2:
            continue
        populated_count = sum(1 for r in records if _is_truthy(r, field_name))
        if populated_count == 0:
            example_id = _read(
                records[0],
                "execution_id",
                _read(records[0], "id", None),
            )
            violations.append(ValidationViolation(
                severity="warning",
                validator_class="writer_contract",
                axis=None,
                field_name=f"{record_class}.{field_name}",
                record_id=example_id,
                observed="<empty across all records>",
                expected=(
                    "at least one producer populates the field "
                    "(writer-contract sub-pattern 1: "
                    "schema-declares-no-writer)"
                ),
                message=(
                    f"All {len(records)} {record_class} records have "
                    f"{field_name} unpopulated. {contract_msg}"
                ),
            ))
    return violations


# Kind-conditioned "must populate" contract (WI-libib engine, WI-hudug entry).
#
# Each entry ``(language, kind, field_name, contract_description)`` asserts that
# symbols matching ``(language, kind)`` populate ``field_name`` — a 100%-NULL
# partition on that cell is a writer regression that the ``(record_class,
# field)``-level sub-patterns above cannot see (a field populated on one kind
# keeps the class-level partition non-empty). Rather than enforce the full
# (kind, field) matrix — rejected by WI-libib's own author as over-broad, since
# most NULL cells are legitimately NULL (a ``variable`` has no meaningful
# ``qualified_name``) — the contract is registered cell by cell: each entry is a
# deliberate "this cell MUST stay populated" assertion, the sound-by-
# construction population ratchet WI-libib was reframed to. The producer half of
# the first entries is done (WI-fagab / ADR-0032), so on current output these
# emit no violation and serve as a regression guard; a future producer that
# drops ``qualified_name`` on Python callables/classes trips them, and the
# resulting ``writer_contract|warning`` cell (unbaselined → ceiling 0) breaks
# the validation_ratchet self-tree gate.
_WRITER_CONTRACT_KIND_MUST_POPULATE: tuple[tuple[str, str, str, str], ...] = (
    ("python", "function", "qualified_name",
     "Python function symbols carry an ADR-0032 qualified_name (WI-fagab)."),
    ("python", "method", "qualified_name",
     "Python method symbols carry an ADR-0032 qualified_name (WI-fagab)."),
    ("python", "class", "qualified_name",
     "Python class symbols carry an ADR-0032 qualified_name (WI-fagab)."),
)


def _check_kind_conditioned_population(
    symbols_list: list[Any],
) -> list[ValidationViolation]:
    """Per-``(language, kind)`` population contract (WI-libib engine, WI-hudug).

    The record-class-level sub-patterns partition only by ``(record_class,
    field)``, so they miss a *kind-conditioned* NULL: ``qualified_name``
    populated on ``function`` symbols but 100% NULL on every ``method`` symbol
    never trips a Symbol-level check, because at least one Symbol carries it.
    This check partitions symbols by ``(language, kind)`` and flags a
    **registered** cell that is 100% NULL across its **non-empty** kind
    partition. An absent partition (the kind isn't on this substrate) is skipped
    — absence is not a population regression — and a single populated symbol
    satisfies the contract (the writer IS reaching the slot; a residual per-
    record gap is a finer, separate concern). One umbrella violation per cell,
    not one per record, because the gap is structural.
    """
    violations: list[ValidationViolation] = []
    for language, kind, field_name, contract_msg in (
        _WRITER_CONTRACT_KIND_MUST_POPULATE
    ):
        partition = [
            s for s in symbols_list
            if _read(s, "kind", None) == kind
            and _read(s, "language", None) == language
        ]
        if not partition:
            continue
        populated = sum(1 for s in partition if _is_truthy(s, field_name))
        if populated == 0:
            violations.append(ValidationViolation(
                severity="warning",
                validator_class="writer_contract",
                axis=None,
                field_name=f"Symbol[{language}/{kind}].{field_name}",
                record_id=_read(partition[0], "id", None),
                observed=(
                    f"<empty across all {len(partition)} "
                    f"{language} {kind} symbols>"
                ),
                expected=(
                    "field populated on at least one symbol of this kind "
                    "(writer-contract kind-conditioned population contract)"
                ),
                message=(
                    f"All {len(partition)} {language} {kind} symbols have "
                    f"{field_name} unpopulated. {contract_msg} "
                    "See INV-luhur / WI-libib (kind-conditioned population)."
                ),
            ))
    return violations


# ----------------------------------------------------------------------
# Phase 3 PR3 — Cross-field coherence validator class
# ----------------------------------------------------------------------
#
# Per ADR-0033 §"Validator classes" #3. Documented field-pair
# invariants the producer pipeline is expected to honor; the validator
# scans every record and reports records that violate one.
#
# Invariants checked in Phase 3 PR3:
#
#   - **dst_ref ↔ dst**: When `Edge.dst_ref` is populated, the legacy
#     `Edge.dst` string must also be populated (the two carry the same
#     external-target identity in different shapes per the
#     `make_unresolved_edge` docstring).
#   - **Class B language/protocol_origin coherence (ADR-0031)**: A
#     Symbol with `language=None` is a synthetic linker stand-in and
#     must have `protocol_origin` populated (and vice versa: a Symbol
#     with `protocol_origin` populated must have `language=None`).
#     File-Symbol Class A exceptions: `kind="file"` skips the check
#     because file Symbols have language but no protocol identity.
#   - **display_label scope (ADR-0032)**: `Symbol.display_label`
#     should appear on synthetic stand-ins (Class B) only —
#     real-source declarations (Class A: kind="function" / "class" /
#     "method" / "variable" etc. in a real source file) leave it
#     `None`. The cleanest heuristic: a Symbol with `display_label`
#     populated AND `language` non-None AND `protocol_origin` None is
#     a Class-A symbol with a display label — that's the smell.
#
# Sub-pattern future-extension hook: this validator's table is the
# WI-mafik / WI-huzuv / WI-nigah-style coherence assertion family.
# Each future cross-field invariant adds a row to the per-record
# checks below.


def _check_cross_field_coherence(
    symbols: Iterable[Any],
    edges: Iterable[Any],
    analysis_runs: Iterable[Any],
) -> list[ValidationViolation]:
    """Cross-field coherence class: field-pair invariants.

    See ADR-0033 §"Validator classes" #3.
    """
    violations: list[ValidationViolation] = []

    # Class B stamping canary (WI-kufib / META-niguz): before schema
    # 0.14.0 relaxed Symbol.language to nullable, the 262 whole-document
    # `language: None is not of type 'string'` validation errors were
    # the de-facto signal that the synthetic stand-in population is
    # under-stamped. The schema fix silences that signal, so the canary
    # moves onto the fields that SHOULD be non-null on a Class B
    # stand-in. Collected per-field; emitted as one umbrella violation
    # per field (writer-contract style) after the loop.
    class_b_missing: dict[str, list[str]] = {
        "Symbol.stable_id": [],
        "Symbol.fingerprint": [],
        "Symbol.discovery_language": [],
        "Symbol.origin": [],
        # META-huvuh (synthetic:F2): the affirmative half of the display_label
        # biconditional — Class-A real-source declarations must NOT carry a
        # display_label (the contrapositive check below), and Class-B synthetic
        # stand-ins MUST. ADR-0032 reserves display_label for Class B.
        "Symbol.display_label": [],
    }

    # ADR-0037 ruling 5: the edge FK predicate below needs each dst node's kind to
    # tell a first-party target from an external_symbol placeholder. Built in the
    # symbol pass so the edge pass needs no second iteration over symbols.
    node_kind_by_id: dict = {}

    # ---- Symbol invariants ----
    for sym in symbols:
        sym_id = getattr(sym, "id", None)
        language = getattr(sym, "language", None)
        protocol_origin = getattr(sym, "protocol_origin", None)
        kind = getattr(sym, "kind", None)
        display_label = getattr(sym, "display_label", None)
        node_kind_by_id[sym_id] = kind

        if language is None and protocol_origin is not None:
            # Class B synthetic stand-in: collect missing identity stamps.
            if getattr(sym, "stable_id", None) is None:
                class_b_missing["Symbol.stable_id"].append(str(sym_id))
            if getattr(sym, "fingerprint", None) is None:
                class_b_missing["Symbol.fingerprint"].append(str(sym_id))
            if getattr(sym, "discovery_language", None) is None:
                class_b_missing["Symbol.discovery_language"].append(str(sym_id))
            if not getattr(sym, "origin", None):
                class_b_missing["Symbol.origin"].append(str(sym_id))
            if display_label is None:
                class_b_missing["Symbol.display_label"].append(str(sym_id))

        # ADR-0031 Class B coherence. File Symbols are an explicit
        # Class A exception per the ADR's per-linker producer policy.
        if kind != "file":
            if language is None and protocol_origin is None:
                # Class-B-without-protocol-origin: ambiguous. Either the
                # producer forgot to set protocol_origin, or this is a
                # legitimate boundary node (e.g., external-symbol synth).
                # Skip to avoid false positives during the migration.
                pass
            elif language is not None and protocol_origin is not None:
                violations.append(ValidationViolation(
                    severity="warning",
                    validator_class="cross_field",
                    field_name="Symbol.language / Symbol.protocol_origin",
                    record_id=sym_id,
                    observed=f"language={language!r}, protocol_origin={protocol_origin!r}",
                    expected=(
                        "Class A: language non-None, protocol_origin None. "
                        "Class B: language None, protocol_origin non-None. "
                        "Both populated together violates ADR-0031."
                    ),
                    message=(
                        f"Symbol {sym_id!r} has BOTH language={language!r} "
                        f"and protocol_origin={protocol_origin!r}. ADR-0031 "
                        "Class A (real source) keeps language; Class B "
                        "(synthetic stand-in) uses protocol_origin with "
                        "language=None. Both populated together is incoherent."
                    ),
                ))

        # ADR-0032 display_label scope. Class A real-source declarations
        # should not carry a display_label; the field is reserved for
        # synthetic linker stand-ins.
        #
        # Exemptions:
        # - kind="file": file pseudo-symbols have a synthesized
        #   display_label as part of their boundary identity (per
        #   ir.py:synthesize_file_symbols_for_dangling_edges).
        # - kind="external_symbol": dangling-edge boundary nodes
        #   (ir.py:1285) use display_label as the canonical printable
        #   form (``f"{language}:{path}:{name}:{kind}"``); this is the
        #   ADR-0032 typed-sibling pattern applied to externals, not a
        #   Class A leak.
        if (
            display_label is not None
            and language is not None
            and protocol_origin is None
            and kind not in ("file", "external_symbol")
        ):
            violations.append(ValidationViolation(
                severity="warning",
                validator_class="cross_field",
                field_name="Symbol.display_label",
                record_id=sym_id,
                observed=f"display_label={display_label!r}",
                expected=(
                    "display_label reserved for Class B synthetic stand-ins "
                    "(language=None, protocol_origin populated); Class A "
                    "real-source declarations leave it None."
                ),
                message=(
                    f"Symbol {sym_id!r} has display_label={display_label!r} "
                    f"but is Class A (language={language!r}, "
                    "protocol_origin=None). ADR-0032 reserves display_label "
                    "for Class B synthetic stand-ins."
                ),
            ))

    # Emit the Class B stamping canary umbrellas (one per field).
    for field_name, missing_ids in class_b_missing.items():
        if not missing_ids:
            continue
        samples = ", ".join(missing_ids[:3])
        violations.append(ValidationViolation(
            severity="warning",
            validator_class="cross_field",
            field_name=field_name,
            record_id=None,
            observed=(
                f"{len(missing_ids)} Class B stand-in(s) with "
                f"{field_name.split('.', 1)[1]} unset"
            ),
            expected=(
                "Every Class B synthetic stand-in (language=None, "
                "protocol_origin populated) carries non-null stable_id, "
                "fingerprint, discovery_language, display_label, and a "
                "non-empty origin."
            ),
            message=(
                f"{len(missing_ids)} Class B synthetic stand-in(s) are "
                f"missing {field_name} (e.g. {samples}). This is the "
                "relocated WI-kufib canary: schema 0.14.0 tolerates "
                "language=None, so under-stamping no longer fails "
                "whole-document validation — it must surface here "
                "instead (META-niguz)."
            ),
        ))

    # ---- Edge invariants ----
    for edge in edges:
        edge_id = getattr(edge, "id", None)
        dst_ref = getattr(edge, "dst_ref", None)
        dst = getattr(edge, "dst", None)

        # ADR-0037 ruling 5: is_resolved=True ⇒ dst is a real, in-repo
        # (first-party) symbol node. The single edge-finalization verdict
        # (finalize sub-step 7) derives is_resolved from exactly this fact,
        # so a surviving violation means a producer/linker wrote is_resolved
        # independently — the WI-kukuk contradiction (resolved flag on an
        # external_symbol placeholder), now a CI failure not a latent defect.
        #
        # Scoped to the external_symbol case: the bare "dst ∈ nodes" half of
        # the FK is satisfied by construction post-boundary-synthesis (every
        # external dst is materialized as a placeholder node), so the only
        # enforceable discriminator is the dst node's KIND. The dst-absent
        # ("dangling") case is owned by the sibling endpoint-closure work
        # (INV-jukok family), not this predicate — keeping it out also means
        # isolated unit fixtures that point an edge outside their symbol set
        # don't trip it.
        if getattr(edge, "is_resolved", True):
            if node_kind_by_id.get(dst) == "external_symbol":
                violations.append(ValidationViolation(
                    severity="error",
                    validator_class="cross_field",
                    field_name="Edge.is_resolved / Edge.dst",
                    record_id=edge_id,
                    observed=f"is_resolved=True, dst={dst!r}, dst_kind='external_symbol'",
                    expected=(
                        "is_resolved=True requires dst to be a first-party "
                        "node (kind != 'external_symbol'); external targets "
                        "are is_resolved=False per ADR-0037 ruling 1."
                    ),
                    message=(
                        f"Edge {edge_id!r} claims is_resolved=True but its dst "
                        f"{dst!r} is an external_symbol placeholder, not an "
                        "in-repo target (ADR-0037 ruling 5)."
                    ),
                ))

        # dst_ref ↔ dst coherence: when dst_ref is populated, the legacy
        # dst string must also be populated.
        if dst_ref is not None and not dst:
            violations.append(ValidationViolation(
                severity="error",
                validator_class="cross_field",
                field_name="Edge.dst / Edge.dst_ref",
                record_id=edge_id,
                observed=f"dst={dst!r}, dst_ref={dst_ref!r}",
                expected=(
                    "When dst_ref is populated, dst must also be "
                    "populated (legacy back-compat per make_unresolved_edge)."
                ),
                message=(
                    f"Edge {edge_id!r} has dst_ref populated but dst is "
                    "empty. Producers MUST stamp both for back-compat with "
                    "the ~34 consumer sites that haven't migrated to "
                    "dst_ref."
                ),
            ))

    # ---- INV-bazij / ADR-0035 §5 umbrella: corpus stable_id collision ----
    # Phase 6 PR3 (INV-bazij P0): stable_id was documented as carrying
    # per-symbol structural identity but the original hash inputs collided
    # at ~60% on the dogfood corpus. v6 (ADR-0035 §1) makes stable_id
    # unique WITHIN A RUN by design, so §5 drops this corpus threshold from
    # 5% to effectively ZERO: any collision is a not-yet-migrated producer
    # to surface, never a rate tolerated below an alarm line. Two changes
    # from the 5%-era check:
    #   * Threshold 0.0 — fire whenever collided > 0.
    #   * Denominator INCLUDES the None-stable_id cohort (WI-niluv): the
    #     rate is over ALL Symbols and the None cohort is disclosed in the
    #     same line, so a clean non-null rate can never silently hide a
    #     large no-stable_id cohort (the 2026-06-01 false all-clear shape).
    # This is the SOFT (warning) cross-file signal for the migration
    # backlog; the HARD per-file uniqueness guarantee is a separate error
    # check (_check_stable_id_per_file_uniqueness). One umbrella violation
    # naming the top-3 groups, not one-per-Symbol. (The fingerprint twin
    # below keeps the non-null denominator — WI-falum, out of §5 scope.)
    _STABLE_ID_COLLISION_THRESHOLD = 0.0
    counter: dict[str, list[Any]] = {}
    total = 0
    none_cohort = 0
    for sym in symbols:
        sid = getattr(sym, "stable_id", None)
        if sid is None:
            none_cohort += 1
            continue
        total += 1
        counter.setdefault(sid, []).append(sym)
    population = total + none_cohort
    if population > 0:
        collided = sum(len(g) for g in counter.values() if len(g) > 1)
        rate = collided / population
        if rate > _STABLE_ID_COLLISION_THRESHOLD:
            # Top 3 largest collision groups, by member count. Secondary key
            # on the stable_id breaks size ties deterministically so the
            # rendered message is byte-stable across symbol-iteration orders
            # (ADR-0043 §6); a bare reverse-by-length leaks insertion order.
            top_groups = sorted(
                ((sid, g) for sid, g in counter.items() if len(g) > 1),
                key=lambda item: (-len(item[1]), item[0]),
            )[:3]
            top_descriptions = []
            for sid, group in top_groups:
                sample_names = sorted({
                    (getattr(s, "name", None) or "?")[:40] for s in group[:5]
                })
                top_descriptions.append(
                    f"{sid} ({len(group)} symbols, e.g. "
                    f"{', '.join(sample_names)})"
                )
            top_str = "; ".join(top_descriptions) if top_descriptions else "(none)"
            # WI-niluv disclosure: the rate is over the full population
            # (None included); the None cohort is named in the same line so
            # the encoding stays biconditional (reader can recover both the
            # collision count and the None count from population).
            none_frac = none_cohort / population
            none_disclosure = (
                f"; none_cohort={none_cohort}/{population} "
                f"({none_frac*100:.1f}% had stable_id=None)"
            )
            violations.append(ValidationViolation(
                severity="warning",
                validator_class="cross_field",
                field_name="Symbol.stable_id",
                record_id=None,
                observed=(
                    f"{collided}/{population} Symbols share a stable_id "
                    f"({rate*100:.1f}% of all Symbols)" + none_disclosure
                ),
                expected=(
                    "stable_id must be unique within a run (ADR-0035 §1; "
                    "collision-free by design). The corpus collision "
                    "threshold is ~0 (ADR-0035 §5); any collision is a "
                    "producer whose hash inputs are too coarse."
                ),
                message=(
                    f"stable_id collision detected: {collided}/{population} "
                    f"Symbols ({rate*100:.1f}% of all Symbols) share an id. "
                    f"Top groups: {top_str}. Per ADR-0035 §5 the corpus "
                    "threshold is ~0; a SITE-axis kind needs its "
                    "occurrence_index populated, a LOGICAL-axis stand-in "
                    "needs deduping (§3)."
                ),
            ))

    # ---- WI-falum umbrella: fingerprint degeneracy ----------------------
    # The structural fingerprint hashes shape + identifiers + literals,
    # so symbols with DISTINCT names should virtually never share one
    # value en masse — sharing under the SAME name is legitimate
    # duplicate code (the design intent), but a single value spread
    # across many names means the producer lost content discrimination.
    # The motivating regression: all 76 TOML dependency nodes (67
    # distinct package names) collapsed to ONE fingerprint because the
    # v1 snippet parse saw only an ERROR tree (WI-falum, 6.0.0). One
    # umbrella violation per run names the top-3 degenerate values.
    #
    # Threshold: 10 distinct names under one value. Same-shape code
    # bodies carry their identifiers in the hash, so even pathological
    # duplicate-heavy repos stay far below this without a producer bug.
    # Names are compared by their SIMPLE form (qualifier segments
    # stripped): 20 test classes each containing an identical
    # ``tracker_set`` fixture method produce 20 qualified names
    # (``TestX.tracker_set``) over one legitimately-shared hash — the
    # subtree starts at the ``def``, so the enclosing class is rightly
    # not part of the content. Distinct SIMPLE names sharing one value
    # is the real degeneracy signal.
    _FINGERPRINT_DEGENERACY_MIN_NAMES = 10
    fp_names: dict[str, set[str]] = {}
    fp_counts: dict[str, int] = {}
    fp_none = 0
    for sym in symbols:
        fp = getattr(sym, "fingerprint", None)
        if fp is None:
            # WI-niluv (twin): count the None-fingerprint cohort instead of
            # silently dropping it, so the firing violation can disclose how
            # large a population the degeneracy scan excluded.
            fp_none += 1
            continue
        name = getattr(sym, "name", None) or "?"
        simple = name.rsplit(".", 1)[-1].rsplit("::", 1)[-1].rsplit("\\", 1)[-1]
        fp_names.setdefault(fp, set()).add(simple)
        fp_counts[fp] = fp_counts.get(fp, 0) + 1
    # Secondary key on the fingerprint value breaks size ties deterministically
    # (ADR-0043 §6 byte-determinism; the analogue of the stable_id umbrella's
    # tie-break above), so the rendered message does not leak iteration order.
    degenerate = sorted(
        (
            (fp, names) for fp, names in fp_names.items()
            if len(names) >= _FINGERPRINT_DEGENERACY_MIN_NAMES
        ),
        key=lambda item: (-len(item[1]), item[0]),
    )
    if degenerate:
        top_descriptions = []
        for fp, names in degenerate[:3]:
            sample = ", ".join(sorted(names)[:5])
            top_descriptions.append(
                f"{fp} ({fp_counts[fp]} symbols, {len(names)} distinct "
                f"names, e.g. {sample})"
            )
        worst_fp, worst_names = degenerate[0]
        # WI-niluv denominator disclosure (twin of the stable_id umbrella).
        fp_total = sum(fp_counts.values())
        fp_population = fp_total + fp_none
        fp_none_disclosure = (
            f"; denominator_scope=non_null ({fp_none}/{fp_population} had "
            "fingerprint=None, EXCLUDED from the scan)"
        )
        violations.append(ValidationViolation(
            severity="warning",
            validator_class="cross_field",
            field_name="Symbol.fingerprint",
            record_id=None,
            observed=(
                f"{len(degenerate)} fingerprint value(s) shared by >= "
                f"{_FINGERPRINT_DEGENERACY_MIN_NAMES} distinctly-named "
                f"symbols; worst: {fp_counts[worst_fp]} symbols / "
                f"{len(worst_names)} names on one value" + fp_none_disclosure
            ),
            expected=(
                "A structural fingerprint (shape + identifiers + "
                "literals) must discriminate distinctly-named content; "
                "mass sharing across names means the producer emitted a "
                "degenerate constant (WI-falum)."
            ),
            message=(
                "Degenerate Symbol.fingerprint value(s) detected: "
                f"{'; '.join(top_descriptions)}. Check the fingerprint "
                "post-pass for spans whose parse drops content "
                "(hypergumbo_core/fingerprint.py); unparseable spans "
                "must yield None, never a shared constant."
            ),
        ))

    return violations


# ----------------------------------------------------------------------
# ADR-0035 §5 — per-file stable_id uniqueness (HARD check) + corpus stats
# ----------------------------------------------------------------------
#
# v6 (ADR-0035 §1) makes stable_id unique within a run by design. §5 turns
# that contract into two enforcement surfaces:
#   * Per-file uniqueness — a HARD (error) check below: within ONE file's
#     emitted Symbols a duplicated stable_id is an error, zero tolerance.
#     This is the by-design-collision-free contract at the producer
#     boundary. (The corpus-wide rate umbrella in _check_cross_field_
#     coherence is the SOFT/warning cross-file companion.)
#   * Honest disclosure — compute_stable_id_stats below surfaces the
#     None-cohort + collision rate over an ALL-Symbols denominator into the
#     report ALWAYS (not only when an umbrella fires), so the 2026-06-01
#     false all-clear (a hidden None-cohort) cannot recur.


def _check_stable_id_per_file_uniqueness(
    symbols: Iterable[Any],
) -> list[ValidationViolation]:
    """ADR-0035 §5 per-file emit-time uniqueness — HARD check (error).

    Within one file's emitted Symbols, a duplicated ``stable_id`` is an
    error, not a rate contribution. Symbols without a ``path`` (pathless
    synthetic stand-ins) are out of scope here — they are caught by the
    corpus-wide umbrella; this check is specifically the in-a-file
    guarantee. One violation per colliding ``(path, stable_id)`` group, not
    one-per-Symbol, so a regression that reintroduces collisions surfaces a
    readable summary rather than flooding the report.
    """
    violations: list[ValidationViolation] = []
    by_file_sid: dict[tuple[str, str], list[Any]] = {}
    for sym in symbols:
        sid = getattr(sym, "stable_id", None)
        if sid is None:
            continue
        path = getattr(sym, "path", None)
        if not path:
            continue
        by_file_sid.setdefault((path, sid), []).append(sym)
    for (path, sid), group in sorted(by_file_sid.items()):
        if len(group) < 2:
            continue
        names = sorted({(getattr(s, "name", None) or "?")[:50] for s in group})
        kinds = sorted({getattr(s, "kind", None) or "?" for s in group})
        # Deterministic representative id (ADR-0043 §6 byte-determinism): the
        # lexicographically-smallest id, independent of symbol-iteration order
        # (group[0] would leak ranked-vs-analyzer-append ordering).
        record_id = min((getattr(s, "id", None) or "") for s in group) or None
        violations.append(ValidationViolation(
            severity="error",
            validator_class="cross_field",
            field_name="Symbol.stable_id",
            record_id=record_id,
            observed=(
                f"{len(group)} Symbols in {path} share stable_id {sid} "
                f"(names: {', '.join(names)}; kinds: {', '.join(kinds)})"
            ),
            expected=(
                "Within one file, every emitted Symbol.stable_id must be "
                "unique (ADR-0035 §5 per-file uniqueness; v6 collision-free "
                "by design)."
            ),
            message=(
                f"Per-file stable_id collision in {path}: {len(group)} "
                f"Symbols share {sid}. A SITE-axis kind (call_site, link) "
                "needs its occurrence_index populated; a LOGICAL-axis "
                "stand-in needs deduping (ADR-0035 §3)."
            ),
        ))
    return violations


def compute_stable_id_stats(symbols: Iterable[Any]) -> dict[str, Any]:
    """ADR-0035 §5 honest-denominator disclosure.

    Corpus-level stable_id population stats over an ALL-Symbols denominator,
    surfaced in ``validation_report.stable_id_stats`` so the None-cohort and
    collision rate are ALWAYS visible — independent of whether the collision
    umbrella fired. This is the structural guard against the 2026-06-01
    false all-clear (a hidden None-cohort behind a clean non-null rate).
    """
    counter: dict[str, list[Any]] = {}
    by_file_sid: dict[tuple[str, str], int] = {}
    total = 0
    none_cohort = 0
    for sym in symbols:
        sid = getattr(sym, "stable_id", None)
        if sid is None:
            none_cohort += 1
            continue
        total += 1
        counter.setdefault(sid, []).append(sym)
        path = getattr(sym, "path", None)
        if path:
            key = (path, sid)
            by_file_sid[key] = by_file_sid.get(key, 0) + 1
    population = total + none_cohort
    collided = sum(len(g) for g in counter.values() if len(g) > 1)
    collision_groups = sum(1 for g in counter.values() if len(g) > 1)
    per_file_collision_groups = sum(1 for c in by_file_sid.values() if c > 1)
    return {
        "total_symbols": population,
        "non_null": total,
        "none_cohort": none_cohort,
        "none_cohort_pct": (
            round(100 * none_cohort / population, 2) if population else 0.0
        ),
        "collision_groups": collision_groups,
        "collided_symbols": collided,
        "collision_rate_pct": (
            round(100 * collided / population, 2) if population else 0.0
        ),
        "per_file_collision_groups": per_file_collision_groups,
    }


# ----------------------------------------------------------------------
# Phase 3 PR4 — Verdict-enum completeness validator class (folds in WI-rolol sub-task A)
# ----------------------------------------------------------------------
#
# Per ADR-0033 §"Validator classes" #4. Generalizes the silent-confirm
# fall-through that drove WI-rolol sub-task A: any verdict-emitting
# code path must enumerate an "inconclusive" (or equivalent) branch
# for the missing-data / malformed-input / broken-binary cases —
# falling through to the positive verdict ("confirmed" / "ok" /
# "pass") is a security false-positive class.
#
# This validator runs at **import time** rather than per-record: it
# introspects the verdict-emitting dataclasses (currently only
# ClaimVerdict) and confirms each has an "inconclusive" value in its
# documented enum. Per-record violations would emit one per claim;
# the structural absence is one violation total.
#
# The validator's table _VERDICT_DATACLASSES is hand-maintained. Each
# entry: (module_path, class_name, field_name, allowed_inconclusive_values).
# New verdict-emitting dataclasses register here; the validator checks
# the documented enum (read from the field's # axis: comment in the
# dataclass declaration via the same static-AST machinery the
# multi_value_field_axis validator uses).


_VERDICT_DATACLASSES: tuple[tuple[str, str, str, frozenset[str]], ...] = (
    # (import_path, class_name, field_name, must-contain values)
    (
        "hypergumbo_core.verify_claims",
        "ClaimVerdict",
        "verdict",
        frozenset({"inconclusive"}),
    ),
)


def _check_verdict_enum_completeness() -> list[ValidationViolation]:
    """Verdict-enum completeness class: every verdict-emitting dataclass
    enumerates an "inconclusive" branch for missing-data cases.

    See ADR-0033 §"Validator classes" #4. Runs at validator-invocation
    time but is structurally a static check — the result depends only
    on the verdict-dataclass declarations, not on emitted records. The
    check is included here (rather than in the static-AST validator)
    so the validator stage owns the entire spec-vs-data surface as a
    single discoverable check set.
    """
    violations: list[ValidationViolation] = []
    for module_path, class_name, field_name, must_contain in _VERDICT_DATACLASSES:
        # Introspect the dataclass's documented enum from the field's
        # type annotation or docstring. For ClaimVerdict, the verdict
        # values are documented in the class docstring (we can't rely
        # on Literal[...] type narrowing — the field is annotated `str`).
        import importlib

        module = importlib.import_module(module_path)
        klass = getattr(module, class_name)
        docstring = (klass.__doc__ or "").lower()
        # Each must-contain value should appear in the docstring as
        # part of the documented verdict enumeration.
        for required_value in must_contain:
            if required_value not in docstring:
                violations.append(ValidationViolation(
                    severity="error",
                    validator_class="verdict_enum",
                    field_name=f"{class_name}.{field_name}",
                    record_id=None,
                    observed=None,
                    expected=(
                        f"verdict enum includes {required_value!r} "
                        "(per ADR-0033 §\"Validator classes\" #4 — "
                        "missing-data / malformed-input cases must "
                        "have a non-confirming verdict branch)"
                    ),
                    message=(
                        f"{class_name}.{field_name} docstring does not "
                        f"mention the {required_value!r} verdict value. "
                        "Silent fall-through to the positive verdict is "
                        "a security false-positive class (INV-bitig P0)."
                    ),
                ))
    return violations


# ----------------------------------------------------------------------
# Phase 5 PR1 — ID-format validator class (ADR-0034)
# ----------------------------------------------------------------------
#
# Every ``Symbol.id`` is required to follow the canonical schema
# ``<language>:<path>:<start>-<end>:<name>:<kind>`` with **single-colon**
# separators. This is the identity contract documented at
# ``analyze/base.py:make_symbol_id`` — the same factory ten language
# analyzers call.
#
# Historically, five linker passes (http, message-queue, database-query,
# subprocess-cli, graphql-resolver, graphql) emitted call_site Symbols
# via an ad-hoc f-string schema ``<path>::<role>::<line>`` with
# **double-colon** separators and no language prefix. INV-sadiv
# documented 218 such nodes; they break cross-language edge detection
# because the path-prefix gets parsed as ``language=packages/...``.
#
# The check below pins the canonical shape at runtime. A non-conforming
# ``Symbol.id`` produces one ``id_format`` violation with the observed
# value and the inferred problem (double colons / wrong field count /
# malformed span).
#
# Out of scope for this PR (Phase 6 PR1/PR3 territory):
# - ``Symbol.stable_id`` format checks (``sha256:<16hex>`` schema).
# - ``Edge.id`` format checks.
# - Stable-id collision counting (INV-bazij P0).
# - Stable-id multiplicity (one stable_id per logical symbol — INV-hunup).

# Canonical ID pattern matching the IR's last-3-tokens parser (ir.py
# ``_parse_dangling_id`` line 1093 — "the path slot may itself contain
# colons"). The shape is fixed at five colon-separated segments where
# the LAST THREE (span, name, kind) are colon-free; the path may
# contain ``:`` (Windows drive prefixes, Rust ``::``-namespaced module
# paths, etc.). The greedy ``.+`` in the path slot backtracks until
# the span-name-kind suffix matches.
#
# - lang: lowercase identifier (one or more alphanum/underscore chars
#   starting with a letter; matches the strings in catalog.all_known_languages)
# - path: any non-empty string (may contain colons)
# - span: digit+-digit+
# - name: anything without a colon (may be empty — file pseudo-symbols
#   use the literal "file"; in practice always non-empty)
# - kind: lowercase identifier (matches symbol_kinds.all_symbol_kind_names)
_CANONICAL_ID_PATTERN = re.compile(
    r"^[a-z][a-z0-9_]*"        # language
    r":.+"                     # path (may contain colons)
    r":\d+-\d+"                # span
    r":[^:]*"                  # name (colon-free)
    r":[a-z][a-z0-9_]*$"       # kind
)


def _classify_id_format_problem(id_str: str) -> str:
    """Return a short tag identifying why the ID is non-canonical.

    Order matters: the most specific (and historically common) failure
    mode is checked first so the validator's violation messages point
    operators at the right fix. The IR's permissive parser
    (``_parse_dangling_id`` last-3-tokens shape) is the source of truth
    for "valid"; this classifier explains why the value falls outside.

    INV-sadiv detection: the legacy path-prefix shape
    ``<path>::<role>::<line>`` produces an ID whose FIRST segment is
    not a valid language identifier and which contains ``::``. Tag
    these explicitly so reviewers know to migrate to ``make_symbol_id``
    even though the surface-level failure (non_canonical_language_prefix
    or wrong_field_count) would also fire.
    """
    parts = id_str.split(":")
    if "::" in id_str:
        # INV-sadiv pre-dates the canonical schema; surface the migration
        # hint before the more-generic shape diagnostics. Note: legitimate
        # canonical IDs with Rust ``::`` in the path slot do NOT reach
        # here because they pass _CANONICAL_ID_PATTERN.match in
        # _check_id_format before _classify_id_format_problem fires.
        return "double_colon_separator (INV-sadiv)"
    # The canonical shape has at least 5 segments; the last 3 are span,
    # name, kind (colon-free).
    if len(parts) < 5:
        return f"wrong_field_count (expected at least 5, got {len(parts)})"
    lang = parts[0]
    span = parts[-3]
    _name = parts[-2]
    kind = parts[-1]
    if not re.match(r"^[a-z][a-z0-9_]*$", lang):
        return f"non_canonical_language_prefix ({lang!r})"
    if not re.match(r"^\d+-\d+$", span):
        # INV-dulah: when there are MORE than 5 segments and the span slot is
        # not a span, the cause is almost always a colon in the NAME slot —
        # the right-anchored parse shifts every slot left by one, so the span
        # position holds a fragment of the name. Reporting "malformed span"
        # here names the symptom and sends the reader to the span code; it
        # cost one real investigation, so the diagnosis is separated out.
        if len(parts) > 5:
            return (
                f"colon_in_name_slot (span slot resolved to {span!r}; a ':' in "
                "the name shifts the right-anchored slots — route the name "
                "through sanitize_id_name_segment)"
            )
        return f"malformed_span_segment ({span!r})"
    if not re.match(r"^[a-z][a-z0-9_]*$", kind):
        return f"non_canonical_kind_suffix ({kind!r})"
    return "unknown"  # pragma: no cover - defensive; canonical regex covers shape


def _check_id_format(symbols: Iterable[Any]) -> list[ValidationViolation]:
    """ID-format conformance class: every Symbol.id matches the canonical schema.

    See ADR-0034 §"ID-format validator". The Phase 5 PR1 closure of
    INV-sadiv ensures the six linker passes that previously emitted
    ``<path>::<role>::<line>`` IDs now use ``make_symbol_id(...)``.
    """
    violations: list[ValidationViolation] = []
    for sym in symbols:
        sym_id = getattr(sym, "id", None)
        if sym_id is None:
            # Required-field absence is an axis_conformance issue, not
            # an id_format issue. Skip here so we don't double-count.
            continue
        if not isinstance(sym_id, str):  # pragma: no cover - defensive
            continue
        if _CANONICAL_ID_PATTERN.match(sym_id):
            continue
        problem = _classify_id_format_problem(sym_id)
        violations.append(ValidationViolation(
            severity="error",
            validator_class="id_format",
            field_name="Symbol.id",
            record_id=sym_id,
            observed=sym_id,
            expected="<language>:<path>:<start>-<end>:<name>:<kind>",
            message=(
                f"Symbol.id does not match the canonical schema: "
                f"{problem}. Use make_symbol_id(...) from analyze/base.py "
                "rather than constructing IDs with f-strings."
            ),
        ))
    return violations


# ----------------------------------------------------------------------
# Phase 6 PR1 — Stable-ID-format sub-check (INV-hunup closure)
# ----------------------------------------------------------------------
#
# The ``id_format`` validator class extends to ``Symbol.stable_id`` to
# enforce the canonical ``sha256:<16hex>`` shape produced by the
# ``make_*_stable_id`` factory family in ``analyze/base.py``. Pre-Phase-6,
# the same self-analysis run that surfaced 25 ``id_format`` violations
# also surfaced ~130 ``stable_id`` violations across five escape
# categories (raw 64-char hex, bare-name, 1-/2-/3-colon composites). The
# fixes ride alongside the validator extension; see ADR-0034 for the
# discipline rationale and the Phase 6 PR1 commit message for the
# per-category source map.
#
# Canonical pattern: ``sha256:`` literal prefix + 16 lowercase hex chars.
# Total length 23. The 16-char window comes from
# ``_short_sha256`` (analyze/base.py:_short_sha256), which truncates the
# 64-char hexdigest to 16 chars for footprint reasons documented at
# ADR-0014 §4.

_CANONICAL_STABLE_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{16}$")


def _classify_stable_id_format_problem(stable_id: str) -> str:
    """Return a short tag identifying why the stable_id is non-canonical.

    Mirrors ``_classify_id_format_problem``'s shape. Order matters: the
    most specific (and historically common) failure mode is checked
    first so the violation messages point operators at the right fix.
    The category names are stable enough to be referenced by issue
    titles and tracker discussion entries.
    """
    if re.match(r"^[0-9a-f]{64}$", stable_id):
        return "raw_hex_no_prefix (64-char hexdigest without sha256: prefix)"
    if ":" not in stable_id:
        return "bare_name_no_prefix"
    if stable_id.startswith("sha256:"):
        suffix = stable_id[len("sha256:"):]
        if not re.match(r"^[0-9a-f]+$", suffix):
            return f"sha256_prefix_with_non_hex_suffix ({suffix!r})"
        return f"sha256_prefix_wrong_length (expected 16 chars, got {len(suffix)})"
    colon_count = stable_id.count(":")
    return f"composite_no_sha_prefix (colon_count={colon_count})"


def _check_stable_id_format(symbols: Iterable[Any]) -> list[ValidationViolation]:
    """Stable-ID-format conformance: every Symbol.stable_id matches the
    canonical ``sha256:<16hex>`` schema.

    See ADR-0034 §"ID-format validator" + Phase 6 PR1 (INV-hunup
    closure). ``None`` is treated as pass: some Symbols legitimately
    have no stable_id (e.g., the message_dispatch sender/handler stand-
    ins explicitly set it to ``None`` per their docstrings). The
    ``axis_conformance`` validator owns required-field presence.
    """
    violations: list[ValidationViolation] = []
    for sym in symbols:
        sym_id = getattr(sym, "id", None)
        stable_id = getattr(sym, "stable_id", None)
        if stable_id is None:
            continue
        if not isinstance(stable_id, str):  # pragma: no cover - defensive
            continue
        if _CANONICAL_STABLE_ID_PATTERN.match(stable_id):
            continue
        problem = _classify_stable_id_format_problem(stable_id)
        violations.append(ValidationViolation(
            severity="error",
            validator_class="id_format",
            field_name="Symbol.stable_id",
            record_id=sym_id,
            observed=stable_id,
            expected="sha256:<16hex>",
            message=(
                f"Symbol.stable_id does not match the canonical schema: "
                f"{problem}. Use a make_*_stable_id factory from "
                "analyze/base.py (or _short_sha256 directly) rather than "
                "constructing stable_ids with f-strings or raw "
                "hashlib.sha256(...).hexdigest()."
            ),
        ))
    return violations


# ----------------------------------------------------------------------
# id-format:F3 — Symbol.id round-trip canary (ADR-0036 Rulings 1-3)
# ----------------------------------------------------------------------
#
# The shape-only ``_CANONICAL_ID_PATTERN`` proves an id has the 5-segment
# grammar but says nothing about the SEMANTICS of the kind / name / span
# slots. This sub-check closes the round-trip: parse the last three
# colon-free tokens (span, name, kind) per ADR-0036 Ruling 1 and assert
# they round-trip to the Symbol's own fields and the symbol-kind registry.
#
# Severity gating (scope A of the Wave-2 identity train): the kind-slot
# membership, kind-slot==Symbol.kind, and non-empty-name checks land at
# ADVISORY (``_ID_ROUNDTRIP_ADVISORY``) because a strict pass red-flags a
# known, id-CHANGING (T1) backlog that cannot clear before the v6 stable_id
# bump:
#   * the ~1645 external_symbol kind-slot disagreements (WI-pubiv),
#   * the route/event role-disagreement cohort (WI-kugaj), and
#   * the tsconfig id kind-slot (DEPRECATE-NO-FOLD per audit-findings 0005;
#     the producer folded Symbol.kind->"file" but left "tsconfig" in the id).
# Those folds change node.id, so they ride the v6 migration. Advisory
# severity makes the violations visible/measurable now; the gating tracker
# item promotes them to error once the backlog clears. The span start<=end
# check has no such backlog and lands at error directly.

_ID_ROUNDTRIP_ADVISORY = "warning"


def _check_id_roundtrip(symbols: Iterable[Any]) -> list[ValidationViolation]:
    """Round-trip conformance for canonical Symbol.ids (id-format:F3).

    Runs only on ids that already pass ``_CANONICAL_ID_PATTERN`` — shape
    failures are owned by ``_check_id_format`` and a non-canonical id
    cannot be parsed safely. For the rest, parse the last three colon-free
    tokens (span, name, kind) via ``rsplit(":", 3)`` (the IR's
    last-3-tokens grammar; the path slot may itself contain colons) and
    check:

    * **kind-slot registry membership** (advisory) — the kind token is a
      registered symbol-kind. Net value over the shape-only pattern: it
      catches divergences like ``tsconfig`` that ``axis_conformance`` is
      blind to (the *Symbol.kind* field carries the registered ``file``
      while the id slot kept the stale ``tsconfig``).
    * **kind-slot == Symbol.kind** (advisory) — the id round-trips to the
      Symbol's own kind. Skipped when ``Symbol.kind`` is absent / None
      (required-field presence is ``axis_conformance``'s job).
    * **name-slot non-empty** (advisory).
    * **span start <= end** (error) — a genuine malformation with no
      id-changing backlog.

    See ADR-0036 Rulings 1-3 and the module comment above for the
    advisory-vs-error severity gating.
    """
    from .symbol_kinds import all_symbol_kind_names

    registered = all_symbol_kind_names()
    violations: list[ValidationViolation] = []
    for sym in symbols:
        sym_id = getattr(sym, "id", None)
        if not isinstance(sym_id, str):
            continue
        if not _CANONICAL_ID_PATTERN.match(sym_id):
            # Shape failures are owned by _check_id_format; the canonical
            # match guarantees the rsplit below yields the span / name /
            # kind slots, so we only parse ids that matched.
            continue
        _lang_path, span_slot, name_slot, kind_slot = sym_id.rsplit(":", 3)

        if kind_slot not in registered:
            violations.append(ValidationViolation(
                severity=_ID_ROUNDTRIP_ADVISORY,
                validator_class="id_format",
                field_name="Symbol.id",
                record_id=sym_id,
                observed=kind_slot,
                expected="a registered symbol-kind (symbol_kinds.all_symbol_kind_names())",
                message=(
                    f"Symbol.id kind-slot {kind_slot!r} is not a registered "
                    "symbol-kind. Register it in symbol_kinds.py or fold the "
                    "producer to a canonical kind via make_symbol_id(...)."
                ),
            ))

        node_kind = getattr(sym, "kind", None)
        if node_kind is not None and kind_slot != node_kind:
            violations.append(ValidationViolation(
                severity=_ID_ROUNDTRIP_ADVISORY,
                validator_class="id_format",
                field_name="Symbol.id",
                record_id=sym_id,
                observed=kind_slot,
                expected=str(node_kind),
                message=(
                    f"Symbol.id kind-slot {kind_slot!r} does not match "
                    f"Symbol.kind {node_kind!r} (round-trip violation). Rebuild "
                    "the id via make_symbol_id(...) with the symbol's own kind."
                ),
            ))

        if name_slot == "":
            violations.append(ValidationViolation(
                severity=_ID_ROUNDTRIP_ADVISORY,
                validator_class="id_format",
                field_name="Symbol.id",
                record_id=sym_id,
                observed=sym_id,
                expected="a non-empty name-slot",
                message=(
                    "Symbol.id name-slot is empty; make_symbol_id(...) requires "
                    "a non-empty name (file pseudo-symbols use the literal 'file')."
                ),
            ))

        start_str, end_str = span_slot.split("-")
        if int(start_str) > int(end_str):
            violations.append(ValidationViolation(
                severity="error",
                validator_class="id_format",
                field_name="Symbol.id",
                record_id=sym_id,
                observed=span_slot,
                expected="start <= end",
                message=(
                    f"Symbol.id span {span_slot!r} has start {start_str} greater "
                    f"than end {end_str}; spans must satisfy start <= end."
                ),
            ))
    return violations


# ----------------------------------------------------------------------
# validator:F2 — referential-integrity FK predicate (WI-moriz keystone;
# regression guard for WI-mosil + synthetic:F1)
# ----------------------------------------------------------------------
#
# Every Symbol and Edge carries an ``origin_run_id`` naming the AnalysisRun
# that produced it (ir.py Symbol.origin_run_id / Edge.origin_run_id). The
# provenance join node -> AnalysisRun is only sound if that id resolves:
# WI-mosil fixed ~96 direct-constructor analyzer symbols left with an EMPTY
# origin_run_id, and synthetic:F1 stamped AnalysisRun provenance for the
# orchestrator/boundary synthesis producers. This predicate is the matching
# regression guard — it re-derives the FK at validation time so a producer
# regression that drops provenance (empty) or names a non-existent run
# (dangling) trips CI instead of silently breaking the join. Both sub-cases
# emit ``cross_field`` (the existing home of FK-style predicates, e.g. the
# ADR-0037 is_resolved<->dst check).
#
# CONTENT-GATED on a non-empty run set: ``validate_ir`` is called with
# ``analysis_runs=[]`` by every isolated unit fixture in the smoke suite (no
# run set to validate against), so gating on "runs exist" scopes the check to
# the production/integration path with zero unit-fixture collateral. The
# LEGACY_DESERIALIZED_SENTINEL is exempt: it is the stand-in
# Symbol/Edge.from_dict substitutes when re-hydrating legacy behavior-map JSON
# that predates the field, NOT a producer defect.
_ORIGIN_FK_CLASS = "cross_field"


def _check_origin_run_id_fk(
    symbols: Iterable[Any],
    edges: Iterable[Any],
    analysis_runs: Iterable[Any],
) -> list[ValidationViolation]:
    """Referential integrity: origin_run_id must reference a real AnalysisRun.

    See ADR-0033 §"Validator classes" #3 (cross-field) and the validator:F2
    family. Content-gated on ``analysis_runs`` being non-empty.
    """
    from .ir import LEGACY_DESERIALIZED_SENTINEL

    runs = list(analysis_runs)
    if not runs:
        return []
    valid_run_ids = {_read(r, "execution_id", None) for r in runs}
    valid_run_ids.discard(None)
    valid_run_ids.discard("")

    violations: list[ValidationViolation] = []
    for record_class, items in (("Symbol", symbols), ("Edge", edges)):
        for obj in items:
            origin_run_id = _read(obj, "origin_run_id", "")
            if origin_run_id == LEGACY_DESERIALIZED_SENTINEL:
                continue
            record_id = _read(obj, "id", None)
            field_name = f"{record_class}.origin_run_id"
            if not origin_run_id:
                violations.append(ValidationViolation(
                    severity="error",
                    validator_class=_ORIGIN_FK_CLASS,
                    field_name=field_name,
                    record_id=record_id,
                    observed=repr(origin_run_id),
                    expected=(
                        "a non-empty origin_run_id naming the producing "
                        "AnalysisRun (WI-mosil: direct-constructor analyzers "
                        "must stamp the run's execution_id)."
                    ),
                    message=(
                        f"{record_class} {record_id!r} has an empty "
                        "origin_run_id; its node->AnalysisRun provenance join "
                        "is broken (WI-mosil regression)."
                    ),
                ))
            elif origin_run_id not in valid_run_ids:
                violations.append(ValidationViolation(
                    severity="error",
                    validator_class=_ORIGIN_FK_CLASS,
                    field_name=field_name,
                    record_id=record_id,
                    observed=repr(origin_run_id),
                    expected=(
                        "origin_run_id must match an AnalysisRun.execution_id "
                        "in this artifact's analysis_runs."
                    ),
                    message=(
                        f"{record_class} {record_id!r} names origin_run_id "
                        f"{origin_run_id!r} with no matching AnalysisRun "
                        "(dangling provenance FK)."
                    ),
                ))
    return violations


# ----------------------------------------------------------------------
# WI-mujor — dangling-endpoint referential-integrity predicate
# ----------------------------------------------------------------------
#
# Every Edge.src / Edge.dst must reference a node id present in the symbol
# set. Post-boundary-synthesis the graph is endpoint-closed by construction:
# every unresolved dst is materialised as an external_symbol placeholder node
# (the ~27% placeholder-dst cohort; the dst slot carries that placeholder's
# id), and the ADR-0037 edge-finalization train closed the src side — the 23
# tier-filtered src-dangling edges WI-mujor was filed against (substrate
# a32c4a31) no longer reproduce: 0 dangling on default / --max-tier 1 /
# --include-docs as re-measured 2026-06-17. This predicate is the standing
# regression guard the Wave-2 gate requires: a producer/filter regression that
# drops a referenced node (so an edge points at a now-absent id) trips CI
# instead of silently passing the validator (the WI-moriz false-all-clear
# class). It is exactly the dst-absent ("dangling") half deliberately left out
# of the is_resolved<->dst FK check (see _check_cross_field_coherence's edge
# loop), now its own predicate covering both endpoints.
#
# CONTENT-GATED on a non-empty run set, identically to _check_origin_run_id_fk:
# every isolated unit fixture calls validate_ir with analysis_runs=[] and
# routinely points an edge outside its tiny symbol set; gating on "runs exist"
# scopes the check to the production/integration path with zero unit-fixture
# collateral. An empty/None endpoint is NOT flagged here (that is the
# dst_ref<->dst coherence predicate's concern) — only a non-empty-but-unresolved
# reference is "dangling", so the two predicates never double-count.
_DANGLING_ENDPOINT_CLASS = "cross_field"


def _check_dangling_endpoint(
    symbols: Iterable[Any],
    edges: Iterable[Any],
    analysis_runs: Iterable[Any],
) -> list[ValidationViolation]:
    """Referential integrity: every non-empty Edge.src/Edge.dst resolves to a
    node in the symbol set. Content-gated on ``analysis_runs`` being non-empty
    (WI-mujor; the dst-absent half deferred from the ADR-0037 is_resolved<->dst
    FK check)."""
    runs = list(analysis_runs)
    if not runs:
        return []
    node_ids = {_read(s, "id", None) for s in symbols}
    node_ids.discard(None)

    violations: list[ValidationViolation] = []
    for edge in edges:
        edge_id = _read(edge, "id", None)
        for slot in ("src", "dst"):
            endpoint = _read(edge, slot, None)
            if not endpoint or endpoint in node_ids:
                continue
            violations.append(ValidationViolation(
                severity="error",
                validator_class=_DANGLING_ENDPOINT_CLASS,
                field_name=f"Edge.{slot}",
                record_id=edge_id,
                observed=f"{slot}={endpoint!r}",
                expected=(
                    f"Edge.{slot} must reference a node id present in the "
                    "symbol set (endpoint-closed by boundary synthesis + "
                    "ADR-0037 edge finalization)."
                ),
                message=(
                    f"Edge {edge_id!r} has a dangling {slot} {endpoint!r} "
                    "with no matching node — its endpoint->node join is "
                    "broken (WI-mujor endpoint-integrity guard)."
                ),
            ))
    return violations


# ----------------------------------------------------------------------
# WI-vudul — fingerprint output-boundary format guard
# ----------------------------------------------------------------------
#
# Symbol.fingerprint is contractually the canonical ``hgfp2:<16hex>`` scheme on
# real source nodes (a whitespace/comment-invariant structural-subtree hash).
# WI-lisog made the central post-pass (``stamp_symbol_fingerprints``) NORMALIZE
# producer-side non-canonical values, and WI-vudul deleted the ~29 dead
# producer bare-hex computations the normalizer was overwriting. This is the
# matching OUTPUT-boundary guard: it asserts the contract holds on the emitted
# substrate, so a future normalization-pass regression (or a producer path the
# normalizer doesn't reach) that lets a bare hex fingerprint survive to output
# trips CI instead of silently shipping a non-canonical identity value
# (INV-kurup's "fields emit non-canonical formats" class, at the fingerprint
# field). Class-B synthetic stand-ins (``language is None``) carry an
# identity-hash second shape (ADR-0031 / WI-lisog) and are EXEMPT; a ``None``
# fingerprint (synthetic external_symbol / file nulls) is skipped.
#
# CONTENT-GATED on a non-empty run set, like the sibling FK/endpoint predicates:
# isolated unit fixtures call ``validate_ir`` with ``analysis_runs=[]`` and some
# carry bare placeholder fingerprints (``fingerprint="fp1"``) on language-typed
# Symbols; gating on "runs exist" scopes the check to the production path with
# zero unit-fixture collateral.
_FINGERPRINT_FORMAT_CLASS = "id_format"


def _check_fingerprint_format(
    symbols: Iterable[Any],
    edges: Iterable[Any],
    analysis_runs: Iterable[Any],
) -> list[ValidationViolation]:
    """Output-boundary guard: every non-null Symbol.fingerprint on a real
    source node (``language is not None``) carries the canonical ``hgfp2:``
    prefix. Content-gated on ``analysis_runs`` (WI-vudul; WI-lisog single-shape
    contract)."""
    runs = list(analysis_runs)
    if not runs:
        return []
    from .fingerprint import _SCHEME_PREFIX

    violations: list[ValidationViolation] = []
    for sym in symbols:
        fingerprint = _read(sym, "fingerprint", None)
        if fingerprint is None:
            continue
        if _read(sym, "language", None) is None:
            continue  # Class-B identity-hash second shape (ADR-0031)
        if str(fingerprint).startswith(_SCHEME_PREFIX):
            continue
        violations.append(ValidationViolation(
            severity="error",
            validator_class=_FINGERPRINT_FORMAT_CLASS,
            field_name="Symbol.fingerprint",
            record_id=_read(sym, "id", None),
            observed=repr(fingerprint),
            expected=(
                f"a source-node fingerprint carrying the {_SCHEME_PREFIX!r} "
                "scheme prefix (the canonical structural-subtree hash)."
            ),
            message=(
                f"Symbol {_read(sym, 'id', None)!r} has a non-canonical "
                f"fingerprint {fingerprint!r} (no {_SCHEME_PREFIX!r} prefix) on "
                "a real source node — a producer bare-hex leak survived to "
                "output (WI-vudul / WI-lisog single-shape contract)."
            ),
        ))
    return violations


def build_validation_report(
    violations: list[ValidationViolation],
    *,
    stable_id_stats: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Construct the ``validation_report`` section for the artifact dict.

    Surfaced as a top-level key alongside ``schema_version``, ``symbols``,
    ``edges``, etc. The ``violations_by_class`` counter is computed here
    so consumers can read summary counts without iterating ``violations``.

    ``wired_checks`` (validator:F2, schema_version 0.3) discloses the set of
    predicates actually wired into ``validate_ir``, mapped to the
    ``validator_class`` each contributes to. It makes a 0 count interpretable:
    "0 instances of these named checks" rather than "0 defects of any kind".
    A defect class absent from this manifest is, by absence, not yet validated
    (WI-moriz: the false-all-clear is now structurally diagnosable).

    ``stable_id_stats`` (ADR-0035 §5, schema_version 0.2): the corpus
    stable_id population/collision/None-cohort disclosure (from
    ``compute_stable_id_stats``). Surfaced as a separate top-level key so
    it is ALWAYS present, independent of whether the collision umbrella
    fired. ``None`` on bare calls (unit tests); the production finalize
    path always supplies it.
    """
    by_class: dict[str, int] = dict.fromkeys(_VALIDATOR_CLASSES, 0)
    for v in violations:
        if v.validator_class in by_class:
            by_class[v.validator_class] += 1
    return {
        "schema_version": VALIDATION_REPORT_SCHEMA_VERSION,
        "violations": [asdict(v) for v in violations],
        "violations_by_class": by_class,
        "wired_checks": [dict(c) for c in _WIRED_CHECKS],
        "stable_id_stats": stable_id_stats,
    }


def emit_stderr_summary(violations: list[ValidationViolation]) -> None:
    """Write a one-line per-class summary of violations to stderr.

    Silent on empty input — the default ``hypergumbo run`` path should
    produce no validator chatter for a clean corpus. Each non-zero class
    gets one ``[warn]`` line.
    """
    if not violations:
        return
    counts: dict[str, int] = {}
    for v in violations:
        counts[v.validator_class] = counts.get(v.validator_class, 0) + 1
    for cls, count in counts.items():
        sys.stderr.write(
            f"[warn] {count} {cls} validation violation(s); "
            "see validation_report in artifact\n"
        )
