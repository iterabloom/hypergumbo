# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: controller → route methods containment.

Creates ``contains_routes`` edges from symbols tagged ``concept: controller``
(class groupings per framework YAML) to the route-handler method symbols
whose spans nest within the controller's span in the same file.

Scope of Coverage (Honest Enumeration)
--------------------------------------
The linker is effective for decorator-based controller families where the
*handler method itself* carries a ``concept: route`` tag AND the controller
class body encloses the method's span in the same source file. Explicitly:

- **Works**: NestJS (``@Controller`` + ``@Get``/``@Post``), Spring Boot
  (``@Controller``/``@RestController`` + ``@GetMapping``/``@PostMapping``),
  ASP.NET (controller base class + ``[HttpGet]``/``[HttpPost]``), Laravel
  (controller base + decorator), Symfony (``@Route`` / ``#[Route]``),
  Phoenix (pipeline + action), Micronaut, Ktor, Grails, CakePHP.
- **Does NOT work out of the box**: Rails (route calls live in
  ``config/routes.rb``, handler method in ``app/controllers/*`` — different
  files, handler method lacks ``concept: route``) and Django class-based
  views whose methods are not decorated with ``@api_view`` (the YAML tags
  the class as controller but not the methods).  Cross-file controller↔
  route association for those frameworks is a separate concern handled (or
  not) by ``route_handler.py``'s Rails/Phoenix paths.

How It Works
------------
1. Find all symbols with ``meta.concepts`` containing ``{"concept":
   "controller"}``.
2. Find all symbols with ``{"concept": "route"}`` — the per-method route
   handlers that decorator-based framework patterns tag.
3. Group by file path (controllers in different files are independent).
4. For each route, pick the tightest (smallest) enclosing controller whose
   span fully contains the route's span. Only the innermost controller
   wins, avoiding redundant edges when a base class and derived class
   coexist in the same file.
5. Emit ``contains_routes`` edges with ``evidence_type="controller_routes"``
   and confidence 0.80.

Why This Design
---------------
The existing ``containment`` linker already emits a generic ``contains``
edge by naming convention (``UsersController.index`` → class
``UsersController``). This linker is additive, not redundant:

- ``contains_routes`` edges carry semantic *that* the method is a route
  under the group, enabling queries like "all URLs handled by
  UsersController" with a single edge-type filter.
- Span-nesting catches the case where naming-convention fails: e.g.,
  ASP.NET ``UsersController.Get`` is named fine, but Django
  ``UserViewSet.list`` has a single underscore instead of a dot in some
  analyzer outputs, and span containment recovers the edge either way.
- The per-framework control dispatch semantics live in the YAML (which
  symbols are tagged as controller / route); the linker is framework-
  agnostic code that consumes those tags (see INV-nimuj).

Why the Innermost Controller Wins
---------------------------------
When base-class and derived-class controllers coexist in the same file
(rare but real in Phoenix / Laravel), both enclose the route span; only
the derived class is the true "route container" for taint / slice queries.
Picking the tightest span avoids emitting a redundant ancestor edge.

Limitations
-----------
- Only links within a single file. Cross-file inheritance-based route
  composition (e.g., Rails ``ApplicationController`` concerns mixed into
  subclasses) is not modeled here — that is a separate routing-inheritance
  concern (file follow-up if demanded).
- Test-file symbols are excluded to prevent spurious edges on fixture
  controllers.
- Bare-string concept shape (``meta.concepts = ["controller"]``) is
  deliberately *not* matched; see INV-tuzub and the shared
  ``_concept_utils.has_concept`` helper.

Subcategory: Framework (framework-specific dispatch — route-group
registration under per-framework controller conventions). Target concept
``controller`` carries 34 producer frameworks per docs/CONCEPTS.md (Phase 2
of WI-gudob / WI-gokop).
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

PASS_ID = make_pass_id("controller-routes-linker")

_TEST_PATH_SEGMENTS = frozenset({"tests", "test", "testing", "conftest", "__tests__"})


def _is_test_path(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    basename = parts[-1] if parts else ""
    return (
        any(p in _TEST_PATH_SEGMENTS for p in parts)
        or basename.startswith("test_")
        or basename.endswith("_test.py")
        or basename.endswith("_test.rb")
    )


def _encloses(container_span, member_span) -> bool:
    return (
        container_span.start_line <= member_span.start_line
        and container_span.end_line >= member_span.end_line
    )


def _span_size(span) -> int:
    return span.end_line - span.start_line


@register_linker(
    "controller-routes",
    priority=56,
    description="Link controller-concept symbols to their nested route methods",
)
def link_controller_routes(ctx: LinkerContext) -> LinkerResult:
    """Create contains_routes edges from controllers to their route methods."""
    controllers_by_file: dict[str, list[Symbol]] = {}
    routes_by_file: dict[str, list[Symbol]] = {}

    for sym in ctx.symbols:
        if sym.span is None:
            continue
        if _is_test_path(sym.path):
            continue
        if has_concept(sym, "controller"):
            controllers_by_file.setdefault(sym.path, []).append(sym)
        if has_concept(sym, "route"):
            routes_by_file.setdefault(sym.path, []).append(sym)

    edges: list[Edge] = []

    for path, controllers in controllers_by_file.items():
        routes = routes_by_file.get(path, [])
        if not routes:
            continue
        for route in routes:
            # Find innermost (tightest) controller whose span encloses
            # the route's span in the same file.
            enclosing = [
                c for c in controllers if _encloses(c.span, route.span)
            ]
            if not enclosing:
                continue
            winner = min(enclosing, key=lambda c: _span_size(c.span))
            edge = Edge.create(
                src=winner.id,
                dst=route.id,
                edge_type="contains_routes",
                line=winner.span.start_line,
                origin=PASS_ID,
                evidence_type="controller_routes",
                confidence=0.80,
            )
            edges.append(edge)

    run = AnalysisRun.create(
        pass_id=PASS_ID,
        version=PASS_VERSION,
    )

    if edges:
        logger.info(
            "controller-routes: %d edges across %d controllers",
            len(edges),
            sum(len(cs) for cs in controllers_by_file.values()),
        )

    return LinkerResult(symbols=[], edges=edges, run=run)
