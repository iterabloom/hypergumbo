# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Caddy module-registry dispatch linker (WI-zizig).

Validates that the linker emits ``dispatches_to`` edges from a Caddy module
struct (identified by its ``CaddyModule() ModuleInfo`` marker method) to the
framework-called lifecycle/handler methods (``Provision`` / ``Validate`` /
``ServeHTTP`` / ...), so those handlers are not misclassified as dead code —
Caddy dispatches them reflectively via ``LoadModuleByID``, so the static call
graph never reaches them.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.caddy_module_dispatch import (
    CADDY_DISPATCHED_METHODS,
    CADDY_MODULE_MARKER,
    _build_go_method_index,
    _short_name,
    link_caddy_module_dispatch,
)
from hypergumbo_core.linkers.registry import LinkerContext


def _ctx(symbols: list[Symbol], edges: list[Edge] | None = None) -> LinkerContext:
    return LinkerContext(
        repo_root=Path("/repo"),
        symbols=symbols,
        edges=list(edges or []),
    )


def _struct_sym(
    name: str,
    *,
    path: str = "modules/gzip/gzip.go",
    span: tuple[int, int] = (1, 5),
    kind: str = "struct",
    language: str = "go",
) -> Symbol:
    return Symbol(
        id=f"{language}:{path}:{span[0]}-{span[1]}:{name}:{kind}",
        name=name,
        kind=kind,
        language=language,
        path=path,
        span=Span(span[0], span[1], 0, 0),
    )


def _method_sym(
    qualified_name: str,
    *,
    path: str = "modules/gzip/gzip.go",
    span: tuple[int, int] = (10, 12),
    language: str = "go",
) -> Symbol:
    return Symbol(
        id=f"{language}:{path}:{span[0]}-{span[1]}:{qualified_name}:method",
        name=qualified_name,
        kind="method",
        language=language,
        path=path,
        span=Span(span[0], span[1], 0, 0),
    )


# ---------------------------------------------------------------------------
# Unit: _short_name / _build_go_method_index
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_short_name_strips_receiver(self) -> None:
        assert _short_name("Gzip.Provision") == "Provision"

    def test_short_name_bare(self) -> None:
        assert _short_name("Provision") == "Provision"

    def test_index_groups_methods_by_receiver(self) -> None:
        m = _method_sym("Gzip.Provision")
        idx = _build_go_method_index([m])
        assert idx == {("modules/gzip/gzip.go", "Gzip"): [m]}

    def test_index_skips_non_go(self) -> None:
        assert _build_go_method_index([_method_sym("C.M", language="java")]) == {}

    def test_index_skips_non_method_kind(self) -> None:
        assert _build_go_method_index([_struct_sym("Gzip")]) == {}

    def test_index_skips_receiverless_name(self) -> None:
        """A method-kind symbol without a dotted receiver is not indexed."""
        assert _build_go_method_index([_method_sym("bareName")]) == {}


# ---------------------------------------------------------------------------
# Integration: link_caddy_module_dispatch
# ---------------------------------------------------------------------------


class TestLinkCaddyModuleDispatch:
    def test_module_emits_provision_edge(self) -> None:
        struct = _struct_sym("Gzip")
        caddy_module = _method_sym("Gzip.CaddyModule", span=(9, 11))
        provision = _method_sym("Gzip.Provision", span=(13, 13))
        result = link_caddy_module_dispatch(_ctx([struct, caddy_module, provision]))
        by_dst = {e.dst: e for e in result.edges}
        assert provision.id in by_dst
        e = by_dst[provision.id]
        # Edge is anchored on the CaddyModule marker method (the reachable
        # node), not the struct.
        assert e.src == caddy_module.id
        assert e.edge_type == "dispatches_to"
        assert e.evidence_type == "ast_call_direct"
        assert (e.meta or {}).get("framework_dispatch") == "caddy_module"
        assert e.confidence == 0.90

    def test_all_handler_methods_get_edges_helper_and_marker_excluded(self) -> None:
        struct = _struct_sym("Handler")
        marker = _method_sym("Handler.CaddyModule", span=(9, 11))
        methods = [
            marker,
            _method_sym("Handler.Provision", span=(13, 15)),
            _method_sym("Handler.Validate", span=(17, 19)),
            _method_sym("Handler.ServeHTTP", span=(21, 30)),
            _method_sym("Handler.internalHelper", span=(32, 34)),  # not dispatched
        ]
        result = link_caddy_module_dispatch(_ctx([struct, *methods]))
        emitted_dst_ids = {e.dst for e in result.edges}
        emitted_short = {
            _short_name(m.name) for m in methods if m.id in emitted_dst_ids
        }
        # Handlers are dispatch targets; the marker (source) and helper are not.
        assert emitted_short == {"Provision", "Validate", "ServeHTTP"}
        assert all(e.src == marker.id for e in result.edges)

    def test_struct_without_marker_ignored(self) -> None:
        """A Go struct with handler-named methods but no CaddyModule marker
        is not a Caddy module — emit nothing."""
        struct = _struct_sym("Plain")
        provision = _method_sym("Plain.Provision")
        assert link_caddy_module_dispatch(_ctx([struct, provision])).edges == []

    def test_non_go_struct_ignored(self) -> None:
        struct = _struct_sym("Gzip", language="python", kind="class")
        caddy_module = _method_sym("Gzip.CaddyModule", language="python")
        provision = _method_sym("Gzip.Provision", language="python")
        result = link_caddy_module_dispatch(_ctx([struct, caddy_module, provision]))
        assert result.edges == []

    def test_no_structs(self) -> None:
        """Early skip when there are no struct symbols."""
        assert link_caddy_module_dispatch(_ctx([_method_sym("Lone.Provision")])).edges == []

    def test_existing_edge_not_duplicated(self) -> None:
        struct = _struct_sym("Gzip")
        caddy_module = _method_sym("Gzip.CaddyModule", span=(9, 11))
        provision = _method_sym("Gzip.Provision", span=(13, 13))
        existing = Edge.create(
            src=caddy_module.id, dst=provision.id, edge_type="dispatches_to",
            line=1, confidence=0.90, origin="other", origin_run_id="rx",
        )
        result = link_caddy_module_dispatch(
            _ctx([struct, caddy_module, provision], [existing])
        )
        # The pre-existing marker→Provision edge is not duplicated.
        assert provision.id not in {e.dst for e in result.edges}

    def test_marker_without_span_emits_line_zero(self) -> None:
        """Edge line comes from the marker method's span; a span-less marker
        yields line 0."""
        struct = _struct_sym("Gzip", path="p")
        caddy_module = Symbol(
            id="go:p:0-0:Gzip.CaddyModule:method", name="Gzip.CaddyModule",
            kind="method", language="go", path="p", span=None,
        )
        provision = _method_sym("Gzip.Provision", path="p", span=(2, 2))
        edges = link_caddy_module_dispatch(_ctx([struct, caddy_module, provision])).edges
        assert edges
        assert all(e.line == 0 for e in edges)


class TestRegistryIntegration:
    def test_linker_is_registered(self) -> None:
        # The module-level import above already triggered @register_linker.
        from hypergumbo_core.linkers.registry import get_all_linkers
        names = {reg.name for reg in get_all_linkers()}
        assert "caddy-module-dispatch-linker" in names

    def test_marker_and_method_table_sane(self) -> None:
        assert CADDY_MODULE_MARKER == "CaddyModule"
        assert "Provision" in CADDY_DISPATCHED_METHODS
        assert "ServeHTTP" in CADDY_DISPATCHED_METHODS
        # The marker is the edge SOURCE, never a dispatch target.
        assert CADDY_MODULE_MARKER not in CADDY_DISPATCHED_METHODS
