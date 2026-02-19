"""Scheme language analyzer using tree-sitter.

This module provides static analysis for Scheme source code, extracting symbols
(functions, variables) and edges (calls).

Scheme is a minimalist Lisp dialect that emphasizes lexical scoping and first-class
continuations. It's widely used in computer science education and research.

Implementation approach:
- Uses TreeSitterAnalyzer base class for grammar checking and parser creation
- Two-pass analysis: First pass collects all symbols, second pass extracts edges
- Handles Scheme-specific constructs like define, lambda, let

Key constructs extracted:
- (define (name args) body) - function definitions
- (define name value) - variable definitions
- (name args) - function calls
"""

from pathlib import Path
from typing import ClassVar, Iterator, Optional, TYPE_CHECKING

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    TreeSitterAnalyzer,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("scheme")


def find_scheme_files(root: Path) -> Iterator[Path]:
    """Find all Scheme files in the given directory."""
    for ext in ("*.scm", "*.ss", "*.sld", "*.sls"):
        for path in find_files(root, [ext]):
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


def _extract_scheme_symbols(
    node: "tree_sitter.Node",
    path: Path,
    repo_root: Path,
    symbols: list[Symbol],
) -> None:
    """Extract symbols from a syntax tree node (recursive)."""
    if _is_define_form(node):
        if _is_function_define(node):
            name = _get_function_name(node)
            if name:
                params = _get_function_params(node)
                signature = f"(define ({name} {' '.join(params)}) ...)"
                rel_path = str(path.relative_to(repo_root))

                sym = Symbol(
                    id=_make_stable_id(path, repo_root, name, "fn"),
                    stable_id=_make_stable_id(path, repo_root, name, "fn"),
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
                symbols.append(sym)
        else:
            name = _get_variable_name(node)
            if name:
                rel_path = str(path.relative_to(repo_root))

                sym = Symbol(
                    id=_make_stable_id(path, repo_root, name, "var"),
                    stable_id=_make_stable_id(path, repo_root, name, "var"),
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
                symbols.append(sym)
        return  # Don't recursively process define children

    # Recursively process children
    for child in node.children:
        _extract_scheme_symbols(child, path, repo_root, symbols)


# Scheme special forms and builtins that should not generate call edges
_SPECIAL_FORMS = frozenset({
    "define", "lambda", "let", "let*", "letrec", "if", "cond",
    "case", "begin", "set!", "quote", "quasiquote", "unquote",
    "unquote-splicing", "and", "or", "do", "delay", "define-syntax",
    "let-syntax", "letrec-syntax", "syntax-rules", "syntax-case",
    "import", "export", "library", "define-library",
})

_BUILTINS = frozenset({
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
})


def _find_enclosing_function(
    node: "tree_sitter.Node", path: Path, repo_root: Path
) -> Optional[str]:
    """Find the enclosing function for a node."""
    current = node.parent
    while current is not None:
        if _is_define_form(current) and _is_function_define(current):
            name = _get_function_name(current)
            if name:
                return _make_stable_id(path, repo_root, name, "fn")
        current = current.parent
    return None  # pragma: no cover


def _extract_scheme_edges(
    node: "tree_sitter.Node",
    path: Path,
    repo_root: Path,
    symbol_registry: dict[str, str],
    run_id: str,
    edges: list[Edge],
) -> None:
    """Extract edges from a syntax tree node (recursive)."""
    # Skip processing inside define forms' signature lists
    if _is_define_form(node):
        # For function defines, only process the body (skip the signature)
        if _is_function_define(node):
            children = [c for c in node.children if c.type not in ("(", ")")]
            # Skip define symbol (0), skip signature list (1), process body (2+)
            for child in children[2:]:
                _extract_scheme_edges(child, path, repo_root, symbol_registry, run_id, edges)
        return  # Don't process other parts of define forms

    # Look for function calls (list expressions that aren't special forms)
    if node.type == "list":
        call_name = _get_call_name(node)
        if call_name:
            if call_name not in _SPECIAL_FORMS and call_name not in _BUILTINS:
                caller_id = _find_enclosing_function(node, path, repo_root)
                if caller_id:
                    callee_id = symbol_registry.get(call_name)
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
                        origin_run_id=run_id,
                        evidence_type="ast_call_direct",
                        confidence=confidence,
                        evidence_lang="scheme",
                    )
                    edges.append(edge)

    # Recursively process children
    for child in node.children:
        _extract_scheme_edges(child, path, repo_root, symbol_registry, run_id, edges)


class SchemeAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Scheme source files using TreeSitterAnalyzer base class."""

    lang = "scheme"
    file_patterns: ClassVar[list[str]] = ["*.scm", "*.ss", "*.sld", "*.sls"]
    language_pack_name = "scheme"

    def analyze(self, repo_root: Path, max_files: Optional[int] = None) -> AnalysisResult:
        """Override analyze for Scheme's recursive tree walking."""
        import time as _time
        import warnings

        start_time = _time.time()
        run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

        if not self._check_grammar_available():
            warnings.warn(
                f"{self.lang} analysis skipped: grammar not available. "
                f"Install the required tree-sitter grammar package.",
                UserWarning,
                stacklevel=2,
            )
            run.duration_ms = int((_time.time() - start_time) * 1000)
            return AnalysisResult(
                run=run,
                skipped=True,
                skip_reason=f"{self.lang} tree-sitter grammar not available",
            )

        scheme_files = list(find_scheme_files(repo_root))
        if not scheme_files:
            return AnalysisResult()

        parser = self._create_parser()
        symbols: list[Symbol] = []
        edges: list[Edge] = []
        symbol_registry: dict[str, str] = {}  # name -> id

        # Pass 1: Collect all symbols
        for path in scheme_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                _extract_scheme_symbols(tree.root_node, path, repo_root, symbols)
            except Exception:  # pragma: no cover
                pass

        # Build symbol registry
        for sym in symbols:
            symbol_registry[sym.name] = sym.id

        # Pass 2: Extract edges
        for path in scheme_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                _extract_scheme_edges(
                    tree.root_node, path, repo_root,
                    symbol_registry, run.execution_id, edges,
                )
            except Exception:  # pragma: no cover
                pass

        run.duration_ms = int((_time.time() - start_time) * 1000)

        return AnalysisResult(
            symbols=symbols,
            edges=edges,
            run=run,
        )


_analyzer = SchemeAnalyzer()


def is_scheme_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with Scheme support is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("scheme")
def analyze_scheme(repo_root: Path) -> AnalysisResult:
    """Analyze Scheme source files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        AnalysisResult containing symbols, edges, and analysis metadata
    """
    return _analyzer.analyze(repo_root)
