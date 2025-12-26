"""C# analysis pass using tree-sitter-c-sharp.

This analyzer uses tree-sitter to parse C# files and extract:
- Class declarations
- Interface declarations
- Struct declarations
- Enum declarations
- Method declarations (inside classes/structs)
- Constructor declarations
- Property declarations
- Function call relationships
- Using directives (imports)
- Object instantiation

If tree-sitter with C# support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-c-sharp is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls, instantiations, and resolve against global symbol registry
4. Detect using directives and object creations

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-c-sharp package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as other language analyzers for consistency
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

PASS_ID = "csharp-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_csharp_files(repo_root: Path) -> Iterator[Path]:
    """Yield all C# files in the repository."""
    yield from find_files(repo_root, ["*.cs"])


def is_csharp_tree_sitter_available() -> bool:
    """Check if tree-sitter with C# grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec("tree_sitter_c_sharp") is None:
        return False
    return True


@dataclass
class CSharpAnalysisResult:
    """Result of analyzing C# files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"csharp:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a C# file node (used as import edge source)."""
    return f"csharp:{path}:1-1:file:file"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


# ASP.NET Core HTTP method attributes
ASPNET_HTTP_ATTRIBUTES = {
    "HttpGet": "GET",
    "HttpPost": "POST",
    "HttpPut": "PUT",
    "HttpDelete": "DELETE",
    "HttpPatch": "PATCH",
    "HttpHead": "HEAD",
    "HttpOptions": "OPTIONS",
}


def _detect_aspnet_route(
    node: "tree_sitter.Node", source: bytes
) -> tuple[str | None, str | None]:
    """Detect ASP.NET Core route attributes on a method.

    Returns (http_method, route_path) if ASP.NET Core route attributes are found.

    Supported patterns:
    - [HttpGet], [HttpPost], [HttpPut], [HttpDelete], [HttpPatch]
    - [HttpGet("{id}")] with route template

    Args:
        node: The method_declaration node.
        source: The source code bytes.

    Returns:
        A tuple of (http_method, route_path), or (None, None) if not a route.
    """
    http_method = None
    route_path = None

    # In C# tree-sitter, attributes come before the method in an attribute_list
    # We need to look at siblings preceding the method or check parent's children
    # Actually, in tree-sitter-c-sharp, method_declaration contains attribute_list as child
    for child in node.children:
        if child.type == "attribute_list":
            # attribute_list contains one or more attributes
            for attr in child.children:
                if attr.type == "attribute":
                    # Get the attribute name
                    attr_name_node = _find_child_by_type(attr, "identifier")
                    if attr_name_node:
                        attr_name = _node_text(attr_name_node, source)
                        if attr_name in ASPNET_HTTP_ATTRIBUTES:
                            http_method = ASPNET_HTTP_ATTRIBUTES[attr_name]

                            # Check for route template in attribute_argument_list
                            arg_list = _find_child_by_type(attr, "attribute_argument_list")
                            if arg_list:
                                for arg in arg_list.children:
                                    if arg.type == "attribute_argument":
                                        # Look for string literal
                                        for arg_child in arg.children:
                                            if arg_child.type == "string_literal":
                                                route_path = _node_text(arg_child, source).strip('"')
                                                break

    if http_method:
        return http_method, route_path
    return None, None


def _find_children_by_type(node: "tree_sitter.Node", type_name: str) -> list["tree_sitter.Node"]:
    """Find all children of given type."""
    return [child for child in node.children if child.type == type_name]


def _extract_method_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract the method name from a method declaration.

    In C#, method_declaration has: [modifiers] return_type method_name(params)
    The return_type might be an identifier (e.g., 'Product') or predefined_type (e.g., 'int').
    The method_name is always an identifier after the return type.
    """
    identifiers = _find_children_by_type(node, "identifier")
    # If return type is a predefined_type (int, void, etc.), first identifier is method name
    # If return type is an identifier (custom type), second identifier is method name
    has_predefined_type = _find_child_by_type(node, "predefined_type") is not None
    has_generic_name = _find_child_by_type(node, "generic_name") is not None

    if has_predefined_type or has_generic_name:
        # Return type is predefined (int, void, etc.) or generic (Task<T>)
        # First identifier is method name
        if identifiers:
            return _node_text(identifiers[0], source)
    else:
        # Return type is a custom type (an identifier)
        # Second identifier is method name
        if len(identifiers) >= 2:
            return _node_text(identifiers[1], source)
        elif identifiers:  # pragma: no cover - defensive fallback
            # Fallback: only one identifier means no custom return type detected
            return _node_text(identifiers[0], source)
    return None  # pragma: no cover - defensive


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
    """Extract symbols from a single C# file."""
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):  # pragma: no cover - IO errors hard to trigger in tests
        return FileAnalysis()

    analysis = FileAnalysis()
    current_class: Optional[str] = None

    def extract_name_from_declaration(node: "tree_sitter.Node") -> Optional[str]:
        """Extract the identifier name from a declaration node."""
        name_node = _find_child_by_type(node, "identifier")
        if name_node:
            return _node_text(name_node, source)
        return None  # pragma: no cover - defensive

    def extract_method_name(node: "tree_sitter.Node") -> Optional[str]:
        """Extract the method name from a method declaration."""
        return _extract_method_name(node, source)

    def visit(node: "tree_sitter.Node") -> None:
        nonlocal current_class

        # Class declaration
        if node.type == "class_declaration":
            name = extract_name_from_declaration(node)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, name, "class"),
                    name=name,
                    kind="class",
                    language="csharp",
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
                analysis.symbol_by_name[name] = symbol

                # Process body with class context
                old_class = current_class
                current_class = name
                for child in node.children:
                    visit(child)
                current_class = old_class
                return

        # Interface declaration
        elif node.type == "interface_declaration":
            name = extract_name_from_declaration(node)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, name, "interface"),
                    name=name,
                    kind="interface",
                    language="csharp",
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
                analysis.symbol_by_name[name] = symbol

                # Process interface body
                old_class = current_class
                current_class = name
                for child in node.children:
                    visit(child)
                current_class = old_class
                return

        # Struct declaration
        elif node.type == "struct_declaration":
            name = extract_name_from_declaration(node)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, name, "struct"),
                    name=name,
                    kind="struct",
                    language="csharp",
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
                analysis.symbol_by_name[name] = symbol

                # Process struct body
                old_class = current_class
                current_class = name
                for child in node.children:
                    visit(child)
                current_class = old_class
                return

        # Enum declaration
        elif node.type == "enum_declaration":
            name = extract_name_from_declaration(node)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, name, "enum"),
                    name=name,
                    kind="enum",
                    language="csharp",
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
                analysis.symbol_by_name[name] = symbol

        # Method declaration
        elif node.type == "method_declaration":
            name = extract_method_name(node)
            if name:
                if current_class:
                    full_name = f"{current_class}.{name}"
                else:
                    full_name = name  # pragma: no cover - should always be in class

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Check for ASP.NET Core route attributes
                http_method, route_path = _detect_aspnet_route(node, source)

                # Build meta dict
                meta: dict[str, str] | None = None
                stable_id: str | None = None

                if http_method or route_path:
                    meta = {}
                    if route_path:
                        meta["route_path"] = route_path
                    if http_method:
                        meta["http_method"] = http_method
                        stable_id = http_method

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, full_name, "method"),
                    name=full_name,
                    kind="method",
                    language="csharp",
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
                    stable_id=stable_id,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[name] = symbol
                analysis.symbol_by_name[full_name] = symbol

        # Constructor declaration
        elif node.type == "constructor_declaration":
            name = extract_name_from_declaration(node)
            if name:
                full_name = f"{current_class}.{name}" if current_class else name

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, full_name, "constructor"),
                    name=full_name,
                    kind="constructor",
                    language="csharp",
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
                analysis.symbol_by_name[name] = symbol
                analysis.symbol_by_name[full_name] = symbol

        # Property declaration
        elif node.type == "property_declaration":
            name = extract_name_from_declaration(node)
            if name:
                full_name = f"{current_class}.{name}" if current_class else name

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, full_name, "property"),
                    name=full_name,
                    kind="property",
                    language="csharp",
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
                analysis.symbol_by_name[name] = symbol
                analysis.symbol_by_name[full_name] = symbol

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
    """Extract call, import, and instantiation edges from a file."""
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):  # pragma: no cover - IO errors hard to trigger in tests
        return []

    edges: list[Edge] = []
    file_id = _make_file_id(str(file_path))
    current_function: Optional[Symbol] = None

    def get_callee_name(node: "tree_sitter.Node") -> Optional[str]:
        """Extract the method name being called from an invocation expression."""
        # Find the expression being invoked (function part before argument_list)
        for child in node.children:
            if child.type == "member_access_expression":
                # e.g., Console.WriteLine or obj.Method
                # Get the last identifier (the method name)
                identifiers = _find_children_by_type(child, "identifier")
                if identifiers:
                    return _node_text(identifiers[-1], source)
            elif child.type == "identifier":
                # Direct function call
                return _node_text(child, source)
        return None  # pragma: no cover - defensive

    def visit(node: "tree_sitter.Node") -> None:
        nonlocal current_function

        # Track current method for call edges
        if node.type == "method_declaration":
            method_name = _extract_method_name(node, source)
            if method_name:
                if method_name in local_symbols:
                    old_function = current_function
                    current_function = local_symbols[method_name]

                    # Process method body
                    for child in node.children:
                        visit(child)

                    current_function = old_function
                    return

        # Track current constructor
        elif node.type == "constructor_declaration":
            name_node = _find_child_by_type(node, "identifier")
            if name_node:
                ctor_name = _node_text(name_node, source)
                if ctor_name in local_symbols:
                    old_function = current_function
                    current_function = local_symbols[ctor_name]

                    for child in node.children:
                        visit(child)

                    current_function = old_function
                    return

        # Using directive
        elif node.type == "using_directive":
            # Get the namespace being imported
            name_node = _find_child_by_type(node, "identifier")
            if not name_node:
                name_node = _find_child_by_type(node, "qualified_name")
            if name_node:
                import_path = _node_text(name_node, source)
                edges.append(Edge.create(
                    src=file_id,
                    dst=f"csharp:{import_path}:0-0:namespace:namespace",
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    evidence_type="using_directive",
                    confidence=0.95,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                ))

        # Invocation expression (method call)
        elif node.type == "invocation_expression":
            if current_function is not None:
                callee_name = get_callee_name(node)
                if callee_name:
                    # Check local symbols first
                    if callee_name in local_symbols:
                        callee = local_symbols[callee_name]
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=callee.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            evidence_type="method_call",
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
                            evidence_type="method_call",
                            confidence=0.80,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))

        # Object creation expression (new ClassName())
        elif node.type == "object_creation_expression":
            if current_function is not None:
                type_node = _find_child_by_type(node, "identifier")
                if type_node:
                    type_name = _node_text(type_node, source)
                    # Check if it's a known class
                    if type_name in local_symbols:
                        target = local_symbols[type_name]
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=target.id,
                            edge_type="instantiates",
                            line=node.start_point[0] + 1,
                            evidence_type="object_creation",
                            confidence=0.90,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))
                    elif type_name in global_symbols:
                        target = global_symbols[type_name]
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=target.id,
                            edge_type="instantiates",
                            line=node.start_point[0] + 1,
                            evidence_type="object_creation",
                            confidence=0.85,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))

        # Recurse
        for child in node.children:
            visit(child)

    visit(tree.root_node)
    return edges


def analyze_csharp(repo_root: Path) -> CSharpAnalysisResult:
    """Analyze all C# files in a repository.

    Returns a CSharpAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-c-sharp is not available, returns a skipped result.
    """
    if not is_csharp_tree_sitter_available():
        warnings.warn(
            "tree-sitter-c-sharp not available. Install with: pip install hypergumbo[csharp]",
            stacklevel=2,
        )
        return CSharpAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-c-sharp not available",
        )

    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Import tree-sitter-c-sharp
    try:
        import tree_sitter_c_sharp
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_c_sharp.language())
        parser = tree_sitter.Parser(lang)
    except Exception as e:  # pragma: no cover - parser load failure hard to trigger
        run.duration_ms = int((time.time() - start_time) * 1000)
        return CSharpAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=f"Failed to load C# parser: {e}",
        )

    # Pass 1: Extract all symbols
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0

    for cs_file in find_csharp_files(repo_root):
        analysis = _extract_symbols_from_file(cs_file, parser, run)
        if analysis.symbols:
            file_analyses[cs_file] = analysis
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

    # Pass 2: Extract edges
    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []

    for cs_file, analysis in file_analyses.items():
        all_symbols.extend(analysis.symbols)

        edges = _extract_edges_from_file(
            cs_file, parser, analysis.symbol_by_name, global_symbols, run
        )
        all_edges.extend(edges)

    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return CSharpAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
