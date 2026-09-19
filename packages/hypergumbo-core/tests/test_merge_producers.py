# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0057 §3 / WI-kokiz: the merge pass, on synthetic two-producer input.

Two producers are registered in an isolated registry for the language
``python``: ``python`` (the incumbent — ``ast`` backend, item span, dotted
names) and ``pyscip`` (a ``scip`` backend, token span, names as emitted).
Records carry ``origin_run_id`` values that join to two AnalysisRun dicts,
which is how the pass learns who produced what. Every rule the module
docstring states is pinned here: pairing by declared key and span role,
incumbent-first scalars with disjoint-coverage fill, origin and meta union,
the item span, the new id, edge / usage-context rewiring, ambiguity left
alone and reported, the single-producer no-op, and the refusal.
"""
from __future__ import annotations

from typing import Any

import pytest

from hypergumbo_core.analyze import registry as _registry_mod
from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze.merge_producers import (
    ABSTENTION_BLIND_ATTRIBUTES,
    CORROBORATED_CONFIDENCE,
    PASS_ID,
    MergeReport,
    merge_producer_records,
)
from hypergumbo_core.analyze.registry import (
    SPAN_ROLE_ITEM,
    SPAN_ROLE_TOKEN,
    MergeAnchor,
    MergeDeclarationError,
    UndeclaredProducerError,
    as_emitted,
    last_segment,
    register_analyzer,
)
from hypergumbo_core.ir import Edge, Span, Symbol, UsageContext, deduplicate_edges

RUN_PY, RUN_SCIP, RUN_LINKER = "run-python", "run-pyscip", "run-linker"
RUNS: list[dict[str, Any]] = [
    {"execution_id": RUN_PY, "pass": "python"},
    {"execution_id": RUN_SCIP, "pass": "pyscip"},
    {"execution_id": RUN_LINKER, "pass": "containment-linker"},
]


def _noop(repo_root: Any) -> AnalysisResult:  # pragma: no cover - never called
    return AnalysisResult(symbols=[], edges=[], run=None)


@pytest.fixture(autouse=True)
def two_producers():
    saved, saved_flag = dict(_registry_mod._ANALYZER_REGISTRY), _registry_mod._discovered
    _registry_mod._ANALYZER_REGISTRY.clear()
    _registry_mod._discovered = True
    register_analyzer(
        "python", backend="ast", priority=50,
        merge=MergeAnchor(name_key=last_segment("."), span_role=SPAN_ROLE_ITEM,
                          observes=("is_exported",)),
    )(_noop)
    register_analyzer(
        "pyscip", backend="scip", priority=45, languages=["python"],
        merge=MergeAnchor(name_key=as_emitted, span_role=SPAN_ROLE_TOKEN, observes=()),
    )(_noop)
    yield
    _registry_mod._ANALYZER_REGISTRY.clear()
    _registry_mod._ANALYZER_REGISTRY.update(saved)
    _registry_mod._discovered = saved_flag


def _sym(
    name: str, kind: str, start: int, end: int, *, run: str, origin: str,
    path: str = "pkg/mod.py", stable_id: str | None = None, meta: dict[str, Any] | None = None,
    signature: str | None = None, start_col: int = 0, end_col: int = 0,
) -> Symbol:
    return Symbol(
        id=f"python:{path}:{start}-{end}:{name}:{kind}",
        name=name, kind=kind, language="python", path=path,
        span=Span(start, end, start_col, end_col),
        origin=origin, origin_run_id=run, stable_id=stable_id, meta=meta, signature=signature,
    )


def _edge(src: str, dst: str, edge_type: str = "calls", origin: str = "python") -> Edge:
    return Edge.create(src=src, dst=dst, edge_type=edge_type, line=1, origin=origin,
                       evidence_type="ast_call", confidence=0.9,
                       origin_run_id=RUN_PY if origin == "python" else RUN_SCIP)


def _incumbent_method() -> Symbol:
    return _sym("Counter.increment", "method", 14, 17, run=RUN_PY, origin="python",
                stable_id="sha256:incumbent", meta={"visibility": "public"},
                signature="def increment(self, by)")


def _scip_method() -> Symbol:
    return _sym("increment", "method", 14, 14, run=RUN_SCIP, origin="scip",
                stable_id="sha256:moniker", meta={"scip_symbol": "pkg/Counter#increment().", "visibility": "pub"},
                start_col=11, end_col=20)


class TestPairing:
    def test_a_token_inside_the_item_with_the_same_key_merges(self) -> None:
        symbols = [_incumbent_method(), _scip_method()]
        runs = [dict(r) for r in RUNS]
        report = merge_producer_records(symbols, [], runs)
        assert len(report.merged) == 1
        assert len(symbols) == 1
        [merged] = symbols
        assert merged.name == "Counter.increment" and merged.kind == "method"
        assert report.languages == ["python"]

    def test_the_merged_record_has_the_item_span_and_a_minted_id(self) -> None:
        symbols = [_incumbent_method(), _scip_method()]
        merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        [merged] = symbols
        assert (merged.span.start_line, merged.span.end_line) == (14, 17)
        # ADR-0036: the id derives from the merged attributes.
        assert merged.id == "python:pkg/mod.py:14-17:Counter.increment:method"

    def test_a_different_key_does_not_merge(self) -> None:
        symbols = [_incumbent_method(), _sym("reset", "method", 14, 14, run=RUN_SCIP, origin="scip")]
        report = merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        assert report.merged == [] and len(symbols) == 2

    def test_a_token_outside_the_item_does_not_merge(self) -> None:
        symbols = [_incumbent_method(), _sym("increment", "method", 30, 30, run=RUN_SCIP, origin="scip")]
        report = merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        assert report.merged == [] and len(symbols) == 2

    def test_two_same_named_items_are_separated_by_containment(self) -> None:
        """The recorded sample's two ``greet``s: the key is ambiguous, the
        containment is not."""
        trait_sig = _sym("Greeter.greet", "method", 25, 25, run=RUN_PY, origin="python")
        impl = _sym("Counter.greet", "method", 29, 31, run=RUN_PY, origin="python")
        scip_sig = _sym("greet", "method", 25, 25, run=RUN_SCIP, origin="scip")
        scip_impl = _sym("greet", "method", 29, 29, run=RUN_SCIP, origin="scip")
        symbols = [trait_sig, impl, scip_sig, scip_impl]
        report = merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        assert len(report.merged) == 2 and report.ambiguous == []
        assert {s.name for s in symbols} == {"Greeter.greet", "Counter.greet"}

    def test_a_record_the_other_producer_never_saw_stays_as_emitted(self) -> None:
        alias = _sym("AardvarkResult", "type_alias", 3, 3, run=RUN_SCIP, origin="scip")
        symbols = [_incumbent_method(), _scip_method(), alias]
        merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        assert alias in symbols and len(symbols) == 2


class TestArbitrationAndProvenance:
    def test_incumbent_first_on_a_contested_attribute(self) -> None:
        incumbent = _sym("parse_configs", "function", 23, 40, run=RUN_PY, origin="python")
        other = _sym("parse_configs", "method", 23, 23, run=RUN_SCIP, origin="scip")
        symbols = [incumbent, other]
        report = merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        assert symbols[0].kind == "function"
        [record] = report.merged
        assert record.candidates["kind"] == [("function", ("python",)), ("method", ("pyscip",))]

    def test_agreement_is_one_value_with_two_provenances(self) -> None:
        symbols = [_incumbent_method(), _scip_method()]
        report = merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        [record] = report.merged
        assert record.candidates["kind"] == [("method", ("python", "pyscip"))]
        assert record.members == {
            "python": "python:pkg/mod.py:14-17:Counter.increment:method",
            "pyscip": "python:pkg/mod.py:14-14:increment:method",
        }

    def test_origin_is_both_producers_incumbent_first(self) -> None:
        symbols = [_incumbent_method(), _scip_method()]
        merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        assert symbols[0].origin == ["python", "scip"]

    def test_meta_is_the_union_with_the_incumbent_winning_a_shared_key(self) -> None:
        symbols = [_incumbent_method(), _scip_method()]
        merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        assert symbols[0].meta == {
            "scip_symbol": "pkg/Counter#increment().",  # only SCIP had it: kept
            "visibility": "public",  # both had it: incumbent's
        }

    def test_stable_id_is_the_incumbents_and_a_missing_one_is_filled(self) -> None:
        symbols = [_incumbent_method(), _scip_method()]
        merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        assert symbols[0].stable_id == "sha256:incumbent"
        bare = _sym("Counter.increment", "method", 14, 17, run=RUN_PY, origin="python")
        symbols = [bare, _scip_method()]
        merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        assert symbols[0].stable_id == "sha256:moniker"
        assert symbols[0].signature is None  # neither observed it: stays unset

    def test_an_attribute_only_the_other_producer_observed_is_taken(self) -> None:
        incumbent = _sym("Counter.increment", "method", 14, 17, run=RUN_PY, origin="python")
        other = _sym("increment", "method", 14, 14, run=RUN_SCIP, origin="scip",
                     signature="fn increment(&mut self, by: i32) -> i32")
        symbols = [incumbent, other]
        report = merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        assert symbols[0].signature == "fn increment(&mut self, by: i32) -> i32"
        [record] = report.merged
        assert record.candidates["signature"] == [
            ("fn increment(&mut self, by: i32) -> i32", ("pyscip",)),
        ]

    def test_the_merged_record_joins_to_this_passs_run(self) -> None:
        symbols = [_incumbent_method(), _scip_method()]
        runs = [dict(r) for r in RUNS]
        report = merge_producer_records(symbols, [], runs)
        assert report.run is not None and report.run.pass_id == PASS_ID
        assert symbols[0].origin_run_id == report.run.execution_id
        assert runs[-1]["pass"] == PASS_ID and runs[-1]["execution_id"] == report.run.execution_id
        assert report.run.nodes_emitted == 1


class TestThePolicyIsTheOnlyKnob:
    """WI-hukuf: precedence and the corroboration level come from the policy;
    the built-in policy is what every other test here exercises."""

    def test_a_global_preference_flips_every_contested_attribute(self) -> None:
        from hypergumbo_core.arbitration import ArbitrationPolicy

        incumbent = _sym("parse_configs", "function", 23, 40, run=RUN_PY, origin="python")
        other = _sym("parse_configs", "method", 23, 23, run=RUN_SCIP, origin="scip")
        symbols = [incumbent, other]
        merge_producer_records(symbols, [], [dict(r) for r in RUNS], policy=ArbitrationPolicy(prefer=("scip",)))
        [merged] = symbols
        assert merged.kind == "method"  # the scip value, not the incumbent's
        assert merged.attribution["kind"] == ["pyscip"]
        assert merged.alternatives["kind"] == [{"value": "function", "origin": ["python"]}]
        assert merged.origin == ["scip", "python"]  # the records' emitted origins, policy order
        assert merged.span is not None and (merged.span.start_line, merged.span.end_line) == (23, 40)  # the item span still

    def test_a_per_attribute_preference_flips_only_that_attribute(self) -> None:
        from hypergumbo_core.arbitration import ArbitrationPolicy

        incumbent = _sym("parse_configs", "function", 23, 40, run=RUN_PY, origin="python",
                         stable_id="sha256:incumbent")
        other = _sym("parse_configs", "method", 23, 23, run=RUN_SCIP, origin="scip", stable_id="sha256:moniker")
        symbols = [incumbent, other]
        merge_producer_records(symbols, [], [dict(r) for r in RUNS],
                               policy=ArbitrationPolicy(prefer_by_attribute={"kind": ("scip",)}))
        [merged] = symbols
        assert merged.kind == "method" and merged.attribution["kind"] == ["pyscip"]
        assert merged.stable_id == "sha256:incumbent" and merged.attribution["stable_id"] == ["python"]
        assert merged.origin == ["python", "scip"]

    def test_the_corroboration_level_is_the_policys(self) -> None:
        from hypergumbo_core.arbitration import ArbitrationPolicy

        symbols = [_incumbent_method(), _scip_method(), _sym("caller", "function", 30, 40, run=RUN_PY, origin="python")]
        caller = symbols[2].id
        edges = [
            _edge(caller, _incumbent_method().id, origin="python"),
            _edge(caller, _scip_method().id, origin="scip"),
        ]
        edges[1].evidence_type = "scip_reference"  # a distinct pathway from the incumbent's ast_call
        report = merge_producer_records(symbols, edges, [dict(r) for r in RUNS],
                                        policy=ArbitrationPolicy(corroborated_confidence=0.9))
        assert report.corroborated == 1
        [survivor] = [e for e in edges if e.confidence_source == "corroborated"]
        assert survivor.confidence == 0.9


class TestEdgesAndContexts:
    def test_edges_from_both_producers_are_rewired_and_collapse_on_dedup(self) -> None:
        caller_inc = _sym("main", "function", 1, 5, run=RUN_PY, origin="python")
        caller_scip = _sym("main", "function", 1, 1, run=RUN_SCIP, origin="scip")
        symbols = [caller_inc, _incumbent_method(), caller_scip, _scip_method()]
        edges = [
            _edge(caller_inc.id, symbols[1].id, origin="python"),
            _edge(caller_scip.id, symbols[3].id, origin="scip"),
        ]
        report = merge_producer_records(symbols, edges, [dict(r) for r in RUNS])
        assert len(report.merged) == 2
        merged_ids = {s.id for s in symbols}
        assert all(e.src in merged_ids and e.dst in merged_ids for e in edges)
        assert all(e.edge_key is None for e in edges if "scip" in e.origin)
        assert len(deduplicate_edges(edges)) == 1  # §11: agree on src, dst, type -> one edge

    def test_derived_from_and_usage_contexts_are_rewired(self) -> None:
        symbols = [_incumbent_method(), _scip_method()]
        scip_id = symbols[1].id
        edge = _edge("python:pkg/mod.py:1-5:main:function", "python:pkg/mod.py:1-1:x:variable")
        edge.derived_from = [scip_id]
        context = UsageContext(
            id="uc1", kind="call", context_name="increment", symbol_ref=scip_id,
            position="args[0]", metadata={}, path="pkg/mod.py", span=Span(14, 14, 0, 0),
        )
        merge_producer_records(symbols, [edge], [dict(r) for r in RUNS], usage_contexts=[context])
        assert edge.derived_from == [symbols[0].id]
        assert context.symbol_ref == symbols[0].id


class TestWhatItRefusesToGuess:
    def test_two_candidate_partners_leave_both_unmerged_and_reported(self) -> None:
        a = _sym("A.run", "method", 10, 20, run=RUN_PY, origin="python")
        b = _sym("B.run", "method", 10, 20, run=RUN_PY, origin="python")  # same key, same span
        other = _sym("run", "method", 10, 10, run=RUN_SCIP, origin="scip")
        symbols = [a, b, other]
        report = merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        assert report.merged == [] and len(symbols) == 3
        assert report.ambiguous == [(other.id, [a.id, b.id])]

    def test_one_incumbent_claimed_twice_is_left_alone(self) -> None:
        incumbent = _incumbent_method()
        first = _sym("increment", "method", 14, 14, run=RUN_SCIP, origin="scip", start_col=4)
        second = _sym("increment", "method", 15, 15, run=RUN_SCIP, origin="scip")
        symbols = [incumbent, first, second]
        report = merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        assert report.merged == [] and len(symbols) == 3
        assert {r[0] for r in report.ambiguous} == {first.id, second.id}


class TestNoOpAndRefusal:
    def test_a_single_producer_is_untouched_and_no_run_is_appended(self) -> None:
        incumbent = _incumbent_method()
        symbols = [incumbent]
        edges = [_edge(incumbent.id, incumbent.id)]
        runs = [dict(r) for r in RUNS]
        report = merge_producer_records(symbols, edges, runs)
        assert isinstance(report, MergeReport) and report.merged == [] and report.run is None
        assert symbols[0] is incumbent and edges[0].edge_key is not None
        assert [r["pass"] for r in runs] == ["python", "pyscip", "containment-linker"]

    def test_records_from_a_non_producer_pass_are_ignored(self) -> None:
        linker_record = _sym("increment", "method", 14, 14, run=RUN_LINKER, origin="containment-linker")
        symbols = [_incumbent_method(), linker_record]
        report = merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        assert report.merged == [] and linker_record in symbols

    def test_a_record_with_no_span_never_pairs(self) -> None:
        incumbent = _incumbent_method()
        spanless = _scip_method()
        spanless.span = None
        symbols = [incumbent, spanless]
        report = merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        assert report.merged == []

    def test_an_undeclared_producer_is_refused_by_name(self) -> None:
        register_analyzer("pyother", backend="lsp", priority=60, languages=["python"])(_noop)
        runs = [dict(r) for r in RUNS] + [{"execution_id": "run-other", "pass": "pyother"}]
        symbols = [_incumbent_method(), _sym("increment", "method", 14, 14, run="run-other", origin="pyother")]
        with pytest.raises(UndeclaredProducerError, match="pyother"):
            merge_producer_records(symbols, [], runs)


class TestItemAgainstItem:
    def test_two_item_role_producers_pair_on_equal_spans(self) -> None:
        _registry_mod._ANALYZER_REGISTRY.pop("pyscip")
        register_analyzer(
            "pyscip", backend="scip", priority=45, languages=["python"],
            merge=MergeAnchor(name_key=as_emitted, span_role=SPAN_ROLE_ITEM),
        )(_noop)
        same = _sym("increment", "method", 14, 17, run=RUN_SCIP, origin="scip")
        shifted = _sym("increment", "method", 14, 18, run=RUN_SCIP, origin="scip")
        symbols = [_incumbent_method(), same]
        assert len(merge_producer_records(symbols, [], [dict(r) for r in RUNS]).merged) == 1
        symbols = [_incumbent_method(), shifted]
        assert merge_producer_records(symbols, [], [dict(r) for r in RUNS]).merged == []


class TestRolesAndProducersBeyondTheRustShape:
    def _reregister(self, name: str, *, backend: str, priority: int, role: str) -> None:
        _registry_mod._ANALYZER_REGISTRY.pop(name, None)
        key = last_segment(".") if name == "python" else as_emitted
        register_analyzer(
            name, backend=backend, priority=priority, languages=["python"],
            merge=MergeAnchor(name_key=key, span_role=role),
        )(_noop)

    def test_a_token_role_incumbent_takes_the_other_producers_item_span(self) -> None:
        """Reverse roles: the incumbent spans the token, the alternative the
        item. The merged record still gets the ITEM span."""
        self._reregister("python", backend="ast", priority=50, role=SPAN_ROLE_TOKEN)
        self._reregister("pyscip", backend="scip", priority=45, role=SPAN_ROLE_ITEM)
        incumbent = _sym("Counter.increment", "method", 14, 14, run=RUN_PY, origin="python", start_col=8)
        other = _sym("increment", "method", 14, 17, run=RUN_SCIP, origin="scip")
        symbols = [incumbent, other]
        report = merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        assert len(report.merged) == 1
        assert (symbols[0].span.start_line, symbols[0].span.end_line) == (14, 17)
        assert symbols[0].id == "python:pkg/mod.py:14-17:Counter.increment:method"

    def test_two_token_role_producers_pair_on_the_same_start_position(self) -> None:
        self._reregister("python", backend="ast", priority=50, role=SPAN_ROLE_TOKEN)
        self._reregister("pyscip", backend="scip", priority=45, role=SPAN_ROLE_TOKEN)
        incumbent = _sym("Counter.increment", "method", 14, 14, run=RUN_PY, origin="python", start_col=8)
        same = _sym("increment", "method", 14, 14, run=RUN_SCIP, origin="scip", start_col=8)
        elsewhere = _sym("increment", "method", 14, 14, run=RUN_SCIP, origin="scip", start_col=30)
        assert len(merge_producer_records([incumbent, same], [], [dict(r) for r in RUNS]).merged) == 1
        incumbent = _sym("Counter.increment", "method", 14, 14, run=RUN_PY, origin="python", start_col=8)
        assert merge_producer_records([incumbent, elsewhere], [], [dict(r) for r in RUNS]).merged == []

    def test_a_record_with_no_run_or_no_language_is_left_alone(self) -> None:
        orphan = _sym("increment", "method", 14, 14, run="", origin="scip")
        unlanguaged = _sym("increment", "method", 14, 14, run=RUN_SCIP, origin="scip")
        unlanguaged.language = None
        symbols = [_incumbent_method(), orphan, unlanguaged]
        report = merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        assert report.merged == [] and len(symbols) == 3

    def test_a_third_producer_joins_an_already_merged_record(self) -> None:
        """Three anchored producers: the two alternatives fold into the same
        incumbent record, and the report keeps one entry with three members."""
        register_analyzer(
            "pylsp", backend="lsp", priority=60, languages=["python"],
            merge=MergeAnchor(name_key=as_emitted, span_role=SPAN_ROLE_TOKEN),
        )(_noop)
        runs = [dict(r) for r in RUNS] + [{"execution_id": "run-lsp", "pass": "pylsp"}]
        lsp = _sym("increment", "method", 14, 14, run="run-lsp", origin="lsp",
                   signature="fn increment(&mut self, by: i32)")
        symbols = [_incumbent_method(), _scip_method(), lsp]
        report = merge_producer_records(symbols, [], runs)
        assert len(symbols) == 1 and len(report.merged) == 1
        [record] = report.merged
        assert set(record.members) == {"python", "pyscip", "pylsp"}
        # incumbent_first: non-alternative backends before the scip arm, so the
        # lsp producer folds in before scip and origin records that order.
        assert symbols[0].origin == ["python", "lsp", "scip"]
        assert record.merged_id == symbols[0].id


# ---------------------------------------------------------------------------
# WI-binis / ADR-0057 §1, §4, §6, §13: the provenance slot and the edge fold
# ---------------------------------------------------------------------------


def _scip_edge(src: str, dst: str, *, confidence: float = 0.85, evidence: str = "scip_occurrence_ref",
               line: int = 1) -> Edge:
    return Edge.create(src=src, dst=dst, edge_type="calls", line=line, origin="scip",
                       evidence_type=evidence, confidence=confidence, origin_run_id=RUN_SCIP)


def _ast_edge(src: str, dst: str, *, confidence: float = 0.5, evidence: str = "ast_call_direct",
              line: int = 1) -> Edge:
    return Edge.create(src=src, dst=dst, edge_type="calls", line=line, origin="python",
                       evidence_type=evidence, confidence=confidence, origin_run_id=RUN_PY)


class TestTheProvenanceSlotOnSymbols:
    def test_a_contested_attribute_records_both_values(self) -> None:
        incumbent = _sym("parse_configs", "function", 23, 40, run=RUN_PY, origin="python")
        other = _sym("parse_configs", "method", 23, 23, run=RUN_SCIP, origin="scip")
        symbols = [incumbent, other]
        merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        [merged] = symbols
        assert merged.kind == "function"  # the stamped default: incumbent first
        assert merged.attribution["kind"] == ["python"]
        assert merged.alternatives["kind"] == [{"value": "method", "origin": ["pyscip"]}]

    def test_agreement_is_one_value_with_two_provenances_and_no_alternative(self) -> None:
        symbols = [_incumbent_method(), _scip_method()]
        merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        [merged] = symbols
        assert merged.attribution["kind"] == ["python", "pyscip"]
        assert merged.attribution["name"] == ["python"]  # names differ by shape: contested
        assert "kind" not in merged.alternatives
        assert merged.alternatives["name"] == [{"value": "increment", "origin": ["pyscip"]}]

    def test_disjoint_coverage_is_one_value_with_one_provenance(self) -> None:
        incumbent = _sym("Counter.increment", "method", 14, 17, run=RUN_PY, origin="python")
        other = _sym("increment", "method", 14, 14, run=RUN_SCIP, origin="scip",
                     signature="fn increment(&mut self, by: i32) -> i32")
        symbols = [incumbent, other]
        merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        [merged] = symbols
        assert merged.signature == "fn increment(&mut self, by: i32) -> i32"
        assert merged.attribution["signature"] == ["pyscip"]
        assert "signature" not in merged.alternatives
        assert "docstring" not in merged.attribution  # neither observed it: no entry at all

    def test_the_token_span_is_kept_as_the_alternative(self) -> None:
        symbols = [_incumbent_method(), _scip_method()]
        merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        [merged] = symbols
        assert merged.attribution["span"] == ["python"]
        assert merged.alternatives["span"] == [
            {"value": {"start_line": 14, "end_line": 14, "start_col": 11, "end_col": 20}, "origin": ["pyscip"]},
        ]

    def test_a_single_producer_record_carries_no_slot(self) -> None:
        incumbent = _incumbent_method()
        symbols = [incumbent]
        merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        assert incumbent.attribution is None and incumbent.alternatives is None

    def test_the_slot_round_trips_through_the_dict_form(self) -> None:
        symbols = [_incumbent_method(), _scip_method()]
        merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        [merged] = symbols
        as_dict = merged.to_dict()
        assert as_dict["attribution"] == merged.attribution
        assert as_dict["alternatives"] == merged.alternatives
        back = Symbol.from_dict(as_dict)
        assert back.attribution == merged.attribution and back.alternatives == merged.alternatives
        assert "attribution" not in _incumbent_method().to_dict()  # omitted when None


class TestTheEdgeFold:
    def _pair(self) -> tuple[list[Symbol], list[Edge]]:
        caller_inc = _sym("main", "function", 1, 5, run=RUN_PY, origin="python")
        caller_scip = _sym("main", "function", 1, 1, run=RUN_SCIP, origin="scip")
        callee_inc, callee_scip = _incumbent_method(), _scip_method()
        symbols = [caller_inc, callee_inc, caller_scip, callee_scip]
        return symbols, [caller_inc, caller_scip, callee_inc, callee_scip]

    def test_two_pathways_to_one_edge_corroborate_at_the_declared_level(self) -> None:
        symbols, (ci, cs, ki, ks) = self._pair()
        edges = [_ast_edge(ci.id, ki.id, line=3), _scip_edge(cs.id, ks.id, line=4)]
        report = merge_producer_records(symbols, edges, [dict(r) for r in RUNS])
        assert len(edges) == 1 and report.edges_folded == 1 and report.corroborated == 1
        [edge] = edges
        assert edge.confidence == CORROBORATED_CONFIDENCE == 0.95
        assert edge.confidence_source == "corroborated"
        assert edge.origin == ["python", "scip"]
        assert edge.evidence_type == "ast_call_direct"  # categorical: incumbent first
        assert edge.attribution["confidence"] == ["python", "pyscip"]
        assert edge.alternatives["confidence"] == [
            {"value": 0.5, "origin": ["python"]}, {"value": 0.85, "origin": ["pyscip"]},
        ]
        assert edge.attribution["evidence_type"] == ["python"]
        assert edge.alternatives["evidence_type"] == [{"value": "scip_occurrence_ref", "origin": ["pyscip"]}]
        assert sorted(edge.meta["call_lines"]) == [3, 4]  # both sites survive the fold
        assert edge.edge_key is None
        assert edge.origin_run_id == report.run.execution_id

    def test_the_same_pathway_twice_is_not_new_evidence(self) -> None:
        symbols, (ci, cs, ki, ks) = self._pair()
        edges = [_ast_edge(ci.id, ki.id, confidence=0.5), _scip_edge(cs.id, ks.id, confidence=0.85, evidence="ast_call_direct")]
        report = merge_producer_records(symbols, edges, [dict(r) for r in RUNS])
        assert len(edges) == 1 and report.corroborated == 0
        [edge] = edges
        assert edge.confidence == 0.5 and edge.confidence_source == "emitter_constant"
        assert edge.attribution["confidence"] == ["python"]
        assert edge.alternatives["confidence"] == [{"value": 0.85, "origin": ["pyscip"]}]

    def test_an_edge_only_one_producer_saw_is_untouched(self) -> None:
        symbols, (ci, cs, ki, ks) = self._pair()
        lonely = _scip_edge(cs.id, ks.id)
        edges = [lonely]
        merge_producer_records(symbols, edges, [dict(r) for r in RUNS])
        assert edges == [lonely] and lonely.attribution is None
        assert lonely.confidence == 0.85 and lonely.confidence_source == "emitter_constant"

    def test_a_fold_with_no_symbol_merge_still_records_the_run(self) -> None:
        """Two producers emitted the same edge between records that did not
        pair: the edges still fold, and the pass's run is still appended."""
        a = _sym("A.run", "method", 10, 20, run=RUN_PY, origin="python")
        b = _sym("B.run", "method", 10, 20, run=RUN_PY, origin="python")
        other = _sym("run", "method", 10, 10, run=RUN_SCIP, origin="scip")
        edges = [_ast_edge(a.id, b.id), _scip_edge(a.id, b.id)]
        runs = [dict(r) for r in RUNS]
        report = merge_producer_records([a, b, other], edges, runs)
        assert report.merged == [] and report.edges_folded == 1
        assert runs[-1]["pass"] == PASS_ID and report.run is not None

    def test_the_slot_round_trips_on_an_edge(self) -> None:
        symbols, (ci, cs, ki, ks) = self._pair()
        edges = [_ast_edge(ci.id, ki.id), _scip_edge(cs.id, ks.id)]
        merge_producer_records(symbols, edges, [dict(r) for r in RUNS])
        [edge] = edges
        as_dict = edge.to_dict()
        assert as_dict["attribution"] == edge.attribution and as_dict["alternatives"] == edge.alternatives
        back = Edge.from_dict(as_dict)
        assert back.attribution == edge.attribution and back.confidence_source == "corroborated"
        assert "attribution" not in _ast_edge("x", "y").to_dict()


class TestUntrackedFillAndForeignEdges:
    def test_an_untracked_attribute_only_the_other_producer_observed_is_filled(self) -> None:
        incumbent = _incumbent_method()
        other = _scip_method()
        other.cyclomatic_complexity = 7
        symbols = [incumbent, other]
        merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        assert symbols[0].cyclomatic_complexity == 7
        assert "cyclomatic_complexity" not in (symbols[0].attribution or {})  # untracked: no slot entry

    def test_an_edge_from_a_non_participant_pass_is_not_folded(self) -> None:
        caller_inc = _sym("main", "function", 1, 5, run=RUN_PY, origin="python")
        caller_scip = _sym("main", "function", 1, 1, run=RUN_SCIP, origin="scip")
        symbols = [caller_inc, _incumbent_method(), caller_scip, _scip_method()]
        linker_edge = Edge.create(src=caller_inc.id, dst=symbols[1].id, edge_type="calls", line=9,
                                  origin="containment-linker", evidence_type="ast_call",
                                  confidence=0.9, origin_run_id=RUN_LINKER)
        ast = _ast_edge(caller_inc.id, symbols[1].id)
        edges = [linker_edge, ast]
        report = merge_producer_records(symbols, edges, [dict(r) for r in RUNS])
        assert report.edges_folded == 0
        assert linker_edge in edges and linker_edge.attribution is None


class TestAnObservationIsDeclaredWhenTheValueCannotSayIt:
    """ADR-0057 §1 / INV-huboz: a producer contributes a candidate for an
    attribute only when it OBSERVED that attribute.

    Nine of the ten tracked attributes say that by value — their ``Symbol``
    default is ``None``, ``""`` or ``[]``, which :func:`_candidates` skips,
    and that is PER RECORD, which is better information than any
    declaration. ``is_exported`` cannot: its default is a concrete
    ``False``, indistinguishable from a measured one, so a producer that
    assigns the field nowhere contributed a phantom ``False`` candidate on
    every record it emitted. For those attributes — and only those — the
    producer declares, on the §10 anchor it already carries.
    """

    def test_the_blind_attributes_are_derived_from_the_record_not_listed(self) -> None:
        """If this fails because a tracked attribute gained a concrete
        default, the registry contract test will already be demanding that
        every anchored producer rule on it. That is the intended sequence:
        the field cannot express abstention, so its producers must."""
        assert ABSTENTION_BLIND_ATTRIBUTES == ("is_exported",)

    def _pair(self, *, incumbent_exported: bool, other_exported: bool) -> list[Symbol]:
        incumbent, other = _incumbent_method(), _scip_method()
        incumbent.is_exported = incumbent_exported
        other.is_exported = other_exported
        return [incumbent, other]

    def test_an_undeclared_producers_default_is_not_a_candidate(self) -> None:
        """``pyscip`` declares ``observes=()``: its ``False`` is the
        dataclass default, not an observation, and must not contest the
        incumbent's measured ``True`` nor corroborate its ``False``."""
        symbols = self._pair(incumbent_exported=True, other_exported=False)
        merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        [merged] = symbols
        assert merged.is_exported is True
        assert merged.attribution["is_exported"] == ["python"]
        assert "is_exported" not in (merged.alternatives or {})

    def test_nor_does_it_manufacture_an_agreement(self) -> None:
        """The subtler half: where the incumbent also holds ``False`` the
        phantom read as AGREEMENT — two producers holding one value — which
        is a stronger claim than the evidence supports."""
        symbols = self._pair(incumbent_exported=False, other_exported=False)
        merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        [merged] = symbols
        assert merged.attribution["is_exported"] == ["python"]

    def test_a_declared_producer_still_contests(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The declaration is not a mute button: a producer that says it
        computes the attribute is arbitrated as before."""
        anchor = _registry_mod._ANALYZER_REGISTRY["pyscip"].merge
        monkeypatch.setattr(
            _registry_mod._ANALYZER_REGISTRY["pyscip"], "merge",
            MergeAnchor(name_key=anchor.name_key, span_role=anchor.span_role,
                        observes=("is_exported",)),
        )
        symbols = self._pair(incumbent_exported=True, other_exported=False)
        merge_producer_records(symbols, [], [dict(r) for r in RUNS])
        [merged] = symbols
        assert merged.is_exported is True
        assert merged.attribution["is_exported"] == ["python"]
        assert merged.alternatives["is_exported"] == [{"value": False, "origin": ["pyscip"]}]

    def test_an_attribute_that_expresses_absence_by_value_is_refused(self) -> None:
        """Declaring one would be redundant at best and silently lossy at
        worst: the record already says, per record, whether it was seen."""
        with pytest.raises(MergeDeclarationError, match="signature"):
            MergeAnchor(name_key=as_emitted, span_role=SPAN_ROLE_TOKEN,
                        observes=("is_exported", "signature"))
