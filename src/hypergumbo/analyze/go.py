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

import time
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol
from .base import (
    AnalysisResult,
    FileAnalysis,
    find_child_by_field,
    find_child_by_type,
    is_grammar_available,
    iter_tree,
    make_file_id,
    make_symbol_id,
    node_text,
)
from .registry import register_analyzer

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
    return is_grammar_available("tree_sitter_go")


# Keep GoAnalysisResult as an alias for backwards compatibility
GoAnalysisResult = AnalysisResult


def _extract_go_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a Go function/method declaration.

    Returns a signature string like "(x int, y string) error" or "(a, b int) (int, error)"
    for Go functions. None if extraction fails.

    Args:
        node: A tree-sitter function_declaration or method_declaration node.
        source: Source bytes of the file.
    """
    if node.type not in ("function_declaration", "method_declaration"):
        return None  # pragma: no cover

    params_node = find_child_by_field(node, "parameters")
    if not params_node:
        return None  # pragma: no cover

    # Extract parameters from parameter_list
    param_strs: list[str] = []
    for child in params_node.children:
        if child.type == "parameter_declaration":
            # Go parameters: can have multiple names sharing a type
            # e.g., "a, b int" or "x string"
            names: list[str] = []
            type_str = ""
            for param_child in child.children:
                if param_child.type == "identifier":
                    names.append(node_text(param_child, source))
                elif param_child.type in ("type_identifier", "pointer_type",
                                          "slice_type", "map_type", "array_type",
                                          "interface_type", "struct_type",
                                          "function_type", "channel_type",
                                          "qualified_type"):
                    type_str = node_text(param_child, source)

            if names and type_str:
                param_strs.append(f"{', '.join(names)} {type_str}")
            elif type_str:  # pragma: no cover
                # Unnamed parameter (rare but valid in Go interfaces)
                param_strs.append(type_str)

    sig = "(" + ", ".join(param_strs) + ")"

    # Extract return type(s) from result field
    result_node = find_child_by_field(node, "result")
    if result_node:
        ret_text = node_text(result_node, source)
        sig += f" {ret_text}"

    return sig


def _extract_import_aliases(
    root_node: "tree_sitter.Node",
    source: bytes,
) -> dict[str, str]:
    """Extract import alias → import path mappings from a Go file.

    Returns a dict mapping alias names to their import paths.
    For imports without explicit aliases, uses the last path component.

    Example:
        import (
            "fmt"                    -> {"fmt": "fmt"}
            pb "github.com/foo/bar"  -> {"pb": "github.com/foo/bar"}
        )
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(root_node):
        if node.type == "import_declaration":
            for child in node.children:
                if child.type == "import_spec":
                    _process_import_spec(child, source, aliases)
                elif child.type == "import_spec_list":
                    for spec in child.children:
                        if spec.type == "import_spec":
                            _process_import_spec(spec, source, aliases)

    return aliases


def _process_import_spec(
    spec: "tree_sitter.Node",
    source: bytes,
    aliases: dict[str, str],
) -> None:
    """Process a single import_spec node and add to aliases dict."""
    path_node = find_child_by_field(spec, "path")
    if not path_node:
        return  # pragma: no cover - defensive for malformed AST

    import_path = node_text(path_node, source).strip('"')

    # Check for explicit alias
    name_node = find_child_by_field(spec, "name")
    if name_node:
        alias = node_text(name_node, source)
        if alias != "_" and alias != ".":  # Ignore blank and dot imports
            aliases[alias] = import_path
    else:
        # No explicit alias - use last component of path
        # e.g., "github.com/foo/bar" -> "bar"
        alias = import_path.rsplit("/", 1)[-1]
        aliases[alias] = import_path


def _import_path_to_dir_hint(import_path: str) -> str | None:
    """Convert an import path to a directory hint for matching.

    For paths like "github.com/example/src/checkoutservice/genproto",
    returns "/src/checkoutservice/genproto" or similar suffix that
    can be used to match against file paths.
    """
    # Look for common patterns that indicate local paths
    if "/src/" in import_path:
        # Extract from /src/ onwards
        tail = import_path.split("/src/", 1)[1]
        return f"/src/{tail}"

    # For other paths, use the last 2-3 components
    parts = import_path.split("/")
    if len(parts) >= 2:
        return "/" + "/".join(parts[-2:])

    return None


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
) -> FileAnalysis:
    """Extract symbols from a single Go file.

    Uses iterative tree traversal to avoid RecursionError on deeply nested code.
    """
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return FileAnalysis()

    analysis = FileAnalysis()

    # Extract import aliases for this file (used later in edge extraction)
    analysis.import_aliases = _extract_import_aliases(tree.root_node, source)

    for node in iter_tree(tree.root_node):
        # Function declaration (including methods with receivers)
        if node.type == "function_declaration":
            name_node = find_child_by_field(node, "name")
            if name_node:
                func_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract function signature
                signature = _extract_go_signature(node, source)

                symbol = Symbol(
                    id=make_symbol_id("go", str(file_path), start_line, end_line, func_name, "function"),
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
                    signature=signature,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[func_name] = symbol

        # Method declaration (function with receiver)
        elif node.type == "method_declaration":
            name_node = find_child_by_field(node, "name")
            receiver_node = find_child_by_field(node, "receiver")

            if name_node:
                method_name = node_text(name_node, source)
                receiver_type = ""

                if receiver_node:
                    # Extract receiver type (e.g., "User" from "(u User)" or "(u *User)")
                    param_list = receiver_node
                    for child in param_list.children:
                        if child.type == "parameter_declaration":
                            type_node = find_child_by_field(child, "type")
                            if type_node:
                                if type_node.type == "pointer_type":
                                    # *User -> User
                                    elem_node = find_child_by_type(type_node, "type_identifier")
                                    if elem_node:
                                        receiver_type = node_text(elem_node, source)
                                elif type_node.type == "type_identifier":
                                    receiver_type = node_text(type_node, source)

                full_name = f"{receiver_type}.{method_name}" if receiver_type else method_name
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract method signature
                signature = _extract_go_signature(node, source)

                symbol = Symbol(
                    id=make_symbol_id("go", str(file_path), start_line, end_line, full_name, "method"),
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
                    signature=signature,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[method_name] = symbol
                analysis.symbol_by_name[full_name] = symbol

        # Type declaration (struct or interface)
        elif node.type == "type_declaration":
            for child in node.children:
                if child.type == "type_spec":
                    name_node = find_child_by_field(child, "name")
                    type_node = find_child_by_field(child, "type")

                    if name_node and type_node:
                        type_name = node_text(name_node, source)
                        start_line = child.start_point[0] + 1
                        end_line = child.end_point[0] + 1

                        if type_node.type == "struct_type":
                            kind = "struct"
                        elif type_node.type == "interface_type":
                            kind = "interface"
                        else:
                            kind = "type"

                        symbol = Symbol(
                            id=make_symbol_id("go", str(file_path), start_line, end_line, type_name, kind),
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

    return analysis


def _get_enclosing_function(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing function/method.

    Args:
        node: The current node.
        source: Source bytes for extracting text.
        local_symbols: Map of function names to Symbol objects.

    Returns:
        The Symbol for the enclosing function, or None if not inside a function.
    """
    current = node.parent
    while current is not None:
        if current.type in ("function_declaration", "method_declaration"):
            name_node = find_child_by_field(current, "name")
            if name_node:
                func_name = node_text(name_node, source)
                if func_name in local_symbols:
                    return local_symbols[func_name]
        current = current.parent
    return None  # pragma: no cover - defensive


def _resolve_callee(
    callee_name: str,
    candidates: list[Symbol],
    import_path_hint: str | None,
) -> Symbol | None:
    """Resolve the best callee from a list of candidates using import path hint.

    When multiple symbols have the same name (e.g., RegisterCheckoutServiceServer
    in multiple genproto directories), use the import path to pick the right one.
    """
    if not candidates:
        return None  # pragma: no cover - defensive

    if len(candidates) == 1:
        return candidates[0]

    # Multiple candidates - try to disambiguate using import path
    if import_path_hint:
        dir_hint = _import_path_to_dir_hint(import_path_hint)
        if dir_hint:
            for candidate in candidates:
                if dir_hint in candidate.path:
                    return candidate

    # Fallback: return first candidate (legacy behavior)
    return candidates[0]


def _extract_edges_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, list[Symbol]],
    run: AnalysisRun,
    import_aliases: dict[str, str] | None = None,
) -> list[Edge]:
    """Extract call and import edges from a file.

    Uses iterative tree traversal to avoid RecursionError on deeply nested code.
    Uses import_aliases to disambiguate when multiple files define the same symbol.
    """
    if import_aliases is None:
        import_aliases = {}

    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return []

    edges: list[Edge] = []
    file_id = make_file_id("go", str(file_path))

    for node in iter_tree(tree.root_node):
        # Detect import statements
        if node.type == "import_declaration":
            # Handle both single imports and import blocks
            for child in node.children:
                if child.type == "import_spec":
                    path_node = find_child_by_field(child, "path")
                    if path_node:
                        import_path = node_text(path_node, source).strip('"')
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
                            path_node = find_child_by_field(spec, "path")
                            if path_node:
                                import_path = node_text(path_node, source).strip('"')
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
            current_function = _get_enclosing_function(node, source, local_symbols)
            if current_function is not None:
                func_node = find_child_by_field(node, "function")
                if func_node:
                    callee_name = None
                    import_path_hint = None

                    if func_node.type == "identifier":
                        # Simple call: helper()
                        callee_name = node_text(func_node, source)
                    elif func_node.type == "selector_expression":
                        # Method call: obj.Method() or pkg.Func()
                        operand_node = find_child_by_field(func_node, "operand")
                        field_node = find_child_by_field(func_node, "field")
                        if field_node:
                            callee_name = node_text(field_node, source)
                        # Check if operand is a package alias
                        if operand_node and operand_node.type == "identifier":
                            alias = node_text(operand_node, source)
                            if alias in import_aliases:
                                import_path_hint = import_aliases[alias]

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
                        # Check global symbols with disambiguation
                        elif callee_name in global_symbols:
                            candidates = global_symbols[callee_name]
                            callee = _resolve_callee(callee_name, candidates, import_path_hint)
                            if callee:
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
                        # Bug #2 fix: Create edge for external/unresolved method calls
                        # This enables linkers to potentially match across languages
                        elif func_node.type == "selector_expression":
                            # For s.Method() where Method is external, create unresolved edge
                            # Use the import path if available to make the ID more specific
                            if import_path_hint:
                                # e.g., go:google.golang.org/grpc:0-0:RegisterService:unresolved
                                dst_id = f"go:{import_path_hint}:0-0:{callee_name}:unresolved"
                            else:
                                # Fallback: use "external" as the path
                                dst_id = f"go:external:0-0:{callee_name}:unresolved"
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=dst_id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="unresolved_method_call",
                                confidence=0.50,  # Lower confidence for unresolved
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                            ))

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
    Uses iterative tree traversal to avoid RecursionError on deeply nested code.
    """
    routes: list[Symbol] = []

    for n in iter_tree(node):
        # Look for call_expression with selector_expression function
        if n.type == "call_expression":
            func_node = find_child_by_field(n, "function")

            if func_node and func_node.type == "selector_expression":
                # Get the method name (e.g., GET, POST, Get, Post)
                field_node = find_child_by_field(func_node, "field")

                if field_node:
                    method_name = node_text(field_node, source)

                    if method_name in GO_HTTP_METHODS:
                        # Extract arguments
                        args_node = find_child_by_field(n, "arguments")
                        if args_node:
                            route_path = None
                            handler_name = None

                            for arg in args_node.children:
                                # First string literal is the route path
                                if arg.type == "interpreted_string_literal" and route_path is None:
                                    # Get the content without quotes
                                    content_node = find_child_by_type(
                                        arg, "interpreted_string_literal_content"
                                    )
                                    if content_node:
                                        route_path = node_text(content_node, source)
                                    else:  # pragma: no cover
                                        # Fallback: strip quotes manually
                                        route_path = node_text(arg, source).strip('"')

                                # Handler is usually an identifier after the path
                                elif arg.type == "identifier" and route_path is not None:
                                    handler_name = node_text(arg, source)
                                    break

                                # Handler could also be a selector (pkg.Handler)
                                elif arg.type == "selector_expression" and route_path is not None:
                                    handler_name = node_text(arg, source)
                                    break

                            if route_path and handler_name:
                                # Normalize method name to uppercase for stable_id
                                normalized_method = method_name.upper()
                                start_line = n.start_point[0] + 1
                                end_line = n.end_point[0] + 1

                                route_sym = Symbol(
                                    id=make_symbol_id(
                                        "go", str(file_path), start_line, end_line,
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

    return routes


@register_analyzer("go", priority=50)
def analyze_go(repo_root: Path, max_files: int | None = None) -> AnalysisResult:
    """Analyze all Go files in a repository.

    Returns an AnalysisResult with symbols, edges, and provenance.
    If tree-sitter-go is not available, returns a skipped result.
    """
    if not is_go_tree_sitter_available():
        warnings.warn(
            "tree-sitter-go not available. Install with: pip install hypergumbo[go]",
            stacklevel=2,
        )
        return AnalysisResult(
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
        return AnalysisResult(
            run=run,
            skipped=True,
            skip_reason=f"Failed to load Go parser: {e}",
        )

    # Pass 1: Extract all symbols
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0
    files_processed = 0

    for go_file in find_go_files(repo_root):
        if max_files is not None and files_processed >= max_files:  # pragma: no cover
            break
        analysis = _extract_symbols_from_file(go_file, parser, run)
        if analysis.symbols:
            file_analyses[go_file] = analysis
            files_processed += 1
        else:
            files_skipped += 1

    # Build global symbol registry - store ALL symbols with same name
    # This enables disambiguation using import paths
    global_symbols: dict[str, list[Symbol]] = {}
    for analysis in file_analyses.values():
        for symbol in analysis.symbols:
            # Store by short name for cross-file resolution
            short_name = symbol.name.split(".")[-1] if "." in symbol.name else symbol.name
            if short_name not in global_symbols:
                global_symbols[short_name] = []
            global_symbols[short_name].append(symbol)
            if symbol.name != short_name:
                if symbol.name not in global_symbols:
                    global_symbols[symbol.name] = []
                global_symbols[symbol.name].append(symbol)

    # Pass 2: Extract edges and routes
    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []

    for go_file, analysis in file_analyses.items():
        all_symbols.extend(analysis.symbols)

        edges = _extract_edges_from_file(
            go_file, parser, analysis.symbol_by_name, global_symbols, run,
            analysis.import_aliases,
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

    return AnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
