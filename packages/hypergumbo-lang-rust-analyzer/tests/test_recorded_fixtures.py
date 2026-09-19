# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0057 §12 / WI-romuh: the committed aardvark-dns recording is what it says.

A recorded fixture is a claim about a producer; these tests keep the claim
checkable. The index parses with the shipped ``scip_pb2``; its counts are
the ones the recorded module states; every document it names is a file in
the committed crate (so the recording and the source it was taken from
cannot drift apart silently); and — the number every later row builds on —
under the anchors both Rust arms declare, the live tree-sitter arm run on
the committed crate pairs exactly the SCIP definitions the module says it
does. The pairing here is computed inline by the declared rule, not by the
merge pass (WI-kokiz), which is what must later reproduce it.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from hypergumbo_core.analyze.registry import (
    MergeAnchor,
    ensure_discovered,
    get_analyzer,
)
from hypergumbo_core.analyze.merge_producers import merge_producer_records
from hypergumbo_core.ir import Symbol, deduplicate_edges
from hypergumbo_core.scip._generated import scip_pb2
from hypergumbo_core.scip.descriptor import is_local_symbol
from hypergumbo_lang_mainstream.rust import analyze_rust
from hypergumbo_lang_rust_analyzer.translate import translate_scip_to_hg

from recorded_rust_analyzer_1_94_0 import (
    AARDVARK_DNS_COUNTS,
    AARDVARK_DNS_AGREEMENT_TALLY,
    AARDVARK_DNS_CALL_SITE_TALLY,
    AARDVARK_DNS_PAIRING,
    AARDVARK_DNS_UPSTREAM_COMMIT,
    PRODUCER_VERSION,
    aardvark_dns_crate_root,
    aardvark_dns_index_bytes,
)


@pytest.fixture(scope="module")
def index() -> scip_pb2.Index:
    parsed = scip_pb2.Index()
    parsed.ParseFromString(aardvark_dns_index_bytes())
    return parsed


class TestTheRecordingIsWhatItSays:
    def test_the_producer_is_pinned(self, index: scip_pb2.Index) -> None:
        tool = index.metadata.tool_info
        assert f"{tool.name} {tool.version}" == PRODUCER_VERSION
        assert AARDVARK_DNS_UPSTREAM_COMMIT == "4444d90fee"

    def test_the_project_root_is_the_committed_crate_not_a_machine_path(
        self, index: scip_pb2.Index,
    ) -> None:
        assert index.metadata.project_root == "file:///recorded/aardvark_dns/crate"

    def test_the_counts_match(self, index: scip_pb2.Index) -> None:
        docs = list(index.documents)
        counts = {
            "documents": len(docs),
            "global_definitions": sum(
                1 for d in docs for o in d.occurrences
                if o.symbol_roles & scip_pb2.SymbolRole.Definition
                and not is_local_symbol(o.symbol)
            ),
            "local_definitions": sum(
                1 for d in docs for o in d.occurrences
                if o.symbol_roles & scip_pb2.SymbolRole.Definition
                and is_local_symbol(o.symbol)
            ),
            "reference_occurrences": sum(
                1 for d in docs for o in d.occurrences
                if not o.symbol_roles & scip_pb2.SymbolRole.Definition
            ),
        }
        assert counts == AARDVARK_DNS_COUNTS

    def test_every_indexed_document_is_a_committed_source_file(
        self, index: scip_pb2.Index,
    ) -> None:
        root = aardvark_dns_crate_root()
        assert (root / "LICENSE").is_file() and (root / "build.rs").is_file()
        missing = [d.relative_path for d in index.documents if not (root / d.relative_path).is_file()]
        assert missing == []
        # And the other way: every .rs file in the crate was indexed.
        on_disk = {p.relative_to(root).as_posix() for p in root.rglob("*.rs")}
        assert on_disk == {d.relative_path for d in index.documents}


def _anchors() -> tuple[MergeAnchor, MergeAnchor]:
    ensure_discovered()
    incumbent, scip = get_analyzer("rust"), get_analyzer("rust_analyzer")
    assert incumbent is not None and scip is not None
    assert isinstance(incumbent.merge, MergeAnchor)
    assert isinstance(scip.merge, MergeAnchor)
    return incumbent.merge, scip.merge


def _read_crate_file(path: str) -> bytes | None:
    candidate = aardvark_dns_crate_root() / path
    return candidate.read_bytes() if candidate.is_file() else None


class TestTheDeclaredAnchorsPairTheRecordedDefinitions:
    def test_pairing_under_the_declared_rule(self) -> None:
        """148 of 169, none ambiguous. The rule, spelled out once so the
        merge pass has a reference to reproduce: same path, equal declared
        name keys, SCIP token line inside the tree-sitter item span."""
        incumbent, scip_anchor = _anchors()
        scip_symbols, _edges = translate_scip_to_hg(aardvark_dns_index_bytes(), _read_crate_file)
        tree_sitter: list[Symbol] = list(analyze_rust(aardvark_dns_crate_root()).symbols)
        assert len(scip_symbols) == AARDVARK_DNS_PAIRING["scip_definitions"]

        by_key: dict[tuple[str, str], list[Symbol]] = {}
        for symbol in tree_sitter:
            by_key.setdefault((symbol.path, incumbent.name_key(symbol.name)), []).append(symbol)

        paired = ambiguous = 0
        unpaired: Counter[str] = Counter()
        unpaired_names: list[str] = []
        kinds_agree: Counter[bool] = Counter()
        disagreements: Counter[tuple[str, str]] = Counter()
        for symbol in scip_symbols:
            twins = [
                t for t in by_key.get((symbol.path, scip_anchor.name_key(symbol.name)), [])
                if t.span.start_line <= symbol.span.start_line <= t.span.end_line
            ]
            if len(twins) == 1:
                paired += 1
                same = symbol.kind == twins[0].kind
                kinds_agree[same] += 1
                if not same:
                    disagreements[(symbol.kind, twins[0].kind)] += 1
            elif twins:
                ambiguous += 1
            else:
                unpaired[symbol.kind] += 1
                if symbol.kind != "namespace":
                    unpaired_names.append(symbol.name)

        assert {
            "scip_definitions": len(scip_symbols),
            "paired": paired,
            "ambiguous": ambiguous,
            "unpaired_namespace": unpaired["namespace"],
            "unpaired_type_alias": unpaired["type_alias"],
            "unpaired_variable": unpaired["variable"],
        } == AARDVARK_DNS_PAIRING
        # The three non-namespace leftovers are constructs the tree-sitter arm
        # emits no Symbol for (two `type` aliases, one `static`) — an incumbent
        # gap (WI-bamar), not a disagreement between the anchors.
        assert sorted(unpaired_names) == ["AardvarkResult", "DNSBACKEND", "ThreadHandleMap"]
        # WI-gapup: the SCIP arm reads the producer's declared kind, so the
        # paired records agree on kind except for `const` items, where SCIP's
        # `constant` is the more precise claim and stays a contest.
        assert {
            "paired": paired,
            "agree": kinds_agree[True],
            "disagree": kinds_agree[False],
            "disagree_constant_vs_variable": disagreements[("constant", "variable")],
        } == AARDVARK_DNS_AGREEMENT_TALLY
        assert set(disagreements) == {("constant", "variable")}

    def test_the_fixture_is_not_the_incumbents_output_in_disguise(self) -> None:
        """The recorded arm must carry something the incumbent does not, or a
        pairing count proves nothing: every SCIP record has a raw moniker and
        a token span, which no tree-sitter record has."""
        scip_symbols, _edges = translate_scip_to_hg(aardvark_dns_index_bytes(), _read_crate_file)
        callables = [s for s in scip_symbols if s.kind == "method"]
        assert callables and all("scip_symbol" in s.meta for s in callables)
        assert all(s.span.start_line == s.span.end_line for s in callables)
        tree_sitter = analyze_rust(aardvark_dns_crate_root()).symbols
        assert not any("scip_symbol" in (s.meta or {}) for s in tree_sitter)


class TestTheCallEdgesAgreeWithTheIncumbentWhereBothSeeTheCall:
    def test_call_site_tally(self) -> None:
        """WI-zapuk: a SCIP reference whose target is a declared callable is
        ``calls``; on paired endpoints those are the same edges the tree-sitter
        arm emits, and now carry the same type. The 113 field-target edges
        keep ``references`` (the row's "field references keep a non-calls
        type"). Twins are found by pairing symbols through the declared
        anchors and then matching (src, dst)."""
        incumbent, scip_anchor = _anchors()
        scip_symbols, scip_edges = translate_scip_to_hg(aardvark_dns_index_bytes(), _read_crate_file)
        result = analyze_rust(aardvark_dns_crate_root())
        tree_sitter_symbols, tree_sitter_edges = list(result.symbols), list(result.edges)

        by_key: dict[tuple[str, str], list[Symbol]] = {}
        for symbol in tree_sitter_symbols:
            by_key.setdefault((symbol.path, incumbent.name_key(symbol.name)), []).append(symbol)
        twin_of: dict[str, str] = {}
        for symbol in scip_symbols:
            twins = [
                t for t in by_key.get((symbol.path, scip_anchor.name_key(symbol.name)), [])
                if t.span.start_line <= symbol.span.start_line <= t.span.end_line
            ]
            if len(twins) == 1:
                twin_of[symbol.id] = twins[0].id
        kind_of = {s.id: s.kind for s in scip_symbols}
        tree_sitter_type = {(e.src, e.dst): e.edge_type for e in tree_sitter_edges}

        by_type: Counter[str] = Counter(e.edge_type for e in scip_edges)
        twins_by_types: Counter[tuple[str, str]] = Counter()
        for edge in scip_edges:
            src, dst = twin_of.get(edge.src), twin_of.get(edge.dst)
            if src is None or dst is None:
                continue
            other = tree_sitter_type.get((src, dst))
            if other is not None:
                twins_by_types[(edge.edge_type, other)] += 1

        measured = {
            "scip_edges": len(scip_edges),
            "calls": by_type["calls"],
            "references": by_type["references"],
            "field_target_references": sum(
                1 for e in scip_edges if kind_of.get(e.dst) == "field" and e.edge_type == "references"
            ),
            "twins": sum(twins_by_types.values()),
            "twins_calls_calls": twins_by_types[("calls", "calls")],
            "twins_references_calls": twins_by_types[("references", "calls")],
        }
        # (the post-fold key is pinned by the merge-pass test below)
        assert measured == {k: AARDVARK_DNS_CALL_SITE_TALLY[k] for k in measured}
        # Every callable-target edge is a call, and only those are.
        callable_kinds = {"function", "method", "constructor"}
        assert all(
            (e.edge_type == "calls") == (kind_of.get(e.dst) in callable_kinds) for e in scip_edges
        )
        assert set(twins_by_types) == {("calls", "calls"), ("references", "calls")}


class TestTheMergePassOnBothArms:
    """WI-kokiz's acceptance, on committed input: the pass folds exactly the
    148 declarations the anchors pair, leaves the 21 SCIP-only records and
    no tree-sitter-only record, stamps both producers on every merged
    record, and rewires the edges so the 121 shared call sites collapse."""

    def _both_arms(self) -> tuple[list[Symbol], list, list[dict[str, str]]]:
        result = analyze_rust(aardvark_dns_crate_root())
        assert result.run is not None
        tree_sitter = [s for s in result.symbols if s.path.startswith("src/") or s.path == "build.rs"]
        for symbol in tree_sitter:
            symbol.origin_run_id = symbol.origin_run_id or result.run.execution_id
        scip_symbols, scip_edges = translate_scip_to_hg(
            aardvark_dns_index_bytes(), _read_crate_file, run_id="scip-run",
        )
        runs = [
            {"execution_id": result.run.execution_id, "pass": "rust"},
            {"execution_id": "scip-run", "pass": "rust_analyzer"},
        ]
        return tree_sitter + scip_symbols, list(result.edges) + scip_edges, runs

    def test_the_pass_folds_the_paired_declarations(self) -> None:
        symbols, edges, runs = self._both_arms()
        before = len(symbols)
        report = merge_producer_records(symbols, edges, runs)
        assert len(report.merged) == AARDVARK_DNS_PAIRING["paired"]
        assert report.ambiguous == []
        assert before - len(symbols) == AARDVARK_DNS_PAIRING["paired"]
        by_origin = Counter(tuple(s.origin) for s in symbols)
        assert by_origin[("rust", "scip")] == AARDVARK_DNS_PAIRING["paired"]
        assert by_origin[("scip",)] == (
            AARDVARK_DNS_PAIRING["scip_definitions"] - AARDVARK_DNS_PAIRING["paired"]
        )
        assert by_origin[("rust",)] == 0  # no tree-sitter-only leftover
        assert runs[-1]["pass"] == "producer-merge"

    def test_merged_records_carry_the_item_span_and_the_moniker(self) -> None:
        symbols, edges, runs = self._both_arms()
        merge_producer_records(symbols, edges, runs)
        merged = [s for s in symbols if s.origin == ["rust", "scip"]]
        callables = [s for s in merged if s.kind in ("function", "method")]
        assert callables and any(s.span.end_line > s.span.start_line for s in callables)
        assert all("scip_symbol" in (s.meta or {}) for s in merged)
        assert all(s.origin_run_id == runs[-1]["execution_id"] for s in merged)

    def test_shared_call_sites_collapse_after_the_fold(self) -> None:
        symbols, edges, runs = self._both_arms()
        tree_sitter_edges = [e for e in edges if e.origin == ["rust"]]
        scip_edges = [e for e in edges if e.origin == ["scip"]]
        separately = len(deduplicate_edges(tree_sitter_edges)) + len(deduplicate_edges(scip_edges))
        report = merge_producer_records(symbols, edges, runs)
        # No edge still names a folded id (external targets are not symbols
        # yet — the orchestrator synthesises those stubs later — so the test
        # is "nothing dangles on a dropped id", not "every endpoint exists").
        folded = set(report.id_remap)
        assert not any(e.src in folded or e.dst in folded for e in edges)
        together = len(deduplicate_edges(edges))
        keys_of = lambda origin: {  # noqa: E731 - local shorthand
            (e.src, e.dst, e.edge_type) for e in edges if e.origin == [origin]
        }
        shared = keys_of("rust") & keys_of("scip")
        assert len(shared) == AARDVARK_DNS_CALL_SITE_TALLY["shared_call_keys_after_fold"]
        assert {k[2] for k in shared} == {"calls"}
        assert separately - together == len(shared)
        # The 121 SCIP call edges that have a twin are exactly the ones on those keys.
        assert sum(
            1 for e in edges if e.origin == ["scip"] and (e.src, e.dst, e.edge_type) in shared
        ) == AARDVARK_DNS_CALL_SITE_TALLY["twins_calls_calls"]
