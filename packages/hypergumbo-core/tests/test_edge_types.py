# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the canonical edge-type registry and drift detection.

The drift test ASTwalks the package source tree, finds every set or
frozenset literal whose elements are all string constants, and asserts
that any such set whose elements overlap the canonical registry is a
subset of the registry. This catches the silent-bug shape ADR-0023
identified — consumer-side hardcoded sets that accumulate values not
in the canonical schema (or miss values that should be in the set).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from hypergumbo_core.edge_types import (
    AXIS_ENDPOINT_SHAPE,
    AXIS_PENDING,
    AXIS_RELATIONSHIP,
    EDGE_TYPES,
    EdgeTypeSpec,
    VALID_AXES,
    all_edge_type_names,
    edge_types_on_axis,
    find_edge_type,
)


# --- Registry invariants ---

def test_registry_has_no_duplicate_names():
    names = [spec.name for spec in EDGE_TYPES]
    assert len(names) == len(set(names))


def test_every_spec_has_a_valid_axis():
    for spec in EDGE_TYPES:
        assert spec.axis in VALID_AXES, (
            f"{spec.name} has invalid axis {spec.axis!r}"
        )


def test_every_spec_has_nonempty_description():
    for spec in EDGE_TYPES:
        assert spec.description, f"{spec.name} has empty description"


def test_depends_on_and_depends_on_manifest_are_distinct_relationships():
    # WI-dinih verdict (KEEP distinct, NOT a fold): depends_on and
    # depends_on_manifest are different relationships, not aliases.
    # depends_on = a package/build manifest DECLARING its dependency
    # (json_config/make/meson/...); depends_on_manifest = the dependency
    # linker RESOLVING an importing source file to a manifest-declared dep
    # (evidence_type=import_to_manifest). Different src kinds, evidence_type,
    # and producer pass -- folding them would conflate declaration with
    # resolution. The strategy's vocab:F1 prose calls them an "alias pair";
    # this test LOCKS the keep-distinct verdict so any future fold is a
    # deliberate decision rather than a silent re-litigation.
    on = find_edge_type("depends_on")
    manifest = find_edge_type("depends_on_manifest")
    assert on is not None and manifest is not None
    assert on.name != manifest.name
    assert on.axis == AXIS_RELATIONSHIP
    assert manifest.axis == AXIS_RELATIONSHIP


def test_specs_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        EDGE_TYPES[0].name = "mutated"  # type: ignore[misc]


def test_valid_axes_constant_matches_module_constants():
    assert VALID_AXES == frozenset(
        {AXIS_RELATIONSHIP, AXIS_ENDPOINT_SHAPE, AXIS_PENDING},
    )


# --- Accessors ---

def test_all_edge_type_names_returns_frozenset():
    names = all_edge_type_names()
    assert isinstance(names, frozenset)
    assert "calls" in names
    assert "extends" in names


def test_edge_types_on_axis_returns_only_matching():
    rels = edge_types_on_axis(AXIS_RELATIONSHIP)
    assert all(spec.axis == AXIS_RELATIONSHIP for spec in rels)
    names = {spec.name for spec in rels}
    # Cross-check a handful of clearly-relationship-shaped values.
    assert {"calls", "extends", "implements", "imports"} <= names


def test_edge_types_on_axis_endpoint_shape_includes_known_deprecation_candidates():
    endpoints = {spec.name for spec in edge_types_on_axis(AXIS_ENDPOINT_SHAPE)}
    # Representative endpoint-shaped values still in the registry. The
    # protocol-call trio (http_calls / grpc_calls / graphql_calls) was
    # producer-migrated in WI-vumum-juvil and stays in the registry as
    # deprecated until Phase 4b' prunes them.
    assert {
        "http_calls", "grpc_calls", "graphql_calls", "script_src",
    } <= endpoints


def test_edge_types_on_axis_unknown_returns_empty():
    assert edge_types_on_axis("not-an-axis") == ()


def test_find_edge_type_returns_spec():
    spec = find_edge_type("calls")
    assert spec is not None
    assert spec.name == "calls"
    assert spec.axis == AXIS_RELATIONSHIP


def test_find_edge_type_unknown_returns_none():
    assert find_edge_type("not-a-real-edge-type") is None


def test_edge_type_spec_is_dataclass():
    assert dataclasses.is_dataclass(EdgeTypeSpec)


# --- Drift detection ---

def test_every_edge_type_named_set_is_a_subset_of_registry():
    """Every set whose target name contains ``EDGE_TYPE`` must contain
    only values from the canonical registry. Catches the silent-bug
    pattern from ADR-0023 — consumer-side hardcoded sets that drift
    from what the analyzers actually emit.

    Delegates to ``edge_types.find_axis_drift`` so the same logic
    powers the pre-commit linter at ``scripts/check-edge-type-drift``.
    """
    from hypergumbo_core.edge_types import find_axis_drift

    repo_root = Path(__file__).resolve().parents[3]
    offenders = find_axis_drift(repo_root)

    assert not offenders, (
        "Hardcoded edge-type sets contain values absent from the "
        "canonical registry "
        "(packages/hypergumbo-core/src/hypergumbo_core/edge_types.py).\n"
        "Either add the missing values to the registry (with an axis "
        "classification) or remove them from the consumer set:\n"
        + "\n".join(offenders)
    )


# --- Unit tests for find_axis_drift / _iter_edge_type_set_assignments ---

def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_find_axis_drift_returns_empty_when_no_packages_dir(tmp_path: Path):
    from hypergumbo_core.edge_types import find_axis_drift

    assert find_axis_drift(tmp_path) == []


def test_find_axis_drift_finds_drift_in_set_literal(tmp_path: Path):
    from hypergumbo_core.edge_types import find_axis_drift

    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_DRIFT_EDGE_TYPES = {"calls", "not-a-real-edge-type"}\n',
    )
    offenders = find_axis_drift(tmp_path)
    assert len(offenders) == 1
    assert "_DRIFT_EDGE_TYPES" in offenders[0]
    assert "not-a-real-edge-type" in offenders[0]


def test_find_axis_drift_finds_drift_in_frozenset_literal(tmp_path: Path):
    from hypergumbo_core.edge_types import find_axis_drift

    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_DRIFT_EDGE_TYPES = frozenset({"calls", "phantom-value"})\n',
    )
    offenders = find_axis_drift(tmp_path)
    assert len(offenders) == 1
    assert "phantom-value" in offenders[0]


def test_find_axis_drift_clean_set_is_not_flagged(tmp_path: Path):
    from hypergumbo_core.edge_types import find_axis_drift

    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_CLEAN_EDGE_TYPES = {"calls", "extends", "implements"}\n',
    )
    assert find_axis_drift(tmp_path) == []


def test_find_axis_drift_skips_test_directories(tmp_path: Path):
    from hypergumbo_core.edge_types import find_axis_drift

    _write(
        tmp_path / "packages" / "demo" / "tests" / "test_x.py",
        '_DRIFT_EDGE_TYPES = {"calls", "fixture-only-value"}\n',
    )
    assert find_axis_drift(tmp_path) == []


def test_find_axis_drift_ignores_unrelated_set_names(tmp_path: Path):
    """Programming-language keyword sets coincidentally sharing an
    element with the registry must not trigger drift detection."""
    from hypergumbo_core.edge_types import find_axis_drift

    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_KEYWORDS = {"extends", "implements", "class", "interface"}\n',
    )
    assert find_axis_drift(tmp_path) == []


def test_find_axis_drift_handles_annotated_assignment(tmp_path: Path):
    from hypergumbo_core.edge_types import find_axis_drift

    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_X_EDGE_TYPES: frozenset[str] = frozenset({"calls", "bogus"})\n',
    )
    offenders = find_axis_drift(tmp_path)
    assert len(offenders) == 1
    assert "bogus" in offenders[0]


def test_find_axis_drift_skips_non_string_elements(tmp_path: Path):
    """Sets with non-string elements (numeric literals, expressions)
    are not treated as edge-type sets."""
    from hypergumbo_core.edge_types import find_axis_drift

    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_EDGE_TYPE_BUDGETS = {1, 2, 3}\n',
    )
    assert find_axis_drift(tmp_path) == []


def test_find_axis_drift_skips_empty_set(tmp_path: Path):
    from hypergumbo_core.edge_types import find_axis_drift

    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_EDGE_TYPES_EMPTY = frozenset()\n',
    )
    assert find_axis_drift(tmp_path) == []


def test_find_axis_drift_skips_multi_target_assignments(tmp_path: Path):
    """``a = b = {...}`` shouldn't be matched — too unusual to handle."""
    from hypergumbo_core.edge_types import find_axis_drift

    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'A_EDGE_TYPES = B_EDGE_TYPES = {"calls", "bogus"}\n',
    )
    # Multi-target assignment doesn't match the single-target rule.
    assert find_axis_drift(tmp_path) == []


def test_find_axis_drift_ignores_sets_without_edge_type_in_name(tmp_path: Path):
    from hypergumbo_core.edge_types import find_axis_drift

    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_RANDOM_NAME = {"calls", "bogus-value"}\n',
    )
    assert find_axis_drift(tmp_path) == []


def test_find_axis_drift_handles_frozenset_with_list_arg(tmp_path: Path):
    from hypergumbo_core.edge_types import find_axis_drift

    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_X_EDGE_TYPES = frozenset(["calls", "bogus"])\n',
    )
    offenders = find_axis_drift(tmp_path)
    assert len(offenders) == 1
    assert "bogus" in offenders[0]


def test_find_axis_drift_skips_annotated_assignment_without_value(tmp_path: Path):
    """``_X_EDGE_TYPES: frozenset[str]`` with no right-hand side parses
    as ``AnnAssign`` with ``value=None``; the walker must skip these
    cleanly rather than crashing on the missing value."""
    from hypergumbo_core.edge_types import find_axis_drift

    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_X_EDGE_TYPES: frozenset[str]\n',
    )
    assert find_axis_drift(tmp_path) == []


# --- Strict-mode wrapper (WI-variv-lujug) ---


def test_find_axis_drift_strict_no_off_axis_consumers():
    """Strict mode reports zero off-axis consumer references after
    WI-vumum-juvil. The protocol-call trio (http_calls / grpc_calls
    / graphql_calls) was the last endpoint_shape family with consumer
    references; this Phase 3 fold dropped http_calls from compact's
    CROSS_CUTTING_EDGE_TYPES and grpc_calls from taint /
    io_boundary's traceable sets, leaving only relationship- and
    pending_classification-axis values in the consumer surfaces.

    This test pins the empty list so a future regression that adds
    an off-axis reference fails loudly. If a new Phase-3-pending
    family appears, this test should fail and the failure message
    should be triaged against the registry: either complete the fold
    (drop the consumer reference + producer migration) or update
    this test with the corresponding tracker item explaining why
    the off-axis reference is load-bearing.
    """
    from hypergumbo_core.edge_types import find_axis_drift

    repo_root = Path(__file__).resolve().parents[3]
    offenders = find_axis_drift(repo_root, strict=True)

    assert offenders == [], (
        "Strict-mode off-axis offenders appeared:\n"
        + "\n".join(offenders)
    )


def test_find_axis_drift_strict_flags_endpoint_shape_value(tmp_path: Path):
    """If a consumer set references an endpoint_shape value (which
    Phase 4b should have removed everywhere), strict mode flags it."""
    from hypergumbo_core.edge_types import find_axis_drift

    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        # 'http_calls' is endpoint_shape per the post-Phase-4b registry
        # (its protocol-specific linker hasn't been migrated yet).
        '_DEMO_EDGE_TYPES = frozenset({"calls", "http_calls"})\n',
    )
    # Default (lax) mode: registered value, no drift.
    assert find_axis_drift(tmp_path) == []
    # Strict mode: http_calls is endpoint_shape → off-axis.
    offenders = find_axis_drift(tmp_path, strict=True)
    assert len(offenders) == 1
    assert "http_calls" in offenders[0]
    assert "not on allowed axis" in offenders[0]


def test_find_axis_drift_strict_allows_pending_classification(tmp_path: Path):
    """Permissive-pending design: pending_classification values stay
    allowed because they are real edges produced by GraphQL/OpenAPI/
    RPC analyzers awaiting per-family audit. Existing consumers (e.g.,
    io_boundary._TRACEABLE_EDGE_TYPES referencing implements_rpc)
    must not be flagged by the strict-mode default."""
    from hypergumbo_core.edge_types import find_axis_drift

    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_DEMO_EDGE_TYPES = frozenset({"calls", "implements_rpc"})\n',
    )
    assert find_axis_drift(tmp_path, strict=True) == []
