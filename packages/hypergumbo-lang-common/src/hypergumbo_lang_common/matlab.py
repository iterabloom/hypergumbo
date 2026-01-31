"""MATLAB language analyzer using tree-sitter.

This module provides static analysis for MATLAB source code, extracting symbols
(functions, classes, properties, methods) and edges (calls).

MATLAB is a high-level language and interactive environment for numerical
computation, visualization, and programming, commonly used in engineering
and scientific research.

Implementation approach:
- Uses tree-sitter-language-pack for MATLAB grammar
- Two-pass analysis: First pass collects all symbols, second pass extracts edges
- Handles MATLAB-specific constructs like classdef, properties, methods blocks

Key constructs extracted:
- function_definition: function output = name(args)
- class_definition: classdef Name
- properties: Property declarations within a class
- methods: Method definitions within a class
- function_call: Direct function calls
"""

import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "matlab.tree_sitter"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class MatlabAnalysisResult:
    """Result of analyzing MATLAB files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: Optional[AnalysisRun] = None
    skipped: bool = False
    skip_reason: str = ""


def is_matlab_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with MATLAB support is available."""
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("matlab")
        return True
    except (ImportError, Exception):  # pragma: no cover
        return False  # pragma: no cover


def find_matlab_files(root: Path) -> Iterator[Path]:
    """Find all MATLAB files in the given directory."""
    for path in root.rglob("*.m"):
        if path.is_file():
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


class MatlabAnalyzer:
    """Analyzer for MATLAB source files."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.symbols: list[Symbol] = []
        self.edges: list[Edge] = []
        self._symbol_registry: dict[str, str] = {}  # name -> id
        self._run_id: str = ""

    def analyze(self) -> MatlabAnalysisResult:
        """Analyze all MATLAB files in the repository."""
        if not is_matlab_tree_sitter_available():
            warnings.warn(
                "MATLAB analysis skipped: tree-sitter-language-pack not available",
                UserWarning,
                stacklevel=2,
            )
            return MatlabAnalysisResult(
                skipped=True,
                skip_reason="tree-sitter-language-pack not available",
            )

        import uuid as uuid_module
        from tree_sitter_language_pack import get_parser

        start_time = time.time()
        self._run_id = f"uuid:{uuid_module.uuid4()}"

        parser = get_parser("matlab")
        matlab_files = list(find_matlab_files(self.repo_root))

        if not matlab_files:
            return MatlabAnalysisResult()

        # Pass 1: Collect all symbols
        for path in matlab_files:
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
        for path in matlab_files:
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
            toolchain={"name": "matlab", "version": "unknown"},
            duration_ms=int(elapsed * 1000),
        )

        return MatlabAnalysisResult(
            symbols=self.symbols,
            edges=self.edges,
            run=run,
        )

    def _extract_symbols(
        self, node: "tree_sitter.Node", path: Path, class_name: Optional[str] = None
    ) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "function_definition":
            name = _get_identifier(node)
            if name:
                params = _extract_function_params(node)
                output = _extract_function_output(node)

                signature = f"function({', '.join(params)})"
                if output:
                    signature = f"{output} = {signature}"

                rel_path = str(path.relative_to(self.repo_root))

                # Determine if this is a method (inside a class) or standalone function
                kind = "method" if class_name else "function"
                qualified_name = f"{class_name}.{name}" if class_name else name

                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, qualified_name, "fn"),
                    stable_id=_make_stable_id(path, self.repo_root, qualified_name, "fn"),
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
                self.symbols.append(sym)

        elif node.type == "class_definition":
            name = _get_identifier(node)
            if name:
                property_count = _count_properties(node)
                method_count = _count_methods(node)
                rel_path = str(path.relative_to(self.repo_root))
                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "class"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "class"),
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
                self.symbols.append(sym)

                # Extract methods within the class
                for child in node.children:
                    if child.type == "methods":
                        for method_child in child.children:
                            if method_child.type == "function_definition":
                                self._extract_symbols(method_child, path, class_name=name)
                return  # Don't recursively process class children again

        # Recursively process children
        for child in node.children:
            self._extract_symbols(child, path, class_name)

    def _extract_edges(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract edges from a syntax tree node."""
        if node.type == "function_call":
            caller_id = self._find_enclosing_function(node, path)
            if caller_id:
                callee_name = _get_identifier(node)

                if callee_name:
                    callee_id = self._symbol_registry.get(callee_name)
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
                        origin_run_id=self._run_id,
                        evidence_type="ast_call_direct",
                        confidence=confidence,
                        evidence_lang="matlab",
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
            return _make_stable_id(path, self.repo_root, qualified_name, "fn")
        return None  # pragma: no cover


def analyze_matlab(repo_root: Path) -> MatlabAnalysisResult:
    """Analyze MATLAB source files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        MatlabAnalysisResult containing symbols, edges, and analysis metadata
    """
    analyzer = MatlabAnalyzer(repo_root)
    return analyzer.analyze()
