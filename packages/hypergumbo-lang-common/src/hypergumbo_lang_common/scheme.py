"""Scheme language analyzer using tree-sitter.

This module provides static analysis for Scheme source code, extracting symbols
(functions, variables) and edges (calls).

Scheme is a minimalist Lisp dialect that emphasizes lexical scoping and first-class
continuations. It's widely used in computer science education and research.

Implementation approach:
- Uses tree-sitter-language-pack for Scheme grammar
- Two-pass analysis: First pass collects all symbols, second pass extracts edges
- Handles Scheme-specific constructs like define, lambda, let

Key constructs extracted:
- (define (name args) body) - function definitions
- (define name value) - variable definitions
- (name args) - function calls
"""

import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "scheme.tree_sitter"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class SchemeAnalysisResult:
    """Result of analyzing Scheme files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: Optional[AnalysisRun] = None
    skipped: bool = False
    skip_reason: str = ""


def is_scheme_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with Scheme support is available."""
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("scheme")
        return True
    except (ImportError, Exception):  # pragma: no cover
        return False  # pragma: no cover


def find_scheme_files(root: Path) -> Iterator[Path]:
    """Find all Scheme files in the given directory."""
    for ext in ("*.scm", "*.ss", "*.sld", "*.sls"):
        for path in root.rglob(ext):
            if path.is_file():
                yield path


def _make_stable_id(path: Path, repo_root: Path, name: str, kind: str) -> str:
    """Create a stable ID for a Scheme symbol."""
    rel_path = path.relative_to(repo_root)
    return f"scheme:{rel_path}:{name}:{kind}"


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8")


def _is_define_form(node: "tree_sitter.Node") -> bool:
    """Check if a list node is a define form."""
    if node.type != "list":
        return False
    children = [c for c in node.children if c.type not in ("(", ")")]
    if len(children) < 2:
        return False
    return children[0].type == "symbol" and _get_node_text(children[0]) == "define"


def _is_function_define(node: "tree_sitter.Node") -> bool:
    """Check if a define form defines a function (define (name args) body)."""
    children = [c for c in node.children if c.type not in ("(", ")")]
    if len(children) < 2:
        return False  # pragma: no cover
    # Second element should be a list (the function signature)
    return children[1].type == "list"


def _get_function_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the function name from a function define form."""
    children = [c for c in node.children if c.type not in ("(", ")")]
    if len(children) < 2:
        return None  # pragma: no cover
    sig_list = children[1]
    sig_children = [c for c in sig_list.children if c.type not in ("(", ")")]
    if sig_children and sig_children[0].type == "symbol":
        return _get_node_text(sig_children[0])
    return None  # pragma: no cover


def _get_function_params(node: "tree_sitter.Node") -> list[str]:
    """Get parameters from a function define form."""
    params = []
    children = [c for c in node.children if c.type not in ("(", ")")]
    if len(children) < 2:
        return params  # pragma: no cover
    sig_list = children[1]
    sig_children = [c for c in sig_list.children if c.type not in ("(", ")")]
    # Skip the first symbol (function name)
    for child in sig_children[1:]:
        if child.type == "symbol":
            params.append(_get_node_text(child))
    return params


def _get_variable_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the variable name from a variable define form."""
    children = [c for c in node.children if c.type not in ("(", ")")]
    if len(children) < 2:
        return None  # pragma: no cover
    if children[1].type == "symbol":
        return _get_node_text(children[1])
    return None  # pragma: no cover


def _get_call_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the function name from a call expression."""
    children = [c for c in node.children if c.type not in ("(", ")")]
    if children and children[0].type == "symbol":
        return _get_node_text(children[0])
    return None  # pragma: no cover


class SchemeAnalyzer:
    """Analyzer for Scheme source files."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.symbols: list[Symbol] = []
        self.edges: list[Edge] = []
        self._symbol_registry: dict[str, str] = {}  # name -> id
        self._run_id: str = ""

    def analyze(self) -> SchemeAnalysisResult:
        """Analyze all Scheme files in the repository."""
        if not is_scheme_tree_sitter_available():
            warnings.warn(
                "Scheme analysis skipped: tree-sitter-language-pack not available",
                UserWarning,
                stacklevel=2,
            )
            return SchemeAnalysisResult(
                skipped=True,
                skip_reason="tree-sitter-language-pack not available",
            )

        import uuid as uuid_module
        from tree_sitter_language_pack import get_parser

        start_time = time.time()
        self._run_id = f"uuid:{uuid_module.uuid4()}"

        parser = get_parser("scheme")
        scheme_files = list(find_scheme_files(self.repo_root))

        if not scheme_files:
            return SchemeAnalysisResult()

        # Pass 1: Collect all symbols
        for path in scheme_files:
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
        for path in scheme_files:
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
            toolchain={"name": "scheme", "version": "unknown"},
            duration_ms=int(elapsed * 1000),
        )

        return SchemeAnalysisResult(
            symbols=self.symbols,
            edges=self.edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if _is_define_form(node):
            if _is_function_define(node):
                name = _get_function_name(node)
                if name:
                    params = _get_function_params(node)
                    signature = f"(define ({name} {' '.join(params)}) ...)"
                    rel_path = str(path.relative_to(self.repo_root))

                    sym = Symbol(
                        id=_make_stable_id(path, self.repo_root, name, "fn"),
                        stable_id=_make_stable_id(path, self.repo_root, name, "fn"),
                        name=name,
                        kind="function",
                        language="scheme",
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
            else:
                name = _get_variable_name(node)
                if name:
                    rel_path = str(path.relative_to(self.repo_root))

                    sym = Symbol(
                        id=_make_stable_id(path, self.repo_root, name, "var"),
                        stable_id=_make_stable_id(path, self.repo_root, name, "var"),
                        name=name,
                        kind="variable",
                        language="scheme",
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
            return  # Don't recursively process define children

        # Recursively process children
        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_edges(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract edges from a syntax tree node."""
        # Skip processing inside define forms' signature lists
        if _is_define_form(node):
            # For function defines, only process the body (skip the signature)
            if _is_function_define(node):
                children = [c for c in node.children if c.type not in ("(", ")")]
                # Skip define symbol (0), skip signature list (1), process body (2+)
                for child in children[2:]:
                    self._extract_edges(child, path)
            return  # Don't process other parts of define forms

        # Look for function calls (list expressions that aren't special forms)
        if node.type == "list":
            call_name = _get_call_name(node)
            if call_name:
                # Skip special forms and built-ins
                special_forms = {
                    "define", "lambda", "let", "let*", "letrec", "if", "cond",
                    "case", "begin", "set!", "quote", "quasiquote", "unquote",
                    "unquote-splicing", "and", "or", "do", "delay", "define-syntax",
                    "let-syntax", "letrec-syntax", "syntax-rules", "syntax-case",
                    "import", "export", "library", "define-library",
                }
                builtins = {
                    "+", "-", "*", "/", "=", "<", ">", "<=", ">=",
                    "eq?", "eqv?", "equal?", "not", "null?", "pair?",
                    "list?", "number?", "string?", "symbol?", "procedure?",
                    "car", "cdr", "cons", "list", "append", "reverse",
                    "length", "map", "filter", "fold", "for-each",
                    "apply", "eval", "read", "write", "display", "newline",
                    "error", "string-append", "string-length", "string-ref",
                    "number->string", "string->number", "symbol->string",
                    "string->symbol", "vector", "vector-ref", "vector-set!",
                    "make-vector", "vector-length", "call/cc",
                    "call-with-current-continuation", "values", "call-with-values",
                }

                if call_name not in special_forms and call_name not in builtins:
                    caller_id = self._find_enclosing_function(node, path)
                    if caller_id:
                        callee_id = self._symbol_registry.get(call_name)
                        confidence = 1.0 if callee_id else 0.6
                        if callee_id is None:
                            callee_id = f"scheme:unresolved:{call_name}"

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
                            evidence_lang="scheme",
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
            if _is_define_form(current) and _is_function_define(current):
                name = _get_function_name(current)
                if name:
                    return _make_stable_id(path, self.repo_root, name, "fn")
            current = current.parent
        return None  # pragma: no cover


def analyze_scheme(repo_root: Path) -> SchemeAnalysisResult:
    """Analyze Scheme source files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        SchemeAnalysisResult containing symbols, edges, and analysis metadata
    """
    analyzer = SchemeAnalyzer(repo_root)
    return analyzer.analyze()
