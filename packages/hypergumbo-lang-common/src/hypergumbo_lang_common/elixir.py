"""Elixir analysis pass using tree-sitter-elixir.

This analyzer uses tree-sitter to parse Elixir files and extract:
- Module declarations (defmodule)
- Function declarations (def/defp)
- Macro declarations (defmacro/defmacrop)
- Function call relationships
- Import relationships (use/import/alias)

If tree-sitter with Elixir support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-languages (with Elixir) is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and import directives

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-languages package which bundles Elixir grammar
- Two-pass allows cross-file call resolution
- Same pattern as Java/PHP/C analyzers for consistency
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

# Phoenix HTTP method macros for route detection
PHOENIX_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "elixir-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_elixir_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Elixir files in the repository."""
    yield from find_files(repo_root, ["*.ex", "*.exs"])


def is_elixir_tree_sitter_available() -> bool:
    """Check if tree-sitter with Elixir grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    # Check for tree_sitter_language_pack which includes Elixir
    if importlib.util.find_spec("tree_sitter_language_pack") is None:
        return False
    return True


@dataclass
class ElixirAnalysisResult:
    """Result of analyzing Elixir files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    usage_contexts: list[UsageContext] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"elixir:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for an Elixir file node (used as import edge source)."""
    return f"elixir:{path}:1-1:file:file"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


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

        target = _find_child_by_type(node, "identifier")
        if not target:  # pragma: no cover - call nodes always have identifier
            continue

        target_name = _node_text(target, source)

        if target_name == "alias":
            args = _find_child_by_type(node, "arguments")
            if not args:  # pragma: no cover - alias always has arguments
                continue

            # Find the module being aliased
            module_node = None
            for child in args.children:
                if child.type == "alias":
                    module_node = child
                    break

            if module_node:
                full_path = _node_text(module_node, source)
                # Check for 'as:' option in keywords
                kw_node = _find_child_by_type(args, "keywords")
                if kw_node:
                    # Look for as: Alias pattern
                    for pair in kw_node.children:
                        if pair.type == "pair":
                            key_node = _find_child_by_type(pair, "keyword")
                            if key_node and _node_text(key_node, source).strip().rstrip(":") == "as":
                                value_node = _find_child_by_type(pair, "alias")
                                if value_node:
                                    alias_name = _node_text(value_node, source)
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
            args = _find_child_by_type(node, "arguments")
            if args:
                for child in args.children:
                    if child.type == "alias":
                        full_path = _node_text(child, source)
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
            target = _find_child_by_type(current, "identifier")
            if target and _node_text(target, source) == "defmodule":
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
            target = _find_child_by_type(current, "identifier")
            if target:
                target_name = _node_text(target, source)
                if target_name in ("def", "defp", "defmacro", "defmacrop"):
                    func_name = _get_function_name(current, source)
                    if func_name and func_name in local_symbols:
                        return local_symbols[func_name]
        current = current.parent
    return None  # pragma: no cover - defensive


def _get_module_name_from_call(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract module name from defmodule call node."""
    args = _find_child_by_type(node, "arguments")
    if args:
        for child in args.children:
            if child.type == "alias":
                return _node_text(child, source)
    return None


def _get_module_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract module name from defmodule call."""
    # defmodule has structure: (call target: (identifier "defmodule") arguments: (arguments (alias)))
    args = _find_child_by_type(node, "arguments")
    if args:
        for child in args.children:
            if child.type == "alias":
                return _node_text(child, source)
    return None


def _get_function_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function name from def/defp/defmacro call."""
    # def has structure: (call target: (identifier "def") arguments: (arguments (call target: (identifier "func_name") ...)))
    args = _find_child_by_type(node, "arguments")
    if args:
        for child in args.children:
            if child.type == "call":
                # The function name is the target of this call
                target = _find_child_by_type(child, "identifier")
                if target:
                    return _node_text(target, source)
            elif child.type == "identifier":
                # Simple case: def foo, do: :ok
                return _node_text(child, source)
    return None


def _extract_elixir_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from def/defp/defmacro call.

    Returns signature in format: (param1, param2, keyword: default)
    Elixir is dynamically typed, so no type annotations are included.
    """
    args = _find_child_by_type(node, "arguments")
    if args is None:  # pragma: no cover - defensive
        return "()"

    for child in args.children:
        if child.type == "call":
            # def foo(a, b) - parameters are in the arguments of the inner call
            inner_args = _find_child_by_type(child, "arguments")
            if inner_args is None:  # pragma: no cover - rare
                return "()"

            params: list[str] = []
            for param in inner_args.children:
                if param.type == "identifier":
                    # Simple positional parameter
                    params.append(_node_text(param, source))
                elif param.type == "binary_operator":  # pragma: no cover - default vals
                    # Default value: param \\ default
                    left = None
                    for pc in param.children:
                        if pc.type == "identifier":
                            left = _node_text(pc, source)
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
                                key_text = _node_text(key_node, source).rstrip(":")
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
    run: AnalysisRun,
) -> tuple[list[UsageContext], list[Symbol]]:
    """Extract UsageContext records AND Symbol objects for Phoenix router DSL calls.

    Detects patterns like:
    - get "/", PageController, :index
    - post "/users", UserController, :create
    - resources "/posts", PostController

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
        target_node = _find_child_by_type(n, "identifier")
        if not target_node:
            continue

        method_name = _node_text(target_node, source).lower()

        # Check if it's a Phoenix route macro
        if method_name not in PHOENIX_HTTP_METHODS and method_name != "resources":
            continue

        # Find arguments
        args_node = _find_child_by_type(n, "arguments")
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
                            route_path = _node_text(sc, source)
                            break
                    if not route_path:  # pragma: no cover
                        route_path = _node_text(child, source).strip('"\'')
            elif arg_index == 1:
                # Second arg is the controller (alias/identifier)
                if child.type == "alias":
                    controller = _node_text(child, source)
                elif child.type == "identifier":  # pragma: no cover
                    controller = _node_text(child, source)
            elif arg_index == 2:
                # Third arg is the action (atom)
                if child.type == "atom":
                    action = _node_text(child, source).lstrip(":")

            arg_index += 1

        if not route_path:  # pragma: no cover
            continue

        # Build metadata
        normalized_path = route_path if route_path.startswith("/") else f"/{route_path}"
        metadata: dict[str, str | None] = {
            "route_path": normalized_path,
            "http_method": method_name.upper() if method_name in PHOENIX_HTTP_METHODS else "RESOURCES",
        }
        if controller:
            metadata["controller"] = controller
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
                    language="elixir",
                    path=str(file_path),
                    span=span,
                    meta={
                        "http_method": http_meth,
                        "route_path": route_pth,
                        "controller": controller,
                        "action": act,
                    },
                    origin=run.pass_id,
                    origin_run_id=run.execution_id,
                )
                route_symbols.append(route_symbol)
        else:
            # Single HTTP method route
            http_method = method_name.upper()
            route_name = f"{http_method} {normalized_path}"
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
                language="elixir",
                path=str(file_path),
                span=span,
                meta={
                    "http_method": http_method,
                    "route_path": normalized_path,
                },
                origin=run.pass_id,
                origin_run_id=run.execution_id,
            )
            if controller:
                route_symbol.meta["controller"] = controller
            if action:
                route_symbol.meta["action"] = action
            route_symbols.append(route_symbol)

    return contexts, route_symbols


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file."""

    symbols: list[Symbol] = field(default_factory=list)
    symbol_by_name: dict[str, Symbol] = field(default_factory=dict)
    current_module: str = ""
    alias_hints: dict[str, str] = field(default_factory=dict)


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
) -> FileAnalysis:
    """Extract symbols from a single Elixir file."""
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return FileAnalysis()

    analysis = FileAnalysis()

    for node in iter_tree(tree.root_node):
        # Check for defmodule
        if node.type == "call":
            target = _find_child_by_type(node, "identifier")
            if target:
                target_name = _node_text(target, source)

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
                            id=_make_symbol_id(str(file_path), start_line, end_line, full_name, "module"),
                            name=full_name,
                            kind="module",
                            language="elixir",
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
                        analysis.symbol_by_name[full_name] = symbol

                elif target_name in ("def", "defp"):
                    func_name = _get_function_name(node, source)
                    if func_name:
                        enclosing_modules = _get_enclosing_modules(node, source)
                        current_module = ".".join(enclosing_modules) if enclosing_modules else ""
                        full_name = f"{current_module}.{func_name}" if current_module else func_name

                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1

                        symbol = Symbol(
                            id=_make_symbol_id(str(file_path), start_line, end_line, full_name, "function"),
                            name=full_name,
                            kind="function",
                            language="elixir",
                            path=str(file_path),
                            span=Span(
                                start_line=start_line,
                                end_line=end_line,
                                start_col=node.start_point[1],
                                end_col=node.end_point[1],
                            ),
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            signature=_extract_elixir_signature(node, source),
                        )
                        analysis.symbols.append(symbol)
                        analysis.symbol_by_name[func_name] = symbol  # Store by short name for local calls

                elif target_name in ("defmacro", "defmacrop"):
                    macro_name = _get_function_name(node, source)
                    if macro_name:
                        enclosing_modules = _get_enclosing_modules(node, source)
                        current_module = ".".join(enclosing_modules) if enclosing_modules else ""
                        full_name = f"{current_module}.{macro_name}" if current_module else macro_name

                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1

                        symbol = Symbol(
                            id=_make_symbol_id(str(file_path), start_line, end_line, full_name, "macro"),
                            name=full_name,
                            kind="macro",
                            language="elixir",
                            path=str(file_path),
                            span=Span(
                                start_line=start_line,
                                end_line=end_line,
                                start_col=node.start_point[1],
                                end_col=node.end_point[1],
                            ),
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            signature=_extract_elixir_signature(node, source),
                        )
                        analysis.symbols.append(symbol)
                        analysis.symbol_by_name[macro_name] = symbol

    # Extract alias hints for disambiguation
    analysis.alias_hints = _extract_alias_hints(tree, source)

    return analysis


def _extract_edges_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run: AnalysisRun,
    resolver: NameResolver | None = None,
    alias_hints: dict[str, str] | None = None,
) -> list[Edge]:
    """Extract call and import edges from a file.

    Args:
        alias_hints: Optional dict mapping short names to full module paths for disambiguation.
    """
    if resolver is None:  # pragma: no cover - defensive
        resolver = NameResolver(global_symbols)
    if alias_hints is None:  # pragma: no cover - defensive default
        alias_hints = {}
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return []

    edges: list[Edge] = []
    file_id = _make_file_id(str(file_path))

    for node in iter_tree(tree.root_node):
        if node.type == "call":
            target = _find_child_by_type(node, "identifier")
            if target:
                target_name = _node_text(target, source)

                # Detect use/import/alias directives
                if target_name == "use":
                    args = _find_child_by_type(node, "arguments")
                    if args:
                        for child in args.children:
                            if child.type == "alias":
                                module_name = _node_text(child, source)
                                edges.append(Edge.create(
                                    src=file_id,
                                    dst=f"elixir:{module_name}:0-0:module:module",
                                    edge_type="imports",
                                    line=node.start_point[0] + 1,
                                    evidence_type="use_directive",
                                    confidence=0.95,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                ))

                elif target_name == "import":
                    args = _find_child_by_type(node, "arguments")
                    if args:
                        for child in args.children:
                            if child.type == "alias":
                                module_name = _node_text(child, source)
                                edges.append(Edge.create(
                                    src=file_id,
                                    dst=f"elixir:{module_name}:0-0:module:module",
                                    edge_type="imports",
                                    line=node.start_point[0] + 1,
                                    evidence_type="import_directive",
                                    confidence=0.95,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                ))

                # Detect function calls within a function body
                elif target_name not in ("def", "defp", "defmacro", "defmacrop", "defmodule"):
                    current_function = _get_enclosing_function(node, source, local_symbols)
                    if current_function is not None:
                        # Check if this is a call to a known local function
                        if target_name in local_symbols:
                            callee = local_symbols[target_name]
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="function_call",
                                confidence=0.85,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                            ))
                        # Check global symbols via resolver
                        else:
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
                                    origin_run_id=run.execution_id,
                                ))

    return edges


def analyze_elixir(repo_root: Path) -> ElixirAnalysisResult:
    """Analyze all Elixir files in a repository.

    Returns an ElixirAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-elixir is not available, returns a skipped result.
    """
    if not is_elixir_tree_sitter_available():
        warnings.warn(
            "tree-sitter-elixir not available. Install with: pip install hypergumbo[elixir]",
            stacklevel=2,
        )
        return ElixirAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-elixir not available",
        )

    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Import tree-sitter-language-pack for Elixir
    try:
        from tree_sitter_language_pack import get_parser
        parser = get_parser("elixir")
    except Exception as e:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return ElixirAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=f"Failed to load Elixir parser: {e}",
        )

    # Pass 1: Extract all symbols
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0

    for ex_file in find_elixir_files(repo_root):
        analysis = _extract_symbols_from_file(ex_file, parser, run)
        if analysis.symbols:
            file_analyses[ex_file] = analysis
        else:
            files_skipped += 1

    # Build global symbol registry
    global_symbols: dict[str, Symbol] = {}
    for analysis in file_analyses.values():
        for symbol in analysis.symbols:
            # Store by short name for cross-file resolution
            short_name = symbol.name.split(".")[-1] if "." in symbol.name else symbol.name
            global_symbols[short_name] = symbol
            global_symbols[symbol.name] = symbol

    # Pass 2: Extract edges and usage contexts
    resolver = NameResolver(global_symbols)
    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []
    all_usage_contexts: list[UsageContext] = []

    for ex_file, analysis in file_analyses.items():
        all_symbols.extend(analysis.symbols)

        edges = _extract_edges_from_file(
            ex_file, parser, analysis.symbol_by_name, global_symbols, run, resolver,
            alias_hints=analysis.alias_hints,
        )
        all_edges.extend(edges)

        # Extract Phoenix router usage contexts and route symbols
        try:
            source = ex_file.read_bytes()
            tree = parser.parse(source)
            usage_contexts, route_symbols = _extract_phoenix_routes(
                tree.root_node, source, ex_file, analysis.symbol_by_name, run
            )
            all_usage_contexts.extend(usage_contexts)
            all_symbols.extend(route_symbols)
        except (OSError, IOError):  # pragma: no cover
            pass

    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return ElixirAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        usage_contexts=all_usage_contexts,
        run=run,
    )
