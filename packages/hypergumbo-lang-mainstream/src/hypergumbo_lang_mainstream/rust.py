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

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol, UsageContext
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.base import iter_tree

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "rust-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# Axum HTTP method functions that define route handlers
# Used by _extract_axum_usage_contexts for YAML pattern matching
AXUM_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


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
    usage_contexts: list[UsageContext] = field(default_factory=list)
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
    use_aliases: dict[str, str] = field(default_factory=dict)


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


def _extract_rust_annotations(
    node: "tree_sitter.Node", source: bytes
) -> list[dict[str, object]]:
    """Extract Rust attributes from preceding siblings of a node.

    Rust attributes like #[get("/path")] or #[derive(Debug)] appear as
    `attribute_item` siblings immediately before the declaration they apply to.

    Args:
        node: The declaration node (function_item, struct_item, etc.)
        source: Source bytes for extracting text.

    Returns:
        List of annotation dicts: [{"name": str, "args": list, "kwargs": dict}]
    """
    annotations: list[dict[str, object]] = []

    if node.parent is None:  # pragma: no cover - defensive
        return annotations

    # Find this node's index in parent's children
    parent = node.parent
    node_index = -1
    for i, child in enumerate(parent.children):
        if child == node:
            node_index = i
            break

    if node_index < 0:
        return annotations  # pragma: no cover

    # Walk backwards from this node collecting attribute_items
    # Stop when we hit a non-attribute (another declaration, etc.)
    for i in range(node_index - 1, -1, -1):
        sibling = parent.children[i]
        if sibling.type == "attribute_item":
            # Parse the attribute: #[name(args)] or #[path::to::name(args)]
            attr_text = _node_text(sibling, source)
            ann = _parse_rust_attribute(attr_text)
            if ann:
                annotations.append(ann)
        elif sibling.type == "line_comment":
            # Skip comments, they don't break the attribute chain
            continue  # pragma: no cover - rare edge case
        else:
            # Any other node type breaks the chain
            break

    # Reverse to maintain source order (we walked backwards)
    annotations.reverse()
    return annotations


def _parse_rust_attribute(attr_text: str) -> Optional[dict[str, object]]:
    """Parse a Rust attribute string into annotation dict.

    Examples:
        #[get("/path")]         -> {"name": "get", "args": ["/path"], "kwargs": {}}
        #[actix_web::get("/")]  -> {"name": "actix_web::get", "args": ["/"], "kwargs": {}}
        #[derive(Debug, Clone)] -> {"name": "derive", "args": ["Debug", "Clone"], "kwargs": {}}
        #[route("/", method = "GET")] -> {"name": "route", "args": ["/"], "kwargs": {"method": "GET"}}

    Args:
        attr_text: Raw attribute text including #[ and ]

    Returns:
        Parsed annotation dict or None if parsing fails.
    """
    # Strip #[ and ] from outer wrapper
    text = attr_text.strip()
    if text.startswith("#[") and text.endswith("]"):
        text = text[2:-1]
    else:
        return None  # pragma: no cover

    # Find the name (before any parentheses)
    paren_pos = text.find("(")
    if paren_pos == -1:
        # No arguments: #[test] or #[cfg(test)]
        return {"name": text.strip(), "args": [], "kwargs": {}}

    name = text[:paren_pos].strip()
    args_str = text[paren_pos + 1:-1] if text.endswith(")") else ""

    # Parse arguments - handle both positional and named
    args: list[str] = []
    kwargs: dict[str, str] = {}

    if args_str:
        # Simple parsing: split by comma, handle quotes
        # This handles common cases like ("/path") or ("/", method = "GET")
        current_arg = ""
        in_string = False
        string_char = ""

        for char in args_str:
            if char in ('"', "'") and not in_string:
                in_string = True
                string_char = char
                current_arg += char
            elif char == string_char and in_string:
                in_string = False
                current_arg += char
            elif char == "," and not in_string:
                arg = current_arg.strip()
                if arg:
                    _add_rust_arg(arg, args, kwargs)
                current_arg = ""
            else:
                current_arg += char

        # Handle last argument
        arg = current_arg.strip()
        if arg:
            _add_rust_arg(arg, args, kwargs)

    return {"name": name, "args": args, "kwargs": kwargs}


def _add_rust_arg(arg: str, args: list[str], kwargs: dict[str, str]) -> None:
    """Add a parsed argument to either args or kwargs list.

    Args:
        arg: The argument string (might be positional or named)
        args: List to append positional args to
        kwargs: Dict to add named args to
    """
    # Check if it's a named argument (contains = outside of string)
    eq_pos = -1
    in_string = False
    for i, char in enumerate(arg):
        if char in ('"', "'"):
            in_string = not in_string
        elif char == "=" and not in_string:
            eq_pos = i
            break

    if eq_pos > 0:
        # Named argument
        key = arg[:eq_pos].strip()
        value = arg[eq_pos + 1:].strip()
        # Strip quotes from value
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        kwargs[key] = value
    else:
        # Positional argument - strip quotes
        if (arg.startswith('"') and arg.endswith('"')) or \
           (arg.startswith("'") and arg.endswith("'")):
            arg = arg[1:-1]
        args.append(arg)


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

    # Extract use statement aliases for disambiguation
    use_aliases = _extract_use_aliases(tree, source)

    analysis = FileAnalysis(use_aliases=use_aliases)

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

                # Extract annotations for YAML pattern matching
                annotations = _extract_rust_annotations(node, source)
                meta: dict[str, object] | None = None
                if annotations:
                    meta = {"annotations": annotations}

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
                    meta=meta,
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

                # Extract annotations for YAML pattern matching (e.g., derive macros)
                annotations = _extract_rust_annotations(node, source)
                meta = {"annotations": annotations} if annotations else None

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
                    meta=meta,
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

                # Extract annotations for YAML pattern matching
                annotations = _extract_rust_annotations(node, source)
                meta = {"annotations": annotations} if annotations else None

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
                    meta=meta,
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

                # Extract annotations for YAML pattern matching
                annotations = _extract_rust_annotations(node, source)
                meta = {"annotations": annotations} if annotations else None

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
                    meta=meta,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[trait_name] = symbol

    return analysis


def _extract_axum_usage_contexts(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    symbol_by_name: dict[str, Symbol],
) -> list[UsageContext]:
    """Extract UsageContext records for Axum route registrations.

    Detects patterns like:
    - .route("/path", get(handler))
    - .route("/users", post(create_user).get(list_users))

    Returns a list of UsageContext records for YAML pattern matching.
    """
    contexts: list[UsageContext] = []

    # Use a stack-based approach to process nodes iteratively
    stack = [node]
    while stack:
        current = stack.pop()

        for child in current.children:
            stack.append(child)

            # Look for method call .route(...)
            if child.type == "call_expression":
                func_node = _find_child_by_field(child, "function")

                if func_node and func_node.type == "field_expression":
                    field_node = _find_child_by_field(func_node, "field")

                    if field_node and _node_text(field_node, source) == "route":
                        # Found .route() call - extract arguments
                        args_node = _find_child_by_type(child, "arguments")
                        if not args_node:  # pragma: no cover
                            continue

                        route_path = None
                        for arg in args_node.children:
                            if arg.type == "string_literal" and route_path is None:
                                route_path = _node_text(arg, source).strip('"')
                                break

                        if not route_path:  # pragma: no cover
                            continue

                        # Extract handler calls (get(handler), post(handler), etc.)
                        for arg in args_node.children:
                            if arg.type == "call_expression":
                                _extract_handler_usage_contexts(
                                    arg, source, file_path, route_path,
                                    symbol_by_name, contexts
                                )

    return contexts


def _extract_handler_usage_contexts(
    call_node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    route_path: str,
    symbol_by_name: dict[str, Symbol],
    contexts: list[UsageContext],
) -> None:
    """Extract UsageContext from handler chain like get(handler).post(handler2).

    Iteratively traverses chained method calls.
    """
    current_call = call_node
    while current_call is not None and current_call.type == "call_expression":
        func_node = _find_child_by_field(current_call, "function")
        if not func_node:
            break  # pragma: no cover

        next_call = None
        method_name = None
        handler_name = None

        # Check if this is an HTTP method call like get(handler)
        if func_node.type == "identifier":
            method_name = _node_text(func_node, source)
            if method_name in AXUM_HTTP_METHODS:
                args_node = _find_child_by_type(current_call, "arguments")
                if args_node:
                    for arg in args_node.children:
                        if arg.type == "identifier":
                            handler_name = _node_text(arg, source)
                            break

        # Check for chained methods like get(h1).post(h2)
        elif func_node.type == "field_expression":
            field_node = _find_child_by_field(func_node, "field")
            value_node = _find_child_by_field(func_node, "value")

            if field_node:
                method_name = _node_text(field_node, source)
                if method_name in AXUM_HTTP_METHODS:
                    args_node = _find_child_by_type(current_call, "arguments")
                    if args_node:
                        for arg in args_node.children:
                            if arg.type == "identifier":
                                handler_name = _node_text(arg, source)
                                break

            # Continue traversing the chain
            if value_node and value_node.type == "call_expression":
                next_call = value_node

        # Create UsageContext if we found a valid handler
        if method_name and method_name in AXUM_HTTP_METHODS and handler_name:
            # Try to resolve handler to a symbol reference
            handler_ref = None
            if handler_name in symbol_by_name:
                handler_ref = symbol_by_name[handler_name].id

            span = Span(
                start_line=current_call.start_point[0] + 1,
                end_line=current_call.end_point[0] + 1,
                start_col=current_call.start_point[1],
                end_col=current_call.end_point[1],
            )

            ctx = UsageContext.create(
                kind="call",
                context_name=f"route.{method_name}",  # e.g., "route.get", "route.post"
                position="args[last]",
                path=str(file_path),
                span=span,
                symbol_ref=handler_ref,
                metadata={
                    "route_path": route_path,
                    "http_method": method_name.upper(),
                    "handler_name": handler_name,
                },
            )
            contexts.append(ctx)

        current_call = next_call


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


def _extract_use_aliases(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract use statement aliases from a parsed Rust tree.

    Maps imported names to their full paths for disambiguation:
    - use crate::module::func; -> func: crate::module::func
    - use std::io::Write; -> Write: std::io::Write
    - use foo::bar as baz; -> baz: foo::bar

    Returns dict mapping local alias -> full import path.
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "use_declaration":
            continue

        # Handle 'use foo::bar as baz;' - use_as_clause
        as_clause = _find_child_by_type(node, "use_as_clause")
        if as_clause:
            # Find the scoped_identifier (foo::bar) and alias (baz)
            path_node = _find_child_by_type(as_clause, "scoped_identifier")
            if not path_node:
                path_node = _find_child_by_type(as_clause, "identifier")
            alias_node = _find_child_by_type(as_clause, "identifier")
            # The alias is typically the last identifier child
            for child in as_clause.children:
                if child.type == "identifier":
                    alias_node = child
            if path_node and alias_node:
                full_path = _node_text(path_node, source)
                alias = _node_text(alias_node, source)
                if alias and full_path:
                    aliases[alias] = full_path
            continue

        # Handle regular 'use foo::bar;' - scoped_identifier
        path_node = _find_child_by_type(node, "scoped_identifier")
        if path_node:
            full_path = _node_text(path_node, source)
            if full_path and "::" in full_path:
                # Last segment is the imported name
                name = full_path.rsplit("::", 1)[-1]
                if name:
                    aliases[name] = full_path
            continue

        # Handle simple 'use foo;'
        id_node = _find_child_by_type(node, "identifier")
        if id_node:
            name = _node_text(id_node, source)
            if name:
                aliases[name] = name

    return aliases


def _extract_edges_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run: AnalysisRun,
    resolver: NameResolver | None = None,
    use_aliases: dict[str, str] | None = None,
) -> list[Edge]:
    """Extract call and import edges from a file.

    Uses iterative tree traversal to avoid RecursionError on deeply nested code.

    Args:
        use_aliases: Optional dict mapping local names to import paths for disambiguation.
    """
    if resolver is None:
        resolver = NameResolver(global_symbols)
    if use_aliases is None:
        use_aliases = {}
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
                        # Check global symbols via resolver
                        else:
                            # Use import path as hint for disambiguation
                            import_hint = use_aliases.get(callee_name)
                            lookup_result = resolver.lookup(callee_name, path_hint=import_hint)
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

    # Pass 2: Extract edges, routes, and usage contexts
    resolver = NameResolver(global_symbols)
    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []
    all_usage_contexts: list[UsageContext] = []

    for rs_file, analysis in file_analyses.items():
        all_symbols.extend(analysis.symbols)

        edges = _extract_edges_from_file(
            rs_file, parser, analysis.symbol_by_name, global_symbols, run, resolver,
            use_aliases=analysis.use_aliases
        )
        all_edges.extend(edges)

        # Extract UsageContext for Axum route YAML pattern matching
        try:
            source = rs_file.read_bytes()
            tree = parser.parse(source)
            usage_contexts = _extract_axum_usage_contexts(
                tree.root_node, source, rs_file, analysis.symbol_by_name
            )
            all_usage_contexts.extend(usage_contexts)
        except (OSError, IOError):  # pragma: no cover
            pass  # Skip files that can't be read

    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return RustAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        usage_contexts=all_usage_contexts,
        run=run,
    )
