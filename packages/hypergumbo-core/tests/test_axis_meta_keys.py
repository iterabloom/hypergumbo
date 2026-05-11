# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the canonical Symbol.meta / Edge.meta key registry.

Mirrors the invariant + accessor tests in ``test_symbol_kinds.py`` and
``test_evidence_types.py``, scoped to the simpler axis taxonomy
(``symbol_meta`` vs. ``edge_meta``; no ``pending_classification``
axis because meta keys are either named or not named — there's no
deferral state for a meta key).

Drift detection is intentionally out of scope here (see the module
docstring of :mod:`hypergumbo_core.axis_meta_keys` — meta-key access
shape differs from the set-literal shape that
``axis_drift.find_drift`` walks; a subscript-access drift linter is
filed as a follow-on).
"""

from __future__ import annotations

import dataclasses

import pytest

from hypergumbo_core.axis_meta_keys import (
    AXIS_EDGE_META,
    AXIS_SYMBOL_META,
    META_KEYS,
    MetaKeySpec,
    VALID_AXES,
    all_meta_key_names,
    find_meta_key,
    meta_keys_on_axis,
)


# --- Registry invariants ---

def test_registry_has_no_duplicate_names():
    names = [spec.name for spec in META_KEYS]
    assert len(names) == len(set(names))


def test_every_spec_has_a_valid_axis():
    for spec in META_KEYS:
        assert spec.axis in VALID_AXES, (
            f"{spec.name} has invalid axis {spec.axis!r}"
        )


def test_every_spec_has_nonempty_description():
    for spec in META_KEYS:
        assert spec.description, f"{spec.name} has empty description"


def test_specs_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        META_KEYS[0].name = "mutated"  # type: ignore[misc]


def test_valid_axes_constant_matches_module_constants():
    assert VALID_AXES == frozenset({AXIS_SYMBOL_META, AXIS_EDGE_META})


def test_every_axis_has_at_least_one_spec():
    """Each declared axis must have at least one registry entry — an
    empty axis section is structurally pointless and signals either a
    typo'd axis name or a botched migration."""
    for axis in VALID_AXES:
        assert meta_keys_on_axis(axis), (
            f"axis {axis!r} has zero specs in the registry"
        )


# --- Accessors ---

def test_all_meta_key_names_returns_frozenset():
    names = all_meta_key_names()
    assert isinstance(names, frozenset)
    # Spot-check well-known keys from each axis.
    assert "framework_role" in names
    assert "framework_dispatch" in names


def test_meta_keys_on_axis_returns_only_matching():
    symbol_keys = meta_keys_on_axis(AXIS_SYMBOL_META)
    assert all(spec.axis == AXIS_SYMBOL_META for spec in symbol_keys)
    names = {spec.name for spec in symbol_keys}
    # Cross-check a handful of canonical Symbol.meta fold residues.
    assert {
        "framework_role", "component_framework", "base_classes",
    } <= names


def test_meta_keys_on_axis_edge_meta_includes_fold_residues():
    edge_keys = {spec.name for spec in meta_keys_on_axis(AXIS_EDGE_META)}
    # Wave 5 (ADR-0028 Cluster C) + Wave 4 (Cluster D) + ADR-0023
    # protocol/bridge vocabularies all live on Edge.meta.
    assert {
        "framework_dispatch", "call_construct", "protocol",
        "bridge_kind", "io_primitive", "access_mode",
    } <= edge_keys


def test_meta_keys_on_axis_unknown_returns_empty():
    assert meta_keys_on_axis("not-an-axis") == ()


def test_find_meta_key_returns_spec():
    spec = find_meta_key("framework_role")
    assert spec is not None
    assert spec.name == "framework_role"
    assert spec.axis == AXIS_SYMBOL_META


def test_find_meta_key_returns_edge_meta_spec():
    spec = find_meta_key("framework_dispatch")
    assert spec is not None
    assert spec.axis == AXIS_EDGE_META


def test_find_meta_key_unknown_returns_none():
    assert find_meta_key("not-a-real-key") is None


def test_meta_key_spec_is_dataclass():
    assert dataclasses.is_dataclass(MetaKeySpec)


# --- Cross-axis hygiene ---

def test_no_meta_key_collides_with_typed_field():
    """Meta keys must NOT collide with named typed fields on Symbol /
    Edge dataclasses. The convention (per the module docstring) is
    that a field elevated to a typed slot is named on the dataclass
    rather than carried in ``meta``. A registry entry colliding with
    a typed field name signals an unfinished promotion (the key was
    added to meta and the typed field was later introduced, but the
    meta entry wasn't retired) — both shapes existing simultaneously
    creates an ambiguity for consumers."""
    from hypergumbo_core.ir import Edge, Symbol

    typed_symbol_fields = {f.name for f in dataclasses.fields(Symbol)}
    typed_edge_fields = {f.name for f in dataclasses.fields(Edge)}
    registry_names = all_meta_key_names()
    collisions = registry_names & (typed_symbol_fields | typed_edge_fields)
    assert not collisions, (
        f"Meta keys collide with typed field names: {sorted(collisions)}. "
        "Either retire the meta entry or rename the typed field."
    )
