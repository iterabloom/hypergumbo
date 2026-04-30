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
    # ADR-0023's representative endpoint-shaped values.
    assert {
        "native_bridge", "message_send", "http_calls", "imports_module",
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
