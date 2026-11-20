# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dart/Flutter analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse Dart files and extract:
- Class declarations (including abstract classes)
- Function declarations (top-level and methods)
- Constructor declarations (named and unnamed)
- Getter and setter declarations
- Enum declarations
- Mixin declarations
- Extension declarations
- Field declarations (class/mixin/extension/enum-body values; WI-jusus)
- Top-level variable declarations (module constants/state; WI-jusus)
- Import and export statements
- Function call relationships
- Inheritance edges from the ``extends`` / ``implements`` / ``with`` / mixin
  ``on`` clauses of classes, mixins and enums (WI-lahub)

If tree-sitter with Dart support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract classes, functions, methods, constructors, getters, setters, enums, mixins, extensions, fields, top-level variables;
   record each type's heritage clauses
2. Pass 2: Detect calls, imports, and instantiations using NameResolver
3. post_process: resolve the heritage clauses over every class and mixin of the
   run into ``extends`` / ``implements`` / ``includes`` edges. The analyzer
   labels these itself rather than leaving them to the shared inheritance
   linker: Dart has no interface kind, so only the clause separates
   ``extends`` from ``implements`` (see the "Inheritance" block below).

Identity (WI-tusad): functions and methods hash their normalized signature
(``make_typed_stable_id``). Constructors, getters, setters, mixins and
extensions record no signature; they take the same formula with an empty one,
over the owner-qualified name (``Mod.Mod.named``). Before, they computed none
and rode the backstop floor key (kind, file, name). A function declared by a
bare ``function_signature`` (top level, or local to another function's body)
hashes its full scope chain (``_dart_scope``; INV-fabih), so a local function
in two functions no longer ties; the top-level ones keep their values.

The hashed signature text writes every type. A function's return type is its
source text, whatever node kind spells it (``List<int>``, ``int?``,
``p.Foo``, ``int Function(int)``, ``(int, String)``, ``Function``); before,
it was the first ``type_identifier`` only, so ``List<int> f()`` and
``List<String> f()`` hashed one text. A field's type is the whole run of type
nodes before its name (it was a lone ``type_identifier``; ``String?``,
``List<T>``, function and record types hashed the empty text).

The hashed text holds types and the names a caller writes, and is read
from the syntax tree (``_dart_hashed_signature``), one parameter at a time:
each parameter's annotations, modifiers and type, with every positional
parameter name in it left out
(its own; an untyped ``x`` reads ``dynamic``; the names inside a function
type, ``void Function(int n)`` -> ``void Function(int)``, at any depth and
in a return or field type too); an old-style function-typed parameter as the
function type it declares (``int g(int a)`` -> ``int Function(int)``); a
named or optional group as its bracket, its parameters and its closing
bracket; a default value left out, value and presence. The parameters are
joined by ``,`` and the return type follows. A type's library prefix is
stripped as the types-first family's shared rule strips it, except where
that strip would cut at a dot inside a function or record type
(``int Function(p.A)`` -> ``A)``) or take a modifier with the prefix
(``required p.Foo`` -> ``Foo``): there the type is written as is, behind its
modifiers. Until scheme v16 the text was the displayed signature put through
the shared rule, which drops each comma-separated piece's last word: in a
``[int c, String? d]`` group it kept ``c`` and lost the ``]`` (``[`` is a
bracket to the split, so the group was one piece), a named group lost its
``}``, function-type parameter names and an untyped parameter's name were
hashed, and a bracket inside an annotation's string (``@A("x)")``)
unbalanced the split. A default value is still DISPLAYED, written ``...``
whole (``_dart_group_text`` reads it from the tree).

A NAMED parameter is hashed with its name, as ``name:Type`` (stable-id v17):
``void f({required int count})`` -> ``({count:required int})``, and inside
a function type ``void Function({int z})`` -> ``void Function({z:int})``.
A caller writes that name (``f(count: 1)``), so renaming it changes the API,
as renaming a Swift argument label does, and both go through the shared
``labelled_parameter`` so they are spelled alike. Until v17 the names were
left out, and ``f({int a})`` / ``f({int b})`` shared an id. Left as they
were: modifiers that are not types (``final``, ``covariant``, ``const``) and
annotations stay in the text, and a record type's positional field names
too. A constructor hashes no parameter at all (see "Identity" above).

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Dart-specific
extraction logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for Dart grammar
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency

Dart-Specific Considerations
----------------------------
- Dart has classes, mixins, extensions, and enums
- Methods are always inside class/mixin/extension bodies
- Constructors can be named (User.guest) or unnamed (User)
- Import/export statements have various forms:
  - import 'dart:io';
  - import 'package:flutter/material.dart';
  - import 'local.dart';
  - export 'src/models.dart';
  - import 'package:foo/foo.dart' show Bar;

AST Structure Notes
-------------------
- method_signature contains function_signature (for regular methods)
  or getter_signature/setter_signature (for accessors)
- function_signature and function_body are siblings at the same level
- Imports: import_or_export > library_import > import_specification >
  configurable_uri > uri > string_literal
- Function calls: expression_statement containing identifier + selector
  with argument_part > arguments

IMPORTED TYPE NAMES (stable-id scheme v17, WI-palus). Every type a typed
stable id hashes is written through the file's prefixed imports and ``show`` lists
(``_dart_type_names``, read once per file and set with ``resolving_type_names``), so an
imported and a qualified spelling of one type hash alike; a name nothing
binds, or the file declares, is hashed as written.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterable, Iterator, Optional

from hypergumbo_core.ir import Edge, ExternalRef, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    LABEL_SEPARATOR,
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    ancestor_labels,
    defer_bare_method_call,
    defer_unresolved_tie,
    find_child_by_type,
    iter_tree,
    labelled_parameter,
    make_file_id,
    make_file_stable_id,
    make_symbol_id,
    make_typed_stable_id,
    make_unresolved_edge,
    make_variable_stable_id,
    node_text,
    symbol_declared_by,
    symbols_at,
    visibility_from_modifiers,
    declares_async,
    FileTypeNames,
    canonical_type_names,
    file_type_names,
    normalize_type,
    resolving_type_names,
)
from hypergumbo_core.paths import normalize_path
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity

from hypergumbo_core.symbol_indexes import SymbolByName
from hypergumbo_core.symbol_resolution import ListNameResolver

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun, UsageContext
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("dart")

#: Kinds that record no signature yet mint the producer stable_id over their
#: owner-qualified name (WI-tusad). Classes and enums take the backstop's
#: table formula.
_UNSIGNED_PRODUCER_KINDS = frozenset({
    "constructor", "getter", "setter", "mixin", "extension",
})


def find_dart_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Dart files in the repository.

    The analyzer's own discovery (``_find_source_files``), so the two cannot drift.
    """
    yield from _analyzer._find_source_files(repo_root)


def _find_next_sibling_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find next sibling of given type."""
    current = node.next_sibling
    while current:
        if current.type == type_name:
            return current
        current = current.next_sibling
    return None


def _get_combined_span(
    start_node: "tree_sitter.Node",
    end_node: Optional["tree_sitter.Node"],
) -> tuple[int, int, int, int]:
    """Get span covering from start_node to end_node (or just start_node if no end)."""
    start_line = start_node.start_point[0] + 1
    start_col = start_node.start_point[1]
    if end_node:
        end_line = end_node.end_point[0] + 1
        end_col = end_node.end_point[1]
    else:
        end_line = start_node.end_point[0] + 1
        end_col = start_node.end_point[1]
    return start_line, end_line, start_col, end_col


#: Comment nodes the grammar may place between the parts of a declaration.
_DART_COMMENT_NODE_TYPES = frozenset({"comment", "documentation_comment"})

#: The node kinds that spell a type where the grammar writes it as a run of
#: siblings rather than one node: a field's or variable's type, before its
#: value list (``p.Foo<int>?`` is ``type_identifier`` ``.`` ``type_identifier``
#: ``type_arguments`` ``nullable_type``; a function type and a record type are
#: one node each).
_DART_TYPE_PART_NODE_TYPES = frozenset({
    "type_identifier", ".", "type_arguments", "nullable_type",
    "function_type", "record_type",
})


def _dart_type_text(
    parts: list["tree_sitter.Node"], source: bytes, *, hashed: bool = False,
) -> Optional[str]:
    """The type spelled by a run of sibling nodes, comments left out, or None.

    The parts are joined without a space: in Dart they are adjacent tokens
    (``p`` ``.`` ``Foo`` ``<int>`` ``?``), and a function or record type is a
    single part written as in the source. ``hashed``: each part as the hash
    reads it, its function types' parameter names left out
    (:func:`_dart_types_text`).
    """
    texts = [
        _dart_types_text([n], source) if hashed else node_text(n, source)
        for n in parts if n.type not in _DART_COMMENT_NODE_TYPES
    ]
    return "".join(texts) if texts else None


def _dart_return_type(
    func_sig_node: "tree_sitter.Node", source: bytes, *, hashed: bool = False,
) -> Optional[str]:
    """The declared return type of a ``function_signature``, as written
    (``hashed``: as the hash reads it, :func:`_dart_type_text`).

    Every child before the ``name`` field is part of it, whatever node kind
    spells it: a plain or prefixed name, type arguments, ``?``, a function
    type, a record type, ``void``. None when no return type is declared.
    """
    name = func_sig_node.child_by_field_name("name")
    if name is None:  # pragma: no cover - the grammar names every function_signature
        return None
    return _dart_type_text(
        [c for c in func_sig_node.children if c.end_byte <= name.start_byte], source,
        hashed=hashed,
    )


def _extract_dart_signature(
    func_sig_node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a Dart function_signature node.

    Dart function signatures look like:
        int add(int x, int y)
        String greet(String name, {bool loud = false})
        void main()

    Returns signature like "(int x, int y) int" or "(String name, {bool loud = ...}) String".
    Each parameter is its source text, a default value written ``...``
    whole (:func:`_dart_group_text`); the return type is its source text
    whatever node kind spells it (``List<int>?``, ``p.Foo``, ``int
    Function(int)``, ``(int, String)``), and ``void`` is omitted.
    """
    params: list[str] = []
    return_type = _dart_return_type(func_sig_node, source)

    for child in func_sig_node.children:
        if child.type == "formal_parameter_list":
            # Extract parameters
            for param_child in child.children:
                if param_child.type == "formal_parameter":
                    param_text = node_text(param_child, source).strip()
                    if param_text:
                        params.append(param_text)
                elif param_child.type == "optional_formal_parameters":
                    params.append(_dart_group_text(param_child, source))

    sig = "(" + ", ".join(params) + ")"
    if return_type and return_type != "void":
        sig += f" {return_type}"
    return sig


#: The tokens that may follow a default value in a parameter group.
_DART_GROUP_SEPARATORS = frozenset({",", "}", "]"})


def _dart_group_text(group: "tree_sitter.Node", source: bytes) -> str:
    """A named or optional parameter group's source text, each default value
    written ``...`` whole.

    The grammar puts a default in the group, after its ``formal_parameter``:
    an ``=`` (or Dart 2's ``:``) and the expression. Everything from the end
    of the parameter to the next ``,``, ``}`` or ``]`` of the group is written
    `` = ...`` (`` : ...``), so the default is replaced however many commas,
    brackets or strings it holds, and the rest of the group is kept as
    written. Until WI-doboh a regex replaced the default only up to its first
    ``,``, ``}`` or ``]``: ``{Size s = const Size(1, 2)}`` was written
    ``{Size s = ..., 2)}``. A default with none of those characters is written
    as the regex wrote it.
    """
    children = group.children
    out: list[bytes] = []
    pos = group.start_byte
    for index, child in enumerate(children):
        if child.type not in ("=", ":") or index == 0 or pos > child.start_byte:
            continue
        end = next((c.start_byte for c in children[index + 1:] if c.type in _DART_GROUP_SEPARATORS),
                   group.end_byte)
        out += [source[pos:children[index - 1].end_byte], b" = ..." if child.type == "=" else b": ..."]
        pos = end
    out.append(source[pos:group.end_byte])
    return b"".join(out).decode("utf-8", errors="replace").strip()


#: Words that may stand before a parameter's type (``required``, ``covariant``
#: and ``final`` in Dart; ``const`` and ``late`` are accepted by the grammar).
_DART_PARAMETER_MODIFIERS: frozenset[str] = frozenset({
    "required", "covariant", "final", "const", "late",
})

#: A library prefix at the head of a type: ``p.`` in ``p.Foo``.
_DART_LIBRARY_PREFIX = re.compile(r"(?:[A-Za-z_$][\w$]*\.)+")


def _dart_type_is_kept(text: str) -> bool:
    """True when ``strip_at_last_dot`` keeps every part of ``text`` but a
    library prefix (``p.Foo<q.Bar>?`` -> ``Foo<q.Bar>?``), or changes nothing.

    The strip keeps what follows the last dot of the text before its first
    ``<`` (or ``[``). On a function type or a record type that dot can sit
    inside the type (``int Function(p.A)`` -> ``A)``), and before a modifier
    it takes the modifier too (``required p.Foo`` -> ``Foo``).
    """
    from hypergumbo_core.analyze.base import strip_at_last_dot

    stripped = strip_at_last_dot(text)
    if stripped == text:
        return True
    prefix = _DART_LIBRARY_PREFIX.match(text)
    return prefix is not None and text[prefix.end():] == stripped


def _normalize_dart_type(text: str) -> str:
    """One type, normalized: its library prefix stripped when the shared FQN
    strip takes off that prefix only (or nothing), else the type as written (a
    function type's or a record type's inner types are kept as a generic's
    arguments are); every other name written through the file's imports
    (:func:`_dart_type_names`, ``normalize_type``)."""
    text = text.strip()
    return normalize_type(text) if _dart_type_is_kept(text) else canonical_type_names(text)


def _dart_import_bindings(tree: "tree_sitter.Tree", source: bytes) -> list[tuple[str, str]]:
    """What each import binds, for a typed stable id (WI-palus): ``import 'x.dart'
    as p;`` binds the prefix ``p`` to ``import("x.dart")``, so ``p.Foo`` is
    ``import("x.dart").Foo``; ``import 'x.dart' show Foo;`` binds ``Foo`` to
    ``import("x.dart").Foo``. An import with neither binds no name it spells
    (every exported name, unlisted), and neither does a prefixed import's
    ``show`` list (its names are reached through the prefix).
    (:func:`_extract_import_hints`, the call resolver's map, binds a prefixed
    import's shown names too.)"""
    bindings: list[tuple[str, str]] = []
    for node in iter_tree(tree.root_node):
        if node.type != "import_specification":
            continue
        uri = _extract_import_path(node, source)
        if uri is None:  # pragma: no cover - an import always names a uri
            continue
        children = node.children
        prefix = next((
            c for i, c in enumerate(children)
            if c.type == "identifier" and i and children[i - 1].type == "as"
        ), None)
        if prefix is not None:
            bindings.append((node_text(prefix, source), f'import("{uri}")'))
            continue
        for combinator in children:
            if combinator.type == "combinator" and combinator.children[0].type == "show":
                for name in combinator.children[1:]:
                    if name.type == "identifier":
                        shown = node_text(name, source)
                        bindings.append((shown, f'import("{uri}").{shown}'))
    return bindings


#: The node types that declare a type name in a Dart file.
_DART_TYPE_DECLARATION_NODES = frozenset({
    "class_definition", "mixin_declaration", "enum_declaration",
    "extension_declaration", "type_alias",
})


def _dart_type_names(tree: "tree_sitter.Tree", source: bytes) -> FileTypeNames:
    """What the file's imports make a type name mean (WI-palus, scheme v17).

    Bound: :func:`_dart_import_bindings`. Declared: every class, mixin, enum,
    extension and typedef the file declares, which keeps its name. No
    defaults: ``dart:core`` is imported into every file, but its types are
    written with a prefix only after an explicit ``import 'dart:core' as
    core;``, which binds the prefix like any other.
    """
    declared = set()
    for node in iter_tree(tree.root_node):
        if node.type in _DART_TYPE_DECLARATION_NODES:
            name = find_child_by_type(node, "identifier")
            if name is None:
                name = find_child_by_type(node, "type_identifier")
            if name is not None:
                declared.add(node_text(name, source))
    return file_type_names(_dart_import_bindings(tree, source), declared=declared)


def _dart_parameter_name(param: "tree_sitter.Node") -> Optional["tree_sitter.Node"]:
    """The identifier that names a ``formal_parameter``, or None.

    The grammar marks it as the ``name`` field, except in two forms: an
    untyped ``x`` (its lone ``identifier``) and an old-style function-typed
    ``int g(int a)``, whose name ``g`` stands before its parameter list. A
    ``this.x`` / ``super.x`` parameter (constructors only) names none here.
    """
    name = param.child_by_field_name("name")
    if name is not None:
        return name
    named = [c for c in param.children if c.type == "identifier"]
    return named[0] if named else None


def _dart_is_old_style(param: "tree_sitter.Node") -> bool:
    """True for an old-style function-typed parameter (``int g(int a)``): its
    own ``formal_parameter_list`` follows its name."""
    return any(c.type == "formal_parameter_list" for c in param.children)


#: The nodes whose ``typed_identifier`` declares a function type's parameter
#: (``int n`` in ``void Function(int n, {String s})``); a record type's named
#: field is a ``typed_identifier`` too, and is a field, not a parameter.
_DART_PARAMETER_TYPE_NODES = frozenset({"normal_parameter_type", "named_parameter_types"})

#: The tokens that end the run of nodes one member of a parameter list or
#: group owns, going backwards from it (``required``, an annotation and a
#: comment are the member's own).
_DART_MEMBER_DELIMITERS = frozenset({"(", "[", "{", ","})


def _dart_is_named_group(group: Optional["tree_sitter.Node"]) -> bool:
    """True for a ``{...}`` group of a parameter list or of a function type's
    parameter types: the parameters a caller passes by NAME."""
    return (group is not None
            and group.type in ("optional_formal_parameters", "named_parameter_types")
            and group.children[0].type == "{")


def _dart_label_start(member: "tree_sitter.Node") -> int:
    """Where a named parameter's label goes: before the first node the member
    owns (``required``, an annotation), comments aside, or before the member."""
    start = member.start_byte
    before = member.prev_sibling
    while before is not None and before.type not in _DART_MEMBER_DELIMITERS:
        if before.type not in _DART_COMMENT_NODE_TYPES:
            start = before.start_byte
        before = before.prev_sibling
    return start


def _dart_types_text(nodes: list["tree_sitter.Node"], source: bytes) -> str:
    """The source text from the first of ``nodes`` to the last, with every
    parameter NAME inside them left out, and comments.

    A name is a ``formal_parameter``'s (:func:`_dart_parameter_name`) or a
    function type's parameter's (``n`` of ``void Function(int n)``), at any
    depth (``Map<String, void Function(int u)>``). An old-style
    function-typed parameter's own name is written ``Function``, so ``int
    g(int a)`` reads ``int Function(int)``, the type it declares, and an
    untyped ``x`` (a parameter that is its name alone) reads ``dynamic``, its
    type: left out, ``f(x)`` would read as ``f()``. Each other name goes with
    the whitespace before it, as a comment does. A record type's field names
    are kept: a named field's name is part of the record type.

    A NAMED parameter's name (in a ``{...}`` group: ``z`` of ``void
    Function({required int z})``, ``b`` of ``int g({int b})``) is a caller's
    to write, so it is written again as the parameter's label, before what
    the parameter owns: ``{z:required int}`` (``labelled_parameter``'s
    spelling). The parameter ``nodes`` ends with is the caller's to label
    (:func:`_dart_hashed_group`).
    """
    edits: list[tuple[int, int, str]] = []
    for top in nodes:
        for node in iter_tree(top):
            if node.type in _DART_COMMENT_NODE_TYPES:
                before = node.prev_sibling
                edits.append((before.end_byte if before is not None else node.start_byte,
                              node.end_byte, ""))
                continue
            name: Optional["tree_sitter.Node"] = None
            if node.type == "formal_parameter":
                name = _dart_parameter_name(node)
            elif (node.type == "typed_identifier" and node.parent is not None
                  and node.parent.type in _DART_PARAMETER_TYPE_NODES):
                name = node.children[-1]
            if name is None:
                continue
            # A named parameter: its name goes before it as its label. The
            # parameter ``nodes`` ends with is labelled by its group instead
            # (:func:`_dart_hashed_group`).
            if _dart_is_named_group(node.parent) and node not in nodes:
                label_at = _dart_label_start(node)
                edits.append((label_at, label_at, node_text(name, source) + LABEL_SEPARATOR))
            if node.type == "formal_parameter" and _dart_is_old_style(node):
                edits.append((name.start_byte, name.end_byte, "Function"))
            else:
                before = name.prev_sibling
                if before is None:
                    # An untyped ``x`` (also inside ``int g(x)``): its type.
                    edits.append((name.start_byte, name.end_byte, "dynamic"))
                else:
                    edits.append((before.end_byte, name.end_byte, ""))
    out: list[bytes] = []
    pos = nodes[0].start_byte
    for start, end, text in sorted(edits):
        out += [source[pos:start], text.encode()]
        pos = end
    out.append(source[pos:nodes[-1].end_byte])
    return b"".join(out).decode("utf-8", errors="replace").strip()


def _dart_hashed_parameter(nodes: list["tree_sitter.Node"], source: bytes) -> str:
    """One parameter's hashed text: its annotations, modifiers and type,
    without its name (:func:`_dart_types_text`); its type's library prefix
    stripped when that is all the shared strip takes, a modifier set aside
    while it is (``required p.Foo`` -> ``required Foo``)."""
    text = _dart_types_text(nodes, source)
    if _dart_type_is_kept(text):
        return _normalize_dart_type(text)
    modifiers: list[str] = []
    word, _, after = text.partition(" ")
    while word in _DART_PARAMETER_MODIFIERS and after.strip():
        modifiers.append(word)
        text = after.lstrip()
        word, _, after = text.partition(" ")
    return " ".join([*modifiers, _normalize_dart_type(text)])


def _dart_hashed_group(group: "tree_sitter.Node", source: bytes) -> str:
    """A named or optional parameter group's hashed text: its bracket, each
    parameter's hashed text (with what stands before it in the group: an
    annotation, ``required``), and its closing bracket. A default value is
    left out, value and presence (it follows the parameter, up to the next
    ``,``); a comment between parameters too.

    In a ``{...}`` group each parameter is hashed with its name, through
    :func:`~hypergumbo_core.analyze.base.labelled_parameter` (``count:required
    int``): a caller writes it (``f(count: 1)``), so it is identity, as a
    Swift argument label is. An ``[...]`` group's names are a caller's to
    leave out, and are."""
    members: list[str] = []
    pending: list["tree_sitter.Node"] = []
    in_default = False
    named = _dart_is_named_group(group)
    for child in group.children[1:-1]:
        if child.type == ",":
            pending, in_default = [], False
        elif child.type == "formal_parameter":
            name = _dart_parameter_name(child) if named else None
            members.append(labelled_parameter(
                node_text(name, source) if name is not None else None,
                _dart_hashed_parameter([*pending, child], source)))
            pending, in_default = [], True
        elif not in_default and (pending or child.type not in _DART_COMMENT_NODE_TYPES):
            pending.append(child)
    return node_text(group.children[0], source) + ",".join(members) + node_text(
        group.children[-1], source)


def _dart_hashed_signature(func_sig_node: "tree_sitter.Node", source: bytes) -> str:
    """The text a Dart function's or method's typed stable_id hashes, read
    from its ``function_signature``: types only, every group closed.

    ``(P,...)R``: each parameter of the list as :func:`_dart_hashed_parameter`
    writes it, a named or optional group as :func:`_dart_hashed_group` does,
    joined by ``,``; then the return type (comments and function-type
    parameter names left out, its library prefix stripped as a parameter's;
    ``void`` or none: nothing). No positional parameter's name is in the
    text, so renaming one moves no id, and a default value is not either; a
    named parameter's name is (:func:`_dart_hashed_group`). Each part is read
    from the tree, so a string in an annotation (``@A("x)")``) or a default
    holds no separator, and layout is the hash's own concern (``layout_free``).
    """
    params: list[str] = []
    plist = next((c for c in func_sig_node.children if c.type == "formal_parameter_list"), None)
    for child in plist.children if plist is not None else ():
        if child.type == "formal_parameter":
            params.append(_dart_hashed_parameter([child], source))
        elif child.type == "optional_formal_parameters":
            params.append(_dart_hashed_group(child, source))
    returned = _dart_return_type(func_sig_node, source, hashed=True) or ""
    if returned.lower() == "void":
        returned = ""
    return f"({','.join(params)})" + (_normalize_dart_type(returned) if returned else "")


def _extract_dart_return_type_name(signature: str | None) -> str | None:
    """Extract the return type name from a Dart function signature.

    Dart signatures use the format ``(params) ReturnType`` where void is omitted.
    Returns the head of the type when it is a simple uppercase identifier
    (filters out primitive types like ``int`` and ``String``), with its type
    arguments and a ``?`` taken off. A function type (``Function``,
    ``Foo Function()``), a record type and a prefixed type give None.

    Examples:
        ``"() ServiceClient"`` → ``"ServiceClient"``
        ``"(String name, int age) User"`` → ``"User"``
        ``"() User?"`` → ``"User"``
        ``"() List<String>"`` → ``"List"``
        ``"()"`` → ``None``
        ``"() int"`` → ``None`` (lowercase = primitive)
    """
    if not signature:
        return None
    paren_idx = signature.rfind(")")
    if paren_idx < 0 or paren_idx == len(signature) - 1:
        return None
    ret_part = signature[paren_idx + 1:].strip().split("<", 1)[0].rstrip("?")
    if ret_part and ret_part != "Function" and ret_part.isidentifier() and ret_part[0].isupper():
        return ret_part
    return None


def _find_enclosing_class(
    node: "tree_sitter.Node",
    source: bytes,
) -> Optional[str]:
    """Find the enclosing class/mixin/extension name for a given node."""
    current = node.parent
    while current:
        if current.type in ("class_definition", "mixin_declaration", "extension_declaration"):
            name_node = find_child_by_type(current, "identifier")
            if name_node:
                return node_text(name_node, source)
        current = current.parent
    return None


def _dart_scope(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """The classes, mixins, extensions and functions enclosing ``node``,
    outermost first (INV-fabih).

    A local function (``int inner() => 1;`` in a function body) was emitted
    under its bare name (``C.inner`` inside a method of ``C``), so two local
    ``inner`` in ``a`` and ``b`` tied and deleting the first moved the second.
    tree-sitter-dart puts a function's body BESIDE its signature, so an
    enclosing function is read off the ``function_body`` ancestor's preceding
    signature. A top-level function or a method of a top-level class keeps
    its value: there the chain is its owner-qualified name.
    """
    def label(n: "tree_sitter.Node") -> Optional[str]:
        if n.type in ("class_definition", "mixin_declaration", "extension_declaration"):
            name_node = find_child_by_type(n, "identifier")
        elif n.type == "function_body" and n.prev_named_sibling is not None:
            sig = n.prev_named_sibling
            if sig.type == "method_signature":
                sig = next((c for c in sig.named_children), sig)
            name_node = find_child_by_type(sig, "identifier")
        else:
            return None
        return node_text(name_node, source) if name_node is not None else None
    return ancestor_labels(node, label)


# WI-jusus (emission-parity): value-declaration list nodes and the contexts that
# make them fields vs top-level variables. In tree-sitter-dart a value declaration
# is an ``initialized_identifier_list`` (var/typed) or ``static_final_declaration_list``
# (const/static/final-inferred). Inside a type BODY (wrapped in a ``declaration``) it is
# a field; directly under ``program`` it is a top-level variable. Function-/method-/
# for-/lambda-/if-block locals use a DISTINCT node (``local_variable_declaration`` →
# ``initialized_variable_definition``) and never match these, so the swift-INV-lanaz /
# go-INV-sidab local-leak class is structurally impossible here (verified by AST probe).
_DART_VALUE_LIST_TYPES = frozenset(
    {"initialized_identifier_list", "static_final_declaration_list"}
)
# The single-name item wrapper inside each list (one per comma-separated name).
_DART_VALUE_ITEM_TYPES = frozenset(
    {"initialized_identifier", "static_final_declaration"}
)
# Type-body node types (class_body serves both class and mixin declarations).
_DART_FIELD_BODY_TYPES = frozenset({"class_body", "extension_body", "enum_body"})
_DART_TYPE_DECL_TYPES = frozenset(
    {
        "class_definition",
        "mixin_declaration",
        "extension_declaration",
        "enum_declaration",
    }
)


def _dart_type_owner(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Nearest enclosing type name for a field (class/mixin/extension/enum).

    Widens ``_find_enclosing_class`` (which omits enums) for the field path,
    without changing that helper's existing call-resolution behaviour.
    """
    current = node.parent
    while current is not None:
        if current.type in _DART_TYPE_DECL_TYPES:
            name_node = find_child_by_type(current, "identifier")
            if name_node is not None:
                return node_text(name_node, source)
            return None  # pragma: no cover - a named type decl always has an identifier
        current = current.parent
    return None  # pragma: no cover - a field-scope list is always under a type decl


def _dart_value_list_has_error_sibling(list_node: "tree_sitter.Node") -> bool:
    """True when a record/list/map *pattern* declaration mis-parsed into this list.

    Dart-3 pattern declarations (``var (a, b) = pair;``, ``final [x, y] = list;``,
    ``final {'k': v} = map;``) are not supported by the bundled grammar; they
    mis-parse and salvage the RHS / first binding as an
    ``initialized_identifier_list``. Every such mis-parse leaves an ``ERROR`` node
    among the list's preceding siblings (back to the ``;`` boundary); a well-formed
    const/var/field/multi-name declaration never does. Skipping on that signal is
    fails-safe — it also drops the rare ``abstract``/``external`` field (which this
    grammar likewise parses under an ``ERROR``), a documented deferral: miss the
    symbol, never emit a wrong one.
    """
    sibling = list_node.prev_sibling
    while sibling is not None and sibling.type != ";":
        if sibling.type == "ERROR":
            return True
        sibling = sibling.prev_sibling
    return False


def _dart_value_type(
    list_node: "tree_sitter.Node", source: bytes, *, hashed: bool = False,
) -> Optional[str]:
    """The declared type for a value list, as written (``hashed``: as the
    hash reads it, :func:`_dart_type_text`), or None.

    The grammar writes the type as the run of siblings before the list
    (``_DART_TYPE_PART_NODE_TYPES``): ``int``, ``p.Foo``, ``List<String>``,
    ``int?``, ``void Function()``, ``(int, String)``. A modifier (``final``,
    ``late``, ``static``, ``const``, ``covariant``), ``var`` (``inferred_type``)
    or anything before the declaration ends the run; ``final x`` and
    ``var x`` declare no type and give None. Comments inside the run are
    left out.
    """
    parts: list["tree_sitter.Node"] = []
    sibling = list_node.prev_sibling
    while sibling is not None and (
        sibling.type in _DART_TYPE_PART_NODE_TYPES or sibling.type in _DART_COMMENT_NODE_TYPES
    ):
        parts.append(sibling)
        sibling = sibling.prev_sibling
    return _dart_type_text(parts[::-1], source, hashed=hashed)


def _dart_value_names(
    list_node: "tree_sitter.Node", source: bytes
) -> list[tuple[str, "tree_sitter.Node"]]:
    """(name, identifier_node) for each comma-separated name in a value list."""
    out: list[tuple[str, "tree_sitter.Node"]] = []
    for item in list_node.children:
        if item.type in _DART_VALUE_ITEM_TYPES:
            name_node = find_child_by_type(item, "identifier")
            if name_node is not None:
                out.append((node_text(name_node, source), item))
    return out


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> list[Symbol]:
    """Extract all symbols from a parsed Dart file, each type a stable id
    hashes written through the file's imports (:func:`_dart_type_names`)."""
    with resolving_type_names(_dart_type_names(tree, source)):
        return _extract_parsed_symbols(tree, source, file_path, run_id)


def _extract_parsed_symbols(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> list[Symbol]:
    """Extract all symbols from a parsed Dart file."""
    symbols: list[Symbol] = []

    # WI-bokab (v7): file-identity anchor for this file's symbols. ``file_path`` is
    # the repo-relative path (DartAnalyzer.extract_symbols_from_file passes ``rel_path``).
    # Folded into make_typed_stable_id's containing slot so same-name methods/functions
    # in different files hash distinctly. Matches the file Symbol's own stable_id
    # (make_file_stable_id("dart", normalize_path(p))).
    file_stable_id = make_file_stable_id("dart", normalize_path(file_path))

    def make_symbol(
        start_line: int,
        end_line: int,
        start_col: int,
        end_col: int,
        name: str,
        kind: str,
        prefix: Optional[str] = None,
        signature: Optional[str] = None,
        hashed_signature: Optional[str] = None,
        complexity: bool = False,
        def_node: Optional["tree_sitter.Node"] = None,
        scope_node: Optional["tree_sitter.Node"] = None,
    ) -> Symbol:
        """Create a Symbol with given span.

        ``scope_node``, when given, is the declaration whose full scope chain
        (:func:`_dart_scope`) the stable_id hashes instead of ``prefix.name``.

        ``complexity=True`` (callable kinds) populates cyclomatic_complexity
        (walked from ``def_node`` — the function_body when present, else the
        signature node) and line_span, per INV-loguk. Gated so non-callable
        kinds (class/mixin/extension/enum) don't aggregate their whole subtree.
        """
        full_name = f"{prefix}.{name}" if prefix else name
        span = Span(
            start_line=start_line,
            end_line=end_line,
            start_col=start_col,
            end_col=end_col,
        )
        sym_id = make_symbol_id("dart", file_path, start_line, end_line, full_name, kind)
        # Dart visibility: underscore-prefixed names are library-private
        modifiers = ["private"] if name.startswith("_") else []

        hashed_name = (
            ".".join((*_dart_scope(scope_node, source), name))
            if scope_node is not None else full_name
        )

        # Typed stable_id (spec §6 typed tier). Dart writes ``async`` / ``async*`` on
        # the function BODY, which ``def_node`` is when the symbol has one.
        is_async = def_node is not None and declares_async(def_node)
        stable_id = None
        if hashed_signature:
            stable_id = make_typed_stable_id(
                kind, hashed_signature, visibility_from_modifiers(modifiers),
                name=name, qualified_name=hashed_name,
                file_stable_id=file_stable_id,
                is_async=is_async,
            )
        if stable_id is None and kind in _UNSIGNED_PRODUCER_KINDS:
            # WI-tusad: constructors, getters, setters, mixins and extensions
            # carry no signature here and rode the backstop floor key. Dart
            # has no overloading (a named constructor is its own name), so the
            # owner-qualified name is the whole discriminant.
            stable_id = make_typed_stable_id(
                kind, "", visibility_from_modifiers(modifiers),
                name=name, qualified_name=full_name,
                file_stable_id=file_stable_id,
                is_async=is_async,
            )

        return Symbol(
            id=sym_id,
            name=full_name,
            kind=kind,
            language="dart",
            path=file_path,
            span=span,
            origin=PASS_ID,
            origin_run_id=run_id,
            stable_id=stable_id,
            signature=signature,
            modifiers=modifiers,
            cyclomatic_complexity=(
                compute_cyclomatic_complexity(def_node, "dart") if complexity else None
            ),
            line_span=(end_line - start_line + 1) if complexity else None,
        )

    for node in iter_tree(tree.root_node):
        # Class declaration
        if node.type == "class_definition":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                name = node_text(name_node, source)
                start_line, end_line, start_col, end_col = _get_combined_span(node, None)
                symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "class"))
            continue

        # Mixin declaration
        if node.type == "mixin_declaration":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                name = node_text(name_node, source)
                start_line, end_line, start_col, end_col = _get_combined_span(node, None)
                symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "mixin"))
            continue

        # Extension declaration
        if node.type == "extension_declaration":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                name = node_text(name_node, source)
                start_line, end_line, start_col, end_col = _get_combined_span(node, None)
                symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "extension"))
            continue

        # Enum declaration
        if node.type == "enum_declaration":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                name = node_text(name_node, source)
                start_line, end_line, start_col, end_col = _get_combined_span(node, None)
                symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "enum"))
            continue

        # Method signature (inside class body) - contains function_signature, getter, or setter
        if node.type == "method_signature":
            class_name = _find_enclosing_class(node, source)

            # Check for getter_signature
            getter_sig = find_child_by_type(node, "getter_signature")
            if getter_sig:
                name_node = find_child_by_type(getter_sig, "identifier")
                if name_node:
                    name = node_text(name_node, source)
                    # Find the function_body sibling
                    body = _find_next_sibling_by_type(node, "function_body")
                    start_line, end_line, start_col, end_col = _get_combined_span(node, body)
                    symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "getter", class_name, complexity=True, def_node=(body if body is not None else node)))
                continue

            # Check for setter_signature
            setter_sig = find_child_by_type(node, "setter_signature")
            if setter_sig:
                name_node = find_child_by_type(setter_sig, "identifier")
                if name_node:
                    name = node_text(name_node, source)
                    body = _find_next_sibling_by_type(node, "function_body")
                    start_line, end_line, start_col, end_col = _get_combined_span(node, body)
                    symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "setter", class_name, complexity=True, def_node=(body if body is not None else node)))
                continue

            # Check for constructor_signature (rare in method_signature)
            ctor_sig = find_child_by_type(node, "constructor_signature")
            if ctor_sig:  # pragma: no cover - constructor in method_signature
                name_parts = []
                for child in ctor_sig.children:
                    if child.type == "identifier":
                        name_parts.append(node_text(child, source))
                if name_parts:
                    name = ".".join(name_parts)
                    body = _find_next_sibling_by_type(node, "function_body")
                    start_line, end_line, start_col, end_col = _get_combined_span(node, body)
                    symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "constructor", class_name, complexity=True, def_node=(body if body is not None else node)))
                continue

            # Regular function_signature inside method_signature
            func_sig = find_child_by_type(node, "function_signature")
            if func_sig:
                name_node = find_child_by_type(func_sig, "identifier")
                if name_node:
                    name = node_text(name_node, source)
                    body = _find_next_sibling_by_type(node, "function_body")
                    start_line, end_line, start_col, end_col = _get_combined_span(node, body)
                    sig = _extract_dart_signature(func_sig, source)
                    symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "method", class_name, signature=sig, hashed_signature=_dart_hashed_signature(func_sig, source), complexity=True, def_node=(body if body is not None else node)))
            continue

        # Top-level function_signature (not inside method_signature)
        if node.type == "function_signature" and (node.parent is None or node.parent.type != "method_signature"):
            class_name = _find_enclosing_class(node, source)
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                name = node_text(name_node, source)
                # Find the function_body sibling
                body = _find_next_sibling_by_type(node, "function_body")
                start_line, end_line, start_col, end_col = _get_combined_span(node, body)
                sig = _extract_dart_signature(node, source)
                symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "function" if not class_name else "method", class_name, signature=sig, hashed_signature=_dart_hashed_signature(node, source), complexity=True, def_node=(body if body is not None else node), scope_node=node))
            continue

        # Constructor signature at top level of class body (rare but possible)
        if node.type == "constructor_signature" and (node.parent is None or node.parent.type != "method_signature"):
            class_name = _find_enclosing_class(node, source)
            name_parts = []
            for child in node.children:
                if child.type == "identifier":
                    name_parts.append(node_text(child, source))
            if name_parts:
                name = ".".join(name_parts)
                body = _find_next_sibling_by_type(node, "function_body")
                start_line, end_line, start_col, end_col = _get_combined_span(node, body)
                symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "constructor", class_name, complexity=True, def_node=(body if body is not None else node)))
            continue

        # Value declarations (WI-jusus emission-parity): field / top-level variable.
        # A field carries its owner as prefix (Owner.name); a top-level variable is
        # bare. Locals never reach here (distinct node type). Fields/variables are
        # kept OUT of the call-resolution registry (see DartAnalyzer.register_symbol),
        # so bare-named variables can never clobber a function key nor become a
        # spurious call/instantiate target.
        if node.type in _DART_VALUE_LIST_TYPES:
            parent = node.parent
            if parent is None:  # pragma: no cover - a list node always has a parent
                continue
            if (
                parent.type == "declaration"
                and parent.parent is not None
                and parent.parent.type in _DART_FIELD_BODY_TYPES
            ):
                scope = "field"
            elif parent.type == "program":
                scope = "variable"
            else:  # pragma: no cover - a value list is only ever under a type
                continue      # body's `declaration` or directly under `program`
            # Pattern-declaration mis-parse guard (var (a, b) = x; etc.).
            if _dart_value_list_has_error_sibling(node):
                continue
            owner = _dart_type_owner(node, source) if scope == "field" else None
            if scope == "field" and owner is None:  # pragma: no cover - always named
                continue
            vtype = _dart_value_type(node, source)
            hashed_type = _dart_value_type(node, source, hashed=True)
            for value_name, item in _dart_value_names(node, source):
                full_name = (
                    f"{owner}.{value_name}" if scope == "field" else value_name
                )
                v_modifiers = ["private"] if value_name.startswith("_") else []
                v_start = item.start_point[0] + 1
                v_end = item.end_point[0] + 1
                if scope == "field":
                    v_stable_id = make_typed_stable_id(
                        "field", _normalize_dart_type(hashed_type) if hashed_type else "",
                        visibility_from_modifiers(v_modifiers),
                        name=value_name, qualified_name=full_name,
                        file_stable_id=file_stable_id,
                    )
                else:
                    v_stable_id = make_variable_stable_id(
                        "dart", file_path, value_name
                    )
                symbols.append(Symbol(
                    id=make_symbol_id(
                        "dart", file_path, v_start, v_end, full_name, scope
                    ),
                    name=full_name,
                    kind=scope,
                    language="dart",
                    path=file_path,
                    span=Span(
                        start_line=v_start,
                        end_line=v_end,
                        start_col=item.start_point[1],
                        end_col=item.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    stable_id=v_stable_id,
                    signature=vtype,
                    modifiers=v_modifiers,
                    is_exported=not value_name.startswith("_"),
                ))
            continue

    return symbols


def _find_enclosing_function(
    node: "tree_sitter.Node",
    function_scopes: dict[int, Symbol],
) -> Optional[Symbol]:
    """Find the function/method that contains this node."""
    current = node.parent
    while current:
        if current.type in ("function_signature", "function_body"):
            # Get the line number and look up
            line = current.start_point[0] + 1
            if line in function_scopes:
                return function_scopes[line]
            # Also check if this is part of a method_signature
            if current.parent and current.parent.type == "method_signature":  # pragma: no cover
                line = current.parent.start_point[0] + 1  # pragma: no cover
                if line in function_scopes:  # pragma: no cover
                    return function_scopes[line]  # pragma: no cover
        current = current.parent
    return None  # pragma: no cover - no enclosing function


def _extract_import_hints(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract import statements for disambiguation.

    In Dart:
        import 'path' as prefix; -> prefix maps to path
        import 'path' show Name1, Name2; -> Name1 and Name2 map to path

    Returns a dict mapping short names/prefixes to full import paths.
    """
    hints: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "import_specification":
            continue

        # Find the import path
        import_path: Optional[str] = None
        as_prefix: Optional[str] = None
        show_names: list[str] = []
        has_as = False

        for child in node.children:
            if child.type == "configurable_uri":
                # Extract path from uri > string_literal
                for sub in iter_tree(child):
                    if sub.type == "string_literal":
                        import_path = node_text(sub, source).strip("'\"")
                        break
            elif child.type == "as":
                has_as = True
            elif child.type == "identifier" and has_as:
                as_prefix = node_text(child, source)
            elif child.type == "combinator":
                # Look for show followed by identifiers
                is_show = False
                for sub in child.children:
                    if sub.type == "show":
                        is_show = True
                    elif sub.type == "identifier" and is_show:
                        show_names.append(node_text(sub, source))

        if import_path:
            if as_prefix:
                hints[as_prefix] = import_path
            for name in show_names:
                hints[name] = import_path

    return hints


def _extract_import_path(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract the import path from an import/export directive using iterative traversal."""
    for n in iter_tree(node):
        if n.type == "string_literal":
            text = node_text(n, source)
            # Remove quotes
            return text.strip("'\"")
    return None  # pragma: no cover - no string literal found


def _extract_param_types(
    node: "tree_sitter.Node", source: bytes
) -> dict[str, str]:
    """Extract parameter name -> type mapping from a Dart function signature.

    Dart function signatures look like:
        int add(int x, int y) { ... }
        void greet(String name) { ... }
        void process(Database db, {bool verbose = false}) { ... }

    Returns mapping like {"x": "int", "y": "int"} or {"db": "Database"}.
    """
    param_types: dict[str, str] = {}

    # Find the formal_parameter_list child
    params_node = find_child_by_type(node, "formal_parameter_list")
    if params_node is None:
        return param_types  # pragma: no cover - no params in function

    for child in params_node.children:
        if child.type == "formal_parameter":
            # formal_parameter contains type_identifier + identifier
            param_type: Optional[str] = None
            param_name: Optional[str] = None

            for subchild in child.children:
                if subchild.type == "type_identifier":
                    param_type = node_text(subchild, source)
                    # Strip generics: List<T> -> List (defensive - tree-sitter usually separates generics)
                    if "<" in param_type:  # pragma: no cover
                        param_type = param_type.split("<")[0]
                elif subchild.type == "identifier":
                    # First identifier after type is the parameter name
                    if param_type is not None and param_name is None:
                        param_name = node_text(subchild, source)

            if param_type and param_name:
                param_types[param_name] = param_type

        elif child.type == "optional_formal_parameters":
            # Handle optional/named parameters: {Database db, bool verbose}
            for opt_child in child.children:
                if opt_child.type == "formal_parameter":
                    param_type = None
                    param_name = None

                    for subchild in opt_child.children:
                        if subchild.type == "type_identifier":
                            param_type = node_text(subchild, source)
                            if "<" in param_type:  # pragma: no cover - defensive
                                param_type = param_type.split("<")[0]
                        elif subchild.type == "identifier":
                            if param_type is not None and param_name is None:
                                param_name = node_text(subchild, source)

                    if param_type and param_name:
                        param_types[param_name] = param_type

    return param_types


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    resolver: NameResolver,
    run_id: str,
    import_hints: dict[str, str] | None = None,
    method_resolver: ListNameResolver | None = None,
) -> list[Edge]:
    """Extract call, import, and instantiation edges from a parsed Dart file.

    Args:
        import_hints: Optional dict mapping short names to full import paths for disambiguation.
        method_resolver: Optional ListNameResolver for AMB-METHOD ambiguity guard.
    """
    if import_hints is None:  # pragma: no cover - defensive default
        import_hints = {}
    edges: list[Edge] = []
    file_id = make_file_id("dart", file_path)

    # Track functions by their enclosing scope
    function_scopes: dict[int, Symbol] = {}  # line -> symbol

    # Variable type tracking: variable_name -> class_name
    # Used for resolving method calls like db.save() to Database.save
    var_types: dict[str, str] = {}

    # First pass: map lines to their symbols. A span-less symbol has no
    # line to key on, so it is omitted from the scope map; consumers
    # (_find_enclosing_function callers) already handle a missing scope
    # by skipping caller attribution.
    for sym in file_symbols:
        if sym.kind in ("function", "method") and sym.span is not None:
            function_scopes[sym.span.start_line] = sym

    for node in iter_tree(tree.root_node):
        # Track parameter types from function declarations
        if node.type == "function_signature":
            param_types = _extract_param_types(node, source)
            var_types.update(param_types)

        # Track return types from function/method calls in variable declarations
        # Pattern: var client = getClient() or var client = factory.create()
        # AST: initialized_variable_definition > [inferred_type] identifier = identifier [selector(.method)] selector(args)
        if node.type == "initialized_variable_definition":
            children = list(node.children)
            var_name = None
            call_target = None
            call_method = None
            has_args = False
            after_eq = False

            for child in children:
                if child.type == "identifier":
                    if not after_eq:
                        var_name = node_text(child, source)
                    elif call_target is None:
                        call_target = node_text(child, source)
                elif child.type == "=":
                    after_eq = True
                elif child.type == "selector" and after_eq:
                    for sel_child in child.children:
                        if sel_child.type == "unconditional_assignable_selector":
                            for sub in sel_child.children:
                                if sub.type == "identifier":
                                    call_method = node_text(sub, source)
                        if sel_child.type in ("argument_part", "arguments"):
                            has_args = True
                    if any(
                        c.type in ("argument_part", "arguments")
                        for c in child.children
                    ):
                        has_args = True

            if var_name and call_target and has_args:
                resolved_sym = None
                if call_method and call_target in var_types:
                    # receiver.method() pattern
                    class_name = var_types[call_target]
                    qualified_name = f"{class_name}.{call_method}"
                    path_hint = import_hints.get(class_name)
                    lookup_result = resolver.lookup(
                        qualified_name, path_hint=path_hint
                    )
                    if lookup_result.found and lookup_result.symbol:
                        resolved_sym = lookup_result.symbol
                elif not call_method:
                    # Simple function call pattern
                    path_hint = import_hints.get(call_target)
                    lookup_result = resolver.lookup(
                        call_target, path_hint=path_hint
                    )
                    if lookup_result.found and lookup_result.symbol:
                        resolved_sym = lookup_result.symbol

                if resolved_sym and resolved_sym.kind in ("function", "method"):
                    ret_name = _extract_dart_return_type_name(
                        resolved_sym.signature
                    )
                    if ret_name:
                        # Verify return type class exists
                        class_result = resolver.lookup(ret_name)
                        if class_result.found:
                            var_types[var_name] = ret_name

        # Import/export directive
        if node.type == "import_or_export":
            import_path = _extract_import_path(node, source)
            if import_path:
                module_id = f"dart:{import_path}:0-0:module:module"
                edge = Edge.create(
                    src=file_id,
                    dst=module_id,
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    evidence_type="import",
                )
                edges.append(edge)

        # Expression statement with function call pattern:
        # expression_statement > identifier + selector(s)
        # The AST can have multiple selectors: one for method name, one for arguments
        # Example: db.save('test') -> identifier('db') + selector('.save') + selector("('test')")
        if node.type == "expression_statement":
            children = list(node.children)
            first_ident = None
            method_name = None
            has_args = False

            for child in children:
                if child.type == "identifier" and first_ident is None:
                    first_ident = node_text(child, source)
                elif child.type == "selector":
                    # Check for method name in unconditional_assignable_selector
                    for sel_child in child.children:
                        if sel_child.type == "unconditional_assignable_selector":
                            for sub in sel_child.children:
                                if sub.type == "identifier":
                                    method_name = node_text(sub, source)
                                    break
                        # Check for arguments in this or any selector
                        if sel_child.type in ("argument_part", "arguments"):
                            has_args = True
                    # Also check for argument_part directly in selector children
                    if any(c.type == "argument_part" or c.type == "arguments" for c in child.children):
                        has_args = True

            if first_ident and has_args:
                caller = _find_enclosing_function(node, function_scopes)
                if caller:
                    if method_name and first_ident in var_types:
                        # Type-inferred method call: receiver.method()
                        class_name = var_types[first_ident]
                        qualified_name = f"{class_name}.{method_name}"
                        path_hint = import_hints.get(class_name)
                        lookup_result = resolver.lookup(qualified_name, path_hint=path_hint)
                        if lookup_result.found and lookup_result.symbol:
                            callee = lookup_result.symbol
                            edge = Edge.create(
                                src=caller.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="ast_call",
                                resolution_score=lookup_result.confidence,
                                meta={"call_construct": "method", "resolution_quality": "type_inferred"},
                            )
                            edges.append(edge)
                    elif not method_name:
                        # Simple function call: func()
                        # AMB-METHOD guard: when 3+ classes define the same
                        # method name, suppress the edge to avoid false positives.
                        path_hint = import_hints.get(first_ident)
                        if method_resolver is not None:
                            if method_resolver.too_ambiguous(first_ident, path_hint=path_hint):
                                continue  # 3+ method candidates, suppress
                        lookup_result = resolver.lookup(first_ident, path_hint=path_hint)
                        if lookup_result.found and lookup_result.symbol:
                            callee = lookup_result.symbol
                            # INV-fahub: a bare call resolving only to a DIFFERENT
                            # class's method on weak short-name evidence is a magnet
                            # (dozens of call sites -> one arbitrary def). Defer it to
                            # the inherited_calls Site-1 walker via an unresolved edge
                            # that carries the call site's enclosing class; free
                            # functions and same-class implicit-``this`` calls bind.
                            enclosing_type = _find_enclosing_class(node, source)
                            if defer_bare_method_call(
                                callee.kind, callee.name,
                                lookup_result.match_type, enclosing_type,
                            ):
                                edges.append(make_unresolved_edge(
                                    "dart", caller.id, first_ident,
                                    node.start_point[0] + 1, PASS_ID, run_id,
                                    module_hint=path_hint or "external",
                                    dst_ref=(
                                        ExternalRef(
                                            lang="dart",
                                            module_path=path_hint,
                                            name=first_ident,
                                        )
                                        if path_hint else None
                                    ),
                                    enclosing_class=enclosing_type,
                                ))
                            else:
                                edge = Edge.create(
                                    src=caller.id,
                                    dst=callee.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                    evidence_type="ast_call",
                                    resolution_score=lookup_result.confidence,
                                    meta={"call_construct": "function"},
                                )
                                edges.append(edge)
                        elif defer_unresolved_tie(lookup_result):
                            # A tie among methods (WI-jomik: no guess) is deferred
                            # exactly as a weak pick was.
                            edges.append(make_unresolved_edge(
                                "dart", caller.id, first_ident,
                                node.start_point[0] + 1, PASS_ID, run_id,
                                module_hint=path_hint or "external",
                                enclosing_class=_find_enclosing_class(node, source),
                            ))

        # Method call in selector (obj.method()) - complex AST pattern
        if node.type == "selector":  # pragma: no cover - method call detection
            for child in node.children:
                if child.type == "unconditional_assignable_selector":
                    method_name = None
                    for sub in child.children:
                        if sub.type == "identifier":
                            method_name = node_text(sub, source)
                    # Check if followed by argument_part
                    if method_name:
                        has_args = any(c.type == "argument_part" for c in node.children)
                        if has_args:
                            caller = _find_enclosing_function(node, function_scopes)
                            if caller:
                                path_hint = import_hints.get(method_name)
                                lookup_result = resolver.lookup(method_name, path_hint=path_hint)
                                if lookup_result.found and lookup_result.symbol:
                                    callee = lookup_result.symbol
                                    # INV-fahub: same magnet gate as the simple-call
                                    # path — a chained-receiver call whose receiver
                                    # token was dropped must not confidently bind to
                                    # an unrelated class's method on suffix evidence.
                                    enclosing_type = _find_enclosing_class(node, source)
                                    if defer_bare_method_call(
                                        callee.kind, callee.name,
                                        lookup_result.match_type, enclosing_type,
                                    ):
                                        edges.append(make_unresolved_edge(
                                            "dart", caller.id, method_name,
                                            node.start_point[0] + 1, PASS_ID, run_id,
                                            module_hint=path_hint or "external",
                                            dst_ref=(
                                                ExternalRef(
                                                    lang="dart",
                                                    module_path=path_hint,
                                                    name=method_name,
                                                )
                                                if path_hint else None
                                            ),
                                            enclosing_class=enclosing_type,
                                        ))
                                    else:
                                        edge = Edge.create(
                                            src=caller.id,
                                            dst=callee.id,
                                            edge_type="calls",
                                            line=node.start_point[0] + 1,
                                            origin=PASS_ID,
                                            origin_run_id=run_id,
                                            evidence_type="ast_call",
                                            resolution_score=lookup_result.confidence,
                                            meta={"call_construct": "method"},
                                        )
                                        edges.append(edge)
                                elif defer_unresolved_tie(lookup_result):
                                    # A tie among methods (WI-jomik: no guess) is deferred
                                    # exactly as a weak pick was.
                                    edges.append(make_unresolved_edge(
                                        "dart", caller.id, method_name,
                                        node.start_point[0] + 1, PASS_ID, run_id,
                                        module_hint=path_hint or "external",
                                        enclosing_class=_find_enclosing_class(node, source),
                                    ))

        # Constructor invocation (ClassName() or new ClassName())
        if node.type in ("new_expression", "const_object_expression"):
            # Look for the type/class name
            for child in node.children:
                if child.type in ("type_identifier", "identifier"):
                    class_name = node_text(child, source)
                    caller = _find_enclosing_function(node, function_scopes)
                    if caller:
                        path_hint = import_hints.get(class_name)
                        lookup_result = resolver.lookup(class_name, path_hint=path_hint)
                        if lookup_result.found and lookup_result.symbol:
                            callee = lookup_result.symbol
                            edge = Edge.create(
                                src=caller.id,
                                dst=callee.id,
                                edge_type="instantiates",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="ast_call",
                                resolution_score=lookup_result.confidence,
                                meta={"call_construct": "constructor"},
                            )
                            edges.append(edge)

                    # Track variable type from constructor assignment
                    # Look for patterns like: var x = new ClassName() or final x = ClassName()
                    # AST: initialized_variable_definition > identifier + new_expression
                    parent = node.parent
                    if parent and parent.type == "initialized_variable_definition":
                        # Pattern: var x = new ClassName() or final x = new ClassName()
                        var_name_node = find_child_by_type(parent, "identifier")
                        if var_name_node:
                            var_name = node_text(var_name_node, source)
                            var_types[var_name] = class_name
                    break

        # Also detect ClassName() pattern (without new keyword) - look for type + arguments
        # This is a complex AST pattern that's hard to trigger reliably in tests
        if node.type == "primary":  # pragma: no cover - implicit constructor call
            children = list(node.children)
            for i, child in enumerate(children):
                if child.type == "type_identifier":
                    class_name = node_text(child, source)
                    # Check for following arguments
                    if i + 1 < len(children) and children[i + 1].type == "arguments":
                        caller = _find_enclosing_function(node, function_scopes)
                        if caller:
                            path_hint = import_hints.get(class_name)
                            lookup_result = resolver.lookup(class_name, path_hint=path_hint)
                            if lookup_result.found and lookup_result.symbol:
                                callee = lookup_result.symbol
                                edge = Edge.create(
                                    src=caller.id,
                                    dst=callee.id,
                                    edge_type="instantiates",
                                    line=node.start_point[0] + 1,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                    evidence_type="ast_call",
                                    resolution_score=lookup_result.confidence,
                                    meta={"call_construct": "constructor"},
                                )
                                edges.append(edge)
                    break

    return edges


# ---------------------------------------------------------------------------
# Inheritance (WI-lahub)
# ---------------------------------------------------------------------------
#
# Dart spells four type-to-type relations, each in its own clause:
#
#   class C extends B with M1, M2 implements I, J { }   (also enum: with/implements)
#   mixin M on B, B2 implements I { }
#
# The analyzer emits these edges itself instead of handing a base list to the
# shared ``inheritance`` linker, because only the CLAUSE separates
# ``extends`` from ``implements`` in Dart: the language has no ``interface``
# kind (every class declares an implicit interface, and ``abstract class`` is
# what an interface is written as), so the linker's label — keyed on whether
# the TARGET is an interface/trait/protocol — would print every Dart
# ``implements`` as ``extends``. Its maps also index no ``mixin`` kind, so a
# ``with`` clause would vanish there.
#
# Clause -> label:
#   extends    -> ``extends``     (ast_extends)
#   implements -> ``implements``  (ast_implements), whatever the source kind:
#                 Dart writes it ``implements`` from a class, an enum and a
#                 mixin alike, and none of those is an abstract-by-kind source
#   with       -> ``includes``    (ast_includes) — a mixin application, the
#                 relation Ruby's ``include`` already carries in this graph
#   on         -> ``extends``     (ast_extends) — a mixin's superclass
#                 constraint: ``super`` inside the mixin IS that type, and the
#                 mixin's members override its members
#
# ``extension E on T`` is NOT inheritance (static extension members), and a
# type-parameter bound (``class G<T extends Num>``) names no base; neither is
# read. A base is resolved over every class and mixin of the run: the
# candidate in the same file, else the only candidate, else the first by id
# flagged as an INV-zuhub fallback (confidence 0.5 +
# ``meta["disambiguation_fallback"]``) — the cascade
# ``inheritance.resolve_target_symbol`` applies. A base with no in-tree
# candidate and no in-tree symbol of that name keeps its relationship as an
# unresolved edge to the ``external`` sentinel module
# (``dart:external:0-0:<Name>:unresolved``), the shape that linker's WI-jubag
# Approach C mints for every other OO language; an in-tree name of another
# kind (a function, a typedef the analyzer does not emit as a type) is
# dropped rather than turned into a false external.

#: Declaration node -> the kind Pass 1 emits for it.
_DART_HERITAGE_DECLS: dict[str, str] = {
    "class_definition": "class",
    "mixin_declaration": "mixin",
    "enum_declaration": "enum",
}

#: Kinds a Dart heritage clause can name (a ``mixin class`` is kind class).
_DART_SUPERTYPE_KINDS = frozenset({"class", "mixin"})

#: Edge label -> evidence type.
_DART_HERITAGE_EVIDENCE: dict[str, str] = {
    "extends": "ast_extends",
    "implements": "ast_implements",
    "includes": "ast_includes",
}

#: Tokens that may continue a mixin's ``on`` clause.
_DART_TYPE_LIST_TOKENS = frozenset({"type_identifier", ".", ",", "type_arguments"})


@dataclass(frozen=True)
class _HeritageRef:
    """One base named in one heritage clause of one declared type."""

    src: Symbol
    base: str  # the type's own name: ``p.Mx<T>`` -> ``Mx``
    edge_type: str
    line: int


def _dart_type_list(
    nodes: Iterable["tree_sitter.Node"], source: bytes,
) -> list[tuple[str, int]]:
    """``(name, line)`` per comma-separated type in a heritage clause.

    The grammar lays a clause out flat — ``implements p . Other
    type_arguments , Shape`` — so a qualified type is consecutive
    ``type_identifier`` tokens joined by ``.``; its LAST one is the type.
    Type arguments are a nested node and name no base.
    """
    groups: list[list["tree_sitter.Node"]] = [[]]
    for node in nodes:
        if node.type == ",":
            groups.append([])
        elif node.type == "type_identifier":
            groups[-1].append(node)
    return [
        (node_text(group[-1], source), group[0].start_point[0] + 1)
        for group in groups
        if group
    ]


def _dart_mixin_on_clause(decl: "tree_sitter.Node") -> list["tree_sitter.Node"]:
    """The tokens of a mixin's ``on`` clause (the grammar does not wrap it)."""
    tokens: list["tree_sitter.Node"] = []
    in_clause = False
    for child in decl.children:
        if child.type == "on":
            in_clause = True
        elif in_clause and child.type in _DART_TYPE_LIST_TOKENS:
            tokens.append(child)
        elif in_clause:
            break
    return tokens


def _dart_heritage_clauses(
    decl: "tree_sitter.Node", source: bytes,
) -> list[tuple[str, str, int]]:
    """``(edge_type, base, line)`` for every base a type declaration names."""
    out: list[tuple[str, str, int]] = []

    def add(edge_type: str, nodes: Iterable["tree_sitter.Node"]) -> None:
        out.extend((edge_type, n, ln) for n, ln in _dart_type_list(nodes, source))

    for child in decl.children:
        if child.type == "superclass":
            add("extends", child.children)
            mixins = find_child_by_type(child, "mixins")
            if mixins is not None:
                add("includes", mixins.children)
        elif child.type == "mixins":
            add("includes", child.children)
        elif child.type == "interfaces":
            add("implements", child.children)
    if decl.type == "mixin_declaration":
        add("extends", _dart_mixin_on_clause(decl))
    return out


def _collect_heritage(
    tree: "tree_sitter.Tree", source: bytes, symbols: list[Symbol],
) -> list[_HeritageRef]:
    """Pass-1 record of every heritage clause, keyed to its declared symbol.

    The declaration is matched to its symbol by POSITION (``symbols_at``):
    Pass 1 spans a class/mixin/enum from its declaration node's start.
    """
    index = symbols_at(symbols)
    refs: list[_HeritageRef] = []
    for node in iter_tree(tree.root_node):
        kind = _DART_HERITAGE_DECLS.get(node.type)
        if kind is None:
            continue
        sym = symbol_declared_by(node, index)
        if sym is None or sym.kind != kind:
            # ``class A = B with M;`` (a mixin application class) gets no
            # symbol from Pass 1, so there is no source to hang its edges on.
            continue
        refs.extend(
            _HeritageRef(sym, base, edge_type, line)
            for edge_type, base, line in _dart_heritage_clauses(node, source)
        )
    return refs


def _pick_supertype(
    ref: _HeritageRef, candidates: list[Symbol],
) -> tuple[Optional[Symbol], bool]:
    """``(target, is_fallback)``: same file, else the only one, else first by id."""
    others = [c for c in candidates if c.id != ref.src.id]
    if not others:
        return None, False
    if len(others) == 1:
        return others[0], False
    same_file = [c for c in others if c.path == ref.src.path]
    if len(same_file) == 1:
        return same_file[0], False
    return min(others, key=lambda c: c.id), True


def _heritage_edges(
    refs: list[_HeritageRef], symbols: list[Symbol], run: "AnalysisRun",
) -> list[Edge]:
    """Resolve the run's heritage records into inheritance edges."""
    supertypes = SymbolByName()
    for sym in symbols:
        if sym.kind in _DART_SUPERTYPE_KINDS:
            supertypes.add(sym)
    intree_names = {s.name for s in symbols}

    edges: list[Edge] = []
    for ref in refs:
        target, is_fallback = _pick_supertype(ref, supertypes.lookup(ref.base))
        if target is not None:
            dst: str = target.id
        elif ref.base in intree_names:
            continue  # an in-tree name: not a supertype, and not an external
        else:
            dst = f"dart:external:0-0:{ref.base}:unresolved"
        edges.append(Edge.create(
            src=ref.src.id,
            dst=dst,
            edge_type=ref.edge_type,
            line=ref.line,
            confidence=0.5 if is_fallback else 0.95,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type=_DART_HERITAGE_EVIDENCE[ref.edge_type],
            is_resolved=target is not None,
            meta={"disambiguation_fallback": True} if is_fallback else None,
        ))
    return edges



class DartAnalyzer(TreeSitterAnalyzer):
    """Dart language analyzer using tree-sitter-language-pack."""

    lang = "dart"
    file_patterns: ClassVar[list[str]] = ["*.dart"]
    language_pack_name = "dart"
    create_file_symbols = True

    #: WI-lahub: heritage clauses recorded in Pass 1, resolved in
    #: ``post_process`` once every file's classes and mixins exist. A list only
    #: while ``analyze()`` runs (None outside it), so a run never replays the
    #: previous one's records. Per-run instance state, like the base class's
    #: ``_field_type_registry``: one analyzer instance serves one run at a time.
    _heritage_refs: Optional[list[_HeritageRef]] = None

    def analyze(
        self, repo_root: Path, max_files: Optional[int] = None,
    ) -> AnalysisResult:
        """The base two-pass run, bracketed by the heritage-record lifetime."""
        self._heritage_refs = []
        try:
            return super().analyze(repo_root, max_files)
        finally:
            self._heritage_refs = None

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract symbols from a Dart file, recording its heritage clauses."""
        analysis = FileAnalysis()
        symbols = _extract_symbols_from_file(tree, source, rel_path, run.execution_id)
        analysis.symbols = symbols
        for sym in symbols:
            analysis.symbol_by_name[sym.name] = sym
        if self._heritage_refs is not None:
            self._heritage_refs.extend(_collect_heritage(tree, source, symbols))
        return analysis

    def post_process(
        self,
        symbols: list[Symbol],
        edges: list[Edge],
        usage_contexts: list["UsageContext"],
        run: "AnalysisRun",
    ) -> tuple[list[Symbol], list[Edge], list["UsageContext"]]:
        """Resolve the run's heritage clauses into inheritance edges."""
        edges = edges + _heritage_edges(self._heritage_refs or [], symbols, run)
        return symbols, edges, usage_contexts

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract import hints for disambiguation."""
        return _extract_import_hints(tree, source)

    def register_symbol(self, symbol: Symbol, global_symbols: dict[str, Symbol]) -> None:
        """Keep field/variable symbols OUT of the call-resolution registry (WI-jusus).

        A ``field``/``variable`` is a data anchor, never a call or instantiation
        target. Registering them would let a bare-named top-level ``variable``
        clobber a same-named ``function``'s flat registry key (a false-negative
        the edge-site kind cannot recover) and let a suffix-matched ``field``
        shadow a real method — so both integrity vectors are closed at this one
        chokepoint rather than by gating every edge site. They remain in
        ``analysis.symbols`` (search / centrality / io-boundaries) because the
        output symbol set is built independently of this registry.
        """
        if symbol.kind in ("field", "variable"):
            return
        super().register_symbol(symbol, global_symbols)

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call, import, and instantiation edges from a Dart file."""
        # AMB-METHOD: build method resolver for ambiguity guard.
        # Dart's base register_symbol stores each symbol under its qualified
        # name only (no short-name duplicates), so no dedup needed.
        global_methods: dict[str, list[Symbol]] = {}
        for sym in global_symbols.values():
            if sym.kind == "method":
                short = sym.name.split(".")[-1] if "." in sym.name else sym.name
                global_methods.setdefault(short, []).append(sym)
        method_resolver = ListNameResolver(global_methods, ambiguity_threshold=3)

        file_symbols = list(local_symbols.values())
        return _extract_edges_from_file(
            tree, source, rel_path,
            file_symbols, resolver,
            run.execution_id, import_hints=import_aliases,
            method_resolver=method_resolver,
        )


_analyzer = DartAnalyzer()


def is_dart_tree_sitter_available() -> bool:
    """Check if tree-sitter with Dart grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("dart")
def analyze_dart(repo_root: Path) -> AnalysisResult:
    """Analyze Dart files in a repository.

    Returns an AnalysisResult with symbols, edges, and provenance.
    If tree-sitter-language-pack is not available, returns a skipped result.
    """
    return _analyzer.analyze(repo_root)
