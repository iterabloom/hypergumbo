# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for dead-code-maybe subcommand."""

import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import cmd_dead_code_maybe


def _make_behavior_map(tmp_path: Path, nodes: list, edges: list) -> Path:
    """Write a behavior map JSON and return its path."""
    bm = {"schema_version": "0.2.2", "nodes": nodes, "edges": edges}
    p = tmp_path / "hg.json"
    p.write_text(json.dumps(bm))
    return p


class TestDeadCodeMaybe:
    """Tests for the dead-code-maybe subcommand."""

    def test_finds_unreachable_function(self, tmp_path: Path) -> None:
        """Functions not reachable from any entrypoint are flagged as dead."""
        import argparse

        nodes = [
            {"id": "py:app.py:1-5:GET /api:route", "name": "api", "kind": "route",
             "language": "python", "path": "app.py", "span": {"start_line": 1, "end_line": 5},
             "meta": {"route_path": "/api", "http_method": "GET"}},
            {"id": "py:app.py:7-10:helper:function", "name": "helper", "kind": "function",
             "language": "python", "path": "app.py", "span": {"start_line": 7, "end_line": 10}},
            {"id": "py:app.py:12-20:orphan:function", "name": "orphan", "kind": "function",
             "language": "python", "path": "app.py", "span": {"start_line": 12, "end_line": 20}},
        ]
        edges = [
            {"type": "calls", "src": "py:app.py:1-5:GET /api:route",
             "dst": "py:app.py:7-10:helper:function"},
        ]
        bm_path = _make_behavior_map(tmp_path, nodes, edges)

        args = argparse.Namespace(
            path=str(tmp_path), input=str(bm_path), format="json",
            seeds="entrypoints", min_confidence=0.0,
        )
        # Redirect stdout to capture output
        import io
        import sys
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            result = cmd_dead_code_maybe(args)
        finally:
            sys.stdout = old_stdout

        assert result == 0
        output = json.loads(captured.getvalue())
        dead_names = {d["name"] for d in output.get("dead_candidates", [])}
        # orphan is not reachable from main → dead
        assert "orphan" in dead_names
        # helper IS reachable from main → NOT dead
        assert "helper" not in dead_names

    def test_all_reachable_returns_empty(self, tmp_path: Path) -> None:
        """When all functions are reachable, dead list is empty."""
        import argparse

        nodes = [
            {"id": "py:app.py:1-5:GET /api:route", "name": "api", "kind": "route",
             "language": "python", "path": "app.py", "span": {"start_line": 1, "end_line": 5},
             "meta": {"route_path": "/api", "http_method": "GET"}},
            {"id": "py:app.py:7-10:helper:function", "name": "helper", "kind": "function",
             "language": "python", "path": "app.py", "span": {"start_line": 7, "end_line": 10}},
        ]
        edges = [
            {"type": "calls", "src": "py:app.py:1-5:GET /api:route",
             "dst": "py:app.py:7-10:helper:function"},
        ]
        bm_path = _make_behavior_map(tmp_path, nodes, edges)

        args = argparse.Namespace(
            path=str(tmp_path), input=str(bm_path), format="json",
            seeds="entrypoints", min_confidence=0.0,
        )
        import io
        import sys
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            result = cmd_dead_code_maybe(args)
        finally:
            sys.stdout = old_stdout

        assert result == 0
        output = json.loads(captured.getvalue())
        assert len(output.get("dead_candidates", [])) == 0

    def test_text_format_output(self, tmp_path: Path) -> None:
        """Text format includes summary and dead function list."""
        import argparse

        nodes = [
            {"id": "py:app.py:1-5:GET /api:route", "name": "api", "kind": "route",
             "language": "python", "path": "app.py", "span": {"start_line": 1, "end_line": 5},
             "meta": {"route_path": "/api", "http_method": "GET"}},
            {"id": "py:app.py:12-20:orphan:function", "name": "orphan", "kind": "function",
             "language": "python", "path": "app.py", "span": {"start_line": 12, "end_line": 20},
             "lines_of_code": 9},
        ]
        bm_path = _make_behavior_map(tmp_path, nodes, [])

        args = argparse.Namespace(
            path=str(tmp_path), input=str(bm_path), format="text",
            seeds="entrypoints", min_confidence=0.0,
        )
        import io
        import sys
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            result = cmd_dead_code_maybe(args)
        finally:
            sys.stdout = old_stdout

        text = captured.getvalue()
        assert "orphan" in text
        assert result == 0

    def test_excludes_test_files(self, tmp_path: Path) -> None:
        """Functions in test files are excluded from dead-code analysis."""
        import argparse

        nodes = [
            {"id": "py:app.py:1-5:GET /api:route", "name": "api", "kind": "route",
             "language": "python", "path": "app.py", "span": {"start_line": 1, "end_line": 5},
             "meta": {"route_path": "/api", "http_method": "GET"}},
            {"id": "py:app.py:7-10:handler:function", "name": "handler", "kind": "function",
             "language": "python", "path": "app.py", "span": {"start_line": 7, "end_line": 10}},
            {"id": "py:tests/test_app.py:1-5:test_main:function", "name": "test_main",
             "kind": "function", "language": "python", "path": "tests/test_app.py",
             "span": {"start_line": 1, "end_line": 5}},
        ]
        edges = [
            {"type": "calls", "src": "py:app.py:1-5:GET /api:route",
             "dst": "py:app.py:7-10:handler:function"},
        ]
        bm_path = _make_behavior_map(tmp_path, nodes, edges)

        args = argparse.Namespace(
            path=str(tmp_path), input=str(bm_path), format="json",
            seeds="entrypoints", min_confidence=0.0,
        )
        import io
        import sys
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            cmd_dead_code_maybe(args)
        finally:
            sys.stdout = old_stdout

        output = json.loads(captured.getvalue())
        dead_names = {d["name"] for d in output.get("dead_candidates", [])}
        # Test functions should not appear in dead list
        assert "test_main" not in dead_names

    def test_no_production_functions(self, tmp_path: Path) -> None:
        """Returns 0 with message when only test/non-callable nodes exist."""
        import argparse

        nodes = [
            {"id": "py:tests/test.py:1-5:test_fn:function", "name": "test_fn",
             "kind": "function", "language": "python", "path": "tests/test.py",
             "span": {"start_line": 1, "end_line": 5}},
            {"id": "py:app.py:1-5:MyClass:class", "name": "MyClass",
             "kind": "class", "language": "python", "path": "app.py",
             "span": {"start_line": 1, "end_line": 5}},
        ]
        bm_path = _make_behavior_map(tmp_path, nodes, [])

        args = argparse.Namespace(
            path=str(tmp_path), input=str(bm_path), format="json",
            seeds="entrypoints", min_confidence=0.0,
        )
        import io
        import sys
        captured_err = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured_err
        try:
            result = cmd_dead_code_maybe(args)
        finally:
            sys.stderr = old_stderr

        assert result == 0

    def test_input_file_not_found(self, tmp_path: Path) -> None:
        """Returns 1 when explicit input file doesn't exist."""
        import argparse

        args = argparse.Namespace(
            path=str(tmp_path), input="/nonexistent/path.json", format="json",
            seeds="entrypoints", min_confidence=0.0,
        )
        result = cmd_dead_code_maybe(args)
        assert result == 1

    def test_text_no_dead_code(self, tmp_path: Path) -> None:
        """Text format shows 'no dead functions' when everything is reachable."""
        import argparse

        nodes = [
            {"id": "py:app.py:1-5:GET /health:route", "name": "health", "kind": "route",
             "language": "python", "path": "app.py", "span": {"start_line": 1, "end_line": 5},
             "meta": {"route_path": "/health", "http_method": "GET"}},
            {"id": "py:app.py:7-10:handler:function", "name": "handler", "kind": "function",
             "language": "python", "path": "app.py", "span": {"start_line": 7, "end_line": 10}},
        ]
        edges = [
            {"type": "calls", "src": "py:app.py:1-5:GET /health:route",
             "dst": "py:app.py:7-10:handler:function"},
        ]
        bm_path = _make_behavior_map(tmp_path, nodes, edges)

        args = argparse.Namespace(
            path=str(tmp_path), input=str(bm_path), format="text",
            seeds="entrypoints", min_confidence=0.0,
        )
        import io
        import sys
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            cmd_dead_code_maybe(args)
        finally:
            sys.stdout = old_stdout

        assert "No potentially dead functions" in captured.getvalue()

    def test_seeds_all_includes_tests(self, tmp_path: Path) -> None:
        """--seeds all uses both entrypoints AND test functions as seeds."""
        import argparse

        nodes = [
            {"id": "py:app.py:1-5:GET /api:route", "name": "api", "kind": "route",
             "language": "python", "path": "app.py", "span": {"start_line": 1, "end_line": 5},
             "meta": {"route_path": "/api", "http_method": "GET"}},
            {"id": "py:app.py:12-20:tested_fn:function", "name": "tested_fn",
             "kind": "function", "language": "python", "path": "app.py",
             "span": {"start_line": 12, "end_line": 20}},
            {"id": "py:tests/test_app.py:1-5:test_fn:function", "name": "test_fn",
             "kind": "function", "language": "python", "path": "tests/test_app.py",
             "span": {"start_line": 1, "end_line": 5}},
        ]
        edges = [
            {"type": "calls", "src": "py:tests/test_app.py:1-5:test_fn:function",
             "dst": "py:app.py:12-20:tested_fn:function"},
        ]
        bm_path = _make_behavior_map(tmp_path, nodes, edges)

        args = argparse.Namespace(
            path=str(tmp_path), input=str(bm_path), format="json",
            seeds="all", min_confidence=0.0,
        )
        import io
        import sys
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            cmd_dead_code_maybe(args)
        finally:
            sys.stdout = old_stdout

        output = json.loads(captured.getvalue())
        dead_names = {d["name"] for d in output.get("dead_candidates", [])}
        # tested_fn IS reachable from test_fn with seeds=all → NOT dead
        assert "tested_fn" not in dead_names
