"""Lua analysis pass using tree-sitter-lua.

This analyzer uses tree-sitter to parse Lua files and extract:
- Function declarations (global and local)
- Method-style function definitions (Table:method)
- Function call relationships
- require statements (imports)

If tree-sitter with Lua support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-lua is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and require statements

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-lua package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency

Lua-Specific Considerations
---------------------------
- Lua has both global functions (`function foo()`) and local functions
  (`local function foo()`)
- Method-style definitions (`Table:method`) are common for OOP patterns
- require() is the standard import mechanism
- Lua is dynamically typed, so call resolution is based on name matching
"""
from __future__ import annotations

import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol
from .base import iter_tree

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "lua-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_lua_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Lua files in the repository."""
    yield from find_files(repo_root, ["*.lua"])


def is_lua_tree_sitter_available() -> bool:
    """Check if tree-sitter with Lua grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_lua") is None:
        return False  # pragma: no cover - tree-sitter-lua not installed
    return True


@dataclass
class LuaAnalysisResult:
    """Result of analyzing Lua files."""

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


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"lua:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Lua file node (used as import edge source)."""
    return f"lua:{path}:1-1:file:file"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _extract_lua_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a function_declaration.

    Returns signature in format: (param1, param2, ...)
    Lua is dynamically typed, so no type annotations.
    """
    params_node = _find_child_by_type(node, "parameters")
    if params_node is None:  # pragma: no cover - defensive
        return "()"

    params: list[str] = []
    for child in params_node.children:
        if child.type == "identifier":
            params.append(_node_text(child, source))
        elif child.type == "spread":  # pragma: no cover - rare varargs
            params.append("...")

    return f"({', '.join(params)})"


def _get_function_name(
    node: "tree_sitter.Node",
    source: bytes,
) -> tuple[str, str]:
    """Extract function name and kind from function_declaration.

    Returns:
        Tuple of (name, kind) where kind is "function" or "method"
    """
    # Look for direct identifier (global/local function)
    name_node = _find_child_by_type(node, "identifier")
    if name_node:
        return _node_text(name_node, source), "function"

    # Look for method_index_expression (Table:method)
    method_expr = _find_child_by_type(node, "method_index_expression")
    if method_expr:
        # Extract Table and method name
        table_id = None
        method_id = None
        for child in method_expr.children:
            if child.type == "identifier":
                if table_id is None:
                    table_id = _node_text(child, source)
                else:
                    method_id = _node_text(child, source)
        if table_id and method_id:
            return f"{table_id}.{method_id}", "method"

    return "", "function"  # pragma: no cover - fallback for unparseable functions


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> list[Symbol]:
    """Extract all symbols from a parsed Lua file.

    Detects:
    - function_declaration (both global and local)
    - Method-style functions (Table:method)
    """
    symbols: list[Symbol] = []

    for node in iter_tree(tree.root_node):
        if node.type == "function_declaration":
            name, kind = _get_function_name(node, source)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                span = Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                sym_id = _make_symbol_id(file_path, start_line, end_line, name, kind)
                symbols.append(Symbol(
                    id=sym_id,
                    name=name,
                    kind=kind,
                    language="lua",
                    path=file_path,
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    signature=_extract_lua_signature(node, source),
                ))

    return symbols


def _find_enclosing_lua_function(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
    global_symbol_registry: dict[str, Symbol],
) -> Optional[Symbol]:
    """Find the function that contains this node by walking up the parent chain."""
    current = node.parent
    while current:
        if current.type == "function_declaration":
            name, _ = _get_function_name(current, source)
            # Check local symbols first, then global
            if name in local_symbols:
                return local_symbols[name]
            if name in global_symbol_registry:  # pragma: no cover - cross-file case
                return global_symbol_registry[name]  # pragma: no cover
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    global_symbol_registry: dict[str, Symbol],
    run_id: str,
) -> list[Edge]:
    """Extract call and import edges from a parsed Lua file.

    Detects:
    - function_call: Direct function calls
    - Method calls (obj:method())
    - require statements
    """
    edges: list[Edge] = []
    file_id = _make_file_id(file_path)

    # Build local symbol map for this file (name -> symbol)
    local_symbols = {s.name: s for s in file_symbols}

    for node in iter_tree(tree.root_node):
        if node.type == "function_call":
            # Extract function name being called
            callee_name = None

            # Direct call: identifier(args)
            first_child = node.children[0] if node.children else None
            if first_child:
                if first_child.type == "identifier":
                    callee_name = _node_text(first_child, source)
                elif first_child.type == "method_index_expression":
                    # Method call: obj:method(args)
                    # Get the method name (last identifier)
                    for child in first_child.children:
                        if child.type == "identifier":
                            callee_name = _node_text(child, source)

            # Check for require() call - special handling for imports
            if callee_name == "require":
                # Find the argument (module name)
                args_node = _find_child_by_type(node, "arguments")
                if args_node:
                    for child in args_node.children:
                        if child.type == "string":
                            # Extract string content
                            content_node = _find_child_by_type(child, "string_content")
                            if content_node:
                                module_name = _node_text(content_node, source)
                                # Create import edge
                                module_id = f"lua:{module_name}:0-0:module:module"
                                edge = Edge.create(
                                    src=file_id,
                                    dst=module_id,
                                    edge_type="imports",
                                    line=node.start_point[0] + 1,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                    evidence_type="require",
                                    confidence=0.95,
                                )
                                edges.append(edge)
            elif callee_name:
                # Regular function call
                # Find the caller (enclosing function)
                caller = _find_enclosing_lua_function(node, source, local_symbols, global_symbol_registry)
                if caller:
                    # Try to resolve callee
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
                    else:
                        # Unresolved call - create edge to unknown target
                        unresolved_id = f"lua:?:0-0:{callee_name}:function"
                        edge = Edge.create(
                            src=caller.id,
                            dst=unresolved_id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                            evidence_type="function_call",
                            confidence=0.50,
                        )
                        edges.append(edge)

    return edges


def analyze_lua(repo_root: Path) -> LuaAnalysisResult:
    """Analyze Lua files in a repository.

    Returns a LuaAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-lua is not available, returns a skipped result.
    """
    start_time = time.time()

    # Create analysis run for provenance
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    if not is_lua_tree_sitter_available():
        skip_reason = (
            "Lua analysis skipped: requires tree-sitter-lua "
            "(pip install tree-sitter-lua)"
        )
        warnings.warn(skip_reason)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return LuaAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=skip_reason,
        )

    import tree_sitter
    import tree_sitter_lua

    LUA_LANGUAGE = tree_sitter.Language(tree_sitter_lua.language())
    parser = tree_sitter.Parser(LUA_LANGUAGE)
    run_id = run.execution_id

    # Pass 1: Parse all files and extract symbols
    file_analyses: list[FileAnalysis] = []
    all_symbols: list[Symbol] = []
    global_symbol_registry: dict[str, Symbol] = {}
    files_analyzed = 0

    for lua_file in find_lua_files(repo_root):
        try:
            source = lua_file.read_bytes()
        except OSError:  # pragma: no cover
            continue

        tree = parser.parse(source)
        if tree.root_node is None:  # pragma: no cover - parser always returns root
            continue

        rel_path = str(lua_file.relative_to(repo_root))

        # Create file symbol
        file_symbol = Symbol(
            id=_make_file_id(rel_path),
            name="file",
            kind="file",
            language="lua",
            path=rel_path,
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin=PASS_ID,
            origin_run_id=run_id,
        )
        all_symbols.append(file_symbol)

        # Extract symbols
        file_symbols = _extract_symbols_from_file(tree, source, rel_path, run_id)
        all_symbols.extend(file_symbols)

        # Register symbols globally (for cross-file resolution)
        for sym in file_symbols:
            global_symbol_registry[sym.name] = sym

        file_analyses.append(FileAnalysis(
            path=rel_path,
            source=source,
            tree=tree,
            symbols=file_symbols,
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

    return LuaAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
