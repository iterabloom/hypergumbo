# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-vogij: the ``repeat-finder`` read-view (structural-clone / refactoring leads).

Groups nodes by ``(language, shape_id)`` — a cluster of >=2 nodes is a set of
structural clones (same control-flow/nesting skeleton, differing only in names
and literals; spec §337/§342). This activates shape_id's one non-redundant
capability over ``fingerprint`` (ADR-0035 §1): clustering structural clones as
candidate copy-paste / extract-helper refactoring leads. Within-language only
(ADR-0014; the language key enforces it).

Default filtering keeps signal dense: trivial clusters (representative
cyclomatic_complexity below ``--min-complexity``, default 2) are dropped, and
only production clones (>=2 production members) are the headline — test-only
clone clusters (parametrized tests are structurally identical by design) are a
labeled disclosure bucket shown with ``--include-tests``. Clusters rank by
duplication burden (member count * representative LOC).
"""

import argparse
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import cmd_repeat_finder


def _node(
    *,
    name: str,
    path: str,
    shape_id: str,
    kind: str = "function",
    language: str = "python",
    cc: int | None = 3,
    line_span: int = 10,
    start: int = 1,
    protocol_origin: str | None = None,
):
    n = {
        "id": f"{language}:{path}:{start}-{start + line_span - 1}:{name}:{kind}",
        "name": name,
        "kind": kind,
        "language": language,
        "path": path,
        "span": {"start_line": start, "end_line": start + line_span - 1},
        "shape_id": shape_id,
        "line_span": line_span,
    }
    if cc is not None:
        n["cyclomatic_complexity"] = cc
    if protocol_origin is not None:
        n["protocol_origin"] = protocol_origin
    return n


def _write_map(tmp_path: Path, nodes: list) -> Path:
    bm = {"schema_version": "0.2.3", "view": "behavior_map", "nodes": nodes, "edges": []}
    p = tmp_path / "survey.json"
    p.write_text(json.dumps(bm))
    return p


def _args(tmp_path: Path, bm: Path, **over):
    base = {
        "path": str(tmp_path), "input": str(bm), "format": "json",
        "min_complexity": 2, "include_tests": False, "limit": 20,
    }
    base.update(over)
    return argparse.Namespace(**base)


def _run_json(tmp_path: Path, nodes: list, capsys, **over) -> dict:
    bm = _write_map(tmp_path, nodes)
    rc = cmd_repeat_finder(_args(tmp_path, bm, **over))
    assert rc == 0
    out = capsys.readouterr().out
    return json.loads(out)


class TestRepeatFinderClustering:
    def test_production_clone_cluster_reported(self, tmp_path, capsys):
        nodes = [
            _node(name="serialize", path="src/a.py", shape_id="sha256:aaaa"),
            _node(name="to_json", path="src/b.py", shape_id="sha256:aaaa"),
        ]
        data = _run_json(tmp_path, nodes, capsys)
        assert data["summary"]["production_clusters"] == 1
        assert len(data["clusters"]) == 1
        c = data["clusters"][0]
        assert c["shape_id"] == "sha256:aaaa"
        assert c["member_count"] == 2
        assert {m["name"] for m in c["members"]} == {"serialize", "to_json"}
        assert c["is_test_only"] is False

    def test_singletons_not_reported(self, tmp_path, capsys):
        nodes = [
            _node(name="only", path="src/a.py", shape_id="sha256:unique1"),
            _node(name="lone", path="src/b.py", shape_id="sha256:unique2"),
        ]
        data = _run_json(tmp_path, nodes, capsys)
        assert data["summary"]["production_clusters"] == 0
        assert data["clusters"] == []

    def test_different_shape_ids_dont_cluster(self, tmp_path, capsys):
        nodes = [
            _node(name="a", path="src/a.py", shape_id="sha256:xx"),
            _node(name="b", path="src/b.py", shape_id="sha256:yy"),
        ]
        data = _run_json(tmp_path, nodes, capsys)
        assert data["summary"]["production_clusters"] == 0

    def test_same_shape_different_language_dont_cluster(self, tmp_path, capsys):
        nodes = [
            _node(name="a", path="src/a.py", shape_id="sha256:z", language="python"),
            _node(name="b", path="src/b.rb", shape_id="sha256:z", language="ruby"),
        ]
        data = _run_json(tmp_path, nodes, capsys)
        # same shape_id but different language keys → two singletons → no cluster
        assert data["summary"]["production_clusters"] == 0

    def test_protocol_origin_synthetic_excluded(self, tmp_path, capsys):
        nodes = [
            _node(name="real", path="src/a.py", shape_id="sha256:s"),
            _node(name="stub", path="src/b.py", shape_id="sha256:s",
                  protocol_origin="kafka"),
        ]
        data = _run_json(tmp_path, nodes, capsys)
        # synthetic stand-in excluded → only 1 real member → no cluster
        assert data["summary"]["production_clusters"] == 0

    def test_non_clone_kinds_ignored(self, tmp_path, capsys):
        nodes = [
            _node(name="a", path="src/a.py", shape_id="sha256:aaaa"),
            _node(name="b", path="src/b.py", shape_id="sha256:aaaa"),
            # a variable and a file node share the shape_id but are not clone-relevant
            _node(name="CONST", path="src/c.py", shape_id="sha256:aaaa", kind="variable"),
            _node(name="file", path="src/d.py", shape_id="sha256:aaaa", kind="file"),
        ]
        data = _run_json(tmp_path, nodes, capsys)
        assert data["summary"]["production_clusters"] == 1
        assert data["clusters"][0]["member_count"] == 2

    def test_missing_shape_id_ignored(self, tmp_path, capsys):
        nodes = [
            _node(name="a", path="src/a.py", shape_id="sha256:aaaa"),
            _node(name="b", path="src/b.py", shape_id="sha256:aaaa"),
            # no shape_id → not clusterable (e.g. body-less declaration)
            {"id": "python:src/e.py:1-3:noshape:function", "name": "noshape",
             "kind": "function", "language": "python", "path": "src/e.py",
             "span": {"start_line": 1, "end_line": 3}, "shape_id": None,
             "cyclomatic_complexity": 3, "line_span": 3},
        ]
        data = _run_json(tmp_path, nodes, capsys)
        assert data["summary"]["production_clusters"] == 1

    def test_missing_line_span_defaults(self, tmp_path, capsys):
        # nodes lacking line_span entirely → representative size falls back to 1
        nodes = [
            {"id": "python:src/a.py:1-1:a:function", "name": "a", "kind": "function",
             "language": "python", "path": "src/a.py",
             "span": {"start_line": 1, "end_line": 1}, "shape_id": "sha256:nols",
             "cyclomatic_complexity": 3},
            {"id": "python:src/b.py:1-1:b:function", "name": "b", "kind": "function",
             "language": "python", "path": "src/b.py",
             "span": {"start_line": 1, "end_line": 1}, "shape_id": "sha256:nols",
             "cyclomatic_complexity": 3},
        ]
        data = _run_json(tmp_path, nodes, capsys)
        assert data["clusters"][0]["line_span"] == 1
        assert data["clusters"][0]["duplication_burden"] == 2


class TestRepeatFinderFiltering:
    def test_trivial_cluster_filtered_by_default(self, tmp_path, capsys):
        nodes = [
            _node(name="stub_a", path="src/a.py", shape_id="sha256:triv", cc=1),
            _node(name="stub_b", path="src/b.py", shape_id="sha256:triv", cc=1),
        ]
        data = _run_json(tmp_path, nodes, capsys)
        assert data["summary"]["production_clusters"] == 0

    def test_trivial_cluster_included_with_min_complexity_1(self, tmp_path, capsys):
        nodes = [
            _node(name="stub_a", path="src/a.py", shape_id="sha256:triv", cc=1),
            _node(name="stub_b", path="src/b.py", shape_id="sha256:triv", cc=1),
        ]
        data = _run_json(tmp_path, nodes, capsys, min_complexity=1)
        assert data["summary"]["production_clusters"] == 1

    def test_cc_absent_cluster_not_filtered(self, tmp_path, capsys):
        # non-Python nodes may lack cyclomatic_complexity — cannot judge triviality,
        # so keep the cluster (rely on ranking) rather than silently drop it.
        nodes = [
            _node(name="a", path="src/a.go", shape_id="sha256:nocc", language="go", cc=None),
            _node(name="b", path="src/b.go", shape_id="sha256:nocc", language="go", cc=None),
        ]
        data = _run_json(tmp_path, nodes, capsys)
        assert data["summary"]["production_clusters"] == 1
        assert data["clusters"][0]["cyclomatic_complexity"] is None


class TestRepeatFinderTestBucket:
    def test_test_only_cluster_hidden_by_default(self, tmp_path, capsys):
        nodes = [
            _node(name="TestA.test_x", path="tests/test_a.py", shape_id="sha256:t", kind="method"),
            _node(name="TestB.test_y", path="tests/test_b.py", shape_id="sha256:t", kind="method"),
        ]
        data = _run_json(tmp_path, nodes, capsys)
        assert data["summary"]["production_clusters"] == 0
        assert data["summary"]["test_only_clusters"] == 1
        assert data["clusters"] == []

    def test_test_only_cluster_shown_with_flag(self, tmp_path, capsys):
        nodes = [
            _node(name="TestA.test_x", path="tests/test_a.py", shape_id="sha256:t", kind="method"),
            _node(name="TestB.test_y", path="tests/test_b.py", shape_id="sha256:t", kind="method"),
        ]
        data = _run_json(tmp_path, nodes, capsys, include_tests=True)
        assert data["summary"]["test_only_clusters"] == 1
        assert len(data["clusters"]) == 1
        assert data["clusters"][0]["is_test_only"] is True

    def test_mixed_cluster_with_two_production_members_is_production(self, tmp_path, capsys):
        nodes = [
            _node(name="a", path="src/a.py", shape_id="sha256:m"),
            _node(name="b", path="src/b.py", shape_id="sha256:m"),
            _node(name="TestC.test_c", path="tests/test_c.py", shape_id="sha256:m", kind="method"),
        ]
        data = _run_json(tmp_path, nodes, capsys)
        assert data["summary"]["production_clusters"] == 1
        c = data["clusters"][0]
        assert c["production_member_count"] == 2
        assert c["member_count"] == 3


class TestRepeatFinderRanking:
    def test_ranked_by_duplication_burden(self, tmp_path, capsys):
        nodes = [
            # cluster A: 2 members * 5 LOC = burden 10
            _node(name="a1", path="src/a1.py", shape_id="sha256:A", line_span=5),
            _node(name="a2", path="src/a2.py", shape_id="sha256:A", line_span=5),
            # cluster B: 3 members * 20 LOC = burden 60 (should rank first)
            _node(name="b1", path="src/b1.py", shape_id="sha256:B", line_span=20),
            _node(name="b2", path="src/b2.py", shape_id="sha256:B", line_span=20),
            _node(name="b3", path="src/b3.py", shape_id="sha256:B", line_span=20),
        ]
        data = _run_json(tmp_path, nodes, capsys)
        shapes = [c["shape_id"] for c in data["clusters"]]
        assert shapes == ["sha256:B", "sha256:A"]
        assert data["clusters"][0]["duplication_burden"] == 60
        assert data["clusters"][1]["duplication_burden"] == 10


class TestRepeatFinderEnvelopeAndText:
    def test_json_envelope_shape(self, tmp_path, capsys):
        nodes = [
            _node(name="a", path="src/a.py", shape_id="sha256:e"),
            _node(name="b", path="src/b.py", shape_id="sha256:e"),
        ]
        data = _run_json(tmp_path, nodes, capsys)
        assert data["view"] == "repeat_finder"
        assert "schema_version" in data
        assert data["summary"]["languages"] == ["python"]
        assert data["summary"]["min_complexity"] == 2

    def test_text_output_no_clones(self, tmp_path, capsys):
        bm = _write_map(tmp_path, [_node(name="lone", path="src/a.py", shape_id="sha256:x")])
        rc = cmd_repeat_finder(_args(tmp_path, bm, format="text"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "No structural-clone clusters" in out

    def test_text_output_lists_cluster(self, tmp_path, capsys):
        nodes = [
            _node(name="serialize", path="src/a.py", shape_id="sha256:aaaa"),
            _node(name="to_json", path="src/b.py", shape_id="sha256:aaaa"),
        ]
        bm = _write_map(tmp_path, nodes)
        rc = cmd_repeat_finder(_args(tmp_path, bm, format="text"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "serialize" in out and "to_json" in out
        assert "extract-helper" in out
        assert "src/a.py" in out

    def test_text_hidden_test_bucket_note(self, tmp_path, capsys):
        nodes = [
            _node(name="TestA.t", path="tests/test_a.py", shape_id="sha256:t", kind="method"),
            _node(name="TestB.t", path="tests/test_b.py", shape_id="sha256:t", kind="method"),
        ]
        bm = _write_map(tmp_path, nodes)
        rc = cmd_repeat_finder(_args(tmp_path, bm, format="text"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "test-only clone cluster" in out
        assert "--include-tests" in out

    def test_text_shows_tests_with_flag(self, tmp_path, capsys):
        nodes = [
            _node(name="TestA.t", path="tests/test_a.py", shape_id="sha256:t", kind="method"),
            _node(name="TestB.t", path="tests/test_b.py", shape_id="sha256:t", kind="method"),
        ]
        bm = _write_map(tmp_path, nodes)
        rc = cmd_repeat_finder(_args(tmp_path, bm, format="text", include_tests=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "Test-only clone clusters" in out

    def test_text_production_and_hidden_tests(self, tmp_path, capsys):
        nodes = [
            _node(name="a", path="src/a.py", shape_id="sha256:p"),
            _node(name="b", path="src/b.py", shape_id="sha256:p"),
            _node(name="TestA.t", path="tests/test_a.py", shape_id="sha256:t", kind="method"),
            _node(name="TestB.t", path="tests/test_b.py", shape_id="sha256:t", kind="method"),
        ]
        bm = _write_map(tmp_path, nodes)
        rc = cmd_repeat_finder(_args(tmp_path, bm, format="text"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "production clone cluster" in out
        assert "test-only clone cluster(s) hidden" in out

    def test_text_production_and_shown_tests(self, tmp_path, capsys):
        nodes = [
            _node(name="a", path="src/a.py", shape_id="sha256:p"),
            _node(name="b", path="src/b.py", shape_id="sha256:p"),
            _node(name="TestA.t", path="tests/test_a.py", shape_id="sha256:t", kind="method"),
            _node(name="TestB.t", path="tests/test_b.py", shape_id="sha256:t", kind="method"),
        ]
        bm = _write_map(tmp_path, nodes)
        rc = cmd_repeat_finder(_args(tmp_path, bm, format="text", include_tests=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "production clone cluster" in out
        assert "Test-only clone clusters (1)" in out

    def test_text_limit_truncates(self, tmp_path, capsys):
        nodes = []
        for i in range(4):
            nodes.append(_node(name=f"a{i}", path=f"src/a{i}.py", shape_id=f"sha256:c{i}"))
            nodes.append(_node(name=f"b{i}", path=f"src/b{i}.py", shape_id=f"sha256:c{i}"))
        bm = _write_map(tmp_path, nodes)
        rc = cmd_repeat_finder(_args(tmp_path, bm, format="text", limit=2))
        assert rc == 0
        out = capsys.readouterr().out
        assert "and 2 more" in out

    def test_bad_input_returns_1(self, tmp_path, capsys):
        args = argparse.Namespace(
            path=str(tmp_path), input=str(tmp_path / "missing.json"),
            format="json", min_complexity=2, include_tests=False, limit=20,
        )
        rc = cmd_repeat_finder(args)
        assert rc == 1
