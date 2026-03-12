# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tcl language analyzer using tree-sitter.

This module provides static analysis for Tcl source code, extracting symbols
(procedures, namespaces, variables) and edges (calls).

Tcl (Tool Command Language) is a dynamic scripting language commonly used for
rapid prototyping, scripted applications, GUIs, and testing. It's particularly
prevalent in EDA (Electronic Design Automation) tools.

Implementation approach:
- Uses TreeSitterAnalyzer base class for two-pass orchestration
- Uses tree-sitter-language-pack for Tcl grammar
- Handles Tcl-specific constructs like proc, namespace eval, and command substitution

Key constructs extracted:
- procedure: proc name {args} {body}
- namespace: namespace eval name {body}
- command_substitution: [command args] (function calls)
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

PASS_ID = make_pass_id("tcl")


def find_tcl_files(root: Path) -> Iterator[Path]:
    """Find all Tcl files in the given directory."""
    for ext in ("*.tcl", "*.tk"):
        for path in find_files(root, [ext]):
            if path.is_file():
                yield path


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8", errors="replace")


def _get_proc_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the procedure name from a procedure node."""
    for child in node.children:
        if child.type == "simple_word":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_proc_params(node: "tree_sitter.Node") -> list[str]:
    """Get parameter names from a procedure's arguments."""
    params = []
    for child in node.children:
        if child.type == "arguments":
            for arg_child in child.children:
                if arg_child.type == "argument":
                    for inner in arg_child.children:
                        if inner.type == "simple_word":
                            params.append(_get_node_text(inner))
    return params


def _get_namespace_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the namespace name from a namespace node."""
    for child in node.children:
        if child.type == "word_list":
            found_eval = False
            for word_child in child.children:
                if word_child.type == "simple_word":
                    text = _get_node_text(word_child)
                    if text == "eval":
                        found_eval = True
                    elif found_eval:
                        return text
    return None  # pragma: no cover


def _count_namespace_procs(node: "tree_sitter.Node") -> int:
    """Count procedures in a namespace."""
    count = 0
    for child in node.children:
        if child.type == "word_list":
            for word_child in child.children:
                if word_child.type == "braced_word":
                    for inner in word_child.children:
                        if inner.type == "procedure":
                            count += 1
    return count


def _get_command_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the command name from a command or command_substitution node."""
    if node.type == "command_substitution":
        for child in node.children:
            if child.type == "command":
                return _get_command_name(child)
    elif node.type == "command":
        for child in node.children:
            if child.type == "simple_word":
                return _get_node_text(child)
    return None  # pragma: no cover


# Tcl built-in commands that should not generate call edges
_BUILTINS = frozenset({
    "puts", "set", "expr", "return", "if", "else", "while",
    "for", "foreach", "switch", "catch", "try", "proc",
    "namespace", "package", "source", "uplevel", "upvar",
    "global", "variable", "incr", "append", "lappend",
    "list", "lindex", "llength", "lsort", "lsearch",
    "string", "regexp", "regsub", "split", "join", "format",
    "scan", "open", "close", "read", "gets", "eof", "flush",
    "seek", "tell", "file", "glob", "cd", "pwd", "exec",
    "eval", "after", "update", "vwait", "info", "array",
    "dict", "clock", "time", "pid", "error", "break",
    "continue", "rename", "unset", "trace", "interp",
})


def _find_enclosing_proc(
    node: "tree_sitter.Node", path: Path, repo_root: Path,
) -> Optional[str]:
    """Find the enclosing procedure for a node."""
    current = node.parent
    namespace_name = None
    proc_name = None
    proc_node = None

    while current is not None:
        if current.type == "namespace":
            if namespace_name is None:
                namespace_name = _get_namespace_name(current)
        if current.type == "procedure":
            if proc_name is None:
                proc_name = _get_proc_name(current)
                proc_node = current
        current = current.parent

    if proc_name and proc_node is not None:
        qualified_name = f"{namespace_name}::{proc_name}" if namespace_name else proc_name
        rel_path = str(path.relative_to(repo_root))
        return make_symbol_id("tcl", rel_path, proc_node.start_point[0] + 1, proc_node.end_point[0] + 1, qualified_name, "proc")
    return None  # pragma: no cover


class TclAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Tcl source files using TreeSitterAnalyzer base class."""

    lang = "tcl"
    file_patterns: ClassVar[list[str]] = ["*.tcl", "*.tk"]
    language_pack_name = "tcl"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract procedure and namespace symbols from a Tcl file."""
        analysis = FileAnalysis()
        rel_parts = Path(rel_path).parts
        repo_root = file_path
        for _ in rel_parts:
            repo_root = repo_root.parent

        self._extract_symbols_recursive(
            tree.root_node, file_path, repo_root, rel_path, run, analysis, None
        )
        return analysis

    def _extract_symbols_recursive(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        rel_path: str, run: "AnalysisRun", analysis: FileAnalysis,
        namespace: Optional[str],
    ) -> None:
        """Extract symbols from a syntax tree node recursively."""
        if node.type == "procedure":
            name = _get_proc_name(node)
            if name:
                params = _get_proc_params(node)
                signature = f"proc {name} {{{', '.join(params)}}}"

                qualified_name = f"{namespace}::{name}" if namespace else name

                sym = Symbol(
                    id=make_symbol_id("tcl", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, qualified_name, "proc"),
                    stable_id=self.compute_stable_id(node, kind="proc"),
                    name=name,
                    kind="function",
                    language="tcl",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    signature=signature,
                    meta={"namespace": namespace} if namespace else None,
                )
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node
                analysis.symbol_by_name[name] = sym

        elif node.type == "namespace":
            name = _get_namespace_name(node)
            if name:
                proc_count = _count_namespace_procs(node)

                sym = Symbol(
                    id=make_symbol_id("tcl", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, name, "namespace"),
                    stable_id=self.compute_stable_id(node, kind="namespace"),
                    name=name,
                    kind="namespace",
                    language="tcl",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta={"proc_count": proc_count},
                )
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node

                # Extract procedures within the namespace
                for child in node.children:
                    if child.type == "word_list":
                        for word_child in child.children:
                            if word_child.type == "braced_word":
                                for inner in word_child.children:
                                    self._extract_symbols_recursive(
                                        inner, path, repo_root, rel_path,
                                        run, analysis, namespace=name,
                                    )
                return  # Don't recursively process namespace children again

        # Recursively process children
        for child in node.children:
            self._extract_symbols_recursive(
                child, path, repo_root, rel_path, run, analysis, namespace
            )

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call edges from a Tcl file."""
        edges: list[Edge] = []
        rel_parts = Path(rel_path).parts
        repo_root = file_path
        for _ in rel_parts:
            repo_root = repo_root.parent

        self._extract_edges_recursive(
            tree.root_node, file_path, repo_root, global_symbols, run, edges,
        )
        return edges

    def _extract_edges_recursive(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        global_symbols: dict, run: "AnalysisRun", edges: list[Edge],
    ) -> None:
        """Extract edges from a syntax tree node recursively."""
        if node.type in ("command_substitution", "command"):
            caller_id = _find_enclosing_proc(node, path, repo_root)
            if caller_id:
                callee_name = _get_command_name(node)

                if callee_name and callee_name not in _BUILTINS:
                    callee_sym = global_symbols.get(callee_name)
                    callee_id = callee_sym.id if callee_sym else None
                    confidence = 1.0 if callee_id else 0.6
                    if callee_id is None:
                        callee_id = f"tcl:unresolved:{callee_name}"

                    line = node.start_point[0] + 1
                    edge = Edge.create(
                        src=caller_id,
                        dst=callee_id,
                        edge_type="calls",
                        line=line,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        evidence_type="ast_call_direct",
                        confidence=confidence,
                        evidence_lang="tcl",
                    )
                    edges.append(edge)

        # Recursively process children
        for child in node.children:
            self._extract_edges_recursive(
                child, path, repo_root, global_symbols, run, edges,
            )


_analyzer = TclAnalyzer()


def is_tcl_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with Tcl support is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("tcl")
def analyze_tcl(repo_root: Path) -> AnalysisResult:
    """Analyze Tcl source files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        AnalysisResult containing symbols, edges, and analysis metadata
    """
    return _analyzer.analyze(repo_root)
