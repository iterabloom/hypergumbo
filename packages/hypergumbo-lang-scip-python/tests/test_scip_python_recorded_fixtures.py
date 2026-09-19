# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0057 §12: the two Python arms on the recorded scip-python 0.6.6 output.

The syntax arm is the live ``python`` analyzer; the SCIP arm is the
committed recording of the real producer through this package's
translation. Nothing here derives the SCIP arm from the syntax arm's
records. What is pinned: the recording is what its module says, the
declared anchors pair every syntax-arm record, the merge pass and the
backend-agreement instrument reproduce the recorded tallies, and — the
number this backend exists for — every module-less method-call receiver
the syntax arm left behind gains a typed twin or a resolved superseder.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from hypergumbo_core.analyze.merge_producers import merge_producer_records
from hypergumbo_core.analyze.registry import (
    MergeAnchor,
    ensure_discovered,
    get_analyzer,
)
from hypergumbo_core.backend_agreement import measure_backend_agreement
from hypergumbo_core.finalize import demote_superseded_stubs
from hypergumbo_core.ir import Symbol, deduplicate_edges
from hypergumbo_core.scip._generated import scip_pb2
from hypergumbo_core.scip.descriptor import is_local_symbol
from hypergumbo_lang_mainstream.py import analyze_python
from hypergumbo_lang_scip_python.translate import translate_scip_python_to_hg

from recorded_scip_python_0_6_6 import (
    PRODUCER_VERSION,
    SAMPLE_PROJECT_ATTRIBUTE_AGREEMENT,
    SAMPLE_PROJECT_CORROBORATED,
    SAMPLE_PROJECT_COUNTS,
    SAMPLE_PROJECT_EDGE_OVERLAP,
    SAMPLE_PROJECT_MODULE_LESS_STUBS,
    SAMPLE_PROJECT_PAIRING,
    SAMPLE_PROJECT_SUPERSEDED,
    SAMPLE_PROJECT_TYPED_BY_SCIP,
    SAMPLE_PROJECT_TYPED_EXTERNALS,
    sample_project_index_bytes,
    sample_project_root,
)


def _index() -> scip_pb2.Index:
    index = scip_pb2.Index()
    index.ParseFromString(sample_project_index_bytes())
    return index


class TestTheRecordingIsWhatItSays:
    def test_producer_documents_and_silences(self) -> None:
        index = _index()
        assert (index.metadata.tool_info.name, index.metadata.tool_info.version) == ("scip-python", PRODUCER_VERSION)
        assert index.metadata.project_root == "file:///sample_project"
        assert len(index.documents) == SAMPLE_PROJECT_COUNTS["documents"]
        assert all(d.language == "" for d in index.documents)  # the silence the importer fills
        infos = [si for d in index.documents for si in d.symbols if not is_local_symbol(si.symbol)]
        assert len(infos) == SAMPLE_PROJECT_COUNTS["global_symbols"]
        assert all(si.kind == 0 for si in infos)
        assert sum(1 for si in infos if si.symbol.endswith(")")) == SAMPLE_PROJECT_COUNTS["parameter_symbols"]
        assert sum(1 for si in infos if si.symbol.endswith("__init__:")) == SAMPLE_PROJECT_COUNTS["module_declarations"]

    def test_every_indexed_document_is_a_committed_file_and_vice_versa(self) -> None:
        root = sample_project_root()
        on_disk = {p.relative_to(root).as_posix() for p in root.rglob("*.py")}
        assert on_disk == {d.relative_path for d in _index().documents}

    def test_external_stdlib_occurrences_are_present(self) -> None:
        external = [o for d in _index().documents for o in d.occurrences
                    if not (o.symbol_roles & 1) and not is_local_symbol(o.symbol) and "python-stdlib" in o.symbol]
        assert len(external) == SAMPLE_PROJECT_COUNTS["external_stdlib_occurrences"]


def _anchors() -> tuple[MergeAnchor, MergeAnchor]:
    ensure_discovered()
    incumbent, scip = get_analyzer("python"), get_analyzer("scip_python")
    assert incumbent is not None and scip is not None
    assert isinstance(incumbent.merge, MergeAnchor) and isinstance(scip.merge, MergeAnchor)
    return incumbent.merge, scip.merge


def _syntax_arm() -> tuple[list[Symbol], list, str]:
    result = analyze_python(sample_project_root())
    assert result.run is not None
    root = sample_project_root()
    for symbol in result.symbols:
        if Path(symbol.path).is_absolute():
            symbol.path = Path(symbol.path).relative_to(root).as_posix()
        symbol.origin_run_id = symbol.origin_run_id or result.run.execution_id
    return list(result.symbols), list(result.edges), result.run.execution_id


class TestTheDeclaredAnchorsPairTheRecording:
    def test_pairing_under_the_declared_rule(self) -> None:
        incumbent, scip_anchor = _anchors()
        scip_symbols, _ = translate_scip_python_to_hg(sample_project_index_bytes(), run_id="scip-run")
        syntax, _, _ = _syntax_arm()
        assert len(scip_symbols) == SAMPLE_PROJECT_PAIRING["scip_symbols"]
        by_key: dict[tuple[str, str], list[Symbol]] = {}
        for symbol in syntax:
            by_key.setdefault((symbol.path, incumbent.name_key(symbol.name)), []).append(symbol)
        paired = ambiguous = 0
        unpaired: Counter[str] = Counter()
        kinds_agree: Counter[bool] = Counter()
        for symbol in scip_symbols:
            twins = [t for t in by_key.get((symbol.path, scip_anchor.name_key(symbol.name)), [])
                     if t.span.start_line <= symbol.span.start_line <= t.span.end_line]
            if len(twins) == 1:
                paired += 1
                kinds_agree[symbol.kind == twins[0].kind] += 1
            elif twins:
                ambiguous += 1
            else:
                unpaired[symbol.kind] += 1
        assert {"scip_symbols": len(scip_symbols), "paired": paired, "ambiguous": ambiguous,
                "unpaired_field": unpaired["field"]} == SAMPLE_PROJECT_PAIRING
        assert kinds_agree == Counter({True: 10})  # the Python arms agree on kind

    def test_the_recording_is_not_the_syntax_arms_output_in_disguise(self) -> None:
        scip_symbols, edges = translate_scip_python_to_hg(sample_project_index_bytes(), run_id="scip-run")
        assert all("scip_symbol" in (s.meta or {}) for s in scip_symbols)
        assert all(s.span.start_line == s.span.end_line for s in scip_symbols)  # identifier tokens
        typed = sorted((e.src.split(":")[-2], e.dst, (e.meta or {}).get("call_construct"), e.line) for e in edges if e.dst_ref)
        assert typed == SAMPLE_PROJECT_TYPED_EXTERNALS


def _two_arm_artifact() -> tuple[dict, list[Symbol], list]:
    syntax, syntax_edges, run_id = _syntax_arm()
    scip_symbols, scip_edges = translate_scip_python_to_hg(sample_project_index_bytes(), run_id="scip-run")
    symbols = syntax + scip_symbols
    edges = syntax_edges + scip_edges
    runs = [{"execution_id": run_id, "pass": "python"}, {"execution_id": "scip-run", "pass": "scip_python"}]
    report = merge_producer_records(symbols, edges, runs)
    assert (len(report.merged), len(report.ambiguous), report.corroborated) == (
        SAMPLE_PROJECT_PAIRING["paired"], 0, SAMPLE_PROJECT_CORROBORATED,
    )
    edges = deduplicate_edges(edges)
    ids = {s.id for s in symbols}
    for edge in edges:
        edge.is_resolved = edge.dst in ids
    assert demote_superseded_stubs(symbols, edges) == SAMPLE_PROJECT_SUPERSEDED
    artifact = {"analysis_runs": runs, "nodes": [s.to_dict() for s in symbols], "edges": [e.to_dict() for e in edges]}
    return artifact, symbols, edges


class TestTheMergePassAndTheInstrumentOnBothArms:
    def test_the_instrument_reproduces_the_recorded_tallies(self) -> None:
        artifact, _, _ = _two_arm_artifact()
        [report] = measure_backend_agreement(artifact)
        assert report.producers == ["python", "scip_python"]
        assert {a.attribute: (a.agree, a.disagree, a.only["python"], a.only["scip_python"]) for a in report.attributes} == SAMPLE_PROJECT_ATTRIBUTE_AGREEMENT
        assert {(e.edge_type, e.resolved): (e.both, e.only["python"], e.only["scip_python"]) for e in report.edges} == SAMPLE_PROJECT_EDGE_OVERLAP
        assert report.pairing["field"] == {"paired": 0, "python": 0, "scip_python": 1}
        assert sum(row["paired"] for row in report.pairing.values()) == SAMPLE_PROJECT_PAIRING["paired"]

    def test_every_module_less_receiver_gains_a_typed_twin_or_a_superseder(self) -> None:
        """The number this backend exists for, on the recording: of the syntax
        arm's 7 module-less method-call stubs, 6 have a SCIP twin at the same
        site carrying the receiver's module, and the seventh (`label`, a
        property) is superseded by the SCIP arm's RESOLVED call."""
        _, symbols, edges = _two_arm_artifact()
        stubs = [e for e in edges if e.edge_type == "calls" and e.dst.startswith("python:external:") and "scip" not in e.origin]
        assert sorted(e.dst.split(":")[-2] for e in stubs) == SAMPLE_PROJECT_MODULE_LESS_STUBS
        typed_sites = {(e.src, e.line, e.dst_ref.name) for e in edges if e.dst_ref and "scip" in e.origin}
        typed = sorted(e.dst.split(":")[-2] for e in stubs if (e.src, e.line, e.dst.split(":")[-2]) in typed_sites)
        assert typed == SAMPLE_PROJECT_TYPED_BY_SCIP
        [superseded] = [e for e in stubs if "superseded_by" in (e.meta or {})]
        assert superseded.dst.split(":")[-2] == "label"
        assert superseded.meta["superseded_by_origin"] == ["scip"]
