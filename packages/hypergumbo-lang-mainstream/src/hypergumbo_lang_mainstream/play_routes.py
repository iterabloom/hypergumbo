# SPDX-License-Identifier: AGPL-3.0-or-later
"""Play Framework routes file parser.

Parses Play Framework ``conf/routes`` files and extracts route symbols
with HTTP method, URL path, and controller action. Play routes files
use a simple text-based DSL (not Scala code), making them amenable to
line-by-line regex parsing.

How It Works
------------
1. Discovers ``conf/routes`` files (and ``*.routes`` partials) under the
   repo root.
2. Parses each line as one of:
   - A route definition: ``METHOD  PATH  CONTROLLER.ACTION(params)``
   - A module include: ``->  PREFIX  module.Routes``
   - A comment or blank line (ignored)
3. Creates ``kind="route"`` Symbol objects with ``meta.route_path``,
   ``meta.http_method``, and ``meta.controller_action`` so the route
   handler linker can wire them to Scala controller methods.

Why Line-by-Line
----------------
Play routes files have a rigid columnar format: whitespace-separated
HTTP method, path, and controller reference. No nesting, no expressions,
no block structure. A regex is more reliable and much faster than pulling
in a tree-sitter grammar for what is essentially a three-column table.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Iterator

from hypergumbo_core.analyze.base import (
    AnalysisResult,
    make_symbol_id,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol, make_pass_id

PASS_ID = make_pass_id("play-routes")
PASS_VERSION = "1"

# HTTP methods recognised by Play Framework router.
_HTTP_METHODS = frozenset({
    "GET", "POST", "PUT", "DELETE", "PATCH",
    "HEAD", "OPTIONS",
})

# Pattern: METHOD  PATH  CONTROLLER.ACTION(params)
# Groups: (method, path, action_with_params)
_ROUTE_RE = re.compile(
    r"^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)"
    r"\s+"
    r"(/\S*)"              # URL path (starts with /)
    r"\s+"
    r"(\S+)"               # controller.action (possibly with params)
    r"(?:\(([^)]*)\))?",   # optional (params) — captured but not required
    re.IGNORECASE,
)

# Module include pattern: ->  /prefix  module.Routes
_MODULE_RE = re.compile(
    r"^->\s+(/\S*)\s+(\S+)",
)


def find_play_routes_files(repo_root: Path) -> Iterator[Path]:
    """Yield Play routes files: conf/routes and *.routes partials."""
    # Primary routes file
    main_routes = repo_root / "conf" / "routes"
    if main_routes.is_file():
        yield main_routes
    # Partial routes files (*.routes) in conf/
    for fpath in find_files(repo_root, ["conf/*.routes"]):
        yield fpath
    # Also check sub-projects (common in multi-module SBT builds)
    for fpath in find_files(repo_root, ["*/conf/routes"]):
        if fpath != main_routes:
            yield fpath


def parse_play_routes(
    content: str,
    file_path: str,
    run_id: str,
) -> tuple[list[Symbol], list[Edge]]:
    """Parse Play routes file content into symbols and edges.

    Returns:
        Tuple of (route_symbols, edges). Edges are empty for now;
        the route_handler linker creates routes_to edges later.
    """
    symbols: list[Symbol] = []
    edges: list[Edge] = []

    for line_no, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()

        # Skip blank lines and comments
        if not line or line.startswith("#"):
            continue

        # Try route match
        m = _ROUTE_RE.match(line)
        if m:
            method = m.group(1).upper()
            path = m.group(2)
            action_full = m.group(3)

            # Strip trailing params: controllers.Foo.bar(id: Int) → controllers.Foo.bar
            action = re.sub(r"\(.*$", "", action_full)
            # Strip leading @ (instance injection marker)
            action = action.lstrip("@")

            route_name = f"{method} {path}"
            span = Span(start_line=line_no, end_line=line_no, start_col=0, end_col=len(raw_line))

            route_id = make_symbol_id(
                "scala",
                path=file_path,
                start_line=line_no,
                end_line=line_no,
                name=route_name,
                kind="route",
            )

            symbols.append(Symbol(
                id=route_id,
                name=route_name,
                kind="route",
                language="scala",
                path=file_path,
                span=span,
                meta={
                    "http_method": method,
                    "route_path": path,
                    "controller_action": action,
                },
                origin=PASS_ID,
                origin_run_id=run_id,
            ))
            continue

        # Try module include: ->  /prefix  module.Routes
        mm = _MODULE_RE.match(line)
        if mm:
            prefix = mm.group(1)
            module_ref = mm.group(2)
            span = Span(start_line=line_no, end_line=line_no, start_col=0, end_col=len(raw_line))
            inc_name = f"-> {prefix} {module_ref}"
            inc_id = make_symbol_id(
                "scala",
                path=file_path,
                start_line=line_no,
                end_line=line_no,
                name=inc_name,
                kind="route_include",
            )
            symbols.append(Symbol(
                id=inc_id,
                name=inc_name,
                kind="route_include",
                language="scala",
                path=file_path,
                span=span,
                meta={
                    "route_prefix": prefix,
                    "module_ref": module_ref,
                },
                origin=PASS_ID,
                origin_run_id=run_id,
            ))

    return symbols, edges


@register_analyzer("play-routes")
def analyze_play_routes(repo_root: Path) -> AnalysisResult:
    """Analyze Play Framework routes files in a repository."""
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    t0 = time.monotonic()

    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []

    for routes_file in find_play_routes_files(repo_root):
        try:
            content = routes_file.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover
            continue

        rel_path = str(routes_file.relative_to(repo_root))
        syms, edges = parse_play_routes(content, rel_path, run.execution_id)
        all_symbols.extend(syms)
        all_edges.extend(edges)

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    run.duration_ms = elapsed_ms
    run.files_analyzed = sum(1 for _ in find_play_routes_files(repo_root))
    return AnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
        skipped=False,
    )
