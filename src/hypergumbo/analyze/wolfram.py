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
1. Check if tree-sitter-wolfram is available (built from source)
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect function calls and imports
4. Track package structure and dependencies

Why This Design
---------------
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

PASS_ID = "wolfram-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_wolfram_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Wolfram files in the repository."""
    yield from find_files(repo_root, ["*.wl", "*.m", "*.wls", "*.nb"])


def is_wolfram_tree_sitter_available() -> bool:
    """Check if tree-sitter with Wolfram grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_wolfram") is None:
        return False  # pragma: no cover - tree-sitter-wolfram not installed
    return True


@dataclass
class WolframAnalysisResult:
    """Result of analyzing Wolfram files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file.

    Stored during pass 1 and processed in pass 2 for cross-file resolution.
    """

    path: str
    source: bytes
    tree: object  # tree_sitter.Tree
    symbols: list[Symbol]


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"wolfram:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Wolfram file node (used as edge source)."""
    return f"wolfram:{path}:1-1:file:file"


def _make_module_id(module_name: str) -> str:
    """Generate ID for a Wolfram module (used as import edge target)."""
    return f"wolfram:{module_name}:0-0:module:module"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(
    node: "tree_sitter.Node", type_name: str
) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None  # pragma: no cover - defensive


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
            param_text = _node_text(child, source).strip()
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
        sym_id = _make_symbol_id(file_path, start_line, end_line, name, kind)
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
                        func_name_node = _find_child_by_type(left_node, "symbol")
                        if func_name_node:
                            func_name = _node_text(func_name_node, source).strip()
                            if func_name:
                                # Extract signature from the call pattern
                                signature = _extract_wolfram_signature(left_node, source)
                                add_symbol(node, func_name, "function", signature=signature)
                    elif left_node.type == "symbol":  # pragma: no cover - simple pattern
                        # Could be a simple pattern like f := ...
                        sym_name = _node_text(left_node, source).strip()  # pragma: no cover
                        if sym_name:  # pragma: no cover
                            add_symbol(node, sym_name, "function")  # pragma: no cover
                elif op_node.type == "=":
                    # Set - variable assignment
                    if left_node.type == "symbol":
                        var_name = _node_text(left_node, source).strip()
                        if var_name:
                            add_symbol(node, var_name, "variable")
                    elif left_node.type == "call":  # pragma: no cover - immediate def
                        # Could be like f[x_] = ... (immediate definition)
                        func_name_node = _find_child_by_type(left_node, "symbol")  # pragma: no cover
                        if func_name_node:  # pragma: no cover
                            func_name = _node_text(func_name_node, source).strip()  # pragma: no cover
                            if func_name:  # pragma: no cover
                                add_symbol(node, func_name, "function")  # pragma: no cover

    return symbols


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    global_symbol_registry: dict[str, Symbol],
    run_id: str,
) -> list[Edge]:
    """Extract import and call edges from a parsed Wolfram file.

    Detects:
    - imports: Get["package`"], Needs["package`"], Import["file"]
    - calls: function calls like Sin[x], Map[f, list]

    Uses iterative traversal to avoid RecursionError on deeply nested code.
    """
    edges: list[Edge] = []
    file_id = _make_file_id(file_path)
    seen_calls: set[str] = set()

    for node in iter_tree(tree.root_node):
        # Look for function calls
        if node.type == "call":
            # First child should be the function name
            func_name_node = _find_child_by_type(node, "symbol")
            if func_name_node:
                func_name = _node_text(func_name_node, source).strip()
                if func_name:
                    # Check for import functions
                    if func_name in ("Get", "Needs", "Import"):
                        # Find the string argument
                        string_node = _find_child_by_type(node, "string")
                        if string_node:
                            string_text = _node_text(string_node, source).strip()
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
                            if func_name in global_symbol_registry:
                                target = global_symbol_registry[func_name]
                                target_id = target.id
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


def analyze_wolfram(repo_root: Path) -> WolframAnalysisResult:
    """Analyze Wolfram files in a repository.

    Returns a WolframAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-wolfram is not available, returns a skipped result.
    """
    start_time = time.time()

    # Create analysis run for provenance
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    if not is_wolfram_tree_sitter_available():  # pragma: no cover - tree-sitter-wolfram not installed
        skip_reason = (
            "Wolfram analysis skipped: requires tree-sitter-wolfram "
            "(build from source: https://github.com/bostick/tree-sitter-wolfram)"
        )
        warnings.warn(skip_reason)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return WolframAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=skip_reason,
        )

    import tree_sitter
    import tree_sitter_wolfram

    WOLFRAM_LANGUAGE = tree_sitter.Language(tree_sitter_wolfram.language())
    parser = tree_sitter.Parser(WOLFRAM_LANGUAGE)
    run_id = run.execution_id

    # Pass 1: Parse all files and extract symbols
    file_analyses: list[FileAnalysis] = []
    all_symbols: list[Symbol] = []
    global_symbol_registry: dict[str, Symbol] = {}
    files_analyzed = 0

    for wolfram_file in find_wolfram_files(repo_root):
        try:
            source = wolfram_file.read_bytes()
        except OSError:  # pragma: no cover
            continue

        tree = parser.parse(source)
        if tree.root_node is None:  # pragma: no cover - parser always returns root
            continue

        rel_path = str(wolfram_file.relative_to(repo_root))

        # Create file symbol
        file_symbol = Symbol(
            id=_make_file_id(rel_path),
            name="file",
            kind="file",
            language="wolfram",
            path=rel_path,
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin=PASS_ID,
            origin_run_id=run_id,
        )
        all_symbols.append(file_symbol)

        # Extract symbols
        file_symbols = _extract_symbols_from_file(tree, source, rel_path, run_id)
        all_symbols.extend(file_symbols)

        # Register symbols globally (for cross-file resolution)
        for sym in file_symbols:
            global_symbol_registry[sym.name] = sym

        file_analyses.append(FileAnalysis(
            path=rel_path,
            source=source,
            tree=tree,
            symbols=file_symbols,
        ))
        files_analyzed += 1

    # Pass 2: Extract edges with cross-file resolution
    all_edges: list[Edge] = []

    for fa in file_analyses:
        edges = _extract_edges_from_file(
            fa.tree,  # type: ignore
            fa.source,
            fa.path,
            fa.symbols,
            global_symbol_registry,
            run_id,
        )
        all_edges.extend(edges)

    run.files_analyzed = files_analyzed
    run.duration_ms = int((time.time() - start_time) * 1000)

    return WolframAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
