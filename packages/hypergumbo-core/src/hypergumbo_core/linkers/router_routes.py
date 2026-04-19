# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: router → route registrations containment.

Creates ``registers_routes`` edges from symbols tagged ``concept: router``
(module / combinator / route-table groupings per framework YAML) to the
``concept: route`` registration symbols whose spans nest within the
router's span in the same file.

Semantic Distinction From ``controller_routes``
-----------------------------------------------
Two different dispatch containers in the concept vocabulary both enclose
``route`` symbols:

- **Controller** (Phase 2 / WI-gokop): class-grouping of route handler
  *methods* (``UsersController.index``, ``UserViewSet.list``). The
  linker's edge type is ``contains_routes``.
- **Router** (this linker, Phase 3 / WI-gudob): module / combinator /
  route-table grouping of route *registration* call sites (Phoenix
  ``get "/path", Controller, :action``; http4s ``HttpRoutes.of { case
  GET -> Root / "x" => ... }``; Nuxt/Remix filesystem-conventional
  routes; Yesod ``parseRoutes`` quasiquote). The linker's edge type is
  ``registers_routes`` — the source is not a handler class but a
  registration container.

Keeping edge types distinct matters for slice queries ("which routes
does this controller class handle?" versus "which routes does this
router module register?") and avoids collapsing semantically different
relationships into one bucket.

Scope of Coverage (Honest Enumeration)
--------------------------------------
The linker is effective when both conditions hold:

1. The router-tagged symbol has a span that encloses its route children.
2. The routes it registers get ``concept: route`` from the same YAML
   pattern set.

- **Works**: Phoenix (``use Phoenix.Router`` module enclosing
  ``get``/``post``/``resources``/``live`` macro calls), http4s
  (``HttpRoutes.of`` call site enclosing ``case GET -> Root / ...``
  arms), http4k (routing combinator calls enclosing ``bind``
  registrations), Yesod (``mkYesod`` quasiquote enclosing route-table
  entries), giraffe / pedestal / ring-compojure route-table
  expressions, cowboy ``routes`` list, sveltekit/remix/nuxt file-route
  tables, vertx ``Router.route()`` chains, plumber ``pr_get``/``pr_post``
  attachments, laminas module router config.
- **Does NOT work out of the box**: Frameworks whose router is purely
  configuration-file-based with no span (pure ``config/routes.rb`` in
  Rails) — the router concept isn't emitted on a code symbol there, so
  nothing to link. Filesystem-convention routers where the routes are
  separate files and cross-file containment is required (a file-level
  routing table that doesn't enclose the handlers textually).

How It Works
------------
1. Find all symbols with ``meta.concepts`` containing ``{"concept":
   "router"}``.
2. Find all symbols with ``{"concept": "route"}``.
3. Group by file path (routers in different files are independent).
4. For each route, pick the tightest (smallest) enclosing router whose
   span fully contains the route's span. Only the innermost router
   wins — avoids redundant edges when nested combinators coexist in
   the same file (http4s: an outer ``HttpRoutes`` call combined with
   an inner ``HttpRoutes.of`` arm).
5. Emit ``registers_routes`` edges with
   ``evidence_type="router_routes"`` and confidence 0.80.

Why This Design
---------------
Parallel to ``controller_routes.py``: the per-framework dispatch
semantics live in the YAML (which symbols are tagged as router /
route); the linker itself is framework-agnostic code that consumes
those tags (INV-nimuj). Keeping router and controller as *two*
linkers (rather than one generalized ``dispatch_container`` linker
matching either concept) preserves the edge-type semantic distinction
and keeps the two linkers independently auditable.

Why the Innermost Router Wins
-----------------------------
In http4s / http4k idioms a single file can combine an outer routes
composition (``HttpRoutes``) with one or more inner ``HttpRoutes.of``
blocks; both carry ``concept: router`` and both enclose the same route
case arms. Only the tightest router is the true "registrant" for that
route; picking the innermost avoids doubled edges.

Limitations
-----------
- Only links within a single file. Filesystem-conventional routing
  (Nuxt ``pages/``, Remix ``routes/``, SvelteKit ``src/routes/``)
  where the router is a virtual file-system table and the handlers
  are separate files is NOT modeled — the router concept either
  appears on a file-level table symbol (handled in-file) or isn't
  recoverable here at all.
- Test-file symbols are excluded to prevent spurious edges on fixture
  routers.
- Bare-string concept shape (``meta.concepts = ["router"]``) is
  deliberately *not* matched; see INV-tuzub and the shared
  ``_concept_utils.has_concept`` helper.

Subcategory: Framework (framework-specific dispatch — route
registration under per-framework router conventions). Target concept
``router`` carries 14 producer frameworks per docs/CONCEPTS.md
(Phase 3 of WI-gudob).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..ir import PASS_VERSION, AnalysisRun, Edge, make_pass_id
from ._concept_utils import has_concept
from .registry import LinkerContext, LinkerResult, register_linker

if TYPE_CHECKING:
    from ..ir import Symbol

logger = logging.getLogger(__name__)

PASS_ID = make_pass_id("router-routes-linker")

_TEST_PATH_SEGMENTS = frozenset({"tests", "test", "testing", "conftest", "__tests__"})


def _is_test_path(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    basename = parts[-1] if parts else ""
    return (
        any(p in _TEST_PATH_SEGMENTS for p in parts)
        or basename.startswith("test_")
        or basename.endswith("_test.py")
        or basename.endswith("_test.rb")
        or basename.endswith("_test.exs")
    )


def _encloses(container_span, member_span) -> bool:
    return (
        container_span.start_line <= member_span.start_line
        and container_span.end_line >= member_span.end_line
    )


def _span_size(span) -> int:
    return span.end_line - span.start_line


@register_linker(
    "router-routes",
    priority=56,
    description="Link router-concept symbols to their nested route registrations",
)
def link_router_routes(ctx: LinkerContext) -> LinkerResult:
    """Create registers_routes edges from routers to nested route symbols."""
    routers_by_file: dict[str, list[Symbol]] = {}
    routes_by_file: dict[str, list[Symbol]] = {}

    for sym in ctx.symbols:
        if sym.span is None:
            continue
        if _is_test_path(sym.path):
            continue
        if has_concept(sym, "router"):
            routers_by_file.setdefault(sym.path, []).append(sym)
        if has_concept(sym, "route"):
            routes_by_file.setdefault(sym.path, []).append(sym)

    edges: list[Edge] = []

    for path, routers in routers_by_file.items():
        routes = routes_by_file.get(path, [])
        if not routes:
            continue
        for route in routes:
            enclosing = [r for r in routers if _encloses(r.span, route.span)]
            if not enclosing:
                continue
            winner = min(enclosing, key=lambda r: _span_size(r.span))
            edge = Edge.create(
                src=winner.id,
                dst=route.id,
                edge_type="registers_routes",
                line=winner.span.start_line,
                origin=PASS_ID,
                evidence_type="router_routes",
                confidence=0.80,
            )
            edges.append(edge)

    run = AnalysisRun.create(
        pass_id=PASS_ID,
        version=PASS_VERSION,
    )

    if edges:
        logger.info(
            "router-routes: %d edges across %d routers",
            len(edges),
            sum(len(rs) for rs in routers_by_file.values()),
        )

    return LinkerResult(symbols=[], edges=edges, run=run)
