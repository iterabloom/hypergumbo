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
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract functions, structs, enums, traits with signatures and annotations
2. Pass 2: Extract call edges, use edges, and Axum usage contexts
3. Post-process: Extract decorated_by edges from attribute metadata

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Rust-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Optional dependency keeps base install lightweight
- Uses tree-sitter-rust package for grammar
- Two-pass allows cross-file call resolution
- Route detection enables `hypergumbo routes` command for Rust
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

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("rust")

# Axum HTTP method functions that define route handlers
# Used by _extract_axum_usage_contexts for YAML pattern matching
AXUM_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def find_rust_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Rust files in the repository."""
    yield from find_files(repo_root, ["*.rs"])


def _find_child_by_field(node: "tree_sitter.Node", field_name: str) -> Optional["tree_sitter.Node"]:
    """Find child by field name."""
    return node.child_by_field_name(field_name)


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
                param_name = node_text(pattern_node, source)
                param_type = node_text(type_node, source)
                param_strs.append(f"{param_name}: {param_type}")
            elif pattern_node:  # pragma: no cover
                # No type annotation (rare in Rust)
                param_strs.append(node_text(pattern_node, source))
        elif child.type == "self_parameter":
            # Handle &self, &mut self, self, etc.
            self_text = node_text(child, source)
            param_strs.append(self_text)

    sig = "(" + ", ".join(param_strs) + ")"

    # Extract return type if present
    return_type_node = _find_child_by_field(node, "return_type")
    if return_type_node:
        ret_type = node_text(return_type_node, source)
        # Remove the leading "-> " if tree-sitter includes it
        if ret_type.startswith("-> "):  # pragma: no cover
            ret_type = ret_type[3:]
        sig += f" -> {ret_type}"

    return sig


def _extract_base_type_name(type_node: "tree_sitter.Node", source: bytes) -> str:
    """Extract the base type identifier from a type node.

    Rust type nodes can be complex:
    - type_identifier: simple type like "User" -> return "User"
    - generic_type: "Writer<'a, M, W>" -> extract base type "Writer"
    - reference_type: "&'a M" -> recursively extract inner type "M"
    - scoped_type_identifier: "std::vec::Vec" -> return full path

    Args:
        type_node: A tree-sitter type node.
        source: Source bytes for extracting text.

    Returns:
        The base type identifier string.
    """
    if type_node.type == "type_identifier":
        # Simple type like User
        return node_text(type_node, source)

    if type_node.type == "generic_type":
        # Generic type like Writer<'a, M, W>
        # The 'type' field contains the base type identifier
        base_type = _find_child_by_field(type_node, "type")
        if base_type:
            return _extract_base_type_name(base_type, source)
        # Fallback: return full text
        return node_text(type_node, source)  # pragma: no cover

    if type_node.type == "reference_type":
        # Reference type like &'a M
        # The 'type' field contains the inner type
        inner_type = _find_child_by_field(type_node, "type")
        if inner_type:
            return _extract_base_type_name(inner_type, source)
        # Fallback: return full text
        return node_text(type_node, source)  # pragma: no cover

    if type_node.type == "scoped_type_identifier":
        # Qualified type like std::vec::Vec - keep full path
        return node_text(type_node, source)

    # Other type nodes - return as-is
    return node_text(type_node, source)


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
                return _extract_base_type_name(type_node, source)
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
            attr_text = node_text(sibling, source)
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
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> FileAnalysis:
    """Extract symbols from a single Rust file.

    Uses iterative tree traversal to avoid RecursionError on deeply nested code.
    """
    analysis = FileAnalysis()

    for node in iter_tree(tree.root_node):
        # Function declaration
        if node.type == "function_item":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                func_name = node_text(name_node, source)
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
                    id=make_symbol_id("rust", str(file_path), start_line, end_line, full_name, kind),
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
                    origin_run_id=run_id,
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
                struct_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract annotations for YAML pattern matching (e.g., derive macros)
                annotations = _extract_rust_annotations(node, source)
                meta = {"annotations": annotations} if annotations else None

                symbol = Symbol(
                    id=make_symbol_id("rust", str(file_path), start_line, end_line, struct_name, "struct"),
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
                    origin_run_id=run_id,
                    meta=meta,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[struct_name] = symbol

        # Enum declaration
        elif node.type == "enum_item":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                enum_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract annotations for YAML pattern matching
                annotations = _extract_rust_annotations(node, source)
                meta = {"annotations": annotations} if annotations else None

                symbol = Symbol(
                    id=make_symbol_id("rust", str(file_path), start_line, end_line, enum_name, "enum"),
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
                    origin_run_id=run_id,
                    meta=meta,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[enum_name] = symbol

        # Trait declaration
        elif node.type == "trait_item":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                trait_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract annotations for YAML pattern matching
                annotations = _extract_rust_annotations(node, source)
                meta = {"annotations": annotations} if annotations else None

                symbol = Symbol(
                    id=make_symbol_id("rust", str(file_path), start_line, end_line, trait_name, "trait"),
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
                    origin_run_id=run_id,
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

                    if field_node and node_text(field_node, source) == "route":
                        # Found .route() call - extract arguments
                        args_node = find_child_by_type(child, "arguments")
                        if not args_node:  # pragma: no cover
                            continue

                        route_path = None
                        for arg in args_node.children:
                            if arg.type == "string_literal" and route_path is None:
                                route_path = node_text(arg, source).strip('"')
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
            method_name = node_text(func_node, source)
            if method_name in AXUM_HTTP_METHODS:
                args_node = find_child_by_type(current_call, "arguments")
                if args_node:
                    for arg in args_node.children:
                        if arg.type == "identifier":
                            handler_name = node_text(arg, source)
                            break

        # Check for chained methods like get(h1).post(h2)
        elif func_node.type == "field_expression":
            field_node = _find_child_by_field(func_node, "field")
            value_node = _find_child_by_field(func_node, "value")

            if field_node:
                method_name = node_text(field_node, source)
                if method_name in AXUM_HTTP_METHODS:
                    args_node = find_child_by_type(current_call, "arguments")
                    if args_node:
                        for arg in args_node.children:
                            if arg.type == "identifier":
                                handler_name = node_text(arg, source)
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
                func_name = node_text(name_node, source)
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
        as_clause = find_child_by_type(node, "use_as_clause")
        if as_clause:
            # Find the scoped_identifier (foo::bar) and alias (baz)
            path_node = find_child_by_type(as_clause, "scoped_identifier")
            if not path_node:
                path_node = find_child_by_type(as_clause, "identifier")
            alias_node = find_child_by_type(as_clause, "identifier")
            # The alias is typically the last identifier child
            for child in as_clause.children:
                if child.type == "identifier":
                    alias_node = child
            if path_node and alias_node:
                full_path = node_text(path_node, source)
                alias = node_text(alias_node, source)
                if alias and full_path:
                    aliases[alias] = full_path
            continue

        # Handle regular 'use foo::bar;' - scoped_identifier
        path_node = find_child_by_type(node, "scoped_identifier")
        if path_node:
            full_path = node_text(path_node, source)
            if full_path and "::" in full_path:
                # Last segment is the imported name
                name = full_path.rsplit("::", 1)[-1]
                if name:
                    aliases[name] = full_path
            continue

        # Handle simple 'use foo;'
        id_node = find_child_by_type(node, "identifier")
        if id_node:
            name = node_text(id_node, source)
            if name:
                aliases[name] = name

    return aliases


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run_id: str,
    resolver: "NameResolver",
    use_aliases: dict[str, str],
) -> list[Edge]:
    """Extract call and import edges from a file.

    Uses iterative tree traversal to avoid RecursionError on deeply nested code.

    Args:
        use_aliases: Dict mapping local names to import paths for disambiguation.
    """
    edges: list[Edge] = []
    file_id = make_file_id("rust", str(file_path))

    for node in iter_tree(tree.root_node):
        # Detect use statements
        if node.type == "use_declaration":
            # Extract the path being imported
            path_node = find_child_by_type(node, "scoped_identifier")
            if not path_node:
                path_node = find_child_by_type(node, "identifier")
            if not path_node:
                path_node = find_child_by_type(node, "use_wildcard")
            if not path_node:
                path_node = find_child_by_type(node, "use_list")

            if path_node:
                import_path = node_text(path_node, source)
                edges.append(Edge.create(
                    src=file_id,
                    dst=f"rust:{import_path}:0-0:module:module",
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    evidence_type="use_declaration",
                    confidence=0.95,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

        # Detect function calls
        elif node.type == "call_expression":
            current_function = _get_enclosing_function(node, source, local_symbols)
            if current_function is not None:
                func_node = _find_child_by_field(node, "function")
                if func_node:
                    # Get the function name being called
                    if func_node.type == "identifier":
                        callee_name = node_text(func_node, source)
                    elif func_node.type == "field_expression":
                        # method call like foo.bar()
                        field_node = _find_child_by_field(func_node, "field")
                        if field_node:
                            callee_name = node_text(field_node, source)
                        else:
                            callee_name = None
                    elif func_node.type == "scoped_identifier":
                        # qualified call like Foo::bar()
                        name_node = _find_child_by_field(func_node, "name")
                        if name_node:
                            callee_name = node_text(name_node, source)
                        else:
                            callee_name = node_text(func_node, source)
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
                                origin_run_id=run_id,
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
                                    origin_run_id=run_id,
                                ))

    return edges


def _extract_attribute_edges(
    symbols: list[Symbol],
    global_symbols: dict[str, Symbol],
    run_id: str,
) -> list[Edge]:
    """Extract decorated_by edges from Rust attribute metadata.

    Creates edges from symbols to their attributes. For example,
    #[test] on a function creates a decorated_by edge from the function
    to the test attribute.

    Args:
        symbols: All symbols extracted from the codebase.
        global_symbols: Map of symbol names to Symbol objects for resolution.
        run_id: The current analysis run execution ID for provenance.

    Returns:
        List of decorated_by edges.
    """
    edges: list[Edge] = []

    for sym in symbols:
        if sym.meta is None:
            continue

        annotations = sym.meta.get("annotations")
        if not annotations or not isinstance(annotations, list):
            continue

        for annotation in annotations:
            if not isinstance(annotation, dict):  # pragma: no cover
                continue

            attr_name = annotation.get("name")
            if not attr_name or not isinstance(attr_name, str):  # pragma: no cover
                continue

            # Try to resolve the attribute to a symbol
            # For qualified names like "actix_web::get", try both full and short name
            attr_sym = global_symbols.get(attr_name)
            if not attr_sym and "::" in attr_name:
                short_name = attr_name.rsplit("::", 1)[-1]
                attr_sym = global_symbols.get(short_name)

            line = sym.span.start_line if sym.span else 0

            if attr_sym:
                # Resolved attribute
                edge = Edge.create(
                    src=sym.id,
                    dst=attr_sym.id,
                    edge_type="decorated_by",
                    line=line,
                    confidence=0.95,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    evidence_type="ast_attribute",
                )
                edges.append(edge)
            else:
                # Unresolved attribute - create unresolved edge
                dst_id = f"rust:unresolved:0-0:{attr_name}:unresolved"
                edge = Edge.create(
                    src=sym.id,
                    dst=dst_id,
                    edge_type="decorated_by",
                    line=line,
                    confidence=0.80,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    evidence_type="ast_attribute",
                )
                edges.append(edge)

    return edges


class RustAnalyzer(TreeSitterAnalyzer):
    """Rust language analyzer using tree-sitter-rust."""

    lang = "rust"
    file_patterns: ClassVar[list[str]] = ["*.rs"]
    grammar_module = "tree_sitter_rust"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract functions, structs, enums, traits from a Rust file."""
        return _extract_symbols_from_file(tree, source, rel_path, run.execution_id)

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract Rust use statement aliases for disambiguation."""
        return _extract_use_aliases(tree, source)

    def register_symbol(
        self, symbol: Symbol, global_symbols: dict,
    ) -> None:
        """Register symbol globally, including short name for cross-file resolution."""
        global_symbols[symbol.name] = symbol
        # Store by short name (last segment after ::) for cross-file resolution
        short_name = symbol.name.split("::")[-1] if "::" in symbol.name else symbol.name
        global_symbols[short_name] = symbol

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call and use edges from a Rust file."""
        return _extract_edges_from_file(
            tree, source, rel_path,
            local_symbols, global_symbols,
            run.execution_id, resolver, import_aliases,
        )

    def extract_usage_contexts_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        symbol_by_name: dict[str, Symbol],
    ) -> list[UsageContext]:
        """Extract Axum route usage contexts from a Rust file."""
        return _extract_axum_usage_contexts(
            tree.root_node, source, file_path, symbol_by_name,
        )

    def post_process(
        self,
        symbols: list[Symbol],
        edges: list[Edge],
        usage_contexts: list[UsageContext],
        run: "AnalysisRun",
    ) -> tuple[list[Symbol], list[Edge], list[UsageContext]]:
        """Extract decorated_by edges from attribute metadata."""
        # Build global symbols map for attribute resolution
        global_symbols: dict[str, Symbol] = {}
        for sym in symbols:
            global_symbols[sym.name] = sym
            short_name = sym.name.split("::")[-1] if "::" in sym.name else sym.name
            global_symbols[short_name] = sym

        attribute_edges = _extract_attribute_edges(symbols, global_symbols, run.execution_id)
        edges.extend(attribute_edges)
        return symbols, edges, usage_contexts


_analyzer = RustAnalyzer()


def is_rust_tree_sitter_available() -> bool:
    """Check if tree-sitter with Rust grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("rust")
def analyze_rust(repo_root: Path) -> AnalysisResult:
    """Analyze Rust files in a repository."""
    return _analyzer.analyze(repo_root)
