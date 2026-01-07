"""Java analysis pass using tree-sitter-java.

This analyzer uses tree-sitter-java to parse Java files and extract:
- Class declarations (symbols)
- Interface declarations (symbols)
- Enum declarations (symbols)
- Method declarations (symbols)
- Constructor declarations (symbols)
- Method call relationships (edges)
- Inheritance relationships: extends, implements (edges)
- Instantiation: new ClassName() (edges)
- Native method declarations for JNI bridge detection

If tree-sitter-java is not installed, the analyzer gracefully degrades
and returns an empty result.

How It Works
------------
1. Check if tree-sitter and tree-sitter-java are available
2. If not available, return empty result (not an error, just no Java analysis)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls/inheritance and resolve against global symbol registry
4. Detect method calls, inheritance, and instantiation patterns

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Java support is separate from other languages to keep modules focused
- Two-pass allows cross-file call resolution and inheritance tracking
- Same pattern as C/PHP/JS analyzers for consistency
- Uses iterative traversal to avoid RecursionError on deeply nested code
"""
from __future__ import annotations

import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol
from .base import (
    AnalysisResult,
    is_grammar_available,
    iter_tree,
    make_symbol_id as _base_make_symbol_id,
    node_text as _node_text,
)

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "java-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# Backwards compatibility alias
JavaAnalysisResult = AnalysisResult


def find_java_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Java files in the repository."""
    yield from find_files(repo_root, ["*.java"])


def is_java_tree_sitter_available() -> bool:
    """Check if tree-sitter and Java grammar are available."""
    return is_grammar_available("tree_sitter_java")


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return _base_make_symbol_id("java", path, start_line, end_line, name, kind)


def _find_identifier_in_children(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Find identifier name in node's children."""
    for child in node.children:
        if child.type == "identifier":
            return _node_text(child, source)
    return None


def _get_class_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract class/interface/enum name from declaration."""
    return _find_identifier_in_children(node, source)


def _get_method_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract method name from method_declaration or constructor_declaration."""
    return _find_identifier_in_children(node, source)


def _extract_type_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract type text from a type node, handling generics and arrays."""
    return _node_text(node, source)


def _extract_java_signature(
    node: "tree_sitter.Node", source: bytes, is_constructor: bool = False
) -> Optional[str]:
    """Extract function signature from a Java method or constructor declaration.

    Returns signature like:
    - "(int a, int b) int" for regular methods
    - "(String message)" for void methods (no return type shown)
    - "(String name, int age)" for constructors (no return type)

    Args:
        node: The method_declaration or constructor_declaration node.
        source: The source code bytes.
        is_constructor: True if this is a constructor (no return type).

    Returns:
        The signature string, or None if extraction fails.
    """
    params_node = None
    return_type = None

    # Find formal_parameters and return type
    for child in node.children:
        if child.type == "formal_parameters":
            params_node = child
        # Return type appears before the identifier for methods
        # Types we care about: void_type, type_identifier, generic_type, array_type, and primitives
        elif child.type in ("void_type", "type_identifier", "generic_type", "array_type",
                            "integral_type", "floating_point_type", "boolean_type"):
            # Only capture if we haven't found params yet (return type comes before name)
            if params_node is None:
                return_type = _extract_type_text(child, source)

    if params_node is None:
        return None  # pragma: no cover

    # Extract parameters
    params: list[str] = []
    for child in params_node.children:
        if child.type == "formal_parameter":
            param_type = None
            param_name = None
            for subchild in child.children:
                if subchild.type in ("type_identifier", "generic_type", "array_type",
                                      "integral_type", "floating_point_type", "boolean_type"):
                    param_type = _extract_type_text(subchild, source)
                elif subchild.type == "identifier":
                    param_name = _node_text(subchild, source)
                elif subchild.type == "dimensions":
                    # Array notation after variable name: String[] args
                    if param_type:
                        param_type += _node_text(subchild, source)
            if param_type and param_name:
                params.append(f"{param_type} {param_name}")
        elif child.type == "spread_parameter":
            # Varargs: String... args
            param_type = None
            param_name = None
            for subchild in child.children:
                if subchild.type in ("type_identifier", "generic_type", "array_type"):
                    param_type = _extract_type_text(subchild, source)
                elif subchild.type == "variable_declarator":
                    for vchild in subchild.children:
                        if vchild.type == "identifier":
                            param_name = _node_text(vchild, source)
                elif subchild.type == "identifier":  # pragma: no cover
                    param_name = _node_text(subchild, source)  # pragma: no cover
            if param_type and param_name:
                params.append(f"{param_type}... {param_name}")

    params_str = ", ".join(params)
    signature = f"({params_str})"

    # Add return type for methods (not constructors), but omit void
    if not is_constructor and return_type and return_type != "void":
        signature += f" {return_type}"

    return signature


def _has_native_modifier(node: "tree_sitter.Node", source: bytes) -> bool:
    """Check if a method declaration has the 'native' modifier."""
    for child in node.children:
        if child.type == "modifiers":
            modifiers_text = _node_text(child, source)
            if "native" in modifiers_text:
                return True
    return False


# Java modifiers that can appear on methods
JAVA_METHOD_MODIFIERS = {
    "public", "private", "protected",
    "static", "final", "abstract",
    "native", "synchronized", "strictfp",
}


def _extract_modifiers(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract all modifiers from a method/constructor declaration.

    Returns a list of modifier strings like ["public", "static", "native"].

    Tree-sitter-java uses modifier keywords as node types directly (e.g., "public",
    "static", "native"), so we can match against the node type.
    """
    del source  # unused - modifiers are captured via node types
    modifiers: list[str] = []
    for child in node.children:
        if child.type == "modifiers":
            # The modifiers node contains individual modifier nodes
            for mod_child in child.children:
                # tree-sitter-java uses modifier keywords as node types
                if mod_child.type in JAVA_METHOD_MODIFIERS:
                    modifiers.append(mod_child.type)
    return modifiers


# Spring Boot route annotation mappings
SPRING_MAPPING_ANNOTATIONS = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}

# JAX-RS HTTP method annotations (marker annotations without arguments)
JAXRS_HTTP_ANNOTATIONS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}


def _detect_spring_boot_route(
    node: "tree_sitter.Node", source: bytes
) -> tuple[str | None, str | None]:
    """Detect Spring Boot route annotations on a method.

    Returns (http_method, route_path) if a Spring Boot route annotation is found.

    Supported patterns:
    - @GetMapping("/path") -> ("GET", "/path")
    - @PostMapping("/path") -> ("POST", "/path")
    - @PutMapping, @DeleteMapping, @PatchMapping
    - @RequestMapping(value = "/path", method = RequestMethod.GET)

    Args:
        node: The method_declaration node.
        source: The source code bytes.

    Returns:
        A tuple of (http_method, route_path), or (None, None) if not a route.
    """
    # Look for modifiers child which contains annotations
    for child in node.children:
        if child.type == "modifiers":
            # Iterate through annotations in modifiers
            for annotation in child.children:
                if annotation.type in ("annotation", "marker_annotation"):
                    # Get the annotation name
                    annotation_name = None
                    annotation_args = None

                    for ann_child in annotation.children:
                        if ann_child.type == "identifier":
                            annotation_name = _node_text(ann_child, source)
                        elif ann_child.type == "annotation_argument_list":
                            annotation_args = ann_child

                    if not annotation_name:  # pragma: no cover
                        continue

                    # Check for @GetMapping, @PostMapping, etc.
                    if annotation_name in SPRING_MAPPING_ANNOTATIONS:
                        http_method = SPRING_MAPPING_ANNOTATIONS[annotation_name]
                        route_path = _extract_spring_route_path(annotation_args, source)
                        return http_method, route_path

                    # Check for @RequestMapping with method attribute
                    if annotation_name == "RequestMapping":
                        return _parse_request_mapping(annotation_args, source)

    return None, None


def _extract_spring_route_path(
    args_node: Optional["tree_sitter.Node"], source: bytes
) -> str | None:
    """Extract route path from annotation arguments.

    Handles:
    - @GetMapping("/path")
    - @GetMapping(value = "/path")
    - @GetMapping(path = "/path")
    """
    if args_node is None:  # pragma: no cover
        return None

    for child in args_node.children:
        # Simple string argument: @GetMapping("/path")
        if child.type == "string_literal":
            return _node_text(child, source).strip('"')

        # Named argument: @GetMapping(value = "/path")
        if child.type == "element_value_pair":
            key = None
            value = None
            for pair_child in child.children:
                if pair_child.type == "identifier":
                    key = _node_text(pair_child, source)
                elif pair_child.type == "string_literal":
                    value = _node_text(pair_child, source).strip('"')
            if key in ("value", "path") and value:
                return value

    return None  # pragma: no cover


def _parse_request_mapping(
    args_node: Optional["tree_sitter.Node"], source: bytes
) -> tuple[str | None, str | None]:
    """Parse @RequestMapping annotation with method attribute.

    Handles:
    - @RequestMapping(value = "/path", method = RequestMethod.GET)
    - @RequestMapping(path = "/path", method = RequestMethod.POST)
    """
    if args_node is None:  # pragma: no cover
        return None, None

    route_path = None
    http_method = None

    for child in args_node.children:
        if child.type == "element_value_pair":
            key = None
            value_node = None
            # The first identifier is the key, everything else (except '=') is the value
            found_key = False
            for pair_child in child.children:
                if pair_child.type == "identifier" and not found_key:
                    key = _node_text(pair_child, source)
                    found_key = True
                elif pair_child.type not in ("=", ):
                    value_node = pair_child

            if key in ("value", "path") and value_node:
                if value_node.type == "string_literal":
                    route_path = _node_text(value_node, source).strip('"')

            if key == "method" and value_node:
                # Handle RequestMethod.GET, field_access, or just identifier (GET)
                method_text = _node_text(value_node, source)
                # Extract the method name (e.g., "GET" from "RequestMethod.GET")
                if "." in method_text:
                    http_method = method_text.split(".")[-1].upper()
                else:
                    http_method = method_text.upper()

    return http_method, route_path


def _detect_jaxrs_route(
    node: "tree_sitter.Node", source: bytes
) -> tuple[str | None, str | None]:
    """Detect JAX-RS route annotations on a method.

    Returns (http_method, route_path) if JAX-RS route annotations are found.

    Supported patterns:
    - @GET, @POST, @PUT, @DELETE, @PATCH (marker annotations)
    - @Path("/{id}") for route path

    Args:
        node: The method_declaration node.
        source: The source code bytes.

    Returns:
        A tuple of (http_method, route_path), or (None, None) if not a route.
    """
    http_method = None
    route_path = None

    # Look for modifiers child which contains annotations
    for child in node.children:
        if child.type == "modifiers":
            # Iterate through annotations in modifiers
            for annotation in child.children:
                if annotation.type == "marker_annotation":
                    # Marker annotation: @GET, @POST, etc. (no arguments)
                    for ann_child in annotation.children:
                        if ann_child.type == "identifier":
                            name = _node_text(ann_child, source)
                            if name in JAXRS_HTTP_ANNOTATIONS:
                                http_method = name.upper()
                                break

                elif annotation.type == "annotation":
                    # Regular annotation: @Path("/route")
                    annotation_name = None
                    annotation_args = None

                    for ann_child in annotation.children:
                        if ann_child.type == "identifier":
                            annotation_name = _node_text(ann_child, source)
                        elif ann_child.type == "annotation_argument_list":
                            annotation_args = ann_child

                    if annotation_name == "Path" and annotation_args:
                        # Extract path from @Path("/route")
                        for arg in annotation_args.children:
                            if arg.type == "string_literal":
                                route_path = _node_text(arg, source).strip('"')
                                break

    # Only return if we found an HTTP method annotation
    if http_method:
        return http_method, route_path
    return None, None


def _get_java_parser() -> Optional["tree_sitter.Parser"]:
    """Get tree-sitter parser for Java."""
    try:
        import tree_sitter
        import tree_sitter_java
    except ImportError:
        return None

    parser = tree_sitter.Parser()
    lang_ptr = tree_sitter_java.language()
    parser.language = tree_sitter.Language(lang_ptr)
    return parser


@dataclass
class _ParsedFile:
    """Holds parsed file data for two-pass analysis.

    Note on type inference: Variable method calls (e.g., stub.method()) are resolved
    using constructor-only type inference. This tracks types from direct constructor
    calls (stub = new Client()) but NOT from factory methods (stub = Client.create()).
    This covers ~90% of real-world cases with minimal complexity.
    """

    path: Path
    tree: "tree_sitter.Tree"
    source: bytes
    # Maps simple class name -> fully qualified name (from imports)
    imports: dict[str, str] | None = None


def _extract_imports(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract import mappings from a parsed Java tree.

    Tracks:
    - import com.example.ClassName; -> ClassName: com.example.ClassName
    - import static com.example.ClassName.method; -> (not tracked, static methods)

    Returns dict mapping simple class name -> fully qualified name.
    """
    imports: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "import_declaration":
            continue

        # Skip static imports for now (they import methods, not classes)
        is_static = any(c.type == "static" for c in node.children)
        if is_static:
            continue

        # Find the scoped_identifier (the fully qualified name)
        for child in node.children:
            if child.type == "scoped_identifier":
                full_name = _node_text(child, source)
                # Extract simple name (last part of qualified name)
                simple_name = full_name.split(".")[-1]
                imports[simple_name] = full_name
                break

    return imports


def _get_class_ancestors(
    node: "tree_sitter.Node", source: bytes
) -> list[str]:
    """Walk up the tree to find enclosing class/interface/enum names.

    Returns a list of class names from outermost to innermost (excluding current node).
    Used to build qualified names for nested types without recursion.
    """
    ancestors: list[str] = []
    current = node.parent
    while current is not None:
        if current.type in ("class_declaration", "interface_declaration", "enum_declaration"):
            name = _get_class_name(current, source)
            if name:
                ancestors.append(name)
        current = current.parent
    # Reverse because we walked from inner to outer
    return list(reversed(ancestors))


def _extract_symbols(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
) -> list[Symbol]:
    """Extract symbols from a parsed Java tree (pass 1).

    Uses iterative traversal to avoid RecursionError on deeply nested code.
    """
    symbols: list[Symbol] = []

    for node in iter_tree(tree.root_node):
        # Class declarations
        if node.type == "class_declaration":
            name = _get_class_name(node, source)
            if name:
                ancestors = _get_class_ancestors(node, source)
                full_name = ".".join(ancestors + [name]) if ancestors else name
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, full_name, "class"),
                    name=full_name,
                    kind="class",
                    language="java",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                symbols.append(symbol)

        # Interface declarations
        elif node.type == "interface_declaration":
            name = _get_class_name(node, source)
            if name:
                ancestors = _get_class_ancestors(node, source)
                full_name = ".".join(ancestors + [name]) if ancestors else name
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, full_name, "interface"),
                    name=full_name,
                    kind="interface",
                    language="java",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                symbols.append(symbol)

        # Enum declarations
        elif node.type == "enum_declaration":
            name = _get_class_name(node, source)
            if name:
                ancestors = _get_class_ancestors(node, source)
                full_name = ".".join(ancestors + [name]) if ancestors else name
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, full_name, "enum"),
                    name=full_name,
                    kind="enum",
                    language="java",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                symbols.append(symbol)

        # Method declarations
        elif node.type == "method_declaration":
            name = _get_method_name(node, source)
            ancestors = _get_class_ancestors(node, source)
            if name and ancestors:
                # Name methods with class prefix
                full_name = f"{'.'.join(ancestors)}.{name}"
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                # Check for native modifier
                is_native = _has_native_modifier(node, source)

                # Extract all modifiers for the modifiers field
                modifiers = _extract_modifiers(node, source)

                # Check for Spring Boot route annotations
                http_method, route_path = _detect_spring_boot_route(node, source)

                # If not Spring Boot, check for JAX-RS annotations
                if not http_method:
                    http_method, route_path = _detect_jaxrs_route(node, source)

                # Build meta dict
                meta: dict[str, str | bool] | None = None
                stable_id: str | None = None

                if is_native:
                    meta = {"is_native": True}

                if http_method or route_path:
                    if meta is None:
                        meta = {}
                    if route_path:
                        meta["route_path"] = route_path
                    if http_method:
                        meta["http_method"] = http_method
                        stable_id = http_method

                # Extract signature
                signature = _extract_java_signature(node, source, is_constructor=False)

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, full_name, "method"),
                    name=full_name,
                    kind="method",
                    language="java",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta=meta,
                    stable_id=stable_id,
                    signature=signature,
                    modifiers=modifiers,
                )
                symbols.append(symbol)

        # Constructor declarations
        elif node.type == "constructor_declaration":
            name = _get_method_name(node, source)
            ancestors = _get_class_ancestors(node, source)
            if name and ancestors:
                full_name = f"{'.'.join(ancestors)}.{name}"
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                # Extract signature (constructors have no return type)
                signature = _extract_java_signature(node, source, is_constructor=True)

                # Extract modifiers for constructors too
                modifiers = _extract_modifiers(node, source)

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, full_name, "constructor"),
                    name=full_name,
                    kind="constructor",
                    language="java",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    signature=signature,
                    modifiers=modifiers,
                )
                symbols.append(symbol)

    return symbols


def _get_enclosing_method(
    node: "tree_sitter.Node",
    source: bytes,
    global_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing method/constructor.

    Returns the Symbol for the enclosing method, or None if not inside a method.
    """
    current = node.parent
    while current is not None:
        if current.type in ("method_declaration", "constructor_declaration"):
            name = _get_method_name(current, source)
            if name:
                # Get class context
                ancestors = _get_class_ancestors(current, source)
                if ancestors:
                    full_name = f"{'.'.join(ancestors)}.{name}"
                    if full_name in global_symbols:
                        return global_symbols[full_name]
            return None  # pragma: no cover  # Found method but couldn't resolve it
        current = current.parent
    return None


def _extract_edges(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
    global_symbols: dict[str, Symbol],
    class_symbols: dict[str, Symbol],
    imports: dict[str, str] | None = None,
) -> list[Edge]:
    """Extract edges from a parsed Java tree (pass 2).

    Uses global symbol registry to resolve cross-file references.
    Uses iterative traversal to avoid RecursionError on deeply nested code.

    Handles:
    - Direct method calls: method(), this.method()
    - Qualified method calls: ClassName.method()
    - Variable method calls: variable.method() (with type inference)
    - Object instantiation: new ClassName()

    Note: Type inference only tracks types from direct constructor calls
    (stub = new Client()), not from factory methods (stub = Client.create()).
    """
    if imports is None:
        imports = {}
    edges: list[Edge] = []
    # Track variable types for type inference: var_name -> class_name
    var_types: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        # Check for extends (superclass) in class declarations
        if node.type == "class_declaration":
            name = _get_class_name(node, source)
            if name:
                ancestors = _get_class_ancestors(node, source)
                current_class = ".".join(ancestors + [name]) if ancestors else name

                # Check for extends (superclass)
                for child in node.children:
                    if child.type == "superclass":
                        # superclass contains "extends" keyword and type_identifier
                        for subchild in child.children:
                            if subchild.type == "type_identifier":
                                parent_name = _node_text(subchild, source)
                                if current_class in class_symbols:
                                    src_sym = class_symbols[current_class]
                                    if parent_name in class_symbols:
                                        dst_sym = class_symbols[parent_name]
                                        edge = Edge.create(
                                            src=src_sym.id,
                                            dst=dst_sym.id,
                                            edge_type="extends",
                                            line=child.start_point[0] + 1,
                                            confidence=0.95,
                                            origin=PASS_ID,
                                            origin_run_id=run.execution_id,
                                            evidence_type="ast_extends",
                                        )
                                        edges.append(edge)

                    # Check for implements (interfaces)
                    if child.type == "super_interfaces":
                        # super_interfaces contains "implements" and type_list
                        for subchild in child.children:
                            if subchild.type == "type_list":
                                for type_node in subchild.children:
                                    if type_node.type == "type_identifier":
                                        iface_name = _node_text(type_node, source)
                                        if current_class in class_symbols:
                                            src_sym = class_symbols[current_class]
                                            if iface_name in class_symbols:
                                                dst_sym = class_symbols[iface_name]
                                                edge = Edge.create(
                                                    src=src_sym.id,
                                                    dst=dst_sym.id,
                                                    edge_type="implements",
                                                    line=type_node.start_point[0] + 1,
                                                    confidence=0.95,
                                                    origin=PASS_ID,
                                                    origin_run_id=run.execution_id,
                                                    evidence_type="ast_implements",
                                                )
                                                edges.append(edge)

        # Method invocations
        elif node.type == "method_invocation":
            current_method = _get_enclosing_method(node, source, global_symbols)
            if current_method:
                # Get the method name being called
                method_name = None
                receiver_name = None
                for child in node.children:
                    if child.type == "identifier":
                        # First identifier is receiver, second is method name
                        if receiver_name is None and method_name is None:
                            # This could be either receiver.method() or just method()
                            receiver_name = _node_text(child, source)
                        else:
                            # This is the method name in receiver.method()
                            method_name = _node_text(child, source)

                # If only one identifier found, it's the method name (no receiver)
                if method_name is None and receiver_name is not None:
                    method_name = receiver_name
                    receiver_name = None

                if method_name:
                    # Get class context
                    ancestors = _get_class_ancestors(node, source)
                    current_class = ".".join(ancestors) if ancestors else None
                    edge_added = False

                    # Case 1: this.method() or method() - resolve in current class
                    if receiver_name is None or receiver_name == "this":
                        if current_class:
                            candidate = f"{current_class}.{method_name}"
                            if candidate in global_symbols:
                                target_sym = global_symbols[candidate]
                                edge = Edge.create(
                                    src=current_method.id,
                                    dst=target_sym.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    confidence=0.95,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    evidence_type="ast_call_direct",
                                )
                                edges.append(edge)
                                edge_added = True

                    # Case 2: ClassName.method() - static call
                    elif receiver_name and receiver_name in class_symbols:
                        candidate = f"{receiver_name}.{method_name}"
                        if candidate in global_symbols:
                            target_sym = global_symbols[candidate]
                            edge = Edge.create(
                                src=current_method.id,
                                dst=target_sym.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                confidence=0.95,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="ast_call_static",
                            )
                            edges.append(edge)
                            edge_added = True

                    # Case 3: variable.method() - use type inference
                    elif receiver_name and receiver_name in var_types:
                        type_class_name = var_types[receiver_name]
                        candidate = f"{type_class_name}.{method_name}"
                        if candidate in global_symbols:
                            target_sym = global_symbols[candidate]
                            edge = Edge.create(
                                src=current_method.id,
                                dst=target_sym.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                confidence=0.85,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="ast_call_type_inferred",
                            )
                            edges.append(edge)
                            edge_added = True

                    # Case 4: Fallback - try imported class or just the receiver name
                    # This handles edge cases where the receiver isn't recognized as a
                    # class or variable but might still match a symbol via imports.
                    # In practice, this is rarely hit since Case 2 handles most static
                    # calls and Case 3 handles most instance calls.
                    if not edge_added and receiver_name:  # pragma: no cover
                        candidates = [f"{receiver_name}.{method_name}"]
                        # Try imported class name
                        if receiver_name in imports:
                            full_class = imports[receiver_name].split(".")[-1]
                            candidates.insert(0, f"{full_class}.{method_name}")
                        for candidate in candidates:
                            if candidate in global_symbols:
                                target_sym = global_symbols[candidate]
                                edge = Edge.create(
                                    src=current_method.id,
                                    dst=target_sym.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    confidence=0.80,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    evidence_type="ast_call_direct",
                                )
                                edges.append(edge)
                                break

        # Object creation: new ClassName()
        elif node.type == "object_creation_expression":
            current_method = _get_enclosing_method(node, source, global_symbols)
            type_name = None

            # Find the type being instantiated
            for child in node.children:
                if child.type == "type_identifier":
                    type_name = _node_text(child, source)
                    if current_method and type_name in class_symbols:
                        target_sym = class_symbols[type_name]
                        edge = Edge.create(
                            src=current_method.id,
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

            # Track variable type for type inference
            # Check if this new expression is part of a variable assignment
            if type_name and node.parent:
                parent = node.parent
                # Java variable declarations: Type varName = new Type();
                if parent.type == "variable_declarator":
                    # Find variable name
                    for pc in parent.children:
                        if pc.type == "identifier":
                            var_name = _node_text(pc, source)
                            var_types[var_name] = type_name
                            break

    return edges


def _analyze_java_file(
    file_path: Path,
    run: AnalysisRun,
) -> tuple[list[Symbol], list[Edge], bool]:
    """Analyze a single Java file (legacy single-pass, used for testing).

    Returns (symbols, edges, success).
    """
    parser = _get_java_parser()
    if parser is None:
        return [], [], False

    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return [], [], False

    symbols = _extract_symbols(tree, source, file_path, run)

    # Build symbol registries for edge extraction
    global_symbols: dict[str, Symbol] = {}
    class_symbols: dict[str, Symbol] = {}

    for sym in symbols:
        global_symbols[sym.name] = sym
        if sym.kind in ("class", "interface", "enum"):
            class_symbols[sym.name] = sym

    edges = _extract_edges(tree, source, file_path, run, global_symbols, class_symbols)
    return symbols, edges, True


def analyze_java(repo_root: Path) -> JavaAnalysisResult:
    """Analyze all Java files in a repository.

    Uses a two-pass approach:
    1. Parse all files and extract symbols into global registry
    2. Detect calls/inheritance and resolve against global symbol registry

    Returns a JavaAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-java is not available, returns empty result (silently skipped).
    """
    start_time = time.time()

    # Create analysis run for provenance
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Check for tree-sitter-java availability
    if not is_java_tree_sitter_available():
        skip_reason = "Java analysis skipped: requires tree-sitter-java (pip install tree-sitter-java)"
        warnings.warn(skip_reason, stacklevel=2)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return JavaAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=skip_reason,
        )

    parser = _get_java_parser()
    if parser is None:
        skip_reason = "Java analysis skipped: requires tree-sitter-java (pip install tree-sitter-java)"
        warnings.warn(skip_reason, stacklevel=2)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return JavaAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=skip_reason,
        )

    # Pass 1: Parse all files and extract symbols
    parsed_files: list[_ParsedFile] = []
    all_symbols: list[Symbol] = []
    files_analyzed = 0
    files_skipped = 0

    for file_path in find_java_files(repo_root):
        try:
            source = file_path.read_bytes()
            tree = parser.parse(source)
            file_imports = _extract_imports(tree, source)
            parsed_files.append(_ParsedFile(
                path=file_path, tree=tree, source=source, imports=file_imports
            ))
            symbols = _extract_symbols(tree, source, file_path, run)
            all_symbols.extend(symbols)
            files_analyzed += 1
        except (OSError, IOError):
            files_skipped += 1

    # Build global symbol registries
    global_symbols: dict[str, Symbol] = {}
    class_symbols: dict[str, Symbol] = {}

    for sym in all_symbols:
        global_symbols[sym.name] = sym
        if sym.kind in ("class", "interface", "enum"):
            class_symbols[sym.name] = sym

    # Pass 2: Extract edges using global symbol registry
    all_edges: list[Edge] = []
    for pf in parsed_files:
        edges = _extract_edges(
            pf.tree, pf.source, pf.path, run,
            global_symbols, class_symbols, pf.imports or {}
        )
        all_edges.extend(edges)

    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return JavaAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
