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

import ast
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

def _edge_type_set_assignments_in_file(path: Path):
    """Yield (lineno, target_name, frozenset_of_string_elements) for
    every module-level assignment ``<NAME> = {...}`` or
    ``<NAME> = frozenset({...})`` where ``NAME`` contains ``EDGE_TYPE``
    and every element is a string literal.

    The name-substring filter avoids false positives from unrelated
    string sets (programming-language keyword vocabularies, stdlib
    method-name catalogs, etc.) that happen to share an element with
    the registry by coincidence."""
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):  # pragma: no cover
        return
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:  # pragma: no cover
        return

    for node in ast.walk(tree):
        target_name: str | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                target_name = node.targets[0].id
                value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_name = node.target.id
            value = node.value
        if target_name is None or "EDGE_TYPE" not in target_name:
            continue
        if value is None:
            continue

        elements: list[ast.expr] | None = None
        if isinstance(value, ast.Set):
            elements = list(value.elts)
        elif (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "frozenset"
            and len(value.args) == 1
            and isinstance(value.args[0], (ast.Set, ast.List, ast.Tuple))
        ):
            elements = list(value.args[0].elts)
        if not elements:
            continue

        values: list[str] = []
        all_strings = True
        for elt in elements:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                values.append(elt.value)
            else:
                all_strings = False
                break
        if all_strings and values:
            yield node.lineno, target_name, frozenset(values)


def test_every_edge_type_named_set_is_a_subset_of_registry():
    """Every set whose target name contains ``EDGE_TYPE`` must contain
    only values from the canonical registry. Catches the silent-bug
    pattern from ADR-0023 — consumer-side hardcoded sets that drift
    from what the analyzers actually emit. Test files are skipped
    because fixture data legitimately uses arbitrary string sets."""
    known_names = all_edge_type_names()
    repo_root = Path(__file__).resolve().parents[3]
    offenders: list[str] = []

    for py_file in (repo_root / "packages").rglob("*.py"):
        if "/tests/" in str(py_file):
            continue
        for lineno, target_name, values in _edge_type_set_assignments_in_file(
            py_file,
        ):
            drift = values - known_names
            if drift:
                rel = py_file.relative_to(repo_root)
                offenders.append(
                    f"{rel}:{lineno} ({target_name}): "
                    f"contains {sorted(drift)} not in canonical registry"
                )

    assert not offenders, (
        "Hardcoded edge-type sets contain values absent from the "
        "canonical registry "
        "(packages/hypergumbo-core/src/hypergumbo_core/edge_types.py).\n"
        "Either add the missing values to the registry (with an axis "
        "classification) or remove them from the consumer set:\n"
        + "\n".join(offenders)
    )
