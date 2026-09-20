# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0057 §12: the two Python arms on the recorded scip-python 0.6.6 output.

The syntax arm is the live ``python`` analyzer; the SCIP arm is the
committed recording of the real producer through this package's
translation. Nothing here derives the SCIP arm from the syntax arm's
records. What is pinned: the recording is what its module says, the
declared anchors pair every syntax-arm record, the merge pass and the
backend-agreement instrument reproduce the recorded tallies, and — the
number this backend exists for — every module-less method-call receiver
the syntax arm left behind is either ABSORBED by the typed twin at the
same site (§15) or superseded by a resolved one (§14). Seven stubs in,
one sentinel stub out.
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
    SAMPLE_PROJECT_ABSORBED,
    SAMPLE_PROJECT_ATTRIBUTE_AGREEMENT,
    SAMPLE_PROJECT_CORROBORATED,
    SAMPLE_PROJECT_COUNTS,
    SAMPLE_PROJECT_EDGE_OVERLAP,
    SAMPLE_PROJECT_EXTERNAL_FOLDS,
    SAMPLE_PROJECT_MODULE_LESS_STUBS,
    SAMPLE_PROJECT_PAIRING,
    SAMPLE_PROJECT_SUPERSEDED,
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
    assert (len(report.merged), len(report.ambiguous), report.corroborated,
            report.external_folds) == (
        SAMPLE_PROJECT_PAIRING["paired"], 0, SAMPLE_PROJECT_CORROBORATED,
        SAMPLE_PROJECT_EXTERNAL_FOLDS,
    )
    edges = deduplicate_edges(edges)
    ids = {s.id for s in symbols}
    for edge in edges:
        edge.is_resolved = edge.dst in ids
    assert demote_superseded_stubs(symbols, edges) == SAMPLE_PROJECT_SUPERSEDED
    artifact = {"analysis_runs": runs, "nodes": [s.to_dict() for s in symbols], "edges": [e.to_dict() for e in edges]}
    return artifact, symbols, edges


class TestWhatTheFoldActsOn:
    def test_the_syntax_arm_alone_emits_seven_module_less_stubs(self) -> None:
        """§15's input, pinned separately so "seven in, one out" has both ends.
        Every one of these says ``external`` in the module slot — the sentinel
        ADR-0051's axiom defines as *not a marker for the absence of an answer*
        — and carries no ``dst_ref``, which is how the abstention is signalled
        positively rather than inferred from the string (§15.1, WI-huzuv)."""
        _, syntax_edges, _ = _syntax_arm()
        stubs = [e for e in syntax_edges
                 if e.edge_type == "calls" and e.dst.startswith("python:external:")]
        assert sorted(e.dst.split(":")[-2] for e in stubs) == SAMPLE_PROJECT_MODULE_LESS_STUBS
        assert all(e.dst_ref is None for e in stubs)


class TestTheMergePassAndTheInstrumentOnBothArms:
    def test_the_instrument_reproduces_the_recorded_tallies(self) -> None:
        artifact, _, _ = _two_arm_artifact()
        [report] = measure_backend_agreement(artifact)
        assert report.producers == ["python", "scip_python"]
        assert {a.attribute: (a.agree, a.disagree, a.only["python"], a.only["scip_python"]) for a in report.attributes} == SAMPLE_PROJECT_ATTRIBUTE_AGREEMENT
        assert {(e.edge_type, e.resolved): (e.both, e.only["python"], e.only["scip_python"]) for e in report.edges} == SAMPLE_PROJECT_EDGE_OVERLAP
        assert report.pairing["field"] == {"paired": 0, "python": 0, "scip_python": 1}
        assert sum(row["paired"] for row in report.pairing.values()) == SAMPLE_PROJECT_PAIRING["paired"]

    def test_every_module_less_receiver_is_absorbed_or_superseded(self) -> None:
        """The number this backend exists for, on the recording. The syntax arm
        emits 7 module-less method-call stubs. Six have a SCIP twin at the same
        site stating the receiver's module, and §15 absorbs each into ONE edge
        carrying that module and both origins — before §15 the artifact held two
        edges for each of those calls. The seventh (`label`, a property) the
        SCIP arm resolves IN-REPO, so no twin states a module and §14's
        supersession is what applies instead."""
        _, symbols, edges = _two_arm_artifact()
        calls = [e for e in edges if e.edge_type == "calls"]
        absorbed = sorted(
            (e.dst_ref.name, e.dst_ref.module_path) for e in calls
            if e.dst_ref and e.dst_ref.module_path and (e.attribution or {}).get("dst_ref")
        )
        assert absorbed == SAMPLE_PROJECT_ABSORBED
        assert all(e.attribution["dst_ref"] == ["scip_python"] for e in calls
                   if (e.attribution or {}).get("dst_ref"))
        assert all(sorted(e.origin) == ["python", "scip"] for e in calls
                   if (e.attribution or {}).get("dst_ref")), "one call, both producers"
        # Exactly one sentinel stub is left, and it is the one no twin typed.
        stubs = [e for e in calls if e.dst.startswith("python:external:")]
        assert [e.dst.split(":")[-2] for e in stubs] == ["label"]
        [superseded] = [e for e in stubs if "superseded_by" in (e.meta or {})]
        assert superseded.meta["superseded_by_origin"] == ["scip"]


class TestTheArmStampsWhatItDeclares:
    def test_every_translated_record_carries_the_declared_origin(self) -> None:
        """INV-gabak: the declaration is checked against the emission.

        This arm registers as ``scip_python`` and declares
        ``emits_origin="scip"``, because the SCIP translation it delegates to
        is shared with the Rust arm and is a pass in its own right (ADR-0044,
        ``catalog._SYNTHETIC_PASS_IDS``). ADR-0057 §14's origin condition
        joins ``Edge.origin`` back to the registry through that declaration,
        so a declaration nothing checks would be a claim in a costume — the
        rule would silently stop recognising this producer.
        """
        ensure_discovered()
        analyzer = get_analyzer("scip_python")
        assert analyzer is not None and analyzer.emits_origin == "scip"
        symbols, edges = translate_scip_python_to_hg(
            sample_project_index_bytes(), run_id="scip-run",
        )
        assert symbols and edges  # an empty translation would pass vacuously
        emitted = {o for r in (*symbols, *edges)
                   for o in ([r.origin] if isinstance(r.origin, str) else r.origin)}
        assert emitted == {analyzer.emits_origin}
