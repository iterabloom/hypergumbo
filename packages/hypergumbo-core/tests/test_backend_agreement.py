# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0057 §5 / WI-dajif: the backend-agreement instrument.

The instrument reads an artifact's own provenance — ``attribution`` /
``alternatives`` on merged records, ``origin_run_id`` → ``analysis_runs``
on single-producer ones — and counts. These tests hand it a small
synthetic artifact for a language with two declared producers and pin
every table it produces, then check the markdown it renders carries the
machine-readable block a citation can be checked against.
"""
from __future__ import annotations

import json
from pathlib import Path
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
from hypergumbo_core.backend_agreement import (
    measure_backend_agreement,
    render_markdown,
    to_json,
)
from hypergumbo_core.cli import main


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


def _node(id_: str, kind: str, *, run: str, language: str = "python", **extra: Any) -> dict[str, Any]:
    return {"id": id_, "name": id_.split(":")[-2], "kind": kind, "language": language, "path": "a.py",
            "span": {"start_line": 1, "end_line": 3, "start_col": 0, "end_col": 0},
            "origin": ["python"], "origin_run_id": run, **extra}


def _edge(src: str, dst: str, *, run: str, resolved: bool = True, **extra: Any) -> dict[str, Any]:
    return {"id": f"edge:{src}->{dst}", "src": src, "dst": dst, "type": "calls", "line": 1,
            "confidence": 0.5, "confidence_source": "emitter_constant", "origin": ["python"],
            "origin_run_id": run, "is_resolved": resolved, "meta": {}, **extra}


def _artifact() -> dict[str, Any]:
    merged = {"attribution": {"kind": ["python"], "name": ["python", "pyscip"], "span": ["python"],
                              "stable_id": ["python"], "is_exported": ["python"]},
              "alternatives": {"kind": [{"value": "method", "origin": ["pyscip"]}],
                               "span": [{"value": {"start_line": 1, "end_line": 1, "start_col": 4, "end_col": 5}, "origin": ["pyscip"]}],
                               "stable_id": [{"value": "sha256:beef", "origin": ["pyscip"]}],
                               "is_exported": [{"value": False, "origin": ["pyscip"]}]},
              "stable_id": "sha256:cafe",
              # WI-pofih: `is_exported` is the one tracked attribute
              # `Symbol.to_dict()` does NOT put at the top level.
              "supply_chain": {"tier": 1, "is_exported": True}}
    agreed = {"attribution": {"kind": ["python", "pyscip"], "name": ["python", "pyscip"], "signature": ["python"]}}
    return {
        "analysis_runs": [
            {"execution_id": "r-py", "pass": "python"},
            {"execution_id": "r-scip", "pass": "pyscip"},
            {"execution_id": "r-merge", "pass": "producer-merge"},
            {"execution_id": "r-link", "pass": "containment-linker"},
        ],
        "nodes": [
            _node("python:a.py:1-3:f:function", "function", run="r-merge", **merged),
            _node("python:a.py:5-6:g:function", "function", run="r-merge", **agreed),
            _node("python:a.py:8-8:C:class", "class", run="r-py"),
            _node("python:a.py:9-9:x:variable", "variable", run="r-scip"),
            _node("python:a.py:10-10:ns:namespace", "namespace", run="r-scip"),
            _node("python:a.py:1-20:a.py:file", "file", run="r-link"),  # not a producer: ignored
            _node("rust:b.rs:1-1:only:function", "function", run="r-py", language="rust"),  # one producer: no report
        ],
        "edges": [
            _edge("python:a.py:1-3:f:function", "python:a.py:5-6:g:function", run="r-merge",
                  confidence_source="corroborated", attribution={"confidence": ["python", "pyscip"]}),
            _edge("python:a.py:5-6:g:function", "python:a.py:8-8:C:class", run="r-py"),
            _edge("python:a.py:1-3:f:function", "python:<external>:0-0:h:external_symbol", run="r-py",
                  resolved=False, meta={"superseded_by": "edge:x", "superseded_by_origin": ["scip"]}),
            _edge("python:a.py:1-3:f:function", "python:a.py:9-9:x:variable", run="r-scip"),
            # a linker's edge is not a producer's: never counted
            _edge("python:a.py:1-20:a.py:file", "python:a.py:8-8:C:class", run="r-link", type="contains"),
            # another language's edge is not this language's: never counted
            _edge("rust:b.rs:1-1:only:function", "rust:b.rs:1-1:only:function", run="r-py"),
        ],
    }


class TestMeasurement:
    def test_one_report_per_multi_producer_language(self) -> None:
        reports = measure_backend_agreement(_artifact())
        assert [r.language for r in reports] == ["python"]
        [report] = reports
        assert report.producers == ["python", "pyscip"]  # incumbent first

    def test_pairing_by_kind(self) -> None:
        [report] = measure_backend_agreement(_artifact())
        assert report.nodes == 5 and report.merged_nodes == 2
        assert report.pairing == {
            "function": {"paired": 2, "python": 0, "pyscip": 0},
            "class": {"paired": 0, "python": 1, "pyscip": 0},
            "variable": {"paired": 0, "python": 0, "pyscip": 1},
            "namespace": {"paired": 0, "python": 0, "pyscip": 1},
        }

    def test_per_attribute_agreement_with_shapes(self) -> None:
        [report] = measure_backend_agreement(_artifact())
        by_attr = {a.attribute: a for a in report.attributes}
        assert by_attr["kind"].agree == 1 and by_attr["kind"].disagree == 1
        assert by_attr["kind"].shapes == [("function", "method", 1)]
        assert by_attr["name"].agree == 2 and by_attr["name"].disagree == 0
        assert by_attr["span"].disagree == 1 and by_attr["span"].shapes == [("1:0-3:0", "1:4-1:5", 1)]
        assert by_attr["signature"].agree == 0 and by_attr["signature"].only == {"python": 1, "pyscip": 0}

    def test_a_nested_attributes_carried_value_is_read_where_it_serialises(self) -> None:
        """WI-pofih: ``Symbol.to_dict()`` nests ``is_exported`` under
        ``supply_chain`` — the only one of the ten TRACKED_ATTRIBUTES not a
        top-level key. Reading the carried value with ``node.get(attribute)``
        printed ``null`` for a value that EXISTS, into a committed table."""
        [report] = measure_backend_agreement(_artifact())
        by_attr = {a.attribute: a for a in report.attributes}
        assert by_attr["is_exported"].disagree == 1
        assert by_attr["is_exported"].shapes == [("true", "false", 1)]

    def test_an_attribute_the_node_does_not_serialise_is_not_shown_as_a_value(self) -> None:
        """Absent is not empty. An attribution naming an attribute the node
        carries nowhere is a defect in whatever produced the artifact; the
        table must say it could not find it, not print a plausible ``null``."""
        artifact = _artifact()
        artifact["nodes"][0]["attribution"]["invented"] = ["python"]
        artifact["nodes"][0]["alternatives"]["invented"] = [{"value": "x", "origin": ["pyscip"]}]
        [report] = measure_backend_agreement(artifact)
        by_attr = {a.attribute: a for a in report.attributes}
        assert by_attr["invented"].shapes == [("(not serialised)", "x", 1)]

    def test_an_opaque_attribute_is_counted_without_shapes(self) -> None:
        [report] = measure_backend_agreement(_artifact())
        by_attr = {a.attribute: a for a in report.attributes}
        assert by_attr["stable_id"].disagree == 1 and by_attr["stable_id"].shapes == []

    def test_edge_overlap_by_type_and_resolution(self) -> None:
        [report] = measure_backend_agreement(_artifact())
        rows = {(e.edge_type, e.resolved): e for e in report.edges}
        assert rows[("calls", True)].both == 1
        assert rows[("calls", True)].only == {"python": 1, "pyscip": 1}
        assert rows[("calls", False)].both == 0 and rows[("calls", False)].only == {"python": 1, "pyscip": 0}
        assert report.corroborated_edges == 1 and report.superseded_edges == 1
        assert {e.edge_type for e in report.edges} == {"calls"}  # the linker's `contains` edge is not a producer's

    def test_a_single_producer_artifact_measures_nothing(self) -> None:
        artifact = _artifact()
        artifact["nodes"] = [n for n in artifact["nodes"] if n["origin_run_id"] in ("r-py", "r-link")]
        artifact["edges"] = []
        assert measure_backend_agreement(artifact) == []


class TestRendering:
    def test_markdown_carries_the_machine_block_and_every_table(self) -> None:
        reports = measure_backend_agreement(_artifact())
        text = render_markdown(reports, artifact_label="synthetic.json", date="2026-09-19")
        assert "kind: backend_agreement" in text
        assert "| **Axis** | backend agreement (ADR-0057 §5, §10" in text
        assert "## python: `python` (incumbent) vs `pyscip`" in text
        assert "| `function` | 2 | 0 | 0 |" in text
        assert "| `kind` | 1 | 1 | 0 | 0 | `function` ← `method` x1 |" in text
        assert "| `signature` | 0 | 0 | 1 | 0 | — |" in text
        assert "| `stable_id` | 0 | 1 | 0 | 0 | (hash derived from the attributes above) |" in text
        assert "| `is_exported` | 0 | 1 | 0 | 0 | `true` ← `false` x1 |" in text
        assert "| `calls` | in-repo | 1 | 1 | 1 |" in text
        assert "Corroborated edges (two distinct pathways, ADR-0057 §13): 1. Superseded external stubs (§14): 1." in text

    def test_an_empty_measurement_says_so(self) -> None:
        text = render_markdown([], artifact_label="one-arm.json", date="2026-09-19")
        assert "No language in this artifact was analysed by two producers" in text

    def test_json_form(self) -> None:
        reports = measure_backend_agreement(_artifact())
        data = to_json(reports, artifact_label="synthetic.json")
        assert data["instrument"] == "backend-agreement"
        assert data["languages"][0]["pairing"]["function"]["paired"] == 2
        by_attr = {a["attribute"]: a for a in data["languages"][0]["attributes"]}
        assert by_attr["kind"]["disagree"] == 1
        assert by_attr["is_exported"]["shapes"] == [{"carried": "true", "alternative": "false", "count": 1}]


class TestTheSubcommand:
    """``hypergumbo backend-agreement ARTIFACT [--format md|json] [--out FILE]``."""

    def test_markdown_to_stdout_by_default(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        artifact = tmp_path / "survey.json"
        artifact.write_text(json.dumps(_artifact()))
        assert main(["backend-agreement", str(artifact)]) == 0
        out = capsys.readouterr().out
        assert "kind: backend_agreement" in out
        assert "| **Artifact** | `survey.json` |" in out

    def test_json_to_a_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        artifact = tmp_path / "survey.json"
        artifact.write_text(json.dumps(_artifact()))
        out_file = tmp_path / "agreement.json"
        assert main(["backend-agreement", str(artifact), "--format", "json", "--out", str(out_file)]) == 0
        data = json.loads(out_file.read_text())
        assert data["languages"][0]["merged_nodes"] == 2
        assert capsys.readouterr().out == ""

    def test_a_missing_or_unreadable_artifact_is_exit_2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["backend-agreement", str(tmp_path / "absent.json")]) == 2
        assert "absent.json" in capsys.readouterr().err
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert main(["backend-agreement", str(bad)]) == 2
        assert "bad.json" in capsys.readouterr().err
