# SPDX-License-Identifier: AGPL-3.0-or-later
"""GDScript (Godot) analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse GDScript files and extract:
- Function definitions (_ready, _process, custom methods)
- Variable declarations (class members)
- Signal declarations
- Class names and inner classes
- Function calls
- Preload/load imports

If tree-sitter with GDScript support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract functions, variables, signals, and classes
2. Pass 2: Extract preload/load import edges and function call edges

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the GDScript-specific
extraction logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates ~150 lines of boilerplate
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for GDScript grammar
- GDScript is essential for Godot game development
- Enables analysis of game code for refactoring and understanding

GDScript-Specific Considerations
--------------------------------
- Scripts typically extend a base class (Node2D, CharacterBody2D)
- class_name declares a global script name
- Signals are event-like declarations
- Functions starting with _ are lifecycle callbacks
- preload() is compile-time import, load() is runtime
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    make_unresolved_edge,
    node_text,
)
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("gdscript")


def find_gdscript_files(repo_root: Path) -> Iterator[Path]:
    """Yield all GDScript files in the repository."""
    yield from find_files(repo_root, ["*.gd"])


def is_gdscript_tree_sitter_available() -> bool:
    """Check if tree-sitter with GDScript grammar is available."""
    return _analyzer._check_grammar_available()


def _extract_function_signature(func_node: "tree_sitter.Node", source: bytes) -> str:
    """Extract function signature showing parameters and return type.

    GDScript function syntax:
        func name(param1: Type1, param2: Type2) -> ReturnType:

    Returns signature like "(amount: int, source: Node) -> int".
    """
    params_parts: list[str] = []
    return_type: Optional[str] = None

    params_node = find_child_by_type(func_node, "parameters")
    if params_node:
        for child in params_node.children:
            if child.type == "typed_parameter":
                ident = find_child_by_type(child, "identifier")
                type_node = find_child_by_type(child, "type")
                if ident:
                    param_str = node_text(ident, source).strip()
                    if type_node:
                        type_str = node_text(type_node, source).strip()
                        param_str = f"{param_str}: {type_str}"
                    params_parts.append(param_str)
            elif child.type == "identifier":
                params_parts.append(node_text(child, source).strip())

    found_arrow = False
    for child in func_node.children:
        if child.type == "->":
            found_arrow = True
        elif found_arrow and child.type == "type":
            return_type = node_text(child, source).strip()
            break

    sig = f"({', '.join(params_parts)})"
    if return_type:
        sig += f" -> {return_type}"
    return sig


def _get_enclosing_function_gdscript(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up parent chain to find enclosing function Symbol."""
    current = node.parent
    while current is not None:
        if current.type == "function_definition":
            name_node = find_child_by_type(current, "name")
            if name_node:
                func_name = node_text(name_node, source).strip()
                return local_symbols.get(func_name)
        current = current.parent
    return None  # pragma: no cover - no enclosing function found


def _make_gd_symbol(
    node: "tree_sitter.Node",
    name: str,
    kind: str,
    file_path: str,
    run_id: str,
    signature: Optional[str] = None,
) -> Symbol:
    """Create a Symbol from a tree-sitter node."""
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    start_col = node.start_point[1]
    end_col = node.end_point[1]

    span = Span(
        start_line=start_line,
        end_line=end_line,
        start_col=start_col,
        end_col=end_col,
    )
    sym_id = make_symbol_id("gdscript", file_path, start_line, end_line, name, kind)
    return Symbol(
        id=sym_id,
        name=name,
        canonical_name=name,
        kind=kind,
        language="gdscript",
        path=file_path,
        span=span,
        origin=PASS_ID,
        origin_run_id=run_id,
        signature=signature,
    )


# ---------------------------------------------------------------------------
# GDScriptAnalyzer: TreeSitterAnalyzer subclass
# ---------------------------------------------------------------------------


class GDScriptAnalyzer(TreeSitterAnalyzer):
    """GDScript language analyzer using tree-sitter-language-pack."""

    lang = "gdscript"
    file_patterns: ClassVar[list[str]] = ["*.gd"]
    language_pack_name = "gdscript"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract function, variable, signal, and class symbols from GDScript."""
        analysis = FileAnalysis()

        for node in iter_tree(tree.root_node):
            if node.type == "function_definition":
                name_node = find_child_by_type(node, "name")
                if name_node:
                    func_name = node_text(name_node, source).strip()
                    sig = _extract_function_signature(node, source)
                    sym = _make_gd_symbol(node, func_name, "function", rel_path, run.execution_id, signature=sig)
                    analysis.symbols.append(sym)
                    analysis.symbol_by_name[func_name] = sym

            elif node.type == "variable_statement":
                name_node = find_child_by_type(node, "name")
                if name_node:
                    var_name = node_text(name_node, source).strip()
                    analysis.symbols.append(_make_gd_symbol(node, var_name, "variable", rel_path, run.execution_id))

            elif node.type == "signal_statement":
                name_node = find_child_by_type(node, "name")
                if name_node:
                    signal_name = node_text(name_node, source).strip()
                    analysis.symbols.append(_make_gd_symbol(node, signal_name, "signal", rel_path, run.execution_id))

            elif node.type == "class_name_statement":
                name_node = find_child_by_type(node, "name")
                if name_node:
                    class_name = node_text(name_node, source).strip()
                    analysis.symbols.append(_make_gd_symbol(node, class_name, "class", rel_path, run.execution_id))

            elif node.type == "class_definition":
                name_node = find_child_by_type(node, "name")
                if name_node:
                    class_name = node_text(name_node, source).strip()
                    analysis.symbols.append(_make_gd_symbol(node, class_name, "class", rel_path, run.execution_id))

        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract import and call edges from GDScript."""
        edges: list[Edge] = []

        for node in iter_tree(tree.root_node):
            if node.type == "call":
                ident_node = find_child_by_type(node, "identifier")
                if ident_node:
                    called_name = node_text(ident_node, source).strip()

                    if called_name in ("preload", "load"):
                        args_node = find_child_by_type(node, "arguments")
                        if args_node:
                            for arg_child in args_node.children:
                                if arg_child.type == "string":
                                    path_str = node_text(arg_child, source).strip().strip('"\'')
                                    edges.append(Edge.create(
                                        src=make_file_id("gdscript", rel_path),
                                        dst=f"gdscript:{path_str}:file",
                                        edge_type="imports",
                                        line=node.start_point[0] + 1,
                                        confidence=0.9,
                                        origin=PASS_ID,
                                    ))
                                    break
                    else:
                        caller = _get_enclosing_function_gdscript(node, source, local_symbols)
                        if caller:
                            if called_name not in ("print", "push_error", "push_warning", "printerr"):
                                result = resolver.lookup(called_name)
                                if result.symbol is not None:
                                    edges.append(Edge.create(
                                        src=caller.id,
                                        dst=result.symbol.id,
                                        edge_type="calls",
                                        line=node.start_point[0] + 1,
                                        confidence=0.85 * result.confidence,
                                        origin=PASS_ID,
                                    ))
                                else:
                                    edges.append(make_unresolved_edge(
                                        "gdscript", caller.id, called_name,
                                        node.start_point[0] + 1,
                                        PASS_ID, run.execution_id,
                                    ))

        return edges


_analyzer = GDScriptAnalyzer()


@register_analyzer("gdscript")
def analyze_gdscript(repo_root: Path) -> AnalysisResult:
    """Analyze all GDScript files in the repository."""
    return _analyzer.analyze(repo_root)
