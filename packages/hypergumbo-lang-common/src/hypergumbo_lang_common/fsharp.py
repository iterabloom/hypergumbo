# SPDX-License-Identifier: AGPL-3.0-or-later
"""F# analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse F# files and extract:
- Module definitions (named_module)
- Function/value definitions (function_or_value_defn): functions with
  parameters (kind ``function``) and module-level ``let`` values (kind
  ``variable``; function-body locals are not emitted)
- Record type definitions (record_type_defn) and their fields (kind
  ``field``, named ``Type.field``)
- Discriminated union definitions (union_type_defn)
- Class, interface and struct types (anon_type_defn, interface_type_defn)
- Members of any of those types (member_defn): methods, properties,
  additional constructors and explicit ``val`` fields
- Open statements (import_decl)
- Function call relationships, and calls, reads and constructions that reach
  a member or a type where the source names the target

If tree-sitter with F# support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Parse all files, extract symbols, and register the functions,
   module values, modules, records and unions in the global registry (fields,
   class-like types and members stay out of the resolver; see
   ``FsharpAnalyzer.register_symbol``), and index the types and members of
   the run (``_FsharpIndex``)
2. Pass 2: Detect calls; resolve what the call site itself decides against
   that index (``_FsharpCallResolver``), the rest against the global registry

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the F#-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for grammar (fsharp)
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency

F#-Specific Considerations
--------------------------
- F# is a functional-first language on .NET
- Functions defined with `let` keyword
- Record types for structured data
- Discriminated unions (sum types) for variants
- `open` statements import namespaces/modules
- Modules organize code hierarchically
- Pattern matching is pervasive
- .fs files may be Forth (Open Firmware Forth, GForth) — content heuristics disambiguate

Scope and identity (WI-tusad)
-----------------------------
A declaration inside a nested ``module A =`` is named with the module chain,
the way code outside the module refers to it: ``A.run``, ``A.Inner.deep``,
record ``A.Point`` and its field ``A.Point.x``, union ``A.Shape``; a class-level
``let helper`` inside ``type Shape(...) =`` is ``Shape.helper``. The file's
own ``module Demo`` header adds nothing (a top-level ``let run`` stays ``run``).
A function bound inside another function keeps its bare name and holds the
enclosing function in its scope chain (``run/inner``). A module-level ``let``
value (``variable``) is named with the module chain too (``A.x``; WI-tusad
scheme bump, INV-fabih): it kept its bare name, so ``let mutable x`` in
modules ``A`` and ``B`` shared one table key and deleting the first moved the
second's id. Its id is the backstop's variable table formula over that name;
the file's ``module`` symbol keeps its name. A qualified call (``A.run x``)
first looks up the dotted name, then falls back to the bare one.

Functions record their argument patterns in ``meta["parameters"]`` (one entry
per pattern, written as the pattern's text; ``()`` counts as one) and mint the
ADR-0035 producer stable_id through ``overload_arity.declared_arity_stable_id``
over the scope chain; records, fields and unions mint the same formula with no
arity. Before, all of them rode the backstop floor key, and ``run`` in
``module A`` and in ``module B`` (two same-name symbols in one file) were left
null.

Class types and members (WI-tamib)
----------------------------------
``type Shape(r) = ...`` is a ``class``, ``interface`` or ``struct`` by F#'s own
rule: an attribute (``[<Struct>]``, ``[<Interface>]``, ``[<AbstractClass>]``,
which also records the ``abstract`` modifier) or an explicit ``class`` /
``struct`` / ``interface ... end`` decides, otherwise a type with no
constructor whose body holds only abstract slots is an interface. These are
table kinds, so the id is the backstop's ``{kind}:{language}:{path}:{name}``
over the module-qualified name. Every type of an ``and`` chain is emitted and
spans its own definition (the first one from the ``type`` keyword or the
attributes above it); generic types are emitted too, and hash their arity
(``Gen`1``), so ``type Gen<'T>`` beside a plain ``type Gen`` moves neither the
plain type's id nor its fields'.

A ``member_defn`` is a symbol named ``Owner.member`` wherever its type holds
it: the type's body, an ``interface IFoo with`` block, a record or union's
``with member``, a ``type X with`` extension (the owner is the type the
extension names; the extension itself declares no type).

- ``member this.Area = ...``, ``member val Tag = ...``, a member with ``get`` /
  ``set`` accessors, an abstract ``Name : string``: ``property``.
- A member with argument patterns, an abstract ``Go : int -> int``:
  ``method``, with ``meta["parameters"]``. A tuple is flattened into the .NET
  parameter list it is (the grammar parses ``(k: float, j: float)`` as one
  typed pattern), ``()`` and ``unit`` are no parameter, ``?x`` has a default
  and a ``ParamArray`` argument is variadic.
- ``new(...) = ...``: ``constructor``, named ``Owner.new``. The primary
  constructor is the type's own header and gets no symbol.
- ``val X: int``: ``field``, like a record's.

Methods, properties and constructors mint ``declared_arity_stable_id`` over
the owner chain, the parameters (the typed tier when one declares a type, so
same-arity overloads differ) and the ``static`` / ``abstract`` / ``default`` /
``override`` keyword: an abstract slot and its ``default`` body share a name
and an arity. A member of ``interface IFoo with`` hashes the interface after
its owner, because a type may implement ``Go`` once per interface. An access
modifier is recorded in ``modifiers`` and not hashed.

A call is credited to the nearest enclosing declaration that has a symbol: a
function, a member, or the type for a call in its body but in no member
(``do``, a class-level ``let`` value). Before, a call in a member body had no
enclosing symbol and was dropped.

Calls that reach members and types (WI-tamib)
---------------------------------------------
A member nothing calls is a dead-code candidate, so the members come with the
edges the source decides. Members and class-like types stay OUT of the
name-keyed registry (a registered ``Shape.Scale`` would suffix-match every
``Scale`` call, whatever its receiver); ``_FsharpIndex`` holds them instead,
and ``_FsharpCallResolver`` asks it only for a type the call site names and
for a member of that type:

- ``Shape.Make 1.0``: a static member through its type. The type is found by
  F#'s own lookup over what the file shows: the written name under every
  enclosing namespace and module of the site, and under each ``open``. A type
  of another file is found only when its namespace or module is opened,
  shared or written; two visible types of one name decide nothing.
- ``Shape(1.0)``, ``new Shape(1.0)``: ``instantiates``, to the type. The
  primary constructor has no symbol of its own, so the type stands for it and
  records its parameters in ``meta["parameters"]``. When the argument count
  picks out an additional ``new(...)`` and the primary constructor does not
  take that count, the edge names that constructor.
- ``this.Scale 2.0`` (whatever the member calls its self identifier): a
  member of the enclosing type; ``this.Area`` read without arguments reaches
  the property.
- ``x.Scale 2.0`` where the file states ``x``'s type: ``(x: Shape)`` on a
  parameter, a ``let`` or a lambda, ``let x = Shape(...)``, ``let x =
  Shape.Make ...`` when ``Make`` declares its return type
  (``FileAnalysis.method_return_types``), a primary-constructor argument or a
  class-level or module-level ``let``. The NEAREST binder of ``x`` decides: an
  untyped inner ``x`` (a match pattern, a ``for``, a lambda) hides a typed
  outer one, and then nothing is drawn.
- ``helper x`` where a ``let`` around the site binds ``helper`` as a
  function: that function, by position (two classes' ``let inner`` and two
  bodies' ``let local`` share a name the registry cannot tell apart).

Overloads follow the C++ analyzer (``overload_arity.choose_overload``): the
one that takes the call's argument count; several are ONE edge to the first
by position with ``resolution_quality="ambiguous"`` and confidence
``1/sqrt(N)``; none keeps the name's pick, because a tuple passed as one
value and a partial application make the count unreliable. An abstract slot
gives way to a body of the same name in the type (its ``default``).

Not resolved, by design: a receiver with no stated type (never the member
name alone), a member reached through a property or a longer chain
(``x.Inner.Scale``), a method named without being applied (``List.map
this.Go``), interface dispatch from a slot to its implementations, and types
brought into scope by ``[<AutoOpen>]``.

Visibility: the analyzer sets ``is_exported`` on no symbol. It records an
access modifier (``private`` / ``internal``) on types and members, the
narrower of a member's own and its type's; finalize derives ``visibility``
from it and a False ``is_exported`` for anything not public.

Kept as they were, because changing them would move ids already emitted: a
function bound in a member body is named after the type (``Shape.local``), not
scoped under the member; a class-level ``let`` in a GENERIC class has a bare
name; a function's ``()`` argument counts as one parameter.

Not emitted: a member of an object expression (``{ new IFoo with member
... }``; its calls stay with the enclosing function), a class-level ``let``
value, enum, delegate, exception and abbreviation types, and a binding with
an optional ``?x`` argument, which the grammar parses as a value pattern. The
bundled grammar does not parse ``.fsi`` signature members, nor the last
declaration of a file that ends without a newline; a type it rejects is an
ERROR node and emits nothing.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import (
    TYPE_CHECKING, ClassVar, Iterator, Mapping, NamedTuple, Optional, Sequence,
)

from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    SymbolsAt,
    TreeSitterAnalyzer,
    enclosing_declared_symbol,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    node_text,
    symbol_declared_by,
    symbols_at,
)
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, StableId, Symbol, make_pass_id
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity
from hypergumbo_core.overload_arity import (
    PARAMETERS_KEY, arity_bounds, choose_overload, declared_arity,
    declared_arity_stable_id, narrow_by_arity, parameter_entry,
    scoped_table_stable_id,
)

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("fsharp")

# Forth files often use .fs extension (Open Firmware Forth, GForth, etc.)
# These patterns indicate Forth rather than F#
_FORTH_PATTERNS = [
    r"^\\ ",  # Forth line comment (backslash followed by space)
    r"^: \w",  # Forth word definition (colon followed by space and word)
    r"\bVALUE\b",  # Forth VALUE word
    r"\bCONSTANT\b",  # Forth CONSTANT word
    r"\bVARIABLE\b",  # Forth VARIABLE word
    r"\bCREATE\b",  # Forth CREATE word
    r"\bDOES>\b",  # Forth DOES> word
    r"\bINCLUDE\b",  # Forth INCLUDE word
]


def _is_likely_forth_file(path: Path, sample_lines: int = 30) -> bool:
    """Check if a .fs file is likely Forth rather than F#.

    Forth and F# both use .fs extension. This function reads the first N lines
    and checks for Forth-specific patterns to avoid parsing Forth as F#.

    Args:
        path: Path to the .fs file.
        sample_lines: Number of lines to sample for detection.

    Returns:
        True if the file appears to be Forth, False if it appears to be F#.
    """
    import re

    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            # Read first N lines for pattern matching
            lines = []
            for i, line in enumerate(f):
                if i >= sample_lines:
                    break
                lines.append(line)
            content = "".join(lines)

            # Check for Forth patterns
            for pattern in _FORTH_PATTERNS:
                if re.search(pattern, content, re.MULTILINE):
                    return True

            return False
    except (OSError, IOError):
        return False


def find_fsharp_files(repo_root: Path) -> Iterator[Path]:
    """Yield all F# files in the repository.

    Filters out .fs files that appear to be Forth (Open Firmware Forth, GForth)
    based on content heuristics.
    """
    for path in find_files(repo_root, ["*.fs", "*.fsi", "*.fsx"]):
        # .fsi and .fsx are unambiguously F#, only .fs needs disambiguation
        if path.suffix == ".fs" and _is_likely_forth_file(path):
            continue
        yield path


def _extract_long_identifier(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract full identifier from long_identifier node."""
    parts = []
    for child in node.children:
        if child.type == "identifier":
            parts.append(node_text(child, source))
    return ".".join(parts)


def _extract_fsharp_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from an F# function_or_value_defn node.

    Returns signature like:
    - "(x: int, y: int): int" for functions with return type
    - "(message: string)" for functions without explicit return type
    - "(): int" for unit parameter functions

    Args:
        node: The function_or_value_defn node.
        source: The source code bytes.

    Returns:
        The signature string, or None if extraction fails.
    """
    params: list[str] = []
    return_type: Optional[str] = None
    found_func_decl = False

    for child in node.children:
        if child.type == "function_declaration_left":
            found_func_decl = True
            # Look for argument_patterns
            for grandchild in child.children:
                if grandchild.type == "argument_patterns":
                    for arg_child in grandchild.children:
                        if arg_child.type == "typed_pattern":
                            # Pattern: identifier_pattern : simple_type
                            param_name = None
                            param_type = None
                            for pattern_child in arg_child.children:
                                if pattern_child.type == "identifier_pattern":
                                    id_node = find_child_by_type(pattern_child, "long_identifier_or_op")
                                    if id_node:
                                        name_node = find_child_by_type(id_node, "identifier")
                                        if name_node:
                                            param_name = node_text(name_node, source)
                                    else:  # pragma: no cover - defensive fallback
                                        # May be a direct identifier
                                        param_name = node_text(pattern_child, source)
                                elif pattern_child.type == "simple_type":
                                    param_type = node_text(pattern_child, source)
                            if param_name and param_type:
                                params.append(f"{param_name}: {param_type}")
                        elif arg_child.type == "const":
                            # Check for unit ()
                            unit_node = find_child_by_type(arg_child, "unit")
                            if unit_node:
                                pass  # unit means no params, just skip
        elif found_func_decl and child.type == "simple_type":
            # Return type annotation after function_declaration_left
            return_type = node_text(child, source)

    params_str = ", ".join(params)
    signature = f"({params_str})"

    if return_type:
        signature += f": {return_type}"

    return signature


_FSHARP_MODULE_SCOPES = frozenset({"named_module", "module_defn", "file"})


def _fsharp_value_is_module_level(node: "tree_sitter.Node") -> bool:
    """Return True if a value ``function_or_value_defn`` is at module scope.

    A module-level ``let x = …`` binding sits under
    ``declaration_expression > <module scope>``, where the scope is
    ``named_module`` / ``module_defn`` (a nested ``module N =`` body) or ``file``
    (a headerless ``.fsx`` script / implicit-module ``.fs`` file, which the
    bundled grammar wraps directly in ``file`` with no module node). A
    function-body local ``let`` reuses the SAME ``function_or_value_defn`` node
    but its ``declaration_expression`` is nested inside the enclosing binding's
    ``function_or_value_defn`` — so the local is excluded even in a headerless
    file (WI-jusus: emit only module-level bindings as ``variable`` data anchors,
    never leak locals). The module-scope allowlist fails safe — a value under an
    unrecognized scope (e.g. a lambda / computation-expression body) is a recall
    miss, never a wrong emission.
    """
    decl_expr = node.parent
    if decl_expr is None:
        return False  # pragma: no cover - defensive: a defn always has a parent
    scope = decl_expr.parent
    return scope is not None and scope.type in _FSHARP_MODULE_SCOPES


def _fsharp_module_prefix(node: "tree_sitter.Node", source: bytes) -> str:
    """``A.Inner.`` for a declaration inside ``module A = ... module Inner = ...``.

    Nested ``module_defn`` blocks count, and so does a class type for a
    class-level ``let`` (``Shape.helper``); the file's ``named_module`` header
    (``module Demo``) adds nothing.
    """
    parts: list[str] = []
    current = node.parent
    while current is not None:
        name_node = None
        if current.type == "module_defn":
            name_node = find_child_by_type(current, "identifier")
        elif current.type == "anon_type_defn":
            type_name = find_child_by_type(current, "type_name")
            name_node = find_child_by_type(type_name, "identifier") if type_name else None
        if name_node is not None:
            parts.append(node_text(name_node, source))
        current = current.parent
    return "".join(f"{part}." for part in reversed(parts))


def _fsharp_function_names(
    node: "tree_sitter.Node", source: bytes, func_name: str,
) -> tuple[str, str]:
    """``(symbol name, scope chain)`` of the function ``node`` binds.

    A function bound inside another function keeps its bare name; its chain is
    the enclosing function's chain plus ``/name``. Otherwise both are the
    module-qualified name.
    """
    current = node.parent
    while current is not None:
        if current.type == "function_or_value_defn":
            outer = _fsharp_function_name(current, source)
            if outer is not None:
                return func_name, (
                    f"{_fsharp_function_names(current, source, outer)[1]}/{func_name}"
                )
        current = current.parent
    qualified = _fsharp_module_prefix(node, source) + func_name
    return qualified, qualified


def _fsharp_function_name(
    node: "tree_sitter.Node", source: bytes,
) -> Optional[str]:
    """The name a ``function_or_value_defn`` binds as a function, else None."""
    func_left = find_child_by_type(node, "function_declaration_left")
    name_node = find_child_by_type(func_left, "identifier") if func_left else None
    return node_text(name_node, source) if name_node is not None else None


def _fsharp_parameters(
    func_left: "tree_sitter.Node", source: bytes,
) -> list[dict[str, object]]:
    """``meta["parameters"]``: each argument pattern of ``function_declaration_left``."""
    args = find_child_by_type(func_left, "argument_patterns")
    if args is None:
        return []  # pragma: no cover - a function_declaration_left has arguments
    return [parameter_entry(node_text(child, source)) for child in args.named_children]


def _fsharp_member_stable_id(kind: str, file_path: str, qualified: str) -> StableId:
    """The producer stable_id of a record, union or field (no arity)."""
    return declared_arity_stable_id(
        kind=kind, lang="fsharp", path=file_path,
        name=qualified.rsplit(".", 1)[-1], qualified_name=qualified, arity=None,
    )


#: The type definitions that are a symbol and can hold members.
_FSHARP_TYPE_DEFNS = frozenset({
    "anon_type_defn", "interface_type_defn", "record_type_defn", "union_type_defn",
})

#: What a ``member_defn`` belongs to: one of those, or a ``type X with``
#: extension, which declares no type of its own.
_FSHARP_MEMBER_OWNERS = _FSHARP_TYPE_DEFNS | {"type_extension"}

#: The nodes a symbol's span can start at, for the enclosing-symbol walk. A
#: type's span starts at its ``type_definition`` (the ``type`` keyword or the
#: attributes above it), or at its own definition after ``and``.
_FSHARP_CALLER_NODES = _FSHARP_TYPE_DEFNS | {
    "function_or_value_defn", "member_defn", "type_definition",
}

#: Member keywords that are modifiers AND identity inputs: an ``abstract`` slot
#: and its ``default`` body share a name and an arity.
_FSHARP_MEMBER_KEYWORDS = frozenset({"static", "abstract", "default", "override"})

#: In a type's body, what an interface cannot hold.
_FSHARP_CONCRETE_ELEMENTS = frozenset({
    "primary_constr_args", "function_or_value_defn", "interface_implementation", "do",
})

#: Kinds kept out of the call resolver (see ``FsharpAnalyzer.register_symbol``).
_FSHARP_UNREGISTERED_KINDS = frozenset({
    "field", "class", "interface", "struct", "method", "property", "constructor",
})


def _fsharp_parts(node: "tree_sitter.Node") -> list["tree_sitter.Node"]:
    """A node's named children without comments, which the grammar lets sit
    anywhere (a ``// note`` after a member's body is that member's last child)."""
    return [child for child in node.named_children if "comment" not in child.type]


def _fsharp_type_text(node: "tree_sitter.Node", source: bytes) -> str:
    """A type as written, on one line."""
    return " ".join(node_text(node, source).split())


def _fsharp_last_type(node: "tree_sitter.Node", source: bytes) -> str:
    """The type a node ends with (``pattern : type``, ``args -> type``)."""
    return "".join(_fsharp_type_text(part, source) for part in _fsharp_parts(node)[-1:])


#: Access modifiers, narrowest first.
_FSHARP_ACCESS_RANK = {"private": 0, "internal": 1, "public": 2}


def _fsharp_access(
    source: bytes, *holders: Optional["tree_sitter.Node"],
) -> list[str]:
    """The narrowest access modifier written on ``holders``, as a modifier list.

    A member is given its own node and its owner's ``type_name``: a member is
    no more visible than its type, so ``member this.M`` of ``type private T``
    is private. Nothing written is nothing recorded (public by default).
    """
    written = [
        node_text(child, source)
        for holder in holders if holder is not None
        for child in holder.children if child.type == "access_modifier"
    ]
    return sorted(written, key=lambda term: _FSHARP_ACCESS_RANK.get(term, 2))[:1]


def _fsharp_type_identity(
    defn: "tree_sitter.Node", source: bytes,
) -> Optional[tuple[str, str]]:
    """``(name, hashed name)`` of a type definition or extension.

    The hashed name carries the generic arity the .NET way (``Gen`1``), so
    ``type Gen<'T>`` and a plain ``type Gen`` of one module, which F# allows
    side by side, do not tie; a plain type hashes its bare name.
    """
    type_name = find_child_by_type(defn, "type_name")
    name_node = type_name.child_by_field_name("type_name") if type_name else None
    if type_name is None or name_node is None:
        return None  # pragma: no cover - a type definition names its type
    name = node_text(name_node, source)
    type_args = find_child_by_type(type_name, "type_arguments")
    arity = sum(
        child.type == "type_argument_defn" for child in type_args.children
    ) if type_args else 0
    return name, f"{name}`{arity}" if arity else name


def _fsharp_declares_only_slots(defn: "tree_sitter.Node") -> bool:
    """True when a type's body holds abstract slots and nothing concrete."""
    slots = 0
    for node in iter_tree(defn):
        if node.type in _FSHARP_CONCRETE_ELEMENTS:
            return False
        if node.type == "member_defn":
            if find_child_by_type(node, "member_signature") is None:
                return False
            slots += 1
    return slots > 0


def _fsharp_class_kind(
    type_def: "tree_sitter.Node", defn: "tree_sitter.Node", source: bytes,
    chained: bool,
) -> tuple[str, list[str]]:
    """``(kind, modifiers)`` of a class-like type, by F#'s own inference.

    An attribute or an explicit ``class`` / ``struct`` / ``interface ... end``
    decides; otherwise a type with no constructor whose body is only abstract
    slots is an interface, and anything else a class. Attributes sit on the
    ``type_definition`` for its first type and inside ``type_name`` after
    ``and``.
    """
    if defn.type == "interface_type_defn":
        return "interface", []
    holders = [find_child_by_type(defn, "type_name")] + ([] if chained else [type_def])
    attributes = {
        node_text(attribute, source).split("(", 1)[0].strip()
        .rsplit(".", 1)[-1].removesuffix("Attribute")
        for holder in holders if holder is not None
        for group in holder.children if group.type == "attributes"
        for attribute in group.named_children
    }
    keywords = {child.type for child in defn.children}
    if "Struct" in attributes or "struct" in keywords:
        return "struct", []
    if "Interface" in attributes:
        return "interface", []
    if "AbstractClass" in attributes:
        return "class", ["abstract"]
    if "Class" in attributes or "class" in keywords:
        return "class", []
    return ("interface" if _fsharp_declares_only_slots(defn) else "class"), []


def _fsharp_type_symbols(
    type_def: "tree_sitter.Node", defn: "tree_sitter.Node", source: bytes,
    file_path: str, run_id: str, chained: bool,
) -> list[Symbol]:
    """The symbol of one type definition, and a record's fields.

    ``chained`` is True after ``and``: the type then spans its own definition.
    The first type of a ``type_definition`` starts where that node does, so
    the ``type`` keyword and the attributes above it are inside the span.
    """
    identity = _fsharp_type_identity(defn, source)
    if identity is None:
        return []  # pragma: no cover - a type definition names its type
    prefix = _fsharp_module_prefix(type_def, source)
    type_name, hashed = prefix + identity[0], prefix + identity[1]
    start = defn if chained else type_def
    start_line = start.start_point[0] + 1
    end_line = defn.end_point[0] + 1
    span = Span(
        start_line=start_line,
        end_line=end_line,
        start_col=start.start_point[1],
        end_col=defn.end_point[1],
    )
    modifiers: list[str] = []
    meta: Optional[dict[str, object]] = None
    line_span: Optional[int] = None
    if defn.type == "record_type_defn":
        kind = "record"
        stable_id = _fsharp_member_stable_id(kind, file_path, hashed)
    elif defn.type == "union_type_defn":
        kind = "union"
        stable_id = _fsharp_member_stable_id(kind, file_path, hashed)
    else:
        kind, modifiers = _fsharp_class_kind(type_def, defn, source, chained)
        # A table kind: the backstop's own formula, over the module-qualified
        # name (types do not nest in types, so the name is the whole scope).
        stable_id = scoped_table_stable_id(
            kind=kind, lang="fsharp", path=file_path, name=hashed,
        )
        line_span = end_line - start_line + 1
        # The primary constructor has no symbol of its own; its parameters
        # are the type's, which is what a construction's arity is read from.
        primary = find_child_by_type(defn, "primary_constr_args")
        if primary is not None:
            meta = {PARAMETERS_KEY: [
                entry for part in _fsharp_parts(primary)
                if part.type != "access_modifier"
                for entry in _fsharp_pattern_parameters(part, source)
            ]}
    modifiers = modifiers + _fsharp_access(source, find_child_by_type(defn, "type_name"))
    symbols = [Symbol(
        id=make_symbol_id("fsharp", file_path, start_line, end_line, type_name, kind),
        stable_id=stable_id,
        name=type_name,
        kind=kind,
        language="fsharp",
        path=file_path,
        span=span,
        origin=PASS_ID,
        origin_run_id=run_id,
        modifiers=modifiers,
        meta=meta,
        line_span=line_span,
    )]
    # WI-jusus: record members are `field` data anchors (owner = the record
    # type). A `record_field`'s name is its first `identifier` child (a
    # `mutable` modifier precedes it, which find_child_by_type skips).
    fields_node = find_child_by_type(defn, "record_fields")
    for field in fields_node.children if fields_node else ():
        fname_node = find_child_by_type(field, "identifier")
        if field.type != "record_field" or fname_node is None:
            continue  # pragma: no cover - record_fields holds named record_field nodes
        f_start = field.start_point[0] + 1
        f_end = field.end_point[0] + 1
        fname = node_text(fname_node, source)
        qualified = f"{type_name}.{fname}"
        symbols.append(Symbol(
            id=make_symbol_id("fsharp", file_path, f_start, f_end, qualified, "field"),
            stable_id=_fsharp_member_stable_id("field", file_path, f"{hashed}.{fname}"),
            name=qualified,
            kind="field",
            language="fsharp",
            path=file_path,
            span=Span(
                start_line=f_start,
                end_line=f_end,
                start_col=field.start_point[1],
                end_col=field.end_point[1],
            ),
            origin=PASS_ID,
            origin_run_id=run_id,
        ))
    return symbols


def _fsharp_member_owner(
    node: "tree_sitter.Node", source: bytes,
) -> Optional[tuple[str, str, "tree_sitter.Node"]]:
    """``(owner name, hashed owner chain, owner node)`` of a ``member_defn``,
    else None.

    The owner is the type the member is written in, or the type a ``type X
    with`` extension names. A member inside ``interface IFoo with`` hashes the
    interface after the owner (``Bar.IFoo``): a type may implement ``Go`` once
    per interface and once of its own, all three named ``Bar.Go``. None for a
    member of an object expression (``{ new IFoo with member ... }``), which
    is an anonymous implementation inside a function and declares no type.
    """
    interface = ""
    current = node.parent
    while current is not None:
        if current.type == "object_expression":
            return None
        if current.type == "interface_implementation":
            interface = "".join(
                "." + "".join(node_text(implemented, source).split())
                for implemented in _fsharp_parts(current)[:1]
            )
        elif current.type in _FSHARP_MEMBER_OWNERS:
            identity = _fsharp_type_identity(current, source)
            if identity is None:
                return None  # pragma: no cover - a type definition names its type
            prefix = _fsharp_module_prefix(current, source)
            return prefix + identity[0], prefix + identity[1] + interface, current
        current = current.parent
    # A type the grammar could not parse: its members sit under an ERROR node.
    return None


def _fsharp_pattern_parameters(
    pattern: "tree_sitter.Node", source: bytes,
) -> list[dict[str, object]]:
    """``meta["parameters"]`` entries for one argument pattern of a member.

    A tuple is a .NET parameter list, so it is flattened; ``()`` is a member
    taking nothing. The grammar parses ``(k: float, j: float)`` as ONE
    ``typed_pattern`` whose left side is the tuple ``k: float, j``: the type
    it carries is the LAST element's.
    """
    kind = pattern.type
    if kind == "const" and find_child_by_type(pattern, "unit") is not None:
        return []
    parts = _fsharp_parts(pattern)
    if kind in ("paren_pattern", "repeat_pattern", "argument_patterns"):
        return [
            entry for child in parts
            for entry in _fsharp_pattern_parameters(child, source)
        ]
    if parts and kind in (
        "typed_pattern", "as_pattern", "optional_pattern", "attribute_pattern",
    ):
        # The pattern is the last child after its attributes, else the first
        # (before ``: type`` and ``as self``).
        inner = parts[-1 if kind == "attribute_pattern" else 0]
        entries = _fsharp_pattern_parameters(inner, source)
        for entry in entries[-1:] if kind == "typed_pattern" else ():
            entry["type"] = _fsharp_type_text(parts[-1], source)
        for entry in entries:
            if kind == "optional_pattern":
                entry["default"] = True
            elif kind == "attribute_pattern" and "ParamArray" in node_text(pattern, source):
                entry["variadic"] = True
        return entries
    return [parameter_entry(node_text(pattern, source))]


def _fsharp_slot_decl(
    signature: "tree_sitter.Node", source: bytes,
) -> Optional[tuple[str, str, Optional[list[dict[str, object]]], str]]:
    """An abstract slot (``member_signature``), in ``_fsharp_member_decl``'s shape.

    ``Go : int -> int`` is a method; ``Name : string`` a property. An argument
    may be named (``x: int``) and optional (``?x: int``); ``unit`` is no
    parameter.
    """
    spec = find_child_by_type(signature, "curried_spec")
    if spec is None:
        return None  # pragma: no cover - a slot declares its type
    name = node_text(_fsharp_parts(signature)[0], source)
    groups = [child for child in spec.children if child.type == "arguments_spec"]
    if not groups:
        return name, "property", None, ""
    params: list[dict[str, object]] = []
    for group in groups:
        for arg in _fsharp_parts(group):
            type_text = _fsharp_last_type(arg, source)
            name_spec = find_child_by_type(arg, "argument_name_spec")
            arg_name = name_spec.child_by_field_name("name") if name_spec else None
            if name_spec is not None and arg_name is not None:
                params.append(parameter_entry(
                    node_text(arg_name, source), type_text,
                    default=node_text(name_spec, source).startswith("?"),
                ))
            elif type_text != "unit":
                params.append(parameter_entry("", type_text))
    return name, "method", params, _fsharp_last_type(spec, source)


def _fsharp_member_arguments(
    defn: "tree_sitter.Node", source: bytes,
) -> tuple[list["tree_sitter.Node"], str]:
    """``(argument patterns, return type)`` of a ``method_or_prop_defn``.

    An argument the grammar types OUTSIDE parentheses carries the member's
    return type, not its own (``member this.R(x: int) : int`` is one
    ``typed_pattern`` around ``(x: int)``): the pattern is its first part.
    """
    patterns: list["tree_sitter.Node"] = []
    result = ""
    for arg in defn.children_by_field_name("args"):
        parts = _fsharp_parts(arg)
        if arg.type == "typed_pattern" and parts:
            result = _fsharp_type_text(parts[-1], source)
            arg = parts[0]
        patterns.append(arg)
    return patterns, result


def _fsharp_member_decl(
    node: "tree_sitter.Node", source: bytes,
) -> Optional[tuple[str, str, Optional[list[dict[str, object]]], str]]:
    """``(name, kind, parameters, return type)`` of a ``member_defn``.

    ``parameters`` is None for a property or a field. A member with argument
    patterns is a method; one with none, or with ``get`` / ``set`` accessors,
    is a property.
    """
    defn = find_child_by_type(node, "method_or_prop_defn")
    ident = defn.child_by_field_name("name") if defn else None
    if defn is not None and ident is not None:
        names = _fsharp_parts(ident) or [ident]
        name = node_text(names[-1], source)
        patterns, result = _fsharp_member_arguments(defn, source)
        if name == "val" and len(names) == 1 and len(patterns) == 1:
            # ``static member val X = 0``: the grammar reads a method named
            # ``val`` taking ``X``.
            return node_text(patterns[0], source), "property", None, ""
        if not patterns:
            return name, "property", None, ""
        return name, "method", [
            entry for pattern in patterns
            for entry in _fsharp_pattern_parameters(pattern, source)
        ], result
    constructor = find_child_by_type(node, "additional_constr_defn")
    if constructor is not None:
        return "new", "constructor", [
            entry for pattern in _fsharp_parts(constructor)[:1]
            for entry in _fsharp_pattern_parameters(pattern, source)
        ], ""
    signature = find_child_by_type(node, "member_signature")
    if signature is not None:
        return _fsharp_slot_decl(signature, source)
    auto_property = find_child_by_type(node, "property_or_ident")
    if auto_property is not None:
        return node_text(auto_property, source), "property", None, ""
    explicit_field = find_child_by_type(node, "identifier")
    if explicit_field is not None:
        return node_text(explicit_field, source), "field", None, ""
    return None  # pragma: no cover - every member form above names itself


def _fsharp_member_symbol(
    node: "tree_sitter.Node", source: bytes, file_path: str, run_id: str,
    returns: dict[str, set[str]],
) -> Optional[Symbol]:
    """The symbol of a ``member_defn``: a method, property, constructor or field.

    Named ``Owner.member`` like a record field; an additional constructor is
    ``Owner.new``. The stable_id is the ADR-0035 formula over the owner chain,
    the declared parameters (the typed tier when one declares a type, so
    overloads differ) and the ``static`` / ``abstract`` / ``default`` /
    ``override`` keyword. An access modifier is recorded, not hashed, and is
    the narrower of the member's own and its type's. A declared return type
    is added to ``returns`` under the member's name.
    """
    owner = _fsharp_member_owner(node, source)
    if owner is None:
        return None
    decl = _fsharp_member_decl(node, source)
    if decl is None:
        return None  # pragma: no cover - every member form names itself
    name, kind, params, result = decl
    display, chain = f"{owner[0]}.{name}", f"{owner[1]}.{name}"
    keywords = [
        child.type for child in node.children if child.type in _FSHARP_MEMBER_KEYWORDS
    ]
    modifiers = keywords + _fsharp_access(
        source, node, find_child_by_type(owner[2], "type_name"),
    )
    if result:
        returns.setdefault(display, set()).add(result)
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    meta: Optional[dict[str, object]] = (
        {PARAMETERS_KEY: params} if params is not None else None
    )
    if kind == "field":
        stable_id = _fsharp_member_stable_id(kind, file_path, chain)
    else:
        stable_id = declared_arity_stable_id(
            kind=kind, lang="fsharp", path=file_path, name=name,
            qualified_name=chain, arity=declared_arity(meta),
            decorators=",".join(sorted(keywords)),
        )
    signature: Optional[str] = None
    if params is not None:
        signature = "(" + ", ".join(
            f"{p['name']}: {p['type']}" if p["name"] and p["type"]
            else str(p["type"] or p["name"])
            for p in params
        ) + ")" + (f": {result}" if result else "")
    return Symbol(
        id=make_symbol_id("fsharp", file_path, start_line, end_line, display, kind),
        stable_id=stable_id,
        meta=meta,
        name=display,
        kind=kind,
        language="fsharp",
        path=file_path,
        span=Span(
            start_line=start_line,
            end_line=end_line,
            start_col=node.start_point[1],
            end_col=node.end_point[1],
        ),
        origin=PASS_ID,
        origin_run_id=run_id,
        modifiers=modifiers,
        signature=signature,
        cyclomatic_complexity=(
            compute_cyclomatic_complexity(node, "fsharp") if params is not None else None
        ),
        line_span=end_line - start_line + 1 if params is not None else None,
    )


#: Kinds a type name can resolve to, and the ones an application constructs.
_FSHARP_TYPE_KINDS = frozenset({"class", "interface", "struct", "record", "union"})
_FSHARP_CONSTRUCTED_KINDS = frozenset({"class", "struct"})

#: Members a call or a read can reach (a ``val`` field is data).
_FSHARP_MEMBER_KINDS = frozenset({"method", "property", "constructor"})

#: A type written as a plain name, with or without type arguments: ``Shape``,
#: ``Geo.Shape``, ``Box<int>``. ``Shape list``, ``Shape option``, ``Shape[]``
#: and ``'T`` are other types and match nothing.
_FSHARP_PLAIN_TYPE = re.compile(r"([A-Za-z_][\w.]*)(<.*>)?")

#: What a dotted path is made of, however the grammar nests it.
_FSHARP_PATH_NODES = frozenset({"long_identifier_or_op", "long_identifier", "identifier", "."})

#: Scopes whose ``let`` bindings every declaration inside them can see.
_FSHARP_SCOPE_NODES = _FSHARP_TYPE_DEFNS | _FSHARP_MODULE_SCOPES | {"namespace"}

#: Parents under which a dotted path is no read of its own: the inner part of
#: a longer path, a generic callee, a pattern.
_FSHARP_NOT_A_READ = frozenset({"dot_expression", "typed_expression", "identifier_pattern"})


class _FsharpTarget(NamedTuple):
    """What a call site resolved to, and how sure the choice among overloads is."""

    symbol: Symbol
    edge_type: str
    construct: str
    confidence: float
    ambiguous: bool


class _FsharpBinding(NamedTuple):
    """What the nearest binder of a name binds it to: a value of ``type`` (None
    when the source states none), or the ``let``-bound ``function``."""

    type: Optional[Symbol] = None
    function: Optional[Symbol] = None


def _fsharp_pick(
    tiers: Sequence[Sequence[Symbol]], arg_count: Optional[int],
    edge_type: str, construct: str,
) -> _FsharpTarget:
    """One of the same-named ``tiers`` members, by ``choose_overload``.

    The C++ analyzer's convention: one that takes the call's argument count is
    the callee; several are an ambiguous record (one edge, to the first by
    position, confidence ``1/sqrt(N)``); none that does keeps the name's pick,
    because the count read off an F# call site is not reliable (a tuple passed
    as one value, a partial application).
    """
    choice = choose_overload(tiers, arg_count)
    if choice.symbol is None:
        choice = choose_overload(tiers, None)
    symbol = choice.symbol or next(sym for tier in tiers for sym in tier)
    return _FsharpTarget(symbol, edge_type, construct, choice.confidence, choice.ambiguous)


def _fsharp_file_names(
    root: "tree_sitter.Node", source: bytes,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(scopes, opens)`` of a file: the names of its ``namespace`` / ``module``
    headers, and every name it ``open``s (in nested modules too)."""
    scopes: list[str] = []
    opens: list[str] = []
    stack = [root]
    while stack:
        for child in stack.pop().children:
            long_id = find_child_by_type(child, "long_identifier")
            name = _extract_long_identifier(long_id, source) if long_id else ""
            if child.type == "import_decl":
                opens.append(name)
            elif child.type in ("named_module", "namespace"):
                scopes.append(name)
                stack.append(child)
            elif child.type == "module_defn":
                stack.append(child)
    return tuple(filter(None, scopes)), tuple(filter(None, opens))


class _FsharpIndex:
    """The types and members of one run, for the calls pass 2 resolves to them.

    The base registry is keyed by name and keeps one symbol per key, so it
    cannot hold two overloads, and its resolver matches a bare suffix, which
    for a member would bind every ``Scale`` call in the repository. This index
    is asked only for a type the call site names and for a member OF that type.
    """

    def __init__(self) -> None:
        self.types: dict[str, list[Symbol]] = {}
        self.members: dict[str, list[Symbol]] = {}
        self.scopes: dict[str, tuple[str, ...]] = {}
        self.opens: dict[str, tuple[str, ...]] = {}

    def add_file(
        self, path: str, symbols: Sequence[Symbol],
        scopes: tuple[str, ...], opens: tuple[str, ...],
    ) -> None:
        """Record one file's declarations and the names it is written under."""
        self.scopes[path] = scopes
        self.opens[path] = opens
        for sym in symbols:
            if sym.kind in _FSHARP_TYPE_KINDS:
                self.types.setdefault(sym.name.rsplit(".", 1)[-1], []).append(sym)
            elif sym.kind in _FSHARP_MEMBER_KINDS:
                self.members.setdefault(sym.name, []).append(sym)

    def _full_names(self, sym: Symbol) -> set[str]:
        """A type's name under each header of its file (``Geo.Lib.M.Shape``)."""
        return {
            f"{scope}.{sym.name}" if scope else sym.name
            for scope in self.scopes.get(sym.path) or ("",)
        }

    def type_named(
        self, written: str, path: str, chain: Sequence[str],
    ) -> Optional[Symbol]:
        """The type ``written`` names at a site in ``path`` inside the nested
        modules ``chain``, or None when no type, or more than one, fits.

        F#'s own lookup, over what the source shows: the written name is tried
        as it stands and under every enclosing scope of the site (its
        namespace and each prefix of it, its module header, its nested
        modules), and again under each name the file ``open``s, itself read
        from those scopes (``open M`` inside ``module Demo`` is ``Demo.M``).
        A type of another file is therefore found only when its namespace or
        module is opened, shared or written. Two fits are decided by the
        site's own file, else not at all.
        """
        scopes = {""}
        for scope in self.scopes.get(path) or ("",):
            parts = [part for part in scope.split(".") if part] + list(chain)
            scopes.update(".".join(parts[:end]) for end in range(1, len(parts) + 1))
        prefixes = scopes | {
            f"{scope}.{opened}" if scope else opened
            for scope in scopes for opened in self.opens.get(path, ())
        }
        wanted = {f"{prefix}.{written}" if prefix else written for prefix in prefixes}
        found = [
            sym for sym in self.types.get(written.rsplit(".", 1)[-1], [])
            if wanted & self._full_names(sym)
        ]
        found = [sym for sym in found if sym.path == path] or found
        return found[0] if len(found) == 1 else None

    def is_only(self, owner: Symbol) -> bool:
        """True when no other type of the run has ``owner``'s name."""
        same = self.types.get(owner.name.rsplit(".", 1)[-1], [])
        return sum(sym.name == owner.name for sym in same) == 1

    def members_of(self, owner: Symbol, name: str) -> list[Symbol]:
        """The members of ``owner`` named ``name``: those written inside its
        definition, else (a ``type X with`` extension) those written outside
        any, when the name ``Owner.member`` can belong to no other type."""
        named = self.members.get(f"{owner.name}.{name}", [])
        inside = [
            member for member in named
            if member.path == owner.path and owner.span is not None
            and member.span is not None
            and owner.span.start_line <= member.span.start_line
            and member.span.end_line <= owner.span.end_line
        ]
        return inside or (named if self.is_only(owner) else [])


def _fsharp_callee_path(
    node: "tree_sitter.Node", source: bytes,
) -> tuple[Optional["tree_sitter.Node"], list[str]]:
    """``(receiver expression, names)`` of a callee or a member access.

    ``A.B.c`` is ``(None, [A, B, c])`` whether the grammar gives one
    ``long_identifier`` or nests ``dot_expression`` nodes; ``Shape(1.0).Scale``
    is ``(the application, [Scale])``; ``Box<int>`` is its name. Anything else
    (an operator, a longer chain on an expression) is ``(None, [])``.
    """
    if node.type == "typed_expression":
        node = (_fsharp_parts(node) or [node])[0]
    if node.type == "long_identifier_or_op":
        leaves = list(iter_tree(node))
        if any(leaf.type not in _FSHARP_PATH_NODES for leaf in leaves):
            return None, []
        return None, [node_text(leaf, source) for leaf in leaves if leaf.type == "identifier"]
    parts = _fsharp_parts(node)
    if node.type == "dot_expression" and len(parts) == 2:
        receiver, names = _fsharp_callee_path(parts[0], source)
        member = _fsharp_callee_path(parts[1], source)[1]
        if receiver is None and names and member:
            return None, names + member
        if receiver is None and not names and len(member) == 1:
            return parts[0], member
    return None, []


def _fsharp_tuple_size(node: "tree_sitter.Node") -> int:
    """How many values a tuple expression holds (the grammar nests it rightward)."""
    if node.type != "tuple_expression":
        return 1
    return sum(_fsharp_tuple_size(part) for part in _fsharp_parts(node))


def _fsharp_argument_count(application: "tree_sitter.Node") -> int:
    """The arguments an application passes, counted as members declare them:
    a tuple is its elements, ``()`` is none, curried arguments add up."""
    count = 0
    for arg in _fsharp_parts(application)[1:]:
        inner = _fsharp_parts(arg)
        if arg.type == "paren_expression" and len(inner) == 1:
            arg = inner[0]
        if arg.type != "unit" and (
            arg.type != "const" or find_child_by_type(arg, "unit") is None
        ):
            count += _fsharp_tuple_size(arg)
    return count


class _FsharpCallResolver:
    """Decides, for one file, which member or type a call site names.

    Five forms, each decided by the source alone: a static member through its
    type (``Shape.Make 1.0``), a construction (``Shape(1.0)``), a member on the
    enclosing member's self identifier (``this.Scale 2.0``), a member on a
    value whose type the file states (an annotation, a construction, or a
    static call that declares its return type), and a ``let``-bound function
    called by its bare name from inside its scope (a class-level ``let
    helper`` from a member, a local function from the body it is bound in).
    A receiver bound with no stated type decides nothing, and nothing is
    matched by the member's name alone.
    """

    def __init__(
        self, index: _FsharpIndex, returns: Mapping[str, str], source: bytes,
        path: str, decl_index: SymbolsAt,
    ) -> None:
        self.index = index
        self.returns = returns
        self.source = source
        self.path = path
        self.decl_index = decl_index
        self._busy: set[int] = set()
        self._scope_lets: dict[int, dict[str, list["tree_sitter.Node"]]] = {}

    # -- types ---------------------------------------------------------------

    def type_named(self, written: str, site: "tree_sitter.Node") -> Optional[Symbol]:
        """The type ``written`` names at ``site`` (``_FsharpIndex.type_named``)."""
        chain: list[str] = []
        current = site.parent
        while current is not None:
            if current.type == "module_defn":
                chain[:0] = [
                    node_text(child, self.source) for child in current.children
                    if child.type == "identifier"
                ][:1]
            current = current.parent
        return self.index.type_named(written, self.path, chain)

    def type_from_text(self, text: object, site: "tree_sitter.Node") -> Optional[Symbol]:
        """The type an annotation names, when it is a plain type name."""
        match = _FSHARP_PLAIN_TYPE.fullmatch(text) if isinstance(text, str) else None
        return self.type_named(match.group(1), site) if match else None

    def owner_type(self, member: "tree_sitter.Node") -> Optional[Symbol]:
        """The type a ``member_defn`` belongs to: the one it is written in, or
        the one its ``type X with`` extension names. None in an object
        expression, whose self is an anonymous object."""
        current = member.parent
        while current is not None and current.type != "object_expression":
            if current.type in _FSHARP_TYPE_DEFNS:
                return symbol_declared_by(current, self.decl_index) or symbol_declared_by(
                    current.parent, self.decl_index,
                )
            if current.type == "type_extension":
                identity = _fsharp_type_identity(current, self.source)
                return self.type_named(identity[0], current) if identity else None
            current = current.parent
        return None

    def expression_type(self, expr: "tree_sitter.Node") -> Optional[Symbol]:
        """The type an expression is written to produce: ``T(...)``,
        ``new T(...)``, or ``T.Make ...`` when ``Make`` declares its return
        type (and no other ``T.Make`` declares another)."""
        while expr.type in ("paren_expression", "prefixed_expression"):
            parts = _fsharp_parts(expr)
            if len(parts) != 1 or (
                expr.type == "prefixed_expression" and expr.children[0].type != "new"
            ):
                return None
            expr = parts[0]
        if expr.type != "application_expression":
            return None
        receiver, names = _fsharp_callee_path(expr.children[0], self.source)
        if receiver is not None or not names:
            return None
        constructed = self.type_named(".".join(names), expr)
        if constructed is not None and constructed.kind in _FSHARP_CONSTRUCTED_KINDS:
            return constructed
        owner = self.type_named(".".join(names[:-1]), expr) if len(names) > 1 else None
        if (
            owner is None or not self.index.is_only(owner)
            or self.binding(names[0], expr) is not None
        ):
            return None
        match = _FSHARP_PLAIN_TYPE.fullmatch(self.returns.get(f"{owner.name}.{names[-1]}", ""))
        # The return type is written in the declaring file, among its modules.
        return self.index.type_named(
            match.group(1), owner.path, owner.name.split(".")[:-1],
        ) if match else None

    # -- what a name is bound to -----------------------------------------------

    def binding(
        self, name: str, site: "tree_sitter.Node",
    ) -> Optional[_FsharpBinding]:
        """What ``name`` is bound to at ``site``, or None when nothing in the
        file binds it (it is then a type or a module, or comes from another
        file).

        Walks outward and stops at the NEAREST binder (a ``let``, a parameter,
        a pattern, the member's self identifier), so an inner untyped ``x``
        hides a typed outer one, and a local ``let helper`` hides a module's.
        """
        child, node = site, site.parent
        while node is not None:
            found = self._bound_in(node, child, name)
            if found is not None:
                return found
            child, node = node, node.parent
        return None

    def _bound_in(
        self, node: "tree_sitter.Node", child: "tree_sitter.Node", name: str,
    ) -> Optional[_FsharpBinding]:
        """What ``node`` binds ``name`` to for code inside ``child``, or None
        when it does not bind it."""
        kind = node.type
        if kind == "member_defn":
            defn = find_child_by_type(node, "method_or_prop_defn")
            ident = defn.child_by_field_name("name") if defn is not None else None
            instance = ident.child_by_field_name("instance") if ident is not None else None
            if instance is not None and node_text(instance, self.source) == name:
                return _FsharpBinding(self.owner_type(node))
            return None
        if kind == "method_or_prop_defn":
            return self._bound_by_patterns(
                _fsharp_member_arguments(node, self.source)[0], name,
            )
        if kind == "function_or_value_defn":
            # A function's parameters, and itself under ``let rec``; a value's
            # own name is not in its body.
            left = find_child_by_type(node, "function_declaration_left")
            found = self._bound_by_patterns([
                part for part in (left.children if left is not None else [])
                if part.type == "argument_patterns"
            ], name)
            if found is None and find_child_by_type(node, "rec") is not None:
                return self._bound_by_let(node, name)
            return found
        if kind in _FSHARP_SCOPE_NODES:
            return self._bound_in_scope(node, child, name)
        # ``let ... in``, ``use``, ``for``, a match rule, a lambda, a property
        # accessor: whatever the children written before the site bind.
        for sibling in node.children:
            if sibling.start_byte >= child.start_byte:
                break
            if sibling.type == "function_or_value_defn":
                found = self._bound_by_let(sibling, name)
            elif sibling.type == "identifier" and kind in (
                "declaration_expression", "for_expression",
            ):
                found = self._bound_by_identifier(node, sibling, name)
            else:
                found = self._bound_by_patterns(
                    [sibling] if "pattern" in sibling.type else [], name,
                )
            if found is not None:
                return found
        return None

    def _bound_in_scope(
        self, scope: "tree_sitter.Node", child: "tree_sitter.Node", name: str,
    ) -> Optional[_FsharpBinding]:
        """A type's primary-constructor arguments and class-level ``let``s, or
        a module's ``let``s, other than the declaration the site is in."""
        primary = find_child_by_type(scope, "primary_constr_args")
        found = self._bound_by_patterns(
            _fsharp_parts(primary) if primary is not None else [], name,
        )
        lets = self._scope_lets.get(scope.id)
        if lets is None:
            lets = self._scope_lets[scope.id] = {}
            for defn in (
                defn for wrapper in scope.children
                if wrapper.type in ("declaration_expression", "type_extension_elements")
                for defn in wrapper.children if defn.type == "function_or_value_defn"
            ):
                for leaf in (leaf for left in _fsharp_parts(defn)[:1] for leaf in iter_tree(left)):
                    if leaf.type == "identifier":
                        lets.setdefault(node_text(leaf, self.source), []).append(defn)
        for defn in lets.get(name, []) if found is None else []:
            if not (child.start_byte <= defn.start_byte and defn.end_byte <= child.end_byte):
                found = found if found is not None else self._bound_by_let(defn, name)
        return found

    def _bound_by_let(
        self, defn: "tree_sitter.Node", name: str,
    ) -> Optional[_FsharpBinding]:
        """A ``let`` that binds ``name``: a function (its symbol, found by the
        definition's position), or a value, typed by its annotation or by
        what it is bound to."""
        if find_child_by_type(defn, "function_declaration_left") is not None:
            return _FsharpBinding(
                function=symbol_declared_by(defn, self.decl_index),
            ) if _fsharp_function_name(defn, self.source) == name else None
        left = find_child_by_type(defn, "value_declaration_left")
        if left is None:
            return None  # pragma: no cover - a let binds a function or a value
        parts = _fsharp_parts(left)
        entries = _fsharp_pattern_parameters(parts[0], self.source) if len(parts) == 1 else []
        if len(entries) == 1 and entries[0]["name"] == name:
            return _FsharpBinding(self._value_type(defn, entries[0]["type"]))
        return _FsharpBinding() if self._mentions(left, name) else None

    def _value_type(self, defn: "tree_sitter.Node", annotated: object) -> Optional[Symbol]:
        """The type of ``let x: T = ...`` (the annotation) or ``let x = T(...)``
        (the expression). ``let!`` and ``use!`` bind the awaited value, which
        the expression does not name."""
        if isinstance(annotated, str):
            return self.type_from_text(annotated, defn)
        for part in _fsharp_parts(defn):
            if part.type.endswith("_type") or part.type == "type_argument":
                return self.type_from_text(_fsharp_type_text(part, self.source), defn)
        body = defn.child_by_field_name("body")
        if body is None or defn.children[0].type != "let" or defn.id in self._busy:
            return None
        self._busy.add(defn.id)
        try:
            return self.expression_type(body)
        finally:
            self._busy.discard(defn.id)

    def _bound_by_identifier(
        self, node: "tree_sitter.Node", ident: "tree_sitter.Node", name: str,
    ) -> Optional[_FsharpBinding]:
        """``use d = Shape(...)`` (typed by the expression) or ``for i = ...``."""
        if node_text(ident, self.source) != name:
            return None
        value = ident.next_named_sibling
        if value is None or node.children[0].type != "use":
            return _FsharpBinding()
        return _FsharpBinding(self.expression_type(value))

    def _bound_by_patterns(
        self, patterns: Sequence["tree_sitter.Node"], name: str,
    ) -> Optional[_FsharpBinding]:
        """Parameters or patterns that bind ``name``: typed by ``(x: Shape)``.
        An optional ``?x: Shape`` is a ``Shape option``, so it states no type
        to call a member on."""
        for pattern in patterns:
            for entry in _fsharp_pattern_parameters(pattern, self.source):
                if entry["name"] == name:
                    return _FsharpBinding(
                        None if entry["default"] else self.type_from_text(entry["type"], pattern)
                    )
            if self._mentions(pattern, name):
                return _FsharpBinding()
        return None

    def _mentions(self, node: "tree_sitter.Node", name: str) -> bool:
        """True when ``name`` occurs as an identifier inside ``node``."""
        return any(
            leaf.type == "identifier" and node_text(leaf, self.source) == name
            for leaf in iter_tree(node)
        )

    # -- the call --------------------------------------------------------------

    def resolve(
        self, callee: "tree_sitter.Node", arg_count: Optional[int],
        constructs: bool = True,
    ) -> Optional[_FsharpTarget]:
        """The member, function or type ``callee`` names, or None when the
        source does not decide it. ``arg_count`` None is a read
        (``this.Area``), which can only be a property; an application can be a
        method, a property holding a function, a ``let``-bound function of an
        enclosing scope, or (unless ``constructs`` is False) a construction."""
        receiver, names = _fsharp_callee_path(callee, self.source)
        if receiver is not None:
            owner = self.expression_type(receiver)
            return self._member(owner, names[0], arg_count, False) if owner else None
        if not names or (arg_count is None and len(names) == 1):
            return None
        bound = self.binding(names[0], callee)
        if len(names) > 1:
            if bound is not None:
                # ``x.Scale``: a member of x's type. ``x.Inner.Scale`` would
                # need the type of a property, which is not followed.
                if bound.type is None or len(names) != 2:
                    return None
                return self._member(bound.type, names[1], arg_count, False)
            owner = self.type_named(".".join(names[:-1]), callee)
            found = self._member(owner, names[-1], arg_count, True) if owner else None
            if found is not None:
                return found
        if arg_count is None:
            return None
        if bound is not None:
            # A bare name some ``let`` around the site binds as a function is
            # that function, whatever else in the repository shares the name.
            return _FsharpTarget(
                bound.function, "calls", "function", 1.0, False,
            ) if bound.function is not None else None
        if not constructs:
            return None
        constructed = self.type_named(".".join(names), callee)
        if constructed is None or constructed.kind not in _FSHARP_CONSTRUCTED_KINDS:
            return None
        # The primary constructor has no symbol of its own: the type stands
        # for it, as the class does where py / js_ts / java land a
        # construction. An additional ``new(...)`` is a declared callable, so
        # when the argument count picks one out and the primary constructor
        # does not take that count, the edge names it (as C# names its
        # constructor); the walks reach the type from it through ``contains``.
        constructors = narrow_by_arity([
            member for member in self.index.members_of(constructed, "new")
            if member.kind == "constructor"
        ], arg_count)
        primary = arity_bounds(constructed)
        if constructors and (primary is None or not primary.admits(arg_count)):
            return _fsharp_pick([constructors], arg_count, "instantiates", "constructor")
        return _FsharpTarget(constructed, "instantiates", "constructor", 1.0, False)

    def _member(
        self, owner: Symbol, name: str, arg_count: Optional[int], static: bool,
    ) -> Optional[_FsharpTarget]:
        """``owner``'s member ``name``, static or not as the call form requires.
        A body is preferred to an abstract slot of the same name: a call of a
        slot the type also implements (``default``) runs that implementation."""
        kinds = ("property",) if arg_count is None else ("method", "property")
        named = [
            member for member in self.index.members_of(owner, name)
            if member.kind in kinds and ("static" in member.modifiers) == static
        ]
        if not named:
            return None
        return _fsharp_pick([
            [member for member in named if "abstract" not in member.modifiers],
            [member for member in named if "abstract" in member.modifiers],
        ], arg_count, "calls", "method")


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
    returns: dict[str, set[str]],
) -> tuple[list[Symbol], str]:
    """Extract all symbols from a parsed F# file.

    Returns (symbols, module_name).

    Detects:
    - named_module (module name)
    - function_or_value_defn (functions/values)
    - type_definition: every record, union, class, interface and struct of
      an ``and`` chain (``_fsharp_type_symbols``)
    - member_defn (methods, properties, constructors, ``val`` fields;
      ``_fsharp_member_symbol``)
    """
    symbols: list[Symbol] = []
    module_name = ""

    for node in iter_tree(tree.root_node):
        # Module declaration
        if node.type == "named_module":
            long_id = find_child_by_type(node, "long_identifier")
            if long_id:
                module_name = _extract_long_identifier(long_id, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                span = Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                sym_id = make_symbol_id("fsharp", file_path, start_line, end_line, module_name, "module")
                symbols.append(Symbol(
                    id=sym_id,
                    name=module_name,
                    kind="module",
                    language="fsharp",
                    path=file_path,
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

        # Function/value definition
        elif node.type == "function_or_value_defn":
            # Check for function_declaration_left (functions with params)
            func_left = find_child_by_type(node, "function_declaration_left")
            if func_left:
                name_node = find_child_by_type(func_left, "identifier")
                if name_node:
                    func_name, chain = _fsharp_function_names(
                        node, source, node_text(name_node, source),
                    )
                    meta: dict[str, object] = {
                        PARAMETERS_KEY: _fsharp_parameters(func_left, source),
                    }
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    span = Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    )
                    sym_id = make_symbol_id("fsharp", file_path, start_line, end_line, func_name, "function")

                    # Extract signature
                    signature = _extract_fsharp_signature(node, source)

                    symbols.append(Symbol(
                        id=sym_id,
                        stable_id=declared_arity_stable_id(
                            kind="function", lang="fsharp", path=file_path,
                            name=node_text(name_node, source),
                            qualified_name=chain, arity=declared_arity(meta),
                        ),
                        meta=meta,
                        name=func_name,
                        kind="function",
                        language="fsharp",
                        path=file_path,
                        span=span,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        signature=signature,
                        cyclomatic_complexity=compute_cyclomatic_complexity(node, "fsharp"),
                        line_span=end_line - start_line + 1,
                    ))
            elif _fsharp_value_is_module_level(node):
                # A module-level `let x = …` value binding — a data anchor,
                # emitted as `variable` (the cross-language WI-jusus convention,
                # replacing the F#-only `value` kind). Function-body locals reuse
                # this node but are excluded by the module-scope guard.
                val_left = find_child_by_type(node, "value_declaration_left")
                if val_left:
                    id_pattern = find_child_by_type(val_left, "identifier_pattern")
                    if id_pattern:
                        long_id = find_child_by_type(id_pattern, "long_identifier_or_op")
                        if long_id:
                            name_node = find_child_by_type(long_id, "identifier")
                            if name_node:
                                # INV-fabih: named with its module chain like
                                # a function (``A.x``), so the backstop's
                                # variable table formula over the name tells
                                # two modules' ``x`` apart (the bare ``x`` tied).
                                val_name = (
                                    _fsharp_module_prefix(node, source)
                                    + node_text(name_node, source)
                                )
                                start_line = node.start_point[0] + 1
                                end_line = node.end_point[0] + 1
                                span = Span(
                                    start_line=start_line,
                                    end_line=end_line,
                                    start_col=node.start_point[1],
                                    end_col=node.end_point[1],
                                )
                                sym_id = make_symbol_id("fsharp", file_path, start_line, end_line, val_name, "variable")
                                symbols.append(Symbol(
                                    id=sym_id,
                                    name=val_name,
                                    kind="variable",
                                    language="fsharp",
                                    path=file_path,
                                    span=span,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                ))

        # Type definitions: every type of an ``and`` chain.
        elif node.type == "type_definition":
            chained = False
            for child in node.children:
                if child.type == "and":
                    chained = True
                elif child.type in _FSHARP_TYPE_DEFNS:
                    symbols.extend(_fsharp_type_symbols(
                        node, child, source, file_path, run_id, chained,
                    ))

        # A member, wherever its type holds it (the type's body, an
        # ``interface ... with`` block, a ``type X with`` extension).
        elif node.type == "member_defn":
            member = _fsharp_member_symbol(node, source, file_path, run_id, returns)
            if member is not None:
                symbols.append(member)

    return symbols, module_name


def _get_enclosing_function_fsharp(
    node: "tree_sitter.Node",
    decl_index: SymbolsAt,
) -> Symbol | None:
    """The nearest declaration containing ``node`` that has a symbol.

    A ``let``-bound function, a member, or the type itself for a call in the
    type's body but in no member (``do helper ()``, ``let cached = helper 1``;
    INV-bamij). Keyed by the definition's POSITION, not its name (INV-midag,
    WI-mapor): two nested modules of one file each binding ``let run`` share a
    name, as two overloads of a member do, and a name lookup credits both
    bodies' calls to the last one. A declaration with no symbol (a member of
    an object expression, a class-level ``let`` value) is walked past.
    """
    return enclosing_declared_symbol(node, decl_index, _FSHARP_CALLER_NODES)


def _extract_module_aliases(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract module aliases for disambiguation.

    In F#:
        module M = List -> M maps to List (produces module_abbrev)
        module M = System.IO -> M maps to System.IO (produces module_abbrev)

    Returns a dict mapping alias names to module names.
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        alias_name = None
        module_name = None

        if node.type == "module_abbrev":
            # module_abbrev: module <identifier> = <long_identifier>
            for child in node.children:
                if child.type == "identifier" and alias_name is None:
                    alias_name = node_text(child, source)
                elif child.type == "long_identifier":
                    module_name = _extract_long_identifier(child, source)

        elif node.type == "module_defn":  # pragma: no cover - complex module defs rarely used
            # module_defn: module <identifier> = <long_identifier_or_op>
            for child in node.children:
                if child.type == "identifier" and alias_name is None:
                    alias_name = node_text(child, source)
                elif child.type == "long_identifier_or_op":
                    # Get the full module path
                    long_id = find_child_by_type(child, "long_identifier")
                    if long_id:
                        module_name = _extract_long_identifier(long_id, source)
                    else:
                        id_node = find_child_by_type(child, "identifier")
                        if id_node:
                            module_name = node_text(id_node, source)

        if alias_name and module_name:
            aliases[alias_name] = module_name

    return aliases


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    resolver: "NameResolver",
    run_id: str,
    index: _FsharpIndex,
    returns: Mapping[str, str],
    module_aliases: dict[str, str] | None = None,
) -> list[Edge]:
    """Extract call and import edges from a parsed F# file.

    Detects:
    - Function calls (application_expression)
    - Calls and reads of members, and constructions, whose target the source
      names (``_FsharpCallResolver``)
    - Open statements (import_decl)
    """
    edges: list[Edge] = []
    file_id = make_file_id("fsharp", file_path)
    if module_aliases is None:  # pragma: no cover - defensive
        module_aliases = {}

    # Every declaration of this file, by position (INV-midag).
    decl_index = symbols_at(file_symbols)
    members = _FsharpCallResolver(index, returns, source, file_path, decl_index)

    def member_edge(src: Symbol, target: _FsharpTarget, at: "tree_sitter.Node") -> Edge:
        meta: dict[str, object] = {"call_construct": target.construct}
        if target.ambiguous:
            meta["resolution_quality"] = "ambiguous"
        return Edge.create(
            src=src.id,
            dst=target.symbol.id,
            edge_type=target.edge_type,
            line=at.start_point[0] + 1,
            origin=PASS_ID,
            origin_run_id=run_id,
            evidence_type="ast_call",
            resolution_score=target.confidence,
            meta=meta,
        )

    for node in iter_tree(tree.root_node):
        if node.type == "import_decl":
            # open Module.Submodule
            long_id = find_child_by_type(node, "long_identifier")
            if long_id:
                module_name = _extract_long_identifier(long_id, source)
                module_id = f"fsharp:{module_name}:0-0:module:module"
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

        elif node.type == "application_expression":
            # Function application - first child is the function being called
            caller = _get_enclosing_function_fsharp(node, decl_index)
            if caller and node.children:
                first_child = node.children[0]
                callee_name: Optional[str] = None
                path_hint: Optional[str] = None
                dotted: Optional[str] = None
                if first_child.type == "long_identifier_or_op":
                    # Check for qualified call (long_identifier with dots)
                    long_id = find_child_by_type(first_child, "long_identifier")
                    if long_id:
                        # Get all identifiers in the long_identifier
                        identifiers = [
                            node_text(c, source)
                            for c in long_id.children
                            if c.type == "identifier"
                        ]
                        if len(identifiers) >= 2:
                            # First part might be a module alias
                            receiver = identifiers[0]
                            callee_name = identifiers[-1]
                            path_hint = module_aliases.get(receiver)
                            dotted = ".".join(identifiers)
                        elif len(identifiers) == 1:  # pragma: no cover - single ident fallback
                            callee_name = identifiers[0]
                    else:
                        # Simple identifier
                        name_node = find_child_by_type(first_child, "identifier")
                        if name_node:
                            callee_name = node_text(name_node, source)

                # ``A.run x`` names the function this analyzer emits
                # as ``A.run``; try the dotted name first.
                lookup_result = resolver.lookup(
                    dotted, allow_suffix=False,
                ) if dotted else None
                if lookup_result is None or not lookup_result.found:
                    # Next, what the call site itself decides (a member, a
                    # type, a function bound in an enclosing scope), ahead of
                    # the bare-name guess below; a bare name that IS a
                    # registered function or value is no construction.
                    registered = resolver.registry.get(
                        callee_name,
                    ) if callee_name and not dotted else None
                    target = members.resolve(
                        first_child, _fsharp_argument_count(node),
                        constructs=registered is None
                        or registered.kind not in ("function", "variable"),
                    )
                    if target is not None:
                        edges.append(member_edge(caller, target, node))
                        continue
                    lookup_result = resolver.lookup(
                        callee_name, path_hint=path_hint,
                    ) if callee_name else None
                if lookup_result is not None and lookup_result.found and lookup_result.symbol:
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
                        meta={"call_construct": "function"},
                    )
                    edges.append(edge)

        elif node.type in ("long_identifier_or_op", "dot_expression"):
            # A read of a property (``this.Area``, ``x.Area`` on a typed
            # ``x``, ``Shape.Unit``) runs its getter. A callee was handled
            # with its application; a pattern and the inner part of a longer
            # path are no read.
            parent = node.parent
            if parent is not None and parent.type not in _FSHARP_NOT_A_READ and not (
                parent.type == "application_expression"
                and parent.children[0].id == node.id
            ):
                read = members.resolve(node, None)
                reader = _get_enclosing_function_fsharp(
                    node, decl_index,
                ) if read is not None else None
                if read is not None and reader is not None:
                    edges.append(member_edge(reader, read, node))

    return edges


class FsharpAnalyzer(TreeSitterAnalyzer):
    """F# language analyzer using tree-sitter-language-pack."""

    lang = "fsharp"
    file_patterns: ClassVar[list[str]] = ["*.fs", "*.fsi", "*.fsx"]
    language_pack_name = "fsharp"
    create_file_symbols = True

    #: The types and members of the run in progress (see ``analyze``).
    _fsharp_index: _FsharpIndex = _FsharpIndex()

    def analyze(
        self, repo_root: Path, max_files: Optional[int] = None,
    ) -> AnalysisResult:
        """Run the two passes over a fresh type and member index.

        The analyzer object outlives a run, and a type of the run before
        must not answer a call of this one.
        """
        self._fsharp_index = _FsharpIndex()
        try:
            return super().analyze(repo_root, max_files)
        finally:
            self._fsharp_index = _FsharpIndex()

    def _find_source_files(self, repo_root: Path) -> Iterator[Path]:
        """Override to filter out Forth .fs files."""
        return find_fsharp_files(repo_root)

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract modules, functions, values, types and members from an F# file."""
        analysis = FileAnalysis()

        returns: dict[str, set[str]] = {}
        file_symbols, _module_name = _extract_symbols_from_file(
            tree, source, rel_path, run.execution_id, returns,
        )
        analysis.symbols.extend(file_symbols)
        # A member's declared return type, in its declared home; one that two
        # overloads declare differently is no fact about the name.
        analysis.method_return_types.update({
            name: next(iter(types)) for name, types in returns.items() if len(types) == 1
        })
        self._fsharp_index.add_file(
            rel_path, file_symbols, *_fsharp_file_names(tree.root_node, source),
        )

        # Register resolvable symbols for edge resolution. Record `field`s are
        # data anchors (never call targets), so they stay out. But an F# module
        # `variable` (a `let` value binding) IS legitimately callable — a
        # lambda / partial-application / composition binding (`let add = fun a b
        # -> …`, `let inc = (+) 1`) is a real call target — and F# forbids a
        # same-name value+function in a module, so there is no clobber to guard
        # against; keep variables resolvable.
        for sym in file_symbols:
            if sym.kind in ("function", "variable"):
                analysis.symbol_by_name[sym.name] = sym

        # Store module aliases in import_aliases for pass 2
        # (extracted separately in get_import_aliases)

        return analysis

    def register_symbol(self, symbol: Symbol, global_symbols: dict[str, Symbol]) -> None:
        """Keep fields, class-like types and members OUT of the name-keyed
        call-resolution registry (WI-jusus, WI-tamib).

        A record ``field`` is a data anchor, never a call target — and the
        ``NameResolver`` suffix index would let a registered qualified field
        (``Rec.name``) suffix-match a bare call ``name(…)`` and mint a wrong
        ``calls`` edge to the field, so the field skip is a load-bearing
        precision guard. A member is kept out for the same reason: a
        registered ``Shape.Scale`` would suffix-match every ``Scale`` call in
        the repository whatever its receiver, and one name holds only the
        last of its overloads. A class-like type is kept out so that it
        cannot re-target a call that resolves to a same-name function today.
        Calls reach both through ``_FsharpIndex``, which is asked only for a
        type the call site names and for a member of that type. ``variable``
        (an F# ``let`` value binding) is NOT
        skipped: it is legitimately callable (point-free / factory idioms) and
        F# forbids a same-name value+function within a module, so keeping it
        resolvable adds recall without a clobber risk (the residual is a narrow
        cross-module data-value-evicts-function false-negative, symmetric with
        the function-vs-function bare-name ambiguity the resolver already
        tolerates). The skipped kinds still reach output/search/centrality
        because the output symbol set is built independently of this registry.
        """
        if symbol.kind in _FSHARP_UNREGISTERED_KINDS:
            return
        super().register_symbol(symbol, global_symbols)

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract F# module aliases."""
        return _extract_module_aliases(tree, source)

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call and import edges from an F# file."""
        # Every symbol of the file: ``local_symbols`` keeps one per name.
        file_symbols = self.file_symbols(local_symbols)
        return _extract_edges_from_file(
            tree, source, rel_path,
            file_symbols, resolver,
            run.execution_id, self._fsharp_index,
            self._method_return_type_registry, module_aliases=import_aliases,
        )


_analyzer = FsharpAnalyzer()


def is_fsharp_tree_sitter_available() -> bool:
    """Check if tree-sitter with F# grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("fsharp")
def analyze_fsharp(repo_root: Path) -> AnalysisResult:
    """Analyze F# files in a repository."""
    return _analyzer.analyze(repo_root)
