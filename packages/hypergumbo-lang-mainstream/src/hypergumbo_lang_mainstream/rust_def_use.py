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
- ``if let`` / ``match`` arm bindings
- ``return`` expressions
- Closure parameters (conservative: all captured vars are uses)

The ``?`` operator is handled at the CFG level (dual control-flow edges
via the ``early_return_on_error`` semantic hook). The extractor treats
the Ok-side binding as a simple ``let`` define.

Hard patterns (borrow alias tracking, ``ref``/``ref mut`` in match arms,
macro invocation analysis) are deferred to Phase 2b (WI-bifog).
"""
from __future__ import annotations

from typing import Any

from hypergumbo_core.cfg import DefUseResult, register_def_use_extractor


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
        # e.g., Some(x), Ok(val)
        names = []
        for child in node.children:
            if child.is_named and child.type != "type_identifier" and child.type != "scoped_identifier":
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
    if condition and condition.type == "let_condition":
        # if let Some(x) = expr
        pattern = condition.child_by_field_name("pattern")
        value = condition.child_by_field_name("value")
        defines = _collect_pattern_names(pattern, source) if pattern else []
        uses = _collect_identifiers(value, source) if value else []
        return DefUseResult(defines=defines, uses=uses)
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
    """Handle closure: |x, y| expr (conservative capture)."""
    params = node.child_by_field_name("parameters")
    defines: list[str] = []
    if params:
        for child in params.children:
            if child.type == "identifier":
                name = _node_text(child, source)
                if name != "_":
                    defines.append(name)
    # Body identifiers are conservative uses (captures)
    body = node.child_by_field_name("body")
    uses = _collect_identifiers(body, source) if body else []
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
}
