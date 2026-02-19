"""Elixir analysis pass using tree-sitter-elixir.

This analyzer uses tree-sitter to parse Elixir files and extract:
- Module declarations (defmodule)
- Function declarations (def/defp)
- Macro declarations (defmacro/defmacrop)
- Function call relationships
- Import relationships (use/import/alias)
- OTP/Phoenix behaviour callback edges (use GenServer, use Phoenix.LiveView, etc.)

If tree-sitter with Elixir support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Parse all files, extract all symbols into global registry
2. Pass 2: Detect calls, resolve against global registry, extract Phoenix
   routes and OTP behaviour callback edges

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Elixir-specific
extraction logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Uses tree-sitter-language-pack for grammar (elixir)
- Two-pass allows cross-file call resolution
- Multi-clause function support via global_symbols __multi__ index
- Phoenix routes and OTP callbacks integrated into the two-pass pipeline
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, UsageContext, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer

# Phoenix HTTP method macros for route detection
PHOENIX_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}

# OTP/Phoenix behaviour callback mappings.
# When a module does `use GenServer`, these callbacks may be implemented.
# The framework invokes them; without edges they appear as orphans.
BEHAVIOUR_CALLBACKS: dict[str, list[str]] = {
    "GenServer": [
        "init", "handle_call", "handle_cast", "handle_info",
        "terminate", "code_change", "handle_continue",
    ],
    "Supervisor": ["init"],
    "Agent": ["init"],
    "Task": ["run"],
    "Phoenix.LiveView": [
        "mount", "handle_event", "render", "handle_params",
        "handle_async", "handle_info", "terminate",
    ],
    "Phoenix.LiveComponent": ["mount", "update", "render", "handle_event"],
    "Phoenix.Component": ["render"],
    "Plug": ["init", "call"],
    "Plug.Builder": ["init", "call"],
    "Plug.Router": ["init", "call"],
}

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("elixir")


def find_elixir_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Elixir files in the repository."""
    yield from find_files(repo_root, ["*.ex", "*.exs"])




def _extract_alias_hints(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract alias and import directives for disambiguation.

    In Elixir:
        alias MyApp.Services.UserService -> UserService maps to MyApp.Services.UserService
        alias MyApp.Services.UserService, as: Svc -> Svc maps to MyApp.Services.UserService
        import MyApp.Math -> all functions from MyApp.Math are available

    Returns a dict mapping short names to full module paths.
    """
    hints: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "call":
            continue

        target = find_child_by_type(node, "identifier")
        if not target:  # pragma: no cover - call nodes always have identifier
            continue

        target_name = node_text(target, source)

        if target_name == "alias":
            args = find_child_by_type(node, "arguments")
            if not args:  # pragma: no cover - alias always has arguments
                continue

            # Find the module being aliased
            module_node = None
            for child in args.children:
                if child.type == "alias":
                    module_node = child
                    break

            if module_node:
                full_path = node_text(module_node, source)
                # Check for 'as:' option in keywords
                kw_node = find_child_by_type(args, "keywords")
                if kw_node:
                    # Look for as: Alias pattern
                    for pair in kw_node.children:
                        if pair.type == "pair":
                            key_node = find_child_by_type(pair, "keyword")
                            if key_node and node_text(key_node, source).strip().rstrip(":") == "as":
                                value_node = find_child_by_type(pair, "alias")
                                if value_node:
                                    alias_name = node_text(value_node, source)
                                    hints[alias_name] = full_path
                                    break
                    else:
                        # No as: found, use last component of module path
                        short_name = full_path.rsplit(".", 1)[-1]
                        hints[short_name] = full_path
                else:
                    # No keywords, use last component of module path
                    short_name = full_path.rsplit(".", 1)[-1]
                    hints[short_name] = full_path

        elif target_name == "import":
            args = find_child_by_type(node, "arguments")
            if args:
                for child in args.children:
                    if child.type == "alias":
                        full_path = node_text(child, source)
                        # For imports, use the full module name as hint
                        short_name = full_path.rsplit(".", 1)[-1]
                        hints[short_name] = full_path
                        break

    return hints


def _get_enclosing_modules(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Walk up the tree to find all enclosing module names, innermost first."""
    modules: list[str] = []
    current = node.parent
    while current is not None:
        if current.type == "call":
            target = find_child_by_type(current, "identifier")
            if target and node_text(target, source) == "defmodule":
                mod_name = _get_module_name_from_call(current, source)
                if mod_name:
                    modules.append(mod_name)
        current = current.parent
    return list(reversed(modules))  # Return outermost first


def _get_enclosing_function(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing function."""
    current = node.parent
    while current is not None:
        if current.type == "call":
            target = find_child_by_type(current, "identifier")
            if target:
                target_name = node_text(target, source)
                if target_name in ("def", "defp", "defmacro", "defmacrop"):
                    func_name = _get_function_name(current, source)
                    if func_name and func_name in local_symbols:
                        return local_symbols[func_name]
        current = current.parent
    return None  # pragma: no cover - defensive


def _get_module_name_from_call(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract module name from defmodule call node."""
    args = find_child_by_type(node, "arguments")
    if args:
        for child in args.children:
            if child.type == "alias":
                return node_text(child, source)
    return None


def _get_module_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract module name from defmodule call."""
    # defmodule has structure: (call target: (identifier "defmodule") arguments: (arguments (alias)))
    args = find_child_by_type(node, "arguments")
    if args:
        for child in args.children:
            if child.type == "alias":
                return node_text(child, source)
    return None


def _get_function_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function name from def/defp/defmacro call."""
    # def has structure: (call target: (identifier "def") arguments: (arguments (call target: (identifier "func_name") ...)))
    args = find_child_by_type(node, "arguments")
    if args:
        for child in args.children:
            if child.type == "call":
                # The function name is the target of this call
                target = find_child_by_type(child, "identifier")
                if target:
                    return node_text(target, source)
            elif child.type == "identifier":
                # Simple case: def foo, do: :ok
                return node_text(child, source)
    return None


def _extract_elixir_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from def/defp/defmacro call.

    Returns signature in format: (param1, param2, keyword: default)
    Elixir is dynamically typed, so no type annotations are included.
    """
    args = find_child_by_type(node, "arguments")
    if args is None:  # pragma: no cover - defensive
        return "()"

    for child in args.children:
        if child.type == "call":
            # def foo(a, b) - parameters are in the arguments of the inner call
            inner_args = find_child_by_type(child, "arguments")
            if inner_args is None:  # pragma: no cover - rare
                return "()"

            params: list[str] = []
            for param in inner_args.children:
                if param.type == "identifier":
                    # Simple positional parameter
                    params.append(node_text(param, source))
                elif param.type == "binary_operator":  # pragma: no cover - default vals
                    # Default value: param \\ default
                    left = None
                    for pc in param.children:
                        if pc.type == "identifier":
                            left = node_text(pc, source)
                            break
                    if left:
                        params.append(f"{left} \\\\ ...")
                elif param.type == "keywords":
                    # Keyword arguments
                    for kw_pair in param.children:
                        if kw_pair.type == "pair":
                            key_node = None
                            for pc in kw_pair.children:
                                if pc.type == "keyword":
                                    key_node = pc
                                    break
                            if key_node:
                                key_text = node_text(key_node, source).rstrip(":")
                                params.append(f"{key_text}: ...")

            return f"({', '.join(params)})"

        elif child.type == "identifier":
            # Simple case: def foo, do: :ok (no parameters)
            return "()"

    return "()"  # pragma: no cover - defensive


def _extract_phoenix_routes(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    symbol_by_name: dict[str, Symbol],
    run_id: str,
    pass_id: str,
) -> tuple[list[UsageContext], list[Symbol]]:
    """Extract UsageContext records AND Symbol objects for Phoenix router DSL calls.

    Detects patterns like:
    - get "/", PageController, :index
    - post "/users", UserController, :create
    - resources "/posts", PostController
    - live "/dashboard", DashboardLive
    - live "/users/:id", UserLive.Show, :show

    The ``live`` macro creates LiveView routes (WebSocket-based server-rendered
    views). These get ``http_method="LIVE"`` to distinguish them from traditional
    HTTP routes.  For handler linking, live routes without an explicit action
    default to ``action="mount"`` (the LiveView entry callback).

    Returns:
        Tuple of (UsageContext list, Symbol list) for YAML pattern matching.
        Symbols have kind="route" which enables route-handler linking.
    """
    contexts: list[UsageContext] = []
    route_symbols: list[Symbol] = []

    for n in iter_tree(node):
        if n.type != "call":
            continue

        # Get the function name (get, post, resources, etc.)
        target_node = find_child_by_type(n, "identifier")
        if not target_node:
            continue

        method_name = node_text(target_node, source).lower()

        # Check if it's a Phoenix route macro
        if (
            method_name not in PHOENIX_HTTP_METHODS
            and method_name not in ("resources", "live")
        ):
            continue

        # Find arguments
        args_node = find_child_by_type(n, "arguments")
        if not args_node:  # pragma: no cover
            continue

        route_path = None
        controller = None
        action = None

        # Parse arguments: path, Controller, :action
        arg_index = 0
        for child in args_node.children:
            if child.type in ("(", ")", ","):
                continue

            if arg_index == 0:
                # First arg is the path (string)
                if child.type == "string":
                    # Extract string content
                    for sc in child.children:
                        if sc.type == "quoted_content":
                            route_path = node_text(sc, source)
                            break
                    if not route_path:  # pragma: no cover
                        route_path = node_text(child, source).strip('"\'')
            elif arg_index == 1:
                # Second arg is the controller/module (alias, identifier, or
                # dotted alias like UserLive.Show)
                if child.type in ("alias", "dot"):
                    controller = node_text(child, source)
                elif child.type == "identifier":  # pragma: no cover
                    controller = node_text(child, source)
            elif arg_index == 2:
                # Third arg is the action (atom)
                if child.type == "atom":
                    action = node_text(child, source).lstrip(":")

            arg_index += 1

        if not route_path:  # pragma: no cover
            continue

        # Build metadata
        normalized_path = route_path if route_path.startswith("/") else f"/{route_path}"
        if method_name in PHOENIX_HTTP_METHODS:
            http_method = method_name.upper()
        elif method_name == "live":
            http_method = "LIVE"
        else:
            http_method = "RESOURCES"
        metadata: dict[str, str | None] = {
            "route_path": normalized_path,
            "http_method": http_method,
        }
        if controller:
            metadata["controller"] = controller
        if method_name == "live" and not action:
            # LiveView routes without explicit action default to "mount"
            # (the entry callback for all LiveView modules)
            action = "mount"
        if action:
            metadata["action"] = action

        # Create UsageContext
        span = Span(
            start_line=n.start_point[0] + 1,
            end_line=n.end_point[0] + 1,
            start_col=n.start_point[1],
            end_col=n.end_point[1],
        )

        ctx = UsageContext.create(
            kind="call",
            context_name=method_name,  # e.g., "get", "post", "resources"
            position="args[0]",
            path=str(file_path),
            span=span,
            symbol_ref=None,  # Router DSL doesn't directly reference symbols
            metadata=metadata,
        )
        contexts.append(ctx)

        # Create route Symbol(s) - enables route-handler linking
        if method_name == "resources":
            # Phoenix resources creates 7 RESTful routes (same as Rails)
            restful_routes = [
                ("GET", normalized_path, "index"),
                ("GET", f"{normalized_path}/new", "new"),
                ("POST", normalized_path, "create"),
                ("GET", f"{normalized_path}/:id", "show"),
                ("GET", f"{normalized_path}/:id/edit", "edit"),
                ("PATCH", f"{normalized_path}/:id", "update"),
                ("DELETE", f"{normalized_path}/:id", "delete"),  # Phoenix uses :delete
            ]
            for http_meth, route_pth, act in restful_routes:
                route_name = f"{http_meth} {route_pth}"
                route_id = make_symbol_id("elixir",
                    path=str(file_path),
                    start_line=span.start_line,
                    end_line=span.end_line,
                    name=route_name,
                    kind="route",
                )
                route_symbol = Symbol(
                    id=route_id,
                    name=route_name,
                    kind="route",
                    language="elixir",
                    path=str(file_path),
                    span=span,
                    meta={
                        "http_method": http_meth,
                        "route_path": route_pth,
                        "controller": controller,
                        "action": act,
                    },
                    origin=pass_id,
                    origin_run_id=run_id,
                )
                route_symbols.append(route_symbol)
        else:
            # Single route (HTTP method or LiveView)
            route_name = f"{http_method} {normalized_path}"
            route_id = make_symbol_id("elixir",
                path=str(file_path),
                start_line=span.start_line,
                end_line=span.end_line,
                name=route_name,
                kind="route",
            )
            route_symbol = Symbol(
                id=route_id,
                name=route_name,
                kind="route",
                language="elixir",
                path=str(file_path),
                span=span,
                meta={
                    "http_method": http_method,
                    "route_path": normalized_path,
                },
                origin=pass_id,
                origin_run_id=run_id,
            )
            if controller:
                route_symbol.meta["controller"] = controller
            if action:
                route_symbol.meta["action"] = action
            route_symbols.append(route_symbol)

    return contexts, route_symbols


def _extract_behaviour_callbacks(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    file_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run_id: str,
    file_symbols_multi: dict[str, list[Symbol]] | None = None,
    global_symbols_multi: dict[str, list[Symbol]] | None = None,
) -> list[Edge]:
    """Extract invokes_callback edges from OTP/Phoenix behaviour declarations.

    When a module uses `use GenServer`, `use Phoenix.LiveView`, etc., the framework
    invokes specific callback functions (init, handle_call, mount, render, ...).
    Without these edges, callback functions appear as orphans.

    Detects `use Module` directives, maps to known behaviours via BEHAVIOUR_CALLBACKS,
    and creates edges from the enclosing module to each implemented callback function.
    """
    edges: list[Edge] = []

    for node in iter_tree(tree.root_node):
        if node.type != "call":
            continue

        target = find_child_by_type(node, "identifier")
        if target is None or node_text(target, source) != "use":
            continue

        # Extract the used module name from arguments
        args = find_child_by_type(node, "arguments")
        if args is None:  # pragma: no cover — use always has arguments
            continue

        used_module = None
        for child in args.children:
            if child.type == "alias":
                used_module = node_text(child, source)
                break

        if used_module is None:  # pragma: no cover — use always takes alias arg
            continue

        # Check if used module matches a known behaviour
        expected_callbacks = BEHAVIOUR_CALLBACKS.get(used_module)
        if expected_callbacks is None:
            continue

        # Find the enclosing module
        enclosing_modules = _get_enclosing_modules(node, source)
        if not enclosing_modules:
            continue  # pragma: no cover — use is always inside a module

        module_name = ".".join(enclosing_modules)

        # Find the module symbol
        module_sym = file_symbols.get(module_name) or global_symbols.get(module_name)
        if module_sym is None:
            continue  # pragma: no cover — module always in file symbols

        # Create edges for each implemented callback (all clauses)
        for callback_name in expected_callbacks:
            qualified = f"{module_name}.{callback_name}"
            # Multi-clause: find all clauses of the callback function
            callees = (
                (file_symbols_multi.get(qualified) if file_symbols_multi else None)
                or (global_symbols_multi.get(qualified) if global_symbols_multi else None)
                or (file_symbols_multi.get(callback_name) if file_symbols_multi else None)
            )
            if not callees:
                # Fallback to single-match index
                single = (
                    file_symbols.get(qualified)
                    or global_symbols.get(qualified)
                    or file_symbols.get(callback_name)
                )
                callees = [single] if single else []
            if not callees:
                continue

            for callee in callees:
                edges.append(Edge.create(
                    src=module_sym.id,
                    dst=callee.id,
                    edge_type="invokes_callback",
                    line=node.start_point[0] + 1,
                    evidence_type="behaviour_callback",
                    confidence=0.9,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

    return edges


def _extract_symbols_from_tree(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> tuple[list[Symbol], dict[str, Symbol], dict[str, list[Symbol]]]:
    """Extract symbols from a parsed Elixir tree.

    Returns:
        Tuple of (symbols, symbol_by_name, symbols_by_name).
        symbol_by_name maps short names to the most recent symbol.
        symbols_by_name maps short names to ALL symbols (for multi-clause).
    """
    symbols: list[Symbol] = []
    symbol_by_name: dict[str, Symbol] = {}
    symbols_by_name: dict[str, list[Symbol]] = {}

    for node in iter_tree(tree.root_node):
        # Check for defmodule
        if node.type == "call":
            target = find_child_by_type(node, "identifier")
            if target:
                target_name = node_text(target, source)

                if target_name == "defmodule":
                    module_name = _get_module_name(node, source)
                    if module_name:
                        # Handle nested modules by walking up
                        enclosing_modules = _get_enclosing_modules(node, source)
                        if enclosing_modules:
                            full_name = f"{'.'.join(enclosing_modules)}.{module_name}"
                        else:
                            full_name = module_name

                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1

                        symbol = Symbol(
                            id=make_symbol_id("elixir", file_path, start_line, end_line, full_name, "module"),
                            name=full_name,
                            kind="module",
                            language="elixir",
                            path=file_path,
                            span=Span(
                                start_line=start_line,
                                end_line=end_line,
                                start_col=node.start_point[1],
                                end_col=node.end_point[1],
                            ),
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        )
                        symbols.append(symbol)
                        symbol_by_name[full_name] = symbol

                elif target_name in ("def", "defp"):
                    func_name = _get_function_name(node, source)
                    if func_name:
                        enclosing_modules = _get_enclosing_modules(node, source)
                        current_module = ".".join(enclosing_modules) if enclosing_modules else ""
                        full_name = f"{current_module}.{func_name}" if current_module else func_name

                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1

                        # defp = private function
                        modifiers = ["private"] if target_name == "defp" else []

                        symbol = Symbol(
                            id=make_symbol_id("elixir", file_path, start_line, end_line, full_name, "function"),
                            name=full_name,
                            kind="function",
                            language="elixir",
                            path=file_path,
                            span=Span(
                                start_line=start_line,
                                end_line=end_line,
                                start_col=node.start_point[1],
                                end_col=node.end_point[1],
                            ),
                            origin=PASS_ID,
                            origin_run_id=run_id,
                            signature=_extract_elixir_signature(node, source),
                            modifiers=modifiers,
                        )
                        symbols.append(symbol)
                        symbol_by_name[func_name] = symbol  # Store by short name for local calls
                        # Multi-clause index: all symbols with the same short name
                        symbols_by_name.setdefault(func_name, []).append(symbol)

                elif target_name in ("defmacro", "defmacrop"):
                    macro_name = _get_function_name(node, source)
                    if macro_name:
                        enclosing_modules = _get_enclosing_modules(node, source)
                        current_module = ".".join(enclosing_modules) if enclosing_modules else ""
                        full_name = f"{current_module}.{macro_name}" if current_module else macro_name

                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1

                        # defmacrop = private macro
                        modifiers = ["private"] if target_name == "defmacrop" else []

                        symbol = Symbol(
                            id=make_symbol_id("elixir", file_path, start_line, end_line, full_name, "macro"),
                            name=full_name,
                            kind="macro",
                            language="elixir",
                            path=file_path,
                            span=Span(
                                start_line=start_line,
                                end_line=end_line,
                                start_col=node.start_point[1],
                                end_col=node.end_point[1],
                            ),
                            origin=PASS_ID,
                            origin_run_id=run_id,
                            signature=_extract_elixir_signature(node, source),
                            modifiers=modifiers,
                        )
                        symbols.append(symbol)
                        symbol_by_name[macro_name] = symbol

    return symbols, symbol_by_name, symbols_by_name


def _extract_edges_from_tree(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run_id: str,
    resolver: "NameResolver",
    alias_hints: dict[str, str] | None = None,
    local_symbols_multi: dict[str, list[Symbol]] | None = None,
    global_symbols_multi: dict[str, list[Symbol]] | None = None,
) -> list[Edge]:
    """Extract call and import edges from a parsed Elixir tree.

    Args:
        alias_hints: Optional dict mapping short names to full module paths for disambiguation.
    """
    if alias_hints is None:  # pragma: no cover - defensive default
        alias_hints = {}

    edges: list[Edge] = []
    file_id = make_file_id("elixir", file_path)

    for node in iter_tree(tree.root_node):
        if node.type == "call":
            target = find_child_by_type(node, "identifier")
            if target:
                target_name = node_text(target, source)

                # Detect use/import/alias directives
                if target_name == "use":
                    args = find_child_by_type(node, "arguments")
                    if args:
                        for child in args.children:
                            if child.type == "alias":
                                module_name = node_text(child, source)
                                edges.append(Edge.create(
                                    src=file_id,
                                    dst=f"elixir:{module_name}:0-0:module:module",
                                    edge_type="imports",
                                    line=node.start_point[0] + 1,
                                    evidence_type="use_directive",
                                    confidence=0.95,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                ))

                elif target_name == "import":
                    args = find_child_by_type(node, "arguments")
                    if args:
                        for child in args.children:
                            if child.type == "alias":
                                module_name = node_text(child, source)
                                edges.append(Edge.create(
                                    src=file_id,
                                    dst=f"elixir:{module_name}:0-0:module:module",
                                    edge_type="imports",
                                    line=node.start_point[0] + 1,
                                    evidence_type="import_directive",
                                    confidence=0.95,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                ))

                # Detect function calls within a function body
                elif target_name not in ("def", "defp", "defmacro", "defmacrop", "defmodule"):
                    current_function = _get_enclosing_function(node, source, local_symbols)
                    if current_function is not None:
                        # Multi-clause: check local file for all clauses with this name
                        local_multi = local_symbols_multi.get(target_name) if local_symbols_multi else None
                        if local_multi:
                            for callee in local_multi:
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=callee.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="function_call",
                                    confidence=0.85,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                ))
                        # Fallback: single-match local lookup (modules, macros)
                        elif target_name in local_symbols:  # pragma: no cover — defensive; multi index covers all symbols
                            callee = local_symbols[target_name]
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="function_call",
                                confidence=0.85,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                            ))
                        # Cross-file: multi-clause global lookup, then resolver
                        else:
                            global_multi = global_symbols_multi.get(target_name) if global_symbols_multi else None
                            if global_multi:
                                for callee in global_multi:
                                    edges.append(Edge.create(
                                        src=current_function.id,
                                        dst=callee.id,
                                        edge_type="calls",
                                        line=node.start_point[0] + 1,
                                        evidence_type="function_call",
                                        confidence=0.80,
                                        origin=PASS_ID,
                                        origin_run_id=run_id,
                                    ))
                            else:  # pragma: no cover — multi-index has same keys as resolver
                                # Use alias hints for disambiguation
                                path_hint = alias_hints.get(target_name)
                                lookup_result = resolver.lookup(target_name, path_hint=path_hint)
                                if lookup_result.found and lookup_result.symbol is not None:
                                    edges.append(Edge.create(
                                        src=current_function.id,
                                        dst=lookup_result.symbol.id,
                                        edge_type="calls",
                                        line=node.start_point[0] + 1,
                                        evidence_type="function_call",
                                        confidence=0.80 * lookup_result.confidence,
                                        origin=PASS_ID,
                                        origin_run_id=run_id,
                                    ))

            # Module-qualified calls: Helper.greet(), App.Services.UserService.find()
            # AST: call -> dot -> (alias, ".", identifier)
            else:
                dot_node = find_child_by_type(node, "dot")
                if dot_node:
                    _handle_dot_call(
                        node, dot_node, source, local_symbols,
                        global_symbols, resolver, alias_hints, edges, run_id,
                    )

    return edges


def _handle_dot_call(
    call_node: "tree_sitter.Node",
    dot_node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    resolver: "NameResolver",
    alias_hints: dict[str, str],
    edges: list[Edge],
    run_id: str,
) -> None:
    """Handle module-qualified calls like Helper.greet() or App.Module.func().

    Extracts the module alias and function name from a dot node, then resolves
    the function using the module-qualified name (e.g., "Helper.greet") against
    the global symbol registry.
    """
    # Extract module alias and function name from dot node
    alias_node = find_child_by_type(dot_node, "alias")
    func_id_node = find_child_by_type(dot_node, "identifier")
    if alias_node is None or func_id_node is None:
        return

    module_name = node_text(alias_node, source)
    func_name = node_text(func_id_node, source)

    # Find the enclosing function (caller)
    current_function = _get_enclosing_function(call_node, source, local_symbols)
    if current_function is None:
        return

    # Build the fully-qualified function name: Module.func_name
    qualified_name = f"{module_name}.{func_name}"

    # Try to resolve the qualified name in global symbols
    if qualified_name in global_symbols:
        callee = global_symbols[qualified_name]
        edges.append(Edge.create(
            src=current_function.id,
            dst=callee.id,
            edge_type="calls",
            line=call_node.start_point[0] + 1,
            evidence_type="module_qualified_call",
            confidence=0.90,
            origin=PASS_ID,
            origin_run_id=run_id,
        ))
        return

    # Try with alias hints: if module_name is an alias, resolve to full path
    if module_name in alias_hints:
        full_module = alias_hints[module_name]
        full_qualified = f"{full_module}.{func_name}"
        if full_qualified in global_symbols:
            callee = global_symbols[full_qualified]
            edges.append(Edge.create(
                src=current_function.id,
                dst=callee.id,
                edge_type="calls",
                line=call_node.start_point[0] + 1,
                evidence_type="module_qualified_call",
                confidence=0.85,
                origin=PASS_ID,
                origin_run_id=run_id,
            ))
            return

    # Try resolver lookup with the function name and module as path hint
    path_hint = alias_hints.get(module_name, module_name)
    lookup_result = resolver.lookup(func_name, path_hint=path_hint)
    if lookup_result.found and lookup_result.symbol is not None:
        edges.append(Edge.create(
            src=current_function.id,
            dst=lookup_result.symbol.id,
            edge_type="calls",
            line=call_node.start_point[0] + 1,
            evidence_type="module_qualified_call",
            confidence=0.75 * lookup_result.confidence,
            origin=PASS_ID,
            origin_run_id=run_id,
        ))
        return

    # Fallback: create an unresolved edge for cross-module calls
    # This allows linkers to match across files/languages
    dst_id = f"elixir:{module_name}:0-0:{func_name}:unresolved"
    edges.append(Edge.create(
        src=current_function.id,
        dst=dst_id,
        edge_type="calls",
        line=call_node.start_point[0] + 1,
        evidence_type="unresolved_module_call",
        confidence=0.50,
        origin=PASS_ID,
        origin_run_id=run_id,
    ))


class ElixirAnalyzer(TreeSitterAnalyzer):
    """Elixir language analyzer using tree-sitter-language-pack.

    Handles multi-clause functions (same name, different patterns),
    Phoenix routes, OTP/Phoenix behaviour callbacks, and
    module-qualified calls with alias resolution.

    The multi-clause index is stored in global_symbols under a ``__multi__``
    key to enable multi-clause edge resolution.
    """

    lang = "elixir"
    file_patterns: ClassVar[list[str]] = ["*.ex", "*.exs"]
    language_pack_name = "elixir"
    create_file_symbols = False

    def register_symbol(
        self,
        symbol: Symbol,
        global_symbols: dict,
    ) -> None:
        """Register symbol with both short and full names for cross-file resolution.

        Also builds a multi-clause index under ``__multi__`` key.
        """
        short_name = symbol.name.split(".")[-1] if "." in symbol.name else symbol.name
        global_symbols[short_name] = symbol
        global_symbols[symbol.name] = symbol
        multi: dict[str, list[Symbol]] = global_symbols.setdefault("__multi__", {})
        multi.setdefault(short_name, []).append(symbol)
        multi.setdefault(symbol.name, []).append(symbol)

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract symbols from an Elixir file."""
        # Stash run_id for extract_usage_contexts_from_file
        self._current_run_id = run.execution_id
        analysis = FileAnalysis()
        symbols, symbol_by_name, _symbols_by_name = _extract_symbols_from_tree(
            tree, source, rel_path, run.execution_id,
        )
        analysis.symbols = symbols
        analysis.symbol_by_name = symbol_by_name
        return analysis

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract alias hints for disambiguation."""
        return _extract_alias_hints(tree, source)

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call, import, module-qualified call, and behaviour callback edges."""
        global_multi: dict[str, list[Symbol]] | None = global_symbols.get("__multi__")

        # Reconstruct local multi from global multi filtered to this file.
        # We filter by path (not by local_symbols IDs) because local_symbols
        # is a dict keyed by name, so multi-clause functions (3 handle_call
        # clauses) only have one entry. Filtering by path captures all clauses.
        local_symbols_multi: dict[str, list[Symbol]] | None = None
        if global_multi:
            local_symbols_multi = {}
            for name, sym_list in global_multi.items():
                local_matches = [s for s in sym_list if s.path == rel_path]
                if local_matches:
                    local_symbols_multi[name] = local_matches

        edges = _extract_edges_from_tree(
            tree, source, rel_path, local_symbols, global_symbols,
            run.execution_id, resolver,
            alias_hints=import_aliases,
            local_symbols_multi=local_symbols_multi,
            global_symbols_multi=global_multi,
        )

        # Behaviour callback edges (use GenServer, use Phoenix.LiveView, etc.)
        callback_edges = _extract_behaviour_callbacks(
            tree, source, file_path, local_symbols, global_symbols,
            run.execution_id,
            file_symbols_multi=local_symbols_multi,
            global_symbols_multi=global_multi,
        )
        edges.extend(callback_edges)

        return edges

    def extract_usage_contexts_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, symbol_by_name: dict[str, Symbol],
    ) -> list[UsageContext]:
        """Extract Phoenix route usage contexts and stash route symbols."""
        run_id = getattr(self, "_current_run_id", "")
        usage_contexts, route_symbols = _extract_phoenix_routes(
            tree.root_node, source, file_path, symbol_by_name,
            run_id, self.pass_id,
        )
        # Stash route symbols to be added in post_process
        self._pending_route_symbols.extend(route_symbols)
        return usage_contexts

    def post_process(
        self,
        symbols: list[Symbol],
        edges: list[Edge],
        usage_contexts: list[UsageContext],
        run: "AnalysisRun",
    ) -> tuple[list[Symbol], list[Edge], list[UsageContext]]:
        """Add stashed route symbols to the final result."""
        symbols.extend(self._pending_route_symbols)
        return symbols, edges, usage_contexts

    def analyze(
        self,
        repo_root: Path,
        max_files: int | None = None,
    ) -> AnalysisResult:
        """Override to initialize pending route symbols before analysis."""
        self._pending_route_symbols: list[Symbol] = []
        self._current_run_id = ""
        return super().analyze(repo_root, max_files=max_files)


_analyzer = ElixirAnalyzer()


def is_elixir_tree_sitter_available() -> bool:
    """Check if tree-sitter with Elixir grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("elixir")
def analyze_elixir(repo_root: Path) -> AnalysisResult:
    """Analyze all Elixir files in a repository.

    Returns an AnalysisResult with symbols, edges, and provenance.
    If tree-sitter-elixir is not available, returns a skipped result.
    """
    return _analyzer.analyze(repo_root)
