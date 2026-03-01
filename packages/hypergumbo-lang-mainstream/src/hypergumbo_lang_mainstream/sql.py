"""SQL schema analysis pass using tree-sitter-sql.

This analyzer uses tree-sitter to parse SQL files and extract:
- Table definitions (CREATE TABLE)
- View definitions (CREATE VIEW)
- Function definitions (CREATE FUNCTION)
- Procedure definitions (CREATE PROCEDURE)
- Trigger definitions (CREATE TRIGGER)
- Index definitions (CREATE INDEX)
- Foreign key reference relationships

If tree-sitter with SQL support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract tables, views, functions, triggers, indexes with fingerprints
2. Pass 2: Extract foreign key reference edges using NameResolver

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the SQL-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Optional dependency keeps base install lightweight
- Uses tree-sitter-sql package for grammar
- Two-pass allows cross-file reference resolution
- SQL-specific: tables, views, functions, triggers are first-class symbols
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("sql")


def find_sql_files(repo_root: Path) -> Iterator[Path]:
    """Yield all SQL files in the repository."""
    yield from find_files(repo_root, ["*.sql"])


def _find_children_by_type(node: "tree_sitter.Node", type_name: str) -> list["tree_sitter.Node"]:  # pragma: no cover
    """Find all children of given type."""
    return [child for child in node.children if child.type == type_name]  # pragma: no cover


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _extract_table_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract table name from a CREATE TABLE node."""
    obj_ref = find_child_by_type(node, "object_reference")
    if obj_ref:
        return node_text(obj_ref, source)
    ident = find_child_by_type(node, "identifier")  # pragma: no cover
    if ident:  # pragma: no cover
        return node_text(ident, source)  # pragma: no cover
    return None  # pragma: no cover


def _extract_view_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract view name from a CREATE VIEW node."""
    obj_ref = find_child_by_type(node, "object_reference")
    if obj_ref:
        return node_text(obj_ref, source)
    return None  # pragma: no cover


def _extract_function_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function name from a CREATE FUNCTION node."""
    obj_ref = find_child_by_type(node, "object_reference")
    if obj_ref:
        return node_text(obj_ref, source)
    return None  # pragma: no cover


def _extract_sql_signature(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function signature from a CREATE FUNCTION node.

    Returns signature in format: (param_name TYPE, ...) RETURNS return_type
    SQL functions have typed parameters.
    """
    params: list[str] = []
    return_type: Optional[str] = None

    func_args = find_child_by_type(node, "function_arguments")
    if func_args:
        for child in func_args.children:
            if child.type == "function_argument":
                param_text = node_text(child, source).strip()
                params.append(param_text)

    found_returns = False
    for child in node.children:
        if child.type == "keyword_returns":
            found_returns = True
        elif found_returns and child.type not in ("keyword_returns",):
            if child.type in ("decimal", "int", "varchar", "text", "boolean",
                              "float", "double", "bigint", "smallint", "real",
                              "numeric", "char", "timestamp", "date", "time",
                              "identifier", "type_identifier"):
                return_type = node_text(child, source)
                break
            if child.type not in ("function_body", "function_language"):
                return_type = node_text(child, source)
                break

    params_str = ", ".join(params) if params else ""
    signature = f"({params_str})"
    if return_type:
        signature += f" RETURNS {return_type}"

    return signature


def _extract_trigger_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract trigger name from a CREATE TRIGGER node."""
    for child in node.children:
        if child.type == "object_reference":
            return node_text(child, source)
    return None  # pragma: no cover


def _extract_index_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract index name from a CREATE INDEX node."""
    ident = find_child_by_type(node, "identifier")
    if ident:
        return node_text(ident, source)
    obj_ref = find_child_by_type(node, "object_reference")  # pragma: no cover
    if obj_ref:  # pragma: no cover
        return node_text(obj_ref, source)  # pragma: no cover
    return None  # pragma: no cover


def _extract_procedure_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract procedure name from a CREATE PROCEDURE node (dialect-specific)."""
    obj_ref = find_child_by_type(node, "object_reference")  # pragma: no cover
    if obj_ref:  # pragma: no cover
        return node_text(obj_ref, source)  # pragma: no cover
    ident = find_child_by_type(node, "identifier")  # pragma: no cover
    if ident:  # pragma: no cover
        return node_text(ident, source)  # pragma: no cover
    return None  # pragma: no cover


def _find_references_in_columns(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Find REFERENCES clauses in column definitions."""
    references: list[str] = []

    for n in iter_tree(node):
        if n.type == "column_definition":
            has_references = False
            for child in n.children:
                if child.type == "keyword_references":
                    has_references = True
                elif has_references and child.type == "object_reference":
                    ref_text = node_text(child, source)
                    if ref_text and ref_text not in references:
                        references.append(ref_text)
                    has_references = False

        if n.type == "object_reference" and n.parent:  # pragma: no cover
            parent_text = node_text(n.parent, source).upper()  # pragma: no cover
            if "REFERENCES" in parent_text:  # pragma: no cover
                ref_name = node_text(n, source)  # pragma: no cover
                if ref_name and ref_name not in references:  # pragma: no cover
                    references.append(ref_name)  # pragma: no cover

    return references


def _extract_sql_symbols(
    root_node: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    symbol_registry: dict[str, Symbol],
) -> None:
    """Extract symbols from SQL AST tree (pass 1)."""
    for node in iter_tree(root_node):
        if node.type == "create_table":
            name = _extract_table_name(node, source)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("sql", rel_path, start_line, end_line, name, "table")
                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="table",
                    name=name,
                    path=rel_path,
                    language="sql",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)
                symbol_registry[name.lower()] = sym

        elif node.type == "create_view":
            name = _extract_view_name(node, source)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("sql", rel_path, start_line, end_line, name, "view")
                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="view",
                    name=name,
                    path=rel_path,
                    language="sql",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)
                symbol_registry[name.lower()] = sym

        elif node.type == "create_function":
            name = _extract_function_name(node, source)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("sql", rel_path, start_line, end_line, name, "function")
                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="function",
                    name=name,
                    path=rel_path,
                    language="sql",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    signature=_extract_sql_signature(node, source),
                )
                symbols.append(sym)
                symbol_registry[name.lower()] = sym

        elif node.type == "create_procedure":  # pragma: no cover
            name = _extract_procedure_name(node, source)  # pragma: no cover
            if name:  # pragma: no cover
                start_line = node.start_point[0] + 1  # pragma: no cover
                end_line = node.end_point[0] + 1  # pragma: no cover
                symbol_id = make_symbol_id("sql", rel_path, start_line, end_line, name, "procedure")  # pragma: no cover
                sym = Symbol(  # pragma: no cover
                    id=symbol_id,  # pragma: no cover
                    stable_id=None,  # pragma: no cover
                    shape_id=None,  # pragma: no cover
                    canonical_name=name,  # pragma: no cover
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],  # pragma: no cover
                    kind="procedure",  # pragma: no cover
                    name=name,  # pragma: no cover
                    path=rel_path,  # pragma: no cover
                    language="sql",  # pragma: no cover
                    span=Span(  # pragma: no cover
                        start_line=start_line,  # pragma: no cover
                        end_line=end_line,  # pragma: no cover
                        start_col=node.start_point[1],  # pragma: no cover
                        end_col=node.end_point[1],  # pragma: no cover
                    ),  # pragma: no cover
                    origin=PASS_ID,  # pragma: no cover
                )  # pragma: no cover
                symbols.append(sym)  # pragma: no cover
                symbol_registry[name.lower()] = sym  # pragma: no cover

        elif node.type == "create_trigger":
            name = _extract_trigger_name(node, source)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("sql", rel_path, start_line, end_line, name, "trigger")
                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="trigger",
                    name=name,
                    path=rel_path,
                    language="sql",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)
                symbol_registry[name.lower()] = sym

        elif node.type == "create_index":
            name = _extract_index_name(node, source)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("sql", rel_path, start_line, end_line, name, "index")
                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="index",
                    name=name,
                    path=rel_path,
                    language="sql",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)
                symbol_registry[name.lower()] = sym


def _extract_sql_edges(
    root_node: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
    resolver: "NameResolver",
) -> None:
    """Extract edges from SQL AST tree (pass 2)."""
    for node in iter_tree(root_node):
        if node.type == "create_table":
            name = _extract_table_name(node, source)
            if name:
                table_sym = None
                for sym in symbols:
                    if sym.name == name and sym.kind == "table" and sym.path == rel_path:
                        table_sym = sym
                        break

                if table_sym:
                    start_line = node.start_point[0] + 1
                    col_defs = find_child_by_type(node, "column_definitions")
                    if col_defs:
                        refs = _find_references_in_columns(col_defs, source)
                        for ref_table in refs:
                            lookup_result = resolver.lookup(ref_table.lower())
                            if lookup_result.found and lookup_result.symbol:
                                dst_id = lookup_result.symbol.id
                                confidence = 0.90 * lookup_result.confidence
                                edge = Edge.create(
                                    src=table_sym.id,
                                    dst=dst_id,
                                    edge_type="references",
                                    line=start_line,
                                    confidence=confidence,
                                    origin=PASS_ID,
                                    evidence_type="sql_foreign_key",
                                )
                                edges.append(edge)


class SqlAnalyzer(TreeSitterAnalyzer):
    """SQL language analyzer using tree-sitter-sql."""

    lang = "sql"
    file_patterns: ClassVar[list[str]] = ["*.sql"]
    grammar_module = "tree_sitter_sql"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract SQL symbols (tables, views, functions, triggers, indexes)."""
        analysis = FileAnalysis()
        symbol_registry: dict[str, Symbol] = {}
        _extract_sql_symbols(
            tree.root_node, source, rel_path,
            analysis.symbols, symbol_registry,
        )
        analysis.symbol_by_name.update(symbol_registry)
        return analysis

    def register_symbol(
        self, symbol: Symbol, global_symbols: dict,
    ) -> None:
        """Register symbol by lowercase name for case-insensitive SQL matching."""
        global_symbols[symbol.name.lower()] = symbol

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract foreign key reference edges from SQL."""
        file_symbols = [s for s in local_symbols.values() if s.path == rel_path]
        edges: list[Edge] = []
        _extract_sql_edges(
            tree.root_node, source, rel_path,
            file_symbols, edges, resolver,
        )
        return edges


_analyzer = SqlAnalyzer()


def is_sql_tree_sitter_available() -> bool:
    """Check if tree-sitter with SQL grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("sql")
def analyze_sql_files(repo_root: Path) -> AnalysisResult:
    """Analyze SQL files in a repository."""
    return _analyzer.analyze(repo_root)
