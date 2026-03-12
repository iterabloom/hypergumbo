# SPDX-License-Identifier: AGPL-3.0-or-later
"""Swift analysis pass using tree-sitter-swift.

This analyzer uses tree-sitter to parse Swift files and extract:
- Function declarations (func)
- Class declarations (class)
- Struct declarations (struct)
- Protocol declarations (protocol)
- Enum declarations (enum)
- Method declarations (inside classes/structs)
- Function call relationships
- Import statements

If tree-sitter with Swift support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract functions, classes, structs, protocols, enums with signatures
2. Pass 2: Extract call edges and import edges using NameResolver

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Swift-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Optional dependency keeps base install lightweight
- Uses tree-sitter-swift package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    make_typed_stable_id,
    node_text,
    visibility_from_modifiers,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("swift")


def find_swift_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Swift files in the repository."""
    yield from find_files(repo_root, ["*.swift"])


def _extract_import_hints(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract import statements for disambiguation.

    In Swift:
        import Foundation -> Foundation as hint
        import MyModule -> MyModule as hint

    Returns a dict mapping module names to their import paths.
    """
    hints: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "import_declaration":
            continue

        # Get the module being imported
        id_node = find_child_by_type(node, "identifier")
        if id_node:
            module_name = node_text(id_node, source)
            if module_name:
                hints[module_name] = module_name

    return hints


def _find_child_by_field(node: "tree_sitter.Node", field_name: str) -> Optional["tree_sitter.Node"]:
    """Find child by field name."""
    return node.child_by_field_name(field_name)


def _extract_base_classes_swift(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract base classes/protocols from Swift type declaration.

    Swift uses the same syntax for class inheritance and protocol conformance:
        class Dog: Animal { }           -> ["Animal"]
        class Car: Vehicle, Drivable { } -> ["Vehicle", "Drivable"]
        struct Point: Equatable { }      -> ["Equatable"]

    The AST has `inheritance_specifier` nodes containing `user_type` with `type_identifier`.
    """
    base_classes: list[str] = []

    for child in node.children:
        if child.type == "inheritance_specifier":
            # Get the type from user_type -> type_identifier
            user_type = find_child_by_type(child, "user_type")
            if user_type:
                type_id = find_child_by_type(user_type, "type_identifier")
                if type_id:
                    base_classes.append(node_text(type_id, source))

    return base_classes


def _get_enclosing_type(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up the tree to find the enclosing class/struct/enum/protocol name."""
    current = node.parent
    while current is not None:
        if current.type in ("class_declaration", "protocol_declaration"):
            name_node = find_child_by_type(current, "type_identifier")
            if name_node:
                return node_text(name_node, source)
        current = current.parent
    return None  # pragma: no cover - defensive


def _get_enclosing_function(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing function/method."""
    current = node.parent
    while current is not None:
        if current.type == "function_declaration":
            name_node = _find_child_by_field(current, "name")
            if not name_node:  # pragma: no cover - defensive fallback
                name_node = find_child_by_type(current, "simple_identifier")
            if name_node:
                func_name = node_text(name_node, source)
                if func_name in local_symbols:
                    return local_symbols[func_name]
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_swift_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a Swift function declaration.

    Returns signature like:
    - "(x: Int, y: Int) -> Int" for regular functions
    - "(message: String)" for void functions (no return type shown)

    Args:
        node: The function_declaration node.
        source: The source code bytes.

    Returns:
        The signature string, or None if extraction fails.
    """
    params: list[str] = []
    return_type = None
    found_closing_paren = False

    # Iterate through children to find parameters and return type
    for child in node.children:
        if child.type == "parameter":
            param_name = None
            param_type = None
            for subchild in child.children:
                if subchild.type == "simple_identifier" and param_name is None:
                    param_name = node_text(subchild, source)
                elif subchild.type in ("user_type", "array_type", "dictionary_type",
                                        "optional_type", "tuple_type", "function_type"):
                    param_type = node_text(subchild, source)
            if param_name and param_type:
                params.append(f"{param_name}: {param_type}")
        elif child.type == ")":
            found_closing_paren = True
        # Return type comes after ) and before function_body
        elif found_closing_paren and child.type in ("user_type", "array_type", "dictionary_type",
                                                      "optional_type", "tuple_type", "function_type"):
            return_type = node_text(child, source)

    params_str = ", ".join(params)
    signature = f"({params_str})"

    if return_type:
        signature += f" -> {return_type}"

    return signature


def normalize_swift_signature(
    signature: str | None,
    type_params: list[str] | None = None,
) -> str | None:
    """Normalize a Swift signature for typed stable_id (ADR-0014 §3)."""
    from hypergumbo_core.analyze.base import normalize_signature_names_first
    return normalize_signature_names_first(signature, type_params, return_sep="->")


# Swift modifier keywords extractable from the AST.
# tree-sitter-swift wraps modifiers in a ``modifiers`` container whose
# children are ``visibility_modifier`` (etc.) nodes wrapping the keyword.
SWIFT_MODIFIER_KEYWORDS = {
    "public", "private", "internal", "open", "fileprivate",
    "static", "class", "final", "override",
    "mutating", "nonmutating", "lazy",
}

_SWIFT_MODIFIER_NODE_TYPES = {
    "visibility_modifier", "ownership_modifier", "mutation_modifier",
    "member_modifier", "function_modifier", "property_modifier",
    "inheritance_modifier",
}


def _extract_modifiers_swift(node: "tree_sitter.Node") -> list[str]:
    """Extract all modifiers from a Swift declaration node.

    Swift tree-sitter groups modifiers under a ``modifiers`` container.
    Each child is a typed wrapper (e.g. ``visibility_modifier``) whose
    single child is the keyword token (e.g. ``public``).

    Returns a list of modifier strings like ``["public", "static"]``.
    """
    modifiers: list[str] = []
    for child in node.children:
        if child.type == "modifiers":
            for mod_node in child.children:
                if mod_node.type in _SWIFT_MODIFIER_NODE_TYPES:
                    for kw in mod_node.children:
                        if kw.type in SWIFT_MODIFIER_KEYWORDS:
                            modifiers.append(kw.type)
                # Some modifiers appear as direct keyword children
                elif mod_node.type in SWIFT_MODIFIER_KEYWORDS:  # pragma: no cover
                    modifiers.append(mod_node.type)
    return modifiers


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> FileAnalysis:
    """Extract symbols from a single Swift file."""
    analysis = FileAnalysis()

    for node in iter_tree(tree.root_node):
        # Function declaration
        if node.type == "function_declaration":
            name_node = _find_child_by_field(node, "name")
            if not name_node:  # pragma: no cover - grammar fallback
                name_node = find_child_by_type(node, "simple_identifier")

            if name_node:
                func_name = node_text(name_node, source)
                enclosing_type = _get_enclosing_type(node, source)
                if enclosing_type:
                    full_name = f"{enclosing_type}.{func_name}"
                    kind = "method"
                else:
                    full_name = func_name
                    kind = "function"

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract signature
                signature = _extract_swift_signature(node, source)
                modifiers = _extract_modifiers_swift(node)

                # Typed stable_id (ADR-0014 §3)
                norm_sig = normalize_swift_signature(signature)
                stable_id = make_typed_stable_id(
                    kind, norm_sig, visibility_from_modifiers(modifiers),
                ) if norm_sig else None

                symbol = Symbol(
                    id=make_symbol_id("swift", str(file_path), start_line, end_line, full_name, kind),
                    name=full_name,
                    kind=kind,
                    language="swift",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    stable_id=stable_id,
                    signature=signature,
                    modifiers=modifiers,
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[func_name] = symbol
                analysis.symbol_by_name[full_name] = symbol

        # Class declaration (class, struct, enum, protocol in tree-sitter-swift)
        elif node.type == "class_declaration":
            is_struct = find_child_by_type(node, "struct") is not None
            is_enum = find_child_by_type(node, "enum") is not None
            is_protocol = find_child_by_type(node, "protocol") is not None

            if is_struct:
                kind = "struct"
            elif is_enum:
                kind = "enum"
            elif is_protocol:  # pragma: no cover - protocols use protocol_declaration
                kind = "protocol"
            else:
                kind = "class"

            name_node = find_child_by_type(node, "type_identifier")

            if name_node:
                type_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                base_classes = _extract_base_classes_swift(node, source)
                meta = {"base_classes": base_classes} if base_classes else None

                symbol = Symbol(
                    id=make_symbol_id("swift", str(file_path), start_line, end_line, type_name, kind),
                    name=type_name,
                    kind=kind,
                    language="swift",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    meta=meta,
                    modifiers=_extract_modifiers_swift(node),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[type_name] = symbol

        # Standalone protocol declaration (for older grammar versions)
        elif node.type == "protocol_declaration":
            name_node = find_child_by_type(node, "type_identifier")

            if name_node:
                type_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                base_classes = _extract_base_classes_swift(node, source)
                meta = {"base_classes": base_classes} if base_classes else None

                symbol = Symbol(
                    id=make_symbol_id("swift", str(file_path), start_line, end_line, type_name, "protocol"),
                    name=type_name,
                    kind="protocol",
                    language="swift",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    meta=meta,
                    modifiers=_extract_modifiers_swift(node),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[type_name] = symbol

    return analysis


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run_id: str,
    resolver: "NameResolver",
    import_aliases: dict[str, str],
) -> list[Edge]:
    """Extract call and import edges from a file."""
    _caller_path = str(file_path)
    edges: list[Edge] = []
    file_id = make_file_id("swift", str(file_path))

    for node in iter_tree(tree.root_node):
        if node.type == "import_declaration":
            id_node = find_child_by_type(node, "identifier")
            if id_node:
                import_path = node_text(id_node, source)
                edges.append(Edge.create(
                    src=file_id,
                    dst=f"swift:{import_path}:0-0:module:module",
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    evidence_type="import_statement",
                    confidence=0.95,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

        elif node.type == "call_expression":
            current_function = _get_enclosing_function(node, source, local_symbols)
            if current_function is not None:
                callee_node = find_child_by_type(node, "simple_identifier")
                if not callee_node:
                    nav_node = find_child_by_type(node, "navigation_expression")  # pragma: no cover - grammar fallback
                    if nav_node:  # pragma: no cover - grammar fallback
                        callee_node = find_child_by_type(nav_node, "simple_identifier")

                if callee_node:
                    callee_name = node_text(callee_node, source)

                    if callee_name in local_symbols:
                        callee = local_symbols[callee_name]
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=callee.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            evidence_type="function_call",
                            confidence=0.85,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        ))
                    else:
                        path_hint = import_aliases.get(callee_name)
                        lookup_result = resolver.lookup(callee_name, path_hint=path_hint, caller_path=_caller_path)
                        if lookup_result.found and lookup_result.symbol is not None:
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=lookup_result.symbol.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="function_call",
                                confidence=0.80 * lookup_result.confidence,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                            ))

        # Function references in non-call contexts (INV-dinur).
        # Pattern 1: value_argument with bare simple_identifier — map(process)
        elif node.type == "value_argument":
            id_node = find_child_by_type(node, "simple_identifier")
            if id_node and len(node.named_children) == 1:
                ref_name = node_text(id_node, source)
                target = local_symbols.get(ref_name)
                if target is None:
                    lookup = resolver.lookup(ref_name, caller_path=_caller_path)
                    if lookup.found and lookup.symbol is not None:
                        target = lookup.symbol
                if target is not None and target.kind in ("function", "method"):
                    current_function = _get_enclosing_function(
                        node, source, local_symbols,
                    )
                    if current_function is not None and target.id != current_function.id:
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=target.id,
                            edge_type="references",
                            line=node.start_point[0] + 1,
                            evidence_type="function_reference",
                            confidence=0.80,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        ))

        # Pattern 2: property_declaration with RHS simple_identifier after =
        # let handler = transform
        elif node.type == "property_declaration":
            children = node.children
            eq_idx = next(
                (i for i, c in enumerate(children) if c.type == "="), -1,
            )
            if eq_idx >= 0 and eq_idx + 1 < len(children):
                rhs = children[eq_idx + 1]
                if rhs.type == "simple_identifier":
                    ref_name = node_text(rhs, source)
                    target = local_symbols.get(ref_name)
                    if target is None:
                        lookup = resolver.lookup(ref_name, caller_path=_caller_path)
                        if lookup.found and lookup.symbol is not None:
                            target = lookup.symbol
                    if target is not None and target.kind in ("function", "method"):
                        current_function = _get_enclosing_function(
                            node, source, local_symbols,
                        )
                        if (
                            current_function is not None
                            and target.id != current_function.id
                        ):
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=target.id,
                                edge_type="references",
                                line=node.start_point[0] + 1,
                                evidence_type="function_reference",
                                confidence=0.80,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                            ))

    return edges


class SwiftAnalyzer(TreeSitterAnalyzer):
    """Swift language analyzer using tree-sitter-swift."""

    lang = "swift"
    file_patterns: ClassVar[list[str]] = ["*.swift"]
    grammar_module = "tree_sitter_swift"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract functions, classes, structs, protocols, enums from a Swift file."""
        return _extract_symbols_from_file(tree, source, rel_path, run.execution_id)

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract Swift import hints for disambiguation."""
        return _extract_import_hints(tree, source)

    def register_symbol(
        self, symbol: Symbol, global_symbols: dict,
    ) -> None:
        """Register symbol by qualified name only.

        The ``NameResolver`` suffix index handles short-name lookups.
        """
        global_symbols[symbol.name] = symbol

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call and import edges from a Swift file."""
        return _extract_edges_from_file(
            tree, source, rel_path,
            local_symbols, global_symbols,
            run.execution_id, resolver, import_aliases,
        )


_analyzer = SwiftAnalyzer()


def is_swift_tree_sitter_available() -> bool:
    """Check if tree-sitter with Swift grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("swift")
def analyze_swift(repo_root: Path) -> AnalysisResult:
    """Analyze Swift files in a repository."""
    return _analyzer.analyze(repo_root)
