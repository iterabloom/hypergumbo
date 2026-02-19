"""Wolfram Language analysis pass using tree-sitter-wolfram.

This analyzer uses tree-sitter to parse Wolfram Language files and extract:
- Function definitions (SetDelayed :=)
- Variable assignments (Set =)
- Function calls
- Import statements (Get, Needs, Import)

Wolfram Language (also known as Mathematica) is a symbolic programming language
used for technical computing, data science, and mathematical modeling.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Parse all files, extract all symbols into global registry
2. Pass 2: Detect function calls and imports

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Wolfram-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates ~150 lines of boilerplate
- Built from source since not on PyPI
- Uses tree-sitter-wolfram grammar (bostick/tree-sitter-wolfram)
- Two-pass allows cross-file resolution
- Wolfram uses [] for function calls, not ()

Wolfram Language Considerations
-------------------------------
- Function definitions use SetDelayed (:=) or Set (=)
- Pattern matching uses underscores (x_) for arguments
- Imports use Get["package`"], Needs["package`"], or <<package`
- Package names end with backtick (`)
- Comments use (* ... *)
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import classify_dot_m_file, find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("wolfram")


def find_wolfram_files(repo_root: Path) -> Iterator[Path]:
    """Yield Wolfram files, disambiguating .m files via content heuristics.

    Wolfram uses .wl, .wls, and .nb extensions unambiguously. The .m extension
    is shared with MATLAB and Objective-C, so those files are classified by
    content before inclusion.
    """
    # Unambiguous Wolfram extensions
    yield from find_files(repo_root, ["*.wl", "*.wls", "*.nb"])
    # Ambiguous .m files — only include if classified as Wolfram
    for path in find_files(repo_root, ["*.m"]):
        if classify_dot_m_file(path) == "wolfram":
            yield path


def _make_module_id(module_name: str) -> str:
    """Generate ID for a Wolfram module (used as import edge target)."""
    return f"wolfram:{module_name}:0-0:module:module"


def _extract_wolfram_signature(
    call_node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a Wolfram function call pattern.

    Wolfram function definitions use pattern matching:
    f[x_, y_] := body -> [x_, y_]
    f[x_Integer, y_List] := body -> [x_Integer, y_List]

    Returns signature string like "[x_, y_]" or None.
    """
    # Find the argument list within the call
    params: list[str] = []

    # Look for pattern arguments in the call's children
    in_brackets = False
    for child in call_node.children:
        if child.type == "[":
            in_brackets = True
            continue
        if child.type == "]":
            break
        if not in_brackets:
            continue

        # Skip commas
        if child.type == ",":  # pragma: no cover - separator
            continue  # pragma: no cover

        # Collect pattern arguments (pattern, blank, etc.)
        if child.type in ("pattern", "blank", "blank_sequence", "blank_null_sequence",
                          "pattern_blank", "pattern_blank_sequence", "pattern_blank_null_sequence",
                          "symbol"):
            param_text = node_text(child, source).strip()
            if param_text:
                params.append(param_text)

    if params:
        return "[" + ", ".join(params) + "]"
    return "[]"


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> list[Symbol]:
    """Extract all symbols from a parsed Wolfram file.

    Detects:
    - function: SetDelayed (:=) with call on left side
    - variable: Set (=) with symbol on left side

    Uses iterative traversal to avoid RecursionError on deeply nested code.
    """
    symbols: list[Symbol] = []
    seen_names: set[str] = set()

    def add_symbol(
        node: "tree_sitter.Node",
        name: str,
        kind: str,
        meta: dict | None = None,
        signature: Optional[str] = None,
    ) -> None:
        """Add a symbol if not already seen."""
        if not name or name in seen_names:  # pragma: no cover - defensive
            return  # pragma: no cover
        seen_names.add(name)

        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        span = Span(
            start_line=start_line,
            end_line=end_line,
            start_col=node.start_point[1],
            end_col=node.end_point[1],
        )
        sym_id = make_symbol_id("wolfram", file_path, start_line, end_line, name, kind)
        sym = Symbol(
            id=sym_id,
            name=name,
            kind=kind,
            language="wolfram",
            path=file_path,
            span=span,
            origin=PASS_ID,
            origin_run_id=run_id,
            signature=signature,
        )
        if meta:  # pragma: no cover - meta rarely used
            sym.meta = meta  # pragma: no cover
        symbols.append(sym)

    for node in iter_tree(tree.root_node):
        # Look for binary expressions with := or =
        if node.type == "binary":
            children = node.children
            # Pattern: left_side operator right_side
            # Check for := (SetDelayed) - function definition
            # Check for = (Set) - assignment
            op_node = None
            left_node = None
            for i, child in enumerate(children):
                if child.type == ":=":
                    op_node = child
                    left_node = children[i - 1] if i > 0 else None
                    break
                elif child.type == "=":
                    op_node = child
                    left_node = children[i - 1] if i > 0 else None
                    break

            if op_node and left_node:
                if op_node.type == ":=":
                    # SetDelayed - function definition
                    # Left side is usually a call like f[x_]
                    if left_node.type == "call":
                        # Get the function name from the call
                        func_name_node = find_child_by_type(left_node, "symbol")
                        if func_name_node:
                            func_name = node_text(func_name_node, source).strip()
                            if func_name:
                                # Extract signature from the call pattern
                                signature = _extract_wolfram_signature(left_node, source)
                                add_symbol(node, func_name, "function", signature=signature)
                    elif left_node.type == "symbol":  # pragma: no cover - simple pattern
                        # Could be a simple pattern like f := ...
                        sym_name = node_text(left_node, source).strip()  # pragma: no cover
                        if sym_name:  # pragma: no cover
                            add_symbol(node, sym_name, "function")  # pragma: no cover
                elif op_node.type == "=":
                    # Set - variable assignment
                    if left_node.type == "symbol":
                        var_name = node_text(left_node, source).strip()
                        if var_name:
                            add_symbol(node, var_name, "variable")
                    elif left_node.type == "call":  # pragma: no cover - immediate def
                        # Could be like f[x_] = ... (immediate definition)
                        func_name_node = find_child_by_type(left_node, "symbol")  # pragma: no cover
                        if func_name_node:  # pragma: no cover
                            func_name = node_text(func_name_node, source).strip()  # pragma: no cover
                            if func_name:  # pragma: no cover
                                add_symbol(node, func_name, "function")  # pragma: no cover

    return symbols


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    resolver: "NameResolver",
    run_id: str,
) -> list[Edge]:
    """Extract import and call edges from a parsed Wolfram file.

    Detects:
    - imports: Get["package`"], Needs["package`"], Import["file"]
    - calls: function calls like Sin[x], Map[f, list]

    Uses iterative traversal to avoid RecursionError on deeply nested code.
    """
    edges: list[Edge] = []
    file_id = make_file_id("wolfram", file_path)
    seen_calls: set[str] = set()

    for node in iter_tree(tree.root_node):
        # Look for function calls
        if node.type == "call":
            # First child should be the function name
            func_name_node = find_child_by_type(node, "symbol")
            if func_name_node:
                func_name = node_text(func_name_node, source).strip()
                if func_name:
                    # Check for import functions
                    if func_name in ("Get", "Needs", "Import"):
                        # Find the string argument
                        string_node = find_child_by_type(node, "string")
                        if string_node:
                            string_text = node_text(string_node, source).strip()
                            # Remove quotes
                            module_name = string_text.strip('"').strip("'")
                            if module_name:
                                module_id = _make_module_id(module_name)
                                edge = Edge.create(
                                    src=file_id,
                                    dst=module_id,
                                    edge_type="imports",
                                    line=node.start_point[0] + 1,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                    evidence_type="import",
                                    confidence=0.95,
                                )
                                edges.append(edge)
                    else:
                        # Regular function call
                        call_key = f"{file_id}->{func_name}"
                        if call_key not in seen_calls:
                            seen_calls.add(call_key)
                            # Check if it's a known symbol
                            result = resolver.lookup(func_name)
                            if result.found:
                                target_id = result.symbol.id
                            else:
                                # Create synthetic ID for built-in
                                target_id = f"wolfram:builtin:0-0:{func_name}:function"

                            edge = Edge.create(
                                src=file_id,
                                dst=target_id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="call",
                                confidence=0.9,
                            )
                            edges.append(edge)

    return edges


class WolframAnalyzer(TreeSitterAnalyzer):
    """Wolfram Language analyzer using tree-sitter-wolfram."""

    lang = "wolfram"
    file_patterns: ClassVar[list[str]] = ["*.wl", "*.wls", "*.nb"]
    grammar_module = "tree_sitter_wolfram"
    create_file_symbols = True

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract function and variable symbols from a Wolfram file."""
        analysis = FileAnalysis()
        symbols = _extract_symbols_from_file(tree, source, rel_path, run.execution_id)
        analysis.symbols = symbols
        for sym in symbols:
            analysis.symbol_by_name[sym.name] = sym
        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract import and call edges from a Wolfram file."""
        # Use the resolver that was built from global_symbols
        return _extract_edges_from_file(
            tree, source, rel_path, [], resolver, run.execution_id,
        )


_analyzer = WolframAnalyzer()


def is_wolfram_tree_sitter_available() -> bool:
    """Check if tree-sitter with Wolfram grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("wolfram")
def analyze_wolfram(repo_root: Path) -> AnalysisResult:
    """Analyze Wolfram files in a repository."""
    return _analyzer.analyze(repo_root)
