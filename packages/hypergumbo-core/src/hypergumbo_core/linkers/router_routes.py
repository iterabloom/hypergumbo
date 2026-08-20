# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: router → route registrations containment.

Creates ``references`` edges (``meta['mechanism']='route_registration'``)
from symbols tagged ``concept: router`` (module / combinator / route-table
groupings per framework YAML) to the ``concept: route`` registration symbols
whose spans nest within the router's span in the same file.

Semantic Distinction From ``controller_routes``
-----------------------------------------------
Two different dispatch containers in the concept vocabulary both enclose
``route`` symbols:

- **Controller** (Phase 2 / WI-gokop): class-grouping of route handler
  *methods* (``UsersController.index``, ``UserViewSet.list``). Emits
  ``contains`` with ``meta['framework_dispatch']='controller_routes'``.
- **Router** (this linker, Phase 3 / WI-gudob): module / combinator /
  route-table grouping of route *registration* call sites (Phoenix
  ``get "/path", Controller, :action``; http4s ``HttpRoutes.of { case
  GET -> Root / "x" => ... }``; Nuxt/Remix filesystem-conventional
  routes; Yesod ``parseRoutes`` quasiquote). Emits ``references`` with
  ``meta['mechanism']='route_registration'`` — the source is not a
  handler class but a registration container.

Both bespoke edge types (``contains_routes`` / ``registers_routes``) were
folded onto the shared canonical types by ADR-0023 §6 Phase 3
(audit-findings 0001 / WI-vasik-jofiv). The distinction that used to live
in the edge type now lives in ``meta``: a slice query asking "which routes
does this controller class handle?" versus "which routes does this router
module register?" discriminates on ``meta['framework_dispatch']``, not on
``edge_type``.

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
5. Emit ``references`` edges with ``evidence_type="ast_call_direct"``,
   confidence 0.80, and ``meta={"mechanism": "route_registration",
   "framework_dispatch": "router_routes"}``.

Why This Design
---------------
Parallel to ``controller_routes.py``: the per-framework dispatch
semantics live in the YAML (which symbols are tagged as router /
route); the linker itself is framework-agnostic code that consumes
those tags (INV-nimuj). Keeping router and controller as *two*
linkers (rather than one generalized ``dispatch_container`` linker
matching either concept) preserves the dispatch-mechanism distinction
in ``meta`` and keeps the two linkers independently auditable.

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
``router`` carries 16 producer frameworks per docs/CONCEPTS.md
(Phase 3 of WI-gudob).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..ir import PASS_VERSION, AnalysisRun, Edge, make_pass_id
from ..paths import is_test_file
from ._concept_utils import has_concept
from .registry import LinkerContext, LinkerResult, register_linker

if TYPE_CHECKING:
    from ..ir import Span, Symbol

logger = logging.getLogger(__name__)

PASS_ID = make_pass_id("router-routes-linker")


def _encloses(container_span: "Span", member_span: "Span") -> bool:
    return (
        container_span.start_line <= member_span.start_line
        and container_span.end_line >= member_span.end_line
    )


def _span_size(span: "Span") -> int:
    return span.end_line - span.start_line


@register_linker(
    "router-routes-linker",
    priority=56,
    description="Link router-concept symbols to their nested route registrations",
    # CNF: router symbols carry the "router" concept, emitted across the same
    # set of HTTP-server frameworks/languages as route handlers.
    depends_on=[["python", "javascript", "ruby", "java", "go", "csharp", "elixir", "php"]],
)
def link_router_routes(ctx: LinkerContext) -> LinkerResult:
    """Create route-registration ``references`` edges from routers to routes."""
    run = AnalysisRun.create(
        pass_id=PASS_ID,
        version=PASS_VERSION,
    )

    # (Symbol, Span) tuples: the entry filter establishes span presence, and
    # carrying the narrowed Span in the bucket lets the type system hold that
    # invariant across the collection boundary (mypy cannot re-derive it from
    # dict[str, list[Symbol]] downstream).
    routers_by_file: dict[str, list[tuple[Symbol, Span]]] = {}
    routes_by_file: dict[str, list[tuple[Symbol, Span]]] = {}

    for sym in ctx.symbols:
        span = sym.span
        if span is None:
            continue
        if is_test_file(sym.path):
            continue
        if has_concept(sym, "router"):
            routers_by_file.setdefault(sym.path, []).append((sym, span))
        if has_concept(sym, "route"):
            routes_by_file.setdefault(sym.path, []).append((sym, span))

    edges: list[Edge] = []

    for path, routers in routers_by_file.items():
        routes = routes_by_file.get(path, [])
        if not routes:
            continue
        for route, route_span in routes:
            enclosing = [
                (r, r_span)
                for r, r_span in routers
                if _encloses(r_span, route_span)
            ]
            if not enclosing:
                continue
            winner, winner_span = min(
                enclosing, key=lambda pair: _span_size(pair[1])
            )
            # ADR-0023 §6 Phase 3 / audit-findings 0001 (WI-vasik-jofiv):
            # Router declares routes (declaration-time, not dispatch).
            # Canonical 'references' +
            # meta['mechanism']='route_registration'.
            edge = Edge.create(
                src=winner.id,
                dst=route.id,
                edge_type="references",
                line=winner_span.start_line,
                origin=PASS_ID,
                evidence_type="ast_call_direct",
                confidence=0.80,
                meta={"mechanism": "route_registration", "framework_dispatch": "router_routes"},
                origin_run_id=run.execution_id,
                derived_from=[winner.id, route.id],
            )
            edges.append(edge)



    if edges:
        logger.info(
            "router-routes: %d edges across %d routers",
            len(edges),
            sum(len(rs) for rs in routers_by_file.values()),
        )

    return LinkerResult(symbols=[], edges=edges, run=run)
