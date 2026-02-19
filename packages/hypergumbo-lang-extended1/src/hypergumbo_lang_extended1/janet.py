"""Janet language analyzer using tree-sitter.

This module provides static analysis for Janet source code, extracting symbols
(functions, variables) and edges (calls).

Janet is a functional and imperative programming language with Lisp-like syntax,
designed for embedding and scripting. It features easy interop with C, a simple
build system, and first-class functions.

Implementation approach:
- Uses TreeSitterAnalyzer base class for two-pass orchestration
- Uses tree-sitter-language-pack for Janet grammar
- Handles Janet-specific constructs like defn, def, and tuple calls

Key constructs extracted:
- (defn name [args] body) - function definitions
- (def name value) - variable definitions
- (name args) - function calls (tuples)
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
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("janet")


def find_janet_files(root: Path) -> Iterator[Path]:
    """Find all Janet files in the given directory."""
    for path in find_files(root, ["*.janet"]):
        if path.is_file():
            yield path


def _make_stable_id(path: Path, repo_root: Path, name: str, kind: str) -> str:
    """Create a stable ID for a Janet symbol."""
    rel_path = path.relative_to(repo_root)
    return f"janet:{rel_path}:{name}:{kind}"


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8")


def _get_function_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the function name from an extra_defs node."""
    for child in node.children:
        if child.type == "symbol":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_function_params(node: "tree_sitter.Node") -> list[str]:
    """Get parameters from an extra_defs node."""
    params = []
    for child in node.children:
        if child.type == "parameters":
            for param_child in child.children:
                if param_child.type == "symbol":
                    params.append(_get_node_text(param_child))
    return params


def _get_variable_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the variable name from a def node."""
    for child in node.children:
        if child.type == "symbol":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_call_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the function name from a tuple call."""
    children = [c for c in node.children if c.type not in ("(", ")")]
    if children and children[0].type == "symbol":
        return _get_node_text(children[0])
    return None  # pragma: no cover


# Janet special forms and builtins that should not generate call edges
_SPECIAL_FORMS = frozenset({
    "defn", "def", "var", "let", "if", "do", "fn", "quote",
    "quasiquote", "unquote", "splice", "while", "break",
    "set", "upscope", "try", "propagate", "cond", "case",
    "match", "label", "return", "defer", "for", "loop",
    "each", "eachk", "eachp", "comptime", "compwhen",
    "import", "use", "require", "short-fn", "seq", "generate",
})

_BUILTINS = frozenset({
    "+", "-", "*", "/", "%", "=", "<", ">", "<=", ">=",
    "not", "and", "or", "nil?", "true?", "false?",
    "number?", "string?", "symbol?", "keyword?", "function?",
    "array?", "tuple?", "table?", "struct?", "buffer?",
    "first", "last", "get", "put", "in", "length", "empty?",
    "keys", "values", "pairs", "map", "filter", "reduce",
    "apply", "partial", "identity", "comp", "juxt",
    "print", "pp", "printf", "string", "keyword", "symbol",
    "int", "float", "array", "tuple", "table", "struct",
    "error", "assert", "type", "describe", "doc",
})


def _find_enclosing_function(
    node: "tree_sitter.Node", path: Path, repo_root: Path
) -> Optional[str]:
    """Find the enclosing function for a node."""
    current = node.parent
    while current is not None:
        if current.type == "extra_defs":
            name = _get_function_name(current)
            if name:
                return _make_stable_id(path, repo_root, name, "fn")
        current = current.parent
    return None  # pragma: no cover


class JanetAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Janet source files using TreeSitterAnalyzer base class."""

    lang = "janet"
    file_patterns: ClassVar[list[str]] = ["*.janet"]
    language_pack_name = "janet"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract function and variable symbols from a Janet file."""
        analysis = FileAnalysis()
        repo_root = file_path.parent
        # Compute repo_root from file_path and rel_path
        rel_parts = Path(rel_path).parts
        repo_root = file_path
        for _ in rel_parts:
            repo_root = repo_root.parent

        self._extract_symbols_recursive(
            tree.root_node, file_path, repo_root, rel_path, run, analysis
        )
        return analysis

    def _extract_symbols_recursive(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        rel_path: str, run: "AnalysisRun", analysis: FileAnalysis,
    ) -> None:
        """Extract symbols from a syntax tree node recursively."""
        if node.type == "extra_defs":
            name = _get_function_name(node)
            if name:
                params = _get_function_params(node)
                signature = f"(defn {name} [{' '.join(params)}] ...)"

                sym = Symbol(
                    id=_make_stable_id(path, repo_root, name, "fn"),
                    stable_id=_make_stable_id(path, repo_root, name, "fn"),
                    name=name,
                    kind="function",
                    language="janet",
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
                    meta={"param_count": len(params)},
                )
                analysis.symbols.append(sym)
                analysis.symbol_by_name[name] = sym
            return  # Don't process children of function definitions

        elif node.type == "def":
            name = _get_variable_name(node)
            if name:
                sym = Symbol(
                    id=_make_stable_id(path, repo_root, name, "var"),
                    stable_id=_make_stable_id(path, repo_root, name, "var"),
                    name=name,
                    kind="variable",
                    language="janet",
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
            return  # Don't process children of variable definitions

        # Recursively process children
        for child in node.children:
            self._extract_symbols_recursive(
                child, path, repo_root, rel_path, run, analysis
            )

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call edges from a Janet file."""
        edges: list[Edge] = []
        # Compute repo_root from file_path and rel_path
        rel_parts = Path(rel_path).parts
        repo_root = file_path
        for _ in rel_parts:
            repo_root = repo_root.parent

        self._extract_edges_recursive(
            tree.root_node, file_path, repo_root, global_symbols,
            run, edges,
        )
        return edges

    def _extract_edges_recursive(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        global_symbols: dict, run: "AnalysisRun", edges: list[Edge],
    ) -> None:
        """Extract edges from a syntax tree node recursively."""
        if node.type == "tuple":
            call_name = _get_call_name(node)
            if call_name:
                if call_name not in _SPECIAL_FORMS and call_name not in _BUILTINS:
                    caller_id = _find_enclosing_function(node, path, repo_root)
                    if caller_id:
                        callee_sym = global_symbols.get(call_name)
                        callee_id = callee_sym.id if callee_sym else None
                        confidence = 1.0 if callee_id else 0.6
                        if callee_id is None:
                            callee_id = f"janet:unresolved:{call_name}"

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
                            evidence_lang="janet",
                        )
                        edges.append(edge)

        # Recursively process children
        for child in node.children:
            self._extract_edges_recursive(
                child, path, repo_root, global_symbols, run, edges,
            )


_analyzer = JanetAnalyzer()


def is_janet_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with Janet support is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("janet")
def analyze_janet(repo_root: Path) -> AnalysisResult:
    """Analyze Janet source files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        AnalysisResult containing symbols, edges, and analysis metadata
    """
    return _analyzer.analyze(repo_root)
