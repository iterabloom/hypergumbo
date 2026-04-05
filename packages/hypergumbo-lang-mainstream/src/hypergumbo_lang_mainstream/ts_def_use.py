# SPDX-License-Identifier: AGPL-3.0-or-later
"""TypeScript/JavaScript def/use extractor for intraprocedural dataflow (ADR-0017 §1c).

Extracts variable definitions and uses from TypeScript tree-sitter AST nodes.
Also works for JavaScript (same core node types). Handles core patterns:

- ``const``/``let``/``var`` declarations with simple, array, and object destructuring
- Assignment expressions (``x = expr``)
- Augmented assignments (``x += expr``)
- Member expression writes (``this.field = expr``) — mutation of receiver
- Subscript expression writes (``data[idx] = val``) — mutation of object
- ``for...of`` / ``for...in`` loops
- ``return`` statements
- Expression statements (bare function calls)

Complex patterns (optional chaining, spread, computed properties, JSX) are
handled conservatively: all identifiers in unknown constructs are treated as
uses. This covers the majority of taint-relevant data flow in TypeScript code.
"""
from __future__ import annotations

from typing import Any

from hypergumbo_core.cfg import DefUseResult, register_def_use_extractor


def _node_text(node: Any, source: bytes) -> str:
    """Extract text from a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _collect_identifiers(node: Any, source: bytes) -> list[str]:
    """Collect all identifier uses from an expression subtree."""
    result: list[str] = []
    _collect_ids_recursive(node, source, result)
    return result


def _collect_ids_recursive(node: Any, source: bytes, result: list[str]) -> None:
    """Recursive identifier collector for TypeScript expressions."""
    if node.type == "identifier":
        name = _node_text(node, source)
        if name not in _TS_SKIP_NAMES:
            result.append(name)
        return

    if node.type == "this":
        result.append("this")
        return

    if node.type in ("type_identifier", "type_annotation", "type_parameters",
                      "string", "template_string", "number", "true", "false",
                      "null", "undefined", "regex", "comment"):
        return

    if node.type == "call_expression":
        func = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        if func:
            if func.type == "member_expression":
                obj = func.child_by_field_name("object")
                if obj:
                    _collect_ids_recursive(obj, source, result)
            elif func.type != "identifier":
                _collect_ids_recursive(func, source, result)
        if args:
            _collect_ids_recursive(args, source, result)
        return

    for child in node.children:
        if child.is_named:
            _collect_ids_recursive(child, source, result)


def _collect_pattern_names(node: Any, source: bytes) -> list[str]:
    """Collect variable names from TS assignment/declaration target patterns."""
    if node.type == "identifier":
        return [_node_text(node, source)]

    if node.type == "shorthand_property_identifier_pattern":
        return [_node_text(node, source)]

    if node.type == "array_pattern":
        names: list[str] = []
        for child in node.children:
            if child.is_named:
                names.extend(_collect_pattern_names(child, source))
        return names

    if node.type == "object_pattern":
        names = []
        for child in node.children:
            if child.type == "shorthand_property_identifier_pattern":
                names.append(_node_text(child, source))
            elif child.type == "pair_pattern":
                value = child.child_by_field_name("value")
                if value:
                    names.extend(_collect_pattern_names(value, source))
            elif child.type in ("object_assignment_pattern", "assignment_pattern"):
                names.extend(_collect_pattern_names(child, source))
        return names

    if node.type == "rest_pattern":
        for child in node.children:
            if child.type == "identifier":
                return [_node_text(child, source)]
        return []  # pragma: no cover

    if node.type in ("assignment_pattern", "object_assignment_pattern"):
        left = node.child_by_field_name("left")
        if left:
            return _collect_pattern_names(left, source)
        # object_assignment_pattern may have the identifier as first named child
        for child in node.children:  # pragma: no cover
            if child.type == "identifier":  # pragma: no cover
                return [_node_text(child, source)]  # pragma: no cover
        return []  # pragma: no cover

    if node.type == "member_expression":
        obj = node.child_by_field_name("object")
        if obj and obj.type == "identifier":
            return [_node_text(obj, source)]
        if obj and obj.type == "this":
            return ["this"]
        return []

    if node.type == "subscript_expression":
        obj = node.child_by_field_name("object")
        if obj and obj.type == "identifier":
            return [_node_text(obj, source)]
        return []

    return []


_TS_SKIP_NAMES = frozenset({
    "undefined", "null", "NaN", "Infinity",
    "console", "JSON", "Math", "Date", "Object", "Array", "String",
    "Number", "Boolean", "Symbol", "BigInt", "Promise", "Map", "Set",
    "WeakMap", "WeakSet", "Error", "TypeError", "RangeError",
    "parseInt", "parseFloat", "isNaN", "isFinite",
    "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    "require", "module", "exports", "__dirname", "__filename",
})


@register_def_use_extractor("typescript")
class TypeScriptDefUseExtractor:
    """Extracts variable definitions and uses from TypeScript tree-sitter AST nodes."""

    language = "typescript"

    def extract(self, node: Any, source: bytes) -> DefUseResult:
        """Return variables defined and used by this AST node."""
        handler = _HANDLERS.get(node.type)
        if handler:
            return handler(node, source)
        return DefUseResult(uses=_collect_identifiers(node, source))


def _handle_lexical_declaration(node: Any, source: bytes) -> DefUseResult:
    """Handle const/let/var declarations."""
    defines: list[str] = []
    uses: list[str] = []
    for child in node.children:
        if child.type == "variable_declarator":
            name_node = child.child_by_field_name("name")
            value_node = child.child_by_field_name("value")
            if name_node:
                defines.extend(_collect_pattern_names(name_node, source))
            if value_node:
                uses.extend(_collect_identifiers(value_node, source))
    return DefUseResult(defines=defines, uses=uses)


def _handle_variable_declaration(node: Any, source: bytes) -> DefUseResult:
    """Handle var declarations (same structure as lexical_declaration)."""
    return _handle_lexical_declaration(node, source)


def _handle_assignment_expression(node: Any, source: bytes) -> DefUseResult:
    """Handle assignment: x = expr, this.field = expr, data[idx] = expr."""
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    defines = _collect_pattern_names(left, source) if left else []
    uses: list[str] = []
    if left and left.type == "subscript_expression":
        uses.extend(_collect_identifiers(left, source))
    if right:
        uses.extend(_collect_identifiers(right, source))
    return DefUseResult(defines=defines, uses=uses)


def _handle_augmented_assignment(node: Any, source: bytes) -> DefUseResult:
    """Handle augmented assignment: x += expr."""
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    left_names = _collect_pattern_names(left, source) if left else []
    uses = list(left_names)
    if right:
        uses.extend(_collect_identifiers(right, source))
    return DefUseResult(defines=left_names, uses=uses)


def _handle_return_statement(node: Any, source: bytes) -> DefUseResult:
    """Handle return statement."""
    uses: list[str] = []
    for child in node.children:
        if child.is_named:
            uses.extend(_collect_identifiers(child, source))
    return DefUseResult(uses=uses)


def _handle_for_in_statement(node: Any, source: bytes) -> DefUseResult:
    """Handle for...of / for...in: for (const item of items)."""
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    defines: list[str] = []
    if left:
        if left.type == "identifier":
            defines.append(_node_text(left, source))
        else:
            defines.extend(_collect_pattern_names(left, source))
    uses = _collect_identifiers(right, source) if right else []
    return DefUseResult(defines=defines, uses=uses)


def _handle_expression_statement(node: Any, source: bytes) -> DefUseResult:
    """Handle expression statement wrapper."""
    for child in node.children:
        if child.is_named:
            handler = _HANDLERS.get(child.type)
            if handler:
                return handler(child, source)
            return DefUseResult(uses=_collect_identifiers(child, source))
    return DefUseResult()


_HANDLERS: dict[str, Any] = {
    "lexical_declaration": _handle_lexical_declaration,
    "variable_declaration": _handle_variable_declaration,
    "assignment_expression": _handle_assignment_expression,
    "augmented_assignment_expression": _handle_augmented_assignment,
    "return_statement": _handle_return_statement,
    "for_in_statement": _handle_for_in_statement,
    "expression_statement": _handle_expression_statement,
}
