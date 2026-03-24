# SPDX-License-Identifier: AGPL-3.0-or-later
"""Haskell analysis pass using tree-sitter-haskell.

This analyzer uses tree-sitter to parse Haskell files and extract:
- Function declarations (with and without type signatures)
- Data type definitions (including records)
- Type class definitions
- Instance declarations
- Import statements
- Function call relationships (including external edges for I/O boundary matching)

If tree-sitter with Haskell support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract functions, data types, type classes, instances with signatures
2. Pass 2: Extract call edges and import edges using NameResolver

Unresolved calls (especially to Prelude and stdlib functions like readFile,
writeFile, putStrLn) produce external edges in the format
``haskell:{module}:0-0:{name}:function`` so the I/O boundary tagging pass
(ADR-0016) can match them against ``io_primitives/haskell.yaml``. Qualified
calls resolved through import aliases carry the full module name as the
module hint for precise matching.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Optional dependency keeps base install lightweight
- Uses tree-sitter-haskell package for grammar (grammar_module)
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency
- External edges bridge Haskell's stdlib to the I/O boundary catalog

Haskell-Specific Considerations
-------------------------------
- Haskell has top-level functions with optional type signatures
- Data types can be simple enums or records with named fields
- Type classes define interfaces, instances implement them
- import statements bring modules into scope (qualified or unqualified)
- Function application uses whitespace (no parens needed)
- Only ``return`` is excluded from call edges (monadic lift, not a real call);
  I/O primitives like putStrLn, print, readFile are kept as edges
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol, make_pass_id
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("haskell")


def find_haskell_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Haskell files in the repository."""
    yield from find_files(repo_root, ["*.hs"])



def _get_function_name(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract function name from function or bind node.

    Handles both 'function' (pattern matching) and 'bind' (simple binding) nodes.
    """
    # First child that's a 'variable' is typically the function name
    for child in node.children:
        if child.type == "variable":
            return node_text(child, source)
    return ""  # pragma: no cover - fallback for unparseable


def _get_module_name(import_node: "tree_sitter.Node", source: bytes) -> str:
    """Extract module name from import node."""
    module_node = find_child_by_type(import_node, "module")
    if module_node:
        return node_text(module_node, source)
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
            type_text = node_text(child, source).strip()
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
            var_node = find_child_by_type(node, "variable")
            if var_node:
                name = node_text(var_node, source)
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
        sym_id = make_symbol_id("haskell", file_path, start_line, end_line, name, kind)
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
    # Only extract module-level definitions — where-clause local bindings
    # (parent chain includes local_binds) produce orphan symbols since
    # they have no external callers.
    for node in iter_tree(tree.root_node):
        if node.type == "function":
            # Function with pattern matching — only module-level
            if node.parent and node.parent.type != "declarations":
                continue
            name = _get_function_name(node, source)
            if name:
                # Look up type signature
                sig = type_signatures.get(name)
                add_symbol(node, name, "function", signature=sig)

        elif node.type == "bind":
            # Simple binding (like main = ...) — only module-level
            if node.parent and node.parent.type != "declarations":
                continue
            name = _get_function_name(node, source)
            if name:
                sig = type_signatures.get(name)
                add_symbol(node, name, "function", signature=sig)

        elif node.type == "data_type":
            # Data type definition
            name_node = find_child_by_type(node, "name")
            if name_node:
                name = node_text(name_node, source)
                add_symbol(node, name, "data")

        elif node.type == "class":
            # Type class definition
            name_node = find_child_by_type(node, "name")
            if name_node:
                name = node_text(name_node, source)
                add_symbol(node, name, "class")

        elif node.type == "instance":
            # Instance declaration
            name_node = find_child_by_type(node, "name")
            type_patterns = find_child_by_type(node, "type_patterns")
            if name_node:
                class_name = node_text(name_node, source)
                type_name = ""
                if type_patterns:
                    # Get the type being instantiated
                    inner_name = find_child_by_type(type_patterns, "name")
                    if inner_name:
                        type_name = node_text(inner_name, source)
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
                module_name = node_text(child, source)
            elif child.type == "as":
                has_as = True
            elif child.type == "module" and has_as:
                # This is the alias
                alias_name = node_text(child, source)

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
    """Extract call, import, and implements edges from a parsed Haskell file.

    Args:
        import_aliases: Optional dict mapping module aliases to full paths.

    Detects:
    - import: Import statements
    - apply: Function application (calls)
    - instance: Typeclass instance → typeclass 'implements' edges
    """
    if import_aliases is None:  # pragma: no cover - defensive default
        import_aliases = {}
    edges: list[Edge] = []
    file_id = make_file_id("haskell", file_path)

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

        elif node.type == "instance":
            # Typeclass instance → typeclass 'implements' edge
            name_node = find_child_by_type(node, "name")
            if name_node:
                class_name = node_text(name_node, source)
                # Build instance symbol name to find the src symbol
                type_patterns = find_child_by_type(node, "type_patterns")
                type_name = ""
                if type_patterns:
                    inner_name = find_child_by_type(type_patterns, "name")
                    if inner_name:
                        type_name = node_text(inner_name, source)
                instance_name = f"{class_name} {type_name}".strip()
                instance_sym = local_symbols.get(instance_name)
                if instance_sym:
                    # Try to resolve the typeclass
                    class_lookup = resolver.lookup(class_name)
                    if class_lookup.found and class_lookup.symbol:
                        class_sym = class_lookup.symbol
                    else:
                        # Look in local symbols (same-file typeclass)
                        class_sym = local_symbols.get(class_name)  # pragma: no cover
                    if class_sym:
                        edge = Edge.create(
                            src=instance_sym.id,
                            dst=class_sym.id,
                            edge_type="implements",
                            line=node.start_point[0] + 1,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                            evidence_type="typeclass_instance",
                            confidence=0.90,
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
                    callee_name = node_text(first_child, source)
                elif first_child.type == "qualified":
                    # Qualified call: M.lookup
                    module_node = find_child_by_type(first_child, "module")
                    var_node = find_child_by_type(first_child, "variable")
                    if module_node and var_node:
                        # Get module alias (M) from module_id
                        module_id_node = find_child_by_type(module_node, "module_id")
                        if module_id_node:
                            alias = node_text(module_id_node, source)
                            path_hint = import_aliases.get(alias)
                        callee_name = node_text(var_node, source)
                elif first_child.type == "apply":  # pragma: no cover - curried application
                    # Curried application - get innermost function
                    innermost = first_child  # pragma: no cover
                    while innermost.children and innermost.children[0].type == "apply":  # pragma: no cover
                        innermost = innermost.children[0]  # pragma: no cover
                    if innermost.children and innermost.children[0].type == "variable":  # pragma: no cover
                        callee_name = node_text(innermost.children[0], source)  # pragma: no cover

                # Skip 'return' (monadic lift, not a real call) but keep
                # I/O primitives like putStrLn, print, readFile, writeFile
                # so they produce edges matchable by the I/O boundary catalog.
                if callee_name and callee_name != "return":
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
                            # Unresolved call — create edge to synthetic
                            # external node so I/O boundary tagging can match
                            # stdlib calls (readFile, putStrLn, etc.).
                            # Use module hint from qualified import when
                            # available, otherwise "external" so the I/O
                            # boundary tagger uses unfiltered short-name
                            # matching (not "?" which would fail module
                            # filtering and return None).
                            module_hint = path_hint if path_hint else "external"
                            ext_id = f"haskell:{module_hint}:0-0:{callee_name}:function"
                            edge = Edge.create(
                                src=caller.id,
                                dst=ext_id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="function_application_external",
                                confidence=0.50,
                            )
                            edges.append(edge)

    return edges


class HaskellAnalyzer(TreeSitterAnalyzer):
    """Haskell language analyzer using tree-sitter-haskell.

    Extracts functions, data types, type classes, instances, imports,
    and function call relationships from Haskell source files.
    """

    lang = "haskell"
    file_patterns: ClassVar[list[str]] = ["*.hs"]
    grammar_module = "tree_sitter_haskell"
    create_file_symbols = True

    def extract_symbols_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        run: AnalysisRun,
    ) -> FileAnalysis:
        """Extract functions, data types, classes, instances from a Haskell file."""
        analysis = FileAnalysis()

        file_symbols = _extract_symbols_from_file(
            tree, source, rel_path, run.execution_id,
        )
        analysis.symbols.extend(file_symbols)

        # Build symbol_by_name for edge resolution
        for sym in file_symbols:
            analysis.symbol_by_name[sym.name] = sym

        return analysis

    def get_import_aliases(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
    ) -> dict[str, str]:
        """Extract Haskell import aliases (import qualified ... as ...)."""
        return _extract_import_aliases(tree, source)

    def extract_edges_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        local_symbols: dict[str, Symbol],
        global_symbols: dict,
        run: AnalysisRun,
        import_aliases: dict[str, str],
        resolver: NameResolver,
    ) -> list[Edge]:
        """Extract call and import edges from a Haskell file."""
        file_symbols = list(local_symbols.values())
        return _extract_edges_from_file(
            tree, source, rel_path, file_symbols,
            resolver, run.execution_id,
            import_aliases=import_aliases,
        )


_analyzer = HaskellAnalyzer()


def is_haskell_tree_sitter_available() -> bool:
    """Check if tree-sitter with Haskell grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("haskell")
def analyze_haskell(repo_root: Path) -> AnalysisResult:
    """Analyze Haskell files in a repository."""
    return _analyzer.analyze(repo_root)
