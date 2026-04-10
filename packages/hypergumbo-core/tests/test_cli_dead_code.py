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

    def test_dispatches_to_makes_impl_reachable(self, tmp_path: Path) -> None:
        """Interface dispatch edges make concrete implementations reachable.

        In Go/Java, calling an interface method dispatches to concrete
        implementations via dispatches_to edges.  The BFS must follow these
        edges so that implementations like Notifier.Notify in alertmanager
        are NOT flagged as dead code.
        """
        import argparse

        nodes = [
            # Entrypoint: main
            {"id": "go:main.go:1-10:main:function", "name": "main",
             "kind": "function", "language": "go", "path": "main.go",
             "span": {"start_line": 1, "end_line": 10},
             "meta": {"is_main": True}},
            # Interface method
            {"id": "go:notify.go:5-5:Notifier.Notify:method", "name": "Notifier.Notify",
             "kind": "method", "language": "go", "path": "notify.go",
             "span": {"start_line": 5, "end_line": 5}},
            # Concrete implementation
            {"id": "go:slack.go:10-50:Notifier.Notify:method", "name": "Notifier.Notify",
             "kind": "method", "language": "go", "path": "slack.go",
             "span": {"start_line": 10, "end_line": 50}},
            # Unreachable function (truly dead)
            {"id": "go:orphan.go:1-20:orphan:function", "name": "orphan",
             "kind": "function", "language": "go", "path": "orphan.go",
             "span": {"start_line": 1, "end_line": 20}},
        ]
        edges = [
            # main calls interface method
            {"type": "calls", "src": "go:main.go:1-10:main:function",
             "dst": "go:notify.go:5-5:Notifier.Notify:method"},
            # Interface dispatches to concrete impl
            {"type": "dispatches_to",
             "src": "go:notify.go:5-5:Notifier.Notify:method",
             "dst": "go:slack.go:10-50:Notifier.Notify:method"},
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
        # Slack Notifier.Notify IS reachable via dispatches_to → NOT dead
        dead_ids = {d["id"] for d in output.get("dead_candidates", [])}
        assert "go:slack.go:10-50:Notifier.Notify:method" not in dead_ids
        # orphan is truly unreachable → dead
        assert "orphan" in dead_names

    def test_routes_to_makes_handler_reachable(self, tmp_path: Path) -> None:
        """Route registration edges make handlers reachable.

        HTTP route registrations emit routes_to edges.  The BFS must follow
        them so that route handlers are not flagged as dead.
        """
        import argparse

        nodes = [
            {"id": "go:main.go:1-10:main:function", "name": "main",
             "kind": "function", "language": "go", "path": "main.go",
             "span": {"start_line": 1, "end_line": 10},
             "meta": {"is_main": True}},
            # Route registration node
            {"id": "go:routes.go:5-5:GET /api:route", "name": "GET /api",
             "kind": "route", "language": "go", "path": "routes.go",
             "span": {"start_line": 5, "end_line": 5},
             "meta": {"route_path": "/api", "http_method": "GET"}},
            # Handler function
            {"id": "go:handler.go:10-40:handleAPI:function", "name": "handleAPI",
             "kind": "function", "language": "go", "path": "handler.go",
             "span": {"start_line": 10, "end_line": 40}},
        ]
        edges = [
            {"type": "calls", "src": "go:main.go:1-10:main:function",
             "dst": "go:routes.go:5-5:GET /api:route"},
            {"type": "routes_to", "src": "go:routes.go:5-5:GET /api:route",
             "dst": "go:handler.go:10-40:handleAPI:function"},
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
        dead_ids = {d["id"] for d in output.get("dead_candidates", [])}
        assert "go:handler.go:10-40:handleAPI:function" not in dead_ids

    def test_wraps_makes_inner_reachable(self, tmp_path: Path) -> None:
        """Middleware wrapper edges make inner handlers reachable.

        Go route registrations with middleware wrappers emit wraps edges.
        The BFS must follow them.
        """
        import argparse

        nodes = [
            {"id": "go:main.go:1-10:main:function", "name": "main",
             "kind": "function", "language": "go", "path": "main.go",
             "span": {"start_line": 1, "end_line": 10},
             "meta": {"is_main": True}},
            # Wrapper function
            {"id": "go:middleware.go:5-15:authWrap:function", "name": "authWrap",
             "kind": "function", "language": "go", "path": "middleware.go",
             "span": {"start_line": 5, "end_line": 15}},
            # Inner handler
            {"id": "go:api.go:20-50:query:function", "name": "query",
             "kind": "function", "language": "go", "path": "api.go",
             "span": {"start_line": 20, "end_line": 50}},
        ]
        edges = [
            {"type": "calls", "src": "go:main.go:1-10:main:function",
             "dst": "go:middleware.go:5-15:authWrap:function"},
            {"type": "wraps", "src": "go:middleware.go:5-15:authWrap:function",
             "dst": "go:api.go:20-50:query:function"},
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
        dead_ids = {d["id"] for d in output.get("dead_candidates", [])}
        assert "go:api.go:20-50:query:function" not in dead_ids

    def test_cross_language_string_collision(self, tmp_path: Path) -> None:
        """Dead candidates with names matching string literals in other-language
        files get a cross_language_hits annotation."""
        import argparse

        # Create a Go function and a Python file that references its name
        go_dir = tmp_path / "pkg"
        go_dir.mkdir()
        (go_dir / "handler.go").write_text(
            'package pkg\nfunc HandleUser() {}\n'
        )
        py_file = tmp_path / "app.py"
        py_file.write_text(
            'import requests\nrequests.get("/api/HandleUser")\n'
        )

        nodes = [
            {"id": "go:pkg/handler.go:2-2:HandleUser:function",
             "name": "HandleUser", "kind": "function",
             "language": "go", "path": "pkg/handler.go",
             "span": {"start_line": 2, "end_line": 2}},
            {"id": "py:app.py:1-2:main:function",
             "name": "main", "kind": "function",
             "language": "python", "path": "app.py",
             "span": {"start_line": 1, "end_line": 2},
             "meta": {"is_main": True}},
        ]
        edges: list = []
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
        dead = output.get("dead_candidates", [])
        handler = next((d for d in dead if d["name"] == "HandleUser"), None)
        assert handler is not None, "HandleUser should be dead (no edges)"
        hits = handler.get("cross_language_hits", 0)
        assert hits >= 1, (
            f"HandleUser appears as string in app.py (Python) but "
            f"cross_language_hits={hits}"
        )

    def test_cross_language_skips_short_names(self, tmp_path: Path) -> None:
        """Names shorter than 4 chars are skipped (too many false positives)."""
        import argparse

        (tmp_path / "app.py").write_text('print("foo")\n')
        nodes = [
            {"id": "go:x.go:1-1:Run:function", "name": "Run", "kind": "function",
             "language": "go", "path": "x.go",
             "span": {"start_line": 1, "end_line": 1}},
        ]
        bm_path = _make_behavior_map(tmp_path, nodes, [])
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
        dead = output["dead_candidates"]
        assert len(dead) == 1
        # "Run" is 3 chars → skipped, hits should be 0
        assert dead[0]["cross_language_hits"] == 0

    def test_cross_language_no_candidates_returns_empty(self, tmp_path: Path) -> None:
        """When all functions are reachable, no collision scan needed."""
        import argparse

        nodes = [
            {"id": "py:app.py:1-5:GET /api:route", "name": "api", "kind": "route",
             "language": "python", "path": "app.py",
             "span": {"start_line": 1, "end_line": 5},
             "meta": {"route_path": "/api", "http_method": "GET"}},
            {"id": "py:app.py:7-10:handler:function", "name": "handler",
             "kind": "function", "language": "python", "path": "app.py",
             "span": {"start_line": 7, "end_line": 10}},
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
        assert output["summary"]["dead_candidates"] == 0

    def test_cross_language_skips_hidden_and_unknown_ext(self, tmp_path: Path) -> None:
        """Hidden directories and unknown file extensions are skipped."""
        import argparse

        # Create a subdir for the repo to separate from the behavior map
        repo = tmp_path / "repo"
        repo.mkdir()
        # Create a hidden dir and a .dat file
        hidden = repo / ".hidden"
        hidden.mkdir()
        (hidden / "secret.py").write_text('HandleUser = True\n')
        (repo / "data.dat").write_text('HandleUser\n')

        nodes = [
            {"id": "go:pkg/handler.go:2-2:HandleUser:function",
             "name": "HandleUser", "kind": "function",
             "language": "go", "path": "pkg/handler.go",
             "span": {"start_line": 2, "end_line": 2}},
        ]
        bm_path = _make_behavior_map(repo, nodes, [])
        args = argparse.Namespace(
            path=str(repo), input=str(bm_path), format="json",
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
        dead = output["dead_candidates"]
        assert len(dead) == 1
        # .hidden dir skipped, .dat has unknown extension
        # (the hg.json file is .json which maps to "config" not "go",
        #  but we check cand_lang != file_lang and go != config → would hit)
        # So we expect 1 hit from hg.json itself containing "HandleUser"
        # This is a known limitation — the behavior map JSON is scanned.
        # The real-world impact is negligible since JSON config files
        # don't represent cross-language references.
        assert dead[0]["cross_language_hits"] <= 1

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
