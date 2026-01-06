"""Swift analysis pass using tree-sitter-swift.

This analyzer uses tree-sitter to parse Swift files and extract:
- Function declarations (func)
- Class declarations (class)
- Struct declarations (struct)
- Protocol declarations (protocol)
- Enum declarations (enum)
- Method declarations (inside classes/structs)
- Function call relationships
- Import statements

If tree-sitter with Swift support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-swift is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and import statements

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-swift package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as Go/Ruby/Kotlin/Rust/Elixir/Java/PHP/C analyzers for consistency
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

PASS_ID = "swift-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_swift_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Swift files in the repository."""
    yield from find_files(repo_root, ["*.swift"])


def is_swift_tree_sitter_available() -> bool:
    """Check if tree-sitter with Swift grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec("tree_sitter_swift") is None:
        return False
    return True


@dataclass
class SwiftAnalysisResult:
    """Result of analyzing Swift files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"swift:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Swift file node (used as import edge source)."""
    return f"swift:{path}:1-1:file:file"


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


def _get_enclosing_type(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up the tree to find the enclosing class/struct/enum/protocol name."""
    current = node.parent
    while current is not None:
        if current.type in ("class_declaration", "protocol_declaration"):
            name_node = _find_child_by_type(current, "type_identifier")
            if name_node:
                return _node_text(name_node, source)
        current = current.parent
    return None  # pragma: no cover - defensive


def _get_enclosing_function(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing function/method."""
    current = node.parent
    while current is not None:
        if current.type == "function_declaration":
            name_node = _find_child_by_field(current, "name")
            if not name_node:  # pragma: no cover - defensive fallback
                name_node = _find_child_by_type(current, "simple_identifier")
            if name_node:
                func_name = _node_text(name_node, source)
                if func_name in local_symbols:
                    return local_symbols[func_name]
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_swift_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a Swift function declaration.

    Returns signature like:
    - "(x: Int, y: Int) -> Int" for regular functions
    - "(message: String)" for void functions (no return type shown)

    Args:
        node: The function_declaration node.
        source: The source code bytes.

    Returns:
        The signature string, or None if extraction fails.
    """
    params: list[str] = []
    return_type = None
    found_closing_paren = False

    # Iterate through children to find parameters and return type
    for child in node.children:
        if child.type == "parameter":
            param_name = None
            param_type = None
            for subchild in child.children:
                if subchild.type == "simple_identifier" and param_name is None:
                    param_name = _node_text(subchild, source)
                elif subchild.type in ("user_type", "array_type", "dictionary_type",
                                        "optional_type", "tuple_type", "function_type"):
                    param_type = _node_text(subchild, source)
            if param_name and param_type:
                params.append(f"{param_name}: {param_type}")
        elif child.type == ")":
            found_closing_paren = True
        # Return type comes after ) and before function_body
        elif found_closing_paren and child.type in ("user_type", "array_type", "dictionary_type",
                                                      "optional_type", "tuple_type", "function_type"):
            return_type = _node_text(child, source)

    params_str = ", ".join(params)
    signature = f"({params_str})"

    if return_type:
        signature += f" -> {return_type}"

    return signature


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
    """Extract symbols from a single Swift file."""
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return FileAnalysis()

    analysis = FileAnalysis()

    for node in iter_tree(tree.root_node):
        # Function declaration
        if node.type == "function_declaration":
            name_node = _find_child_by_field(node, "name")
            if not name_node:  # pragma: no cover - grammar fallback
                name_node = _find_child_by_type(node, "simple_identifier")

            if name_node:
                func_name = _node_text(name_node, source)
                enclosing_type = _get_enclosing_type(node, source)
                if enclosing_type:
                    full_name = f"{enclosing_type}.{func_name}"
                    kind = "method"
                else:
                    full_name = func_name
                    kind = "function"

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract signature
                signature = _extract_swift_signature(node, source)

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, full_name, kind),
                    name=full_name,
                    kind=kind,
                    language="swift",
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
                analysis.symbol_by_name[full_name] = symbol

        # Class declaration (class, struct, enum, protocol in tree-sitter-swift)
        # The grammar represents all type declarations as class_declaration
        # with different keyword children (class, struct, enum, protocol)
        elif node.type == "class_declaration":
            # Determine the kind based on keyword child
            is_struct = _find_child_by_type(node, "struct") is not None
            is_enum = _find_child_by_type(node, "enum") is not None
            is_protocol = _find_child_by_type(node, "protocol") is not None

            if is_struct:
                kind = "struct"
            elif is_enum:
                kind = "enum"
            elif is_protocol:  # pragma: no cover - protocols use protocol_declaration
                kind = "protocol"
            else:
                kind = "class"

            name_node = _find_child_by_type(node, "type_identifier")

            if name_node:
                type_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, type_name, kind),
                    name=type_name,
                    kind=kind,
                    language="swift",
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
                analysis.symbol_by_name[type_name] = symbol

        # Standalone protocol declaration (for older grammar versions)
        elif node.type == "protocol_declaration":  # pragma: no cover - grammar fallback
            name_node = _find_child_by_type(node, "type_identifier")

            if name_node:
                type_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, type_name, "protocol"),
                    name=type_name,
                    kind="protocol",
                    language="swift",
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
                analysis.symbol_by_name[type_name] = symbol

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

    for node in iter_tree(tree.root_node):
        # Detect import statements
        if node.type == "import_declaration":
            # Get the module being imported
            id_node = _find_child_by_type(node, "identifier")
            if id_node:
                import_path = _node_text(id_node, source)
                edges.append(Edge.create(
                    src=file_id,
                    dst=f"swift:{import_path}:0-0:module:module",
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    evidence_type="import_statement",
                    confidence=0.95,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                ))

        # Detect function calls
        elif node.type == "call_expression":
            current_function = _get_enclosing_function(node, source, local_symbols)
            if current_function is not None:
                # Get the function being called
                callee_node = _find_child_by_type(node, "simple_identifier")
                if not callee_node:
                    # Try navigation expression for method calls
                    nav_node = _find_child_by_type(node, "navigation_expression")  # pragma: no cover - grammar fallback
                    if nav_node:  # pragma: no cover - grammar fallback
                        callee_node = _find_child_by_type(nav_node, "simple_identifier")

                if callee_node:
                    callee_name = _node_text(callee_node, source)

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

    return edges


def analyze_swift(repo_root: Path) -> SwiftAnalysisResult:
    """Analyze all Swift files in a repository.

    Returns a SwiftAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-swift is not available, returns a skipped result.
    """
    if not is_swift_tree_sitter_available():
        warnings.warn(
            "tree-sitter-swift not available. Install with: pip install hypergumbo[swift]",
            stacklevel=2,
        )
        return SwiftAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-swift not available",
        )

    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Import tree-sitter-swift
    try:
        import tree_sitter_swift
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_swift.language())
        parser = tree_sitter.Parser(lang)
    except Exception as e:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return SwiftAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=f"Failed to load Swift parser: {e}",
        )

    # Pass 1: Extract all symbols
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0

    for swift_file in find_swift_files(repo_root):
        analysis = _extract_symbols_from_file(swift_file, parser, run)
        if analysis.symbols:
            file_analyses[swift_file] = analysis
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

    for swift_file, analysis in file_analyses.items():
        all_symbols.extend(analysis.symbols)

        edges = _extract_edges_from_file(
            swift_file, parser, analysis.symbol_by_name, global_symbols, run
        )
        all_edges.extend(edges)

    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return SwiftAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
