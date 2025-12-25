"""Kotlin analysis pass using tree-sitter-kotlin.

This analyzer uses tree-sitter to parse Kotlin files and extract:
- Function declarations (fun)
- Class declarations (class, data class)
- Object declarations (object)
- Interface declarations (interface)
- Method declarations (inside classes/objects)
- Function call relationships
- Import statements

If tree-sitter with Kotlin support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-kotlin is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and import statements

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-kotlin package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as Go/Ruby/Rust/Elixir/Java/PHP/C analyzers for consistency
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

PASS_ID = "kotlin-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_kotlin_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Kotlin files in the repository."""
    yield from find_files(repo_root, ["*.kt"])


def is_kotlin_tree_sitter_available() -> bool:
    """Check if tree-sitter with Kotlin grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec("tree_sitter_kotlin") is None:
        return False
    return True


@dataclass
class KotlinAnalysisResult:
    """Result of analyzing Kotlin files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"kotlin:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Kotlin file node (used as import edge source)."""
    return f"kotlin:{path}:1-1:file:file"


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


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
) -> FileAnalysis:
    """Extract symbols from a single Kotlin file."""
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return FileAnalysis()

    analysis = FileAnalysis()
    current_class: Optional[str] = None

    def visit(node: "tree_sitter.Node") -> None:
        nonlocal current_class

        # Function declaration
        if node.type == "function_declaration":
            name_node = _find_child_by_field(node, "name")
            if not name_node:  # pragma: no cover - grammar fallback
                name_node = _find_child_by_type(node, "identifier")

            if name_node:
                func_name = _node_text(name_node, source)
                if current_class:
                    full_name = f"{current_class}.{func_name}"
                    kind = "method"
                else:
                    full_name = func_name
                    kind = "function"

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, full_name, kind),
                    name=full_name,
                    kind=kind,
                    language="kotlin",
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
                analysis.symbol_by_name[func_name] = symbol
                analysis.symbol_by_name[full_name] = symbol

        # Class declaration (also handles interfaces in Kotlin AST)
        elif node.type == "class_declaration":
            # Check if it's an interface
            is_interface = _find_child_by_type(node, "interface") is not None

            name_node = _find_child_by_field(node, "name")
            if not name_node:  # pragma: no cover - grammar fallback
                name_node = _find_child_by_type(node, "identifier")

            if name_node:
                type_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                kind = "interface" if is_interface else "class"

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, type_name, kind),
                    name=type_name,
                    kind=kind,
                    language="kotlin",
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

                # Process body with class context
                old_class = current_class
                current_class = type_name
                for child in node.children:
                    visit(child)
                current_class = old_class
                return

        # Object declaration
        elif node.type == "object_declaration":
            name_node = _find_child_by_field(node, "name")
            if not name_node:  # pragma: no cover - grammar fallback
                name_node = _find_child_by_type(node, "type_identifier")

            if name_node:
                object_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, object_name, "object"),
                    name=object_name,
                    kind="object",
                    language="kotlin",
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
                analysis.symbol_by_name[object_name] = symbol

                # Process object body with class context
                old_class = current_class
                current_class = object_name
                for child in node.children:
                    visit(child)
                current_class = old_class
                return

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
    """Extract call and import edges from a file."""
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return []

    edges: list[Edge] = []
    file_id = _make_file_id(str(file_path))
    current_function: Optional[Symbol] = None

    def visit(node: "tree_sitter.Node") -> None:
        nonlocal current_function

        # Track current function for call edges
        if node.type == "function_declaration":
            name_node = _find_child_by_field(node, "name")
            if not name_node:  # pragma: no cover - grammar fallback
                name_node = _find_child_by_type(node, "identifier")
            if name_node:
                func_name = _node_text(name_node, source)
                if func_name in local_symbols:
                    old_function = current_function
                    current_function = local_symbols[func_name]

                    # Process function body
                    for child in node.children:
                        visit(child)

                    current_function = old_function
                    return

        # Detect import statements
        elif node.type == "import":
            # Get the qualified identifier being imported
            id_node = _find_child_by_type(node, "qualified_identifier")
            if not id_node:
                id_node = _find_child_by_type(node, "identifier")
            if id_node:
                import_path = _node_text(id_node, source)
                edges.append(Edge.create(
                    src=file_id,
                    dst=f"kotlin:{import_path}:0-0:package:package",
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    evidence_type="import_statement",
                    confidence=0.95,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                ))

        # Detect function calls
        elif node.type == "call_expression":
            if current_function is not None:
                # Get the function being called
                callee_node = _find_child_by_type(node, "identifier")
                if not callee_node:
                    # Try navigation suffix (e.g., Helpers.greet())
                    nav_node = _find_child_by_type(node, "navigation_suffix")
                    if nav_node:  # pragma: no cover - grammar fallback
                        callee_node = _find_child_by_type(nav_node, "identifier")

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

        # Recurse
        for child in node.children:
            visit(child)

    visit(tree.root_node)
    return edges


def analyze_kotlin(repo_root: Path) -> KotlinAnalysisResult:
    """Analyze all Kotlin files in a repository.

    Returns a KotlinAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-kotlin is not available, returns a skipped result.
    """
    if not is_kotlin_tree_sitter_available():
        warnings.warn(
            "tree-sitter-kotlin not available. Install with: pip install hypergumbo[kotlin]",
            stacklevel=2,
        )
        return KotlinAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-kotlin not available",
        )

    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Import tree-sitter-kotlin
    try:
        import tree_sitter_kotlin
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_kotlin.language())
        parser = tree_sitter.Parser(lang)
    except Exception as e:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return KotlinAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=f"Failed to load Kotlin parser: {e}",
        )

    # Pass 1: Extract all symbols
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0

    for kt_file in find_kotlin_files(repo_root):
        analysis = _extract_symbols_from_file(kt_file, parser, run)
        if analysis.symbols:
            file_analyses[kt_file] = analysis
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

    for kt_file, analysis in file_analyses.items():
        all_symbols.extend(analysis.symbols)

        edges = _extract_edges_from_file(
            kt_file, parser, analysis.symbol_by_name, global_symbols, run
        )
        all_edges.extend(edges)

    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return KotlinAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
