# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pascal language analyzer using tree-sitter.

This module provides static analysis for Pascal source code, extracting symbols
(procedures, functions, programs, units) and edges (calls).

Pascal is a classic imperative and procedural programming language designed for
teaching structured programming. It's still widely used through Delphi, Free Pascal,
and Lazarus IDE. Modern Object Pascal supports object-oriented programming.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Collect all symbols (programs, units, procedures, functions)
2. Pass 2: Extract call edges from exprCall and identifier-statement patterns

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Pascal-specific extraction
logic.

Key constructs extracted:
- program ... - main program definition
- unit ... - module/library definition
- function name(args): type - function definitions
- procedure name(args) - procedure definitions
- name(args) - procedure/function calls
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    make_symbol_id,
    make_unresolved_edge,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("pascal")


def is_pascal_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with Pascal support is available."""
    return _analyzer._check_grammar_available()


def find_pascal_files(root: Path) -> Iterator[Path]:
    """Find all Pascal files in the given directory."""
    extensions = ("*.pas", "*.pp", "*.dpr", "*.lpr")
    for ext in extensions:
        for path in find_files(root, [ext]):
            if path.is_file():
                yield path


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8", errors="replace")


def _get_identifier(node: "tree_sitter.Node") -> Optional[str]:
    """Get the identifier child of a node."""
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_proc_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the name of a procedure/function from a defProc node."""
    for child in node.children:
        if child.type == "declProc":
            return _get_identifier(child)
    return None  # pragma: no cover


def _get_proc_kind(node: "tree_sitter.Node") -> str:
    """Get whether this is a function or procedure."""
    for child in node.children:
        if child.type == "declProc":
            for subchild in child.children:
                if subchild.type == "kFunction":
                    return "function"
                elif subchild.type == "kProcedure":
                    return "procedure"
    return "procedure"  # default  # pragma: no cover


def _get_proc_params(node: "tree_sitter.Node") -> list[str]:
    """Get parameter names from a defProc node."""
    params = []
    for child in node.children:
        if child.type == "declProc":
            for subchild in child.children:
                if subchild.type == "declArgs":
                    for arg in subchild.children:
                        if arg.type == "declArg":
                            for arg_child in arg.children:
                                if arg_child.type == "identifier":
                                    params.append(_get_node_text(arg_child))
    return params


def _get_return_type(node: "tree_sitter.Node") -> Optional[str]:
    """Get the return type of a function from a defProc node."""
    for child in node.children:
        if child.type == "declProc":
            found_colon = False
            for subchild in child.children:
                if subchild.type == ":":
                    found_colon = True
                elif found_colon and subchild.type == "typeref":
                    return _get_identifier(subchild)
    return None


def _get_call_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the function/procedure name from an exprCall node."""
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child)
    return None  # pragma: no cover


def _find_enclosing_function(
    node: "tree_sitter.Node", rel_path: str,
) -> Optional[str]:
    """Find the enclosing function/procedure for a node.

    Returns the symbol ID (make_symbol_id format) of the nearest defProc
    ancestor, or None if the node is at module level.
    """
    current = node.parent
    while current is not None:
        if current.type == "defProc":
            name = _get_proc_name(current)
            if name:
                return make_symbol_id(
                    "pascal", rel_path,
                    current.start_point[0] + 1,
                    current.end_point[0] + 1,
                    name, "function",
                )
        current = current.parent
    return None  # pragma: no cover


# Pascal builtins to skip during edge extraction
_PASCAL_BUILTINS = {
    # I/O
    "write", "writeln", "read", "readln", "readkey",
    # Memory
    "new", "dispose", "getmem", "freemem", "setlength",
    # String
    "length", "copy", "delete", "insert", "pos", "concat",
    "uppercase", "lowercase", "trim", "stringreplace",
    # Math
    "inc", "dec", "abs", "sqr", "sqrt", "sin", "cos", "tan",
    "exp", "ln", "log", "power", "round", "trunc", "frac",
    "random", "randomize",
    # Conversion
    "ord", "chr", "inttostr", "strtoint", "floattostr",
    "strtofloat", "formatfloat", "format",
    # System
    "halt", "exit", "break", "continue", "sleep",
    "assigned", "sizeof", "typeof", "high", "low",
    # File
    "assign", "reset", "rewrite", "append", "close",
    "eof", "eoln", "fileexists",
    # Array
    "fillchar", "move",
}


# ---------------------------------------------------------------------------
# PascalAnalyzer: TreeSitterAnalyzer subclass
# ---------------------------------------------------------------------------


class PascalAnalyzer(TreeSitterAnalyzer):
    """Pascal language analyzer using tree-sitter-language-pack."""

    lang = "pascal"
    file_patterns: ClassVar[list[str]] = ["*.pas", "*.pp", "*.dpr", "*.lpr"]
    language_pack_name = "pascal"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract program, unit, function, and procedure symbols from Pascal."""
        analysis = FileAnalysis()
        self._extract_symbols_recursive(tree.root_node, rel_path, analysis)
        return analysis

    def _extract_symbols_recursive(
        self, node: "tree_sitter.Node", rel_path: str, analysis: FileAnalysis,
    ) -> None:
        """Recursively extract symbols, stopping descent into defProc bodies."""
        if node.type == "program":
            name = _get_identifier(node)
            if name is None:
                for child in node.children:
                    if child.type == "moduleName":
                        name = _get_node_text(child)
                        break
            if name:
                sym_id = make_symbol_id(
                    "pascal", rel_path,
                    node.start_point[0] + 1, node.end_point[0] + 1,
                    name, "program",
                )
                sym = Symbol(
                    id=sym_id,
                    stable_id=self.compute_stable_id(node, kind="program"),
                    name=name,
                    kind="program",
                    language="pascal",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node

        elif node.type == "unit":
            name = None
            for child in node.children:
                if child.type == "moduleName":
                    name = _get_identifier(child)
                    if name is None:  # pragma: no cover
                        name = _get_node_text(child)
                    break
            if name:
                sym_id = make_symbol_id(
                    "pascal", rel_path,
                    node.start_point[0] + 1, node.end_point[0] + 1,
                    name, "module",
                )
                sym = Symbol(
                    id=sym_id,
                    stable_id=self.compute_stable_id(node, kind="module"),
                    name=name,
                    kind="module",
                    language="pascal",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node

        elif node.type == "defProc":
            name = _get_proc_name(node)
            if name:
                kind = _get_proc_kind(node)
                params = _get_proc_params(node)
                return_type = _get_return_type(node)

                if kind == "function":
                    signature = f"function {name}({', '.join(params)}): {return_type or 'unknown'}"
                else:
                    signature = f"procedure {name}({', '.join(params)})"

                sym_id = make_symbol_id(
                    "pascal", rel_path,
                    node.start_point[0] + 1, node.end_point[0] + 1,
                    name, "function",
                )
                sym = Symbol(
                    id=sym_id,
                    stable_id=self.compute_stable_id(node, kind="function"),
                    name=name,
                    kind="function",
                    language="pascal",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    signature=signature,
                    meta={"param_count": len(params), "proc_kind": kind},
                )
                analysis.symbols.append(sym)
                analysis.symbol_by_name[name] = sym
                analysis.node_for_symbol[sym.id] = node
            return  # Don't recurse into function bodies for symbol extraction

        # Recursively process children
        for child in node.children:
            self._extract_symbols_recursive(child, rel_path, analysis)

    def register_symbol(self, symbol: Symbol, global_symbols: dict) -> None:
        """Register symbols with lowercase names for case-insensitive matching."""
        global_symbols[symbol.name.lower()] = symbol

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call edges from Pascal exprCall and statement nodes."""
        edges: list[Edge] = []
        self._extract_edges_recursive(
            tree.root_node, rel_path, run.execution_id, global_symbols, edges,
        )
        return edges

    def _extract_edges_recursive(
        self, node: "tree_sitter.Node", rel_path: str, run_id: str,
        global_symbols: dict, edges: list[Edge],
    ) -> None:
        """Recursively extract call edges."""
        call_name = None
        call_node = node

        if node.type == "exprCall":
            call_name = _get_call_name(node)
        elif node.type == "statement":
            children = [c for c in node.children if c.type not in (";",)]
            if len(children) == 1 and children[0].type == "identifier":
                call_name = _get_node_text(children[0])
                call_node = children[0]

        if call_name:
            if call_name.lower() not in _PASCAL_BUILTINS:
                caller_id = _find_enclosing_function(node, rel_path)
                if caller_id:
                    callee_sym = global_symbols.get(call_name.lower())
                    if isinstance(callee_sym, Symbol):
                        edge = Edge.create(
                            src=caller_id,
                            dst=callee_sym.id,
                            edge_type="calls",
                            line=call_node.start_point[0] + 1,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                            evidence_type="ast_call_direct",
                            confidence=1.0,
                            evidence_lang="pascal",
                        )
                    else:
                        edge = make_unresolved_edge(
                            "pascal", caller_id, call_name,
                            call_node.start_point[0] + 1,
                            PASS_ID, run_id,
                        )
                    edges.append(edge)

        # Recursively process children
        for child in node.children:
            self._extract_edges_recursive(child, rel_path, run_id, global_symbols, edges)


_analyzer = PascalAnalyzer()


@register_analyzer("pascal")
def analyze_pascal(repo_root: Path) -> AnalysisResult:
    """Analyze Pascal source files in a repository."""
    return _analyzer.analyze(repo_root)
