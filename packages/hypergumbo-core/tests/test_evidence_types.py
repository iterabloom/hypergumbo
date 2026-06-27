# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the canonical Edge.evidence_type registry and drift detection.

The drift test AST-walks the package source tree, finds every set or
frozenset literal whose elements are all string constants, and asserts
that any such set whose target name contains ``EVIDENCE_TYPE`` is a
subset of the canonical registry. Catches the silent-bug shape ADR-0028
identified — consumer-side hardcoded sets that accumulate values not in
the canonical schema (or miss values that should be in the set).

Round-trip tests for ``Edge.is_resolved`` (the new sibling field per
ADR-0028) live here too: producer→to_dict→from_dict must preserve the
boolean, and a pre-0.4.2-shaped dict (no ``is_resolved`` key) must
default to ``True``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from hypergumbo_core.evidence_types import (
    AXIS_INFERENCE_PATHWAY,
    AXIS_PENDING,
    EVIDENCE_TYPES,
    EvidenceTypeSpec,
    VALID_AXES,
    all_evidence_type_names,
    evidence_types_on_axis,
    find_evidence_type,
)
from hypergumbo_core.ir import Edge


# --- Registry invariants ---

def test_registry_has_no_duplicate_names():
    names = [spec.name for spec in EVIDENCE_TYPES]
    assert len(names) == len(set(names))


def test_every_spec_has_a_valid_axis():
    for spec in EVIDENCE_TYPES:
        assert spec.axis in VALID_AXES, (
            f"{spec.name} has invalid axis {spec.axis!r}"
        )


def test_every_spec_has_nonempty_description():
    for spec in EVIDENCE_TYPES:
        assert spec.description, f"{spec.name} has empty description"


def test_specs_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        EVIDENCE_TYPES[0].name = "mutated"  # type: ignore[misc]


def test_valid_axes_constant_matches_module_constants():
    assert VALID_AXES == frozenset(
        {AXIS_INFERENCE_PATHWAY, AXIS_PENDING},
    )


def test_inference_pathway_axis_is_non_empty():
    """Cluster A (canonical inference labels) is the seed of the
    registry. An empty inference_pathway section signals a packaging /
    loading failure."""
    assert evidence_types_on_axis(AXIS_INFERENCE_PATHWAY)


def test_endpoint_shape_axis_is_retired():
    """Phase 4b: the ``endpoint_shape`` axis was retired in PR #3635.
    The axis literal must not be re-introduced as a live spec
    classification, and the retired constant must remain ABSENT from
    :data:`VALID_AXES`. The constant itself stays defined as a
    backwards-compat import target for
    :mod:`hypergumbo_core.audit_findings`'s per-axis validator
    binding."""
    import hypergumbo_core.evidence_types as et
    assert et.AXIS_ENDPOINT_SHAPE == "endpoint_shape"
    assert et.AXIS_ENDPOINT_SHAPE not in VALID_AXES
    assert et.AXIS_ENDPOINT_SHAPE not in {spec.axis for spec in EVIDENCE_TYPES}


# --- Accessors ---

def test_all_evidence_type_names_returns_frozenset():
    names = all_evidence_type_names()
    assert isinstance(names, frozenset)
    assert "ast_call_direct" in names
    assert "scip_occurrence_ref" in names


def test_evidence_types_on_axis_returns_only_matching():
    pathways = evidence_types_on_axis(AXIS_INFERENCE_PATHWAY)
    assert all(spec.axis == AXIS_INFERENCE_PATHWAY for spec in pathways)
    names = {spec.name for spec in pathways}
    # Cross-check a handful of canonical inference labels.
    assert {"ast_call_direct", "ast_import", "ast_extends",
            "naming_convention", "scip_occurrence_ref"} <= names


def test_evidence_types_on_axis_endpoint_shape_removed_after_phase_4b():
    """Phase 4b complement of the prior 'includes deprecation
    candidates' assertion: every value that occupied
    ``AXIS_ENDPOINT_SHAPE`` during the Phase 4a window must be absent
    from the registry. Cluster B (resolution-status) values folded to
    canonical inference + ``Edge.is_resolved=False``; Cluster C
    (framework-dispatch) values folded to canonical inference +
    ``meta['framework_dispatch']`` / ``meta['detection_pattern']``;
    Cluster D (call-construct) values folded to ``ast_call`` apex +
    ``meta['call_construct']``."""
    names = all_evidence_type_names()
    retired = {
        "ast_annotation_unresolved", "unresolved_external_call",  # B
        "django_orm_dispatch", "kafka_streams_dispatch",  # C
        "function_call", "method_call",  # D
    }
    assert not (retired & names), (
        f"Retired Phase-4b evidence types reappeared in registry: "
        f"{sorted(retired & names)}"
    )


def test_evidence_types_on_axis_unknown_returns_empty():
    assert evidence_types_on_axis("not-an-axis") == ()


def test_find_evidence_type_returns_spec():
    spec = find_evidence_type("ast_call_direct")
    assert spec is not None
    assert spec.name == "ast_call_direct"
    assert spec.axis == AXIS_INFERENCE_PATHWAY


def test_find_evidence_type_unknown_returns_none():
    assert find_evidence_type("not-a-real-evidence-type") is None


def test_ast_call_method_folded_to_ast_call_apex():
    """vocab:F1 / WI-nibis (audit-findings 0012 Cluster 28D): the parked peer
    ``ast_call_method`` folds to the ``ast_call`` apex + ``meta['call_construct']``;
    it is no longer a registry member."""
    assert find_evidence_type("ast_call_method") is None
    assert find_evidence_type("ast_call") is not None


def test_evidence_type_spec_is_dataclass():
    assert dataclasses.is_dataclass(EvidenceTypeSpec)


# --- Drift detection (live tree) ---

def test_every_evidence_type_named_set_is_a_subset_of_registry():
    """Every set whose target name contains ``EVIDENCE_TYPE`` must
    contain only values from the canonical registry. Catches the
    silent-bug pattern from ADR-0028 — consumer-side hardcoded sets
    that drift from what the analyzers actually emit.

    Delegates to ``evidence_types.find_axis_drift`` so the same logic
    powers the pre-commit linter at
    ``scripts/check-evidence-type-drift``.
    """
    from hypergumbo_core.evidence_types import find_axis_drift

    repo_root = Path(__file__).resolve().parents[3]
    offenders = find_axis_drift(repo_root)

    assert not offenders, (
        "Hardcoded Edge.evidence_type sets contain values absent from "
        "the canonical registry "
        "(packages/hypergumbo-core/src/hypergumbo_core/evidence_types.py).\n"
        "Either add the missing values to the registry (with an axis "
        "classification) or remove them from the consumer set:\n"
        + "\n".join(offenders)
    )


# --- Synthetic-fixture drift tests ---

def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_find_axis_drift_returns_empty_when_no_packages_dir(tmp_path: Path):
    from hypergumbo_core.evidence_types import find_axis_drift
    assert find_axis_drift(tmp_path) == []


def test_find_axis_drift_finds_drift_in_set_literal(tmp_path: Path):
    from hypergumbo_core.evidence_types import find_axis_drift

    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_DRIFT_EVIDENCE_TYPES = {"ast_call_direct", "not-a-real-evidence"}\n',
    )
    offenders = find_axis_drift(tmp_path)
    assert len(offenders) == 1
    assert "_DRIFT_EVIDENCE_TYPES" in offenders[0]
    assert "not-a-real-evidence" in offenders[0]


def test_find_axis_drift_clean_set_is_not_flagged(tmp_path: Path):
    from hypergumbo_core.evidence_types import find_axis_drift

    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_CLEAN_EVIDENCE_TYPES = {"ast_call_direct", "ast_import", "ast_extends"}\n',
    )
    assert find_axis_drift(tmp_path) == []


# --- Edge.is_resolved sibling-field round-trip (ADR-0028) ---

def test_edge_is_resolved_default_is_true():
    """Per ADR-0028 §"Sibling-field design call-out", the dominant
    case is `is_resolved=True` (~90% of edges have well-determined
    dsts). Producers explicitly set `is_resolved=False` only in the
    unresolved case."""
    e = Edge.create("a", "b", "calls", 1, origin="test", origin_run_id="test")
    assert e.is_resolved is True


def test_edge_is_resolved_round_trip_true():
    e = Edge.create("a", "b", "calls", 1, is_resolved=True, origin="test", origin_run_id="test")
    d = e.to_dict()
    assert d["is_resolved"] is True
    e2 = Edge.from_dict(d)
    assert e2.is_resolved is True


def test_edge_is_resolved_round_trip_false():
    """Phase-3 Cluster B producers set this when dst is unresolved."""
    e = Edge.create("a", "b", "calls", 1, is_resolved=False, origin="test", origin_run_id="test")
    d = e.to_dict()
    assert d["is_resolved"] is False
    e2 = Edge.from_dict(d)
    assert e2.is_resolved is False


def test_edge_from_dict_missing_is_resolved_defaults_to_true():
    """Backward compatibility: pre-0.4.2 behavior maps don't carry
    `is_resolved`. The default-True path applies."""
    legacy_dict = {
        "id": "edge:sha256:deadbeef",
        "edge_key": "edgekey:sha256:deadbeef",
        "src": "a",
        "dst": "b",
        "type": "calls",
        "line": 1,
        "confidence": 0.85,
        "origin": "test",
        "origin_run_id": "run-1",
        "quality": None,
        "meta": {"evidence_type": "ast_call_direct"},
        # NOTE: no "is_resolved" key — pre-0.4.2 shape.
    }
    e = Edge.from_dict(legacy_dict)
    assert e.is_resolved is True
