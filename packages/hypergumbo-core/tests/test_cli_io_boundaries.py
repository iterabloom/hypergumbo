# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the hypergumbo io-boundaries command (ADR-0016).

Covers cmd_io_boundaries CLI command: detecting I/O boundary calls from
a behavior map and displaying the boundary map.  Includes tests for
enriched text output (call sites, entry points, high-risk markers,
per-primitive counts), --by-file grouping, and --boundary/--primitive
filtering.
"""
import json
from pathlib import Path

from hypergumbo_core.cli import cmd_io_boundaries
from hypergumbo_core.schema import SCHEMA_VERSION


class FakeArgs:
    """Minimal namespace for testing command functions."""

    pass


def _make_behavior_map(nodes, edges, entrypoints=None):
    """Create a minimal behavior map dict."""
    bmap = {
        "schema_version": SCHEMA_VERSION,
        "nodes": nodes,
        "edges": edges,
    }
    if entrypoints is not None:
        bmap["entrypoints"] = entrypoints
    return bmap


def _make_args(tmp_path, bmap, **overrides):
    """Write behavior map to file and return FakeArgs with defaults."""
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.json_output = False
    args.by_file = False
    args.boundary = None
    args.primitive = None
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_SINGLE_FS_READ = {
    "nodes": [
        {
            "id": "python:src/main.py:1-5:main:function",
            "name": "main",
            "kind": "function",
            "language": "python",
            "path": "src/main.py",
            "span": {"start_line": 1, "end_line": 5},
        },
    ],
    "edges": [
        {
            "src": "python:src/main.py:1-5:main:function",
            "dst": "python:stdlib/os.py:100-102:os.listdir:function",
            "type": "calls",
            "confidence": 0.9,
        },
    ],
}

_MULTI_BOUNDARY = {
    "nodes": [
        {
            "id": "python:src/app.py:1-20:app:function",
            "name": "app",
            "kind": "function",
            "language": "python",
            "path": "src/app.py",
            "span": {"start_line": 1, "end_line": 20},
        },
        {
            "id": "python:src/deploy.py:1-10:deploy:function",
            "name": "deploy",
            "kind": "function",
            "language": "python",
            "path": "src/deploy.py",
            "span": {"start_line": 1, "end_line": 10},
        },
    ],
    "edges": [
        {
            "src": "python:src/app.py:1-20:app:function",
            "dst": "python:stdlib/os.py:1:os.listdir:function",
            "type": "calls",
        },
        {
            "src": "python:src/app.py:5-20:app:function",
            "dst": "python:stdlib/sub.py:1:subprocess.run:function",
            "type": "calls",
        },
        {
            "src": "python:src/deploy.py:1-10:deploy:function",
            "dst": "python:stdlib/sub.py:1:subprocess.Popen:function",
            "type": "calls",
        },
        {
            "src": "python:src/deploy.py:8-10:deploy:function",
            "dst": "python:stdlib/shutil.py:1:shutil.rmtree:function",
            "type": "calls",
        },
        {
            "src": "python:src/app.py:10-20:app:function",
            "dst": "python:stdlib/socket.py:1:socket.socket.send:method",
            "type": "calls",
        },
    ],
}


# ---------------------------------------------------------------------------
# Original tests (updated with explicit args attributes)
# ---------------------------------------------------------------------------


def test_cmd_io_boundaries_detects_fs_read(tmp_path: Path, capsys) -> None:
    """Detects fs_read boundary calls in a behavior map."""
    bmap = _make_behavior_map(**_SINGLE_FS_READ)
    args = _make_args(tmp_path, bmap)

    rc = cmd_io_boundaries(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "fs_read" in out
    assert "os.listdir" in out


def test_cmd_io_boundaries_json_output(tmp_path: Path, capsys) -> None:
    """JSON output mode produces valid JSON with boundary data."""
    bmap = _make_behavior_map(
        nodes=[
            {
                "id": "python:src/app.py:1-5:app:function",
                "name": "app",
                "kind": "function",
                "language": "python",
                "path": "src/app.py",
                "span": {"start_line": 1, "end_line": 5},
            },
        ],
        edges=[
            {
                "src": "python:src/app.py:1-5:app:function",
                "dst": "python:stdlib/sub.py:1-2:subprocess.run:function",
                "type": "calls",
                "confidence": 0.9,
            },
        ],
    )
    args = _make_args(tmp_path, bmap, json_output=True)

    rc = cmd_io_boundaries(args)
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["total_io_edges"] == 1
    assert "subprocess" in data["boundaries"]


def test_cmd_io_boundaries_no_io_calls(tmp_path: Path, capsys) -> None:
    """When no I/O calls found, reports accordingly."""
    bmap = _make_behavior_map(
        nodes=[
            {
                "id": "python:src/math.py:1-5:calc:function",
                "name": "calc",
                "kind": "function",
                "language": "python",
                "path": "src/math.py",
                "span": {"start_line": 1, "end_line": 5},
            },
        ],
        edges=[
            {
                "src": "python:src/math.py:1-5:calc:function",
                "dst": "python:stdlib/math.py:1-2:math.sqrt:function",
                "type": "calls",
            },
        ],
    )
    args = _make_args(tmp_path, bmap)

    rc = cmd_io_boundaries(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "No I/O boundary calls detected" in out


def test_cmd_io_boundaries_missing_input(tmp_path: Path) -> None:
    """Returns error when input file doesn't exist."""
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(tmp_path / "nonexistent.json")
    args.json_output = False
    args.by_file = False
    args.boundary = None
    args.primitive = None

    rc = cmd_io_boundaries(args)
    assert rc == 1


def test_cmd_io_boundaries_multiple_boundaries(tmp_path: Path, capsys) -> None:
    """Detects multiple boundary types in one behavior map."""
    bmap = _make_behavior_map(**_MULTI_BOUNDARY)
    args = _make_args(tmp_path, bmap, json_output=True)

    rc = cmd_io_boundaries(args)
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total_io_edges"] == 5
    assert "fs_read" in data["boundaries"]
    assert "subprocess" in data["boundaries"]
    assert "net_send" in data["boundaries"]


# ---------------------------------------------------------------------------
# New tests: caller info / call sites
# ---------------------------------------------------------------------------


def test_shows_caller_info(tmp_path: Path, capsys) -> None:
    """Text output includes the caller function name and file path."""
    bmap = _make_behavior_map(**_SINGLE_FS_READ)
    args = _make_args(tmp_path, bmap)

    cmd_io_boundaries(args)
    out = capsys.readouterr().out
    # Should show caller name + location
    assert "main" in out
    assert "<-" in out  # call-site indicator


def test_shows_caller_info_fallback(tmp_path: Path, capsys) -> None:
    """When node not in index, falls back to parsing symbol ID."""
    bmap = _make_behavior_map(
        nodes=[
            {
                "id": "python:src/mystery.py:1-5:mystery:function",
                "name": "mystery",
                "kind": "function",
                "language": "python",
                "path": "src/mystery.py",
                "span": {"start_line": 1, "end_line": 5},
            },
        ],
        edges=[
            {
                # src does NOT match any node id
                "src": "python:src/unknown.py:10-12:helper:function",
                "dst": "python:stdlib/os.py:100-102:os.listdir:function",
                "type": "calls",
            },
        ],
    )
    args = _make_args(tmp_path, bmap)

    cmd_io_boundaries(args)
    out = capsys.readouterr().out
    assert "helper" in out
    assert "<-" in out


# ---------------------------------------------------------------------------
# New tests: entry points displayed
# ---------------------------------------------------------------------------


def test_shows_entry_points(tmp_path: Path, capsys) -> None:
    """Text output includes entry point info when entrypoints are present."""
    bmap = _make_behavior_map(
        nodes=[
            {
                "id": "python:src/main.py:1-5:main:function",
                "name": "main",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5},
            },
            {
                "id": "python:src/helper.py:1-5:helper:function",
                "name": "helper",
                "kind": "function",
                "language": "python",
                "path": "src/helper.py",
                "span": {"start_line": 1, "end_line": 5},
            },
        ],
        edges=[
            {
                "src": "python:src/main.py:1-5:main:function",
                "dst": "python:src/helper.py:1-5:helper:function",
                "type": "calls",
            },
            {
                "src": "python:src/helper.py:1-5:helper:function",
                "dst": "python:stdlib/os.py:100-102:os.listdir:function",
                "type": "calls",
            },
        ],
        entrypoints=[
            {"symbol_id": "python:src/main.py:1-5:main:function"},
        ],
    )
    args = _make_args(tmp_path, bmap)

    cmd_io_boundaries(args)
    out = capsys.readouterr().out
    assert "reachable from" in out
    assert "main" in out
    assert "entry point" in out


# ---------------------------------------------------------------------------
# New tests: per-primitive counts
# ---------------------------------------------------------------------------


def test_primitive_counts(tmp_path: Path, capsys) -> None:
    """Text output shows per-primitive call counts."""
    bmap = _make_behavior_map(
        nodes=[
            {
                "id": "python:src/a.py:1-5:read_dir:function",
                "name": "read_dir",
                "kind": "function",
                "language": "python",
                "path": "src/a.py",
                "span": {"start_line": 1, "end_line": 5},
            },
            {
                "id": "python:src/b.py:1-5:scan:function",
                "name": "scan",
                "kind": "function",
                "language": "python",
                "path": "src/b.py",
                "span": {"start_line": 1, "end_line": 5},
            },
        ],
        edges=[
            {
                "src": "python:src/a.py:1-5:read_dir:function",
                "dst": "python:stdlib/os.py:1:os.listdir:function",
                "type": "calls",
            },
            {
                "src": "python:src/b.py:1-5:scan:function",
                "dst": "python:stdlib/os.py:1:os.listdir:function",
                "type": "calls",
            },
        ],
    )
    args = _make_args(tmp_path, bmap)

    cmd_io_boundaries(args)
    out = capsys.readouterr().out
    assert "os.listdir (2)" in out


# ---------------------------------------------------------------------------
# New tests: high-risk highlighting
# ---------------------------------------------------------------------------


def test_high_risk_marker_in_text(tmp_path: Path, capsys) -> None:
    """High-risk primitives are highlighted in text output."""
    bmap = _make_behavior_map(**_MULTI_BOUNDARY)
    args = _make_args(tmp_path, bmap)

    cmd_io_boundaries(args)
    out = capsys.readouterr().out
    assert "HIGH RISK" in out
    assert "subprocess.Popen" in out
    assert "shutil.rmtree" in out


def test_no_high_risk_marker_for_safe_primitives(tmp_path: Path, capsys) -> None:
    """Safe primitives do not get HIGH RISK markers."""
    bmap = _make_behavior_map(**_SINGLE_FS_READ)
    args = _make_args(tmp_path, bmap)

    cmd_io_boundaries(args)
    out = capsys.readouterr().out
    assert "HIGH RISK" not in out


# ---------------------------------------------------------------------------
# New tests: --boundary filter
# ---------------------------------------------------------------------------


def test_filter_boundary(tmp_path: Path, capsys) -> None:
    """--boundary filters to a specific boundary type."""
    bmap = _make_behavior_map(**_MULTI_BOUNDARY)
    args = _make_args(tmp_path, bmap, boundary="fs_read")

    cmd_io_boundaries(args)
    out = capsys.readouterr().out
    assert "fs_read" in out
    assert "subprocess" not in out
    assert "net_send" not in out


def test_filter_boundary_no_match(tmp_path: Path, capsys) -> None:
    """--boundary with non-matching type shows no results."""
    bmap = _make_behavior_map(**_SINGLE_FS_READ)
    args = _make_args(tmp_path, bmap, boundary="net_recv")

    cmd_io_boundaries(args)
    out = capsys.readouterr().out
    assert "No I/O boundary calls detected" in out


def test_filter_boundary_json(tmp_path: Path, capsys) -> None:
    """--boundary filter works with --json output."""
    bmap = _make_behavior_map(**_MULTI_BOUNDARY)
    args = _make_args(tmp_path, bmap, json_output=True, boundary="subprocess")

    cmd_io_boundaries(args)
    data = json.loads(capsys.readouterr().out)
    assert set(data["boundaries"].keys()) == {"subprocess"}
    # Filtered total reflects only subprocess chains
    assert data["total_io_edges"] == 2


# ---------------------------------------------------------------------------
# New tests: --primitive filter
# ---------------------------------------------------------------------------


def test_filter_primitive(tmp_path: Path, capsys) -> None:
    """--primitive filters to a specific primitive."""
    bmap = _make_behavior_map(**_MULTI_BOUNDARY)
    args = _make_args(tmp_path, bmap, primitive="subprocess.run")

    cmd_io_boundaries(args)
    out = capsys.readouterr().out
    assert "subprocess.run" in out
    assert "subprocess.Popen" not in out
    assert "os.listdir" not in out


def test_filter_primitive_no_match(tmp_path: Path, capsys) -> None:
    """--primitive with non-matching name shows no results."""
    bmap = _make_behavior_map(**_SINGLE_FS_READ)
    args = _make_args(tmp_path, bmap, primitive="nonexistent.func")

    cmd_io_boundaries(args)
    out = capsys.readouterr().out
    assert "No I/O boundary calls detected" in out


def test_filter_primitive_json(tmp_path: Path, capsys) -> None:
    """--primitive filter works with --json output."""
    bmap = _make_behavior_map(**_MULTI_BOUNDARY)
    args = _make_args(tmp_path, bmap, json_output=True, primitive="shutil.rmtree")

    cmd_io_boundaries(args)
    data = json.loads(capsys.readouterr().out)
    assert data["total_io_edges"] == 1
    # shutil.rmtree is fs_write
    assert "fs_write" in data["boundaries"]
    assert data["boundaries"]["fs_write"]["primitive_counts"] == {"shutil.rmtree": 1}


# ---------------------------------------------------------------------------
# New tests: --by-file view
# ---------------------------------------------------------------------------


def test_by_file_view(tmp_path: Path, capsys) -> None:
    """--by-file groups output by source file."""
    bmap = _make_behavior_map(**_MULTI_BOUNDARY)
    args = _make_args(tmp_path, bmap, by_file=True)

    cmd_io_boundaries(args)
    out = capsys.readouterr().out
    assert "by File" in out
    assert "src/app.py" in out
    assert "src/deploy.py" in out
    # Should show boundary type labels in brackets
    assert "[fs_read]" in out or "[subprocess]" in out


def test_by_file_with_filter(tmp_path: Path, capsys) -> None:
    """--by-file and --boundary work together."""
    bmap = _make_behavior_map(**_MULTI_BOUNDARY)
    args = _make_args(tmp_path, bmap, by_file=True, boundary="subprocess")

    cmd_io_boundaries(args)
    out = capsys.readouterr().out
    assert "by File" in out
    assert "[subprocess]" in out
    assert "[fs_read]" not in out


def test_by_file_no_results(tmp_path: Path, capsys) -> None:
    """--by-file with filter yielding no results shows empty message."""
    bmap = _make_behavior_map(**_SINGLE_FS_READ)
    args = _make_args(tmp_path, bmap, by_file=True, boundary="net_recv")

    cmd_io_boundaries(args)
    out = capsys.readouterr().out
    assert "No I/O boundary calls detected" in out


def test_by_file_high_risk(tmp_path: Path, capsys) -> None:
    """--by-file view shows HIGH RISK markers per file."""
    bmap = _make_behavior_map(**_MULTI_BOUNDARY)
    args = _make_args(tmp_path, bmap, by_file=True)

    cmd_io_boundaries(args)
    out = capsys.readouterr().out
    assert "HIGH RISK" in out


# ---------------------------------------------------------------------------
# New tests: enriched JSON output
# ---------------------------------------------------------------------------


def test_json_output_enriched(tmp_path: Path, capsys) -> None:
    """JSON output includes chains, primitive_counts, and has_high_risk."""
    bmap = _make_behavior_map(**_MULTI_BOUNDARY)
    args = _make_args(tmp_path, bmap, json_output=True)

    cmd_io_boundaries(args)
    data = json.loads(capsys.readouterr().out)

    sub_boundary = data["boundaries"]["subprocess"]
    assert "primitive_counts" in sub_boundary
    assert "chains" in sub_boundary
    assert "has_high_risk" in sub_boundary
    assert sub_boundary["has_high_risk"] is True
    assert sub_boundary["primitive_counts"]["subprocess.run"] == 1
    assert sub_boundary["primitive_counts"]["subprocess.Popen"] == 1

    fs_boundary = data["boundaries"]["fs_read"]
    assert fs_boundary["has_high_risk"] is False


def test_caller_format_node_no_line(tmp_path: Path, capsys) -> None:
    """Caller display works when node span has no start_line."""
    bmap = _make_behavior_map(
        nodes=[
            {
                "id": "python:src/mod.py:1-5:mod:module",
                "name": "mod",
                "kind": "module",
                "language": "python",
                "path": "src/mod.py",
                "span": {},  # no start_line
            },
        ],
        edges=[
            {
                "src": "python:src/mod.py:1-5:mod:module",
                "dst": "python:stdlib/os.py:1-2:os.listdir:function",
                "type": "calls",
            },
        ],
    )
    args = _make_args(tmp_path, bmap)
    cmd_io_boundaries(args)
    out = capsys.readouterr().out
    assert "mod" in out
    assert "src/mod.py" in out


def test_caller_format_node_no_path(tmp_path: Path, capsys) -> None:
    """Caller display works when node has no path."""
    bmap = _make_behavior_map(
        nodes=[
            {
                "id": "python:virtual:1-5:ghost:function",
                "name": "ghost",
                "kind": "function",
                "language": "python",
                "path": "",
                "span": {"start_line": 1},
            },
        ],
        edges=[
            {
                "src": "python:virtual:1-5:ghost:function",
                "dst": "python:stdlib/os.py:1-2:os.listdir:function",
                "type": "calls",
            },
        ],
    )
    args = _make_args(tmp_path, bmap)
    cmd_io_boundaries(args)
    out = capsys.readouterr().out
    assert "ghost" in out


def test_caller_fallback_no_path(tmp_path: Path, capsys) -> None:
    """Fallback caller format works when symbol ID has no file path."""
    bmap = _make_behavior_map(
        nodes=[
            {
                "id": "python:src/x.py:1-5:x:function",
                "name": "x",
                "kind": "function",
                "language": "python",
                "path": "src/x.py",
                "span": {"start_line": 1, "end_line": 5},
            },
        ],
        edges=[
            {
                # src is a short symbol ID (no file path) not matching any node
                "src": "py:short",
                "dst": "python:stdlib/os.py:1-2:os.listdir:function",
                "type": "calls",
            },
        ],
    )
    args = _make_args(tmp_path, bmap)
    cmd_io_boundaries(args)
    out = capsys.readouterr().out
    # Should fall through to returning the raw symbol_id
    assert "py:short" in out


def test_caller_fallback_no_file_path(tmp_path: Path, capsys) -> None:
    """Fallback: 5-part symbol ID where path extraction returns empty."""
    bmap = _make_behavior_map(
        nodes=[
            {
                "id": "python:src/x.py:1-5:x:function",
                "name": "x",
                "kind": "function",
                "language": "python",
                "path": "src/x.py",
                "span": {"start_line": 1, "end_line": 5},
            },
        ],
        edges=[
            {
                # 5 colon-separated parts, but no :\d+-\d+: pattern so path
                # extraction returns "" — exercises the `return name` fallback
                "src": "python:modname:nospan:helper:function",
                "dst": "python:stdlib/os.py:1-2:os.listdir:function",
                "type": "calls",
            },
        ],
    )
    args = _make_args(tmp_path, bmap)
    cmd_io_boundaries(args)
    out = capsys.readouterr().out
    assert "helper" in out


def test_relativize_no_repo_root(tmp_path: Path, capsys) -> None:
    """_relativize returns path unchanged when repo_root is None."""
    from hypergumbo_core.cli import _relativize
    assert _relativize("/some/path", None) == "/some/path"
    assert _relativize("", None) == ""


def test_by_file_entry_points(tmp_path: Path, capsys) -> None:
    """--by-file view shows entry point traces."""
    bmap = _make_behavior_map(
        nodes=[
            {
                "id": "python:src/main.py:1-5:main:function",
                "name": "main",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5},
            },
            {
                "id": "python:src/helper.py:1-5:helper:function",
                "name": "helper",
                "kind": "function",
                "language": "python",
                "path": "src/helper.py",
                "span": {"start_line": 1, "end_line": 5},
            },
        ],
        edges=[
            {
                "src": "python:src/main.py:1-5:main:function",
                "dst": "python:src/helper.py:1-5:helper:function",
                "type": "calls",
            },
            {
                "src": "python:src/helper.py:1-5:helper:function",
                "dst": "python:stdlib/os.py:100-102:os.listdir:function",
                "type": "calls",
            },
        ],
        entrypoints=[
            {"symbol_id": "python:src/main.py:1-5:main:function"},
        ],
    )
    args = _make_args(tmp_path, bmap, by_file=True)

    cmd_io_boundaries(args)
    out = capsys.readouterr().out
    assert "reachable from" in out
    assert "main" in out


def test_objc_io_boundaries_detected(tmp_path: Path, capsys) -> None:
    """ObjC I/O boundaries are detected despite 'objective-c' vs 'objc' mismatch.

    Nodes report language='objective-c' but symbol IDs use 'objc:' prefix.
    The catalog loading must bridge this mismatch so tag_io_boundaries can
    match ObjC I/O primitives.
    """
    bmap = _make_behavior_map(
        nodes=[
            {
                "id": "objc:src/Manager.m:1-5:Manager.cleanup:method",
                "name": "Manager.cleanup",
                "kind": "method",
                "language": "objective-c",
                "path": "src/Manager.m",
                "span": {"start_line": 1, "end_line": 5},
            },
        ],
        edges=[
            {
                "src": "objc:src/Manager.m:1-5:Manager.cleanup:method",
                "dst": "objc:external:0-0:removeItemAtPath:error::unresolved",
                "type": "calls",
                "confidence": 0.5,
            },
        ],
    )
    args = _make_args(tmp_path, bmap, json_output=True)

    rc = cmd_io_boundaries(args)
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total_io_edges"] >= 1, (
        f"Expected >=1 ObjC IO edges, got {data['total_io_edges']}. "
        f"Boundaries: {list(data.get('boundaries', {}).keys())}"
    )
    assert "fs_write" in data["boundaries"]


class TestIoBoundariesExcludeTests:
    """Tests for --exclude-tests flag on io-boundaries command."""

    def test_exclude_tests_filters_test_chains(self, tmp_path, capsys):
        """Chains from test files are excluded when --exclude-tests is set."""
        bmap = _make_behavior_map(
            nodes=[
                {
                    "id": "python:src/main.py:1-5:main:function",
                    "name": "main",
                    "kind": "function",
                    "language": "python",
                    "path": "src/main.py",
                    "span": {"start_line": 1, "end_line": 5},
                },
                {
                    "id": "python:tests/test_main.py:1-5:test_func:function",
                    "name": "test_func",
                    "kind": "function",
                    "language": "python",
                    "path": "tests/test_main.py",
                    "span": {"start_line": 1, "end_line": 5},
                },
                {
                    "id": "python:os:0-0:remove:unresolved",
                    "name": "remove",
                    "kind": "unresolved",
                    "language": "python",
                    "path": "",
                    "span": {"start_line": 0, "end_line": 0},
                },
            ],
            edges=[
                {
                    "src": "python:src/main.py:1-5:main:function",
                    "dst": "python:os:0-0:remove:unresolved",
                    "type": "calls",
                    "meta": {"callee": "os.remove"},
                },
                {
                    "src": "python:tests/test_main.py:1-5:test_func:function",
                    "dst": "python:os:0-0:remove:unresolved",
                    "type": "calls",
                    "meta": {"callee": "os.remove"},
                },
            ],
        )

        # Without --exclude-tests: both chains present
        args = _make_args(tmp_path, bmap, json_output=True, exclude_tests=False)
        rc = cmd_io_boundaries(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["total_io_edges"] == 2

        # With --exclude-tests: only prod chain
        args = _make_args(tmp_path, bmap, json_output=True, exclude_tests=True)
        rc = cmd_io_boundaries(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["total_io_edges"] == 1
        chains = data["boundaries"]["fs_write"]["chains"]
        assert len(chains) == 1
        assert "test" not in chains[0]["io_edge_src"]

    def test_exclude_tests_removes_empty_boundary_types(self, tmp_path, capsys):
        """Boundary types with only test chains are removed entirely."""
        bmap = _make_behavior_map(
            nodes=[
                {
                    "id": "python:tests/test_io.py:1-5:test_write:function",
                    "name": "test_write",
                    "kind": "function",
                    "language": "python",
                    "path": "tests/test_io.py",
                    "span": {"start_line": 1, "end_line": 5},
                },
                {
                    "id": "python:os:0-0:remove:unresolved",
                    "name": "remove",
                    "kind": "unresolved",
                    "language": "python",
                    "path": "",
                    "span": {"start_line": 0, "end_line": 0},
                },
            ],
            edges=[
                {
                    "src": "python:tests/test_io.py:1-5:test_write:function",
                    "dst": "python:os:0-0:remove:unresolved",
                    "type": "calls",
                    "meta": {"callee": "os.remove"},
                },
            ],
        )

        args = _make_args(tmp_path, bmap, json_output=True, exclude_tests=True)
        rc = cmd_io_boundaries(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["total_io_edges"] == 0
        assert len(data["boundaries"]) == 0
