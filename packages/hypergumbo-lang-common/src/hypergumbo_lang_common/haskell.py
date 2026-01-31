"""Haskell analysis pass using tree-sitter-haskell.

This analyzer uses tree-sitter to parse Haskell files and extract:
- Function declarations (with and without type signatures)
- Data type definitions (including records)
- Type class definitions
- Instance declarations
- Import statements
- Function call relationships

If tree-sitter with Haskell support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-haskell is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and import statements

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-haskell package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency

Haskell-Specific Considerations
-------------------------------
- Haskell has top-level functions with optional type signatures
- Data types can be simple enums or records with named fields
- Type classes define interfaces, instances implement them
- import statements bring modules into scope (qualified or unqualified)
- Function application uses whitespace (no parens needed)
"""
from __future__ import annotations

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

PASS_ID = "haskell-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_haskell_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Haskell files in the repository."""
    yield from find_files(repo_root, ["*.hs"])


def is_haskell_tree_sitter_available() -> bool:
    """Check if tree-sitter with Haskell grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_haskell") is None:
        return False  # pragma: no cover - tree-sitter-haskell not installed
    return True


@dataclass
class HaskellAnalysisResult:
    """Result of analyzing Haskell files."""

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
    import_aliases: dict[str, str] = field(default_factory=dict)


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"haskell:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Haskell file node (used as import edge source)."""
    return f"haskell:{path}:1-1:file:file"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _get_function_name(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract function name from function or bind node.

    Handles both 'function' (pattern matching) and 'bind' (simple binding) nodes.
    """
    # First child that's a 'variable' is typically the function name
    for child in node.children:
        if child.type == "variable":
            return _node_text(child, source)
    return ""  # pragma: no cover - fallback for unparseable


def _get_module_name(import_node: "tree_sitter.Node", source: bytes) -> str:
    """Extract module name from import node."""
    module_node = _find_child_by_type(import_node, "module")
    if module_node:
        return _node_text(module_node, source)
    return ""  # pragma: no cover


def _extract_haskell_signature(
    sig_node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract type signature from a Haskell signature node.

    Haskell type signatures look like:
        add :: Int -> Int -> Int

    The signature node contains:
    - variable (function name)
    - :: token
    - function/type (the type expression)

    Returns the type part like ":: Int -> Int -> Int".
    """
    # Find the :: token and everything after it
    found_colons = False
    type_parts: list[str] = []

    for child in sig_node.children:
        if child.type == "::":
            found_colons = True
            type_parts.append("::")
        elif found_colons:
            # Collect the type expression
            type_text = _node_text(child, source).strip()
            if type_text:
                type_parts.append(type_text)

    if type_parts:
        return " ".join(type_parts)
    return None  # pragma: no cover - defensive, called only when signature exists


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> list[Symbol]:
    """Extract all symbols from a parsed Haskell file.

    Detects:
    - function/bind: Function definitions
    - data_type: Data type definitions
    - class: Type class definitions
    - instance: Instance declarations
    """
    symbols: list[Symbol] = []
    seen_names: set[str] = set()

    # First pass: collect type signatures
    type_signatures: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type == "signature":
            # Get function name from variable child
            var_node = _find_child_by_type(node, "variable")
            if var_node:
                name = _node_text(var_node, source)
                sig = _extract_haskell_signature(node, source)
                if sig:
                    type_signatures[name] = sig

    def add_symbol(
        node: "tree_sitter.Node",
        name: str,
        kind: str,
        signature: Optional[str] = None,
    ) -> None:
        """Add a symbol if not already seen."""
        if not name or name in seen_names:
            return  # pragma: no cover - skip empty/duplicate names
        seen_names.add(name)

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
            language="haskell",
            path=file_path,
            span=span,
            origin=PASS_ID,
            origin_run_id=run_id,
            signature=signature,
        ))

    # Second pass: extract symbols
    for node in iter_tree(tree.root_node):
        if node.type == "function":
            # Function with pattern matching
            name = _get_function_name(node, source)
            if name:
                # Look up type signature
                sig = type_signatures.get(name)
                add_symbol(node, name, "function", signature=sig)

        elif node.type == "bind":
            # Simple binding (like main = ...)
            name = _get_function_name(node, source)
            if name:
                sig = type_signatures.get(name)
                add_symbol(node, name, "function", signature=sig)

        elif node.type == "data_type":
            # Data type definition
            name_node = _find_child_by_type(node, "name")
            if name_node:
                name = _node_text(name_node, source)
                add_symbol(node, name, "data")

        elif node.type == "class":
            # Type class definition
            name_node = _find_child_by_type(node, "name")
            if name_node:
                name = _node_text(name_node, source)
                add_symbol(node, name, "class")

        elif node.type == "instance":
            # Instance declaration
            name_node = _find_child_by_type(node, "name")
            type_patterns = _find_child_by_type(node, "type_patterns")
            if name_node:
                class_name = _node_text(name_node, source)
                type_name = ""
                if type_patterns:
                    # Get the type being instantiated
                    inner_name = _find_child_by_type(type_patterns, "name")
                    if inner_name:
                        type_name = _node_text(inner_name, source)
                instance_name = f"{class_name} {type_name}".strip()
                add_symbol(node, instance_name, "instance")

    return symbols


def _find_enclosing_function_haskell(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Find the function that contains this node by walking up parents."""
    current = node.parent
    while current:
        if current.type in ("function", "bind"):
            name = _get_function_name(current, source)
            if name in local_symbols:
                return local_symbols[name]
        current = current.parent
    return None  # pragma: no cover - no enclosing function


def _extract_import_aliases(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract import aliases for disambiguation.

    In Haskell:
        import qualified Data.Map as M -> M maps to Data.Map

    Returns a dict mapping alias names to full module paths.
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "import":
            continue

        # Check for 'as' keyword followed by alias
        module_name: Optional[str] = None
        alias_name: Optional[str] = None
        has_as = False

        for child in node.children:
            if child.type == "module" and not has_as:
                # This is the main module being imported
                module_name = _node_text(child, source)
            elif child.type == "as":
                has_as = True
            elif child.type == "module" and has_as:
                # This is the alias
                alias_name = _node_text(child, source)

        if module_name and alias_name:
            aliases[alias_name] = module_name

    return aliases


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    resolver: NameResolver,
    run_id: str,
    import_aliases: dict[str, str] | None = None,
) -> list[Edge]:
    """Extract call and import edges from a parsed Haskell file.

    Args:
        import_aliases: Optional dict mapping module aliases to full paths.

    Detects:
    - import: Import statements
    - apply: Function application (calls)
    """
    if import_aliases is None:  # pragma: no cover - defensive default
        import_aliases = {}
    edges: list[Edge] = []
    file_id = _make_file_id(file_path)

    # Build local symbol map for this file (name -> symbol)
    local_symbols = {s.name: s for s in file_symbols}

    for node in iter_tree(tree.root_node):
        if node.type == "import":
            # Import statement
            module_name = _get_module_name(node, source)
            if module_name:
                module_id = f"haskell:{module_name}:0-0:module:module"
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

        elif node.type == "apply":
            # Function application
            # First child is typically the function being called
            if node.children:
                first_child = node.children[0]
                callee_name = None
                path_hint: Optional[str] = None

                if first_child.type == "variable":
                    callee_name = _node_text(first_child, source)
                elif first_child.type == "qualified":
                    # Qualified call: M.lookup
                    module_node = _find_child_by_type(first_child, "module")
                    var_node = _find_child_by_type(first_child, "variable")
                    if module_node and var_node:
                        # Get module alias (M) from module_id
                        module_id_node = _find_child_by_type(module_node, "module_id")
                        if module_id_node:
                            alias = _node_text(module_id_node, source)
                            path_hint = import_aliases.get(alias)
                        callee_name = _node_text(var_node, source)
                elif first_child.type == "apply":  # pragma: no cover - curried application
                    # Curried application - get innermost function
                    innermost = first_child  # pragma: no cover
                    while innermost.children and innermost.children[0].type == "apply":  # pragma: no cover
                        innermost = innermost.children[0]  # pragma: no cover
                    if innermost.children and innermost.children[0].type == "variable":  # pragma: no cover
                        callee_name = _node_text(innermost.children[0], source)  # pragma: no cover

                if callee_name and callee_name not in ("print", "putStrLn", "return"):
                    # Find the caller (enclosing function)
                    caller = _find_enclosing_function_haskell(
                        node, source, local_symbols
                    )
                    if caller:
                        # Try to resolve callee via resolver only
                        lookup_result = resolver.lookup(callee_name, path_hint=path_hint)
                        if lookup_result.found and lookup_result.symbol:
                            callee = lookup_result.symbol
                            confidence = 0.85 * lookup_result.confidence
                            edge = Edge.create(
                                src=caller.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="function_application",
                                confidence=confidence,
                            )
                            edges.append(edge)
                        else:
                            # Unresolved call - create edge to unknown target
                            unresolved_id = f"haskell:?:0-0:{callee_name}:function"
                            edge = Edge.create(
                                src=caller.id,
                                dst=unresolved_id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="function_application",
                                confidence=0.50,
                            )
                            edges.append(edge)

    return edges


def analyze_haskell(repo_root: Path) -> HaskellAnalysisResult:
    """Analyze Haskell files in a repository.

    Returns a HaskellAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-haskell is not available, returns a skipped result.
    """
    start_time = time.time()

    # Create analysis run for provenance
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    if not is_haskell_tree_sitter_available():
        skip_reason = (
            "Haskell analysis skipped: requires tree-sitter-haskell "
            "(pip install tree-sitter-haskell)"
        )
        warnings.warn(skip_reason)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return HaskellAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=skip_reason,
        )

    import tree_sitter
    import tree_sitter_haskell

    HASKELL_LANGUAGE = tree_sitter.Language(tree_sitter_haskell.language())
    parser = tree_sitter.Parser(HASKELL_LANGUAGE)
    run_id = run.execution_id

    # Pass 1: Parse all files and extract symbols
    file_analyses: list[FileAnalysis] = []
    all_symbols: list[Symbol] = []
    global_symbol_registry: dict[str, Symbol] = {}
    files_analyzed = 0

    for hs_file in find_haskell_files(repo_root):
        try:
            source = hs_file.read_bytes()
        except OSError:  # pragma: no cover
            continue

        tree = parser.parse(source)
        if tree.root_node is None:  # pragma: no cover - parser always returns root
            continue

        rel_path = str(hs_file.relative_to(repo_root))

        # Create file symbol
        file_symbol = Symbol(
            id=_make_file_id(rel_path),
            name="file",
            kind="file",
            language="haskell",
            path=rel_path,
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin=PASS_ID,
            origin_run_id=run_id,
        )
        all_symbols.append(file_symbol)

        # Extract symbols
        file_symbols = _extract_symbols_from_file(tree, source, rel_path, run_id)
        all_symbols.extend(file_symbols)

        # Extract import aliases for disambiguation
        import_aliases = _extract_import_aliases(tree, source)

        # Register symbols globally (for cross-file resolution)
        for sym in file_symbols:
            global_symbol_registry[sym.name] = sym

        file_analyses.append(FileAnalysis(
            path=rel_path,
            source=source,
            tree=tree,
            symbols=file_symbols,
            import_aliases=import_aliases,
        ))
        files_analyzed += 1

    # Pass 2: Extract edges with cross-file resolution
    all_edges: list[Edge] = []
    resolver = NameResolver(global_symbol_registry)

    for fa in file_analyses:
        edges = _extract_edges_from_file(
            fa.tree,  # type: ignore
            fa.source,
            fa.path,
            fa.symbols,
            resolver,
            run_id,
            import_aliases=fa.import_aliases,
        )
        all_edges.extend(edges)

    run.files_analyzed = files_analyzed

    return HaskellAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
