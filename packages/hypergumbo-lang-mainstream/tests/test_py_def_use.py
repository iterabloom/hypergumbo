# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Python def/use extractor (ADR-0017 §1c).

Validates variable definition and use extraction from Python tree-sitter
AST nodes. Tests use real tree-sitter parsing to ensure correct handling
of assignments, augmented assignments, unpacking, for loops, returns,
deletes, and expression statements.
"""
from __future__ import annotations

from typing import Any

import tree_sitter
from tree_sitter_language_pack import get_language

from hypergumbo_core.cfg import (
    DefUseResult,
    clear_def_use_extractors,
    get_def_use_extractor,
)
from hypergumbo_lang_mainstream.py_def_use import (
    PythonDefUseExtractor,
    _collect_identifiers,
    _collect_pattern_names,
    _handle_assignment,
    _handle_augmented_assignment,
    _handle_delete,
    _handle_for,
    _handle_for_in_clause,
    _handle_return,
)


def _parse(source: str) -> tuple[Any, bytes]:
    """Parse Python source and return (tree, source_bytes)."""
    lang = get_language("python")
    parser = tree_sitter.Parser(lang)
    src = source.encode("utf-8")
    tree = parser.parse(src)
    return tree, src


def _first_stmt(tree: Any) -> Any:
    """Return the first statement node in the module."""
    return tree.root_node.children[0]


class TestPythonDefUseExtractor:
    """Test the PythonDefUseExtractor class."""

    def test_registered(self) -> None:
        # Re-register in case core tests cleared the registry
        import importlib
        import hypergumbo_lang_mainstream.py_def_use as mod
        importlib.reload(mod)
        ext = get_def_use_extractor("python")
        assert ext is not None
        assert ext.language == "python"

    def test_simple_assignment(self) -> None:
        tree, src = _parse("x = 1\n")
        ext = PythonDefUseExtractor()
        result = ext.extract(_first_stmt(tree), src)
        assert result.defines == ["x"]
        assert result.uses == []

    def test_assignment_with_expression(self) -> None:
        tree, src = _parse("y = x + z\n")
        ext = PythonDefUseExtractor()
        result = ext.extract(_first_stmt(tree), src)
        assert result.defines == ["y"]
        assert set(result.uses) == {"x", "z"}

    def test_tuple_unpacking(self) -> None:
        tree, src = _parse("a, b = func()\n")
        ext = PythonDefUseExtractor()
        result = ext.extract(_first_stmt(tree), src)
        assert set(result.defines) == {"a", "b"}

    def test_augmented_assignment(self) -> None:
        tree, src = _parse("x += y\n")
        ext = PythonDefUseExtractor()
        result = ext.extract(_first_stmt(tree), src)
        assert result.defines == ["x"]
        assert "x" in result.uses  # x is read before modify
        assert "y" in result.uses

    def test_return_statement(self) -> None:
        tree, src = _parse("return result\n")
        ext = PythonDefUseExtractor()
        result = ext.extract(_first_stmt(tree), src)
        assert result.defines == []
        assert "result" in result.uses

    def test_return_expression(self) -> None:
        tree, src = _parse("return x + y\n")
        ext = PythonDefUseExtractor()
        result = ext.extract(_first_stmt(tree), src)
        assert set(result.uses) == {"x", "y"}

    def test_delete_statement(self) -> None:
        tree, src = _parse("del var\n")
        ext = PythonDefUseExtractor()
        result = ext.extract(_first_stmt(tree), src)
        assert result.defines == ["var"]

    def test_for_loop_header(self) -> None:
        tree, src = _parse("for i in items:\n    pass\n")
        ext = PythonDefUseExtractor()
        result = ext.extract(_first_stmt(tree), src)
        assert result.defines == ["i"]
        assert "items" in result.uses

    def test_bare_call(self) -> None:
        """A bare call like process(data) parses as a call node."""
        tree, src = _parse("process(data)\n")
        ext = PythonDefUseExtractor()
        result = ext.extract(_first_stmt(tree), src)
        assert result.defines == []
        assert "data" in result.uses

    def test_method_call(self) -> None:
        tree, src = _parse("obj.method(arg)\n")
        ext = PythonDefUseExtractor()
        result = ext.extract(_first_stmt(tree), src)
        assert "obj" in result.uses
        assert "arg" in result.uses

    def test_complex_call_expression(self) -> None:
        """Callable from complex expression: (get_func())(arg)."""
        tree, src = _parse("(get_func())(arg)\n")
        ext = PythonDefUseExtractor()
        result = ext.extract(_first_stmt(tree), src)
        assert "arg" in result.uses

    def test_nested_attribute_assignment(self) -> None:
        """obj.nested.attr = val — only top-level obj."""
        tree, src = _parse("obj.nested.attr = val\n")
        ext = PythonDefUseExtractor()
        result = ext.extract(_first_stmt(tree), src)
        # Nested attribute: obj_node is an attribute, not identifier
        assert "val" in result.uses

    def test_nested_subscript_assignment(self) -> None:
        """obj.x[0] = val — subscript with non-identifier value."""
        tree, src = _parse("obj.x[0] = val\n")
        ext = PythonDefUseExtractor()
        result = ext.extract(_first_stmt(tree), src)
        assert "val" in result.uses

    def test_attribute_assignment(self) -> None:
        tree, src = _parse("self.x = value\n")
        ext = PythonDefUseExtractor()
        result = ext.extract(_first_stmt(tree), src)
        assert "self" in result.defines  # mutates self
        assert "value" in result.uses

    def test_subscript_assignment(self) -> None:
        tree, src = _parse("data[key] = value\n")
        ext = PythonDefUseExtractor()
        result = ext.extract(_first_stmt(tree), src)
        assert "data" in result.defines  # mutates data
        assert "value" in result.uses

    def test_builtin_names_excluded(self) -> None:
        tree, src = _parse("x = len(items)\n")
        ext = PythonDefUseExtractor()
        result = ext.extract(_first_stmt(tree), src)
        assert "len" not in result.uses
        assert "items" in result.uses
        assert result.defines == ["x"]

    def test_unknown_node_type(self) -> None:
        """Unknown node types should just collect identifiers as uses."""
        tree, src = _parse("if x:\n    pass\n")
        ext = PythonDefUseExtractor()
        result = ext.extract(_first_stmt(tree), src)
        # if_statement is not in _HANDLERS, so default handler kicks in
        assert "x" in result.uses

    def test_assignment_from_call(self) -> None:
        tree, src = _parse("result = process(data)\n")
        ext = PythonDefUseExtractor()
        result = ext.extract(_first_stmt(tree), src)
        assert result.defines == ["result"]
        assert "data" in result.uses
        # process is a callee, not a use
        assert "process" not in result.uses


class TestCollectIdentifiers:
    """Test identifier collection helpers."""

    def test_simple_identifier(self) -> None:
        tree, src = _parse("x\n")
        node = _first_stmt(tree)  # expression_statement
        result = _collect_identifiers(node, src)
        assert "x" in result

    def test_binary_expression(self) -> None:
        tree, src = _parse("a + b\n")
        node = _first_stmt(tree)
        result = _collect_identifiers(node, src)
        assert set(result) == {"a", "b"}

    def test_call_with_args(self) -> None:
        tree, src = _parse("foo(a, b)\n")
        node = _first_stmt(tree)
        result = _collect_identifiers(node, src)
        assert "a" in result
        assert "b" in result
        assert "foo" not in result  # callee excluded

    def test_attribute_access(self) -> None:
        tree, src = _parse("obj.method(arg)\n")
        node = _first_stmt(tree)
        result = _collect_identifiers(node, src)
        assert "obj" in result
        assert "arg" in result


class TestCollectPatternNames:
    """Test pattern name collection for assignment targets."""

    def test_simple_identifier(self) -> None:
        tree, src = _parse("x = 1\n")
        left = _first_stmt(tree).child_by_field_name("left")
        assert _collect_pattern_names(left, src) == ["x"]

    def test_pattern_list(self) -> None:
        tree, src = _parse("a, b, c = t\n")
        left = _first_stmt(tree).child_by_field_name("left")
        assert _collect_pattern_names(left, src) == ["a", "b", "c"]

    def test_star_unpacking(self) -> None:
        tree, src = _parse("a, *rest = items\n")
        left = _first_stmt(tree).child_by_field_name("left")
        names = _collect_pattern_names(left, src)
        assert "a" in names
        assert "rest" in names

    def test_attribute_target(self) -> None:
        tree, src = _parse("self.x = 1\n")
        left = _first_stmt(tree).child_by_field_name("left")
        assert _collect_pattern_names(left, src) == ["self"]

    def test_subscript_target(self) -> None:
        tree, src = _parse("data[0] = 1\n")
        left = _first_stmt(tree).child_by_field_name("left")
        assert _collect_pattern_names(left, src) == ["data"]

    def test_unknown_pattern(self) -> None:
        """Non-identifier patterns should return empty list."""
        tree, src = _parse("1 + 2\n")
        node = _first_stmt(tree)  # expression_statement
        # This is not a valid assignment target, but test the fallback
        assert _collect_pattern_names(node, src) == []


class TestForInClause:
    """Test comprehension for-clause handling."""

    def test_comprehension_for(self) -> None:
        tree, src = _parse("[x for x in items]\n")
        comp = _first_stmt(tree)  # list_comprehension directly
        for_clause = None
        for child in comp.children:
            if child.type == "for_in_clause":
                for_clause = child
                break
        assert for_clause is not None
        result = _handle_for_in_clause(for_clause, src)
        assert result.defines == ["x"]
        assert "items" in result.uses
