# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical registry of ``Symbol.protocol_origin`` values (ADR-0031).

Per ADR-0031, the new ``Symbol.protocol_origin`` typed field names the
protocol or framework family that a synthetic-stand-in Symbol represents
(Kafka topic, WebSocket endpoint, IPC channel, WASM module, GraphQL
operation, etc.). Linkers fabricating synthetic stand-ins populate this
field; real-source-declaration Symbols leave it ``None``.

This module is the structural sibling of
:mod:`hypergumbo_core.symbol_kinds` and
:mod:`hypergumbo_core.evidence_types`. Same shape, different field.

Why a registry rather than free-form strings
--------------------------------------------

ADR-0024 §"Fold-residue discipline" prescribes typed-field promotion
when a candidate value-set would recur with ≥3 distinct values OR ≥2
producer modules. For the protocol-identity semantic that
``Symbol.protocol_origin`` carries, both thresholds passed by wide
margin on the day this field was introduced (>=10 distinct protocol
values across 8+ producer linker modules per the ADR-0031 audit). The
registry is the runtime accessor that catalog-derived consumers like
the ``# axis: protocol-origin`` declaration check at
``hypergumbo_core.multi_value_field_axis`` and the future axis-conformance
validator class at ``hypergumbo_core.spec_validator`` (ADR-0033 Phase 3
PR1) both call into.

Compared to the catalog-derived ``language`` axis (which is computed
from ``@register_analyzer`` / ``@register_linker`` declarations on the
fly), the protocol-origin axis is hand-maintained here because protocol
origins are not tied to analyzer/linker registration metadata — each
linker chooses its own protocol identity for its synthetic emits, and
the canonical list is the union of those choices documented at one site.

Why some sentinels move here instead of staying on the language axis
--------------------------------------------------------------------

Pre-ADR-0031, four linkers (``annotation_convention``, ``wasm_bindgen``,
``grpc`` Route synthetic, ``openapi``) hardcoded non-language sentinels
in ``Symbol.language``: ``"unknown"``, ``"wasm"``, ``"protobuf"``,
``"openapi"``. These values were never actually language tags — they
were protocol identities smuggled through the language field because
no protocol-origin axis existed yet. ADR-0031's migration moves them
out of ``language=`` into ``protocol_origin=`` (per the LITERAL-SENTINEL
class in the ADR's migration table); ``annotation_convention``'s
``"unknown"`` becomes ``"annotation"`` here, ``wasm_bindgen``'s
``"wasm"`` stays, etc.

Seeding (ADR-0031 §"Phase 0 — protocol_origins module")
-------------------------------------------------------

Initial value set covers every linker named in the ADR-0031 migration
table that emits synthetic stand-ins. Future Class-B linkers (per
ADR-0031's per-linker producer policy) extend this set; the property
test in ``tests/test_protocol_origins.py`` checks the per-value spec
shape but does NOT pin the count (the set grows with each new linker).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class ProtocolOriginSpec:
    """A single ``Symbol.protocol_origin`` value and its description.

    Mirrors ``EvidenceTypeSpec`` shape but without an axis taxonomy —
    every protocol-origin value is on the single ``protocol-origin``
    axis. Frozen because adding fields to existing values in subsequent
    PRs should produce a deliberate diff, not a drive-by mutation.
    """

    name: str
    description: str


PROTOCOL_ORIGINS: Final[tuple[ProtocolOriginSpec, ...]] = (
    # ---- Annotation conventions (ADR-0031: annotation_convention.py) ----
    ProtocolOriginSpec(
        "annotation",
        "Symbols synthesised from agent-readable code annotations "
        "(e.g., '@hg:publishes', '@hg:subscribes', '@hg:owns') — the "
        "annotation_convention linker's emit family. Previously emitted "
        "with the language='unknown' sentinel per the ADR-0031 audit."
    ),

    # ---- Database / messaging / streaming ----
    ProtocolOriginSpec(
        "database_query",
        "Synthetic stand-ins for database query operations (SQL / ORM "
        "method invocations / ODM document operations). database_query "
        "linker emits."
    ),
    ProtocolOriginSpec(
        "event_sourcing",
        "Synthetic stand-ins for event-sourcing patterns (event publish, "
        "subscribe, aggregate replay). event_sourcing linker emits."
    ),
    ProtocolOriginSpec(
        "message_queue",
        "Synthetic stand-ins for message-queue patterns (Kafka topics, "
        "RabbitMQ queues, Redis pub/sub channels). message_queue linker "
        "emits. Cross-language detection consumer reads this via "
        "Symbol.discovery_language to apply the -0.1 confidence "
        "adjustment for cross-language flows."
    ),

    # ---- API / service protocols ----
    ProtocolOriginSpec(
        "graphql",
        "Synthetic stand-ins for GraphQL operations (Query / Mutation / "
        "Subscription root types). graphql + graphql_resolver linker "
        "emits."
    ),
    ProtocolOriginSpec(
        "grpc",
        "Synthetic stand-ins for gRPC services and Route synthetics. "
        "grpc linker emits. Note: the proto file scan path keeps "
        "language='proto' as a Class-A real-source-declaration "
        "(per ADR-0031 §'protobuf collapse'); only the Route synthetic "
        "is Class-B with protocol_origin='grpc'."
    ),
    ProtocolOriginSpec(
        "http",
        "Synthetic stand-ins for HTTP client / fetch / axios calls "
        "discovered as escape points. http linker emits."
    ),
    ProtocolOriginSpec(
        "openapi",
        "Synthetic stand-ins for OpenAPI / Swagger spec-derived routes. "
        "openapi linker emits. Previously hardcoded language='openapi' "
        "per the ADR-0031 LITERAL-SENTINEL audit."
    ),
    ProtocolOriginSpec(
        "websocket",
        "Synthetic stand-ins for WebSocket endpoints and message handlers. "
        "websocket linker emits."
    ),

    # ---- IPC / bridge / native-interop protocols ----
    ProtocolOriginSpec(
        "ipc",
        "Synthetic stand-ins for inter-process communication channels. "
        "ipc linker emits. Previously hardcoded language='javascript' "
        "per the ADR-0031 LITERAL-HOST audit."
    ),
    ProtocolOriginSpec(
        "phoenix_ipc",
        "Synthetic stand-ins for Phoenix Channels / LiveView IPC. "
        "phoenix_ipc linker emits. Previously hardcoded "
        "language='elixir'."
    ),
    ProtocolOriginSpec(
        "subprocess_cli",
        "Synthetic stand-ins for subprocess / Popen / Process spawn "
        "boundaries. subprocess_cli linker emits. Previously hardcoded "
        "language='python'."
    ),
    ProtocolOriginSpec(
        "objc_bridge",
        "Synthetic stand-ins for the Swift ↔ Objective-C bridge crossings. "
        "swift_objc linker emits. Previously hardcoded language='swift'."
    ),
    ProtocolOriginSpec(
        "solidity_abi",
        "Synthetic stand-ins for Solidity ABI calls from JS/TS clients. "
        "solidity_abi linker emits. Previously hardcoded "
        "language='typescript'."
    ),
    ProtocolOriginSpec(
        "tauri_ipc",
        "Synthetic stand-ins for Tauri IPC invocations between webview "
        "and Rust host. tauri_ipc linker emits."
    ),
    ProtocolOriginSpec(
        "wasm",
        "Synthetic stand-ins for WASM module imports and exports. "
        "wasm_bindgen linker emits. Previously hardcoded "
        "language='wasm' (sentinel, not a real language tag) per the "
        "ADR-0031 LITERAL-SENTINEL audit."
    ),

    # ---- Frontend / module-system protocols ----
    ProtocolOriginSpec(
        "js_module",
        "Synthetic stand-ins for JavaScript module-system resolution "
        "(npm-installed packages, local require / import targets without "
        "a discovered source file). js_module linker emits."
    ),
    ProtocolOriginSpec(
        "yjs_crdt",
        "Synthetic stand-ins for Yjs CRDT document operations "
        "(awareness, shared types, providers). yjs_crdt linker emits. "
        "Previously hardcoded language='typescript'."
    ),

    # ---- Cross-cutting families ----
    ProtocolOriginSpec(
        "crypto_flow",
        "Synthetic stand-ins for crypto-API write/read pairs "
        "(WebCrypto, libsodium-style flows). crypto_flow linker emits."
    ),
    ProtocolOriginSpec(
        "message_dispatch",
        "Synthetic stand-ins for in-process message-dispatch primitives "
        "(emit / publish / fire). message_dispatch linker emits."
    ),
)


def all_protocol_origin_names() -> frozenset[str]:
    """Return the set of canonical protocol-origin values.

    This is the accessor the ``# axis: protocol-origin`` declaration in
    ``multi_value_field_axis._known_axes()`` reads, and the runtime
    axis-conformance validator (ADR-0033 Phase 3 PR1, future) will read
    when it lands. Mirror of ``all_evidence_type_names`` /
    ``all_symbol_kind_names`` /
    :func:`hypergumbo_core.catalog.all_known_languages`.
    """
    return frozenset(spec.name for spec in PROTOCOL_ORIGINS)


def get_protocol_origin_spec(name: str) -> ProtocolOriginSpec | None:
    """Return the spec for *name*, or ``None`` if not in the registry.

    Used by future audit-findings tooling and the eventual
    spec-comparison report generator. Not used by hot-path code at
    Phase-0 land time.
    """
    for spec in PROTOCOL_ORIGINS:
        if spec.name == name:
            return spec
    return None
