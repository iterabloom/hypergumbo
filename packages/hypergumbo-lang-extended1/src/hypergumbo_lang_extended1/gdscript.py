"""GDScript (Godot) analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse GDScript files and extract:
- Function definitions (_ready, _process, custom methods)
- Variable declarations (class members)
- Signal declarations
- Class names and inner classes
- Function calls
- Preload/load imports

If tree-sitter with GDScript support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter with GDScript grammar is available
2. If not available, return skipped result (not an error)
3. Parse all .gd files and extract symbols
4. Detect preload/load calls for import edges
5. Detect function calls for call edges

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for GDScript grammar
- GDScript is essential for Godot game development
- Enables analysis of game code for refactoring and understanding

GDScript-Specific Considerations
--------------------------------
- Scripts typically extend a base class (Node2D, CharacterBody2D)
- class_name declares a global script name
- Signals are event-like declarations
- Functions starting with _ are lifecycle callbacks
- preload() is compile-time import, load() is runtime
"""
from __future__ import annotations

import importlib.util
import time
import uuid
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

PASS_ID = "gdscript-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_gdscript_files(repo_root: Path) -> Iterator[Path]:
    """Yield all GDScript files in the repository."""
    yield from find_files(repo_root, ["*.gd"])


def is_gdscript_tree_sitter_available() -> bool:
    """Check if tree-sitter with GDScript grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_language_pack") is None:
        return False  # pragma: no cover - language pack not installed
    try:
        from tree_sitter_language_pack import get_language
        get_language("gdscript")
        return True
    except Exception:  # pragma: no cover - gdscript grammar not available
        return False


@dataclass
class GDScriptAnalysisResult:
    """Result of analyzing GDScript files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"gdscript:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a GDScript file node (used as import edge source)."""
    return f"gdscript:{path}:1-1:file:file"


def _make_edge_id() -> str:
    """Generate a unique edge ID."""
    return f"edge:gdscript:{uuid.uuid4().hex[:12]}"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None  # pragma: no cover - defensive


def _extract_function_signature(func_node: "tree_sitter.Node", source: bytes) -> str:
    """Extract function signature showing parameters and return type.

    GDScript function syntax:
        func name(param1: Type1, param2: Type2) -> ReturnType:

    Returns signature like "(amount: int, source: Node) -> int".
    """
    params_parts: list[str] = []
    return_type: Optional[str] = None

    # Find parameters
    params_node = _find_child_by_type(func_node, "parameters")
    if params_node:
        for child in params_node.children:
            if child.type == "typed_parameter":
                # Extract identifier and type
                ident = _find_child_by_type(child, "identifier")
                type_node = _find_child_by_type(child, "type")
                if ident:
                    param_str = _node_text(ident, source).strip()
                    if type_node:
                        type_str = _node_text(type_node, source).strip()
                        param_str = f"{param_str}: {type_str}"
                    params_parts.append(param_str)
            elif child.type == "identifier":
                # Untyped parameter
                params_parts.append(_node_text(child, source).strip())

    # Find return type (type node after ->)
    found_arrow = False
    for child in func_node.children:
        if child.type == "->":
            found_arrow = True
        elif found_arrow and child.type == "type":
            return_type = _node_text(child, source).strip()
            break

    sig = f"({', '.join(params_parts)})"
    if return_type:
        sig += f" -> {return_type}"
    return sig


def _get_enclosing_function_gdscript(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up parent chain to find enclosing function Symbol."""
    current = node.parent
    while current is not None:
        if current.type == "function_definition":
            name_node = _find_child_by_type(current, "name")
            if name_node:
                func_name = _node_text(name_node, source).strip()
                return local_symbols.get(func_name)
        current = current.parent
    return None  # pragma: no cover - no enclosing function found


def _make_gd_symbol(
    node: "tree_sitter.Node",
    name: str,
    kind: str,
    file_path: str,
    run_id: str,
    signature: Optional[str] = None,
) -> Symbol:
    """Create a Symbol from a tree-sitter node."""
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    start_col = node.start_point[1]
    end_col = node.end_point[1]

    span = Span(
        start_line=start_line,
        end_line=end_line,
        start_col=start_col,
        end_col=end_col,
    )
    sym_id = _make_symbol_id(file_path, start_line, end_line, name, kind)
    return Symbol(
        id=sym_id,
        name=name,
        canonical_name=name,
        kind=kind,
        language="gdscript",
        path=file_path,
        span=span,
        origin=PASS_ID,
        origin_run_id=run_id,
        signature=signature,
    )


def _extract_gdscript_symbols(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
    local_symbols: dict[str, Symbol],
) -> list[Symbol]:
    """Extract symbols from a parsed GDScript file (pass 1)."""
    symbols: list[Symbol] = []

    for node in iter_tree(tree.root_node):
        if node.type == "function_definition":
            name_node = _find_child_by_type(node, "name")
            if name_node:
                func_name = _node_text(name_node, source).strip()
                sig = _extract_function_signature(node, source)
                sym = _make_gd_symbol(node, func_name, "function", file_path, run_id, signature=sig)
                symbols.append(sym)
                local_symbols[func_name] = sym

        elif node.type == "variable_statement":
            name_node = _find_child_by_type(node, "name")
            if name_node:
                var_name = _node_text(name_node, source).strip()
                symbols.append(_make_gd_symbol(node, var_name, "variable", file_path, run_id))

        elif node.type == "signal_statement":
            name_node = _find_child_by_type(node, "name")
            if name_node:
                signal_name = _node_text(name_node, source).strip()
                symbols.append(_make_gd_symbol(node, signal_name, "signal", file_path, run_id))

        elif node.type == "class_name_statement":
            name_node = _find_child_by_type(node, "name")
            if name_node:
                class_name = _node_text(name_node, source).strip()
                symbols.append(_make_gd_symbol(node, class_name, "class", file_path, run_id))

        elif node.type == "class_definition":
            name_node = _find_child_by_type(node, "name")
            if name_node:
                class_name = _node_text(name_node, source).strip()
                symbols.append(_make_gd_symbol(node, class_name, "class", file_path, run_id))

    return symbols


def _extract_gdscript_edges(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    local_symbols: dict[str, Symbol],
    resolver: NameResolver,
) -> list[Edge]:
    """Extract edges from a parsed GDScript file (pass 2)."""
    edges: list[Edge] = []

    for node in iter_tree(tree.root_node):
        if node.type == "call":
            ident_node = _find_child_by_type(node, "identifier")
            if ident_node:
                called_name = _node_text(ident_node, source).strip()

                # Check for preload/load imports
                if called_name in ("preload", "load"):
                    args_node = _find_child_by_type(node, "arguments")
                    if args_node:
                        for arg_child in args_node.children:
                            if arg_child.type == "string":
                                path_str = _node_text(arg_child, source).strip().strip('"\'')
                                edges.append(Edge(
                                    id=_make_edge_id(),
                                    src=_make_file_id(file_path),
                                    dst=f"gdscript:{path_str}:file",
                                    edge_type="imports",
                                    line=node.start_point[0] + 1,
                                    confidence=0.9,
                                    origin=PASS_ID,
                                ))
                                break
                else:
                    # Check if inside a function for call edge
                    caller = _get_enclosing_function_gdscript(node, source, local_symbols)
                    if caller:
                        # Skip built-in functions like print
                        if called_name not in ("print", "push_error", "push_warning", "printerr"):
                            # Try to resolve the callee
                            result = resolver.lookup(called_name)
                            if result.symbol is not None:
                                dst_id = result.symbol.id
                                confidence = 0.85 * result.confidence
                            else:
                                dst_id = f"gdscript:external:{called_name}:function"
                                confidence = 0.70

                            edges.append(Edge(
                                id=_make_edge_id(),
                                src=caller.id,
                                dst=dst_id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                confidence=confidence,
                                origin=PASS_ID,
                            ))

    return edges


def analyze_gdscript(repo_root: Path) -> GDScriptAnalysisResult:
    """Analyze all GDScript files in the repository.

    Uses two-pass analysis for cross-file call resolution.

    Args:
        repo_root: Path to the repository root.

    Returns:
        GDScriptAnalysisResult with symbols and edges found.
    """
    if not is_gdscript_tree_sitter_available():
        warnings.warn("GDScript analysis skipped: tree-sitter-language-pack not available")
        return GDScriptAnalysisResult(skipped=True, skip_reason="tree-sitter-language-pack not available")

    from tree_sitter_language_pack import get_parser

    parser = get_parser("gdscript")
    run_id = f"uuid:{uuid.uuid4()}"
    start_time = time.time()
    files_analyzed = 0

    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []

    # Collect file data for two-pass processing
    file_data: list[tuple[Path, bytes, object]] = []
    file_local_symbols: dict[str, dict[str, Symbol]] = {}

    # Pass 1: Extract all symbols from all files
    for file_path in find_gdscript_files(repo_root):
        try:
            source = file_path.read_bytes()
            tree = parser.parse(source)
            files_analyzed += 1
            file_data.append((file_path, source, tree))

            rel_path = str(file_path.relative_to(repo_root))
            local_symbols: dict[str, Symbol] = {}

            symbols = _extract_gdscript_symbols(tree, source, rel_path, run_id, local_symbols)
            all_symbols.extend(symbols)
            file_local_symbols[rel_path] = local_symbols

        except (OSError, IOError):  # pragma: no cover - defensive
            continue  # Skip files we can't read

    # Build resolver from all function symbols
    global_symbols: dict[str, Symbol] = {}
    for sym in all_symbols:
        if sym.kind == "function":
            global_symbols[sym.name] = sym
    resolver = NameResolver(global_symbols)

    # Pass 2: Extract edges using resolver
    for file_path, source, tree in file_data:
        rel_path = str(file_path.relative_to(repo_root))
        local_symbols = file_local_symbols.get(rel_path, {})

        edges = _extract_gdscript_edges(tree, source, rel_path, local_symbols, resolver)
        all_edges.extend(edges)

    duration_ms = int((time.time() - start_time) * 1000)

    run = AnalysisRun(
        execution_id=run_id,
        pass_id=PASS_ID,
        version=PASS_VERSION,
        files_analyzed=files_analyzed,
        duration_ms=duration_ms,
    )

    return GDScriptAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
