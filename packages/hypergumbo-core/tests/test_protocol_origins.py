# SPDX-License-Identifier: AGPL-3.0-or-later
"""Property tests for the Symbol.protocol_origin canonical registry (ADR-0031).

Mirror of ``test_evidence_types.py`` / ``test_symbol_kinds.py`` for the new
``protocol-origin`` axis introduced by ADR-0031 Phase 0. These tests are
*invariant* checks rather than "this catalog has exactly N entries" pins —
the catalog is expected to grow as future Class-B linkers join the
ADR-0031 migration table; what mustn't grow is sloppiness in the spec
shape.
"""
from __future__ import annotations

import re

from hypergumbo_core.protocol_origins import (
    PROTOCOL_ORIGINS,
    ProtocolOriginSpec,
    all_protocol_origin_names,
    get_protocol_origin_spec,
)


_KEBAB_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def test_protocol_origin_names_are_unique() -> None:
    """No duplicate ``name`` across the registry — two specs would imply
    two different meanings for the same emitted value, which is exactly
    the kind of axis drift this registry exists to prevent."""
    names = [spec.name for spec in PROTOCOL_ORIGINS]
    assert len(names) == len(set(names)), (
        "Duplicate protocol_origin name(s) detected: "
        f"{[n for n in names if names.count(n) > 1]}"
    )


def test_protocol_origin_names_are_snake_case_lower() -> None:
    """Names use ``snake_case_lower`` per the conventions of sibling axes
    (`evidence_type`, `symbol_kind`). Hyphens are reserved for axis-name
    spellings (``protocol-origin``), not for axis values."""
    for spec in PROTOCOL_ORIGINS:
        assert _KEBAB_RE.match(spec.name), (
            f"Protocol origin name {spec.name!r} is not snake_case_lower; "
            "use [a-z][a-z0-9_]*."
        )


def test_protocol_origin_descriptions_are_substantive() -> None:
    """Descriptions name the producer linker and explain the synthetic
    semantic. Empty or one-line descriptions are a smell — the registry
    is also the documentation surface for catalog-derived consumers."""
    for spec in PROTOCOL_ORIGINS:
        assert len(spec.description) >= 30, (
            f"Protocol origin {spec.name!r} has a too-short description: "
            f"{spec.description!r}. Aim for >=30 chars naming producer + "
            "what the synthetic stand-in represents."
        )


def test_all_protocol_origin_names_matches_specs() -> None:
    """The frozenset accessor returns exactly the names declared in
    ``PROTOCOL_ORIGINS``. Catalog drift here would silently break the
    axis-conformance validator's catalog-lookup."""
    assert all_protocol_origin_names() == frozenset(
        spec.name for spec in PROTOCOL_ORIGINS
    )


def test_get_protocol_origin_spec_lookup() -> None:
    """``get_protocol_origin_spec`` returns the right spec for known
    names and ``None`` for unknowns."""
    spec = get_protocol_origin_spec("kafka_topic_not_real")
    assert spec is None

    for known in ("websocket", "wasm", "openapi", "graphql"):
        spec = get_protocol_origin_spec(known)
        assert spec is not None
        assert spec.name == known


def test_seed_covers_adr_0031_migration_table() -> None:
    """Every Class-B protocol_origin value named in the ADR-0031 §"Per-linker
    producer policy" table is present in the seed catalog. Pins the
    seeding completeness claim from the ADR.
    """
    names = all_protocol_origin_names()
    # Per ADR-0031 §"Per-linker producer policy" migration table
    adr_required = {
        "annotation",
        "database_query",
        "event_sourcing",
        "graphql",
        "grpc",
        "http",
        "ipc",
        "js_module",
        "message_queue",
        "openapi",
        "phoenix_ipc",
        "solidity_abi",
        "subprocess_cli",
        "objc_bridge",
        "tauri_ipc",
        "wasm",
        "websocket",
        "yjs_crdt",
    }
    missing = adr_required - names
    assert not missing, (
        f"ADR-0031 migration table names {len(missing)} protocol origin(s) "
        f"absent from the seed catalog: {sorted(missing)}. The ADR's "
        "Phase 1 producer migration won't have a valid target value for "
        "those linkers."
    )


def test_spec_is_frozen() -> None:
    """``ProtocolOriginSpec`` is frozen — once a value's spec is in the
    registry, drive-by mutation is forbidden. New fields require a
    deliberate diff."""
    import dataclasses

    spec = PROTOCOL_ORIGINS[0]
    try:
        spec.name = "mutated"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("ProtocolOriginSpec should be frozen")


def test_axis_registered_in_multi_value_field_axis() -> None:
    """``protocol-origin`` axis is wired into the static-AST validator's
    ``_known_axes()`` dict so ``# axis: protocol-origin`` annotations on
    Symbol fields pass the lint."""
    from hypergumbo_core.multi_value_field_axis import _known_axes

    axes = _known_axes()
    assert "protocol-origin" in axes
    assert callable(axes["protocol-origin"])
    # Sanity: the callable returns the catalog
    assert axes["protocol-origin"]() == all_protocol_origin_names()
