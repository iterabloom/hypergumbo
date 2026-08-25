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
- Enum cases and protocol requirements as ``field`` / member symbols
- Stored properties (``field``) and top-level bindings (``variable``)
- Route-marker symbols, appended in ``post_process`` once the whole file
  set is known
- Function call relationships, and ``references`` edges where a symbol is
  named without being called
- Import statements
- Usage contexts for Vapor/Hummingbird route registrations

If tree-sitter with Swift support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract functions, classes, structs, protocols, enums with signatures
2. Pass 2: Extract call, import and ``references`` edges using NameResolver

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

Population of ``is_exported`` follows Swift's default-internal rule: a
declaration is exported only when its modifier list contains ``public`` or
``open``; ``internal`` (the implicit default), ``fileprivate``, and
``private`` items are not exported. Three emitters are exempt and set
``is_exported=True`` unconditionally, because the construct carries no
modifier list of its own: enum cases, protocol requirements, and the
route-marker symbols added in ``post_process``.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, ExternalRef, Span, Symbol, UsageContext, make_pass_id
from hypergumbo_core.qualified_name_axis import separator_for_language
from hypergumbo_core.analyze.base import (
    constructed_from_callee,
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    defer_bare_method_call,
    make_file_id,
    make_file_stable_id,
    make_route_symbol,
    make_symbol_id,
    make_typed_stable_id,
    make_unresolved_edge,
    node_text,
    visibility_from_modifiers,
)
from hypergumbo_core.paths import normalize_path
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_lang_mainstream.symbol_introspection import (
    compute_cyclomatic_complexity,
    extract_preceding_doc_comment,
)

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


# Node types whose DIRECT ``property_declaration`` children are stored properties
# of a type body (struct/class/actor/extension -> ``class_body``; enum ->
# ``enum_class_body``). A binding whose direct parent is anything else — most
# importantly ``statements`` (a method/init/closure body) — is a LOCAL, not a
# field. Keying field-eligibility off this direct parent (rather than merely
# "has some enclosing type") is what distinguishes a stored property from a
# method-local ``let``/``var``, which also parses as ``property_declaration`` and
# also has an enclosing type (INV-lanaz).
_STORED_PROPERTY_BODY_TYPES = frozenset({"class_body", "enum_class_body"})


def _get_enclosing_type(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up the tree to find the enclosing type name.

    tree-sitter-swift models struct/class/enum/actor AND ``extension`` all as
    ``class_declaration``. Read the grammar's ``name`` field rather than
    scanning direct children for a ``type_identifier``: an ``extension``'s
    extended type is wrapped in a ``user_type`` node, so a direct-child
    ``type_identifier`` search returns None (WI-kudir) — which silently demoted
    every extension member to a bare file-level symbol (method->function, and
    names/qualified-names lost their ``Type.`` prefix). The ``name`` field
    points at the right node for both plain types and extensions.
    """
    current = node.parent
    while current is not None:
        if current.type in ("class_declaration", "protocol_declaration"):
            name_node = current.child_by_field_name("name")
            if name_node:
                return node_text(name_node, source)
        current = current.parent
    return None  # pragma: no cover - defensive


def _get_swift_type_ancestors(
    node: "tree_sitter.Node", source: bytes
) -> list[str]:
    """Walk up the tree collecting all enclosing class/struct/enum/protocol names.

    Returns the chain from outermost to innermost (excluding the current
    node itself).
    """
    chain: list[str] = []
    current = node.parent
    while current is not None:
        if current.type in ("class_declaration", "protocol_declaration"):
            # ``name`` field (not a direct ``type_identifier`` scan) so that
            # ``extension T``'s user_type-wrapped name resolves (WI-kudir).
            name_node = current.child_by_field_name("name")
            if name_node:
                chain.append(node_text(name_node, source))
        current = current.parent
    return list(reversed(chain))


def _make_swift_qualified_name(
    ancestors: list[str], name: str
) -> str:
    """Build a Swift qualified name: ``Type1.Type2.symbol_name``.

    Swift has no source-level package concept (modules are at build level,
    not in source), so qualified_name comprises only the type-ancestor
    chain plus the symbol name.
    """
    sep = separator_for_language("swift")  # "."
    parts: list[str] = list(ancestors)
    parts.append(name)
    return sep.join(parts)


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
    # WI-bokab (v7): file-identity anchor for this file's symbols. ``file_path`` is
    # the repo-relative path (the extract override passes ``rel_path``). Folded into
    # make_typed_stable_id's containing slot so same-name functions/methods in
    # different files hash distinctly. Uses make_file_stable_id("swift", ...) — the
    # same value the file Symbol's own stable_id carries.
    file_stable_id = make_file_stable_id("swift", normalize_path(file_path))

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
                    name=func_name, qualified_name=full_name,
                    file_stable_id=file_stable_id,
                ) if norm_sig else None

                type_ancestors = _get_swift_type_ancestors(node, source)
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
                    docstring=extract_preceding_doc_comment(node, source, "swift"),
                    modifiers=modifiers,
                    line_span=end_line - start_line + 1,
                    is_exported=any(m in modifiers for m in ("public", "open")),
                    qualified_name=_make_swift_qualified_name(type_ancestors, func_name),
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, "swift"),
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

                type_modifiers = _extract_modifiers_swift(node)
                type_ancestors = _get_swift_type_ancestors(node, source)
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
                    modifiers=type_modifiers,
                    line_span=end_line - start_line + 1,
                    is_exported=any(m in type_modifiers for m in ("public", "open")),
                    qualified_name=_make_swift_qualified_name(type_ancestors, type_name),
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

                proto_modifiers = _extract_modifiers_swift(node)
                type_ancestors = _get_swift_type_ancestors(node, source)
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
                    modifiers=proto_modifiers,
                    line_span=end_line - start_line + 1,
                    is_exported=any(m in proto_modifiers for m in ("public", "open")),
                    qualified_name=_make_swift_qualified_name(type_ancestors, type_name),
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

                type_ancestors = _get_swift_type_ancestors(node, source)
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
                    line_span=end_line - start_line + 1,
                    qualified_name=_make_swift_qualified_name(type_ancestors, type_name),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[type_name] = symbol

        # WI-duguk: enum CASES. The analyzer already emitted an enum's methods
        # and computed properties, so an enum carrying one method looked healthy
        # on any "does this container have a member" probe while every case was
        # invisible — and a reverse slice from the enum returned it alone.
        # Emitted as kind="field" (a case is a named value of the type), the
        # same choice the D and Nim analyzers made.
        #
        # ``case green, blue`` is a SINGLE enum_entry carrying TWO
        # simple_identifier children, so this emits per IDENTIFIER, not per
        # entry — a per-entry loop drops every case after the first comma. An
        # associated-value case (``case rgb(Int, Int)``) keeps its types in a
        # sibling ``enum_type_parameters`` node, so the direct-child scan reads
        # the bare case name and nothing else.
        elif node.type == "enum_entry":
            enclosing_type = _get_enclosing_type(node, source)
            if enclosing_type:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                modifiers = _extract_modifiers_swift(node)
                type_ancestors = _get_swift_type_ancestors(node, source)
                for child in node.children:
                    if child.type != "simple_identifier":
                        continue
                    case_name = node_text(child, source)
                    full_name = f"{enclosing_type}.{case_name}"
                    qualified = _make_swift_qualified_name(
                        type_ancestors, case_name,
                    )
                    sym = Symbol(
                        id=make_symbol_id(
                            "swift", str(file_path), start_line, end_line,
                            full_name, "field",
                        ),
                        name=full_name,
                        kind="field",
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
                        modifiers=modifiers,
                        stable_id=make_typed_stable_id(
                            "field", "",
                            visibility_from_modifiers(modifiers),
                            name=case_name, qualified_name=qualified,
                            file_stable_id=file_stable_id,
                        ),
                        line_span=end_line - start_line + 1,
                        # A case is as reachable as its enum; Swift has no
                        # per-case access modifier.
                        is_exported=True,
                        qualified_name=qualified,
                    )
                    analysis.symbols.append(sym)
                    analysis.node_for_symbol[sym.id] = child
                    analysis.symbol_by_name[full_name] = sym

        # WI-duguk: protocol REQUIREMENTS. A protocol body emitted nothing at
        # all, so a reverse slice from a protocol found only the container.
        # A function requirement is a method; a property requirement is a
        # ``kind="property"`` rather than a ``field`` because ``{ get }`` is a
        # computed-access contract and never storage.
        elif node.type in (
            "protocol_function_declaration", "protocol_property_declaration",
        ):
            is_function = node.type == "protocol_function_declaration"
            if is_function:
                id_node = find_child_by_type(node, "simple_identifier")
            else:
                pat = find_child_by_type(node, "pattern")
                id_node = (
                    find_child_by_type(pat, "simple_identifier") if pat else None
                )
            enclosing_type = _get_enclosing_type(node, source)
            if id_node is not None and enclosing_type:
                member_name = node_text(id_node, source)
                full_name = f"{enclosing_type}.{member_name}"
                kind = "method" if is_function else "property"
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                modifiers = _extract_modifiers_swift(node)

                if is_function:
                    signature = _extract_swift_signature(node, source)
                else:
                    # The declared type is the whole contract of a property
                    # requirement; read it from the same type_annotation slot
                    # the stored-property branch uses.
                    type_ann = find_child_by_type(node, "type_annotation")
                    signature = None
                    for tc in type_ann.children if type_ann else ():
                        if tc.type in (
                            "user_type", "array_type", "dictionary_type",
                            "optional_type", "tuple_type", "function_type",
                        ):
                            signature = node_text(tc, source)
                            break

                type_ancestors = _get_swift_type_ancestors(node, source)
                qualified = _make_swift_qualified_name(type_ancestors, member_name)
                sym = Symbol(
                    id=make_symbol_id(
                        "swift", str(file_path), start_line, end_line,
                        full_name, kind,
                    ),
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
                    signature=signature,
                    modifiers=modifiers,
                    stable_id=make_typed_stable_id(
                        kind, signature or "",
                        visibility_from_modifiers(modifiers),
                        name=member_name, qualified_name=qualified,
                        file_stable_id=file_stable_id,
                    ),
                    line_span=end_line - start_line + 1,
                    # Reachable exactly when the protocol is; a requirement
                    # carries no access modifier of its own.
                    is_exported=True,
                    qualified_name=qualified,
                )
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node
                analysis.symbol_by_name[full_name] = sym

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

                    type_ancestors = _get_swift_type_ancestors(node, source)
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
                        line_span=end_line - start_line + 1,
                        is_exported=any(m in modifiers for m in ("public", "open")),
                        qualified_name=_make_swift_qualified_name(type_ancestors, prop_name),
                    )
                    analysis.symbols.append(symbol)
                    analysis.node_for_symbol[symbol.id] = node
                    analysis.symbol_by_name[full_name] = symbol

        # WI-jusus (emission-parity F5): STORED properties / top-level bindings.
        # A non-computed property_declaration (no computed_property child; those
        # matched the branch above as kind="property") is either a STORED
        # property of a type body -> kind="field", or a top-level let/var ->
        # kind="variable". Swift reuses one property_declaration node for both AND
        # for method-/init-/closure-local bindings; the DIRECT parent
        # discriminates. A stored property's parent is a type body
        # (class_body/enum_class_body); a top-level binding's parent is
        # source_file; a local binding's parent is `statements`. We must gate on
        # the direct parent — NOT merely on `_get_enclosing_type` being truthy,
        # because a local inside a *method* also has an enclosing type and would
        # otherwise leak in as a field (INV-lanaz). Locals are skipped
        # (module-level-only contract).
        elif node.type == "property_declaration":
            pat = find_child_by_type(node, "pattern")
            id_node = find_child_by_type(pat, "simple_identifier") if pat else None
            parent_type = node.parent.type if node.parent is not None else ""
            is_top_level = parent_type == "source_file"
            enclosing_type = (
                _get_enclosing_type(node, source)
                if parent_type in _STORED_PROPERTY_BODY_TYPES
                else None
            )
            if id_node is not None and (enclosing_type or is_top_level):
                prop_name = node_text(id_node, source)
                if enclosing_type:
                    kind = "field"
                    full_name = f"{enclosing_type}.{prop_name}"
                else:
                    kind = "variable"
                    full_name = prop_name

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                modifiers = _extract_modifiers_swift(node)

                # Declared type from type_annotation (None for inferred `let x = 5`).
                type_ann = find_child_by_type(node, "type_annotation")
                prop_type = None
                if type_ann:
                    for tc in type_ann.children:
                        if tc.type in (
                            "user_type", "array_type", "dictionary_type",
                            "optional_type", "tuple_type", "function_type",
                        ):
                            prop_type = node_text(tc, source)
                            break

                type_ancestors = _get_swift_type_ancestors(node, source)
                qualified = _make_swift_qualified_name(type_ancestors, prop_name)
                sym = Symbol(
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
                    signature=prop_type,
                    modifiers=modifiers,
                    meta=(
                        {"constructed_from": _sw_cf}
                        if (_sw_cf := constructed_from_callee(
                            find_child_by_type(node, "call_expression"), source))
                        else None
                    ),
                    stable_id=make_typed_stable_id(
                        kind, prop_type or "",
                        visibility_from_modifiers(modifiers),
                        name=prop_name, qualified_name=qualified,
                        file_stable_id=file_stable_id,
                    ),
                    line_span=end_line - start_line + 1,
                    is_exported=any(m in modifiers for m in ("public", "open")),
                    qualified_name=qualified,
                )
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node
                analysis.symbol_by_name[full_name] = sym

        # Subscript declaration
        elif node.type == "subscript_declaration":
            sub_label = _subscript_name(node, source)
            enclosing_type = _get_enclosing_type(node, source)
            full_name = f"{enclosing_type}.{sub_label}" if enclosing_type else sub_label

            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            modifiers = _extract_modifiers_swift(node)
            signature = _extract_subscript_signature(node, source)

            type_ancestors = _get_swift_type_ancestors(node, source)
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
                line_span=end_line - start_line + 1,
                is_exported=any(m in modifiers for m in ("public", "open")),
                qualified_name=_make_swift_qualified_name(type_ancestors, sub_label),
                cyclomatic_complexity=compute_cyclomatic_complexity(node, "swift"),
            )
            analysis.symbols.append(symbol)
            analysis.node_for_symbol[symbol.id] = node
            analysis.symbol_by_name[full_name] = symbol

    return analysis


def _extract_call_target(
    call_node: "tree_sitter.Node",
    source: bytes,
) -> tuple[str, str | None, bool]:
    """Extract the method name, receiver hint and receiver PRESENCE.

    For bare function calls like ``print("x")``, returns
    ``("print", None, False)``. For navigation calls like
    ``session.request(url)``, returns ``("request", "session", True)``. For
    chained calls like ``URLSession.shared.dataTask(with: url)``, returns
    ``("dataTask", "URLSession", True)``.

    THE THIRD ELEMENT IS NOT REDUNDANT WITH THE SECOND (INV-pirot). The hint is
    the first *simple_identifier* in the navigation chain, and a receiver that
    is an EXPRESSION contributes none: ``(o as! T).createFile(...)``,
    ``make().createFile(...)``, ``T(x).createFile(...)`` all walk to
    ``("createFile", None, True)``. Reading a ``None`` hint as "no receiver"
    made every one of those a bare call, which is the shape that reaches the
    unresolved emit with no ``call_construct`` and lets a bare short name bind
    a catalogued sanitizer as a phantom barrier.

    Returns:
        (callee_name, receiver_hint, has_receiver) — callee_name is empty
        string if no identifier could be extracted.
    """
    # Case 1: Direct call — call_expression has a simple_identifier child
    id_node = find_child_by_type(call_node, "simple_identifier")
    if id_node:
        return (node_text(id_node, source), None, False)

    # Case 2: Navigation call — call_expression has a navigation_expression child
    nav_node = find_child_by_type(call_node, "navigation_expression")
    if not nav_node:  # pragma: no cover - well-formed Swift always has one of the above
        return ("", None, False)

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
        # ``nav_node`` exists, so there IS a receiver expression -- whether or
        # not it contributed an identifier we can name.
        return (method_name, receiver_hint, True)

    # Fallback: if no navigation_suffix found, use the first simple_identifier
    if receiver_parts:  # pragma: no cover - navigation_expression always has suffix
        return (receiver_parts[0], None, False)

    return ("", None, False)  # pragma: no cover


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
        elif node.type == "parameter":
            # INV-fahub / WI-votar recall recovery: thread function/method
            # parameter types (previously dropped) into the receiver map so a
            # param-typed receiver (`func handle(client: Client)` → its
            # ``client.foo()`` calls) resolves via the type-qualified path
            # instead of misbinding to an arbitrary same-named def below.
            pname, ptype = _swift_param_name_and_type(node, source)
            if pname and ptype:
                var_types[pname] = ptype

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
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

        elif node.type == "call_expression":
            current_function = _get_enclosing_function(node, source, local_symbols)
            if current_function is not None:
                # INV-fahub Site-1: enclosing type short name for a bare /
                # implicit-``self`` call, so a deferred bare→method call can be
                # recovered by the inherited_calls MRO walker (inherited) or left
                # external (cross-class magnet).
                _enclosing_type = _get_enclosing_type(node, source)
                callee_name, receiver_hint, has_receiver = _extract_call_target(
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
                                evidence_type="ast_call",
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                meta={"call_construct": "function"},
                            ))
                            resolved = True
                        elif qualified_name in global_symbols:
                            callee = global_symbols[qualified_name]
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="ast_call",
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                meta={"call_construct": "function"},
                            ))
                            resolved = True

                    if not resolved and has_receiver:
                        # INV-fahub (WI-votar): a method call `recv.m()` whose
                        # receiver type could not be resolved MUST NOT fall
                        # through to the bare short-name binds below and bind to
                        # an arbitrary same-named internal def (the
                        # create/delete/run @0.80 funnel). Emit an honest
                        # unresolved external edge instead, mirroring py.py:
                        # `calls` / external-unresolved dst / `is_resolved=False`
                        # / `evidence_type="ast_call"` (→ 0.40) /
                        # `call_construct="method"`. Stamp `receiver_type_hint`
                        # when the receiver's TYPE is known (its method just was
                        # not found in-repo) so the shared inherited_calls linker
                        # can recover it (Site-2 Step-1); an untyped/duck
                        # receiver gets no hint (bias to unresolved). The linker
                        # is the sole minter of the resolved edge (INV-nilud).
                        # INV-pirot widened the guard from "the receiver has
                        # a NAME" to "there is a receiver", so a nameless
                        # receiver expression reaches this branch too -- which
                        # is the branch's own purpose, and more true of a
                        # nameless receiver rather than less: ``make().m()``
                        # cannot be a call on the enclosing type.
                        gate_meta: dict = {"call_construct": "method"}
                        receiver_type = (
                            var_types.get(receiver_hint) if receiver_hint else None
                        )
                        if receiver_type:
                            gate_meta["receiver_type_hint"] = receiver_type
                        # Preserve the WI-huzuv external dst_ref (module_path from
                        # the receiver's known type / import alias / receiver name,
                        # matching make_unresolved_edge) so a module-qualified
                        # external call (`HelpersModule.doWork()`) keeps its
                        # structured reference even while suppressed.
                        #
                        # THE OLD LAST CLAUSE HERE READ "`receiver_hint` is
                        # non-None here, so the fallback is always a real
                        # value". That stopped being true when INV-pirot
                        # widened the guard above: a receiver EXPRESSION has no
                        # spelling, so the chain can now exhaust. ``"external"``
                        # is what ``make_unresolved_edge`` already writes for an
                        # absent module hint, it matches the ``dst`` minted
                        # three lines below, and ``_module_from_symbol_path``
                        # returns "" for it -- so an untyped receiver keeps
                        # yielding no module candidate and INV-finoh's refusal
                        # is preserved rather than widened.
                        gate_path_hint = (
                            receiver_type
                            or import_aliases.get(callee_name)
                            or receiver_hint
                            or "external"
                        )
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=f"swift:external:0-0:{callee_name}:unresolved",
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            evidence_type="ast_call",
                            is_resolved=False,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                            meta=gate_meta,
                            dst_ref=ExternalRef(
                                lang="swift",
                                module_path=gate_path_hint,
                                name=callee_name,
                            ),
                        ))
                        resolved = True

                    if not resolved and callee_name in local_symbols:
                        # Swift keys ``local_symbols`` by full name (``Type.method``),
                        # so a bare short name resolves here only to a same-file
                        # top-level FUNCTION — never a class-member method (those go
                        # through the resolver path below, which is INV-fahub-gated).
                        # A free-function bind is legitimate (callable bare), so no
                        # magnet gate is needed on this branch.
                        callee = local_symbols[callee_name]
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=callee.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            evidence_type="ast_call",
                            origin=PASS_ID,
                            origin_run_id=run_id,
                            meta={"call_construct": "function"},
                        ))
                        resolved = True

                    if not resolved:
                        # Bare call only — a receiver call is gated above (which
                        # sets resolved=True), so receiver_hint is None here.
                        # Resolve by short name via the resolver, else emit an
                        # honest external edge. INV-fahub: a bare call resolving
                        # only to a DIFFERENT type's method on weak short-name
                        # evidence is a magnet — defer to Site-1 (enclosing_class).
                        path_hint = import_aliases.get(callee_name)
                        lookup_result = resolver.lookup(callee_name, path_hint=path_hint, caller_path=_caller_path)
                        _sym = lookup_result.symbol
                        _defer = _sym is not None and defer_bare_method_call(
                            _sym.kind, _sym.name,
                            lookup_result.match_type, _enclosing_type,
                        )
                        if lookup_result.found and _sym is not None and not _defer:
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=_sym.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="ast_call",
                                confidence=0.80 * lookup_result.confidence,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                meta={"call_construct": "function"},
                            ))
                        else:
                            edges.append(make_unresolved_edge(
                                "swift", current_function.id, callee_name,
                                node.start_point[0] + 1, PASS_ID, run_id,
                                module_hint=path_hint or "external",
                                dst_ref=(
                                    ExternalRef(lang="swift", module_path=path_hint, name=callee_name)
                                    if path_hint else None
                                ),
                                enclosing_class=_enclosing_type,
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
                                origin=PASS_ID,
                                origin_run_id=run_id,
                            ))

    return edges


# ---------------------------------------------------------------------------
# Usage context extraction (Vapor / Hummingbird route detection)
# ---------------------------------------------------------------------------

_VAPOR_HTTP_METHODS = frozenset({"get", "post", "put", "delete", "patch"})
_VAPOR_RECEIVERS = frozenset({"app", "routes", "router"})
# Methods that RETURN a RoutesBuilder (chainable prefix / closure group).
_VAPOR_GROUP_METHODS = frozenset({"grouped", "group"})
# Valid HTTP methods for the explicit ``.on(.VERB, …)`` form — gates against
# generic ``.on(.someEvent)`` DSLs that are not routes.
_VAPOR_ON_HTTP_METHODS = frozenset({
    "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE", "CONNECT",
})
# The extractor only runs on files that import a supported web framework —
# the root anchor is a bare name (`app`/`routes`/`router`), so this import gate
# is what keeps a same-named non-builder variable from fabricating routes.
_VAPOR_FRAMEWORK_IMPORTS = frozenset({"Vapor", "Hummingbird"})


def _swift_imports_vapor(root_node: "tree_sitter.Node", source: bytes) -> bool:
    """True when the file imports Vapor or Hummingbird (the route-pass gate)."""
    for node in iter_tree(root_node):
        if node.type == "import_declaration":
            id_node = find_child_by_type(node, "identifier")
            if id_node is not None and node_text(id_node, source) in _VAPOR_FRAMEWORK_IMPORTS:
                return True
    return False


def _swift_nav_receiver_method(
    nav_node: "tree_sitter.Node", source: bytes,
) -> tuple[Optional["tree_sitter.Node"], Optional[str]]:
    """Split a ``navigation_expression`` (``RECEIVER.method``) into (receiver, method).

    The receiver is the sole non-suffix child — a ``simple_identifier`` for a
    bare receiver, or a nested ``call_expression`` / ``navigation_expression``
    for a grouped chain. The ``.`` token and interleaved comments are skipped.
    """
    method: Optional[str] = None
    suffix = find_child_by_type(nav_node, "navigation_suffix")
    if suffix is not None:
        method_id = find_child_by_type(suffix, "simple_identifier")
        if method_id is not None:
            method = node_text(method_id, source)
    receiver = next(
        (child for child in nav_node.children
         if child.type not in ("navigation_suffix", ".", "comment", "multiline_comment")),
        None,
    )
    return receiver, method


def _swift_string_segments(
    call_suffix: Optional["tree_sitter.Node"], source: bytes,
) -> list[str]:
    """Unlabeled string-literal path segments of a call (skips ``use:``/middleware)."""
    segments: list[str] = []
    if call_suffix is None:  # pragma: no cover - well-formed Swift always has one
        return segments
    value_args = find_child_by_type(call_suffix, "value_arguments")
    if value_args is None:
        return segments
    for arg in value_args.children:
        if arg.type != "value_argument":
            continue
        if find_child_by_type(arg, "value_argument_label") is not None:
            continue
        str_lit = find_child_by_type(arg, "line_string_literal")
        if str_lit is None:
            continue
        text_node = find_child_by_type(str_lit, "line_str_text")
        if text_node is None:  # pragma: no cover - empty string literal
            continue
        seg = node_text(text_node, source).strip("/")
        if seg:
            segments.append(seg)
    return segments


def _swift_on_verb_and_segments(
    call_suffix: Optional["tree_sitter.Node"], source: bytes,
) -> tuple[Optional[str], list[str]]:
    """Parse a Vapor ``.on(.VERB, "path"…, use:)`` call into (verb, path segments)."""
    verb: Optional[str] = None
    segments: list[str] = []
    if call_suffix is None:  # pragma: no cover - well-formed Swift always has one
        return verb, segments
    value_args = find_child_by_type(call_suffix, "value_arguments")
    if value_args is None:  # pragma: no cover - `.on()` always has arguments
        return verb, segments
    for arg in value_args.children:
        if arg.type != "value_argument":
            continue
        if find_child_by_type(arg, "value_argument_label") is not None:
            continue
        prefix_expr = find_child_by_type(arg, "prefix_expression")
        if prefix_expr is not None:
            if verb is None:
                verb_id = find_child_by_type(prefix_expr, "simple_identifier")
                if verb_id is not None:
                    verb = node_text(verb_id, source).upper()
            continue
        str_lit = find_child_by_type(arg, "line_string_literal")
        if str_lit is not None:
            text_node = find_child_by_type(str_lit, "line_str_text")
            if text_node is not None:
                seg = node_text(text_node, source).strip("/")
                if seg:
                    segments.append(seg)
    return verb, segments


def _swift_lambda_param_name(
    call_suffix: Optional["tree_sitter.Node"], source: bytes,
) -> Optional[str]:
    """Name of a trailing closure's first parameter (the sub-builder), if any."""
    if call_suffix is None:  # pragma: no cover - callers pass a real suffix
        return None
    lam = find_child_by_type(call_suffix, "lambda_literal")
    if lam is None:
        return None
    lft = find_child_by_type(lam, "lambda_function_type")
    if lft is None:
        return None
    for node in iter_tree(lft):
        if node.type == "lambda_parameter":
            id_node = find_child_by_type(node, "simple_identifier")
            if id_node is not None:
                return node_text(id_node, source)
    return None  # pragma: no cover - a lambda_parameter always wraps an identifier


def _swift_param_name_and_type(
    param_node: "tree_sitter.Node", source: bytes,
) -> tuple[Optional[str], Optional[str]]:
    """Internal parameter name (last identifier before the type) and its type text."""
    idents = [c for c in param_node.children if c.type == "simple_identifier"]
    name = node_text(idents[-1], source) if idents else None
    ptype: Optional[str] = None
    user_type = find_child_by_type(param_node, "user_type")
    if user_type is not None:
        type_id = find_child_by_type(user_type, "type_identifier")
        ptype = node_text(type_id, source) if type_id is not None else node_text(user_type, source)
    return name, ptype


def _swift_is_builder_type(ptype: Optional[str]) -> bool:
    """True when a parameter type denotes a Vapor route builder root."""
    if ptype is None:
        return False
    return "RoutesBuilder" in ptype or ptype == "Application"


def _swift_rhs_after_equals(
    node: "tree_sitter.Node",
) -> Optional["tree_sitter.Node"]:
    """The initializer expression following the last ``=`` in a binding node."""
    eq_index: Optional[int] = None
    for i, child in enumerate(node.children):
        if child.type == "=":
            eq_index = i
    if eq_index is None or eq_index + 1 >= len(node.children):
        return None
    return node.children[eq_index + 1]


def _extract_vapor_usage_contexts(
    root_node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    symbol_by_name: dict[str, Symbol],
    run_id: str = "",
) -> tuple[list[UsageContext], list[Symbol]]:
    """Extract UsageContext records and route symbols for Vapor/Hummingbird routes.

    Handles the full grouped-builder surface a RouteCollection controller uses
    (INV-povit), not just a bare receiver + verb:

    - bare verb: ``app.get("hello") { req in ... }`` / ``routes.post("users", use: h)``
    - method-chained groups: ``app.grouped("api").grouped("users").get(use: h)``
      (a ``.grouped(<Middleware>)`` link contributes no path segment)
    - closure groups: ``routes.group("todos") { todos in todos.get(use: h) }``
    - variable-bound builders: ``let g = routes.grouped("x"); g.get(use: h)``
      (tracked forward through the block; reassignment updates/invalidates)
    - explicit method: ``app.on(.GET, "stream", use: h)``
    - the ``Application.routes`` property and a differently-named
      ``RoutesBuilder`` parameter as builder roots.

    The receiver chain is resolved recursively to an accumulated group-path
    prefix (``resolve_builder``); the root anchor is a reserved receiver name
    (``app``/``routes``/``router``) or a bound builder variable, and the pass
    only runs on files that import Vapor/Hummingbird — together these gate out
    ``.grouped``/``.get`` chains on unrelated types. Non-literal path segments
    and un-tracked builder aliases fail safe (a miss, never a wrong route).

    Creates both UsageContext records (for framework pattern matching) and
    route-marker Symbol objects so routes appear in ``hypergumbo routes``.

    Returns:
        Tuple of (UsageContext list, Symbol list).
    """
    contexts: list[UsageContext] = []
    route_symbols: list[Symbol] = []

    # Root-anchored on a framework import: the whole extractor is gated so a
    # same-named non-builder variable (`app`/`routes`/`router`) in a non-web
    # file cannot fabricate routes.
    if not _swift_imports_vapor(root_node, source):
        return contexts, route_symbols

    def resolve_builder(
        node: Optional["tree_sitter.Node"], bindings: dict[str, Optional[list[str]]],
    ) -> Optional[list[str]]:
        """Accumulated group-path prefix if ``node`` is a Vapor RoutesBuilder.

        Returns ``None`` when ``node`` is not a builder rooted at a reserved
        receiver / bound builder variable — that ``None`` is the false-positive
        guard for arbitrary ``.grouped``/``.group`` chains on other types.
        """
        if node is None:  # pragma: no cover - defensive
            return None
        if node.type == "simple_identifier":
            name = node_text(node, source)
            if name in bindings:  # a binding wins over the reserved names
                # ``None`` is an explicit shadow — the name was rebound to a
                # non-builder in this scope, so it is no longer a route builder.
                bound = bindings[name]
                return list(bound) if bound is not None else None
            if name in _VAPOR_RECEIVERS:
                return []
            return None
        if node.type == "navigation_expression":
            # `app.routes` — the Application's RoutesBuilder (no path segment).
            recv, method = _swift_nav_receiver_method(node, source)
            if method == "routes":
                return resolve_builder(recv, bindings)
            return None
        if node.type == "call_expression":
            nav = find_child_by_type(node, "navigation_expression")
            if nav is None:
                return None
            recv, method = _swift_nav_receiver_method(nav, source)
            if method not in _VAPOR_GROUP_METHODS:
                return None
            base = resolve_builder(recv, bindings)
            if base is None:
                return None
            call_suffix = find_child_by_type(node, "call_suffix")
            return base + _swift_string_segments(call_suffix, source)
        return None

    def receiver_display(receiver: Optional["tree_sitter.Node"]) -> str:
        """A readable ``<name>`` for context_name — the root anchor of a chain."""
        node = receiver
        while node is not None:
            if node.type == "simple_identifier":
                return node_text(node, source)
            if node.type == "navigation_expression":
                node, _ = _swift_nav_receiver_method(node, source)
                continue
            if node.type == "call_expression":
                nav = find_child_by_type(node, "navigation_expression")
                node = nav
                continue
            return "route"  # pragma: no cover - defensive
        return "route"  # pragma: no cover - defensive

    def emit_route(
        segments: list[str], http_method: str,
        receiver: Optional["tree_sitter.Node"], method_name: str,
        node: "tree_sitter.Node",
    ) -> None:
        route_path = "/".join(segments)
        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )
        contexts.append(UsageContext.create(
            kind="call",
            context_name=f"{receiver_display(receiver)}.{method_name}",
            position="args[last]",
            path=str(file_path),
            span=span,
            metadata={"route_path": route_path, "http_method": http_method},
        ))
        route_symbols.append(make_route_symbol(
            language="swift",
            path=str(file_path),
            span=span,
            method=http_method,
            # Vapor route components arrive without the leading slash; the
            # factory normalizes an EMPTY path to "/", so the leading slash is
            # supplied here to keep the name and route_path byte-identical to
            # what this producer emitted before the migration.
            route_path=f"/{route_path}" if route_path else "/",
            origin=PASS_ID,
            origin_run_id=run_id,
            is_exported=True,
        ))

    def handle_call(call: "tree_sitter.Node", bindings: dict[str, Optional[list[str]]]) -> None:
        # A trailing-closure call ``f(args) { }`` parses two ways depending on
        # position: as one call_expression (nav + call_suffix holding BOTH the
        # value_arguments and the lambda), or — in an initializer — as a nested
        # call_expression (inner ``f(args)`` + an outer call_suffix holding just
        # the lambda). Normalize to (nav, path_suffix, closure_suffix).
        outer_suffix = find_child_by_type(call, "call_suffix")
        nav = find_child_by_type(call, "navigation_expression")
        if nav is not None:
            path_suffix = outer_suffix
            closure_suffix = outer_suffix
        else:
            inner = find_child_by_type(call, "call_expression")
            if inner is not None:
                nav = find_child_by_type(inner, "navigation_expression")
                path_suffix = find_child_by_type(inner, "call_suffix")
            else:
                path_suffix = outer_suffix
            closure_suffix = outer_suffix
        # Bindings used when descending into a trailing closure — extended only
        # for a `.group(...) { param in … }` sub-builder closure.
        descend_bindings = bindings
        if nav is not None:
            receiver, method = _swift_nav_receiver_method(nav, source)
            if method in _VAPOR_HTTP_METHODS:
                prefix = resolve_builder(receiver, bindings)
                if prefix is not None:
                    emit_route(
                        prefix + _swift_string_segments(path_suffix, source),
                        method.upper(), receiver, method, call,
                    )
            elif method == "on":
                prefix = resolve_builder(receiver, bindings)
                if prefix is not None:
                    verb, on_segs = _swift_on_verb_and_segments(path_suffix, source)
                    if verb in _VAPOR_ON_HTTP_METHODS:
                        emit_route(prefix + on_segs, verb, receiver, method, call)
            elif method in _VAPOR_GROUP_METHODS:
                prefix = resolve_builder(receiver, bindings)
                if prefix is not None:
                    param = _swift_lambda_param_name(closure_suffix, source)
                    if param is not None:
                        this_prefix = prefix + _swift_string_segments(path_suffix, source)
                        descend_bindings = {**bindings, param: this_prefix}
        # `handle_call` OWNS this call: descend into its trailing closure exactly
        # once (with any group binding) so nested route calls are found without
        # double-processing.
        if closure_suffix is not None:
            walk(closure_suffix, descend_bindings)

    def process_stmt(
        child: "tree_sitter.Node", bindings: dict[str, Optional[list[str]]],
    ) -> dict[str, Optional[list[str]]]:
        """Process one node, returning bindings visible to LATER siblings.

        A ``None`` value means a builder-capable name was shadowed/invalidated
        (see the reassignment cases below); the return type mirrors the
        ``bindings`` param, which already carries that Optional.
        """
        kind = child.type
        if kind == "property_declaration":
            result = bindings
            pattern = find_child_by_type(child, "pattern")
            name_id = (
                find_child_by_type(pattern, "simple_identifier")
                if pattern is not None else None
            )
            if name_id is not None:
                name = node_text(name_id, source)
                rhs = _swift_rhs_after_equals(child)
                prefix = resolve_builder(rhs, bindings) if rhs is not None else None
                if prefix is not None:
                    result = {**bindings, name: prefix}
                elif name in bindings or name in _VAPOR_RECEIVERS:
                    # A `let`/`var` binding a builder-capable name to a
                    # non-builder shadows it (kills the reserved-name anchor).
                    result = {**bindings, name: None}
            # Descend into the initializer/computed body so a route registered
            # there (e.g. `let route = app.get(...)`) is still found.
            walk(child, bindings)
            return result
        if kind == "assignment":
            result = bindings
            target = find_child_by_type(child, "directly_assignable_expression")
            name_id = (
                find_child_by_type(target, "simple_identifier")
                if target is not None else None
            )
            if name_id is not None:
                name = node_text(name_id, source)
                if name in bindings or name in _VAPOR_RECEIVERS:
                    rhs = _swift_rhs_after_equals(child)
                    # ``None`` when reassigned to a non-builder — invalidate /
                    # shadow (fail safe), never keep a stale prefix.
                    result = {
                        **bindings,
                        name: resolve_builder(rhs, bindings) if rhs is not None else None,
                    }
            walk(child, bindings)
            return result
        if kind == "function_declaration":
            # Seed a differently-named RoutesBuilder/Application parameter as a
            # builder root; seeds are scoped to this function's body.
            seeds = dict(bindings)
            for param in child.children:
                if param.type != "parameter":
                    continue
                pname, ptype = _swift_param_name_and_type(param, source)
                if pname is not None and pname != "_" and _swift_is_builder_type(ptype):
                    seeds[pname] = []
            walk(child, seeds)
            return bindings
        if kind == "call_expression":
            handle_call(child, bindings)
            return bindings
        walk(child, bindings)
        return bindings

    def walk(node: "tree_sitter.Node", bindings: dict[str, Optional[list[str]]]) -> None:
        current = bindings
        for child in node.children:
            current = process_stmt(child, current)

    walk(root_node, {})
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
