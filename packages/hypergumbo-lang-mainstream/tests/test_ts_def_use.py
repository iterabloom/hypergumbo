# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for TypeScript def/use extractor (ADR-0017 §1c Phase 3)."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import tree_sitter
from tree_sitter_language_pack import get_language

from hypergumbo_core.cfg import DefUseResult, clear_def_use_extractors, get_def_use_extractor
from hypergumbo_lang_mainstream.ts_def_use import (
    TypeScriptDefUseExtractor,
    _collect_identifiers,
    _collect_pattern_names,
    _handle_expression_statement,
)


def _parse(source: str) -> tuple[Any, bytes]:
    lang = get_language("typescript")
    parser = tree_sitter.Parser(lang)
    src = source.encode("utf-8")
    return parser.parse(src), src


def _fn_body_stmts(source: str) -> tuple[list[Any], bytes]:
    tree, src = _parse(source)
    fn = tree.root_node.children[0]
    body = fn.child_by_field_name("body")
    return [c for c in body.children if c.is_named], src


def _first_stmt(source: str) -> tuple[Any, bytes]:
    stmts, src = _fn_body_stmts(source)
    return stmts[0], src


class TestTypeScriptDefUseExtractor:
    def test_registered(self) -> None:
        import importlib
        import hypergumbo_lang_mainstream.ts_def_use as mod
        importlib.reload(mod)
        ext = get_def_use_extractor("typescript")
        assert ext is not None
        assert ext.language == "typescript"

    def test_const_simple(self) -> None:
        stmt, src = _first_stmt("function f() { const x = 1; }")
        ext = TypeScriptDefUseExtractor()
        result = ext.extract(stmt, src)
        assert result.defines == ["x"]

    def test_const_with_expr(self) -> None:
        stmt, src = _first_stmt("function f(a: number, b: number) { const y = a + b; }")
        ext = TypeScriptDefUseExtractor()
        result = ext.extract(stmt, src)
        assert result.defines == ["y"]
        assert set(result.uses) == {"a", "b"}

    def test_array_destructuring(self) -> None:
        stmt, src = _first_stmt("function f() { const [a, b] = func(); }")
        ext = TypeScriptDefUseExtractor()
        result = ext.extract(stmt, src)
        assert set(result.defines) == {"a", "b"}

    def test_object_destructuring(self) -> None:
        stmt, src = _first_stmt("function f() { const { x, y } = obj; }")
        ext = TypeScriptDefUseExtractor()
        result = ext.extract(stmt, src)
        assert set(result.defines) == {"x", "y"}
        assert "obj" in result.uses

    def test_assignment(self) -> None:
        stmts, src = _fn_body_stmts("function f() { let x = 0; x = 1; }")
        ext = TypeScriptDefUseExtractor()
        result = ext.extract(stmts[1], src)
        assert "x" in result.defines

    def test_augmented_assignment(self) -> None:
        stmts, src = _fn_body_stmts("function f() { let x = 0; x += y; }")
        ext = TypeScriptDefUseExtractor()
        result = ext.extract(stmts[1], src)
        assert "x" in result.defines
        assert "x" in result.uses
        assert "y" in result.uses

    def test_member_write(self) -> None:
        stmt, src = _first_stmt("function f() { this.field = value; }")
        ext = TypeScriptDefUseExtractor()
        result = ext.extract(stmt, src)
        assert "this" in result.defines
        assert "value" in result.uses

    def test_subscript_write(self) -> None:
        stmt, src = _first_stmt("function f() { data[idx] = val; }")
        ext = TypeScriptDefUseExtractor()
        result = ext.extract(stmt, src)
        assert "data" in result.defines
        assert "val" in result.uses

    def test_for_of(self) -> None:
        stmt, src = _first_stmt("function f() { for (const item of items) { process(item); } }")
        ext = TypeScriptDefUseExtractor()
        result = ext.extract(stmt, src)
        assert "item" in result.defines
        assert "items" in result.uses

    def test_return_statement(self) -> None:
        stmt, src = _first_stmt("function f() { return result; }")
        ext = TypeScriptDefUseExtractor()
        result = ext.extract(stmt, src)
        assert "result" in result.uses

    def test_call_expression(self) -> None:
        stmt, src = _first_stmt("function f() { process(data); }")
        ext = TypeScriptDefUseExtractor()
        result = ext.extract(stmt, src)
        assert "data" in result.uses

    def test_method_call(self) -> None:
        stmt, src = _first_stmt("function f() { obj.method(arg); }")
        ext = TypeScriptDefUseExtractor()
        result = ext.extract(stmt, src)
        assert "obj" in result.uses
        assert "arg" in result.uses

    def test_unknown_node(self) -> None:
        ext = TypeScriptDefUseExtractor()
        node = MagicMock()
        node.type = "unknown_type"
        node.children = []
        result = ext.extract(node, b"")
        assert isinstance(result, DefUseResult)

    def test_builtin_names_excluded(self) -> None:
        stmt, src = _first_stmt("function f() { const x = JSON.parse(data); }")
        ext = TypeScriptDefUseExtractor()
        result = ext.extract(stmt, src)
        assert "JSON" not in result.uses
        assert "data" in result.uses

    def test_empty_expression_statement(self) -> None:
        node = MagicMock()
        node.type = "expression_statement"
        node.children = []
        result = _handle_expression_statement(node, b"")
        assert result.defines == []
        assert result.uses == []

    def test_object_pattern_pair(self) -> None:
        """Object destructuring with rename: { x: renamed }."""
        stmt, src = _first_stmt("function f() { const { x: renamed } = obj; }")
        ext = TypeScriptDefUseExtractor()
        result = ext.extract(stmt, src)
        assert "renamed" in result.defines

    def test_member_expression_non_this(self) -> None:
        """member_expression with non-this, non-identifier object."""
        stmt, src = _first_stmt("function f() { a().b = 1; }")
        ext = TypeScriptDefUseExtractor()
        result = ext.extract(stmt, src)
        # Complex member target — no defines (can't track)
        # But the call is a use

    def test_subscript_complex_object(self) -> None:
        """subscript_expression with non-identifier object."""
        stmt, src = _first_stmt("function f() { a().x[0] = 1; }")
        ext = TypeScriptDefUseExtractor()
        result = ext.extract(stmt, src)
        assert isinstance(result, DefUseResult)


    def test_rest_pattern(self) -> None:
        """Array destructuring with rest: [a, ...rest]."""
        stmt, src = _first_stmt("function f() { const [a, ...rest] = arr; }")
        ext = TypeScriptDefUseExtractor()
        result = ext.extract(stmt, src)
        assert "a" in result.defines
        assert "rest" in result.defines

    def test_assignment_pattern_default(self) -> None:
        """Destructuring with default: { x = 0 }."""
        stmt, src = _first_stmt("function f() { const { x = 0 } = obj; }")
        ext = TypeScriptDefUseExtractor()
        result = ext.extract(stmt, src)
        assert "x" in result.defines

    def test_complex_call_func(self) -> None:
        """Call where function is not identifier or member_expression."""
        stmt, src = _first_stmt("function f() { (getHandler())(data); }")
        ext = TypeScriptDefUseExtractor()
        result = ext.extract(stmt, src)
        assert "data" in result.uses

    def test_member_identifier_target(self) -> None:
        """obj.field = val where obj is a plain identifier."""
        stmt, src = _first_stmt("function f() { obj.field = val; }")
        ext = TypeScriptDefUseExtractor()
        result = ext.extract(stmt, src)
        assert "obj" in result.defines
        assert "val" in result.uses

    def test_for_in_with_pattern(self) -> None:
        """for...of with destructuring."""
        stmt, src = _first_stmt("function f() { for (const [k, v] of items) {} }")
        ext = TypeScriptDefUseExtractor()
        result = ext.extract(stmt, src)
        assert "items" in result.uses

    def test_variable_declaration(self) -> None:
        """var declaration handled same as lexical."""
        stmt, src = _first_stmt("function f() { var x = 1; }")
        ext = TypeScriptDefUseExtractor()
        result = ext.extract(stmt, src)
        assert "x" in result.defines


class TestCollectPatternNames:
    def test_unknown_pattern(self) -> None:
        node = MagicMock()
        node.type = "unknown_pattern"
        assert _collect_pattern_names(node, b"") == []


class TestCollectIdentifiers:
    def test_this_collected(self) -> None:
        tree, src = _parse("function f() { this.x; }")
        fn = tree.root_node.children[0]
        body = fn.child_by_field_name("body")
        ids = _collect_identifiers(body, src)
        assert "this" in ids

    def test_type_annotation_skipped(self) -> None:
        tree, src = _parse("function f() { const x: number = 1; }")
        fn = tree.root_node.children[0]
        body = fn.child_by_field_name("body")
        ids = _collect_identifiers(body, src)
        assert "number" not in ids
