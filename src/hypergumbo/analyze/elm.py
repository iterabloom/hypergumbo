"""Elm analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse Elm files and extract:
- Module definitions (module_declaration)
- Function/value definitions (value_declaration)
- Type alias definitions (type_alias_declaration)
- Custom type definitions (type_declaration)
- Port declarations (port_annotation)
- Import statements (import_clause)
- Function call relationships

If tree-sitter with Elm support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-language-pack (elm) is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and import statements

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for grammar (elm)
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency

Elm-Specific Considerations
---------------------------
- Elm is a functional language for web frontends
- Functions are defined with type annotations and value declarations
- Custom types (union types) are like enums with associated data
- Type aliases provide named record types
- Ports enable JavaScript interop
- All functions are pure; side effects through Cmd messages
"""
from __future__ import annotations

import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from .base import iter_tree
from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "elm-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_elm_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Elm files in the repository."""
    yield from find_files(repo_root, ["*.elm"])


def is_elm_tree_sitter_available() -> bool:
    """Check if tree-sitter with Elm grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_language_pack") is None:
        return False  # pragma: no cover - language pack not installed
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("elm")
        return True
    except Exception:  # pragma: no cover - elm not supported
        return False


@dataclass
class ElmAnalysisResult:
    """Result of analyzing Elm files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file.

    Stored during pass 1 and processed in pass 2 for cross-file resolution.
    """

    path: str
    source: bytes
    tree: object  # tree_sitter.Tree
    symbols: list[Symbol]
    module_name: str  # Elm module name from module declaration


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"elm:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for an Elm file node (used as import edge source)."""
    return f"elm:{path}:1-1:file:file"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None  # pragma: no cover - defensive fallback


def _extract_elm_signature(
    decl_left: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a function_declaration_left node.

    Returns signature in format: (param1, param2)
    Elm function parameters follow the function name.
    """
    params: list[str] = []

    # Skip the first child (function name) and collect parameters
    found_name = False
    for child in decl_left.children:
        if child.type == "lower_case_identifier":
            if not found_name:
                found_name = True
                continue
            # Additional lower_case_identifiers are parameters
            params.append(_node_text(child, source))  # pragma: no cover - params are lower_pattern
        elif child.type == "pattern":  # pragma: no cover - complex patterns
            # Pattern matching in parameters
            params.append(_node_text(child, source))
        elif child.type == "lower_pattern":
            # Simple parameter pattern
            params.append(_node_text(child, source))

    return f"({', '.join(params)})"


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> tuple[list[Symbol], str]:
    """Extract all symbols from a parsed Elm file.

    Returns (symbols, module_name).

    Detects:
    - module_declaration (module name)
    - value_declaration (functions/values)
    - type_alias_declaration (type aliases)
    - type_declaration (custom types)
    - port_annotation (ports)
    """
    symbols: list[Symbol] = []
    module_name = ""

    for node in tree.root_node.children:
        # Module declaration
        if node.type == "module_declaration":
            qid = _find_child_by_type(node, "upper_case_qid")
            if qid:
                module_name = _node_text(qid, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                span = Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                sym_id = _make_symbol_id(file_path, start_line, end_line, module_name, "module")
                symbols.append(Symbol(
                    id=sym_id,
                    name=module_name,
                    kind="module",
                    language="elm",
                    path=file_path,
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

        # Value/function declaration
        elif node.type == "value_declaration":
            decl_left = _find_child_by_type(node, "function_declaration_left")
            if decl_left:
                name_node = _find_child_by_type(decl_left, "lower_case_identifier")
                if name_node:
                    func_name = _node_text(name_node, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    span = Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    )
                    sym_id = _make_symbol_id(file_path, start_line, end_line, func_name, "function")
                    symbols.append(Symbol(
                        id=sym_id,
                        name=func_name,
                        kind="function",
                        language="elm",
                        path=file_path,
                        span=span,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        signature=_extract_elm_signature(decl_left, source),
                    ))

        # Type alias
        elif node.type == "type_alias_declaration":
            name_node = _find_child_by_type(node, "upper_case_identifier")
            if name_node:
                type_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                span = Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                sym_id = _make_symbol_id(file_path, start_line, end_line, type_name, "type")
                symbols.append(Symbol(
                    id=sym_id,
                    name=type_name,
                    kind="type",
                    language="elm",
                    path=file_path,
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

        # Custom type (union type)
        elif node.type == "type_declaration":
            name_node = _find_child_by_type(node, "upper_case_identifier")
            if name_node:
                type_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                span = Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                sym_id = _make_symbol_id(file_path, start_line, end_line, type_name, "type")
                symbols.append(Symbol(
                    id=sym_id,
                    name=type_name,
                    kind="type",
                    language="elm",
                    path=file_path,
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

        # Port declaration
        elif node.type == "port_annotation":
            name_node = _find_child_by_type(node, "lower_case_identifier")
            if name_node:
                port_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                span = Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                sym_id = _make_symbol_id(file_path, start_line, end_line, port_name, "port")
                symbols.append(Symbol(
                    id=sym_id,
                    name=port_name,
                    kind="port",
                    language="elm",
                    path=file_path,
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

    return symbols, module_name


def _get_enclosing_function(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Symbol | None:
    """Walk up parent chain to find enclosing function."""
    current = node.parent
    while current is not None:
        if current.type == "value_declaration":
            decl_left = _find_child_by_type(current, "function_declaration_left")
            if decl_left:
                name_node = _find_child_by_type(decl_left, "lower_case_identifier")
                if name_node:
                    func_name = _node_text(name_node, source)
                    sym = local_symbols.get(func_name)
                    if sym:
                        return sym
        current = current.parent
    return None  # pragma: no cover - no enclosing function found


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    global_symbol_registry: dict[str, Symbol],
    run_id: str,
) -> list[Edge]:
    """Extract call and import edges from a parsed Elm file.

    Detects:
    - Function calls (function_call_expr, value_expr)
    - Import statements (import_clause)
    """
    edges: list[Edge] = []
    file_id = _make_file_id(file_path)

    # Build local symbol map (name -> symbol)
    local_symbols = {s.name: s for s in file_symbols}

    for node in iter_tree(tree.root_node):
        if node.type == "import_clause":
            # import Module [exposing (...)]
            qid = _find_child_by_type(node, "upper_case_qid")
            if qid:
                module_name = _node_text(qid, source)
                module_id = f"elm:{module_name}:0-0:module:module"
                edge = Edge.create(
                    src=file_id,
                    dst=module_id,
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    evidence_type="import",
                    confidence=0.95,
                )
                edges.append(edge)

        elif node.type == "function_call_expr":
            # Function application
            caller = _get_enclosing_function(node, source, local_symbols)
            if caller:
                # First child of function_call_expr is the function being called
                value_expr = _find_child_by_type(node, "value_expr")
                if value_expr:
                    value_qid = _find_child_by_type(value_expr, "value_qid")
                    if value_qid:
                        callee_name_node = _find_child_by_type(value_qid, "lower_case_identifier")
                        if callee_name_node:
                            callee_name = _node_text(callee_name_node, source)
                            callee = local_symbols.get(callee_name) or global_symbol_registry.get(callee_name)
                            if callee:
                                edge = Edge.create(
                                    src=caller.id,
                                    dst=callee.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                    evidence_type="function_call",
                                    confidence=0.85,
                                )
                                edges.append(edge)

    return edges


def analyze_elm(repo_root: Path) -> ElmAnalysisResult:
    """Analyze Elm files in a repository.

    Returns an ElmAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-language-pack is not available, returns a skipped result.
    """
    start_time = time.time()

    # Create analysis run for provenance
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    if not is_elm_tree_sitter_available():  # pragma: no cover - tested via mock
        skip_reason = (
            "Elm analysis skipped: requires tree-sitter-language-pack "
            "(pip install tree-sitter-language-pack)"
        )
        warnings.warn(skip_reason)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return ElmAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=skip_reason,
        )

    from tree_sitter_language_pack import get_parser

    parser = get_parser("elm")
    run_id = run.execution_id

    # Pass 1: Parse all files and extract symbols
    file_analyses: list[FileAnalysis] = []
    all_symbols: list[Symbol] = []
    global_symbol_registry: dict[str, Symbol] = {}
    files_analyzed = 0

    for elm_file in find_elm_files(repo_root):
        try:
            source = elm_file.read_bytes()
        except OSError:  # pragma: no cover
            continue

        tree = parser.parse(source)
        if tree.root_node is None:  # pragma: no cover - parser always returns root
            continue

        rel_path = str(elm_file.relative_to(repo_root))

        # Create file symbol
        file_symbol = Symbol(
            id=_make_file_id(rel_path),
            name="file",
            kind="file",
            language="elm",
            path=rel_path,
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin=PASS_ID,
            origin_run_id=run_id,
        )
        all_symbols.append(file_symbol)

        # Extract symbols
        file_symbols, module_name = _extract_symbols_from_file(tree, source, rel_path, run_id)
        all_symbols.extend(file_symbols)

        # Register symbols globally (for cross-file resolution)
        for sym in file_symbols:
            global_symbol_registry[sym.name] = sym

        file_analyses.append(FileAnalysis(
            path=rel_path,
            source=source,
            tree=tree,
            symbols=file_symbols,
            module_name=module_name,
        ))
        files_analyzed += 1

    # Pass 2: Extract edges with cross-file resolution
    all_edges: list[Edge] = []

    for fa in file_analyses:
        edges = _extract_edges_from_file(
            fa.tree,  # type: ignore
            fa.source,
            fa.path,
            fa.symbols,
            global_symbol_registry,
            run_id,
        )
        all_edges.extend(edges)

    run.files_analyzed = files_analyzed
    run.duration_ms = int((time.time() - start_time) * 1000)

    return ElmAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
