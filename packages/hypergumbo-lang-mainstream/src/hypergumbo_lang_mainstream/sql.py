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
1. Check if tree-sitter-sql is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect foreign key references and resolve against registry
4. Detect relationships between tables

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-sql package for grammar
- Two-pass allows cross-file reference resolution
- SQL-specific: tables, views, functions, triggers are first-class symbols
"""
from __future__ import annotations

import hashlib
import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from hypergumbo_core.analyze.base import iter_tree
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.symbol_resolution import NameResolver

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "sql-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_sql_files(repo_root: Path) -> Iterator[Path]:
    """Yield all SQL files in the repository."""
    yield from find_files(repo_root, ["*.sql"])


def is_sql_tree_sitter_available() -> bool:
    """Check if tree-sitter with SQL grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover
    if importlib.util.find_spec("tree_sitter_sql") is None:
        return False  # pragma: no cover
    return True


@dataclass
class SQLAnalysisResult:
    """Result of analyzing SQL files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"sql:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:  # pragma: no cover
    """Generate ID for a SQL file node (used as edge source)."""
    return f"sql:{path}:1-1:file:file"  # pragma: no cover


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None  # pragma: no cover


def _find_children_by_type(node: "tree_sitter.Node", type_name: str) -> list["tree_sitter.Node"]:  # pragma: no cover
    """Find all children of given type."""
    return [child for child in node.children if child.type == type_name]  # pragma: no cover


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _extract_table_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract table name from a CREATE TABLE node."""
    obj_ref = _find_child_by_type(node, "object_reference")
    if obj_ref:
        return _node_text(obj_ref, source)
    # Try identifier as fallback
    ident = _find_child_by_type(node, "identifier")  # pragma: no cover
    if ident:  # pragma: no cover
        return _node_text(ident, source)  # pragma: no cover
    return None  # pragma: no cover


def _extract_view_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract view name from a CREATE VIEW node."""
    obj_ref = _find_child_by_type(node, "object_reference")
    if obj_ref:
        return _node_text(obj_ref, source)
    return None  # pragma: no cover


def _extract_function_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function name from a CREATE FUNCTION node."""
    obj_ref = _find_child_by_type(node, "object_reference")
    if obj_ref:
        return _node_text(obj_ref, source)
    return None  # pragma: no cover


def _extract_sql_signature(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function signature from a CREATE FUNCTION node.

    Returns signature in format: (param_name TYPE, ...) RETURNS return_type
    SQL functions have typed parameters.
    """
    params: list[str] = []
    return_type: Optional[str] = None

    # Find function_arguments (parameter list)
    func_args = _find_child_by_type(node, "function_arguments")
    if func_args:
        for child in func_args.children:
            if child.type == "function_argument":
                # Extract parameter text (name TYPE)
                param_text = _node_text(child, source).strip()
                params.append(param_text)

    # Find return type (after RETURNS keyword)
    found_returns = False
    for child in node.children:
        if child.type == "keyword_returns":
            found_returns = True
        elif found_returns and child.type not in ("keyword_returns",):
            # This should be the return type (decimal, int, varchar, etc.)
            if child.type in ("decimal", "int", "varchar", "text", "boolean",
                              "float", "double", "bigint", "smallint", "real",
                              "numeric", "char", "timestamp", "date", "time",
                              "identifier", "type_identifier"):
                return_type = _node_text(child, source)
                break
            # Also check for complex types
            if child.type not in ("function_body", "function_language"):
                return_type = _node_text(child, source)
                break

    params_str = ", ".join(params) if params else ""
    signature = f"({params_str})"
    if return_type:
        signature += f" RETURNS {return_type}"

    return signature


def _extract_trigger_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract trigger name from a CREATE TRIGGER node."""
    # First object_reference is typically the trigger name
    for child in node.children:
        if child.type == "object_reference":
            return _node_text(child, source)
    return None  # pragma: no cover


def _extract_index_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract index name from a CREATE INDEX node."""
    ident = _find_child_by_type(node, "identifier")
    if ident:
        return _node_text(ident, source)
    # Try object_reference as fallback
    obj_ref = _find_child_by_type(node, "object_reference")  # pragma: no cover
    if obj_ref:  # pragma: no cover
        return _node_text(obj_ref, source)  # pragma: no cover
    return None  # pragma: no cover


def _extract_procedure_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract procedure name from a CREATE PROCEDURE node (dialect-specific)."""
    obj_ref = _find_child_by_type(node, "object_reference")  # pragma: no cover
    if obj_ref:  # pragma: no cover
        return _node_text(obj_ref, source)  # pragma: no cover
    ident = _find_child_by_type(node, "identifier")  # pragma: no cover
    if ident:  # pragma: no cover
        return _node_text(ident, source)  # pragma: no cover
    return None  # pragma: no cover


def _find_references_in_columns(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Find REFERENCES clauses in column definitions."""
    references: list[str] = []

    for n in iter_tree(node):
        # Look for column_definition nodes that contain keyword_references
        if n.type == "column_definition":
            has_references = False
            for child in n.children:
                if child.type == "keyword_references":
                    has_references = True
                elif has_references and child.type == "object_reference":
                    # This is the referenced table
                    ref_text = _node_text(child, source)
                    if ref_text and ref_text not in references:
                        references.append(ref_text)
                    has_references = False  # Found our reference

        # Also check for foreign_key constraint (alternative syntax)
        if n.type == "object_reference" and n.parent:  # pragma: no cover
            # Check if parent context suggests this is a foreign key reference
            parent_text = _node_text(n.parent, source).upper()  # pragma: no cover
            if "REFERENCES" in parent_text:  # pragma: no cover
                ref_name = _node_text(n, source)  # pragma: no cover
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
    """Extract symbols from SQL AST tree (pass 1).

    Args:
        root_node: Root tree-sitter node to process
        source: Source file bytes
        rel_path: Relative path to file
        symbols: List to append symbols to
        symbol_registry: Registry mapping lowercase names to Symbol objects
    """
    for node in iter_tree(root_node):
        if node.type == "create_table":
            name = _extract_table_name(node, source)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "table")
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
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "view")
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
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "function")
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

        # Note: CREATE PROCEDURE syntax varies by dialect and may not be
        # supported by the tree-sitter-sql grammar in all cases
        elif node.type == "create_procedure":  # pragma: no cover
            name = _extract_procedure_name(node, source)  # pragma: no cover
            if name:  # pragma: no cover
                start_line = node.start_point[0] + 1  # pragma: no cover
                end_line = node.end_point[0] + 1  # pragma: no cover
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "procedure")  # pragma: no cover
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
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "trigger")
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
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "index")
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
    resolver: NameResolver,
) -> None:
    """Extract edges from SQL AST tree (pass 2).

    Uses NameResolver for reference resolution to enable cross-file symbol lookup.

    Args:
        root_node: Root tree-sitter node to process
        source: Source file bytes
        rel_path: Relative path to file
        symbols: List of symbols (to find table symbol IDs)
        edges: List to append edges to
        resolver: NameResolver for reference resolution
    """
    for node in iter_tree(root_node):
        if node.type == "create_table":
            name = _extract_table_name(node, source)
            if name:
                # Find the symbol ID for this table
                table_sym = None
                for sym in symbols:
                    if sym.name == name and sym.kind == "table" and sym.path == rel_path:
                        table_sym = sym
                        break

                if table_sym:
                    start_line = node.start_point[0] + 1
                    # Look for REFERENCES in column definitions
                    col_defs = _find_child_by_type(node, "column_definitions")
                    if col_defs:
                        refs = _find_references_in_columns(col_defs, source)
                        for ref_table in refs:
                            # Use resolver for reference resolution
                            lookup_result = resolver.lookup(ref_table.lower())
                            if lookup_result.found and lookup_result.symbol:
                                dst_id = lookup_result.symbol.id
                                confidence = 0.90 * lookup_result.confidence
                                edge = Edge(
                                    id=_make_edge_id(table_sym.id, dst_id, "references"),
                                    src=table_sym.id,
                                    dst=dst_id,
                                    edge_type="references",
                                    line=start_line,
                                    confidence=confidence,
                                    origin=PASS_ID,
                                    evidence_type="sql_foreign_key",
                                )
                                edges.append(edge)


def analyze_sql_files(repo_root: Path) -> SQLAnalysisResult:
    """Analyze SQL files in the repository.

    Uses two-pass analysis:
    - Pass 1: Extract all symbols from all files
    - Pass 2: Extract edges using NameResolver for cross-file resolution

    Args:
        repo_root: Path to the repository root

    Returns:
        SQLAnalysisResult with symbols and edges
    """
    if not is_sql_tree_sitter_available():  # pragma: no cover
        return SQLAnalysisResult(  # pragma: no cover
            skipped=True,  # pragma: no cover
            skip_reason="tree-sitter-sql not installed (pip install tree-sitter-sql)",  # pragma: no cover
        )  # pragma: no cover

    import tree_sitter
    import tree_sitter_sql

    start_time = time.time()
    files_analyzed = 0
    files_skipped = 0
    warnings_list: list[str] = []

    symbols: list[Symbol] = []
    edges: list[Edge] = []

    # Global symbol registry for cross-file resolution: name -> Symbol
    global_symbol_registry: dict[str, Symbol] = {}

    # Create parser
    try:
        parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_sql.language()))
    except Exception as e:  # pragma: no cover
        warnings.warn(f"Failed to initialize SQL parser: {e}")
        return SQLAnalysisResult(
            skipped=True,
            skip_reason=f"Failed to initialize parser: {e}",
        )

    sql_files = list(find_sql_files(repo_root))

    # Store parsed trees for pass 2
    parsed_files: list[tuple[str, bytes, object]] = []

    # Pass 1: Extract all symbols
    for sql_path in sql_files:
        try:
            rel_path = str(sql_path.relative_to(repo_root))
            source = sql_path.read_bytes()
            tree = parser.parse(source)
            files_analyzed += 1

            # Extract symbols
            _extract_sql_symbols(
                tree.root_node,
                source,
                rel_path,
                symbols,
                global_symbol_registry,
            )

            # Store for pass 2
            parsed_files.append((rel_path, source, tree))

        except Exception as e:  # pragma: no cover
            files_skipped += 1  # pragma: no cover
            warnings_list.append(f"Failed to parse {sql_path}: {e}")  # pragma: no cover

    # Create resolver from global registry
    resolver = NameResolver(global_symbol_registry)

    # Pass 2: Extract edges using resolver
    for rel_path, source, tree in parsed_files:
        _extract_sql_edges(
            tree.root_node,  # type: ignore
            source,
            rel_path,
            symbols,
            edges,
            resolver,
        )

    duration_ms = int((time.time() - start_time) * 1000)

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = duration_ms
    run.warnings = warnings_list

    return SQLAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=run,
    )
