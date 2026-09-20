# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0057 §15 / WI-hupod: a PARTIAL external identity key is absorbed by a complete one.

The syntax arm emits a method call whose receiver module it could not
determine; the type-aware arm emits the SAME call with the module stated.
Under §11 alone the two ``dst`` strings differ and the artifact carries two
edges for one call — 3,973 of 4,133 sites on this repository. §15 rules that
for an endpoint outside the repo the identity is ``dst_ref``, which can
abstain, and not the ``dst`` string, whose module segment defaults to the
``external`` sentinel that ADR-0051's axiom defines as *not a marker for the
absence of an answer*. An external key missing its module component is
PARTIAL; a partial key matches a complete one that agrees on every component
it states, and two partial keys do not match each other.

What is NOT asserted, and has a test saying so: that the stated module is
correct (§15 only says one producer stated one and the other abstained);
that two same-named calls on one line are one call (they are not, and the
rule refuses rather than guesses); that a partial matches a partial.
"""
from __future__ import annotations

from typing import Any

import pytest

from hypergumbo_core.analyze import registry as _registry_mod
from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze.merge_producers import merge_producer_records
from hypergumbo_core.analyze.registry import (
    SPAN_ROLE_ITEM,
    SPAN_ROLE_TOKEN,
    MergeAnchor,
    as_emitted,
    last_segment,
    register_analyzer,
)
from hypergumbo_core.ir import Edge, ExternalRef, Span, Symbol

RUN_PY, RUN_SCIP = "run-python", "run-pyscip"
RUNS: list[dict[str, Any]] = [
    {"execution_id": RUN_PY, "pass": "python"},
    {"execution_id": RUN_SCIP, "pass": "pyscip"},
]
CALLER = "python:pkg/mod.py:14-17:Counter.increment:method"
#: The syntax arm's shape when it could not determine the receiver's module.
STUB = "python:external:0-0:glob:unresolved"
#: The type-aware arm's shape for the same call — the syntax arm's own id
#: grammar, with the module slot filled.
TYPED_REF = ExternalRef(lang="python", module_path="pathlib.Path", name="glob")
TYPED = "python:pathlib.Path:0-0:glob:unresolved"


def _noop(repo_root: Any) -> AnalysisResult:  # pragma: no cover - never called
    return AnalysisResult(symbols=[], edges=[], run=None)


@pytest.fixture(autouse=True)
def two_producers():
    saved, saved_flag = dict(_registry_mod._ANALYZER_REGISTRY), _registry_mod._discovered
    _registry_mod._ANALYZER_REGISTRY.clear()
    _registry_mod._discovered = True
    register_analyzer("python", backend="ast", priority=50,
                      merge=MergeAnchor(name_key=last_segment("."), span_role=SPAN_ROLE_ITEM,
                                        observes=()))(_noop)
    register_analyzer("pyscip", backend="scip", priority=45, languages=["python"],
                      merge=MergeAnchor(name_key=as_emitted, span_role=SPAN_ROLE_TOKEN,
                                        observes=()))(_noop)
    yield
    _registry_mod._ANALYZER_REGISTRY.clear()
    _registry_mod._ANALYZER_REGISTRY.update(saved)
    _registry_mod._discovered = saved_flag


def _paired_symbols() -> list[Symbol]:
    """One declaration both producers emit — what makes the language a
    merge participant at all (``precedence`` is built from paired records)."""
    return [
        Symbol(id=CALLER, name="Counter.increment", kind="method", language="python",
               path="pkg/mod.py", span=Span(14, 17, 0, 0), origin="python",
               origin_run_id=RUN_PY, stable_id="sha256:incumbent"),
        Symbol(id="python:pkg/mod.py:14-14:increment:method", name="increment", kind="method",
               language="python", path="pkg/mod.py", span=Span(14, 14, 11, 20), origin="scip",
               origin_run_id=RUN_SCIP, stable_id="sha256:moniker"),
    ]


def _edge(dst: str, *, run: str, origin: str, line: int = 20, ref: ExternalRef | None = None,
          evidence: str = "ast_call", edge_type: str = "calls",
          callee: str | None = "glob", lines: list[int] | None = None) -> Edge:
    edge = Edge.create(src=CALLER, dst=dst, edge_type=edge_type, line=line, origin=[origin],
                       evidence_type=evidence, confidence=0.75, origin_run_id=run)
    edge.is_resolved = False
    edge.dst_ref = ref
    meta = dict(edge.meta or {})
    if callee is not None:
        meta["callee_name"] = callee
    if lines is not None:
        meta["call_lines"] = lines
    edge.meta = meta or None
    return edge


def _merge(edges: list[Edge]):
    report = merge_producer_records(_paired_symbols(), edges, [dict(r) for r in RUNS])
    return report, edges


class TestTheFold:
    def test_a_partial_key_is_absorbed_by_the_complete_one(self) -> None:
        stub = _edge(STUB, run=RUN_PY, origin="python")
        typed = _edge(TYPED, run=RUN_SCIP, origin="scip", ref=TYPED_REF)
        report, edges = _merge([stub, typed])
        assert len(edges) == 1, "one call is one edge"
        survivor = edges[0]
        assert survivor.dst_ref == TYPED_REF
        assert survivor.dst == TYPED, "the dst string is rebuilt from the stated ref"
        assert survivor.origin == ["python", "scip"]
        assert report.external_folds == 1

    def test_the_survivor_is_the_incumbent_with_a_re_minted_id(self) -> None:
        stub = _edge(STUB, run=RUN_PY, origin="python")
        typed = _edge(TYPED, run=RUN_SCIP, origin="scip", ref=TYPED_REF)
        before = stub.id
        _, edges = _merge([stub, typed])
        survivor = edges[0]
        assert survivor is stub, "incumbent-first (§5)"
        assert survivor.id != before, "Edge.create derives the id from dst, so it is re-minted"
        assert survivor.edge_key is None, "reset so deduplicate_edges recomputes it"

    def test_the_stating_producer_is_named_and_the_abstention_is_not(self) -> None:
        """§15.6. An abstention contributes NO entry at all (§1), so recording
        ``module_path: "external"`` as an alternative would stamp the defect
        into the slot that exists to cure it."""
        stub = _edge(STUB, run=RUN_PY, origin="python")
        typed = _edge(TYPED, run=RUN_SCIP, origin="scip", ref=TYPED_REF)
        _, edges = _merge([stub, typed])
        assert edges[0].attribution["dst_ref"] == ["pyscip"]
        assert "dst_ref" not in (edges[0].alternatives or {})

    def test_two_distinct_pathways_corroborate(self) -> None:
        stub = _edge(STUB, run=RUN_PY, origin="python", evidence="ast_call")
        typed = _edge(TYPED, run=RUN_SCIP, origin="scip", ref=TYPED_REF,
                      evidence="scip_occurrence_ref")
        _, edges = _merge([stub, typed])
        assert edges[0].confidence_source == "corroborated"

    def test_the_absorbed_edge_s_call_sites_survive(self) -> None:
        stub = _edge(STUB, run=RUN_PY, origin="python", line=20, lines=[20, 31])
        typed = _edge(TYPED, run=RUN_SCIP, origin="scip", ref=TYPED_REF, line=31)
        _, edges = _merge([stub, typed])
        assert sorted((edges[0].meta or {}).get("call_lines", [])) == [20, 31]

    def test_the_incumbent_may_be_the_one_that_states_the_module(self) -> None:
        typed = _edge(TYPED, run=RUN_PY, origin="python", ref=TYPED_REF)
        stub = _edge(STUB, run=RUN_SCIP, origin="scip")
        _, edges = _merge([typed, stub])
        assert len(edges) == 1
        assert edges[0].dst_ref == TYPED_REF
        assert edges[0].attribution["dst_ref"] == ["python"]


class TestWhatDoesNotFold:
    def test_two_partial_keys_never_match_each_other(self) -> None:
        """§15.1: neither states a module, so there is nothing to agree on and
        nothing to fill from. (§11 still folds them on the identical ``dst`` —
        what must not happen is §15 claiming an absorption.)"""
        report, _ = _merge([_edge(STUB, run=RUN_PY, origin="python"),
                            _edge(STUB, run=RUN_SCIP, origin="scip")])
        assert report.external_folds == 0

    def test_a_different_callee_on_the_same_line_is_a_different_call(self) -> None:
        stub = _edge(STUB, run=RUN_PY, origin="python", callee="glob")
        typed = _edge("python:pathlib.Path:0-0:resolve:unresolved", run=RUN_SCIP, origin="scip",
                      ref=ExternalRef(lang="python", module_path="pathlib.Path", name="resolve"),
                      callee="resolve")
        _, edges = _merge([stub, typed])
        assert len(edges) == 2

    def test_a_different_edge_type_does_not_fold(self) -> None:
        stub = _edge(STUB, run=RUN_PY, origin="python")
        typed = _edge(TYPED, run=RUN_SCIP, origin="scip", ref=TYPED_REF, edge_type="references")
        _, edges = _merge([stub, typed])
        assert len(edges) == 2

    def test_a_different_line_does_not_fold(self) -> None:
        stub = _edge(STUB, run=RUN_PY, origin="python", line=20)
        typed = _edge(TYPED, run=RUN_SCIP, origin="scip", ref=TYPED_REF, line=21)
        _, edges = _merge([stub, typed])
        assert len(edges) == 2

    def test_one_producers_two_calls_on_a_line_are_not_a_contradiction(self) -> None:
        stub = _edge(STUB, run=RUN_PY, origin="python")
        typed = _edge(TYPED, run=RUN_PY, origin="python", ref=TYPED_REF)
        _, edges = _merge([stub, typed])
        assert len(edges) == 2

    def test_an_in_repo_dst_is_untouched(self) -> None:
        """§15 is about endpoints OUTSIDE the repo; §14 owns the resolved case."""
        stub = _edge(STUB, run=RUN_PY, origin="python")
        inrepo = _edge("python:pkg/other.py:1-9:glob:function", run=RUN_SCIP, origin="scip")
        _, edges = _merge([stub, inrepo])
        assert len(edges) == 2


class TestAmbiguityIsRefused:
    def test_two_complete_candidates_disagreeing_on_the_module_are_reported(self) -> None:
        stub = _edge(STUB, run=RUN_PY, origin="python")
        a = _edge(TYPED, run=RUN_SCIP, origin="scip", ref=TYPED_REF)
        b = _edge("python:glob:0-0:glob:unresolved", run=RUN_SCIP, origin="scip",
                  ref=ExternalRef(lang="python", module_path="glob", name="glob"))
        report, edges = _merge([stub, a, b])
        assert len(edges) == 3, "nothing is folded"
        assert report.external_folds == 0
        assert report.ambiguous, "the refusal is recorded, not silent"

    def test_candidates_that_agree_on_the_module_are_not_ambiguous(self) -> None:
        """Which one absorbs is immaterial when they state the same module."""
        stub = _edge(STUB, run=RUN_PY, origin="python")
        a = _edge(TYPED, run=RUN_SCIP, origin="scip", ref=TYPED_REF)
        b = _edge(TYPED, run=RUN_SCIP, origin="scip", ref=TYPED_REF)
        report, edges = _merge([stub, a, b])
        assert report.external_folds == 1
        assert report.ambiguous == []
