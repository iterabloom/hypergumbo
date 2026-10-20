# SPDX-License-Identifier: AGPL-3.0-or-later
"""Julia analysis pass using tree-sitter-julia.

This analyzer uses tree-sitter to parse Julia files and extract:
- Module declarations
- Function declarations (both full and short-form)
- Struct declarations (mutable and immutable)
- Struct member fields (kind="field"; WI-jusus)
- Abstract type declarations
- Macro declarations
- Constant declarations
- Module-level variable bindings (kind="variable"; WI-jusus), kept out of call
  resolution
- Function call relationships
- Import statements (import/using)

If tree-sitter with Julia support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration.
The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Julia-specific extraction logic.

1. Check if tree-sitter-julia is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and import statements

A parametric method (``f(x::T) where T``, full or short form) is read through
its ``where`` clauses; before WI-tusad it emitted no symbol. Neither did a
method of a function owned elsewhere (``function Base.show(io, x)``,
``Base.length(x::Foo) = 1``): it is now emitted under the name as written,
``Base.show`` (:func:`_julia_callee_name`). A zero-method generic function
(``function f end``, which declares ``f`` and gives it no method) is emitted
too, its stable_id marked with the ``generic`` decorator so it never ties a
zero-parameter method ``f()`` of the same scope.

Identity (WI-tusad)
-------------------
Julia dispatches on the positional parameters' types, so ``f(x::Int)`` and
``f(x::String)`` are two methods of one name in one file. Functions and macros
record their positional parameters in ``meta["parameters"]`` (``::T`` as the
type, ``y=2`` a default, ``xs...`` variadic; keyword parameters after ``;`` are
not dispatched on and not listed) and mint the ADR-0035 producer stable_id
with ``overload_arity.declared_arity_stable_id``: the typed tier as soon as a
parameter declares a type, the enclosing module chain -- and, for a function
nested in another, the enclosing functions (``_julia_scope_chain``; INV-fabih)
-- as the qualified name.
Abstract types and consts take the same formula with no arity. Before, none of
them computed one, and two methods of one name nulled each other on the
backstop floor key. Struct fields keep their typed formula, over the module
chain and the struct; modules, structs and variables keep the table formula,
over the module chain (``overload_arity.scoped_table_stable_id``; INV-fabih:
two ``struct Inner`` in modules ``A`` and ``B`` shared one bare-name table key,
so deleting the first moved the second's id and its fields'). Top-level ones
keep their values.

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-julia package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as other language analyzers for consistency
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Iterator, Optional

from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    SymbolsAt,
    TreeSitterAnalyzer,
    ancestor_labels,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_file_stable_id,
    make_symbol_id,
    make_typed_stable_id,
    node_text,
    symbol_declared_by,
    symbols_at,
    visibility_from_modifiers,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity
from hypergumbo_core.overload_arity import (
    PARAMETERS_KEY, declared_arity, declared_arity_stable_id, parameter_entry,
    scoped_table_stable_id,
)

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("julia")


def find_julia_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Julia files in the repository.

    The analyzer's own discovery (``_find_source_files``), so the two cannot drift.
    """
    yield from _analyzer._find_source_files(repo_root)


def _get_enclosing_module(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up the tree to find the enclosing module name."""
    current = node.parent
    while current is not None:
        if current.type == "module_definition":
            id_node = find_child_by_type(current, "identifier")
            if id_node:
                return node_text(id_node, source)
        current = current.parent
    return None  # pragma: no cover - defensive


def _julia_module_chain(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """The names of every module enclosing ``node``, outermost first."""
    chain: list[str] = []
    current = node.parent
    while current is not None:
        if current.type == "module_definition":
            id_node = find_child_by_type(current, "identifier")
            if id_node:
                chain.append(node_text(id_node, source))
        current = current.parent
    chain.reverse()
    return chain


def _julia_scope_chain(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Every module AND function enclosing ``node``, outermost first: the
    chain a function's stable_id hashes (INV-fabih). ``function inner`` (or a
    short-form ``inner(x) = x``) nested in functions ``a`` and ``b`` shared
    the module chain alone, tied, and deleting the first moved the second; a
    function outside any function keeps its value."""
    def label(n: "tree_sitter.Node") -> Optional[str]:
        if n.type == "module_definition":
            id_node = find_child_by_type(n, "identifier")
            return node_text(id_node, source) if id_node is not None else None
        if n.type == "function_definition":
            return _extract_function_name(n, source)
        return None
    return ancestor_labels(node, label)



def _julia_call_of(node: "tree_sitter.Node") -> Optional["tree_sitter.Node"]:
    """The ``call_expression`` a definition's signature or LHS is built on.

    Unwraps the return-type annotation (``f(x)::Int``) and any number of
    ``where`` clauses (``f(x::T) where T``, ``g(x) where {T} where {S}``),
    in either order. Before WI-tusad a ``where`` form was not unwrapped, so a
    parametric method -- the ordinary way to write a generic one in Julia --
    emitted no symbol at all. None when the node is not a call underneath
    (a plain value binding).
    """
    while node.type in ("where_expression", "typed_expression") and node.children:
        node = node.children[0]
    return node if node.type == "call_expression" else None


def _julia_parameter(
    arg: "tree_sitter.Node", source: bytes,
) -> Optional[dict[str, Any]]:
    """One ``meta["parameters"]`` entry for a positional argument-list element.

    ``x``, ``x::Int``, ``::Int`` (an unnamed typed parameter), ``y=2`` /
    ``y::Int=2`` (a default) and ``xs...`` / ``xs::Int...`` (variadic). A
    destructuring ``(a, b)`` takes its text as its name. Punctuation yields None.
    """
    if not arg.is_named or arg.type in ("comment", "line_comment", "block_comment"):
        return None
    default = variadic = False
    if arg.type == "named_argument" and arg.children:
        default, arg = True, arg.children[0]
    if arg.type == "splat_expression" and arg.children:
        variadic, arg = True, arg.children[0]
    if arg.type == "identifier":
        return parameter_entry(node_text(arg, source), default=default, variadic=variadic)
    if arg.type in ("typed_expression", "unary_typed_expression"):
        name = ""
        type_text: Optional[str] = None
        after_colons = False
        for child in arg.children:
            if child.type == "::":
                after_colons = True
            elif not after_colons and child.type == "identifier" and not name:
                name = node_text(child, source)
            elif after_colons and child.is_named and type_text is None:
                type_text = node_text(child, source)
        return parameter_entry(name, type_text, default=default, variadic=variadic)
    return parameter_entry(node_text(arg, source), default=default, variadic=variadic)


def _julia_parameters(call_node: "tree_sitter.Node", source: bytes) -> list[dict[str, Any]]:
    """``meta["parameters"]`` of a function or macro: its POSITIONAL parameters.

    Julia dispatches on the positional parameters' types, so they are the
    identity a method has among its same-name siblings (``f(x::Int)`` /
    ``f(x::String)``: the typed tier of ``declared_arity_stable_id``). The
    keyword parameters after ``;`` take no part in dispatch -- ``f(x; a=1)``
    REPLACES ``f(x; b=1)`` -- and are not listed.
    """
    entries: list[dict[str, Any]] = []
    args = find_child_by_type(call_node, "argument_list")
    for arg in args.children if args is not None else ():
        if arg.type == ";":
            break
        entry = _julia_parameter(arg, source)
        if entry is not None:
            entries.append(entry)
    return entries


def _julia_stable_id(
    kind: str, file_path: str, name: str, chain: list[str],
    meta: Optional[dict[str, Any]], decorators: str = "",
) -> str:
    """The ADR-0035 producer stable_id: module chain plus declared parameters."""
    return declared_arity_stable_id(
        kind=kind, lang="julia", path=file_path, name=name,
        qualified_name=".".join([*chain, name]), arity=declared_arity(meta),
        decorators=decorators,
    )


def _get_enclosing_function_julia(
    node: "tree_sitter.Node",
    decl_index: SymbolsAt,
) -> Optional[Symbol]:
    """The function whose definition contains ``node``: a ``function`` block,
    or a short-form ``f(x) = ...`` assignment.

    Keyed by the definition's POSITION, not its name (INV-midag, WI-mapor).
    Julia's multiple dispatch defines one name once per method signature, and
    the name lookup credited every method's calls to the last one.
    """
    current = node.parent
    while current is not None:
        if current.type == "function_definition" or (
            current.type == "assignment"
            and _julia_short_form_call_node(current) is not None
        ):
            sym = symbol_declared_by(current, decl_index)
            if sym is not None:
                return sym
        current = current.parent
    return None  # pragma: no cover - defensive


def _julia_callee_name(call_node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """The name a method definition's call is built on, as written.

    ``f(x)`` -> ``f``; ``Base.show(io, x)`` -> ``Base.show`` (a method added to
    a function another module owns, the usual way to extend ``show`` or
    ``length``). None for a callable-object definition (``(o::Foo)(x)``).
    """
    head = call_node.named_children[0] if call_node.named_children else None
    if head is not None and head.type in ("identifier", "field_expression"):
        return node_text(head, source)
    return None


def _julia_generic_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """The name a zero-method ``function f end`` declares.

    Its signature is a bare name, not a call; None when it is a call (a
    method). The grammar does not parse ``function Base.f end`` (an ERROR
    node), so a qualified zero-method declaration is not reached.
    """
    sig_node = find_child_by_type(node, "signature")
    head = sig_node.named_children[0] if sig_node is not None and sig_node.named_children else None
    if head is not None and head.type == "identifier":
        return node_text(head, source)
    return None


def _extract_function_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function name from signature or assignment."""
    # For full function definitions: function name(args)
    call_node = _julia_signature_call(node)
    if call_node:
        return _julia_callee_name(call_node, source)
    return None


def _julia_signature_call(node: "tree_sitter.Node") -> Optional["tree_sitter.Node"]:
    """The ``call_expression`` of a ``function``/``macro`` block's signature."""
    sig_node = find_child_by_type(node, "signature")
    if sig_node is None or not sig_node.named_children:  # pragma: no cover - the
        return None  # grammar gives every block a signature (``function f end``: ``f``)
    return _julia_call_of(sig_node.named_children[0])


def _extract_julia_signature(
    node: "tree_sitter.Node", source: bytes, is_short_form: bool = False
) -> Optional[str]:
    """Extract function signature from a Julia function definition.

    Returns signature like:
    - "(x::Int, y::Int)::Int" for typed functions
    - "(message::String)" for untyped return
    - "(x)" for short-form functions

    Args:
        node: The function_definition or assignment node.
        source: The source code bytes.
        is_short_form: True for assignment-based function definitions.

    Returns:
        The signature string, or None if extraction fails.
    """
    params: list[str] = []
    return_type: Optional[str] = None

    if is_short_form:
        # Short form: f(x) = expr (also typed / parametric, unwrapped)
        left_node = _julia_short_form_call_node(node)
        if left_node is not None:
            arg_list = find_child_by_type(left_node, "argument_list")
            if arg_list:
                for child in arg_list.children:
                    if child.type in ("typed_expression", "identifier"):
                        params.append(node_text(child, source))
    else:
        # Full form: function name(args)::ReturnType
        sig_node = find_child_by_type(node, "signature")
        while (
            sig_node is not None
            and sig_node.named_children
            and sig_node.named_children[0].type == "where_expression"
        ):
            sig_node = sig_node.named_children[0]  # f(x::T) where T
        if sig_node:
            # Check if signature has typed return
            typed_expr = find_child_by_type(sig_node, "typed_expression")
            if typed_expr:
                # Get call_expression and return type
                call_node = find_child_by_type(typed_expr, "call_expression")
                for child in typed_expr.children:
                    if child.type == "identifier" and call_node:
                        # This is the return type (after ::)
                        return_type = node_text(child, source)
                if call_node:
                    arg_list = find_child_by_type(call_node, "argument_list")
                    if arg_list:
                        for child in arg_list.children:
                            if child.type == "typed_expression":
                                params.append(node_text(child, source))
                            elif child.type == "identifier":  # pragma: no cover - untyped in typed func
                                params.append(node_text(child, source))
            else:
                # No typed return
                call_node = find_child_by_type(sig_node, "call_expression")
                if call_node:
                    arg_list = find_child_by_type(call_node, "argument_list")
                    if arg_list:
                        for child in arg_list.children:
                            if child.type == "typed_expression":
                                params.append(node_text(child, source))
                            elif child.type == "identifier":
                                params.append(node_text(child, source))

    params_str = ", ".join(params)
    signature = f"({params_str})"

    if return_type:
        signature += f"::{return_type}"

    return signature


def _julia_struct_field(
    node: "tree_sitter.Node", source: bytes
) -> tuple[Optional[str], Optional[str]]:
    """(name, type) for a struct-body member (WI-jusus).

    A ``typed_expression`` (``x::Float64``) yields ``("x", "Float64")`` — the
    identifier before ``::`` is the name, the first named node after ``::`` is
    the (possibly parametric) type. A bare ``identifier`` field (``label``)
    yields ``("label", None)``. Julia-1.8 ``const x::T`` fields arrive here
    already unwrapped from their ``const_statement``.
    """
    if node.type == "identifier":
        return node_text(node, source), None
    name: Optional[str] = None
    vtype: Optional[str] = None
    after_colons = False
    for child in node.children:
        if child.type == "::":
            after_colons = True
        elif not after_colons and child.type == "identifier" and name is None:
            name = node_text(child, source)
        elif after_colons and child.is_named and vtype is None:
            vtype = node_text(child, source)
    return name, vtype


# A ``module_definition`` (also ``baremodule``) body or ``source_file`` (a
# top-level file with no module wrapper) is where a direct ``assignment`` is
# module-level state -> ``kind="variable"`` (WI-jusus).
_JULIA_MODULE_SCOPES = frozenset({"module_definition", "source_file"})

# Wrappers that do NOT introduce a new scope: an assignment inside one is still
# in its enclosing scope. Walking through these reaches the real scope: a
# module-level ``begin…end`` (``compound_statement``), ``if…elseif…else``, and a
# ``global x = …`` statement are all module-level. Anything NOT here and NOT a
# module scope (``function_definition`` / ``let_statement`` / ``for``/``while``/``try`` /
# ``do_clause`` / ``local_statement``) INTRODUCES a scope or opts out, so it stops the
# walk and the binding is excluded. A chained ``x = y = 5`` link (an ``assignment``
# ancestor) is transparent ONLY when it is a value binding — a short-form
# function ``f(x) = …`` is ALSO an ``assignment`` but introduces the function's
# own scope, so it is handled explicitly in the walk, not via this set.
_JULIA_SCOPE_TRANSPARENT = frozenset({
    "global_statement", "compound_statement", "if_statement",
    "else_clause", "elseif_clause",
})


def _julia_short_form_call_node(
    assignment: "tree_sitter.Node",
) -> Optional["tree_sitter.Node"]:
    """Return the ``call_expression`` of a short-form function ``assignment``
    (``f(x) = …``, typed ``f(x)::Int = …``, parametric ``f(x::T) where T = …``),
    or None for a plain value binding.
    """
    lhs = assignment.children[0] if assignment.children else None
    if lhs is None:
        return None  # pragma: no cover - an assignment always has a LHS
    return _julia_call_of(lhs)


def _julia_variable_scope(node: "tree_sitter.Node") -> Optional["tree_sitter.Node"]:
    """Return the enclosing MODULE scope of an ``assignment``, or None if local.

    Ascends through scope-transparent wrappers to the nearest
    ``module_definition``/``source_file``; stops and returns None at the first
    scope-introducing / scope-opt-out ancestor (fails-safe: an unrecognized
    wrapper excludes rather than risks leaking a local as module state). A
    crossed ``assignment`` is a transparent chain link (``x = y = 5``) UNLESS it
    is a short-form function (``f(x) = begin … end``), whose body is its own
    scope, not module state.

    Fails-safe recall gap (documented, non-blocking): a ``global x = …`` INSIDE a
    function is module state but is excluded (the walk stops at the enclosing
    function); the meaningful module-level ``global`` form still emits.
    """
    current = node.parent
    while current is not None:
        if current.type in _JULIA_MODULE_SCOPES:
            return current
        if current.type == "assignment":
            if _julia_short_form_call_node(current) is not None:
                return None
            current = current.parent
            continue
        if current.type in _JULIA_SCOPE_TRANSPARENT:
            current = current.parent
            continue
        return None
    return None  # pragma: no cover - an assignment is always within a scope node


def _julia_assignment_target_names(
    lhs: "tree_sitter.Node", source: bytes
) -> list[str]:
    """Return the bound variable name(s) from an ``assignment`` LHS, or [].

    - ``identifier`` (``x = …``)               -> [``x``]
    - ``typed_expression`` (``y::Int = …``)    -> recurse on the PRE-``::`` target
      (its first child); the trailing identifier is the TYPE, not a name — so a
      compound target ``(a,b)::Point`` / ``(x)::Int`` binds the values, never the
      type. (A ``call_expression`` pre-target — a short-form typed function
      ``f(x)::Int`` — is routed to the FUNCTION emitter upstream and yields [] here.)
    - ``open_tuple`` / ``tuple_expression`` / ``parenthesized_expression`` -> recurse
      into each element (handles nested + typed tuple targets)
    - ``splat_expression`` (``a, rest... = xs`` -> ``rest``) -> recurse to its identifier
    - ``field_expression`` (``obj.f = …``) / ``index_expression`` (``arr[i] = …``) /
      ``call_expression`` are MUTATIONS or function defs, not declarations -> []
    """
    if lhs.type == "identifier":
        return [node_text(lhs, source)]
    if lhs.type in ("typed_expression", "splat_expression"):
        return _julia_assignment_target_names(lhs.children[0], source) if lhs.children else []
    if lhs.type in ("open_tuple", "tuple_expression", "parenthesized_expression"):
        names: list[str] = []
        for child in lhs.children:
            names.extend(_julia_assignment_target_names(child, source))
        return names
    return []


def _extract_symbols_from_tree(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> FileAnalysis:
    """Extract symbols from a parsed Julia file's tree."""
    analysis = FileAnalysis()

    # WI-jusus (emission-parity): file-identity anchor so same-named struct
    # fields in different files hash to distinct stable_ids.
    file_stable_id = make_file_stable_id("julia", file_path)

    def add_struct_field(
        field_node: "tree_sitter.Node", name: str, vtype: Optional[str], owner: str,
        owner_scope: list[str],
    ) -> None:
        """Append a ``kind="field"`` struct-member Symbol (WI-jusus).

        Deliberately NOT registered in ``symbol_by_name`` (nor, via
        ``JuliaAnalyzer.register_symbol``, in the global registry): a struct
        field is a data anchor, never a call target, so keeping it out of
        resolution prevents a bare-named field from shadowing a same-named
        function. It still reaches output/search/centrality because the output
        symbol set is ``analysis.symbols``.
        """
        full_name = f"{owner}.{name}"
        modifiers = ["private"] if name.startswith("_") else []
        start_line = field_node.start_point[0] + 1
        end_line = field_node.end_point[0] + 1
        analysis.symbols.append(Symbol(
            id=make_symbol_id(
                "julia", file_path, start_line, end_line, full_name, "field"
            ),
            name=full_name,
            kind="field",
            language="julia",
            path=file_path,
            span=Span(
                start_line=start_line,
                end_line=end_line,
                start_col=field_node.start_point[1],
                end_col=field_node.end_point[1],
            ),
            origin=PASS_ID,
            origin_run_id=run_id,
            # INV-fabih: the owner's module chain too (two ``struct Inner``
            # in modules ``A`` and ``B`` shared ``Inner.v``).
            stable_id=make_typed_stable_id(
                "field", vtype or "", visibility_from_modifiers(modifiers),
                name=name, qualified_name=".".join((*owner_scope, full_name)),
                file_stable_id=file_stable_id,
            ),
            signature=vtype,
            modifiers=modifiers,
            is_exported=not name.startswith("_"),
        ))

    # (scope-node start byte, name) pairs already emitted as module variables —
    # de-dupes a rebound binding (x = 5; x = 10) within a module scope.
    seen_module_vars: set[tuple[int, str]] = set()

    for node in iter_tree(tree.root_node):
        # Module definition
        if node.type == "module_definition":
            id_node = find_child_by_type(node, "identifier")
            if id_node:
                module_name = node_text(id_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=make_symbol_id("julia", file_path, start_line, end_line, module_name, "module"),
                    name=module_name,
                    kind="module",
                    language="julia",
                    path=file_path,
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    stable_id=scoped_table_stable_id(
                        kind="module", lang="julia", path=file_path, name=module_name,
                        scope=_julia_module_chain(node, source),
                    ),
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[module_name] = symbol

        # Function definition (full form)
        elif node.type == "function_definition":
            func_name = _extract_function_name(node, source)
            generic_name = None if func_name else _julia_generic_name(node, source)
            if generic_name:
                # ``function f end``: a generic function with no method yet.
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                current_module = _get_enclosing_module(node, source)
                full_name = f"{current_module}.{generic_name}" if current_module else generic_name
                symbol = Symbol(
                    id=make_symbol_id("julia", file_path, start_line, end_line, full_name, "function"),
                    stable_id=_julia_stable_id(
                        "function", file_path, generic_name,
                        _julia_scope_chain(node, source), None, "generic",
                    ),
                    name=generic_name,
                    kind="function",
                    language="julia",
                    path=file_path,
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    line_span=end_line - start_line + 1,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name.setdefault(generic_name, symbol)
            if func_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                current_module = _get_enclosing_module(node, source)
                full_name = f"{current_module}.{func_name}" if current_module else func_name

                # Extract signature
                signature = _extract_julia_signature(node, source, is_short_form=False)
                call_node = _julia_signature_call(node)
                fn_meta = {PARAMETERS_KEY: _julia_parameters(call_node, source)} if call_node else None

                symbol = Symbol(
                    id=make_symbol_id("julia", file_path, start_line, end_line, full_name, "function"),
                    stable_id=_julia_stable_id(
                        "function", file_path, func_name,
                        _julia_scope_chain(node, source), fn_meta,
                    ),
                    meta=fn_meta,
                    name=func_name,
                    kind="function",
                    language="julia",
                    path=file_path,
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    signature=signature,
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, "julia"),
                    line_span=end_line - start_line + 1,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[func_name] = symbol

        # Short-form function definition: f(x) = expr  (or typed f(x)::Int = expr)
        elif node.type == "assignment":
            left_node = node.children[0] if node.children else None
            # A short-form function has a call_expression LHS (possibly wrapped in
            # a typed_expression for a return type). Unwrap it so the typed form
            # emits a FUNCTION, not a phantom variable named after the return type.
            call_node = _julia_short_form_call_node(node)
            if call_node is not None:
                short_name = _julia_callee_name(call_node, source)
                if short_name:
                    func_name = short_name
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1

                    # Extract signature
                    signature = _extract_julia_signature(node, source, is_short_form=True)
                    fn_meta = {PARAMETERS_KEY: _julia_parameters(call_node, source)}

                    symbol = Symbol(
                        id=make_symbol_id("julia", file_path, start_line, end_line, func_name, "function"),
                        stable_id=_julia_stable_id(
                            "function", file_path, func_name,
                            _julia_scope_chain(node, source), fn_meta,
                        ),
                        meta=fn_meta,
                        name=func_name,
                        kind="function",
                        language="julia",
                        path=file_path,
                        span=Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        signature=signature,
                        cyclomatic_complexity=compute_cyclomatic_complexity(node, "julia"),
                        line_span=end_line - start_line + 1,
                    )
                    analysis.symbols.append(symbol)
                    analysis.symbol_by_name[func_name] = symbol

            # Module-level variable assignment (WI-jusus). const is handled by the
            # const_statement branch (its assignment's parent is const_statement,
            # which is not a scope the walk crosses). The scope walk sees through
            # module-level begin/if/global wrappers and chained x = y = 5 while
            # excluding function/loop/let/do/local bindings.
            else:
                scope = _julia_variable_scope(node)
                if scope is not None:
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    for var_name in _julia_assignment_target_names(left_node, source):
                        # De-dupe a rebound module variable (x = 5; x = 10) within
                        # the same module scope — one binding, one symbol.
                        dedup_key = (scope.start_byte, var_name)
                        if dedup_key in seen_module_vars:
                            continue
                        seen_module_vars.add(dedup_key)
                        symbol = Symbol(
                            id=make_symbol_id(
                                "julia", file_path, start_line, end_line, var_name, "variable"
                            ),
                            name=var_name,
                            kind="variable",
                            language="julia",
                            path=file_path,
                            span=Span(
                                start_line=start_line,
                                end_line=end_line,
                                start_col=node.start_point[1],
                                end_col=node.end_point[1],
                            ),
                            origin=PASS_ID,
                            origin_run_id=run_id,
                            stable_id=scoped_table_stable_id(
                                kind="variable", lang="julia", path=file_path,
                                name=var_name, scope=_julia_module_chain(node, source),
                            ),
                        )
                        # A module variable is a DATA anchor kept OUT of call
                        # resolution (not written to symbol_by_name, and skipped by
                        # register_symbol): resolving it would let a bare
                        # (unqualified) value name EXACT-match and beat a
                        # same-named function's module-qualified suffix-match,
                        # minting a wrong cross-module `calls` edge — and there is
                        # no existing edge to preserve (variables were unemitted
                        # before this slice), so excluding them is no regression.
                        # It still reaches output/search/centrality via
                        # analysis.symbols.
                        analysis.symbols.append(symbol)

        # Struct definition
        elif node.type == "struct_definition":
            type_head = find_child_by_type(node, "type_head")
            if type_head:
                # Get name from type_head (handles both simple and subtype syntax)
                id_node = find_child_by_type(type_head, "identifier")
                if not id_node:  # pragma: no cover
                    # Try binary_expression for subtype syntax (Circle <: Shape)
                    bin_node = find_child_by_type(type_head, "binary_expression")
                    if bin_node:
                        id_node = find_child_by_type(bin_node, "identifier")

                if id_node:
                    struct_name = node_text(id_node, source)
                    struct_scope = _julia_module_chain(node, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1

                    symbol = Symbol(
                        id=make_symbol_id("julia", file_path, start_line, end_line, struct_name, "struct"),
                        name=struct_name,
                        kind="struct",
                        language="julia",
                        path=file_path,
                        span=Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        stable_id=scoped_table_stable_id(
                            kind="struct", lang="julia", path=file_path,
                            name=struct_name, scope=struct_scope,
                        ),
                    )
                    analysis.symbols.append(symbol)
                    analysis.symbol_by_name[struct_name] = symbol

                    # WI-jusus: emit member fields owned by this struct. Fields
                    # are the struct body's typed_expression (``x::T``) and bare
                    # identifier (untyped) members; inner constructors
                    # (function_definition / short-form assignment) are skipped.
                    # The bundled tree-sitter-julia grammar lists members as
                    # DIRECT children of struct_definition; the language-pack
                    # grammar wraps them in a `block` — handle both. Julia-1.8
                    # `const x::T` fields parse as an ERROR under the bundled
                    # grammar (predates the feature) and are deferred fails-safe
                    # (miss, never emit a wrong symbol).
                    block = find_child_by_type(node, "block")
                    members = block.children if block is not None else node.children
                    for member in members:
                        if member.type in ("typed_expression", "identifier"):
                            fname, ftype = _julia_struct_field(member, source)
                            if fname is not None:
                                add_struct_field(member, fname, ftype, struct_name, struct_scope)

        # Abstract type definition
        elif node.type == "abstract_definition":
            type_head = find_child_by_type(node, "type_head")
            if type_head:
                id_node = find_child_by_type(type_head, "identifier")
                if id_node:
                    abstract_name = node_text(id_node, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1

                    symbol = Symbol(
                        id=make_symbol_id("julia", file_path, start_line, end_line, abstract_name, "abstract"),
                        stable_id=_julia_stable_id(
                            "abstract", file_path, abstract_name,
                            _julia_module_chain(node, source), None,
                        ),
                        name=abstract_name,
                        kind="abstract",
                        language="julia",
                        path=file_path,
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
                    analysis.symbol_by_name[abstract_name] = symbol

        # Macro definition
        elif node.type == "macro_definition":
            sig_node = find_child_by_type(node, "signature")
            if sig_node:
                call_node = find_child_by_type(sig_node, "call_expression")
                if call_node:
                    id_node = find_child_by_type(call_node, "identifier")
                    if id_node:
                        macro_name = node_text(id_node, source)
                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1
                        macro_meta = {PARAMETERS_KEY: _julia_parameters(call_node, source)}

                        symbol = Symbol(
                            id=make_symbol_id("julia", file_path, start_line, end_line, macro_name, "macro"),
                            stable_id=_julia_stable_id(
                                "macro", file_path, macro_name,
                                _julia_module_chain(node, source), macro_meta,
                            ),
                            meta=macro_meta,
                            name=macro_name,
                            kind="macro",
                            language="julia",
                            path=file_path,
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
                        analysis.symbol_by_name[macro_name] = symbol

        # Const statement
        elif node.type == "const_statement":
            assign_node = find_child_by_type(node, "assignment")
            if assign_node and assign_node.children:
                # INV-vopap: emit one const Symbol per bound name. Reusing the
                # assignment-target extractor (the same helper the module-variable
                # branch uses) handles multi-name (`const a, b = 1, 2`) and typed
                # (`const K::Int = 3`) LHS forms that the old single-`identifier`
                # lookup dropped entirely — those consts emitted ZERO symbols.
                lhs = assign_node.children[0]
                current_module = _get_enclosing_module(node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                for const_name in _julia_assignment_target_names(lhs, source):
                    # Qualify the id with the enclosing module (mirrors the
                    # function branch) so two module-nested consts sharing a bare
                    # name get distinct ids; the display name stays bare.
                    full_name = (
                        f"{current_module}.{const_name}" if current_module else const_name
                    )
                    symbol = Symbol(
                        id=make_symbol_id("julia", file_path, start_line, end_line, full_name, "const"),
                        stable_id=_julia_stable_id(
                            "const", file_path, const_name,
                            _julia_module_chain(node, source), None,
                        ),
                        name=const_name,
                        kind="const",
                        language="julia",
                        path=file_path,
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
                    # INV-vopap: a const IS a legit call target (`const f = () -> …`),
                    # so keep it resolvable — but never clobber a same-named
                    # function already in the resolver map (function-preferring
                    # tie-break for the const-vs-function collision).
                    existing = analysis.symbol_by_name.get(const_name)
                    if existing is None or existing.kind != "function":
                        analysis.symbol_by_name[const_name] = symbol

    return analysis


def _extract_import_aliases(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract import aliases for disambiguation.

    In Julia:
        import Pkg as P -> P maps to Pkg

    Returns a dict mapping alias names to module names.
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type not in ("import_statement", "using_statement"):
            continue

        # Find import_alias child (e.g., Pkg as P)
        alias_node = find_child_by_type(node, "import_alias")
        if alias_node:
            module_name = None
            alias_name = None
            found_as = False

            for child in alias_node.children:
                if child.type == "identifier":
                    if not found_as:
                        module_name = node_text(child, source)
                    else:
                        alias_name = node_text(child, source)
                elif child.type == "as":
                    found_as = True

            if module_name and alias_name:
                aliases[alias_name] = module_name

    return aliases


def _extract_edges_from_tree(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    local_symbols: dict[str, Symbol],
    run_id: str,
    resolver: "NameResolver",
    import_aliases: dict[str, str],
    file_symbols: list[Symbol],
) -> list[Edge]:
    """Extract call and import edges from a parsed Julia file.

    ``file_symbols`` is every symbol of the file, for the position-keyed
    enclosing lookup (INV-midag); ``local_symbols`` keeps one per name.
    """
    edges: list[Edge] = []
    file_id = make_file_id("julia", file_path)
    decl_index = symbols_at(file_symbols)

    for node in iter_tree(tree.root_node):
        # Detect import/using statements
        if node.type in ("import_statement", "using_statement"):
            # Get the import path
            scoped_node = find_child_by_type(node, "scoped_identifier")
            if scoped_node:
                import_path = node_text(scoped_node, source)
            else:
                id_node = find_child_by_type(node, "identifier")
                if id_node:
                    import_path = node_text(id_node, source)
                else:
                    import_path = None  # pragma: no cover

            if import_path:
                edges.append(Edge.create(
                    src=file_id,
                    dst=f"julia:{import_path}:0-0:package:package",
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    evidence_type="import_statement",
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

        # Detect function calls
        elif node.type == "call_expression":
            current_function = _get_enclosing_function_julia(node, decl_index)
            if current_function is not None:
                callee_name: Optional[str] = None
                path_hint: Optional[str] = None

                # Check for qualified call (field_expression: P.add)
                field_node = find_child_by_type(node, "field_expression")
                if field_node:
                    # Get receiver and method from field_expression
                    identifiers = [c for c in field_node.children if c.type == "identifier"]
                    if len(identifiers) >= 2:
                        receiver = node_text(identifiers[0], source)
                        callee_name = node_text(identifiers[-1], source)
                        # Look up receiver in import aliases
                        path_hint = import_aliases.get(receiver)
                else:
                    # Simple call
                    id_node = find_child_by_type(node, "identifier")
                    if id_node:
                        callee_name = node_text(id_node, source)

                if callee_name:
                    # Check local symbols first
                    if callee_name in local_symbols:
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
                    # Check global symbols via resolver
                    else:
                        lookup_result = resolver.lookup(callee_name, path_hint=path_hint)
                        if lookup_result.found and lookup_result.symbol is not None:
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=lookup_result.symbol.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="ast_call",
                                resolution_score=lookup_result.confidence,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                meta={"call_construct": "function"},
                            ))

    return edges


class JuliaAnalyzer(TreeSitterAnalyzer):
    """Julia language analyzer using tree-sitter-julia."""

    lang = "julia"
    file_patterns: ClassVar[list[str]] = ["*.jl"]
    grammar_module = "tree_sitter_julia"

    def register_symbol(self, symbol: Symbol, global_symbols: dict[str, Symbol]) -> None:
        """Keep ``field`` and ``variable`` data anchors OUT of call resolution (WI-jusus).

        Neither a struct ``field`` nor a module ``variable`` is a call target. A
        module variable's name is bare (unqualified), so registering it would let
        it EXACT-match a bare ``foo()`` call and beat a same-named function's
        module-qualified suffix-match — a wrong cross-module ``calls`` edge. And
        there is no existing edge to preserve (variables were unemitted before
        this slice), so excluding them is no regression — unlike the F# case where
        value bindings were already resolvable. Both kinds still reach
        ``analysis.symbols`` (search / centrality / io-boundaries), which is built
        independently of this registry.
        """
        if symbol.kind in ("field", "variable"):
            return
        super().register_symbol(symbol, global_symbols)

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract Julia symbols from a single file."""
        return _extract_symbols_from_tree(tree, source, rel_path, run.execution_id)

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract Julia import aliases (import Pkg as P)."""
        return _extract_import_aliases(tree, source)

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call and import edges from a Julia file."""
        return _extract_edges_from_tree(
            tree, source, rel_path, local_symbols,
            run.execution_id, resolver, import_aliases,
            self.file_symbols(local_symbols),
        )


_analyzer = JuliaAnalyzer()


def is_julia_tree_sitter_available() -> bool:
    """Check if tree-sitter with Julia grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("julia")
def analyze_julia(repo_root: Path) -> AnalysisResult:
    """Analyze all Julia files in a repository.

    Returns a AnalysisResult with symbols, edges, and provenance.
    If tree-sitter-julia is not available, returns a skipped result.
    """
    return _analyzer.analyze(repo_root)
