# SPDX-License-Identifier: AGPL-3.0-or-later
"""A route marker's host language moved fields, and nothing it ranks moved with it (WI-mupal).

Every route marker is an ADR-0031 Class B stand-in now: ``language`` is None
and the language of the file holding the registration is
``discovery_language``. Entrypoint ranking asks "which language's code is this
about" twice: once to scale the connectivity boost by language dominance, and
once to demote library exports in a language that also has semantic entrypoints
(go and rails put the ``http_route`` entrypoint ON the marker). Both read
``Symbol.host_language``.

The test is metamorphic: the same marker as a Class-B stand-in and as the
Class-A record it used to be must rank every entrypoint identically. Reverting
either read to ``Symbol.language`` breaks it: the route loses its language
weight, and the library export escapes its demotion.
"""
# covers: packages/hypergumbo-core/src/hypergumbo_core/entrypoints.py
from __future__ import annotations

import dataclasses

from hypergumbo_core.analyze.base import make_route_symbol
from hypergumbo_core.entrypoints import detect_entrypoints
from hypergumbo_core.ir import Edge, Span, Symbol


def _fn(name: str, path: str, line: int, **meta: object) -> Symbol:
    return Symbol(
        id=f"go:{path}:{line}-{line}:{name}:function", name=name, kind="function",
        language="go", path=path, span=Span(line, line, 0, 0), origin="go",
        origin_run_id="r", meta=dict(meta),
    )


def _graph() -> tuple[list[Symbol], list[Edge], Symbol]:
    marker = make_route_symbol(
        language="go", path="main.go", span=Span(5, 5, 0, 0), method="GET",
        route_path="/a", origin="go", origin_run_id="r", handler_ref="handler",
    )
    handler, helper = _fn("handler", "main.go", 3), _fn("helper", "main.go", 4)
    exported = _fn("Exported", "lib/lib.go", 2, concepts=[{"concept": "library_export"}])
    edges = [
        Edge.create(src=marker.id, dst=handler.id, edge_type="dispatches_to", line=5,
                    origin="test", origin_run_id="r"),
        Edge.create(src=marker.id, dst=helper.id, edge_type="calls", line=5,
                    origin="test", origin_run_id="r"),
        Edge.create(src=handler.id, dst=helper.id, edge_type="calls", line=3,
                    origin="test", origin_run_id="r"),
        # A caller lifts the demoted export above the rank floor through the
        # in-degree boost, so it stays in the output to be compared.
        Edge.create(src=helper.id, dst=exported.id, edge_type="calls", line=4,
                    origin="test", origin_run_id="r"),
    ]
    return [marker, handler, helper, exported], edges, marker


def _ranks(nodes: list[Symbol], edges: list[Edge]) -> dict[tuple[str, str], float]:
    names = {n.id: n.name for n in nodes}
    return {(ep.kind.value, names[ep.symbol_id]): ep.rank_score for ep in detect_entrypoints(nodes, edges)}


def test_the_marker_is_a_class_b_stand_in() -> None:
    *_, marker = _graph()
    assert (marker.language, marker.discovery_language, marker.protocol_origin) == (None, "go", "http")
    assert marker.host_language == "go"


def test_moving_the_host_language_moves_no_rank() -> None:
    nodes, edges, marker = _graph()
    class_a = dataclasses.replace(marker, language="go", discovery_language=None, protocol_origin=None)
    as_stand_in = _ranks(nodes, edges)
    as_declaration = _ranks([class_a if n is marker else n for n in nodes], edges)
    assert {("http_route", "GET /a"), ("library_export", "Exported")} <= set(as_stand_in)  # reach
    assert as_stand_in == as_declaration
