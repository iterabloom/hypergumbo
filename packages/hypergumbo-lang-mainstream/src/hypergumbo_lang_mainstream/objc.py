# SPDX-License-Identifier: AGPL-3.0-or-later
"""Objective-C analyzer using tree-sitter.

This analyzer extracts classes, protocols, methods, and properties from
Objective-C source files (.m, .mm, .h). It uses tree-sitter-objc for parsing
when available, falling back gracefully when the grammar is not installed.

Node types handled:
- class_interface: @interface declarations
- class_implementation: @implementation definitions
- protocol_declaration: @protocol definitions
- method_declaration: Method declarations in interfaces
- method_definition: Method implementations
- property_declaration: @property declarations
- preproc_include: #import statements
- message_expression: [receiver message] method calls

Three-pass analysis:
- Pass 1: Extract all symbols from all files and collect methods into a global registry
- Pass 1.5: Propagate each class's base_classes into its methods' meta['parent_base_classes'] (so .m @implementation methods inherit the .h @interface bases for framework pattern matching)
- Pass 2: Extract edges using the global symbol registry — resolve method-call edges (local, then cross-file via NameResolver), emit #import edges, and emit unresolved calls with a module hint for PascalCase (class) receivers.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Optional

from hypergumbo_core.discovery import classify_dot_m_file, find_files
from hypergumbo_core.ir import AnalysisRun, Edge, ExternalRef, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    TreeSitterAnalyzer,
    defer_bare_method_call,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    make_unresolved_edge,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("objc")


def _is_objc_tree_sitter_available_legacy() -> bool:  # pragma: no cover - replaced by TreeSitterAnalyzer
    """Legacy availability check -- replaced by TreeSitterAnalyzer._check_grammar_available."""
    pass


def find_objc_files(root: Path) -> list[Path]:
    """Find Objective-C files, disambiguating .m files via content heuristics.

    Extensions:
    - .m: Shared with MATLAB and Wolfram — classified by content
    - .mm: Objective-C++ (unambiguous)
    - .h: Header files (unambiguous for Objective-C analysis)
    """
    # Unambiguous extensions
    result = list(find_files(root, ["*.mm", "*.h"]))
    # Ambiguous .m files — only include if classified as Objective-C
    result.extend(p for p in find_files(root, ["*.m"]) if classify_dot_m_file(p) == "objc")
    return result


def _extract_type_name(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract type name from a type_name node, handling pointers."""
    parts: list[str] = []
    for child in node.children:
        if child.type in ("primitive_type", "type_identifier"):
            parts.append(node_text(child, source))
        elif child.type == "abstract_pointer_declarator":
            parts.append("*")
    return "".join(parts)


def _extract_objc_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract method signature from an Objective-C method declaration/definition.

    Returns signature like:
    - "(int x, int y): int" for methods with return type
    - "(NSString* message)" for void methods (void omitted)
    - "(): NSString*" for no-params methods with return type

    Args:
        node: The method_declaration or method_definition node.
        source: The source code bytes.

    Returns:
        The signature string, or None if extraction fails.
    """
    params: list[str] = []
    return_type: Optional[str] = None

    for child in node.children:
        if child.type == "method_type":
            # This is the return type
            type_name_node = find_child_by_type(child, "type_name")
            if type_name_node:
                return_type = _extract_type_name(type_name_node, source)
        elif child.type == "method_parameter":
            # Extract parameter type and name
            param_type = None
            param_name = None
            for subchild in child.children:
                if subchild.type == "method_type":
                    type_name_node = find_child_by_type(subchild, "type_name")
                    if type_name_node:
                        param_type = _extract_type_name(type_name_node, source)
                elif subchild.type == "identifier":
                    param_name = node_text(subchild, source)
            if param_type and param_name:
                params.append(f"{param_type} {param_name}")

    params_str = ", ".join(params)
    signature = f"({params_str})"

    if return_type and return_type != "void":
        signature += f": {return_type}"

    return signature


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file."""

    symbols: list[Symbol] = field(default_factory=list)
    symbol_by_name: dict[str, Symbol] = field(default_factory=dict)
    methods_by_name: dict[str, Symbol] = field(default_factory=dict)
    current_class: str | None = None


def _extract_class_name(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract class name from class_interface or class_implementation node."""
    # Find the identifier that follows @interface or @implementation
    for child in node.children:
        if child.type == "identifier":
            return node_text(child, source)
    return None  # pragma: no cover


def _objc_category_name(node: "tree_sitter.Node", source: bytes) -> str | None:
    """The category name of ``@interface Cls (Cat)`` / ``@implementation Cls (Cat)``, else ``None``.

    tree-sitter-objc shapes a category as the same ``class_interface`` /
    ``class_implementation`` node with ``( identifier )`` after the class
    identifier, so the class symbol minted for it carries the FRAMEWORK class's
    name. WI-higob reads this to keep ``UIImage`` / ``NSData`` out of the
    project-class set: a category extends a class the repo does not define.
    """
    seen_paren = False
    for child in node.children:
        if child.type == "(":
            seen_paren = True
        elif seen_paren and child.type == "identifier":
            return node_text(child, source)
    return None


def _extract_base_classes_objc(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract base classes/protocols from Objective-C class interface.

    Objective-C uses single inheritance and multiple protocol conformance:
        @interface Dog : Animal <MyProtocol>      -> ["Animal", "MyProtocol"]
        @interface Cat : Animal <A, B>            -> ["Animal", "A", "B"]

    The AST structure:
    - First identifier = class name
    - Second identifier (after `:`) = superclass
    - parameterized_arguments contains protocol conformance
    """
    base_classes: list[str] = []
    seen_class_name = False
    seen_colon = False

    for child in node.children:
        if child.type == "identifier":
            if not seen_class_name:
                # First identifier is the class name, skip it
                seen_class_name = True
            elif seen_colon:
                # Identifier after `:` is the superclass
                base_classes.append(node_text(child, source))
        elif child.type == ":":
            seen_colon = True
        elif child.type == "parameterized_arguments":
            # Protocol conformance: <ProtocolA, ProtocolB>
            for param_child in child.children:
                if param_child.type == "type_name":
                    type_id = find_child_by_type(param_child, "type_identifier")
                    if type_id:
                        base_classes.append(node_text(type_id, source))

    return base_classes


def _extract_protocol_name(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract protocol name from protocol_declaration node."""
    for child in node.children:
        if child.type == "identifier":
            return node_text(child, source)
    return None  # pragma: no cover


def _extract_method_name(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract method selector from method_declaration or method_definition.

    Objective-C selectors include colons for keyword parts:
    ``-(void)setX:(int)x Y:(int)y`` → ``setX:Y:``

    The tree-sitter-objc AST interleaves identifier (selector keyword) and
    method_parameter nodes: ``identifier(setX) method_parameter(:int x)
    identifier(Y) method_parameter(:int y)``.  An identifier followed by a
    method_parameter is a keyword part and gets ``:`` appended.
    """
    parts: list[str] = []
    children = node.children
    for i, child in enumerate(children):
        if child.type == "identifier":
            name = node_text(child, source)
            # Keyword selector: identifier followed by method_parameter
            next_child = children[i + 1] if i + 1 < len(children) else None
            if next_child is not None and next_child.type == "method_parameter":
                parts.append(name + ":")
            else:
                parts.append(name)

    if parts:
        return "".join(parts)
    return None  # pragma: no cover


def _extract_property_name(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract property name from property_declaration node."""
    # Property declaration has a struct_declaration child with the var name
    struct_decl = find_child_by_type(node, "struct_declaration")
    if struct_decl:
        for child in struct_decl.children:
            if child.type == "struct_declarator":
                # May be pointer_declarator or identifier
                for decl_child in child.children:
                    if decl_child.type == "pointer_declarator":
                        for ptr_child in decl_child.children:
                            if ptr_child.type == "identifier":
                                return node_text(ptr_child, source)
                    elif decl_child.type == "identifier":
                        return node_text(decl_child, source)
    return None  # pragma: no cover


def _is_class_method(node: "tree_sitter.Node") -> bool:  # pragma: no cover - unused
    """Check if a method is a class method (starts with +)."""
    for child in node.children:
        if child.type == "+":
            return True
        if child.type == "-":
            return False
    return False  # default to instance


def _get_enclosing_class_objc(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Walk up the tree to find enclosing class/implementation name."""
    current = node.parent
    while current is not None:
        if current.type in ("class_interface", "class_implementation"):
            return _extract_class_name(current, source)
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
    repo_rel_path: str,
) -> FileAnalysis:
    """Extract symbols from a single Objective-C file.

    Uses iterative traversal to avoid RecursionError on deeply nested code.

    ``repo_rel_path`` is the repo-relative path of this file (computed in
    ``analyze()`` via ``relative_to(repo_root)``). ``file_path`` / the legacy
    ``rel_path`` local below are absolute (``find_objc_files`` yields absolute
    paths), so the WI-bokab file-identity anchor MUST be derived from
    ``repo_rel_path`` — folding the absolute path would make stable_ids
    location-dependent (a regression).
    """
    analysis = FileAnalysis()
    rel_path = str(file_path)
    # WI-bokab (v7): file-identity anchor for this file's symbols. Folded into
    # compute_stable_id's containing slot so same-(kind, name, qualified_name)
    # symbols in different files hash distinctly. Uses the repo-relative path
    # (NOT the absolute ``rel_path`` above) so the id is location-independent.
    file_stable_id = _analyzer._file_anchor(repo_rel_path)

    try:
        source = file_path.read_bytes()
    except (OSError, IOError):  # pragma: no cover
        return analysis

    tree = parser.parse(source)

    for node in iter_tree(tree.root_node):
        if node.type in ("class_interface", "class_implementation"):
            class_name = _extract_class_name(node, source)
            if class_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("objc", rel_path, start_line, end_line, class_name, "class")

                # Extract base classes/protocols for inheritance linker
                base_classes = _extract_base_classes_objc(node, source)
                _class_meta: dict[str, Any] = {}
                if base_classes:
                    _class_meta["base_classes"] = base_classes
                _category = _objc_category_name(node, source)
                if _category:
                    _class_meta["category"] = _category
                meta = _class_meta or None

                symbol = Symbol(
                    id=symbol_id,
                    name=class_name,
                    kind="class",
                    language="objc",
                    path=rel_path,
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta=meta,
                    stable_id=_analyzer.compute_stable_id(node, kind="class", name=class_name, file_stable_id=file_stable_id),
                    shape_id=_analyzer.compute_shape_id(node),
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[class_name] = symbol

        elif node.type == "protocol_declaration":
            protocol_name = _extract_protocol_name(node, source)
            if protocol_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("objc",
                    rel_path, start_line, end_line, protocol_name, "protocol"
                )

                symbol = Symbol(
                    id=symbol_id,
                    name=protocol_name,
                    kind="protocol",
                    language="objc",
                    path=rel_path,
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    stable_id=_analyzer.compute_stable_id(node, kind="protocol", name=protocol_name, file_stable_id=file_stable_id),
                    shape_id=_analyzer.compute_shape_id(node),
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[protocol_name] = symbol

        elif node.type in ("method_declaration", "method_definition"):
            method_name = _extract_method_name(node, source)
            if method_name:
                # Prefix with class name if inside a class
                current_class = _get_enclosing_class_objc(node, source)
                full_name = f"{current_class}.{method_name}" if current_class else method_name
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("objc", rel_path, start_line, end_line, full_name, "method")

                # Extract signature
                signature = _extract_objc_signature(node, source)

                symbol = Symbol(
                    id=symbol_id,
                    name=full_name,
                    kind="method",
                    language="objc",
                    path=rel_path,
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    signature=signature,
                    stable_id=_analyzer.compute_stable_id(node, kind="method", name=full_name, file_stable_id=file_stable_id),
                    shape_id=_analyzer.compute_shape_id(node),
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, "objc"),
                    line_span=end_line - start_line + 1,
                )
                analysis.symbols.append(symbol)
                analysis.methods_by_name[method_name] = symbol

        elif node.type == "property_declaration":
            prop_name = _extract_property_name(node, source)
            if prop_name:
                current_class = _get_enclosing_class_objc(node, source)
                full_name = f"{current_class}.{prop_name}" if current_class else prop_name
                start_line = node.start_point[0] + 1
                symbol_id = make_symbol_id("objc", rel_path, start_line, start_line, full_name, "property")

                symbol = Symbol(
                    id=symbol_id,
                    name=full_name,
                    kind="property",
                    language="objc",
                    path=rel_path,
                    span=Span(
                        start_line=start_line,
                        end_line=start_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    stable_id=_analyzer.compute_stable_id(node, kind="property", name=full_name, file_stable_id=file_stable_id),
                    shape_id=_analyzer.compute_shape_id(node),
                )
                analysis.symbols.append(symbol)

    return analysis


def _extract_import_path(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract import path from preproc_include node."""
    # Check for system_lib_string (<...>)
    for child in node.children:
        if child.type == "system_lib_string":
            text = node_text(child, source)
            # Remove angle brackets
            return text.strip("<>")
        elif child.type == "string_literal":
            # Local import "..."
            for str_child in child.children:
                if str_child.type == "string_content":
                    return node_text(str_child, source)
    return None  # pragma: no cover


def _extract_message_selector(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract the selector from a message_expression.

    ``[receiver selectorPart1:arg selectorPart2:arg2]`` → ``selectorPart1:selectorPart2:``

    The tree-sitter-objc grammar flattens keyword messages — selector keywords,
    colons, and arguments are all direct children of ``message_expression``::

        message_expression
          [ identifier(receiver) identifier(removeItemAtPath) : identifier(path) identifier(error) : &err ]

    Selector keywords are identifiers followed by ``:``; argument identifiers
    follow ``:`` and must be skipped.  Simple messages like ``[obj doSomething]``
    have a single identifier after the receiver with no colon.
    """
    parts: list[str] = []
    seen_receiver = False
    children = node.children

    for i, child in enumerate(children):
        if child.type == "identifier":
            if not seen_receiver:
                # First identifier is the receiver — skip it
                seen_receiver = True
                continue
            # Check if NEXT sibling is ":"
            next_child = children[i + 1] if i + 1 < len(children) else None
            if next_child is not None and next_child.type == ":":
                # Keyword part: identifier before ":"
                parts.append(node_text(child, source) + ":")
            elif not parts:
                # Simple message (no colons) like [obj doSomething]
                parts.append(node_text(child, source))
            # Otherwise it's an argument identifier — skip
        elif child.type == "message_expression":
            # Nested message like [[obj alloc] init] — receiver is another message
            if not seen_receiver:
                seen_receiver = True

    if parts:
        return "".join(parts)
    return None  # pragma: no cover


def _extract_message_receiver(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract the receiver identifier from a ``message_expression``.

    Returns the receiver name when it is a simple identifier (e.g.,
    ``self``, ``NSString``, ``viewModel``). Returns None for nested
    message receivers like ``[[obj alloc] init]`` — there is no single
    receiver name to attribute the call to.

    WI-nigah Tier 2 uses this in conjunction with the ObjC convention
    that class names are PascalCase: receivers whose first letter is
    uppercase are treated as class-message targets and become the
    ``module_path`` of the structured ``dst_ref``. Lowercase receivers
    (``self``, ``super``, local vars) get no ``dst_ref``.
    """
    for child in node.children:
        if child.type == "identifier":
            return node_text(child, source)
        if child.type == "message_expression":
            return None
    return None  # pragma: no cover - defensive


def _get_enclosing_method_objc(
    node: "tree_sitter.Node",
    source: bytes,
    local_methods: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find enclosing method definition."""
    current = node.parent
    while current is not None:
        if current.type == "method_definition":
            method_name = _extract_method_name(current, source)
            if method_name and method_name in local_methods:
                return local_methods[method_name]
        current = current.parent
    return None  # pragma: no cover - defensive


def _first_descendant(node: "tree_sitter.Node", kind: str) -> "tree_sitter.Node | None":
    """The first descendant of ``kind`` in document order, or ``None``."""
    for sub in iter_tree(node):
        if sub.type == kind:
            return sub
    return None


def _objc_declared_receiver_types(
    root: "tree_sitter.Node", source: bytes,
) -> list[tuple[int, int, dict[str, str]]]:
    """Per method / function body: ``(start_byte, end_byte, {receiver name: declared class})``.

    WI-higob (objc). Two declaration shapes carry a receiver's class:
    a typed parameter ``(NSFileManager *)fm`` (``method_parameter`` ->
    ``type_identifier`` + trailing ``identifier``) and a local
    ``NSFileManager *x = ...`` (``declaration`` -> ``type_identifier`` +
    the identifier under its declarator). ``id obj`` has no
    ``type_identifier`` and stays untyped. Last declaration wins within a
    body, which is what a rebinding means in straight-line code.
    """
    spans: list[tuple[int, int, dict[str, str]]] = []
    for node in iter_tree(root):
        if node.type not in ("method_definition", "function_definition"):
            continue
        types: dict[str, str] = {}
        for sub in iter_tree(node):
            if sub.type == "method_parameter":
                t = _first_descendant(sub, "type_identifier")
                names = [c for c in sub.children if c.type == "identifier"]
                if t is not None and names:
                    types[node_text(names[-1], source)] = node_text(t, source)
            elif sub.type == "declaration":
                t = next((c for c in sub.children if c.type == "type_identifier"), None)
                if t is None:
                    continue
                for c in sub.children:
                    if c.type in ("init_declarator", "pointer_declarator", "identifier"):
                        name = c if c.type == "identifier" else _first_descendant(c, "identifier")
                        if name is not None:
                            types[node_text(name, source)] = node_text(t, source)
        spans.append((node.start_byte, node.end_byte, types))
    return spans


def _objc_receiver_type(
    spans: list[tuple[int, int, dict[str, str]]],
    node: "tree_sitter.Node",
    name: str,
) -> str | None:
    """The declared class of receiver ``name`` in the innermost body holding ``node``."""
    best: str | None = None
    best_len: int | None = None
    for start, end, types in spans:
        if start <= node.start_byte < end and name in types:
            if best_len is None or end - start < best_len:
                best, best_len = types[name], end - start
    return best


def _extract_edges_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    local_methods: dict[str, Symbol],
    method_resolver: NameResolver,
    run: AnalysisRun,
    project_classes: frozenset[str] = frozenset(),
) -> list[Edge]:
    """Extract edges from a file using global symbol knowledge.

    Uses iterative traversal to avoid RecursionError on deeply nested code.
    """
    edges: list[Edge] = []
    _caller_path = str(file_path)
    rel_path = str(file_path)
    file_id = make_file_id("objc", rel_path)

    try:
        source = file_path.read_bytes()
    except (OSError, IOError):  # pragma: no cover
        return edges

    tree = parser.parse(source)
    _recv_spans = _objc_declared_receiver_types(tree.root_node, source)

    for node in iter_tree(tree.root_node):
        # Handle imports
        if node.type == "preproc_include":
            # Check if it's #import (not #include)
            has_import = any(c.type == "#import" for c in node.children)
            if has_import:
                import_path = _extract_import_path(node, source)
                if import_path:
                    line = node.start_point[0] + 1
                    edges.append(Edge.create(
                        src=file_id,
                        dst=import_path,
                        edge_type="imports",
                        line=line,
                        evidence_type="import_statement",
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))

        # Handle message expressions (method calls)
        elif node.type == "message_expression":
            selector = _extract_message_selector(node, source)
            current_method = _get_enclosing_method_objc(node, source, local_methods)
            if selector and current_method is not None:
                line = node.start_point[0] + 1

                # Try local match first (same-file, higher confidence)
                if selector in local_methods:
                    callee = local_methods[selector]
                    edges.append(Edge.create(
                        src=current_method.id,
                        dst=callee.id,
                        edge_type="calls",
                        line=line,
                        evidence_type="message_send",
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))
                else:
                    # Try cross-file resolution via resolver.
                    # INV-fahub: ``global_methods`` is keyed by the bare
                    # SELECTOR (short name), so every resolver hit is an
                    # *exact short-name* match that collapses all same-named
                    # methods across classes to one arbitrary def — a magnet
                    # (many receiver-blind message sends → one def). That is
                    # weak cross-class evidence, so it is flagged ``"suffix"``
                    # (as the Scala/Swift bare-call gate does for a short-name
                    # hit) and run through ``defer_bare_method_call``: a
                    # DIFFERENT class's method is WITHHELD — deferred to the
                    # inherited_calls Site-1 walker via ``enclosing_class`` —
                    # while a same-class implicit-``self`` hit (owner ==
                    # enclosing) still binds directly.
                    _enclosing_type = _get_enclosing_class_objc(node, source)
                    lookup_result = method_resolver.lookup(selector, caller_path=_caller_path)
                    _sym = lookup_result.symbol
                    _defer = _sym is not None and defer_bare_method_call(
                        _sym.kind, _sym.name, "suffix", _enclosing_type,
                    )
                    if lookup_result.found and _sym is not None and not _defer:
                        edges.append(Edge.create(
                            src=current_method.id,
                            dst=_sym.id,
                            edge_type="calls",
                            line=line,
                            evidence_type="message_send",
                            confidence=0.75 * lookup_result.confidence,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            meta={"call_locality": "cross_file"},
                        ))
                    else:
                        # Not found, or a deferred cross-class magnet. Emit an
                        # honest unresolved edge carrying ``enclosing_class`` so
                        # the inherited_calls Site-1 walker can later recover a
                        # genuine inherited implicit-``self`` call.
                        # WI-nigah Tier 2: if the receiver looks like an
                        # ObjC class name (PascalCase), use it as the
                        # ``module_path`` for the structured ``dst_ref``.
                        # ``self`` / ``super`` / local-var receivers
                        # (lowercase) get no dst_ref — no module signal.
                        #
                        # INV-fibis disclosure parity: BOTH branches stamp
                        # ``call_construct="method"``. An objc message send IS a
                        # method call by construction — there is no free-function
                        # message — so the stamp is unconditional here, unlike
                        # java/rust where a receiver has to be present. Without
                        # it, objc reached a clean boundary verdict over 122
                        # catalogued method sinks (95% of its catalogue) and
                        # named none of them.
                        # WI-higob: a lowercase receiver the body DECLARES
                        # (``NSFileManager *fm``, a ``(NSFileManager *)fm``
                        # parameter) carries that class the way a class
                        # message does -- the catalogue's objc rows key by
                        # bare class, so the declaration is the whole match.
                        # A PROJECT class is a symbol, not a module, and rides
                        # in ``receiver_type_hint`` only; the class-MESSAGE
                        # arm keeps its WI-nigah behaviour unchanged (a
                        # project class in that slot is WI-marok's question).
                        receiver_name = _extract_message_receiver(node, source)
                        _declared = (
                            _objc_receiver_type(_recv_spans, node, receiver_name)
                            if receiver_name and not receiver_name[0].isupper()
                            else None
                        )
                        if receiver_name and receiver_name[0].isupper():
                            _module: str | None = receiver_name
                        elif _declared is not None and _declared not in project_classes:
                            _module = _declared
                        else:
                            _module = None
                        if _module is not None:
                            edges.append(make_unresolved_edge(
                                "objc", current_method.id, selector,
                                line, PASS_ID, run.execution_id,
                                module_hint=_module,
                                dst_ref=ExternalRef(
                                    lang="objc",
                                    module_path=_module,
                                    name=selector,
                                ),
                                enclosing_class=_enclosing_type,
                                receiver_type_hint=_declared,
                                call_construct="method",
                            ))
                        else:
                            edges.append(make_unresolved_edge(
                                "objc", current_method.id, selector,
                                line, PASS_ID, run.execution_id,
                                enclosing_class=_enclosing_type,
                                receiver_type_hint=_declared,
                                call_construct="method",
                            ))

    return edges


class ObjCAnalyzer(TreeSitterAnalyzer):
    """Objective-C analyzer using tree-sitter-objc.

    Overrides ``analyze()`` because Objective-C uses a custom two-pass
    structure with per-file FileAnalysis, global method registry, and
    cross-file message-send resolution.
    """

    lang = "objc"
    file_patterns: ClassVar[list[str]] = ["*.m", "*.mm", "*.h"]
    grammar_module = "tree_sitter_objc"

    def analyze(
        self,
        repo_root: Path,
        max_files: Optional[int] = None,
    ) -> AnalysisResult:
        """Run Objective-C analysis with two-pass symbol/edge extraction."""
        if not self._check_grammar_available():
            warnings.warn(
                f"{self.lang} analysis skipped: grammar not available. "
                f"Install the required tree-sitter grammar package.",
                UserWarning,
                stacklevel=2,
            )
            return AnalysisResult(
                skipped=True,
                skip_reason=f"{self.lang} tree-sitter grammar not available",
            )

        try:
            parser = self._create_parser()
        except Exception as e:
            return AnalysisResult(
                skipped=True,
                skip_reason=f"Failed to load Objective-C parser: {e}",
            )

        run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

        all_files = find_objc_files(repo_root)
        if not all_files:  # pragma: no cover - no ObjC files in test
            return AnalysisResult(run=run)

        # Pass 1: Extract symbols from all files
        all_symbols: list[Symbol] = []
        file_analyses: dict[Path, FileAnalysis] = {}
        global_methods: dict[str, Symbol] = {}

        for objc_file in all_files:
            # WI-bokab (v7): repo-relative path for the file-identity anchor.
            # ``find_objc_files`` yields absolute paths, so we derive the
            # repo-relative form here (where ``repo_root`` is in scope) and
            # thread it down — folding an absolute path would make stable_ids
            # location-dependent.
            repo_rel_path = str(objc_file.relative_to(repo_root))
            analysis = _extract_symbols_from_file(
                objc_file, parser, run, repo_rel_path
            )
            file_analyses[objc_file] = analysis
            all_symbols.extend(analysis.symbols)

            # Collect methods globally for cross-file resolution
            for selector, sym in analysis.methods_by_name.items():
                global_methods[selector] = sym

        # Pass 1.5: Propagate parent_base_classes to methods
        # Class @interface (in .h) declares base_classes; methods in
        # @implementation (in .m) need those as parent_base_classes for
        # framework pattern matching (e.g., UIKit lifecycle hooks).
        class_bases: dict[str, list[str]] = {}
        for sym in all_symbols:
            if sym.kind == "class" and sym.meta and sym.meta.get("base_classes"):
                class_bases[sym.name] = sym.meta["base_classes"]

        for sym in all_symbols:
            if sym.kind == "method" and "." in sym.name:
                class_name = sym.name.rsplit(".", 1)[0]
                bases = class_bases.get(class_name)
                if bases:
                    if sym.meta is None:
                        sym.meta = {}
                    sym.meta["parent_base_classes"] = bases

        # Pass 2: Extract edges using global symbol knowledge
        method_resolver = NameResolver(global_methods)
        all_edges: list[Edge] = []

        # WI-higob: a class the repo DECLARES (not merely extends with a
        # category) -- `UIImage (Transform)` does not make UIImage a project
        # class, and its catalogued rows stay reachable through the slot.
        _project_classes = frozenset(
            sym.name for sym in all_symbols
            if sym.kind == "class" and not (sym.meta or {}).get("category")
        )
        for objc_file, analysis in file_analyses.items():
            edges = _extract_edges_from_file(
                objc_file, parser, analysis.methods_by_name, method_resolver, run,
                project_classes=_project_classes,
            )
            all_edges.extend(edges)

        return AnalysisResult(
            symbols=all_symbols,
            edges=all_edges,
            run=run,
        )


_analyzer = ObjCAnalyzer()


def is_objc_tree_sitter_available() -> bool:
    """Check if tree-sitter with Objective-C grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("objc")
def analyze_objc(root: Path) -> AnalysisResult:
    """Analyze Objective-C files in a directory."""
    return _analyzer.analyze(root)
