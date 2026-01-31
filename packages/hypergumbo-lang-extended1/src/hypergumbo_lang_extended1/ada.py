"""Ada analysis pass using tree-sitter.

Detects:
- Package specifications and bodies
- Function and procedure declarations/implementations
- Type definitions (records, enums, arrays)
- Constants and variables
- With/use clauses (imports)

Ada is a strongly-typed, safety-critical language used in aerospace,
defense, medical devices, and embedded systems where reliability is paramount.
The tree-sitter-ada parser handles .ads (spec), .adb (body), and .ada files.

How It Works
------------
1. Check if tree-sitter with Ada grammar is available
2. If not available, return skipped result (not an error)
3. Parse all .ads, .adb, and .ada files
4. Extract package declarations and bodies
5. Extract function and procedure definitions with signatures
6. Extract type definitions (records, etc.)
7. Extract constants
8. Track with clauses as import edges

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for Ada grammar
- Ada is essential for safety-critical systems (aerospace, defense, medical)
- Supports both specification (.ads) and body (.adb) files
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

PASS_ID = "ada-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_ada_files(repo_root: Path) -> Iterator[Path]:
    """Find all Ada files in the repository."""
    yield from find_files(repo_root, ["*.ads", "*.adb", "*.ada"])


@dataclass
class AdaAnalysisResult:
    """Result of analyzing Ada files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def is_ada_tree_sitter_available() -> bool:
    """Check if tree-sitter-ada is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_language_pack") is None:
        return False  # pragma: no cover - language pack not installed
    try:
        from tree_sitter_language_pack import get_language

        get_language("ada")
        return True
    except Exception:  # pragma: no cover - ada grammar not available
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
    package_renames: dict[str, str] = field(default_factory=dict)  # alias -> full_path


def _make_symbol(ctx: _FileContext, node: "tree_sitter.Node", name: str, kind: str,
                 signature: Optional[str] = None, meta: Optional[dict] = None) -> Symbol:
    """Create a Symbol with consistent formatting."""
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    sym_id = f"ada:{ctx.rel_path}:{start_line}-{end_line}:{name}:{kind}"
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
        language="ada",
        path=ctx.rel_path,
        span=span,
        origin=PASS_ID,
        origin_run_id=ctx.run_id,
        stable_id=f"ada:{ctx.rel_path}:{name}",
        signature=signature,
        meta=meta,
    )


def _extract_formal_part(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract parameters from formal_part node."""
    formal_part = _find_child_by_type(node, "formal_part")
    if formal_part:
        return _node_text(formal_part, source)
    return None


def _process_package_declaration(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a package declaration (spec)."""
    name_node = _find_child_by_type(node, "identifier")
    if not name_node:
        return  # pragma: no cover - defensive

    pkg_name = _node_text(name_node, ctx.source)
    ctx.symbols.append(_make_symbol(ctx, node, pkg_name, "package"))


def _process_package_body(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a package body."""
    name_node = _find_child_by_type(node, "identifier")
    if not name_node:
        return  # pragma: no cover - defensive

    pkg_name = _node_text(name_node, ctx.source)
    ctx.symbols.append(_make_symbol(ctx, node, pkg_name, "package"))


def _process_subprogram_declaration(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a function or procedure declaration."""
    # Look for function_specification or procedure_specification
    func_spec = _find_child_by_type(node, "function_specification")
    proc_spec = _find_child_by_type(node, "procedure_specification")

    if func_spec:
        _process_function_spec(ctx, func_spec)
    elif proc_spec:
        _process_procedure_spec(ctx, proc_spec)


def _process_subprogram_body(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a function or procedure body (implementation)."""
    func_spec = _find_child_by_type(node, "function_specification")
    proc_spec = _find_child_by_type(node, "procedure_specification")

    if func_spec:
        _process_function_spec(ctx, func_spec)
    elif proc_spec:
        _process_procedure_spec(ctx, proc_spec)


def _process_function_spec(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a function specification."""
    name_node = _find_child_by_type(node, "identifier")
    if not name_node:
        return  # pragma: no cover - defensive

    func_name = _node_text(name_node, ctx.source)

    # Build signature from formal_part and result_profile
    signature_parts = []
    formal_part = _extract_formal_part(node, ctx.source)
    if formal_part:
        signature_parts.append(formal_part)

    result = _find_child_by_type(node, "result_profile")
    if result:
        signature_parts.append(_node_text(result, ctx.source))

    signature = " ".join(signature_parts) if signature_parts else None
    ctx.symbols.append(_make_symbol(ctx, node, func_name, "function", signature=signature))


def _process_procedure_spec(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a procedure specification."""
    name_node = _find_child_by_type(node, "identifier")
    if not name_node:
        return  # pragma: no cover - defensive

    proc_name = _node_text(name_node, ctx.source)
    signature = _extract_formal_part(node, ctx.source)
    ctx.symbols.append(_make_symbol(ctx, node, proc_name, "procedure", signature=signature))


def _process_type_declaration(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a type declaration (record, enum, etc.)."""
    name_node = _find_child_by_type(node, "identifier")
    if not name_node:
        return  # pragma: no cover - defensive

    type_name = _node_text(name_node, ctx.source)
    ctx.symbols.append(_make_symbol(ctx, node, type_name, "type"))


def _process_object_declaration(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process an object declaration (constant or variable)."""
    name_node = _find_child_by_type(node, "identifier")
    if not name_node:
        return  # pragma: no cover - defensive

    obj_name = _node_text(name_node, ctx.source)

    # Check if it's a constant
    is_constant = _find_child_by_type(node, "constant") is not None
    kind = "constant" if is_constant else "variable"

    ctx.symbols.append(_make_symbol(ctx, node, obj_name, kind))


def _process_with_clause(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a with clause (import)."""
    # Find selected_component or identifier for the imported package
    for child in node.children:
        if child.type == "selected_component":
            import_name = _node_text(child, ctx.source)
            ctx.edges.append(
                Edge(
                    id=f"edge:ada:{uuid.uuid4().hex[:12]}",
                    src=ctx.file_stable_id,
                    dst=f"ada:?:{import_name}:package",
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    confidence=0.9,
                    origin=PASS_ID,
                    origin_run_id=ctx.run_id,
                )
            )
        elif child.type == "identifier":
            text = _node_text(child, ctx.source)
            if text != "with":  # Skip the keyword
                ctx.edges.append(
                    Edge(
                        id=f"edge:ada:{uuid.uuid4().hex[:12]}",
                        src=ctx.file_stable_id,
                        dst=f"ada:?:{text}:package",
                        edge_type="imports",
                        line=node.start_point[0] + 1,
                        confidence=0.9,
                        origin=PASS_ID,
                        origin_run_id=ctx.run_id,
                    )
                )


def _extract_package_renames(
    tree: "tree_sitter.Tree", source: bytes
) -> dict[str, str]:
    """Extract package renaming declarations for disambiguation.

    In Ada:
        package TIO renames Ada.Text_IO;

    Returns a dict mapping alias names to full package paths.
    """
    renames: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "package_renaming_declaration":
            continue

        alias_name: Optional[str] = None
        full_path: Optional[str] = None

        for child in node.children:
            if child.type == "identifier" and alias_name is None:
                alias_name = _node_text(child, source)
            elif child.type == "selected_component":
                full_path = _node_text(child, source)

        if alias_name and full_path:
            renames[alias_name] = full_path

    return renames


def _get_call_target_name(node: "tree_sitter.Node", source: bytes) -> tuple[Optional[str], Optional[str]]:
    """Extract the target name from a procedure_call_statement or function_call.

    Returns (target_name, receiver) where:
    - target_name is the simple name (last identifier) for resolution
    - receiver is the first part of qualified calls (e.g., "TIO" from "TIO.Put_Line")
    """
    # For procedure_call_statement: first child is identifier or selected_component
    # For function_call: first child is identifier or selected_component
    for child in node.children:
        if child.type == "identifier":
            return (_node_text(child, source), None)
        elif child.type == "selected_component":
            # Get all identifiers for qualified calls
            identifiers: list[str] = []
            for sub in child.children:
                if sub.type == "identifier":
                    identifiers.append(_node_text(sub, source))
            if identifiers:
                # last is target, first is receiver
                target_name = identifiers[-1]
                receiver = identifiers[0] if len(identifiers) > 1 else None
                return (target_name, receiver)
    return (None, None)  # pragma: no cover - defensive


def _find_enclosing_subprogram(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Find the enclosing function/procedure Symbol by walking up parents."""
    current = node.parent
    while current is not None:
        if current.type == "subprogram_body":
            # Find the function/procedure name
            func_spec = _find_child_by_type(current, "function_specification")
            proc_spec = _find_child_by_type(current, "procedure_specification")
            spec = func_spec or proc_spec
            if spec:
                name_node = _find_child_by_type(spec, "identifier")
                if name_node:
                    name = _node_text(name_node, source)
                    sym = local_symbols.get(name)
                    if sym:
                        return sym
        current = current.parent
    return None  # pragma: no cover - defensive


def _process_node(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a single tree-sitter node (non-recursive dispatch)."""
    if node.type == "package_declaration":
        _process_package_declaration(ctx, node)
    elif node.type == "package_body":
        _process_package_body(ctx, node)
    elif node.type == "subprogram_declaration":
        _process_subprogram_declaration(ctx, node)
    elif node.type == "subprogram_body":
        _process_subprogram_body(ctx, node)
    elif node.type == "full_type_declaration":
        _process_type_declaration(ctx, node)
    elif node.type == "object_declaration":
        _process_object_declaration(ctx, node)


def analyze_ada(repo_root: Path) -> AdaAnalysisResult:
    """Analyze Ada files in a repository.

    Uses two-pass analysis:
    - Pass 1: Extract all symbols from all files
    - Pass 2: Extract edges (imports + calls) using NameResolver

    Returns an AdaAnalysisResult with symbols for packages, functions, procedures,
    types, and constants, plus edges for with clauses (imports) and calls.
    """
    if not is_ada_tree_sitter_available():
        warnings.warn("Ada analysis skipped: tree-sitter-ada unavailable")
        return AdaAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-ada unavailable",
        )

    from tree_sitter_language_pack import get_parser

    parser = get_parser("ada")

    symbols: list[Symbol] = []
    edges: list[Edge] = []
    files_analyzed = 0
    run_id = str(uuid.uuid4())
    start_time = time.time()

    # Global symbol registry for cross-file resolution
    global_symbol_registry: dict[str, Symbol] = {}

    # Store parsed files for pass 2
    parsed_files: list[tuple[str, bytes, object, str]] = []

    # Pass 1: Extract symbols from all files
    for file_path in find_ada_files(repo_root):
        try:
            source = file_path.read_bytes()
        except (OSError, IOError):  # pragma: no cover
            continue

        tree = parser.parse(source)
        files_analyzed += 1

        rel_path = str(file_path.relative_to(repo_root))
        file_stable_id = f"ada:{rel_path}:file:"

        ctx = _FileContext(
            source=source,
            rel_path=rel_path,
            file_stable_id=file_stable_id,
            run_id=run_id,
            symbols=symbols,
            edges=[],  # Don't collect edges in pass 1
        )

        # Extract symbols only
        for node in iter_tree(tree.root_node):
            if node.type in ("package_declaration", "package_body", "subprogram_declaration",
                             "subprogram_body", "full_type_declaration", "object_declaration"):
                _process_node(ctx, node)

        # Extract package renames for disambiguation
        package_renames = _extract_package_renames(tree, source)

        # Register symbols globally
        for sym in symbols:
            if sym.path == rel_path:
                global_symbol_registry[sym.name] = sym

        # Store for pass 2
        parsed_files.append((rel_path, source, tree, file_stable_id, package_renames))

    # Create resolver from global registry
    resolver = NameResolver(global_symbol_registry)

    # Pass 2: Extract edges (imports + calls)
    for rel_path, source, tree, file_stable_id, package_renames in parsed_files:
        # Build local symbol map for this file (functions/procedures only)
        local_symbols = {s.name: s for s in symbols
                         if s.path == rel_path and s.kind in ("function", "procedure")}

        ctx = _FileContext(
            source=source,
            rel_path=rel_path,
            file_stable_id=file_stable_id,
            run_id=run_id,
            symbols=[],  # Not adding symbols in pass 2
            edges=edges,
            package_renames=package_renames,
        )

        for node in iter_tree(tree.root_node):  # type: ignore
            # Process imports (with_clause)
            if node.type == "with_clause":
                _process_with_clause(ctx, node)

            # Process procedure calls
            elif node.type == "procedure_call_statement":
                target_name, receiver = _get_call_target_name(node, source)
                if target_name:
                    caller = _find_enclosing_subprogram(node, source, local_symbols)
                    if caller:
                        # Get path hint from package renames
                        path_hint = package_renames.get(receiver) if receiver else None
                        # Use resolver for callee resolution
                        lookup_result = resolver.lookup(target_name, path_hint=path_hint)
                        if lookup_result.found and lookup_result.symbol:
                            dst_id = lookup_result.symbol.id
                            confidence = 0.85 * lookup_result.confidence
                        else:
                            # External procedure (e.g., Ada.Text_IO.Put_Line)
                            dst_id = f"ada:external:{target_name}:procedure"
                            confidence = 0.70

                        edges.append(Edge(
                            id=f"edge:ada:{uuid.uuid4().hex[:12]}",
                            src=caller.id,
                            dst=dst_id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            confidence=confidence,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        ))

            # Process function calls
            elif node.type == "function_call":
                target_name, receiver = _get_call_target_name(node, source)
                if target_name:
                    caller = _find_enclosing_subprogram(node, source, local_symbols)
                    if caller:
                        # Get path hint from package renames
                        path_hint = package_renames.get(receiver) if receiver else None
                        # Use resolver for callee resolution
                        lookup_result = resolver.lookup(target_name, path_hint=path_hint)
                        if lookup_result.found and lookup_result.symbol:
                            dst_id = lookup_result.symbol.id
                            confidence = 0.85 * lookup_result.confidence
                        else:
                            # External function
                            dst_id = f"ada:external:{target_name}:function"
                            confidence = 0.70

                        edges.append(Edge(
                            id=f"edge:ada:{uuid.uuid4().hex[:12]}",
                            src=caller.id,
                            dst=dst_id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            confidence=confidence,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        ))

    duration_ms = int((time.time() - start_time) * 1000)
    return AdaAnalysisResult(
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
