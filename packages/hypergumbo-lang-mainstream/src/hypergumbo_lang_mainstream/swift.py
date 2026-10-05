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
- Route-marker symbols, extracted per file and appended in ``post_process``
- Function call relationships, and ``references`` edges where a symbol is
  named without being called
- Import statements
- Usage contexts for Vapor/Hummingbird route registrations

If tree-sitter with Swift support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract functions, classes, structs, protocols, enums with signatures.
   Each function's declared return type feeds the base return-type registry
   (``_swift_return_type_name``), each class-level property's type the
   field-type registry (``_register_swift_field_type``), and each
   declaration's argument labels ``meta["arg_labels"]``.
2. Pass 2: Extract call, import and ``references`` edges using NameResolver.
   A receiver is typed before resolution: locals, parameters (a closure's
   annotated ones too) and every ``let`` / ``var`` clause of an ``if`` /
   ``guard`` / ``while`` condition from a scope map per callable body --
   function, closure, initialiser, subscript, accessor, protocol requirement
   -- and per block (``_SWIFT_BLOCK_NODES``), in which a binding is visible
   only from the end of its declaration and the innermost visible binding
   decides. A declaration no reader can type -- an unannotated closure
   parameter, a loop variable, a ``catch`` / ``case let`` / tuple name, a local
   whose initialiser nothing types -- still SHADOWS and answers "unknown",
   never the outer name's type (WI-silos). A declaration in no scope is a
   type's MEMBER, recorded under its owning type, or a true global; a bare
   name not bound locally resolves to the enclosing type's members (through
   its bases) before the file's globals, so one type's property never types
   another type's receiver (WI-tagir). Every expression -- a receiver and a
   binding's initialiser alike -- is typed by one walker
   (``_swift_receiver_expr_type``) by what it EVALUATES to: a call result
   through the return-type registry, walking the owner's bases (a method
   inherited from a test base, XCTest's ``expectation``); a member chain
   (``config.fileManager``) through the head's type and that type's declared
   member; a literal by its default type; ``??`` and ``?:`` by the type every
   operand agrees on; a cast, a force unwrap or a constructor (``_T()``,
   ``Self()``, ``T.init()``) by the type it names (WI-hojib).
   - A ``Type.method`` bind is refused when the call supplies a label no
     overload declares (``_swift_labels_admit_call``, INV-fatap), or when a
     ``static`` / ``class`` member is called on an instance
     (``_static_member_on_instance``).
   - A receiver call that does not resolve becomes an unresolved edge rather
     than a short-name guess: an EXTERNAL receiver type (``FileManager``)
     fills the ``ExternalRef`` module slot, a project type rides only in
     ``meta["receiver_type_hint"]``.
   - A bare call to a different type's method on short-name evidence is
     deferred with ``enclosing_class`` for the ``inherited_calls`` linker;
     a call in no function is anchored on its enclosing class or protocol
     body, else the file (INV-bamij).

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Swift-specific extraction
logic -- plus one override of ``parse_source``, which gives a backtick raw
identifier containing spaces (Swift 6.1's spelling for a test name) one
canonical, underscored spelling, so a symbol's name and id do not depend on
the grammar release (INV-bisok; tree-sitter-swift 0.7.3 could not parse that
spelling, nor ``try`` in an ``if`` / ``guard`` / ``while`` condition, and its
files were retried through a rewrite; 0.7.4 parses both).

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
route-marker symbols added in ``post_process``. A type recovered from an
ERROR node carries no modifiers and leaves ``is_exported`` unset (``None``).
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable, ClassVar, Iterator, NamedTuple, Optional

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
    file_anchor_symbol,
    enclosing_declared_symbol,
    make_file_id,
    make_file_stable_id,
    make_route_symbol,
    make_symbol_id,
    make_typed_stable_id,
    make_unresolved_edge,
    node_text,
    symbol_declared_by,
    symbols_at,
    SymbolsAt,
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

#: INV-bamij: declarations whose BODY anchors a call that sits in no function.
_TYPE_BODY_NODES: frozenset[str] = frozenset({"class_declaration", "protocol_declaration"})

#: WI-mofil: the nodes whose body is a receiver-typing SCOPE -- one map each,
#: looked up innermost-first. Every callable body, not only ``func``: a closure,
#: an initialiser / deinitialiser, a subscript, and each property accessor
#: (``computed_property`` is the shorthand getter's body and holds ``get`` /
#: ``set``; ``willSet`` / ``didSet`` are clauses of their own).
#: WI-silos: a ``protocol_function_declaration`` too -- it has no body, but its
#: parameters reached the FILE-level map without one and typed a same-named
#: receiver in every function of the file.
_SWIFT_SCOPE_NODES: frozenset[str] = frozenset({
    "function_declaration", "lambda_literal", "init_declaration",
    "deinit_declaration", "subscript_declaration", "computed_property",
    "computed_getter", "computed_setter", "willset_clause", "didset_clause",
    "protocol_function_declaration",
})

#: WI-silos: the BLOCK scopes inside a callable body. A ``statements`` node is
#: every brace-delimited body (``if`` / ``else`` / ``for`` / ``while`` / ``do`` /
#: ``catch`` / ``repeat`` / ``switch`` case / ``guard``'s ``else``), so a local
#: declared in one ends with it. The four constructs that bind a name OUTSIDE
#: their body are scopes too, so the name covers what Swift lets it cover: an
#: ``if`` / ``while`` condition clause binds the rest of the condition and the
#: body (an ``if``'s ``else`` is excluded by position, see ``_bind``); a ``for``
#: loop variable binds its ``where`` clause and body, not the sequence; a
#: ``catch`` / ``case`` pattern binds its body. ``guard`` is deliberately NOT
#: here: its clauses bind the rest of the ENCLOSING block.
_SWIFT_BLOCK_NODES: frozenset[str] = frozenset({
    "statements", "if_statement", "while_statement", "for_statement",
    "catch_block", "switch_entry",
})

#: A scope's key in the receiver-typing map: ``(node type, start byte, end byte)``.
_ScopeKey = tuple[str, int, int]


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
    decl_index: SymbolsAt,
) -> Optional[Symbol]:
    """The function, computed property or subscript whose declaration contains
    ``node``.

    Keyed by the declaration's POSITION, not its name (INV-midag). The key was
    the qualified name, which every overload of a method shares: Alamofire's
    ``Session.webSocketRequest``, ``HTTPHeaders.add`` and
    ``DataRequest.serializingResponse``. So 261 of 8,343 swift call edges on a
    26-repo run were anchored to an overload that does not contain the call.
    A declaration with no symbol is walked past.
    """
    current = node.parent
    while current is not None:
        if (
            current.type in ("function_declaration", "subscript_declaration")
            or (current.type == "property_declaration"
                and find_child_by_type(current, "computed_property"))
        ):
            sym = symbol_declared_by(current, decl_index)
            if sym is not None:
                return sym
        current = current.parent
    return None  # pragma: no cover - defensive


def _swift_bare_type(text: str) -> str | None:
    """``FileManager`` for ``FileManager``, ``FileManager?``, ``Result<T, E>``; ``None`` otherwise.

    WI-higob. The registry's VALUE must be the receiver-typing name the
    catalogue keys by, so an optional is unwrapped and generic arguments are
    dropped; a collection, tuple or function type is not a receiver type
    the catalogue knows and yields ``None``.
    """
    t = text.strip().rstrip("?!").strip()
    if "<" in t:
        t = t.split("<", 1)[0]
    if not t or not _swift_is_type_spelling(t) or any(c in t for c in "[]()-> ,"):
        return None
    return t


def _swift_is_type_spelling(name: str) -> bool:
    """Is ``name`` spelled like a TYPE (``FileManager``, ``_URLEncodedFormDecoder``)?

    WI-hojib. Swift spells types capitalised and values lowercase, and a leading
    underscore marks an implementation-private name of EITHER kind: hummingbird's
    ``_URLEncodedFormDecoder`` is a type, Kingfisher's ``_config`` a value. Reading
    the first character alone made every underscored name neither -- a
    constructor ``_URLEncodedFormDecoder(...)`` typed nothing, and a value head
    ``_storage.p.m()`` was not recognised as a value chain. This is the one rule;
    :func:`_swift_is_value_spelling` is its counterpart.
    """
    return name.lstrip("_")[:1].isupper()


def _swift_is_value_spelling(name: str) -> bool:
    """Is ``name`` spelled like a VALUE (``session``, ``_config``)? See
    :func:`_swift_is_type_spelling`."""
    return name.lstrip("_")[:1].islower()


#: WI-hojib: a literal's DEFAULT type -- what it evaluates to with no contextual
#: type, which is the case for an unannotated binding (``var rfc1123 = ""``) and
#: for a literal receiver (``"x".write(toFile:...)``). A collection literal is not
#: here: ``_swift_bare_type`` refuses collections as receiver types, and ``nil``
#: and a regex literal name no catalogue receiver.
_SWIFT_LITERAL_TYPES: dict[str, str] = {
    "line_string_literal": "String",
    "multi_line_string_literal": "String",
    "raw_string_literal": "String",
    "integer_literal": "Int",
    "hex_literal": "Int",
    "oct_literal": "Int",
    "bin_literal": "Int",
    "real_literal": "Double",
    "boolean_literal": "Bool",
}


def _swift_type_parameter_names(
    node: "tree_sitter.Node", source: bytes,
) -> set[str]:
    """The generic parameter names in scope at a declaration.

    WI-higob. ``func map<U>(...) -> U`` returns a name that is a type only
    INSIDE the declaration; at a call site it names nothing, so registering it
    puts a meaningless module in the slot. The constraint in ``<V: Codable>`` is
    nested under a ``user_type`` rather than being a direct child, so reading
    the first ``type_identifier`` of each ``type_parameter`` takes the parameter
    name and never its bound.
    """
    names: set[str] = set()
    cur: "tree_sitter.Node | None" = node
    while cur is not None:
        if cur.type in (
            "function_declaration", "class_declaration", "protocol_declaration",
        ):
            params = find_child_by_type(cur, "type_parameters")
            for tp in (params.children if params is not None else []):
                if tp.type == "type_parameter":
                    ident = find_child_by_type(tp, "type_identifier")
                    if ident is not None:
                        names.add(node_text(ident, source))
        if cur.type == "protocol_declaration":
            # WI-hojib: a protocol's ``associatedtype`` is its generic parameter
            # (hummingbird's ``associatedtype Responder: HTTPResponder`` then
            # ``var responder: Responder { get }``): a conformer binds it.
            body = find_child_by_type(cur, "protocol_body")
            for decl in (body.children if body is not None else []):
                if decl.type == "associatedtype_declaration":
                    ident = find_child_by_type(decl, "type_identifier")
                    if ident is not None:
                        names.add(node_text(ident, source))
        cur = cur.parent
    return names


def _swift_return_type_name(node: "tree_sitter.Node", source: bytes) -> str | None:
    """The bare declared return type of a ``function_declaration``, or ``None``.

    WI-higob (INV-dihos phase 6). Mirrors :func:`_extract_swift_signature`'s
    walk -- the type node after the parameter list's closing paren -- but
    keeps only what :func:`_swift_bare_type` admits, so the registry never
    carries ``[String]`` or ``() -> Void`` as a receiver type.

    Two spellings name a type only from INSIDE the declaration and were
    measured putting a meaningless module in the slot once slice 2 let a chained
    receiver read this registry: ``Self`` (76 sites on Alamofire, every one a
    fluent ``-> Self`` builder) resolves to the enclosing type, which is what it
    means; a generic PARAMETER (2 sites) resolves to nothing and is refused.
    """
    found_closing_paren = False
    for child in node.children:
        if child.type == ")":
            found_closing_paren = True
        elif found_closing_paren and child.type in ("user_type", "optional_type"):
            name = _swift_bare_type(node_text(child, source))
            if name == "Self":
                return _get_enclosing_type(node, source)
            if name is not None and name in _swift_type_parameter_names(node, source):
                return None
            return name
    return None

def _swift_member_declaration(node: "tree_sitter.Node") -> "tree_sitter.Node | None":
    """The type declaration ``node`` is a MEMBER of, or ``None``.

    The first ``class_declaration`` (class / struct / enum / actor / extension)
    or ``protocol_declaration`` up the parent chain, provided no callable body
    (``_SWIFT_SCOPE_NODES``) and no ERROR node comes first. WI-tagir: the walk
    used to stop only at ``function_declaration``, so a ``let`` inside an
    ``init``, a closure or an accessor registered as a FIELD of the enclosing
    type; error recovery re-parents declarations arbitrarily (INV-bisok), so a
    chain through an ERROR is not trusted either.
    """
    cur = node.parent
    while cur is not None:
        if cur.type in _SWIFT_SCOPE_NODES or cur.type == "ERROR":
            return None
        if cur.type in ("class_declaration", "protocol_declaration"):
            return cur
        cur = cur.parent
    return None


def _swift_member_type(
    node: "tree_sitter.Node", source: bytes, vtype: str | None,
) -> str | None:
    """A member's type as a TYPE, or ``None`` when it is a generic parameter.

    WI-hojib. ``class Box<T> { var value: T }`` declares ``value`` of whatever
    ``T`` is bound to at the use; ``T`` names no type, and registering it put
    ``T`` in the module slot once a member chain (``box.value.m()``) read the
    member. The same refusal ``_swift_return_type_name`` makes for a return
    type. A parameter of an EXTERNAL generic type an extension adds members to
    (``extension Result { var success: Success? }``) is not declared in the
    file and is not caught here.
    """
    if vtype is None:
        return None
    bare = vtype.split("<", 1)[0].rstrip("?!")
    return None if bare in _swift_type_parameter_names(node, source) else vtype


def _register_swift_field_type(
    node: "tree_sitter.Node", source: bytes, analysis: FileAnalysis,
) -> None:
    """Record a typed MEMBER property in ``analysis.class_field_types``.

    WI-higob. A ``property_declaration`` that is a member of a
    ``class_declaration`` (class / struct / enum / extension) is registered
    with its declared, constructed or singleton type (``_extract_var_type``)
    under the type's name, so a method in ANOTHER file -- an extension, a
    subclass -- can type a bare ``session`` receiver through the base
    aggregation (``_field_type_registry``). Membership is
    :func:`_swift_member_declaration`'s.

    WI-tagir: a protocol's property requirement (``protocol_property_declaration``,
    ``var identifier: String { get }``) is a member of the protocol, and so of
    every conformer through the base walk -- a protocol extension's bare
    ``identifier`` is that requirement, never a same-named property of some
    other type in the file. WI-hojib: a generic parameter is not a type
    (:func:`_swift_member_type`).
    """
    decl = _swift_member_declaration(node)
    name_node = decl.child_by_field_name("name") if decl is not None else None
    if name_node is None:
        return
    owner = node_text(name_node, source)
    vname, vtype = _extract_var_type(node, source)
    vtype = _swift_member_type(node, source, vtype)
    if vname and vtype:
        analysis.class_field_types.setdefault(owner, {}).setdefault(vname, vtype)


def _swift_parameter_labels(
    node: "tree_sitter.Node", source: bytes,
) -> list[str | None]:
    """The ARGUMENT LABELS a declaration requires, in order.

    INV-fatap. A Swift parameter carries an external label and an internal name:
    ``removeItem(atPath p: String)`` is called ``removeItem(atPath:)``. The label
    is the FIRST ``simple_identifier`` of the ``parameter`` node -- the only one
    when the two coincide (``plain(x: Int)``) -- and ``_`` means the argument is
    passed with no label at all, recorded as ``None`` so it compares equal to a
    call that omits one.
    """
    labels: list[str | None] = []
    for child in node.children:
        if child.type == "parameter":
            ids = [c for c in child.children if c.type == "simple_identifier"]
            label = node_text(ids[0], source) if ids else None
            labels.append(None if label == "_" else label)
    return labels


def _swift_call_argument_labels(
    call: "tree_sitter.Node", source: bytes,
) -> list[str | None]:
    """The ARGUMENT LABELS a call site supplies, in order (``None`` where absent)."""
    suffix = find_child_by_type(call, "call_suffix")
    args = find_child_by_type(suffix, "value_arguments") if suffix is not None else None
    labels: list[str | None] = []
    for arg in (args.children if args is not None else []):
        if arg.type == "value_argument":
            label = find_child_by_type(arg, "value_argument_label")
            labels.append(node_text(label, source) if label is not None else None)
    return labels


def _swift_labels_admit_call(
    qualified_name: str,
    call_labels: list[str | None],
    label_sets: dict[str, list[tuple[str | None, ...]]],
) -> bool:
    """Whether ANY declaration of ``qualified_name`` admits a call with these labels.

    INV-fatap. Swift identifies a method by its labels, so ``removeItem(at:)`` and
    ``removeItem(atPath:)`` are different methods and a bare-name match is not
    evidence of the callee. The question is asked of every OVERLOAD, not of the one
    that survived ``local_symbols[Type.method]``: keyed by bare name, a second
    declaration overwrites the first, and refusing on the survivor alone withdrew
    291 TRUE binds on Alamofire against 49 false ones.

    Refuses only on positive evidence -- a label the call supplies that no
    declaration has. Everything else is admitted deliberately, because the
    alternatives all lose true binds: a declaration with DEFAULTED parameters is
    called with a subset of its labels, a trailing-closure call supplies no
    ``value_arguments`` at all, and a name with no recorded labels has nothing to
    compare.
    """
    declared = label_sets.get(qualified_name)
    if not declared or not call_labels:
        return True
    return any(
        all(label in candidate for label in call_labels) for candidate in declared
    )


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
        # WI-higob: a class-level property's type joins the repo-wide
        # field-type registry (a separate ``if`` so the kind chain below
        # is untouched).
        if node.type in ("property_declaration", "protocol_property_declaration"):
            _register_swift_field_type(node, source, analysis)
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
                # WI-higob: the return-type registry's producer side. Keyed the
                # way java / kotlin / go key theirs (``<Owner>.<method>``, a
                # free function by its bare name), first writer wins in the
                # base aggregation, and WI-lalot's loader feeds the same dict.
                _ret_type = _swift_return_type_name(node, source)
                if _ret_type is not None:
                    analysis.method_return_types.setdefault(full_name, _ret_type)
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
                    # INV-fatap: the labels a call must supply to reach THIS
                    # declaration. Carried as data rather than re-parsed from
                    # ``signature``, whose parameter types contain commas.
                    meta={"arg_labels": _swift_parameter_labels(node, source)},
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


def _static_member_on_instance(
    receiver_hint: str, callee: Symbol, declared_names: set[str],
) -> bool:
    """True when ``callee`` is a ``static`` / ``class`` member but the receiver is an INSTANCE.

    INV-kotob pass-3 read-back: once ``let fileManager = FileManager.default``
    was typed, ``fileManager.removeItem(at:)`` bound by bare name to a test-only
    ``static func removeItem(at:)`` in an extension of ``FileManager`` -- a
    member an instance cannot call -- and the catalogued Foundation boundary
    vanished behind a resolved edge. The receiver is an instance when it is a
    declared variable or parameter, or spelled lowercase; a capitalised head
    that is not declared (``FileManager.removeItem(atPath:)``) is the type
    itself and may bind the static member.
    """
    if not any(m in ("static", "class") for m in (callee.modifiers or [])):
        return False
    return receiver_hint in declared_names or _swift_is_value_spelling(receiver_hint)


def _swift_call_is_value_chain(call_node: "tree_sitter.Node", source: bytes) -> bool:
    """Is this call ``v.p.m()`` -- a member chain of 2+ hops on a VALUE head?

    WI-sulas. The receiver of such a call is ``v.p``, not ``v``, so the head
    must not name it. A ``self`` head (WI-sizas types ``self.a`` itself) and a
    TYPE head (``URLSession.shared.dataTask``, capitalised) are not value chains
    here. Depth counts the nested ``navigation_expression`` nodes down the
    operand side: ``v.m`` is 1, ``v.p.m`` is 2.
    """
    nav = find_child_by_type(call_node, "navigation_expression")
    depth = 0
    node: "tree_sitter.Node | None" = nav
    while node is not None and node.type == "navigation_expression":
        depth += 1
        node = node.children[0] if node.children else None
    if depth < 2 or node is None or node.type != "simple_identifier":
        return False
    head = node_text(node, source)
    return head != "self" and _swift_is_value_spelling(head)


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
        # WI-sulas: A VALUE CHAIN IS NOT TYPED BY ITS HEAD. For ``v.p.m()`` the
        # receiver is ``v.p``, and naming ``v`` made the emit site stamp
        # typeof(v): ``ch.pipeline.addHandler`` landed in the module slot as
        # ``Channel`` (the receiver is a ChannelPipeline), and ``s.db.save()``
        # with ``s: Store`` resolved to ``Store.save``. 1,020 such sites on four
        # repositories, 20 of 20 sampled wrong. With no hint the receiver
        # EXPRESSION is typed by the walker that already types expression
        # receivers, which answers or stays silent. Left as they were: a
        # single hop (``v.m()``), a ``self`` head (WI-sizas types ``self.a``
        # itself) and a TYPE head (``URLSession.shared.dataTask``), which was
        # not read back.
        if receiver_hint is not None and _swift_call_is_value_chain(call_node, source):
            receiver_hint = None
        # ``nav_node`` exists, so there IS a receiver expression -- whether or
        # not it contributed an identifier we can name.
        return (method_name, receiver_hint, True)

    # Fallback: if no navigation_suffix found, use the first simple_identifier
    if receiver_parts:  # pragma: no cover - navigation_expression always has suffix
        return (receiver_parts[0], None, False)

    return ("", None, False)  # pragma: no cover


def _swift_nav_receiver(
    call: "tree_sitter.Node",
    source: bytes,
) -> "tree_sitter.Node | None":
    """The receiver EXPRESSION node of a navigation call, or ``None``.

    WI-higob slice 2. ``_extract_call_target`` reports the receiver's first
    *simple_identifier*, which a receiver that is an expression does not have.
    This returns the node itself so its type can be computed rather than named.
    """
    nav = find_child_by_type(call, "navigation_expression")
    if nav is None:  # pragma: no cover - the caller only asks about nav calls
        return None
    return next(
        (c for c in nav.children if c.is_named and c.type != "navigation_suffix"),
        None,
    )


class _SwiftTyping(NamedTuple):
    """What the expression walker may ask about names, bound to ONE position.

    WI-hojib. The walker used to take a name resolver, the return-type registry
    and a field resolver separately, and could therefore answer only what those
    three could: it typed a bare name, ``self.<property>`` and ``<owner>.<method>``
    on the enclosing type, and nothing reached through a base class or a second
    hop. The four lookups below are the whole of what an expression's type
    depends on, each walking base classes:

    * ``name``: a bare name -- the innermost visible binding, then the enclosing
      type's members, then the file's globals (``_type_of``);
    * ``field``: a member of the enclosing type (``self.<property>``);
    * ``member``: ``(type, name)`` -- the declared type of that type's member;
    * ``returns``: ``(type, method)`` -- what that type's method returns, and with
      type ``None`` what a free function of that name returns.
    """

    name: Callable[[str], "str | None"]
    field: Callable[[str], "str | None"]
    member: Callable[[str, str], "str | None"]
    returns: Callable[["str | None", str], "str | None"]


def _swift_receiver_expr_type(
    node: "tree_sitter.Node | None",
    source: bytes,
    env: _SwiftTyping,
) -> str | None:
    """The TYPE an expression evaluates to, or ``None`` when nothing names it.

    WI-higob slice 2. The wrappers that carry no type of their own -- ``try``,
    ``await``, parentheses -- are stripped; a cast names its type outright; a
    call is typed by :func:`_swift_call_type`, which recurses back here for its
    own receiver, so ``a.b().c().d()`` is walked rather than one level being
    special-cased. A generic constructor (``constructor_expression``) names
    its type as a plain one does, and a bare name is looked up in the scope
    map (WI-mofil). A parenthesised TUPLE (more than one element) is not a
    receiver type the catalogue knows and yields ``None``, as does a cast to a
    collection (``o as! [String]``) -- ``_swift_bare_type`` is the one rule for
    which spellings are receiver types.

    WI-dodop adds the ``self.<property>`` head. ``self`` parses as
    ``self_expression`` rather than ``simple_identifier``, so
    ``_extract_call_target`` collects nothing into ``receiver_parts`` and
    reports ``receiver_hint=None`` -- writing ``self.`` in front of a property
    destroyed the hint for the IDENTICAL call (``db.write(x)`` typed,
    ``self.db.write(x)`` not). 393 of 1,913 classified untyped vapor sites.

    ``env.field`` and NOT ``env.name`` for that case, and the distinction
    is load-bearing: ``name`` consults scoped locals BEFORE the enclosing type's
    members, but ``self.db`` means the FIELD whatever a local happens to be
    called. Resolving it through ``name`` would stamp a confidently wrong type on
    a shadowed name -- and ``method_call_recovery`` step 3a treats a stamped
    ``receiver_type_hint`` as grounds to REFUTE a class hint, so a wrong stamp
    does not merely fail to help, it DELETES a correct recovery.

    WI-hojib types the rest by what each expression evaluates to:

    * a member chain on a value (``config.fileManager``, ``self.inner.session``)
      is the head's type, then that type's DECLARED member (``env.member``,
      through its bases). A member the head's type does not declare, or a head
      that is a TYPE (``FileManager.default``, a static member), names nothing.
      An optional chain (``a?.b``) parses as the same node and evaluates to the
      wrapped type, which is what an optional's reader already strips to;
    * a literal is its default type (``_SWIFT_LITERAL_TYPES``);
    * ``a ?? b`` and ``c ? a : b`` are the type EVERY operand agrees on. One
      known operand does not decide: a literal's type is contextual (``""`` is
      a Substring beside a Substring), and an upcast to a protocol is invisible
      from the operand that is not upcast.
    """
    while node is not None:
        if node.type in ("try_expression", "await_expression", "tuple_expression"):
            # ``try_operator`` is a NAMED child of ``try_expression``; the
            # ``await`` keyword and the parentheses are not.
            inner = [
                c for c in node.children
                if c.is_named and c.type != "try_operator"
            ]
            node = inner[0] if len(inner) == 1 else None
            continue
        if node.type == "postfix_expression" and any(
            c.type == "bang" for c in node.children
        ):
            # WI-silos: a force unwrap ``x!`` evaluates to ``x``'s type, which
            # an optional's reader already strips to the wrapped type
            # (``let m = self.manager!``). Only ``!``: another postfix
            # operator (``a...``, a partial range) changes the type.
            inner = [c for c in node.children if c.is_named and c.type != "bang"]
            node = inner[0] if len(inner) == 1 else None
            continue
        if node.type == "as_expression":
            cast_to = find_child_by_type(node, "user_type")
            return (
                _swift_bare_type(node_text(cast_to, source))
                if cast_to is not None else None
            )
        if node.type == "constructor_expression":
            # WI-mofil: a GENERIC constructor (``ManagedAtomic<Bool>(false)``)
            # parses as ``constructor_expression``, not ``call_expression``, so
            # it was untyped while ``Store()`` was typed. Its ``user_type``
            # names the type; a collection (``[String](...)``) has none and
            # stays a refusal, through the same ``_swift_bare_type`` rule.
            built = find_child_by_type(node, "user_type")
            return (
                _swift_bare_type(node_text(built, source))
                if built is not None else None
            )
        if node.type in _SWIFT_LITERAL_TYPES:
            return _SWIFT_LITERAL_TYPES[node.type]
        if node.type in ("nil_coalescing_expression", "ternary_expression"):
            operands = node.named_children
            if node.type == "ternary_expression":
                operands = operands[1:]  # the condition is not a value of the result
            types = [_swift_receiver_expr_type(o, source, env) for o in operands]
            if types and types[0] is not None and all(t == types[0] for t in types):
                return types[0]
            return None
        if node.type == "simple_identifier":
            # WI-mofil: a bare NAME (``let t = s``, ``guard let fm = maybe``)
            # evaluates to whatever the scope map says that name is.
            return env.name(node_text(node, source))
        if node.type == "call_expression":
            return _swift_call_type(node, source, env)
        if node.type == "navigation_expression":
            head = node.children[0] if node.children else None
            suffix = find_child_by_type(node, "navigation_suffix")
            ident = (
                find_child_by_type(suffix, "simple_identifier")
                if suffix is not None else None
            )
            if head is None or ident is None:
                return None
            member = node_text(ident, source)
            if head.type == "self_expression":
                return env.field(member)
            owner = _swift_receiver_expr_type(head, source, env)
            return env.member(owner, member) if owner is not None else None
        return None
    return None


def _swift_call_type(
    call: "tree_sitter.Node",
    source: bytes,
    env: _SwiftTyping,
) -> str | None:
    """The TYPE a ``call_expression`` evaluates to, or ``None``.

    WI-higob. A bare capitalised callee is a constructor and evaluates to its
    own type; a method call is looked up in the return-type registry under
    ``<owner>.<method>``, where the owner is the receiver's type -- from the
    scope map for a named receiver, from the head itself for a capitalised one,
    and from :func:`_swift_receiver_expr_type` when the receiver is an
    expression. A bare lowercase callee is looked up on the enclosing type
    and then as a free function. Recursion terminates on the AST: each step
    descends into a strictly smaller subtree.

    WI-hojib. ``env.returns`` walks the owner's BASES: Alamofire's test
    subclasses call ``stored(Session())``, declared on ``BaseTestCase``, and
    XCTest's ``expectation(description:)`` is declared on ``XCTestCase`` (a
    library row), so the enclosing type's own key missed both. A ``self``
    receiver is the implicit self -- ``self.m()`` is the call ``m()`` -- and
    ``init`` constructs its owner (``self.init()`` in a static method,
    ``T.init(...)``). A constructor spelled ``_T(...)`` is a constructor
    (``_swift_is_type_spelling``), and ``Self(...)`` constructs the enclosing
    type, as a ``-> Self`` return type does.
    """
    callee_name, receiver_hint, has_receiver = _extract_call_target(call, source)
    if not callee_name:  # pragma: no cover - defensive, mirrors the gate
        return None
    if has_receiver:
        owner = env.name(receiver_hint) if receiver_hint else None
        if owner is None and receiver_hint is not None and _swift_is_type_spelling(receiver_hint):
            owner = receiver_hint
        if owner is None and receiver_hint is None:
            receiver = _swift_nav_receiver(call, source)
            if receiver is not None and receiver.type == "self_expression":
                owner = _get_enclosing_type(call, source)
            else:
                owner = _swift_receiver_expr_type(receiver, source, env)
        if owner == "Self":
            # ``Self.init(...)`` / ``Self.make()``: the enclosing type, as a
            # ``-> Self`` return type names it -- never a type called ``Self``.
            owner = _get_enclosing_type(call, source)
        if owner is None:
            return None
        # Generics are stripped the same way the module slot strips them:
        # the registry is keyed by the bare owner name.
        owner = owner.split("<", 1)[0]
        if callee_name == "init":
            return owner
        return env.returns(owner, callee_name)
    if callee_name == "Self":
        return _get_enclosing_type(call, source)
    if _swift_is_type_spelling(callee_name):
        return callee_name  # a constructor evaluates to its own type
    enclosing = _get_enclosing_type(call, source)
    found = env.returns(enclosing, callee_name) if enclosing else None
    return found if found is not None else env.returns(None, callee_name)


def _swift_call_result_type(
    node: "tree_sitter.Node",
    source: bytes,
    env: _SwiftTyping,
    children: "list[tree_sitter.Node] | None" = None,
) -> str | None:
    """The type of the expression a ``property_declaration`` is initialised with.

    WI-higob. ``let s = store.session()`` looks up ``<type of store>.session``;
    ``let s = Store.make()`` looks up ``Store.make``; a bare ``let s =
    makeSession()`` inside a type looks up ``<enclosing type>.makeSession`` and
    then the free function. Slice 2 replaced this function's own three-level
    ``try``/``await`` unwrapping and its single-level receiver lookup with the
    shared expression walker, so a chained initialiser (``let s =
    Store().session()``) and a cast (``let fm = o as! FileManager``) are typed
    by the same rule that types a chained RECEIVER. Anything the registry does
    not hold yields ``None`` -- the local stays untyped, which is what an
    unknown result means.

    ``children`` (WI-silos) restricts the read to one clause of a
    multi-binding declaration (``_swift_declaration_clauses``). The walker's
    field resolver (WI-silos) types a ``self.<property>`` initialiser the way
    the emit site types a ``self.<property>`` receiver: one walker, one answer.
    """
    for child in node.children if children is None else children:
        vtype = _swift_receiver_expr_type(child, source, env)
        if vtype is not None:
            return vtype
    return None


def _swift_condition_bindings(
    node: "tree_sitter.Node",
) -> list[tuple["tree_sitter.Node", "tree_sitter.Node | None", "tree_sitter.Node | None"]]:
    """Every ``let`` / ``var`` clause of an ``if`` / ``guard`` / ``while`` condition.

    Returns ``(name, annotation, rhs)`` per clause; ``annotation`` is the
    clause's ``type_annotation`` node and ``rhs`` the expression after its
    ``=``, either ``None`` when absent (``if let s`` shorthand has neither).

    WI-mofil. The grammar lays a condition out FLAT -- clauses are runs of
    siblings separated by top-level ``,`` tokens, ending at the body's
    ``statements`` (or ``{`` / ``else``). The arm this replaces took the
    condition's FIRST ``simple_identifier`` and its FIRST ``=``: in ``if flag,
    let s = f()`` it typed ``flag`` as ``f()``'s result, in ``if let a = f(),
    let b = g()`` it never bound ``b``, and in ``if case let .some(x) = y`` it
    bound the case NAME ``some``. A clause is read here only when it OPENS
    with ``let`` / ``var`` followed by a name; a ``case`` clause (an enum
    payload, typed by nothing here) and a boolean clause bind nothing.
    """
    clauses: list[list["tree_sitter.Node"]] = [[]]
    for child in node.children:
        if child.type in ("statements", "{", "else"):
            break
        if child.type == ",":
            clauses.append([])
        else:
            clauses[-1].append(child)
    out: list[tuple["tree_sitter.Node", "tree_sitter.Node | None", "tree_sitter.Node | None"]] = []
    for clause in clauses:
        # The keyword (``if`` / ``guard`` / ``while``) opens the first clause.
        body = [c for c in clause if c.type not in ("if", "guard", "while")]
        if (
            len(body) < 2
            or body[0].type != "value_binding_pattern"
            or body[1].type != "simple_identifier"
        ):
            continue
        annotation = next((c for c in body[2:3] if c.type == "type_annotation"), None)
        eq = next((i for i, c in enumerate(body) if c.type == "="), None)
        rhs = (
            next((c for c in body[eq + 1:] if c.is_named), None)
            if eq is not None else None
        )
        out.append((body[1], annotation, rhs))
    return out


def _swift_pattern_names(
    pattern: "tree_sitter.Node", *, binding: bool,
) -> list["tree_sitter.Node"]:
    """The ``simple_identifier`` nodes a ``pattern`` BINDS (WI-silos).

    ``binding`` says whether the context already binds a bare name -- a
    declaration's pattern, a ``for`` loop variable -- or whether a ``let`` /
    ``var`` must say so, as in a ``case`` pattern, where a bare name is an
    EXPRESSION pattern that reads the outer name (``case .third(g)``). A
    ``value_binding_pattern`` child turns binding on for its pattern and every
    pattern nested in it (``case let .other(w, k)``). A name is bound only when
    it is its pattern's whole content: a pattern that also holds a ``.`` or a
    type is an enum-case or type pattern, and its identifier is the CASE name
    (``.some``), never a variable.
    """
    binding = binding or any(c.type == "value_binding_pattern" for c in pattern.children)
    named = [c for c in pattern.named_children if c.type != "value_binding_pattern"]
    if (
        binding
        and len(named) == 1
        and named[0].type == "simple_identifier"
        and not any(c.type == "." for c in pattern.children)
    ):
        return [named[0]]
    out: list["tree_sitter.Node"] = []
    for c in named:
        if c.type == "pattern":
            out.extend(_swift_pattern_names(c, binding=binding))
    return out


def _swift_condition_case_names(node: "tree_sitter.Node") -> list["tree_sitter.Node"]:
    """Every name a ``case`` clause of an ``if`` / ``guard`` / ``while`` condition binds.

    WI-silos. The grammar lays a case clause out FLAT, like the rest of the
    condition: ``if case let .some(x) = y`` is ``case``, a
    ``value_binding_pattern``, ``.``, the case name, then ``pattern`` (``x``);
    ``guard case .a(let p, var q) = r`` puts each ``let`` inside its own
    ``pattern``. A clause runs from ``case`` to its ``=``; a ``let`` / ``var``
    between them makes every pattern after it binding. The commas inside a
    payload are top-level tokens here, which is why this does not reuse
    ``_swift_condition_bindings``' comma split.
    """
    out: list["tree_sitter.Node"] = []
    in_case = False
    case_let = False
    for child in node.children:
        if child.type in ("statements", "{", "else"):
            break
        if child.type == "case":
            in_case, case_let = True, False
        elif child.type == "=":
            in_case = False
        elif in_case and child.type == "value_binding_pattern":
            case_let = True
        elif in_case and child.type == "pattern":
            out.extend(_swift_pattern_names(child, binding=case_let))
    return out


def _swift_declaration_clauses(node: "tree_sitter.Node") -> list[list["tree_sitter.Node"]]:
    """A ``property_declaration``'s children, split into one list per declared clause.

    WI-silos. ``let a = FileManager(), b = bar()`` is ONE declaration node whose
    children run ``pattern = expr , pattern = expr``. Read whole, the
    declaration reader took the LAST pattern's name and the FIRST constructor's
    type, stamping ``FileManager`` on ``b`` and leaving ``a`` unbound. A comma
    inside an initialiser is nested in its expression, so a top-level ``,``
    separates clauses. A single-clause declaration yields its children
    unchanged.
    """
    clauses: list[list["tree_sitter.Node"]] = [[]]
    for child in node.children:
        if child.type == ",":
            clauses.append([])
        else:
            clauses[-1].append(child)
    return clauses


def _extract_var_type(
    node: "tree_sitter.Node",
    source: bytes,
    children: "list[tree_sitter.Node] | None" = None,
) -> tuple[str | None, str | None]:
    """Extract variable name and type from a property_declaration node.

    Returns (var_name, type_name). Type is inferred from:
    1. Explicit type annotation: ``let x: Store = ...`` → ("x", "Store")
    2. Constructor call: ``let x = Store()`` → ("x", "Store")
    3. No type available: ``let x = compute()`` → ("x", None)

    ``children`` (WI-silos) restricts the read to one clause of a
    multi-binding declaration (``_swift_declaration_clauses``).
    """
    var_name: str | None = None
    type_name: str | None = None

    for child in node.children if children is None else children:
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
            else:
                # WI-higob: ``var task: URLSessionTask?`` -- an optional
                # annotation names its wrapped type; the receiver typing is
                # the same (the call is ``task?.resume()`` or follows a
                # ``guard let``).
                opt_node = find_child_by_type(child, "optional_type")
                if opt_node is not None:
                    type_name = _swift_bare_type(node_text(opt_node, source))
        elif child.type == "call_expression" and type_name is None:
            # Constructor call: `Store()`, `URLSession()`
            # Extract the type from the constructor name
            id_node = find_child_by_type(child, "simple_identifier")
            if id_node:
                ctor_name = node_text(id_node, source)
                # Constructor calls start with uppercase (WI-hojib: after any
                # leading ``_`` -- ``_URLEncodedFormDecoder(...)``).
                if ctor_name == "Self":
                    # WI-hojib: ``Self(...)`` constructs the enclosing type,
                    # as a ``-> Self`` return type names it.
                    type_name = _get_enclosing_type(node, source)
                elif ctor_name and _swift_is_type_spelling(ctor_name):
                    type_name = ctor_name
        elif child.type == "navigation_expression" and type_name is None:
            # INV-kotob: ``Type.member`` with a capitalised head and a
            # lowercase member is a singleton (``FileManager.default``,
            # ``URLSession.shared``) or an enum case (``Method.get``), and
            # its value IS a ``Type``. A nested head (``A.b.c``) or a
            # capitalised member (``String.Encoding``) is left untyped.
            head = find_child_by_type(child, "simple_identifier")
            suffix = find_child_by_type(child, "navigation_suffix")
            member = find_child_by_type(suffix, "simple_identifier") if suffix else None
            if head is not None and member is not None:
                head_text = node_text(head, source)
                if (
                    _swift_is_type_spelling(head_text)
                    and _swift_is_value_spelling(node_text(member, source))
                ):
                    type_name = head_text

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
    method_return_type_registry: dict[str, str] | None = None,
    field_type_registry: dict[str, dict[str, str]] | None = None,
    arg_label_sets: dict[str, list[tuple[str | None, ...]]] | None = None,
    file_symbols: "list[Symbol] | None" = None,
) -> list[Edge]:
    """Extract call and import edges from a file.

    ``method_return_type_registry`` (WI-higob): ``<Owner>.<method>`` -> the
    bare type the method returns, aggregated over the repo in Pass 1 and
    later fed with library rows by WI-lalot's loader. Read when a local is
    bound to a call result so ``let s = store.session()`` types ``s``.
    ``field_type_registry`` (WI-higob): ``<Type>`` -> ``{property: type}`` for
    every class-level property in the repo, read for a bare receiver inside
    a type's method that no scope types -- BEFORE the file's globals
    (WI-tagir) -- and for a member chain's member (WI-hojib), through the
    owning type and its base classes, after this file's own members.
    """
    # Every declaration of this file, by position (INV-midag): ``local_symbols``
    # keeps ONE symbol per qualified name, and overloads share one.
    decl_index = symbols_at(
        file_symbols if file_symbols is not None
        else list({s.id: s for s in local_symbols.values()}.values()))
    if method_return_type_registry is None:
        method_return_type_registry = {}
    if arg_label_sets is None:
        arg_label_sets = {}
    if field_type_registry is None:
        field_type_registry = {}
    _caller_path = str(file_path)
    edges: list[Edge] = []
    file_id = make_file_id("swift", str(file_path))
    file_anchor = file_anchor_symbol("swift", str(file_path), PASS_ID, run_id)

    # Build variable → type mapping for receiver type tracking (ADR-0017 §1c)
    var_types: dict[str, str] = {}
    # WI-higob: receiver types are SCOPED to the function that declares them
    # -- one map per ``function_declaration`` body, file-level declarations in
    # ``var_types`` -- so a later ``let s`` in another function no longer
    # overwrites this function's ``s`` (the file-scoped map typed
    # ``s.fileExists`` in one method from a ``let s`` in the next; kotlin had
    # the same bug and got a scope stack under WI-nasuf, objc keys by body
    # span). Lookups walk innermost-first, then the file level.
    # One map per function, keyed by the function node's start byte. Scope
    # membership follows the exact PARENT CHAIN, never byte-span containment:
    # in a file tree-sitter recovers with ERROR nodes (Alamofire's
    # Session.swift, INV-bisok) a function_declaration's span swallowed the
    # class body, so a class property bound by span landed in that function
    # and every method outside it lost the property. A declaration whose
    # chain reaches a class / protocol / ERROR before any function is
    # file-level; a lookup walks its own function ancestors innermost-first,
    # then the file level.
    # WI-mofil: a scoped binding is VISIBLE FROM a byte offset -- the end of
    # its declaration -- and a lookup takes the latest binding visible at the
    # use. Keyed by name alone, a later ``let container = KDC<K>(...)`` retyped
    # the field ``container`` two lines above it (hummingbird), and ``let s =
    # s.session()`` read its own initialiser's ``s`` as the local. File-level
    # declarations stay position-free: a global is visible throughout.
    # WI-silos: a binding is ``(visible_from, visible_to, type)`` and its TYPE
    # MAY BE ``None``. A declaration the pass cannot type -- an unannotated
    # closure parameter, a local no registry types, a loop variable, a
    # ``catch`` / ``case let`` / tuple name -- is still a declaration: it
    # SHADOWS, and the innermost visible binding answers, typed or not. Recorded
    # only when typed, it left no entry, and the lookup fell THROUGH it to the
    # outer binding or the field and stamped that type on a different variable.
    # ``visible_to`` closes an ``if`` clause's binding at the ``else``. Keys are
    # ``(type, start, end)``: a block and the ``if`` that is its only statement
    # share a span, and a closure called as a block's first statement shares
    # its start.
    _scoped_types: dict[_ScopeKey, dict[str, list[tuple[int, int, str | None]]]] = {}
    # WI-tagir: this file's type MEMBERS, by owning type: ``{owner: {name: type
    # | None}}``. ``var_types`` holds true globals only.
    _file_members: dict[str, dict[str, str | None]] = {}
    # ... and by the DECLARATION that holds them, for the lookup from inside it.
    _decl_members: dict[_ScopeKey, dict[str, str | None]] = {}
    _forever = len(source) + 1

    def _is_scope(n: "tree_sitter.Node") -> bool:
        if n.type in _SWIFT_SCOPE_NODES:
            return True
        # WI-silos: a block is a scope, but not one error recovery built: a
        # ``statements`` directly under an ERROR is not a body the source
        # wrote, and the ERROR rule below applies to it instead.
        return n.type in _SWIFT_BLOCK_NODES and not (
            n.type == "statements" and n.parent is not None and n.parent.type == "ERROR"
        )

    def _function_ancestors(n: "tree_sitter.Node", *, through_errors: bool) -> list[_ScopeKey]:
        # ERROR nodes cut both ways. A DECLARATION under one is file-level:
        # error recovery cannot be trusted to have attached it to the right
        # function (Session.swift's `rootQueue` sat under two ERRORs inside a
        # function whose span swallowed the class). A LOOKUP walks through
        # them: a call inside an error-recovered body still belongs to the
        # function that encloses it, and stopping early lost that function's
        # own parameters (Vernissage's `request.logger.info`).
        chain: list[_ScopeKey] = []
        cur = n.parent
        while cur is not None:
            # WI-mofil: every callable BODY is a scope -- its parameters and
            # locals are invisible outside it. Only ``function_declaration``
            # was one: a closure-local ``let`` landed in the enclosing
            # function's map, and an init / subscript / accessor body's
            # landed in the FILE-level map, typing that name in every method.
            # WI-silos: and every BLOCK (``_SWIFT_BLOCK_NODES``).
            if _is_scope(cur):
                chain.append((cur.type, cur.start_byte, cur.end_byte))
            elif cur.type in ("class_declaration", "protocol_declaration"):
                break
            elif cur.type == "ERROR" and not through_errors:
                break
            cur = cur.parent
        return chain

    def _bind(
        n: "tree_sitter.Node", name: str, vtype: str | None, visible_from: int,
        visible_to: int = _forever,
    ) -> None:
        # The binding lands in the innermost scope enclosing ``n``.
        # WI-tagir: a declaration in no scope is a type's MEMBER or a true
        # global, and the two no longer share one map. A member is recorded
        # under its declaration (``_decl_members``) and its owner's name
        # (``_file_members``), typed or not -- an untyped member is still that
        # type's member and shadows a same-named global inside it -- first
        # writer winning by name, as in Pass 1's registry. A global stays
        # typed-only and position-free.
        chain = _function_ancestors(n, through_errors=False)
        if not chain:
            decl = _swift_member_declaration(n)
            name_node = decl.child_by_field_name("name") if decl is not None else None
            if decl is None or name_node is None:
                if vtype is not None:
                    var_types[name] = vtype
                return
            vtype = _swift_member_type(n, source, vtype)
            _decl_members.setdefault(
                (decl.type, decl.start_byte, decl.end_byte), {},
            ).setdefault(name, vtype)
            _file_members.setdefault(node_text(name_node, source), {}).setdefault(name, vtype)
            return
        _scoped_types.setdefault(chain[0], {}).setdefault(name, []).append(
            (visible_from, visible_to, vtype),
        )

    def _enclosing_decl(n: "tree_sitter.Node") -> "tuple[str, _ScopeKey] | None":
        # The innermost type declaration around a USE: its name, and its key in
        # ``_decl_members``. Through ERROR nodes, as ``_get_enclosing_type``.
        cur = n.parent
        while cur is not None and cur.type not in ("class_declaration", "protocol_declaration"):
            cur = cur.parent
        name_node = cur.child_by_field_name("name") if cur is not None else None
        if cur is None or name_node is None:
            return None
        return node_text(name_node, source), (cur.type, cur.start_byte, cur.end_byte)

    def _mro(owner: str) -> Iterator[str]:
        # WI-higob: a type, then its bases breadth-first (class symbols carry
        # ``base_classes``), bounded and cycle-safe. An EXTERNAL base
        # (``XCTestCase``) is yielded by name -- its library rows are keyed by
        # it -- but has no symbol, so the walk stops there. WI-tagir: a dotted
        # spelling (``extension OfflineRetrier.State``, an annotation
        # ``URLEncodedFormEncoder.NilEncoding``) is the nested type declared by
        # its last segment, which is the name its declaration registers under.
        queue = [owner.split("<", 1)[0]]
        seen: set[str] = set()
        while queue and len(seen) < 32:
            cls = queue.pop(0)
            if cls in seen:
                continue
            seen.add(cls)
            yield cls
            if "." in cls:
                queue.insert(0, cls.rsplit(".", 1)[1])
            sym = global_symbols.get(cls) or local_symbols.get(cls)
            for base in ((sym.meta or {}).get("base_classes", []) if sym is not None else []):
                if base not in seen:
                    queue.append(base)

    def _member_lookup(
        owner: str, name: str, decl: "_ScopeKey | None" = None,
    ) -> tuple[bool, str | None]:
        # WI-tagir: is ``name`` a member of ``owner`` (or a base), and of what
        # type? The enclosing DECLARATION's own members first -- three nested
        # ``Inner`` classes in one file (Alamofire Combine.swift) each have
        # their own ``request`` -- then this file's members by type name (the
        # binding pass types a call-result initialiser, which Pass 1 cannot),
        # then the repo-wide field registry. ``(True, None)`` is a member
        # nothing types.
        if decl is not None and name in _decl_members.get(decl, {}):
            return True, _decl_members[decl][name]
        for cls in _mro(owner):
            mine = _file_members.get(cls)
            if mine is not None and name in mine:
                return True, mine[name]
            hit = field_type_registry.get(cls, {}).get(name)
            if hit is not None:
                return True, hit
        return False, None

    def _member_type(owner: str, name: str) -> str | None:
        return _member_lookup(owner, name)[1]

    def _returns(owner: str | None, callee: str) -> str | None:
        # WI-hojib: what ``<owner>.<callee>`` returns, through the owner's bases
        # (``stored`` on ``BaseTestCase``, ``expectation`` on ``XCTestCase``);
        # with no owner, what the free function ``callee`` returns.
        if owner is None:
            return method_return_type_registry.get(callee)
        for cls in _mro(owner):
            hit = method_return_type_registry.get(f"{cls}.{callee}")
            if hit is not None:
                return hit
        return None

    def _type_of(name: str, n: "tree_sitter.Node") -> str | None:
        # Swift's unqualified lookup order: the innermost visible local, then
        # the enclosing type's members (and its bases'), then the file's
        # globals. WI-tagir: the globals used to come SECOND, from a map that
        # also held every type's properties, so type A's ``store`` typed B's.
        at = n.start_byte
        for key in _function_ancestors(n, through_errors=True):
            visible = [
                b for b in _scoped_types.get(key, {}).get(name, ())
                if b[0] <= at < b[1]
            ]
            if visible:
                # WI-silos: the innermost visible binding DECIDES, and an
                # untyped one answers "unknown" -- never the outer name's type.
                return max(visible, key=lambda b: b[0])[2]
        enclosing = _enclosing_decl(n)
        if enclosing is not None:
            found, vtype = _member_lookup(enclosing[0], name, enclosing[1])
            if found:
                return vtype
        return var_types.get(name)

    def _typing_at(at: "tree_sitter.Node") -> _SwiftTyping:
        # The walker's lookups, bound to one position. ``field`` is
        # deliberately NOT ``name`` (WI-dodop): ``self.db`` names the enclosing
        # type's member whatever a local in scope is called.
        enclosing = _enclosing_decl(at)

        def _name(nm: str) -> str | None:
            return _type_of(nm, at)

        def _field(nm: str) -> str | None:
            if enclosing is None:
                return None
            return _member_lookup(enclosing[0], nm, enclosing[1])[1]

        return _SwiftTyping(_name, _field, _member_type, _returns)

    # INV-kotob: every declared NAME, typed or not, so a capitalised variable
    # (``let AF = Session.default``) is never mistaken for a type reference.
    declared_names: set[str] = set()

    def _declare(at: "tree_sitter.Node", name_node: "tree_sitter.Node", visible_from: int,
                 visible_to: int = _forever) -> None:
        # WI-silos: a name no reader types, bound as what it is -- unknown.
        name = node_text(name_node, source)
        declared_names.add(name)
        _bind(at, name, None, visible_from, visible_to)

    for node in iter_tree(tree.root_node):
        if node.type == "property_declaration":
            # WI-silos: one clause at a time (``let a = X(), b = y()``), and a
            # clause whose pattern the reader does not name (a tuple) still
            # declares every name in it.
            for clause in _swift_declaration_clauses(node):
                vname, vtype = _extract_var_type(node, source, clause)
                if vname is None:
                    for pat in (c for c in clause if c.type == "pattern"):
                        for nm in _swift_pattern_names(pat, binding=True):
                            _declare(node, nm, clause[-1].end_byte)
                    continue
                declared_names.add(vname)
                if vtype is None:
                    # WI-higob: a call RESULT takes the callee's declared return
                    # type from the registry (in-repo and library rows).
                    vtype = _swift_call_result_type(
                        node, source, _typing_at(node), clause,
                    )
                _bind(node, vname, vtype, clause[-1].end_byte)
        elif node.type in ("if_statement", "guard_statement", "while_statement"):
            # WI-higob: `if let x = <expr>` / `guard let x = <expr>` bind a name
            # to an expression whose type slice 2's walker already computes.
            # WI-mofil: EVERY `let`/`var` clause binds, a clause annotation
            # names the type outright, and `while let` binds the same way. A
            # shorthand clause (`if let s`) has no RHS and re-binds `s` to
            # itself, which the outer declaration already types -- so it binds
            # nothing, and the outer binding answers.
            # WI-silos: the name binds where its NAME node sits: an `if` /
            # `while` clause into that statement's scope (the rest of the
            # condition and the body), a `guard` clause into the enclosing
            # block. An `if` clause's binding ends at the `else`.
            _else = next((c for c in node.children if c.type == "else"), None)
            _until = (
                _else.start_byte
                if _else is not None and node.type == "if_statement" else _forever
            )
            for _name, _ann, _rhs in _swift_condition_bindings(node):
                _bound = node_text(_name, source)
                declared_names.add(_bound)
                if _ann is None and _rhs is None:
                    continue
                _bt = (
                    _swift_bare_type(node_text(_ann, source).lstrip(":"))
                    if _ann is not None else None
                )
                if _bt is None and _rhs is not None:
                    # WI-silos: with the field resolver, as at the emit
                    # site -- `if let body = self.body` is the field's type.
                    _bt = _swift_receiver_expr_type(_rhs, source, _typing_at(_rhs))
                _bind(_name, _bound, _bt, (_rhs or _ann or _name).end_byte, _until)
            for _nm in _swift_condition_case_names(node):
                _declare(_nm, _nm, _nm.end_byte, _until)
        elif node.type == "for_statement":
            # WI-silos: the loop variable binds the `where` clause and the body,
            # NOT the sequence after `in` (`for s in s.tasks` reads the outer
            # `s`). Its element type is WI-bodav's; here it only shadows.
            _kids = node.children
            _in = next((i for i, c in enumerate(_kids) if c.type == "in"), None)
            _pat = next((c for c in _kids if c.type == "pattern"), None)
            if _in is not None and _pat is not None and _in + 1 < len(_kids):
                _seq_end = _kids[_in + 1].end_byte
                _loop_binds = not any(c.type == "case" for c in _pat.children)
                for _nm in _swift_pattern_names(_pat, binding=_loop_binds):
                    _declare(_pat, _nm, _seq_end)
        elif node.type == "catch_block":
            # WI-silos: `catch let e` / `catch E.bad(let m)` bind the body.
            for _pat in (c for c in node.children if c.type == "pattern"):
                for _nm in _swift_pattern_names(_pat, binding=False):
                    _declare(_pat, _nm, _pat.end_byte)
        elif node.type == "switch_pattern":
            # WI-silos: a `case` payload bound by `let` / `var` binds its body.
            for _pat in (c for c in node.children if c.type == "pattern"):
                for _nm in _swift_pattern_names(_pat, binding=False):
                    _declare(node, _nm, node.end_byte)
        elif node.type in ("parameter", "lambda_parameter"):
            # WI-mofil: an ANNOTATED closure parameter (`{ (fm: FileManager)
            # in ... }`) has the parameter's layout and binds the same way,
            # into the closure's own scope.
            # INV-fahub / WI-votar recall recovery: thread function/method
            # parameter types (previously dropped) into the receiver map so a
            # param-typed receiver (`func handle(client: Client)` → its
            # ``client.foo()`` calls) resolves via the type-qualified path
            # instead of misbinding to an arbitrary same-named def below.
            # WI-silos: an UNANNOTATED closure parameter, or a parameter whose
            # type the reader does not extract (`[String]`, a function type),
            # binds too, untyped: it is not the outer name it shadows.
            pname, ptype = _swift_param_name_and_type(node, source)
            if pname:
                declared_names.add(pname)
                _bind(node, pname, ptype, node.end_byte)

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
            # INV-bamij: a call in no function (a top-level statement, a
            # closure bound to a global ``let``) is anchored on the file.
            current_function: Optional[Symbol] = (
                _get_enclosing_function(node, source, decl_index)
                or enclosing_declared_symbol(node, decl_index, _TYPE_BODY_NODES)
                or file_anchor
            )
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
                        type_name = _type_of(receiver_hint, node) or receiver_hint
                        # WI-mofil: a declared type keeps its spelling
                        # (``Result<Success, Failure>``); symbols are keyed by
                        # the bare name, as the module slot strips it.
                        qualified_name = f"{type_name.split('<', 1)[0]}.{callee_name}"
                        # INV-fatap: a bare-name match is not evidence of the
                        # callee. A project extension declaring
                        # ``removeItem(atPath:)`` captured
                        # ``fileManager.removeItem(at:)`` -- a call it cannot
                        # compile against -- and the catalogued Foundation
                        # boundary vanished behind a resolved edge.
                        _labels_ok = _swift_labels_admit_call(
                            qualified_name,
                            _swift_call_argument_labels(node, source),
                            arg_label_sets,
                        )
                        if (
                            qualified_name in local_symbols
                            and _labels_ok
                            and not _static_member_on_instance(
                                receiver_hint, local_symbols[qualified_name], declared_names,
                            )
                        ):
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
                        elif (
                            qualified_name in global_symbols
                            and _labels_ok
                            and not _static_member_on_instance(
                                receiver_hint, global_symbols[qualified_name], declared_names,
                            )
                        ):
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
                        # WI-sulas: say the receiver is a member chain, so the
                        # method-call-recovery linker does not read a class the
                        # caller instantiates as this call's receiver (it guessed
                        # ``router.middlewares.add`` into ``Router.add``).
                        if receiver_hint is None and _swift_call_is_value_chain(node, source):
                            gate_meta["receiver"] = "field_chain"
                        receiver_type = (
                            _type_of(receiver_hint, node) if receiver_hint else None
                        )
                        # INV-kotob: the chain head ``FileManager`` in
                        # ``FileManager.default.fileExists(...)`` is the TYPE
                        # itself -- unless the file declares a variable of
                        # that spelling (``let AF = Session.default``).
                        if (
                            receiver_type is None
                            and receiver_hint
                            and _swift_is_type_spelling(receiver_hint)
                            and receiver_hint not in declared_names
                        ):
                            receiver_type = receiver_hint
                        # WI-higob slice 2: the receiver is an EXPRESSION and
                        # names nothing -- ``store.session().resume()``,
                        # ``Store().session()``, ``(o as! FileManager).x()``,
                        # any of them under ``try`` / ``await``. Until now every
                        # such site took the ``external`` placeholder while the
                        # SAME call one binding apart (``let s =
                        # store.session(); s.resume()``) was typed through the
                        # return-type registry, so one catalogued sink was
                        # reachable in one spelling and unmatchable in the
                        # other. ``receiver_hint`` is None for exactly this
                        # family (INV-pirot established that a nameless
                        # receiver still HAS a receiver), which is the gate.
                        if receiver_type is None and receiver_hint is None:
                            receiver_type = _swift_receiver_expr_type(
                                _swift_nav_receiver(node, source), source,
                                _typing_at(node),
                            )
                        if receiver_type:
                            gate_meta["receiver_type_hint"] = receiver_type
                        # The WI-huzuv structured dst_ref survives suppression
                        # (a module-qualified `HelpersModule.doWork()` keeps its
                        # import-alias module); an untyped receiver yields no
                        # module candidate at all, so INV-finoh's refusal is
                        # preserved rather than widened (INV-pirot: a receiver
                        # EXPRESSION has no spelling and lands here too).
                        # INV-kotob. The old chain here ended in ``receiver_hint``
                        # -- the receiver's VARIABLE NAME -- so ``fm.fileExists``
                        # shipped ``dst_ref.module_path == "fm"``: on Alamofire
                        # 42% of unresolved method edges carried such a name, a
                        # present-but-wrong hint that every consumer refuses
                        # outright, while the dst id said ``external`` at the
                        # same site. One answer now, written to both: the
                        # receiver's TYPE when it is external (the catalogue keys
                        # swift rows by bare type, ``FileManager``), an import
                        # alias for a module-qualified call, else the ``external``
                        # placeholder. A PROJECT type is a symbol, not a module;
                        # it rides in ``receiver_type_hint`` only.
                        _bare_type = receiver_type.split("<", 1)[0] if receiver_type else None
                        _external_type = (
                            _bare_type
                            if _bare_type
                            and _bare_type not in local_symbols
                            and _bare_type not in global_symbols
                            else None
                        )
                        _module = _external_type or import_aliases.get(callee_name)
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=f"swift:{_module or 'external'}:0-0:{callee_name}:unresolved",
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            evidence_type="ast_call",
                            is_resolved=False,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                            meta=gate_meta,
                            dst_ref=ExternalRef(
                                lang="swift",
                                module_path=_module,
                                name=callee_name,
                            ) if _module else None,
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
                        node, source, decl_index,
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
                            node, source, decl_index,
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
    else:
        # WI-higob: an optional parameter (``t: URLSessionTask?``) is typed
        # by its wrapped type, as an optional property is.
        opt_node = find_child_by_type(param_node, "optional_type")
        if opt_node is not None:
            ptype = _swift_bare_type(node_text(opt_node, source))
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


def _swift_canonical_raw_identifiers(
    tree: "tree_sitter.Tree", source: bytes,
) -> bytes | None:
    """Spell every raw identifier that contains a space with underscores.

    INV-bisok. A backtick RAW IDENTIFIER containing spaces is Swift 6.1's spelling
    for a test name: ``func `posts are returned for an anonymous user`()``.
    tree-sitter-swift 0.7.4 parses it as a ``simple_identifier`` whose text keeps
    the spaces; 0.7.3 could not parse it at all, and its file was retried through
    a rewrite that turned each such space into an underscore. That rewrite is gone
    with the 0.7.3 lag, but the NAME it produced is kept, so a symbol's name and
    id do not depend on which grammar release is installed: every space inside a
    ``simple_identifier`` that starts with a backtick becomes an underscore,
    byte for byte, so every span still points at the real file.

    Only identifier leaves are touched, never string contents (a string's
    ``line_str_text`` is another node type), so ``"use `a b` here"`` survives.
    Returns ``None`` when there is nothing to rewrite.
    """
    spans = [
        (n.start_byte, n.end_byte) for n in iter_tree(tree.root_node)
        if n.type == "simple_identifier"
        and source[n.start_byte:n.start_byte + 1] == b"`"
        and b" " in source[n.start_byte:n.end_byte]
    ]
    if not spans:
        return None
    out = bytearray(source)
    for start, end in spans:
        out[start:end] = source[start:end].replace(b" ", b"_")
    return bytes(out)


class SwiftAnalyzer(TreeSitterAnalyzer):
    """Swift language analyzer using tree-sitter-swift."""

    def parse_source(
        self, parser: "tree_sitter.Parser", source: bytes,
    ) -> "tuple[bytes, tree_sitter.Tree]":
        """Parse, then re-parse once if a raw identifier needed canonical spelling.

        INV-bisok: see :func:`_swift_canonical_raw_identifiers`. The rewrite is
        length-preserving, so the re-parse has the same shape and every span is
        unchanged; it exists so that node text read from the tree and from the
        returned bytes agree.
        """
        tree = parser.parse(source)
        canonical = _swift_canonical_raw_identifiers(tree, source)
        if canonical is None:
            return source, tree
        return canonical, parser.parse(canonical)

    lang = "swift"
    file_patterns: ClassVar[list[str]] = ["*.swift"]
    grammar_module = "tree_sitter_swift"

    def __init__(self) -> None:
        super().__init__()
        self._pending_route_symbols: list[Symbol] = []
        #: INV-fatap: ``Type.method`` -> every overload's argument-label tuple.
        #: Filled in :meth:`register_symbol`, read in Pass 2, and cleared in
        #: :meth:`post_process` so a second analysis in the same process does not
        #: inherit the first one's declarations (the base clears its own
        #: registries for the same reason).
        self._arg_label_sets: dict[str, list[tuple[str | None, ...]]] = {}

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

        INV-fatap: this is also the one place every OVERLOAD is still visible.
        The base calls it for every symbol of every file before Pass 2, and the
        line below then overwrites by ``Type.method`` -- so by Pass 2 the label
        sets of all but one declaration are gone. Accumulating them here lets the
        label check ask "does ANY overload admit this call" rather than only
        asking about whichever one survived, which is the difference between 49
        correct refusals and 340 withdrawn binds on Alamofire.
        """
        labels = (symbol.meta or {}).get("arg_labels")
        if labels is not None:
            self._arg_label_sets.setdefault(symbol.name, []).append(tuple(labels))
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
            method_return_type_registry=self._method_return_type_registry,
            arg_label_sets=self._arg_label_sets,
            field_type_registry=self._field_type_registry,
            file_symbols=self.file_symbols(local_symbols),
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
        """Add stashed route symbols to the final result, and reset per-run state."""
        symbols.extend(self._pending_route_symbols)
        self._pending_route_symbols = []
        # INV-fatap: the analyzer is a module-level singleton, so the label map
        # must not survive into the next repository's analysis (the base clears
        # its field / return-type registries at the same point, for the same
        # reason).
        self._arg_label_sets = {}
        return symbols, edges, usage_contexts


_analyzer = SwiftAnalyzer()


def is_swift_tree_sitter_available() -> bool:
    """Check if tree-sitter with Swift grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("swift")
def analyze_swift(repo_root: Path) -> AnalysisResult:
    """Analyze Swift files in a repository."""
    return _analyzer.analyze(repo_root)
