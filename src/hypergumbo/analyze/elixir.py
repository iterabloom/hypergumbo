"""Elixir analysis pass using tree-sitter-elixir.

This analyzer uses tree-sitter to parse Elixir files and extract:
- Module declarations (defmodule)
- Function declarations (def/defp)
- Macro declarations (defmacro/defmacrop)
- Function call relationships
- Import relationships (use/import/alias)

If tree-sitter with Elixir support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-languages (with Elixir) is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and import directives

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-languages package which bundles Elixir grammar
- Two-pass allows cross-file call resolution
- Same pattern as Java/PHP/C analyzers for consistency
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

PASS_ID = "elixir-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_elixir_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Elixir files in the repository."""
    yield from find_files(repo_root, ["*.ex", "*.exs"])


def is_elixir_tree_sitter_available() -> bool:
    """Check if tree-sitter with Elixir grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    # Check for tree_sitter_language_pack which includes Elixir
    if importlib.util.find_spec("tree_sitter_language_pack") is None:
        return False
    return True


@dataclass
class ElixirAnalysisResult:
    """Result of analyzing Elixir files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"elixir:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for an Elixir file node (used as import edge source)."""
    return f"elixir:{path}:1-1:file:file"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _get_enclosing_modules(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Walk up the tree to find all enclosing module names, innermost first."""
    modules: list[str] = []
    current = node.parent
    while current is not None:
        if current.type == "call":
            target = _find_child_by_type(current, "identifier")
            if target and _node_text(target, source) == "defmodule":
                mod_name = _get_module_name_from_call(current, source)
                if mod_name:
                    modules.append(mod_name)
        current = current.parent
    return list(reversed(modules))  # Return outermost first


def _get_enclosing_function(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing function."""
    current = node.parent
    while current is not None:
        if current.type == "call":
            target = _find_child_by_type(current, "identifier")
            if target:
                target_name = _node_text(target, source)
                if target_name in ("def", "defp", "defmacro", "defmacrop"):
                    func_name = _get_function_name(current, source)
                    if func_name and func_name in local_symbols:
                        return local_symbols[func_name]
        current = current.parent
    return None  # pragma: no cover - defensive


def _get_module_name_from_call(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract module name from defmodule call node."""
    args = _find_child_by_type(node, "arguments")
    if args:
        for child in args.children:
            if child.type == "alias":
                return _node_text(child, source)
    return None


def _get_module_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract module name from defmodule call."""
    # defmodule has structure: (call target: (identifier "defmodule") arguments: (arguments (alias)))
    args = _find_child_by_type(node, "arguments")
    if args:
        for child in args.children:
            if child.type == "alias":
                return _node_text(child, source)
    return None


def _get_function_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function name from def/defp/defmacro call."""
    # def has structure: (call target: (identifier "def") arguments: (arguments (call target: (identifier "func_name") ...)))
    args = _find_child_by_type(node, "arguments")
    if args:
        for child in args.children:
            if child.type == "call":
                # The function name is the target of this call
                target = _find_child_by_type(child, "identifier")
                if target:
                    return _node_text(target, source)
            elif child.type == "identifier":
                # Simple case: def foo, do: :ok
                return _node_text(child, source)
    return None


def _extract_elixir_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from def/defp/defmacro call.

    Returns signature in format: (param1, param2, keyword: default)
    Elixir is dynamically typed, so no type annotations are included.
    """
    args = _find_child_by_type(node, "arguments")
    if args is None:  # pragma: no cover - defensive
        return "()"

    for child in args.children:
        if child.type == "call":
            # def foo(a, b) - parameters are in the arguments of the inner call
            inner_args = _find_child_by_type(child, "arguments")
            if inner_args is None:  # pragma: no cover - rare
                return "()"

            params: list[str] = []
            for param in inner_args.children:
                if param.type == "identifier":
                    # Simple positional parameter
                    params.append(_node_text(param, source))
                elif param.type == "binary_operator":  # pragma: no cover - default vals
                    # Default value: param \\ default
                    left = None
                    for pc in param.children:
                        if pc.type == "identifier":
                            left = _node_text(pc, source)
                            break
                    if left:
                        params.append(f"{left} \\\\ ...")
                elif param.type == "keywords":
                    # Keyword arguments
                    for kw_pair in param.children:
                        if kw_pair.type == "pair":
                            key_node = None
                            for pc in kw_pair.children:
                                if pc.type == "keyword":
                                    key_node = pc
                                    break
                            if key_node:
                                key_text = _node_text(key_node, source).rstrip(":")
                                params.append(f"{key_text}: ...")

            return f"({', '.join(params)})"

        elif child.type == "identifier":
            # Simple case: def foo, do: :ok (no parameters)
            return "()"

    return "()"  # pragma: no cover - defensive


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file."""

    symbols: list[Symbol] = field(default_factory=list)
    symbol_by_name: dict[str, Symbol] = field(default_factory=dict)
    current_module: str = ""


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
) -> FileAnalysis:
    """Extract symbols from a single Elixir file."""
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return FileAnalysis()

    analysis = FileAnalysis()

    for node in iter_tree(tree.root_node):
        # Check for defmodule
        if node.type == "call":
            target = _find_child_by_type(node, "identifier")
            if target:
                target_name = _node_text(target, source)

                if target_name == "defmodule":
                    module_name = _get_module_name(node, source)
                    if module_name:
                        # Handle nested modules by walking up
                        enclosing_modules = _get_enclosing_modules(node, source)
                        if enclosing_modules:
                            full_name = f"{'.'.join(enclosing_modules)}.{module_name}"
                        else:
                            full_name = module_name

                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1

                        symbol = Symbol(
                            id=_make_symbol_id(str(file_path), start_line, end_line, full_name, "module"),
                            name=full_name,
                            kind="module",
                            language="elixir",
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
                        analysis.symbol_by_name[full_name] = symbol

                elif target_name in ("def", "defp"):
                    func_name = _get_function_name(node, source)
                    if func_name:
                        enclosing_modules = _get_enclosing_modules(node, source)
                        current_module = ".".join(enclosing_modules) if enclosing_modules else ""
                        full_name = f"{current_module}.{func_name}" if current_module else func_name

                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1

                        symbol = Symbol(
                            id=_make_symbol_id(str(file_path), start_line, end_line, full_name, "function"),
                            name=full_name,
                            kind="function",
                            language="elixir",
                            path=str(file_path),
                            span=Span(
                                start_line=start_line,
                                end_line=end_line,
                                start_col=node.start_point[1],
                                end_col=node.end_point[1],
                            ),
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            signature=_extract_elixir_signature(node, source),
                        )
                        analysis.symbols.append(symbol)
                        analysis.symbol_by_name[func_name] = symbol  # Store by short name for local calls

                elif target_name in ("defmacro", "defmacrop"):
                    macro_name = _get_function_name(node, source)
                    if macro_name:
                        enclosing_modules = _get_enclosing_modules(node, source)
                        current_module = ".".join(enclosing_modules) if enclosing_modules else ""
                        full_name = f"{current_module}.{macro_name}" if current_module else macro_name

                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1

                        symbol = Symbol(
                            id=_make_symbol_id(str(file_path), start_line, end_line, full_name, "macro"),
                            name=full_name,
                            kind="macro",
                            language="elixir",
                            path=str(file_path),
                            span=Span(
                                start_line=start_line,
                                end_line=end_line,
                                start_col=node.start_point[1],
                                end_col=node.end_point[1],
                            ),
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            signature=_extract_elixir_signature(node, source),
                        )
                        analysis.symbols.append(symbol)
                        analysis.symbol_by_name[macro_name] = symbol

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
        if node.type == "call":
            target = _find_child_by_type(node, "identifier")
            if target:
                target_name = _node_text(target, source)

                # Detect use/import/alias directives
                if target_name == "use":
                    args = _find_child_by_type(node, "arguments")
                    if args:
                        for child in args.children:
                            if child.type == "alias":
                                module_name = _node_text(child, source)
                                edges.append(Edge.create(
                                    src=file_id,
                                    dst=f"elixir:{module_name}:0-0:module:module",
                                    edge_type="imports",
                                    line=node.start_point[0] + 1,
                                    evidence_type="use_directive",
                                    confidence=0.95,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                ))

                elif target_name == "import":
                    args = _find_child_by_type(node, "arguments")
                    if args:
                        for child in args.children:
                            if child.type == "alias":
                                module_name = _node_text(child, source)
                                edges.append(Edge.create(
                                    src=file_id,
                                    dst=f"elixir:{module_name}:0-0:module:module",
                                    edge_type="imports",
                                    line=node.start_point[0] + 1,
                                    evidence_type="import_directive",
                                    confidence=0.95,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                ))

                # Detect function calls within a function body
                elif target_name not in ("def", "defp", "defmacro", "defmacrop", "defmodule"):
                    current_function = _get_enclosing_function(node, source, local_symbols)
                    if current_function is not None:
                        # Check if this is a call to a known local function
                        if target_name in local_symbols:
                            callee = local_symbols[target_name]
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
                        elif target_name in global_symbols:
                            callee = global_symbols[target_name]
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


def analyze_elixir(repo_root: Path) -> ElixirAnalysisResult:
    """Analyze all Elixir files in a repository.

    Returns an ElixirAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-elixir is not available, returns a skipped result.
    """
    if not is_elixir_tree_sitter_available():
        warnings.warn(
            "tree-sitter-elixir not available. Install with: pip install hypergumbo[elixir]",
            stacklevel=2,
        )
        return ElixirAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-elixir not available",
        )

    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Import tree-sitter-language-pack for Elixir
    try:
        from tree_sitter_language_pack import get_parser
        parser = get_parser("elixir")
    except Exception as e:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return ElixirAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=f"Failed to load Elixir parser: {e}",
        )

    # Pass 1: Extract all symbols
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0

    for ex_file in find_elixir_files(repo_root):
        analysis = _extract_symbols_from_file(ex_file, parser, run)
        if analysis.symbols:
            file_analyses[ex_file] = analysis
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

    for ex_file, analysis in file_analyses.items():
        all_symbols.extend(analysis.symbols)

        edges = _extract_edges_from_file(
            ex_file, parser, analysis.symbol_by_name, global_symbols, run
        )
        all_edges.extend(edges)

    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return ElixirAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
