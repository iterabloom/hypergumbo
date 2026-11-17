# SPDX-License-Identifier: AGPL-3.0-or-later
"""``AnalysisRun.non_id_edge_endpoints``: every pass's run says how many of its
edge endpoints are not node ids, and names the first few.

``analyze.edge_target.non_id_edge_endpoints`` in ``grammar_only`` mode, called
at the two chokepoints every pass's output crosses before its run is
serialized: ``all_analyzers.collect_analyzer_result`` (analyzers) and
``linkers.registry._run_linker_with_cache`` (linkers). These tests go through
those chokepoints, not through the helper alone.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import hypergumbo_core.analyze.edge_target as edge_target
from hypergumbo_core.analyze.all_analyzers import collect_analyzer_result
from hypergumbo_core.analyze.base import AnalysisResult, make_file_id
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.limits import Limits
from hypergumbo_core.linkers.registry import (
    LinkerContext,
    LinkerResult,
    _run_linker_with_cache,
)
from hypergumbo_core.schema import SCHEMA_VERSION
from hypergumbo_core.survey_io import load_substrate

_FILE = make_file_id("meson", "meson.build")


def _edge(src: str, dst: str) -> Edge:
    return Edge.create(
        src=src, dst=dst, edge_type="includes", line=1, origin="meson-v1",
        origin_run_id="run",
    )


def _sym(name: str) -> Symbol:
    return Symbol(
        id=f"meson:meson.build:2-3:{name}:function", name=name, kind="function",
        language="meson", path="meson.build", span=Span(2, 3, 0, 1),
        origin="meson-v1",
    )


def _collect(edges: list[Edge], symbols: list[Symbol] | None = None) -> dict:
    run = AnalysisRun.create(pass_id="meson", version="1.0.0")
    runs: list[dict] = []
    collect_analyzer_result(
        AnalysisResult(symbols=symbols or [], edges=edges, run=run),
        runs, [], [], [], Limits(),
    )
    assert len(runs) == 1
    return runs[0]


def _endpoint_warnings(run: dict) -> list[str]:
    return [w for w in run.get("warnings", []) if "are not node ids" in w]


class TestTheAnalyzerChokepoint:
    def test_a_hand_built_endpoint_is_counted_and_named(self) -> None:
        run = _collect([_edge(_FILE, "meson:subdir:src")])
        assert run["non_id_edge_endpoints"] == 1
        [warning] = _endpoint_warnings(run)
        assert "'meson:subdir:src'" in warning

    def test_src_and_dst_count_apiece(self) -> None:
        run = _collect([_edge("nix:shell.nix:1:import", "amsmath")])
        assert run["non_id_edge_endpoints"] == 2

    @pytest.mark.parametrize("dst", [
        "go:bytes:0-0:package:package",
        "python:os:0-0:os.environ:attribute",
        "makefile:check:0-0:check:target",
        "markdown:LICENSE:0-0:LICENSE:doc_link",
        "cpp:../common/bits.h:0-0:header:header",
    ])
    def test_an_in_grammar_stand_in_with_a_use_site_token_is_not_counted(
        self, dst: str,
    ) -> None:
        """ADR-0036 Ruling 2's unfinished kind-slot migration is a different
        matter; the count must not be drowned by it."""
        run = _collect([_edge(_FILE, dst)])
        assert run["non_id_edge_endpoints"] == 0
        assert _endpoint_warnings(run) == []

    def test_a_well_formed_id_of_a_node_this_pass_did_not_emit_is_not_counted(
        self,
    ) -> None:
        run = _collect([_edge("meson:other.build:4-9:f:function", _FILE)])
        assert run["non_id_edge_endpoints"] == 0

    def test_an_emitted_symbol_is_not_counted(self) -> None:
        f, g = _sym("f"), _sym("g")
        run = _collect([_edge(f.id, g.id)], [f, g])
        assert run["non_id_edge_endpoints"] == 0

    def test_the_warning_names_the_first_three_distinct_endpoints(self) -> None:
        dsts = ["meson:subdir:a", "meson:subdir:a", "meson:subdir:b",
                "meson:subdir:c", "meson:subdir:d"]
        run = _collect([_edge(_FILE, d) for d in dsts])
        assert run["non_id_edge_endpoints"] == 5
        [warning] = _endpoint_warnings(run)
        assert warning.startswith("UserWarning: 5 edge endpoint(s)")
        assert warning.endswith(
            "first 3 distinct: 'meson:subdir:a', 'meson:subdir:b', 'meson:subdir:c'"
        )

    def test_a_skipped_result_is_untouched(self) -> None:
        run = AnalysisRun.create(pass_id="meson", version="1.0.0")
        result = AnalysisResult(
            edges=[_edge(_FILE, "meson:subdir:src")], run=run,
            skipped=True, skip_reason="grammar unavailable",
        )
        runs: list[dict] = []
        collect_analyzer_result(result, runs, [], [], [], Limits())
        assert runs == []
        assert run.non_id_edge_endpoints is None
        assert run.warnings == []

    def test_a_run_synthesized_for_output_without_one_is_counted(self) -> None:
        result = AnalysisResult(edges=[_edge(_FILE, "meson:subdir:src")], run=None)
        runs: list[dict] = []
        collect_analyzer_result(
            result, runs, [], [], [], Limits(), analyzer_name="meson",
        )
        assert runs[0]["non_id_edge_endpoints"] == 1


class TestTheLinkerChokepoint:
    def _run(self, edges: list[Edge], symbols: list[Symbol] | None = None) -> AnalysisRun:
        run = AnalysisRun.create(pass_id="lk", version="1.0.0")
        result = LinkerResult(symbols=symbols or [], edges=edges, run=run)
        _run_linker_with_cache(lambda ctx: result, LinkerContext(repo_root=Path("/t")))
        return run

    def test_a_hand_built_endpoint_is_counted_and_named(self) -> None:
        run = self._run([_edge(_FILE, "http:route:/users")])
        assert run.non_id_edge_endpoints == 1
        assert "'http:route:/users'" in run.warnings[-1]

    def test_an_edge_between_other_passes_nodes_is_not_counted(self) -> None:
        """A linker's edges end on analyzers' nodes; that is what a linker
        is for, so the grammar question is the only one asked."""
        run = self._run([_edge(
            "python:a.py:1-2:f:function", "javascript:b.js:3-4:g:function",
        )])
        assert run.non_id_edge_endpoints == 0
        assert run.warnings == []


class TestTheChokepointsAskThePublicFunction:
    """Both chokepoints count with ``non_id_edge_endpoints`` itself, in
    grammar mode, not with a copy of its rule."""

    @pytest.fixture
    def spy(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []

        def fake(symbols: Any, edges: Any, **kwargs: Any) -> list[tuple[str, Edge]]:
            edges = list(edges)
            calls.append({"symbols": list(symbols), "edges": edges, **kwargs})
            return [("dst", edges[0])] * 7

        monkeypatch.setattr(edge_target, "non_id_edge_endpoints", fake)
        return calls

    def test_the_analyzer_chokepoint(self, spy: list[dict[str, Any]]) -> None:
        f = _sym("f")
        edge = _edge(f.id, f.id)
        run = _collect([edge], [f])
        assert spy == [{"symbols": [f], "edges": [edge], "grammar_only": True}]
        assert run["non_id_edge_endpoints"] == 7

    def test_the_linker_chokepoint(self, spy: list[dict[str, Any]]) -> None:
        edge = _edge(_FILE, _FILE)
        run = TestTheLinkerChokepoint()._run([edge])
        assert spy == [{"symbols": [], "edges": [edge], "grammar_only": True}]
        assert run.non_id_edge_endpoints == 7


class TestTheFieldOnASavedMap:
    @pytest.mark.parametrize("value", [None, 0, 4])
    def test_round_trip(self, tmp_path: Path, value: int | None) -> None:
        run = AnalysisRun.create(pass_id="meson", version="1.0.0")
        run.non_id_edge_endpoints = value
        path = tmp_path / "survey.json"
        path.write_text(json.dumps({
            "schema_version": SCHEMA_VERSION, "view": "behavior_map",
            "nodes": [], "edges": [], "analysis_runs": [run.to_dict()],
        }))
        [loaded] = load_substrate(path)["analysis_runs"]
        assert "non_id_edge_endpoints" in loaded
        assert loaded["non_id_edge_endpoints"] == value
