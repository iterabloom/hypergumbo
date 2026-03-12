# SPDX-License-Identifier: AGPL-3.0-or-later
"""Middleware chain linker for connecting consecutive middleware functions.

Creates ``middleware_chain`` edges between consecutive middleware symbols
in the same file, ordered by source line. This enables forward/reverse
slices to traverse the middleware execution pipeline.

How It Works
------------
1. Find all symbols with ``"middleware"`` in ``meta.concepts``
2. Group by file path (middleware in different files are independent chains)
3. Sort each group by source line (registration/declaration order)
4. Create ``references`` edges with ``evidence_type="middleware_chain"``
   between consecutive middleware in each group

Why This Design
---------------
Web frameworks execute middleware in declaration/registration order.
Flask ``@app.before_request`` decorators run top-to-bottom, Django
``MIDDLEWARE`` processes in list order, Go ``router.Use()`` applies in
call order. By chaining middleware symbols in source-line order, slices
from a request entrypoint traverse the full middleware pipeline before
reaching the route handler.

JS/TS already creates middleware_chain edges inline in the analyzer
(Express ``app.get('/path', mw1, mw2, handler)``). This linker provides
the same capability for Python, Go, and other frameworks where middleware
is detected via YAML framework patterns rather than inline in route calls.

Limitations
-----------
- Only chains middleware within the same file. Cross-file middleware
  ordering (e.g., Django settings.MIDDLEWARE) requires config file parsing.
- Does not connect middleware to specific route handlers. Global middleware
  applies to all routes, but we don't model that relationship here.
- Test-file middleware is excluded to prevent false edges.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..ir import PASS_VERSION, AnalysisRun, Edge, make_pass_id
from .registry import LinkerContext, LinkerResult, register_linker

if TYPE_CHECKING:
    from ..ir import Symbol

logger = logging.getLogger(__name__)

PASS_ID = make_pass_id("middleware-chain-linker")

# Path segments that indicate test files
_TEST_PATH_SEGMENTS = frozenset({"tests", "test", "testing", "conftest", "__tests__"})


def _is_test_path(path: str) -> bool:
    """Check if a path belongs to a test file."""
    parts = path.replace("\\", "/").split("/")
    basename = parts[-1] if parts else ""
    return (
        any(p in _TEST_PATH_SEGMENTS for p in parts)
        or basename.startswith("test_")
        or basename.endswith("_test.py")
    )


@register_linker(
    "middleware-chain",
    priority=55,
    description="Chain consecutive middleware symbols in the same file",
)
def link_middleware_chain(ctx: LinkerContext) -> LinkerResult:
    """Create middleware_chain edges between consecutive middleware symbols."""
    # Collect middleware symbols grouped by file
    middleware_by_file: dict[str, list[Symbol]] = {}
    for sym in ctx.symbols:
        if not sym.meta:
            continue
        concepts = sym.meta.get("concepts", [])
        if "middleware" not in concepts:
            continue
        if sym.span is None:
            continue
        if _is_test_path(sym.path):
            continue
        middleware_by_file.setdefault(sym.path, []).append(sym)

    edges: list[Edge] = []

    for _path, mw_list in middleware_by_file.items():
        # Sort by source line
        mw_list.sort(key=lambda s: s.span.start_line if s.span else 0)

        # Create edges between consecutive middleware
        for i in range(len(mw_list) - 1):
            src = mw_list[i]
            dst = mw_list[i + 1]
            edge = Edge.create(
                src=src.id,
                dst=dst.id,
                edge_type="references",
                line=src.span.start_line if src.span else 0,
                origin=PASS_ID,
                evidence_type="middleware_chain",
                confidence=0.70,
            )
            edges.append(edge)

    run = AnalysisRun.create(
        pass_id=PASS_ID,
        version=PASS_VERSION,
    )

    if edges:
        logger.info(
            "middleware-chain: %d edges across %d files",
            len(edges),
            len([f for f in middleware_by_file.values() if len(f) > 1]),
        )

    return LinkerResult(symbols=[], edges=edges, run=run)
