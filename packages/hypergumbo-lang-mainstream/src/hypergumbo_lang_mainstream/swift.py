# SPDX-License-Identifier: AGPL-3.0-or-later
"""Swift analysis pass using tree-sitter-swift.

This analyzer uses tree-sitter to parse Swift files and extract:
- Function declarations (func)
- Class declarations (class)
- Struct declarations (struct)
- Protocol declarations (protocol)
- Enum declarations (enum)
- Method declarations (inside classes/structs)
- Computed properties and subscripts
- Function call relationships
- Import statements
- Usage contexts for Vapor/Hummingbird route registrations

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
from hypergumbo_core.ir import Edge, Span, Symbol, UsageContext, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    make_typed_stable_id,
    make_unresolved_edge,
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


_CLASS_KEYWORDS = frozenset({"class", "struct", "enum", "protocol"})


def _recover_class_from_error_node(
    node: "tree_sitter.Node", source: bytes,
) -> tuple[str, str] | None:
    """Try to recover a class/struct/enum/protocol name from an ERROR node.

    tree-sitter-swift fails on certain patterns (preprocessor directives like
    #if/#else/#endif, _$ identifiers, @dynamicMemberLookup) and produces ERROR
    nodes instead of proper class_declaration nodes. When this happens, the
    ERROR node still contains the keyword (class/struct/enum/protocol) and
    a simple_identifier with the type name.

    Returns (name, kind) or None if recovery isn't possible.
    """
    # Look for a class/struct/enum/protocol keyword child followed by a name
    keyword_kind: str | None = None
    for child in node.children:
        if child.type in _CLASS_KEYWORDS:
            keyword_kind = child.type
            continue
        if keyword_kind and child.type == "simple_identifier":
            name = node_text(child, source)
            if name:
                return (name, keyword_kind)
            return None
        # type_identifier also works (some grammar versions)
        if keyword_kind and child.type == "type_identifier":
            name = node_text(child, source)
            if name:
                return (name, keyword_kind)
            return None
    return None


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


def _subscript_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Build a subscript name like ``subscript(key:)`` from a subscript_declaration node.

    Uses parameter label names followed by colons, matching Swift's standard
    subscript disambiguation convention (similar to function argument labels).
    """
    labels: list[str] = []
    for child in node.children:
        if child.type == "parameter":
            id_node = find_child_by_type(child, "simple_identifier")
            if id_node:
                labels.append(node_text(id_node, source) + ":")
    if labels:
        return f"subscript({''.join(labels)})"
    return "subscript()"  # pragma: no cover - subscripts always have params


def _extract_subscript_signature(
    node: "tree_sitter.Node", source: bytes,
) -> Optional[str]:
    """Extract signature from a subscript_declaration node.

    Returns a signature like ``(index: Int) -> JSON``.
    """
    params: list[str] = []
    return_type = None
    found_closing_paren = False

    for child in node.children:
        if child.type == "parameter":
            param_name = None
            param_type = None
            for subchild in child.children:
                if subchild.type == "simple_identifier" and param_name is None:
                    param_name = node_text(subchild, source)
                elif subchild.type in (
                    "user_type", "array_type", "dictionary_type",
                    "optional_type", "tuple_type", "function_type",
                ):
                    param_type = node_text(subchild, source)
            if param_name and param_type:
                params.append(f"{param_name}: {param_type}")
        elif child.type == ")":
            found_closing_paren = True
        elif found_closing_paren and child.type in (
            "user_type", "array_type", "dictionary_type",
            "optional_type", "tuple_type", "function_type",
        ):
            return_type = node_text(child, source)

    params_str = ", ".join(params)
    sig = f"({params_str})"
    if return_type:
        sig += f" -> {return_type}"
    return sig


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
    """Walk up the tree to find the enclosing function, computed property, or subscript."""
    current = node.parent
    while current is not None:
        if current.type == "function_declaration":
            name_node = _find_child_by_field(current, "name")
            if not name_node:  # pragma: no cover - defensive fallback
                name_node = find_child_by_type(current, "simple_identifier")
            if name_node:
                func_name = node_text(name_node, source)
                # Try qualified name first (methods are only registered qualified)
                enclosing = _get_enclosing_type(current, source)
                qualified = f"{enclosing}.{func_name}" if enclosing else func_name
                if qualified in local_symbols:
                    return local_symbols[qualified]
                # Fallback: bare name for top-level functions
                if func_name in local_symbols:  # pragma: no cover - qualified handles this
                    return local_symbols[func_name]
        elif current.type == "property_declaration" and find_child_by_type(current, "computed_property"):
            # Computed property — look up by qualified name
            pat = find_child_by_type(current, "pattern")
            if pat:
                id_node = find_child_by_type(pat, "simple_identifier")
                if id_node:
                    prop_name = node_text(id_node, source)
                    enclosing = _get_enclosing_type(current, source)
                    qualified = f"{enclosing}.{prop_name}" if enclosing else prop_name
                    if qualified in local_symbols:
                        return local_symbols[qualified]
        elif current.type == "subscript_declaration":
            # Subscript — look up by qualified subscript name
            sub_name = _subscript_name(current, source)
            if sub_name:
                enclosing = _get_enclosing_type(current, source)
                qualified = f"{enclosing}.{sub_name}" if enclosing else sub_name
                if qualified in local_symbols:
                    return local_symbols[qualified]
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
                # Register by qualified name only (AMB-METHOD invariant).
                # Methods are NOT registered by bare name to prevent
                # short-name collisions when multiple types define the
                # same method (append, filter, get). Bare calls fall
                # through to the NameResolver which handles ambiguity.
                # Top-level functions: full_name == func_name, so they're
                # still registered by their bare name.
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

        # ERROR node recovery: tree-sitter-swift fails on certain patterns
        # (preprocessor directives, _$ identifiers, @dynamicMemberLookup) and
        # produces ERROR nodes instead of class_declaration. Recover the class
        # name from the ERROR node's children when possible.
        elif node.type == "ERROR":
            recovered = _recover_class_from_error_node(node, source)
            if recovered:
                type_name, kind = recovered
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

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
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[type_name] = symbol

        # Computed property (var x: T { get { ... } })
        elif node.type == "property_declaration" and find_child_by_type(node, "computed_property"):
            pat = find_child_by_type(node, "pattern")
            if pat:
                id_node = find_child_by_type(pat, "simple_identifier")
                if id_node:
                    prop_name = node_text(id_node, source)
                    enclosing_type = _get_enclosing_type(node, source)
                    full_name = f"{enclosing_type}.{prop_name}" if enclosing_type else prop_name

                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    modifiers = _extract_modifiers_swift(node)

                    # Extract return type from type_annotation
                    type_ann = find_child_by_type(node, "type_annotation")
                    ret_type = None
                    if type_ann:
                        for tc in type_ann.children:
                            if tc.type in (
                                "user_type", "array_type", "dictionary_type",
                                "optional_type", "tuple_type", "function_type",
                            ):
                                ret_type = node_text(tc, source)
                                break
                    signature = f"() -> {ret_type}" if ret_type else None

                    symbol = Symbol(
                        id=make_symbol_id("swift", str(file_path), start_line, end_line, full_name, "property"),
                        name=full_name,
                        kind="property",
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
                        signature=signature,
                        modifiers=modifiers,
                    )
                    analysis.symbols.append(symbol)
                    analysis.node_for_symbol[symbol.id] = node
                    analysis.symbol_by_name[full_name] = symbol

        # Subscript declaration
        elif node.type == "subscript_declaration":
            sub_label = _subscript_name(node, source)
            enclosing_type = _get_enclosing_type(node, source)
            full_name = f"{enclosing_type}.{sub_label}" if enclosing_type else sub_label

            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            modifiers = _extract_modifiers_swift(node)
            signature = _extract_subscript_signature(node, source)

            symbol = Symbol(
                id=make_symbol_id("swift", str(file_path), start_line, end_line, full_name, "subscript"),
                name=full_name,
                kind="subscript",
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
                signature=signature,
                modifiers=modifiers,
            )
            analysis.symbols.append(symbol)
            analysis.node_for_symbol[symbol.id] = node
            analysis.symbol_by_name[full_name] = symbol

    return analysis


def _extract_call_target(
    call_node: "tree_sitter.Node",
    source: bytes,
) -> tuple[str, str | None]:
    """Extract the method name and receiver hint from a call_expression.

    For bare function calls like ``print("x")``, returns ``("print", None)``.
    For navigation calls like ``session.request(url)``, returns
    ``("request", "session")``.  For chained calls like
    ``URLSession.shared.dataTask(with: url)``, returns
    ``("dataTask", "URLSession")``.

    Returns:
        (callee_name, receiver_hint) — callee_name is empty string if
        no identifier could be extracted.
    """
    # Case 1: Direct call — call_expression has a simple_identifier child
    id_node = find_child_by_type(call_node, "simple_identifier")
    if id_node:
        return (node_text(id_node, source), None)

    # Case 2: Navigation call — call_expression has a navigation_expression child
    nav_node = find_child_by_type(call_node, "navigation_expression")
    if not nav_node:  # pragma: no cover - well-formed Swift always has one of the above
        return ("", None)

    # Walk the navigation chain to find the last navigation_suffix's identifier
    # (that's the method being called) and the first identifier (the receiver).
    method_name = ""
    receiver_parts: list[str] = []

    def _walk_nav(n: "tree_sitter.Node") -> None:
        nonlocal method_name
        for child in n.children:
            if child.type == "simple_identifier":
                # Collect as receiver part; the last one seen at the
                # top-level navigation_suffix is the method name.
                receiver_parts.append(node_text(child, source))
            elif child.type == "navigation_suffix":
                suffix_id = find_child_by_type(child, "simple_identifier")
                if suffix_id:
                    method_name = node_text(suffix_id, source)
            elif child.type == "navigation_expression":
                _walk_nav(child)

    _walk_nav(nav_node)

    if method_name:
        # receiver_hint is the first identifier in the chain
        # (e.g. "URLSession" from URLSession.shared.dataTask)
        receiver_hint = receiver_parts[0] if receiver_parts else None
        return (method_name, receiver_hint)

    # Fallback: if no navigation_suffix found, use the first simple_identifier
    if receiver_parts:  # pragma: no cover - navigation_expression always has suffix
        return (receiver_parts[0], None)

    return ("", None)  # pragma: no cover


def _extract_var_type(node: "tree_sitter.Node", source: bytes) -> tuple[str | None, str | None]:
    """Extract variable name and type from a property_declaration node.

    Returns (var_name, type_name). Type is inferred from:
    1. Explicit type annotation: ``let x: Store = ...`` → ("x", "Store")
    2. Constructor call: ``let x = Store()`` → ("x", "Store")
    3. No type available: ``let x = compute()`` → ("x", None)
    """
    var_name: str | None = None
    type_name: str | None = None

    for child in node.children:
        if child.type == "pattern":
            # The pattern contains the variable name
            id_node = find_child_by_type(child, "simple_identifier")
            if id_node:
                var_name = node_text(id_node, source)
            elif child.named_child_count == 0:  # pragma: no cover
                # pattern IS the identifier text
                var_name = node_text(child, source)  # pragma: no cover
        elif child.type == "type_annotation":
            # Explicit type: `: Store`, `: String`, `: Int`
            type_node = find_child_by_type(child, "user_type")
            if type_node:
                type_name = node_text(type_node, source)
        elif child.type == "call_expression" and type_name is None:
            # Constructor call: `Store()`, `URLSession()`
            # Extract the type from the constructor name
            id_node = find_child_by_type(child, "simple_identifier")
            if id_node:
                ctor_name = node_text(id_node, source)
                # Constructor calls start with uppercase
                if ctor_name and ctor_name[0].isupper():
                    type_name = ctor_name

    return (var_name, type_name)


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

    # Build variable → type mapping for receiver type tracking (ADR-0017 §1c)
    var_types: dict[str, str] = {}
    for node in iter_tree(tree.root_node):
        if node.type == "property_declaration":
            vname, vtype = _extract_var_type(node, source)
            if vname and vtype:
                var_types[vname] = vtype

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
                callee_name, receiver_hint = _extract_call_target(
                    node, source,
                )
                if callee_name:
                    resolved = False

                    # Try type-qualified resolution (receiver type tracking)
                    if receiver_hint and not resolved:
                        type_name = var_types.get(receiver_hint) or receiver_hint
                        qualified_name = f"{type_name}.{callee_name}"
                        if qualified_name in local_symbols:
                            callee = local_symbols[qualified_name]
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="function_call",
                                confidence=0.90,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                            ))
                            resolved = True
                        elif qualified_name in global_symbols:
                            callee = global_symbols[qualified_name]
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="function_call",
                                confidence=0.90,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                            ))
                            resolved = True

                    if not resolved and callee_name in local_symbols:
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
                        resolved = True

                    if not resolved:
                        # Build path hint: try type name first, then import alias, then receiver
                        path_hint = None
                        if receiver_hint and receiver_hint in var_types:
                            path_hint = var_types[receiver_hint]
                        if not path_hint:
                            path_hint = (
                                import_aliases.get(callee_name)
                                or receiver_hint
                            )
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
                        else:
                            edges.append(make_unresolved_edge(
                                "swift", current_function.id, callee_name,
                                node.start_point[0] + 1, PASS_ID, run_id,
                                module_hint=path_hint or "external",
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


# ---------------------------------------------------------------------------
# Usage context extraction (Vapor / Hummingbird route detection)
# ---------------------------------------------------------------------------

_VAPOR_HTTP_METHODS = frozenset({"get", "post", "put", "delete", "patch"})
_VAPOR_RECEIVERS = frozenset({"app", "routes", "router"})


def _extract_vapor_usage_contexts(
    root_node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    symbol_by_name: dict[str, Symbol],
    run_id: str = "",
) -> tuple[list[UsageContext], list[Symbol]]:
    """Extract UsageContext records and route symbols for Vapor/Hummingbird routes.

    Detects patterns like:
    - ``app.get("hello") { req in ... }``
    - ``routes.post("users") { req in ... }``
    - ``app.get("users", use: controller.index)``

    Creates both UsageContext records (for framework pattern matching) and
    route Symbol objects (kind="route") so routes appear in ``hypergumbo routes``.

    Returns:
        Tuple of (UsageContext list, Symbol list).
    """
    contexts: list[UsageContext] = []
    route_symbols: list[Symbol] = []

    for node in iter_tree(root_node):
        if node.type != "call_expression":
            continue

        # Look for navigation_expression (receiver.method pattern)
        nav_node = find_child_by_type(node, "navigation_expression")
        if nav_node is None:
            continue

        # Extract receiver and method from navigation chain
        receiver_name: str | None = None
        method_name: str | None = None

        id_node = find_child_by_type(nav_node, "simple_identifier")
        if id_node:
            receiver_name = node_text(id_node, source)

        nav_suffix = find_child_by_type(nav_node, "navigation_suffix")
        if nav_suffix:
            suffix_id = find_child_by_type(nav_suffix, "simple_identifier")
            if suffix_id:
                method_name = node_text(suffix_id, source)

        if (
            receiver_name is None
            or method_name is None
            or receiver_name not in _VAPOR_RECEIVERS
            or method_name not in _VAPOR_HTTP_METHODS
        ):
            continue

        # Extract route path segments from string literal arguments
        call_suffix = find_child_by_type(node, "call_suffix")
        if call_suffix is None:  # pragma: no cover - well-formed Swift
            continue

        value_args = find_child_by_type(call_suffix, "value_arguments")
        if value_args is None:  # pragma: no cover - well-formed Swift
            continue

        path_segments: list[str] = []
        for arg in value_args.children:
            if arg.type != "value_argument":
                continue
            # Skip labeled arguments (like use: handler)
            if find_child_by_type(arg, "value_argument_label") is not None:
                continue
            str_lit = find_child_by_type(arg, "line_string_literal")
            if str_lit:
                text_node = find_child_by_type(str_lit, "line_str_text")
                if text_node:
                    path_segments.append(node_text(text_node, source))

        route_path = "/".join(path_segments) if path_segments else ""
        context_name = f"{receiver_name}.{method_name}"

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )
        http_method = method_name.upper()

        ctx = UsageContext.create(
            kind="call",
            context_name=context_name,
            position="args[last]",
            path=str(file_path),
            span=span,
            metadata={
                "route_path": route_path,
                "http_method": http_method,
            },
        )
        contexts.append(ctx)

        # Create route symbol so routes appear in `hypergumbo routes`
        route_name = f"{http_method} /{route_path}" if route_path else f"{http_method} /"
        route_id = make_symbol_id(
            "swift",
            path=str(file_path),
            start_line=span.start_line,
            end_line=span.end_line,
            name=route_name,
            kind="route",
        )
        route_symbols.append(Symbol(
            id=route_id,
            name=route_name,
            kind="route",
            language="swift",
            path=str(file_path),
            span=span,
            meta={
                "http_method": http_method,
                "route_path": f"/{route_path}" if route_path else "/",
            },
            origin=PASS_ID,
            origin_run_id=run_id,
        ))

    return contexts, route_symbols


class SwiftAnalyzer(TreeSitterAnalyzer):
    """Swift language analyzer using tree-sitter-swift."""

    lang = "swift"
    file_patterns: ClassVar[list[str]] = ["*.swift"]
    grammar_module = "tree_sitter_swift"

    def __init__(self) -> None:
        super().__init__()
        self._pending_route_symbols: list[Symbol] = []

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

    def extract_usage_contexts_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, symbol_by_name: dict[str, Symbol],
    ) -> list[UsageContext]:
        """Extract Vapor/Hummingbird route usage contexts and stash route symbols."""
        run_id = getattr(self, "_current_run_id", "")
        contexts, route_symbols = _extract_vapor_usage_contexts(
            tree.root_node, source, file_path, symbol_by_name, run_id,
        )
        self._pending_route_symbols.extend(route_symbols)
        return contexts

    def post_process(
        self, symbols: list[Symbol], edges: list[Edge],
        usage_contexts: list[UsageContext], run: "AnalysisRun",
    ) -> tuple[list[Symbol], list[Edge], list[UsageContext]]:
        """Add stashed route symbols to the final result."""
        symbols.extend(self._pending_route_symbols)
        self._pending_route_symbols = []
        return symbols, edges, usage_contexts


_analyzer = SwiftAnalyzer()


def is_swift_tree_sitter_available() -> bool:
    """Check if tree-sitter with Swift grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("swift")
def analyze_swift(repo_root: Path) -> AnalysisResult:
    """Analyze Swift files in a repository."""
    return _analyzer.analyze(repo_root)
