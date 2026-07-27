# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `hypergumbo slice --io-boundary <category>` (WI-fakuv filter half).

The io-boundary classification is derived ephemerally at slice time (no
persisted field, per the io-boundary REFRAME / WI-puvun): ``_apply_io_boundary_filter``
runs ``compute_boundary_map`` over the slice's edges in memory, then keeps only
the edges reaching the requested category plus their endpoints and the entry.
"""
import json
from pathlib import Path

from hypergumbo_core.cli import (
    _apply_io_boundary_filter,
    build_parser,
    cmd_slice,
)
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.schema import SCHEMA_VERSION
from hypergumbo_core.slice import SliceQuery, slice_graph

# A python stdlib filesystem-read primitive id, mirroring the known-good
# fixture in test_cli_io_boundaries (os.listdir → fs_read).
_OS_LISTDIR_ID = "python:stdlib/os.py:100-102:os.listdir:function"


def _py_symbol(name: str, path: str, start: int = 1) -> Symbol:
    return Symbol(
        id=f"python:{path}:{start}-{start + 4}:{name}:function",
        name=name,
        kind="function",
        language="python",
        path=path,
        span=Span(start_line=start, end_line=start + 4, start_col=0, end_col=0),
    )


def _calls_edge(src_id: str, dst_id: str) -> Edge:
    return Edge.create(
        src=src_id,
        dst=dst_id,
        edge_type="calls",
        line=1,
        origin="python",
        origin_run_id="uuid:test",
        confidence=0.9,
    )


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
class TestSliceIoBoundaryParser:
    def test_slice_accepts_io_boundary(self):
        args = build_parser().parse_args(
            ["slice", ".", "--entry", "main", "--io-boundary", "fs_read"]
        )
        assert args.io_boundary == "fs_read"

    def test_slice_io_boundary_default_none(self):
        args = build_parser().parse_args(["slice", ".", "--entry", "main"])
        assert args.io_boundary is None


# ---------------------------------------------------------------------------
# Filter helper
# ---------------------------------------------------------------------------
class TestApplyIoBoundaryFilter:
    def _graph(self):
        main = _py_symbol("main", "src/app.py")
        helper = _py_symbol("helper", "src/util.py", start=20)
        os_listdir = Symbol(
            id=_OS_LISTDIR_ID,
            name="os.listdir",
            kind="function",
            language="python",
            path="stdlib/os.py",
            span=Span(start_line=100, end_line=102, start_col=0, end_col=0),
        )
        # A synthetic node with language=None (ADR-0031 class-B stand-in) and a
        # node in an unsupported language exercise the catalog-loop branches.
        none_lang = Symbol(
            id="none:syn:0-0:syn:synthetic",
            name="syn",
            kind="synthetic",
            language=None,
            path="syn",
            span=Span(start_line=0, end_line=0, start_col=0, end_col=0),
        )
        bad_lang = Symbol(
            id="klingon:k.k:1-1:kfn:function",
            name="kfn",
            kind="function",
            language="klingon",
            path="k.k",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
        )
        e_io = _calls_edge(main.id, os_listdir.id)
        e_plain = _calls_edge(main.id, helper.id)
        nodes = [main, helper, os_listdir, none_lang, bad_lang]
        edges = [e_io, e_plain]
        result = slice_graph(nodes, edges, SliceQuery(entrypoint="main", max_hops=5))
        return nodes, edges, result, main, helper, os_listdir, e_io, e_plain

    def test_keeps_only_matching_boundary_edges(self):
        nodes, edges, result, main, helper, os_listdir, e_io, e_plain = self._graph()
        # Sanity: both edges are in the pre-filter slice.
        assert {e_io.id, e_plain.id} <= set(result.edge_ids)

        kept = _apply_io_boundary_filter(result, nodes, edges, "fs_read")

        assert kept == 1
        assert result.edge_ids == {e_io.id}
        # Endpoints of the kept io edge + the entry survive; the non-io callee
        # (helper) is pruned.
        assert os_listdir.id in result.node_ids
        assert main.id in result.node_ids
        assert helper.id not in result.node_ids

    def test_non_matching_category_empties_to_entry(self):
        nodes, edges, result, main, helper, os_listdir, e_io, e_plain = self._graph()

        kept = _apply_io_boundary_filter(result, nodes, edges, "net_send")

        assert kept == 0
        assert result.edge_ids == set()
        # The entry anchor is retained even with no matching boundary.
        assert result.node_ids == {main.id}


# ---------------------------------------------------------------------------
# cmd_slice wiring
# ---------------------------------------------------------------------------
def _write_map(tmp_path: Path) -> Path:
    bmap = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/app.py:1-5:main:function",
                "name": "main",
                "kind": "function",
                "language": "python",
                "path": "src/app.py",
                "span": {"start_line": 1, "end_line": 5},
            },
            {
                "id": "python:src/util.py:1-5:helper:function",
                "name": "helper",
                "kind": "function",
                "language": "python",
                "path": "src/util.py",
                "span": {"start_line": 1, "end_line": 5},
            },
            {
                "id": _OS_LISTDIR_ID,
                "name": "os.listdir",
                "kind": "function",
                "language": "python",
                "path": "stdlib/os.py",
                "span": {"start_line": 100, "end_line": 102},
            },
        ],
        "edges": [
            {
                "id": "edge-main-oslistdir",
                "src": "python:src/app.py:1-5:main:function",
                "dst": _OS_LISTDIR_ID,
                "type": "calls",
                "confidence": 0.9,
            },
            {
                "id": "edge-main-helper",
                "src": "python:src/app.py:1-5:main:function",
                "dst": "python:src/util.py:1-5:helper:function",
                "type": "calls",
                "confidence": 0.9,
            },
        ],
    }
    map_file = tmp_path / "hg.json"
    map_file.write_text(json.dumps(bmap))
    return map_file


class TestCmdSliceIoBoundary:
    def test_valid_category_filters_slice(self, tmp_path, capsys):
        map_file = _write_map(tmp_path)
        out_file = tmp_path / "slice.json"
        args = build_parser().parse_args(
            [
                "slice", str(map_file),
                "--entry", "main",
                "--io-boundary", "fs_read",
                "--out", str(out_file),
            ]
        )
        rc = cmd_slice(args)

        assert rc == 0
        feature = json.loads(out_file.read_text())["feature"]
        # Only the fs_read edge (main -> os.listdir) survives; the os.listdir
        # endpoint is kept, the non-io callee (helper) is pruned.
        assert len(feature["edge_ids"]) == 1
        assert _OS_LISTDIR_ID in feature["node_ids"]
        assert "python:src/util.py:1-5:helper:function" not in feature["node_ids"]
        assert "--io-boundary fs_read" in capsys.readouterr().out

    def test_unknown_category_exits_2(self, tmp_path, capsys):
        map_file = _write_map(tmp_path)
        args = build_parser().parse_args(
            ["slice", str(map_file), "--entry", "main", "--io-boundary", "bogus_cat"]
        )
        rc = cmd_slice(args)

        assert rc == 2
        assert "unknown --io-boundary category 'bogus_cat'" in capsys.readouterr().err
