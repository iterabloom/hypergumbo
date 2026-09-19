# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0057 §14 / WI-lihis: a resolved edge demotes a same-site external stub.

The rule, on synthetic edges from two declared Python producers: an
unresolved edge (``is_resolved=False``, dst an external stub) is demoted —
``rank_score`` scaled by ``SUPERSEDED_STUB_RANK_FACTOR``, never deleted,
``confidence`` untouched — when a RESOLVED edge shares its ``src``, one of
its call lines, its ``edge_type`` and its declared callee name key, and that
resolved edge carries a producer the stub's own ``origin`` lacks. The stamp
``meta.superseded_by`` / ``meta.superseded_by_origin`` records which edge
did it, so a low ``rank_score`` is never an unexplained number. Every
condition has a test that removes it and sees no demotion: a different
callee on the same line is a different call, the same producer's two calls
on one line are not a contradiction, and two stubs never demote each other.
"""
from __future__ import annotations

from typing import Any

import pytest

from hypergumbo_core.analyze import registry as _registry_mod
from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze.registry import (
    SPAN_ROLE_ITEM,
    SPAN_ROLE_TOKEN,
    MergeAnchor,
    as_emitted,
    last_segment,
    register_analyzer,
)
from hypergumbo_core.finalize import SUPERSEDED_STUB_RANK_FACTOR, demote_superseded_stubs
from hypergumbo_core.ir import Edge, ExternalRef, Span, Symbol


def _noop(repo_root: Any) -> AnalysisResult:  # pragma: no cover - never called
    return AnalysisResult(symbols=[], edges=[], run=None)


@pytest.fixture(autouse=True)
def two_producers():
    saved, saved_flag = dict(_registry_mod._ANALYZER_REGISTRY), _registry_mod._discovered
    _registry_mod._ANALYZER_REGISTRY.clear()
    _registry_mod._discovered = True
    register_analyzer("python", backend="ast", priority=50,
                      merge=MergeAnchor(name_key=last_segment("."), span_role=SPAN_ROLE_ITEM))(_noop)
    register_analyzer("pyscip", backend="scip", priority=45, languages=["python"],
                      merge=MergeAnchor(name_key=as_emitted, span_role=SPAN_ROLE_TOKEN))(_noop)
    yield
    _registry_mod._ANALYZER_REGISTRY.clear()
    _registry_mod._ANALYZER_REGISTRY.update(saved)
    _registry_mod._discovered = saved_flag


CALLER = "python:pkg/a.py:1-9:main:function"
WRAP = "python:pkg/b.py:20-30:Result.wrap:method"
OTHER = "python:pkg/b.py:40-50:Result.other:method"
STUB = "python:<external>:0-0:wrap:external_symbol"


def _symbols() -> list[Symbol]:
    def sym(id_: str, name: str, kind: str, path: str, start: int, end: int) -> Symbol:
        return Symbol(id=id_, name=name, kind=kind, language="python", path=path,
                      span=Span(start, end, 0, 0), origin="python", origin_run_id="r")
    return [
        sym(CALLER, "main", "function", "pkg/a.py", 1, 9),
        sym(WRAP, "Result.wrap", "method", "pkg/b.py", 20, 30),
        sym(OTHER, "Result.other", "method", "pkg/b.py", 40, 50),
    ]


def _edge(dst: str, *, origin: list[str], line: int = 5, resolved: bool, edge_type: str = "calls",
          confidence: float = 0.5, lines: list[int] | None = None) -> Edge:
    edge = Edge.create(src=CALLER, dst=dst, edge_type=edge_type, line=line, origin=origin,
                       evidence_type="ast_call_direct", confidence=confidence, origin_run_id="r")
    edge.is_resolved = resolved
    if not resolved:
        edge.dst_ref = ExternalRef(lang="python", module_path="", name="wrap")
    if lines is not None:
        edge.meta = {**(edge.meta or {}), "call_lines": lines}
    edge.rank_score = confidence
    return edge


class TestTheRule:
    def test_a_resolved_call_from_another_producer_demotes_the_same_site_stub(self) -> None:
        stub = _edge(STUB, origin=["python"], resolved=False)
        resolved = _edge(WRAP, origin=["scip"], resolved=True, confidence=0.85)
        edges = [stub, resolved]
        assert demote_superseded_stubs(_symbols(), edges) == 1
        assert len(edges) == 2  # never deleted
        assert stub.rank_score == pytest.approx(0.5 * SUPERSEDED_STUB_RANK_FACTOR)
        assert stub.confidence == 0.5  # ADR-0039 ruling 3: never on confidence
        assert stub.meta["superseded_by"] == resolved.id
        assert stub.meta["superseded_by_origin"] == ["scip"]
        assert resolved.rank_score == 0.85 and "superseded_by" not in (resolved.meta or {})

    def test_a_merged_two_producer_edge_also_supersedes(self) -> None:
        stub = _edge(STUB, origin=["python"], resolved=False)
        resolved = _edge(WRAP, origin=["python", "scip"], resolved=True, confidence=0.95)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 1
        assert stub.meta["superseded_by_origin"] == ["python", "scip"]

    def test_a_shared_call_line_counts_not_only_the_first(self) -> None:
        stub = _edge(STUB, origin=["python"], resolved=False, line=5, lines=[5, 8])
        resolved = _edge(WRAP, origin=["scip"], resolved=True, line=8)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 1


class TestWhatDoesNotDemote:
    def test_a_different_callee_on_the_same_line_is_a_different_call(self) -> None:
        stub = _edge(STUB, origin=["python"], resolved=False)
        resolved = _edge(OTHER, origin=["scip"], resolved=True)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 0
        assert stub.rank_score == 0.5 and "superseded_by" not in (stub.meta or {})

    def test_the_same_producers_two_calls_on_one_line_are_not_a_contradiction(self) -> None:
        stub = _edge(STUB, origin=["python"], resolved=False)
        resolved = _edge(WRAP, origin=["python"], resolved=True)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 0

    def test_a_different_line_does_not_supersede(self) -> None:
        stub = _edge(STUB, origin=["python"], resolved=False, line=5)
        resolved = _edge(WRAP, origin=["scip"], resolved=True, line=6)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 0

    def test_a_different_edge_type_does_not_supersede(self) -> None:
        stub = _edge(STUB, origin=["python"], resolved=False)
        resolved = _edge(WRAP, origin=["scip"], resolved=True, edge_type="references")
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 0

    def test_two_stubs_never_demote_each_other(self) -> None:
        a = _edge(STUB, origin=["python"], resolved=False)
        b = _edge(STUB, origin=["scip"], resolved=False)
        assert demote_superseded_stubs(_symbols(), [a, b]) == 0

    def test_a_language_with_one_producer_is_untouched(self) -> None:
        _registry_mod._ANALYZER_REGISTRY.pop("pyscip")
        stub = _edge(STUB, origin=["python"], resolved=False)
        resolved = _edge(WRAP, origin=["scip"], resolved=True)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 0

    def test_a_stub_without_a_dst_ref_reads_the_callee_from_its_id(self) -> None:
        stub = _edge(STUB, origin=["python"], resolved=False)
        stub.dst_ref = None
        resolved = _edge(WRAP, origin=["scip"], resolved=True)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 1

    def test_an_unset_rank_score_is_taken_from_confidence(self) -> None:
        stub = _edge(STUB, origin=["python"], resolved=False)
        stub.rank_score = None
        resolved = _edge(WRAP, origin=["scip"], resolved=True)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 1
        assert stub.rank_score == pytest.approx(0.5 * SUPERSEDED_STUB_RANK_FACTOR)
