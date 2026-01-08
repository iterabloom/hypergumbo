"""Scala analysis pass using tree-sitter-scala.

This analyzer uses tree-sitter to parse Scala files and extract:
- Function definitions (def)
- Class definitions (class)
- Object definitions (object)
- Trait definitions (trait)
- Method definitions (inside classes/objects/traits)
- Function call relationships
- Import statements

If tree-sitter with Scala support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-scala is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and import statements

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-scala package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as Go/Ruby/Kotlin/Swift/Rust/Elixir/Java/PHP/C analyzers
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

PASS_ID = "scala-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_scala_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Scala files in the repository."""
    yield from find_files(repo_root, ["*.scala"])


def is_scala_tree_sitter_available() -> bool:
    """Check if tree-sitter with Scala grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec("tree_sitter_scala") is None:
        return False
    return True


@dataclass
class ScalaAnalysisResult:
    """Result of analyzing Scala files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"scala:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Scala file node (used as import edge source)."""
    return f"scala:{path}:1-1:file:file"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _get_enclosing_type(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up the tree to find the enclosing class/object/trait name."""
    current = node.parent
    while current is not None:
        if current.type in ("class_definition", "object_definition", "trait_definition"):
            name_node = _find_child_by_type(current, "identifier")
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
        if current.type == "function_definition":
            name_node = _find_child_by_type(current, "identifier")
            if name_node:
                func_name = _node_text(name_node, source)
                if func_name in local_symbols:
                    return local_symbols[func_name]
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_scala_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a Scala function definition.

    Returns signature like:
    - "(x: Int, y: Int): Int" for regular functions
    - "(message: String)" for Unit functions (Unit omitted)

    Args:
        node: The function_definition or function_declaration node.
        source: The source code bytes.

    Returns:
        The signature string, or None if extraction fails.
    """
    params: list[str] = []
    return_type = None
    found_params = False

    # Iterate through children to find parameters and return type
    for child in node.children:
        if child.type == "parameters":
            found_params = True
            for subchild in child.children:
                if subchild.type == "parameter":
                    param_name = None
                    param_type = None
                    for pc in subchild.children:
                        if pc.type == "identifier" and param_name is None:
                            param_name = _node_text(pc, source)
                        elif pc.type in ("type_identifier", "generic_type", "tuple_type",
                                         "function_type", "infix_type"):
                            param_type = _node_text(pc, source)
                    if param_name and param_type:
                        params.append(f"{param_name}: {param_type}")
        # Return type is a type_identifier that comes after parameters
        elif found_params and child.type in ("type_identifier", "generic_type",
                                              "tuple_type", "function_type", "infix_type"):
            return_type = _node_text(child, source)

    params_str = ", ".join(params)
    signature = f"({params_str})"

    if return_type and return_type != "Unit":
        signature += f": {return_type}"

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
    """Extract symbols from a single Scala file."""
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return FileAnalysis()

    analysis = FileAnalysis()

    for node in iter_tree(tree.root_node):
        # Function definition (def name(...))
        if node.type == "function_definition":
            name_node = _find_child_by_type(node, "identifier")

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
                signature = _extract_scala_signature(node, source)

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, full_name, kind),
                    name=full_name,
                    kind=kind,
                    language="scala",
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

        # Function declaration (abstract method in trait)
        elif node.type == "function_declaration":
            name_node = _find_child_by_type(node, "identifier")

            if name_node:
                func_name = _node_text(name_node, source)
                enclosing_type = _get_enclosing_type(node, source)
                if enclosing_type:
                    full_name = f"{enclosing_type}.{func_name}"
                else:
                    full_name = func_name  # pragma: no cover - abstract methods are in traits

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract signature
                signature = _extract_scala_signature(node, source)

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, full_name, "method"),
                    name=full_name,
                    kind="method",
                    language="scala",
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

        # Class definition
        elif node.type == "class_definition":
            name_node = _find_child_by_type(node, "identifier")

            if name_node:
                type_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, type_name, "class"),
                    name=type_name,
                    kind="class",
                    language="scala",
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

        # Object definition
        elif node.type == "object_definition":
            name_node = _find_child_by_type(node, "identifier")

            if name_node:
                type_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, type_name, "object"),
                    name=type_name,
                    kind="object",
                    language="scala",
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

        # Trait definition
        elif node.type == "trait_definition":
            name_node = _find_child_by_type(node, "identifier")

            if name_node:
                type_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, type_name, "trait"),
                    name=type_name,
                    kind="trait",
                    language="scala",
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
            # Build full import path from identifier children
            identifiers = [child for child in node.children if child.type == "identifier"]
            if identifiers:
                import_path = ".".join(_node_text(id_node, source) for id_node in identifiers)
                edges.append(Edge.create(
                    src=file_id,
                    dst=f"scala:{import_path}:0-0:package:package",
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
                callee_node = _find_child_by_type(node, "identifier")
                if not callee_node:
                    # Try field expression for method calls
                    field_node = _find_child_by_type(node, "field_expression")  # pragma: no cover - grammar fallback
                    if field_node:  # pragma: no cover - grammar fallback
                        callee_node = _find_child_by_type(field_node, "identifier")

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


def analyze_scala(repo_root: Path) -> ScalaAnalysisResult:
    """Analyze all Scala files in a repository.

    Returns a ScalaAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-scala is not available, returns a skipped result.
    """
    if not is_scala_tree_sitter_available():
        warnings.warn(
            "tree-sitter-scala not available. Install with: pip install hypergumbo[scala]",
            stacklevel=2,
        )
        return ScalaAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-scala not available",
        )

    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Import tree-sitter-scala
    try:
        import tree_sitter_scala
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_scala.language())
        parser = tree_sitter.Parser(lang)
    except Exception as e:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return ScalaAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=f"Failed to load Scala parser: {e}",
        )

    # Pass 1: Extract all symbols
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0

    for scala_file in find_scala_files(repo_root):
        analysis = _extract_symbols_from_file(scala_file, parser, run)
        if analysis.symbols:
            file_analyses[scala_file] = analysis
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

    for scala_file, analysis in file_analyses.items():
        all_symbols.extend(analysis.symbols)

        edges = _extract_edges_from_file(
            scala_file, parser, analysis.symbol_by_name, global_symbols, run
        )
        all_edges.extend(edges)

    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return ScalaAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
