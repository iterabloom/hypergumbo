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
1. Pass 1: Extract functions, structs, enums, traits with signatures and annotations;
   extract struct field types for the base-class field type registry
2. Pass 2: Extract call edges (including self.field.method() via Strategy 1.5),
   use edges, and Axum usage contexts
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
    make_typed_stable_id,
    node_text,
    visibility_from_modifiers,
)
from hypergumbo_core.analyze.registry import register_analyzer

from hypergumbo_core.symbol_resolution import ListNameResolver, LookupResult

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("rust")

# Standard library wrapper types that implement Deref to their inner type.
# When resolving struct field types, these are unwrapped to expose the
# effective dispatch type (e.g. Box<App> → App, Arc<Client> → Client).
_RUST_DEREF_WRAPPERS: frozenset[str] = frozenset({
    "Box", "Arc", "Rc", "Mutex", "RwLock", "RefCell", "Cell",
    "Pin", "MutexGuard", "RwLockReadGuard", "RwLockWriteGuard",
})

# Async spawn functions that schedule a task on an executor.
# When we see e.g. tokio::spawn(my_task), the interesting call target
# is ``my_task``, not ``spawn`` itself.
_SPAWN_FUNCTIONS: frozenset[str] = frozenset({
    "tokio::spawn", "tokio::task::spawn",
    "tokio::task::spawn_blocking",
    "rayon::spawn", "rayon::spawn_fifo",
    "async_std::task::spawn",
    "async_std::task::spawn_blocking",
})

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


def normalize_rust_signature(
    signature: str | None,
    type_params: list[str] | None = None,
) -> str | None:
    """Normalize a Rust signature for typed stable_id (ADR-0014 §3)."""
    from hypergumbo_core.analyze.base import normalize_signature_names_first
    return normalize_signature_names_first(
        signature, type_params, return_sep="->", skip_self=True,
    )


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


def _extract_modifiers_rust(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract visibility modifiers from a Rust declaration node.

    Rust tree-sitter uses a ``visibility_modifier`` child node containing
    ``pub`` optionally followed by a scope like ``(crate)`` or ``(super)``.
    Items without ``visibility_modifier`` are private by default.

    Returns e.g. ``["pub"]``, ``["pub(crate)"]``, or ``[]`` (private).
    """
    modifiers: list[str] = []
    for child in node.children:
        if child.type == "visibility_modifier":
            vis_text = child.text.decode("utf-8", errors="replace") if child.text else "pub"
            modifiers.append(vis_text)
    return modifiers


def _is_inside_cfg_test(
    node: "tree_sitter.Node", source: bytes,
) -> bool:
    """Check whether *node* is nested inside a ``#[cfg(test)]`` module.

    Walks ancestor nodes looking for a ``mod_item`` whose preceding sibling
    attributes include ``#[cfg(test)]``.  This catches the idiomatic Rust
    pattern::

        #[cfg(test)]
        mod tests {
            fn helper() { ... }   // <-- should be marked as test code
        }

    Individual functions already carry their own ``#[test]`` annotation, but
    helper functions and other items inside the module do not.  This function
    bridges that gap so that ``is_test_node`` in the slicer correctly
    excludes them from production slices.
    """
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.type == "mod_item":
            for ann in _extract_rust_annotations(ancestor, source):
                name = ann.get("name", "")
                if name == "cfg" and "test" in ann.get("args", []):
                    return True
        ancestor = ancestor.parent
    return False


def _unwrap_deref_type(type_node: "tree_sitter.Node", source: bytes) -> str:
    """Unwrap Deref-implementing wrapper types to get the inner type.

    Recursively strips ``Box<Arc<T>>`` → ``T``.  When the outermost type
    is not a known wrapper (or the node is a plain ``type_identifier``),
    returns the type text as-is.  References (``&T``, ``&mut T``) are
    also unwrapped.

    Args:
        type_node: A tree-sitter type node from a ``field_declaration``.
        source: Source bytes for text extraction.

    Returns:
        The innermost non-wrapper type name string.
    """
    # Reference types: &T, &mut T → unwrap to inner type
    if type_node.type == "reference_type":
        inner = type_node.child_by_field_name("type")
        if inner:
            return _unwrap_deref_type(inner, source)
        return node_text(type_node, source)  # pragma: no cover — defensive

    # Generic type: Box<T>, Arc<Mutex<T>> → check if wrapper
    if type_node.type == "generic_type":
        base = type_node.child_by_field_name("type")
        if base:
            base_name = node_text(base, source)
            if base_name in _RUST_DEREF_WRAPPERS:
                # Find the type_arguments child and extract the first argument
                args_node = find_child_by_type(type_node, "type_arguments")
                if args_node:
                    for child in args_node.children:
                        if child.is_named:
                            return _unwrap_deref_type(child, source)
            return base_name
        return node_text(type_node, source)  # pragma: no cover — defensive

    # type_identifier: plain type name
    if type_node.type == "type_identifier":
        return node_text(type_node, source)

    # scoped_type_identifier: e.g. std::sync::Mutex
    if type_node.type == "scoped_type_identifier":
        return node_text(type_node, source)

    return node_text(type_node, source)


def _extract_struct_field_types(
    tree: "tree_sitter.Tree", source: bytes,
) -> dict[str, dict[str, str]]:
    """Extract field name → type mappings from struct declarations.

    Walks all ``struct_item`` nodes and their ``field_declaration_list``
    children. Wrapper types (``Box``, ``Arc``, etc.) are unwrapped via
    ``_unwrap_deref_type`` so the registry contains effective dispatch types.

    Tuple structs (``struct Foo(Bar)``) are skipped — their fields are
    positional, not named, so ``self.0.method()`` patterns don't apply.

    Args:
        tree: Parsed tree-sitter tree for a single file.
        source: Source bytes for text extraction.

    Returns:
        Mapping of struct_name → {field_name → type_name}.
    """
    result: dict[str, dict[str, str]] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "struct_item":
            continue
        name_node = node.child_by_field_name("name")
        if not name_node:
            continue  # pragma: no cover — defensive; struct_item always has name
        struct_name = node_text(name_node, source)

        # Find the field_declaration_list (named struct, not tuple struct)
        body = find_child_by_type(node, "field_declaration_list")
        if not body:
            continue

        fields: dict[str, str] = {}
        for child in body.children:
            if child.type != "field_declaration":
                continue
            field_name_node = child.child_by_field_name("name")
            field_type_node = child.child_by_field_name("type")
            if field_name_node and field_type_node:
                fname = node_text(field_name_node, source)
                ftype = _unwrap_deref_type(field_type_node, source)
                fields[fname] = ftype

        if fields:
            result[struct_name] = fields

    return result


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

                # Inherit #[cfg(test)] from enclosing module so slicer
                # can exclude helper functions inside test modules.
                if _is_inside_cfg_test(node, source):
                    cfg_test_ann = {
                        "name": "cfg", "args": ["test"], "kwargs": {},
                    }
                    if cfg_test_ann not in annotations:
                        annotations = [*annotations, cfg_test_ann]

                meta: dict[str, object] | None = None
                if annotations:
                    meta = {"annotations": annotations}

                modifiers = _extract_modifiers_rust(node, source)

                # Typed stable_id (ADR-0014 §3)
                norm_sig = normalize_rust_signature(signature)
                stable_id = make_typed_stable_id(
                    kind, norm_sig, visibility_from_modifiers(modifiers),
                ) if norm_sig else None

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
                    stable_id=stable_id,
                    signature=signature,
                    meta=meta,
                    modifiers=modifiers,
                    lines_of_code=end_line - start_line + 1,
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
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
                if _is_inside_cfg_test(node, source):
                    cfg_test_ann = {
                        "name": "cfg", "args": ["test"], "kwargs": {},
                    }
                    if cfg_test_ann not in annotations:
                        annotations = [*annotations, cfg_test_ann]
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
                    modifiers=_extract_modifiers_rust(node, source),
                    lines_of_code=end_line - start_line + 1,
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
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
                if _is_inside_cfg_test(node, source):
                    cfg_test_ann = {
                        "name": "cfg", "args": ["test"], "kwargs": {},
                    }
                    if cfg_test_ann not in annotations:
                        annotations = [*annotations, cfg_test_ann]
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
                    modifiers=_extract_modifiers_rust(node, source),
                    lines_of_code=end_line - start_line + 1,
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
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
                    modifiers=_extract_modifiers_rust(node, source),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    meta=meta,
                    lines_of_code=end_line - start_line + 1,
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[trait_name] = symbol

    # Extract struct field types for base-class field type registry.
    # Deref wrappers (Box, Arc, etc.) are unwrapped during extraction
    # so the registry contains effective dispatch types.
    analysis.class_field_types = _extract_struct_field_types(tree, source)

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
    span_index: dict[tuple[int, int], Symbol] | None = None,
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing function.

    Uses a span-based index to avoid the short-name collision where
    ``symbol_by_name["compute"]`` is overwritten by whichever impl
    block is processed last, causing call edges to get the wrong
    ``src``.  Falls back to name-based lookup when no span index is
    provided (backward compatibility).

    Args:
        node: The current node.
        source: Source bytes for extracting text.
        local_symbols: Map of function names to Symbol objects.
        span_index: Optional ``(start_line, end_line) → Symbol`` index.

    Returns:
        The Symbol for the enclosing function, or None if not inside a function.
    """
    current = node.parent
    while current is not None:
        if current.type == "function_item":
            # Prefer span-based lookup (immune to name collisions)
            if span_index is not None:
                start_line = current.start_point[0] + 1
                end_line = current.end_point[0] + 1
                sym = span_index.get((start_line, end_line))
                if sym is not None:
                    return sym
            # Fallback: name-based lookup
            name_node = _find_child_by_field(current, "name")
            if name_node:
                func_name = node_text(name_node, source)
                # Try qualified name first (e.g., "Diff::compute")
                impl_target = _get_impl_target(current, source)
                if impl_target:  # pragma: no cover - defensive fallback
                    qualified = f"{impl_target}::{func_name}"
                    if qualified in local_symbols:
                        return local_symbols[qualified]
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
    method_resolver: ListNameResolver | None = None,
    span_index: dict[tuple[int, int], Symbol] | None = None,
    field_type_registry: dict[str, dict[str, str]] | None = None,
    analyzer: "RustAnalyzer | None" = None,
) -> list[Edge]:
    """Extract call and import edges from a file.

    Uses iterative tree traversal to avoid RecursionError on deeply nested code.

    Args:
        use_aliases: Dict mapping local names to import paths for disambiguation.
        field_type_registry: Aggregated struct field types for receiver resolution.
        analyzer: RustAnalyzer instance for resolve_receiver_type() calls.
        method_resolver: Optional ListNameResolver with ambiguity_threshold for
            method-specific lookups.  When provided, used for method calls
            (``foo.bar()``) to guard against 3+ ambiguous candidates.
        span_index: Optional line-span index for enclosing function detection.
            Built from global_symbols to avoid name collisions in symbol_by_name.
    """
    edges: list[Edge] = []
    file_id = make_file_id("rust", str(file_path))

    for node in iter_tree(tree.root_node):
        # Detect trait implementations: impl Trait for Struct → implements edge
        if node.type == "impl_item":
            trait_node = node.child_by_field_name("trait")
            type_node = node.child_by_field_name("type")
            if trait_node and type_node:
                trait_name = _extract_base_type_name(trait_node, source)
                impl_type_name = _extract_base_type_name(type_node, source)
                if trait_name and impl_type_name:
                    trait_sym = local_symbols.get(trait_name) or global_symbols.get(trait_name)
                    # Fallback: for qualified names like module::Trait, try short name
                    if not trait_sym and "::" in trait_name:
                        short_trait = trait_name.rsplit("::", 1)[-1]
                        trait_sym = local_symbols.get(short_trait) or global_symbols.get(short_trait)
                    impl_sym = local_symbols.get(impl_type_name) or global_symbols.get(impl_type_name)
                    if trait_sym and impl_sym:
                        edges.append(Edge.create(
                            src=impl_sym.id,
                            dst=trait_sym.id,
                            edge_type="implements",
                            line=node.start_point[0] + 1,
                            evidence_type="trait_impl",
                            confidence=0.95,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        ))
                    elif not trait_sym and impl_sym:
                        # Unresolved trait: definition not in analyzed files
                        # (e.g., tonic gRPC traits generated from .proto).
                        # Create a lower-confidence edge so the relationship
                        # is captured even when the trait source isn't available.
                        short_name = trait_name.rsplit("::", 1)[-1] if "::" in trait_name else trait_name
                        if short_name not in _RUST_STD_TRAIT_NAMES:
                            unresolved_id = make_symbol_id(
                                "rust", "unresolved", 0, 0, short_name, "trait",
                            )
                            edges.append(Edge.create(
                                src=impl_sym.id,
                                dst=unresolved_id,
                                edge_type="implements",
                                line=node.start_point[0] + 1,
                                evidence_type="trait_impl_unresolved",
                                confidence=0.70,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                            ))

        # Detect use statements
        elif node.type == "use_declaration":
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
            current_function = _get_enclosing_function(node, source, local_symbols, span_index)
            if current_function is not None:
                func_node = _find_child_by_field(node, "function")
                if func_node:
                    # Unwrap generic_function (turbofish):
                    # x.collect::<Vec<i32>>() wraps a field_expression
                    # inside a generic_function node; similarly
                    # Foo::<T>::bar() wraps a scoped_identifier.
                    inner = func_node
                    if inner.type == "generic_function":
                        for child in inner.children:
                            if child.type in (
                                "identifier", "field_expression",
                                "scoped_identifier",
                            ):
                                inner = child
                                break
                    # Get the function name being called
                    is_method_call = False
                    full_scoped_name = None
                    if inner.type == "identifier":
                        callee_name = node_text(inner, source)
                    elif inner.type == "field_expression":
                        # method call like foo.bar()
                        is_method_call = True
                        field_node = _find_child_by_field(inner, "field")
                        if field_node:
                            callee_name = node_text(field_node, source)
                        else:
                            callee_name = None
                    elif inner.type == "scoped_identifier":
                        # Qualified call like Foo::bar() or
                        # Foo::<T>::bar() (turbofish).
                        # Extract full qualified name for precise lookup.
                        name_node = _find_child_by_field(inner, "name")
                        path_node = _find_child_by_field(inner, "path")
                        if name_node:
                            callee_name = node_text(name_node, source)
                        else:
                            callee_name = node_text(inner, source)
                        # Build clean scoped name stripping type args:
                        # "PublicParams::<E1,E2>::setup" → "PublicParams::setup"
                        if path_node and path_node.type == "generic_type":
                            type_id = next(
                                (c for c in path_node.children
                                 if c.type == "type_identifier"),
                                None,
                            )
                            if type_id and callee_name:
                                full_scoped_name = (
                                    f"{node_text(type_id, source)}"
                                    f"::{callee_name}"
                                )
                        if full_scoped_name is None:
                            full_scoped_name = node_text(inner, source)
                        # Resolve Self:: to actual type name from enclosing impl block
                        if full_scoped_name and full_scoped_name.startswith("Self::"):
                            impl_type = _get_impl_target(node, source)
                            if impl_type:
                                full_scoped_name = f"{impl_type}::{full_scoped_name[6:]}"
                    else:
                        callee_name = None

                    if callee_name:
                        resolved = False

                        # Async spawn detection: tokio::spawn(task()),
                        # tokio::task::spawn(task()), rayon::spawn(task())
                        # Create a call edge to the spawned task, not to spawn itself.
                        if full_scoped_name in _SPAWN_FUNCTIONS:
                            args_node = _find_child_by_field(node, "arguments")
                            if args_node:
                                for arg_child in args_node.children:
                                    if arg_child.type == "call_expression":
                                        # The spawned task is itself a call — it will
                                        # be processed when iter_tree reaches it, so
                                        # just skip creating an edge to spawn().
                                        pass
                                    elif arg_child.type == "async_block":
                                        # async { ... } — calls inside will be
                                        # visited by iter_tree normally.
                                        pass
                                    elif arg_child.type == "identifier":
                                        # tokio::spawn(my_task) — bare function ref
                                        ref_name = node_text(arg_child, source)
                                        target = local_symbols.get(ref_name)
                                        if target is None:
                                            lr = resolver.lookup(ref_name)
                                            if lr.found and lr.symbol is not None:
                                                target = lr.symbol
                                        if target is not None:
                                            edges.append(Edge.create(
                                                src=current_function.id,
                                                dst=target.id,
                                                edge_type="calls",
                                                line=node.start_point[0] + 1,
                                                evidence_type="async_spawn",
                                                confidence=0.85,
                                                origin=PASS_ID,
                                                origin_run_id=run_id,
                                            ))
                            # Don't resolve spawn itself as a callee
                            resolved = True

                        # Strategy 1: Try full scoped name first (e.g., "Diff::compute")
                        # This gives precise resolution for qualified calls.
                        if not resolved and full_scoped_name and full_scoped_name != callee_name:
                            if full_scoped_name in local_symbols:
                                callee = local_symbols[full_scoped_name]
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=callee.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="function_call",
                                    confidence=0.90,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                ))
                                resolved = True
                            else:
                                import_hint = use_aliases.get(callee_name)
                                lookup_result = resolver.lookup(
                                    full_scoped_name, path_hint=import_hint,
                                )
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
                                    resolved = True

                                # Strategy 1b: Strip module prefixes from
                                # the scoped name.  For
                                # codex_agent::CodexAgent::new, try
                                # "CodexAgent::new" before falling back to
                                # bare "new" (which has many ambiguous
                                # candidates).
                                if not resolved:
                                    parts = full_scoped_name.split("::")
                                    for i in range(1, len(parts) - 1):
                                        suffix = "::".join(parts[i:])
                                        if suffix in local_symbols:
                                            callee = local_symbols[suffix]
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
                                            resolved = True
                                            break
                                        lr = resolver.lookup(
                                            suffix, path_hint=import_hint,
                                        )
                                        if lr.found and lr.symbol is not None:
                                            edges.append(Edge.create(
                                                src=current_function.id,
                                                dst=lr.symbol.id,
                                                edge_type="calls",
                                                line=node.start_point[0] + 1,
                                                evidence_type="function_call",
                                                confidence=0.80 * lr.confidence,
                                                origin=PASS_ID,
                                                origin_run_id=run_id,
                                            ))
                                            resolved = True
                                            break

                        # Strategy 1.5: Resolve self.field.method() via
                        # field type registry.  Fires for method calls where
                        # the receiver is a field_expression rooted at self.
                        # Known receiver type makes even blocklisted methods
                        # (e.g. Client::send) unambiguous.
                        if (
                            not resolved
                            and is_method_call
                            and analyzer is not None
                            and field_type_registry
                        ):
                            value_node = inner.child_by_field_name("value")
                            if value_node is not None:
                                impl_target = _get_impl_target(
                                    node, source,
                                )
                                receiver_type = analyzer.resolve_receiver_type(
                                    value_node, source, impl_target,
                                )
                                if receiver_type is not None:
                                    typed_name = f"{receiver_type}::{callee_name}"
                                    target = (
                                        local_symbols.get(typed_name)
                                        or global_symbols.get(typed_name)
                                    )
                                    if target is None:
                                        lookup = resolver.lookup(typed_name)
                                        if lookup.found and lookup.symbol:
                                            target = lookup.symbol
                                    if target is not None:
                                        edges.append(Edge.create(
                                            src=current_function.id,
                                            dst=target.id,
                                            edge_type="calls",
                                            line=node.start_point[0] + 1,
                                            evidence_type="typed_field_call",
                                            confidence=0.88,
                                            origin=PASS_ID,
                                            origin_run_id=run_id,
                                        ))
                                        resolved = True

                        # Strategy 2: Fall back to short name
                        if not resolved:
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
                                # Use method_resolver (ambiguity guard: 3+ candidates
                                # → unresolved) for: (a) method calls (foo.bar()),
                                # (b) scoped identifier fallback (Type::new() where
                                # full "Type::new" wasn't found).  Both are
                                # method-like calls that should not resolve to
                                # arbitrary same-name symbols.
                                use_method_guard = (
                                    is_method_call or full_scoped_name is not None
                                )
                                if use_method_guard and method_resolver is not None:
                                    if callee_name in _RUST_GENERIC_TRAIT_METHODS:
                                        # Generic trait methods (.into(), .clone(),
                                        # .len(), etc.) cannot be resolved without
                                        # receiver type info — skip lookup to avoid
                                        # false edges.
                                        lookup_result = LookupResult(symbol=None)
                                    else:
                                        # Pass the caller's directory as path_hint
                                        # to prefer same-module methods over
                                        # cross-module ones with the same name
                                        # (e.g., nova/nifs.rs over
                                        # neutron/nifs.rs when called from nova/).
                                        caller_dir = (
                                            file_path.rsplit("/", 1)[0]
                                            if "/" in file_path else ""
                                        )
                                        lookup_result = method_resolver.lookup(
                                            callee_name,
                                            path_hint=caller_dir if caller_dir else None,
                                            soft_hint=True,
                                        )
                                else:
                                    import_hint = use_aliases.get(callee_name)
                                    lookup_result = resolver.lookup(callee_name, path_hint=import_hint)
                                if lookup_result.found and lookup_result.symbol is not None:
                                    confidence = 0.80 * lookup_result.confidence
                                    edges.append(Edge.create(
                                        src=current_function.id,
                                        dst=lookup_result.symbol.id,
                                        edge_type="calls",
                                        line=node.start_point[0] + 1,
                                        evidence_type="function_call",
                                        confidence=confidence,
                                        origin=PASS_ID,
                                        origin_run_id=run_id,
                                    ))

    return edges


# Compiler-provided attributes that must never resolve to user-defined symbols.
# Covers: testing, conditional compilation, diagnostics, code-generation hints,
# derive macros, linking, FFI, documentation, async runtimes (tokio), and
# serialization (serde).  Proc-macro *crate* attributes (e.g. ``serde``,
# ``tokio``) are included because the crate re-exports only derive/attribute
# macros — a user function named ``serde`` is never the intended target.
_BUILTIN_RUST_ATTRIBUTES: frozenset[str] = frozenset({
    # Testing
    "test", "bench", "ignore", "should_panic",
    # Conditional compilation
    "cfg", "cfg_attr",
    # Derive
    "derive",
    # Diagnostics / lints
    "allow", "warn", "deny", "forbid", "deprecated", "must_use",
    # Code generation
    "inline", "cold", "no_mangle", "track_caller", "target_feature",
    "instruction_set",
    # Linking / FFI
    "link", "link_name", "link_section", "no_link", "export_name",
    "link_ordinal", "no_builtins", "repr", "used",
    # Documentation
    "doc",
    # Module / crate level
    "path", "no_std", "no_implicit_prelude", "macro_use", "macro_export",
    "crate_type", "no_main", "recursion_limit", "type_length_limit",
    # Proc-macro
    "proc_macro", "proc_macro_derive", "proc_macro_attribute",
    # Type system
    "non_exhaustive",
    # Runtime
    "panic_handler", "global_allocator", "windows_subsystem",
    # Common ecosystem proc-macro crate names (not user functions)
    "serde", "tokio", "async_trait", "tracing", "instrument",
})

# Generic trait method names that create false-positive in-degree when resolved
# via method_resolver.  Without receiver type information, calls like `x.into()`
# or `v.push()` resolve to arbitrary concrete implementations (e.g.,
# `StatusRow::into` absorbing 817 `.into()` edges in penumbra).  These method
# names are blocked from short-name resolution; fully-scoped calls like
# `StatusRow::into()` still resolve via Strategy 1.
_RUST_GENERIC_TRAIT_METHODS: frozenset[str] = frozenset({
    # core::convert
    "into", "from", "try_into", "try_from",
    # core::fmt / Display / ToString
    "fmt", "to_string",
    # core::default
    "default",
    # core::clone
    "clone", "clone_from",
    # core::cmp / core::hash
    "eq", "ne", "partial_cmp", "cmp", "hash",
    # core::ops
    "deref", "deref_mut", "drop",
    # core::iter — combinators are on Iterator, Option, Result
    "next", "into_iter", "map", "filter", "fold", "collect", "flat_map",
    "find", "any", "all", "for_each",
    # core::convert (ref)
    "as_ref", "as_mut",
    # std collection methods (ambiguous without receiver type)
    "len", "is_empty", "push", "pop", "get", "insert", "remove", "contains",
    "iter", "iter_mut", "extend",
    # Option / Result combinators
    "and_then", "or_else", "map_err", "unwrap_or", "unwrap_or_else",
    "ok", "err", "expect", "ok_or", "ok_or_else",
    # serde
    "serialize", "deserialize",
    # core::str / parsing
    "from_str", "parse", "unwrap",
    # std::sync::atomic — .load()/.store() on AtomicU64, AtomicU8, etc.
    # Without receiver type info, x.load() conflates AtomicU64.load()
    # with domain-specific load() methods (WI-bakak: 22 false positives).
    "load", "store", "fetch_add", "fetch_sub", "compare_exchange", "swap",
    # std::io — Read/Write trait methods
    "read", "write", "flush",
    # Constructor / builder — ubiquitous across types
    "new", "build",
    # Channel / async
    "send", "recv",
    # Command — .output() conflates with test utilities (ripgrep bakeoff)
    "output", "status", "spawn",
    # Logging — ubiquitous across log/tracing crates
    "warn", "error", "info", "debug", "trace",
})


# Standard library trait names that should NOT generate unresolved implements
# edges.  These are ubiquitous auto-derived or manually-impl'd traits whose
# definitions are in std/core and won't be in the project's symbol registry.
# Creating unresolved edges for them would be pure noise — a developer never
# needs to know "MyStruct implements Clone" in the call graph.
_RUST_STD_TRAIT_NAMES: frozenset[str] = frozenset({
    # core::marker
    "Copy", "Send", "Sync", "Sized", "Unpin",
    # core::clone
    "Clone",
    # core::cmp
    "PartialEq", "Eq", "PartialOrd", "Ord",
    # core::fmt
    "Debug", "Display",
    # core::hash
    "Hash",
    # core::default
    "Default",
    # core::convert
    "From", "Into", "TryFrom", "TryInto", "AsRef", "AsMut",
    # core::ops
    "Deref", "DerefMut", "Drop", "Add", "Sub", "Mul", "Div", "Rem",
    "Neg", "Not", "BitAnd", "BitOr", "BitXor", "Shl", "Shr",
    "Index", "IndexMut", "Fn", "FnMut", "FnOnce",
    "AddAssign", "SubAssign", "MulAssign", "DivAssign",
    # core::iter
    "Iterator", "IntoIterator", "FromIterator", "ExactSizeIterator",
    "DoubleEndedIterator",
    # core::future
    "Future",
    # std::io
    "Read", "Write", "Seek", "BufRead",
    # std::error
    "Error",
    # serde (extremely common, not architectural)
    "Serialize", "Deserialize", "Serializer", "Deserializer",
    # std::string
    "ToString",
    # std::borrow
    "Borrow", "BorrowMut", "ToOwned",
})


def _extract_attribute_edges(
    symbols: list[Symbol],
    global_symbols: dict[str, Symbol],
    run_id: str,
) -> list[Edge]:
    """Extract decorated_by edges from Rust attribute metadata.

    Creates edges from symbols to their attributes. For example,
    ``#[my_macro]`` on a function creates a ``decorated_by`` edge from the
    function to the macro symbol (if resolvable).

    Built-in compiler attributes (``test``, ``cfg``, ``derive``, ``inline``,
    ``allow``, ``must_use``, …) are **never** resolved against
    ``global_symbols``.  Without this guard, a user-defined function named
    ``test`` would be incorrectly linked to every ``#[test]`` annotation in the
    crate — a common false-positive in test-heavy codebases (WI-votaj).

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

            # Built-in attributes must never resolve to user symbols.
            # Skip entirely — they have no user-space definition, and even
            # unresolved edges create noise (derive gets 175 in-edges).
            # For qualified names like "tracing::instrument", check both
            # the full name and the crate name (first path component).
            if attr_name in _BUILTIN_RUST_ATTRIBUTES:
                continue
            if "::" in attr_name:
                crate_name = attr_name.split("::", 1)[0]
                if crate_name in _BUILTIN_RUST_ATTRIBUTES:
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
        """Register symbol globally by qualified name.

        Does NOT register by short name for ``::``-qualified symbols
        (e.g., ``Diff::compute``). The suffix index in ``NameResolver``
        handles ``"compute"`` → ``"Diff::compute"`` lookups. Registering
        the short name caused false exact matches when multiple types
        share a method name (the last one registered won the key).
        """
        global_symbols[symbol.name] = symbol

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call and use edges from a Rust file."""
        # Build method resolver lazily (once per analyze() call).
        # Keyed on global_symbols identity to invalidate between runs.
        cache = getattr(self, "_mr_cache", None)
        if cache is None or cache[0] is not global_symbols:
            global_methods: dict[str, list[Symbol]] = {}
            for sym in global_symbols.values():
                if sym.kind in ("method", "function"):
                    short = sym.name.split("::")[-1] if "::" in sym.name else sym.name
                    global_methods.setdefault(short, []).append(sym)
            mr = ListNameResolver(global_methods, ambiguity_threshold=3)
            self._mr_cache = (global_symbols, mr)
        else:
            mr = cache[1]

        # Build span index from global_symbols for this file.
        # local_symbols (symbol_by_name) loses entries when short names
        # collide — e.g., free function "caller" is overwritten by
        # method "Foo::caller".  The global registry preserves all
        # qualified names, so filtering by path gives a complete set.
        # Symbols store paths as relative (rel_path), not absolute.
        file_syms = [
            s for s in global_symbols.values()
            if s.path == rel_path and s.kind in ("function", "method")
        ]
        span_idx = {(s.span.start_line, s.span.end_line): s for s in file_syms}

        return _extract_edges_from_file(
            tree, source, rel_path,
            local_symbols, global_symbols,
            run.execution_id, resolver, import_aliases,
            method_resolver=mr,
            span_index=span_idx,
            field_type_registry=self._field_type_registry,
            analyzer=self,
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
