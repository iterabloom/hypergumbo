# SPDX-License-Identifier: AGPL-3.0-or-later
"""A projected view's ``metrics`` describe the view (WI-lokuj).

A compact or tiered view writes a slim form of each selected node, and its
metrics used to be counted off those slim records. A slim node has no
``supply_chain`` block, so every node was counted in tier ``unknown``; a
selection holds no file node, so every language read ``files: 0``; and the
whole repository's framework-pattern counts sat beside counts of the
selection with nothing to tell them apart.

``_check`` reads EVERY figure of a view's metrics and compares it with what
it claims to describe: the view's own arrays, or the source map. A figure it
has no rule for fails the key-set assertion, so a figure added to a view
later must be given a rule here.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from hypergumbo_core import compact
from hypergumbo_core.compact import (
    CompactConfig,
    format_compact_behavior_map,
    format_tiered_behavior_map,
)
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.metrics import compute_metrics, view_metrics

TIER_NAMES = {1: "first_party", 2: "internal_dep", 3: "external_dep"}
PATTERNS = {"django": 3, "naming-conventions": 0, "test-frameworks": 41}


def _symbol(name: str, language: str, path: str, tier: int) -> Symbol:
    symbol = Symbol(
        id=f"{language}:{path}:1-10:{name}:function", name=name, kind="function",
        language=language, path=path,
        span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
        docstring=f"{name} does one thing and says so at some length.",
    )
    symbol.supply_chain_tier = tier
    symbol.supply_chain_reason = f"tier_{tier}"
    return symbol


def _edge(src: Symbol, dst: Symbol, confidence: float) -> Edge:
    return Edge(id=f"edge:{src.id}->{dst.id}", src=src.id, dst=dst.id,
                edge_type="calls", line=1, confidence=confidence,
                origin="test", origin_run_id="test")


def _repository(per_language: int = 40) -> tuple[dict[str, Any], list[Symbol], list[Edge]]:
    """Three languages; first-party code calling an internal and an external
    dependency, so a selection that keeps the graph connected holds nodes of
    more than one tier. Each language has a route marker, a node with no
    language of its own that the full map counts under its host language
    (ADR-0031); the markers are the entry points, so a view takes them. Two
    file nodes, which no selection takes."""
    symbols: list[Symbol] = []
    routes: list[Symbol] = []
    edges: list[Edge] = []
    for language, ext in (("python", "py"), ("go", "go"), ("javascript", "js")):
        vendored = _symbol(f"{language}_vendored", language, f"vendor/{language}/lib.{ext}", 3)
        shared = _symbol(f"{language}_shared", language, f"libs/{language}/shared.{ext}", 2)
        own = [_symbol(f"{language}_fn_{i}", language, f"src/{language}/m{i % 5}.{ext}", 1)
               for i in range(per_language)]
        route = _symbol(f"GET /{language}", language, f"src/{language}/routes.{ext}", 1)
        route.language, route.discovery_language = None, language
        routes.append(route)
        symbols += [vendored, shared, route, *own]
        edges.append(_edge(route, own[0], 0.9))
        for i, fn in enumerate(own):
            edges.append(_edge(fn, vendored, 0.85))
            edges.append(_edge(fn, shared, 0.5))
            edges.append(_edge(fn, own[(i + 1) % per_language], 0.95))
    files = [Symbol(id=f"python:src/python/m{i}.py:1-1:file:file", name=f"m{i}.py",
                    kind="file", language="python", path=f"src/python/m{i}.py",
                    span=Span(1, 1, 0, 0)) for i in range(2)]
    nodes = [s.to_dict() for s in symbols + files]
    edge_dicts = [e.to_dict() for e in edges]
    profile = {"languages": {"python": {"files": 9, "loc": 900},
                             "go": {"files": 7, "loc": 700},
                             "javascript": {"files": 6, "loc": 600}}}
    behavior_map = {
        "nodes": nodes,
        "edges": edge_dicts,
        "entrypoints": [{"symbol_id": s.id, "kind": "http_route", "confidence": 0.95}
                        for s in routes],
        "profile": profile,
        "metrics": compute_metrics(nodes, edge_dicts, profile=profile,
                                   framework_patterns=PATTERNS),
    }
    return behavior_map, symbols + files, edges


def _check(view: dict[str, Any], source: dict[str, Any], symbols: list[Symbol]) -> None:
    """Every figure of ``view["metrics"]`` against what it describes."""
    metrics = view["metrics"]
    nodes, edges = view["nodes"], view["edges"]
    full = {s.id: s for s in symbols}
    assert set(metrics) == {
        "total_nodes", "total_edges", "total_files", "avg_confidence",
        "edge_confidence", "languages", "by_supply_chain_tier", "repository",
    }

    # The view's own arrays.
    assert metrics["total_nodes"] == len(nodes)
    assert metrics["total_edges"] == len(edges)
    assert metrics["total_files"] == len({n["path"] for n in nodes})
    confidences = sorted(e["confidence"] for e in edges)
    assert metrics["avg_confidence"] == round(sum(confidences) / len(confidences), 3)
    assert metrics["edge_confidence"]["histogram"] == {
        f"{value:.2f}": count for value, count in sorted(Counter(confidences).items())}
    assert metrics["edge_confidence"]["median"] == confidences[(len(confidences) - 1) // 2]

    # Each node under the language the full map counts it under: its own, or
    # its host's when it has none. The slim node carries only the first.
    language_of = {n["id"]: full[n["id"]].language or full[n["id"]].discovery_language
                   for n in nodes}
    assert metrics["languages"] == {
        language: {"nodes": count,
                   "edges": sum(1 for e in edges if language_of[e["src"]] == language)}
        for language, count in Counter(language_of.values()).items()}

    # Each node in the tier of its FULL record; the slim node has none.
    assert all("supply_chain" not in n for n in nodes)
    tier_of = {n["id"]: TIER_NAMES[full[n["id"]].supply_chain_tier] for n in nodes}
    assert metrics["by_supply_chain_tier"] == {
        tier: {"nodes": count,
               "edges": sum(1 for e in edges if tier_of[e["src"]] == tier),
               "edges_incident": sum(1 for e in edges
                                     if tier in (tier_of[e["src"]], tier_of[e["dst"]]))}
        for tier, count in Counter(tier_of.values()).items()}

    # The source map.
    assert metrics["repository"] == {
        "framework_patterns": source["metrics"]["framework_patterns"]}


def _reaches(view: dict[str, Any], source: dict[str, Any]) -> None:
    """The view is a real selection of a multi-language, multi-tier graph;
    without that every comparison in ``_check`` could pass on an empty or a
    whole map."""
    selectable = sum(1 for n in source["nodes"] if n["kind"] != "file")
    assert 0 < len(view["nodes"]) < selectable
    assert view["edges"]
    assert len(view["metrics"]["languages"]) >= 2
    assert len(view["metrics"]["by_supply_chain_tier"]) >= 2
    assert view["metrics"]["total_nodes"] != source["metrics"]["total_nodes"]


@pytest.mark.parametrize(("target_tokens", "per_language"), [(4000, 12), (16000, 40)])
def test_every_figure_of_a_tiered_views_metrics(target_tokens: int, per_language: int) -> None:
    """Each budget on a repository it cannot hold whole."""
    source, symbols, edges = _repository(per_language)
    view = format_tiered_behavior_map(source, symbols, edges, target_tokens)
    _reaches(view, source)
    _check(view, source, symbols)


@pytest.mark.parametrize("connectivity_aware", [False, True])
def test_every_figure_of_a_compact_views_metrics(connectivity_aware: bool) -> None:
    source, symbols, edges = _repository()
    view = format_compact_behavior_map(
        source, symbols, edges, CompactConfig(min_symbols=20, max_symbols=30),
        connectivity_aware=connectivity_aware)
    _reaches(view, source)
    _check(view, source, symbols)


def test_no_node_of_a_view_is_counted_in_an_unknown_tier() -> None:
    """The filed defect, stated on its own: one ``unknown`` bucket holding
    every node, beside a full map that reports real tiers."""
    source, symbols, edges = _repository()
    assert set(source["metrics"]["by_supply_chain_tier"]) == {
        "first_party", "internal_dep", "external_dep"}
    view = format_tiered_behavior_map(source, symbols, edges, 4000)
    assert len(view["metrics"]["by_supply_chain_tier"]) >= 2
    assert "unknown" not in view["metrics"]["by_supply_chain_tier"]


def test_a_node_with_no_language_of_its_own_is_counted_under_its_host() -> None:
    """The same mechanism as the ``unknown`` tier: the slim node drops
    ``discovery_language``, and a count read off it files the node under a
    language ``unknown`` the full map does not have."""
    source, symbols, edges = _repository()
    assert "unknown" not in source["metrics"]["languages"]
    view = format_tiered_behavior_map(source, symbols, edges, 16000)
    assert any("language" not in n for n in view["nodes"])
    assert "unknown" not in view["metrics"]["languages"]


def test_a_view_does_not_report_a_file_count_it_cannot_have() -> None:
    """The full map counts its file nodes per language; a selection holds no
    file node, and says nothing rather than 0."""
    source, symbols, edges = _repository()
    assert source["metrics"]["languages"]["python"]["files"] == 2
    assert source["metrics"]["debug"]["analyzed_file_symbols"] == 2
    view = format_tiered_behavior_map(source, symbols, edges, 16000)
    assert all(n["kind"] != "file" for n in view["nodes"])
    assert "debug" not in view["metrics"]
    for counts in view["metrics"]["languages"].values():
        assert set(counts) == {"nodes", "edges"}


def test_the_search_for_a_selection_leaves_one_metrics_block(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The tiered search recomputes the block for each selection it settles
    on. Each time it must read the source map's block, not the one it wrote
    last, or the second pass would lose ``repository``."""
    source, symbols, edges = _repository()
    calls = []
    real = compact._recompute_view_metrics

    def counted(view_map: dict[str, Any], selected: list[Symbol],
                source_map: dict[str, Any]) -> None:
        real(view_map, selected, source_map)
        calls.append(dict(view_map["metrics"]))
        real(view_map, selected, source_map)
        assert view_map["metrics"] == calls[-1]

    monkeypatch.setattr(compact, "_recompute_view_metrics", counted)
    view = format_tiered_behavior_map(source, symbols, edges, 4000)
    assert len(calls) >= 2
    assert view["metrics"] == calls[-1]
    assert view["metrics"]["repository"] == {"framework_patterns": PATTERNS}


def test_a_source_without_the_count_gives_a_view_without_repository() -> None:
    """An absent count is not a count of nothing."""
    source, symbols, edges = _repository()
    del source["metrics"]["framework_patterns"]
    view = format_tiered_behavior_map(source, symbols, edges, 16000)
    assert "repository" not in view["metrics"]
    assert view["metrics"]["total_nodes"] == len(view["nodes"])


def test_a_source_without_metrics_gives_a_view_without_metrics() -> None:
    source, symbols, edges = _repository()
    del source["metrics"]
    for view in (
        format_tiered_behavior_map(source, symbols, edges, 16000),
        format_compact_behavior_map(source, symbols, edges,
                                    CompactConfig(min_symbols=20, max_symbols=30)),
    ):
        assert view["nodes"]
        assert "metrics" not in view


def test_view_metrics_leaves_the_source_block_alone() -> None:
    source, symbols, _edges = _repository()
    before = repr(source["metrics"])
    block = view_metrics([symbols[0].to_dict()], [], source["metrics"])
    assert repr(source["metrics"]) == before
    assert block["total_nodes"] == 1
    assert block["edge_confidence"] == {"histogram": {}, "median": None}
