# SPDX-License-Identifier: AGPL-3.0-or-later
"""Go def/use extractor for intraprocedural dataflow analysis (ADR-0017 §1c).

Extracts variable definitions (writes) and uses (reads) from Go tree-sitter
AST nodes, so ``solve_reaching_defs`` can chain a value from where it is
bound to where it is consumed. Without this, Go taint findings rest on call
reachability alone — "this function touches a secret and reaches a sink"
rather than "this value reaches that sink".

How It Works
------------
``extract()`` dispatches on ``node.type`` through ``_HANDLERS``. Anything
unrecognised falls back to "every identifier in the subtree is a use",
which is the conservative direction: it can only add spurious *uses*
(over-linking), never invent a definition that silently drops a real flow.

Handled statement shapes, all of them children of a ``statement_list``:

- ``a := expr`` / ``a, b := f()``           — short_var_declaration
- ``var x T = expr`` / ``const c = expr``   — var_declaration, const_declaration
- ``x = expr``, ``x += expr``               — assignment_statement
- ``obj.field = expr``                      — mutation of the *receiver*
- ``arr[i] = expr``                         — mutation of the *container*
- ``i++`` / ``i--``                         — inc_statement, dec_statement
- ``ch <- v``                               — send_statement (defines nothing)
- ``go f(x)``, bare calls                   — go_statement, expression_statement

Why a Node Type Must Also Be Declared Atomic
--------------------------------------------
This module is only half the mechanism. ``CfgBuilder`` decides which AST
nodes become ``CfgStatement``s, and it descends through any compound node
that neither ``classify()`` nor ``atomic_statement`` claims. Until go.yaml
declared ``atomic_statement``, the Go CFG bottomed out at bare
``identifier`` leaves, so ``extract()`` was never handed a
``short_var_declaration`` and produced zero DDG edges. An extractor can be
entirely correct against hand-picked nodes and contribute nothing; the
end-to-end assertion in the tests is what pins that.

Scope
-----
Intraprocedural and statement-level, matching the Python and Rust
extractors. Cross-function flow needs function summaries (ADR-0017 Phase
3). Two Go-specific bindings are *not* reached, because the CFG mapping's
conditional/loop hooks name only condition/body children and never the
initializer: ``if err := do(); err != nil`` and ``for i := 0; ...``, plus
``for i, v := range xs``. Those variables are invisible to def/use today.
Pointer aliasing (``p := &x; *p = tainted``) is not modelled — the same
class of gap as Rust borrow aliases.
"""
from __future__ import annotations

from typing import Any, Callable

from hypergumbo_core.cfg import DefUseResult, register_def_use_extractor
from hypergumbo_core.ddg_build import LanguageDdgSpec, register_ddg_language

# Go predeclared identifiers. These are never user variables, so treating
# them as uses would link statements through names that carry no data.
_GO_SKIP_NAMES = frozenset({
    "nil", "true", "false", "iota",
    "len", "cap", "make", "new", "append", "copy", "delete",
    "panic", "recover", "print", "println", "close",
    "complex", "real", "imag", "min", "max", "clear",
    "bool", "byte", "rune", "string", "error", "any",
    "int", "int8", "int16", "int32", "int64",
    "uint", "uint8", "uint16", "uint32", "uint64", "uintptr",
    "float32", "float64", "complex64", "complex128",
})

# Blank identifier: assigning to it discards the value, so it is not a
# definition anything can later read.
_BLANK = "_"

# Node types that carry no variable reference at all.
_LITERAL_TYPES = frozenset({
    "int_literal", "float_literal", "imaginary_literal", "rune_literal",
    "interpreted_string_literal", "raw_string_literal",
    "interpreted_string_literal_content", "string_content", "escape_sequence",
    "true", "false", "nil", "comment",
    "type_identifier", "field_identifier", "package_identifier",
    "qualified_type", "pointer_type", "slice_type", "map_type",
    "array_type", "channel_type", "function_type", "struct_type",
    "interface_type",
})


def _node_text(node: Any, source: bytes) -> str:
    """Extract text from a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _collect_identifiers(node: Any, source: bytes) -> list[str]:
    """Collect all identifier uses from an expression subtree."""
    result: list[str] = []
    _collect_ids_recursive(node, source, result)
    return result


def _collect_ids_recursive(node: Any, source: bytes, result: list[str]) -> None:
    """Recursive identifier collector for Go expressions."""
    if node.type == "identifier":
        name = _node_text(node, source)
        if name not in _GO_SKIP_NAMES and name != _BLANK:
            result.append(name)
        return

    if node.type in _LITERAL_TYPES:
        return

    if node.type == "call_expression":
        func = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        if func is not None:
            if func.type == "selector_expression":
                # log.Println(x) — `log` is a real reference, `Println` is
                # a field name, not a variable.
                operand = func.child_by_field_name("operand")
                if operand is not None:
                    _collect_ids_recursive(operand, source, result)
            elif func.type != "identifier":
                _collect_ids_recursive(func, source, result)
        if args is not None:
            _collect_ids_recursive(args, source, result)
        return

    for child in node.children:
        if child.is_named:
            _collect_ids_recursive(child, source, result)


def _collect_pattern_names(node: Any, source: bytes) -> list[str]:
    """Collect assignable variable names from an assignment target."""
    if node.type == "identifier":
        name = _node_text(node, source)
        return [] if name == _BLANK else [name]

    if node.type == "selector_expression":
        # obj.field = v mutates obj, which is what downstream reads see.
        operand = node.child_by_field_name("operand")
        if operand is not None and operand.type == "identifier":
            return [_node_text(operand, source)]
        return []

    if node.type == "index_expression":
        operand = node.child_by_field_name("operand")
        if operand is not None and operand.type == "identifier":
            return [_node_text(operand, source)]
        return []

    return []


def _names_from_expression_list(node: Any, source: bytes) -> list[str]:
    """Collect assignable names from an `expression_list` target."""
    names: list[str] = []
    for child in node.children:
        if child.is_named:
            names.extend(_collect_pattern_names(child, source))
    return names


@register_def_use_extractor("go")
class GoDefUseExtractor:
    """Extracts variable definitions and uses from Go tree-sitter AST nodes."""

    language = "go"

    def extract(self, node: Any, source: bytes) -> DefUseResult:
        """Return variables defined and used by this AST node."""
        handler = _HANDLERS.get(node.type)
        if handler:
            return handler(node, source)
        return DefUseResult(uses=_collect_identifiers(node, source))


def _handle_short_var_declaration(node: Any, source: bytes) -> DefUseResult:
    """Handle `a := expr` and `a, b := f()`."""
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    defines = _names_from_expression_list(left, source) if left is not None else []
    uses = _collect_identifiers(right, source) if right is not None else []
    return DefUseResult(defines=defines, uses=uses)


def _handle_spec_declaration(node: Any, source: bytes) -> DefUseResult:
    """Handle `var`/`const` declarations, which wrap one or more specs.

    A spec carries any number of `name` fields plus an optional `value`,
    so `var a, b = f(c)` defines both names and uses `c`.
    """
    defines: list[str] = []
    uses: list[str] = []
    for spec in node.children:
        if not spec.is_named:
            continue
        for i, child in enumerate(spec.children):
            field = spec.field_name_for_child(i)
            if field == "name":
                name = _node_text(child, source)
                if name != _BLANK:
                    defines.append(name)
            elif field == "value":
                uses.extend(_collect_identifiers(child, source))
    return DefUseResult(defines=defines, uses=uses)


def _handle_assignment_statement(node: Any, source: bytes) -> DefUseResult:
    """Handle `x = expr`, `obj.f = expr`, `arr[i] = expr`, `x += expr`."""
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    defines = _names_from_expression_list(left, source) if left is not None else []
    uses: list[str] = []

    # An augmented assignment (`+=`) reads its target before writing it.
    # The operator is an anonymous child, so read it off the source between
    # the two expression lists rather than guessing a node type.
    if left is not None and right is not None:
        operator = source[left.end_byte:right.start_byte].strip()
        if operator not in (b"=",):
            uses.extend(defines)

    # `arr[i] = v` reads arr and i as well as writing arr.
    if left is not None:
        for child in left.children:
            if child.is_named and child.type == "index_expression":
                uses.extend(_collect_identifiers(child, source))

    if right is not None:
        uses.extend(_collect_identifiers(right, source))
    return DefUseResult(defines=defines, uses=uses)


def _handle_inc_dec_statement(node: Any, source: bytes) -> DefUseResult:
    """Handle `i++` / `i--`, which both read and write the operand."""
    names: list[str] = []
    for child in node.children:
        if child.is_named:
            names.extend(_collect_pattern_names(child, source))
    return DefUseResult(defines=names, uses=list(names))


def _handle_send_statement(node: Any, source: bytes) -> DefUseResult:
    """Handle `ch <- v`. The channel is read, not defined."""
    uses: list[str] = []
    for field in ("channel", "value"):
        child = node.child_by_field_name(field)
        if child is not None:
            uses.extend(_collect_identifiers(child, source))
    return DefUseResult(uses=uses)


def _handle_expression_statement(node: Any, source: bytes) -> DefUseResult:
    """Handle a bare expression statement (usually a call)."""
    uses: list[str] = []
    for child in node.children:
        if child.is_named:
            uses.extend(_collect_identifiers(child, source))
    return DefUseResult(uses=uses)


# Typed as the callable it holds rather than ``Any``: every value here is a
# ``(node, source) -> DefUseResult`` handler, and declaring that is what lets
# ``extract`` return the dispatch result directly instead of returning ``Any``
# from a function annotated ``DefUseResult``. A bare ``Any`` here silenced the
# one place the dispatch contract could be checked at all.
_HANDLERS: dict[str, Callable[[Any, bytes], DefUseResult]] = {
    "short_var_declaration": _handle_short_var_declaration,
    "var_declaration": _handle_spec_declaration,
    "const_declaration": _handle_spec_declaration,
    "assignment_statement": _handle_assignment_statement,
    "expression_statement": _handle_expression_statement,
    "inc_statement": _handle_inc_dec_statement,
    "dec_statement": _handle_inc_dec_statement,
    "send_statement": _handle_send_statement,
    "go_statement": _handle_expression_statement,
}


# --------------------------------------------------------------------------
# Repo-level DDG spec (ADR-0017 §1c)
#
# Registered from this package rather than from core because naming a Go
# method requires the analyzer's receiver-type helper, and reproducing that
# in core would fork the convention: the symbol ids minted here must match
# the ones go.py emitted for the same declarations, or `ddg_symbols` will
# not line up with the structural BFS's node keys.
# --------------------------------------------------------------------------


def _go_function_name(node: Any, source: bytes) -> str:
    """Name a Go function or method exactly as the Go analyzer does."""
    from .go import _extract_receiver_type_from_node

    name_node = node.child_by_field_name("name")
    if name_node is None:  # pragma: no cover - grammar always supplies a name
        return ""
    name = _node_text(name_node, source)
    receiver_node = node.child_by_field_name("receiver")
    if receiver_node is None:
        return name
    receiver_type = _extract_receiver_type_from_node(receiver_node, source)
    return f"{receiver_type}.{name}" if receiver_type else name


def _go_symbol_kind(node: Any) -> str:
    """Methods and plain functions occupy different id kind slots."""
    return "method" if node.type == "method_declaration" else "function"


register_ddg_language(LanguageDdgSpec(
    language="go",
    file_glob="*.go",
    function_node_types=frozenset({"function_declaration", "method_declaration"}),
    name_for=_go_function_name,
    kind_for=_go_symbol_kind,
))
