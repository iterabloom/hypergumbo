"""Gleam language analyzer using tree-sitter.

This module provides static analysis for Gleam source code, extracting symbols
(functions, types, type aliases) and edges (imports, calls).

Gleam is a type-safe functional programming language for the Erlang VM (BEAM)
and JavaScript. It emphasizes simplicity, correctness, and friendly error messages.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Collect all symbols (functions, types, type aliases)
2. Pass 2: Extract import edges and call edges

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Gleam-specific extraction
logic.

Key constructs extracted:
- function: Public and private function declarations
- type_definition: Custom types with constructors (similar to enums/ADTs)
- type_alias: Type aliases
- import: Module imports
- function_call: Direct and qualified function calls
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

PASS_ID = make_pass_id("gleam")


def is_gleam_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with Gleam support is available."""
    return _analyzer._check_grammar_available()


def find_gleam_files(root: Path) -> Iterator[Path]:
    """Find all Gleam files in the given directory."""
    for path in find_files(root, ["*.gleam"]):
        if path.is_file():
            yield path


def _make_stable_id(rel_path: str, name: str, kind: str) -> str:
    """Create a stable ID for a Gleam symbol."""
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


def _find_enclosing_function(
    node: "tree_sitter.Node", rel_path: str,
) -> Optional[str]:
    """Find the enclosing function for a node."""
    current = node.parent
    while current is not None:
        if current.type == "function":
            name = _get_identifier(current)
            if name:
                return _make_stable_id(rel_path, name, "fn")
        current = current.parent
    return None  # pragma: no cover


# ---------------------------------------------------------------------------
# GleamAnalyzer: TreeSitterAnalyzer subclass
# ---------------------------------------------------------------------------


class GleamAnalyzer(TreeSitterAnalyzer):
    """Gleam language analyzer using tree-sitter-language-pack."""

    lang = "gleam"
    file_patterns: ClassVar[list[str]] = ["*.gleam"]
    language_pack_name = "gleam"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract function, type, and type alias symbols from Gleam."""
        analysis = FileAnalysis()
        self._extract_symbols_recursive(tree.root_node, rel_path, run.execution_id, analysis)
        return analysis

    def _extract_symbols_recursive(
        self, node: "tree_sitter.Node", rel_path: str, run_id: str,
        analysis: FileAnalysis,
    ) -> None:
        """Recursively extract symbols from Gleam AST."""
        if node.type == "function":
            name = _get_identifier(node)
            if name:
                params = _extract_function_params(node)
                return_type = _extract_return_type(node)
                is_pub = _is_public(node)

                signature = f"fn({', '.join(params)})"
                if return_type:
                    signature += f" -> {return_type}"

                sym = Symbol(
                    id=_make_stable_id(rel_path, name, "fn"),
                    stable_id=_make_stable_id(rel_path, name, "fn"),
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
                analysis.symbols.append(sym)
                analysis.symbol_by_name[name] = sym

        elif node.type == "type_definition":
            name = _get_type_name(node)
            if name:
                constructor_count = _count_constructors(node)
                is_pub = _is_public(node)
                sym = Symbol(
                    id=_make_stable_id(rel_path, name, "type"),
                    stable_id=_make_stable_id(rel_path, name, "type"),
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
                analysis.symbols.append(sym)

        elif node.type == "type_alias":
            name = _get_type_name(node)
            if name:
                is_pub = _is_public(node)
                sym = Symbol(
                    id=_make_stable_id(rel_path, name, "type_alias"),
                    stable_id=_make_stable_id(rel_path, name, "type_alias"),
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
                analysis.symbols.append(sym)

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
        """Extract import and call edges from Gleam."""
        edges: list[Edge] = []
        self._extract_edges_recursive(
            tree.root_node, rel_path, run.execution_id, global_symbols, edges,
        )
        return edges

    def _extract_edges_recursive(
        self, node: "tree_sitter.Node", rel_path: str, run_id: str,
        global_symbols: dict, edges: list[Edge],
    ) -> None:
        """Recursively extract edges from Gleam AST."""
        if node.type == "import":
            for child in node.children:
                if child.type == "module":
                    import_path = _get_node_text(child)
                    line = node.start_point[0] + 1
                    edge = Edge.create(
                        src=f"file:{rel_path}",
                        dst=f"gleam:import:{import_path}",
                        edge_type="imports",
                        line=line,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        evidence_type="ast_import",
                        confidence=1.0,
                        evidence_lang="gleam",
                    )
                    edges.append(edge)

        elif node.type == "function_call":
            caller_id = _find_enclosing_function(node, rel_path)
            if caller_id:
                callee_name = None
                is_qualified = False

                for child in node.children:
                    if child.type == "identifier":
                        callee_name = _get_node_text(child)
                        break
                    elif child.type == "field_access":
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
                        callee_sym = global_symbols.get(callee_name)
                        if isinstance(callee_sym, Symbol):
                            callee_id = callee_sym.id
                            confidence = 1.0
                        else:
                            callee_id = f"gleam:unresolved:{callee_name}"
                            confidence = 0.6

                    line = node.start_point[0] + 1
                    edge = Edge.create(
                        src=caller_id,
                        dst=callee_id,
                        edge_type="calls",
                        line=line,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        evidence_type="ast_call_direct" if not is_qualified else "ast_call_method",
                        confidence=confidence,
                        evidence_lang="gleam",
                    )
                    edges.append(edge)

        # Recursively process children
        for child in node.children:
            self._extract_edges_recursive(child, rel_path, run_id, global_symbols, edges)


_analyzer = GleamAnalyzer()


@register_analyzer("gleam")
def analyze_gleam(repo_root: Path) -> AnalysisResult:
    """Analyze Gleam source files in a repository."""
    return _analyzer.analyze(repo_root)
