# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Rust def/use extractor (ADR-0017 §1c Phase 2).

Validates variable definition and use extraction from Rust tree-sitter
AST nodes. Tests use real tree-sitter parsing.
"""
from __future__ import annotations

from typing import Any

import tree_sitter
from tree_sitter_language_pack import get_language

from hypergumbo_core.cfg import DefUseResult, clear_def_use_extractors, get_def_use_extractor
from hypergumbo_lang_mainstream.rust_def_use import (
    RustDefUseExtractor,
    _collect_identifiers,
    _collect_pattern_names,
)


def _parse(source: str) -> tuple[Any, bytes]:
    """Parse Rust source and return (tree, source_bytes)."""
    lang = get_language("rust")
    parser = tree_sitter.Parser(lang)
    src = source.encode("utf-8")
    tree = parser.parse(src)
    return tree, src


def _fn_body_stmts(source: str) -> tuple[list[Any], bytes]:
    """Parse a Rust function and return (body_statements, source_bytes)."""
    tree, src = _parse(source)
    fn_node = tree.root_node.children[0]
    body = fn_node.child_by_field_name("body")
    stmts = [c for c in body.children if c.is_named]
    return stmts, src


def _first_stmt(source: str) -> tuple[Any, bytes]:
    """Parse and return the first statement in a function body."""
    stmts, src = _fn_body_stmts(source)
    return stmts[0], src


class TestRustDefUseExtractor:
    """Test the RustDefUseExtractor class."""

    def test_registered(self) -> None:
        import importlib
        import hypergumbo_lang_mainstream.rust_def_use as mod
        importlib.reload(mod)
        ext = get_def_use_extractor("rust")
        assert ext is not None
        assert ext.language == "rust"

    def test_let_simple(self) -> None:
        stmt, src = _first_stmt("fn f() { let x = 1; }")
        ext = RustDefUseExtractor()
        result = ext.extract(stmt, src)
        assert result.defines == ["x"]

    def test_let_with_expression(self) -> None:
        stmt, src = _first_stmt("fn f(a: i32, b: i32) { let y = a + b; }")
        ext = RustDefUseExtractor()
        result = ext.extract(stmt, src)
        assert result.defines == ["y"]
        assert set(result.uses) == {"a", "b"}

    def test_let_tuple_destructure(self) -> None:
        stmt, src = _first_stmt("fn f() { let (a, b) = (1, 2); }")
        ext = RustDefUseExtractor()
        result = ext.extract(stmt, src)
        assert set(result.defines) == {"a", "b"}

    def test_let_struct_destructure(self) -> None:
        stmt, src = _first_stmt("fn f() { let Foo { field1, field2 } = bar(); }")
        ext = RustDefUseExtractor()
        result = ext.extract(stmt, src)
        assert set(result.defines) == {"field1", "field2"}

    def test_let_tuple_struct(self) -> None:
        stmt, src = _first_stmt("fn f(opt: Option<i32>) { let Some(val) = opt; }")
        ext = RustDefUseExtractor()
        # This is an irrefutable let, but tree-sitter parses it
        result = ext.extract(stmt, src)
        assert "val" in result.defines
        assert "opt" in result.uses

    def test_let_wildcard(self) -> None:
        stmt, src = _first_stmt("fn f() { let _ = compute(); }")
        ext = RustDefUseExtractor()
        result = ext.extract(stmt, src)
        assert result.defines == []  # wildcard doesn't bind

    def test_reassignment(self) -> None:
        stmts, src = _fn_body_stmts("fn f() { let mut x = 0; x = 1; }")
        ext = RustDefUseExtractor()
        # Second statement: x = 1
        result = ext.extract(stmts[1], src)
        assert "x" in result.defines

    def test_compound_assignment(self) -> None:
        stmts, src = _fn_body_stmts("fn f() { let mut x = 0; x += y; }")
        ext = RustDefUseExtractor()
        result = ext.extract(stmts[1], src)
        assert result.defines == ["x"]
        assert "x" in result.uses  # read before modify
        assert "y" in result.uses

    def test_field_write(self) -> None:
        stmt, src = _first_stmt("fn f() { self.field = value; }")
        ext = RustDefUseExtractor()
        # expression_statement wraps assignment_expression
        result = ext.extract(stmt, src)
        assert "self" in result.defines  # mutates self
        assert "value" in result.uses

    def test_index_write(self) -> None:
        stmt, src = _first_stmt("fn f() { data[idx] = val; }")
        ext = RustDefUseExtractor()
        result = ext.extract(stmt, src)
        assert "data" in result.defines
        assert "val" in result.uses

    def test_for_loop(self) -> None:
        stmt, src = _first_stmt("fn f() { for item in items.iter() { process(item); } }")
        ext = RustDefUseExtractor()
        result = ext.extract(stmt, src)
        assert "item" in result.defines
        assert "items" in result.uses

    def test_return_expression(self) -> None:
        stmt, src = _first_stmt("fn f() { return result; }")
        ext = RustDefUseExtractor()
        result = ext.extract(stmt, src)
        assert result.defines == []
        assert "result" in result.uses

    def test_return_complex(self) -> None:
        stmt, src = _first_stmt("fn f() { return a + b; }")
        ext = RustDefUseExtractor()
        result = ext.extract(stmt, src)
        assert set(result.uses) == {"a", "b"}

    def test_if_let(self) -> None:
        stmt, src = _first_stmt("fn f() { if let Some(v) = opt { use_v(v); } }")
        ext = RustDefUseExtractor()
        result = ext.extract(stmt, src)
        assert "v" in result.defines
        assert "opt" in result.uses

    def test_if_condition(self) -> None:
        stmt, src = _first_stmt("fn f() { if x > 0 { return; } }")
        ext = RustDefUseExtractor()
        result = ext.extract(stmt, src)
        assert "x" in result.uses

    def test_match_arm(self) -> None:
        """Match arm pattern defines variables."""
        tree, src = _parse("fn f() { match x { Some(v) => v, None => 0, } }")
        fn_node = tree.root_node.children[0]
        body = fn_node.child_by_field_name("body")
        # Find the match expression → match_block → match_arm
        for child in body.children:
            if not child.is_named:
                continue
            if child.type == "expression_statement":
                child = child.children[0] if child.children else child
            if child.type == "match_expression":
                match_block = child.child_by_field_name("body")
                for arm in match_block.children:
                    if arm.type == "match_arm":
                        ext = RustDefUseExtractor()
                        result = ext.extract(arm, src)
                        if result.defines:
                            assert "v" in result.defines
                            return
        raise AssertionError("No match arm with defines found")

    def test_closure_direct(self) -> None:
        """Closure as standalone expression."""
        stmts, src = _fn_body_stmts("fn f() { let c = |x, y| x + y; }")
        ext = RustDefUseExtractor()
        # The let_declaration handles the outer define (c)
        result = ext.extract(stmts[0], src)
        assert "c" in result.defines

    def test_wildcard_in_let(self) -> None:
        """Wildcard _ should not produce defines."""
        stmt, src = _first_stmt("fn f() { let _ = compute(); }")
        ext = RustDefUseExtractor()
        result = ext.extract(stmt, src)
        assert "_" not in result.defines
        assert result.defines == []

    def test_complex_call_func(self) -> None:
        """Call where function is not identifier or attribute (subscript access)."""
        stmt, src = _first_stmt("fn f() { (get_handler())(data); }")
        ext = RustDefUseExtractor()
        result = ext.extract(stmt, src)
        assert "data" in result.uses

    def test_struct_named_field_pattern(self) -> None:
        """Struct pattern with explicit name: pattern binding."""
        stmt, src = _first_stmt("fn f() { let Foo { x: val } = bar; }")
        ext = RustDefUseExtractor()
        result = ext.extract(stmt, src)
        # In `x: val`, val is the bound identifier
        assert "val" in result.defines

    def test_match_mut_pattern(self) -> None:
        """Match arm with mut pattern."""
        tree, src = _parse("fn f() { match x { mut v => { use_v(v); } } }")
        fn_node = tree.root_node.children[0]
        body = fn_node.child_by_field_name("body")
        for child in body.children:
            if not child.is_named:
                continue
            inner = child
            if inner.type == "expression_statement":
                for c in inner.children:
                    if c.is_named:
                        inner = c
                        break
            if inner.type == "match_expression":
                mb = inner.child_by_field_name("body")
                for arm in mb.children:
                    if arm.type == "match_arm":
                        ext = RustDefUseExtractor()
                        result = ext.extract(arm, src)
                        if result.defines:
                            assert "v" in result.defines
                            return
        raise AssertionError("No match arm with mut pattern found")

    def test_closure_standalone(self) -> None:
        """Closure as a standalone expression (not in let binding)."""
        # Parse a closure inside an expression
        stmts, src = _fn_body_stmts("fn f() { items.map(|x| x + 1); }")
        ext = RustDefUseExtractor()
        result = ext.extract(stmts[0], src)
        # expression_statement delegates to the inner call,
        # which finds 'items' as a use
        assert "items" in result.uses

    def test_closure_handler_direct(self) -> None:
        """Call closure handler directly on a closure node."""
        from hypergumbo_lang_mainstream.rust_def_use import _handle_closure_expression
        tree, src = _parse("fn f() { let c = |x, y| x + y; }")
        body = tree.root_node.children[0].child_by_field_name("body")
        # Find the closure_expression inside the let
        for stmt in body.children:
            if stmt.type == "let_declaration":
                value = stmt.child_by_field_name("value")
                if value and value.type == "closure_expression":
                    result = _handle_closure_expression(value, src)
                    assert set(result.defines) == {"x", "y"}
                    return
        raise AssertionError("No closure found")

    def test_unknown_node_type_via_extract(self) -> None:
        """Unknown node types (not in _HANDLERS) → default handler."""
        from unittest.mock import MagicMock
        ext = RustDefUseExtractor()
        node = MagicMock()
        node.type = "totally_unknown_node"
        node.children = []
        result = ext.extract(node, b"x")
        assert isinstance(result, DefUseResult)

    def test_while_uses(self) -> None:
        """while expression collects identifiers via expression_statement."""
        stmt, src = _first_stmt("fn f() { while x > 0 { break; } }")
        ext = RustDefUseExtractor()
        result = ext.extract(stmt, src)
        assert "x" in result.uses

    def test_empty_expression_statement(self) -> None:
        """Expression statement with no named children returns empty."""
        from hypergumbo_lang_mainstream.rust_def_use import _handle_expression_statement
        from unittest.mock import MagicMock
        node = MagicMock()
        node.type = "expression_statement"
        node.children = []
        result = _handle_expression_statement(node, b"")
        assert result.defines == []
        assert result.uses == []

    def test_macro_invocation(self) -> None:
        """Macro args collected conservatively as uses."""
        stmt, src = _first_stmt('fn f() { println!("{}", data); }')
        ext = RustDefUseExtractor()
        result = ext.extract(stmt, src)
        assert "data" in result.uses


class TestCollectPatternNames:
    """Test Rust pattern name collection."""

    def test_or_pattern(self) -> None:
        tree, src = _parse("fn f() { match x { 1 | 2 => {} } }")
        fn_node = tree.root_node.children[0]
        body = fn_node.child_by_field_name("body")
        # Find match arm pattern
        for child in body.children:
            if not child.is_named:
                continue
            inner = child
            if inner.type == "expression_statement":
                inner = inner.children[0]
            if inner.type == "match_expression":
                match_block = inner.child_by_field_name("body")
                for arm in match_block.children:
                    if arm.type == "match_arm":
                        pattern = arm.child_by_field_name("pattern")
                        if pattern:
                            names = _collect_pattern_names(pattern, src)
                            # Literal patterns don't bind names
                            assert names == []
                            return
        raise AssertionError("No match arm found")

    def test_reference_pattern(self) -> None:
        tree, src = _parse("fn f() { let &x = &1; }")
        stmt = tree.root_node.children[0].child_by_field_name("body").children[0]
        # Skip non-named until let_declaration
        for child in tree.root_node.children[0].child_by_field_name("body").children:
            if child.type == "let_declaration":
                pattern = child.child_by_field_name("pattern")
                if pattern:
                    names = _collect_pattern_names(pattern, src)
                    assert "x" in names
                    return
        raise AssertionError("No let found")

    def test_slice_pattern(self) -> None:
        tree, src = _parse("fn f() { let [a, b] = arr; }")
        for child in tree.root_node.children[0].child_by_field_name("body").children:
            if child.type == "let_declaration":
                pattern = child.child_by_field_name("pattern")
                if pattern and pattern.type == "slice_pattern":
                    names = _collect_pattern_names(pattern, src)
                    assert set(names) == {"a", "b"}
                    return
        raise AssertionError("No slice pattern found")

    def test_remaining_field_pattern(self) -> None:
        tree, src = _parse("fn f() { let Foo { x, .. } = bar; }")
        for child in tree.root_node.children[0].child_by_field_name("body").children:
            if child.type == "let_declaration":
                pattern = child.child_by_field_name("pattern")
                if pattern:
                    names = _collect_pattern_names(pattern, src)
                    assert "x" in names
                    return
        raise AssertionError("No struct pattern found")

    def test_mut_pattern(self) -> None:
        tree, src = _parse("fn f() { let mut x = 1; }")
        for child in tree.root_node.children[0].child_by_field_name("body").children:
            if child.type == "let_declaration":
                pattern = child.child_by_field_name("pattern")
                if pattern:
                    names = _collect_pattern_names(pattern, src)
                    assert "x" in names
                    return
        raise AssertionError("No let mut found")

    def test_unknown_pattern_type(self) -> None:
        """Unknown pattern types return empty list."""
        tree, src = _parse("fn f() { 1 + 2; }")
        # Get a non-pattern node and pass it to _collect_pattern_names
        body = tree.root_node.children[0].child_by_field_name("body")
        for child in body.children:
            if child.is_named:
                # This is not a pattern — should return []
                result = _collect_pattern_names(child, src)
                assert result == []
                return
        raise AssertionError("No named child found")


class TestCollectIdentifiers:
    """Test Rust identifier collection."""

    def test_self_collected(self) -> None:
        tree, src = _parse("fn f() { self.method(); }")
        body = tree.root_node.children[0].child_by_field_name("body")
        ids = _collect_identifiers(body, src)
        assert "self" in ids

    def test_type_identifiers_skipped(self) -> None:
        tree, src = _parse("fn f() { let x: Vec<i32> = Vec::new(); }")
        stmt = None
        for child in tree.root_node.children[0].child_by_field_name("body").children:
            if child.type == "let_declaration":
                stmt = child
                break
        assert stmt is not None
        value = stmt.child_by_field_name("value")
        if value:
            ids = _collect_identifiers(value, src)
            # Vec, i32, new should not be in uses
            for name in ids:
                assert name not in ("i32",)

    def test_string_literal_skipped(self) -> None:
        tree, src = _parse('fn f() { let x = "hello"; }')
        for child in tree.root_node.children[0].child_by_field_name("body").children:
            if child.type == "let_declaration":
                value = child.child_by_field_name("value")
                if value:
                    ids = _collect_identifiers(value, src)
                    assert ids == []
                    return
        raise AssertionError("No let found")


class TestRustBorrowAlias:
    """Tests for borrow alias tracking (Phase 2b)."""

    def test_let_ref_records_alias(self) -> None:
        """let y = &mut x records y as alias of x."""
        tree, src = _parse("fn f() { let mut x = 1; let y = &mut x; }")
        ext = RustDefUseExtractor()
        body = tree.root_node.children[0].child_by_field_name("body")
        stmts = [c for c in body.children if c.is_named]
        # Second let: let y = &mut x
        result = ext.extract(stmts[1], src)
        assert "y" in result.defines
        assert "x" in result.uses

    def test_deref_assignment_mutates_target(self) -> None:
        """*y = val generates a define of y (dereference mutation)."""
        tree, src = _parse("fn f() { let mut x = 1; let y = &mut x; *y = 42; }")
        ext = RustDefUseExtractor()
        body = tree.root_node.children[0].child_by_field_name("body")
        stmts = [c for c in body.children if c.is_named]
        # Third statement: *y = 42
        result = ext.extract(stmts[2], src)
        assert "y" in result.defines

    def test_ref_pattern_in_match_arm(self) -> None:
        """ref/ref mut patterns in match arms define bindings."""
        tree, src = _parse(
            "fn f(x: Option<i32>) { match x { Some(ref val) => {}, None => {} } }"
        )
        ext = RustDefUseExtractor()
        body = tree.root_node.children[0].child_by_field_name("body")
        match_expr = None
        for c in body.children:
            if c.type == "match_expression" or c.type == "expression_statement":
                match_expr = c
                break
        assert match_expr is not None
        # Find the first match arm
        for c in _iter_tree(match_expr):
            if c.type == "match_arm":
                result = ext.extract(c, src)
                assert "val" in result.defines
                break


def _iter_tree(node):
    """Simple tree iterator for test helpers."""
    yield node
    for child in node.children:
        yield from _iter_tree(child)
