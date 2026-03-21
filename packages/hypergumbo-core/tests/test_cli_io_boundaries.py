# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the hypergumbo io-boundaries command (ADR-0016).

Covers cmd_io_boundaries CLI command: detecting I/O boundary calls from
a behavior map and displaying the boundary map.
"""
import json
from pathlib import Path

from hypergumbo_core.cli import cmd_io_boundaries
from hypergumbo_core.schema import SCHEMA_VERSION


class FakeArgs:
    """Minimal namespace for testing command functions."""

    pass


def _make_behavior_map(nodes, edges):
    """Create a minimal behavior map dict."""
    return {
        "schema_version": SCHEMA_VERSION,
        "nodes": nodes,
        "edges": edges,
    }


def test_cmd_io_boundaries_detects_fs_read(tmp_path: Path, capsys) -> None:
    """Detects fs_read boundary calls in a behavior map."""
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
        ],
        edges=[
            {
                "src": "python:src/main.py:1-5:main:function",
                "dst": "python:stdlib/os.py:100-102:os.listdir:function",
                "type": "calls",
                "confidence": 0.9,
            },
        ],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.json_output = False

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
                "id": "python:src/app.py:1:app:function",
                "name": "app",
                "kind": "function",
                "language": "python",
                "path": "src/app.py",
                "span": {"start_line": 1, "end_line": 5},
            },
        ],
        edges=[
            {
                "src": "python:src/app.py:1:app:function",
                "dst": "python:stdlib/sub.py:1:subprocess.run:function",
                "type": "calls",
                "confidence": 0.9,
            },
        ],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.json_output = True

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
                "id": "python:src/math.py:1:calc:function",
                "name": "calc",
                "kind": "function",
                "language": "python",
                "path": "src/math.py",
                "span": {"start_line": 1, "end_line": 5},
            },
        ],
        edges=[
            {
                "src": "python:src/math.py:1:calc:function",
                "dst": "python:stdlib/math.py:1:math.sqrt:function",
                "type": "calls",
                "confidence": 0.9,
            },
        ],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.json_output = False

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

    rc = cmd_io_boundaries(args)
    assert rc == 1


def test_cmd_io_boundaries_multiple_boundaries(tmp_path: Path, capsys) -> None:
    """Detects multiple boundary types in one behavior map."""
    bmap = _make_behavior_map(
        nodes=[
            {
                "id": "python:src/app.py:1:app:function",
                "name": "app",
                "kind": "function",
                "language": "python",
                "path": "src/app.py",
                "span": {"start_line": 1, "end_line": 5},
            },
        ],
        edges=[
            {
                "src": "python:src/app.py:1:app:function",
                "dst": "python:stdlib/os.py:1:os.listdir:function",
                "type": "calls",
                "confidence": 0.9,
            },
            {
                "src": "python:src/app.py:5:app:function",
                "dst": "python:stdlib/sub.py:1:subprocess.run:function",
                "type": "calls",
                "confidence": 0.9,
            },
            {
                "src": "python:src/app.py:10:app:function",
                "dst": "python:stdlib/socket.py:1:socket.socket.send:method",
                "type": "calls",
                "confidence": 0.9,
            },
        ],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.json_output = True

    rc = cmd_io_boundaries(args)
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total_io_edges"] == 3
    assert set(data["boundaries"].keys()) == {"fs_read", "net_send", "subprocess"}
