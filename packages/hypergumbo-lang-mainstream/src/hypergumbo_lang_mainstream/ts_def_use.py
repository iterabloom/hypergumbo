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
from hypergumbo_core.ddg_build import LanguageDdgSpec, register_ddg_language


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


@register_def_use_extractor("javascript")
@register_def_use_extractor("typescript")
class TypeScriptDefUseExtractor:
    """Extracts variable definitions and uses from TypeScript/JavaScript AST nodes.

    Registered under BOTH keys off one class because ``_HANDLERS`` is keyed on
    tree-sitter node types and tree-sitter-javascript emits the same ones. A
    second class would be a copy whose only difference is the string it is
    registered under — and a copy is the thing that drifts.

    Each registration gets its own instance with its own ``language`` stamp;
    see ``register_def_use_extractor``.
    """

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


# Registered so that TypeScript's def/use extractor is actually reachable from
# the repo-level DDG builder; without a spec, build_repo_ddg skips the language
# outright and the extractor stays dead no matter how correct it is.
#
# SCOPE, deliberately narrow and disclosed rather than implied:
#
#   * Only `function_declaration`. Class methods (`method_definition`) are NOT
#     registered, because the analyzer's kind slot for them is getter/setter
#     sensitive (js_ts.py decides "method" / "getter" / "setter" inline) and
#     re-deriving that classification here would put a second copy of a
#     production judgement in a consumer — the precise shape that has produced
#     wrong numbers in this subsystem before. Reusing the analyzer's decision
#     requires extracting it into a shared helper first; filed separately.
#
#   * TypeScript currently has ZERO catalogued taint sinks (6 sources, 0 sinks),
#     so no TypeScript DDG edge can change a taint verdict today whatever this
#     covers. That is stated so the registration is not mistaken for a taint
#     improvement: it makes the language conform to the wiring gate and be ready
#     when sinks land, and nothing more.
#
#   * JavaScript is now wired too (WI-nonad), which is why there are two specs
#     below rather than one. It matters more than the TypeScript registration
#     it rode in on, and for the opposite reason: TypeScript carries 6 sources
#     and ZERO sinks, JavaScript carries 50 sources and 83 SINKS, so JavaScript
#     is the half of this pair whose DDG edges can actually change a taint
#     verdict. The three keys it needed — mapping, extractor, spec — are all
#     aliases or second registrations rather than new logic, because
#     `cfg_nodes/typescript.yaml` states in its own header that it "also covers
#     JavaScript (same node types in tree-sitter-javascript)". See
#     `cfg._CFG_MAPPING_ALIASES` for why that assertion is encoded once instead
#     of copied into a second YAML.
#
#     NOT A CLAIM ABOUT RECALL. Wiring makes `.js` files reach the machinery;
#     it does not establish that any taint verdict improves. That is an A/B on
#     express (all `.js`) and apollo-server, read per-flow against source, and
#     it is deliberately NOT asserted here.
#
#     One decayed number corrected: an earlier revision of this comment cited
#     apollo-server at 588 DDG edges over 93 symbols and express at ZERO. The
#     express-is-zero half is what this change addresses; the apollo-server
#     figure was measured on an older tree and should be re-measured before
#     being cited, not copied forward.
register_ddg_language(LanguageDdgSpec(
    language="typescript",
    file_glob="*.ts",
    function_node_types=frozenset({"function_declaration"}),
))

# Same grammar, same function node types, different glob. `*.js` only — `.mjs`
# / `.cjs` / `.jsx` are deliberately NOT claimed here: each is a separate
# question about what the discovery layer classifies as `javascript`, and
# widening the glob on an assumption is how a spec starts lying about its own
# coverage.
register_ddg_language(LanguageDdgSpec(
    language="javascript",
    file_glob="*.js",
    function_node_types=frozenset({"function_declaration"}),
))
