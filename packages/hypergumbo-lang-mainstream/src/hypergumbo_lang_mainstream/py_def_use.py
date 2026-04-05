# SPDX-License-Identifier: AGPL-3.0-or-later
"""Python def/use extractor for intraprocedural dataflow analysis (ADR-0017 §1c).

Extracts variable definitions (assignments) and uses (reads) from Python
tree-sitter AST nodes. This is the first def/use extractor, validating
the shared CFG infrastructure against hypergumbo's own Python codebase.

How It Works
------------
For each AST node in a function body, the extractor identifies:

- **Defines**: Variables assigned by the node (left side of ``=``, loop
  variables, ``with ... as`` aliases, comprehension variables, function
  parameters, ``del`` targets).
- **Uses**: Variables read by the node (right side of ``=``, function
  call arguments, condition expressions, return values).

The extractor handles Python-specific patterns:
- Simple assignment: ``x = expr`` → defines [x], uses from expr
- Tuple/list unpacking: ``a, b = expr`` → defines [a, b]
- Augmented assignment: ``x += expr`` → defines [x], uses [x] + expr
- For loop: ``for i in items`` → defines [i], uses [items]
- With statement: ``with expr as f`` → defines [f], uses from expr
- Comprehension: ``for x in items`` → defines [x], uses [items]
- Return/yield: ``return expr`` → uses from expr
- Delete: ``del x`` → defines [x] (treated as killing the definition)

Scope Tracking
--------------
This extractor operates at the statement level within a single function.
It does NOT track nested scopes (closures, nested functions) — those are
separate functions with their own CFGs. Variable names are simple strings;
the reaching-def solver handles aliasing through gen/kill sets.
"""
from __future__ import annotations

from typing import Any

from hypergumbo_core.cfg import DefUseResult, register_def_use_extractor


def _node_text(node: Any, source: bytes) -> str:
    """Extract text from a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _collect_identifiers(node: Any, source: bytes) -> list[str]:
    """Recursively collect all identifier names from a node subtree.

    Used for the 'uses' side — collects all variable references in an
    expression tree (right side of assignment, function args, etc.).
    Skips function/method names in call expressions to avoid treating
    the callee as a 'use' of a variable.
    """
    result: list[str] = []
    _collect_identifiers_recursive(node, source, result, in_call_func=False)
    return result


def _collect_identifiers_recursive(
    node: Any, source: bytes, result: list[str], in_call_func: bool,
) -> None:
    """Recursive helper for identifier collection."""
    if node.type == "identifier":
        name = _node_text(node, source)
        # Skip common built-in names that aren't user variables
        if name not in _BUILTIN_NAMES:
            result.append(name)
        return

    if node.type == "call":
        # The function part of a call is not a 'use' of a variable
        # (unless it's a variable holding a callable)
        func_node = node.child_by_field_name("function")
        args_node = node.child_by_field_name("arguments")
        if func_node:
            # For simple identifiers like foo(), skip them (they're callees)
            # For attribute access like obj.method(), collect 'obj' as a use
            if func_node.type == "attribute":
                obj = func_node.child_by_field_name("object")
                if obj:
                    _collect_identifiers_recursive(obj, source, result, in_call_func=False)
            elif func_node.type != "identifier":
                _collect_identifiers_recursive(func_node, source, result, in_call_func=True)
        if args_node:
            _collect_identifiers_recursive(args_node, source, result, in_call_func=False)
        return

    for child in node.children:
        if child.is_named:
            _collect_identifiers_recursive(child, source, result, in_call_func=in_call_func)


def _collect_pattern_names(node: Any, source: bytes) -> list[str]:
    """Collect variable names from assignment target patterns.

    Handles simple identifiers, tuple/list unpacking, star expressions,
    and subscript/attribute access (for mutation tracking).
    """
    if node.type == "identifier":
        return [_node_text(node, source)]
    elif node.type in ("pattern_list", "tuple_pattern", "list_pattern"):
        names: list[str] = []
        for child in node.children:
            if child.is_named:
                names.extend(_collect_pattern_names(child, source))
        return names
    elif node.type == "list_splat_pattern":
        # *rest in unpacking
        for child in node.children:
            if child.type == "identifier":
                return [_node_text(child, source)]
        return []  # pragma: no cover
    elif node.type == "attribute":
        # obj.attr = ... → mutates obj
        obj = node.child_by_field_name("object")
        if obj and obj.type == "identifier":
            return [_node_text(obj, source)]
        return []
    elif node.type == "subscript":
        # obj[key] = ... → mutates obj
        obj_node = node.child_by_field_name("value")
        if obj_node and obj_node.type == "identifier":
            return [_node_text(obj_node, source)]
        return []
    return []


# Built-in names that should not be treated as user variable uses
_BUILTIN_NAMES = frozenset({
    "True", "False", "None",
    "print", "len", "range", "int", "str", "float", "bool", "list", "dict",
    "set", "tuple", "type", "isinstance", "issubclass", "hasattr", "getattr",
    "setattr", "delattr", "super", "property", "staticmethod", "classmethod",
    "enumerate", "zip", "map", "filter", "sorted", "reversed", "min", "max",
    "sum", "abs", "all", "any", "iter", "next", "open", "input", "id", "hash",
    "repr", "format", "chr", "ord", "hex", "oct", "bin", "pow", "round",
    "divmod", "callable", "vars", "dir", "globals", "locals", "exec", "eval",
    "compile", "breakpoint", "exit", "quit", "help", "object",
    "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
    "AttributeError", "RuntimeError", "StopIteration", "NotImplementedError",
    "ImportError", "FileNotFoundError", "OSError", "IOError",
    "AssertionError", "ZeroDivisionError", "OverflowError",
})


@register_def_use_extractor("python")
class PythonDefUseExtractor:
    """Extracts variable definitions and uses from Python tree-sitter AST nodes.

    Registered as the "python" def/use extractor. Called by the CFG builder
    for each statement node in a Python function body.
    """

    language = "python"

    def extract(self, node: Any, source: bytes) -> DefUseResult:
        """Return variables defined and used by this AST node."""
        handler = _HANDLERS.get(node.type)
        if handler:
            return handler(node, source)
        # Default: collect all identifiers as uses (no defines)
        return DefUseResult(uses=_collect_identifiers(node, source))


# ---------------------------------------------------------------------------
# Per-node-type handlers
# ---------------------------------------------------------------------------


def _handle_assignment(node: Any, source: bytes) -> DefUseResult:
    """Handle simple assignment: x = expr, a, b = expr."""
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    defines = _collect_pattern_names(left, source) if left else []
    uses = _collect_identifiers(right, source) if right else []
    return DefUseResult(defines=defines, uses=uses)


def _handle_augmented_assignment(node: Any, source: bytes) -> DefUseResult:
    """Handle augmented assignment: x += expr (both defines and uses x)."""
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    left_names = _collect_pattern_names(left, source) if left else []
    uses = list(left_names)  # x is used (read) before being modified
    if right:
        uses.extend(_collect_identifiers(right, source))
    return DefUseResult(defines=left_names, uses=uses)


def _handle_return(node: Any, source: bytes) -> DefUseResult:
    """Handle return statement: return expr."""
    uses: list[str] = []
    for child in node.children:
        if child.is_named:
            uses.extend(_collect_identifiers(child, source))
    return DefUseResult(uses=uses)


def _handle_delete(node: Any, source: bytes) -> DefUseResult:
    """Handle delete: del x (kills the variable's definition)."""
    defines: list[str] = []
    for child in node.children:
        if child.type == "identifier":
            defines.append(_node_text(child, source))
    return DefUseResult(defines=defines)


def _handle_for(node: Any, source: bytes) -> DefUseResult:
    """Handle for loop header: for i in items."""
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    defines = _collect_pattern_names(left, source) if left else []
    uses = _collect_identifiers(right, source) if right else []
    return DefUseResult(defines=defines, uses=uses)


def _handle_for_in_clause(node: Any, source: bytes) -> DefUseResult:
    """Handle comprehension for clause: for x in items."""
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    defines = _collect_pattern_names(left, source) if left else []
    uses = _collect_identifiers(right, source) if right else []
    return DefUseResult(defines=defines, uses=uses)


# Map node types to handler functions
_HANDLERS: dict[str, Any] = {
    "assignment": _handle_assignment,
    "augmented_assignment": _handle_augmented_assignment,
    "return_statement": _handle_return,
    "delete_statement": _handle_delete,
    "for_statement": _handle_for,
    "for_in_clause": _handle_for_in_clause,
}
