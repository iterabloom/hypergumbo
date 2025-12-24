"""Rust analysis pass using tree-sitter-rust.

This analyzer uses tree-sitter to parse Rust files and extract:
- Function declarations (fn)
- Struct declarations (struct)
- Enum declarations (enum)
- Impl blocks and their methods
- Trait declarations
- Function call relationships
- Import relationships (use statements)

If tree-sitter with Rust support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-rust is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and use statements

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-rust package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as Elixir/Java/PHP/C analyzers for consistency
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

PASS_ID = "rust-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_rust_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Rust files in the repository."""
    yield from find_files(repo_root, ["*.rs"])


def is_rust_tree_sitter_available() -> bool:
    """Check if tree-sitter with Rust grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec("tree_sitter_rust") is None:
        return False
    return True


@dataclass
class RustAnalysisResult:
    """Result of analyzing Rust files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"rust:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Rust file node (used as import edge source)."""
    return f"rust:{path}:1-1:file:file"


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
    """Extract symbols from a single Rust file."""
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return FileAnalysis()

    analysis = FileAnalysis()
    current_impl_target: Optional[str] = None

    def visit(node: "tree_sitter.Node") -> None:
        nonlocal current_impl_target

        # Function declaration
        if node.type == "function_item":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                func_name = _node_text(name_node, source)
                if current_impl_target:
                    full_name = f"{current_impl_target}::{func_name}"
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
                    language="rust",
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

        # Struct declaration
        elif node.type == "struct_item":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                struct_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, struct_name, "struct"),
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
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[struct_name] = symbol

        # Enum declaration
        elif node.type == "enum_item":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                enum_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, enum_name, "enum"),
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
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[enum_name] = symbol

        # Trait declaration
        elif node.type == "trait_item":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                trait_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, trait_name, "trait"),
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
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[trait_name] = symbol

        # Impl block - track the target type
        elif node.type == "impl_item":
            type_node = _find_child_by_field(node, "type")
            if type_node:
                old_impl_target = current_impl_target
                current_impl_target = _node_text(type_node, source)

                # Process children (methods)
                for child in node.children:
                    visit(child)

                current_impl_target = old_impl_target
                return  # Already processed children

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
        if node.type == "function_item":
            name_node = _find_child_by_field(node, "name")
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

        # Detect use statements
        elif node.type == "use_declaration":
            # Extract the path being imported
            path_node = _find_child_by_type(node, "scoped_identifier")
            if not path_node:
                path_node = _find_child_by_type(node, "identifier")
            if not path_node:
                path_node = _find_child_by_type(node, "use_wildcard")
            if not path_node:
                path_node = _find_child_by_type(node, "use_list")

            if path_node:
                import_path = _node_text(path_node, source)
                edges.append(Edge.create(
                    src=file_id,
                    dst=f"rust:{import_path}:0-0:module:module",
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    evidence_type="use_declaration",
                    confidence=0.95,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                ))

        # Detect function calls
        elif node.type == "call_expression":
            if current_function is not None:
                func_node = _find_child_by_field(node, "function")
                if func_node:
                    # Get the function name being called
                    if func_node.type == "identifier":
                        callee_name = _node_text(func_node, source)
                    elif func_node.type == "field_expression":
                        # method call like foo.bar()
                        field_node = _find_child_by_field(func_node, "field")
                        if field_node:
                            callee_name = _node_text(field_node, source)
                        else:
                            callee_name = None
                    elif func_node.type == "scoped_identifier":
                        # qualified call like Foo::bar()
                        name_node = _find_child_by_field(func_node, "name")
                        if name_node:
                            callee_name = _node_text(name_node, source)
                        else:
                            callee_name = _node_text(func_node, source)
                    else:
                        callee_name = None

                    if callee_name:
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


def analyze_rust(repo_root: Path) -> RustAnalysisResult:
    """Analyze all Rust files in a repository.

    Returns a RustAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-rust is not available, returns a skipped result.
    """
    if not is_rust_tree_sitter_available():
        warnings.warn(
            "tree-sitter-rust not available. Install with: pip install hypergumbo[rust]",
            stacklevel=2,
        )
        return RustAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-rust not available",
        )

    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Import tree-sitter-rust
    try:
        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)
    except Exception as e:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return RustAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=f"Failed to load Rust parser: {e}",
        )

    # Pass 1: Extract all symbols
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0

    for rs_file in find_rust_files(repo_root):
        analysis = _extract_symbols_from_file(rs_file, parser, run)
        if analysis.symbols:
            file_analyses[rs_file] = analysis
        else:
            files_skipped += 1

    # Build global symbol registry
    global_symbols: dict[str, Symbol] = {}
    for analysis in file_analyses.values():
        for symbol in analysis.symbols:
            # Store by short name for cross-file resolution
            short_name = symbol.name.split("::")[-1] if "::" in symbol.name else symbol.name
            global_symbols[short_name] = symbol
            global_symbols[symbol.name] = symbol

    # Pass 2: Extract edges
    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []

    for rs_file, analysis in file_analyses.items():
        all_symbols.extend(analysis.symbols)

        edges = _extract_edges_from_file(
            rs_file, parser, analysis.symbol_by_name, global_symbols, run
        )
        all_edges.extend(edges)

    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return RustAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
