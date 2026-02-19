"""V language analyzer using tree-sitter.

This module provides static analysis for V source code, extracting symbols
(functions, structs, enums, interfaces) and edges (imports, calls).

V is a statically typed compiled programming language designed to be simple,
fast, and safe. It aims to be a pragmatic alternative to C with modern features.

Implementation approach:
- Uses TreeSitterAnalyzer base class for two-pass orchestration
- Uses tree-sitter-language-pack for V grammar
- Handles V-specific constructs like pub visibility, modules, structs, enums

Key constructs extracted:
- function_declaration: fn name(params) return_type { body }
- struct_declaration: struct Name { fields }
- enum_declaration: enum Name { variants }
- interface_declaration: interface Name { methods }
- import_declaration: import module
- call_expression: func(args)
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

PASS_ID = make_pass_id("v")


def find_v_files(root: Path) -> Iterator[Path]:
    """Find all V files in the given directory."""
    for path in find_files(root, ["*.v"]):
        if path.is_file():
            yield path


def _make_stable_id(path: Path, repo_root: Path, name: str, kind: str) -> str:
    """Create a stable ID for a V symbol."""
    rel_path = path.relative_to(repo_root)
    return f"v:{rel_path}:{name}:{kind}"


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8")


def _get_identifier(node: "tree_sitter.Node") -> Optional[str]:
    """Get the identifier name from a node's children."""
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_type_identifier(node: "tree_sitter.Node") -> Optional[str]:
    """Get the type identifier from a node's children."""
    for child in node.children:
        if child.type == "type_identifier":
            return _get_node_text(child)
    return None  # pragma: no cover


def _is_public(node: "tree_sitter.Node") -> bool:
    """Check if a declaration is public (has 'pub' keyword)."""
    for child in node.children:
        if child.type == "pub":
            return True
    return False


def _extract_function_params(node: "tree_sitter.Node") -> list[str]:
    """Extract parameter names from a function declaration."""
    params = []
    for child in node.children:
        if child.type == "parameter_list":
            for param_child in child.children:
                if param_child.type == "parameter_declaration":
                    for param_part in param_child.children:
                        if param_part.type == "identifier":
                            params.append(_get_node_text(param_part))
                            break
    return params


def _extract_return_type(node: "tree_sitter.Node") -> Optional[str]:
    """Extract return type from a function declaration."""
    saw_params = False
    for child in node.children:
        if child.type == "parameter_list":
            saw_params = True
        elif saw_params and child.type in ("builtin_type", "type_identifier", "pointer_type"):
            return _get_node_text(child).strip()
        elif child.type == "block":
            break  # Stop at function body
    return None


def _count_struct_fields(node: "tree_sitter.Node") -> int:
    """Count fields in a struct declaration."""
    for child in node.children:
        if child.type == "struct_field_declaration_list":
            count = 0
            for field_child in child.children:
                if field_child.type == "struct_field_declaration":
                    count += 1
            return count
    return 0  # pragma: no cover


def _count_enum_variants(node: "tree_sitter.Node") -> int:
    """Count variants in an enum declaration."""
    for child in node.children:
        if child.type == "enum_member_declaration_list":
            count = 0
            for member_child in child.children:
                if member_child.type == "enum_member":
                    count += 1
            return count
    return 0  # pragma: no cover


def _find_enclosing_function(
    node: "tree_sitter.Node", path: Path, repo_root: Path,
) -> Optional[str]:
    """Find the enclosing function for a node."""
    current = node.parent
    while current is not None:
        if current.type == "function_declaration":
            name = _get_identifier(current)
            if name:
                return _make_stable_id(path, repo_root, name, "fn")
        current = current.parent
    return None  # pragma: no cover


class VAnalyzer(TreeSitterAnalyzer):
    """Analyzer for V source files using TreeSitterAnalyzer base class."""

    lang = "v"
    file_patterns: ClassVar[list[str]] = ["*.v"]
    language_pack_name = "v"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract function, struct, enum, and interface symbols from a V file."""
        analysis = FileAnalysis()
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
        if node.type == "function_declaration":
            name = _get_identifier(node)
            if name:
                params = _extract_function_params(node)
                return_type = _extract_return_type(node)
                is_pub = _is_public(node)

                signature = f"fn({', '.join(params)})"
                if return_type:
                    signature += f" {return_type}"

                sym = Symbol(
                    id=_make_stable_id(path, repo_root, name, "fn"),
                    stable_id=_make_stable_id(path, repo_root, name, "fn"),
                    name=name,
                    kind="function",
                    language="v",
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
                    meta={"is_public": is_pub},
                )
                analysis.symbols.append(sym)
                analysis.symbol_by_name[name] = sym

        elif node.type == "struct_declaration":
            name = _get_type_identifier(node)
            if name:
                field_count = _count_struct_fields(node)
                is_pub = _is_public(node)
                sym = Symbol(
                    id=_make_stable_id(path, repo_root, name, "struct"),
                    stable_id=_make_stable_id(path, repo_root, name, "struct"),
                    name=name,
                    kind="class",
                    language="v",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta={"is_public": is_pub, "field_count": field_count},
                )
                analysis.symbols.append(sym)

        elif node.type == "enum_declaration":
            name = _get_type_identifier(node)
            if name:
                variant_count = _count_enum_variants(node)
                is_pub = _is_public(node)
                sym = Symbol(
                    id=_make_stable_id(path, repo_root, name, "enum"),
                    stable_id=_make_stable_id(path, repo_root, name, "enum"),
                    name=name,
                    kind="enum",
                    language="v",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta={"is_public": is_pub, "variant_count": variant_count},
                )
                analysis.symbols.append(sym)

        elif node.type == "interface_declaration":
            name = _get_type_identifier(node)
            if name:
                is_pub = _is_public(node)
                sym = Symbol(
                    id=_make_stable_id(path, repo_root, name, "interface"),
                    stable_id=_make_stable_id(path, repo_root, name, "interface"),
                    name=name,
                    kind="interface",
                    language="v",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta={"is_public": is_pub},
                )
                analysis.symbols.append(sym)

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
        """Extract import and call edges from a V file."""
        edges: list[Edge] = []
        rel_parts = Path(rel_path).parts
        repo_root = file_path
        for _ in rel_parts:
            repo_root = repo_root.parent

        self._extract_edges_recursive(
            tree.root_node, file_path, repo_root, rel_path,
            global_symbols, run, edges,
        )
        return edges

    def _extract_edges_recursive(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        rel_path: str, global_symbols: dict,
        run: "AnalysisRun", edges: list[Edge],
    ) -> None:
        """Extract edges from a syntax tree node recursively."""
        if node.type == "import_declaration":
            for child in node.children:
                if child.type == "import_path":
                    import_path = _get_node_text(child)
                    line = node.start_point[0] + 1
                    edge = Edge.create(
                        src=f"file:{rel_path}",
                        dst=f"v:import:{import_path}",
                        edge_type="imports",
                        line=line,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        evidence_type="ast_import",
                        confidence=1.0,
                        evidence_lang="v",
                    )
                    edges.append(edge)

        elif node.type == "call_expression":
            caller_id = _find_enclosing_function(node, path, repo_root)
            if caller_id:
                callee_name = _get_identifier(node)

                if callee_name:
                    callee_sym = global_symbols.get(callee_name)
                    callee_id = callee_sym.id if callee_sym else None
                    confidence = 1.0 if callee_id else 0.6
                    if callee_id is None:
                        callee_id = f"v:unresolved:{callee_name}"

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
                        evidence_lang="v",
                    )
                    edges.append(edge)

        # Recursively process children
        for child in node.children:
            self._extract_edges_recursive(
                child, path, repo_root, rel_path, global_symbols, run, edges,
            )


_analyzer = VAnalyzer()


def is_v_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with V support is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("v")
def analyze_v(repo_root: Path) -> AnalysisResult:
    """Analyze V source files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        AnalysisResult containing symbols, edges, and analysis metadata
    """
    return _analyzer.analyze(repo_root)
