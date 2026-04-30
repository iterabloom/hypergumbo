# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical registry of edge types in hypergumbo's behavior map.

Per ADR-0023, every value in the canonical registry should have
``axis="relationship"`` — ``edge_type`` names the relationship that
produced the edge, with endpoint properties queried from the
endpoint nodes themselves rather than smuggled into the type label.

This module is the single source of truth: ``scripts/generate-schema``
imports ``EDGE_TYPES`` to emit both the JSON Schema enum and per-value
axis annotations (under the ``x-axis-of-values`` extension keyword).
Consumers that need a subset of edge types (for example
``ranking._STRUCTURAL_EDGE_TYPES``) should call
``edge_types_on_axis(...)`` rather than maintain their own hardcoded
set; the property test in ``tests/test_edge_types.py`` enforces that
every hardcoded set in the codebase is a subset of this registry.

Axis taxonomy:

- ``relationship`` — ADR-0023 compliant. The value names the
  relationship between src and dst.
- ``endpoint_shape`` — deprecation candidate per ADR-0023 §6. The
  value's meaning is captured by ``src.kind`` / ``dst.kind`` /
  language metadata; migration plan folds these back into
  relationship-shaped names.
- ``pending_classification`` — deferred to per-family audit per
  ADR-0023 §5 (the dispatch and publish families contain a mix of
  genuinely distinct relationships and protocol-conditional aliases;
  per-value verdicts arrive with each family's audit).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterator


AXIS_RELATIONSHIP: Final[str] = "relationship"
AXIS_ENDPOINT_SHAPE: Final[str] = "endpoint_shape"
AXIS_PENDING: Final[str] = "pending_classification"

VALID_AXES: Final[frozenset[str]] = frozenset({
    AXIS_RELATIONSHIP,
    AXIS_ENDPOINT_SHAPE,
    AXIS_PENDING,
})


@dataclass(frozen=True)
class EdgeTypeSpec:
    """A single edge type and its axis classification."""

    name: str
    axis: str
    description: str


EDGE_TYPES: Final[tuple[EdgeTypeSpec, ...]] = (
    # ADR-0023 compliant — the value names the relationship.
    EdgeTypeSpec(
        "calls", AXIS_RELATIONSHIP,
        "Caller invokes callee.",
    ),
    EdgeTypeSpec(
        "imports", AXIS_RELATIONSHIP,
        "Module imports another module or symbol.",
    ),
    EdgeTypeSpec(
        "instantiates", AXIS_RELATIONSHIP,
        "Constructor or factory creates an instance.",
    ),
    EdgeTypeSpec(
        "extends", AXIS_RELATIONSHIP,
        "Class extends a superclass.",
    ),
    EdgeTypeSpec(
        "implements", AXIS_RELATIONSHIP,
        "Class implements an interface.",
    ),
    EdgeTypeSpec(
        "contains", AXIS_RELATIONSHIP,
        "Container symbol holds member symbol.",
    ),
    EdgeTypeSpec(
        "uses", AXIS_RELATIONSHIP,
        "Generic symbol-usage relationship.",
    ),
    EdgeTypeSpec(
        "references", AXIS_RELATIONSHIP,
        "Symbol references another by name without invocation.",
    ),
    EdgeTypeSpec(
        "depends_on", AXIS_RELATIONSHIP,
        "Generic dependency relationship.",
    ),
    EdgeTypeSpec(
        "depends_on_manifest", AXIS_RELATIONSHIP,
        "Dependency declared in a package or build manifest.",
    ),
    EdgeTypeSpec(
        "sources", AXIS_RELATIONSHIP,
        "Sources another file (e.g., shell ``source``).",
    ),
    EdgeTypeSpec(
        "subprocess_calls", AXIS_RELATIONSHIP,
        "Symbol invokes another symbol via a subprocess.",
    ),
    EdgeTypeSpec(
        "links", AXIS_RELATIONSHIP,
        "Generic linkage relationship.",
    ),
    EdgeTypeSpec(
        "wraps", AXIS_RELATIONSHIP,
        "Decorator or middleware wraps the target symbol.",
    ),
    EdgeTypeSpec(
        "module_attr_ref", AXIS_RELATIONSHIP,
        "Reads an attribute on an imported module (e.g., os.environ).",
    ),

    # Deprecation candidates per ADR-0023 §6. Endpoint properties
    # leaked into the edge_type label; migration folds these back into
    # relationship-shaped names with kind/language metadata on the
    # endpoint nodes.
    EdgeTypeSpec(
        "imports_module", AXIS_ENDPOINT_SHAPE,
        "Imports targeting a module/file specifically (use 'imports').",
    ),
    EdgeTypeSpec(
        "imports_component", AXIS_ENDPOINT_SHAPE,
        "Imports targeting a UI component (Vue/Svelte/React); per "
        "ADR-0023 §6, fold into 'imports' + dst.kind == 'component'.",
    ),
    EdgeTypeSpec(
        "model_reference", AXIS_ENDPOINT_SHAPE,
        "ORM reference to a model class; per ADR-0023 §6, fold into "
        "'references' + dst.kind == 'model'.",
    ),
    EdgeTypeSpec(
        "type_ref", AXIS_ENDPOINT_SHAPE,
        "TypeScript reference to a type symbol; per ADR-0023 §6, fold "
        "into 'references' + dst.kind == 'type'.",
    ),
    EdgeTypeSpec(
        "renders_component", AXIS_ENDPOINT_SHAPE,
        "JSX/template render of a UI component; per ADR-0023 §6 review, "
        "likely 'references' with meta['construct'] == 'jsx'.",
    ),
    EdgeTypeSpec(
        "query_references", AXIS_ENDPOINT_SHAPE,
        "Query reference to a database object (table, column, view); "
        "per ADR-0023 §6, fold into 'references' + dst.kind == 'query'.",
    ),
    EdgeTypeSpec(
        "script_src", AXIS_ENDPOINT_SHAPE,
        "HTML ``<script src=...>`` reference.",
    ),
    EdgeTypeSpec(
        "base_image", AXIS_ENDPOINT_SHAPE,
        "Dockerfile ``FROM`` base image reference.",
    ),
    EdgeTypeSpec(
        "kernel_launch", AXIS_ENDPOINT_SHAPE,
        "GPU kernel invocation.",
    ),
    EdgeTypeSpec(
        "native_bridge", AXIS_ENDPOINT_SHAPE,
        "JNI/FFI bridge to native code (use 'calls' + bridge meta).",
    ),
    EdgeTypeSpec(
        "message_send", AXIS_ENDPOINT_SHAPE,
        "Message produced to a queue/topic.",
    ),
    EdgeTypeSpec(
        "message_receive", AXIS_ENDPOINT_SHAPE,
        "Message consumed from a queue/topic.",
    ),
    EdgeTypeSpec(
        "websocket_message", AXIS_ENDPOINT_SHAPE,
        "WebSocket message exchange.",
    ),
    EdgeTypeSpec(
        "websocket_connection", AXIS_ENDPOINT_SHAPE,
        "WebSocket connection establishment.",
    ),
    EdgeTypeSpec(
        "grpc_calls", AXIS_ENDPOINT_SHAPE,
        "gRPC call (use 'calls' + protocol meta).",
    ),
    EdgeTypeSpec(
        "http_calls", AXIS_ENDPOINT_SHAPE,
        "HTTP call (use 'calls' + protocol meta).",
    ),
    EdgeTypeSpec(
        "graphql_calls", AXIS_ENDPOINT_SHAPE,
        "GraphQL call (use 'calls' + protocol meta).",
    ),
    EdgeTypeSpec(
        "message_queue", AXIS_ENDPOINT_SHAPE,
        "Message queue endpoint reference.",
    ),
    EdgeTypeSpec(
        "cgo_bridge", AXIS_ENDPOINT_SHAPE,
        "Go cgo FFI bridge (use 'calls' + bridge meta).",
    ),
    EdgeTypeSpec(
        "ffi_bridge", AXIS_ENDPOINT_SHAPE,
        "Generic FFI bridge (use 'calls' + bridge meta).",
    ),
    EdgeTypeSpec(
        "napi_bridge", AXIS_ENDPOINT_SHAPE,
        "Node-API native bridge (use 'calls' + bridge meta).",
    ),
    EdgeTypeSpec(
        "wasm_bridge", AXIS_ENDPOINT_SHAPE,
        "WebAssembly bridge invocation (use 'calls' + bridge meta).",
    ),
    EdgeTypeSpec(
        "wasm_load", AXIS_ENDPOINT_SHAPE,
        "WebAssembly module load.",
    ),
    EdgeTypeSpec(
        "bridge_invokes", AXIS_ENDPOINT_SHAPE,
        "Generic bridge-mediated invocation (use 'calls' + bridge meta).",
    ),
    EdgeTypeSpec(
        "ipc_calls", AXIS_ENDPOINT_SHAPE,
        "Inter-process call (use 'calls' + protocol meta).",
    ),
    EdgeTypeSpec(
        "ipc_event", AXIS_ENDPOINT_SHAPE,
        "Inter-process event dispatch.",
    ),

    # Per-family audit pending per ADR-0023 §5.
    EdgeTypeSpec(
        "dispatches_to", AXIS_PENDING,
        "Dispatch family — pending per-family audit.",
    ),
    EdgeTypeSpec(
        "routes_to", AXIS_PENDING,
        "Dispatch family — pending per-family audit.",
    ),
    EdgeTypeSpec(
        "event_publishes", AXIS_PENDING,
        "Publish family — pending per-family audit.",
    ),
    EdgeTypeSpec(
        "resolver_implements", AXIS_PENDING,
        "GraphQL resolver pattern — pending per-family audit.",
    ),
    EdgeTypeSpec(
        "resolver_for_type", AXIS_PENDING,
        "GraphQL resolver-type binding — pending per-family audit.",
    ),
    EdgeTypeSpec(
        "openapi_implements", AXIS_PENDING,
        "OpenAPI handler pattern — pending per-family audit.",
    ),
    EdgeTypeSpec(
        "implements_rpc", AXIS_PENDING,
        "RPC implementation binding — pending per-family audit.",
    ),
    EdgeTypeSpec(
        "di_resolves", AXIS_PENDING,
        "DI container resolution — pending per-family audit.",
    ),
)


def all_edge_type_names() -> frozenset[str]:
    """Return every canonical edge type name."""
    return frozenset(spec.name for spec in EDGE_TYPES)


def edge_types_on_axis(axis: str) -> tuple[EdgeTypeSpec, ...]:
    """Return all edge type specs whose axis equals *axis*.

    Use this in place of hardcoded sets like
    ``_STRUCTURAL_EDGE_TYPES = {"extends", "implements"}``: query by
    axis (or by another property) instead of enumerating values, so
    new specs that match the axis are picked up automatically.
    """
    return tuple(spec for spec in EDGE_TYPES if spec.axis == axis)


def find_edge_type(name: str) -> EdgeTypeSpec | None:
    """Look up an edge type by name; return None if not registered."""
    for spec in EDGE_TYPES:
        if spec.name == name:
            return spec
    return None


# ---------------------------------------------------------------------------
# Axis-coherence drift detection
# ---------------------------------------------------------------------------
#
# AST-walk helpers that catch the silent-bug shape from ADR-0023: consumer-
# side hardcoded sets of edge_type values that drift from the canonical
# registry (either by missing values that runtime emits, or by including
# values that runtime never emits — see the audit playbook's Step 4).
#
# Used by the property test in ``tests/test_edge_types.py`` and by the
# pre-commit linter at ``scripts/check-edge-type-drift``.


def _iter_edge_type_set_assignments(
    path: Path,
) -> Iterator[tuple[int, str, frozenset[str]]]:
    """Yield ``(lineno, target_name, frozenset_of_string_elements)`` for
    every module-level ``<NAME> = {...}`` or
    ``<NAME> = frozenset({...})`` assignment in *path* where ``NAME``
    contains the substring ``EDGE_TYPE`` and every element is a string
    literal.

    The name-substring filter prevents false positives from unrelated
    string sets (programming-language keyword vocabularies, language
    stdlib method-name catalogs, etc.) that happen to share an element
    with the registry by coincidence.

    Files that fail to read (binary garbage, permission errors) or to
    parse (syntax errors mid-edit) are silently skipped — this helper
    is best-effort and treats unreadable files as "no offenders found
    here."
    """
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
            if len(node.targets) == 1 and isinstance(
                node.targets[0], ast.Name,
            ):
                target_name = node.targets[0].id
                value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(
            node.target, ast.Name,
        ):
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


def find_axis_drift(repo_root: Path) -> list[str]:
    """Return human-readable drift offenders found under
    ``repo_root/packages/``.

    AST-walks every ``.py`` file under ``packages/`` (excluding
    ``tests/`` directories — fixture data legitimately uses arbitrary
    string sets), looking for module-level assignments whose target
    name contains ``EDGE_TYPE``. Asserts every value in those sets is
    in the canonical registry (``EDGE_TYPES``); emits one offender
    line per drifted set.

    Returns an empty list if no drift is detected. Files outside
    ``packages/`` are not scanned (no edge-type sets live there in
    practice; the search scope is intentionally narrow to keep the
    pre-commit lint fast).
    """
    known_names = all_edge_type_names()
    offenders: list[str] = []
    packages_dir = repo_root / "packages"
    if not packages_dir.is_dir():
        return offenders
    for py_file in packages_dir.rglob("*.py"):
        if "/tests/" in str(py_file):
            continue
        for lineno, target_name, values in _iter_edge_type_set_assignments(
            py_file,
        ):
            drift = values - known_names
            if drift:
                try:
                    rel = py_file.relative_to(repo_root)
                except ValueError:  # pragma: no cover
                    rel = py_file
                offenders.append(
                    f"{rel}:{lineno} ({target_name}): "
                    f"contains {sorted(drift)} not in canonical registry"
                )
    return offenders
