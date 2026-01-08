"""PHP analysis pass using tree-sitter-php.

This analyzer uses tree-sitter-php to parse PHP files and extract:
- Function declarations (symbols)
- Class declarations (symbols)
- Method declarations (symbols)
- Laravel route definitions (Route::get, Route::post, etc.)
- Function call relationships (edges)
- Method call relationships (edges)
- Static method call relationships (edges)
- Object instantiation relationships (edges)

If tree-sitter-php is not installed, the analyzer gracefully degrades
and returns an empty result.

How It Works
------------
1. Check if tree-sitter and tree-sitter-php are available
2. If not available, return empty result (not an error, just no PHP analysis)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls, method calls, static calls, and instantiation

Why This Design
---------------
- Optional dependency keeps base install lightweight
- PHP support is separate from JS/TS to keep modules focused
- Two-pass allows cross-file call resolution
- Same pattern as JS/TS analyzer for consistency
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

PASS_ID = "php-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# Laravel HTTP route methods
LARAVEL_HTTP_METHODS = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "delete": "DELETE",
    "patch": "PATCH",
    "head": "HEAD",
    "options": "OPTIONS",
}


def find_php_files(repo_root: Path) -> Iterator[Path]:
    """Yield all PHP files in the repository."""
    yield from find_files(repo_root, ["*.php"])


def is_php_tree_sitter_available() -> bool:
    """Check if tree-sitter and PHP grammar are available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec("tree_sitter_php") is None:
        return False
    return True


@dataclass
class PhpAnalysisResult:
    """Result of analyzing PHP files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"php:{path}:{start_line}-{end_line}:{name}:{kind}"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_name_in_children(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Find identifier name in node's children."""
    for child in node.children:
        if child.type == "name":
            return _node_text(child, source)
    return None


def _get_enclosing_class(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up the tree to find the enclosing class name."""
    current = node.parent
    while current is not None:
        if current.type == "class_declaration":
            name = _find_name_in_children(current, source)
            if name:
                return name
        current = current.parent
    return None  # pragma: no cover - defensive


def _get_enclosing_function_php(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    global_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing function/method for PHP."""
    current = node.parent

    while current is not None:
        if current.type == "function_definition":
            name = _find_name_in_children(current, source)
            if name and name in global_symbols:
                sym = global_symbols[name]
                if sym.path == str(file_path):
                    return sym

        if current.type == "method_declaration":
            name = _find_name_in_children(current, source)
            if name:
                # Find enclosing class by walking up further
                class_name = _get_enclosing_class(current, source)
                if class_name:
                    full_name = f"{class_name}.{name}"
                    if full_name in global_symbols:
                        sym = global_symbols[full_name]
                        if sym.path == str(file_path):
                            return sym
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_php_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a PHP function or method declaration.

    Returns signature like:
    - "(int $x, int $y): int" for typed functions
    - "(string $msg)" for void functions

    Args:
        node: The function_definition or method_declaration node.
        source: The source code bytes.

    Returns:
        The signature string, or None if extraction fails.
    """
    params: list[str] = []
    return_type = None
    found_params = False

    # Iterate through children to find parameters and return type
    for child in node.children:
        if child.type == "formal_parameters":
            found_params = True
            for subchild in child.children:
                if subchild.type == "simple_parameter":
                    param_type = None
                    param_name = None
                    for pc in subchild.children:
                        if pc.type in ("primitive_type", "named_type", "nullable_type",
                                        "optional_type", "union_type"):
                            param_type = _node_text(pc, source)
                        elif pc.type == "variable_name":
                            param_name = _node_text(pc, source)
                    if param_name:
                        if param_type:
                            params.append(f"{param_type} {param_name}")
                        else:
                            params.append(param_name)
        # Return type comes after formal_parameters
        elif found_params and child.type in ("primitive_type", "named_type", "nullable_type",
                                              "optional_type", "union_type"):
            return_type = _node_text(child, source)

    params_str = ", ".join(params)
    signature = f"({params_str})"

    if return_type and return_type != "void":
        signature += f": {return_type}"

    return signature


def _detect_laravel_route(
    node: "tree_sitter.Node", source: bytes
) -> tuple[str | None, str | None]:
    """Detect Laravel Route::get(), Route::post(), etc. static calls.

    Returns (http_method, route_path) if this is a Laravel route, else (None, None).
    """
    if node.type != "scoped_call_expression":
        return None, None  # pragma: no cover

    scope_node = node.child_by_field_name("scope")
    name_node = node.child_by_field_name("name")

    if not scope_node or not name_node:
        return None, None  # pragma: no cover

    # Check if this is Route::method()
    scope_text = _node_text(scope_node, source)
    if scope_text != "Route":
        return None, None

    method_name = _node_text(name_node, source)
    if method_name not in LARAVEL_HTTP_METHODS:
        return None, None  # pragma: no cover

    http_method = LARAVEL_HTTP_METHODS[method_name]
    route_path = None

    # Extract route path from first argument
    args_node = node.child_by_field_name("arguments")
    if args_node:
        for child in args_node.children:
            if child.type == "argument":
                # First argument is the route path
                for arg_child in child.children:
                    if arg_child.type == "string":
                        # Extract content from string node
                        for str_child in arg_child.children:
                            if str_child.type == "string_content":
                                route_path = _node_text(str_child, source)
                                break
                        if route_path is None:  # pragma: no cover
                            # Fallback: try to get the whole string and strip quotes
                            raw = _node_text(arg_child, source)
                            route_path = raw.strip("'\"")
                        break
                break

    return http_method, route_path


def _get_php_parser() -> Optional["tree_sitter.Parser"]:
    """Get tree-sitter parser for PHP."""
    try:
        import tree_sitter
        import tree_sitter_php
    except ImportError:
        return None

    parser = tree_sitter.Parser()
    # PHP has two grammars: php and php_only. We use php which includes HTML.
    lang_ptr = tree_sitter_php.language_php()
    parser.language = tree_sitter.Language(lang_ptr)
    return parser


@dataclass
class _ParsedFile:
    """Holds parsed file data for two-pass analysis."""

    path: Path
    tree: "tree_sitter.Tree"
    source: bytes


def _extract_symbols(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
) -> list[Symbol]:
    """Extract symbols from a parsed PHP tree (pass 1)."""
    symbols: list[Symbol] = []

    for node in iter_tree(tree.root_node):
        # Laravel route detection: Route::get(), Route::post(), etc.
        if node.type == "scoped_call_expression":
            http_method, route_path = _detect_laravel_route(node, source)
            if http_method:
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                route_name = f"Route::{http_method.lower()}({route_path or '...'})"
                meta: dict[str, str] = {"http_method": http_method}
                if route_path:
                    meta["route_path"] = route_path
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, route_name, "route"),
                    name=route_name,
                    kind="route",
                    language="php",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    stable_id=http_method,
                    meta=meta,
                )
                symbols.append(symbol)

        # Function declarations
        elif node.type == "function_definition":
            name = _find_name_in_children(node, source)
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                signature = _extract_php_signature(node, source)
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "function"),
                    name=name,
                    kind="function",
                    language="php",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    signature=signature,
                )
                symbols.append(symbol)

        # Class declarations
        elif node.type == "class_declaration":
            name = _find_name_in_children(node, source)
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "class"),
                    name=name,
                    kind="class",
                    language="php",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                symbols.append(symbol)

        # Method declarations (inside classes)
        elif node.type == "method_declaration":
            name = _find_name_in_children(node, source)
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                enclosing_class = _get_enclosing_class(node, source)
                full_name = f"{enclosing_class}.{name}" if enclosing_class else name
                signature = _extract_php_signature(node, source)
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, full_name, "method"),
                    name=full_name,
                    kind="method",
                    language="php",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    signature=signature,
                )
                symbols.append(symbol)

    return symbols


def _extract_edges(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
    global_symbols: dict[str, Symbol],
    global_methods: dict[str, list[Symbol]],
    global_classes: dict[str, Symbol],
) -> list[Edge]:
    """Extract edges from a parsed PHP tree (pass 2).

    Uses global symbol registries to resolve cross-file references.
    """
    edges: list[Edge] = []

    for node in iter_tree(tree.root_node):
        # Function calls: func_name()
        if node.type == "function_call_expression":
            func_node = node.child_by_field_name("function")
            if func_node and func_node.type == "name":
                callee_name = _node_text(func_node, source)
                current_function = _get_enclosing_function_php(node, source, file_path, global_symbols)
                if current_function and callee_name in global_symbols:
                    target_sym = global_symbols[callee_name]
                    edge = Edge.create(
                        src=current_function.id,
                        dst=target_sym.id,
                        edge_type="calls",
                        line=node.start_point[0] + 1,
                        confidence=0.95,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        evidence_type="ast_call_direct",
                    )
                    edges.append(edge)

        # Method calls: $this->method() or $obj->method()
        elif node.type == "member_call_expression":
            current_function = _get_enclosing_function_php(node, source, file_path, global_symbols)
            if current_function:
                # Get the method name
                name_node = node.child_by_field_name("name")
                obj_node = node.child_by_field_name("object")
                if name_node:
                    method_name = _node_text(name_node, source)

                    # Check if it's $this->method()
                    is_this_call = obj_node and obj_node.type == "variable_name" and _node_text(obj_node, source) == "$this"

                    current_class_name = _get_enclosing_class(node, source)
                    if is_this_call and current_class_name:
                        # Try to resolve to a method in the same class
                        full_name = f"{current_class_name}.{method_name}"
                        if full_name in global_symbols:
                            target_sym = global_symbols[full_name]
                            edge = Edge.create(
                                src=current_function.id,
                                dst=target_sym.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                confidence=0.95,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="ast_method_this",
                            )
                            edges.append(edge)
                    elif method_name in global_methods:
                        # Try to resolve to any method with this name
                        # Use lower confidence since we can't be sure of the type
                        for target_sym in global_methods[method_name]:
                            edge = Edge.create(
                                src=current_function.id,
                                dst=target_sym.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                confidence=0.60,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="ast_method_inferred",
                            )
                            edges.append(edge)

        # Static method calls: ClassName::method()
        elif node.type == "scoped_call_expression":
            current_function = _get_enclosing_function_php(node, source, file_path, global_symbols)
            if current_function:
                scope_node = node.child_by_field_name("scope")
                name_node = node.child_by_field_name("name")
                if scope_node and name_node:
                    class_name = _node_text(scope_node, source)
                    method_name = _node_text(name_node, source)

                    # Handle self:: and static::
                    current_class_name = _get_enclosing_class(node, source)
                    if class_name in ("self", "static") and current_class_name:
                        class_name = current_class_name

                    full_name = f"{class_name}.{method_name}"
                    if full_name in global_symbols:
                        target_sym = global_symbols[full_name]
                        edge = Edge.create(
                            src=current_function.id,
                            dst=target_sym.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            confidence=0.95,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            evidence_type="ast_static_call",
                        )
                        edges.append(edge)

        # Object instantiation: new ClassName()
        elif node.type == "object_creation_expression":
            current_function = _get_enclosing_function_php(node, source, file_path, global_symbols)
            if current_function:
                # Get the class name
                for child in node.children:
                    if child.type == "name":
                        class_name = _node_text(child, source)
                        if class_name in global_classes:
                            target_sym = global_classes[class_name]
                            edge = Edge.create(
                                src=current_function.id,
                                dst=target_sym.id,
                                edge_type="instantiates",
                                line=node.start_point[0] + 1,
                                confidence=0.95,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="ast_new",
                            )
                            edges.append(edge)
                        break

    return edges


def _analyze_php_file(
    file_path: Path,
    run: AnalysisRun,
) -> tuple[list[Symbol], list[Edge], bool]:
    """Analyze a single PHP file (legacy single-pass, used for testing).

    Returns (symbols, edges, success).
    """
    parser = _get_php_parser()
    if parser is None:
        return [], [], False

    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return [], [], False

    symbols = _extract_symbols(tree, source, file_path, run)

    # Build symbol registry for edge extraction
    global_symbols: dict[str, Symbol] = {}
    global_methods: dict[str, list[Symbol]] = {}
    global_classes: dict[str, Symbol] = {}

    for sym in symbols:
        global_symbols[sym.name] = sym
        if sym.kind == "method":
            # Extract just the method name (after the dot)
            method_name = sym.name.split(".")[-1] if "." in sym.name else sym.name
            if method_name not in global_methods:
                global_methods[method_name] = []
            global_methods[method_name].append(sym)
        elif sym.kind == "class":
            global_classes[sym.name] = sym

    edges = _extract_edges(tree, source, file_path, run, global_symbols, global_methods, global_classes)
    return symbols, edges, True


def analyze_php(repo_root: Path) -> PhpAnalysisResult:
    """Analyze all PHP files in a repository.

    Uses a two-pass approach:
    1. Parse all files and extract symbols into global registry
    2. Detect calls and resolve against global symbol registry

    Returns a PhpAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-php is not available, returns empty result (silently skipped).
    """
    start_time = time.time()

    # Create analysis run for provenance
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Check for tree-sitter-php availability
    if not is_php_tree_sitter_available():
        skip_reason = "PHP analysis skipped: requires tree-sitter-php (pip install tree-sitter-php)"
        warnings.warn(skip_reason, stacklevel=2)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return PhpAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=skip_reason,
        )

    parser = _get_php_parser()
    if parser is None:
        skip_reason = "PHP analysis skipped: requires tree-sitter-php (pip install tree-sitter-php)"
        warnings.warn(skip_reason, stacklevel=2)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return PhpAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=skip_reason,
        )

    # Pass 1: Parse all files and extract symbols
    parsed_files: list[_ParsedFile] = []
    all_symbols: list[Symbol] = []
    files_analyzed = 0
    files_skipped = 0

    for file_path in find_php_files(repo_root):
        try:
            source = file_path.read_bytes()
            tree = parser.parse(source)
            parsed_files.append(_ParsedFile(path=file_path, tree=tree, source=source))
            symbols = _extract_symbols(tree, source, file_path, run)
            all_symbols.extend(symbols)
            files_analyzed += 1
        except (OSError, IOError):
            files_skipped += 1

    # Build global symbol registries
    global_symbols: dict[str, Symbol] = {}
    global_methods: dict[str, list[Symbol]] = {}
    global_classes: dict[str, Symbol] = {}

    for sym in all_symbols:
        global_symbols[sym.name] = sym
        if sym.kind == "method":
            # Extract just the method name (after the dot)
            method_name = sym.name.split(".")[-1] if "." in sym.name else sym.name
            if method_name not in global_methods:
                global_methods[method_name] = []
            global_methods[method_name].append(sym)
        elif sym.kind == "class":
            global_classes[sym.name] = sym

    # Pass 2: Extract edges using global symbol registry
    all_edges: list[Edge] = []
    for pf in parsed_files:
        edges = _extract_edges(
            pf.tree, pf.source, pf.path, run,
            global_symbols, global_methods, global_classes
        )
        all_edges.extend(edges)

    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return PhpAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
