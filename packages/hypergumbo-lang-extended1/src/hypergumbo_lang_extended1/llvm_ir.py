# SPDX-License-Identifier: AGPL-3.0-or-later
"""LLVM IR analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse LLVM IR files (.ll) and extract:
- Function definitions (define)
- Function declarations (declare)
- Global variable definitions
- Function call relationships

If tree-sitter with LLVM IR support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
- Pass 1: Parse all files, extract all symbols into global registry
- Pass 2: Detect calls and resolve against global symbol registry

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the LLVM IR-specific
extraction logic.

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-llvm for grammar (direct grammar module)
- Two-pass allows cross-file call resolution (for multi-file IR projects)
- Same pattern as other tree-sitter analyzers for consistency

LLVM IR-Specific Considerations
-------------------------------
- LLVM IR is the intermediate representation used by LLVM-based compilers
- Functions are defined with `define` and declared with `declare`
- Global variables are defined with `@name = global/constant`
- Local variables use `%` prefix, global identifiers use `@` prefix
- Functions can have attributes like `dso_local`, `noinline`, etc.
- Call instructions reference functions by their `@name`
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    iter_tree,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun

PASS_ID = make_pass_id("llvm_ir")


def find_llvm_ir_files(repo_root: Path) -> Iterator[Path]:
    """Yield all LLVM IR files in the repository."""
    yield from find_files(repo_root, ["*.ll"])


def _get_function_name(header_node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract function name from function_header node.

    The function name is stored in a global_var node (e.g., @add).
    """
    for child in iter_tree(header_node):
        if child.type == "global_var":
            name = node_text(child, source)
            # Remove @ prefix for internal use
            return name.lstrip("@")
    return None  # pragma: no cover - defensive fallback


def _get_global_var_name(global_node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract global variable name from global_global node."""
    for child in global_node.children:
        if child.type == "global_var":
            name = node_text(child, source)
            return name.lstrip("@")
    return None  # pragma: no cover - defensive fallback


def _get_call_target(call_node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract the called function name from an instruction_call node.

    The target is typically in value > var > global_var.
    """
    for child in iter_tree(call_node):
        if child.type == "global_var":
            name = node_text(child, source)
            return name.lstrip("@")
    return None  # pragma: no cover - defensive fallback


def _find_enclosing_function(
    node: "tree_sitter.Node", source: bytes
) -> str | None:
    """Find the name of the function containing this node."""
    current = node.parent
    while current is not None:
        if current.type == "fn_define":
            # Find function_header child
            for child in current.children:
                if child.type == "function_header":
                    return _get_function_name(child, source)
        current = current.parent
    return None  # pragma: no cover - defensive fallback


def _extract_arguments(header_node: "tree_sitter.Node", source: bytes) -> str:
    """Extract function signature from function_header."""
    for child in iter_tree(header_node):
        if child.type == "argument_list":
            return node_text(child, source)
    return "()"  # pragma: no cover - defensive fallback


def _get_return_type(header_node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract return type from function_header."""
    for child in header_node.children:
        if child.type == "type":
            return node_text(child, source)
    return None  # pragma: no cover - defensive fallback


def _extract_symbols_from_tree(
    tree: "tree_sitter.Tree", source: bytes, rel_path: str,
    run_id: str, analysis: FileAnalysis,
) -> list[tuple[str, str, int, int]]:
    """Extract symbols and call sites from a single LLVM IR file.

    Returns a list of call tuples: (caller_name, callee_name, line, col).
    """
    calls: list[tuple[str, str, int, int]] = []

    for node in iter_tree(tree.root_node):
        if node.type == "fn_define":
            # Function definition
            header = None
            for child in node.children:
                if child.type == "function_header":
                    header = child
                    break

            if header is not None:
                name = _get_function_name(header, source)
                if name:
                    ret_type = _get_return_type(header, source)
                    args = _extract_arguments(header, source)
                    signature = f"{ret_type or 'void'} @{name}{args}"
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    sym_id = make_symbol_id("llvm_ir", rel_path, start_line, end_line, name, "function")

                    symbol = Symbol(
                        id=sym_id,
                        name=name,
                        kind="function",
                        language="llvm_ir",
                        path=rel_path,
                        span=Span(
                            start_line=start_line,
                            start_col=node.start_point[1],
                            end_line=end_line,
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        canonical_name=f"@{name}",
                        signature=signature,
                    )
                    analysis.symbols.append(symbol)
                    analysis.symbol_by_name[name] = symbol

        elif node.type == "declare":
            # Function declaration (external function)
            header = None
            for child in node.children:
                if child.type == "function_header":
                    header = child
                    break

            if header is not None:
                name = _get_function_name(header, source)
                if name:
                    ret_type = _get_return_type(header, source)
                    args = _extract_arguments(header, source)
                    signature = f"{ret_type or 'void'} @{name}{args}"
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    sym_id = make_symbol_id("llvm_ir", rel_path, start_line, end_line, name, "declaration")

                    symbol = Symbol(
                        id=sym_id,
                        name=name,
                        kind="declaration",
                        language="llvm_ir",
                        path=rel_path,
                        span=Span(
                            start_line=start_line,
                            start_col=node.start_point[1],
                            end_line=end_line,
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        canonical_name=f"@{name}",
                        signature=signature,
                    )
                    analysis.symbols.append(symbol)
                    analysis.symbol_by_name[name] = symbol

        elif node.type == "global_global":
            # Global variable definition
            name = _get_global_var_name(node, source)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                sym_id = make_symbol_id("llvm_ir", rel_path, start_line, end_line, name, "variable")

                symbol = Symbol(
                    id=sym_id,
                    name=name,
                    kind="variable",
                    language="llvm_ir",
                    path=rel_path,
                    span=Span(
                        start_line=start_line,
                        start_col=node.start_point[1],
                        end_line=end_line,
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    canonical_name=f"@{name}",
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[name] = symbol

        elif node.type == "instruction_call":
            # Function call
            caller = _find_enclosing_function(node, source)
            callee = _get_call_target(node, source)
            if caller and callee:
                calls.append((
                    caller,
                    callee,
                    node.start_point[0] + 1,
                    node.start_point[1],
                ))

    return calls


def _resolve_calls(
    all_calls: list[tuple[str, str, int, int]],
    resolver: NameResolver,
    run_id: str,
) -> list[Edge]:
    """Resolve call sites to known symbols (pass 2).

    Creates edges for calls where both caller and callee are in the registry.
    Uses NameResolver for flexible symbol lookup with confidence tracking.
    """
    edges: list[Edge] = []
    base_confidence = 0.90

    for caller_name, callee_name, line, _col in all_calls:
        caller_result = resolver.lookup(caller_name)
        callee_result = resolver.lookup(callee_name)

        if caller_result.found and callee_result.found:
            caller_sym = caller_result.symbol
            callee_sym = callee_result.symbol
            assert caller_sym is not None
            assert callee_sym is not None
            # Combine base confidence with resolver confidence
            confidence = base_confidence * min(
                caller_result.confidence, callee_result.confidence
            )
            edge = Edge.create(
                src=caller_sym.id,
                dst=callee_sym.id,
                edge_type="calls",
                line=line,
                confidence=confidence,
                origin=PASS_ID,
                origin_run_id=run_id,
                evidence_type="ast_call_direct",
            )
            edges.append(edge)

    return edges


# ---------------------------------------------------------------------------
# LlvmIrAnalyzer: TreeSitterAnalyzer subclass
# ---------------------------------------------------------------------------


class LlvmIrAnalyzer(TreeSitterAnalyzer):
    """Analyzer for LLVM IR files using TreeSitterAnalyzer base class."""

    lang = "llvm_ir"
    file_patterns: ClassVar[list[str]] = ["*.ll"]
    grammar_module = "tree_sitter_llvm"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract symbols from a single LLVM IR file.

        Also stores call tuples in import_aliases under a special key
        for later retrieval in the edge pass.
        """
        analysis = FileAnalysis()
        calls = _extract_symbols_from_tree(
            tree, source, rel_path, run.execution_id, analysis,
        )
        # Store calls for pass 2 via import_aliases (a dict[str, str] hack:
        # we serialize the calls list as a string keyed by "__calls__")
        # Instead, we store them in the analysis object's symbol_by_name under
        # a special key. The base class passes symbol_by_name to extract_edges.
        # We'll use a different approach: store calls in a module-level dict.
        _pending_calls[id(tree)] = calls
        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call edges from a single LLVM IR file.

        Uses NameResolver for cross-file symbol lookup.
        """
        # Re-extract calls from this file's tree (since base class re-parses)
        calls = _extract_symbols_from_tree(
            tree, source, rel_path, run.execution_id,
            FileAnalysis(),  # dummy - we only need the calls
        )
        return _resolve_calls(calls, resolver, run.execution_id)


# Module-level storage for call tuples between passes
_pending_calls: dict[int, list[tuple[str, str, int, int]]] = {}

_analyzer = LlvmIrAnalyzer()


def is_llvm_tree_sitter_available() -> bool:
    """Check if tree-sitter with LLVM IR grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("llvm_ir")
def analyze_llvm_ir(repo_root: Path) -> AnalysisResult:
    """Analyze LLVM IR files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing symbols, edges, and analysis metadata
    """
    return _analyzer.analyze(repo_root)
