"""Haxe language analyzer using tree-sitter.

This module provides static analysis for Haxe source code, extracting symbols
(classes, interfaces, functions) and edges (calls, inheritance).

Haxe is a high-level, cross-platform programming language and compiler that
can compile to many target platforms including JavaScript, C++, C#, Java,
Python, Lua, PHP, Flash, and HashLink bytecode.

Implementation approach:
- Uses TreeSitterAnalyzer base class for two-pass orchestration
- Uses tree-sitter-language-pack for Haxe grammar
- Handles classes, interfaces, functions, and method calls

Key constructs extracted:
- class Name { ... } - class definitions
- interface Name { ... } - interface definitions
- function name(args): Type - function definitions
- name(args) - function calls
- obj.method(args) - method calls
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

PASS_ID = make_pass_id("haxe")


def find_haxe_files(root: Path) -> Iterator[Path]:
    """Find all Haxe files in the given directory."""
    for path in find_files(root, ["*.hx"]):
        if path.is_file():
            yield path


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8")


def _get_identifier(node: "tree_sitter.Node") -> Optional[str]:
    """Get the identifier child of a node."""
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_function_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the function name from a function_declaration node."""
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child)
        elif child.type == "new":
            return "new"
    return None  # pragma: no cover


def _get_function_params(node: "tree_sitter.Node") -> list[str]:
    """Get parameter names from a function_declaration node."""
    params = []
    for child in node.children:
        if child.type == "function_arg":
            for arg_child in child.children:
                if arg_child.type == "identifier":
                    params.append(_get_node_text(arg_child))
                    break
    return params


def _get_return_type(node: "tree_sitter.Node") -> Optional[str]:
    """Get the return type from a function_declaration node."""
    found_paren = False
    for child in node.children:
        if child.type == ")":
            found_paren = True
        elif found_paren and child.type == "type":
            for type_child in child.children:
                if type_child.type == "identifier":
                    return _get_node_text(type_child)
            return _get_node_text(child)  # pragma: no cover
    return None


def _is_public(node: "tree_sitter.Node") -> bool:
    """Check if a node has public visibility."""
    for child in node.children:
        if child.type == "public":
            return True
    return False


def _is_static(node: "tree_sitter.Node") -> bool:
    """Check if a function is static."""
    for child in node.children:
        if child.type == "static":
            return True
    return False


def _get_call_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the function name from a call_expression node."""
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child)
        elif child.type == "member_expression":
            for member_child in reversed(child.children):
                if member_child.type == "identifier":
                    return _get_node_text(member_child)
    return None  # pragma: no cover


# Haxe built-in functions that should not generate call edges
_BUILTINS = frozenset({
    "trace", "haxe", "Type",
    "Math", "abs", "floor", "ceil", "round", "sqrt",
    "sin", "cos", "tan", "min", "max", "pow", "log",
    "String", "charAt", "charCodeAt", "indexOf",
    "lastIndexOf", "split", "substr", "substring",
    "toLowerCase", "toUpperCase", "toString",
    "Array", "push", "pop", "shift", "unshift",
    "concat", "join", "slice", "splice", "sort",
    "reverse", "filter", "map", "length",
    "Std", "int", "parseFloat", "parseInt", "is",
    "string", "random",
    "Lambda", "array", "exists", "find",
})


def _find_enclosing_context(
    node: "tree_sitter.Node", path: Path, repo_root: Path,
) -> tuple[Optional[str], Optional[str]]:
    """Find the enclosing function and class for a node.

    Returns:
        Tuple of (function_id, class_name). function_id is the stable ID
        of the enclosing function, class_name is the name of the enclosing
        class (if any).
    """
    current = node.parent
    func_name = None
    func_node = None
    class_name = None

    while current is not None:
        if current.type == "function_declaration":
            if func_name is None:
                func_name = _get_function_name(current)
                func_node = current
        elif current.type in ("class_declaration", "interface_declaration"):
            if class_name is None:
                class_name = _get_identifier(current)
        current = current.parent

    if func_name and func_node is not None:
        qualified_name = f"{class_name}.{func_name}" if class_name else func_name
        rel_path = str(path.relative_to(repo_root))
        func_id = make_symbol_id("haxe", rel_path, func_node.start_point[0] + 1, func_node.end_point[0] + 1, qualified_name, "fn")
        return func_id, class_name
    return None, class_name  # pragma: no cover


class HaxeAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Haxe source files using TreeSitterAnalyzer base class."""

    lang = "haxe"
    file_patterns: ClassVar[list[str]] = ["*.hx"]
    language_pack_name = "haxe"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract class, interface, and function symbols from a Haxe file."""
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
        current_class: Optional[str],
    ) -> None:
        """Extract symbols from a syntax tree node recursively."""
        if node.type == "class_declaration":
            name = _get_identifier(node)
            if name:
                is_abstract = any(c.type == "abstract" for c in node.children)

                sym = Symbol(
                    id=make_symbol_id("haxe", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, name, "class"),
                    stable_id=self.compute_stable_id(node, kind="class"),
                    name=name,
                    kind="class",
                    language="haxe",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta={"is_abstract": is_abstract},
                )
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node

                for child in node.children:
                    self._extract_symbols_recursive(
                        child, path, repo_root, rel_path, run, analysis, name
                    )
                return

        elif node.type == "interface_declaration":
            name = _get_identifier(node)
            if name:
                sym = Symbol(
                    id=make_symbol_id("haxe", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, name, "interface"),
                    stable_id=self.compute_stable_id(node, kind="interface"),
                    name=name,
                    kind="interface",
                    language="haxe",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node

                for child in node.children:
                    self._extract_symbols_recursive(
                        child, path, repo_root, rel_path, run, analysis, name
                    )
                return

        elif node.type == "function_declaration":
            name = _get_function_name(node)
            if name:
                params = _get_function_params(node)
                return_type = _get_return_type(node)
                is_pub = _is_public(node)
                is_stat = _is_static(node)

                qualified_name = f"{current_class}.{name}" if current_class else name

                type_str = return_type if return_type else "Void"
                signature = f"function {name}({', '.join(params)}): {type_str}"

                sym = Symbol(
                    id=make_symbol_id("haxe", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, qualified_name, "fn"),
                    stable_id=self.compute_stable_id(node, kind="fn"),
                    name=qualified_name,
                    kind="function",
                    language="haxe",
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
                    meta={
                        "param_count": len(params),
                        "is_public": is_pub,
                        "is_static": is_stat,
                        "class": current_class,
                    },
                )
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node
                analysis.symbol_by_name[qualified_name] = sym
            return  # Don't recurse into function bodies for symbol extraction

        # Recursively process children
        for child in node.children:
            self._extract_symbols_recursive(
                child, path, repo_root, rel_path, run, analysis, current_class
            )

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call edges from a Haxe file."""
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
        if node.type == "call_expression":
            has_new = any(c.type == "new" for c in node.children)
            if not has_new:
                call_name = _get_call_name(node)
                if call_name and call_name not in _BUILTINS:
                    caller_id, enclosing_class = _find_enclosing_context(
                        node, path, repo_root,
                    )
                    if caller_id:
                        callee_id = None
                        if enclosing_class:
                            qualified_name = f"{enclosing_class}.{call_name}"
                            callee_sym = global_symbols.get(qualified_name)
                            callee_id = callee_sym.id if callee_sym else None

                        if callee_id is None:
                            callee_sym = global_symbols.get(call_name)
                            callee_id = callee_sym.id if callee_sym else None

                        confidence = 1.0 if callee_id else 0.6
                        if callee_id is None:
                            callee_id = f"haxe:unresolved:{call_name}"

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
                            evidence_lang="haxe",
                        )
                        edges.append(edge)

        # Recursively process children
        for child in node.children:
            self._extract_edges_recursive(
                child, path, repo_root, global_symbols, run, edges,
            )


_analyzer = HaxeAnalyzer()


def is_haxe_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with Haxe support is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("haxe")
def analyze_haxe(repo_root: Path) -> AnalysisResult:
    """Analyze Haxe source files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        AnalysisResult containing symbols, edges, and analysis metadata
    """
    return _analyzer.analyze(repo_root)
