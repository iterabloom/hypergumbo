"""Ruby analysis pass using tree-sitter-ruby.

This analyzer uses tree-sitter to parse Ruby files and extract:
- Method definitions (def)
- Class declarations (class)
- Module declarations (module)
- Method call relationships
- Require/require_relative statements

If tree-sitter with Ruby support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-ruby is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect method calls and require statements

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-ruby package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as Go/Rust/Elixir/Java/PHP/C analyzers for consistency
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

PASS_ID = "ruby-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_ruby_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Ruby files in the repository."""
    yield from find_files(repo_root, ["*.rb"])


def is_ruby_tree_sitter_available() -> bool:
    """Check if tree-sitter with Ruby grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec("tree_sitter_ruby") is None:
        return False
    return True


@dataclass
class RubyAnalysisResult:
    """Result of analyzing Ruby files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"ruby:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Ruby file node (used as import edge source)."""
    return f"ruby:{path}:1-1:file:file"


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
    """Extract symbols from a single Ruby file."""
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return FileAnalysis()

    analysis = FileAnalysis()
    current_class: Optional[str] = None
    current_module: Optional[str] = None

    def visit(node: "tree_sitter.Node") -> None:
        nonlocal current_class, current_module

        # Method definition
        if node.type == "method":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                method_name = _node_text(name_node, source)
                # Qualify with class/module name if inside one
                if current_class:
                    full_name = f"{current_class}#{method_name}"
                elif current_module:
                    full_name = f"{current_module}.{method_name}"
                else:
                    full_name = method_name

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, full_name, "method"),
                    name=full_name,
                    kind="method",
                    language="ruby",
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
                analysis.symbol_by_name[method_name] = symbol
                analysis.symbol_by_name[full_name] = symbol

        # Class definition
        elif node.type == "class":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                class_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, class_name, "class"),
                    name=class_name,
                    kind="class",
                    language="ruby",
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

                # Process class body with class context
                old_class = current_class
                current_class = class_name
                for child in node.children:
                    visit(child)
                current_class = old_class
                return  # Already processed children

        # Module definition
        elif node.type == "module":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                module_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, module_name, "module"),
                    name=module_name,
                    kind="module",
                    language="ruby",
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

                # Process module body with module context
                old_module = current_module
                current_module = module_name
                for child in node.children:
                    visit(child)
                current_module = old_module
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
    current_method: Optional[Symbol] = None

    def visit(node: "tree_sitter.Node") -> None:
        nonlocal current_method

        # Track current method for call edges
        if node.type == "method":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                method_name = _node_text(name_node, source)
                if method_name in local_symbols:
                    old_method = current_method
                    current_method = local_symbols[method_name]

                    # Process method body
                    for child in node.children:
                        visit(child)

                    current_method = old_method
                    return

        # Detect call nodes (require statements and method calls)
        elif node.type == "call":
            # Get method name from identifier child
            method_node = None
            for child in node.children:
                if child.type == "identifier":
                    method_node = child
                    break

            if method_node:
                callee_name = _node_text(method_node, source)

                # Handle require/require_relative as imports
                if callee_name in ("require", "require_relative"):
                    args_node = _find_child_by_field(node, "arguments")
                    if args_node:
                        for arg in args_node.children:
                            if arg.type == "string":
                                content_node = _find_child_by_type(arg, "string_content")
                                if content_node:
                                    import_path = _node_text(content_node, source)
                                    edges.append(Edge.create(
                                        src=file_id,
                                        dst=f"ruby:{import_path}:0-0:file:file",
                                        edge_type="imports",
                                        line=node.start_point[0] + 1,
                                        evidence_type="require_statement",
                                        confidence=0.95,
                                        origin=PASS_ID,
                                        origin_run_id=run.execution_id,
                                    ))

                # Handle regular method calls
                elif current_method is not None:
                    # Check local symbols first
                    if callee_name in local_symbols:
                        callee = local_symbols[callee_name]
                        edges.append(Edge.create(
                            src=current_method.id,
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
                            src=current_method.id,
                            dst=callee.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            evidence_type="method_call",
                            confidence=0.80,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))

        # Detect bare method calls (identifier nodes that are method names)
        elif node.type == "identifier":
            if current_method is not None:
                callee_name = _node_text(node, source)
                # Check if this identifier is a known method
                if callee_name in local_symbols:
                    callee = local_symbols[callee_name]
                    if callee.kind == "method" and callee.id != current_method.id:
                        edges.append(Edge.create(
                            src=current_method.id,
                            dst=callee.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            evidence_type="bare_method_call",
                            confidence=0.75,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))
                elif callee_name in global_symbols:
                    callee = global_symbols[callee_name]
                    if callee.kind == "method" and callee.id != current_method.id:
                        edges.append(Edge.create(
                            src=current_method.id,
                            dst=callee.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            evidence_type="bare_method_call",
                            confidence=0.70,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))

        # Recurse
        for child in node.children:
            visit(child)

    visit(tree.root_node)
    return edges


def analyze_ruby(repo_root: Path) -> RubyAnalysisResult:
    """Analyze all Ruby files in a repository.

    Returns a RubyAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-ruby is not available, returns a skipped result.
    """
    if not is_ruby_tree_sitter_available():
        warnings.warn(
            "tree-sitter-ruby not available. Install with: pip install hypergumbo[ruby]",
            stacklevel=2,
        )
        return RubyAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-ruby not available",
        )

    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Import tree-sitter-ruby
    try:
        import tree_sitter_ruby
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_ruby.language())
        parser = tree_sitter.Parser(lang)
    except Exception as e:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return RubyAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=f"Failed to load Ruby parser: {e}",
        )

    # Pass 1: Extract all symbols
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0

    for rb_file in find_ruby_files(repo_root):
        analysis = _extract_symbols_from_file(rb_file, parser, run)
        if analysis.symbols:
            file_analyses[rb_file] = analysis
        else:
            files_skipped += 1

    # Build global symbol registry
    global_symbols: dict[str, Symbol] = {}
    for analysis in file_analyses.values():
        for symbol in analysis.symbols:
            # Store by short name for cross-file resolution
            short_name = symbol.name.split("#")[-1] if "#" in symbol.name else symbol.name
            short_name = short_name.split(".")[-1] if "." in short_name else short_name
            global_symbols[short_name] = symbol
            global_symbols[symbol.name] = symbol

    # Pass 2: Extract edges
    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []

    for rb_file, analysis in file_analyses.items():
        all_symbols.extend(analysis.symbols)

        edges = _extract_edges_from_file(
            rb_file, parser, analysis.symbol_by_name, global_symbols, run
        )
        all_edges.extend(edges)

    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return RubyAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
