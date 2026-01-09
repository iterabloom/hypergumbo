"""Rust analysis pass using tree-sitter-rust.

This analyzer uses tree-sitter to parse Rust files and extract:
- Function declarations (fn)
- Struct declarations (struct)
- Enum declarations (enum)
- Impl blocks and their methods
- Trait declarations
- Function call relationships
- Import relationships (use statements)
- Axum route handlers (.route("/path", get(handler)))
- Actix-web route handlers (#[get("/path")], #[post("/path")])

If tree-sitter with Rust support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-rust is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls, use statements, and routes
4. Route detection:
   - Axum: Find `.route("/path", get(handler))` patterns
   - Actix-web: Find `#[get("/path")]` attribute macros on functions
   - Create route symbols with stable_id = HTTP method

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-rust package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as Elixir/Java/PHP/C analyzers for consistency
- Route detection enables `hypergumbo routes` command for Rust
"""
from __future__ import annotations

import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol
from .base import iter_tree

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "rust-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# Axum HTTP method functions that define route handlers (deprecated - use rust-web.yaml)
# Used in patterns like .route("/path", get(handler))
AXUM_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}

# Actix-web attribute macros that define route handlers (deprecated - use rust-web.yaml)
# Used in patterns like #[get("/path")] async fn handler() {}
ACTIX_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}

# Deprecation tracking for analyzer-level route detection (ADR-0003 v1.0.x)
# Framework-specific route detection is deprecated in favor of YAML patterns
_deprecated_route_warnings_emitted: set[str] = set()


def _emit_route_deprecation_warning(framework: str) -> None:
    """Emit deprecation warning for analyzer-level route detection.

    This is deprecated in ADR-0003 v1.0.x. Use YAML patterns instead.
    Warning emitted once per framework per session.
    """
    if framework in _deprecated_route_warnings_emitted:
        return
    _deprecated_route_warnings_emitted.add(framework)
    warnings.warn(
        f"{framework} analyzer-level route detection is deprecated. "
        f"Use framework YAML patterns (--frameworks) for semantic detection. "
        f"See ADR-0003 for migration guidance.",
        DeprecationWarning,
        stacklevel=4,
    )


def find_rust_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Rust files in the repository."""
    yield from find_files(repo_root, ["*.rs"])


def is_rust_tree_sitter_available() -> bool:
    """Check if tree-sitter with Rust grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec("tree_sitter_rust") is None:
        return False
    return True


@dataclass
class RustAnalysisResult:
    """Result of analyzing Rust files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"rust:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Rust file node (used as import edge source)."""
    return f"rust:{path}:1-1:file:file"


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


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file."""

    symbols: list[Symbol] = field(default_factory=list)
    symbol_by_name: dict[str, Symbol] = field(default_factory=dict)


def _extract_rust_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a Rust function_item node.

    Returns a signature string like "(x: i32, y: String) -> bool" or None
    if extraction fails.

    Args:
        node: A tree-sitter function_item node.
        source: Source bytes of the file.
    """
    if node.type != "function_item":
        return None  # pragma: no cover

    params_node = _find_child_by_field(node, "parameters")
    if not params_node:
        return None  # pragma: no cover

    # Extract parameters
    param_strs: list[str] = []
    for child in params_node.children:
        if child.type == "parameter":
            # Each parameter has pattern and optional type
            pattern_node = _find_child_by_field(child, "pattern")
            type_node = _find_child_by_field(child, "type")

            if pattern_node and type_node:
                param_name = _node_text(pattern_node, source)
                param_type = _node_text(type_node, source)
                param_strs.append(f"{param_name}: {param_type}")
            elif pattern_node:  # pragma: no cover
                # No type annotation (rare in Rust)
                param_strs.append(_node_text(pattern_node, source))
        elif child.type == "self_parameter":
            # Handle &self, &mut self, self, etc.
            self_text = _node_text(child, source)
            param_strs.append(self_text)

    sig = "(" + ", ".join(param_strs) + ")"

    # Extract return type if present
    return_type_node = _find_child_by_field(node, "return_type")
    if return_type_node:
        ret_type = _node_text(return_type_node, source)
        # Remove the leading "-> " if tree-sitter includes it
        if ret_type.startswith("-> "):  # pragma: no cover
            ret_type = ret_type[3:]
        sig += f" -> {ret_type}"

    return sig


def _get_impl_target(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up the tree to find the enclosing impl block's target type.

    Args:
        node: The current node.
        source: Source bytes for extracting text.

    Returns:
        The impl target type name, or None if not inside an impl block.
    """
    current = node.parent
    while current is not None:
        if current.type == "impl_item":
            type_node = _find_child_by_field(current, "type")
            if type_node:
                return _node_text(type_node, source)
        current = current.parent
    return None


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
) -> FileAnalysis:
    """Extract symbols from a single Rust file.

    Uses iterative tree traversal to avoid RecursionError on deeply nested code.
    """
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return FileAnalysis()

    analysis = FileAnalysis()

    for node in iter_tree(tree.root_node):
        # Function declaration
        if node.type == "function_item":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                func_name = _node_text(name_node, source)
                impl_target = _get_impl_target(node, source)
                if impl_target:
                    full_name = f"{impl_target}::{func_name}"
                    kind = "method"
                else:
                    full_name = func_name
                    kind = "function"

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract function signature
                signature = _extract_rust_signature(node, source)

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, full_name, kind),
                    name=full_name,
                    kind=kind,
                    language="rust",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    signature=signature,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[func_name] = symbol
                analysis.symbol_by_name[full_name] = symbol

        # Struct declaration
        elif node.type == "struct_item":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                struct_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, struct_name, "struct"),
                    name=struct_name,
                    kind="struct",
                    language="rust",
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
                analysis.symbol_by_name[struct_name] = symbol

        # Enum declaration
        elif node.type == "enum_item":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                enum_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, enum_name, "enum"),
                    name=enum_name,
                    kind="enum",
                    language="rust",
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
                analysis.symbol_by_name[enum_name] = symbol

        # Trait declaration
        elif node.type == "trait_item":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                trait_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, trait_name, "trait"),
                    name=trait_name,
                    kind="trait",
                    language="rust",
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
                analysis.symbol_by_name[trait_name] = symbol

    return analysis


def _extract_axum_routes(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
) -> list[Symbol]:
    """Extract Axum route handler symbols from a tree-sitter node.

    Detects patterns like:
    - .route("/path", get(handler))
    - .route("/users", post(create_user).get(list_users))

    Creates symbols with stable_id = HTTP method for route discovery.
    Uses iterative tree traversal to avoid RecursionError on deeply nested code.
    """
    routes: list[Symbol] = []

    def extract_handlers_from_call(
        call_node: "tree_sitter.Node", route_path: str
    ) -> None:
        """Extract handler functions from method chain iteratively.

        Handles patterns like: get(handler).post(other_handler)
        Uses a while loop instead of recursion to traverse chained calls.
        """
        current_call = call_node
        while current_call is not None and current_call.type == "call_expression":
            func_node = _find_child_by_field(current_call, "function")
            if not func_node:
                break  # pragma: no cover

            next_call = None

            # Check if this is an HTTP method call like get(handler)
            if func_node.type == "identifier":
                method_name = _node_text(func_node, source)
                if method_name in AXUM_HTTP_METHODS:
                    # Extract handler name from arguments
                    args_node = _find_child_by_type(current_call, "arguments")
                    if args_node:
                        for arg in args_node.children:
                            if arg.type == "identifier":
                                handler_name = _node_text(arg, source)
                                start_line = current_call.start_point[0] + 1
                                end_line = current_call.end_point[0] + 1

                                route_sym = Symbol(
                                    id=_make_symbol_id(
                                        str(file_path), start_line, end_line,
                                        f"{method_name.upper()} {route_path}", "route"
                                    ),
                                    stable_id=method_name,  # HTTP method for route discovery
                                    name=handler_name,
                                    kind="route",
                                    language="rust",
                                    path=str(file_path),
                                    span=Span(
                                        start_line=start_line,
                                        end_line=end_line,
                                        start_col=current_call.start_point[1],
                                        end_col=current_call.end_point[1],
                                    ),
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    meta={"route_path": route_path, "http_method": method_name.upper()},
                                )
                                routes.append(route_sym)
                                break

            # Check for chained methods like get(h1).post(h2)
            elif func_node.type == "field_expression":
                # The field is the method name (post)
                field_node = _find_child_by_field(func_node, "field")
                # The value is the previous call (get(h1))
                value_node = _find_child_by_field(func_node, "value")

                if field_node:
                    method_name = _node_text(field_node, source)
                    if method_name in AXUM_HTTP_METHODS:
                        # Extract handler from this method's arguments
                        args_node = _find_child_by_type(current_call, "arguments")
                        if args_node:
                            for arg in args_node.children:
                                if arg.type == "identifier":
                                    handler_name = _node_text(arg, source)
                                    start_line = current_call.start_point[0] + 1
                                    end_line = current_call.end_point[0] + 1

                                    route_sym = Symbol(
                                        id=_make_symbol_id(
                                            str(file_path), start_line, end_line,
                                            f"{method_name.upper()} {route_path}", "route"
                                        ),
                                        stable_id=method_name,
                                        name=handler_name,
                                        kind="route",
                                        language="rust",
                                        path=str(file_path),
                                        span=Span(
                                            start_line=start_line,
                                            end_line=end_line,
                                            start_col=current_call.start_point[1],
                                            end_col=current_call.end_point[1],
                                        ),
                                        origin=PASS_ID,
                                        origin_run_id=run.execution_id,
                                        meta={"route_path": route_path, "http_method": method_name.upper()},
                                    )
                                    routes.append(route_sym)
                                    break

                # Continue to the chained call
                if value_node and value_node.type == "call_expression":
                    next_call = value_node

            current_call = next_call

    for n in iter_tree(node):
        # Look for .route("/path", handler) pattern
        if n.type == "call_expression":
            func_node = _find_child_by_field(n, "function")

            # Check if this is a method call .route(...)
            if func_node and func_node.type == "field_expression":
                field_node = _find_child_by_field(func_node, "field")
                if field_node and _node_text(field_node, source) == "route":
                    # Extract arguments
                    args_node = _find_child_by_type(n, "arguments")
                    if args_node:
                        route_path = None
                        handler_call = None

                        for arg in args_node.children:
                            # First string argument is the route path
                            if arg.type == "string_literal" and route_path is None:
                                route_path = _node_text(arg, source).strip('"')
                            # Call expression is the handler(s)
                            elif arg.type == "call_expression" and route_path:
                                handler_call = arg
                                break

                        if route_path and handler_call:
                            extract_handlers_from_call(handler_call, route_path)

    return routes


def _extract_actix_routes(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
) -> list[Symbol]:
    """Extract Actix-web route handler symbols from attribute macros.

    Detects patterns like:
    - #[get("/path")]
    - #[post("/users")]
    - #[actix_web::get("/path")]

    Creates symbols with stable_id = HTTP method for route discovery.
    Uses iterative tree traversal to avoid RecursionError on deeply nested code.
    """
    routes: list[Symbol] = []

    # Use a stack-based approach to process nodes and their children
    # Each item is a node whose children we need to scan for attribute+function pairs
    stack = [node]
    while stack:
        current = stack.pop()

        # Iterate through children looking for attribute + function pairs
        for i, child in enumerate(current.children):
            if child.type == "attribute_item":
                attr_text = _node_text(child, source)

                # Check for HTTP method attributes
                for method in ACTIX_HTTP_METHODS:
                    # Match patterns like #[get("/path")] or #[actix_web::get("/path")]
                    if f"[{method}(" in attr_text or f"::{method}(" in attr_text:
                        # Extract the path from the first quoted string in the attribute
                        # Handles: #[get("/path")] and #[post("/path", data = "<form>")]
                        path_start = attr_text.find('"')
                        if path_start != -1:
                            # Find the closing quote of the first string (not the last quote)
                            path_end = attr_text.find('"', path_start + 1)
                        else:
                            path_end = -1  # pragma: no cover
                        if path_start != -1 and path_end > path_start:
                            route_path = attr_text[path_start + 1:path_end]

                            # Look for the next function_item sibling
                            for j in range(i + 1, len(current.children)):
                                sibling = current.children[j]
                                if sibling.type == "function_item":
                                    name_node = _find_child_by_field(sibling, "name")
                                    if name_node:
                                        handler_name = _node_text(name_node, source)
                                        start_line = sibling.start_point[0] + 1
                                        end_line = sibling.end_point[0] + 1

                                        route_sym = Symbol(
                                            id=_make_symbol_id(
                                                str(file_path), start_line, end_line,
                                                f"{method.upper()} {route_path}", "route"
                                            ),
                                            stable_id=method,
                                            name=handler_name,
                                            kind="route",
                                            language="rust",
                                            path=str(file_path),
                                            span=Span(
                                                start_line=start_line,
                                                end_line=end_line,
                                                start_col=sibling.start_point[1],
                                                end_col=sibling.end_point[1],
                                            ),
                                            origin=PASS_ID,
                                            origin_run_id=run.execution_id,
                                            meta={"route_path": route_path, "http_method": method.upper()},
                                        )
                                        routes.append(route_sym)
                                    break
                                # Skip other attributes and comments
                                elif sibling.type not in (  # pragma: no cover
                                    "attribute_item", "line_comment"
                                ):
                                    break  # pragma: no cover
                        break

            # Add child to stack for processing (for impl blocks, mod blocks, etc.)
            stack.append(child)

    return routes


def _get_enclosing_function(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing function.

    Args:
        node: The current node.
        source: Source bytes for extracting text.
        local_symbols: Map of function names to Symbol objects.

    Returns:
        The Symbol for the enclosing function, or None if not inside a function.
    """
    current = node.parent
    while current is not None:
        if current.type == "function_item":
            name_node = _find_child_by_field(current, "name")
            if name_node:
                func_name = _node_text(name_node, source)
                if func_name in local_symbols:
                    return local_symbols[func_name]
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_edges_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run: AnalysisRun,
) -> list[Edge]:
    """Extract call and import edges from a file.

    Uses iterative tree traversal to avoid RecursionError on deeply nested code.
    """
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return []

    edges: list[Edge] = []
    file_id = _make_file_id(str(file_path))

    for node in iter_tree(tree.root_node):
        # Detect use statements
        if node.type == "use_declaration":
            # Extract the path being imported
            path_node = _find_child_by_type(node, "scoped_identifier")
            if not path_node:
                path_node = _find_child_by_type(node, "identifier")
            if not path_node:
                path_node = _find_child_by_type(node, "use_wildcard")
            if not path_node:
                path_node = _find_child_by_type(node, "use_list")

            if path_node:
                import_path = _node_text(path_node, source)
                edges.append(Edge.create(
                    src=file_id,
                    dst=f"rust:{import_path}:0-0:module:module",
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    evidence_type="use_declaration",
                    confidence=0.95,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                ))

        # Detect function calls
        elif node.type == "call_expression":
            current_function = _get_enclosing_function(node, source, local_symbols)
            if current_function is not None:
                func_node = _find_child_by_field(node, "function")
                if func_node:
                    # Get the function name being called
                    if func_node.type == "identifier":
                        callee_name = _node_text(func_node, source)
                    elif func_node.type == "field_expression":
                        # method call like foo.bar()
                        field_node = _find_child_by_field(func_node, "field")
                        if field_node:
                            callee_name = _node_text(field_node, source)
                        else:
                            callee_name = None
                    elif func_node.type == "scoped_identifier":
                        # qualified call like Foo::bar()
                        name_node = _find_child_by_field(func_node, "name")
                        if name_node:
                            callee_name = _node_text(name_node, source)
                        else:
                            callee_name = _node_text(func_node, source)
                    else:
                        callee_name = None

                    if callee_name:
                        # Check local symbols first
                        if callee_name in local_symbols:
                            callee = local_symbols[callee_name]
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
                        # Check global symbols
                        elif callee_name in global_symbols:
                            callee = global_symbols[callee_name]
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="function_call",
                                confidence=0.80,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                            ))

    return edges


def analyze_rust(repo_root: Path) -> RustAnalysisResult:
    """Analyze all Rust files in a repository.

    Returns a RustAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-rust is not available, returns a skipped result.
    """
    if not is_rust_tree_sitter_available():
        warnings.warn(
            "tree-sitter-rust not available. Install with: pip install hypergumbo[rust]",
            stacklevel=2,
        )
        return RustAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-rust not available",
        )

    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Import tree-sitter-rust
    try:
        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)
    except Exception as e:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return RustAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=f"Failed to load Rust parser: {e}",
        )

    # Pass 1: Extract all symbols
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0

    for rs_file in find_rust_files(repo_root):
        analysis = _extract_symbols_from_file(rs_file, parser, run)
        if analysis.symbols:
            file_analyses[rs_file] = analysis
        else:
            files_skipped += 1

    # Build global symbol registry
    global_symbols: dict[str, Symbol] = {}
    for analysis in file_analyses.values():
        for symbol in analysis.symbols:
            # Store by short name for cross-file resolution
            short_name = symbol.name.split("::")[-1] if "::" in symbol.name else symbol.name
            global_symbols[short_name] = symbol
            global_symbols[symbol.name] = symbol

    # Pass 2: Extract edges and Axum routes
    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []

    for rs_file, analysis in file_analyses.items():
        all_symbols.extend(analysis.symbols)

        edges = _extract_edges_from_file(
            rs_file, parser, analysis.symbol_by_name, global_symbols, run
        )
        all_edges.extend(edges)

        # Extract route handlers (Axum and Actix-web) - deprecated
        try:
            source = rs_file.read_bytes()
            tree = parser.parse(source)
            # Axum: .route("/path", get(handler)) - deprecated - use YAML
            axum_routes = _extract_axum_routes(tree.root_node, source, rs_file, run)
            if axum_routes:
                _emit_route_deprecation_warning("Axum")
            all_symbols.extend(axum_routes)
            # Actix-web: #[get("/path")] async fn handler() {} - deprecated - use YAML
            actix_routes = _extract_actix_routes(tree.root_node, source, rs_file, run)
            if actix_routes:
                _emit_route_deprecation_warning("Actix-web")
            all_symbols.extend(actix_routes)
        except (OSError, IOError):
            pass  # Skip files that can't be read

    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return RustAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
