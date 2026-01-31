"""Janet language analyzer using tree-sitter.

This module provides static analysis for Janet source code, extracting symbols
(functions, variables) and edges (calls).

Janet is a functional and imperative programming language with Lisp-like syntax,
designed for embedding and scripting. It features easy interop with C, a simple
build system, and first-class functions.

Implementation approach:
- Uses tree-sitter-language-pack for Janet grammar
- Two-pass analysis: First pass collects all symbols, second pass extracts edges
- Handles Janet-specific constructs like defn, def, and tuple calls

Key constructs extracted:
- (defn name [args] body) - function definitions
- (def name value) - variable definitions
- (name args) - function calls (tuples)
"""

import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "janet.tree_sitter"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class JanetAnalysisResult:
    """Result of analyzing Janet files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: Optional[AnalysisRun] = None
    skipped: bool = False
    skip_reason: str = ""


def is_janet_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with Janet support is available."""
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("janet")
        return True
    except (ImportError, Exception):  # pragma: no cover
        return False  # pragma: no cover


def find_janet_files(root: Path) -> Iterator[Path]:
    """Find all Janet files in the given directory."""
    for path in root.rglob("*.janet"):
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


class JanetAnalyzer:
    """Analyzer for Janet source files."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.symbols: list[Symbol] = []
        self.edges: list[Edge] = []
        self._symbol_registry: dict[str, str] = {}  # name -> id
        self._run_id: str = ""

    def analyze(self) -> JanetAnalysisResult:
        """Analyze all Janet files in the repository."""
        if not is_janet_tree_sitter_available():
            warnings.warn(
                "Janet analysis skipped: tree-sitter-language-pack not available",
                UserWarning,
                stacklevel=2,
            )
            return JanetAnalysisResult(
                skipped=True,
                skip_reason="tree-sitter-language-pack not available",
            )

        import uuid as uuid_module
        from tree_sitter_language_pack import get_parser

        start_time = time.time()
        self._run_id = f"uuid:{uuid_module.uuid4()}"

        parser = get_parser("janet")
        janet_files = list(find_janet_files(self.repo_root))

        if not janet_files:
            return JanetAnalysisResult()

        # Pass 1: Collect all symbols
        for path in janet_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._extract_symbols(tree.root_node, path)
            except Exception:  # pragma: no cover
                pass

        # Build symbol registry
        for sym in self.symbols:
            self._symbol_registry[sym.name] = sym.id

        # Pass 2: Extract edges
        for path in janet_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._extract_edges(tree.root_node, path)
            except Exception:  # pragma: no cover
                pass

        elapsed = time.time() - start_time

        run = AnalysisRun(
            execution_id=self._run_id,
            run_signature="",
            pass_id=PASS_ID,
            version=PASS_VERSION,
            toolchain={"name": "janet", "version": "unknown"},
            duration_ms=int(elapsed * 1000),
        )

        return JanetAnalysisResult(
            symbols=self.symbols,
            edges=self.edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "extra_defs":
            # Function definition: (defn name [args] body)
            name = _get_function_name(node)
            if name:
                params = _get_function_params(node)
                signature = f"(defn {name} [{' '.join(params)}] ...)"
                rel_path = str(path.relative_to(self.repo_root))

                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "fn"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "fn"),
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
                    signature=signature,
                    meta={"param_count": len(params)},
                )
                self.symbols.append(sym)
            return  # Don't process children of function definitions

        elif node.type == "def":
            # Variable definition: (def name value)
            name = _get_variable_name(node)
            if name:
                rel_path = str(path.relative_to(self.repo_root))

                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "var"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "var"),
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
                )
                self.symbols.append(sym)
            return  # Don't process children of variable definitions

        # Recursively process children
        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_edges(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract edges from a syntax tree node."""
        # Look for function calls (tuple expressions)
        if node.type == "tuple":
            call_name = _get_call_name(node)
            if call_name:
                # Skip special forms and built-ins
                special_forms = {
                    "defn", "def", "var", "let", "if", "do", "fn", "quote",
                    "quasiquote", "unquote", "splice", "while", "break",
                    "set", "upscope", "try", "propagate", "cond", "case",
                    "match", "label", "return", "defer", "for", "loop",
                    "each", "eachk", "eachp", "comptime", "compwhen",
                    "import", "use", "require", "short-fn", "seq", "generate",
                }
                builtins = {
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
                }

                if call_name not in special_forms and call_name not in builtins:
                    caller_id = self._find_enclosing_function(node, path)
                    if caller_id:
                        callee_id = self._symbol_registry.get(call_name)
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
                            origin_run_id=self._run_id,
                            evidence_type="ast_call_direct",
                            confidence=confidence,
                            evidence_lang="janet",
                        )
                        self.edges.append(edge)

        # Recursively process children
        for child in node.children:
            self._extract_edges(child, path)

    def _find_enclosing_function(
        self, node: "tree_sitter.Node", path: Path
    ) -> Optional[str]:
        """Find the enclosing function for a node."""
        current = node.parent
        while current is not None:
            if current.type == "extra_defs":
                name = _get_function_name(current)
                if name:
                    return _make_stable_id(path, self.repo_root, name, "fn")
            current = current.parent
        return None  # pragma: no cover


def analyze_janet(repo_root: Path) -> JanetAnalysisResult:
    """Analyze Janet source files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        JanetAnalysisResult containing symbols, edges, and analysis metadata
    """
    analyzer = JanetAnalyzer(repo_root)
    return analyzer.analyze()
