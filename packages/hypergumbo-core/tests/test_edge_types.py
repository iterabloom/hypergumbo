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
    is_grpc_rpc_implementation,
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


def test_edge_types_endpoint_shape_axis_fully_drained():
    """ADR-0023 §6 fold COMPLETE (WI-pumav, audit-findings 0017): every
    endpoint_shape value has been producer-migrated to a canonical relationship
    + meta discriminator and pruned, so the endpoint_shape axis of
    Edge.edge_type is now EMPTY."""
    endpoints = {spec.name for spec in edge_types_on_axis(AXIS_ENDPOINT_SHAPE)}
    assert endpoints == set(), (
        f"Expected endpoint_shape axis drained to empty, got {sorted(endpoints)}"
    )


def test_edge_types_pending_classification_axis_fully_drained():
    """WI-sumik / WI-pusuv Option B (audit-findings 0016): the resolver/
    OpenAPI/RPC family — the last pending_classification values — was
    producer-migrated to canonical implements/references + meta and pruned,
    so the pending_classification axis of Edge.edge_type is now EMPTY. The
    AXIS_PENDING constant is retained for a future edge type that lands
    pending its per-family audit."""
    pending = {spec.name for spec in edge_types_on_axis(AXIS_PENDING)}
    assert pending == set(), (
        f"Expected pending_classification axis drained to empty, got {sorted(pending)}"
    )


def test_resolver_openapi_rpc_family_pruned_from_registry():
    """WI-sumik Batches A/B (audit-findings 0016): the four resolver/OpenAPI/RPC
    pending_classification values were producer-migrated to canonical
    relationships + meta (resolver_implements/implements_rpc -> implements +
    protocol; resolver_for_type/openapi_implements -> references + ref_construct)
    and pruned from the registry entirely."""
    names = all_edge_type_names()
    pruned = {
        "resolver_implements", "resolver_for_type",
        "openapi_implements", "implements_rpc",
    }
    assert pruned.isdisjoint(names)


def test_long_tail_endpoint_shape_values_pruned_from_registry():
    """WI-pumav Batches 1a-7 (ADR-0023 Phase 4b'): all 21 long-tail
    endpoint_shape values (the WI-tavas-voror registry-completeness sweep) were
    producer-migrated to canonical relationships + meta and pruned from the
    registry entirely."""
    names = all_edge_type_names()
    pruned = {
        "abi_call", "association", "base_image", "build_tag_alternative_of",
        "caller_invokes", "contains_routes", "crypto_flow", "depends",
        "extends_template", "includes_class", "includes_template",
        "invokes_callback", "kernel_launch", "links_to", "notifies_resource",
        "renders", "requires_resource", "signal_receiver", "template_calls",
        "uses_mixin", "uses_vocabulary",
    }
    assert pruned.isdisjoint(names)


def test_protocol_call_trio_pruned_from_registry():
    """WI-hirud (ADR-0023 Phase 4b'): the protocol-call trio (http_calls /
    grpc_calls / graphql_calls) was producer-migrated to canonical ``calls`` +
    ``meta['protocol']`` in WI-vumum-juvil and is now pruned from the registry
    entirely — no ``endpoint_shape`` entries remain for it."""
    names = all_edge_type_names()
    assert {"http_calls", "grpc_calls", "graphql_calls"}.isdisjoint(names)


def test_script_src_pruned_from_registry():
    """WI-pumav Batch 0 (ADR-0023 Phase 4b'): ``script_src`` was the first
    long-tail endpoint_shape value to be producer-migrated — ``html.py`` emits
    canonical ``references`` + ``meta['ref_construct']='script_src'`` (INV-vavat)
    and no producer emits the ``script_src`` edge type. Its dead registry entry
    is now pruned, discharging the WI-pusuv access_mode-census coupling that was
    deferred on it."""
    names = all_edge_type_names()
    assert "script_src" not in names


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


# --- is_grpc_rpc_implementation predicate (audit-findings 0016) ---

def test_is_grpc_rpc_implementation_true():
    """The folded gRPC RPC-implementation edge: implements + protocol=grpc."""
    assert is_grpc_rpc_implementation("implements", {"protocol": "grpc"}) is True
    # extra meta keys don't matter
    assert is_grpc_rpc_implementation(
        "implements", {"protocol": "grpc", "framework_dispatch": "grpc_go_server"}
    ) is True


def test_is_grpc_rpc_implementation_false_wrong_edge_type():
    """A calls edge with protocol=grpc is a gRPC *call*, not the RPC impl."""
    assert is_grpc_rpc_implementation("calls", {"protocol": "grpc"}) is False


def test_is_grpc_rpc_implementation_false_no_meta():
    """A plain structural implements edge (no meta) is not the folded form."""
    assert is_grpc_rpc_implementation("implements", None) is False


def test_is_grpc_rpc_implementation_false_wrong_protocol():
    """implements + a non-grpc protocol (or none) is not the folded form."""
    assert is_grpc_rpc_implementation("implements", {"protocol": "graphql"}) is False
    assert is_grpc_rpc_implementation("implements", {}) is False


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


def test_inheritance_edge_types_are_registered_is_a_relationships():
    """WI-lobif: INHERITANCE_EDGE_TYPES is the canonical class is-a set that the
    slice and ranking consumers query in place of a hardcoded
    {extends, implements} literal — which silently omitted the registered
    `inherits` edge type (Solidity-style inheritance)."""
    from hypergumbo_core.edge_types import INHERITANCE_EDGE_TYPES

    # The fix: `inherits` is included alongside extends/implements.
    assert "inherits" in INHERITANCE_EDGE_TYPES
    assert INHERITANCE_EDGE_TYPES == frozenset({"extends", "inherits", "implements"})
    # Subset coherence: every member is a registered relationship-axis edge type.
    assert INHERITANCE_EDGE_TYPES <= all_edge_type_names()
    for name in INHERITANCE_EDGE_TYPES:
        spec = find_edge_type(name)
        assert spec is not None and spec.axis == AXIS_RELATIONSHIP


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


def test_find_axis_drift_strict_flags_endpoint_shape_value(tmp_path: Path, monkeypatch):
    """If a consumer set references an endpoint_shape value, strict mode flags
    it. The Edge.edge_type endpoint_shape axis is now EMPTY (the ADR-0023
    fold-tail drained it — WI-pumav / audit-findings 0017), so inject a synthetic
    endpoint_shape value to exercise the off-axis flagging."""
    from hypergumbo_core import edge_types as et
    from hypergumbo_core.edge_types import (
        AXIS_ENDPOINT_SHAPE,
        EdgeTypeSpec,
        find_axis_drift,
    )

    synthetic = EdgeTypeSpec(
        "synthetic_endpoint_shape_value", AXIS_ENDPOINT_SHAPE, "test-only",
    )
    monkeypatch.setattr(et, "EDGE_TYPES", tuple(et.EDGE_TYPES) + (synthetic,))

    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_DEMO_EDGE_TYPES = frozenset({"calls", "synthetic_endpoint_shape_value"})\n',
    )
    # Default (lax) mode: registered value, no drift.
    assert find_axis_drift(tmp_path) == []
    # Strict mode: the synthetic value is endpoint_shape → off-axis.
    offenders = find_axis_drift(tmp_path, strict=True)
    assert len(offenders) == 1
    assert "synthetic_endpoint_shape_value" in offenders[0]
    assert "not on allowed axis" in offenders[0]


def test_find_axis_drift_strict_allows_pending_classification(tmp_path: Path, monkeypatch):
    """Permissive-pending design: pending_classification values stay allowed
    because they are real edges awaiting a per-family audit. The
    pending_classification axis is now EMPTY (the resolver/OpenAPI/RPC family
    folded per audit-findings 0016 — WI-sumik / WI-pusuv), so inject a synthetic
    pending value to exercise the allow path (mirrors the endpoint_shape drain's
    synthetic-value approach above)."""
    from hypergumbo_core import edge_types as et
    from hypergumbo_core.edge_types import (
        AXIS_PENDING,
        EdgeTypeSpec,
        find_axis_drift,
    )

    synthetic = EdgeTypeSpec(
        "synthetic_pending_value", AXIS_PENDING, "test-only",
    )
    monkeypatch.setattr(et, "EDGE_TYPES", tuple(et.EDGE_TYPES) + (synthetic,))

    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_DEMO_EDGE_TYPES = frozenset({"calls", "synthetic_pending_value"})\n',
    )
    # Strict mode: pending_classification is an allowed axis → no drift.
    assert find_axis_drift(tmp_path, strict=True) == []
