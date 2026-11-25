# SPDX-License-Identifier: AGPL-3.0-or-later
"""Kotlin analysis pass using tree-sitter-kotlin.

This analyzer uses tree-sitter to parse Kotlin files and extract:
- Function declarations (fun)
- Class declarations (class, data class)
- Object declarations (object), whose stable_id is the ADR-0035 producer
  formula (``make_typed_stable_id`` over the package/class chain; WI-tusad).
  Methods, fields and enum entries hash the FULL class/object/function chain
  (``_kotlin_scope``: ``A.Inner.go``, not the emitted nearest-owner name
  ``Inner.go``), and classes and interfaces mint
  ``overload_arity.scoped_table_stable_id`` over it, so two nested
  ``class Inner`` in different outer classes do not share one bare-name table
  key and deleting one outer class does not move the other's ids (INV-fabih).
  For a top-level type and its members the chain adds nothing beyond the
  name already hashed. Every declaration that owns code labels the chain: a
  ``fun`` and a secondary constructor by name and hashed signature, a
  property, a setter, an ``init`` block and an enum entry each by what
  tells it from its siblings, a companion member's label starting with
  ``companion``, so the locals of two constructors, two overloads or two
  getters do not tie (they did until scheme v19, WI-finam).
- Interface declarations (interface)
- Method declarations (inside classes/objects)
- Annotations on classes, functions, methods, objects, and properties
  (meta.decorators), which
  also become ``decorated_by`` edges (``_extract_annotation_edges``)
- Property declarations: a ``field`` Symbol for a class/object/interface
  or enum-body property, a ``variable`` Symbol for a top-level one
- Enum entries, one ``field`` Symbol each, named ``Color.RED`` (WI-dorop)
- Function call relationships
- ``extends`` / ``implements`` inheritance edges
  (``_extract_inheritance_edges``)
- ``references`` edges from callable references (``::``) and navigation
- Import statements

If tree-sitter with Kotlin support is not installed, the analyzer
gracefully degrades: it emits a ``UserWarning`` and returns a skipped
result (``skip_reason_code=DEPENDENCY_UNAVAILABLE``).

How It Works
------------
1. Check if tree-sitter-kotlin is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry,
     then annotate each file's edges with dataflow (ADR-0015 Tier 1)
4. Detect function calls and import statements
5. Post-passes over the assembled result: inheritance edges, annotation
   edges, and the Gradle/Maven ``dependency_manifest`` from
   ``parse_jvm_dependencies``

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-kotlin package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as Go/Ruby/Rust/Elixir/Java/PHP/C analyzers for consistency

Receiver typing (WI-nasuf). An instance-method call on an external
receiver emits an edge that carries the receiver's DECLARED type rather
than the receiver variable's name: ``_kt_declared_type`` reads the
declaration, ``_kt_chain_root_constructor`` types a chain root bound to a
constructor call, and ``_kt_receiver_module`` turns that type into the
edge's ``module_hint`` (falling back to the ``external`` sentinel when it
cannot). ``receiver_type_hint`` carries the project-local type key
alongside it. This is what lets a catalogued method-kind I/O primitive be
reached through a typed receiver — see ADR-0051 for why the module slot
must name the static owner path and never the variable. Function-scoped
``var_types`` (``ScopedVarTypes``) keeps a name from carrying its type into
another function: a local's declared, constructor, library-call and
return-type bindings and a parameter's type all close with the function that
binds them (INV-zabud). A constructor property and a property declared outside
every function are bound for the file.

Library return types (WI-jubim). A receiver that is the VALUE a library
call returns -- ``Runtime.getRuntime().exec(c)``, or ``val r =
Runtime.getRuntime()`` then ``r.exec(c)`` -- is typed by
``_library_call_type``, which folds a call chain over the library-signature
rows: java.yaml's, which kotlin reads through
``library_signatures.ROW_PARENTS``. The chain's root is a typed local, a type
named by the file's import or java.lang, an inline JDK path, or a
constructor; each row's fully-qualified answer is the owner of the next link,
and the first link no row answers ends the walk untyped. A local bound to
such a chain takes the row's answer ahead of the chain-root constructor.

Synthetic getters. A property read that is Kotlin's spelling of a Java
getter (``p.inputStream`` for ``getInputStream()``, ``.isEmpty`` for
``isEmpty()``) emits an unresolved ``call_construct: method`` edge to that
getter, the owner in the module slot (``_synthetic_getter``), when the file
establishes the receiver's owner -- a typed local, a library call chain, or
another such getter -- and a library-signature row says that owner declares
it; a field or a Kotlin property adds nothing.

Other receiver shapes. An untyped receiver spelled as a TYPE
(``System.getenv(k)``, ``Files.readAllBytes(p)``) is a static call whose
module slot is the owner that ``jvm_implicit_imports.static_owner_module``
names (the file's import, or java.lang minus ``KOTLIN_SHADOWED_JAVA_LANG``;
a project type keeps the placeholder). A navigation-chain receiver goes
through ``_emit_navigation_receiver_call``: ``this.field.m()`` uses the
field's type, an inline fully-qualified path under
``_KOTLIN_INLINE_QUALIFIED_ROOTS`` names its owner via
``inline_qualified_owner``, and a path of names ending in a type names the
owner the call is on. The enclosing class of a ``super.m()``, ``this.m()``,
``::m`` or ``this::m`` is the one whose declaration contains the call, by
POSITION, and its own member is this file's declaration at ``<class
qualified name>.m`` (``_own_member``); a base class resolves same-file,
import, same package, then by id (``_resolve_base_class_kotlin``). Position
and path are used because the run-wide name-keyed table keeps one ``Foo`` and
one ``Foo.m`` across every package's ``Foo`` (WI-gikuk).

Which declaration a call means (ADR-0064). This analyzer binds a call only
where the language's own evidence is in hand: a bare ``m()`` or ``this.m()``
whose enclosing type declares ``m`` (found by position). Every other call is
REPORTED, with the facts the call site has, and the shared linkers decide
through each file's import table (``_kotlin_import_scope``, handed over in
``AnalysisResult.import_scopes``) and the caller's package:

* a bare ``helper()`` or a construction ``Request(url)`` (Kotlin writes a
  construction as a call): a bare stub, with ``enclosing_class`` when it is
  written inside a type (the inherited-calls linker walks the type's
  ancestors, then looks the name up among the functions AND types visible
  from the file); a construction of a nested or qualified type
  (``Request.Builder()``, ``okhttp3.Request(..)``) is a bare stub of the type
  as written;
* ``x.m()`` on a receiver whose type the file establishes (a parameter, a
  declared or constructed local, a property, a chained construction, a
  library call's row, a bound callee's declared return type):
  ``receiver_type_hint``, the type as written;
* ``Type.m()`` / ``Outer.Inner.m()``: ``qualifier``, the owner as written;
* ``this.m()`` the type does not declare: ``enclosing_class``; ``super.m()``:
  ``enclosing_class`` and ``super_receiver``;
* a receiver nobody can type: no fact. The single-holder name linker may bind
  it to the repository's ONE method of that name, unless the name is one the
  standard library defines on its types (``stdlib_member_name``, the screen
  :data:`_KOTLIN_LIBRARY_MEMBER_NAMES`).

Nothing here looks a callee up by its name among the run's declarations any
more: the run-wide name table, the same-file name table and the explicit
import's re-check of their answer (WI-tipoh) are the import table's work now.
What is lost with them: a variable initialised by a call the linkers bind
(``val r = Factory.make()``) is no longer typed by that callee's declared
return type, except where this file declares the callee as a top-level
function or as a member of the enclosing type.

The process's own standard streams (WI-rabum). ``System.err.println(k)`` is
a call on the ``java.io.PrintStream`` the field declares, emitted with that
type in the module slot and ``io_target_kind: std_stream``, which java.yaml's
PrintStream rows require (``requires_target_kind``). ``System.out`` /
``.err`` / ``.in`` read as values become ``module_attr_ref`` edges through the
shared ``emit_module_attribute_refs``, matching ``java.lang.System``'s
attribute rows (logging; ipc_recv) and naming the call that uses the stream
(``attr_carrier``). The call half follows java's rule (WI-dorus)
through ``jvm_implicit_imports``: a bare ``System`` counts only when no import,
project type or local renames it. The value-read half checks only imports and
project types, because it runs after the walk, when no scope is open to ask
about locals.

Call sources. A call that sits in no function (a top-level ``val``
initialiser or lambda, a ``by lazy`` block, a class or object body) is drawn
from the enclosing class or object declaration (``_TYPE_BODY_NODES``; a
``companion object`` has no symbol of its own, so its body's calls are drawn
from the class) and, failing that, from the file anchor symbol.

Extension functions (WI-fuhav / WI-lodij). ``fun Receiver.name()`` records
``meta.extension_receiver``. A call ``recv.name()`` on a receiver of that
type is emitted UNRESOLVED with ``receiver_type_hint``; the shared
``receiver_type_dispatch`` linker emits the resolved ``ast_call_extension``
edge. A reference ``Receiver::name`` that names no member of a type
``Receiver`` resolves to the extension function ``name`` declared on
``Receiver`` when exactly one is (a ``references`` edge, evidence
``callable_reference``): Ktor's ``module = Application::module`` is how a
server is told which module to run. The index of extensions by receiver is
built from every file's symbols, since the one-per-name ``global_symbols``
would let a same-named plain function hide an extension.

Grammar repair (WI-tusad; WI-rufub). tree-sitter-kotlin 1.1.0 turns a body
whose last member shares the line of its closing brace into one ERROR node
when it is nested (``class Inner { fun go() = 1 }`` inside a class) or when a
second such declaration follows (``interface Shape { fun area(): Int }`` then
``class Square : Shape { ... }``), which loses the declarations around it.
``_parse_kotlin`` retries such a parse on a same-length copy with a ``;``
before that brace (``_close_brace_repair``) and keeps whichever tree has fewer
errors; positions are unchanged, so node text still comes from the original
bytes. The shape is legal Kotlin, though rare outside test strings.

Signature text and the typed stable_id. A function's signature is
``(name: Type, ...): Return`` with every type written as its source text,
whatever node kind it is (``_KT_TYPE_SLOT_NODES``: a parenthesized type,
``dynamic`` and a definitely-non-null ``T & Any`` included) and a vararg
parameter written ``vararg name: Type``. ``normalize_kotlin_signature`` hashes
that text through the shared names-first normalizer (names dropped, FQN
prefixes stripped) with two Kotlin additions: ``vararg`` is kept in front of
the parameter's type, and an extension function's receiver is written in
front, ``String.()Int``. The function's own type-parameter clause is hashed
too (``normalize_with_type_parameters``, through ``_kotlin_callable_hashed``):
each type parameter is written as its position and the clause, bounds
included, goes in front, so ``fun <T : Number> f(x: T): T`` hashes
``<$0 : Number>($0)$0`` and renaming ``T`` moves no id. A function type's parameter names are left out of
every type hashed, a property's and a receiver's too
(``_kt_without_function_type_names``: ``(x: Int) -> Unit`` reads ``(Int) ->
Unit``; until scheme v17 renaming one moved the id). ``suspend`` is not in the text; it is the
``:async`` slot of ``make_typed_stable_id``. A class property hashes its
declared type as written, of any kind, preceded by ``Receiver.`` when it is
an extension property. A renderer that skipped a type kind made two
declarations that differ only in that type share one id.

Population of ``is_exported`` follows Kotlin's default-public rule: a
declaration is exported unless its modifier list contains ``private``,
``internal``, or ``protected``. Extension functions are unconditionally
treated as exported (see the ``receiver_type`` branch).

OVERLOADS BY ARGUMENT COUNT (WI-tosut). Every ``fun`` symbol (function,
method, local function, abstract interface member) declares its parameters in
``meta["parameters"]``, one ``overload_arity.parameter_entry`` each, read
from its ``function_value_parameters`` node (``_kt_parameters_meta``; never
from ``signature``, ADR-0058): ``p: Int = 0`` has ``default``, ``vararg xs:
T`` is ``variadic``, an extension's receiver is not a parameter. A list that
does not parse writes no key (unknown arity, never excluded). Constructors
have no symbol of their own (the primary one's parameters sit on the class
declaration, a secondary one emits nothing), so a class declares none and is
never narrowed. Every stub a call makes carries ``argument_count``
(``_kt_argument_count``: a trailing lambda is one, named arguments count, a
spread makes it unknown), which the shared linkers read to leave out the
overloads that cannot take it. The bind made here, the enclosing type's own
member, chooses the same way: of the overloads the count admits, the one
declared last, the one taken before the count was read (``_own_member``).

IMPORTED TYPE NAMES (stable-id scheme v17, WI-palus). Every type a typed
stable id hashes is written through the file's imports (an alias as what it names) and the default imports
(``_kotlin_type_names``, read once per file and set with ``resolving_type_names``), so an
imported and a qualified spelling of one type hash alike; a name nothing
binds, or the file declares, is hashed as written.
"""
from __future__ import annotations

import re
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Final, Iterator, Optional

from hypergumbo_core.dataflow import annotate_dataflow as _annotate_dataflow, get_dataflow_config as _get_dataflow_config
from hypergumbo_core.discovery import find_files
from hypergumbo_core.library_signatures import load_library_signatures
from hypergumbo_core.import_scope import ImportScope
from hypergumbo_core.type_visibility import visible_types
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.overload_arity import narrow_by_arity, parameter_entry, scoped_table_stable_id
from hypergumbo_core.paths import normalize_path
from hypergumbo_core.qualified_name_axis import separator_for_language
from hypergumbo_core import resolved_by
from hypergumbo_core.symbol_resolution import LookupResult, NameResolver
from hypergumbo_core.analyze.base import (
    normalize_with_type_parameters,
    AnalysisResult,
    SymbolsAt,
    TreeSitterAnalyzer,
    ancestor_labels,
    populate_docstrings_from_tree,
    find_child_by_type,
    iter_tree,
    layout_free,
    file_anchor_symbol,
    emit_module_attribute_refs,
    enclosing_declared_symbol,
    make_file_id,
    record_partial_parse,
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
    resolving_type_names,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.var_scope import ScopedVarTypes
from hypergumbo_lang_mainstream.import_bound_bases import external_base_edge, external_import_fqn
from hypergumbo_lang_mainstream.jvm_implicit_imports import (
    JAVA_LANG_SYSTEM_FIELDS,
    KOTLIN_SHADOWED_JAVA_LANG,
    PRINT_STREAM_TYPE,
    STD_STREAM_TARGET_KIND,
    bare_system_is_java_lang,
    imported_elsewhere,
    inline_qualified_owner,
    is_known_library_method,
    is_std_output_receiver,
    java_lang_system_aliases,
    kotlin_default_spellings,
    kotlin_import_path,
    static_owner_module,
)
from hypergumbo_lang_mainstream.symbol_introspection import (
    compute_cyclomatic_complexity,
    extract_preceding_doc_comment,
)
from hypergumbo_core.pass_silence import DEPENDENCY_UNAVAILABLE, PASS_CRASHED

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("kotlin")

#: Declarations whose BODY anchors a call that sits in no function (INV-bamij).
_TYPE_BODY_NODES: frozenset[str] = frozenset({"class_declaration", "object_declaration", "companion_object"})

#: The declarations whose ``base_classes`` a ``super.m()`` inside them walks
#: (found by position; INV-dupol, WI-gikuk).
_SUPER_OWNER_NODES: frozenset[str] = frozenset({"class_declaration", "object_declaration"})

#: The kinds of a Kotlin type declaration: what a receiver's type, a
#: qualifier or a construction names.
_KT_TYPE_KINDS: frozenset[str] = frozenset({"class", "interface", "object"})

#: What a call written ``m()`` or ``this.m()`` binds to when the enclosing type
#: itself declares ``m``: a method, or a property holding a function. A nested
#: type ``Inner()`` is a construction, which the linker finds through the
#: caller's enclosing scopes like any other type.
_OWN_MEMBER_KINDS: frozenset[str] = frozenset({"method", "field"})


def find_kotlin_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Kotlin files in the repository."""
    yield from find_files(repo_root, ["*.kt"])


def _is_kotlin_tree_sitter_available_legacy() -> bool:  # pragma: no cover - replaced by TreeSitterAnalyzer
    """Legacy availability check -- replaced by TreeSitterAnalyzer._check_grammar_available."""
    pass


def _find_child_by_field(node: "tree_sitter.Node", field_name: str) -> Optional["tree_sitter.Node"]:
    """Find child by field name."""
    return node.child_by_field_name(field_name)


def _extract_kotlin_receiver_type(
    func_node: "tree_sitter.Node",
    source: bytes,
) -> Optional[str]:
    """Return the receiver type name if *func_node* is an extension function.

    WI-fuhav: tree-sitter-kotlin parses ``fun Receiver.name(...) { }``
    as a ``function_declaration`` whose children run as
    ``[fun, user_type, '.', identifier, function_value_parameters, ...]``.
    The ``user_type`` child sits BEFORE the ``identifier`` that carries
    the function name — this is the distinguishing marker of an
    extension function. Regular functions (``fun name() {}``) have
    no receiver sibling.

    Returns the text of the receiver user_type when detected, or
    None for plain (non-extension) functions.
    """
    saw_user_type = False
    receiver_text: Optional[str] = None
    for child in func_node.children:
        if child.type in ("simple_identifier", "identifier") and saw_user_type:
            # We reached the function name after already seeing the
            # receiver user_type — confirmed extension function.
            return receiver_text
        if child.type == "user_type":
            # Record (but don't commit yet — the function_declaration
            # body may contain user_type nodes further down that
            # aren't receivers). We only commit if the NEXT identifier
            # arrives before any structural block starts.
            saw_user_type = True
            receiver_text = (
                child.text.decode("utf-8", errors="replace")
                if child.text else None
            )
        elif child.type in (
            "function_value_parameters",
            "function_body",
            "type_parameters",
            "modifiers",
            "{",
        ):
            # These sit after the name — if we hit one before confirming
            # the name, there was no preceding receiver.
            return None
    return None  # pragma: no cover - defensive fallback for malformed AST


# Kotlin modifier keyword types grouped by category.
# tree-sitter-kotlin wraps each in a typed node (visibility_modifier,
# inheritance_modifier, etc.) whose single child is the keyword.
KOTLIN_MODIFIER_KEYWORDS = {
    # visibility
    "public", "private", "protected", "internal",
    # inheritance
    "open", "final", "abstract", "sealed",
    # member
    "override", "lateinit",
    # class
    "data", "inner", "enum", "annotation", "value",
    # function
    "inline", "tailrec", "operator", "infix", "suspend",
}

# Node types that wrap modifier keywords
_KOTLIN_MODIFIER_NODE_TYPES = {
    "visibility_modifier", "inheritance_modifier", "member_modifier",
    "class_modifier", "function_modifier", "type_modifier",
    "property_modifier", "parameter_modifier",
}


def _extract_modifiers(node: "tree_sitter.Node") -> list[str]:
    """Extract all modifiers from a Kotlin declaration node.

    Kotlin tree-sitter groups modifiers under a ``modifiers`` container.
    Each child is a typed wrapper (e.g. ``visibility_modifier``) whose
    single child is the keyword token (e.g. ``public``).

    Returns a list of modifier strings like ``["public", "open"]``.
    """
    modifiers: list[str] = []
    for child in node.children:
        if child.type == "modifiers":
            for mod_node in child.children:
                if mod_node.type in _KOTLIN_MODIFIER_NODE_TYPES:
                    for kw in mod_node.children:
                        if kw.type in KOTLIN_MODIFIER_KEYWORDS:
                            modifiers.append(kw.type)
    return modifiers


def _kotlin_value_to_python(
    node: "tree_sitter.Node", source: bytes,
) -> str | int | float | bool | list | None:
    """Convert a Kotlin tree-sitter AST value node to a Python representation.

    Handles strings, numbers, booleans, identifiers, and arrays.
    Mirrors Java's ``_java_value_to_python`` for consistency.
    """
    if node.type == "string_literal":
        # Extract content from between quotes
        for child in node.children:
            if child.type == "string_content":
                return node_text(child, source)
        # Fallback: strip quotes (string_content always present in tree-sitter-kotlin)
        return node_text(node, source).strip('"')  # pragma: no cover - defensive
    elif node.type == "number_literal":
        # tree-sitter-kotlin uses "number_literal" for integers and hex
        text = node_text(node, source)
        try:
            if text.startswith(("0x", "0X")):
                return int(text, 16)
            return int(text.rstrip("lL"))
        except ValueError:  # pragma: no cover - defensive
            return text
    elif node.type == "float_literal":
        # tree-sitter-kotlin uses "float_literal" for decimal floats
        text = node_text(node, source)
        try:
            return float(text.rstrip("fFdD"))
        except ValueError:  # pragma: no cover - defensive
            return text
    elif node.type == "identifier":
        # tree-sitter-kotlin parses true/false as identifiers
        text = node_text(node, source)
        if text == "true":
            return True
        elif text == "false":
            return False
        return text
    elif node.type == "navigation_expression":
        # Handle qualified references like Enum.VALUE
        return node_text(node, source)
    elif node.type == "collection_literal":  # pragma: no cover - rare in annotation values
        # Handle array literals: [value1, value2]
        result: list[object] = []
        for child in node.children:
            if child.type not in ("[", "]", ","):
                result.append(_kotlin_value_to_python(child, source))
        return result
    # Fallback: return text representation
    return node_text(node, source)  # pragma: no cover - defensive


def _extract_annotation_info(
    annotation_node: "tree_sitter.Node", source: bytes,
) -> dict[str, object]:
    """Extract full annotation information from a Kotlin annotation AST node.

    Kotlin annotations have two forms in tree-sitter:
    1. Simple: ``annotation → @ + user_type`` (e.g., ``@Entity``)
    2. With args: ``annotation → @ + constructor_invocation → user_type + value_arguments``

    Returns a dict with:
    - name: annotation name (e.g., "Entity", "Table")
    - args: list of positional arguments
    - kwargs: dict of keyword arguments (name=value pairs)
    """
    name = ""
    args: list[object] = []
    kwargs: dict[str, object] = {}

    for child in annotation_node.children:
        if child.type == "user_type":
            # Simple annotation: @Entity
            name = node_text(child, source)
        elif child.type == "constructor_invocation":
            # Annotation with arguments: @Table(name = "users")
            for ci_child in child.children:
                if ci_child.type == "user_type":
                    name = node_text(ci_child, source)
                elif ci_child.type == "value_arguments":
                    for va_child in ci_child.children:
                        if va_child.type != "value_argument":
                            continue
                        # Check if this is a named or positional argument.
                        # Named args have: identifier = value
                        # Positional args have: value only
                        # We track the = sign to distinguish name from value
                        # since both can be "identifier" nodes.
                        has_eq = any(p.type == "=" for p in va_child.children)
                        arg_name: str | None = None
                        arg_value: object = None
                        found_eq = False
                        for part in va_child.children:
                            if part.type == "=" and has_eq:
                                found_eq = True
                            elif has_eq and not found_eq and part.type == "identifier":
                                # Before =: this is the parameter name
                                arg_name = node_text(part, source)
                            elif has_eq and found_eq:
                                # After =: this is the value
                                arg_value = _kotlin_value_to_python(part, source)
                            else:
                                # No = sign: positional argument
                                arg_value = _kotlin_value_to_python(part, source)
                        if has_eq and arg_name is not None:
                            kwargs[arg_name] = arg_value
                        elif arg_value is not None:
                            args.append(arg_value)

    return {"name": name, "args": args, "kwargs": kwargs}


def _extract_annotations(
    node: "tree_sitter.Node", source: bytes,
) -> list[dict[str, object]]:
    """Extract all annotations from a Kotlin declaration node.

    Annotations appear inside the ``modifiers`` container alongside
    visibility/inheritance modifiers. Each annotation is an ``annotation``
    node type.

    Returns list of annotation info dicts: [{"name": str, "args": list, "kwargs": dict}]
    """
    decorators: list[dict[str, object]] = []

    for child in node.children:
        if child.type == "modifiers":
            for mod_child in child.children:
                if mod_child.type == "annotation":
                    dec_info = _extract_annotation_info(mod_child, source)
                    if dec_info["name"]:
                        decorators.append(dec_info)

    return decorators


def _get_enclosing_class(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up the tree to find the enclosing class/object/interface name."""
    current = node.parent
    while current is not None:
        if current.type in ("class_declaration", "object_declaration"):
            name_node = _find_child_by_field(current, "name")
            if not name_node:  # pragma: no cover - defensive fallback
                name_node = find_child_by_type(current, "identifier")
                if not name_node:
                    name_node = find_child_by_type(current, "type_identifier")
            if name_node:
                return node_text(name_node, source)
        current = current.parent
    return None  # pragma: no cover - defensive


def _get_kotlin_class_ancestors(
    node: "tree_sitter.Node", source: bytes
) -> list[str]:
    """Walk up the tree collecting all enclosing class/object names.

    Returns the chain from outermost to innermost (excluding the current
    node itself).
    """
    chain: list[str] = []
    current = node.parent
    while current is not None:
        if current.type in ("class_declaration", "object_declaration"):
            name_node = _find_child_by_field(current, "name")
            if not name_node:  # pragma: no cover - defensive fallback
                name_node = find_child_by_type(current, "identifier")
                if not name_node:
                    name_node = find_child_by_type(current, "type_identifier")
            if name_node:
                chain.append(node_text(name_node, source))
        current = current.parent
    return list(reversed(chain))


def _extract_kotlin_package(
    root: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract the Kotlin package from the ``package_header`` declaration.

    Returns ``None`` for files without a package header (the default
    unnamed package).
    """
    for child in root.children:
        if child.type == "package_header":
            for sub in child.children:
                if sub.type in ("identifier", "qualified_identifier"):
                    text = node_text(sub, source)
                    if text and text != "package":
                        return text
    return None


def _make_kotlin_qualified_name(
    package: Optional[str], ancestors: list[str], name: str
) -> str:
    """Build a Kotlin qualified name: ``package.ClassChain.symbol_name``."""
    sep = separator_for_language("kotlin")  # "."
    parts: list[str] = []
    if package:
        parts.append(package)
    parts.extend(ancestors)
    parts.append(name)
    return sep.join(parts)


#: Type declarations whose name labels a scope a Kotlin member is nested in
#: (an anonymous ``object : T { }`` literal names none and is not listed).
_KOTLIN_TYPE_SCOPE_NODES: Final = frozenset({"class_declaration", "object_declaration"})

#: Scope labels of the code-owning declarations that have no name of their
#: own. Neither can be a function's label: a name holds no ``{`` and never
#: starts with ``<`` (a backquoted name keeps its backquotes), so ``init {}``
#: is not ``fun init()`` and ``<init>(Int)`` is not ``fun constructor(a:
#: Int)``. ``<init>`` is the JVM's name for a constructor.
_KOTLIN_INIT_BLOCK_LABEL: Final = "init {}"
_KOTLIN_CONSTRUCTOR_LABEL: Final = "<init>"


def _kotlin_callable_hashed(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """What a ``fun``'s (or a secondary constructor's) typed stable_id hashes
    beside its name and scope: its normalized signature, receiver in front
    (``String.(Int)``), or ``None`` when the signature does not normalize.

    The one reader for the function's own id and for its label in the scope
    chain of what it declares (:func:`_kotlin_callable_label`), so the two
    cannot disagree.
    """
    receiver_node = _kt_receiver_type_node(node)
    return normalize_with_type_parameters(
        normalize_kotlin_signature, _extract_kotlin_signature(node, source), node, source,
        receiver=node_text(receiver_node, source) if receiver_node is not None else None,
    )


def _kotlin_callable_label(node: "tree_sitter.Node", source: bytes) -> str:
    """A ``fun``'s or secondary constructor's label in the scope chain
    (:func:`_kotlin_scope`): its name and what its own id hashes beside it
    (:func:`_kotlin_callable_hashed`), canonically spaced (``layout_free``):
    ``f(Int)``, ``f(vararg Int)``, ``ext String.(Int)``, ``<init>(String)``.

    A name alone is shared by two overloads, by an extension function on
    two receivers, and by a companion object's ``fun`` and its class's; the
    declarations local to them tied (WI-finam, the defect WI-podor fixed for
    Swift). A companion member's label starts with ``companion``
    (:func:`_kotlin_companion_word`), as a Swift ``static`` one starts with
    ``static``. A ``fun`` whose hashed text is ``()`` and that is no
    companion member keeps its bare name, so what it declares hashes what it
    did; a name followed by ``(`` or by a space is never a bare name, so the
    spellings cannot meet. A constructor has no name of its own: it is
    ``<init>`` followed by its hashed text, ``()`` included.
    """
    text = layout_free(_kotlin_callable_hashed(node, source) or "", separators=True)
    if node.type == "secondary_constructor":
        return _KOTLIN_CONSTRUCTOR_LABEL + (text or "()")
    name_node = _find_child_by_field(node, "name")
    name = node_text(name_node, source) if name_node is not None else ""
    if text not in ("", "()"):
        # A receiver's type starts with a word, which would run into the name.
        name = name + text if text.startswith("(") else f"{name} {text}"
    return _kotlin_companion_word(node) + name


def _kotlin_companion_word(node: "tree_sitter.Node") -> str:
    """``companion `` for a declaration in a companion object's body, else
    ``""``: the word that tells a companion member's label from the label of
    the same-named member of its class.

    A word in the member's LABEL, not a label of the companion object itself:
    so what a companion ``fun f()`` declares is not what the class's ``fun
    f()`` declares, while the companion members' own ids hash what they did.
    (A companion member and a same-named member of its class still tie with
    each other: their chain is the class's in both; a label for the companion
    object would split them and move every companion member's id.)
    """
    body = node.parent
    if body is None or body.type != "class_body" or body.parent is None:
        return ""
    return "companion " if body.parent.type == "companion_object" else ""


def _kotlin_property_label(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """A ``property_declaration``'s label in the scope chain: ``val p`` /
    ``var p``, an extension property's receiver before its name (``val
    String.p``, written as the property's own id writes it); ``None`` for a
    destructuring declaration, which binds no single name.

    A getter, a setter and an initialiser (a lambda, an ``object`` literal)
    are code the property owns, so what they declare is scoped by it: two
    getters' local ``fun g()`` tied, as did two lambdas bound to ``val a``
    and ``val b``. The keyword makes a property's label two words, which no
    function's label is; a companion member's starts with ``companion``.
    """
    var_decl = find_child_by_type(node, "variable_declaration")
    name_node = find_child_by_type(var_decl, "identifier") if var_decl is not None else None
    if name_node is None:
        return None
    keyword = next((c.type for c in node.children if c.type in ("val", "var")), "val")
    receiver = _kt_receiver_type_node(node)
    name = node_text(name_node, source)
    if receiver is not None:
        written = canonical_type_names(
            _kt_without_function_type_names(node_text(receiver, source)))
        name = f"{written}.{name}"
    return layout_free(f"{_kotlin_companion_word(node)}{keyword} {name}")


def _kotlin_scope(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """The labels of the declarations enclosing ``node``, outermost first:
    what a member's and a nested type's stable_id hash beside its own name
    (INV-fabih).

    The emitted name keeps the nearest owner (``Inner.go``), which two nested
    ``class Inner`` in ``A`` and ``B`` share; the package is single per file,
    so the file anchor already identifies it and it is left out (a member of
    a top-level class hashes the same ``Class.member`` it emits).

    Every declaration that owns code is a label, by what identifies it among
    its siblings: a class or object by its name; an enum entry by its name;
    a ``fun`` or secondary constructor by :func:`_kotlin_callable_label`; an
    ``init`` block by ``init {}``; a property by
    :func:`_kotlin_property_label`; a setter by ``set`` (a getter adds
    nothing, like Swift's). Until scheme v19 only classes, objects and
    ``fun`` names were labels, so a function local to a constructor, an
    accessor, an ``init`` block or a property's lambda was scoped by its
    class, and two same-named locals of two constructors, two overloads, two
    getters, a getter and its setter, or a companion ``fun`` and its class's
    tied (WI-finam); so did the members two enum entries override. A
    lambda, an ``object`` literal and a block have no name and add nothing:
    two same-named locals of two unbound lambdas, or of two ``init`` blocks,
    still share a chain (split by occurrence order). A companion object is
    no label (:func:`_kotlin_companion_word`).
    """
    def label(n: "tree_sitter.Node") -> Optional[str]:
        if n.type in ("function_declaration", "secondary_constructor"):
            return _kotlin_callable_label(n, source)
        if n.type == "property_declaration":
            return _kotlin_property_label(n, source)
        if n.type == "anonymous_initializer":
            return _KOTLIN_INIT_BLOCK_LABEL
        if n.type == "setter":
            return "set"
        if n.type == "enum_entry":
            name_node = find_child_by_type(n, "identifier")
        elif n.type in _KOTLIN_TYPE_SCOPE_NODES:
            name_node = _find_child_by_field(n, "name")
        else:
            return None
        return node_text(name_node, source) if name_node is not None else None
    return ancestor_labels(node, label)


def _close_brace_repair(source: bytes) -> bytes:
    """``source`` with the blank before each same-line closing ``}`` turned
    into ``;``: same length, same line and column for every byte.

    tree-sitter-kotlin 1.1.0 cannot parse a NESTED body whose last member
    ends on the line of its closing brace (``class A {`` / ``class Inner {
    fun go(): Int = 1 }`` / ``}``, ``companion object { val x = 1 }``, an
    ``object { ... }`` literal in a function): the whole enclosing
    declaration becomes an ERROR node, and without the repair the analyzer
    sees only the stray ``function go`` inside it, dropping ``A``, ``Inner``
    and everything after. A ``;`` before the brace is a legal statement/member
    separator and the grammar accepts it. Only a ``}`` in code (not in a
    comment, string, character literal or ``${}`` template) that follows
    other code on its line, not an opening ``{``, and a blank is rewritten.
    """
    out = bytearray(source)
    n = len(source)
    i = 0
    # [mode, brace depth, is a ${...} template]; mode is code, str or raw.
    stack: list[list[Any]] = [["code", 0, False]]
    line_has_code = False
    last_code = 0
    while i < n:
        c = source[i]
        top = stack[-1]
        if top[0] != "code":
            if top[0] == "str" and c == 0x5C:  # backslash escape
                i += 2
                continue
            if top[0] == "str" and c == 0x22:
                stack.pop()
                i += 1
                continue
            if top[0] == "raw" and source.startswith(b'"""', i):
                while i < n and source[i] == 0x22:  # a run of quotes closes at its end
                    i += 1
                stack.pop()
                continue
            if source.startswith(b"${", i):
                stack.append(["code", 0, True])
                i += 2
                continue
            line_has_code = line_has_code and c != 0x0A
            i += 1
            continue
        if c == 0x0A:
            line_has_code = False
        elif source.startswith(b"//", i):
            end = source.find(b"\n", i)
            i = n if end < 0 else end
            continue
        elif source.startswith(b"/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                step = source[i:i + 2]
                depth += 1 if step == b"/*" else -1 if step == b"*/" else 0
                i += 2 if step in (b"/*", b"*/") else 1
            continue
        elif c == 0x22:
            raw = source.startswith(b'"""', i)
            stack.append(["raw" if raw else "str", 0, False])
            i += 3 if raw else 1
            line_has_code, last_code = True, c
            continue
        elif c == 0x27:  # a character literal: skip it whole
            close = source.find(b"'", i + (3 if source[i + 1:i + 2] == b"\\" else 2))
            i = n if close < 0 else close + 1
            line_has_code, last_code = True, c
            continue
        elif c == 0x7B:
            top[1] += 1
        elif c == 0x7D:
            if top[2] and top[1] == 0:  # the end of a ${...} template
                stack.pop()
                i += 1
                continue
            top[1] -= 1
            if (not top[2] and line_has_code and last_code != 0x7B
                    and source[i - 1] in (0x20, 0x09)):
                out[i - 1] = 0x3B
        if c not in (0x0A, 0x20, 0x09, 0x0D):
            line_has_code, last_code = True, c
        i += 1
    return bytes(out)


def _error_count(tree: "tree_sitter.Tree") -> int:
    """The error sites in ``tree``: ERROR and MISSING nodes, and nodes flagged
    ``has_error`` with no child that is (the grammar can flag a body whose
    error node it does not expose). Descends only where errors are."""
    count = 0
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if not node.has_error:
            continue
        children = node.children
        if node.is_error or node.is_missing or not any(c.has_error for c in children):
            count += 1
        stack.extend(children)
    return count


def _parse_kotlin(parser: "tree_sitter.Parser", source: bytes) -> "tree_sitter.Tree":
    """Parse ``source``, retrying through :func:`_close_brace_repair` when the
    grammar reports an error; the repaired tree is kept only when it has fewer
    errors. Positions match ``source`` either way (the repair keeps every
    byte's offset, line and column), so node text is still read from the
    original bytes. Every parse of a Kotlin file goes through here, so the
    symbol, edge and dataflow passes see the same tree.
    """
    tree = parser.parse(source)
    if not tree.root_node.has_error:
        return tree
    repaired = parser.parse(_close_brace_repair(source))
    return repaired if _error_count(repaired) < _error_count(tree) else tree


def _get_enclosing_function(
    node: "tree_sitter.Node",
    source: bytes,
    declared: SymbolsAt,
) -> Optional[Symbol]:
    """The function or method whose declaration contains ``node``.

    Keyed by the declaration's POSITION, not its short name: one file can
    hold ``HttpUrl.parse`` and ``Builder.parse``, or one test name under two
    ``describe`` objects, and a name-keyed lookup returns whichever registered
    last, naming a src that does not contain the call (INV-midag). A
    ``function_declaration`` with no symbol (a local ``fun``) is walked past, to
    the function that does have one.
    """
    current = node.parent
    while current is not None:
        if current.type == "function_declaration":
            sym = symbol_declared_by(current, declared)
            if sym is not None:
                return sym
        current = current.parent
    return None  # pragma: no cover - defensive


# Kotlin body nodes that place a property_declaration in a *local* scope: a
# val/var here is a function/block/lambda-local binding, not an API surface, and
# is NOT emitted as a symbol (WI-jusus; the swift/go INV-lanaz/INV-sidab guard —
# without this a function-local ``val`` would leak into the graph as a module
# variable). ``block`` covers function bodies, ``if``/``for``/``while`` bodies AND
# ``init { }`` (anonymous_initializer) bodies — the last is the subtle case: an
# init-block ``val`` sits under ``block -> anonymous_initializer -> class_body``,
# so without ``block`` in this set it would wrongly reach ``class_body`` and be
# emitted as a field. The node-type strings target the analyzer's bundled
# tree-sitter-kotlin grammar (``identifier``/``block``, not the newer
# ``simple_identifier``/``control_structure_body``).
_KOTLIN_LOCAL_SCOPE_TYPES = frozenset(
    {"function_body", "block", "lambda_literal", "anonymous_function"}
)
# Body nodes that place a property_declaration in a *field* scope (class /
# object / interface / enum body).
_KOTLIN_FIELD_SCOPE_TYPES = frozenset({"class_body", "enum_class_body"})


def _kotlin_property_scope(node: "tree_sitter.Node") -> Optional[str]:
    """Classify a ``property_declaration`` by its nearest body-defining ancestor.

    Returns ``"field"`` (class/object/interface/enum body), ``"variable"``
    (top-level — reaches ``source_file`` first), or ``None`` for a function-/
    block-/lambda-local ``val``/``var``. The *nearest* body-defining ancestor
    wins, so a local inside a class method classifies as local, not field — this
    scope walk is the swift/go INV-lanaz/INV-sidab regression guard.
    """
    current = node.parent
    while current is not None:
        if current.type in _KOTLIN_LOCAL_SCOPE_TYPES:
            return None
        if current.type in _KOTLIN_FIELD_SCOPE_TYPES:
            return "field"
        if current.type == "source_file":
            return "variable"
        # Defensive walk-up for an intermediate wrapper node between the
        # property and its scope (e.g. a parser ``ERROR`` node in a malformed
        # parse). In well-formed parses of the bundled grammar a
        # property_declaration's immediate parent is always the scope node, so
        # this advance does not run.
        current = current.parent  # pragma: no cover - immediate parent always resolves
    return None  # pragma: no cover - every node is under source_file


#: Every node kind tree-sitter-kotlin 1.1.0 puts in a TYPE SLOT: a
#: parameter's type, a return type, an extension receiver, a property's
#: declared type. ``dynamic`` (Kotlin/JS) is an anonymous token, not a named
#: node. Annotations and ``suspend`` sit INSIDE these (a ``type_modifiers``
#: child), so the node's text carries them. The renderers read only the first
#: three until the PR 2 scheme bump and dropped the other three, with their
#: parameter, from the text a stable_id hashes.
_KT_TYPE_SLOT_NODES: Final[frozenset[str]] = frozenset({
    "user_type", "nullable_type", "function_type",
    "parenthesized_type", "non_nullable_type", "dynamic",
})

#: How a ``vararg`` parameter is written in the signature text, in front of
#: its name as in the source (``vararg x: Int``).
_KT_VARARG_PREFIX: Final[str] = "vararg "

#: What follows a function type's parameter list: its ``->``.
_KT_FUNCTION_ARROW_RE = re.compile(r"\s*->")
#: A function-type parameter's name and its ``:`` (``x: `` in ``(x: Int) ->
#: Unit``); a backquoted name too.
_KT_FUNCTION_PARAMETER_NAME_RE = re.compile(r"(?:`[^`]*`|[^\W\d]\w*)\s*:(?!:)\s*")


def _kt_without_function_type_names(text: str, *, declaration: bool = False) -> str:
    """``text`` (a type, or a signature when ``declaration``) with each
    function type's parameter names left out: ``(x: Int) -> Unit`` reads
    ``(Int) -> Unit``, at any depth (stable-id v17;
    :func:`~hypergumbo_core.analyze.base.drop_function_type_parameter_names`).
    A function type is a group its ``->`` follows; Kotlin has no tuple type,
    so no other group is followed by one."""
    from hypergumbo_core.analyze.base import drop_arrow_function_type_parameter_names

    return drop_arrow_function_type_parameter_names(
        text, _KT_FUNCTION_ARROW_RE, _KT_FUNCTION_PARAMETER_NAME_RE, declaration=declaration)


def _kt_is_vararg(parameter_modifiers: "tree_sitter.Node") -> bool:
    """True when a ``parameter_modifiers`` node carries ``vararg``.

    The grammar puts a parameter's modifiers in a SIBLING node that precedes
    the ``parameter`` inside ``function_value_parameters``, not inside it.
    """
    return any(
        kw.type == "vararg"
        for mod in parameter_modifiers.children
        if mod.type == "parameter_modifier"
        for kw in mod.children
    )


@dataclass(frozen=True)
class _KtParameter:
    """One parameter a ``function_value_parameters`` list declares, as written."""

    name: str | None
    type_text: str | None
    vararg: bool
    default: bool


def _kt_declared_parameters(
    parameters: "tree_sitter.Node", source: bytes,
) -> list[_KtParameter]:
    """The parameters of a ``function_value_parameters`` node, in order.

    The grammar writes what belongs to one parameter as SIBLINGS of its
    ``parameter`` node: the ``parameter_modifiers`` before it (``vararg``,
    annotations) and, for a default value, ``=`` and the expression after it.
    The name is the parameter's first identifier and the type its last type
    node (the one reading the signature text and the parameter list share).
    """
    found: list[_KtParameter] = []
    vararg = False
    for child in parameters.children:
        if child.type == "parameter_modifiers":
            vararg = _kt_is_vararg(child)
        elif child.type == "parameter":
            name: str | None = None
            type_text: str | None = None
            for part in child.children:
                if part.type == "identifier" and name is None:
                    name = node_text(part, source)
                elif part.type in _KT_TYPE_SLOT_NODES:
                    type_text = node_text(part, source)
            found.append(_KtParameter(name, type_text, vararg, False))
            vararg = False
        elif child.type == "=" and found:
            last = found[-1]
            found[-1] = _KtParameter(last.name, last.type_text, last.vararg, True)
    return found


def _kt_parameters_meta(
    decl: "tree_sitter.Node", source: bytes,
) -> list[dict[str, Any]] | None:
    """``meta["parameters"]`` of a ``fun`` (``overload_arity.parameter_entry``
    per declared parameter, the empty list for none): ``p: Int = 0`` has a
    default and ``vararg xs: T`` takes any number. An extension's receiver
    is not a parameter (``meta["extension_receiver"]``). ``None`` (the key is
    not written: the arity is unknown) when the list does not parse, since a
    parameter lost to an error would otherwise read as one the callable does
    not take."""
    parameters = find_child_by_type(decl, "function_value_parameters")
    if parameters is None or parameters.has_error:
        return None
    return [
        parameter_entry(p.name or "", p.type_text, default=p.default, variadic=p.vararg)
        for p in _kt_declared_parameters(parameters, source)
    ]


def _kt_argument_count(call: "tree_sitter.Node") -> int | None:
    """How many arguments a ``call_expression`` writes, or ``None`` when the
    count is not what the callee receives (a spread ``*xs``).

    Each ``value_argument`` is one, named or not (a comment in the list is
    not a ``value_argument``), and a trailing lambda is one more. The grammar
    hangs a trailing lambda written after parentheses (``f(a) { }``) on an
    OUTER ``call_expression`` whose callee is this call, and one written
    without them (``f { }``) on this call itself. An outer call with an
    argument list of its own (``h()(1) { }``) is a call of what ``h()``
    returns, and its lambda is not ``h``'s.
    """
    count = 0
    for child in call.children:
        if child.type == "annotated_lambda":
            count += 1
        elif child.type == "value_arguments":
            for argument in child.children:
                if argument.type != "value_argument":
                    continue
                if find_child_by_type(argument, "spread_expression") is not None:
                    return None
                count += 1
    outer = call.parent
    if outer is not None and outer.type == "call_expression" and outer.children[0] == call:
        rest = [c for c in outer.children[1:] if not c.is_extra]
        if rest and all(c.type == "annotated_lambda" for c in rest):
            count += len(rest)
    return count


def _kt_receiver_type_node(decl: "tree_sitter.Node") -> Optional["tree_sitter.Node"]:
    """The extension receiver's type node of a function or property, or ``None``.

    ``fun String?.f()`` and ``val List<T>.p`` write the receiver as a type node
    directly followed by a ``.`` token, ahead of the declared name; any of
    the type kinds can take that place (``fun ((Int) -> Unit).f()``,
    ``fun dynamic.f()``). Modifiers and type parameters may precede it.
    """
    for child in decl.children:
        if child.type in ("identifier", "variable_declaration", "function_value_parameters"):
            return None
        if child.type in _KT_TYPE_SLOT_NODES:
            nxt = child.next_sibling
            if nxt is not None and nxt.type == ".":
                return child
    return None  # pragma: no cover - defensive: a declaration with no name node


def _kt_declared_type_text(holder: "tree_sitter.Node", source: bytes) -> str | None:
    """Source text of the first type node directly under ``holder``, or ``None``."""
    for child in holder.children:
        if child.type in _KT_TYPE_SLOT_NODES:
            return node_text(child, source)
    return None


def _extract_kotlin_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a Kotlin function declaration.

    Returns signature like:
    - "(x: Int, y: Int): Int" for regular functions
    - "(message: String)" for Unit (void) functions
    - "(vararg xs: Int)" for a vararg parameter

    Every type node kind (``_KT_TYPE_SLOT_NODES``) is written as its source
    text; a parameter or return type of a kind with no branch used to vanish.
    The extension receiver is not part of this text (it is in
    ``meta.extension_receiver`` and in the hashed text, see
    ``normalize_kotlin_signature``). Default values, ``crossinline`` /
    ``noinline`` and parameter annotations are not written.

    Args:
        node: The function_declaration node.
        source: The source code bytes.

    Returns:
        The signature string, or None if extraction fails.
    """
    params: list[str] = []
    return_type = None
    found_params = False

    # Iterate through children to find parameters and return type
    for child in node.children:
        if child.type == "function_value_parameters":
            found_params = True
            for param in _kt_declared_parameters(child, source):
                if param.name and param.type_text:
                    prefix = _KT_VARARG_PREFIX if param.vararg else ""
                    params.append(f"{prefix}{param.name}: {param.type_text}")
        # Return type comes after function_value_parameters and before function_body
        elif found_params and child.type in _KT_TYPE_SLOT_NODES:
            return_type = node_text(child, source)

    params_str = ", ".join(params)
    signature = f"({params_str})"

    if return_type and return_type != "Unit":
        signature += f": {return_type}"

    return signature


def normalize_kotlin_signature(
    signature: str | None,
    type_params: list[str] | None = None,
    *,
    receiver: str | None = None,
) -> str | None:
    """Normalize a Kotlin signature for typed stable_id (spec §6 typed tier).

    The shared names-first normalizer keeps each parameter's text after its
    first ``:``, so it would drop the ``vararg`` written before the name.
    When a parameter is a vararg, each parameter is normalized on its own by
    that same normalizer and the vararg one is written ``vararg <Type>``:
    ``(a: Int, vararg b: kotlin.String)`` -> ``(Int,vararg String)``. A
    signature with no vararg parameter takes the shared result unchanged.

    ``receiver`` is an extension function's receiver type, written in front
    as Kotlin writes a receiver function type (``String.()Int``), FQN prefix
    stripped like a parameter type. A receiver-less text starts with ``(``,
    so the two cannot meet. Every type's other names are written through the
    file's imports (``normalize_type``, :func:`_kotlin_type_names`).

    Every type keeps a nested type's enclosing types (``keep_type_path``:
    a receiver ``Map.Entry<K, V>`` and ``Other.Entry<K, V>`` hash apart,
    as do parameters ``Foo.Bar`` and ``Baz.Bar``), and a function, ``suspend``
    or annotated type is written whole (the shared FQN strip no longer cuts
    them at their last dot).
    """
    from hypergumbo_core.analyze.base import (
        find_matching_paren,
        normalize_signature_names_first,
        normalize_type,
        split_params_top_level,
    )
    if signature is not None:
        signature = _kt_without_function_type_names(signature, declaration=True)
    norm = normalize_signature_names_first(
        signature, type_params, return_sep=":", keep_type_path=True)
    if norm is None or signature is None:
        return None
    close = find_matching_paren(signature, 0)
    params = split_params_top_level(signature[1:close])
    if any(p.startswith(_KT_VARARG_PREFIX) for p in params):
        types: list[str] = []
        for p in params:
            is_vararg = p.startswith(_KT_VARARG_PREFIX)
            if is_vararg:
                p = p[len(_KT_VARARG_PREFIX):]
            one = normalize_signature_names_first(
                f"({p})", type_params, return_sep=":", keep_type_path=True)
            types.append((_KT_VARARG_PREFIX if is_vararg else "") + (one or "()")[1:-1])
        tail = normalize_signature_names_first(
            "()" + signature[close + 1:], type_params, return_sep=":", keep_type_path=True,
        )
        norm = f"({','.join(types)}){(tail or '()')[2:]}"
    if receiver:
        written = normalize_type(
            _kt_without_function_type_names(receiver.strip()), type_params,
            keep_type_path=True)
        norm = f"{written}.{norm}"
    return norm


def _extract_kotlin_return_type_name(signature: str | None) -> str | None:
    """Extract the return type name from a Kotlin function signature.

    Kotlin signatures use the format ``(params): ReturnType``.
    Returns the type name only if it is a simple identifier; Tier 2
    (WI-jujup) strips the nullable ``?`` suffix so the underlying type
    still drives ``var_types`` inference at call sites. Returns None
    for ``Unit`` return type (which is already omitted from the
    signature by ``_extract_kotlin_signature``).

    Examples:
        ``"(): ServiceClient"`` → ``"ServiceClient"``
        ``"(name: String): User"`` → ``"User"``
        ``"(): User?"`` → ``"User"`` (WI-jujup nullable strip)
        ``"()"`` → ``None``
        ``"(): List<String>"`` → ``None``
    """
    if not signature:
        return None
    paren_idx = signature.rfind(")")
    if paren_idx < 0:
        return None  # pragma: no cover - all Kotlin signatures contain parens
    after_paren = signature[paren_idx + 1:].strip()
    if not after_paren.startswith(":"):
        return None
    ret_part = after_paren[1:].strip()
    # WI-jujup Tier 2 escape hatch: strip the nullable ``?`` suffix.
    # ``Foo?`` is structurally ``Foo``; downstream ``class_symbols``
    # lookup gates on the bare type anyway.
    if ret_part.endswith("?"):
        ret_part = ret_part[:-1].rstrip()
    if ret_part and ret_part.isidentifier():
        return ret_part
    return None


def _extract_param_types(
    node: "tree_sitter.Node", source: bytes
) -> dict[str, str]:
    """Extract parameter name -> type mapping from a function declaration.

    This enables type inference for method calls on parameters, e.g.:
        fun process(client: Client) {
            client.send()  // resolves to Client.send
        }

    Returns:
        Dict mapping parameter names to their type names (simple name only).
    """
    param_types: dict[str, str] = {}

    for child in node.children:
        if child.type == "function_value_parameters":
            for subchild in child.children:
                if subchild.type == "parameter":
                    param_name = None
                    param_type = None
                    for pc in subchild.children:
                        if pc.type == "identifier" and param_name is None:
                            param_name = node_text(pc, source)
                        elif pc.type in ("user_type", "nullable_type", "function_type"):
                            type_text = node_text(pc, source)
                            # Extract base type name (strip nullable ?, generics <>, etc.)
                            if type_text:
                                # Remove nullable suffix
                                type_text = type_text.rstrip("?")
                                # Remove generic parameters
                                if "<" in type_text:  # pragma: no cover
                                    type_text = type_text.split("<")[0]
                                param_type = type_text
                    if param_name and param_type:
                        param_types[param_name] = param_type

    return param_types


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file.

    Type inference tracks types from:
    - Constructor calls: val obj = MyClass() -> obj has type MyClass
    - Function parameters: fun process(client: Client) -> client has type Client

    - Function returns (val obj = getMyClass()), when the call resolves to a
      project function whose declared return type is a project class
    WI-nasuf: it also binds a DECLARED type (``val c: Connection = make()``), a bare or
    dotted constructor (``java.io.File(p)``), and a chained construction
    (``File(p).writeText(s)``); an instance-method call nothing resolves emits an
    unresolved edge whose module slot is the receiver's type qualified through the
    file's imports (or the ``external`` placeholder), stamped ``call_construct: method``.
    """

    symbols: list[Symbol] = field(default_factory=list)
    symbol_by_name: dict[str, Symbol] = field(default_factory=dict)
    imports: dict[str, str] = field(default_factory=dict)
    #: The file's ``package`` header, ``None`` for the default package. A
    #: base named without an import is looked up here first (WI-gikuk).
    package: Optional[str] = None
    #: The file's import table, handed to the linkers (ADR-0064;
    #: :func:`_kotlin_import_scope`).
    import_scope: ImportScope = field(default_factory=ImportScope)


def _kt_declared_type(type_node: "tree_sitter.Node | None", source: bytes) -> str | None:
    """The receiver-typing name a Kotlin ``user_type`` carries, or ``None``.

    WI-nasuf. ``List<String>`` names ``List``; ``java.io.File`` names itself;
    a nullable ``File?`` names ``File``. A function type (``() -> T``) is not
    a receiver type and yields ``None``.
    """
    if type_node is None or type_node.type != "user_type":
        return None
    text = node_text(type_node, source).strip()
    if "<" in text:
        text = text.split("<", 1)[0]
    text = text.rstrip("?").strip()
    return text or None


def _kt_constructor_type(callee: "tree_sitter.Node", source: bytes) -> str | None:
    """The type a constructor CALLEE names -- ``File`` or ``java.io.File`` -- or ``None``.

    A bare capitalised identifier is a constructor by Kotlin convention (the
    rule the ``property_declaration`` branch already applied); a dotted
    navigation whose last segment is capitalised is a qualified one. Anything
    else (``make()``, ``obj.build()``) is a call whose result this analyzer
    cannot type here.
    """
    if callee.type == "identifier":
        name = node_text(callee, source)
        return name if name[:1].isupper() else None
    if callee.type == "navigation_expression":
        # okhttp writes builder chains one segment per line
        # (``Request\n  .Builder()``); the segments are the evidence, not the
        # whitespace between them.
        text = "".join(node_text(callee, source).split())
        parts = text.split(".")
        if len(parts) >= 2 and parts[-1][:1].isupper() and all(
            p.isidentifier() for p in parts
        ):
            return text
    return None


def _kt_chain_root_constructor(call_expr: "tree_sitter.Node", source: bytes) -> str | None:
    """The constructor a call CHAIN is rooted at -- ``MockResponse().setBody(x)`` names
    ``MockResponse``, ``Cfg(..).subConfig("a").subConfig("b")`` names ``Cfg`` -- or ``None``.

    The local is typed from the chain's ROOT constructor rather than only when
    the outermost callee is a constructor: a builder chain returns its root's
    type often enough to be right at the okhttp and detekt sites checked, and
    no better evidence exists here (typing a builder's return type is
    WI-lalot's). (WI-nasuf)
    """
    node: "tree_sitter.Node | None" = call_expr
    depth = 0
    while node is not None and node.type == "call_expression":
        callee: "tree_sitter.Node | None" = next(
            (c for c in node.children if c.type in ("identifier", "navigation_expression")),
            None,
        )
        if callee is None:
            return None
        found = _kt_constructor_type(callee, source)
        if found is not None:
            # A DOTTED constructor at the root of a LONGER chain --
            # ``Request.Builder().url(u).build()`` -- types the local as the
            # OUTER class (``Request``), which is what
            # ``.build()`` returns; the precise ``Request.Builder`` is right only
            # when the constructor IS the whole initializer. A lowercase-headed
            # path (``java.io.File(p).resolve(x)``) is a package path and stays.
            if depth > 0 and "." in found and found[:1].isupper():
                return found.split(".", 1)[0]
            return found
        if callee.type != "navigation_expression":
            return None
        # ``a.b().c`` -- step into the receiver of the outermost navigation
        node = next((c for c in callee.children if c.type == "call_expression"), None)
        depth += 1
    return None


#: The roots a fully-qualified inline call is taken at its word for
#: (INV-dupol): the JDK and the Kotlin standard library, the packages
#: kotlin.yaml's rows describe. scala's twin is ``scala._INLINE_QUALIFIED_ROOTS``.
_KOTLIN_INLINE_QUALIFIED_ROOTS: Final = frozenset({"java", "javax", "kotlin"})


def _kt_inline_path(node: "tree_sitter.Node", source: bytes) -> str | None:
    """The dotted text of a receiver built only of identifiers, else ``None``.

    ``java.nio.file.Files`` parses as nested ``navigation_expression`` nodes
    with an identifier at every level. A call, an index, ``this`` or a literal
    anywhere in the chain means the receiver is a value, not a path.
    """
    if node.type == "identifier":
        return node_text(node, source)
    if node.type != "navigation_expression":
        return None
    named = [c for c in node.children if c.is_named]
    # The grammar's navigation_expression is one receiver and one suffix; this
    # guards a variant that nests anything else there.
    if len(named) != 2 or named[1].type != "identifier":  # pragma: no cover - grammar guard
        return None
    head = _kt_inline_path(named[0], source)
    return None if head is None else f"{head}.{node_text(named[1], source)}"


def _kt_receiver_module(
    type_name: str, imports: dict[str, str], *, is_project_type: Callable[[str], bool],
) -> str | None:
    """The module slot a receiver TYPE names, or ``None`` when the file gives no path.

    An imported simple name maps through the import (``File`` ->
    ``java.io.File``); an inline dotted path whose head is lowercase
    (``java.io.File``) names itself; a nested ``Outer.Inner`` on an imported
    outer maps through the outer, and on an outer the file does not import
    yields ``None``. A name is never written into the slot bare (a simple name
    there asserts a module that does not exist). See ADR-0051 (WI-nasuf).

    A bare name the file never imports falls through to
    :func:`jvm_implicit_imports.static_owner_module`, the closed list of
    auto-imported ``java.lang`` names. A name ON that list has no import to
    forget (JLS 7.3), and the cases where it is NOT java.lang's -- a project
    type, an import of another type of that name, a ``kotlin.*`` name that
    shadows it -- are the ones that function already excludes. The rule is the
    same for a declared TYPE as for a receiver spelled AS a type
    (``System.getenv(k)``, WI-kilap). It matters because catalogued sources
    and sinks live on auto-imported modules -- the ``env_read`` sources
    (``System.getProperty`` / ``getenv`` / ``getProperties``) and the
    ``subprocess`` sinks (``Runtime.exec``, ``ProcessBuilder.start`` /
    ``command``) -- so ``fun f(pb: ProcessBuilder) { pb.start() }`` must name
    ``java.lang.ProcessBuilder`` exactly as the spelled-out path does
    (INV-dibij). A name off the list (``InputStream`` without an import) is
    not java.lang's and yields ``None``.
    """
    if type_name in imports:
        return imports[type_name]
    if "." in type_name:
        head, _, rest = type_name.partition(".")
        if head[:1].islower():
            return type_name
        if head in imports:
            return f"{imports[head]}.{rest}"
        return None
    return static_owner_module(
        type_name, imports, shadowed=KOTLIN_SHADOWED_JAVA_LANG,
        is_project_type=is_project_type(type_name),
    )


def _extract_imports(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract import mappings from a parsed Kotlin tree.

    Tracks: import com.example.ClassName -> ClassName: com.example.ClassName

    Returns dict mapping simple class name -> fully qualified name.
    """
    imports: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "import":
            continue

        # Find the qualified_identifier
        id_node = find_child_by_type(node, "qualified_identifier")
        if not id_node:
            id_node = find_child_by_type(node, "identifier")
        if id_node:
            full_name = node_text(id_node, source)
            # Extract simple name (last part of qualified name)
            simple_name = full_name.split(".")[-1]
            imports[simple_name] = full_name

    return imports


#: The node types that declare a type name in a Kotlin file.
_KT_TYPE_DECLARATION_NODES = frozenset({"class_declaration", "object_declaration", "type_alias"})


def _kotlin_import_nodes(
    tree: "tree_sitter.Tree", source: bytes,
) -> Iterator[tuple[str, str | None, bool]]:
    """Each ``import`` of the file as ``(path, alias, star)``: ``import a.b.Foo``
    is ``("a.b.Foo", None, False)``, ``import a.b.Foo as Bar`` is
    ``("a.b.Foo", "Bar", False)``, ``import a.b.*`` is ``("a.b", None, True)``."""
    for node in iter_tree(tree.root_node):
        if node.type != "import":
            continue
        path_node = find_child_by_type(node, "qualified_identifier")
        if path_node is None:
            path_node = find_child_by_type(node, "identifier")
        if path_node is None:  # pragma: no cover - an import always names a path
            continue
        alias = next((
            c for i, c in enumerate(node.children)
            if c.type == "identifier" and i > 0 and node.children[i - 1].type == "as"
        ), None)
        yield (
            node_text(path_node, source),
            node_text(alias, source) if alias is not None else None,
            any(c.type == "*" for c in node.children),
        )


def _kotlin_import_bindings(
    tree: "tree_sitter.Tree", source: bytes,
) -> list[tuple[str, str]]:
    """What each import binds: ``import a.b.Foo`` binds ``Foo``, ``import
    a.b.Foo as Bar`` binds ``Bar``, both to ``a.b.Foo``; ``import a.b.*``
    binds nothing. (:func:`_extract_imports`, the module-slot map, reads
    an alias's original name and a star import's package; neither is what
    the file's text binds.)"""
    return [
        (alias or path.rsplit(".", 1)[-1], path)
        for path, alias, star in _kotlin_import_nodes(tree, source) if not star
    ]


def _kotlin_import_scope(tree: "tree_sitter.Tree", source: bytes) -> ImportScope:
    """The file's import table for the shared call resolution (ADR-0064).

    ``import a.b.Foo`` binds ``Foo`` to the declaration ``a.b.Foo``
    (``add_named``), ``import a.b.Foo as Bar`` binds ``Bar`` to it, and
    ``import a.b.*`` brings in every name of ``a.b`` (``add_open_wildcard``).
    A companion object's member is imported through ``Companion``
    (``import a.b.Foo.Companion.of``) while its qualified name has no such
    segment (``a.b.Foo.of``): the segment is dropped (``kotlin_import_path``).
    The file's package is the table's ``own_namespace``: Kotlin packages do
    not nest, so a name is looked for in the caller's enclosing declarations
    and its own package, never in a parent package. What is NOT recorded:
    Kotlin's default
    imports (``kotlin.*``, ``kotlin.collections.*``, ``java.lang.*``, ...),
    where nothing of a repository is declared.

    The table says the analyzer screens library names
    (``library_names_screened``): a call whose name the standard library
    defines on its types is handed over with ``stdlib_member_name``
    (:data:`_KOTLIN_LIBRARY_MEMBER_NAMES`).
    """
    scope = ImportScope(
        library_names_screened=True,
        own_namespace=_extract_kotlin_package(tree.root_node, source) or "",
    )
    for path, alias, star in _kotlin_import_nodes(tree, source):
        if star:
            scope.add_open_wildcard(kotlin_import_path(path))
            continue
        module, _, name = kotlin_import_path(path).rpartition(".")
        scope.add_named(module, name, alias=alias)
    return scope


#: THE SCREEN OF LIBRARY NAMES (ADR-0064): functions and methods the Kotlin
#: standard library defines on its own types, so a call of one of them on a
#: receiver this analyzer cannot type (``items.map { }``, ``s.trim()``,
#: ``x.let { }``) is the library's at least as likely as the repository's one
#: declaration of that name. Such a call is handed over with
#: ``stdlib_member_name``, and the single-holder name linker does not bind it
#: by its name. Taken from the standard library's API reference
#: (kotlinlang.org/api/core/kotlin-stdlib), package by package, as named in
#: each group below; NOT from the repositories measured. A typed or qualified
#: call is decided through its type whatever its name: the screen matters only
#: where nothing else does.
_KOTLIN_LIBRARY_MEMBER_NAMES: Final = frozenset({
    # kotlin.Any (members of every type), kotlin.Comparable, kotlin.Function
    "equals", "hashCode", "toString", "compareTo", "invoke",
    # kotlin (Standard.kt): the scope functions and their kin
    "let", "run", "with", "apply", "also", "takeIf", "takeUnless", "repeat",
    # kotlin.AutoCloseable / kotlin.io.Closeable: close and use
    "close", "use",
    # kotlin.Lazy, kotlin.Result
    "isInitialized", "getOrNull", "getOrThrow", "getOrElse", "getOrDefault",
    "exceptionOrNull", "onSuccess", "onFailure", "fold", "recover", "recoverCatching",
    "mapCatching", "runCatching",
    # kotlin.Throwable
    "printStackTrace", "addSuppressed", "stackTraceToString",
    # kotlin.Number, kotlin.Int / Long / ... (conversions and operators)
    "toByte", "toShort", "toInt", "toLong", "toFloat", "toDouble", "toChar",
    "toUByte", "toUShort", "toUInt", "toULong",
    "plus", "minus", "times", "div", "rem", "mod", "inc", "dec", "unaryMinus",
    "unaryPlus", "not", "and", "or", "xor", "inv", "shl", "shr", "ushr",
    "rangeTo", "rangeUntil", "countOneBits", "countLeadingZeroBits",
    "countTrailingZeroBits", "takeHighestOneBit", "takeLowestOneBit",
    "rotateLeft", "rotateRight", "toBigInteger", "toBigDecimal",
    # kotlin.ranges
    "coerceIn", "coerceAtLeast", "coerceAtMost", "until", "downTo", "step",
    "reversed", "contains", "first", "last", "random", "randomOrNull",
    # kotlin.collections: Iterable / Collection / List / Set / Map and their
    # Mutable variants, and arrays (members and extension functions)
    "iterator", "hasNext", "next", "remove", "add", "addAll", "removeAll",
    "retainAll", "clear", "containsAll", "containsKey", "containsValue",
    "get", "set", "put", "putAll", "getOrPut", "putIfAbsent", "isEmpty",
    "isNotEmpty", "isNullOrEmpty", "orEmpty", "ifEmpty", "indexOf",
    "lastIndexOf", "indexOfFirst", "indexOfLast", "subList", "elementAt",
    "elementAtOrNull", "elementAtOrElse", "firstOrNull", "lastOrNull", "single",
    "singleOrNull", "find", "findLast", "component1", "component2",
    "component3", "component4", "component5",
    "map", "mapNotNull", "mapIndexed", "mapIndexedNotNull", "mapTo", "mapKeys",
    "mapValues", "flatMap", "flatten", "filter", "filterNot", "filterNotNull",
    "filterIsInstance", "filterIndexed", "filterKeys", "filterValues", "forEach",
    "forEachIndexed", "onEach", "any", "all", "none", "count", "sum", "sumOf",
    "average", "max", "min", "maxOf", "minOf", "maxOrNull", "minOrNull", "maxBy",
    "minBy", "maxByOrNull", "minByOrNull", "maxWith", "minWith", "sorted",
    "sortedBy", "sortedByDescending", "sortedDescending", "sortedWith", "sort",
    "sortBy", "sortByDescending", "sortWith", "shuffled", "shuffle", "reverse",
    "asReversed", "distinct", "distinctBy", "groupBy", "groupingBy", "eachCount",
    "associate", "associateBy", "associateWith", "partition", "zip",
    "zipWithNext", "unzip", "chunked", "windowed", "take", "takeLast",
    "takeWhile", "takeLastWhile", "drop", "dropLast", "dropWhile",
    "dropLastWhile", "slice", "reduce", "reduceOrNull", "reduceIndexed",
    "foldIndexed", "runningFold", "runningReduce", "scan", "withIndex",
    "joinTo", "joinToString", "toList", "toMutableList", "toSet",
    "toMutableSet", "toHashSet", "toSortedSet", "toMap", "toMutableMap",
    "toSortedMap", "toTypedArray", "toCollection", "toPair", "asList",
    "asIterable", "asSequence", "intersect", "union", "subtract",
    "removeFirst", "removeLast", "removeFirstOrNull", "removeLastOrNull",
    "removeIf", "removeAt", "addFirst", "addLast", "binarySearch", "fill",
    "copyOf", "copyOfRange", "copyInto", "contentEquals", "contentHashCode",
    "contentToString", "plusElement", "minusElement", "requireNoNulls",
    "getValue", "withDefault",
    # kotlin.sequences
    "generateSequence", "sequence", "yield", "yieldAll", "constrainOnce",
    # kotlin.text: String / CharSequence / StringBuilder / Char / Regex
    "trim", "trimStart", "trimEnd", "trimIndent", "trimMargin",
    "split", "lines", "lineSequence", "substring", "substringBefore",
    "substringAfter", "substringBeforeLast", "substringAfterLast", "subSequence",
    "startsWith", "endsWith", "replace", "replaceFirst", "replaceRange",
    "lowercase", "uppercase", "lowercaseChar", "uppercaseChar", "toLowerCase",
    "toUpperCase", "capitalize", "decapitalize", "replaceFirstChar", "format",
    "padStart", "padEnd", "isBlank", "isNotBlank", "isNullOrBlank", "ifBlank",
    "toIntOrNull", "toLongOrNull", "toDoubleOrNull", "toFloatOrNull",
    "toBoolean", "toBooleanStrict", "toBooleanStrictOrNull", "toByteArray",
    "encodeToByteArray", "decodeToString", "toCharArray", "toRegex", "matches",
    "matchEntire", "containsMatchIn", "findAll", "removePrefix", "removeSuffix",
    "removeSurrounding", "prependIndent", "commonPrefixWith", "commonSuffixWith",
    "regionMatches", "equalsIgnoreCase", "chunkedSequence", "digitToInt",
    "digitToIntOrNull", "isDigit", "isLetter", "isLetterOrDigit", "isWhitespace",
    "isUpperCase", "isLowerCase", "append", "appendLine", "appendRange",
    "insert", "setLength", "deleteCharAt", "deleteRange", "setRange",
    "buildString", "codePointAt", "toHexString", "hexToByteArray",
    # kotlin.io: File and stream extensions
    "readText", "writeText", "appendText", "readBytes", "writeBytes",
    "appendBytes", "readLines", "forEachLine", "useLines", "bufferedReader",
    "bufferedWriter", "reader", "writer", "inputStream", "outputStream",
    "copyTo", "copyRecursively", "deleteRecursively", "walk", "readLine",
})


def _kotlin_type_names(tree: "tree_sitter.Tree", source: bytes) -> FileTypeNames:
    """What the file's imports make a type name mean (WI-palus, scheme v17).

    Bound: :func:`_kotlin_import_bindings`. Defaults: kotlin's default imports
    (:func:`jvm_implicit_imports.kotlin_default_spellings`), so
    ``kotlin.String`` is hashed as ``String``. Declared: every class, object,
    interface and typealias the file declares, which keeps its name.
    """
    declared = set()
    for node in iter_tree(tree.root_node):
        if node.type in _KT_TYPE_DECLARATION_NODES:
            name = next(
                (c for c in node.children if c.type in ("identifier", "type_identifier")),
                None,
            )
            if name is not None:
                declared.add(node_text(name, source))
    return file_type_names(
        _kotlin_import_bindings(tree, source), declared=declared,
        defaults=kotlin_default_spellings(),
    )


def _extract_delegation_specifiers(
    node: "tree_sitter.Node",
    source: bytes,
) -> list[str]:
    """Extract base classes and interfaces from delegation_specifiers (META-001).

    Kotlin syntax: class Foo : Bar(), Interface1, Interface2 { }
    AST structure:
        class_declaration
            identifier "Foo"
            delegation_specifiers
                delegation_specifier
                    constructor_invocation (for classes with ())
                        user_type
                            identifier "Bar"
                delegation_specifier
                    user_type (for interfaces without ())
                        identifier "Interface1"

    Returns list of base type names (without parentheses).
    """
    base_classes: list[str] = []

    # Find delegation_specifiers child
    for child in node.children:
        if child.type == "delegation_specifiers":
            # Iterate through each delegation_specifier
            for spec in child.children:
                if spec.type != "delegation_specifier":
                    continue

                # Look for user_type directly or inside constructor_invocation
                for spec_child in spec.children:
                    if spec_child.type == "user_type":
                        # Interface implementation (no parentheses)
                        base_name = _extract_user_type_name(spec_child, source)
                        if base_name:
                            base_classes.append(base_name)
                        break
                    elif spec_child.type == "constructor_invocation":
                        # Class inheritance (with parentheses)
                        for inv_child in spec_child.children:
                            if inv_child.type == "user_type":
                                base_name = _extract_user_type_name(inv_child, source)
                                if base_name:
                                    base_classes.append(base_name)
                                break
                        break

    return base_classes


def _extract_user_type_name(user_type_node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """The name a ``user_type`` node writes, without its type arguments.

    Every identifier of the path, joined with ``.``: ``List``,
    ``WebSocket.Factory``, ``kotlin.collections.List``. The first identifier
    alone was returned until ADR-0064's batch 5a, so ``class C :
    WebSocket.Factory`` recorded the base ``WebSocket`` and C "implemented"
    the outer interface: with a hierarchy walker, ``c.close()`` was then bound
    to ``WebSocket.close``. The inheritance linker reads a dotted base by its
    last segment and checks the owner it is written with.
    """
    parts = [
        node_text(child, source) for child in user_type_node.children
        if child.type in ("simple_identifier", "identifier", "type_identifier")
    ]
    if parts:
        return ".".join(parts)
    # Defensive fallback for unexpected AST shapes
    if True:  # pragma: no cover
        text = node_text(user_type_node, source)
        if "<" in text:
            text = text.split("<")[0]
        return text if text else None
    return None  # unreachable, but keeps mypy happy


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
    file_stable_id: str = "",
    *,
    rel_path: str,
) -> FileAnalysis:
    """Extract symbols from a single Kotlin file.

    WI-bokab (v7): ``file_stable_id`` is the file-identity anchor for this
    file's symbols. ``KotlinAnalyzer.analyze`` computes it once per file from
    the REPO-RELATIVE path (``make_file_stable_id("kotlin", normalize_path(rel_path))``)
    — ``file_path`` here is ABSOLUTE (``find_kotlin_files`` yields absolute
    paths), so it cannot be folded directly. The anchor is threaded into the
    ``make_typed_stable_id`` call's ``file_stable_id=`` slot so same-name
    functions/methods in different files hash distinctly. Defaults to ``""``
    (the no-op fold) for legacy/test callers that don't supply it.

    ``rel_path`` is that repo-relative path, for the identity inputs that take
    a path rather than the anchor: a top-level ``val``'s
    ``make_variable_stable_id``. Hashing the absolute ``file_path`` instead
    would give the same file another id in every checkout (WI-tusad).
    """
    try:
        source = file_path.read_bytes()
        tree = _parse_kotlin(parser, source)
    except (OSError, IOError) as e:  # pragma: no cover - IO errors hard to trigger in tests
        run.record_failed_file(str(file_path), f"{type(e).__name__}: {e}")
        return FileAnalysis()
    # WI-bulaz: a parse that needed error recovery is recorded, not silent.
    record_partial_parse(run, str(file_path), tree)
    # WI-palus: every type a stable id hashes is written through the imports.
    with resolving_type_names(_kotlin_type_names(tree, source)):
        return _extract_parsed_symbols(
            file_path, source, tree, run, file_stable_id, rel_path=rel_path)


def _extract_parsed_symbols(
    file_path: Path,
    source: bytes,
    tree: "tree_sitter.Tree",
    run: AnalysisRun,
    file_stable_id: str,
    *,
    rel_path: str,
) -> FileAnalysis:
    """The symbols of one parsed Kotlin file (:func:`_extract_symbols_from_file`)."""
    analysis = FileAnalysis()
    analysis.imports = _extract_imports(tree, source)
    analysis.import_scope = _kotlin_import_scope(tree, source)
    # Extract package once for qualified_name population (ADR-0032).
    package_name = _extract_kotlin_package(tree.root_node, source)
    analysis.package = package_name

    for node in iter_tree(tree.root_node):
        # Function declaration
        if node.type == "function_declaration":
            name_node = _find_child_by_field(node, "name")
            if not name_node:  # pragma: no cover - grammar fallback
                name_node = find_child_by_type(node, "identifier")

            if name_node:
                func_name = node_text(name_node, source)
                enclosing_class = _get_enclosing_class(node, source)
                if enclosing_class:
                    full_name = f"{enclosing_class}.{func_name}"
                    kind = "method"
                else:
                    full_name = func_name
                    kind = "function"

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract signature
                signature = _extract_kotlin_signature(node, source)
                modifiers = _extract_modifiers(node)

                # Typed stable_id (spec §6 typed tier). An extension
                # function's receiver is hashed in front of its parameters.
                norm_sig = _kotlin_callable_hashed(node, source)
                # INV-fabih: the full class/object/function chain, not the
                # nearest owner (``full_name``) two nested owners share.
                stable_id = make_typed_stable_id(
                    kind, norm_sig, visibility_from_modifiers(modifiers),
                    name=func_name,
                    qualified_name=".".join((*_kotlin_scope(node, source), func_name)),
                    file_stable_id=file_stable_id,
                    is_async=declares_async(node),
                ) if norm_sig else None

                # Extract annotations (decorators)
                annotations = _extract_annotations(node, source)
                func_meta: dict[str, object] | None = None
                if annotations:
                    func_meta = {"decorators": annotations}

                # WI-fuhav: detect Kotlin extension functions.
                # fun Receiver.name() {} is an extension function; the
                # receiver type is the user_type sibling before the
                # name identifier. Extension functions are inherently
                # part of the public API of their receiver — something
                # external calls them — so mark is_exported=True so
                # dead-code-maybe's --seeds exports mode treats them
                # as reachable. Also record the receiver type in meta
                # for the receiver_type_dispatch linker.
                receiver_type = _extract_kotlin_receiver_type(node, source)
                func_is_exported = not any(
                    m in modifiers for m in ("private", "internal", "protected")
                )
                if receiver_type is not None:
                    if func_meta is None:
                        func_meta = {}
                    func_meta["extension_receiver"] = receiver_type
                    func_is_exported = True
                parameters = _kt_parameters_meta(node, source)
                if parameters is not None:
                    func_meta = {**(func_meta or {}), "parameters": parameters}

                cls_ancestors = _get_kotlin_class_ancestors(node, source)
                symbol = Symbol(
                    id=make_symbol_id("kotlin", str(file_path), start_line, end_line, full_name, kind),
                    name=full_name,
                    kind=kind,
                    language="kotlin",
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
                    docstring=extract_preceding_doc_comment(node, source, "kotlin"),
                    modifiers=modifiers,
                    meta=func_meta,
                    shape_id=_analyzer.compute_shape_id(node),
                    is_exported=func_is_exported,
                    line_span=end_line - start_line + 1,
                    qualified_name=_make_kotlin_qualified_name(package_name, cls_ancestors, func_name),
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, "kotlin"),
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[func_name] = symbol
                analysis.symbol_by_name[full_name] = symbol

        # Class declaration (also handles interfaces in Kotlin AST)
        elif node.type == "class_declaration":
            # Check if it's an interface
            is_interface = find_child_by_type(node, "interface") is not None

            name_node = _find_child_by_field(node, "name")
            if not name_node:  # pragma: no cover - grammar fallback
                name_node = find_child_by_type(node, "identifier")

            if name_node:
                type_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                kind = "interface" if is_interface else "class"

                # Extract base classes/interfaces (META-001)
                meta: dict[str, object] | None = None
                base_classes = _extract_delegation_specifiers(node, source)
                if base_classes:
                    meta = {"base_classes": base_classes}

                # Extract annotations (decorators)
                annotations = _extract_annotations(node, source)
                if annotations:
                    if meta is None:
                        meta = {}
                    meta["decorators"] = annotations

                class_modifiers = _extract_modifiers(node)
                cls_ancestors = _get_kotlin_class_ancestors(node, source)
                symbol = Symbol(
                    id=make_symbol_id("kotlin", str(file_path), start_line, end_line, type_name, kind),
                    name=type_name,
                    kind=kind,
                    language="kotlin",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    # INV-fabih: the table formula over the enclosing scopes
                    # (the backstop's own value for a top-level type).
                    stable_id=scoped_table_stable_id(
                        kind=kind, lang="kotlin", path=rel_path, name=type_name,
                        scope=_kotlin_scope(node, source),
                    ),
                    meta=meta,
                    modifiers=class_modifiers,
                    shape_id=_analyzer.compute_shape_id(node),
                    line_span=end_line - start_line + 1,
                    is_exported=not any(
                        m in class_modifiers
                        for m in ("private", "internal", "protected")
                    ),
                    qualified_name=_make_kotlin_qualified_name(package_name, cls_ancestors, type_name),
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[type_name] = symbol

                # WI-dorop: one kind="field" per enum ENTRY, named `Color.RED`
                # — the `.` separator kotlin already uses for its fields
                # (`f"{enclosing_class}.{prop_name}"`) and one the containment
                # linker splits on.
                #
                # NOTE THE CONTAINER'S KIND. Kotlin emits an `enum class` as
                # kind="class" carrying an "enum" modifier; there is no
                # kind="enum" anywhere in this analyzer. So the gate below is
                # STRUCTURAL — the presence of an `enum_class_body` node —
                # rather than a test on the modifier string or the kind. Two
                # consequences worth knowing: `class` is in both
                # `containment.CONTAINER_KINDS` and the slicer's set, so these
                # members root and expand correctly without any kind change;
                # but anything keyed on kind="enum" (including the G2
                # emission-parity matrix's `_ENUM_CONTAINER_KINDS`) will never
                # see a Kotlin enum at all. Verify this by contains-out or a
                # reverse slice, never by an enum-kind query.
                #
                # Unlike Swift, each constant is its own `enum_entry` node, so
                # a per-entry loop is correct here; `MERCURY(3.3)` carries its
                # `value_arguments` after the identifier and is otherwise the
                # same shape.
                enum_body = find_child_by_type(node, "enum_class_body")
                enum_chain = ".".join((*_kotlin_scope(node, source), type_name))
                for entry in enum_body.children if enum_body else ():
                    if entry.type != "enum_entry":
                        continue
                    e_name_node = find_child_by_type(entry, "identifier")
                    if e_name_node is None:  # pragma: no cover - always names
                        continue
                    e_name = node_text(e_name_node, source)
                    e_full = f"{type_name}.{e_name}"
                    e_start = entry.start_point[0] + 1
                    e_end = entry.end_point[0] + 1
                    e_qualified = _make_kotlin_qualified_name(
                        package_name, [*cls_ancestors, type_name], e_name,
                    )
                    entry_sym = Symbol(
                        id=make_symbol_id(
                            "kotlin", str(file_path), e_start, e_end,
                            e_full, "field",
                        ),
                        name=e_full,
                        kind="field",
                        language="kotlin",
                        path=str(file_path),
                        span=Span(
                            start_line=e_start,
                            end_line=e_end,
                            start_col=entry.start_point[1],
                            end_col=entry.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        # An entry is as reachable as its enum; Kotlin has no
                        # per-entry visibility modifier.
                        modifiers=class_modifiers,
                        stable_id=make_typed_stable_id(
                            "field", "",
                            visibility_from_modifiers(class_modifiers),
                            name=e_name, qualified_name=f"{enum_chain}.{e_name}",
                            file_stable_id=file_stable_id,
                        ),
                        line_span=e_end - e_start + 1,
                        is_exported=not any(
                            m in class_modifiers
                            for m in ("private", "internal", "protected")
                        ),
                        qualified_name=e_qualified,
                    )
                    analysis.symbols.append(entry_sym)
                    analysis.symbol_by_name[e_full] = entry_sym

        # Object declaration
        elif node.type == "object_declaration":
            name_node = _find_child_by_field(node, "name")
            if not name_node:  # pragma: no cover - grammar fallback
                name_node = find_child_by_type(node, "type_identifier")

            if name_node:
                object_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract annotations (decorators)
                obj_annotations = _extract_annotations(node, source)
                obj_meta: dict[str, object] | None = None
                if obj_annotations:
                    obj_meta = {"decorators": obj_annotations}

                obj_modifiers = _extract_modifiers(node)
                cls_ancestors = _get_kotlin_class_ancestors(node, source)
                obj_qualified = _make_kotlin_qualified_name(
                    package_name, cls_ancestors, object_name,
                )
                symbol = Symbol(
                    id=make_symbol_id("kotlin", str(file_path), start_line, end_line, object_name, "object"),
                    name=object_name,
                    kind="object",
                    language="kotlin",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    modifiers=obj_modifiers,
                    meta=obj_meta,
                    # The ADR-0035 producer formula over the full
                    # package/class chain (WI-tusad).
                    stable_id=make_typed_stable_id(
                        "object", "", visibility_from_modifiers(obj_modifiers),
                        name=object_name, qualified_name=obj_qualified,
                        file_stable_id=file_stable_id,
                    ),
                    shape_id=_analyzer.compute_shape_id(node),
                    line_span=end_line - start_line + 1,
                    is_exported=not any(
                        m in obj_modifiers
                        for m in ("private", "internal", "protected")
                    ),
                    qualified_name=obj_qualified,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[object_name] = symbol

        # Property declaration (val/var) — WI-jusus (emission-parity F5): emit a
        # kind="field" Symbol for a class/object/interface/enum-body property and
        # a kind="variable" Symbol for a top-level (module) property. A function-/
        # block-local val/var is a local binding, not an API surface, and is
        # skipped (see _kotlin_property_scope).
        elif node.type == "property_declaration":
            scope = _kotlin_property_scope(node)
            if scope is not None:
                var_decl = find_child_by_type(node, "variable_declaration")
                if var_decl is None:  # pragma: no cover - field/variable scope always declares
                    continue
                name_node = find_child_by_type(var_decl, "identifier")
                if name_node is None:  # pragma: no cover - a variable_declaration always names
                    continue
                prop_name = node_text(name_node, source)
                enclosing_class = (
                    _get_enclosing_class(node, source) if scope == "field" else None
                )
                if scope == "field" and enclosing_class is None:
                    # Anonymous object-literal member: no named owner. Skip
                    # (mirrors the C# named-enclosing-class field gate).
                    continue

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                # Every type node kind, as written (a field's hashed type is
                # not normalized), each name through the file's imports; an
                # extension property's receiver goes in front of it, joined by
                # ``.`` as the declaration writes it.
                prop_type = _kt_declared_type_text(var_decl, source)
                prop_receiver = _kt_receiver_type_node(node)
                hashed_type = _kt_without_function_type_names(prop_type or "")
                if prop_receiver is not None:
                    hashed_type = (
                        f"{_kt_without_function_type_names(node_text(prop_receiver, source))}"
                        f".{hashed_type}"
                    )
                hashed_type = canonical_type_names(hashed_type)
                prop_modifiers = _extract_modifiers(node)
                prop_annotations = _extract_annotations(node, source)
                prop_meta: dict[str, object] | None = None
                if prop_annotations:
                    prop_meta = {"decorators": prop_annotations}
                cls_ancestors = _get_kotlin_class_ancestors(node, source)

                if scope == "field":
                    full_name = f"{enclosing_class}.{prop_name}"
                    stable_id = make_typed_stable_id(
                        "field", hashed_type,
                        visibility_from_modifiers(prop_modifiers),
                        name=prop_name,
                        qualified_name=".".join((*_kotlin_scope(node, source), prop_name)),
                        file_stable_id=file_stable_id,
                    )
                else:
                    full_name = prop_name
                    stable_id = make_variable_stable_id(
                        "kotlin", normalize_path(rel_path), prop_name
                    )

                symbol = Symbol(
                    id=make_symbol_id("kotlin", str(file_path), start_line, end_line, full_name, scope),
                    name=full_name,
                    kind=scope,
                    language="kotlin",
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
                    signature=prop_type,
                    docstring=extract_preceding_doc_comment(node, source, "kotlin"),
                    modifiers=prop_modifiers,
                    meta=prop_meta,
                    shape_id=_analyzer.compute_shape_id(node),
                    is_exported=not any(
                        m in prop_modifiers
                        for m in ("private", "internal", "protected")
                    ),
                    line_span=end_line - start_line + 1,
                    qualified_name=_make_kotlin_qualified_name(
                        package_name, cls_ancestors, prop_name
                    ),
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[prop_name] = symbol
                if scope == "field":
                    analysis.symbol_by_name[full_name] = symbol

    populate_docstrings_from_tree(tree.root_node, source, analysis.symbols)

    return analysis


def _kt_names_project_path(imported: str, project_by_qualified: dict[str, Symbol]) -> bool:
    """Whether an import names a symbol the project declares, or a member of one."""
    parts = kotlin_import_path(imported).split(".")
    return any(".".join(parts[:i]) in project_by_qualified for i in range(len(parts), 0, -1))


def _extract_edges_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    imports: dict[str, str],
    run: AnalysisRun,
    resolver: NameResolver | None = None,
    extension_index: dict[str, list[Symbol]] | None = None,
    file_symbols: list[Symbol] | None = None,
    project_by_qualified: dict[str, Symbol] | None = None,
    import_scopes: dict[str, ImportScope] | None = None,
    declared_by_name: dict[str, list[Symbol]] | None = None,
) -> list[Edge]:
    """Extract call and import edges from a file.

    Handles:
    - Simple function calls: helper()
    - Navigation calls: Object.method(), instance.method()
    - Type inference from constructor assignments: val x = ClassName()

    ``project_by_qualified`` maps every project symbol's qualified name to it:
    the declaration an explicit import names, and a member of a type the
    call's receiver names. ``import_scopes`` holds every file's import table
    (:func:`_kotlin_import_scope`) by the path its symbols carry (this file's is
    read from the tree when not given); ``declared_by_name`` holds every type and top-level function of the run by
    its short name. With the two, the declared-name rule the linkers apply
    (``type_visibility.visible_types``) says which declaration a written name
    means here, which is how a variable a call initialises is typed by the
    callee's declared return type.
    """
    if project_by_qualified is None:
        project_by_qualified = {}
    if declared_by_name is None:
        declared_by_name = {}
    if import_scopes is None:
        import_scopes = {}
    if resolver is None:
        resolver = NameResolver(global_symbols)
    if extension_index is None:
        extension_index = {}
    # Every declaration of this file, by position (INV-midag). ``local_symbols``
    # keeps ONE symbol per name, so it cannot tell two ``parse`` methods apart.
    decl_symbols = (
        file_symbols if file_symbols is not None
        else list({s.id: s for s in local_symbols.values()}.values()))
    decl_index = symbols_at(decl_symbols)
    try:
        source = file_path.read_bytes()
        tree = _parse_kotlin(parser, source)
    except (OSError, IOError):
        return []

    _caller_path = str(file_path)
    file_package = _extract_kotlin_package(tree.root_node, source)
    file_scope = import_scopes.get(_caller_path) or _kotlin_import_scope(tree, source)
    edges: list[Edge] = []
    file_id = make_file_id("kotlin", str(file_path))
    file_anchor = file_anchor_symbol("kotlin", str(file_path), PASS_ID, run.execution_id)
    # Declared once: the call arm assigns a Symbol (narrowed), the reference
    # arms an Optional.
    current_function: Optional[Symbol]
    # How many arguments the call the call arm is on writes
    # (``_kt_argument_count``); every stub ``_report`` makes carries it.
    current_arguments: int | None = None

    # Variable types: var_name -> type. A LOCAL or a PARAMETER closes with its
    # function (``ScopedVarTypes``). Were the map file-scoped,
    # ``var factory: LoggingEventListener.Factory = ...`` in one method would
    # retype the class FIELD ``factory`` (a ``TestValueFactory``) used in a
    # later method, putting a wrong module in the slot (WI-nasuf; java's
    # INV-vugon), and ``fun a(db: Other)`` would type ``val db = make()`` in the
    # next function (INV-zabud). A property outside every function and a
    # constructor property (``class C(val svc: Service)``) are bound for the
    # file.
    var_types = ScopedVarTypes()

    # Build class/object symbols dict for static call resolution
    class_symbols: dict[str, Symbol] = {
        s.name: s for s in global_symbols.values()
        if s.kind in ("class", "object", "interface")
    }

    def _is_project_receiver(name: str) -> bool:
        """Whether a receiver spelled as a TYPE names a PROJECT type here.

        WI-tipoh: an explicit import of a path OUTSIDE the project outranks a
        same-named project type. An import of a project path keeps the
        placeholder the Tier-2 linkers resolve from (WI-kilap).
        """
        return name in class_symbols and (
            name not in imports
            or not imported_elsewhere(class_symbols[name].qualified_name, imports[name])
            or _kt_names_project_path(imports[name], project_by_qualified)
        )

    def _bare_system_here() -> bool:
        """Whether a bare ``System`` is java.lang's at this point of the walk."""
        return bare_system_is_java_lang(
            imports, is_project_type=_is_project_receiver("System"),
            is_local="System" in var_types,
        )

    #: java.yaml's rows, read through ``ROW_PARENTS`` (WI-jubim): kotlin has
    #: no row file of its own and calls the JDK directly.
    library_returns = load_library_signatures("kotlin")

    def _library_call_type(call: "tree_sitter.Node") -> str | None:
        """What a call CHAIN returns, per the library-signature rows, or ``None``.

        WI-jubim, java's ``_call_return_type`` in kotlin's tree:
        ``Runtime.getRuntime().exec(c)`` calls ``exec`` on the VALUE
        ``getRuntime`` returns, and only a row says what that is. The chain is
        rooted at a receiver whose type the file establishes -- a typed local,
        a type the call is made on (its import or java.lang), an inline JDK
        path, or a constructor -- and each link's answer, a fully-qualified
        type, is the owner the next link is looked up on. The first link no
        row answers ends the walk with ``None``, so a project method in the
        chain (which has no row) never lets a later link be guessed.
        """
        links: list[str] = []
        node = call
        while node.type == "call_expression":
            callee = node.children[0]
            if callee.type != "navigation_expression":
                break
            named = [c for c in callee.children if c.is_named]
            # The grammar's navigation_expression is one receiver and one
            # suffix (``_kt_inline_path`` guards the same shape).
            if len(named) != 2 or named[1].type != "identifier":  # pragma: no cover - grammar guard
                return None
            links.append(node_text(named[1], source))
            node = named[0]
        if not links:
            return None
        owner: str | None
        if node.type == "identifier":
            name = node_text(node, source)
            owner = (
                _kt_receiver_module(var_types[name], imports, is_project_type=_is_project_receiver) if name in var_types
                else static_owner_module(
                    name, imports, shadowed=KOTLIN_SHADOWED_JAVA_LANG,
                    is_project_type=_is_project_receiver(name),
                )
            )
        elif node.type == "navigation_expression":
            path = _kt_inline_path(node, source)
            owner = None if path is None else inline_qualified_owner(
                path, links[-1], set(var_types) | set(imports),
                roots=_KOTLIN_INLINE_QUALIFIED_ROOTS,
            )
        elif node.type == "call_expression":
            created = next(
                (c for c in node.children if c.type == "identifier"), None)
            created_type = None if created is None else _kt_constructor_type(created, source)
            owner = None if created_type is None else _kt_receiver_module(created_type, imports, is_project_type=_is_project_receiver)
        else:
            owner = None
        for link in reversed(links):
            if owner is None:
                return None
            owner = library_returns.get(f"{owner}.{link}")
        return owner

    def _synthetic_getter(nav: "tree_sitter.Node") -> tuple[str, str] | None:
        """``(owner, getter)`` when a property read is a known Java getter call.

        WI-bojud-sator: kotlin reads ``getInputStream()`` as ``.inputStream``
        (and ``isEmpty()`` as ``.isEmpty``), so the read parses as a
        ``navigation_expression`` and emitted no edge, while the rows are keyed
        by the Java method. The owner is the type the file establishes for the
        receiver -- a typed local, a library call chain, or another such getter
        -- and the read counts only where a row says that owner declares the
        getter (:func:`is_known_library_method`), so a field or a kotlin
        property adds nothing.
        """
        named = [c for c in nav.children if c.is_named]
        if len(named) != 2 or named[1].type != "identifier":  # pragma: no cover - grammar guard
            return None
        receiver, prop = named[0], node_text(named[1], source)
        owner: str | None = None
        if receiver.type == "call_expression":
            owner = _library_call_type(receiver)
        elif receiver.type == "identifier" and node_text(receiver, source) in var_types:
            owner = _kt_receiver_module(var_types[node_text(receiver, source)], imports, is_project_type=_is_project_receiver)
        elif receiver.type == "navigation_expression":
            inner = _synthetic_getter(receiver)
            owner = None if inner is None else library_returns.get(".".join(inner))
        if owner is None:
            return None
        cap = prop[:1].upper() + prop[1:]
        names = [f"get{cap}", f"is{cap}"]
        if prop.startswith("is") and prop[2:3].isupper():
            names.insert(0, prop)
        getter = next(
            (g for g in names if is_known_library_method("kotlin", f"{owner}.{g}")), None)
        return None if getter is None else (owner, getter)

    def _report(
        name: str, line: int, *, module_hint: str = "external",
        receiver_type_hint: str | None = None, qualifier: str | None = None,
        enclosing_class: str | None = None, super_receiver: bool = False,
        on_receiver: bool = True,
    ) -> None:
        """One call this analyzer does not bind, handed to the linkers with the
        facts the call site has (ADR-0064): the receiver's type as written, the
        owner the call names, the enclosing type and whether the call is on
        ``super``. A name the standard library defines on its types is marked
        (:data:`_KOTLIN_LIBRARY_MEMBER_NAMES`), so that no linker binds it by
        its name alone."""
        assert current_function is not None  # narrowed by the call arm
        edges.append(make_unresolved_edge(
            "kotlin", current_function.id, name, line, PASS_ID, run.execution_id,
            module_hint=module_hint, receiver_type_hint=receiver_type_hint,
            qualifier=qualifier, enclosing_class=enclosing_class,
            super_receiver=super_receiver,
            call_construct="method" if on_receiver else None,
            stdlib_member_name=name in _KOTLIN_LIBRARY_MEMBER_NAMES,
            argument_count=current_arguments,
        ))

    def _report_named_owner(owner: str, member: str, line: int, module_hint: str) -> None:
        """A call on a receiver that is a NAME, not a value: a type by Kotlin's
        naming (``Util.helper()``, ``Outer.Inner.m()``) or a package path
        (``okhttp3.Request(..)``). A capitalised member is a construction of a
        nested or qualified type (``Request.Builder()``), reported as a bare
        call of the type as written, which the linker looks up through the
        file's imports; any other member is a call on the owner the call site
        names (the ``qualifier``)."""
        if member[:1].isupper():
            _report(f"{owner}.{member}", line, module_hint=module_hint, on_receiver=False)
        else:
            _report(member, line, module_hint=module_hint, qualifier=owner)

    def _type_declared_variable(
        call: "tree_sitter.Node", callee: Symbol, ret_name: str | None,
    ) -> None:
        """``val x = f()``: ``x`` is typed by ``ret_name``, ``f``'s declared
        return type, a simple name read where ``f`` is DECLARED (its file's
        imports and enclosing scopes), which need not mean the same type here,
        or anything: Kotlin infers ``x``'s type and the caller's file need not
        import it. The type is written as the declaring file's name for it
        when this file means the same type by it (an extension on that name is
        then found), else by its qualified name. A return type no declaration
        of the run answers (a library's) types nothing, and a declaration that
        writes its own type (``val w: WebSocket = newWebSocket()``) keeps it:
        a variable's type is the type its declaration writes."""
        prop_decl = call.parent
        if not ret_name or prop_decl is None or prop_decl.type != "property_declaration":
            return
        returned = _visible_declaration(ret_name, _KT_TYPE_KINDS, place=callee)
        var_decl = find_child_by_type(prop_decl, "variable_declaration")
        var_name_node = find_child_by_type(var_decl, "identifier") if var_decl else None
        if returned is None or var_name_node is None or _kt_declared_type(
            find_child_by_type(var_decl, "user_type"), source,
        ) is not None:
            return
        here = _visible_declaration(ret_name, _KT_TYPE_KINDS)
        var_types[node_text(var_name_node, source)] = (
            ret_name if here is not None and here.qualified_name == returned.qualified_name
            else returned.qualified_name or ""
        )

    def _emit_navigation_receiver_call(
        receiver: "tree_sitter.Node", method_name: str, line: int,
        *, this_property: str | None,
    ) -> None:
        """INV-dupol: one edge for a call whose receiver is a navigation chain.

        In java's spelling of the same shapes: ``this.field.m()`` names the
        field's declared type, a fully-qualified inline path names its owner,
        a path of names ending in a type (``Outer.Inner.m()``) names the owner
        the call is on, and anything else keeps the ``external`` placeholder so
        the untyped-receiver disclosure names the call. A chain through ANOTHER
        object's field (``h.file.m()``) stays a placeholder: ``var_types`` is
        file-wide and keyed by bare name, so typing it from there could put the
        wrong type in the slot (INV-kotob).
        """
        recv_type = var_types.get(this_property) if this_property else None
        if recv_type is not None:
            module = _kt_receiver_module(recv_type, imports, is_project_type=_is_project_receiver)
            _report(method_name, line, module_hint=module or "external", receiver_type_hint=recv_type)
            return
        # WI-rabum, java's WI-dorus: ``System.err.println(k)`` is a call on the
        # PrintStream the field declares, writing the process's own stream --
        # what java.yaml's ``requires_target_kind: std_stream`` rows wait for.
        if is_std_output_receiver(
            node_text(receiver, source),
            bare_system_is_java_lang=_bare_system_here(),
        ):
            assert current_function is not None  # narrowed by the call arm
            stdio_call = make_unresolved_edge(
                "kotlin", current_function.id, method_name, line, PASS_ID,
                run.execution_id, module_hint=PRINT_STREAM_TYPE,
                receiver_type_hint=PRINT_STREAM_TYPE, call_construct="method",
                argument_count=current_arguments,
            )
            stdio_call.meta = {
                **(stdio_call.meta or {}), "io_target_kind": STD_STREAM_TARGET_KIND,
            }
            edges.append(stdio_call)
            return
        path = _kt_inline_path(receiver, source)
        if path is not None:
            owner = inline_qualified_owner(
                path, method_name, set(var_types) | set(imports),
                roots=_KOTLIN_INLINE_QUALIFIED_ROOTS,
            )
            if owner is not None:
                _report(method_name, line, module_hint=owner)
                return
            if path.split(".", 1)[0] not in var_types and (
                path.rsplit(".", 1)[-1][:1].isupper() or method_name[:1].isupper()
            ):
                _report_named_owner(path, method_name, line, "external")
                return
        _report(method_name, line)

    # WI-gikuk: this file's declarations by qualified name (``a.Foo.helper``),
    # every one of a name in declaration order: the overloads of a method.
    own_by_qualified: dict[str, list[Symbol]] = {}
    for decl in decl_symbols:
        if decl.qualified_name:
            own_by_qualified.setdefault(decl.qualified_name, []).append(decl)

    def _visible_declaration(
        written: str, kinds: frozenset[str], place: Symbol | None = None,
    ) -> Symbol | None:
        """The ONE declaration of one of ``kinds`` that ``written`` names from
        ``place`` (default: the caller), by the rule the linkers apply
        (``type_visibility.visible_types``: the imports of the place's file and
        its enclosing scopes); ``None`` when none is visible or several are.
        It types variables; it binds no call."""
        at = place if place is not None else current_function
        assert at is not None  # narrowed by the call arm
        # Every file of the run hands over a table; a file read on its own
        # (no tables passed) sees the other files' declarations by their
        # qualified names only.
        scope = file_scope if at.path == _caller_path else import_scopes.get(at.path, ImportScope())
        # A call drawn from the file anchor (code in no declaration the
        # parse found) has no qualified name; its scope is the file's package.
        qualified = at.qualified_name or (f"{file_package}.<file>" if file_package else None)
        found = visible_types(
            written, qualified, scope,
            lambda short: [s for s in declared_by_name.get(short, ()) if s.kind in kinds],
        )
        if not found or len({s.qualified_name for s in found}) != 1:
            return None
        return found[0]

    def _member_of(owner: str, member: str) -> Symbol | None:
        """``owner.member``: the member the type ``owner`` (as written) means
        from here declares itself, or ``None``."""
        declared = _visible_declaration(owner, _KT_TYPE_KINDS)
        return project_by_qualified.get(f"{declared.qualified_name}.{member}") if declared else None

    def _own_member(
        node: "tree_sitter.Node", member: str,
        kinds: frozenset[str] | None = _OWN_MEMBER_KINDS,
        argument_count: int | None = None,
    ) -> Symbol | None:
        """The member ``member`` of the type whose declaration contains
        ``node`` (by POSITION): this file's declaration at ``<class qualified
        name>.<member>``, of one of ``kinds`` (any kind when ``None``). The
        run-wide resolver keeps one ``Foo.helper`` for every ``Foo`` in the
        project, so it could bind ``b.Foo``'s ``this.helper()`` to ``a.Foo``'s
        (WI-gikuk).

        Of several (the overloads of a method), the ones that can take the
        ``argument_count`` arguments the call writes
        (``overload_arity.narrow_by_arity`` over their ``meta["parameters"]``),
        and of those the one declared LAST, the one taken before the count was
        read: the types of the arguments are not read (WI-tosut). None of them
        taking it is ``None``: the call is reported, for the linkers to look
        in the type's ancestors. A name whose last declaration is not of
        ``kinds`` is ``None``, as before."""
        owner = enclosing_declared_symbol(node, decl_index, _SUPER_OWNER_NODES)
        if owner is None or not owner.qualified_name:
            return None
        declared = own_by_qualified.get(f"{owner.qualified_name}.{member}")
        if not declared or (kinds is not None and declared[-1].kind not in kinds):
            return None
        admitted = narrow_by_arity(
            [d for d in declared if kinds is None or d.kind in kinds], argument_count,
        )
        return admitted[-1] if admitted else None

    def _own_class_lookup(
        node: "tree_sitter.Node", member: str, name_key: str,
    ) -> LookupResult:
        """``::member`` / ``this::member``: the enclosing class's OWN member
        first (``_own_member``); when this class declares no such member (an
        extension function, an inherited one) the name lookup of ``name_key``
        stands. A callable reference is not a call: ADR-0064 has not moved it."""
        own = _own_member(node, member, kinds=None)
        if own is not None:
            return LookupResult(symbol=own, confidence=NameResolver.CONFIDENCE_EXACT)
        return resolver.lookup(name_key, caller_path=_caller_path)

    def _emit_super_call(call: "tree_sitter.Node", method_name: str) -> None:
        """INV-dupol: ``super.m()`` calls a BASE's ``m``.

        Reported with the enclosing type and ``super_receiver``: the
        inherited-calls linker walks the enclosing type's ancestors, the
        superclass first, then the interfaces, past the enclosing type's own
        override (ADR-0064). The enclosing type is the one whose declaration
        contains the call. The module slot names the first declared base
        through its import (or java.lang), as before, for the taint gates.
        """
        owner_sym = enclosing_declared_symbol(call, decl_index, _SUPER_OWNER_NODES)
        bases = list((owner_sym.meta or {}).get("base_classes", [])) if owner_sym else []
        base = bases[0] if bases else None
        module = (
            static_owner_module(
                base, imports, shadowed=KOTLIN_SHADOWED_JAVA_LANG,
                is_project_type=base in class_symbols,
            )
            if base else None
        )
        _report(
            method_name, call.start_point[0] + 1, module_hint=module or "external",
            enclosing_class=owner_sym.name if owner_sym is not None else None,
            super_receiver=True,
        )

    for node in iter_tree(tree.root_node):
        var_types.close_scopes_before(node)
        # Detect import statements
        if node.type == "import":
            # Get the qualified identifier being imported
            id_node = find_child_by_type(node, "qualified_identifier")
            if not id_node:
                id_node = find_child_by_type(node, "identifier")
            if id_node:
                import_path = node_text(id_node, source)
                edges.append(Edge.create(
                    src=file_id,
                    dst=f"kotlin:{import_path}:0-0:package:package",
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    evidence_type="import_statement",
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                ))

        # Track constructor parameter types: class Ctrl(private val svc: Service)
        elif node.type == "class_parameter":
            param_name_node = find_child_by_type(node, "identifier")
            type_node = find_child_by_type(node, "user_type")
            if param_name_node and type_node:
                param_name = node_text(param_name_node, source)
                type_name = node_text(type_node, source)
                if type_name:
                    # Strip generics
                    if "<" in type_name:
                        type_name = type_name.split("<")[0]
                    if type_name[0].isupper():
                        var_types.bind_outer(param_name, type_name)

        # Track variable types from property declarations: val x = ClassName()
        elif node.type == "property_declaration":
            # Find variable_declaration and call_expression children
            var_decl = find_child_by_type(node, "variable_declaration")
            call_expr = find_child_by_type(node, "call_expression")
            # WI-nasuf: the DECLARED type (``val c: Connection = make()``) is a
            # receiver-typing source whatever the initializer -- java's
            # INV-vugon rule; the constructor, bare (``File(p)``) or dotted
            # (``java.io.File(p)``), still narrows it afterwards.
            var_name_node = find_child_by_type(var_decl, "identifier") if var_decl else None
            if var_decl and var_name_node:
                declared = _kt_declared_type(find_child_by_type(var_decl, "user_type"), source)
                if declared is not None:
                    var_types[node_text(var_name_node, source)] = declared
            if var_decl and call_expr and var_name_node:
                # WI-jubim: a row's answer for the whole chain
                # (``val r = Runtime.getRuntime()``) first; it is a stated
                # return type, where the chain root is a builder heuristic.
                _ctor_type: str | None = (
                    _library_call_type(call_expr)
                    or _kt_chain_root_constructor(call_expr, source)
                )
                if _ctor_type:
                    var_types[node_text(var_name_node, source)] = _ctor_type

        # Function declarations - extract parameter types for type inference
        elif node.type == "function_declaration":
            var_types.open_scope(node)
            param_types = _extract_param_types(node, source)
            # Parameter types, for method call resolution; they close with the
            # function like its locals (INV-zabud).
            for param_name, param_type in param_types.items():
                var_types[param_name] = param_type

        # Detect function calls
        elif node.type == "call_expression":
            # A call in no function (a top-level ``val``'s lambda or
            # initialiser, a ``by lazy`` block, a class or object body) is
            # drawn from the enclosing class or object declaration, else
            # anchored on the file (INV-bamij).
            current_function = (
                _get_enclosing_function(node, source, decl_index)
                or enclosing_declared_symbol(node, decl_index, _TYPE_BODY_NODES)
                or file_anchor
            )
            current_arguments = _kt_argument_count(node)

            # Check for navigation_expression (Object.method() or instance.method())
            nav_node = find_child_by_type(node, "navigation_expression")
            if nav_node:
                # Navigation expression structure varies:
                # - Object.method(): identifier . identifier
                # - this.method(): this_expression . identifier
                # - instance.method(): identifier . identifier
                #
                # The receiver is the FIRST named child, WHATEVER its node
                # type, and the method is the last: ``"x".trim()``,
                # ``(f).readText()``, ``fs[0].readText()`` and ``super.go()``
                # have receivers that are not ``this``, a navigation, a call or
                # an identifier, and a call with no edge is invisible to
                # io-boundaries and to the coverage gate (INV-dupol). The chained receiver
                # (``File(p).writeText(s)``, WI-nasuf) and a nested
                # ``this.property`` are first children like any other.
                named_children = [c for c in nav_node.children if c.is_named]
                receiver_node = (
                    named_children[0] if len(named_children) >= 2 else None
                )
                method_node = (
                    named_children[-1]
                    if len(named_children) >= 2
                    and named_children[-1].type == "identifier"
                    else None
                )

                if receiver_node and method_node:
                    method_name = node_text(method_node, source)
                    line = node.start_point[0] + 1

                    # ``this.m()``: the enclosing type's OWN member, the one
                    # whose declaration contains the call by position. Any
                    # other (inherited) is reported with the enclosing type,
                    # whose ancestors the inherited-calls linker walks.
                    if receiver_node.type == "this_expression":
                        own = _own_member(node, method_name, argument_count=current_arguments)
                        if own is not None:
                            edges.append(Edge.create(
                                src=current_function.id, dst=own.id,
                                edge_type="calls", line=line,
                                resolution_score=NameResolver.CONFIDENCE_EXACT,
                                origin=PASS_ID, origin_run_id=run.execution_id,
                                evidence_type="ast_call_this",
                                resolved_by=resolved_by.OWN_TYPE,
                            ))
                        else:
                            _report(method_name, line, enclosing_class=_get_enclosing_class(node, source))

                    # ``this.p.m()`` and every other receiver that is itself
                    # a navigation: one edge each (INV-dupol), so that an
                    # inline ``java.nio.file.Files.readAllBytes(p)`` is not lost.
                    elif receiver_node.type == "navigation_expression":
                        this_node = find_child_by_type(receiver_node, "this_expression")
                        prop_node = find_child_by_type(receiver_node, "identifier")
                        _emit_navigation_receiver_call(
                            receiver_node, method_name, line,
                            this_property=(
                                node_text(prop_node, source)
                                if this_node and prop_node else None
                            ),
                        )

                    # INV-dupol: ``super.m()`` is the base's ``m``, never the
                    # override that makes the call.
                    elif receiver_node.type == "super_expression":
                        _emit_super_call(node, method_name)

                    else:
                        receiver_name = node_text(receiver_node, source)
                        # WI-nasuf: a chained receiver has no name to look up;
                        # its type is what its constructor callee names.
                        chained_type: str | None = None
                        if receiver_node.type == "call_expression":
                            receiver_name = ""
                            _inner_callee = find_child_by_type(
                                receiver_node, "identifier",
                            ) or find_child_by_type(receiver_node, "navigation_expression")
                            if _inner_callee is not None:
                                chained_type = _kt_constructor_type(_inner_callee, source)
                            # WI-jubim: ``Runtime.getRuntime().exec(c)`` -- the
                            # receiver is what a library call returns.
                            if chained_type is None:
                                chained_type = _library_call_type(receiver_node)
                        _recv_type = (
                            chained_type if chained_type is not None
                            else var_types.get(receiver_name)
                        )

                        # ``val r = x.m()`` / ``val r = Type.m()``: ``r`` is
                        # typed by the declared return of the ``m`` the type of
                        # ``x`` (or ``Type``) declares, else of an extension
                        # ``m`` declared on that type's name (WI-lodij,
                        # INV-vigaf). The call itself is the linkers' (the
                        # inherited-calls and the receiver-type-dispatch linkers
                        # bind it from the stub below).
                        resolved_nav_sym: Symbol | None = None
                        if receiver_name in var_types:
                            resolved_nav_sym = _member_of(
                                var_types[receiver_name], method_name,
                            ) or next((
                                ext for ext in extension_index.get(
                                    var_types[receiver_name].split("<")[0], ())
                                if ext.name.rsplit(".", 1)[-1] == method_name
                            ), None)
                        elif receiver_node.type == "identifier" and receiver_name[:1].isupper():
                            resolved_nav_sym = _member_of(receiver_name, method_name)
                        if resolved_nav_sym is not None and resolved_nav_sym.kind in ("function", "method"):
                            _type_declared_variable(
                                node, resolved_nav_sym,
                                _extract_kotlin_return_type_name(resolved_nav_sym.signature),
                            )

                        # A typed receiver: its type as written, for the
                        # linkers to look up through the file's imports; its
                        # module (the import, an inline path, java.lang) in the
                        # slot the taint gates read (WI-nasuf, ADR-0051).
                        if _recv_type is not None:
                            _report(
                                method_name, line,
                                module_hint=_kt_receiver_module(
                                    _recv_type, imports, is_project_type=_is_project_receiver,
                                ) or "external",
                                receiver_type_hint=_recv_type,
                            )
                        else:
                            # WI-kilap: an untyped receiver spelled as a TYPE
                            # (``Files.readAllBytes(p)``, ``System.getenv(k)``)
                            # is a static call whose owner is the module: the
                            # file's import or java.lang's closed list; a
                            # project type, or an import of a project path,
                            # keeps the placeholder (WI-tipoh).
                            _module = static_owner_module(
                                receiver_name, imports,
                                shadowed=KOTLIN_SHADOWED_JAVA_LANG,
                                is_project_type=_is_project_receiver(receiver_name),
                            ) or "external"
                            if receiver_node.type == "identifier" and (
                                receiver_name[:1].isupper() or method_name[:1].isupper()
                            ):
                                _report_named_owner(receiver_name, method_name, line, _module)
                            else:
                                _report(method_name, line, module_hint=_module)
            else:
                # Simple function call: ``helper()``, or a construction
                # ``Foo()``. Bound here: a member of the enclosing type, found
                # by position (the language's implicit receiver). Anything else
                # is reported: a function or a type the file's package or
                # imports make visible, a member of an object or companion the
                # file imports by name (``import
                # okhttp3.Headers.Companion.headersOf``), or a member the
                # enclosing type inherits, is the linkers' to find (ADR-0064),
                # with the enclosing type for the inherited-calls walk.
                callee_node = find_child_by_type(node, "identifier")
                if callee_node:
                    callee_name = node_text(callee_node, source)
                    line = node.start_point[0] + 1
                    own = _own_member(node, callee_name, argument_count=current_arguments)
                    imported = file_scope.named(callee_name) if own is None else None
                    imported_member = (
                        project_by_qualified.get(f"{imported.module}.{imported.name}")
                        if imported is not None and imported.module else None
                    )
                    if imported_member is not None and imported_member.kind not in _OWN_MEMBER_KINDS:
                        imported_member = None
                    if own is not None:
                        edges.append(Edge.create(
                            src=current_function.id, dst=own.id,
                            edge_type="calls", line=line,
                            evidence_type="ast_call",
                            origin=PASS_ID, origin_run_id=run.execution_id,
                            meta={"call_construct": "function"},
                            resolved_by=resolved_by.OWN_TYPE,
                        ))
                    else:
                        _report(
                            callee_name, line,
                            module_hint=imports.get(callee_name) or "external",
                            enclosing_class=_get_enclosing_class(node, source),
                            on_receiver=False,
                        )
                    # ``val db = getDb()``: ``db`` is typed by the declared
                    # return of the own member bound above, else of the member
                    # the file imports by that name (read at the import's
                    # path, binding nothing), else of the top-level function
                    # the name means here.
                    resolved_simple_sym = own or imported_member or _visible_declaration(
                        callee_name, frozenset({"function"}),
                    )
                    if resolved_simple_sym is not None and resolved_simple_sym.kind in ("function", "method"):
                        _type_declared_variable(
                            node, resolved_simple_sym,
                            _extract_kotlin_return_type_name(resolved_simple_sym.signature),
                        )

        # Callable references: ::functionName (unqualified)
        elif node.type == "callable_reference":
            current_function = _get_enclosing_function(node, source, decl_index)
            if current_function is None:  # pragma: no cover
                continue
            # Structure: :: identifier
            ref_name_node = find_child_by_type(node, "identifier")
            if ref_name_node:
                ref_name = node_text(ref_name_node, source)
                # Try enclosing class scope first
                enclosing_class = _get_enclosing_class(node, source)
                target_name = (
                    f"{enclosing_class}.{ref_name}" if enclosing_class else ref_name
                )
                lookup_result = _own_class_lookup(node, ref_name, target_name)
                if not (lookup_result.found and lookup_result.symbol is not None):
                    lookup_result = resolver.lookup(ref_name, caller_path=_caller_path)
                if lookup_result.found and lookup_result.symbol is not None:
                    edges.append(Edge.create(
                        src=current_function.id,
                        dst=lookup_result.symbol.id,
                        edge_type="references",
                        line=node.start_point[0] + 1,
                        resolution_score=lookup_result.confidence,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        evidence_type="callable_reference",
                    ))

        # Qualified callable references: App::method or this::method
        # These appear as navigation_expression with :: separator.
        elif node.type == "navigation_expression":
            # Only handle :: references (not normal . navigation)
            has_double_colon = any(
                not c.is_named and c.type == "::" for c in node.children
            )
            parent = node.parent
            if not has_double_colon and not (
                parent is not None and parent.type in ("call_expression", "assignment")
                and parent.children[0] == node
            ) and (getter := _synthetic_getter(node)) is not None:
                edges.append(make_unresolved_edge(
                    "kotlin",
                    (
                        _get_enclosing_function(node, source, decl_index)
                        or enclosing_declared_symbol(node, decl_index, _TYPE_BODY_NODES)
                        or file_anchor
                    ).id,
                    getter[1], node.start_point[0] + 1, PASS_ID, run.execution_id,
                    module_hint=getter[0], call_construct="method",
                ))
            if has_double_colon:
                current_function = _get_enclosing_function(
                    node, source, decl_index,
                )
                if current_function is None:  # pragma: no cover
                    continue
                named_children = [c for c in node.children if c.is_named]
                if len(named_children) >= 2:
                    receiver_node = named_children[0]
                    method_node = named_children[1]
                    method_name = node_text(method_node, source)

                    if receiver_node.type == "this_expression":
                        enclosing_class = _get_enclosing_class(node, source)
                        target = (
                            f"{enclosing_class}.{method_name}"
                            if enclosing_class
                            else method_name
                        )
                        lookup_result = _own_class_lookup(node, method_name, target)
                    else:
                        receiver_name = node_text(receiver_node, source)
                        target = f"{receiver_name}.{method_name}"
                        lookup_result = resolver.lookup(target, caller_path=_caller_path)
                        if not lookup_result.found:
                            # ``Application::module`` names an EXTENSION
                            # function, registered under its own name, not
                            # ``Application.module``: the one declared on that
                            # receiver, when exactly one is (Ktor's
                            # ``embeddedServer(..., module = Application::module)``).
                            extensions = [
                                sym for sym in (extension_index or {}).get(receiver_name, ())
                                if sym.name == method_name
                            ]
                            if len(extensions) == 1:
                                lookup_result = LookupResult(
                                    symbol=extensions[0],
                                    confidence=NameResolver.CONFIDENCE_EXACT,
                                )

                    if lookup_result.found and lookup_result.symbol is not None:
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=lookup_result.symbol.id,
                            edge_type="references",
                            line=node.start_point[0] + 1,
                            resolution_score=lookup_result.confidence,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            evidence_type="callable_reference",
                        ))

    # WI-rabum: ``System.out`` / ``.err`` / ``.in`` READ as values, through the
    # shared emitter java uses (WI-lozug). java.yaml's ``java.lang.System``
    # attribute rows (logging; ipc_recv) reach kotlin through _CATALOG_PARENTS
    # and need an edge to match. tree-sitter-kotlin declares no fields, so the
    # parts are named by position (``@first`` / ``@last``) and an argument sits
    # in a ``value_argument`` wrapper. A local ``System`` is not considered
    # here: the walk is over, and no scope is open to ask.
    emit_module_attribute_refs(
        tree.root_node, source,
        java_lang_system_aliases(bare_system_is_java_lang=bare_system_is_java_lang(
            imports, is_project_type=_is_project_receiver("System"), is_local=False,
        )),
        file_anchor, "kotlin", edges,
        node_kinds=("navigation_expression",),
        object_field_names=("@first",),
        property_field_names=("@last",),
        pass_id=PASS_ID,
        run_id=run.execution_id,
        call_node_kinds=("call_expression",),
        call_function_field_names=("@first",),
        carrier_call_kinds=("call_expression",),
        carrier_arguments_field="@value_arguments",
        carrier_argument_wrapper_kinds=("value_argument",),
        attribute_names=JAVA_LANG_SYSTEM_FIELDS,
        enclosing_symbols=decl_symbols,
    )
    return edges


def _resolve_base_class_kotlin(
    base_name: str,
    child_sym: Symbol,
    candidates_by_name: dict[str, list[Symbol]],
    sym_file_imports: dict[str, dict[str, str]],
    package_by_path: dict[str, Optional[str]] | None = None,
) -> Symbol | None:
    """Resolve a base class/interface name to a specific Symbol, disambiguating collisions.

    When multiple classes or interfaces share the same name (e.g., test stubs named
    'Model'), uses a priority cascade:

    1. Same-file match: base class defined in the same file as the child
    2. Import match: child's file has ``import com.example.models.Model`` and a
       candidate's fully qualified name or file path matches
    3. Same-package match (WI-gikuk): the candidate whose qualified name is
       ``<child's package>.<base_name>``. Kotlin needs no import for a class
       of its own package, so ``package b; class Foo : Base()`` with
       ``a.Base`` and ``b.Base`` in the repo is ``b.Base``, not the by-id
       pick. After the import tier: an explicit import shadows
       a same-package class.
    4. First by ID: deterministic fallback (sorted by symbol ID)

    Args:
        base_name: The base class/interface name to resolve (e.g., 'Model')
        child_sym: The child class symbol (for file context)
        candidates_by_name: Multi-value lookup: name -> list of Symbol candidates
        sym_file_imports: Maps symbol ID -> file-level imports dict
            (simple name -> fully qualified name)
        package_by_path: Each file's package (``None`` = default package);
            without it the same-package tier is skipped.

    Returns:
        The resolved base class/interface Symbol, or None if no match found.
    """
    candidates = candidates_by_name.get(base_name)
    if not candidates:
        return None

    # WI-pivap: a base the file imports from a package no in-repo class lives
    # in is external, however many in-repo namesakes there are.
    if external_import_fqn(base_name, child_sym, candidates_by_name, sym_file_imports):
        return None

    if len(candidates) == 1:
        return candidates[0]

    child_path = child_sym.path or ""

    # 1. Same-file match: prefer base defined in the same file
    same_file = [c for c in candidates if c.path == child_path]
    if len(same_file) == 1:
        return same_file[0]

    # 2. Import match: check if child's file imports resolve to a candidate
    imports = sym_file_imports.get(child_sym.id, {})
    if base_name in imports:
        fqn = imports[base_name]
        # Convert FQN to path segments for matching against file paths
        # e.g., "com.example.models.Model" -> try "com/example/models/Model",
        # then progressively shorter suffixes
        fqn_as_path = fqn.replace(".", "/")
        for cand in candidates:
            cand_path = cand.path or ""
            cand_no_ext = cand_path.rsplit(".kt", 1)[0]
            if cand_no_ext.endswith(fqn_as_path):
                return cand
        # Try matching just the last package segment + class name
        # e.g., "com.example.models.Model" -> "models/Model" matches ".../models/Model.kt"
        fqn_parts = fqn.split(".")
        if len(fqn_parts) >= 2:
            short_path = "/".join(fqn_parts[-2:])
            for cand in candidates:
                cand_path = cand.path or ""
                cand_no_ext = cand_path.rsplit(".kt", 1)[0]
                if cand_no_ext.endswith(short_path):
                    return cand

    # 3. Same-package match, by package-qualified name.
    if package_by_path is not None and child_path in package_by_path:
        child_package = package_by_path[child_path]
        same_package_qn = f"{child_package}.{base_name}" if child_package else base_name
        same_package = [c for c in candidates if c.qualified_name == same_package_qn]
        if len(same_package) == 1:
            return same_package[0]

    # 4. Deterministic fallback: first by symbol ID (sorted for stability)
    candidates_sorted = sorted(candidates, key=lambda c: c.id)
    return candidates_sorted[0]


def _extract_inheritance_edges(
    symbols: list[Symbol],
    class_by_name: dict[str, list[Symbol]],
    interface_by_name: dict[str, list[Symbol]],
    sym_file_imports: dict[str, dict[str, str]],
    run: AnalysisRun,
    package_by_path: dict[str, Optional[str]] | None = None,
) -> list[Edge]:
    """Extract extends/implements edges from class inheritance (META-001).

    For each class or interface with base_classes metadata, creates:
    - extends edges to base classes
    - implements edges from a class to an interface
    - extends edges from an interface to its base interface (WI-kalug)

    When multiple classes or interfaces share the same name (common in repos with
    test stubs), uses import-aware disambiguation via ``_resolve_base_class_kotlin()``
    to find the correct target.

    Args:
        symbols: All extracted symbols
        class_by_name: Multi-value lookup: class name -> list of Symbol candidates
        interface_by_name: Multi-value lookup: interface name -> list of Symbol candidates
        sym_file_imports: Maps symbol ID -> file-level imports dict
        run: Current analysis run for provenance

    Returns:
        List of extends/implements edges for inheritance relationships
    """
    edges: list[Edge] = []

    for sym in symbols:
        if sym.kind not in ("class", "interface"):
            continue

        base_classes = sym.meta.get("base_classes", []) if sym.meta else []
        if not base_classes:
            continue

        for base_class_name in base_classes:
            # Strip generics: List<Int> -> List
            base_name = base_class_name.split("<")[0] if "<" in base_class_name else base_class_name

            # Determine edge type based on whether target is interface or class
            # Try interface first, then class, using disambiguation
            iface_sym = _resolve_base_class_kotlin(
                base_name, sym, interface_by_name, sym_file_imports, package_by_path,
            )
            if iface_sym is not None and iface_sym.id != sym.id:
                # An interface EXTENDS its base interface; only a class
                # implements one (WI-kalug, matching the core linker).
                edge_type = "extends" if sym.kind == "interface" else "implements"
                base_sym = iface_sym
            else:
                class_sym = _resolve_base_class_kotlin(
                    base_name, sym, class_by_name, sym_file_imports, package_by_path,
                )
                if class_sym is not None and class_sym.id != sym.id:
                    edge_type = "extends"
                    base_sym = class_sym
                else:
                    # WI-pivap: an imported base with in-repo namesakes but no
                    # in-repo class of its qualified name is external. Its
                    # clause is not recorded, so it is labelled ``extends``,
                    # as the core linker labels external bases.
                    ext = external_import_fqn(
                        base_name, sym, {**class_by_name, **interface_by_name},
                        sym_file_imports,
                    ) if base_name in class_by_name or base_name in interface_by_name else None
                    if ext is not None:
                        edges.append(external_base_edge(
                            sym, ext, "extends",
                            sym.span.start_line if sym.span else 0, PASS_ID, run,
                        ))
                    continue  # External type

            edge = Edge.create(
                src=sym.id,
                dst=base_sym.id,
                edge_type=edge_type,
                line=sym.span.start_line if sym.span else 0,
                confidence=0.95,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="ast_extends" if edge_type == "extends" else "ast_implements",
            )
            edges.append(edge)

    return edges


# Standard JVM annotations that carry no architectural information.
# Shared concept with Java's _STANDARD_JAVA_ANNOTATIONS — Kotlin uses the
# same JVM annotation ecosystem.  Suppressed from unresolved decorated_by
# edges to avoid dangling edges to nonexistent nodes (WI-divob).
_STANDARD_JVM_ANNOTATIONS = frozenset({
    # java.lang / kotlin.jvm
    "Override", "Deprecated", "SuppressWarnings", "SafeVarargs",
    "FunctionalInterface", "JvmStatic", "JvmOverloads", "JvmField",
    "JvmName", "JvmSynthetic", "JvmDefault", "JvmRecord",
    # java.lang.annotation
    "Retention", "Target", "Documented", "Inherited", "Repeatable",
    # javax.annotation / jakarta.annotation
    "Nullable", "Nonnull", "NonNull", "CheckForNull",
    "Generated", "PostConstruct", "PreDestroy", "Resource",
    # Common testing annotations
    "Test", "BeforeEach", "AfterEach", "BeforeAll", "AfterAll",
    "ParameterizedTest", "RepeatedTest", "DisplayName", "Disabled",
    "Nested", "Tag", "ExtendWith", "RunWith",
    # Common quality/nullability annotations
    "NotNull", "NotEmpty", "NotBlank", "Valid",
    "VisibleForTesting", "Beta", "Internal",
})


def _extract_annotation_edges(
    symbols: list[Symbol],
    global_symbols: dict[str, Symbol],
    run: AnalysisRun,
) -> list[Edge]:
    """Extract decorated_by edges from annotation metadata.

    For each symbol with decorators metadata, creates decorated_by edges to
    annotation types that exist in the analyzed codebase. External annotations
    (e.g., Spring's @Service, JPA's @Entity) get lower-confidence unresolved
    edges that still surface the annotation pattern in the graph.

    Args:
        symbols: All extracted symbols
        global_symbols: Map of name -> Symbol for annotation lookup
        run: Current analysis run for provenance

    Returns:
        List of decorated_by edges for annotation relationships
    """
    edges: list[Edge] = []

    for sym in symbols:
        if sym.meta is None:
            continue

        decorators = sym.meta.get("decorators")
        if not decorators or not isinstance(decorators, list):
            continue

        for decorator in decorators:
            if not isinstance(decorator, dict):  # pragma: no cover
                continue

            dec_name = decorator.get("name")
            if not dec_name or not isinstance(dec_name, str):  # pragma: no cover
                continue

            annotation_sym = global_symbols.get(dec_name)
            line = sym.span.start_line if sym.span else 0

            if annotation_sym:
                edge = Edge.create(
                    src=sym.id,
                    dst=annotation_sym.id,
                    edge_type="decorated_by",
                    line=line,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_annotation",
                )
                edges.append(edge)
            elif dec_name not in _STANDARD_JVM_ANNOTATIONS:
                # Only emit unresolved edges for non-standard annotations.
                # Standard JVM annotations (@Override, @Deprecated, etc.) carry
                # no architectural information and would create dangling edges.
                dst_id = f"kotlin:unresolved:0-0:{dec_name}:unresolved"
                edge = Edge.create(
                    src=sym.id,
                    dst=dst_id,
                    edge_type="decorated_by",
                    line=line,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_annotation",
                    is_resolved=False,
                )
                edges.append(edge)

    return edges


class KotlinAnalyzer(TreeSitterAnalyzer):
    """Kotlin analyzer using tree-sitter-kotlin grammar."""

    lang = "kotlin"
    file_patterns: ClassVar[list[str]] = ["*.kt"]
    grammar_module = "tree_sitter_kotlin"

    def analyze(self, repo_root: Path, max_files: int | None = None) -> AnalysisResult:
        """Analyze all Kotlin files in a repository.

        Returns an AnalysisResult with symbols, edges, and provenance.
        If tree-sitter-kotlin is not available, returns a skipped result.
        """
        if not self._check_grammar_available():
            warnings.warn(
                "tree-sitter-kotlin not available. Install with: pip install hypergumbo[kotlin]",
                stacklevel=2,
            )
            return AnalysisResult(
                skipped=True,
                skip_reason="tree-sitter-kotlin not available",
                skip_reason_code=DEPENDENCY_UNAVAILABLE,
            )

        start_time = time.time()
        run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

        # Import tree-sitter-kotlin
        try:
            parser = self._create_parser()
        except Exception as e:
            run.duration_ms = int((time.time() - start_time) * 1000)
            return AnalysisResult(
                run=run,
                skipped=True,
                skip_reason=f"Failed to load Kotlin parser: {e}",
                skip_reason_code=PASS_CRASHED,
            )

        # Pass 1: Extract all symbols
        file_analyses: dict[Path, FileAnalysis] = {}
        files_skipped = 0

        for kt_file in find_kotlin_files(repo_root):
            # WI-bokab (v7): compute the file-identity anchor from the
            # REPO-RELATIVE path. ``kt_file`` is absolute (``find_kotlin_files``
            # yields absolute paths joined onto ``repo_root``), so folding
            # ``str(kt_file)`` directly would make stable_ids
            # location-dependent — the regression the WI-bokab smoke test guards.
            rel_path = str(kt_file.relative_to(repo_root))
            file_stable_id = make_file_stable_id("kotlin", normalize_path(rel_path))
            analysis = _extract_symbols_from_file(
                kt_file, parser, run, file_stable_id, rel_path=rel_path,
            )
            if analysis.symbols:
                file_analyses[kt_file] = analysis
            else:
                files_skipped += 1

        # Build global symbol registry: callable references (``::f``,
        # ``X::m``) and annotation targets still look a name up in it; no call
        # does (ADR-0064).
        global_symbols: dict[str, Symbol] = {}
        for analysis in file_analyses.values():
            for symbol in analysis.symbols:
                # Store by short name for cross-file resolution
                short_name = symbol.name.split(".")[-1] if "." in symbol.name else symbol.name
                global_symbols[short_name] = symbol
                global_symbols[symbol.name] = symbol

        # Pass 2: Extract edges
        resolver = NameResolver(global_symbols)

        # Index extension functions by receiver type. Pass 1 tags each
        # one's receiver in meta["extension_receiver"]; here those tags
        # become a name-based lookup, which pass 2 uses to match
        # ``receiver.extFn()`` and track the extension's return type (the
        # resolved edge itself comes from the receiver_type_dispatch
        # linker; WI-visaz, WI-lodij). Generic receivers
        # (``List<Int>``, ``Set<T>``) collapse to their base name so
        # ``nums: List<Int>`` matches ``fun List<Int>.sumSafe()``.
        # Read from every file's symbols, not from ``global_symbols``, which
        # keeps ONE symbol per name: a top-level ``fun module()`` there
        # replaced ``fun Application.module()``, so neither ``app.module()``
        # nor ``Application::module`` could find the extension. The dict by
        # id keeps each symbol once.
        ext_syms_by_id: dict[str, Symbol] = {
            sym.id: sym
            for analysis in file_analyses.values()
            for sym in analysis.symbols
            if (sym.meta or {}).get("extension_receiver")
        }
        extension_index: dict[str, list[Symbol]] = {}
        for sym in ext_syms_by_id.values():
            receiver_type = sym.meta["extension_receiver"]
            receiver_base = (
                receiver_type.split("<")[0]
                if "<" in receiver_type
                else receiver_type
            )
            extension_index.setdefault(receiver_base, []).append(sym)

        all_symbols: list[Symbol] = []
        all_edges: list[Edge] = []

        # WI-tipoh: every project symbol by qualified name, from EVERY file.
        # ``global_symbols`` keeps one symbol per name, so alone it would bind
        # an import to a same-named symbol at another path.
        project_by_qualified: dict[str, Symbol] = {}
        # Every type and top-level function by its short name: the candidates
        # ``type_visibility`` picks among when a variable is typed by a callee.
        declared_by_name: dict[str, list[Symbol]] = {}
        for analysis in file_analyses.values():
            for sym in analysis.symbols:
                if sym.qualified_name:
                    project_by_qualified.setdefault(sym.qualified_name, sym)
                if sym.kind in _KT_TYPE_KINDS or sym.kind == "function":
                    declared_by_name.setdefault(sym.name, []).append(sym)

        # Every file's import table (ADR-0064), by the path its symbols carry.
        import_scopes = {
            str(kt_file): analysis.import_scope for kt_file, analysis in file_analyses.items()
        }
        for kt_file, analysis in file_analyses.items():
            all_symbols.extend(analysis.symbols)

            edges = _extract_edges_from_file(
                kt_file, parser, analysis.symbol_by_name, global_symbols,
                analysis.imports, run, resolver,
                extension_index=extension_index, file_symbols=analysis.symbols,
                project_by_qualified=project_by_qualified,
                import_scopes=import_scopes,
                declared_by_name=declared_by_name,
            )
            # ADR-0015 Tier 1: annotate edges with dataflow access modes
            try:
                _kt_source = kt_file.read_bytes()
                _kt_tree = _parse_kotlin(parser, _kt_source)
                _kt_df = _get_dataflow_config("kotlin")
                if _kt_df is not None:
                    edges = _annotate_dataflow(edges, _kt_tree, _kt_source, _kt_df)
            except (OSError, IOError):  # pragma: no cover
                pass
            all_edges.extend(edges)

        run.files_analyzed = len(file_analyses)
        run.files_skipped = files_skipped
        run.duration_ms = int((time.time() - start_time) * 1000)

        # Extract inheritance edges (META-001: base_classes metadata -> extends/implements edges)
        # Build multi-value lookups for disambiguation (INV-015)
        class_by_name: dict[str, list[Symbol]] = {}
        interface_by_name: dict[str, list[Symbol]] = {}
        for s in all_symbols:
            if s.kind == "class":
                if s.name not in class_by_name:
                    class_by_name[s.name] = []
                class_by_name[s.name].append(s)
            elif s.kind == "interface":
                if s.name not in interface_by_name:
                    interface_by_name[s.name] = []
                interface_by_name[s.name].append(s)
        # Build per-symbol imports mapping for disambiguation
        sym_file_imports: dict[str, dict[str, str]] = {}
        for _kt_file, analysis in file_analyses.items():
            for sym in analysis.symbols:
                sym_file_imports[sym.id] = analysis.imports
        inheritance_edges = _extract_inheritance_edges(
            all_symbols, class_by_name, interface_by_name, sym_file_imports, run,
            package_by_path={
                str(kt_file): analysis.package
                for kt_file, analysis in file_analyses.items()
            },
        )
        all_edges.extend(inheritance_edges)

        # Extract annotation edges (decorators → decorated_by edges)
        annotation_edges = _extract_annotation_edges(all_symbols, global_symbols, run)
        all_edges.extend(annotation_edges)

        # Parse Gradle/Maven deps for tier classification of boundary nodes (WI-duhom)
        from hypergumbo_lang_mainstream.jvm_deps import parse_jvm_dependencies
        jvm_manifest = parse_jvm_dependencies(repo_root)

        return AnalysisResult(
            symbols=all_symbols,
            edges=all_edges,
            run=run,
            dependency_manifest=jvm_manifest if jvm_manifest.entries else None,
            import_scopes=import_scopes,
        )


_analyzer = KotlinAnalyzer()


def is_kotlin_tree_sitter_available() -> bool:
    """Check if tree-sitter-kotlin grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("kotlin")
def analyze_kotlin(repo_root: Path) -> AnalysisResult:
    """Analyze all Kotlin files in a repository (wrapper)."""
    return _analyzer.analyze(repo_root)
