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
3. Creates route-marker Symbol objects via ``make_route_symbol`` —
   ``kind="function"`` with ``meta.framework_role='route'`` per the
   ADR-0027 Phase-3 route→function fold (there is no ``route`` symbol
   kind) — carrying ``meta.route_path``, ``meta.http_method``, and
   ``meta.controller_action`` so the route handler linker can wire them
   to Scala controller methods.
4. Mints a second marker for each module-include line, carrying
   ``meta.framework_role='route_include'`` with ``meta.route_prefix`` and
   ``meta.module_ref``, so a prefix-mounted sub-router is a node in its own
   right rather than vanishing from the route table.

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
    make_route_symbol,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import (
    PASS_VERSION,
    AnalysisRun,
    Edge,
    Span,
    Symbol,
    make_pass_id,
)

PASS_ID = make_pass_id("play-routes")
# WI-riroz: use the canonical ir.PASS_VERSION (=__version__). A module-local
# PASS_VERSION="1" previously shadowed it, corrupting this pass's run_signature
# (AnalysisRun.version feeds _compute_run_signature) and decoupling it from the
# package release version that every other pass reports.

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
        the route_handler linker creates dispatches_to edges later.
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

            span = Span(start_line=line_no, end_line=line_no, start_col=0, end_col=len(raw_line))
            symbols.append(make_route_symbol(
                language="scala",
                path=file_path,
                span=span,
                method=method,
                route_path=path,
                origin=PASS_ID,
                origin_run_id=run_id,
                extra_meta={"controller_action": action},
            ))
            continue

        # Try module include: ->  /prefix  module.Routes
        mm = _MODULE_RE.match(line)
        if mm:
            prefix = mm.group(1)
            module_ref = mm.group(2)
            span = Span(start_line=line_no, end_line=line_no, start_col=0, end_col=len(raw_line))
            # WI-zugob: name changes from "-> {prefix} {module_ref}" to
            # "-> {prefix}". The included module keeps its own meta key
            # (module_ref), so nothing is lost — but a name is now derived from
            # method+path like every other route marker, instead of a third
            # bespoke format. The id kind-slot stops being the unregistered
            # "route_include" fossil; the role stays on meta.framework_role.
            symbols.append(make_route_symbol(
                language="scala",
                path=file_path,
                span=span,
                method="->",
                route_path=prefix,
                origin=PASS_ID,
                origin_run_id=run_id,
                framework_role="route_include",
                extra_meta={"route_prefix": prefix, "module_ref": module_ref},
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
