# SPDX-License-Identifier: AGPL-3.0-or-later
"""``dispatches_to`` edges are call-shaped for taint (INV-zuhig).

Seven Framework linkers (go_cobra, decorator_dispatch, django_orm_dispatch,
jackson_dispatch, kafka_streams_dispatch, caddy_module_dispatch,
airflow_framework_dispatch, rust_trait_dispatch — and now
argparse_dispatch) emit ``dispatches_to`` for "runtime dispatch will
invoke dst with data src controls". Before this change taint was blind
to the whole family: ``TAINT_CALL_EDGE_TYPES`` held only ``calls`` and
``module_attr_ref``, so a framework-dispatched handler could never mint
as a ``start_at: callee`` source and BFS could never cross a dispatch
boundary.

INV-zuhig measured the consequence: the self-proof's 18 claims declare
``cmd_*`` handlers as callee-seeded sources, the only production edge
into them is argparse dispatch, and sources minted ZERO flows — every
``confirmed_with_caveats`` was vacuous on the taint side.

Membership is monotone-additive: adjacency and minting only grow, so
this cannot delete a finding by itself (sanitizer registration over
dispatch edges is the one non-additive surface; the last test pins the
``references`` boundary so the analyzer's registration-reference edges
stay inert).
"""
from __future__ import annotations

from typing import ClassVar

from hypergumbo_core.taint import (
    TAINT_CALL_EDGE_TYPES,
    TaintSink,
    TaintSource,
    _is_taint_call_edge,
    propagate_taint_structural,
)


def _make_edge(src: str, dst: str, edge_type: str = "calls") -> dict:
    return {
        "src": src,
        "dst": dst,
        "type": edge_type,
        "is_resolved": not dst.endswith(":unresolved"),
    }


class TestDispatchesToIsCallShaped:
    def test_membership(self) -> None:
        assert "dispatches_to" in TAINT_CALL_EDGE_TYPES

    def test_predicate(self) -> None:
        assert _is_taint_call_edge({"type": "dispatches_to"}) is True

    def test_references_still_excluded(self) -> None:
        # The analyzer's ``set_defaults(func=cmd_x)`` registration edge is
        # a ``references`` edge; only the LINKER's call-shaped edge mints.
        assert _is_taint_call_edge({"type": "references"}) is False


class TestDispatchesToMintsCalleeSources:
    """The INV-zuhig shape: dispatch is the only edge into the source."""

    _SOURCES: ClassVar[list[TaintSource]] = [TaintSource(
        taint_label="runtime_cli_entry",
        module="hypergumbo_core.cli",
        name="cmd_sketch",
        kind="function",
        start_at="callee",
    )]
    _SINKS: ClassVar[list[TaintSink]] = [TaintSink(
        zone="network", trust_level="untrusted",
        module="external", name="net_send", kind="function",
    )]

    def test_dispatch_edge_mints_callee_seeded_source(self) -> None:
        edges = [
            _make_edge(
                "py:cli.py:1-5:build_parser:function",
                "py:cli.py:10-20:cmd_sketch:function",
                edge_type="dispatches_to",
            ),
            _make_edge(
                "py:cli.py:10-20:cmd_sketch:function",
                "py:external:0-0:net_send:unresolved",
            ),
        ]
        findings = propagate_taint_structural(
            edges, self._SOURCES, self._SINKS, [],
        )
        assert len(findings) == 1
        assert "cmd_sketch" in findings[0].source_symbol
        assert findings[0].sink_zone == "network"

    def test_references_edge_does_not_mint(self) -> None:
        # Same graph with the analyzer's raw ``references`` edge instead
        # of the linker's ``dispatches_to``: no minting. This pins the
        # boundary INV-zuhig documents — the reference recorded at the
        # registration site is not evidence the handler runs.
        edges = [
            _make_edge(
                "py:cli.py:1-5:build_parser:function",
                "py:cli.py:10-20:cmd_sketch:function",
                edge_type="references",
            ),
            _make_edge(
                "py:cli.py:10-20:cmd_sketch:function",
                "py:external:0-0:net_send:unresolved",
            ),
        ]
        findings = propagate_taint_structural(
            edges, self._SOURCES, self._SINKS, [],
        )
        assert findings == []

    def test_bfs_traverses_dispatch_edge_mid_path(self) -> None:
        # Source mints on a plain call edge; the path to the sink crosses
        # a dispatch boundary (e.g. a handler registered by the tainted
        # function's caller). Taint must flow across it.
        sources = [TaintSource(
            taint_label="untrusted_input",
            module="external",
            name="read_input",
            kind="function",
        )]
        edges = [
            _make_edge(
                "py:app.py:1-5:main:function",
                "py:external:0-0:read_input:unresolved",
            ),
            _make_edge(
                "py:app.py:1-5:main:function",
                "py:app.py:10-20:handler:function",
                edge_type="dispatches_to",
            ),
            _make_edge(
                "py:app.py:10-20:handler:function",
                "py:external:0-0:net_send:unresolved",
            ),
        ]
        findings = propagate_taint_structural(
            edges, sources, self._SINKS, [],
        )
        assert len(findings) == 1
        assert findings[0].sink_zone == "network"
