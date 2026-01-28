"""Route-handler linker for connecting routes to their handler functions.

This linker creates routes_to edges from route symbols to their handler symbols
using metadata stored during route detection.

How It Works
------------
1. Find all route symbols (kind="route")
2. Extract handler reference from metadata:
   - Rails: controller_action = "users#index" → UsersController#index
   - Phoenix: controller = "UserController", action = "index" → UserController.index
   - Laravel: controller_action = "UserController@index"
   - Express/JS: handler_ref = "userController.list" → list function
3. Resolve handler reference to actual method/function symbols
4. Create routes_to edges linking routes to handlers

Why This Design
---------------
- Converts handler metadata into actual graph edges
- Enables traversal from routes to their implementation
- Supports multiple frameworks via pluggable resolution strategies
- Post-hoc linking works with complete symbol table

Supported Frameworks
--------------------
- Ruby/Rails: controller_action = "controller#action"
- Elixir/Phoenix: controller + action fields
- PHP/Laravel: controller_action = "Controller@action"
- JS/TS Express: handler_ref = "module.function"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..ir import AnalysisRun, Edge, Symbol
from .registry import LinkerContext, LinkerResult, LinkerRequirement, register_linker

if TYPE_CHECKING:
    pass

PASS_ID = "route-handler-linker-v1"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class RouteHandlerResult:
    """Result of route-handler linking."""

    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None


def _normalize_rails_controller(controller: str) -> str:
    """Convert Rails controller name to class name.

    Examples:
        users -> UsersController
        admin/users -> Admin::UsersController
    """
    parts = controller.split("/")
    normalized_parts = []
    for part in parts:
        # Convert snake_case to CamelCase and add Controller suffix for last part
        words = part.split("_")
        camel = "".join(word.capitalize() for word in words)
        normalized_parts.append(camel)

    if len(normalized_parts) == 1:
        return f"{normalized_parts[0]}Controller"
    else:
        # Namespace::Controller format
        return "::".join(normalized_parts[:-1]) + f"::{normalized_parts[-1]}Controller"


def _resolve_rails_handler(
    controller_action: str, symbol_by_name: dict[str, Symbol]
) -> Symbol | None:
    """Resolve Rails controller#action to a handler symbol.

    Args:
        controller_action: String like "users#index" or "admin/users#show"
        symbol_by_name: Lookup table of symbols by name

    Returns:
        Matching Symbol or None
    """
    if "#" not in controller_action:
        return None

    controller, action = controller_action.split("#", 1)
    controller_class = _normalize_rails_controller(controller)

    # Try exact match: UsersController#index
    full_name = f"{controller_class}#{action}"
    if full_name in symbol_by_name:
        return symbol_by_name[full_name]

    # Try variations
    # UsersController.index (some analyzers use . instead of #)
    dot_name = f"{controller_class}.{action}"
    if dot_name in symbol_by_name:
        return symbol_by_name[dot_name]

    # Try just action name with class metadata match
    if action in symbol_by_name:
        sym = symbol_by_name[action]
        sym_class = (sym.meta or {}).get("class", "")
        if controller_class in sym_class or sym_class.endswith(controller_class):
            return sym

    return None


def _resolve_laravel_handler(
    controller_action: str, symbol_by_name: dict[str, Symbol]
) -> Symbol | None:
    """Resolve Laravel Controller@action to a handler symbol.

    Args:
        controller_action: String like "UserController@index"
        symbol_by_name: Lookup table of symbols by name

    Returns:
        Matching Symbol or None
    """
    if "@" not in controller_action:  # pragma: no cover - validated by caller
        return None

    controller, action = controller_action.split("@", 1)

    # Try exact match: UserController@index
    full_name = f"{controller}@{action}"
    if full_name in symbol_by_name:  # pragma: no cover - defensive: rare exact match
        return symbol_by_name[full_name]

    # Try with :: separator: UserController::index
    colon_name = f"{controller}::{action}"
    if colon_name in symbol_by_name:  # pragma: no cover - defensive: PHP namespace style
        return symbol_by_name[colon_name]

    # Try dot separator: UserController.index
    dot_name = f"{controller}.{action}"
    if dot_name in symbol_by_name:
        return symbol_by_name[dot_name]

    # Try just action name with class metadata match
    if action in symbol_by_name:  # pragma: no cover - defensive: fallback lookup
        sym = symbol_by_name[action]
        sym_class = (sym.meta or {}).get("class", "")
        if controller in sym_class or sym_class.endswith(controller):
            return sym

    return None  # pragma: no cover - defensive: no match found


def _resolve_phoenix_handler(
    controller: str, action: str, symbol_by_name: dict[str, Symbol]
) -> Symbol | None:
    """Resolve Phoenix controller/action to a handler symbol.

    Args:
        controller: Controller module name like "UserController"
        action: Action function name like "index"
        symbol_by_name: Lookup table of symbols by name

    Returns:
        Matching Symbol or None
    """
    # Try exact match: UserController.index
    full_name = f"{controller}.{action}"
    if full_name in symbol_by_name:
        return symbol_by_name[full_name]

    # Try with Web suffix: AppWeb.UserController.index
    for name, sym in symbol_by_name.items():
        if name.endswith(f".{controller}.{action}"):
            return sym
        if name.endswith(f"{controller}.{action}"):
            return sym

    return None  # pragma: no cover - defensive: iteration found no match


def _resolve_express_handler(
    handler_ref: str, symbol_by_name: dict[str, Symbol]
) -> Symbol | None:
    """Resolve Express/JS handler_ref to a handler symbol.

    Args:
        handler_ref: Handler reference like "userController.list" or "list"
        symbol_by_name: Lookup table of symbols by name

    Returns:
        Matching Symbol or None (excludes route symbols to avoid self-reference)
    """

    def is_handler(sym: Symbol) -> bool:
        """Check if symbol is a potential handler (not a route itself)."""
        return sym.kind in ("function", "method", "arrow_function")

    # Try exact match first (must be a function/method, not a route)
    if handler_ref in symbol_by_name:
        sym = symbol_by_name[handler_ref]
        if is_handler(sym):
            return sym

    # Extract function name from qualified reference (module.function)
    if "." in handler_ref:
        parts = handler_ref.split(".")
        func_name = parts[-1]  # Last part is the function name

        # Try just the function name
        if func_name in symbol_by_name:
            sym = symbol_by_name[func_name]
            if is_handler(sym):
                return sym

        # Try looking for symbols that end with the function name
        for name, sym in symbol_by_name.items():
            if (name.endswith(f".{func_name}") or name == func_name) and is_handler(sym):
                return sym

    return None  # pragma: no cover - defensive: no match found


def _extract_handler_ref(route: Symbol) -> dict[str, str] | None:
    """Extract handler reference info from route metadata.

    Returns dict with 'type' and relevant fields, or None.
    """
    meta = route.meta or {}

    # controller_action can be Rails (users#index) or Laravel (UserController@index)
    if "controller_action" in meta:
        controller_action = meta["controller_action"]
        if "@" in controller_action:
            # Laravel format: UserController@index
            return {"type": "laravel", "controller_action": controller_action}
        else:
            # Rails format: users#index
            return {"type": "rails", "controller_action": controller_action}

    # Phoenix: controller + action fields
    if "controller" in meta and "action" in meta:
        return {
            "type": "phoenix",
            "controller": meta["controller"],
            "action": meta["action"],
        }

    # Express/JS: handler_ref field (e.g., "userController.list")
    if meta.get("handler_ref"):
        return {"type": "express", "handler_ref": meta["handler_ref"]}

    # Django: view_name (handled separately via deferred resolution)

    return None


def link_routes_to_handlers(
    symbols: list[Symbol],
    edges: list[Edge],
) -> RouteHandlerResult:
    """Link route symbols to their handler functions.

    Args:
        symbols: All symbols from all analyzers
        edges: Existing edges (not modified)

    Returns:
        RouteHandlerResult with new edges and run info
    """
    # Build symbol lookup by name
    symbol_by_name: dict[str, Symbol] = {}
    for s in symbols:
        symbol_by_name[s.name] = s
        # Also index by qualified name if available
        if s.meta and s.meta.get("qualified_name"):
            symbol_by_name[s.meta["qualified_name"]] = s

    # Find route symbols
    routes = [s for s in symbols if s.kind == "route"]

    new_edges: list[Edge] = []
    routes_linked = 0

    for route in routes:
        handler_ref = _extract_handler_ref(route)
        if not handler_ref:
            continue

        handler: Symbol | None = None

        if handler_ref["type"] == "rails":
            handler = _resolve_rails_handler(
                handler_ref["controller_action"], symbol_by_name
            )
        elif handler_ref["type"] == "laravel":
            handler = _resolve_laravel_handler(
                handler_ref["controller_action"], symbol_by_name
            )
        elif handler_ref["type"] == "phoenix":
            handler = _resolve_phoenix_handler(
                handler_ref["controller"], handler_ref["action"], symbol_by_name
            )
        elif handler_ref["type"] == "express":
            handler = _resolve_express_handler(
                handler_ref["handler_ref"], symbol_by_name
            )

        if handler:
            edge = Edge(
                id=f"edge:{route.id}->routes_to->{handler.id}",
                src=route.id,
                dst=handler.id,
                edge_type="routes_to",
                line=route.span.start_line if route.span else 0,
                confidence=0.9,
                origin=PASS_ID,
                meta={k: v for k, v in handler_ref.items() if k != "type"},
            )
            new_edges.append(edge)
            routes_linked += 1

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    run.files_analyzed = len(routes)  # Using this field to track routes processed

    return RouteHandlerResult(edges=new_edges, run=run)


def _check_routes_available(ctx: LinkerContext) -> int:
    """Check how many route symbols have handler metadata."""
    count = 0
    for s in ctx.symbols:
        if s.kind == "route" and _extract_handler_ref(s):
            count += 1
    return count


@register_linker(
    "route_handler",
    priority=60,  # After basic analysis, before HTTP client linking
    requirements=[
        LinkerRequirement(
            name="routes_with_handlers",
            description="Route symbols with handler metadata (controller_action, etc.)",
            check=_check_routes_available,
        )
    ],
)
def link_route_handler(ctx: LinkerContext) -> LinkerResult:
    """Linker entry point for registry."""
    result = link_routes_to_handlers(ctx.symbols, ctx.edges)

    return LinkerResult(
        symbols=[],
        edges=result.edges,
        run=result.run,
    )
