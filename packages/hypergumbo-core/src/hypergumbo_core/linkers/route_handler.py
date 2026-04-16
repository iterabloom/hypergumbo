# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: route-handler for connecting routes to their handler functions.

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
5. For React Router v6.4+ routes: resolve loader_ref/action_ref to function
   symbols and create additional routes_to edges with role=loader/action metadata

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
- React Router v6.4+: loader_ref/action_ref = data loader/action function names
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


@dataclass
class _RailsIndex:
    """Pre-built indexes for fast Rails handler resolution.

    Built once per link_routes_to_handlers call and shared across all
    route resolutions. Avoids O(routes * symbols) linear scans.
    """

    symbol_by_name: dict[str, Symbol]
    # action -> [(sym_class_part, sep, name, sym)] for suffix/reverse/CI matching
    by_action: dict[str, list[tuple[str, str, str, Symbol]]]
    # (action, class_lower) -> (name, sym) for case-insensitive exact match
    by_action_class_lower: dict[tuple[str, str], tuple[str, Symbol]]

    @staticmethod
    def build(symbol_by_name: dict[str, Symbol]) -> _RailsIndex:
        """Build indexes from symbol_by_name."""
        by_action: dict[str, list[tuple[str, str, str, Symbol]]] = {}
        by_action_class_lower: dict[tuple[str, str], tuple[str, Symbol]] = {}
        for name, sym in symbol_by_name.items():
            for sep in ("#", "."):
                if sep not in name:
                    continue
                sym_class_part, sym_action = name.rsplit(sep, 1)
                by_action.setdefault(sym_action, []).append(
                    (sym_class_part, sep, name, sym)
                )
                key = (sym_action, sym_class_part.lower())
                if key not in by_action_class_lower:
                    by_action_class_lower[key] = (name, sym)
        return _RailsIndex(
            symbol_by_name=symbol_by_name,
            by_action=by_action,
            by_action_class_lower=by_action_class_lower,
        )


def _resolve_rails_handler(
    controller_action: str, symbol_by_name: dict[str, Symbol],
    rails_index: _RailsIndex | None = None,
) -> Symbol | None:
    """Resolve Rails controller#action to a handler symbol.

    Args:
        controller_action: String like "users#index" or "admin/users#show"
        symbol_by_name: Lookup table of symbols by name
        rails_index: Pre-built index for fast resolution. When provided,
            avoids O(N) linear scans of symbol_by_name.

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

    # Use pre-built index for suffix/reverse/CI matching when available
    if rails_index is not None:
        return _resolve_rails_handler_indexed(
            controller_class, action, rails_index
        )

    # Fallback: linear scan (legacy path for direct callers without index)
    return _resolve_rails_handler_scan(  # pragma: no cover
        controller_class, action, symbol_by_name
    )


def _resolve_rails_handler_indexed(
    controller_class: str, action: str, idx: _RailsIndex,
) -> Symbol | None:
    """Resolve Rails handler using pre-built indexes.

    Replaces three O(N) linear scans with O(candidates) lookups keyed
    by action name.
    """
    candidates = idx.by_action.get(action, [])

    # Suffix matching for deeply namespaced controllers
    hash_suffix = f"::{controller_class}"
    for sym_class_part, _sep, _name, sym in candidates:
        if sym_class_part == controller_class:
            return sym  # pragma: no cover - caught by exact-match dict lookups
        if sym_class_part.endswith(hash_suffix):
            return sym

    # Reverse suffix matching
    for sym_class_part, _sep, _name, sym in candidates:
        reverse_suffix = f"::{sym_class_part}"
        if controller_class.endswith(reverse_suffix):
            return sym

    # Case-insensitive fallback for Rails acronym inflections (ADR-0008).
    # Rails treats words like IP, HTTP, SMTP, API as acronyms:
    # 'ip_pool_rules' → 'IPPoolRulesController', not 'IpPoolRulesController'.
    controller_lower = controller_class.lower()
    for sym_class_part, _sep, _name, sym in candidates:
        sym_class_lower = sym_class_part.lower()
        if sym_class_lower == controller_lower:
            return sym
        ci_suffix = f"::{controller_lower}"
        if sym_class_lower.endswith(ci_suffix):
            return sym
        reverse_ci_suffix = f"::{sym_class_lower}"
        if controller_lower.endswith(reverse_ci_suffix):
            return sym

    return None


def _resolve_rails_handler_scan(
    controller_class: str, action: str, symbol_by_name: dict[str, Symbol],
) -> Symbol | None:  # pragma: no cover
    """Legacy linear-scan fallback for Rails handler resolution.

    Used only when _resolve_rails_handler is called without a pre-built index.
    """
    # Suffix matching for deeply namespaced controllers
    hash_suffix = f"::{controller_class}#{action}"
    dot_suffix = f"::{controller_class}.{action}"
    for name, sym in symbol_by_name.items():
        if name.endswith(hash_suffix) or name.endswith(dot_suffix):
            return sym

    # Reverse suffix matching
    for name, sym in symbol_by_name.items():
        for sep in ("#", "."):
            if sep not in name:
                continue
            sym_class_part, sym_action = name.rsplit(sep, 1)
            if sym_action != action:
                continue
            if sym_class_part == controller_class:
                continue
            reverse_suffix = f"::{sym_class_part}"
            if controller_class.endswith(reverse_suffix):
                return sym

    # Case-insensitive fallback
    controller_lower = controller_class.lower()
    for name, sym in symbol_by_name.items():
        for sep in ("#", "."):
            if sep not in name:
                continue
            sym_class_part, sym_action = name.rsplit(sep, 1)
            if sym_action != action:
                continue
            if sym_class_part.lower() == controller_lower:
                return sym
            ci_suffix = f"::{controller_lower}"
            if sym_class_part.lower().endswith(ci_suffix):
                return sym
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

    Handles both traditional Express handlers (functions) and JSX Route
    component references. JSX ``<Route element={<Users />} />`` sets
    handler_ref to the component name, which may be a function, class,
    or module_file symbol. When an exact name match fails, tries common
    React naming patterns like appending "Component" or "View".

    Args:
        handler_ref: Handler reference like "userController.list" or "Users"
        symbol_by_name: Lookup table of symbols by name

    Returns:
        Matching Symbol or None (excludes route symbols to avoid self-reference)
    """

    def is_handler(sym: Symbol) -> bool:
        """Check if symbol is a potential handler (not a route itself)."""
        return sym.kind in ("function", "method", "arrow_function")

    def is_component(sym: Symbol) -> bool:
        """Check if symbol is a React component (class or module_file)."""
        return sym.kind in ("class", "module_file")

    # Try exact match first — prefer function/method kinds, fall back to class/module
    if handler_ref in symbol_by_name:
        sym = symbol_by_name[handler_ref]
        if is_handler(sym):
            return sym
        if is_component(sym):
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

    # JSX component suffix matching: <Route element={<ContentCDN />} /> may
    # reference a symbol named ContentCDNComponent, ContentCDNView, etc.
    _COMPONENT_SUFFIXES = ("Component", "View", "Page", "Screen", "Container")
    for suffix in _COMPONENT_SUFFIXES:
        candidate = handler_ref + suffix
        if candidate in symbol_by_name:
            sym = symbol_by_name[candidate]
            if is_handler(sym) or is_component(sym):
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
    handler_name: str,
    symbol_by_name: dict[str, Symbol],
    symbols_by_short_name: dict[str, list[Symbol]] | None = None,
    route_path: str | None = None,
) -> Symbol | None:
    """Resolve Go handler function name to a handler symbol.

    Go web frameworks (Gin, Echo, Fiber, Chi) pass handler functions directly
    as arguments to route registration calls. The handler can be:
    - Simple identifier: "listUsers"
    - Package-qualified: "handlers.GetAPI"
    - Receiver-method form: "api.query" where ``api`` is a local var of type
      ``*API`` (Go convention: lowercase variable, uppercase type).

    The receiver-method case is the cause of a particularly nasty
    misresolution: ``api.query`` cannot match the symbol stored as
    ``API.query`` (different case), so the resolver falls back to the
    bare short name ``query``, which collides with any unrelated
    package-level function named ``query`` elsewhere in the repo.
    Concrete failure: prometheus's ``GET /query`` route resolved to
    ``cmd/promtool/unittest.go:611:query:function`` instead of
    ``web/api/v1/api.go:502:API.query:method``.

    Disambiguation rule: when the qualified-name lookup misses, prefer
    candidates whose path matches the route's source file (the route's
    `path`, supplied via ``route_path``).  This is the structurally
    correct hint because the registering call site lives in the same
    file as the handler method.

    Args:
        handler_name: Handler function name from route metadata.
        symbol_by_name: Lookup table of symbols by full name (single
            candidate per name; first-wins).
        symbols_by_short_name: Optional multi-valued lookup by short
            name (last segment of dotted name).  When provided,
            same-file disambiguation kicks in.  When omitted, the
            resolver falls back to the legacy single-candidate path.
        route_path: Optional source path of the route symbol used for
            same-file disambiguation.

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

    # For qualified names like "handlers.GetAPI" or "api.query", try the
    # last segment.  When multiple candidates share the short name, prefer
    # the one whose path matches the route's source file — this catches
    # the receiver-method case where ``api.query`` can't match ``API.query``
    # (case mismatch) but the correct method lives in the same file as the
    # route.
    if "." in handler_name:
        func_name = handler_name.rsplit(".", 1)[-1]

        if (
            symbols_by_short_name is not None
            and route_path
            and func_name in symbols_by_short_name
        ):
            # Same-file preference: pick the candidate whose path equals
            # the route's source file.  Falls through to the legacy
            # single-candidate lookup if no in-file match exists.
            for cand in symbols_by_short_name[func_name]:
                if is_handler(cand) and cand.path == route_path:
                    return cand

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

    # handler_ref may be a full symbol ID (e.g., "python:/path:line:Class:class")
    # or a short name (e.g., "userController.list" for Express).
    # Full IDs contain "://" and should be resolved by ID, not by name.
    if meta.get("handler_ref"):
        href = meta["handler_ref"]
        if ":" in href and href.split(":")[0].isalpha() and "/" in href:
            # Looks like a symbol ID (language:/path:span:name:kind)
            return {"type": "direct", "handler_id": href}
        return {"type": "express", "handler_ref": href}

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
    # Build symbol lookup by name and by ID.
    # Name lookup prefers handler-kind symbols over routes because in Go/JS/Django,
    # route symbols often share the same name as their handler function. A naive
    # overwrite would shadow the function with the route, causing resolution to fail.
    # ID lookup is used for direct handler_ref resolution (Flask-RESTful, etc.).
    symbol_by_name: dict[str, Symbol] = {}
    symbol_by_id: dict[str, Symbol] = {}
    # Multi-valued lookup by short name (last segment of dotted name).  Used by
    # the Go resolver to disambiguate between same-named symbols (e.g. an unrelated
    # ``query`` function and an ``API.query`` method) by preferring the candidate
    # in the same file as the route.
    symbols_by_short_name: dict[str, list[Symbol]] = {}
    for s in symbols:
        symbol_by_id[s.id] = s
        existing = symbol_by_name.get(s.name)
        if existing is None or existing.kind == "route":
            symbol_by_name[s.name] = s
        # Also index by qualified name if available
        if s.meta and s.meta.get("qualified_name"):
            qn = s.meta["qualified_name"]
            existing_qn = symbol_by_name.get(qn)
            if existing_qn is None or existing_qn.kind == "route":
                symbol_by_name[qn] = s
        # Index by short name for same-file disambiguation
        if s.kind in ("function", "method"):
            short = s.name.rsplit(".", 1)[-1] if "." in s.name else s.name
            symbols_by_short_name.setdefault(short, []).append(s)

    # Find route symbols
    routes = [s for s in symbols if s.kind == "route"]

    # Build Rails index once if there are any Rails routes
    rails_index: _RailsIndex | None = None
    for route in routes:
        ref = _extract_handler_ref(route)
        if ref and ref["type"] == "rails":
            rails_index = _RailsIndex.build(symbol_by_name)
            break

    new_edges: list[Edge] = []
    routes_linked = 0

    for route in routes:
        handler_ref = _extract_handler_ref(route)
        if not handler_ref:
            continue

        handler: Symbol | None = None

        if handler_ref["type"] == "direct":
            handler = symbol_by_id.get(handler_ref["handler_id"])
        elif handler_ref["type"] == "rails":
            handler = _resolve_rails_handler(
                handler_ref["controller_action"], symbol_by_name,
                rails_index=rails_index,
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
                handler_ref["handler_name"],
                symbol_by_name,
                symbols_by_short_name=symbols_by_short_name,
                route_path=route.path,
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

        # React Router v6.4+ loader/action linking: resolve loader_ref and
        # action_ref metadata to function symbols and create additional edges.
        route_meta = route.meta or {}
        route_line = route.span.start_line if route.span else 0
        for ref_key, role in (("loader_ref", "loader"), ("action_ref", "action")):
            ref_name = route_meta.get(ref_key)
            if not ref_name:
                continue
            target = _resolve_express_handler(ref_name, symbol_by_name)
            if target:
                la_edge = Edge.create(
                    src=route.id,
                    dst=target.id,
                    edge_type="routes_to",
                    line=route_line,
                    confidence=0.85,
                    origin=PASS_ID,
                    meta={"role": role, ref_key: ref_name},
                )
                new_edges.append(la_edge)

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
