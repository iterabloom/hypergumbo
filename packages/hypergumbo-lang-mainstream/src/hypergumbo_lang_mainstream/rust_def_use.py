# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rust def/use extractor for intraprocedural dataflow analysis (ADR-0017 §1c).

Extracts variable definitions (assignments) and uses (reads) from Rust
tree-sitter AST nodes. Handles the "Simple + Moderate" patterns from
ADR-0017 §1c Phase 2:

- ``let`` bindings (simple, tuple, struct destructuring)
- Reassignment (``x = expr``)
- Compound assignment (``x += expr``)
- Field writes (``self.field = expr``) — treated as mutation of ``self``
- Index writes (``data[idx] = val``) — treated as mutation of ``data``
- ``for`` loops (``for item in iter``)
- ``if let`` / ``while let`` / let-chains and ``match`` arm bindings
- ``return`` expressions
- Writes through a dereference (``*y = expr``)
- Closures: every identifier the body reads is a use of the statement that
  creates the closure, except the closure's own parameters. The body itself
  is built as its own CFG region (``nested_function`` in
  ``cfg_nodes/rust.yaml``), so its bindings never define the outer variables.

The ``?`` operator is handled at the CFG level (dual control-flow edges
via the ``early_return`` hook, ``semantics: return_on_err``, in
``cfg_nodes/rust.yaml``). The extractor treats the Ok-side binding as a
simple ``let`` define.

``ref``/``ref mut`` match-arm patterns and macro invocation arguments were
addressed in Phase 2b (WI-bifog). Borrow aliasing is not tracked: a write
through one reference does not define the variable it borrows.
"""
from __future__ import annotations

from typing import Any

from hypergumbo_core.cfg import DefUseResult, register_def_use_extractor
from hypergumbo_core.ddg_build import LanguageDdgSpec, register_ddg_language


def _node_text(node: Any, source: bytes) -> str:
    """Extract text from a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _collect_identifiers(node: Any, source: bytes) -> list[str]:
    """Collect all identifier uses from an expression subtree.

    Skips function/method names in call expressions (they're callees,
    not variable uses). Skips type identifiers and common Rust keywords.
    """
    result: list[str] = []
    _collect_ids_recursive(node, source, result)
    return result


def _collect_ids_recursive(node: Any, source: bytes, result: list[str]) -> None:
    """Recursive identifier collector for Rust expressions."""
    if node.type == "identifier":
        name = _node_text(node, source)
        if name not in _RUST_SKIP_NAMES:
            result.append(name)
        return

    if node.type == "self":
        result.append("self")
        return

    if node.type in ("type_identifier", "primitive_type", "scoped_identifier",
                      "use_declaration", "attribute_item", "line_comment",
                      "block_comment", "string_literal", "char_literal",
                      "integer_literal", "float_literal", "boolean_literal"):
        return

    if node.type == "call_expression":
        func = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        if func:
            # For method calls (field_expression), collect the receiver
            if func.type == "field_expression":
                value = func.child_by_field_name("value")
                if value:
                    _collect_ids_recursive(value, source, result)
            elif func.type != "identifier" and func.type != "scoped_identifier":
                _collect_ids_recursive(func, source, result)
        if args:
            _collect_ids_recursive(args, source, result)
        return

    if node.type == "macro_invocation":
        # Conservative: collect all identifiers from macro arguments
        for child in node.children:
            if child.type == "token_tree":
                _collect_ids_recursive(child, source, result)
        return

    for child in node.children:
        if child.is_named:
            _collect_ids_recursive(child, source, result)


def _collect_pattern_names(node: Any, source: bytes) -> list[str]:
    """Collect variable names bound by a Rust pattern.

    Handles: identifiers, tuple patterns, struct patterns (shorthand
    and named fields), tuple struct patterns (Some(x)), or patterns,
    slice patterns, and the wildcard ``_``.
    """
    if node.type == "identifier":
        name = _node_text(node, source)
        if name == "_":
            return []  # pragma: no cover — tree-sitter uses `_` node type
        return [name]

    if node.type in ("tuple_pattern", "slice_pattern"):
        names: list[str] = []
        for child in node.children:
            if child.is_named:
                names.extend(_collect_pattern_names(child, source))
        return names

    if node.type == "struct_pattern":
        names = []
        for child in node.children:
            if child.type == "field_pattern":
                # field_pattern contains shorthand_field_identifier or name: pattern
                for fc in child.children:
                    if fc.type == "shorthand_field_identifier":
                        names.append(_node_text(fc, source))
                    elif fc.type == "identifier":
                        names.append(_node_text(fc, source))
            elif child.type == "remaining_field_pattern":
                # .. in struct patterns — no bindings
                pass
        return names

    if node.type == "tuple_struct_pattern":
        # e.g., Some(x), Ok(val). The constructor is the ``type`` field, and
        # this grammar spells a bare one as an ``identifier``: without the
        # field test ``Some`` was collected as a bound NAME (seen once
        # WI-losod handed ``if let Some(x) = opt`` to this collector).
        names = []
        for i, child in enumerate(node.children):
            if (
                child.is_named
                and node.field_name_for_child(i) != "type"
                and child.type != "type_identifier"
                and child.type != "scoped_identifier"
            ):
                names.extend(_collect_pattern_names(child, source))
        return names

    if node.type == "or_pattern":
        # a | b — collect from all alternatives
        names = []
        for child in node.children:
            if child.is_named:
                names.extend(_collect_pattern_names(child, source))
        return names

    if node.type == "reference_pattern":
        # &x or &mut x
        for child in node.children:
            if child.is_named and child.type != "mutable_specifier":
                return _collect_pattern_names(child, source)
        return []  # pragma: no cover

    if node.type == "ref_pattern":
        # ref x or ref mut x — borrow binding in match arms
        for child in node.children:
            if child.type == "identifier":
                return [_node_text(child, source)]
            if child.is_named and child.type not in ("mutable_specifier",):  # pragma: no cover
                return _collect_pattern_names(child, source)
        return []  # pragma: no cover

    if node.type == "mut_pattern":
        # mut x
        for child in node.children:
            if child.type == "identifier":
                return [_node_text(child, source)]
        return []  # pragma: no cover

    if node.type == "match_pattern":
        # match_pattern wraps the actual pattern in match arms
        for child in node.children:
            if child.is_named:
                return _collect_pattern_names(child, source)
        return []  # pragma: no cover

    return []


# Names to skip when collecting identifier uses (Rust built-ins/keywords)
_RUST_SKIP_NAMES = frozenset({
    "_", "self", "Self", "super", "crate",
    "true", "false",
    "Some", "None", "Ok", "Err",
    "Vec", "String", "Box", "Rc", "Arc", "Cell", "RefCell",
    "Option", "Result", "HashMap", "HashSet", "BTreeMap", "BTreeSet",
    "println", "eprintln", "format", "panic", "todo", "unimplemented",
    "unreachable", "assert", "assert_eq", "assert_ne", "debug_assert",
    "cfg", "derive", "allow", "warn", "deny",
})


@register_def_use_extractor("rust")
class RustDefUseExtractor:
    """Extracts variable definitions and uses from Rust tree-sitter AST nodes.

    Registered as the "rust" def/use extractor. Handles Simple + Moderate
    patterns per ADR-0017 §1c Phase 2.
    """

    language = "rust"

    def extract(self, node: Any, source: bytes) -> DefUseResult:
        """Return variables defined and used by this AST node."""
        handler = _HANDLERS.get(node.type)
        if handler:
            return handler(node, source)
        # Default: collect all identifiers as uses
        return DefUseResult(uses=_collect_identifiers(node, source))


# ---------------------------------------------------------------------------
# Per-node-type handlers
# ---------------------------------------------------------------------------


def _handle_let_declaration(node: Any, source: bytes) -> DefUseResult:
    """Handle let binding: let x = expr, let (a, b) = expr, let Foo { f } = expr."""
    pattern = node.child_by_field_name("pattern")
    value = node.child_by_field_name("value")
    defines = _collect_pattern_names(pattern, source) if pattern else []
    uses = _collect_identifiers(value, source) if value else []
    return DefUseResult(defines=defines, uses=uses)


def _handle_assignment_expression(node: Any, source: bytes) -> DefUseResult:
    """Handle reassignment: x = expr, self.field = expr, data[idx] = expr."""
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    defines: list[str] = []
    uses: list[str] = []

    if left:
        if left.type == "identifier":
            defines.append(_node_text(left, source))
        elif left.type == "field_expression":
            # self.field = expr → mutates self/receiver
            value = left.child_by_field_name("value")
            if value:
                name = _node_text(value, source)
                defines.append(name)
        elif left.type == "index_expression":
            # data[idx] = expr → mutates data
            for child in left.children:
                if child.type == "identifier":
                    defines.append(_node_text(child, source))
                    break
            # idx is a use
            uses.extend(_collect_identifiers(left, source))
        elif left.type == "unary_expression":
            # *y = expr → dereference mutation (mutates through borrow)
            for child in left.children:
                if child.type == "identifier":
                    defines.append(_node_text(child, source))
                    break

    if right:
        uses.extend(_collect_identifiers(right, source))

    return DefUseResult(defines=defines, uses=uses)


def _handle_compound_assignment(node: Any, source: bytes) -> DefUseResult:
    """Handle compound assignment: x += expr (both defines and uses x)."""
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    left_names: list[str] = []
    if left and left.type == "identifier":
        left_names = [_node_text(left, source)]
    uses = list(left_names)  # x is read before modify
    if right:
        uses.extend(_collect_identifiers(right, source))
    return DefUseResult(defines=left_names, uses=uses)


def _handle_for_expression(node: Any, source: bytes) -> DefUseResult:
    """Handle for loop: for item in items.iter()."""
    pattern = node.child_by_field_name("pattern")
    value = node.child_by_field_name("value")
    defines = _collect_pattern_names(pattern, source) if pattern else []
    uses = _collect_identifiers(value, source) if value else []
    return DefUseResult(defines=defines, uses=uses)


def _handle_return_expression(node: Any, source: bytes) -> DefUseResult:
    """Handle return: return expr."""
    uses: list[str] = []
    for child in node.children:
        if child.is_named:
            uses.extend(_collect_identifiers(child, source))
    return DefUseResult(uses=uses)


def _handle_if_expression(node: Any, source: bytes) -> DefUseResult:
    """Handle if expression header (condition only, not body)."""
    condition = node.child_by_field_name("condition")
    if condition and condition.type in ("let_condition", "let_chain"):
        # if let Some(x) = expr
        return _handle_let_condition(condition, source)
    uses = _collect_identifiers(condition, source) if condition else []
    return DefUseResult(uses=uses)


def _handle_match_arm(node: Any, source: bytes) -> DefUseResult:
    """Handle match arm: pattern => expr."""
    pattern = node.child_by_field_name("pattern")
    defines = _collect_pattern_names(pattern, source) if pattern else []
    # The arm value is a use, but handled as a separate statement by CFG
    return DefUseResult(defines=defines)


def _handle_expression_statement(node: Any, source: bytes) -> DefUseResult:
    """Handle expression_statement wrapper (contains inner expression)."""
    # Delegate to the inner expression
    for child in node.children:
        if child.is_named:
            handler = _HANDLERS.get(child.type)
            if handler:
                return handler(child, source)
            return DefUseResult(uses=_collect_identifiers(child, source))
    return DefUseResult()


def _handle_closure_expression(node: Any, source: bytes) -> DefUseResult:
    """Handle closure: |x, y| expr (conservative capture).

    The body's identifiers are uses -- a captured value is read by the
    statement that creates the closure -- EXCEPT the closure's own parameters,
    which are bindings of the closure, not of the enclosing function. They were
    once returned as DEFINITIONS here, which was harmless only while nothing
    handed this handler a node: since cfg_nodes/rust.yaml records a closure
    met outside an atomic statement as a statement of its own (WI-faful), a
    parameter named like an outer variable would have killed it.
    """
    params = node.child_by_field_name("parameters")
    own: set[str] = set()
    if params:
        for child in params.children:
            if child.type == "identifier":
                own.add(_node_text(child, source))
    body = node.child_by_field_name("body")
    uses = _collect_identifiers(body, source) if body else []
    return DefUseResult(uses=[u for u in uses if u not in own])


def _handle_let_condition(node: Any, source: bytes) -> DefUseResult:
    """Handle `let PAT = expr` in an `if` / `while` header (WI-losod).

    The CFG records the condition of `if let Some(x) = opt {` as its statement,
    and in this grammar that node is a `let_condition`: it DEFINES the
    pattern's names and reads the value. A `let_chain` (`if let A(a) = x &&
    c`) holds several, each defining its own pattern.
    """
    if node.type == "let_chain":
        defines: list[str] = []
        uses: list[str] = []
        for child in node.named_children:
            handler = _HANDLERS.get(child.type)
            part = handler(child, source) if handler else DefUseResult(
                uses=_collect_identifiers(child, source),
            )
            defines.extend(part.defines)
            uses.extend(part.uses)
        return DefUseResult(defines=defines, uses=uses)
    pattern = node.child_by_field_name("pattern")
    value = node.child_by_field_name("value")
    defines = _collect_pattern_names(pattern, source) if pattern else []
    uses = _collect_identifiers(value, source) if value else []
    return DefUseResult(defines=defines, uses=uses)


# Map node types to handler functions
_HANDLERS: dict[str, Any] = {
    "let_declaration": _handle_let_declaration,
    "assignment_expression": _handle_assignment_expression,
    "compound_assignment_expr": _handle_compound_assignment,
    "for_expression": _handle_for_expression,
    "return_expression": _handle_return_expression,
    "if_expression": _handle_if_expression,
    "match_arm": _handle_match_arm,
    "expression_statement": _handle_expression_statement,
    "closure_expression": _handle_closure_expression,
    "let_condition": _handle_let_condition,
    "let_chain": _handle_let_condition,
}


# Rust has one function construct: an inherent or trait method is still a
# `function_item`, just nested inside an `impl_item`. So neither `name_for` nor
# `kind_for` is supplied — the defaults (the node's `name` field, kind
# "function") already match what the Rust analyzer puts in the id, and matching
# it is the point. A DDG symbol id that disagrees with the analyzer's id for
# the same function attributes coverage to a symbol nothing else refers to,
# which reads downstream as "this function has no DDG data".
register_ddg_language(LanguageDdgSpec(
    language="rust",
    function_node_types=frozenset({"function_item"}),
))
