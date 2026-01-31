"""Gleam language analyzer using tree-sitter.

This module provides static analysis for Gleam source code, extracting symbols
(functions, types, type aliases) and edges (imports, calls).

Gleam is a type-safe functional programming language for the Erlang VM (BEAM)
and JavaScript. It emphasizes simplicity, correctness, and friendly error messages.

Implementation approach:
- Uses tree-sitter-language-pack for Gleam grammar
- Two-pass analysis: First pass collects all symbols, second pass extracts edges
- Handles Gleam-specific constructs like pub functions, custom types, type aliases

Key constructs extracted:
- function: Public and private function declarations
- type_definition: Custom types with constructors (similar to enums/ADTs)
- type_alias: Type aliases
- import: Module imports
- function_call: Direct and qualified function calls
"""

import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "gleam.tree_sitter"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class GleamAnalysisResult:
    """Result of analyzing Gleam files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: Optional[AnalysisRun] = None
    skipped: bool = False
    skip_reason: str = ""


def is_gleam_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with Gleam support is available."""
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("gleam")
        return True
    except (ImportError, Exception):  # pragma: no cover
        return False  # pragma: no cover


def find_gleam_files(root: Path) -> Iterator[Path]:
    """Find all Gleam files in the given directory."""
    for path in root.rglob("*.gleam"):
        if path.is_file():
            yield path


def _make_stable_id(path: Path, repo_root: Path, name: str, kind: str) -> str:
    """Create a stable ID for a Gleam symbol."""
    rel_path = path.relative_to(repo_root)
    return f"gleam:{rel_path}:{name}:{kind}"


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8")


def _get_identifier(node: "tree_sitter.Node") -> Optional[str]:
    """Get the identifier name from a node's children."""
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_type_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the type name from a type_definition node."""
    for child in node.children:
        if child.type == "type_name":
            for subchild in child.children:
                if subchild.type == "type_identifier":
                    return _get_node_text(subchild)
    return None  # pragma: no cover


def _is_public(node: "tree_sitter.Node") -> bool:
    """Check if a declaration is public (has visibility_modifier 'pub')."""
    for child in node.children:
        if child.type == "visibility_modifier":
            return _get_node_text(child) == "pub"
    return False


def _extract_function_params(node: "tree_sitter.Node") -> list[str]:
    """Extract parameter names from a function node."""
    params = []
    for child in node.children:
        if child.type == "function_parameters":
            for param_child in child.children:
                if param_child.type == "function_parameter":
                    for param_part in param_child.children:
                        if param_part.type == "identifier":
                            params.append(_get_node_text(param_part))
                            break
    return params


def _extract_return_type(node: "tree_sitter.Node") -> Optional[str]:
    """Extract return type from a function node."""
    saw_arrow = False
    for child in node.children:
        if child.type == "->":
            saw_arrow = True
        elif saw_arrow and child.type == "type":
            return _get_node_text(child).strip()
    return None


def _count_constructors(node: "tree_sitter.Node") -> int:
    """Count data constructors in a type_definition."""
    for child in node.children:
        if child.type == "data_constructors":
            return sum(1 for c in child.children if c.type == "data_constructor")
    return 0  # pragma: no cover


class GleamAnalyzer:
    """Analyzer for Gleam source files."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.symbols: list[Symbol] = []
        self.edges: list[Edge] = []
        self._symbol_registry: dict[str, str] = {}  # name -> id
        self._run_id: str = ""

    def analyze(self) -> GleamAnalysisResult:
        """Analyze all Gleam files in the repository."""
        if not is_gleam_tree_sitter_available():
            warnings.warn(
                "Gleam analysis skipped: tree-sitter-language-pack not available",
                UserWarning,
                stacklevel=2,
            )
            return GleamAnalysisResult(
                skipped=True,
                skip_reason="tree-sitter-language-pack not available",
            )

        import uuid as uuid_module
        from tree_sitter_language_pack import get_parser

        start_time = time.time()
        self._run_id = f"uuid:{uuid_module.uuid4()}"

        parser = get_parser("gleam")
        gleam_files = list(find_gleam_files(self.repo_root))

        if not gleam_files:
            return GleamAnalysisResult()

        # Pass 1: Collect all symbols
        for path in gleam_files:
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
        for path in gleam_files:
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
            toolchain={"name": "gleam", "version": "unknown"},
            duration_ms=int(elapsed * 1000),
        )

        return GleamAnalysisResult(
            symbols=self.symbols,
            edges=self.edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "function":
            name = _get_identifier(node)
            if name:
                params = _extract_function_params(node)
                return_type = _extract_return_type(node)
                is_pub = _is_public(node)

                signature = f"fn({', '.join(params)})"
                if return_type:
                    signature += f" -> {return_type}"

                rel_path = str(path.relative_to(self.repo_root))
                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "fn"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "fn"),
                    name=name,
                    kind="function",
                    language="gleam",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    signature=signature,
                    meta={"is_public": is_pub},
                )
                self.symbols.append(sym)

        elif node.type == "type_definition":
            name = _get_type_name(node)
            if name:
                constructor_count = _count_constructors(node)
                is_pub = _is_public(node)
                rel_path = str(path.relative_to(self.repo_root))
                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "type"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "type"),
                    name=name,
                    kind="class",
                    language="gleam",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta={"is_public": is_pub, "constructor_count": constructor_count},
                )
                self.symbols.append(sym)

        elif node.type == "type_alias":
            # Type aliases: pub type Name = OtherType
            name = _get_type_name(node)
            if name:
                is_pub = _is_public(node)
                rel_path = str(path.relative_to(self.repo_root))
                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "type_alias"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "type_alias"),
                    name=name,
                    kind="type",
                    language="gleam",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta={"is_public": is_pub, "is_alias": True},
                )
                self.symbols.append(sym)

        # Recursively process children
        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_edges(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract edges from a syntax tree node."""
        if node.type == "import":
            # Extract import target
            for child in node.children:
                if child.type == "module":
                    import_path = _get_node_text(child)
                    rel_path = str(path.relative_to(self.repo_root))
                    line = node.start_point[0] + 1
                    edge = Edge.create(
                        src=f"file:{rel_path}",
                        dst=f"gleam:import:{import_path}",
                        edge_type="imports",
                        line=line,
                        origin=PASS_ID,
                        origin_run_id=self._run_id,
                        evidence_type="ast_import",
                        confidence=1.0,
                        evidence_lang="gleam",
                    )
                    self.edges.append(edge)

        elif node.type == "function_call":
            caller_id = self._find_enclosing_function(node, path)
            if caller_id:
                # Get the callee
                callee_name = None
                is_qualified = False

                for child in node.children:
                    if child.type == "identifier":
                        callee_name = _get_node_text(child)
                        break
                    elif child.type == "field_access":
                        # Qualified call like io.println
                        is_qualified = True
                        parts = []
                        for subchild in child.children:
                            if subchild.type == "identifier":
                                parts.append(_get_node_text(subchild))
                            elif subchild.type == "label":
                                parts.append(_get_node_text(subchild))
                        if parts:
                            callee_name = ".".join(parts)
                        break

                if callee_name:
                    if is_qualified:
                        callee_id = f"gleam:external:{callee_name}"
                        confidence = 0.8
                    else:
                        callee_id = self._symbol_registry.get(callee_name)
                        confidence = 1.0 if callee_id else 0.6
                        if callee_id is None:
                            callee_id = f"gleam:unresolved:{callee_name}"

                    line = node.start_point[0] + 1
                    edge = Edge.create(
                        src=caller_id,
                        dst=callee_id,
                        edge_type="calls",
                        line=line,
                        origin=PASS_ID,
                        origin_run_id=self._run_id,
                        evidence_type="ast_call_direct" if not is_qualified else "ast_call_method",
                        confidence=confidence,
                        evidence_lang="gleam",
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
            if current.type == "function":
                name = _get_identifier(current)
                if name:
                    return _make_stable_id(path, self.repo_root, name, "fn")
            current = current.parent
        return None  # pragma: no cover


def analyze_gleam(repo_root: Path) -> GleamAnalysisResult:
    """Analyze Gleam source files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        GleamAnalysisResult containing symbols, edges, and analysis metadata
    """
    analyzer = GleamAnalyzer(repo_root)
    return analyzer.analyze()
