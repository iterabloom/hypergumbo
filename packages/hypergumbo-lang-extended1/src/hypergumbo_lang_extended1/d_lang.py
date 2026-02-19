"""D language analysis pass using tree-sitter.

Detects:
- Module declarations
- Import statements
- Function definitions (top-level)
- Method definitions (inside struct/class/interface, qualified names)
- Struct definitions
- Class definitions
- Interface definitions

D is a systems programming language that combines low-level control
with modern features like garbage collection, closures, and ranges.
The tree-sitter-d parser handles .d and .di (interface) files.

How It Works
------------
1. Check if tree-sitter with D grammar is available
2. If not available, return skipped result (not an error)
3. Parse all .d and .di files
4. Extract module declarations
5. Extract function definitions with signatures
6. For functions inside struct/class/interface: extract as ``method``
   with qualified name (e.g., ``Searcher.search``) so the containment
   linker can create ``contains`` edges from parent to method
7. Extract struct, class, and interface definitions
8. Track import statements as edges

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for D grammar
- D is used for systems programming as a modern C++ alternative
- Supports both source (.d) and interface (.di) files
- Methods use qualified names to enable containment linking
"""
from __future__ import annotations

import importlib.util
import time
import uuid
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from hypergumbo_core.analyze.base import AnalysisResult, iter_tree, node_text, find_child_by_type
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "d-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_d_files(repo_root: Path) -> Iterator[Path]:
    """Find all D language files in the repository."""
    yield from find_files(repo_root, ["*.d", "*.di"])


def is_d_tree_sitter_available() -> bool:
    """Check if tree-sitter-d is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_language_pack") is None:
        return False  # pragma: no cover - language pack not installed
    try:
        from tree_sitter_language_pack import get_language

        get_language("d")
        return True
    except Exception:  # pragma: no cover - d grammar not available
        return False


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
    sym_id = f"d:{ctx.rel_path}:{start_line}-{end_line}:{name}:{kind}"
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
        language="d",
        path=ctx.rel_path,
        span=span,
        origin=PASS_ID,
        origin_run_id=ctx.run_id,
        stable_id=f"d:{ctx.rel_path}:{name}",
        signature=signature,
        meta=meta,
    )


def _process_module_declaration(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a module declaration."""
    # module_fqn contains the module name
    fqn = find_child_by_type(node, "module_fqn")
    if not fqn:
        return  # pragma: no cover - defensive

    mod_name = node_text(fqn, ctx.source)
    ctx.symbols.append(_make_symbol(ctx, node, mod_name, "module"))


def _process_import_declaration(
    ctx: _FileContext,
    node: "tree_sitter.Node",
    module_registry: dict[str, str] | None = None,
) -> None:
    """Process an import declaration.

    When *module_registry* maps module names to their symbol IDs, the
    dst of the import edge is resolved to the actual module symbol.
    Otherwise, the dst falls back to the unresolved ``d:?:name:module``
    format used for external / standard-library imports.
    """
    # Find the imported module
    imported = find_child_by_type(node, "imported")
    if not imported:
        return  # pragma: no cover - defensive

    fqn = find_child_by_type(imported, "module_fqn")
    if not fqn:
        return  # pragma: no cover - defensive

    import_name = node_text(fqn, ctx.source)

    # Resolve to actual module symbol if available
    if module_registry and import_name in module_registry:
        dst = module_registry[import_name]
        confidence = 0.95
    else:
        dst = f"d:?:{import_name}:module"
        confidence = 0.9

    ctx.edges.append(
        Edge(
            id=f"edge:d:{uuid.uuid4().hex[:12]}",
            src=ctx.file_stable_id,
            dst=dst,
            edge_type="imports",
            line=node.start_point[0] + 1,
            confidence=confidence,
            origin=PASS_ID,
            origin_run_id=ctx.run_id,
        )
    )


_CONTAINER_NODE_TYPES = frozenset({
    "struct_declaration", "class_declaration", "interface_declaration",
})


def _find_parent_container(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Walk up to find the enclosing struct/class/interface name."""
    current = node.parent
    while current is not None:
        if current.type in _CONTAINER_NODE_TYPES:
            name_node = find_child_by_type(current, "identifier")
            if name_node:
                return node_text(name_node, source)
        current = current.parent
    return None


def _process_function_declaration(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a function declaration.

    If the function is inside a struct, class, or interface, it is
    extracted as a method with a qualified name (e.g., ``Searcher.search``).
    Top-level functions keep ``kind="function"``.
    """
    name_node = find_child_by_type(node, "identifier")
    if not name_node:
        return  # pragma: no cover - defensive

    func_name = node_text(name_node, ctx.source)

    # Get parameters for signature
    params = find_child_by_type(node, "parameters")
    signature = node_text(params, ctx.source) if params else "()"

    # Check if function is inside a container (struct/class/interface)
    parent_name = _find_parent_container(node, ctx.source)
    if parent_name:
        qualified_name = f"{parent_name}.{func_name}"
        ctx.symbols.append(_make_symbol(ctx, node, qualified_name, "method", signature=signature))
    else:
        ctx.symbols.append(_make_symbol(ctx, node, func_name, "function", signature=signature))


def _process_struct_declaration(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a struct declaration."""
    name_node = find_child_by_type(node, "identifier")
    if not name_node:
        return  # pragma: no cover - defensive

    struct_name = node_text(name_node, ctx.source)
    ctx.symbols.append(_make_symbol(ctx, node, struct_name, "struct"))


def _process_class_declaration(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a class declaration."""
    name_node = find_child_by_type(node, "identifier")
    if not name_node:
        return  # pragma: no cover - defensive

    class_name = node_text(name_node, ctx.source)
    ctx.symbols.append(_make_symbol(ctx, node, class_name, "class"))


def _process_interface_declaration(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process an interface declaration."""
    name_node = find_child_by_type(node, "identifier")
    if not name_node:
        return  # pragma: no cover - defensive

    iface_name = node_text(name_node, ctx.source)
    ctx.symbols.append(_make_symbol(ctx, node, iface_name, "interface"))


def _find_enclosing_function_d(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Find the enclosing function Symbol by walking up parents.

    For methods inside structs/classes/interfaces, the local_symbols map
    uses qualified names (e.g., ``Searcher.search``), so we build the
    qualified name when the enclosing function is inside a container.
    """
    current = node.parent
    while current is not None:
        if current.type == "function_declaration":
            name_node = find_child_by_type(current, "identifier")
            if name_node:
                name = node_text(name_node, source)
                # Try bare name first (top-level function)
                sym = local_symbols.get(name)
                if sym:
                    return sym
                # Try qualified name (method inside container)
                parent_name = _find_parent_container(current, source)
                if parent_name:
                    sym = local_symbols.get(f"{parent_name}.{name}")
                    if sym:
                        return sym
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_import_aliases(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract import aliases for disambiguation.

    In D:
        import math = std.math; -> math maps to std.math

    Returns a dict mapping alias names to module paths.
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "import_declaration":
            continue

        # Find imported node containing alias = module_fqn
        imported = find_child_by_type(node, "imported")
        if not imported:  # pragma: no cover - defensive
            continue

        # Check if there's an alias (identifier before =)
        alias_name = None
        module_path = None

        for child in imported.children:
            if child.type == "identifier":
                alias_name = node_text(child, source)
            elif child.type == "module_fqn":
                module_path = node_text(child, source)

        if alias_name and module_path:
            aliases[alias_name] = module_path

    return aliases


def _extract_imported_modules(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> list[str]:
    """Extract all imported module paths for import-scope disambiguation.

    In D, ``import errors;`` makes all public symbols from the ``errors``
    module available in the current scope.  When resolving a bare call
    like ``error()``, we prefer symbols from imported modules over
    identically-named symbols in unrelated files.

    Returns a list of module path strings converted to file-path style
    (dots replaced with ``/``), e.g. ``["errors", "dmd/errors"]``.
    """
    modules: list[str] = []

    for node in iter_tree(tree.root_node):
        if node.type != "import_declaration":
            continue

        imported = find_child_by_type(node, "imported")
        if not imported:  # pragma: no cover - defensive
            continue

        fqn = find_child_by_type(imported, "module_fqn")
        if not fqn:
            continue  # pragma: no cover - defensive

        module_name = node_text(fqn, source)
        # Convert D module path (dots) to file path (slashes)
        # e.g., "dmd.errors" -> "dmd/errors"
        modules.append(module_name.replace(".", "/"))

    return modules


def _get_call_target_name_d(
    node: "tree_sitter.Node", source: bytes
) -> tuple[Optional[str], Optional[str]]:
    """Extract the target name and receiver from a call_expression.

    Handles three call patterns in D:
    1. ``writeln("hello")`` → identifier child → (writeln, None)
    2. ``errors.error("bad")`` → type > identifier, '.', identifier → (error, errors)
    3. ``to!string(42)`` → type > template_instance > identifier → (to, None)

    Returns (target_name, receiver) where receiver is the module prefix
    for qualified calls like math.sin().
    """
    for child in node.children:
        if child.type == "identifier":
            return (node_text(child, source), None)
        elif child.type == "type":
            # Check for template_instance first: to!string(42)
            for subchild in child.children:
                if subchild.type == "template_instance":
                    tmpl_name = find_child_by_type(subchild, "identifier")
                    if tmpl_name:
                        return (node_text(tmpl_name, source), None)
            # Qualified call like math.sin()
            # type has: identifier (math), '.', identifier (sin)
            parts = []
            for subchild in child.children:
                if subchild.type == "identifier":
                    parts.append(node_text(subchild, source))
            if len(parts) >= 2:
                return (parts[-1], parts[0])
            elif len(parts) == 1:  # pragma: no cover - defensive
                return (parts[0], None)
    return (None, None)  # pragma: no cover - defensive


def _get_ufcs_template_name(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract the template function name from a UFCS property_expression.

    Handles patterns like ``arr.map!(a => a * 2)`` where the tree-sitter AST
    produces a ``property_expression`` with a ``template_instance`` child.

    Returns the function name (e.g., "map") or None if not a UFCS template.
    """
    for child in node.children:
        if child.type == "template_instance":
            tmpl_name = find_child_by_type(child, "identifier")
            if tmpl_name:
                return node_text(tmpl_name, source)
    return None


def _resolve_and_emit_call_edge(
    caller: Symbol,
    target_name: str,
    receiver: Optional[str],
    import_aliases: dict[str, str],
    imported_modules: list[str],
    resolver: NameResolver,
    edges: list[Edge],
    node: "tree_sitter.Node",
    run_id: str,
) -> None:
    """Resolve a call target and emit a call edge.

    Shared between regular call_expression and UFCS property_expression
    handling. Uses import aliases for qualified calls and import-scope
    path_hints for bare calls to disambiguate cross-file resolution.
    """
    # Get path hint from import aliases if receiver is aliased
    path_hint: Optional[str] = None
    if receiver:
        path_hint = import_aliases.get(receiver)

    # Use resolver for callee resolution.  For bare calls
    # (no receiver), pass imported module paths as hints
    # so the resolver prefers symbols from imported modules
    # over identically-named symbols in unrelated files.
    hints = imported_modules if not receiver else None
    lookup_result = resolver.lookup(
        target_name,
        path_hint=path_hint,
        path_hints=hints,
    )
    if lookup_result.found and lookup_result.symbol:
        dst_id = lookup_result.symbol.id
        confidence = 0.85 * lookup_result.confidence
    else:
        # External function (e.g., writeln from std.stdio)
        dst_id = f"d:external:{target_name}:function"
        confidence = 0.70

    edges.append(Edge(
        id=f"edge:d:{uuid.uuid4().hex[:12]}",
        src=caller.id,
        dst=dst_id,
        edge_type="calls",
        line=node.start_point[0] + 1,
        confidence=confidence,
        origin=PASS_ID,
        origin_run_id=run_id,
    ))


@register_analyzer("d")
def analyze_d(repo_root: Path) -> AnalysisResult:
    """Analyze D language files in a repository.

    Uses two-pass analysis:
    - Pass 1: Extract all symbols from all files
    - Pass 2: Extract edges (imports + calls) using NameResolver

    Returns a AnalysisResult with symbols for modules, functions, structs,
    classes, and interfaces, plus edges for imports and calls.
    """
    if not is_d_tree_sitter_available():
        warnings.warn("D analysis skipped: tree-sitter-d unavailable")
        return AnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-d unavailable",
        )

    from tree_sitter_language_pack import get_parser

    parser = get_parser("d")

    symbols: list[Symbol] = []
    edges: list[Edge] = []
    files_analyzed = 0
    run_id = str(uuid.uuid4())
    start_time = time.time()

    # Global symbol registry for cross-file resolution
    global_symbol_registry: dict[str, Symbol] = {}

    # Store parsed files for pass 2:
    # (rel_path, source, tree, file_stable_id, import_aliases, imported_modules)
    parsed_files: list[tuple[str, bytes, object, str, dict[str, str], list[str]]] = []

    # Pass 1: Extract symbols from all files
    for file_path in find_d_files(repo_root):
        try:
            source = file_path.read_bytes()
        except (OSError, IOError):  # pragma: no cover
            continue

        tree = parser.parse(source)
        files_analyzed += 1

        rel_path = str(file_path.relative_to(repo_root))
        file_stable_id = f"d:{rel_path}:file:"

        # Extract import aliases and module paths for disambiguation
        import_aliases = _extract_import_aliases(tree, source)
        imported_modules = _extract_imported_modules(tree, source)

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
            if node.type == "module_declaration":
                _process_module_declaration(ctx, node)
            elif node.type == "function_declaration":
                _process_function_declaration(ctx, node)
            elif node.type == "struct_declaration":
                _process_struct_declaration(ctx, node)
            elif node.type == "class_declaration":
                _process_class_declaration(ctx, node)
            elif node.type == "interface_declaration":
                _process_interface_declaration(ctx, node)

        # Register symbols globally using module-qualified names.
        # This prevents name collisions: errors.error and unrelated.error
        # both exist in the registry, enabling suffix matching with path
        # hint disambiguation when the caller imports one module.
        file_stem = Path(rel_path).stem  # "errors.d" -> "errors"
        for sym in symbols:
            if sym.path == rel_path:
                qualified = f"{file_stem}.{sym.name}"
                global_symbol_registry[qualified] = sym
                # Also register unqualified for exact-match fallback
                # (last writer wins, but suffix matching has all)
                global_symbol_registry[sym.name] = sym

        # Store for pass 2
        parsed_files.append((rel_path, source, tree, file_stable_id, import_aliases, imported_modules))

    # Build module name → symbol ID mapping for import edge resolution.
    # Module symbols have kind="module" and name like "dmd.lexer".
    module_registry: dict[str, str] = {
        s.name: s.id for s in symbols if s.kind == "module"
    }

    # Create resolver from global registry
    resolver = NameResolver(global_symbol_registry)

    # Pass 2: Extract edges (imports + calls)
    for rel_path, source, tree, file_stable_id, import_aliases, imported_modules in parsed_files:
        # Build local symbol map for this file (functions and methods)
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
            if node.type == "import_declaration":
                _process_import_declaration(ctx, node, module_registry)

            # Process function calls
            elif node.type == "call_expression":
                target_name, receiver = _get_call_target_name_d(node, source)
                if target_name:
                    caller = _find_enclosing_function_d(node, source, local_symbols)
                    if caller:
                        _resolve_and_emit_call_edge(
                            caller, target_name, receiver,
                            import_aliases, imported_modules,
                            resolver, edges, node, run_id,
                        )

            # Process UFCS template calls: arr.map!(fn)
            # These appear as property_expression with template_instance child
            elif node.type == "property_expression":
                ufcs_name = _get_ufcs_template_name(node, source)
                if ufcs_name:
                    caller = _find_enclosing_function_d(node, source, local_symbols)
                    if caller:
                        _resolve_and_emit_call_edge(
                            caller, ufcs_name, None,
                            import_aliases, imported_modules,
                            resolver, edges, node, run_id,
                        )

    duration_ms = int((time.time() - start_time) * 1000)
    return AnalysisResult(
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
