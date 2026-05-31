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

Four validator classes are planned (ADR-0033 §"Validator classes"); this
Phase-0 module ships the scaffolding (the ``ValidationViolation`` dataclass,
the public ``validate_ir`` entry point, the pipeline wire-up at
``cli.py``'s end-of-pipeline post-pass slot) with all classes off. Each
class lands in a dedicated PR in Phase 3 of the campaign and progressively
populates the violations list:

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
   ``Symbol.display_label`` populated on synthetic stand-ins only.
4. **Verdict-enum completeness** — verdict-emitting code paths must enumerate
   an ``inconclusive`` (or equivalent) branch for missing-data cases.
   Folds in WI-rolol sub-task A (``ClaimVerdict.inconclusive``).

Why scaffold first
------------------
Landing the validator stage with no checks enabled is intentional. The
module's presence and the pipeline wire-up establish (a) the artifact's
``validation_report`` section as a stable surface that consumers and CI
can rely on, (b) the ``ValidationViolation`` dataclass as the structured
shape that all future check classes emit, and (c) the warn-not-fail
default behavior the campaign committed to. Subsequent PRs add one check
class each, with confidence that the wire-up is already validated by the
Phase-0 smoke test.

Default failure behavior
------------------------
The validator does NOT fail ``hypergumbo run`` by default. Violations are:

* Written into the artifact's ``validation_report`` section.
* Summarized to stderr (``"[warn] N axis-conformance violations; see
  validation_report in <artifact>"``).
* CI-gated by a separate test (``tests/test_validation_report_empty.py``)
  that runs the self-analysis corpus and fails when
  ``validation_report.violations`` is non-empty.

This soft-introduction posture lets the validator land cleanly: users see
violations as informational warnings; CI catches regressions; the
self-analysis dogfooding workflow drives the violation count to zero.

See ADR-0033 for the full architectural decision.
"""
from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Optional

VALIDATION_REPORT_SCHEMA_VERSION = "0.1"

# Stable enum-like sets. Mirrored in the ADR-0033 §"Output format" table
# and in `ValidationViolation.severity` / `validator_class` axis annotations.
_SEVERITIES = ("error", "warning", "info")
_VALIDATOR_CLASSES = (
    "axis_conformance",
    "writer_contract",
    "cross_field",
    "verdict_enum",
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
    validator_class: str  # axis: bounded-enum {"axis_conformance", "writer_contract", "cross_field", "verdict_enum"}
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

    Phase-0 stub: returns ``[]``. Each Phase-3 PR adds one class's checks
    to this function. The argument types are deliberately ``Iterable[Any]``
    rather than typed `Symbol`/`Edge`/`AnalysisRun` to keep this module
    decoupled from the IR module — `validate_ir` reads attributes by name
    and is therefore tolerant of dataclass-shape evolution.
    """
    violations: list[ValidationViolation] = []
    # Phase 3 PR1 — axis-conformance checks land here
    # Phase 3 PR2 — writer-contract checks land here
    # Phase 3 PR3 — cross-field coherence checks land here
    # Phase 3 PR4 — verdict-enum completeness checks land here
    return violations


def build_validation_report(
    violations: list[ValidationViolation],
) -> dict[str, Any]:
    """Construct the ``validation_report`` section for the artifact dict.

    Surfaced as a top-level key alongside ``schema_version``, ``symbols``,
    ``edges``, etc. The ``violations_by_class`` counter is computed here
    so consumers can read summary counts without iterating ``violations``.
    """
    by_class: dict[str, int] = dict.fromkeys(_VALIDATOR_CLASSES, 0)
    for v in violations:
        if v.validator_class in by_class:
            by_class[v.validator_class] += 1
    return {
        "schema_version": VALIDATION_REPORT_SCHEMA_VERSION,
        "violations": [asdict(v) for v in violations],
        "violations_by_class": by_class,
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
