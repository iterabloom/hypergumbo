# SPDX-License-Identifier: AGPL-3.0-or-later
"""C# analysis pass using tree-sitter-c-sharp.

This analyzer uses tree-sitter to parse C# files and extract:
- Class declarations, and record declarations (``record``, ``record class``,
  ``record struct``) as kind ``record`` through the same branch: bases,
  attributes and members like a class's, so their members are not bare
  names (WI-zukat)
- Interface declarations
- Struct declarations
- Enum declarations
- Method declarations (inside classes/structs)
- Constructor declarations
- Property declarations, whose stable_id is the ADR-0035 producer formula
  (``make_typed_stable_id`` over the declared type and the namespace/type
  chain), not the backstop floor key (WI-tusad). Methods, constructors,
  fields, enum members and the WI-binap members hash the FULL chain of block
  namespaces and types (``_csharp_scope``: ``A.Inner.Go``, not the emitted
  nearest-owner name ``Inner.Go``), and classes, interfaces, structs and
  enums mint ``overload_arity.scoped_table_stable_id`` over it, so two nested
  ``class Inner``, or two in sibling namespace blocks, do not share one
  bare-name table key and deleting the first does not move the second's ids
  (INV-fabih). A file-scoped namespace is single per file and not in the
  chain.
- A class, record, struct or interface records its base list in
  ``meta["base_classes"]``; the inheritance linker turns it into ``extends``
  / ``implements`` edges; an interface records its bases too, so
  ``interface IChild : IBase`` gets its edge (WI-ruzid).
  The emitted ``qualified_name`` carries every enclosing namespace, the
  file-scoped one included (``A.B.C.Go``, ``N.M.K``).
- Indexers (``subscript``, named ``Type.this[]``), operators and conversion
  operators (``method``, ``Type.operator+`` / ``Type.operator int``),
  destructors (``method``, ``Type.~Type``) and events declared with
  ``add`` / ``remove`` accessors (``event``) (WI-binap)
- Field declarations, one ``field`` symbol per declarator (WI-jusus)
- The signature of a method, constructor, indexer and operator
  (``_format_csharp_params``) writes each parameter's modifiers (``ref``,
  ``out``, ``in``, ``this``, ``scoped``, ``readonly``), its type as written
  whatever node kind spells it (a tuple, ``A::B``, ``delegate*<int, void>``,
  ``scoped Span<int>``) and its name, a ``params`` array (which the grammar
  puts in the parameter list, not in a ``parameter`` node) and ``__arglist``;
  the method's return type is its ``returns`` field, whatever its kind. This
  text is hashed into the typed stable_id (``normalize_csharp_signature``,
  the types-first family's rule applied one type at a time, keeping the
  modifiers and tuple types, and each type's qualifier as written), so
  ``F(ref int x)`` and ``F(int x)`` do not share an id, nor ``F(A.B x)`` and
  ``F(C.B x)``. Before, only seven type node kinds
  were written and the rest of the parameter, or the return type, was
  dropped.
- Enum members, each a ``field`` symbol named ``Enum.Member`` (WI-duguk)
- Function call relationships (including chained field type resolution)
- ``decorated_by`` edges from C# attributes
- ``references`` edges for method-group references (a method named as a
  value rather than invoked), tagged ``meta.ref_construct="method_group"``
- Using directives (imports)
- Object instantiation (``instantiates`` edges, landing on the constructor
  when the type declares one)
- ADR-0015 dataflow annotation on the emitted edges

If tree-sitter with C# support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-c-sharp is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry, and
     read each file's ``using`` directives once (``_csharp_usings``): the
     stable-id type names, the base hints, and the table handed to the
     linkers in ``AnalysisResult.import_scopes`` (ADR-0064, piece b), which
     says which usings are in scope in the file: its own, then those of its
     project, every ``global using`` of the project's files and the MSBuild
     ``<Using>`` items of its project files (``_csharp_project_usings``)
   - Pass 2: Detect calls, instantiations, using directives and method-group
     references
     - A call is bound here only on C#'s own evidence in this file: the
       enclosing type's own method (``own_type``) or a method of the
       receiver's known type (``receiver_type``). Every other call is
       emitted unresolved with the facts the shared pass reads
       (``receiver_type_hint``, ``qualifier``, ``enclosing_class``; see
       ``_extract_edges_from_file``), and ``inherited_calls`` decides from
       the file's table; neither the file's short-name table nor a run-wide
       lookup of the callee's name binds a call (ADR-0064). An unresolved
       call's module slot is the receiver only when that names a type
       (``_csharp_unresolved_module``), else ``external``.
     - A call, object creation or method-group reference is credited to the
       member whose declaration contains it, found by POSITION
       (``_CSHARP_MEMBER_BODY_NODES``, INV-midag): a method, constructor,
       property (its accessors and its initialiser), indexer, operator,
       destructor or event. A local function or lambda has no symbol and is
       walked past (WI-binap).
     - A call in no member (a field initialiser's lambda) is anchored on its
       enclosing type (``_CSHARP_TYPE_BODY_NODES``), else on the file
       (INV-bamij). An object creation or method-group reference there is
       still dropped.
4. Annotate each file's edges with ADR-0015 dataflow access modes
5. Post-pass over all symbols: turn attribute metadata into ``decorated_by`` edges

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-c-sharp package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as other language analyzers for consistency

Population of ``is_exported`` follows C#'s access-modifier rule: a type
or member is considered exported only when its declaration carries the
``public`` keyword (internal / protected / private items are not). An
enum member has no modifier of its own and inherits its enum's status.

IMPORTED TYPE NAMES (stable-id scheme v17, WI-palus). Every type a typed
stable id hashes is written through the file's ``using`` aliases
(``_csharp_type_names``, read once per file and set with ``resolving_type_names``), so an
imported and a qualified spelling of one type hash alike; a name nothing
binds, or the file declares, is hashed as written.

BUILT-IN TYPE NAMES (stable-id scheme v19, WI-juzom). The same table holds the
language's alias table (``_CSHARP_KEYWORD_TYPES``) as its defaults: a keyword
type and its framework spelling (``string`` / ``System.String`` /
``global::System.String``, and a bare ``String`` under ``using System;``) are
one type with one spelling in the hash, the keyword.
"""
from __future__ import annotations

from pathlib import Path
import re
import xml.etree.ElementTree as ET  # nosec B405 - reading the analysed repository's project files
from typing import TYPE_CHECKING, ClassVar, Iterator, NamedTuple, Optional, TypeAlias

from hypergumbo_core.dataflow import annotate_dataflow as _annotate_dataflow, get_dataflow_config as _get_dataflow_config
from hypergumbo_core.discovery import find_files
from hypergumbo_core.import_scope import ImportScope
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.qualified_name_axis import separator_for_language
from hypergumbo_core import resolved_by
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.base import (
    normalize_with_type_parameters,
    find_matching_paren,
    record_partial_parse,
    AnalysisResult,
    FileAnalysis as _BaseFileAnalysis,
    TreeSitterAnalyzer,
    ancestor_labels,
    find_child_by_type,
    iter_tree,
    enclosing_declared_symbol,
    file_anchor_symbol,
    make_file_id,
    make_file_stable_id,
    make_symbol_id,
    make_typed_stable_id,
    make_unresolved_edge,
    node_text,
    populate_docstrings_from_tree,
    visibility_from_modifiers,
    SymbolsAt,
    symbols_at,
    declares_async,
    FileTypeNames,
    canonical_type_names,
    file_type_names,
    resolving_type_names,
)
from hypergumbo_core.overload_arity import scoped_stable_id, scoped_table_stable_id
from hypergumbo_core.paths import normalize_path
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.var_scope import ScopedVarTypes
from hypergumbo_lang_mainstream.symbol_introspection import (
    compute_cyclomatic_complexity,
    extract_preceding_doc_comment,
)
from hypergumbo_core.pass_silence import DEPENDENCY_UNAVAILABLE

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("csharp")


def find_csharp_files(repo_root: Path) -> Iterator[Path]:
    """Yield all C# files in the repository."""
    yield from find_files(repo_root, ["*.cs"])



def _extract_annotations(
    node: "tree_sitter.Node", source: bytes
) -> list[dict[str, object]]:
    """Extract all C# attributes from a node (class, interface, method, etc).

    C# attributes appear in attribute_list children. Each attribute may have:
    - name: The attribute name (e.g., "HttpGet", "Route", "ApiController")
    - args: Positional arguments from attribute_argument_list
    - kwargs: Named arguments (name = value pairs)

    Returns list of attribute info dicts: [{"name": str, "args": list, "kwargs": dict}]
    """
    annotations: list[dict[str, object]] = []

    for child in node.children:
        if child.type == "attribute_list":
            # attribute_list contains one or more attributes
            for attr in child.children:
                if attr.type == "attribute":
                    attr_info: dict[str, object] = {"name": "", "args": [], "kwargs": {}}

                    # Get the attribute name (may be qualified like System.Serializable)
                    for attr_child in attr.children:
                        if attr_child.type == "identifier":
                            attr_info["name"] = node_text(attr_child, source)
                        elif attr_child.type == "qualified_name":
                            attr_info["name"] = node_text(attr_child, source)

                    # Extract arguments from attribute_argument_list
                    arg_list = find_child_by_type(attr, "attribute_argument_list")
                    if arg_list:
                        args: list[str] = []
                        kwargs: dict[str, str] = {}

                        for arg in arg_list.children:
                            if arg.type == "attribute_argument":
                                # Named argument detection. In tree-sitter-c-sharp
                                # 0.23.5 (the pinned version), `Name = "Get"` is
                                # a flat sequence of `identifier`, `=`, <value>
                                # children directly under `attribute_argument`,
                                # with no `assignment_expression` wrapper.
                                # Detect by presence of the `=` token as a
                                # direct child.
                                if find_child_by_type(arg, "=") is not None:
                                    # Named argument: name = value
                                    name_node = find_child_by_type(
                                        arg, "identifier"
                                    )
                                    arg_name = (
                                        node_text(name_node, source)
                                        if name_node
                                        else ""
                                    )

                                    # Value is the first string_literal after =
                                    for arg_child in arg.children:
                                        if arg_child.type == "string_literal":
                                            value = node_text(arg_child, source)
                                            if value.startswith('"') and value.endswith(
                                                '"'
                                            ):
                                                value = value[1:-1]
                                            if arg_name:
                                                kwargs[arg_name] = value
                                            break
                                else:
                                    # Positional argument
                                    for arg_child in arg.children:
                                        if arg_child.type == "string_literal":
                                            value = node_text(arg_child, source)
                                            # Strip quotes from string literals
                                            if value.startswith('"') and value.endswith(
                                                '"'
                                            ):
                                                value = value[1:-1]
                                            args.append(value)
                                            break
                                        else:
                                            # Non-string literal (e.g., number, bool)
                                            value = node_text(arg_child, source)
                                            args.append(value)
                                            break

                        attr_info["args"] = args
                        attr_info["kwargs"] = kwargs

                    if attr_info["name"]:
                        annotations.append(attr_info)

    return annotations


# C# modifiers that can appear on declarations.
# tree-sitter-c-sharp wraps each keyword in a ``modifier`` node whose
# single child has the keyword as its node type.
CSHARP_MODIFIERS = {
    "public", "private", "protected", "internal",
    "static", "abstract", "virtual", "override",
    "sealed", "async", "readonly", "extern",
    "partial", "unsafe", "volatile", "new",
}


def _extract_modifiers(node: "tree_sitter.Node") -> list[str]:
    """Extract all modifiers from a C# declaration node.

    In tree-sitter-c-sharp, each modifier keyword is wrapped in a separate
    ``modifier`` child node.  For example ``public static void Foo()``
    produces two ``modifier`` children: one containing ``public`` and one
    containing ``static``.

    Returns a list of modifier strings like ``["public", "static"]``.
    """
    modifiers: list[str] = []
    for child in node.children:
        if child.type == "modifier":
            for kw in child.children:
                if kw.type in CSHARP_MODIFIERS:
                    modifiers.append(kw.type)
    return modifiers


def _find_children_by_type(node: "tree_sitter.Node", type_name: str) -> list["tree_sitter.Node"]:
    """Find all children of given type."""
    return [child for child in node.children if child.type == type_name]


#: The node types that spell a type when it is not a bare ``identifier`` (a
#: user type): a parameter's type, a method's return type.
_CSHARP_SIGNATURE_TYPE_NODES: tuple[str, ...] = (
    "predefined_type", "generic_name", "array_type",
    "nullable_type", "qualified_name", "ref_type", "pointer_type",
)


def _extract_type_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract type text from a type node."""
    return node_text(node, source)


def _extract_param_types(
    node: "tree_sitter.Node", source: bytes
) -> dict[str, str]:
    """Extract parameter name -> type mapping from a member declaration (a
    method, constructor, operator or indexer; ``_CSHARP_MEMBER_BODY_NODES``).

    This enables type inference for method calls on parameters, e.g.:
        void Process(Database db) {
            db.Save();  // resolves to Database.Save
        }

    Returns:
        Dict mapping parameter names to their type names (simple name only).
    """
    param_types: dict[str, str] = {}

    # The parameter list: a ``parameter_list``, or an indexer's
    # ``bracketed_parameter_list``; a property and an event have none.
    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        return param_types

    # Extract parameter types
    for child in params_node.children:
        if child.type == "parameter":
            param_type = None
            param_name = None
            for subchild in child.children:
                # Type nodes
                if subchild.type in _CSHARP_SIGNATURE_TYPE_NODES:
                    param_type = _extract_type_text(subchild, source)
                    # Strip generic parameters: List<T> -> List
                    if "<" in param_type:
                        param_type = param_type.split("<")[0]
                    # Strip array brackets: int[] -> int
                    if "[" in param_type:
                        param_type = param_type.split("[")[0]
                    # Strip nullable: int? -> int
                    param_type = param_type.rstrip("?")
                # Custom types use identifier
                elif subchild.type == "identifier":
                    if param_type is None:
                        # First identifier is the type
                        param_type = node_text(subchild, source)
                    else:
                        # Second identifier is the name
                        param_name = node_text(subchild, source)
            if param_type and param_name:
                param_types[param_name] = param_type

    return param_types


def _extract_csharp_signature(
    node: "tree_sitter.Node", source: bytes, is_constructor: bool = False
) -> Optional[str]:
    """Extract function signature from a C# method or constructor declaration.

    Returns signature like:
    - "(int x, int y) int" for regular methods
    - "(string msg)" for void methods (no return type shown)
    - "(string name, int age)" for constructors (no return type)

    Args:
        node: The method_declaration or constructor_declaration node.
        source: The source code bytes.
        is_constructor: True if this is a constructor (no return type).

    Returns:
        The signature string, or None if extraction fails.
    """
    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        return None  # pragma: no cover - a method and a constructor declare parameters

    signature = _format_csharp_params(params_node, source)

    # The declared return type, whatever node kind spells it (a tuple, an
    # alias-qualified name and a function pointer included); ``void`` and a
    # constructor's absent one are omitted.
    returns = None if is_constructor else node.child_by_field_name("returns")
    if returns is not None:
        return_type = node_text(returns, source)
        if return_type != "void":
            signature += f" {return_type}"

    return signature


def _format_csharp_params(params_node: "tree_sitter.Node", source: bytes) -> str:
    """``(int x, ref Foo y)`` for a ``parameter_list`` or an indexer's
    ``bracketed_parameter_list``: each parameter as its modifiers, its type
    and its name, the type as written whatever node kind spells it.

    A ``params`` array is not a ``parameter`` node: the grammar puts its
    ``params`` token, its ``type`` and its ``name`` in the list itself, so it
    is written from those. ``__arglist`` is a parameter with a name and no
    type. A default value is not written.
    """
    params: list[str] = []
    for child in params_node.children:
        if child.type == "parameter":
            type_node = child.child_by_field_name("type")
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue  # pragma: no cover - the grammar names every parameter
            words = [node_text(m, source) for m in child.children if m.type == "modifier"]
            if type_node is not None:
                words.append(node_text(type_node, source))
            words.append(node_text(name_node, source))
            params.append(" ".join(words))
        elif child.type == "params":
            array_type = params_node.child_by_field_name("type")
            array_name = params_node.child_by_field_name("name")
            if array_type is None or array_name is None:
                continue  # pragma: no cover - the grammar requires both
            params.append(
                f"params {node_text(array_type, source)} {node_text(array_name, source)}"
            )
    return f"({', '.join(params)})"


#: WI-binap: member declarations with a body that holds calls, beyond the
#: method and the constructor, and the registry kind each is emitted as. An
#: indexer is Swift's ``subscript``; an operator and a destructor are methods,
#: as C++ emits ``operator==`` and ``~S``.
_CSHARP_BODY_MEMBER_DECLS: dict[str, str] = {
    "indexer_declaration": "subscript",
    "operator_declaration": "method",
    "conversion_operator_declaration": "method",
    "destructor_declaration": "method",
    "event_declaration": "event",
}

#: INV-midag / WI-binap: every declaration whose BODY makes it the enclosing
#: member of a call in it, found by POSITION. A property owns its accessors and
#: its initialiser. A local function or a lambda has no symbol and is walked
#: past to the member that declares it.
_CSHARP_MEMBER_BODY_NODES: frozenset[str] = frozenset({
    "method_declaration", "constructor_declaration", "property_declaration",
    *_CSHARP_BODY_MEMBER_DECLS,
})

#: What a local variable or a parameter is scoped to (INV-zabud): a member, or
#: a local function or lambda inside one.
_CSHARP_VARIABLE_SCOPE_NODES: frozenset[str] = _CSHARP_MEMBER_BODY_NODES | {
    "local_function_statement", "lambda_expression", "anonymous_method_expression",
}


def _csharp_body_member(
    node: "tree_sitter.Node", source: bytes,
) -> Optional[tuple[str, str, str]]:
    """``(kind, name, signature)`` for a ``_CSHARP_BODY_MEMBER_DECLS`` node.

    Names follow the source spelling: ``this[]``, ``operator+``,
    ``operator int`` (a conversion operator, named by its target type, since
    C# forbids an implicit and an explicit one to the same type), ``~A`` and
    the event's own name. Signatures are a method's: ``(int i) int``; an
    event's is its delegate type, as a field's is its type.
    """
    kind = _CSHARP_BODY_MEMBER_DECLS[node.type]
    type_node = node.child_by_field_name("type")
    type_text = node_text(type_node, source) if type_node is not None else None
    if node.type == "event_declaration":
        name_node = node.child_by_field_name("name")
        if name_node is None or type_text is None:
            return None  # pragma: no cover - the grammar requires both
        return kind, node_text(name_node, source), type_text
    if node.type == "destructor_declaration":
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None  # pragma: no cover - the grammar requires it
        name = f"~{node_text(name_node, source)}"
    elif node.type == "indexer_declaration":
        name = "this[]"
    elif node.type == "operator_declaration":
        op_node = node.child_by_field_name("operator")
        if op_node is None:
            return None  # pragma: no cover - the grammar requires it
        name = f"operator{node_text(op_node, source)}"
    else:  # conversion_operator_declaration: named by its target type
        name = f"operator {type_text}"
    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        return None  # pragma: no cover - every form here declares parameters
    signature = _format_csharp_params(params_node, source)
    if type_text and type_text != "void":
        signature += f" {type_text}"
    return kind, name, signature


#: The parameter modifiers ``_format_csharp_params`` writes before a type.
_CSHARP_PARAMETER_MODIFIERS: frozenset[str] = frozenset({
    "this", "scoped", "ref", "out", "in", "readonly", "params",
})

#: Modifiers written before a return type: ``ref`` and ``ref readonly``.
_CSHARP_RETURN_MODIFIERS: frozenset[str] = frozenset({"ref", "readonly"})


def _is_csharp_tuple_type(text: str) -> bool:
    """True when ``text`` is a tuple type, ``(int, string)``: it opens with a
    parenthesis that closes at its end (a nullable or array tuple,
    ``(int, int)?`` / ``(int, int)[]``, does not)."""
    close = find_matching_paren(text, 0)
    return close > 0 and close == len(text) - 1


def _normalize_csharp_type(
    text: str, type_params: list[str] | None, *, parameter: bool,
) -> str:
    """One parameter (``ref Foo x``) or return type (``Foo``), normalized.

    The types-first family's rule (``normalize_signature_types_first``,
    shared with Java, Dart and Groovy) is applied to the type itself, with
    ``keep_qualifier``: a C# type is hashed AS WRITTEN, its namespace or
    enclosing types included (``A.B`` and ``C.B`` hash apart; WI-ririg owner
    ruling). Until the WI-ririg commit the family's FQN strip cut a type's head
    to its last segment. A ``using`` alias is written as the type it names, and
    a framework spelling of a built-in type as its keyword (``System.String``
    -> ``string``; :func:`_csharp_type_names`). A parameter's modifiers (``ref``, ``out``, ...) are taken off
    first and put back, a return type's ``ref`` / ``ref readonly`` likewise,
    and a tuple type is kept whole; the family would otherwise read a
    modifier as the type and a tuple's element as its name.
    """
    from hypergumbo_core.analyze.base import (
        normalize_generic_params,
        normalize_signature_types_first,
    )

    modifiers: list[str] = []
    if parameter:
        # A modifier comes off only while a type and a name remain after it:
        # ``scoped`` is a contextual keyword, so ``scoped x`` is a parameter of
        # type ``scoped``.
        head, _, rest = text.partition(" ")
        while head in _CSHARP_PARAMETER_MODIFIERS and len(rest.split()) >= 2:
            modifiers.append(head)
            text = rest
            head, _, rest = text.partition(" ")
        words = text.rsplit(None, 1)
        type_text = words[0]
    else:
        # A ``ref`` / ``ref readonly`` return comes off the same way (v15 cut
        # ``ref System.Int32`` to ``Int32``, losing the ``ref``).
        head, _, rest = text.partition(" ")
        while head in _CSHARP_RETURN_MODIFIERS and rest.strip():
            modifiers.append(head)
            text = rest.strip()
            head, _, rest = text.partition(" ")
        type_text = text
    if _is_csharp_tuple_type(type_text):
        written = normalize_generic_params(
            canonical_type_names(type_text, skip=type_params or ()), type_params or [])
    else:
        shared = normalize_signature_types_first(
            f"({text})" if parameter else f"() {text}", type_params,
            skip_void_return=False, keep_qualifier=True,
        )
        assert shared is not None  # the text opens with its own parameter list
        written = shared[1:-1] if parameter else shared[2:]
    return " ".join([*modifiers, written])


def normalize_csharp_signature(
    signature: str | None,
    type_params: list[str] | None = None,
) -> str | None:
    """Normalize a C# signature for typed stable_id (spec §6 typed tier).

    ``(Type name, ...) Return`` becomes ``(Type,...)Return`` by the
    types-first family's rule: names dropped, every type as written (its
    qualifier included: no FQN strip in C#), declared type parameters by
    position, ``void`` omitted. Each parameter and the return type are
    normalized one at a time (``_normalize_csharp_type``), which keeps the
    parameter modifiers and tuple types.
    """
    if not signature:
        return None
    close = find_matching_paren(signature, 0)
    if close < 0:
        return None
    from hypergumbo_core.analyze.base import split_params_top_level

    params = [
        _normalize_csharp_type(p, type_params, parameter=True)
        for p in split_params_top_level(signature[1:close]) if p
    ]
    normalized = f"({','.join(params)})"
    returned = signature[close + 1:].strip()
    if returned and returned.lower() != "void":
        normalized += _normalize_csharp_type(returned, type_params, parameter=False)
    return normalized


_CSHARP_ASYNC_WRAPPER_TYPES = ("Task", "ValueTask", "IAsyncEnumerable")


def _extract_csharp_return_type_name(signature: str | None) -> str | None:
    """Extract the return type name from a C# method signature.

    C# signatures use the format ``(params) ReturnType`` where void is omitted.
    Returns the type name only if it is a simple uppercase identifier (filters
    out primitive types like ``int``, ``string`` and generics like ``List<T>``).
    WI-jujup Tier 2 escape hatch: ``Task<T>`` / ``ValueTask<T>`` /
    ``IAsyncEnumerable<T>`` unwrap to ``T`` because the C# ``await`` operator
    dereferences the wrapper at the call site, so ``var x = await
    Method()`` should bind ``x`` to ``T``, not to the wrapper type.

    Examples:
        ``"() ServiceClient"`` → ``"ServiceClient"``
        ``"(string name, int age) User"`` → ``"User"``
        ``"() Task<User>"`` → ``"User"`` (WI-jujup async unwrap)
        ``"() ValueTask<Order>"`` → ``"Order"``
        ``"() IAsyncEnumerable<Item>"`` → ``"Item"``
        ``"()"`` → ``None``
        ``"() string"`` → ``None`` (lowercase = primitive)
        ``"() Task<int>"`` → ``None`` (inner is primitive)
        ``"() Task"`` → ``None`` (no inner type)
        ``"() List<string>"`` → ``None`` (generic non-async wrapper)
    """
    if not signature:
        return None
    paren_idx = signature.rfind(")")
    if paren_idx < 0 or paren_idx == len(signature) - 1:
        return None
    ret_part = signature[paren_idx + 1:].strip()
    # WI-jujup Tier 2: unwrap async wrapper types — Task<T>, ValueTask<T>,
    # IAsyncEnumerable<T>. The await operator strips the wrapper so the
    # var_types entry should be the inner type.
    if "<" in ret_part and ret_part.endswith(">"):
        outer = ret_part[: ret_part.index("<")]
        if outer in _CSHARP_ASYNC_WRAPPER_TYPES:
            inner = ret_part[ret_part.index("<") + 1 : -1].strip()
            # The inner type itself must pass the same uppercase-identifier
            # filter; nested generics (Task<List<T>>) are not unwrapped
            # further — that's a separate, much rarer pattern.
            if inner.isidentifier() and inner and inner[0].isupper():
                return inner
            return None
    if ret_part and ret_part.isidentifier() and ret_part[0].isupper():
        # Bare wrapper types (``Task`` non-generic) name the wrapper itself,
        # not any inner type — drop them so var_types doesn't accidentally
        # bind a variable to "Task".
        if ret_part in _CSHARP_ASYNC_WRAPPER_TYPES:
            return None
        return ret_part
    return None


def _track_csharp_return_type(
    resolved_sym: "Symbol",
    node: "tree_sitter.Node",
    source: bytes,
    var_types: dict[str, str],
) -> None:
    """Track variable type from a resolved method's return type annotation.

    When ``var client = factory.GetClient()`` resolves to ``Factory.GetClient``
    with signature ``() ServiceClient``, this sets ``var_types["client"] = "ServiceClient"``
    so that subsequent ``client.Fetch()`` resolves via type inference.

    The callee is always a declaration of the caller's own file (the only
    calls this analyzer binds), so the return type is written in the caller's
    scope; where that type is declared does not matter here: a call on the
    variable whose method this file does not hold carries the type to the
    linker as ``receiver_type_hint``.

    The invocation_expression AST in C# has the parent chain:
    ``equals_value_clause > variable_declarator > identifier``.
    """
    if resolved_sym.kind != "method":
        return  # pragma: no cover - callers resolve to methods
    ret_name = _extract_csharp_return_type_name(resolved_sym.signature)
    if not ret_name:
        return
    parent = node.parent
    if not parent:
        return  # pragma: no cover - AST nodes always have parents
    # Direct child of variable_declarator
    if parent.type == "variable_declarator":
        name_node = find_child_by_type(parent, "identifier")
        if name_node:
            var_types[node_text(name_node, source)] = ret_name
    # Inside equals_value_clause -> variable_declarator
    elif parent.type == "equals_value_clause":  # pragma: no cover - alt AST pattern
        grandparent = parent.parent
        if grandparent and grandparent.type == "variable_declarator":
            name_node = find_child_by_type(grandparent, "identifier")
            if name_node:
                var_types[node_text(name_node, source)] = ret_name


def _extract_method_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract the method name from a method declaration.

    In C#, method_declaration has: [modifiers] return_type method_name(params)
    The return_type might be an identifier (e.g., 'Product') or predefined_type (e.g., 'int').
    The method_name is always an identifier after the return type.
    """
    identifiers = _find_children_by_type(node, "identifier")
    # If return type is a predefined_type (int, void, etc.), first identifier is method name
    # If return type is an identifier (custom type), second identifier is method name
    has_predefined_type = find_child_by_type(node, "predefined_type") is not None
    has_generic_name = find_child_by_type(node, "generic_name") is not None

    if has_predefined_type or has_generic_name:
        # Return type is predefined (int, void, etc.) or generic (Task<T>)
        # First identifier is method name
        if identifiers:
            return node_text(identifiers[0], source)
    else:
        # Return type is a custom type (an identifier)
        # Second identifier is method name
        if len(identifiers) >= 2:
            return node_text(identifiers[1], source)
        elif identifiers:  # pragma: no cover - defensive fallback
            # Fallback: only one identifier means no custom return type detected
            return node_text(identifiers[0], source)
    return None  # pragma: no cover - defensive


# Use the base FileAnalysis; using_aliases maps to import_aliases
FileAnalysis: TypeAlias = _BaseFileAnalysis


def _get_enclosing_class(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up the tree to find the nearest enclosing type's name (class,
    interface, struct, enum or record: ``_CSHARP_TYPE_DECLS``).

    Args:
        node: The current node.
        source: Source bytes for extracting text.

    Returns:
        The name of the enclosing type, or None if not inside a type.
    """
    current = node.parent
    while current is not None:
        if current.type in _CSHARP_TYPE_DECLS:
            name_node = find_child_by_type(current, "identifier")
            if name_node:
                return node_text(name_node, source)
        current = current.parent
    return None  # pragma: no cover - defensive


def _get_csharp_class_ancestors(
    node: "tree_sitter.Node", source: bytes
) -> list[str]:
    """Walk up the tree to find all enclosing class/interface/struct names.

    Returns the chain from outermost to innermost (excluding the current
    node itself). Used to build qualified names for nested types.
    """
    ancestors: list[str] = []
    current = node.parent
    while current is not None:
        if current.type in (
            "class_declaration", "interface_declaration",
            "struct_declaration", "enum_declaration", "record_declaration",
        ):
            name_node = find_child_by_type(current, "identifier")
            if name_node:
                ancestors.append(node_text(name_node, source))
        current = current.parent
    return list(reversed(ancestors))


def _get_csharp_enclosing_namespace(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """The namespace ``node`` sits in, every enclosing one joined outermost
    first (``A.B`` for ``namespace A { namespace B {``), or None.

    Handles both block-style ``namespace Foo.Bar { ... }`` and the C# 10
    file-scoped ``namespace Foo.Bar;``, which the grammar parses as an
    earlier SIBLING of the declarations it covers, not their ancestor. Both
    are joined, so ``qualified_name`` reads ``A.B.C.Go`` (not ``B.C.Go``)
    and ``N.M.K`` (not ``K``) (WI-tusad).
    """
    def name_of(n: "tree_sitter.Node") -> Optional[str]:
        # The name child can be an identifier or qualified_name
        for child in n.children:
            if child.type in ("qualified_name", "identifier"):
                return node_text(child, source)
        return None  # pragma: no cover - a namespace declaration is named
    chain = ancestor_labels(
        node, lambda n: name_of(n) if n.type == "namespace_declaration" else None,
    )
    root = node
    while root.parent is not None:
        root = root.parent
    file_scoped = [
        name_of(c) for c in root.children
        if c.type == "file_scoped_namespace_declaration"
        and c.start_byte < node.start_byte
    ]
    chain = [n for n in file_scoped[-1:] if n] + chain
    return ".".join(chain) if chain else None


#: Type declarations a C# member or nested type can sit in.
_CSHARP_TYPE_DECLS: frozenset[str] = frozenset({
    "class_declaration", "interface_declaration", "struct_declaration",
    "enum_declaration", "record_declaration",
})


def _csharp_scope(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """The block namespaces and types enclosing ``node``, outermost first: what
    a member's and a nested type's stable_id hash beside its own name
    (INV-fabih).

    A block ``namespace A { ... }`` can repeat within a file (two ``class
    Inner`` in namespaces ``A`` and ``B``), so it is a scope; a file-scoped
    ``namespace A;`` is single per file, already identified by the file
    anchor, and left out. Types: every enclosing class, interface, struct,
    enum and record, not only the nearest (``_get_enclosing_class``), which
    two nested ``Inner`` share.
    """
    def label(n: "tree_sitter.Node") -> Optional[str]:
        if n.type == "namespace_declaration":
            name = n.child_by_field_name("name")
        elif n.type in _CSHARP_TYPE_DECLS:
            name = n.child_by_field_name("name")
        else:
            return None
        return node_text(name, source) if name is not None else None
    return ancestor_labels(node, label)


def _csharp_member_chain(node: "tree_sitter.Node", source: bytes, name: str) -> str:
    """``name`` qualified by :func:`_csharp_scope`, the qualified name a member
    hashes (``A.Inner.Go``; the emitted name stays ``Inner.Go``)."""
    return ".".join((*_csharp_scope(node, source), name))


def _make_csharp_qualified_name(
    namespace: Optional[str], ancestors: list[str], name: str
) -> str:
    """Build a C# qualified name from namespace + class chain + symbol name."""
    sep = separator_for_language("csharp")  # "."
    parts: list[str] = []
    if namespace:
        parts.append(namespace)
    parts.extend(ancestors)
    parts.append(name)
    return sep.join(parts)


class _CSharpUsings(NamedTuple):
    """What one file's ``using`` directives say, read in ONE walk
    (:func:`_csharp_usings`), in the shapes three readers need.

    - ``namespaces``, ``type_aliases``: a ``using N;`` and a ``using A = X.Y;``
      whose target is a qualified name, ``using static`` left out (WI-pivap).
      Read by the stable-id type names (:func:`_csharp_type_names`) and the
      base hints the inheritance linker reads (:func:`_csharp_base_hints`).
    - ``path_hints``: local name to a dotted path, the shape the run-wide
      ``NameResolver`` takes as a path hint; read by the ``new T()`` lookup
      only (``using A.B.C;`` gives ``C``, ``using static`` is not told apart).
    - ``scope``: the table handed to the linkers (ADR-0064, piece b). Here
      it holds the file's own directives; :func:`_csharp_project_usings`
      adds the usings of its project before it is handed over.
    - ``project_wide``: the file's ``global using`` directives as
      ``(target, alias)`` (``alias`` ``None`` for a namespace), which are in
      scope in every file of its project.
    """

    namespaces: list[str]
    type_aliases: dict[str, str]
    path_hints: dict[str, str]
    scope: ImportScope
    project_wide: list[tuple[str, Optional[str]]]


#: A dotted path of identifiers: what an alias target must be to name a type
#: or namespace the table can point at (a tuple alias, C# 12, is not one).
_DOTTED_PATH = re.compile(r"@?[A-Za-z_]\w*(\.@?[A-Za-z_]\w*)*")


def _strip_type_arguments(text: str) -> str:
    """``A.B<C<D>>.E`` -> ``A.B.E``: every balanced ``<...>`` removed, and a
    leading ``global::`` (the global namespace, already where a full name
    starts)."""
    out: list[str] = []
    depth = 0
    for ch in text:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(depth - 1, 0)
        elif depth == 0:
            out.append(ch)
    stripped = "".join(out).replace(" ", "")
    return stripped[len("global::"):] if stripped.startswith("global::") else stripped


def _add_using(scope: ImportScope, target: str, alias: Optional[str]) -> None:
    """``using target;`` (``alias`` ``None``) or ``using alias = target;`` into
    the table. ``target`` is a dotted path, type arguments already stripped.

    An alias the table already binds is kept: a project's usings are added
    after the file's own, and C# rejects one alias declared twice in a
    compilation unit (CS1537), so a second binding is never the meaning.
    """
    if alias is None:
        scope.add_open_wildcard(target)
        return
    if scope.named(alias) is not None:
        return
    module, _, name = target.rpartition(".")
    if _DOTTED_PATH.fullmatch(target) is None:
        # Not a type or namespace path (a tuple): the alias still
        # shadows, and names nothing the repository declares.
        module, name = "", target
    scope.add_named(module=module, name=name, alias=alias)


def _csharp_usings(tree: "tree_sitter.Tree", source: bytes) -> _CSharpUsings:
    """Every ``using`` directive of a file, read once (see :class:`_CSharpUsings`).

    The table (``scope``): ``using N;`` and ``global using N;`` bring every
    type of namespace ``N`` into scope (``add_open_wildcard``); an alias
    ``using A = X.Y.Z;`` names one type or namespace (``add_named(module=
    "X.Y", name="Z", alias="A")``, ``module=""`` for ``using A = B;``), its
    type arguments dropped. ``using static X.T;`` brings T's static MEMBERS
    into scope by bare name, which the table cannot say: nothing is recorded
    for it, and a bare call to such a member stays unresolved. A ``global
    using`` (not ``static``) is also listed in ``project_wide``.
    """
    namespaces: list[str] = []
    type_aliases: dict[str, str] = {}
    path_hints: dict[str, str] = {}
    scope = ImportScope()
    project_wide: list[tuple[str, Optional[str]]] = []
    for node in iter_tree(tree.root_node):
        if node.type != "using_directive":
            continue
        static = any(c.type == "static" for c in node.children)
        is_global = any(c.type == "global" for c in node.children)
        qualified = find_child_by_type(node, "qualified_name")
        first_id = find_child_by_type(node, "identifier")
        equals = next((i for i, c in enumerate(node.children) if c.type == "="), None)
        if equals is not None:
            if first_id is None or equals + 1 >= len(node.children):
                continue  # pragma: no cover - the grammar names an alias and its target
            alias = node_text(first_id, source)
            if qualified is not None:
                path_hints[alias] = node_text(qualified, source)
                if not static:
                    type_aliases[alias] = node_text(qualified, source)
            target = _strip_type_arguments(node_text(node.children[equals + 1], source))
            _add_using(scope, target, alias)
            if is_global:
                project_wide.append((target, alias))
            continue
        if qualified is not None:
            full_path = node_text(qualified, source)
            if "." in full_path:
                path_hints[full_path.rsplit(".", 1)[-1]] = full_path
        elif first_id is not None:
            path_hints[node_text(first_id, source)] = node_text(first_id, source)
        target_node = qualified or first_id
        if static or target_node is None:
            continue
        namespaces.append(node_text(target_node, source))
        target = _strip_type_arguments(node_text(target_node, source))
        _add_using(scope, target, None)
        if is_global:
            project_wide.append((target, None))
    return _CSharpUsings(namespaces, type_aliases, path_hints, scope, project_wide)


#: The MSBuild files whose ``<Using>`` items the files of a C# project see.
_MSBUILD_USING_FILES = ["*.csproj", "Directory.Build.props", "Directory.Build.targets"]


def _xml_local_name(name: str) -> str:
    """An element or attribute name without its XML namespace, lower-cased
    (an old-style project file puts every element in the MSBuild 2003
    namespace; MSBuild reads item and attribute names case-insensitively)."""
    return name.rpartition("}")[2].lower()


def _msbuild_usings(path: Path) -> Optional[list[tuple[bool, str, Optional[str]]]]:
    """The ``<Using>`` items of one MSBuild file, in document order, as
    ``(include, target, alias)``: ``include`` is False for ``<Using
    Remove="N" />``. ``None`` when the file cannot be read or is not XML.

    ``Include`` / ``Remove`` may list several items (``A;B``). An item whose
    value is no dotted path (an MSBuild property ``$(X)``, a wildcard) names
    nothing this reader can evaluate and is skipped. ``Static="true"`` is
    ``global using static``, which the table cannot say: skipped. A
    ``Condition`` on the item, its ``ItemGroup`` or a ``Choose`` around it
    is not evaluated (see :func:`_csharp_project_usings`).
    """
    try:
        root = ET.fromstring(path.read_bytes())  # noqa: S314  # nosec B314 - a project file of the analysed repository
    except (OSError, ET.ParseError):
        return None
    items: list[tuple[bool, str, Optional[str]]] = []
    for element in root.iter():
        if _xml_local_name(element.tag) != "using":
            continue
        attrs = {_xml_local_name(k): v.strip() for k, v in element.attrib.items()}
        if attrs.get("static", "").lower() == "true":
            continue
        for key, include in (("include", True), ("remove", False)):
            for value in attrs.get(key, "").split(";"):
                target = _strip_type_arguments(value.strip())
                if _DOTTED_PATH.fullmatch(target) is None:
                    continue
                items.append((include, target, (attrs.get("alias") or None) if include else None))
    return items


def _csharp_project_usings(
    repo_root: Path, usings: dict[Path, _CSharpUsings], run: AnalysisRun,
) -> None:
    """Add to each file's table (``usings[f].scope``) the usings in scope in
    it that are not written in it: C#'s table is "the usings in scope in
    this file", and two kinds of using apply to every file of a project.

    WHICH PROJECT. A file belongs to the project of the nearest directory at
    or above it, within ``repo_root``, that holds a ``*.csproj``: a file
    under two nested projects belongs to the inner one (SDK projects compile
    every ``.cs`` below them, and a repository nests a project in another
    only by excluding it from the outer one). A directory with two
    ``.csproj`` files is ONE project whose MSBuild usings are those of both
    (the two build the same files; a using either declares is in scope in
    some build). Files with no such directory form one group of their own:
    a Unity tree has no project file in the repository and its editor
    compiles every loose script into one assembly (``Assembly-CSharp``).
    Not followed: ``<Compile Include="..\\X.cs">`` items that link a file
    from outside the project's directory, and a project file above
    ``repo_root``.

    1. ``global using`` directives (namespace and alias) of every file of a
       project, in every file of it (``_CSharpUsings.project_wide``).
       ``global using static`` is not recorded, like ``using static``.
    2. MSBuild ``<Using Include="N" />`` items (``Alias="A"`` too, and
       ``<Using Remove="N" />``), which the SDK writes into a generated
       file of ``global using`` lines: read from every
       ``Directory.Build.props`` from ``repo_root`` down to the project's
       directory, then the project file, then every
       ``Directory.Build.targets`` likewise: the order MSBuild evaluates
       them in, so a ``Remove`` removes what was included before it. MSBuild
       itself imports only the NEAREST ``Directory.Build.*``, and an outer
       one only through an ``<Import>`` in it; every one is read here, so a
       using of an outer file that no import reaches is read as in scope.
       A ``Condition`` (on the item, its ``ItemGroup``, a ``Choose``) is not
       evaluated: the using is in scope, a namespace that is in scope in
       some build configuration. ``<Import Project=...>`` is not followed.
       The SDK's implicit usings (``<ImplicitUsings>enable``) are not added:
       they are ``System.*`` namespaces, where the repository declares
       nothing. A file that is not XML is skipped and named in a warning of
       the run.

    Each file's table gets them after its own usings, in a fixed order (the
    files of a project by path), whatever order the files were read in.
    """
    by_dir: dict[Path, list[Path]] = {}
    for path in sorted(find_files(repo_root, _MSBUILD_USING_FILES)):
        by_dir.setdefault(path.parent, []).append(path)
    projects = {d for d, paths in by_dir.items() if any(p.suffix.lower() == ".csproj" for p in paths)}
    read: dict[Path, Optional[list[tuple[bool, str, Optional[str]]]]] = {}

    def msbuild_usings(project: Path) -> list[tuple[str, Optional[str]]]:
        above = [d for d in (project, *project.parents) if d.is_relative_to(repo_root)][::-1]
        files = [p for d in above for p in by_dir.get(d, ()) if p.name == "Directory.Build.props"]
        files += [p for p in by_dir[project] if p.suffix.lower() == ".csproj"]
        files += [p for d in above for p in by_dir.get(d, ()) if p.name == "Directory.Build.targets"]
        kept: dict[tuple[str, Optional[str]], None] = {}
        for path in files:
            if path not in read:
                read[path] = _msbuild_usings(path)
            for include, target, alias in read[path] or ():
                if include:
                    kept[(target, alias)] = None
                else:
                    for key in [k for k in kept if k[0] == target]:
                        del kept[key]
        return list(kept)

    groups: dict[Optional[Path], list[Path]] = {}
    for cs_file in sorted(usings):
        project = next((d for d in cs_file.parents if d in projects), None)
        groups.setdefault(project, []).append(cs_file)
    for project, members in groups.items():
        in_scope = [u for f in members for u in usings[f].project_wide]
        if project is not None:
            in_scope += msbuild_usings(project)
        for cs_file in members:
            for target, alias in in_scope:
                _add_using(usings[cs_file].scope, target, alias)
    unread = sorted(str(p.relative_to(repo_root)) for p, items in read.items() if items is None)
    if unread:
        run.warnings.append(
            f"csharp: {len(unread)} MSBuild file(s) are not XML; their <Using> items "
            f"were not read: {', '.join(unread[:5])}"
        )


def _extract_base_list(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract base classes and interfaces from base_list (META-001).

    C# syntax: class User : BaseModel, IEntity, IDisposable { }
    AST structure:
        class_declaration
            identifier "User"
            base_list
                identifier "BaseModel"
                identifier "IEntity"
                identifier "IDisposable"

    Returns list of base type names.
    """
    base_classes: list[str] = []

    for child in node.children:
        if child.type == "base_list":
            for base_child in child.children:
                # A record's ``: Base(X)`` passes its primary constructor's
                # arguments on: the type is the node's first child.
                if base_child.type == "primary_constructor_base_type":
                    base_child = base_child.children[0]
                if base_child.type in ("identifier", "generic_name", "qualified_name"):
                    base_name = node_text(base_child, source)
                    # Strip generic parameters: List<int> -> List
                    if "<" in base_name:
                        base_name = base_name.split("<")[0]
                    if base_name:
                        base_classes.append(base_name)
            break

    return base_classes


#: The node types that declare a type name in a C# file.
_CSHARP_TYPE_DECLARATION_NODES = frozenset({
    "class_declaration", "struct_declaration", "interface_declaration",
    "enum_declaration", "record_declaration", "record_struct_declaration",
    "delegate_declaration",
})


#: C#'s keyword types, by the ``System`` type each one aliases (C# language
#: specification, "Simple types" and the ``object`` / ``string`` reference
#: types; ``nint`` / ``nuint`` are aliases of ``System.IntPtr`` /
#: ``System.UIntPtr`` since C# 11). Not listed: ``dynamic``, which the
#: specification gives its own conversions rather than an alias, and ``void``,
#: whose ``System.Void`` cannot be written as a type.
_CSHARP_KEYWORD_TYPES: dict[str, str] = {
    "Boolean": "bool", "Byte": "byte", "SByte": "sbyte", "Char": "char",
    "Decimal": "decimal", "Double": "double", "Single": "float",
    "Int16": "short", "UInt16": "ushort", "Int32": "int", "UInt32": "uint",
    "Int64": "long", "UInt64": "ulong", "IntPtr": "nint", "UIntPtr": "nuint",
    "Object": "object", "String": "string",
}


def _csharp_type_names(
    tree: "tree_sitter.Tree", source: bytes, usings: _CSharpUsings,
) -> FileTypeNames:
    """What the file's ``using`` directives make a type name mean (WI-palus,
    scheme v17), and which spelling of a built-in type is hashed (WI-juzom,
    scheme v19).

    Bound: each alias, ``using Str = System.String;`` (and ``global using``),
    to what it names (:func:`_csharp_usings`), so ``Str`` and
    ``System.String`` hash alike, and ``Gen.List`` under ``using Gen =
    System.Collections.Generic;`` is ``System.Collections.Generic.List``. A
    ``using System;`` imports a namespace and binds no other name the file
    writes: ``Date`` and ``Foo.Date`` stay apart (a type is hashed as written,
    WI-ririg). It does bind the bare framework names of the built-in types
    (``String``, ``Int32``, ...) that the file neither aliases nor declares.
    Defaults: the alias table (``_CSHARP_KEYWORD_TYPES``), so
    ``System.String``, ``global::System.String`` and that bare ``String`` are
    hashed as the keyword ``string``, the spelling C# code writes most. A
    ``String`` the file declares keeps its name. Declared: every type the file
    declares.
    """
    namespaces, aliases = usings.namespaces, usings.type_aliases
    declared = set()
    for node in iter_tree(tree.root_node):
        if node.type in _CSHARP_TYPE_DECLARATION_NODES:
            name = node.child_by_field_name("name")
            if name is not None:
                declared.add(node_text(name, source))
    bindings = list(aliases.items())
    if "System" in namespaces:
        bindings += [(n, f"System.{n}") for n in _CSHARP_KEYWORD_TYPES if n not in aliases]
    return file_type_names(
        bindings, declared=declared, root="global::",
        defaults={f"System.{n}": kw for n, kw in _CSHARP_KEYWORD_TYPES.items()},
    )


def _csharp_base_hints(
    base_classes: list[str], ns_name: str | None,
    using_namespaces: list[str], using_aliases: dict[str, str],
) -> dict[str, object]:
    """Meta the inheritance linker reads to check a BARE base (WI-pivap):
    ``visible_namespaces`` (the enclosing namespace chain, the ``using``
    namespaces and the global namespace) and ``base_class_imports`` (a base
    written as a ``using`` alias)."""
    chain = (ns_name or "").split(".") if ns_name else []
    visible = [".".join(chain[:i]) for i in range(len(chain), 0, -1)]
    hints: dict[str, object] = {
        "visible_namespaces": [*visible, *using_namespaces, ""],
    }
    imports = {b: using_aliases[b] for b in base_classes if b in using_aliases}
    if imports:
        hints["base_class_imports"] = imports
    return hints


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
    rel_path: str,
) -> tuple[FileAnalysis, Optional[_CSharpUsings]]:
    """Extract symbols from a single C# file, and what its ``using``
    directives say (:func:`_csharp_usings`; ``None`` for a file that could not
    be read).

    Uses iterative tree traversal to avoid RecursionError on deeply nested code.

    ``rel_path`` is the repo-relative path of ``file_path`` (computed by the
    ``analyze()`` override, which holds ``repo_root``). It is the
    file-identity input (WI-bokab, v7) — folded into ``make_typed_stable_id``'s
    ``containing_stable_id`` slot so same-``(kind, name, qualified_name)`` methods
    and constructors in different files hash distinctly. ``file_path`` itself is
    ABSOLUTE here (it comes from ``find_csharp_files(repo_root)``), so it must NOT
    be used for the anchor — folding an absolute path would make stable_ids
    location-dependent.
    """
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError) as e:  # pragma: no cover - IO errors hard to trigger in tests
        run.record_failed_file(str(file_path), f"{type(e).__name__}: {e}")
        return FileAnalysis(), None
    record_partial_parse(run, rel_path, tree)  # WI-bulaz
    usings = _csharp_usings(tree, source)
    # WI-palus: every type a stable id hashes is written through the aliases.
    with resolving_type_names(_csharp_type_names(tree, source, usings)):
        return _extract_parsed_symbols(file_path, source, tree, run, rel_path, usings), usings


def _extract_parsed_symbols(
    file_path: Path,
    source: bytes,
    tree: "tree_sitter.Tree",
    run: AnalysisRun,
    rel_path: str,
    usings: _CSharpUsings,
) -> FileAnalysis:
    """The symbols of one parsed C# file (:func:`_extract_symbols_from_file`)."""

    # File-identity anchor for this file's symbols (WI-bokab, v7). Built from the
    # repo-relative ``rel_path`` (NOT the absolute ``file_path``) so it byte-matches
    # the file Symbol's own stable_id (``populate_kind_stable_ids`` computes the same
    # value from the normalized repo-relative path). Folded into
    # make_typed_stable_id's containing slot below.
    file_stable_id = make_file_stable_id("csharp", normalize_path(rel_path))

    using_namespaces, using_type_aliases = usings.namespaces, usings.type_aliases
    analysis = FileAnalysis(import_aliases=usings.path_hints)

    def extract_name_from_declaration(node: "tree_sitter.Node") -> Optional[str]:
        """Extract the declared name from a declaration node.

        Uses the grammar's ``name`` field, which points at the member/type name
        unambiguously. A bare first-``identifier`` scan is WRONG for
        ``property_declaration``: a user-defined return type (``WaveInfo``,
        ``FeatureConfig``) is itself an ``identifier`` that PRECEDES the property
        name, so the first identifier is the type, not the member. Predefined /
        array / generic return types are non-``identifier`` nodes, which is why
        the old scan only misfired on user-typed properties. Every declaration
        kind routed here (class/interface/struct/enum/constructor/property) tags
        its name with a ``name`` field, so this is a strict correction with no
        behavior change for the non-property kinds.
        """
        name_node = node.child_by_field_name("name")
        if name_node:
            return node_text(name_node, source)
        return None  # pragma: no cover - defensive

    for node in iter_tree(tree.root_node):
        # Class or record declaration. A record (``record``, ``record class``,
        # ``record struct``) is a type with a class's bases, attributes and
        # members, emitted as kind "record", so its members are not bare and
        # their calls do not bind to other classes (WI-zukat).
        if node.type in ("class_declaration", "record_declaration"):
            type_kind = "record" if node.type == "record_declaration" else "class"
            name = extract_name_from_declaration(node)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract annotations for FRAMEWORK_PATTERNS phase
                annotations = _extract_annotations(node, source)
                # Extract base classes/interfaces (META-001)
                base_classes = _extract_base_list(node, source)

                meta: dict[str, object] | None = None
                if annotations or base_classes:
                    meta = {}
                    if annotations:
                        meta["annotations"] = annotations
                    if base_classes:
                        meta["base_classes"] = base_classes

                class_modifiers = _extract_modifiers(node)
                ns_name = _get_csharp_enclosing_namespace(node, source)
                if meta is not None and base_classes:
                    meta.update(_csharp_base_hints(
                        base_classes, ns_name, using_namespaces, using_type_aliases,
                    ))
                cls_ancestors = _get_csharp_class_ancestors(node, source)
                symbol = Symbol(
                    id=make_symbol_id("csharp", str(file_path), start_line, end_line, name, type_kind),
                    name=name,
                    kind=type_kind,
                    language="csharp",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta=meta,
                    # INV-fabih: the table formula over the enclosing block
                    # namespaces and types (the backstop's value at top level).
                    # A record is no table kind, so it mints the ADR-0035
                    # producer formula over the same chain, as Java's does.
                    stable_id=(scoped_stable_id if type_kind == "record" else scoped_table_stable_id)(
                        kind=type_kind, lang="csharp", path=rel_path, name=name,
                        scope=_csharp_scope(node, source),
                    ),
                    modifiers=class_modifiers,
                    line_span=end_line - start_line + 1,
                    is_exported="public" in class_modifiers,
                    qualified_name=_make_csharp_qualified_name(ns_name, cls_ancestors, name),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[name] = symbol

        # Interface declaration
        elif node.type == "interface_declaration":
            name = extract_name_from_declaration(node)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                iface_modifiers = _extract_modifiers(node)
                # WI-ruzid: ``interface IChild : IBase`` records its bases as
                # a class does; the inheritance linker turns an interface
                # source's bases into ``extends`` edges (WI-kalug).
                iface_bases = _extract_base_list(node, source)
                ns_name = _get_csharp_enclosing_namespace(node, source)
                cls_ancestors = _get_csharp_class_ancestors(node, source)
                symbol = Symbol(
                    id=make_symbol_id("csharp", str(file_path), start_line, end_line, name, "interface"),
                    name=name,
                    kind="interface",
                    language="csharp",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    # INV-fabih: the table formula over the enclosing block
                    # namespaces and types (the backstop's value at top level).
                    stable_id=scoped_table_stable_id(
                        kind="interface", lang="csharp", path=rel_path, name=name,
                        scope=_csharp_scope(node, source),
                    ),
                    modifiers=iface_modifiers,
                    meta={
                        "base_classes": iface_bases,
                        **_csharp_base_hints(
                            iface_bases, ns_name, using_namespaces, using_type_aliases,
                        ),
                    } if iface_bases else None,
                    line_span=end_line - start_line + 1,
                    is_exported="public" in iface_modifiers,
                    qualified_name=_make_csharp_qualified_name(ns_name, cls_ancestors, name),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[name] = symbol

        # Struct declaration
        elif node.type == "struct_declaration":
            name = extract_name_from_declaration(node)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                struct_modifiers = _extract_modifiers(node)
                ns_name = _get_csharp_enclosing_namespace(node, source)
                cls_ancestors = _get_csharp_class_ancestors(node, source)
                symbol = Symbol(
                    id=make_symbol_id("csharp", str(file_path), start_line, end_line, name, "struct"),
                    name=name,
                    kind="struct",
                    language="csharp",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    # INV-fabih: the table formula over the enclosing block
                    # namespaces and types (the backstop's value at top level).
                    stable_id=scoped_table_stable_id(
                        kind="struct", lang="csharp", path=rel_path, name=name,
                        scope=_csharp_scope(node, source),
                    ),
                    modifiers=struct_modifiers,
                    line_span=end_line - start_line + 1,
                    is_exported="public" in struct_modifiers,
                    qualified_name=_make_csharp_qualified_name(ns_name, cls_ancestors, name),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[name] = symbol

        # Enum declaration
        elif node.type == "enum_declaration":
            name = extract_name_from_declaration(node)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                enum_modifiers = _extract_modifiers(node)
                ns_name = _get_csharp_enclosing_namespace(node, source)
                cls_ancestors = _get_csharp_class_ancestors(node, source)
                symbol = Symbol(
                    id=make_symbol_id("csharp", str(file_path), start_line, end_line, name, "enum"),
                    name=name,
                    kind="enum",
                    language="csharp",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    # INV-fabih: the table formula over the enclosing block
                    # namespaces and types (the backstop's value at top level).
                    stable_id=scoped_table_stable_id(
                        kind="enum", lang="csharp", path=rel_path, name=name,
                        scope=_csharp_scope(node, source),
                    ),
                    modifiers=enum_modifiers,
                    line_span=end_line - start_line + 1,
                    is_exported="public" in enum_modifiers,
                    qualified_name=_make_csharp_qualified_name(ns_name, cls_ancestors, name),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[name] = symbol

                # WI-duguk: the enum's MEMBERS. Without them the enum is a
                # container with nothing in it, so the containment linker roots
                # nothing and `slice --reverse` from the enum returns the
                # container alone. Named `Color.Red` to match the class-field
                # branch below, whose `.` the containment linker splits on.
                # An explicitly-valued member (`Green = 2`) carries the literal
                # after its identifier and is otherwise the same node.
                member_list = find_child_by_type(
                    node, "enum_member_declaration_list",
                )
                enum_chain = _csharp_member_chain(node, source, name)
                for member in member_list.children if member_list else ():
                    if member.type != "enum_member_declaration":
                        continue
                    m_name_node = find_child_by_type(member, "identifier")
                    if m_name_node is None:  # pragma: no cover - always named
                        continue
                    m_name = node_text(m_name_node, source)
                    m_full = f"{name}.{m_name}"
                    m_start = member.start_point[0] + 1
                    m_end = member.end_point[0] + 1
                    m_qualified = _make_csharp_qualified_name(
                        ns_name, [*cls_ancestors, name], m_name,
                    )
                    m_sym = Symbol(
                        id=make_symbol_id(
                            "csharp", str(file_path), m_start, m_end,
                            m_full, "field",
                        ),
                        name=m_full,
                        kind="field",
                        language="csharp",
                        path=str(file_path),
                        span=Span(
                            start_line=m_start,
                            end_line=m_end,
                            start_col=member.start_point[1],
                            end_col=member.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        stable_id=make_typed_stable_id(
                            "field", name,
                            visibility_from_modifiers(enum_modifiers),
                            name=m_name, qualified_name=f"{enum_chain}.{m_name}",
                            file_stable_id=file_stable_id,
                        ),
                        modifiers=enum_modifiers,
                        line_span=m_end - m_start + 1,
                        # A member is as reachable as its enum; C# has no
                        # per-member access modifier.
                        is_exported="public" in enum_modifiers,
                        qualified_name=m_qualified,
                    )
                    analysis.symbols.append(m_sym)
                    analysis.node_for_symbol[m_sym.id] = member
                    analysis.symbol_by_name[m_full] = m_sym

        # Method declaration
        elif node.type == "method_declaration":
            name = _extract_method_name(node, source)
            if name:
                current_class = _get_enclosing_class(node, source)
                if current_class:
                    full_name = f"{current_class}.{name}"
                else:
                    full_name = name  # pragma: no cover - should always be in class

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract all annotations for FRAMEWORK_PATTERNS phase
                annotations = _extract_annotations(node, source)

                # Build meta dict
                meta = None
                if annotations:
                    meta = {"annotations": annotations}

                # Extract signature
                signature = _extract_csharp_signature(node, source, is_constructor=False)
                modifiers = _extract_modifiers(node)

                # Typed stable_id (spec §6 typed tier)
                norm_sig = normalize_with_type_parameters(
                    normalize_csharp_signature, signature, node, source)
                # INV-fabih: the full namespace/type chain, not the nearest
                # owner (``full_name``) two nested owners share.
                stable_id = make_typed_stable_id(
                    "method", norm_sig, visibility_from_modifiers(modifiers),
                    name=name, qualified_name=_csharp_member_chain(node, source, name),
                    file_stable_id=file_stable_id,
                    is_async=declares_async(node),
                ) if norm_sig else None

                ns_name = _get_csharp_enclosing_namespace(node, source)
                cls_ancestors = _get_csharp_class_ancestors(node, source)
                symbol = Symbol(
                    id=make_symbol_id("csharp", str(file_path), start_line, end_line, full_name, "method"),
                    name=full_name,
                    kind="method",
                    language="csharp",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta=meta,
                    stable_id=stable_id,
                    signature=signature,
                    docstring=extract_preceding_doc_comment(node, source, "csharp"),
                    modifiers=modifiers,
                    line_span=end_line - start_line + 1,
                    is_exported="public" in modifiers,
                    qualified_name=_make_csharp_qualified_name(ns_name, cls_ancestors, name),
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, "csharp"),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[name] = symbol
                analysis.symbol_by_name[full_name] = symbol

        # Constructor declaration
        elif node.type == "constructor_declaration":
            name = extract_name_from_declaration(node)
            if name:
                current_class = _get_enclosing_class(node, source)
                full_name = f"{current_class}.{name}" if current_class else name

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract signature (constructors have no return type)
                signature = _extract_csharp_signature(node, source, is_constructor=True)
                modifiers = _extract_modifiers(node)

                # Typed stable_id (spec §6 typed tier)
                norm_sig = normalize_with_type_parameters(
                    normalize_csharp_signature, signature, node, source)
                stable_id = make_typed_stable_id(
                    "constructor", norm_sig, visibility_from_modifiers(modifiers),
                    name=name, qualified_name=_csharp_member_chain(node, source, name),
                    file_stable_id=file_stable_id,
                ) if norm_sig else None

                ns_name = _get_csharp_enclosing_namespace(node, source)
                cls_ancestors = _get_csharp_class_ancestors(node, source)
                symbol = Symbol(
                    id=make_symbol_id("csharp", str(file_path), start_line, end_line, full_name, "constructor"),
                    name=full_name,
                    kind="constructor",
                    language="csharp",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    stable_id=stable_id,
                    signature=signature,
                    docstring=extract_preceding_doc_comment(node, source, "csharp"),
                    modifiers=modifiers,
                    line_span=end_line - start_line + 1,
                    is_exported="public" in modifiers,
                    qualified_name=_make_csharp_qualified_name(ns_name, cls_ancestors, name),
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, "csharp"),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[name] = symbol
                analysis.symbol_by_name[full_name] = symbol

        # Property declaration
        elif node.type == "property_declaration":
            name = extract_name_from_declaration(node)
            if name:
                current_class = _get_enclosing_class(node, source)
                full_name = f"{current_class}.{name}" if current_class else name

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                prop_modifiers = _extract_modifiers(node)
                ns_name = _get_csharp_enclosing_namespace(node, source)
                cls_ancestors = _get_csharp_class_ancestors(node, source)
                prop_qualified = _make_csharp_qualified_name(ns_name, cls_ancestors, name)
                prop_type = node.child_by_field_name("type")
                symbol = Symbol(
                    id=make_symbol_id("csharp", str(file_path), start_line, end_line, full_name, "property"),
                    name=full_name,
                    kind="property",
                    language="csharp",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    # The block namespaces and types around it (a file-scoped
                    # namespace is single per file: the file anchor has it).
                    stable_id=make_typed_stable_id(
                        "property",
                        canonical_type_names(node_text(prop_type, source))
                        if prop_type is not None else "",
                        visibility_from_modifiers(prop_modifiers),
                        name=name, qualified_name=_csharp_member_chain(node, source, name),
                        file_stable_id=file_stable_id,
                    ),
                    modifiers=prop_modifiers,
                    line_span=end_line - start_line + 1,
                    is_exported="public" in prop_modifiers,
                    qualified_name=prop_qualified,
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[name] = symbol
                analysis.symbol_by_name[full_name] = symbol

        # Indexer / operator / conversion operator / destructor / event with
        # accessors -- member forms with a body, given a symbol so their calls
        # are not credited to the class (WI-binap).
        elif node.type in _CSHARP_BODY_MEMBER_DECLS:
            body_member = _csharp_body_member(node, source)
            if body_member is not None:
                m_kind, m_name, m_signature = body_member
                current_class = _get_enclosing_class(node, source)
                full_name = f"{current_class}.{m_name}" if current_class else m_name
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                modifiers = _extract_modifiers(node)
                annotations = _extract_annotations(node, source)
                ns_name = _get_csharp_enclosing_namespace(node, source)
                cls_ancestors = _get_csharp_class_ancestors(node, source)
                # The event's "signature" is its delegate type (a field's is its
                # type); the others' parameter lists normalise like a method's.
                norm_sig = (
                    m_signature if m_kind == "event"
                    else normalize_csharp_signature(m_signature) or m_signature
                )
                symbol = Symbol(
                    id=make_symbol_id("csharp", str(file_path), start_line, end_line, full_name, m_kind),
                    name=full_name,
                    kind=m_kind,
                    language="csharp",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta={"annotations": annotations} if annotations else None,
                    stable_id=make_typed_stable_id(
                        m_kind, norm_sig, visibility_from_modifiers(modifiers),
                        name=m_name,
                        qualified_name=_csharp_member_chain(node, source, m_name),
                        file_stable_id=file_stable_id,
                    ),
                    signature=m_signature,
                    docstring=extract_preceding_doc_comment(node, source, "csharp"),
                    modifiers=modifiers,
                    line_span=end_line - start_line + 1,
                    is_exported="public" in modifiers,
                    qualified_name=_make_csharp_qualified_name(ns_name, cls_ancestors, m_name),
                    cyclomatic_complexity=(
                        None if m_kind == "event"
                        else compute_cyclomatic_complexity(node, "csharp")
                    ),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                # Only the qualified name: none of these is called by a bare name
                # (``this[]``, ``operator+``, ``~A``), and an event with accessors
                # cannot be invoked at all (CS0079).
                analysis.symbol_by_name[full_name] = symbol

        # Field declarations — WI-jusus (emission-parity F5): emit a kind="field"
        # Symbol per declarator AND populate class_field_types for chained-call
        # resolution. C# field attributes ([Inject] DI, EF [Column]/[Key] ORM)
        # live in attribute_list and flow through meta["annotations"] into
        # _extract_attribute_edges as decorated_by edges (anchored on the field).
        # Properties are a separate property_declaration -> kind="property".
        elif node.type == "field_declaration":
            enclosing = _get_enclosing_class(node, source)
            if enclosing:
                var_decl = find_child_by_type(node, "variable_declaration")
                if var_decl:
                    # Full declared type (signature): the first child of the
                    # variable_declaration (identifier / predefined_type /
                    # generic_name / qualified_name / array_type, ...).
                    type_node = var_decl.children[0] if var_decl.children else None
                    field_type = (
                        node_text(type_node, source) if type_node is not None else None
                    )
                    # Bare user type NAME (for class_field_types chained resolution).
                    type_name = None
                    type_id = find_child_by_type(var_decl, "identifier")
                    if type_id:
                        type_name = node_text(type_id, source)
                    else:
                        gen_name = find_child_by_type(var_decl, "generic_name")
                        if gen_name:
                            gen_id = find_child_by_type(gen_name, "identifier")
                            if gen_id:
                                type_name = node_text(gen_id, source)
                    modifiers = _extract_modifiers(node)
                    annotations = _extract_annotations(node, source)
                    ns_name = _get_csharp_enclosing_namespace(node, source)
                    cls_ancestors = _get_csharp_class_ancestors(node, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    for var_declarator in var_decl.children:
                        if var_declarator.type != "variable_declarator":
                            continue
                        name_node = find_child_by_type(var_declarator, "identifier")
                        if name_node is None:
                            continue  # pragma: no cover - a declarator always names a field
                        field_name = node_text(name_node, source)
                        full_name = f"{enclosing}.{field_name}"
                        qualified = _make_csharp_qualified_name(
                            ns_name, cls_ancestors, field_name,
                        )
                        f_sym = Symbol(
                            id=make_symbol_id("csharp", str(file_path), start_line, end_line, full_name, "field"),
                            name=full_name,
                            kind="field",
                            language="csharp",
                            path=str(file_path),
                            span=Span(
                                start_line=start_line,
                                end_line=end_line,
                                start_col=node.start_point[1],
                                end_col=node.end_point[1],
                            ),
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            meta={"annotations": annotations} if annotations else None,
                            stable_id=make_typed_stable_id(
                                "field", canonical_type_names(field_type or ""),
                                visibility_from_modifiers(modifiers),
                                name=field_name,
                                qualified_name=_csharp_member_chain(node, source, field_name),
                                file_stable_id=file_stable_id,
                            ),
                            signature=field_type,
                            modifiers=modifiers,
                            line_span=end_line - start_line + 1,
                            is_exported="public" in modifiers,
                            qualified_name=qualified,
                        )
                        analysis.symbols.append(f_sym)
                        analysis.node_for_symbol[f_sym.id] = node
                        analysis.symbol_by_name[field_name] = f_sym
                        analysis.symbol_by_name[full_name] = f_sym
                        if type_name:
                            if enclosing not in analysis.class_field_types:
                                analysis.class_field_types[enclosing] = {}
                            analysis.class_field_types[enclosing][field_name] = type_name

    # Post-pass (INV-jahiv) to populate docstrings on any
    # symbols (e.g. class/interface/struct/enum/property) whose emit
    # site does not call extract_preceding_doc_comment inline. CSharp
    # overrides analyze() and bypasses the base run_pipeline path, so
    # we re-implement the same post-pass here.
    populate_docstrings_from_tree(tree.root_node, source, analysis.symbols)

    return analysis


def _enclosing_member(node: "tree_sitter.Node", decl_index: SymbolsAt) -> Optional[Symbol]:
    """The member whose declaration contains ``node``: a method, constructor,
    property, indexer, operator, destructor or event (``_CSHARP_MEMBER_BODY_NODES``).

    Keyed by the declaration's POSITION, not its name (INV-midag). The short
    name is shared by overloads, by two indexers (both ``this[]``), and by one
    method name in two classes of one file: livebook's ElixirKit.cs defines
    ``Stop`` / ``Subscribe`` in both ``API`` and ``Release`` / ``Client``. A
    name lookup would return whichever registered last.
    """
    return enclosing_declared_symbol(node, decl_index, _CSHARP_MEMBER_BODY_NODES)


#: INV-bamij: type declarations whose BODY anchors a call in no method (a
#: static field's lambda or initialiser).
_CSHARP_TYPE_BODY_NODES: frozenset[str] = frozenset({
    "class_declaration", "struct_declaration", "interface_declaration",
    "record_declaration", "enum_declaration",
})


def _csharp_unresolved_module(
    receiver_name: Optional[str],
    var_types: dict[str, str],
    local_symbols: dict[str, Symbol],
    enclosing_class: Optional[str],
) -> str:
    """The module slot for a call nothing resolved (INV-bamij's csharp half).

    The receiver's own spelling only when it names a TYPE: capitalised, not a
    tracked local, and not a member of the enclosing class (C# spells
    properties in PascalCase too, so ``Config.Load()`` may be a call on a
    property, and a member name in the slot is INV-kotob's defect). Anything
    else is ``external``.
    """
    if (
        receiver_name
        and receiver_name[:1].isupper()
        and receiver_name not in var_types
        and f"{enclosing_class}.{receiver_name}" not in local_symbols
    ):
        return receiver_name
    return "external"


#: A declaration of a local name inside a function, by the field that names
#: it (a ``variable_declarator`` and a lambda's ``implicit_parameter`` are
#: read apart): what makes ``x`` in ``x.M()`` a variable and not a type.
_CSHARP_LOCAL_NAME_FIELDS: dict[str, str] = {
    "parameter": "name", "foreach_statement": "left",
    "catch_declaration": "name", "declaration_pattern": "name",
    "declaration_expression": "name",
}


def _declared_local_names(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """The local names ``node`` declares: a variable (``var x``, ``Foo x``,
    ``var (a, b)``), a parameter (of a member, a local function or a lambda),
    a ``foreach`` / ``catch`` / pattern / ``out var`` variable. Empty for any
    other node. Each is a declaration by the grammar, so a name found here IS
    a variable; a variable this misses is only not known to be one."""
    if node.type == "implicit_parameter":
        return [node_text(node, source)]
    if node.type == "variable_declarator":
        tuple_pattern = find_child_by_type(node, "tuple_pattern")
        names = (
            [c for c in tuple_pattern.children if c.type == "identifier"]
            if tuple_pattern is not None
            else _find_children_by_type(node, "identifier")[:1]
        )
        return [node_text(n, source) for n in names]
    field = _CSHARP_LOCAL_NAME_FIELDS.get(node.type)
    named = node.child_by_field_name(field) if field is not None else None
    if named is not None and named.type == "identifier":
        return [node_text(named, source)]
    return []


def _local_function_in_scope(
    node: "tree_sitter.Node", name: str, source: bytes,
) -> bool:
    """Whether a local function called ``name`` is visible at ``node``: one
    declared in a block (or a top-level statement) that encloses ``node``. C#
    makes a local function visible throughout its block, before its
    declaration too, and it hides a member of the same name. A local function
    has no symbol, so such a call is to nothing the graph holds."""
    current = node.parent
    while current is not None and current.type not in _CSHARP_TYPE_DECLS:
        for child in current.children:
            if child.type == "global_statement" and child.named_children:
                child = child.named_children[0]
            if child.type == "local_function_statement":
                declared = child.child_by_field_name("name")
                if declared is not None and node_text(declared, source) == name:
                    return True
        current = current.parent
    return False


def _dotted_receiver(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """The receiver as a dotted name when it can name a type or a namespace
    path (``Util``, ``Ns.Util``, ``List<int>`` as ``List``), else None (a
    call, ``this``, an indexer, a literal ...)."""
    if node.type == "identifier":
        return node_text(node, source)
    if node.type == "generic_name":
        head = find_child_by_type(node, "identifier")
        return node_text(head, source) if head is not None else None
    if node.type == "member_access_expression":
        expression = node.child_by_field_name("expression")
        name = node.child_by_field_name("name")
        if expression is None or name is None or name.type != "identifier":
            return None
        owner = _dotted_receiver(expression, source)
        return f"{owner}.{node_text(name, source)}" if owner else None
    return None


def _resolve_member_chain(
    node: "tree_sitter.Node",
    source: bytes,
    field_type_registry: dict[str, dict[str, str]],
    enclosing_class: str | None,
) -> str | None:
    """Resolve a chained member_access_expression rooted at ``this`` to a type.

    Walks nested ``member_access_expression`` nodes to decompose a receiver like
    ``this._inner._db`` into segments [``_inner``, ``_db``], then iteratively
    resolves through ``field_type_registry`` starting from ``enclosing_class``.

    Returns the resolved type name, or None if resolution fails at any step.
    """
    if not field_type_registry or enclosing_class is None:
        return None  # pragma: no cover - caller checks registry exists

    # Decompose the chain leaf-to-root.
    segments: list[str] = []
    cur = node
    while cur.type == "member_access_expression":
        ids = _find_children_by_type(cur, "identifier")
        if ids:
            segments.append(node_text(ids[-1], source))
        else:
            return None  # pragma: no cover - valid C# always has identifiers
        # Descend into the nested member_access_expression (the receiver part)
        inner = find_child_by_type(cur, "member_access_expression")
        if inner:
            cur = inner
        else:
            # Check if the remaining child is ``this``
            this_node = find_child_by_type(cur, "this")
            if this_node:
                break
            return None  # pragma: no cover - non-this base (e.g. foo._x)

    # Must be rooted at ``this``
    if cur.type == "member_access_expression":
        if not find_child_by_type(cur, "this"):
            return None  # pragma: no cover - defensive
    elif cur.type != "this":  # pragma: no cover - defensive
        return None

    # Segments were collected leaf-to-root; reverse to walk root-to-leaf.
    segments.reverse()

    # Walk the chain starting from enclosing class.
    current_type = enclosing_class
    for field_name in segments:
        fields = field_type_registry.get(current_type)
        if fields is None:
            return None
        next_type = fields.get(field_name)
        if next_type is None:
            return None
        current_type = next_type

    return current_type


def _count_signature_params(signature: "str | None") -> "int | None":
    """Number of top-level parameters in a C# ctor/method signature string.

    ``"(int a, string b)"`` -> 2, ``"()"`` -> 0. Commas nested inside angle
    brackets / parens / brackets / braces — generics like ``Dict<K, V>``, tuple
    types like ``(int, int)`` — are not counted as separators. Returns ``None``
    when there is no parseable parameter list. Used for arity-based
    overloaded-constructor disambiguation (WI-mivav); an exact param/arg count
    match is a heuristic, not full C# overload resolution (optional and
    ``params`` arguments are not modeled — those fall back to the collapsed pick).
    """
    if not signature:
        return None
    start = signature.find("(")
    end = signature.rfind(")")
    if start == -1 or end == -1 or end < start:
        return None
    inner = signature[start + 1:end].strip()
    if not inner:
        return 0
    depth = 0
    count = 1
    for ch in inner:
        if ch in "(<[{":
            depth += 1
        elif ch in ")>]}":
            depth = depth - 1 if depth > 0 else 0
        elif ch == "," and depth == 0:
            count += 1
    return count


def _extract_edges_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run: AnalysisRun,
    resolver: NameResolver | None = None,
    using_aliases: dict[str, str] | None = None,
    field_type_registry: dict[str, dict[str, str]] | None = None,
    global_ctors: dict[str, list[Symbol]] | None = None,
    file_symbols: list[Symbol] | None = None,
) -> list[Edge]:
    """Extract call, import, and instantiation edges from a file.

    Uses iterative tree traversal to avoid RecursionError on deeply nested code.

    Type inference tracks types from:
    - Constructor calls: var db = new Database() -> db has type Database
    - Method/constructor parameters: void Process(Database db) -> db has type Database
    - Fields, and a call this file resolves whose declared return type is a
      type name: var db = GetDb() -> db has the type GetDb returns

    A CALL IS BOUND HERE ONLY ON EVIDENCE C# ITSELF USES (ADR-0064): the
    enclosing type's own method in this file (``own_type``), and a method of
    the receiver's known type in this file (``receiver_type``). Anything else
    is emitted unresolved WITH the facts the shared pass reads, and the
    ``inherited_calls`` linker decides from the file's ``using`` table
    (``AnalysisResult.import_scopes``) and the caller's namespaces:

    - a receiver of known type: ``receiver_type_hint``, the type as written;
    - a receiver that names a type or a namespace path (``Util.Helper()``,
      ``Ns.Util.Helper()``) and is no variable or member known here:
      ``qualifier``, the receiver as written;
    - a bare call, ``this.M()``, ``base.M()``: ``enclosing_class``, unless a
      local function or a local variable of that name is in scope (then it
      calls nothing the graph holds).

    Neither the file's short-name table nor a run-wide lookup of the callee's
    name is a reason to bind a call any more: a name looked up among every
    declaration was wrong about half the time when the name had a second
    holder (ADR-0064, the sample). ``new T()`` (R10 to R12) is unchanged.

    Args:
        using_aliases: Local name -> dotted path, the ``new T()`` lookup's path hint.
        field_type_registry: Aggregated class→{field→type} map for chained field resolution.
    """
    # Every declaration of this file, by position (INV-midag): ``local_symbols``
    # keeps ONE symbol per name.
    decl_index = symbols_at(
        file_symbols if file_symbols is not None
        else list({s.id: s for s in local_symbols.values()}.values()))
    if resolver is None:  # pragma: no cover - defensive
        resolver = NameResolver(global_symbols)
    if using_aliases is None:  # pragma: no cover - defensive default
        using_aliases = {}
    if global_ctors is None:  # pragma: no cover - defensive default
        global_ctors = {}
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):  # pragma: no cover - IO errors hard to trigger in tests
        return []

    _caller_path = str(file_path)
    edges: list[Edge] = []
    file_id = make_file_id("csharp", str(file_path))
    file_anchor = file_anchor_symbol("csharp", str(file_path), PASS_ID, run.execution_id)
    current_function: Optional[Symbol]
    # Variable types for type inference: var_name -> class_name. A local or a
    # parameter closes with the member, local function or lambda that binds it
    # (``ScopedVarTypes``, INV-zabud); a field is bound for the file.
    var_types = ScopedVarTypes()
    # Every local name declared so far in the open functions, typed or not
    # (``_declared_local_names``): a receiver or a bare callee of that name is
    # a variable, never a type or a member.
    declared_locals = ScopedVarTypes()

    def get_callee_name(node: "tree_sitter.Node") -> Optional[str]:
        """The name of the method an invocation calls: the member's name of
        ``x.M()`` (``M`` of ``x.M<T>()`` too), the identifier of ``M()``;
        None for any other callee (``x?.M()``, ``M<T>()``)."""
        function = node.child_by_field_name("function")
        if function is not None and function.type == "member_access_expression":
            name = function.child_by_field_name("name")
            if name is not None and name.type == "generic_name":
                name = find_child_by_type(name, "identifier")
            return node_text(name, source) if name is not None else None
        if function is not None and function.type == "identifier":
            return node_text(function, source)
        return None

    def bind(
        caller: Symbol, call: "tree_sitter.Node", callee: Symbol,
        mark: str, meta: dict[str, object],
    ) -> None:
        """A resolved call edge, and the type its result gives a variable."""
        edges.append(Edge.create(
            src=caller.id,
            dst=callee.id,
            edge_type="calls",
            line=call.start_point[0] + 1,
            evidence_type="ast_call",
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            meta=meta,
            resolved_by=mark,
        ))
        _track_csharp_return_type(callee, call, source, var_types)

    def file_target(call: "tree_sitter.Node") -> Optional[Symbol]:
        """The declaration of this file a call reaches on C#'s own evidence:
        a bare / ``this.`` call to the enclosing type's method, or a call on a
        receiver of known type to that type's method. (``base.M()`` never
        reaches the enclosing type's own ``M``.)"""
        callee = get_callee_name(call)
        function = call.child_by_field_name("function")
        receiver = (
            function.child_by_field_name("expression")
            if function is not None and function.type == "member_access_expression"
            else None
        )
        if callee is None or (receiver is not None and receiver.type == "base"):
            return None
        if receiver is None or receiver.type == "this":
            enclosing = _get_enclosing_class(call, source)
            return local_symbols.get(f"{enclosing}.{callee}") if enclosing else None
        owner = receiver_type(receiver)
        return local_symbols.get(f"{owner}.{callee}") if owner else None

    def receiver_type(receiver: "tree_sitter.Node") -> Optional[str]:
        """The receiver's type as the source writes it, when this file knows
        it: a typed variable, parameter or field (``var_types``), ``this._f``,
        ``new T(...)``, or a call this file resolves whose declared return
        type is a type name (``GetDb().Execute()``)."""
        if receiver.type == "identifier":
            return var_types.get(node_text(receiver, source))
        if receiver.type == "member_access_expression":
            owner = receiver.child_by_field_name("expression")
            name = receiver.child_by_field_name("name")
            if owner is not None and owner.type == "this" and name is not None:
                return var_types.get(node_text(name, source))
            return None
        if receiver.type == "object_creation_expression":
            created = receiver.child_by_field_name("type")
            return node_text(created, source) if created is not None else None
        if receiver.type == "invocation_expression":
            called = file_target(receiver)
            if called is not None and called.kind == "method":
                return _extract_csharp_return_type_name(called.signature)
        return None

    for node in iter_tree(tree.root_node):
        var_types.close_scopes_before(node)
        declared_locals.close_scopes_before(node)
        if node.type in _CSHARP_VARIABLE_SCOPE_NODES:
            var_types.open_scope(node)
            declared_locals.open_scope(node)
        if declared_locals.scope_end is not None:
            for local_name in _declared_local_names(node, source):
                declared_locals[local_name] = ""
        # Using directive
        if node.type == "using_directive":
            # Get the namespace being imported
            name_node = find_child_by_type(node, "identifier")
            if not name_node:
                name_node = find_child_by_type(node, "qualified_name")
            if name_node:
                import_path = node_text(name_node, source)
                edges.append(Edge.create(
                    src=file_id,
                    dst=f"csharp:{import_path}:0-0:namespace:namespace",
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    evidence_type="using_directive",
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                ))

        # Member declarations - extract parameter types for type inference
        # (a method's, a constructor's, an operator's or an indexer's)
        elif node.type in _CSHARP_MEMBER_BODY_NODES:
            param_types = _extract_param_types(node, source)
            # Parameter types, for method call resolution inside the member.
            for param_name, param_type in param_types.items():
                var_types[param_name] = param_type

        # Field declarations - track field types: private readonly IService _svc;
        elif node.type == "field_declaration":
            var_decl = find_child_by_type(node, "variable_declaration")
            if var_decl:
                # Type can be identifier (simple) or generic_name (generic)
                type_name = None
                type_id = find_child_by_type(var_decl, "identifier")
                if type_id:
                    type_name = node_text(type_id, source)
                else:
                    gen_name = find_child_by_type(var_decl, "generic_name")
                    if gen_name:
                        gen_id = find_child_by_type(gen_name, "identifier")
                        if gen_id:
                            type_name = node_text(gen_id, source)
                var_declarator = find_child_by_type(
                    var_decl, "variable_declarator"
                )
                if type_name and var_declarator:
                    name_node = find_child_by_type(
                        var_declarator, "identifier"
                    )
                    if name_node:
                        field_name = node_text(name_node, source)
                        var_types[field_name] = type_name

        # Invocation expression (method call)
        elif node.type == "invocation_expression":
            # INV-bamij: a call in no method is anchored on its type, else file.
            current_function = (
                _enclosing_member(node, decl_index)
                or enclosing_declared_symbol(node, decl_index, _CSHARP_TYPE_BODY_NODES)
                or file_anchor
            )
            callee_name = get_callee_name(node)
            if callee_name is None:
                continue
            line = node.start_point[0] + 1
            enclosing_cls = _get_enclosing_class(node, source)
            function = node.child_by_field_name("function")
            receiver = (
                function.child_by_field_name("expression")
                if function is not None and function.type == "member_access_expression"
                else None
            )

            # A bare call, ``this.M()``, ``base.M()``: the enclosing type's own
            # method in this file; else the linker walks the type's ancestors
            # from the caller's own type (ADR-0029). A local function or a
            # local variable of that name hides every member: the call reaches
            # nothing the graph holds. ``base.M()`` calls the BASE type's M,
            # never the enclosing type's own (the override it is usually
            # written in): where the own type declares M no fact the shared
            # pass reads says "start at the parent", so that call stays
            # unresolved; where it does not, the walk from the own type is the
            # walk from its base.
            if receiver is None or receiver.type in ("this", "base"):
                hidden = receiver is None and (
                    callee_name in declared_locals
                    or _local_function_in_scope(node, callee_name, source)
                )
                own = (
                    local_symbols.get(f"{enclosing_cls}.{callee_name}")
                    if enclosing_cls and not hidden else None
                )
                if own is not None and receiver is not None and receiver.type == "base":
                    hidden = True
                    own = None
                if own is not None:
                    bind(current_function, node, own, resolved_by.OWN_TYPE,
                         {"call_construct": "method"})
                else:
                    edges.append(make_unresolved_edge(
                        "csharp", current_function.id, callee_name, line,
                        PASS_ID, run.execution_id,
                        enclosing_class=None if hidden else enclosing_cls,
                    ))
                continue

            # An explicit receiver. The module slot keeps its old spelling: a
            # receiver identifier (``this._f``'s field) when it names a type.
            receiver_name: Optional[str] = None
            if receiver.type == "identifier":
                receiver_name = node_text(receiver, source)
            elif receiver.type == "member_access_expression":
                owner = receiver.child_by_field_name("expression")
                field_name = receiver.child_by_field_name("name")
                if owner is not None and owner.type == "this" and field_name is not None:
                    receiver_name = node_text(field_name, source)
            unresolved_module = _csharp_unresolved_module(
                receiver_name, var_types, local_symbols, enclosing_cls,
            )

            # The receiver's type: a field chain from ``this`` (R1), else what
            # this file knows of the receiver (R3).
            known_type: Optional[str] = None
            meta: dict[str, object] = {"call_construct": "method"}
            if receiver.type == "member_access_expression" and field_type_registry:
                known_type = _resolve_member_chain(
                    receiver, source, field_type_registry, enclosing_cls,
                )
                if known_type is not None:
                    meta["receiver"] = "field_chain"
            if known_type is None:
                known_type = receiver_type(receiver)
                meta["resolution_quality"] = "type_inferred"
            if known_type is not None:
                target = local_symbols.get(f"{known_type}.{callee_name}")
                if target is not None:
                    bind(current_function, node, target, resolved_by.RECEIVER_TYPE, meta)
                else:
                    edges.append(make_unresolved_edge(
                        "csharp", current_function.id, callee_name, line,
                        PASS_ID, run.execution_id,
                        module_hint=unresolved_module,
                        receiver_type_hint=_strip_type_arguments(known_type),
                        call_construct="method",
                    ))
                continue

            # No type known. A receiver that can name a type or a namespace
            # path is handed over as written; the linker binds it only if a
            # type of that name is visible from the caller. A name this file
            # knows as a variable, or as a member of the enclosing type, is
            # not a type: the call stays unresolved with no owner.
            qualifier = _dotted_receiver(receiver, source)
            if qualifier is not None:
                head = qualifier.split(".", 1)[0]
                if (
                    head in var_types or head in declared_locals
                    or f"{enclosing_cls}.{head}" in local_symbols
                ):
                    qualifier = None
            edges.append(make_unresolved_edge(
                "csharp", current_function.id, callee_name, line,
                PASS_ID, run.execution_id,
                module_hint=unresolved_module,
                call_construct="method",
                qualifier=qualifier,
            ))

        # Object creation expression (new ClassName())
        elif node.type == "object_creation_expression":
            current_function = _enclosing_member(node, decl_index)
            type_node = find_child_by_type(node, "identifier")
            type_name = node_text(type_node, source) if type_node else None

            if current_function is not None and type_name:
                # WI-fagit: `new X(...)` invokes the CONSTRUCTOR, so the
                # 'instantiates' edge must land on the ctor node — not the class
                # node, which conflates 'referenced as a type' with
                # 'instantiated' and leaves a reverse-slice on the ctor
                # ('who constructs this?') with zero callers. Prefer the
                # `Type.Type` constructor symbol (same-file map first, then the
                # global registry for cross-file); C# forbids a non-ctor member
                # named the same as its enclosing type (CS0542), so this key
                # only ever resolves to a constructor. Fall back to the class
                # node when the type declares no explicit constructor (the
                # implicit default ctor has no node to anchor on).
                # WI-mivav: global_symbols/local_symbols are keyed by name and
                # last-write-wins, so overloaded constructors collapse to one
                # anchor. When several ctors share this key, prefer the overload
                # whose parameter count matches this `new X(...)`'s argument
                # count; fall back to the collapsed pick on a tie/miss (this is a
                # heuristic, not full C# overload resolution).
                ctor_key = f"{type_name}.{type_name}"
                ctor = local_symbols.get(ctor_key) or global_symbols.get(ctor_key)
                # The file's table first, else the run-wide one (last writer).
                _by = (
                    resolved_by.SAME_FILE if ctor_key in local_symbols
                    else resolved_by.NAME
                )
                overloads = global_ctors.get(ctor_key)
                if overloads and len(overloads) > 1:
                    arg_list = find_child_by_type(node, "argument_list")
                    arg_count = (
                        sum(1 for c in arg_list.children if c.type == "argument")
                        if arg_list is not None else 0
                    )
                    matches = [
                        c for c in overloads
                        if _count_signature_params(c.signature) == arg_count
                    ]
                    if len(matches) == 1:
                        ctor = matches[0]
                        # By arity among every same-keyed ctor of the run.
                        _by = resolved_by.NAME
                    elif ctor is None:  # pragma: no cover - global_symbols has the key
                        ctor = overloads[0]
                if ctor is not None:
                    edges.append(Edge.create(
                        src=current_function.id,
                        dst=ctor.id,
                        edge_type="instantiates",
                        line=node.start_point[0] + 1,
                        evidence_type="ast_call",
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        meta={"call_construct": "constructor"},
                        resolved_by=_by,
                    ))
                elif type_name in local_symbols:
                    target = local_symbols[type_name]
                    edges.append(Edge.create(
                        src=current_function.id,
                        dst=target.id,
                        edge_type="instantiates",
                        line=node.start_point[0] + 1,
                        evidence_type="ast_call",
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        meta={"call_construct": "constructor"},
                        resolved_by=resolved_by.SAME_FILE,
                    ))
                else:
                    # Use import path for disambiguation
                    import_hint = using_aliases.get(type_name)
                    lookup_result = resolver.lookup(type_name, path_hint=import_hint, caller_path=_caller_path)
                    if lookup_result.found and lookup_result.symbol is not None:
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=lookup_result.symbol.id,
                            edge_type="instantiates",
                            line=node.start_point[0] + 1,
                            evidence_type="ast_call",
                            resolution_score=lookup_result.confidence,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            meta={"call_construct": "constructor"},
                            resolved_by=lookup_result.resolved_by,
                        ))

            # Track variable type for type inference
            # C#: var db = new Database() or Database db = new Database()
            if type_name and node.parent:
                parent = node.parent
                # Check for variable_declarator (var x = new Class())
                if parent.type == "variable_declarator":
                    var_name_node = find_child_by_type(parent, "identifier")
                    if var_name_node:
                        var_name = node_text(var_name_node, source)
                        var_types[var_name] = type_name
                # Check for equals_value_clause in a variable_declaration
                # (alternative AST pattern - defensive code)
                elif parent.type == "equals_value_clause":  # pragma: no cover - alt AST pattern
                    grandparent = parent.parent
                    if grandparent and grandparent.type == "variable_declarator":
                        var_name_node = find_child_by_type(grandparent, "identifier")
                        if var_name_node:
                            var_name = node_text(var_name_node, source)
                            var_types[var_name] = type_name

        # Method group references: bare identifier in argument or assignment
        # that resolves to a function/method symbol.
        # Pattern 1: argument containing only an identifier — items.ForEach(Process)
        elif node.type == "argument":
            id_node = find_child_by_type(node, "identifier")
            if id_node and len(node.named_children) == 1:
                ref_name = node_text(id_node, source)
                target = local_symbols.get(ref_name)
                if target is None and resolver is not None:
                    lookup = resolver.lookup(ref_name, caller_path=_caller_path)
                    if lookup.found and lookup.symbol is not None:
                        target = lookup.symbol
                if target is not None and target.kind in ("function", "method"):
                    current_function = _enclosing_member(node, decl_index)
                    if current_function is not None and target.id != current_function.id:
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=target.id,
                            edge_type="references",
                            line=node.start_point[0] + 1,
                            evidence_type="ast_call",
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            meta={"ref_construct": "method_group"},
                        ))

        # Pattern 2: variable_declarator with RHS identifier — Action handler = Handle
        elif node.type == "variable_declarator":
            children = node.children
            eq_idx = next(
                (i for i, c in enumerate(children) if c.type == "="), -1,
            )
            if eq_idx >= 0 and eq_idx + 1 < len(children):
                rhs = children[eq_idx + 1]
                if rhs.type == "identifier":
                    ref_name = node_text(rhs, source)
                    target = local_symbols.get(ref_name)
                    if target is None and resolver is not None:
                        lookup = resolver.lookup(ref_name, caller_path=_caller_path)
                        if lookup.found and lookup.symbol is not None:
                            target = lookup.symbol
                    if target is not None and target.kind in ("function", "method"):
                        current_function = _enclosing_member(node, decl_index)
                        if (
                            current_function is not None
                            and target.id != current_function.id
                        ):
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=target.id,
                                edge_type="references",
                                line=node.start_point[0] + 1,
                                evidence_type="ast_call",
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                meta={"ref_construct": "method_group"},
                            ))

    return edges


# Standard .NET/C# attributes that carry no architectural information.
# Suppressed from unresolved decorated_by edges to avoid dangling edges (WI-divob).
_STANDARD_CSHARP_ATTRIBUTES = frozenset({
    # System
    "Obsolete", "Serializable", "NonSerialized", "Flags",
    "CLSCompliant", "ThreadStatic", "MTAThread", "STAThread",
    # System.Diagnostics
    "Conditional", "DebuggerDisplay", "DebuggerStepThrough",
    "DebuggerHidden", "DebuggerBrowsable",
    # System.Runtime
    "MethodImpl", "DllImport", "StructLayout", "MarshalAs",
    # System.ComponentModel
    "Description", "Category", "Browsable", "EditorBrowsable",
    "DefaultValue",
    # Testing
    "Test", "TestFixture", "SetUp", "TearDown", "TestCase",
    "Fact", "Theory", "InlineData", "ClassData",
    # Code analysis
    "SuppressMessage", "ExcludeFromCodeCoverage",
    # Compiler
    "CallerMemberName", "CallerFilePath", "CallerLineNumber",
    "CompilerGenerated",
})


def _extract_attribute_edges(
    symbols: list[Symbol],
    global_symbols: dict[str, Symbol],
    run: AnalysisRun,
) -> list[Edge]:
    """Extract decorated_by edges from C# attribute metadata.

    For each symbol (class, interface, method) with annotations metadata,
    creates decorated_by edges to attribute types that exist in the
    analyzed codebase. This enables visibility of attribute patterns
    like ASP.NET Core's [ApiController], [HttpGet], [Service], etc.

    Args:
        symbols: All extracted symbols
        global_symbols: Map of name -> Symbol for attribute lookup
        run: Current analysis run for provenance

    Returns:
        List of decorated_by edges for attribute relationships
    """
    edges: list[Edge] = []

    for sym in symbols:
        if sym.meta is None:
            continue

        # C# uses "annotations" instead of "decorators" for historical reasons
        annotations = sym.meta.get("annotations")
        if not annotations or not isinstance(annotations, list):
            continue

        for annotation in annotations:
            if not isinstance(annotation, dict):  # pragma: no cover
                continue

            attr_name = annotation.get("name")
            if not attr_name or not isinstance(attr_name, str):  # pragma: no cover
                continue

            # Try to resolve the attribute to a symbol
            # C# attributes may be referenced with or without "Attribute" suffix
            attr_sym = global_symbols.get(attr_name)
            if not attr_sym:
                attr_sym = global_symbols.get(f"{attr_name}Attribute")

            line = sym.span.start_line if sym.span else 0

            if attr_sym:
                edge = Edge.create(
                    src=sym.id,
                    dst=attr_sym.id,
                    edge_type="decorated_by",
                    line=line,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_attribute",
                )
                edges.append(edge)
            elif attr_name not in _STANDARD_CSHARP_ATTRIBUTES:
                # Only emit unresolved edges for non-standard attributes
                # (likely framework attributes like [ApiController]).
                # Standard attributes ([Obsolete], [Test], etc.) carry no
                # architectural information and would create dangling edges.
                dst_id = f"csharp:unresolved:0-0:{attr_name}:unresolved"
                edge = Edge.create(
                    src=sym.id,
                    dst=dst_id,
                    edge_type="decorated_by",
                    line=line,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_attribute",
                    is_resolved=False,
                )
                edges.append(edge)

    return edges


def _stamp_shape_ids(
    analyzer: TreeSitterAnalyzer, analysis: "_BaseFileAnalysis",
) -> None:
    """Fill ``shape_id`` from ``node_for_symbol`` for the C# ``analyze()``
    override (WI-lutob).

    The base ``TreeSitterAnalyzer.analyze()`` auto-stamps ``shape_id`` (and
    docstrings) from ``analysis.node_for_symbol`` after extraction, but
    ``CSharpAnalyzer.analyze()`` reimplements the pipeline and never runs that
    loop — without this every body-bearing C# symbol would have
    ``shape_id=None`` and no structural-clone key (spec §6 shape_id). This replays just the shape_id half over the C# analysis's
    already-populated ``node_for_symbol`` (docstrings are handled in the C#
    extraction path). Never clobbers an existing value.
    """
    if not analysis.node_for_symbol:
        return
    sym_by_id: dict[str, Symbol] = {s.id: s for s in analysis.symbols}
    for sym_id, ts_node in analysis.node_for_symbol.items():
        sym = sym_by_id.get(sym_id)
        if sym is not None and sym.shape_id is None:
            sym.shape_id = analyzer.compute_shape_id(ts_node)


class CSharpAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based C# analyzer.

    Uses tree-sitter-c-sharp to parse C# files and extract classes, interfaces,
    structs, enums, methods, constructors, properties, call edges, import edges,
    instantiation edges, and attribute (decorated_by) edges.

    Overrides ``analyze`` for two reasons:
    1. ``_extract_symbols_from_file`` does its own file reading/parsing
    2. ``_extract_attribute_edges`` post-processing requires all symbols+global_symbols
    """

    lang = "csharp"
    file_patterns: ClassVar[list[str]] = ["*.cs"]
    grammar_module = "tree_sitter_c_sharp"

    def register_symbol(
        self,
        symbol: Symbol,
        global_symbols: dict,
    ) -> None:
        """Register symbol by qualified name only.

        The ``NameResolver`` suffix index handles short-name lookups.
        """
        global_symbols[symbol.name] = symbol

    def analyze(
        self,
        repo_root: Path,
        max_files: Optional[int] = None,
    ) -> AnalysisResult:
        """Run C# analysis with attribute edge post-processing.

        The C# analyzer uses ``_extract_symbols_from_file`` which reads files
        internally, and adds an ``_extract_attribute_edges`` post-processing step.
        """
        import time as _time
        import warnings as _warnings

        start_time = _time.time()
        run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

        if not self._check_grammar_available():
            _warnings.warn(
                f"{self.lang} analysis skipped: grammar not available. "
                f"Install the required tree-sitter grammar package.",
                UserWarning,
                stacklevel=2,
            )
            run.duration_ms = int((_time.time() - start_time) * 1000)
            return AnalysisResult(
                run=run,
                skipped=True,
                skip_reason=f"{self.lang} tree-sitter grammar not available",
                skip_reason_code=DEPENDENCY_UNAVAILABLE,
            )

        parser = self._create_parser()

        # Pass 1: Extract all symbols
        file_analyses: dict[Path, FileAnalysis] = {}
        # What each file's ``using`` directives say; its table (ADR-0064,
        # piece b) is handed over once its project's usings are added.
        file_usings: dict[Path, _CSharpUsings] = {}
        files_skipped = 0

        for cs_file in find_csharp_files(repo_root):
            if max_files is not None and len(file_analyses) >= max_files:
                break  # pragma: no cover

            # Compute the repo-relative path here (where repo_root is in scope)
            # and thread it down (WI-bokab, v7). ``cs_file`` from
            # find_csharp_files(repo_root) is ABSOLUTE; folding it would make
            # stable_ids location-dependent (the absolute-path regression trap).
            rel_path = str(cs_file.relative_to(repo_root))
            analysis, usings = _extract_symbols_from_file(cs_file, parser, run, rel_path)
            if usings is not None:
                file_usings[cs_file] = usings
            if analysis.symbols:
                # WI-lutob: this override bypasses the base analyze()'s shape_id
                # auto-stamp loop; stamp it here from node_for_symbol.
                _stamp_shape_ids(self, analysis)
                file_analyses[cs_file] = analysis
            else:
                files_skipped += 1

        # The usings in scope in each file, by the path ``Symbol.path``
        # spells; every file read gets a table, an empty one too: its presence
        # says this analyzer hands its calls over.
        _csharp_project_usings(repo_root, file_usings, run)
        import_scopes = {str(f): u.scope for f, u in file_usings.items()}

        # Build global symbol registry
        global_symbols: dict[str, Symbol] = {}
        # WI-mivav: multi-value ctor registry (global_symbols collapses
        # overloads); keyed by the `Type.Type` name so the object-creation site
        # can pick the arity-matching overload.
        global_ctors: dict[str, list[Symbol]] = {}
        for analysis in file_analyses.values():
            for symbol in analysis.symbols:
                self.register_symbol(symbol, global_symbols)
                if symbol.kind == "constructor":
                    global_ctors.setdefault(symbol.name, []).append(symbol)

        # Aggregate field type registry for chained this.field.field.Method()
        field_type_registry: dict[str, dict[str, str]] = {}
        for analysis in file_analyses.values():
            for class_name, fields in analysis.class_field_types.items():
                if class_name not in field_type_registry:
                    field_type_registry[class_name] = {}
                for fname, ftype in fields.items():
                    field_type_registry[class_name].setdefault(fname, ftype)

        # Pass 2: Extract edges
        resolver = NameResolver(global_symbols)
        all_symbols: list[Symbol] = []
        all_edges: list[Edge] = []

        for cs_file, analysis in file_analyses.items():
            all_symbols.extend(analysis.symbols)

            edges = _extract_edges_from_file(
                cs_file, parser, analysis.symbol_by_name, global_symbols, run, resolver,
                using_aliases=analysis.import_aliases,
                field_type_registry=field_type_registry,
                global_ctors=global_ctors,
                file_symbols=analysis.symbols,
            )
            # ADR-0015 Tier 1: annotate edges with dataflow access modes
            try:
                _cs_source = cs_file.read_bytes()
                _cs_tree = parser.parse(_cs_source)
                _cs_df = _get_dataflow_config("csharp")
                if _cs_df is not None:
                    edges = _annotate_dataflow(edges, _cs_tree, _cs_source, _cs_df)
            except (OSError, IOError):  # pragma: no cover
                pass
            all_edges.extend(edges)

        # Extract attribute edges (INV-012: annotations metadata -> decorated_by edges)
        attribute_edges = _extract_attribute_edges(all_symbols, global_symbols, run)
        all_edges.extend(attribute_edges)

        run.files_analyzed = len(file_analyses)
        run.files_skipped = files_skipped
        run.duration_ms = int((_time.time() - start_time) * 1000)

        return AnalysisResult(
            symbols=all_symbols,
            edges=all_edges,
            run=run,
            import_scopes=import_scopes,
        )


_analyzer = CSharpAnalyzer()


def is_csharp_tree_sitter_available() -> bool:
    """Check if tree-sitter with C# grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("csharp")
def analyze_csharp(repo_root: Path) -> AnalysisResult:
    """Analyze all C# files in a repository.

    Returns a AnalysisResult with symbols, edges, and provenance.
    If tree-sitter-c-sharp is not available, returns a skipped result.
    """
    return _analyzer.analyze(repo_root)
