"""Go analysis pass using tree-sitter-go.

This analyzer uses tree-sitter to parse Go files and extract:
- Function declarations (func)
- Method declarations (func with receiver)
- Struct declarations (type X struct)
- Interface declarations (type X interface)
- Function call relationships
- Import relationships (import statements)
- Web framework routes (Gin, Echo, Fiber)

If tree-sitter with Go support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-go is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls, imports, and routes
4. Route detection:
   - Gin/Echo: r.GET("/path", handler), e.POST("/path", handler)
   - Fiber: app.Get("/path", handler) (lowercase methods)
   - Creates route symbols with stable_id = HTTP method

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-go package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as Rust/Elixir/Java/PHP/C analyzers for consistency
- Route detection enables `hypergumbo routes` command for Go
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

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "go-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# Go web framework HTTP method names
# Gin/Echo use uppercase: GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS
# Fiber uses lowercase: Get, Post, Put, Delete, Patch, Head, Options
GO_HTTP_METHODS = {
    "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS",
    "Get", "Post", "Put", "Delete", "Patch", "Head", "Options",
}


def find_go_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Go files in the repository."""
    yield from find_files(repo_root, ["*.go"])


def is_go_tree_sitter_available() -> bool:
    """Check if tree-sitter with Go grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec("tree_sitter_go") is None:
        return False
    return True


@dataclass
class GoAnalysisResult:
    """Result of analyzing Go files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"go:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Go file node (used as import edge source)."""
    return f"go:{path}:1-1:file:file"


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


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
) -> FileAnalysis:
    """Extract symbols from a single Go file."""
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return FileAnalysis()

    analysis = FileAnalysis()

    def visit(node: "tree_sitter.Node") -> None:
        # Function declaration (including methods with receivers)
        if node.type == "function_declaration":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                func_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, func_name, "function"),
                    name=func_name,
                    kind="function",
                    language="go",
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
                analysis.symbol_by_name[func_name] = symbol

        # Method declaration (function with receiver)
        elif node.type == "method_declaration":
            name_node = _find_child_by_field(node, "name")
            receiver_node = _find_child_by_field(node, "receiver")

            if name_node:
                method_name = _node_text(name_node, source)
                receiver_type = ""

                if receiver_node:
                    # Extract receiver type (e.g., "User" from "(u User)" or "(u *User)")
                    param_list = receiver_node
                    for child in param_list.children:
                        if child.type == "parameter_declaration":
                            type_node = _find_child_by_field(child, "type")
                            if type_node:
                                if type_node.type == "pointer_type":
                                    # *User -> User
                                    elem_node = _find_child_by_type(type_node, "type_identifier")
                                    if elem_node:
                                        receiver_type = _node_text(elem_node, source)
                                elif type_node.type == "type_identifier":
                                    receiver_type = _node_text(type_node, source)

                full_name = f"{receiver_type}.{method_name}" if receiver_type else method_name
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, full_name, "method"),
                    name=full_name,
                    kind="method",
                    language="go",
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
                analysis.symbol_by_name[method_name] = symbol
                analysis.symbol_by_name[full_name] = symbol

        # Type declaration (struct or interface)
        elif node.type == "type_declaration":
            for child in node.children:
                if child.type == "type_spec":
                    name_node = _find_child_by_field(child, "name")
                    type_node = _find_child_by_field(child, "type")

                    if name_node and type_node:
                        type_name = _node_text(name_node, source)
                        start_line = child.start_point[0] + 1
                        end_line = child.end_point[0] + 1

                        if type_node.type == "struct_type":
                            kind = "struct"
                        elif type_node.type == "interface_type":
                            kind = "interface"
                        else:
                            kind = "type"

                        symbol = Symbol(
                            id=_make_symbol_id(str(file_path), start_line, end_line, type_name, kind),
                            name=type_name,
                            kind=kind,
                            language="go",
                            path=str(file_path),
                            span=Span(
                                start_line=start_line,
                                end_line=end_line,
                                start_col=child.start_point[1],
                                end_col=child.end_point[1],
                            ),
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        )
                        analysis.symbols.append(symbol)
                        analysis.symbol_by_name[type_name] = symbol

        # Recurse into children
        for child in node.children:
            visit(child)

    visit(tree.root_node)
    return analysis


def _extract_edges_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run: AnalysisRun,
) -> list[Edge]:
    """Extract call and import edges from a file."""
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return []

    edges: list[Edge] = []
    file_id = _make_file_id(str(file_path))
    current_function: Optional[Symbol] = None

    def visit(node: "tree_sitter.Node") -> None:
        nonlocal current_function

        # Track current function for call edges
        if node.type in ("function_declaration", "method_declaration"):
            name_node = _find_child_by_field(node, "name")
            if name_node:
                func_name = _node_text(name_node, source)
                if func_name in local_symbols:
                    old_function = current_function
                    current_function = local_symbols[func_name]

                    # Process function body
                    for child in node.children:
                        visit(child)

                    current_function = old_function
                    return

        # Detect import statements
        elif node.type == "import_declaration":
            # Handle both single imports and import blocks
            for child in node.children:
                if child.type == "import_spec":
                    path_node = _find_child_by_field(child, "path")
                    if path_node:
                        import_path = _node_text(path_node, source).strip('"')
                        edges.append(Edge.create(
                            src=file_id,
                            dst=f"go:{import_path}:0-0:package:package",
                            edge_type="imports",
                            line=child.start_point[0] + 1,
                            evidence_type="import_declaration",
                            confidence=0.95,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))
                elif child.type == "import_spec_list":
                    for spec in child.children:
                        if spec.type == "import_spec":
                            path_node = _find_child_by_field(spec, "path")
                            if path_node:
                                import_path = _node_text(path_node, source).strip('"')
                                edges.append(Edge.create(
                                    src=file_id,
                                    dst=f"go:{import_path}:0-0:package:package",
                                    edge_type="imports",
                                    line=spec.start_point[0] + 1,
                                    evidence_type="import_declaration",
                                    confidence=0.95,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                ))

        # Detect function calls
        elif node.type == "call_expression":
            if current_function is not None:
                func_node = _find_child_by_field(node, "function")
                if func_node:
                    callee_name = None

                    if func_node.type == "identifier":
                        # Simple call: helper()
                        callee_name = _node_text(func_node, source)
                    elif func_node.type == "selector_expression":
                        # Method call: obj.Method() or pkg.Func()
                        field_node = _find_child_by_field(func_node, "field")
                        if field_node:
                            callee_name = _node_text(field_node, source)

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

        # Recurse
        for child in node.children:
            visit(child)

    visit(tree.root_node)
    return edges


def _extract_go_routes(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
) -> list[Symbol]:
    """Extract Go web framework route symbols from a tree-sitter node.

    Detects patterns like:
    - Gin/Echo: r.GET("/path", handler), e.POST("/users", createUser)
    - Fiber: app.Get("/path", handler) (lowercase methods)

    Creates symbols with stable_id = HTTP method for route discovery.
    """
    routes: list[Symbol] = []

    def visit(n: "tree_sitter.Node") -> None:
        # Look for call_expression with selector_expression function
        if n.type == "call_expression":
            func_node = _find_child_by_field(n, "function")

            if func_node and func_node.type == "selector_expression":
                # Get the method name (e.g., GET, POST, Get, Post)
                field_node = _find_child_by_field(func_node, "field")

                if field_node:
                    method_name = _node_text(field_node, source)

                    if method_name in GO_HTTP_METHODS:
                        # Extract arguments
                        args_node = _find_child_by_field(n, "arguments")
                        if args_node:
                            route_path = None
                            handler_name = None

                            for arg in args_node.children:
                                # First string literal is the route path
                                if arg.type == "interpreted_string_literal" and route_path is None:
                                    # Get the content without quotes
                                    content_node = _find_child_by_type(
                                        arg, "interpreted_string_literal_content"
                                    )
                                    if content_node:
                                        route_path = _node_text(content_node, source)
                                    else:  # pragma: no cover
                                        # Fallback: strip quotes manually
                                        route_path = _node_text(arg, source).strip('"')

                                # Handler is usually an identifier after the path
                                elif arg.type == "identifier" and route_path is not None:
                                    handler_name = _node_text(arg, source)
                                    break

                                # Handler could also be a selector (pkg.Handler)
                                elif arg.type == "selector_expression" and route_path is not None:
                                    handler_name = _node_text(arg, source)
                                    break

                            if route_path and handler_name:
                                # Normalize method name to uppercase for stable_id
                                normalized_method = method_name.upper()
                                start_line = n.start_point[0] + 1
                                end_line = n.end_point[0] + 1

                                route_sym = Symbol(
                                    id=_make_symbol_id(
                                        str(file_path), start_line, end_line,
                                        f"{normalized_method} {route_path}", "route"
                                    ),
                                    stable_id=normalized_method.lower(),
                                    name=handler_name,
                                    kind="route",
                                    language="go",
                                    path=str(file_path),
                                    span=Span(
                                        start_line=start_line,
                                        end_line=end_line,
                                        start_col=n.start_point[1],
                                        end_col=n.end_point[1],
                                    ),
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    meta={
                                        "route_path": route_path,
                                        "http_method": normalized_method,
                                    },
                                )
                                routes.append(route_sym)

        # Recurse
        for child in n.children:
            visit(child)

    visit(node)
    return routes


def analyze_go(repo_root: Path) -> GoAnalysisResult:
    """Analyze all Go files in a repository.

    Returns a GoAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-go is not available, returns a skipped result.
    """
    if not is_go_tree_sitter_available():
        warnings.warn(
            "tree-sitter-go not available. Install with: pip install hypergumbo[go]",
            stacklevel=2,
        )
        return GoAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-go not available",
        )

    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Import tree-sitter-go
    try:
        import tree_sitter_go
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_go.language())
        parser = tree_sitter.Parser(lang)
    except Exception as e:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return GoAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=f"Failed to load Go parser: {e}",
        )

    # Pass 1: Extract all symbols
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0

    for go_file in find_go_files(repo_root):
        analysis = _extract_symbols_from_file(go_file, parser, run)
        if analysis.symbols:
            file_analyses[go_file] = analysis
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

    # Pass 2: Extract edges and routes
    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []

    for go_file, analysis in file_analyses.items():
        all_symbols.extend(analysis.symbols)

        edges = _extract_edges_from_file(
            go_file, parser, analysis.symbol_by_name, global_symbols, run
        )
        all_edges.extend(edges)

        # Extract web framework routes (Gin, Echo, Fiber)
        try:
            source = go_file.read_bytes()
            tree = parser.parse(source)
            routes = _extract_go_routes(tree.root_node, source, go_file, run)
            all_symbols.extend(routes)
        except (OSError, IOError):  # pragma: no cover
            pass  # Skip files that can't be read

    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return GoAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
