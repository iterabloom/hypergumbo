# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the canonical Symbol.kind registry and drift detection.

The drift test AST-walks the package source tree, finds every set or
frozenset literal whose elements are all string constants, and asserts
that any such set whose target name contains ``KIND`` is a subset of
the canonical registry. This catches the silent-bug shape ADR-0027
identified — consumer-side hardcoded sets that accumulate values not
in the canonical schema (or miss values that should be in the set).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from hypergumbo_core.symbol_kinds import (
    AXIS_LANGUAGE_CONSTRUCT,
    AXIS_PENDING,
    SYMBOL_KINDS,
    SymbolKindSpec,
    VALID_AXES,
    all_symbol_kind_names,
    find_symbol_kind,
    symbol_kinds_on_axis,
)


# --- Registry invariants ---

def test_registry_has_no_duplicate_names():
    names = [spec.name for spec in SYMBOL_KINDS]
    assert len(names) == len(set(names))


def test_every_spec_has_a_valid_axis():
    for spec in SYMBOL_KINDS:
        assert spec.axis in VALID_AXES, (
            f"{spec.name} has invalid axis {spec.axis!r}"
        )


def test_every_spec_has_nonempty_description():
    for spec in SYMBOL_KINDS:
        assert spec.description, f"{spec.name} has empty description"


def test_specs_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        SYMBOL_KINDS[0].name = "mutated"  # type: ignore[misc]


def test_valid_axes_constant_matches_module_constants():
    assert VALID_AXES == frozenset(
        {AXIS_LANGUAGE_CONSTRUCT, AXIS_PENDING},
    )


def test_endpoint_shape_axis_is_retired():
    """Phase 4b: the ``endpoint_shape`` axis was retired in PR #3633.
    The axis literal must not be re-introduced as a live spec
    classification, and the retired constant must remain ABSENT from
    :data:`VALID_AXES`. The constant itself stays defined as a
    backwards-compat import target for
    :mod:`hypergumbo_core.audit_findings`'s per-axis validator
    binding."""
    import hypergumbo_core.symbol_kinds as sk
    assert sk.AXIS_ENDPOINT_SHAPE == "endpoint_shape"
    assert sk.AXIS_ENDPOINT_SHAPE not in VALID_AXES
    assert sk.AXIS_ENDPOINT_SHAPE not in {spec.axis for spec in SYMBOL_KINDS}


def test_every_axis_has_at_least_one_spec():
    """Each declared axis must have at least one registry entry — an
    empty axis section is structurally pointless and signals either a
    typo'd axis name or a botched migration."""
    for axis in VALID_AXES:
        assert symbol_kinds_on_axis(axis), (
            f"axis {axis!r} has zero specs in the registry"
        )


# --- Accessors ---

def test_all_symbol_kind_names_returns_frozenset():
    names = all_symbol_kind_names()
    assert isinstance(names, frozenset)
    assert "function" in names
    assert "class" in names


def test_symbol_kinds_on_axis_returns_only_matching():
    constructs = symbol_kinds_on_axis(AXIS_LANGUAGE_CONSTRUCT)
    assert all(spec.axis == AXIS_LANGUAGE_CONSTRUCT for spec in constructs)
    names = {spec.name for spec in constructs}
    # Cross-check a handful of clearly-syntactic-construct values.
    assert {"function", "class", "method", "struct", "interface"} <= names


def test_symbol_kinds_on_axis_endpoint_shape_removed_after_phase_4b():
    """Phase 4b complement of the prior 'includes deprecation
    candidates' assertion: every value that occupied
    ``AXIS_ENDPOINT_SHAPE`` during the Phase 4a window must be absent
    from the registry. Cluster D framework_role values folded to
    canonical + ``meta['framework_role']``; Cluster E call_construct
    / edge-label values folded to ``call_site`` + ``meta['call_kind']``
    or shipped as DEPRECATE-NO-FOLD; Cluster B/G/H file-shape /
    build-config / long-tail values folded to canonical + the
    relevant meta key."""
    names = all_symbol_kind_names()
    retired = {
        "event_publisher", "graphql_resolver", "route_mount",
        "call", "function_call", "subprocess_call",
    }
    assert not (retired & names), (
        f"Retired Phase-4b values reappeared in registry: "
        f"{sorted(retired & names)}"
    )


def test_symbol_kinds_on_axis_unknown_returns_empty():
    assert symbol_kinds_on_axis("not-an-axis") == ()


def test_find_symbol_kind_returns_spec():
    spec = find_symbol_kind("function")
    assert spec is not None
    assert spec.name == "function"
    assert spec.axis == AXIS_LANGUAGE_CONSTRUCT


def test_find_symbol_kind_unknown_returns_none():
    assert find_symbol_kind("not-a-real-kind") is None


def test_symbol_kind_spec_is_dataclass():
    assert dataclasses.is_dataclass(SymbolKindSpec)


# --- Drift detection (live tree) ---

def test_every_symbol_kind_named_set_is_a_subset_of_registry():
    """Every set whose target name contains ``KIND`` must contain only
    values from the canonical registry. Catches the silent-bug pattern
    from ADR-0027 — consumer-side hardcoded sets that drift from what
    the analyzers actually emit.

    Delegates to ``symbol_kinds.find_axis_drift`` so the same logic
    powers the pre-commit linter at ``scripts/check-symbol-kind-drift``.
    """
    from hypergumbo_core.symbol_kinds import find_axis_drift

    repo_root = Path(__file__).resolve().parents[3]
    offenders = find_axis_drift(repo_root)

    assert not offenders, (
        "Hardcoded Symbol.kind sets contain values absent from the "
        "canonical registry "
        "(packages/hypergumbo-core/src/hypergumbo_core/symbol_kinds.py).\n"
        "Either add the missing values to the registry (with an axis "
        "classification) or remove them from the consumer set:\n"
        + "\n".join(offenders)
    )


# --- Synthetic-fixture drift tests ---

def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_find_axis_drift_returns_empty_when_no_packages_dir(tmp_path: Path):
    from hypergumbo_core.symbol_kinds import find_axis_drift
    assert find_axis_drift(tmp_path) == []


def test_find_axis_drift_finds_drift_in_set_literal(tmp_path: Path):
    from hypergumbo_core.symbol_kinds import find_axis_drift

    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_DRIFT_KINDS = {"function", "not-a-real-kind"}\n',
    )
    offenders = find_axis_drift(tmp_path)
    assert len(offenders) == 1
    assert "_DRIFT_KINDS" in offenders[0]
    assert "not-a-real-kind" in offenders[0]


def test_find_axis_drift_clean_set_is_not_flagged(tmp_path: Path):
    from hypergumbo_core.symbol_kinds import find_axis_drift

    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_CLEAN_KINDS = {"function", "method", "class"}\n',
    )
    assert find_axis_drift(tmp_path) == []


def test_find_axis_drift_excludes_protocol_kinds_and_bridge_kinds(tmp_path: Path):
    """``PROTOCOL_KINDS`` and ``BRIDGE_KINDS`` are vocabularies for
    ``Edge.meta`` keys (``meta['protocol']``, ``meta['bridge_kind']``),
    not Symbol.kind sets. They share the ``KIND`` substring with the
    name filter but live on a different axis; the wrapper excludes them
    by name so legitimate Edge.meta vocabularies don't trigger drift.
    """
    from hypergumbo_core.symbol_kinds import find_axis_drift

    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'PROTOCOL_KINDS = frozenset({"http", "grpc", "graphql"})\n'
        'BRIDGE_KINDS = frozenset({"cgo", "ffi", "napi", "wasm"})\n',
    )
    assert find_axis_drift(tmp_path) == []
