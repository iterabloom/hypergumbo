"""Ruby analysis pass using tree-sitter-ruby.

This analyzer uses tree-sitter to parse Ruby files and extract:
- Method definitions (def)
- Class declarations (class)
- Module declarations (module)
- Method call relationships
- Require/require_relative statements
- Rails callback edges (before_action, after_action, around_action)

If tree-sitter with Ruby support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-ruby is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect method calls and require statements

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-ruby package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as Go/Rust/Elixir/Java/PHP/C analyzers for consistency
"""
from __future__ import annotations

import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol, UsageContext
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.base import iter_tree
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "ruby-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# HTTP methods for Rails/Sinatra route detection (used by UsageContext extraction)
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "match"}

# Rails callback methods that create implicit call edges from controller to method.
# Includes modern (before_action) and legacy (before_filter) Rails API names.
RAILS_CALLBACK_METHODS = frozenset({
    "before_action", "after_action", "around_action",
    "before_filter", "after_filter", "around_filter",
})

# Directories that contain test files where get/post/delete are HTTP test helpers,
# not route definitions.
_TEST_DIR_NAMES = frozenset({"spec", "test", "tests", "features", "test-suite"})


def _is_test_directory(file_path: Path, repo_root: Path) -> bool:
    """Check if file is inside a test/spec directory.

    Returns True for files under spec/, test/, tests/, features/, test-suite/.
    These directories use HTTP method helpers (get, post, delete) for making
    test requests, NOT for defining routes.
    """
    try:
        rel = file_path.relative_to(repo_root)
    except ValueError:  # pragma: no cover — files always under repo_root
        return False  # pragma: no cover
    return bool(_TEST_DIR_NAMES & set(rel.parts))


def _is_route_definition_file(file_path: Path, repo_root: Path) -> bool:
    """Check if file is a route definition file.

    Returns True for:
    - Files named routes.rb (e.g., config/routes.rb)
    - Files inside a routes/ directory (e.g., config/routes/api.rb)

    Rails routes are defined in config/routes.rb and optionally split into
    config/routes/*.rb partials. Matching by name/directory catches both
    standard and non-standard layouts.
    """
    if file_path.name == "routes.rb":
        return True
    try:
        rel = file_path.relative_to(repo_root)
    except ValueError:  # pragma: no cover — files always under repo_root
        return False  # pragma: no cover
    return "routes" in rel.parts


def find_ruby_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Ruby files in the repository."""
    yield from find_files(repo_root, ["*.rb"])


def is_ruby_tree_sitter_available() -> bool:
    """Check if tree-sitter with Ruby grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec("tree_sitter_ruby") is None:
        return False
    return True


@dataclass
class RubyAnalysisResult:
    """Result of analyzing Ruby files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    usage_contexts: list[UsageContext] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"ruby:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Ruby file node (used as import edge source)."""
    return f"ruby:{path}:1-1:file:file"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _find_child_by_field(node: "tree_sitter.Node", field_name: str) -> Optional["tree_sitter.Node"]:
    """Find child by field name."""
    return node.child_by_field_name(field_name)


def _snake_to_pascal(name: str) -> str:
    """Convert snake_case to PascalCase.

    Examples:
        user_service -> UserService
        http_client -> HttpClient
        api -> Api
    """
    return "".join(word.capitalize() for word in name.split("_"))


def _extract_require_hints(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract require/require_relative statements and infer class/module names.

    Ruby convention maps snake_case file paths to PascalCase class names.
    For example:
        require 'user_service' -> hints that UserService class comes from this path
        require 'math/calculator' -> hints that Calculator class comes from this path

    Returns a dict mapping inferred class/module names to their require paths.
    """
    hints: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "call":
            continue

        # Get method name
        method_node = None
        for child in node.children:
            if child.type == "identifier":
                method_node = child
                break

        if not method_node:  # pragma: no cover - call nodes always have identifier
            continue

        callee_name = _node_text(method_node, source)
        if callee_name not in ("require", "require_relative"):
            continue

        # Extract the require path from arguments
        args_node = _find_child_by_field(node, "arguments")
        if not args_node:  # pragma: no cover - require always has arguments
            continue

        for arg in args_node.children:
            if arg.type == "string":
                content_node = _find_child_by_type(arg, "string_content")
                if content_node:
                    require_path = _node_text(content_node, source)
                    # Extract the last component and convert to PascalCase
                    # 'math/calculator' -> 'calculator' -> 'Calculator'
                    basename = require_path.rsplit("/", 1)[-1]
                    # Remove .rb extension if present
                    if basename.endswith(".rb"):
                        basename = basename[:-3]
                    if basename:
                        class_name = _snake_to_pascal(basename)
                        hints[class_name] = require_path

    return hints


def _get_enclosing_class_or_module(node: "tree_sitter.Node", source: bytes) -> tuple[Optional[str], str]:
    """Walk up the tree to find the enclosing class or module name.

    Returns (name, type) where type is 'class' or 'module'.
    """
    current = node.parent
    while current is not None:
        if current.type == "class":
            name_node = _find_child_by_field(current, "name")
            if name_node:
                return _node_text(name_node, source), "class"
        elif current.type == "module":
            name_node = _find_child_by_field(current, "name")
            if name_node:
                return _node_text(name_node, source), "module"
        current = current.parent
    return None, ""  # pragma: no cover - defensive


def _get_enclosing_method(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing method (instance or class method)."""
    current = node.parent
    while current is not None:
        if current.type in ("method", "singleton_method"):
            name_node = _find_child_by_field(current, "name")
            if name_node:
                method_name = _node_text(name_node, source)
                if method_name in local_symbols:
                    return local_symbols[method_name]
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_ruby_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract method signature from a method node.

    Returns signature in format: (param, param2 = ..., keyword:, &block)
    Ruby is dynamically typed, so no type annotations are included.
    """
    params: list[str] = []

    # Find parameters node
    params_node = _find_child_by_field(node, "parameters")
    if params_node is None:
        return "()"

    for child in params_node.children:
        if child.type == "identifier":
            # Simple positional parameter
            params.append(_node_text(child, source))
        elif child.type == "optional_parameter":
            # Parameter with default value: name = value
            name_node = _find_child_by_type(child, "identifier")
            if name_node:
                param_name = _node_text(name_node, source)
                params.append(f"{param_name} = ...")
        elif child.type == "keyword_parameter":
            # Keyword parameter: name: or name: value
            name_node = _find_child_by_type(child, "identifier")
            if name_node:
                param_name = _node_text(name_node, source)
                # Check if it has a default value (look for value node after identifier)
                has_value = False
                for pc in child.children:
                    # Skip the identifier and punctuation - look for an actual value
                    if pc.type not in ("identifier", ":"):
                        has_value = True
                        break
                if has_value:
                    params.append(f"{param_name}: ...")
                else:
                    params.append(f"{param_name}:")
        elif child.type == "splat_parameter":
            # *args
            name_node = _find_child_by_type(child, "identifier")
            if name_node:
                param_name = _node_text(name_node, source)
                params.append(f"*{param_name}")
            else:
                params.append("*")  # pragma: no cover - bare splat
        elif child.type == "hash_splat_parameter":
            # **kwargs
            name_node = _find_child_by_type(child, "identifier")
            if name_node:
                param_name = _node_text(name_node, source)
                params.append(f"**{param_name}")
            else:
                params.append("**")  # pragma: no cover - bare hash splat
        elif child.type == "block_parameter":
            # &block
            name_node = _find_child_by_type(child, "identifier")
            if name_node:
                param_name = _node_text(name_node, source)
                params.append(f"&{param_name}")

    params_str = ", ".join(params)
    return f"({params_str})"


def _extract_rails_routes(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    symbol_by_name: dict[str, Symbol],
    run: AnalysisRun,
) -> tuple[list[UsageContext], list[Symbol]]:
    """Extract UsageContext records AND Symbol objects for Rails/Sinatra route DSL calls.

    Detects patterns like:
    - Rails: get '/users', to: 'users#index'
    - Rails: post '/login', to: 'sessions#create'
    - Rails: resources :users, resource :profile
    - Sinatra: get '/path' do ... end
    - Sinatra: post '/users' do ... end

    The has_block metadata field distinguishes Sinatra (with block) from Rails (with to: option).

    Returns:
        Tuple of (UsageContext list, Symbol list) for YAML pattern matching.
        Symbols have kind="route" which matches rails.yaml symbol_kind pattern.
    """
    contexts: list[UsageContext] = []
    route_symbols: list[Symbol] = []

    for n in iter_tree(node):
        if n.type != "call":
            continue

        # Get the method name
        method_node = None
        for child in n.children:
            if child.type == "identifier":
                method_node = child
                break

        if method_node is None:  # pragma: no cover
            continue

        method_name = _node_text(method_node, source).lower()

        # Check if it's an HTTP method route or resources
        if method_name not in HTTP_METHODS and method_name not in ("resources", "resource"):
            continue

        # Extract route path from first argument
        args_node = _find_child_by_field(n, "arguments")
        if not args_node:  # pragma: no cover
            continue

        route_path = None
        controller_action = None

        for arg in args_node.children:
            # String path for HTTP method routes
            if arg.type == "string" and method_name in HTTP_METHODS:
                content_node = _find_child_by_type(arg, "string_content")
                if content_node:
                    route_path = _node_text(content_node, source)
                    break
            # Symbol for resources/resource
            elif arg.type == "simple_symbol" and method_name in ("resources", "resource"):
                route_path = _node_text(arg, source).strip(":")
                break
            # Handle "path" => "controller#action" syntax (pair with string key)
            elif arg.type == "pair" and method_name in HTTP_METHODS:
                # Extract key (path) and value (controller#action) from pair
                pair_children = list(arg.children)
                if len(pair_children) >= 2:
                    key_node = pair_children[0]
                    val_node = pair_children[-1]  # Last child is value
                    if key_node.type == "string":
                        key_content = _find_child_by_type(key_node, "string_content")
                        if key_content:
                            route_path = _node_text(key_content, source)
                    if val_node.type == "string":
                        val_content = _find_child_by_type(val_node, "string_content")
                        if val_content:
                            controller_action = _node_text(val_content, source)
                if route_path:
                    break

        if not route_path:  # pragma: no cover
            continue

        # Try to extract controller#action from 'to:' option (e.g., match "path", to: "ctrl#act")
        for arg in args_node.children:
            if arg.type == "pair":
                for pair_child in arg.children:
                    if pair_child.type in ("hash_key_symbol", "simple_symbol"):
                        key_text = _node_text(pair_child, source).strip(":")
                        if key_text == "to":
                            for sibling in arg.children:
                                if sibling.type == "string":
                                    content = _find_child_by_type(sibling, "string_content")
                                    if content:
                                        controller_action = _node_text(content, source)

        # Check for block (Sinatra style: get '/path' do ... end)
        has_block = False
        for child in n.children:
            if child.type in ("do_block", "block"):
                has_block = True
                break

        # Build metadata
        http_method = method_name.upper() if method_name in HTTP_METHODS else "RESOURCES"
        metadata: dict[str, str | bool] = {
            "route_path": route_path,
            "http_method": http_method,
            "has_block": has_block,  # True for Sinatra-style, False for Rails-style
        }
        if controller_action:
            metadata["controller_action"] = controller_action
        elif method_name in ("resources", "resource"):
            # For resources :users, infer controller_action from resource name
            # Rails convention: resources :users → UsersController#index (primary entry)
            # This enables route-handler linking for resource routes
            metadata["controller_action"] = f"{route_path}#index"

        # Create span
        span = Span(
            start_line=n.start_point[0] + 1,
            end_line=n.end_point[0] + 1,
            start_col=n.start_point[1],
            end_col=n.end_point[1],
        )

        # Create UsageContext (for backwards compatibility)
        ctx = UsageContext.create(
            kind="call",
            context_name=method_name,  # e.g., "get", "post", "resources"
            position="args[0]",
            path=str(file_path),
            span=span,
            symbol_ref=None,  # Route DSL doesn't reference a handler symbol directly
            metadata=metadata,
        )
        contexts.append(ctx)

        # Create route Symbol(s) (kind="route" matches rails.yaml pattern)
        # This enables route detection and entrypoint detection for Rails apps
        normalized_path = route_path if route_path.startswith("/") else f"/{route_path}"

        # For resources/resource, expand into all RESTful routes
        # This enables route-handler linking for all controller actions
        if method_name == "resources":
            # resources :users creates 7 RESTful routes
            restful_routes = [
                ("GET", normalized_path, "index"),  # GET /users
                ("GET", f"{normalized_path}/new", "new"),  # GET /users/new
                ("POST", normalized_path, "create"),  # POST /users
                ("GET", f"{normalized_path}/:id", "show"),  # GET /users/:id
                ("GET", f"{normalized_path}/:id/edit", "edit"),  # GET /users/:id/edit
                ("PATCH", f"{normalized_path}/:id", "update"),  # PATCH /users/:id
                ("DELETE", f"{normalized_path}/:id", "destroy"),  # DELETE /users/:id
            ]
            # Controller name from resource (users → users)
            controller_name = route_path
            for http_meth, route_pth, action in restful_routes:
                route_name = f"{http_meth} {route_pth}"
                route_id = _make_symbol_id(
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
                    language="ruby",
                    path=str(file_path),
                    span=span,
                    meta={
                        "http_method": http_meth,
                        "route_path": route_pth,
                        "controller_action": f"{controller_name}#{action}",
                    },
                    origin=run.pass_id,
                    origin_run_id=run.execution_id,
                )
                route_symbols.append(route_symbol)
        elif method_name == "resource":
            # resource :profile creates 6 RESTful routes (no index)
            # Singular resource uses singular path but plural controller
            restful_routes = [
                ("GET", normalized_path, "show"),  # GET /profile
                ("GET", f"{normalized_path}/new", "new"),  # GET /profile/new
                ("POST", normalized_path, "create"),  # POST /profile
                ("GET", f"{normalized_path}/edit", "edit"),  # GET /profile/edit
                ("PATCH", normalized_path, "update"),  # PATCH /profile
                ("DELETE", normalized_path, "destroy"),  # DELETE /profile
            ]
            # Rails convention: resource :profile → ProfilesController (pluralized)
            # Don't double the 's' if name already ends in 's' (audit_logs, settings)
            controller_name = route_path if route_path.endswith("s") else f"{route_path}s"
            for http_meth, route_pth, action in restful_routes:
                route_name = f"{http_meth} {route_pth}"
                route_id = _make_symbol_id(
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
                    language="ruby",
                    path=str(file_path),
                    span=span,
                    meta={
                        "http_method": http_meth,
                        "route_path": route_pth,
                        "controller_action": f"{controller_name}#{action}",
                    },
                    origin=run.pass_id,
                    origin_run_id=run.execution_id,
                )
                route_symbols.append(route_symbol)
        else:
            # Regular HTTP method route (get, post, etc.)
            route_name = f"{http_method} {normalized_path}"
            route_id = _make_symbol_id(
                path=str(file_path),
                start_line=span.start_line,
                end_line=span.end_line,
                name=route_name,
                kind="route",
            )
            route_meta: dict[str, str | bool] = {
                "http_method": http_method,
                "route_path": normalized_path,
                "has_block": has_block,
            }
            if controller_action:
                route_meta["controller_action"] = controller_action
            route_symbol = Symbol(
                id=route_id,
                name=route_name,
                kind="route",
                language="ruby",
                path=str(file_path),
                span=span,
                meta=route_meta,
                origin=run.pass_id,
                origin_run_id=run.execution_id,
            )
            route_symbols.append(route_symbol)

    return contexts, route_symbols


def _extract_rails_callbacks(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    file_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    resolver: NameResolver,
    run: AnalysisRun,
) -> list[Edge]:
    """Extract invokes_callback edges from Rails controller callback declarations.

    Rails controllers declare callbacks at class level:
        before_action :authenticate!
        after_action :log_request, :track_metrics
        around_action :measure_time

    These create implicit calls from the controller class to the named method.
    The callback method may be in the same class (private method) or inherited
    from a parent class (e.g., ApplicationController#authenticate!).

    Also handles legacy Rails 3 API: before_filter, after_filter, around_filter.

    Returns edges with type 'invokes_callback' from the class symbol to each
    callback method symbol.
    """
    edges: list[Edge] = []

    for node in iter_tree(tree.root_node):
        if node.type != "call":
            continue

        # Get method name via the 'method' field
        method_node = node.child_by_field_name("method")
        if method_node is None:  # pragma: no cover
            continue

        method_name = _node_text(method_node, source)
        if method_name not in RAILS_CALLBACK_METHODS:
            continue

        # Must be at class body level (not inside a method)
        enclosing_class_name, enclosing_type = _get_enclosing_class_or_module(node, source)
        if enclosing_type != "class" or enclosing_class_name is None:
            continue

        # Must not be inside a method (class-level declaration)
        current = node.parent
        inside_method = False
        while current is not None:
            if current.type == "method":
                inside_method = True
                break
            if current.type in ("class", "module"):
                break
            current = current.parent
        if inside_method:
            continue  # pragma: no cover — unusual but defensive

        # Find the class symbol
        class_sym = file_symbols.get(enclosing_class_name)
        if class_sym is None:  # pragma: no cover — class always in file symbols
            continue

        # Extract callback method names from symbol arguments
        args_node = _find_child_by_field(node, "arguments")
        if args_node is None:  # pragma: no cover — callbacks always have arguments
            continue

        for arg in args_node.children:
            if arg.type != "simple_symbol":
                continue

            callback_name = _node_text(arg, source).lstrip(":")

            # Resolve the callback method:
            # 1. Try qualified name in same class (ClassName#callback)
            qualified = f"{enclosing_class_name}#{callback_name}"
            callee = file_symbols.get(qualified) or global_symbols.get(qualified)

            # 2. Try bare name in file-local symbols
            if callee is None:
                callee = file_symbols.get(callback_name)

            # 3. Try bare name in global symbols (parent class)
            if callee is None:
                callee = global_symbols.get(callback_name)

            # 4. Try resolver for fuzzy matching (steps 1-3 cover all normal cases;
            #    this handles edge cases where method name differs from symbol key)
            if callee is None:  # pragma: no cover — steps 1-3 resolve all known patterns
                lookup = resolver.lookup(callback_name)
                if lookup.found and lookup.symbol is not None:
                    callee = lookup.symbol

            if callee is None:
                continue

            edges.append(Edge.create(
                src=class_sym.id,
                dst=callee.id,
                edge_type="invokes_callback",
                line=node.start_point[0] + 1,
                evidence_type="rails_callback",
                confidence=0.9,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
            ))

    return edges


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file."""

    symbols: list[Symbol] = field(default_factory=list)
    symbol_by_name: dict[str, Symbol] = field(default_factory=dict)
    require_hints: dict[str, str] = field(default_factory=dict)


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
) -> FileAnalysis:
    """Extract symbols from a single Ruby file."""
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return FileAnalysis()

    analysis = FileAnalysis()

    for node in iter_tree(tree.root_node):
        # Method definition (instance methods)
        if node.type == "method":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                method_name = _node_text(name_node, source)
                # Qualify with class/module name if inside one
                enclosing_name, enclosing_type = _get_enclosing_class_or_module(node, source)
                if enclosing_type == "class" and enclosing_name:
                    full_name = f"{enclosing_name}#{method_name}"
                elif enclosing_type == "module" and enclosing_name:
                    full_name = f"{enclosing_name}.{method_name}"
                else:
                    full_name = method_name

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, full_name, "method"),
                    name=full_name,
                    kind="method",
                    language="ruby",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    signature=_extract_ruby_signature(node, source),
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[method_name] = symbol
                analysis.symbol_by_name[full_name] = symbol

        # Singleton method definition (class methods: def self.method_name)
        elif node.type == "singleton_method":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                method_name = _node_text(name_node, source)
                # Class methods use dot separator: ClassName.method_name
                enclosing_name, _ = _get_enclosing_class_or_module(node, source)
                if enclosing_name:
                    full_name = f"{enclosing_name}.{method_name}"
                else:
                    full_name = method_name

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, full_name, "method"),
                    name=full_name,
                    kind="method",
                    language="ruby",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    signature=_extract_ruby_signature(node, source),
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[method_name] = symbol
                analysis.symbol_by_name[full_name] = symbol

        # Class definition
        elif node.type == "class":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                class_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract superclass if present (META-001)
                meta: dict[str, object] | None = None
                superclass_node = _find_child_by_field(node, "superclass")
                if superclass_node:
                    # The superclass node contains a child with the base class name
                    # For "class User < BaseModel": superclass has constant "BaseModel"
                    # For "class User < ActiveRecord::Base": superclass has scope_resolution
                    # We take the first named child which is the actual type reference
                    for child in superclass_node.children:
                        if child.is_named:
                            superclass_name = _node_text(child, source)
                            meta = {"base_classes": [superclass_name]}
                            break

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, class_name, "class"),
                    name=class_name,
                    kind="class",
                    language="ruby",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta=meta,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[class_name] = symbol

        # Module definition
        elif node.type == "module":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                module_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, module_name, "module"),
                    name=module_name,
                    kind="module",
                    language="ruby",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[module_name] = symbol

    # Extract require hints for disambiguation
    analysis.require_hints = _extract_require_hints(tree, source)

    return analysis


def _try_receiver_call(
    receiver_node: "tree_sitter.Node",
    method_name: str,
    source: bytes,
    current_method: Symbol,
    global_symbols: dict[str, Symbol],
    resolver: NameResolver,
    line: int,
    edges: list[Edge],
    run: AnalysisRun,
) -> bool:
    """Try to resolve a receiver-qualified method call.

    Handles constant receivers (User.find) and scope_resolution receivers
    (ActiveRecord::Base.connection). Extracts the receiver class name and
    looks up ClassName#method or ClassName.method in global symbols.

    Returns True if an edge was created, False to fall through to bare name lookup.
    """
    # Extract receiver class name from constant, scope_resolution, or chained call
    receiver_class: str | None = None
    if receiver_node.type == "constant":
        receiver_class = _node_text(receiver_node, source)
    elif receiver_node.type == "scope_resolution":
        # Extract full qualified name (e.g., "Voice::InboundCallBuilder")
        # for inline-namespace class definitions
        full_name = _node_text(receiver_node, source)
        # Also extract rightmost segment as fallback (e.g., "InboundCallBuilder")
        name_node = _find_child_by_field(receiver_node, "name")
        short_name = _node_text(name_node, source) if name_node is not None else None
        receiver_class = full_name
    elif receiver_node.type == "call":
        # Handle chained calls: ClassName.new(args).method()
        # Extract class from the inner call's receiver
        inner_receiver = receiver_node.child_by_field_name("receiver")
        if inner_receiver is not None:
            if inner_receiver.type == "constant":
                receiver_class = _node_text(inner_receiver, source)
            elif inner_receiver.type == "scope_resolution":
                receiver_class = _node_text(inner_receiver, source)

    if receiver_class is None:
        return False

    # Build list of candidate class names to try
    # For scope_resolution: try full name first, then short name as fallback
    candidates = [receiver_class]
    if receiver_node.type == "scope_resolution" and short_name and short_name != receiver_class:
        candidates.append(short_name)

    # Try Class#method (instance method convention) and Class.method (module method)
    for candidate in candidates:
        for sep in ("#", "."):
            qualified = f"{candidate}{sep}{method_name}"
            if qualified in global_symbols:
                callee = global_symbols[qualified]
                if callee.id != current_method.id:
                    edges.append(Edge.create(
                        src=current_method.id,
                        dst=callee.id,
                        edge_type="calls",
                        line=line,
                        evidence_type="receiver_call",
                        confidence=0.85,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))
                    return True

    # Try resolver with class name as path hint for suffix matching
    lookup_result = resolver.lookup(method_name, path_hint=receiver_class)
    if lookup_result.found and lookup_result.symbol is not None:
        callee = lookup_result.symbol
        if callee.id != current_method.id:
            edges.append(Edge.create(
                src=current_method.id,
                dst=callee.id,
                edge_type="calls",
                line=line,
                evidence_type="receiver_call",
                confidence=0.75 * lookup_result.confidence,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
            ))
            return True

    return False


def _extract_edges_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run: AnalysisRun,
    resolver: NameResolver | None = None,
    require_hints: dict[str, str] | None = None,
) -> list[Edge]:
    """Extract call and import edges from a file.

    Args:
        require_hints: Optional dict mapping class/module names to require paths for disambiguation.
    """
    if resolver is None:  # pragma: no cover - defensive
        resolver = NameResolver(global_symbols)
    if require_hints is None:  # pragma: no cover - defensive default
        require_hints = {}
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return []

    edges: list[Edge] = []
    file_id = _make_file_id(str(file_path))

    for node in iter_tree(tree.root_node):
        # Detect call nodes (require statements and method calls)
        if node.type == "call":
            # Get method name from the 'method' field (not the first identifier!)
            # For `data.chop`, the tree has:
            #   call: receiver=identifier("data"), method=identifier("chop")
            # We want "chop", not "data"
            method_node = node.child_by_field_name("method")
            # Fallback for simple calls without receiver (e.g., `require "foo"`)
            # All tested call forms have method field, but this handles potential
            # edge cases in tree-sitter-ruby grammar versions.
            if method_node is None:  # pragma: no cover
                for child in node.children:
                    if child.type == "identifier":
                        method_node = child
                        break

            if method_node:
                callee_name = _node_text(method_node, source)

                # Handle require/require_relative as imports
                if callee_name in ("require", "require_relative"):
                    args_node = _find_child_by_field(node, "arguments")
                    if args_node:
                        for arg in args_node.children:
                            if arg.type == "string":
                                content_node = _find_child_by_type(arg, "string_content")
                                if content_node:
                                    import_path = _node_text(content_node, source)
                                    edges.append(Edge.create(
                                        src=file_id,
                                        dst=f"ruby:{import_path}:0-0:file:file",
                                        edge_type="imports",
                                        line=node.start_point[0] + 1,
                                        evidence_type="require_statement",
                                        confidence=0.95,
                                        origin=PASS_ID,
                                        origin_run_id=run.execution_id,
                                    ))

                # Handle regular method calls
                else:
                    current_method = _get_enclosing_method(node, source, local_symbols)
                    if current_method is not None:
                        # Try receiver-qualified resolution first
                        receiver_node = node.child_by_field_name("receiver")
                        if receiver_node is not None and _try_receiver_call(
                            receiver_node, callee_name, source,
                            current_method, global_symbols, resolver,
                            node.start_point[0] + 1, edges, run,
                        ):
                            pass  # Resolved via receiver
                        # Check local symbols first
                        elif callee_name in local_symbols:
                            callee = local_symbols[callee_name]
                            # Skip self-referential edges (e.g., logger method
                            # calling Postal.logger where method name matches)
                            if callee.id != current_method.id:
                                edges.append(Edge.create(
                                    src=current_method.id,
                                    dst=callee.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="method_call",
                                    confidence=0.85,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                ))
                        # Check global symbols via resolver
                        else:
                            # Use require hints for disambiguation
                            path_hint = require_hints.get(callee_name)
                            lookup_result = resolver.lookup(callee_name, path_hint=path_hint)
                            if lookup_result.found and lookup_result.symbol is not None:
                                edges.append(Edge.create(
                                    src=current_method.id,
                                    dst=lookup_result.symbol.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="method_call",
                                    confidence=0.80 * lookup_result.confidence,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                ))

        # Detect bare method calls (identifier nodes that are method names)
        # Skip identifiers that are part of a call/method_call - those are handled above
        elif node.type == "identifier":
            # If parent is a call-related node, skip (already handled by call handler)
            if node.parent is not None and node.parent.type in (
                "call", "method_call", "element_reference", "scope_resolution"
            ):
                continue
            current_method = _get_enclosing_method(node, source, local_symbols)
            if current_method is not None:
                callee_name = _node_text(node, source)
                # Check if this identifier is a known method
                if callee_name in local_symbols:
                    callee = local_symbols[callee_name]
                    if callee.kind == "method" and callee.id != current_method.id:
                        edges.append(Edge.create(
                            src=current_method.id,
                            dst=callee.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            evidence_type="bare_method_call",
                            confidence=0.75,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))
                else:
                    # Use require hints for disambiguation
                    path_hint = require_hints.get(callee_name)
                    lookup_result = resolver.lookup(callee_name, path_hint=path_hint)
                    if lookup_result.found and lookup_result.symbol is not None:
                        callee = lookup_result.symbol
                        if callee.kind == "method" and callee.id != current_method.id:
                            edges.append(Edge.create(
                                src=current_method.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="bare_method_call",
                                confidence=0.70 * lookup_result.confidence,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                            ))

    return edges


def _resolve_base_class_ruby(
    base_name: str,
    child_sym: Symbol,
    class_by_name: dict[str, list[Symbol]],
    sym_file_require_hints: dict[str, dict[str, str]],
) -> Symbol | None:
    """Resolve a base class name to a specific Symbol, disambiguating collisions.

    When multiple classes share the same name (e.g., test stubs named 'Model'),
    uses a priority cascade:

    1. Same-file match: base class defined in the same file as the child
    2. Require-hint match: child's file has ``require_relative 'path/to/model'``
       and the inferred PascalCase class name matches the base_name; then match
       the require path against candidate file paths
    3. First by ID: deterministic fallback (sorted by symbol ID)

    Args:
        base_name: The base class name to resolve (e.g., 'Model')
        child_sym: The child class symbol (for file context)
        class_by_name: Multi-value lookup: class name -> list of Symbol candidates
        sym_file_require_hints: Maps symbol ID -> file-level require_hints dict
            (PascalCase class name -> require path)

    Returns:
        The resolved base class Symbol, or None if no match found.
    """
    candidates = class_by_name.get(base_name)
    if not candidates:
        return None

    if len(candidates) == 1:
        return candidates[0]

    child_path = child_sym.path or ""

    # 1. Same-file match: prefer base class in the same file
    same_file = [c for c in candidates if c.path == child_path]
    if len(same_file) == 1:
        return same_file[0]

    # 2. Require-hint match: check if child's file requires match a candidate
    require_hints = sym_file_require_hints.get(child_sym.id, {})
    if base_name in require_hints:
        require_path = require_hints[base_name]
        # Match require path against candidate file paths
        # e.g., require_path="models/model" matches candidate path "models/model.rb"
        for cand in candidates:
            cand_path = cand.path or ""
            # Strip .rb extension for comparison
            cand_no_ext = cand_path.rsplit(".rb", 1)[0]
            if cand_no_ext.endswith(require_path):
                return cand

    # 3. Deterministic fallback: first by symbol ID (sorted for stability)
    candidates_sorted = sorted(candidates, key=lambda c: c.id)
    return candidates_sorted[0]


def _extract_inheritance_edges(
    symbols: list[Symbol],
    class_by_name: dict[str, list[Symbol]],
    sym_file_require_hints: dict[str, dict[str, str]],
    run: AnalysisRun,
) -> list[Edge]:
    """Extract extends edges from class inheritance (META-001).

    For each class with base_classes metadata, creates extends edges to
    base classes that exist in the analyzed codebase. This enables the
    type hierarchy linker to create dispatches_to edges for polymorphic dispatch.

    When multiple classes share the same name (common in large repos with test stubs),
    uses require-hint-aware disambiguation via ``_resolve_base_class_ruby()`` to find
    the correct target.

    Args:
        symbols: All extracted symbols
        class_by_name: Multi-value lookup: class name -> list of Symbol candidates
        sym_file_require_hints: Maps symbol ID -> file-level require_hints dict
        run: Current analysis run for provenance

    Returns:
        List of extends edges for inheritance relationships
    """
    edges: list[Edge] = []

    for sym in symbols:
        if sym.kind != "class":
            continue

        base_classes = sym.meta.get("base_classes", []) if sym.meta else []
        if not base_classes:
            continue

        for base_class_name in base_classes:
            # Handle qualified names like "ActiveRecord::Base" -> look for last segment too
            base_name = base_class_name.split("::")[-1] if "::" in base_class_name else base_class_name

            # Try exact match first (for fully qualified names), then last segment
            base_sym = _resolve_base_class_ruby(
                base_class_name, sym, class_by_name, sym_file_require_hints
            )
            if base_sym is None and base_name != base_class_name:
                base_sym = _resolve_base_class_ruby(
                    base_name, sym, class_by_name, sym_file_require_hints
                )
            if base_sym is None or base_sym.id == sym.id:
                continue

            edge = Edge.create(
                src=sym.id,
                dst=base_sym.id,
                edge_type="extends",
                line=sym.span.start_line if sym.span else 0,
                confidence=0.95,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="ast_extends",
            )
            edges.append(edge)

    return edges


@register_analyzer("ruby")
def analyze_ruby(repo_root: Path) -> RubyAnalysisResult:
    """Analyze all Ruby files in a repository.

    Returns a RubyAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-ruby is not available, returns a skipped result.
    """
    if not is_ruby_tree_sitter_available():
        warnings.warn(
            "tree-sitter-ruby not available. Install with: pip install hypergumbo[ruby]",
            stacklevel=2,
        )
        return RubyAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-ruby not available",
        )

    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Import tree-sitter-ruby
    try:
        import tree_sitter_ruby
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_ruby.language())
        parser = tree_sitter.Parser(lang)
    except Exception as e:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return RubyAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=f"Failed to load Ruby parser: {e}",
        )

    # Pass 1: Extract all symbols from all files
    file_analyses: dict[Path, FileAnalysis] = {}
    all_rb_files: list[Path] = list(find_ruby_files(repo_root))
    files_skipped = 0

    for rb_file in all_rb_files:
        analysis = _extract_symbols_from_file(rb_file, parser, run)
        if analysis.symbols:
            file_analyses[rb_file] = analysis
        else:
            files_skipped += 1

    # Build global symbol registry
    global_symbols: dict[str, Symbol] = {}
    for analysis in file_analyses.values():
        for symbol in analysis.symbols:
            # Store by short name for cross-file resolution
            short_name = symbol.name.split("#")[-1] if "#" in symbol.name else symbol.name
            short_name = short_name.split(".")[-1] if "." in short_name else short_name
            global_symbols[short_name] = symbol
            global_symbols[symbol.name] = symbol

    # Pass 2: Extract edges from files with symbols
    resolver = NameResolver(global_symbols)
    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []
    all_usage_contexts: list[UsageContext] = []

    for rb_file, analysis in file_analyses.items():
        all_symbols.extend(analysis.symbols)

        edges = _extract_edges_from_file(
            rb_file, parser, analysis.symbol_by_name, global_symbols, run, resolver,
            require_hints=analysis.require_hints,
        )
        all_edges.extend(edges)

    # Pass 2b: Extract Rails callback edges (before_action, after_action, etc.)
    # These create invokes_callback edges from controller class → callback method.
    for rb_file, analysis in file_analyses.items():
        try:
            source = rb_file.read_bytes()
            tree = parser.parse(source)
        except (OSError, IOError):  # pragma: no cover
            continue
        callback_edges = _extract_rails_callbacks(
            tree, source, rb_file, analysis.symbol_by_name, global_symbols,
            resolver, run,
        )
        all_edges.extend(callback_edges)

    # Pass 3: Extract usage contexts and route symbols with file-path filtering.
    # Test files (spec/, test/, etc.) use get/post/delete as HTTP test helpers,
    # NOT route definitions. Only detect routes in:
    # 1. Route definition files (routes.rb, config/routes/*.rb)
    # 2. Non-test app files with Sinatra-style do-block routes
    for rb_file in all_rb_files:
        is_test = _is_test_directory(rb_file, repo_root)
        if is_test:
            continue  # Skip test files entirely — HTTP methods are test helpers
        try:
            source = rb_file.read_bytes()
            tree = parser.parse(source)
            symbol_by_name = file_analyses.get(rb_file, FileAnalysis()).symbol_by_name
            usage_contexts, route_symbols = _extract_rails_routes(
                tree.root_node, source, rb_file, symbol_by_name, run
            )
            if _is_route_definition_file(rb_file, repo_root):
                # Route definition files: include all detected routes
                all_usage_contexts.extend(usage_contexts)
                all_symbols.extend(route_symbols)
            else:
                # Non-route app files: only Sinatra-style routes (with do blocks)
                # Bare get/post calls without blocks are likely HTTP client calls
                for ctx in usage_contexts:
                    if ctx.metadata.get("has_block"):
                        all_usage_contexts.append(ctx)
                for sym in route_symbols:
                    if sym.meta.get("has_block"):
                        all_symbols.append(sym)
        except (OSError, IOError):  # pragma: no cover
            pass

    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    # Extract inheritance edges (META-001: base_classes metadata -> extends edges)
    # Build multi-value class lookup for disambiguation (INV-015)
    class_by_name: dict[str, list[Symbol]] = {}
    for s in all_symbols:
        if s.kind == "class":
            if s.name not in class_by_name:
                class_by_name[s.name] = []
            class_by_name[s.name].append(s)
    # Build per-symbol require_hints mapping for import disambiguation
    sym_file_require_hints: dict[str, dict[str, str]] = {}
    for _rb_file, analysis in file_analyses.items():
        for sym in analysis.symbols:
            sym_file_require_hints[sym.id] = analysis.require_hints
    inheritance_edges = _extract_inheritance_edges(
        all_symbols, class_by_name, sym_file_require_hints, run
    )
    all_edges.extend(inheritance_edges)

    return RubyAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        usage_contexts=all_usage_contexts,
        run=run,
    )
