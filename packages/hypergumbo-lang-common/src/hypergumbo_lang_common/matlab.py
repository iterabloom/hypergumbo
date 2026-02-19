"""MATLAB language analyzer using tree-sitter.

This module provides static analysis for MATLAB source code, extracting symbols
(functions, classes, properties, methods) and edges (calls).

MATLAB is a high-level language and interactive environment for numerical
computation, visualization, and programming, commonly used in engineering
and scientific research.

Uses TreeSitterAnalyzer base class for grammar checking and parser creation.
The analyze() method is fully overridden because MATLAB requires:
- Custom .m file disambiguation (Objective-C vs Wolfram vs MATLAB)
- Stateful symbol registry for cross-file call resolution
- Recursive AST traversal with class context tracking

Key constructs extracted:
- function_definition: function output = name(args)
- class_definition: classdef Name
- properties: Property declarations within a class
- methods: Method definitions within a class
- function_call: Direct function calls
"""

import time
import warnings
from pathlib import Path
from typing import ClassVar, Iterator, Optional, TYPE_CHECKING

from hypergumbo_core.discovery import classify_dot_m_file, find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.analyze.base import AnalysisResult, TreeSitterAnalyzer
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "matlab.tree_sitter"
PASS_VERSION = "hypergumbo-0.1.0"


def find_matlab_files(root: Path) -> Iterator[Path]:
    """Find MATLAB files, excluding .m files that belong to Objective-C or Wolfram.

    Uses content-based heuristics to disambiguate .m files shared by three
    languages. Only yields files classified as MATLAB.
    """
    for path in find_files(root, ["*.m"]):
        if path.is_file() and classify_dot_m_file(path) == "matlab":
            yield path


def _make_stable_id(path: Path, repo_root: Path, name: str, kind: str) -> str:
    """Create a stable ID for a MATLAB symbol."""
    rel_path = path.relative_to(repo_root)
    return f"matlab:{rel_path}:{name}:{kind}"


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8")


def _get_identifier(node: "tree_sitter.Node") -> Optional[str]:
    """Get the identifier name from a node's children."""
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child)
    return None  # pragma: no cover


def _extract_function_params(node: "tree_sitter.Node") -> list[str]:
    """Extract parameter names from a function definition."""
    params = []
    for child in node.children:
        if child.type == "function_arguments":
            for arg_child in child.children:
                if arg_child.type == "identifier":
                    params.append(_get_node_text(arg_child))
    return params


def _extract_function_output(node: "tree_sitter.Node") -> Optional[str]:
    """Extract output variable from a function definition."""
    for child in node.children:
        if child.type == "function_output":
            for out_child in child.children:
                if out_child.type == "identifier":
                    return _get_node_text(out_child)
    return None  # pragma: no cover


def _count_properties(node: "tree_sitter.Node") -> int:
    """Count properties in a class definition."""
    count = 0
    for child in node.children:
        if child.type == "properties":
            for prop_child in child.children:
                if prop_child.type == "property":
                    count += 1
    return count


def _count_methods(node: "tree_sitter.Node") -> int:
    """Count methods in a class definition."""
    count = 0
    for child in node.children:
        if child.type == "methods":
            for method_child in child.children:
                if method_child.type == "function_definition":
                    count += 1
    return count


def _extract_symbols_recursive(
    node: "tree_sitter.Node",
    path: Path,
    repo_root: Path,
    symbols: list[Symbol],
    run_id: str,
    class_name: Optional[str] = None,
) -> None:
    """Extract symbols from a syntax tree node recursively."""
    if node.type == "function_definition":
        name = _get_identifier(node)
        if name:
            params = _extract_function_params(node)
            output = _extract_function_output(node)

            signature = f"function({', '.join(params)})"
            if output:
                signature = f"{output} = {signature}"

            rel_path = str(path.relative_to(repo_root))

            # Determine if this is a method (inside a class) or standalone function
            kind = "method" if class_name else "function"
            qualified_name = f"{class_name}.{name}" if class_name else name

            sym = Symbol(
                id=_make_stable_id(path, repo_root, qualified_name, "fn"),
                stable_id=_make_stable_id(path, repo_root, qualified_name, "fn"),
                name=name,
                kind=kind,
                language="matlab",
                path=rel_path,
                span=Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
                signature=signature,
                meta={"class": class_name} if class_name else None,
            )
            symbols.append(sym)

    elif node.type == "class_definition":
        name = _get_identifier(node)
        if name:
            property_count = _count_properties(node)
            method_count = _count_methods(node)
            rel_path = str(path.relative_to(repo_root))
            sym = Symbol(
                id=_make_stable_id(path, repo_root, name, "class"),
                stable_id=_make_stable_id(path, repo_root, name, "class"),
                name=name,
                kind="class",
                language="matlab",
                path=rel_path,
                span=Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
                meta={"property_count": property_count, "method_count": method_count},
            )
            symbols.append(sym)

            # Extract methods within the class
            for child in node.children:
                if child.type == "methods":
                    for method_child in child.children:
                        if method_child.type == "function_definition":
                            _extract_symbols_recursive(
                                method_child, path, repo_root, symbols, run_id, class_name=name
                            )
            return  # Don't recursively process class children again

    # Recursively process children
    for child in node.children:
        _extract_symbols_recursive(child, path, repo_root, symbols, run_id, class_name)


def _find_enclosing_function(
    node: "tree_sitter.Node", path: Path, repo_root: Path,
) -> Optional[str]:
    """Find the enclosing function for a node."""
    current = node.parent
    class_name = None
    func_name = None
    # Walk all the way up to find both function and class context
    while current is not None:
        if current.type == "class_definition":
            if class_name is None:
                class_name = _get_identifier(current)
        if current.type == "function_definition":
            if func_name is None:  # Capture innermost function
                func_name = _get_identifier(current)
        current = current.parent

    if func_name:
        qualified_name = f"{class_name}.{func_name}" if class_name else func_name
        return _make_stable_id(path, repo_root, qualified_name, "fn")
    return None  # pragma: no cover


def _extract_edges_recursive(
    node: "tree_sitter.Node",
    path: Path,
    repo_root: Path,
    edges: list[Edge],
    symbol_registry: dict[str, str],
    run_id: str,
) -> None:
    """Extract edges from a syntax tree node recursively."""
    if node.type == "function_call":
        caller_id = _find_enclosing_function(node, path, repo_root)
        if caller_id:
            callee_name = _get_identifier(node)

            if callee_name:
                callee_id = symbol_registry.get(callee_name)
                confidence = 1.0 if callee_id else 0.6
                if callee_id is None:
                    callee_id = f"matlab:unresolved:{callee_name}"

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
                    evidence_lang="matlab",
                )
                edges.append(edge)

    # Recursively process children
    for child in node.children:
        _extract_edges_recursive(child, path, repo_root, edges, symbol_registry, run_id)


class MatlabAnalyzer(TreeSitterAnalyzer):
    """MATLAB language analyzer using tree-sitter-language-pack.

    Overrides analyze() entirely because MATLAB requires:
    - Custom .m file disambiguation via classify_dot_m_file
    - Stateful symbol registry for cross-file call resolution
    - Empty result (no run) when no MATLAB files found
    """

    lang = "matlab"
    pass_id = PASS_ID
    pass_version = PASS_VERSION
    file_patterns: ClassVar[list[str]] = ["*.m"]
    language_pack_name = "matlab"

    def analyze(self, repo_root: Path, max_files=None) -> AnalysisResult:
        """Analyze all MATLAB files in the repository."""
        if not self._check_grammar_available():
            warnings.warn(
                "MATLAB analysis skipped: tree-sitter-language-pack not available",
                UserWarning,
                stacklevel=2,
            )
            return AnalysisResult(
                skipped=True,
                skip_reason="tree-sitter-language-pack not available",
            )

        import uuid as uuid_module

        start_time = time.time()
        run_id = f"uuid:{uuid_module.uuid4()}"

        parser = self._create_parser()
        matlab_files = list(find_matlab_files(repo_root))

        if not matlab_files:
            return AnalysisResult()

        symbols: list[Symbol] = []
        edges: list[Edge] = []
        symbol_registry: dict[str, str] = {}  # name -> id

        # Pass 1: Collect all symbols
        for path in matlab_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                _extract_symbols_recursive(tree.root_node, path, repo_root, symbols, run_id)
            except Exception:  # pragma: no cover
                pass

        # Build symbol registry
        for sym in symbols:
            symbol_registry[sym.name] = sym.id

        # Pass 2: Extract edges
        for path in matlab_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                _extract_edges_recursive(
                    tree.root_node, path, repo_root, edges, symbol_registry, run_id
                )
            except Exception:  # pragma: no cover
                pass

        elapsed = time.time() - start_time

        run = AnalysisRun(
            execution_id=run_id,
            run_signature="",
            pass_id=PASS_ID,
            version=PASS_VERSION,
            toolchain={"name": "matlab", "version": "unknown"},
            duration_ms=int(elapsed * 1000),
        )

        return AnalysisResult(
            symbols=symbols,
            edges=edges,
            run=run,
        )


_analyzer = MatlabAnalyzer()


def is_matlab_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with MATLAB support is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("matlab")
def analyze_matlab(repo_root: Path) -> AnalysisResult:
    """Analyze MATLAB source files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        AnalysisResult containing symbols, edges, and analysis metadata
    """
    return _analyzer.analyze(repo_root)
