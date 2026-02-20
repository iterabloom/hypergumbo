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
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract module, function, struct, class, interface symbols
2. Pass 2: Extract import edges and call edges using NameResolver

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the D-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates ~150 lines of boilerplate
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for D grammar
- D is used for systems programming as a modern C++ alternative
- Supports both source (.d) and interface (.di) files
- Methods use qualified names to enable containment linking
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    iter_tree,
    make_symbol_id,
    node_text,
    find_child_by_type,
)
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun

PASS_ID = make_pass_id("d")


def find_d_files(repo_root: Path) -> Iterator[Path]:
    """Find all D language files in the repository."""
    yield from find_files(repo_root, ["*.d", "*.di"])


# ---------------------------------------------------------------------------
# Symbol extraction helpers
# ---------------------------------------------------------------------------


def _make_symbol(
    rel_path: str, run_id: str, node: "tree_sitter.Node",
    name: str, kind: str, source: bytes,
    analyzer: "DAnalyzer",
    signature: Optional[str] = None, meta: Optional[dict] = None,
) -> Symbol:
    """Create a Symbol with consistent formatting."""
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    span = Span(
        start_line=start_line,
        start_col=node.start_point[1],
        end_line=end_line,
        end_col=node.end_point[1],
    )
    return Symbol(
        id=make_symbol_id("d", rel_path, start_line, end_line, name, kind),
        name=name,
        canonical_name=name,
        kind=kind,
        language="d",
        path=rel_path,
        span=span,
        origin=PASS_ID,
        origin_run_id=run_id,
        stable_id=analyzer.compute_stable_id(node, kind=kind),
        signature=signature,
        meta=meta,
    )


def _process_module_declaration(
    source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node",
    analyzer: "DAnalyzer",
) -> Optional[Symbol]:
    """Process a module declaration."""
    fqn = find_child_by_type(node, "module_fqn")
    if not fqn:
        return None  # pragma: no cover - defensive

    mod_name = node_text(fqn, source)
    return _make_symbol(rel_path, run_id, node, mod_name, "module", source, analyzer)


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


def _process_function_declaration(
    source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node",
    analyzer: "DAnalyzer",
) -> Optional[Symbol]:
    """Process a function declaration.

    If the function is inside a struct, class, or interface, it is
    extracted as a method with a qualified name (e.g., ``Searcher.search``).
    Top-level functions keep ``kind="function"``.
    """
    name_node = find_child_by_type(node, "identifier")
    if not name_node:
        return None  # pragma: no cover - defensive

    func_name = node_text(name_node, source)

    # Get parameters for signature
    params = find_child_by_type(node, "parameters")
    signature = node_text(params, source) if params else "()"

    # Check if function is inside a container (struct/class/interface)
    parent_name = _find_parent_container(node, source)
    if parent_name:
        qualified_name = f"{parent_name}.{func_name}"
        return _make_symbol(rel_path, run_id, node, qualified_name, "method", source, analyzer, signature=signature)
    else:
        return _make_symbol(rel_path, run_id, node, func_name, "function", source, analyzer, signature=signature)


def _process_struct_declaration(
    source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node",
    analyzer: "DAnalyzer",
) -> Optional[Symbol]:
    """Process a struct declaration."""
    name_node = find_child_by_type(node, "identifier")
    if not name_node:
        return None  # pragma: no cover - defensive

    struct_name = node_text(name_node, source)
    return _make_symbol(rel_path, run_id, node, struct_name, "struct", source, analyzer)


def _process_class_declaration(
    source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node",
    analyzer: "DAnalyzer",
) -> Optional[Symbol]:
    """Process a class declaration."""
    name_node = find_child_by_type(node, "identifier")
    if not name_node:
        return None  # pragma: no cover - defensive

    class_name = node_text(name_node, source)
    return _make_symbol(rel_path, run_id, node, class_name, "class", source, analyzer)


def _process_interface_declaration(
    source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node",
    analyzer: "DAnalyzer",
) -> Optional[Symbol]:
    """Process an interface declaration."""
    name_node = find_child_by_type(node, "identifier")
    if not name_node:
        return None  # pragma: no cover - defensive

    iface_name = node_text(name_node, source)
    return _make_symbol(rel_path, run_id, node, iface_name, "interface", source, analyzer)


# ---------------------------------------------------------------------------
# Import alias and edge extraction helpers
# ---------------------------------------------------------------------------


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


def _process_import_declaration(
    source: bytes,
    file_stable_id: str,
    run_id: str,
    node: "tree_sitter.Node",
    module_registry: dict[str, str] | None = None,
) -> list[Edge]:
    """Process an import declaration.

    When *module_registry* maps module names to their symbol IDs, the
    dst of the import edge is resolved to the actual module symbol.
    Otherwise, the dst falls back to the unresolved ``d:?:name:module``
    format used for external / standard-library imports.
    """
    edges: list[Edge] = []

    imported = find_child_by_type(node, "imported")
    if not imported:
        return edges  # pragma: no cover - defensive

    fqn = find_child_by_type(imported, "module_fqn")
    if not fqn:
        return edges  # pragma: no cover - defensive

    import_name = node_text(fqn, source)

    # Resolve to actual module symbol if available
    if module_registry and import_name in module_registry:
        dst = module_registry[import_name]
        confidence = 0.95
    else:
        dst = f"d:?:{import_name}:module"
        confidence = 0.9

    edges.append(
        Edge(
            id=f"edge:d:{uuid.uuid4().hex[:12]}",
            src=file_stable_id,
            dst=dst,
            edge_type="imports",
            line=node.start_point[0] + 1,
            confidence=confidence,
            origin=PASS_ID,
            origin_run_id=run_id,
        )
    )
    return edges


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


def _get_call_target_name_d(
    node: "tree_sitter.Node", source: bytes
) -> tuple[Optional[str], Optional[str]]:
    """Extract the target name and receiver from a call_expression.

    Handles three call patterns in D:
    1. ``writeln("hello")`` -> identifier child -> (writeln, None)
    2. ``errors.error("bad")`` -> type > identifier, '.', identifier -> (error, errors)
    3. ``to!string(42)`` -> type > template_instance > identifier -> (to, None)

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


# ---------------------------------------------------------------------------
# DAnalyzer: TreeSitterAnalyzer subclass
# ---------------------------------------------------------------------------


class DAnalyzer(TreeSitterAnalyzer):
    """D language analyzer using tree-sitter-language-pack.

    D analysis has complex cross-file resolution requirements:
    - Module-qualified symbol registration for disambiguation
    - Import aliases for qualified call resolution
    - Import-scope path hints for bare call disambiguation
    - UFCS template call detection

    The base class handles grammar checking, parser creation, file discovery,
    timing, and result assembly. This subclass overrides register_symbol to
    use module-qualified names, and uses a custom resolver setup.
    """

    lang = "d"
    file_patterns: ClassVar[list[str]] = ["*.d", "*.di"]
    language_pack_name = "d"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract module, function, struct, class, and interface symbols."""
        analysis = FileAnalysis()

        for node in iter_tree(tree.root_node):
            sym: Optional[Symbol] = None
            if node.type == "module_declaration":
                sym = _process_module_declaration(source, rel_path, run.execution_id, node, self)
            elif node.type == "function_declaration":
                sym = _process_function_declaration(source, rel_path, run.execution_id, node, self)
            elif node.type == "struct_declaration":
                sym = _process_struct_declaration(source, rel_path, run.execution_id, node, self)
            elif node.type == "class_declaration":
                sym = _process_class_declaration(source, rel_path, run.execution_id, node, self)
            elif node.type == "interface_declaration":
                sym = _process_interface_declaration(source, rel_path, run.execution_id, node, self)

            if sym:
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node
                if sym.kind in ("function", "method"):
                    analysis.symbol_by_name[sym.name] = sym

        return analysis

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract D import aliases (import alias = module)."""
        return _extract_import_aliases(tree, source)

    def register_symbol(
        self, symbol: Symbol, global_symbols: dict,
    ) -> None:
        """Register symbols with module-qualified names for disambiguation.

        This prevents name collisions: errors.error and unrelated.error
        both exist in the registry, enabling suffix matching with path
        hint disambiguation when the caller imports one module.
        """
        file_stem = Path(symbol.path).stem  # "errors.d" -> "errors"
        qualified = f"{file_stem}.{symbol.name}"
        global_symbols[qualified] = symbol
        # Also register unqualified for exact-match fallback
        # (last writer wins, but suffix matching has all)
        global_symbols[symbol.name] = symbol

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract import and call edges from a D file."""
        edges: list[Edge] = []
        file_stable_id = f"d:{rel_path}:file:"

        # Extract imported modules for scope-based disambiguation
        imported_modules = _extract_imported_modules(tree, source)

        # Build module registry for import edge resolution
        module_registry: dict[str, str] = {}
        for sym in global_symbols.values():
            if isinstance(sym, Symbol) and sym.kind == "module":
                module_registry[sym.name] = sym.id

        for node in iter_tree(tree.root_node):
            # Process imports
            if node.type == "import_declaration":
                edges.extend(_process_import_declaration(
                    source, file_stable_id, run.execution_id, node, module_registry,
                ))

            # Process function calls
            elif node.type == "call_expression":
                target_name, receiver = _get_call_target_name_d(node, source)
                if target_name:
                    caller = _find_enclosing_function_d(node, source, local_symbols)
                    if caller:
                        _resolve_and_emit_call_edge(
                            caller, target_name, receiver,
                            import_aliases, imported_modules,
                            resolver, edges, node, run.execution_id,
                        )

            # Process UFCS template calls: arr.map!(fn)
            elif node.type == "property_expression":
                ufcs_name = _get_ufcs_template_name(node, source)
                if ufcs_name:
                    caller = _find_enclosing_function_d(node, source, local_symbols)
                    if caller:
                        _resolve_and_emit_call_edge(
                            caller, ufcs_name, None,
                            import_aliases, imported_modules,
                            resolver, edges, node, run.execution_id,
                        )

        return edges


_analyzer = DAnalyzer()


def is_d_tree_sitter_available() -> bool:
    """Check if tree-sitter-d is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("d")
def analyze_d(repo_root: Path) -> AnalysisResult:
    """Analyze D language files in a repository.

    Uses two-pass analysis:
    - Pass 1: Extract all symbols from all files
    - Pass 2: Extract edges (imports + calls) using NameResolver

    Returns a AnalysisResult with symbols for modules, functions, structs,
    classes, and interfaces, plus edges for imports and calls.
    """
    return _analyzer.analyze(repo_root)
