"""Julia analysis pass using tree-sitter-julia.

This analyzer uses tree-sitter to parse Julia files and extract:
- Module declarations
- Function declarations (both full and short-form)
- Struct declarations (mutable and immutable)
- Abstract type declarations
- Macro declarations
- Constant declarations
- Function call relationships
- Import statements (import/using)

If tree-sitter with Julia support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-julia is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and import statements

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-julia package for grammar
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

PASS_ID = "julia-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_julia_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Julia files in the repository."""
    yield from find_files(repo_root, ["*.jl"])


def is_julia_tree_sitter_available() -> bool:
    """Check if tree-sitter with Julia grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec("tree_sitter_julia") is None:
        return False
    return True


@dataclass
class JuliaAnalysisResult:
    """Result of analyzing Julia files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"julia:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Julia file node (used as import edge source)."""
    return f"julia:{path}:1-1:file:file"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file."""

    symbols: list[Symbol] = field(default_factory=list)
    symbol_by_name: dict[str, Symbol] = field(default_factory=dict)


def _extract_function_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function name from signature or assignment."""
    # For full function definitions: function name(args)
    sig_node = _find_child_by_type(node, "signature")
    if sig_node:
        call_node = _find_child_by_type(sig_node, "call_expression")
        if call_node:
            id_node = _find_child_by_type(call_node, "identifier")
            if id_node:
                return _node_text(id_node, source)
    return None  # pragma: no cover


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
) -> FileAnalysis:
    """Extract symbols from a single Julia file."""
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):  # pragma: no cover - file system edge case
        return FileAnalysis()

    analysis = FileAnalysis()
    current_module: Optional[str] = None

    def visit(node: "tree_sitter.Node") -> None:
        nonlocal current_module

        # Module definition
        if node.type == "module_definition":
            id_node = _find_child_by_type(node, "identifier")
            if id_node:
                module_name = _node_text(id_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, module_name, "module"),
                    name=module_name,
                    kind="module",
                    language="julia",
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
                analysis.symbol_by_name[module_name] = symbol

                # Process body with module context
                old_module = current_module
                current_module = module_name
                for child in node.children:
                    visit(child)
                current_module = old_module
                return

        # Function definition (full form)
        elif node.type == "function_definition":
            func_name = _extract_function_name(node, source)
            if func_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                full_name = f"{current_module}.{func_name}" if current_module else func_name

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, full_name, "function"),
                    name=func_name,
                    kind="function",
                    language="julia",
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

        # Short-form function definition: f(x) = expr
        elif node.type == "assignment":
            # Check if left side is a call_expression (function definition)
            left_node = node.children[0] if node.children else None
            if left_node and left_node.type == "call_expression":
                id_node = _find_child_by_type(left_node, "identifier")
                if id_node:
                    func_name = _node_text(id_node, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1

                    symbol = Symbol(
                        id=_make_symbol_id(str(file_path), start_line, end_line, func_name, "function"),
                        name=func_name,
                        kind="function",
                        language="julia",
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

        # Struct definition
        elif node.type == "struct_definition":
            type_head = _find_child_by_type(node, "type_head")
            if type_head:
                # Get name from type_head (handles both simple and subtype syntax)
                id_node = _find_child_by_type(type_head, "identifier")
                if not id_node:  # pragma: no cover
                    # Try binary_expression for subtype syntax (Circle <: Shape)
                    bin_node = _find_child_by_type(type_head, "binary_expression")
                    if bin_node:
                        id_node = _find_child_by_type(bin_node, "identifier")

                if id_node:
                    struct_name = _node_text(id_node, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1

                    symbol = Symbol(
                        id=_make_symbol_id(str(file_path), start_line, end_line, struct_name, "struct"),
                        name=struct_name,
                        kind="struct",
                        language="julia",
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
                    analysis.symbol_by_name[struct_name] = symbol

        # Abstract type definition
        elif node.type == "abstract_definition":
            type_head = _find_child_by_type(node, "type_head")
            if type_head:
                id_node = _find_child_by_type(type_head, "identifier")
                if id_node:
                    abstract_name = _node_text(id_node, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1

                    symbol = Symbol(
                        id=_make_symbol_id(str(file_path), start_line, end_line, abstract_name, "abstract"),
                        name=abstract_name,
                        kind="abstract",
                        language="julia",
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
                    analysis.symbol_by_name[abstract_name] = symbol

        # Macro definition
        elif node.type == "macro_definition":
            sig_node = _find_child_by_type(node, "signature")
            if sig_node:
                call_node = _find_child_by_type(sig_node, "call_expression")
                if call_node:
                    id_node = _find_child_by_type(call_node, "identifier")
                    if id_node:
                        macro_name = _node_text(id_node, source)
                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1

                        symbol = Symbol(
                            id=_make_symbol_id(str(file_path), start_line, end_line, macro_name, "macro"),
                            name=macro_name,
                            kind="macro",
                            language="julia",
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
                        analysis.symbol_by_name[macro_name] = symbol

        # Const statement
        elif node.type == "const_statement":
            assign_node = _find_child_by_type(node, "assignment")
            if assign_node:
                id_node = _find_child_by_type(assign_node, "identifier")
                if id_node:
                    const_name = _node_text(id_node, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1

                    symbol = Symbol(
                        id=_make_symbol_id(str(file_path), start_line, end_line, const_name, "const"),
                        name=const_name,
                        kind="const",
                        language="julia",
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
                    analysis.symbol_by_name[const_name] = symbol

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
    except (OSError, IOError):  # pragma: no cover - file system edge case
        return []

    edges: list[Edge] = []
    file_id = _make_file_id(str(file_path))
    current_function: Optional[Symbol] = None

    def visit(node: "tree_sitter.Node") -> None:
        nonlocal current_function

        # Track current function for call edges
        if node.type == "function_definition":
            func_name = _extract_function_name(node, source)
            if func_name and func_name in local_symbols:
                old_function = current_function
                current_function = local_symbols[func_name]

                # Process function body
                for child in node.children:
                    visit(child)

                current_function = old_function
                return

        # Track short-form function for call edges
        elif node.type == "assignment":
            left_node = node.children[0] if node.children else None
            if left_node and left_node.type == "call_expression":
                id_node = _find_child_by_type(left_node, "identifier")
                if id_node:
                    func_name = _node_text(id_node, source)
                    if func_name in local_symbols:
                        old_function = current_function
                        current_function = local_symbols[func_name]

                        # Process right side
                        for child in node.children[2:]:
                            visit(child)

                        current_function = old_function
                        return

        # Detect import/using statements
        elif node.type in ("import_statement", "using_statement"):
            # Get the import path
            scoped_node = _find_child_by_type(node, "scoped_identifier")
            if scoped_node:
                import_path = _node_text(scoped_node, source)
            else:
                id_node = _find_child_by_type(node, "identifier")
                if id_node:
                    import_path = _node_text(id_node, source)
                else:
                    import_path = None  # pragma: no cover

            if import_path:
                edges.append(Edge.create(
                    src=file_id,
                    dst=f"julia:{import_path}:0-0:package:package",
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
                id_node = _find_child_by_type(node, "identifier")
                if id_node:
                    callee_name = _node_text(id_node, source)

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


def analyze_julia(repo_root: Path) -> JuliaAnalysisResult:
    """Analyze all Julia files in a repository.

    Returns a JuliaAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-julia is not available, returns a skipped result.
    """
    if not is_julia_tree_sitter_available():
        warnings.warn(
            "tree-sitter-julia not available. Install with: pip install hypergumbo[julia]",
            stacklevel=2,
        )
        return JuliaAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-julia not available",
        )

    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Import tree-sitter-julia
    try:
        import tree_sitter_julia
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_julia.language())
        parser = tree_sitter.Parser(lang)
    except Exception as e:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return JuliaAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=f"Failed to load Julia parser: {e}",
        )

    # Pass 1: Extract all symbols
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0
    all_files: list[Path] = []

    for julia_file in find_julia_files(repo_root):
        all_files.append(julia_file)
        analysis = _extract_symbols_from_file(julia_file, parser, run)
        if analysis.symbols:
            file_analyses[julia_file] = analysis
        else:
            files_skipped += 1

    # Build global symbol registry
    global_symbols: dict[str, Symbol] = {}
    for analysis in file_analyses.values():
        for symbol in analysis.symbols:
            global_symbols[symbol.name] = symbol

    # Pass 2: Extract edges
    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []

    for julia_file, analysis in file_analyses.items():
        all_symbols.extend(analysis.symbols)

        edges = _extract_edges_from_file(
            julia_file, parser, analysis.symbol_by_name, global_symbols, run
        )
        all_edges.extend(edges)

    # Also extract edges from files without symbols (for import-only files)
    for julia_file in all_files:
        if julia_file not in file_analyses:
            edges = _extract_edges_from_file(
                julia_file, parser, {}, global_symbols, run
            )
            all_edges.extend(edges)

    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return JuliaAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
