"""Fennel language analyzer using tree-sitter.

This module provides static analysis for Fennel source code, extracting symbols
(functions, variables) and edges (calls).

Fennel is a lisp that compiles to Lua. It combines Lua's simplicity, speed, and
reach with a rich macro system and expressive syntax. It's commonly used for
game development and embedded scripting.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Collect all symbols (fn definitions, local variables)
2. Pass 2: Extract call edges from list expressions

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Fennel-specific extraction
logic.

Key constructs extracted:
- (fn name [args] body) - function definitions
- (local name value) - variable definitions
- (name args) - function calls (lists)
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
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("fennel")


def is_fennel_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with Fennel support is available."""
    return _analyzer._check_grammar_available()


def find_fennel_files(root: Path) -> Iterator[Path]:
    """Find all Fennel files in the given directory."""
    for path in find_files(root, ["*.fnl"]):
        if path.is_file():
            yield path


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8", errors="replace")


def _get_function_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the function name from an fn node."""
    for child in node.children:
        if child.type == "symbol":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_function_params(node: "tree_sitter.Node") -> list[str]:
    """Get parameters from an fn node."""
    params = []
    for child in node.children:
        if child.type == "parameters":
            for param_child in child.children:
                if param_child.type == "binding":
                    for binding_child in param_child.children:
                        if binding_child.type == "symbol":
                            params.append(_get_node_text(binding_child))
    return params


def _get_local_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the variable name from a local node."""
    for child in node.children:
        if child.type == "binding":
            for binding_child in child.children:
                if binding_child.type == "symbol":
                    return _get_node_text(binding_child)
    return None  # pragma: no cover


def _get_call_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the function name from a list call."""
    children = [c for c in node.children if c.type not in ("(", ")")]
    if children and children[0].type == "symbol":
        return _get_node_text(children[0])
    return None  # pragma: no cover


def _find_enclosing_function(
    node: "tree_sitter.Node", rel_path: str
) -> Optional[str]:
    """Find the enclosing function for a node."""
    current = node.parent
    while current is not None:
        if current.type == "fn":
            name = _get_function_name(current)
            if name:
                return make_symbol_id("fennel", rel_path, current.start_point[0]+1, current.end_point[0]+1, name, "fn")
        current = current.parent
    return None  # pragma: no cover


# Fennel special forms and builtins to skip during edge extraction
_SPECIAL_FORMS = {
    "fn", "lambda", "\u03bb", "local", "let", "if", "when", "unless",
    "do", "while", "for", "each", "collect", "icollect", "fcollect",
    "accumulate", "faccumulate", "match", "case", "case-try",
    "or", "and", "not", "set", "tset", "global", "var",
    "import-macros", "require-macros", "include", "eval-compiler",
    "macros", "macro", "quote", "hashfn", "#", "partial",
    "pick-values", "pick-args", "lua", "macrodebug", "assert-compile",
    "doto", "->", "->>", "-?>", "-?>>", "?.", "with-open",
}

_BUILTINS = {
    "+", "-", "*", "/", "%", "^", "..", "=", "<", ">", "<=", ">=",
    "~=", "#", "length", "type", "tonumber", "tostring",
    "print", "error", "assert", "pairs", "ipairs", "next",
    "select", "unpack", "table.insert", "table.remove",
    "table.concat", "table.sort", "table.pack", "table.unpack",
    "string.format", "string.sub", "string.len", "string.find",
    "string.match", "string.gsub", "string.rep", "string.byte",
    "string.char", "string.lower", "string.upper",
    "math.abs", "math.floor", "math.ceil", "math.sqrt",
    "math.sin", "math.cos", "math.min", "math.max",
    "pcall", "xpcall", "rawget", "rawset", "rawequal",
    "setmetatable", "getmetatable", "collectgarbage",
    "require", "loadfile", "dofile", "io.open", "io.read", "io.write",
}


# ---------------------------------------------------------------------------
# FennelAnalyzer: TreeSitterAnalyzer subclass
# ---------------------------------------------------------------------------


class FennelAnalyzer(TreeSitterAnalyzer):
    """Fennel language analyzer using tree-sitter-language-pack."""

    lang = "fennel"
    file_patterns: ClassVar[list[str]] = ["*.fnl"]
    language_pack_name = "fennel"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract function and variable symbols from a Fennel file."""
        analysis = FileAnalysis()
        self._extract_symbols_recursive(tree.root_node, rel_path, run.execution_id, analysis)
        return analysis

    def _extract_symbols_recursive(
        self, node: "tree_sitter.Node", rel_path: str, run_id: str,
        analysis: FileAnalysis,
    ) -> None:
        """Recursively extract symbols, stopping descent into fn/local bodies."""
        if node.type == "fn":
            name = _get_function_name(node)
            if name:
                params = _get_function_params(node)
                signature = f"(fn {name} [{' '.join(params)}] ...)"

                sym = Symbol(
                    id=make_symbol_id("fennel", rel_path, node.start_point[0]+1, node.end_point[0]+1, name, "fn"),
                    stable_id=self.compute_stable_id(node, kind="fn"),
                    name=name,
                    kind="function",
                    language="fennel",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    signature=signature,
                    meta={"param_count": len(params)},
                )
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node
                analysis.symbol_by_name[name] = sym
            return  # Don't process children of function definitions

        elif node.type == "local":
            name = _get_local_name(node)
            if name:
                sym = Symbol(
                    id=make_symbol_id("fennel", rel_path, node.start_point[0]+1, node.end_point[0]+1, name, "var"),
                    stable_id=self.compute_stable_id(node, kind="var"),
                    name=name,
                    kind="variable",
                    language="fennel",
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
            return  # Don't process children of variable definitions

        # Recursively process children
        for child in node.children:
            self._extract_symbols_recursive(child, rel_path, run_id, analysis)

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call edges from Fennel list expressions."""
        edges: list[Edge] = []
        self._extract_edges_recursive(
            tree.root_node, rel_path, run.execution_id, global_symbols, edges,
        )
        return edges

    def _extract_edges_recursive(
        self, node: "tree_sitter.Node", rel_path: str, run_id: str,
        global_symbols: dict, edges: list[Edge],
    ) -> None:
        """Recursively extract call edges from list expressions."""
        if node.type == "list":
            call_name = _get_call_name(node)
            if call_name:
                if call_name not in _SPECIAL_FORMS and call_name not in _BUILTINS:
                    caller_id = _find_enclosing_function(node, rel_path)
                    if caller_id:
                        callee_sym = global_symbols.get(call_name)
                        if isinstance(callee_sym, Symbol):
                            callee_id = callee_sym.id
                            confidence = 1.0
                        else:
                            callee_id = f"fennel:unresolved:{call_name}"
                            confidence = 0.6

                        line = node.start_point[0] + 1
                        edge = Edge.create(
                            src=caller_id,
                            dst=callee_id,
                            edge_type="calls",
                            line=line,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                            evidence_type="ast_call_direct",
                            confidence=confidence,
                            evidence_lang="fennel",
                        )
                        edges.append(edge)

        # Recursively process children
        for child in node.children:
            self._extract_edges_recursive(child, rel_path, run_id, global_symbols, edges)


_analyzer = FennelAnalyzer()


@register_analyzer("fennel")
def analyze_fennel(repo_root: Path) -> AnalysisResult:
    """Analyze Fennel source files in a repository."""
    return _analyzer.analyze(repo_root)
