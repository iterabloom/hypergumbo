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
   - Django: view_name = "list_users" or "accounts.views.list_users"
   - Go/Gin: handler_name = "listUsers" or "handlers.GetAPI"
3. Resolve handler reference to actual method/function symbols
4. Create routes_to edges linking routes to handlers

Why This Design
---------------
- Converts handler metadata into actual graph edges
- Enables traversal from routes to their implementation
- Supports multiple frameworks via pluggable resolution strategies
- Post-hoc linking works with complete symbol table

Note: Route symbols (kind="route") are created by language analyzers, not by the
framework pattern enrichment layer. Enrichment adds ``concept: route`` to handler
symbols (tagging the view function); this linker connects route *entities* to
handlers via ``routes_to`` edges. Both are derived from the same UsageContext
extraction pass — see each analyzer's "Route Detection Architecture" docs.

Supported Frameworks
--------------------
- Ruby/Rails: controller_action = "controller#action"
- Elixir/Phoenix: controller + action fields
- PHP/Laravel: controller_action = "Controller@action"
- JS/TS Express: handler_ref = "module.function"
- Python/Django: view_name = "view_function" or "module.view_function"
- Go/Gin/Echo/Fiber/Chi: handler_name = "functionName" or "pkg.FunctionName"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..ir import AnalysisRun, Edge, PASS_VERSION, Symbol, make_pass_id
from .registry import LinkerContext, LinkerResult, LinkerRequirement, register_linker

if TYPE_CHECKING:
    pass

PASS_ID = make_pass_id("route-handler-linker")


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

    # Try suffix matching for deeply namespaced controllers.
    # Rails routes use short names (e.g., "users#index" → "UsersController#index")
    # but actual symbols may be deeply namespaced
    # (e.g., "Api::V1::Accounts::UsersController#index").
    hash_suffix = f"::{controller_class}#{action}"
    dot_suffix = f"::{controller_class}.{action}"
    for name, sym in symbol_by_name.items():
        if name.endswith(hash_suffix) or name.endswith(dot_suffix):
            return sym

    # Reverse suffix matching: route has a namespaced controller_action
    # (e.g., "api/v1/statuses#destroy" → "Api::V1::StatusesController") but the
    # symbol has a SHORT name (e.g., "StatusesController#destroy") because the
    # Ruby analyzer only captures the immediately enclosing class. Check if the
    # normalized controller_class ENDS WITH the symbol's class portion, respecting
    # namespace boundaries (:: separator) to avoid false positives like
    # "SubscriptionStatusesController" matching "StatusesController".
    for name, sym in symbol_by_name.items():
        for sep in ("#", "."):
            if sep not in name:
                continue
            sym_class_part, sym_action = name.rsplit(sep, 1)
            if sym_action != action:
                continue
            if sym_class_part == controller_class:
                # Already handled by exact match above.
                continue  # pragma: no cover
            # Check namespace-boundary-separated suffix
            reverse_suffix = f"::{sym_class_part}"
            if controller_class.endswith(reverse_suffix):
                return sym

    # Case-insensitive fallback for Rails acronym inflections (ADR-0008).
    # Rails treats words like IP, HTTP, SMTP, API as acronyms:
    # 'ip_pool_rules' → 'IPPoolRulesController', not 'IpPoolRulesController'.
    # Our naive CamelCase conversion can't replicate Rails' custom acronym list,
    # so we fall back to case-insensitive matching after exact match fails.
    controller_lower = controller_class.lower()
    for name, sym in symbol_by_name.items():
        # Check ClassName#action or ClassName.action (case-insensitive on class)
        for sep in ("#", "."):
            if sep not in name:
                continue
            sym_class_part, sym_action = name.rsplit(sep, 1)
            if sym_action != action:
                continue
            # Full match (case-insensitive on controller class portion)
            if sym_class_part.lower() == controller_lower:
                return sym
            # Suffix match for deeply namespaced controllers
            ci_suffix = f"::{controller_lower}"
            if sym_class_part.lower().endswith(ci_suffix):
                return sym
            # Reverse suffix (case-insensitive): controller_class ends with
            # symbol's class portion at a namespace boundary.
            reverse_ci_suffix = f"::{sym_class_part.lower()}"
            if controller_lower.endswith(reverse_ci_suffix):
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

    # Suffix match for PHP backslash-namespaced controllers.
    # Routes use short names (UserController@index) but symbols may be FQ
    # (App\Http\Controllers\UserController.index). Match on \Controller.action
    # suffix to handle namespace resolution.
    backslash_suffix = f"\\{controller}.{action}"
    dot_suffix_only = f".{controller}.{action}"
    for name, sym in symbol_by_name.items():
        if name.endswith(backslash_suffix) or name.endswith(dot_suffix_only):
            return sym

    return None


def _resolve_phoenix_handler(
    controller: str, action: str, symbol_by_name: dict[str, Symbol],
    *, is_live: bool = False,
) -> Symbol | None:
    """Resolve Phoenix controller/action to a handler symbol.

    Args:
        controller: Controller module name like "UserController" or
            LiveView module name like "HomeLive"
        action: Action function name like "index", or LiveView action
            like "page"/"mount"
        symbol_by_name: Lookup table of symbols by name
        is_live: Whether this is a Phoenix LiveView route. If True,
            falls back to resolving the module itself when
            ``controller.action`` isn't found (LiveView modules handle
            actions via ``handle_params/3``, not per-action functions).

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

    # LiveView fallback: resolve to the module itself.
    # LiveView modules handle actions in handle_params/3, not
    # separate functions, so controller.action won't exist.
    if is_live:
        if controller in symbol_by_name:
            sym = symbol_by_name[controller]
            if sym.kind == "module":
                return sym
        # Try with Web suffix: AppWeb.HomeLive
        for name, sym in symbol_by_name.items():
            if sym.kind != "module":
                continue
            if name.endswith(f".{controller}") or name == controller:
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


def _resolve_django_handler(
    view_name: str, symbol_by_name: dict[str, Symbol]
) -> Symbol | None:
    """Resolve Django view_name to a handler symbol.

    Django URL patterns reference views by name, which can be:
    - Simple function name: "list_users"
    - Class-based view: "UserListView"
    - Module-qualified: "accounts.views.list_accounts"

    Args:
        view_name: View name from URL pattern metadata
        symbol_by_name: Lookup table of symbols by name

    Returns:
        Matching Symbol or None
    """

    def is_handler(sym: Symbol) -> bool:
        """Check if symbol is a potential Django handler (function or class)."""
        return sym.kind in ("function", "method", "class")

    # Try exact match first
    if view_name in symbol_by_name:
        sym = symbol_by_name[view_name]
        if is_handler(sym):
            return sym

    # Extract function/class name from module-qualified reference
    if "." in view_name:
        parts = view_name.split(".")
        simple_name = parts[-1]  # Last part is the view function/class name

        # Try just the simple name
        if simple_name in symbol_by_name:
            sym = symbol_by_name[simple_name]
            if is_handler(sym):
                return sym

    return None


def _resolve_go_handler(
    handler_name: str, symbol_by_name: dict[str, Symbol]
) -> Symbol | None:
    """Resolve Go handler function name to a handler symbol.

    Go web frameworks (Gin, Echo, Fiber, Chi) pass handler functions directly
    as arguments to route registration calls. The handler can be:
    - Simple identifier: "listUsers"
    - Package-qualified: "handlers.GetAPI"

    Args:
        handler_name: Handler function name from route metadata
        symbol_by_name: Lookup table of symbols by name

    Returns:
        Matching Symbol or None (excludes route symbols to avoid self-reference)
    """

    def is_handler(sym: Symbol) -> bool:
        """Check if symbol is a potential handler (not a route itself)."""
        return sym.kind in ("function", "method")

    # Try exact match first
    if handler_name in symbol_by_name:
        sym = symbol_by_name[handler_name]
        if is_handler(sym):
            return sym

    # For qualified names like "handlers.GetAPI", try the last segment
    if "." in handler_name:
        func_name = handler_name.rsplit(".", 1)[-1]

        if func_name in symbol_by_name:
            sym = symbol_by_name[func_name]
            if is_handler(sym):
                return sym

        # Try suffix match across all symbols
        for name, sym in symbol_by_name.items():
            if name.endswith(f".{func_name}") and is_handler(sym):
                return sym

    return None


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
        ref: dict[str, str] = {
            "type": "phoenix",
            "controller": meta["controller"],
            "action": meta["action"],
        }
        if meta.get("http_method") == "LIVE":
            ref["live"] = "true"
        return ref

    # Express/JS: handler_ref field (e.g., "userController.list")
    if meta.get("handler_ref"):
        return {"type": "express", "handler_ref": meta["handler_ref"]}

    # Django: view_name field (e.g., "list_users" or "accounts.views.list_accounts")
    if meta.get("view_name"):
        return {"type": "django", "view_name": meta["view_name"]}

    # Go (Gin/Echo/Fiber/Chi): handler_name field (e.g., "listUsers" or "handlers.GetAPI")
    if meta.get("handler_name"):
        return {"type": "go", "handler_name": meta["handler_name"]}

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
    # Build symbol lookup by name, preferring handler-kind symbols over routes.
    # In Go/JS/Django, route symbols often share the same name as their handler
    # function. A naive overwrite would shadow the function with the route,
    # causing the resolver to fail (routes aren't functions/methods).
    symbol_by_name: dict[str, Symbol] = {}
    for s in symbols:
        existing = symbol_by_name.get(s.name)
        if existing is None or existing.kind == "route":
            symbol_by_name[s.name] = s
        # Also index by qualified name if available
        if s.meta and s.meta.get("qualified_name"):
            qn = s.meta["qualified_name"]
            existing_qn = symbol_by_name.get(qn)
            if existing_qn is None or existing_qn.kind == "route":
                symbol_by_name[qn] = s

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
                handler_ref["controller"], handler_ref["action"], symbol_by_name,
                is_live=handler_ref.get("live") == "true",
            )
        elif handler_ref["type"] == "express":
            handler = _resolve_express_handler(
                handler_ref["handler_ref"], symbol_by_name
            )
        elif handler_ref["type"] == "django":
            handler = _resolve_django_handler(
                handler_ref["view_name"], symbol_by_name
            )
        elif handler_ref["type"] == "go":
            handler = _resolve_go_handler(
                handler_ref["handler_name"], symbol_by_name
            )

        if handler:
            edge = Edge.create(
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
