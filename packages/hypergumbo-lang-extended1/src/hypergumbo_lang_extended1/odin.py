"""Odin language analyzer using tree-sitter.

This module provides static analysis for Odin source code, extracting symbols
(procedures, structs, enums, unions) and edges (imports, calls).

Odin is a general-purpose systems programming language designed as an alternative
to C. It emphasizes simplicity, readability, and metaprogramming capabilities.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
- Pass 1: Collect symbols (procedures, structs, enums, unions)
- Pass 2: Extract edges (imports, calls) with cross-file resolution

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Odin-specific extraction
logic.

Odin grammar key patterns:
- procedure_declaration: name :: proc(...) { body }
- struct_declaration: Name :: struct { fields... }
- enum_declaration: Name :: enum { variants... }
- union_declaration: Name :: union { variants... }
- import_declaration: import "package:module"
- call_expression: func(args) or obj.method(args)
- member_expression: module.symbol
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol
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

PASS_ID = "odin.tree_sitter"
PASS_VERSION = "hypergumbo-0.1.0"


def find_odin_files(root: Path) -> Iterator[Path]:
    """Find all Odin files in the given directory."""
    for path in find_files(root, ["*.odin"]):
        if path.is_file():
            yield path


def _make_stable_id(rel_path: str, name: str, kind: str) -> str:
    """Create a stable ID for an Odin symbol."""
    return f"odin:{rel_path}:{name}:{kind}"


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8")


def _get_identifier(node: "tree_sitter.Node") -> Optional[str]:
    """Get the identifier name from a declaration node."""
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_string_content(node: "tree_sitter.Node") -> Optional[str]:
    """Extract string content from a string node."""
    for child in node.children:
        if child.type == "string_content":
            return _get_node_text(child)
    return None  # pragma: no cover


def _extract_procedure_params(node: "tree_sitter.Node") -> list[str]:
    """Extract parameter names from a procedure node."""
    params = []
    for child in node.children:
        if child.type == "procedure":
            for proc_child in child.children:
                if proc_child.type == "parameters":
                    for param_child in proc_child.children:
                        if param_child.type == "parameter":
                            for param_part in param_child.children:
                                if param_part.type == "identifier":
                                    params.append(_get_node_text(param_part))
                                    break
    return params


def _extract_procedure_return_type(node: "tree_sitter.Node") -> Optional[str]:
    """Extract return type from a procedure node."""
    for child in node.children:
        if child.type == "procedure":
            saw_arrow = False
            for proc_child in child.children:
                if proc_child.type == "->":
                    saw_arrow = True
                elif saw_arrow and proc_child.type == "type":
                    # Get the type identifier
                    for type_child in proc_child.children:
                        if type_child.type == "identifier":
                            return _get_node_text(type_child)
                    return _get_node_text(proc_child)  # pragma: no cover
    return None


def _extract_symbols_recursive(
    node: "tree_sitter.Node", rel_path: str, analysis: FileAnalysis,
) -> None:
    """Extract symbols from a syntax tree node."""
    if node.type == "procedure_declaration":
        name = _get_identifier(node)
        if name:
            params = _extract_procedure_params(node)
            return_type = _extract_procedure_return_type(node)
            signature = f"proc({', '.join(params)})"
            if return_type:
                signature += f" -> {return_type}"

            sym = Symbol(
                id=_make_stable_id(rel_path, name, "proc"),
                stable_id=_make_stable_id(rel_path, name, "proc"),
                name=name,
                kind="function",
                language="odin",
                path=rel_path,
                span=Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
                signature=signature,
            )
            analysis.symbols.append(sym)
            analysis.symbol_by_name[sym.name] = sym

    elif node.type == "struct_declaration":
        name = _get_identifier(node)
        if name:
            # Count fields
            field_count = sum(1 for c in node.children if c.type == "field")

            sym = Symbol(
                id=_make_stable_id(rel_path, name, "struct"),
                stable_id=_make_stable_id(rel_path, name, "struct"),
                name=name,
                kind="class",
                language="odin",
                path=rel_path,
                span=Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
                meta={"field_count": field_count},
            )
            analysis.symbols.append(sym)
            analysis.symbol_by_name[sym.name] = sym

    elif node.type == "enum_declaration":
        name = _get_identifier(node)
        if name:
            sym = Symbol(
                id=_make_stable_id(rel_path, name, "enum"),
                stable_id=_make_stable_id(rel_path, name, "enum"),
                name=name,
                kind="enum",
                language="odin",
                path=rel_path,
                span=Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
            )
            analysis.symbols.append(sym)
            analysis.symbol_by_name[sym.name] = sym

    elif node.type == "union_declaration":
        name = _get_identifier(node)
        if name:
            sym = Symbol(
                id=_make_stable_id(rel_path, name, "union"),
                stable_id=_make_stable_id(rel_path, name, "union"),
                name=name,
                kind="class",
                language="odin",
                path=rel_path,
                span=Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
                meta={"is_union": True},
            )
            analysis.symbols.append(sym)
            analysis.symbol_by_name[sym.name] = sym

    # Recursively process children
    for child in node.children:
        _extract_symbols_recursive(child, rel_path, analysis)


def _find_enclosing_function(
    node: "tree_sitter.Node", rel_path: str,
) -> Optional[str]:
    """Find the enclosing function for a node. Returns its stable ID."""
    current = node.parent
    while current is not None:
        if current.type == "procedure_declaration":
            name = _get_identifier(current)
            if name:
                return _make_stable_id(rel_path, name, "proc")
        current = current.parent
    return None  # pragma: no cover


def _extract_edges_recursive(
    node: "tree_sitter.Node", rel_path: str,
    symbol_registry: dict[str, str], run_id: str, edges: list[Edge],
) -> None:
    """Extract edges from a syntax tree node."""
    if node.type == "import_declaration":
        # Extract import path
        for child in node.children:
            if child.type == "string":
                import_path = _get_string_content(child)
                if import_path:
                    # Create import edge
                    line = node.start_point[0] + 1
                    edge = Edge.create(
                        src=f"file:{rel_path}",
                        dst=f"odin:import:{import_path}",
                        edge_type="imports",
                        line=line,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        evidence_type="ast_import",
                        confidence=1.0,
                        evidence_lang="odin",
                    )
                    edges.append(edge)

    elif node.type == "call_expression":
        # Find the enclosing function
        caller_id = _find_enclosing_function(node, rel_path)
        if caller_id:
            # Get the callee name
            callee_name = None
            for child in node.children:
                if child.type == "identifier":
                    callee_name = _get_node_text(child)
                    break

            if callee_name:
                # Try to resolve the callee
                callee_id = symbol_registry.get(callee_name)
                confidence = 1.0 if callee_id else 0.6
                if callee_id is None:
                    callee_id = f"odin:unresolved:{callee_name}"

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
                    evidence_lang="odin",
                )
                edges.append(edge)

    elif node.type == "member_expression":
        # Handle qualified calls like fmt.println
        parent = node.parent
        if parent and parent.type == "member_expression":
            pass  # pragma: no cover
        else:
            call_child = None
            for child in node.children:
                if child.type == "call_expression":
                    call_child = child
                    break

            if call_child:
                caller_id = _find_enclosing_function(node, rel_path)
                if caller_id:
                    # Get module and function name
                    module_name = None
                    func_name = None
                    for child in node.children:
                        if child.type == "identifier":
                            if module_name is None:
                                module_name = _get_node_text(child)
                            else:
                                func_name = _get_node_text(child)  # pragma: no cover
                    for child in call_child.children:
                        if child.type == "identifier":
                            func_name = _get_node_text(child)
                            break

                    if module_name and func_name:
                        qualified_name = f"{module_name}.{func_name}"
                        callee_id = f"odin:external:{qualified_name}"

                        line = node.start_point[0] + 1
                        edge = Edge.create(
                            src=caller_id,
                            dst=callee_id,
                            edge_type="calls",
                            line=line,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                            evidence_type="ast_call_method",
                            confidence=0.8,
                            evidence_lang="odin",
                        )
                        edges.append(edge)

    # Recursively process children
    for child in node.children:
        _extract_edges_recursive(child, rel_path, symbol_registry, run_id, edges)


# ---------------------------------------------------------------------------
# OdinAnalyzer: TreeSitterAnalyzer subclass
# ---------------------------------------------------------------------------


class OdinAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Odin source files using TreeSitterAnalyzer base class."""

    lang = "odin"
    pass_id = PASS_ID
    pass_version = PASS_VERSION
    file_patterns: ClassVar[list[str]] = ["*.odin"]
    grammar_module = "tree_sitter_odin"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract symbols from a single Odin file."""
        analysis = FileAnalysis()
        _extract_symbols_recursive(tree.root_node, rel_path, analysis)
        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract import and call edges from a single Odin file."""
        edges: list[Edge] = []

        # Build symbol registry (name -> id) from global symbols
        symbol_registry: dict[str, str] = {}
        for sym_name, sym in global_symbols.items():
            symbol_registry[sym_name] = sym.id

        _extract_edges_recursive(
            tree.root_node, rel_path, symbol_registry, run.execution_id, edges,
        )
        return edges


_analyzer = OdinAnalyzer()


def is_odin_tree_sitter_available() -> bool:
    """Check if tree-sitter and tree-sitter-odin are available."""
    return _analyzer._check_grammar_available()


@register_analyzer("odin")
def analyze_odin(repo_root: Path) -> AnalysisResult:
    """Analyze Odin source files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        AnalysisResult containing symbols, edges, and analysis metadata
    """
    return _analyzer.analyze(repo_root)
