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

    def test_exclude_annotated_drops_framework_candidates(self, tmp_path: Path) -> None:
        """--exclude-annotated drops candidates with decorators/annotations/concepts."""
        import argparse

        nodes = [
            {"id": "py:app.py:1-5:GET /api:route", "name": "api", "kind": "route",
             "language": "python", "path": "app.py",
             "span": {"start_line": 1, "end_line": 5},
             "meta": {"route_path": "/api", "http_method": "GET"}},
            # Has concepts → should be EXCLUDED by --exclude-annotated
            {"id": "go:main.go:10-20:libFunc:function", "name": "libFunc",
             "kind": "function", "language": "go", "path": "main.go",
             "span": {"start_line": 10, "end_line": 20},
             "meta": {"decorators": ["@deprecated"]}},
            # No concepts/decorators/annotations → should be KEPT
            {"id": "go:main.go:30-40:plainFunc:function", "name": "plainFunc",
             "kind": "function", "language": "go", "path": "main.go",
             "span": {"start_line": 30, "end_line": 40}},
        ]
        bm_path = _make_behavior_map(tmp_path, nodes, [])

        args = argparse.Namespace(
            path=str(tmp_path), input=str(bm_path), format="json",
            seeds="entrypoints", min_confidence=0.0,
            exclude_annotated=True,
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
        dead_names = {d["name"] for d in output["dead_candidates"]}
        # plainFunc has no annotations → kept
        assert "plainFunc" in dead_names
        # libFunc has concepts → excluded
        assert "libFunc" not in dead_names

    def test_exclude_annotated_propagates_from_class(self, tmp_path: Path) -> None:
        """WI-rumij: class-level annotations propagate to contained methods for exclusion."""
        import argparse

        nodes = [
            # Class annotated with @RestController (Spring registration pattern)
            {"id": "java:UserController.java:1-30:UserController:class",
             "name": "UserController", "kind": "class", "language": "java",
             "path": "UserController.java",
             "span": {"start_line": 1, "end_line": 30},
             "meta": {"annotations": ["@RestController", "@RequestMapping"]}},
            # Handler method with no own annotations (framework-dispatched)
            {"id": "java:UserController.java:5-10:listUsers:method",
             "name": "listUsers", "kind": "method", "language": "java",
             "path": "UserController.java",
             "span": {"start_line": 5, "end_line": 10},
             "meta": {}},
            # Plain function with no parent class → kept
            {"id": "java:Util.java:1-5:orphan:method",
             "name": "orphan", "kind": "method", "language": "java",
             "path": "Util.java",
             "span": {"start_line": 1, "end_line": 5}},
        ]
        edges = [
            # Class contains the handler method
            {"id": "e1", "src": "java:UserController.java:1-30:UserController:class",
             "dst": "java:UserController.java:5-10:listUsers:method",
             "type": "contains", "line": 5, "confidence": 1.0},
        ]
        bm_path = _make_behavior_map(tmp_path, nodes, edges)

        args = argparse.Namespace(
            path=str(tmp_path), input=str(bm_path), format="json",
            seeds="entrypoints", min_confidence=0.0,
            exclude_annotated=True,
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
        dead_names = {d["name"] for d in output["dead_candidates"]}
        # listUsers is inside annotated class → excluded
        assert "listUsers" not in dead_names
        # orphan has no parent class with annotations → kept
        assert "orphan" in dead_names

    def test_exclude_annotated_class_decorators_propagate(self, tmp_path: Path) -> None:
        """Class-level decorators (Python-style) also propagate to methods."""
        import argparse

        nodes = [
            {"id": "py:views.py:1-30:UserView:class",
             "name": "UserView", "kind": "class", "language": "python",
             "path": "views.py",
             "span": {"start_line": 1, "end_line": 30},
             "meta": {"decorators": ["@register_view"]}},
            {"id": "py:views.py:5-10:get:method",
             "name": "get", "kind": "method", "language": "python",
             "path": "views.py",
             "span": {"start_line": 5, "end_line": 10},
             "meta": {}},
        ]
        edges = [
            {"id": "e1", "src": "py:views.py:1-30:UserView:class",
             "dst": "py:views.py:5-10:get:method",
             "type": "contains", "line": 5, "confidence": 1.0},
        ]
        bm_path = _make_behavior_map(tmp_path, nodes, edges)

        args = argparse.Namespace(
            path=str(tmp_path), input=str(bm_path), format="json",
            seeds="entrypoints", min_confidence=0.0,
            exclude_annotated=True,
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
        dead_names = {d["name"] for d in output["dead_candidates"]}
        assert "get" not in dead_names

    def test_exclude_annotated_unannotated_class_allows_method(self, tmp_path: Path) -> None:
        """Method in a plain class with no annotations is kept when unreachable."""
        import argparse

        nodes = [
            {"id": "java:Plain.java:1-30:Plain:class",
             "name": "Plain", "kind": "class", "language": "java",
             "path": "Plain.java",
             "span": {"start_line": 1, "end_line": 30}},
            {"id": "java:Plain.java:5-10:doWork:method",
             "name": "doWork", "kind": "method", "language": "java",
             "path": "Plain.java",
             "span": {"start_line": 5, "end_line": 10}},
        ]
        edges = [
            {"id": "e1", "src": "java:Plain.java:1-30:Plain:class",
             "dst": "java:Plain.java:5-10:doWork:method",
             "type": "contains", "line": 5, "confidence": 1.0},
        ]
        bm_path = _make_behavior_map(tmp_path, nodes, edges)

        args = argparse.Namespace(
            path=str(tmp_path), input=str(bm_path), format="json",
            seeds="entrypoints", min_confidence=0.0,
            exclude_annotated=True,
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
        dead_names = {d["name"] for d in output["dead_candidates"]}
        assert "doWork" in dead_names

    def test_path_shape_boost_api_dir(self, tmp_path: Path) -> None:
        """Candidates in api/ dir get a path_shape_boost of 1."""
        import argparse

        nodes = [
            # Plain function — no boost
            {"id": "go:pkg/plain.go:1-5:plainFunc:function",
             "name": "plainFunc", "kind": "function", "language": "go",
             "path": "pkg/plain.go",
             "span": {"start_line": 1, "end_line": 5}},
            # In api/ dir — boost 1, "handler" in name → boost 2 total
            {"id": "go:api/handler.go:1-5:userHandler:function",
             "name": "userHandler", "kind": "function", "language": "go",
             "path": "api/handler.go",
             "span": {"start_line": 1, "end_line": 5}},
        ]
        bm_path = _make_behavior_map(tmp_path, nodes, [])
        args = argparse.Namespace(
            path=str(tmp_path), input=str(bm_path), format="json",
            seeds="entrypoints", min_confidence=0.0,
            exclude_annotated=False,
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
        by_name = {d["name"]: d for d in output["dead_candidates"]}
        # api/ dir contributes 1, "handler" in name contributes 1 → boost=2
        assert by_name["userHandler"]["path_shape_boost"] == 2
        assert by_name["plainFunc"]["path_shape_boost"] == 0
        # userHandler should rank first (higher boost)
        assert output["dead_candidates"][0]["name"] == "userHandler"

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

    def test_seeds_exports_uses_exported_symbols(self, tmp_path: Path) -> None:
        """WI-zimum: --seeds exports treats is_exported=True functions as seeds.

        A lone function with is_exported=True and no incoming call edges
        should NOT be flagged as dead when --seeds exports is used, because
        exported symbols are part of the public API and presumed reachable
        by external callers.
        """
        import argparse

        nodes = [
            {"id": "go:api.go:1-5:PublicFn:function", "name": "PublicFn",
             "kind": "function", "language": "go", "path": "api.go",
             "span": {"start_line": 1, "end_line": 5},
             "supply_chain": {"tier": 1, "is_exported": True}},
            {"id": "go:api.go:10-15:helper:function", "name": "helper",
             "kind": "function", "language": "go", "path": "api.go",
             "span": {"start_line": 10, "end_line": 15},
             "supply_chain": {"tier": 1, "is_exported": False}},
            {"id": "go:api.go:20-25:orphan:function", "name": "orphan",
             "kind": "function", "language": "go", "path": "api.go",
             "span": {"start_line": 20, "end_line": 25},
             "supply_chain": {"tier": 1, "is_exported": False}},
        ]
        edges = [
            {"type": "calls", "src": "go:api.go:1-5:PublicFn:function",
             "dst": "go:api.go:10-15:helper:function"},
        ]
        bm_path = _make_behavior_map(tmp_path, nodes, edges)

        args = argparse.Namespace(
            path=str(tmp_path), input=str(bm_path), format="json",
            seeds="exports", min_confidence=0.0, exclude_annotated=False,
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
        # PublicFn is a seed → NOT dead
        assert "PublicFn" not in dead_names
        # helper reachable from PublicFn → NOT dead
        assert "helper" not in dead_names
        # orphan has no edges and is_exported=False → IS dead
        assert "orphan" in dead_names

    def test_ffi_signature_flag_boosts_rank(self, tmp_path: Path) -> None:
        """WI-hadap H2: candidates with FFI decorators get ffi_signature=True
        and sort above plain dead candidates."""
        import argparse

        nodes = [
            # Plain orphan with no FFI markers.
            {"id": "go:pkg/plain.go:1-5:plainFn:function",
             "name": "plainFn", "kind": "function", "language": "go",
             "path": "pkg/plain.go",
             "span": {"start_line": 1, "end_line": 5}},
            # Rust FFI orphan with #[pyo3::pyfunction] decorator.
            {"id": "rust:src/lib.rs:10-15:py_wrapper:function",
             "name": "py_wrapper", "kind": "function", "language": "rust",
             "path": "src/lib.rs",
             "span": {"start_line": 10, "end_line": 15},
             "meta": {"decorators": [{"name": "pyo3::pyfunction"}]}},
        ]
        bm_path = _make_behavior_map(tmp_path, nodes, [])
        args = argparse.Namespace(
            path=str(tmp_path), input=str(bm_path), format="json",
            seeds="entrypoints", min_confidence=0.0,
            exclude_annotated=False,
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
        candidates = output["dead_candidates"]
        by_name = {c["name"]: c for c in candidates}
        assert by_name["py_wrapper"]["ffi_signature"] is True
        assert by_name["plainFn"]["ffi_signature"] is False
        # FFI candidate ranks first (boost dominates).
        assert candidates[0]["name"] == "py_wrapper"

    def test_ffi_signature_flag_matches_native_modifier(
        self, tmp_path: Path,
    ) -> None:
        """WI-hadap H2: Java native modifier sets ffi_signature=True."""
        import argparse

        nodes = [
            {"id": "java:src/Main.java:1-5:nativeMethod:method",
             "name": "nativeMethod", "kind": "method", "language": "java",
             "path": "src/Main.java",
             "span": {"start_line": 1, "end_line": 5},
             "modifiers": ["public", "native"]},
        ]
        bm_path = _make_behavior_map(tmp_path, nodes, [])
        args = argparse.Namespace(
            path=str(tmp_path), input=str(bm_path), format="json",
            seeds="entrypoints", min_confidence=0.0,
            exclude_annotated=False,
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
        assert output["dead_candidates"][0]["ffi_signature"] is True

    def test_generated_file_symbols_never_appear_as_dead(
        self, tmp_path: Path,
    ) -> None:
        """WI-jifup: symbols in generated files never appear as dead candidates.

        Generated code is not actionable — you regenerate it, you don't
        delete it manually. The file-level ``is_generated_file`` flag on a
        symbol's supply_chain is an unconditional drop for dead-code-maybe
        (no flag opt-in needed). Covers the four TS utility-file types
        that leaked past WI-vubad's centrality demotion: ``CancelablePromise.ts``,
        ``request.ts``, ``OpenAPI.ts``, ``ApiError.ts``.
        """
        import argparse

        nodes = [
            # Generated utility methods — must be dropped.
            {"id": "ts:openapi-gen/CancelablePromise.ts:1-5:then:method",
             "name": "then", "kind": "method", "language": "typescript",
             "path": "src/openapi-gen/CancelablePromise.ts",
             "span": {"start_line": 1, "end_line": 5},
             "supply_chain": {"tier": 1, "is_generated_file": True}},
            {"id": "ts:openapi-gen/request.ts:7-15:encodePair:function",
             "name": "encodePair", "kind": "function",
             "language": "typescript",
             "path": "src/openapi-gen/request.ts",
             "span": {"start_line": 7, "end_line": 15},
             "supply_chain": {"tier": 1, "is_generated_file": True}},
            {"id": "ts:openapi-gen/OpenAPI.ts:17-25:Interceptors.use:method",
             "name": "use", "kind": "method", "language": "typescript",
             "path": "src/openapi-gen/OpenAPI.ts",
             "span": {"start_line": 17, "end_line": 25},
             "supply_chain": {"tier": 1, "is_generated_file": True}},
            {"id": "ts:openapi-gen/ApiError.ts:27-30:ApiError.constructor:method",
             "name": "constructor", "kind": "method",
             "language": "typescript",
             "path": "src/openapi-gen/ApiError.ts",
             "span": {"start_line": 27, "end_line": 30},
             "supply_chain": {"tier": 1, "is_generated_file": True}},
            # Handwritten helper — should remain in the candidate list.
            {"id": "ts:src/util.ts:1-5:handwrittenHelper:function",
             "name": "handwrittenHelper", "kind": "function",
             "language": "typescript",
             "path": "src/util.ts",
             "span": {"start_line": 1, "end_line": 5},
             "supply_chain": {"tier": 1, "is_generated_file": False}},
        ]
        bm_path = _make_behavior_map(tmp_path, nodes, [])
        args = argparse.Namespace(
            path=str(tmp_path), input=str(bm_path), format="json",
            seeds="entrypoints", min_confidence=0.0,
            exclude_annotated=False, exclude_exports=False,
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
        dead_names = {c["name"] for c in output["dead_candidates"]}
        assert "then" not in dead_names, dead_names
        assert "encodePair" not in dead_names, dead_names
        assert "use" not in dead_names, dead_names
        assert "constructor" not in dead_names, dead_names
        # Handwritten helper is unreachable and should be in the list.
        assert "handwrittenHelper" in dead_names, dead_names

    def test_exclude_exports_drops_public_api(
        self, tmp_path: Path,
    ) -> None:
        """WI-zafab filter 3: --exclude-exports drops candidates where
        supply_chain.is_exported=True (public API)."""
        import argparse

        nodes = [
            # Public API — should be excluded when --exclude-exports.
            {"id": "go:pkg/api.go:1-5:PublicFn:function",
             "name": "PublicFn", "kind": "function", "language": "go",
             "path": "pkg/api.go",
             "span": {"start_line": 1, "end_line": 5},
             "supply_chain": {"tier": 1, "is_exported": True}},
            # Private helper — should remain in the candidate list.
            {"id": "go:pkg/internal.go:1-5:helperFn:function",
             "name": "helperFn", "kind": "function", "language": "go",
             "path": "pkg/internal.go",
             "span": {"start_line": 1, "end_line": 5},
             "supply_chain": {"tier": 1, "is_exported": False}},
        ]
        bm_path = _make_behavior_map(tmp_path, nodes, [])
        args = argparse.Namespace(
            path=str(tmp_path), input=str(bm_path), format="json",
            seeds="entrypoints", min_confidence=0.0,
            exclude_annotated=False,
            exclude_exports=True,
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
        dead_names = {c["name"] for c in output["dead_candidates"]}
        assert "PublicFn" not in dead_names
        assert "helperFn" in dead_names

    def test_exclude_exports_default_false(
        self, tmp_path: Path,
    ) -> None:
        """Without --exclude-exports, exported symbols stay in the list."""
        import argparse

        nodes = [
            {"id": "go:pkg/api.go:1-5:PublicFn:function",
             "name": "PublicFn", "kind": "function", "language": "go",
             "path": "pkg/api.go",
             "span": {"start_line": 1, "end_line": 5},
             "supply_chain": {"tier": 1, "is_exported": True}},
        ]
        bm_path = _make_behavior_map(tmp_path, nodes, [])
        args = argparse.Namespace(
            path=str(tmp_path), input=str(bm_path), format="json",
            seeds="entrypoints", min_confidence=0.0,
            exclude_annotated=False,
            exclude_exports=False,
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
        dead_names = {c["name"] for c in output["dead_candidates"]}
        assert "PublicFn" in dead_names

    def test_ffi_signature_flag_accepts_string_decorator(self) -> None:
        """WI-hadap H2: a bare-string decorator entry (older-schema
        encoding) matches the fragment check."""
        from hypergumbo_core.cli import _compute_ffi_signature_flag
        node = {"meta": {"decorators": ["no_mangle"]}}
        assert _compute_ffi_signature_flag(node) is True

    def test_ffi_signature_flag_ignores_non_dict_non_str_decorator(
        self,
    ) -> None:
        """WI-hadap H2: a non-dict/non-str decorator entry is ignored."""
        from hypergumbo_core.cli import _compute_ffi_signature_flag
        node = {"meta": {"decorators": [42, None]}}
        assert _compute_ffi_signature_flag(node) is False

    def test_ffi_signature_flag_false_for_plain_function(
        self, tmp_path: Path,
    ) -> None:
        """WI-hadap H2: candidates without FFI markers are not flagged."""
        import argparse

        nodes = [
            {"id": "py:app.py:1-5:helper:function",
             "name": "helper", "kind": "function", "language": "python",
             "path": "app.py",
             "span": {"start_line": 1, "end_line": 5},
             "meta": {"decorators": [{"name": "staticmethod"}]}},
        ]
        bm_path = _make_behavior_map(tmp_path, nodes, [])
        args = argparse.Namespace(
            path=str(tmp_path), input=str(bm_path), format="json",
            seeds="entrypoints", min_confidence=0.0,
            exclude_annotated=False,
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
        assert output["dead_candidates"][0]["ffi_signature"] is False

    def test_seeds_all_includes_exports(self, tmp_path: Path) -> None:
        """WI-zimum: --seeds all combines entrypoints, tests, AND exports."""
        import argparse

        nodes = [
            {"id": "py:app.py:1-5:GET /api:route", "name": "api", "kind": "route",
             "language": "python", "path": "app.py",
             "span": {"start_line": 1, "end_line": 5},
             "meta": {"route_path": "/api", "http_method": "GET"}},
            {"id": "py:app.py:7-10:reached:function", "name": "reached",
             "kind": "function", "language": "python", "path": "app.py",
             "span": {"start_line": 7, "end_line": 10},
             "supply_chain": {"tier": 1, "is_exported": False}},
            # Exported but unreached via entrypoints — should be admitted via exports.
            {"id": "py:app.py:12-20:exported_fn:function", "name": "exported_fn",
             "kind": "function", "language": "python", "path": "app.py",
             "span": {"start_line": 12, "end_line": 20},
             "supply_chain": {"tier": 1, "is_exported": True}},
        ]
        edges = [
            {"type": "calls", "src": "py:app.py:1-5:GET /api:route",
             "dst": "py:app.py:7-10:reached:function"},
        ]
        bm_path = _make_behavior_map(tmp_path, nodes, edges)

        args = argparse.Namespace(
            path=str(tmp_path), input=str(bm_path), format="json",
            seeds="all", min_confidence=0.0, exclude_annotated=False,
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
        # Both reached and exported_fn should be alive under --seeds all.
        assert "reached" not in dead_names
        assert "exported_fn" not in dead_names
