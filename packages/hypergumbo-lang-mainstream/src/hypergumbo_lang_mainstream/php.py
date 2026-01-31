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

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol, UsageContext
from hypergumbo_core.symbol_resolution import ListNameResolver, NameResolver
from hypergumbo_core.analyze.base import iter_tree

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "php-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# Laravel HTTP route methods - used by _extract_laravel_routes
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
    usage_contexts: list[UsageContext] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"php:{path}:{start_line}-{end_line}:{name}:{kind}"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _extract_use_aliases(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    r"""Extract use statements for disambiguation.

    In PHP:
        use Namespace\ClassName; -> ClassName maps to Namespace\ClassName
        use Namespace\ClassName as Alias; -> Alias maps to Namespace\ClassName

    Returns a dict mapping short names to full qualified names.
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "namespace_use_declaration":
            continue

        # Each use declaration can have multiple clauses
        for child in node.children:
            if child.type == "namespace_use_clause":
                # Find qualified_name and check for alias
                path_node = None
                alias_name = None
                has_as = False

                for sub in child.children:
                    if sub.type == "qualified_name":
                        path_node = sub
                    elif sub.type == "as":
                        has_as = True
                    elif sub.type == "name" and has_as:
                        # This is the alias after 'as'
                        alias_name = _node_text(sub, source)

                if path_node:
                    full_path = _node_text(path_node, source)
                    if alias_name:
                        aliases[alias_name] = full_path
                    else:
                        # Use last component of namespace path
                        short_name = full_path.rsplit("\\", 1)[-1]
                        if short_name:
                            aliases[short_name] = full_path

    return aliases


def _find_name_in_children(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Find identifier name in node's children."""
    for child in node.children:
        if child.type == "name":
            return _node_text(child, source)
    return None


def _extract_base_classes_php(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract base class and interface names from class declaration.

    Handles:
    - class Foo extends Bar
    - class Foo implements IBar
    - class Foo extends Bar implements IBaz, IQux

    Args:
        node: class_declaration node
        source: Source code bytes

    Returns:
        List of base class/interface names
    """
    base_classes: list[str] = []

    for child in node.children:
        # extends clause: class Foo extends Bar
        if child.type == "base_clause":
            for sub in child.children:
                if sub.type == "name":
                    base_classes.append(_node_text(sub, source))
                elif sub.type == "qualified_name":
                    # Fully qualified: extends \Namespace\Class
                    base_classes.append(_node_text(sub, source))
        # implements clause: class Foo implements IBar, IBaz
        elif child.type == "class_interface_clause":
            for sub in child.children:
                if sub.type == "name":
                    base_classes.append(_node_text(sub, source))
                elif sub.type == "qualified_name":
                    base_classes.append(_node_text(sub, source))

    return base_classes


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


def _extract_controller_action(
    args_node: "tree_sitter.Node", source: bytes
) -> str | None:
    """Extract controller@action from Laravel route second argument.

    Supports two syntaxes:
    - Array: [Controller::class, 'action']
    - String: 'Controller@action'

    Returns:
        String like 'UserController@index' or None if not extractable.
    """
    arg_index = 0
    for child in args_node.children:
        if child.type != "argument":
            continue

        if arg_index == 0:
            # First arg is route path, skip
            arg_index += 1
            continue

        if arg_index == 1:
            # Second arg is controller reference
            for arg_child in child.children:
                # Array syntax: [Controller::class, 'action']
                if arg_child.type == "array_creation_expression":
                    controller = None
                    action = None
                    for arr_child in arg_child.children:
                        if arr_child.type == "array_element_initializer":
                            for elem in arr_child.children:
                                if elem.type == "class_constant_access_expression":
                                    # Controller::class - first name child is the class
                                    for cc in elem.children:
                                        if cc.type == "name":
                                            controller = _node_text(cc, source)
                                            break
                                elif elem.type == "encapsed_string":
                                    # "action" with double-quoted encapsed_string
                                    for str_child in elem.children:  # pragma: no cover
                                        if str_child.type == "string_content":
                                            action = _node_text(str_child, source)
                                            break
                                elif elem.type == "string":
                                    # 'action' with single-quoted string
                                    for str_child in elem.children:
                                        if str_child.type == "string_content":
                                            action = _node_text(str_child, source)
                                            break
                    if controller and action:
                        return f"{controller}@{action}"

                # String syntax: 'Controller@action'
                elif arg_child.type in ("string", "encapsed_string"):
                    for str_child in arg_child.children:
                        if str_child.type == "string_content":
                            text = _node_text(str_child, source)
                            if "@" in text:
                                return text
            break

        arg_index += 1  # pragma: no cover - loop breaks at arg_index == 1

    return None


def _extract_laravel_routes(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
) -> tuple[list[UsageContext], list[Symbol]]:
    """Extract UsageContext records AND Symbol objects for Laravel Route facade calls.

    Detects patterns like:
    - Route::get('/users', [Controller::class, 'action'])
    - Route::post('/login', 'Controller@action')
    - Route::resource('photos', Controller::class)
    - Route::apiResource('posts', Controller::class)

    Returns:
        Tuple of (UsageContext list, Symbol list) for YAML pattern matching.
        Symbols have kind="route" which enables route-handler linking.
    """
    contexts: list[UsageContext] = []
    route_symbols: list[Symbol] = []

    for node in iter_tree(tree.root_node):
        if node.type != "scoped_call_expression":
            continue

        scope_node = node.child_by_field_name("scope")
        name_node = node.child_by_field_name("name")

        if not scope_node or not name_node:  # pragma: no cover - defensive
            continue

        # Check if this is Route::method()
        scope_text = _node_text(scope_node, source)
        if scope_text != "Route":
            continue

        method_name = _node_text(name_node, source).lower()

        # HTTP method routes
        if method_name in LARAVEL_HTTP_METHODS:
            http_method = LARAVEL_HTTP_METHODS[method_name]
        elif method_name in ("resource", "apiresource"):
            http_method = "RESOURCE"
        elif method_name == "match":
            http_method = "MATCH"
        elif method_name == "any":
            http_method = "ANY"
        else:  # pragma: no cover - unknown Route:: method
            continue

        # Extract route path from first argument
        route_path = None
        controller_action = None
        args_node = node.child_by_field_name("arguments")
        if args_node:
            for child in args_node.children:
                if child.type == "argument":
                    for arg_child in child.children:
                        if arg_child.type == "string":
                            for str_child in arg_child.children:
                                if str_child.type == "string_content":
                                    route_path = _node_text(str_child, source)
                                    break
                            if route_path is None:  # pragma: no cover
                                raw = _node_text(arg_child, source)
                                route_path = raw.strip("'\"")
                            break
                    break

            # Extract controller@action from second argument
            controller_action = _extract_controller_action(args_node, source)

        if not route_path:
            continue

        # Normalize route path
        normalized_path = route_path if route_path.startswith("/") else f"/{route_path}"

        # Build metadata
        metadata: dict[str, str] = {
            "route_path": normalized_path,
            "http_method": http_method,
        }
        if controller_action:
            metadata["controller_action"] = controller_action

        # Create UsageContext
        span = Span(
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            start_col=node.start_point[1],
            end_col=node.end_point[1],
        )

        ctx = UsageContext.create(
            kind="call",
            context_name=method_name,
            position="args[0]",
            path=str(file_path),
            span=span,
            symbol_ref=None,
            metadata=metadata,
        )
        contexts.append(ctx)

        # Create route Symbol(s) - enables route-handler linking
        if http_method == "RESOURCE":
            # Laravel resource creates 7 RESTful routes
            # Extract controller from second arg for resource routes
            controller = None
            if args_node:
                arg_index = 0
                for child in args_node.children:
                    if child.type != "argument":
                        continue
                    if arg_index == 1:
                        for arg_child in child.children:
                            if arg_child.type == "class_constant_access_expression":
                                # Controller::class - first name child is the controller
                                for cc in arg_child.children:
                                    if cc.type == "name":
                                        controller = _node_text(cc, source)
                                        break
                        break
                    arg_index += 1

            if controller:
                restful_routes = [
                    ("GET", normalized_path, "index"),
                    ("GET", f"{normalized_path}/create", "create"),
                    ("POST", normalized_path, "store"),
                    ("GET", f"{normalized_path}/{{id}}", "show"),
                    ("GET", f"{normalized_path}/{{id}}/edit", "edit"),
                    ("PUT", f"{normalized_path}/{{id}}", "update"),
                    ("DELETE", f"{normalized_path}/{{id}}", "destroy"),
                ]
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
                        language="php",
                        path=str(file_path),
                        span=span,
                        meta={
                            "http_method": http_meth,
                            "route_path": route_pth,
                            "controller_action": f"{controller}@{action}",
                        },
                        origin=run.pass_id,
                        origin_run_id=run.execution_id,
                    )
                    route_symbols.append(route_symbol)
        else:
            # Single HTTP method route
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
                language="php",
                path=str(file_path),
                span=span,
                meta={
                    "http_method": http_method,
                    "route_path": normalized_path,
                },
                origin=run.pass_id,
                origin_run_id=run.execution_id,
            )
            if controller_action:
                route_symbol.meta["controller_action"] = controller_action
            route_symbols.append(route_symbol)

    return contexts, route_symbols


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
    use_aliases: dict[str, str] = field(default_factory=dict)


def _extract_symbols(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
) -> list[Symbol]:
    """Extract symbols from a parsed PHP tree (pass 1)."""
    symbols: list[Symbol] = []

    for node in iter_tree(tree.root_node):
        # Function declarations
        if node.type == "function_definition":
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

                # Extract base classes and interfaces
                base_classes = _extract_base_classes_php(node, source)
                meta = {"base_classes": base_classes} if base_classes else None

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "class"),
                    name=name,
                    kind="class",
                    language="php",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta=meta,
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
    symbol_resolver: NameResolver | None = None,
    method_resolver: ListNameResolver | None = None,
    class_resolver: NameResolver | None = None,
    use_aliases: dict[str, str] | None = None,
) -> list[Edge]:
    """Extract edges from a parsed PHP tree (pass 2).

    Uses global symbol registries to resolve cross-file references.

    Args:
        use_aliases: Optional dict mapping short names to full qualified names for disambiguation.
    """
    if symbol_resolver is None:  # pragma: no cover - defensive
        symbol_resolver = NameResolver(global_symbols)
    if method_resolver is None:  # pragma: no cover - defensive
        method_resolver = ListNameResolver(global_methods)
    if class_resolver is None:  # pragma: no cover - defensive
        class_resolver = NameResolver(global_classes)
    if use_aliases is None:  # pragma: no cover - defensive default
        use_aliases = {}
    edges: list[Edge] = []

    for node in iter_tree(tree.root_node):
        # Function calls: func_name()
        if node.type == "function_call_expression":
            func_node = node.child_by_field_name("function")
            if func_node and func_node.type == "name":
                callee_name = _node_text(func_node, source)
                current_function = _get_enclosing_function_php(node, source, file_path, global_symbols)
                if current_function:
                    # Use use_aliases for disambiguation
                    path_hint = use_aliases.get(callee_name)
                    lookup_result = symbol_resolver.lookup(callee_name, path_hint=path_hint)
                    if lookup_result.found and lookup_result.symbol is not None:
                        edge = Edge.create(
                            src=current_function.id,
                            dst=lookup_result.symbol.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            confidence=0.95 * lookup_result.confidence,
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
                        lookup_result = symbol_resolver.lookup(full_name)
                        if lookup_result.found and lookup_result.symbol is not None:
                            edge = Edge.create(
                                src=current_function.id,
                                dst=lookup_result.symbol.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                confidence=0.95 * lookup_result.confidence,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="ast_method_this",
                            )
                            edges.append(edge)
                    else:
                        # Try to resolve to any method with this name
                        lookup_result = method_resolver.lookup(method_name)
                        if lookup_result.found and lookup_result.candidates:
                            # Use lower confidence since we can't be sure of the type
                            for target_sym in lookup_result.candidates:
                                edge = Edge.create(
                                    src=current_function.id,
                                    dst=target_sym.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    confidence=0.60 * lookup_result.confidence,
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

                    # Resolve class alias if present
                    resolved_class = use_aliases.get(class_name, class_name)
                    full_name = f"{resolved_class}.{method_name}"
                    path_hint = use_aliases.get(class_name)
                    lookup_result = symbol_resolver.lookup(full_name, path_hint=path_hint)
                    if lookup_result.found and lookup_result.symbol is not None:
                        edge = Edge.create(
                            src=current_function.id,
                            dst=lookup_result.symbol.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            confidence=0.95 * lookup_result.confidence,
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
                        # Use use_aliases for disambiguation
                        path_hint = use_aliases.get(class_name)
                        lookup_result = class_resolver.lookup(class_name, path_hint=path_hint)
                        if lookup_result.found and lookup_result.symbol is not None:
                            edge = Edge.create(
                                src=current_function.id,
                                dst=lookup_result.symbol.id,
                                edge_type="instantiates",
                                line=node.start_point[0] + 1,
                                confidence=0.95 * lookup_result.confidence,
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
    use_aliases = _extract_use_aliases(tree, source)

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

    edges = _extract_edges(
        tree, source, file_path, run, global_symbols, global_methods, global_classes,
        use_aliases=use_aliases,
    )
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
            use_aliases = _extract_use_aliases(tree, source)
            parsed_files.append(_ParsedFile(
                path=file_path, tree=tree, source=source, use_aliases=use_aliases
            ))
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
    symbol_resolver = NameResolver(global_symbols)
    method_resolver = ListNameResolver(global_methods)
    class_resolver = NameResolver(global_classes)
    all_edges: list[Edge] = []
    for pf in parsed_files:
        edges = _extract_edges(
            pf.tree, pf.source, pf.path, run,
            global_symbols, global_methods, global_classes,
            symbol_resolver, method_resolver, class_resolver,
            use_aliases=pf.use_aliases,
        )
        all_edges.extend(edges)

    # Pass 3: Extract UsageContexts and route symbols for framework pattern matching
    all_usage_contexts: list[UsageContext] = []
    for pf in parsed_files:
        contexts, route_symbols = _extract_laravel_routes(pf.tree, pf.source, pf.path, run)
        all_usage_contexts.extend(contexts)
        all_symbols.extend(route_symbols)

    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return PhpAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        usage_contexts=all_usage_contexts,
        run=run,
    )
