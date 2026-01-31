"""Nim language analysis pass using tree-sitter.

Detects:
- Import statements
- Type definitions (objects, enums, tuples)
- Proc definitions (procedures)
- Func definitions (pure functions)
- Method definitions

Nim is a compiled systems programming language with Python-like syntax,
combining low-level control with high-level expressiveness.
The tree-sitter-nim parser handles .nim, .nims, and .nimble files.

How It Works
------------
1. Check if tree-sitter with Nim grammar is available
2. If not available, return skipped result (not an error)
3. Parse all .nim, .nims, and .nimble files
4. Extract proc/func/method definitions with signatures
5. Extract type definitions
6. Track import statements as edges

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for Nim grammar
- Nim is growing in systems programming communities
- Supports source, script, and package files
"""
from __future__ import annotations

import importlib.util
import time
import uuid
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.base import iter_tree

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "nim-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_nim_files(repo_root: Path) -> Iterator[Path]:
    """Find all Nim files in the repository."""
    yield from find_files(repo_root, ["*.nim", "*.nims", "*.nimble"])


@dataclass
class NimAnalysisResult:
    """Result of analyzing Nim files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def is_nim_tree_sitter_available() -> bool:
    """Check if tree-sitter-nim is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_language_pack") is None:
        return False  # pragma: no cover - language pack not installed
    try:
        from tree_sitter_language_pack import get_language

        get_language("nim")
        return True
    except Exception:  # pragma: no cover - nim grammar not available
        return False


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text from a tree-sitter node."""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(
    node: "tree_sitter.Node", child_type: str
) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == child_type:
            return child
    return None  # pragma: no cover - defensive


@dataclass
class _FileContext:
    """Context for processing a single file."""

    source: bytes
    rel_path: str
    file_stable_id: str
    run_id: str
    symbols: list[Symbol]
    edges: list[Edge]
    import_aliases: dict[str, str] = field(default_factory=dict)


def _make_symbol(ctx: _FileContext, node: "tree_sitter.Node", name: str, kind: str,
                 signature: Optional[str] = None, meta: Optional[dict] = None) -> Symbol:
    """Create a Symbol with consistent formatting."""
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    sym_id = f"nim:{ctx.rel_path}:{start_line}-{end_line}:{name}:{kind}"
    span = Span(
        start_line=start_line,
        start_col=node.start_point[1],
        end_line=end_line,
        end_col=node.end_point[1],
    )
    return Symbol(
        id=sym_id,
        name=name,
        canonical_name=name,
        kind=kind,
        language="nim",
        path=ctx.rel_path,
        span=span,
        origin=PASS_ID,
        origin_run_id=ctx.run_id,
        stable_id=f"nim:{ctx.rel_path}:{name}",
        signature=signature,
        meta=meta,
    )


def _process_proc_declaration(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a proc declaration."""
    name_node = _find_child_by_type(node, "identifier")
    if not name_node:
        return  # pragma: no cover - defensive

    proc_name = _node_text(name_node, ctx.source)

    # Get parameters for signature
    params = _find_child_by_type(node, "parameter_declaration_list")
    signature = _node_text(params, ctx.source) if params else "()"

    ctx.symbols.append(_make_symbol(ctx, node, proc_name, "function", signature=signature))


def _process_func_declaration(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a func declaration (pure function)."""
    name_node = _find_child_by_type(node, "identifier")
    if not name_node:
        return  # pragma: no cover - defensive

    func_name = _node_text(name_node, ctx.source)

    # Get parameters for signature
    params = _find_child_by_type(node, "parameter_declaration_list")
    signature = _node_text(params, ctx.source) if params else "()"

    ctx.symbols.append(_make_symbol(ctx, node, func_name, "function", signature=signature))


def _process_method_declaration(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a method declaration."""
    name_node = _find_child_by_type(node, "identifier")
    if not name_node:
        return  # pragma: no cover - defensive

    method_name = _node_text(name_node, ctx.source)

    # Get parameters for signature
    params = _find_child_by_type(node, "parameter_declaration_list")
    signature = _node_text(params, ctx.source) if params else "()"

    ctx.symbols.append(_make_symbol(ctx, node, method_name, "method", signature=signature))


def _process_type_declaration(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a type declaration."""
    # Look for type_symbol_declaration > identifier
    type_sym = _find_child_by_type(node, "type_symbol_declaration")
    if not type_sym:
        return  # pragma: no cover - defensive

    name_node = _find_child_by_type(type_sym, "identifier")
    if not name_node:
        return  # pragma: no cover - defensive

    type_name = _node_text(name_node, ctx.source)
    ctx.symbols.append(_make_symbol(ctx, node, type_name, "type"))


def _extract_import_aliases(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract import aliases for disambiguation.

    In Nim:
        import strutils as su -> su maps to strutils

    Returns a dict mapping alias names to module names.
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "import_statement":
            continue

        # Find expression_list containing imports
        expr_list = _find_child_by_type(node, "expression_list")
        if not expr_list:  # pragma: no cover - defensive for malformed import
            continue

        for child in expr_list.children:
            if child.type == "infix_expression":
                # import X as Y -> infix_expression with 'as' operator
                module_name = None
                alias_name = None
                found_as = False

                for subchild in child.children:
                    if subchild.type == "identifier":
                        if not found_as:
                            module_name = _node_text(subchild, source)
                        else:
                            alias_name = _node_text(subchild, source)
                    elif subchild.type == "as":
                        found_as = True

                if module_name and alias_name:
                    aliases[alias_name] = module_name

    return aliases


def _process_import_statement(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process an import statement."""
    # Find expression_list with imported modules
    expr_list = _find_child_by_type(node, "expression_list")
    if expr_list:
        for child in expr_list.children:
            if child.type == "identifier":
                import_name = _node_text(child, ctx.source)
                ctx.edges.append(
                    Edge(
                        id=f"edge:nim:{uuid.uuid4().hex[:12]}",
                        src=ctx.file_stable_id,
                        dst=f"nim:?:{import_name}:module",
                        edge_type="imports",
                        line=node.start_point[0] + 1,
                        confidence=0.9,
                        origin=PASS_ID,
                        origin_run_id=ctx.run_id,
                    )
                )
            elif child.type == "infix_expression":
                # import X as Y -> extract base module name
                for subchild in child.children:
                    if subchild.type == "identifier":
                        import_name = _node_text(subchild, ctx.source)
                        ctx.edges.append(
                            Edge(
                                id=f"edge:nim:{uuid.uuid4().hex[:12]}",
                                src=ctx.file_stable_id,
                                dst=f"nim:?:{import_name}:module",
                                edge_type="imports",
                                line=node.start_point[0] + 1,
                                confidence=0.9,
                                origin=PASS_ID,
                                origin_run_id=ctx.run_id,
                            )
                        )
                        break  # Only take the first identifier (module name)


def _find_enclosing_proc_nim(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Find the enclosing proc/func/method Symbol by walking up parents."""
    current = node.parent
    while current is not None:
        if current.type in ("proc_declaration", "func_declaration", "method_declaration"):
            name_node = _find_child_by_type(current, "identifier")
            if name_node:
                name = _node_text(name_node, source)
                sym = local_symbols.get(name)
                if sym:
                    return sym
        current = current.parent
    return None  # pragma: no cover - defensive


def _get_call_target_name_nim(
    node: "tree_sitter.Node", source: bytes
) -> tuple[Optional[str], Optional[str]]:
    """Extract the target name and receiver from a call node.

    Returns (target_name, receiver) where receiver is the module prefix
    for qualified calls like su.strip().
    """
    for child in node.children:
        if child.type == "identifier":
            return (_node_text(child, source), None)
        elif child.type == "dot_expression":
            # Qualified call like su.strip()
            parts = []
            for subchild in child.children:
                if subchild.type == "identifier":
                    parts.append(_node_text(subchild, source))
            if len(parts) >= 2:
                # Last part is the function name, first is the receiver
                return (parts[-1], parts[0])
            elif len(parts) == 1:  # pragma: no cover - defensive
                return (parts[0], None)
    return (None, None)  # pragma: no cover - defensive


def analyze_nim(repo_root: Path) -> NimAnalysisResult:
    """Analyze Nim files in a repository.

    Uses two-pass analysis:
    - Pass 1: Extract all symbols from all files
    - Pass 2: Extract edges (imports + calls) using NameResolver

    Returns a NimAnalysisResult with symbols for procs, funcs, methods, and types,
    plus edges for imports and calls.
    """
    if not is_nim_tree_sitter_available():
        warnings.warn("Nim analysis skipped: tree-sitter-nim unavailable")
        return NimAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-nim unavailable",
        )

    from tree_sitter_language_pack import get_parser

    parser = get_parser("nim")

    symbols: list[Symbol] = []
    edges: list[Edge] = []
    files_analyzed = 0
    run_id = str(uuid.uuid4())
    start_time = time.time()

    # Global symbol registry for cross-file resolution
    global_symbol_registry: dict[str, Symbol] = {}

    # Store parsed files for pass 2: (rel_path, source, tree, file_stable_id, import_aliases)
    parsed_files: list[tuple[str, bytes, object, str, dict[str, str]]] = []

    # Pass 1: Extract symbols from all files
    for file_path in find_nim_files(repo_root):
        try:
            source = file_path.read_bytes()
        except (OSError, IOError):  # pragma: no cover
            continue

        tree = parser.parse(source)
        files_analyzed += 1

        rel_path = str(file_path.relative_to(repo_root))
        file_stable_id = f"nim:{rel_path}:file:"

        # Extract import aliases for disambiguation
        import_aliases = _extract_import_aliases(tree, source)

        ctx = _FileContext(
            source=source,
            rel_path=rel_path,
            file_stable_id=file_stable_id,
            run_id=run_id,
            symbols=symbols,
            edges=[],  # Don't collect edges in pass 1
            import_aliases=import_aliases,
        )

        # Extract symbols only
        for node in iter_tree(tree.root_node):
            if node.type == "proc_declaration":
                _process_proc_declaration(ctx, node)
            elif node.type == "func_declaration":
                _process_func_declaration(ctx, node)
            elif node.type == "method_declaration":
                _process_method_declaration(ctx, node)
            elif node.type == "type_declaration":
                _process_type_declaration(ctx, node)

        # Register symbols globally
        for sym in symbols:
            if sym.path == rel_path:
                global_symbol_registry[sym.name] = sym

        # Store for pass 2
        parsed_files.append((rel_path, source, tree, file_stable_id, import_aliases))

    # Create resolver from global registry
    resolver = NameResolver(global_symbol_registry)

    # Pass 2: Extract edges (imports + calls)
    for rel_path, source, tree, file_stable_id, import_aliases in parsed_files:
        # Build local symbol map for this file (procs/funcs/methods only)
        local_symbols = {s.name: s for s in symbols
                         if s.path == rel_path and s.kind in ("function", "method")}

        ctx = _FileContext(
            source=source,
            rel_path=rel_path,
            file_stable_id=file_stable_id,
            run_id=run_id,
            symbols=[],  # Not adding symbols in pass 2
            edges=edges,
            import_aliases=import_aliases,
        )

        for node in iter_tree(tree.root_node):  # type: ignore
            # Process imports
            if node.type == "import_statement":
                _process_import_statement(ctx, node)

            # Process function calls
            elif node.type == "call":
                target_name, receiver = _get_call_target_name_nim(node, source)
                if target_name:
                    caller = _find_enclosing_proc_nim(node, source, local_symbols)
                    if caller:
                        # Get path hint from import aliases if receiver is aliased
                        path_hint: Optional[str] = None
                        if receiver:
                            path_hint = import_aliases.get(receiver)

                        # Use resolver for callee resolution
                        lookup_result = resolver.lookup(target_name, path_hint=path_hint)
                        if lookup_result.found and lookup_result.symbol:
                            dst_id = lookup_result.symbol.id
                            confidence = 0.85 * lookup_result.confidence
                        else:
                            # External function (e.g., echo from system)
                            dst_id = f"nim:external:{target_name}:function"
                            confidence = 0.70

                        edges.append(Edge(
                            id=f"edge:nim:{uuid.uuid4().hex[:12]}",
                            src=caller.id,
                            dst=dst_id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            confidence=confidence,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        ))

    duration_ms = int((time.time() - start_time) * 1000)
    return NimAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=AnalysisRun(
            execution_id=run_id,
            pass_id=PASS_ID,
            version=PASS_VERSION,
            files_analyzed=files_analyzed,
            duration_ms=duration_ms,
        ),
    )
