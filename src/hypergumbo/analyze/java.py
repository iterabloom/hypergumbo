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

PASS_ID = "java-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_java_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Java files in the repository."""
    yield from find_files(repo_root, ["*.java"])


def is_java_tree_sitter_available() -> bool:
    """Check if tree-sitter and Java grammar are available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec("tree_sitter_java") is None:
        return False
    return True


@dataclass
class JavaAnalysisResult:
    """Result of analyzing Java files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"java:{path}:{start_line}-{end_line}:{name}:{kind}"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


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


def _has_native_modifier(node: "tree_sitter.Node", source: bytes) -> bool:
    """Check if a method declaration has the 'native' modifier."""
    for child in node.children:
        if child.type == "modifiers":
            modifiers_text = _node_text(child, source)
            if "native" in modifiers_text:
                return True
    return False


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
    """Extract symbols from a parsed Java tree (pass 1)."""
    symbols: list[Symbol] = []
    class_stack: list[str] = []  # Track nested class context

    def visit(node: "tree_sitter.Node") -> None:
        # Class declarations
        if node.type == "class_declaration":
            name = _get_class_name(node, source)
            if name:
                full_name = ".".join(class_stack + [name]) if class_stack else name
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

                # Process children with class context
                class_stack.append(name)
                for child in node.children:
                    visit(child)
                class_stack.pop()
                return

        # Interface declarations
        if node.type == "interface_declaration":
            name = _get_class_name(node, source)
            if name:
                full_name = ".".join(class_stack + [name]) if class_stack else name
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

                # Process children with interface context
                class_stack.append(name)
                for child in node.children:
                    visit(child)
                class_stack.pop()
                return

        # Enum declarations
        if node.type == "enum_declaration":
            name = _get_class_name(node, source)
            if name:
                full_name = ".".join(class_stack + [name]) if class_stack else name
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

                class_stack.append(name)
                for child in node.children:
                    visit(child)
                class_stack.pop()
                return

        # Method declarations
        if node.type == "method_declaration":
            name = _get_method_name(node, source)
            if name and class_stack:
                # Name methods with class prefix
                full_name = f"{'.'.join(class_stack)}.{name}"
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                # Check for native modifier
                is_native = _has_native_modifier(node, source)

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
                )
                symbols.append(symbol)

        # Constructor declarations
        if node.type == "constructor_declaration":
            name = _get_method_name(node, source)
            if name and class_stack:
                full_name = f"{'.'.join(class_stack)}.{name}"
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, full_name, "constructor"),
                    name=full_name,
                    kind="constructor",
                    language="java",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                symbols.append(symbol)

        # Recurse into children
        for child in node.children:
            visit(child)

    visit(tree.root_node)
    return symbols


def _extract_edges(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
    global_symbols: dict[str, Symbol],
    class_symbols: dict[str, Symbol],
) -> list[Edge]:
    """Extract edges from a parsed Java tree (pass 2).

    Uses global symbol registry to resolve cross-file references.
    """
    edges: list[Edge] = []
    class_stack: list[str] = []
    method_stack: list[Symbol] = []

    def get_current_class() -> Optional[str]:
        return ".".join(class_stack) if class_stack else None

    def get_current_method() -> Optional[Symbol]:
        return method_stack[-1] if method_stack else None

    def visit(node: "tree_sitter.Node") -> None:
        # Track class context and detect extends/implements
        if node.type == "class_declaration":
            name = _get_class_name(node, source)
            if name:
                class_stack.append(name)
                current_class = get_current_class()

                # Check for extends (superclass)
                for child in node.children:
                    if child.type == "superclass":
                        # superclass contains "extends" keyword and type_identifier
                        for subchild in child.children:
                            if subchild.type == "type_identifier":
                                parent_name = _node_text(subchild, source)
                                if current_class and current_class in class_symbols:
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
                                        if current_class and current_class in class_symbols:
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

                for child in node.children:
                    visit(child)
                class_stack.pop()
                return

        # Track interface context
        if node.type == "interface_declaration":
            name = _get_class_name(node, source)
            if name:
                class_stack.append(name)
                for child in node.children:
                    visit(child)
                class_stack.pop()
                return

        # Track enum context
        if node.type == "enum_declaration":
            name = _get_class_name(node, source)
            if name:
                class_stack.append(name)
                for child in node.children:
                    visit(child)
                class_stack.pop()
                return

        # Track method context
        if node.type in ("method_declaration", "constructor_declaration"):
            name = _get_method_name(node, source)
            current_class = get_current_class()
            if name and current_class:
                full_name = f"{current_class}.{name}"
                if full_name in global_symbols:
                    method_sym = global_symbols[full_name]
                    method_stack.append(method_sym)
                    for child in node.children:
                        visit(child)
                    method_stack.pop()
                    return

        # Method invocations
        if node.type == "method_invocation":
            current_method = get_current_method()
            if current_method:
                # Get the method name being called
                method_name = None
                for child in node.children:
                    if child.type == "identifier":
                        method_name = _node_text(child, source)
                        break

                if method_name:
                    current_class = get_current_class()
                    # Try to resolve: this.method(), method(), ClassName.method()
                    candidates = []
                    if current_class:
                        candidates.append(f"{current_class}.{method_name}")
                    # Also check for static calls like Helper.getValue()
                    # Look for object/class reference before the method
                    for child in node.children:
                        if child.type == "identifier":
                            ref_name = _node_text(child, source)
                            if ref_name != method_name:
                                candidates.append(f"{ref_name}.{method_name}")

                    for candidate in candidates:
                        if candidate in global_symbols:
                            target_sym = global_symbols[candidate]
                            edge = Edge.create(
                                src=current_method.id,
                                dst=target_sym.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                confidence=0.90,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="ast_call_direct",
                            )
                            edges.append(edge)
                            break

        # Object creation: new ClassName()
        if node.type == "object_creation_expression":
            current_method = get_current_method()
            if current_method:
                # Find the type being instantiated
                for child in node.children:
                    if child.type == "type_identifier":
                        type_name = _node_text(child, source)
                        if type_name in class_symbols:
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

        # Recurse into children
        for child in node.children:
            visit(child)

    visit(tree.root_node)
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
            parsed_files.append(_ParsedFile(path=file_path, tree=tree, source=source))
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
            global_symbols, class_symbols
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
