"""Groovy analysis pass using tree-sitter-groovy.

This analyzer uses tree-sitter to parse Groovy files and extract:
- Class declarations
- Interface declarations
- Enum declarations
- Method declarations (inside classes)
- Top-level function definitions (def keyword)
- Function call relationships
- Import statements

Note: Trait declarations are not currently supported by tree-sitter-groovy v0.1.2
(the grammar parses 'trait X' as a function call). Support will be added when
the grammar is updated.

It also handles Gradle build files (.gradle) which use Groovy DSL.

If tree-sitter with Groovy support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-groovy is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and import statements

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-groovy package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as Kotlin/Java/Scala analyzers for consistency
- Gradle build files are analyzed as Groovy code
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

PASS_ID = "groovy-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_groovy_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Groovy files in the repository.

    Includes both .groovy files and .gradle build files.
    """
    yield from find_files(repo_root, ["*.groovy", "*.gradle"])


def is_groovy_tree_sitter_available() -> bool:
    """Check if tree-sitter with Groovy grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec("tree_sitter_groovy") is None:
        return False
    return True


@dataclass
class GroovyAnalysisResult:
    """Result of analyzing Groovy files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"groovy:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Groovy file node (used as import edge source)."""
    return f"groovy:{path}:1-1:file:file"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _find_child_by_field(node: "tree_sitter.Node", field_name: str) -> Optional["tree_sitter.Node"]:  # pragma: no cover
    """Find child by field name."""
    return node.child_by_field_name(field_name)


def _get_enclosing_class(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up the tree to find the enclosing class/interface/trait name."""
    current = node.parent
    while current is not None:
        if current.type in ("class_declaration", "interface_declaration", "trait_declaration"):
            name_node = _find_child_by_type(current, "identifier")
            if name_node:
                return _node_text(name_node, source)
        current = current.parent
    return None  # pragma: no cover - defensive


def _get_enclosing_function_groovy(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing method/function."""
    current = node.parent
    while current is not None:
        if current.type == "method_declaration":
            name_node = _find_child_by_type(current, "identifier")
            if name_node:
                method_name = _node_text(name_node, source)
                if method_name in local_symbols:
                    return local_symbols[method_name]
        elif current.type == "function_definition":
            name_node = _find_child_by_type(current, "identifier")
            if name_node:
                func_name = _node_text(name_node, source)
                if func_name in local_symbols:
                    return local_symbols[func_name]
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_groovy_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract method signature from a method_declaration or function_definition node.

    Returns signature in format: (Type param, Type param2): ReturnType
    Omits void return types.
    """
    params: list[str] = []
    return_type: Optional[str] = None

    # Look for formal_parameters node
    for child in node.children:
        if child.type == "formal_parameters":
            for param in child.children:
                if param.type == "formal_parameter":
                    param_type = None
                    param_name = None
                    for pc in param.children:
                        if pc.type in ("type_identifier", "primitive_type",
                                       "array_type", "generic_type"):
                            param_type = _node_text(pc, source)
                        elif pc.type == "identifier":
                            param_name = _node_text(pc, source)
                    if param_type and param_name:
                        params.append(f"{param_type} {param_name}")
                    elif param_name:  # pragma: no cover - dynamic typing
                        params.append(param_name)
        elif child.type in ("type_identifier", "primitive_type", "void_type",
                            "array_type", "generic_type"):
            # Return type appears before the method name
            return_type = _node_text(child, source)

    params_str = ", ".join(params)
    signature = f"({params_str})"

    if return_type and return_type != "void":
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
    """Extract symbols from a single Groovy file."""
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):  # pragma: no cover - file system edge case
        return FileAnalysis()

    analysis = FileAnalysis()

    for node in iter_tree(tree.root_node):
        # Class declaration
        if node.type == "class_declaration":
            name_node = _find_child_by_type(node, "identifier")

            if name_node:
                class_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, class_name, "class"),
                    name=class_name,
                    kind="class",
                    language="groovy",
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
                analysis.symbol_by_name[class_name] = symbol

        # Interface declaration
        elif node.type == "interface_declaration":
            name_node = _find_child_by_type(node, "identifier")

            if name_node:
                iface_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, iface_name, "interface"),
                    name=iface_name,
                    kind="interface",
                    language="groovy",
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
                analysis.symbol_by_name[iface_name] = symbol

        # Trait declaration (Groovy-specific)
        # NOTE: tree-sitter-groovy v0.1.2 doesn't produce trait_declaration nodes
        # This code is kept for future grammar updates
        elif node.type == "trait_declaration":  # pragma: no cover - grammar limitation
            name_node = _find_child_by_type(node, "identifier")

            if name_node:
                trait_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, trait_name, "trait"),
                    name=trait_name,
                    kind="trait",
                    language="groovy",
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
                analysis.symbol_by_name[trait_name] = symbol

        # Enum declaration
        elif node.type == "enum_declaration":
            name_node = _find_child_by_type(node, "identifier")

            if name_node:
                enum_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, enum_name, "enum"),
                    name=enum_name,
                    kind="enum",
                    language="groovy",
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
                analysis.symbol_by_name[enum_name] = symbol

        # Method declaration (inside class/trait)
        elif node.type == "method_declaration":
            name_node = _find_child_by_type(node, "identifier")

            if name_node:
                method_name = _node_text(name_node, source)
                current_class = _get_enclosing_class(node, source)
                if current_class:
                    full_name = f"{current_class}.{method_name}"
                else:  # pragma: no cover - defensive: methods always in classes
                    full_name = method_name

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, full_name, "method"),
                    name=full_name,
                    kind="method",
                    language="groovy",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    signature=_extract_groovy_signature(node, source),
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[method_name] = symbol
                analysis.symbol_by_name[full_name] = symbol

        # Function definition (def keyword at top level)
        elif node.type == "function_definition":
            name_node = _find_child_by_type(node, "identifier")
            current_class = _get_enclosing_class(node, source)

            if name_node and current_class is None:
                func_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, func_name, "function"),
                    name=func_name,
                    kind="function",
                    language="groovy",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    signature=_extract_groovy_signature(node, source),
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[func_name] = symbol

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
    except (OSError, IOError):  # pragma: no cover - file system edge case
        return []

    edges: list[Edge] = []
    file_id = _make_file_id(str(file_path))

    for node in iter_tree(tree.root_node):
        # Detect import statements
        if node.type == "import_declaration":
            # Get the scoped identifier being imported
            id_node = _find_child_by_type(node, "scoped_identifier")
            if not id_node:  # pragma: no cover - grammar fallback
                id_node = _find_child_by_type(node, "identifier")
            if id_node:
                import_path = _node_text(id_node, source)
                edges.append(Edge.create(
                    src=file_id,
                    dst=f"groovy:{import_path}:0-0:package:package",
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    evidence_type="import_statement",
                    confidence=0.95,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                ))

        # Detect function calls (various forms in Groovy)
        # method_invocation: helper() inside a method
        # juxt_function_call: println "hello" (Groovy's operator-less call syntax)
        elif node.type in ("method_invocation", "juxt_function_call"):
            current_function = _get_enclosing_function_groovy(node, source, local_symbols)
            if current_function is not None:
                # Get the function/method being called
                callee_node = _find_child_by_type(node, "identifier")
                if not callee_node:  # pragma: no cover - grammar fallback
                    callee_node = _find_child_by_type(node, "simple_identifier")

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


def analyze_groovy(repo_root: Path) -> GroovyAnalysisResult:
    """Analyze all Groovy files in a repository.

    Returns a GroovyAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-groovy is not available, returns a skipped result.
    """
    if not is_groovy_tree_sitter_available():
        warnings.warn(
            "tree-sitter-groovy not available. Install with: pip install hypergumbo[groovy]",
            stacklevel=2,
        )
        return GroovyAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-groovy not available",
        )

    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Import tree-sitter-groovy
    try:
        import tree_sitter_groovy
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_groovy.language())
        parser = tree_sitter.Parser(lang)
    except Exception as e:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return GroovyAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=f"Failed to load Groovy parser: {e}",
        )

    # Pass 1: Extract all symbols
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0

    for groovy_file in find_groovy_files(repo_root):
        analysis = _extract_symbols_from_file(groovy_file, parser, run)
        if analysis.symbols:
            file_analyses[groovy_file] = analysis
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

    for groovy_file, analysis in file_analyses.items():
        all_symbols.extend(analysis.symbols)

        edges = _extract_edges_from_file(
            groovy_file, parser, analysis.symbol_by_name, global_symbols, run
        )
        all_edges.extend(edges)

    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return GroovyAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
