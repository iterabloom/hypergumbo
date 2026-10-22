# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Go def/use extractor (ADR-0017 §1c).

The end-to-end test in :class:`TestGoDdgEndToEnd` is the load-bearing one.
A def/use extractor can be perfectly correct against hand-picked AST nodes
and still contribute nothing, because the CFG builder decides which nodes
ever reach ``extract()``. Before ``atomic_statement`` was added to go.yaml
the Go CFG emitted bare ``identifier`` leaves, so an extractor keyed on
``short_var_declaration`` was never called with one and produced exactly
zero DDG edges — while every unit test below would have passed.
"""
from __future__ import annotations

from typing import Any

import pytest
import tree_sitter
from tree_sitter_language_pack import get_language

from hypergumbo_core.cfg import (
    build_function_cfg,
    get_def_use_extractor,
    load_cfg_mapping,
    populate_def_use_for_cfg,
    solve_reaching_defs,
)
from hypergumbo_lang_mainstream.go_def_use import (
    GoDefUseExtractor,
    _collect_identifiers,
    _collect_pattern_names,
)


def _parse(source: str) -> tuple[Any, bytes]:
    lang = get_language("go")
    parser = tree_sitter.Parser(lang)
    src = source.encode("utf-8")
    return parser.parse(src), src


def _fn_body(source: str) -> tuple[Any, bytes]:
    tree, src = _parse(source)
    fn = next(c for c in tree.root_node.children if c.type == "function_declaration")
    return fn.child_by_field_name("body"), src


def _stmts(source: str) -> tuple[list[Any], bytes]:
    body, src = _fn_body(source)
    out: list[Any] = []
    for child in body.children:
        if child.type == "statement_list":
            out.extend(c for c in child.children if c.is_named)
        elif child.is_named:
            out.append(child)
    return out, src


def _first(source: str) -> tuple[Any, bytes]:
    stmts, src = _stmts(source)
    return stmts[0], src


def _extract(source: str) -> Any:
    stmt, src = _first(source)
    return GoDefUseExtractor().extract(stmt, src)


def _wrap(body: str) -> str:
    return "package m\nfunc f() {\n\t" + body + "\n}\n"


class TestRegistration:
    def test_registered_under_go(self) -> None:
        import importlib

        import hypergumbo_lang_mainstream.go_def_use as mod

        importlib.reload(mod)
        ext = get_def_use_extractor("go")
        assert ext is not None
        assert ext.language == "go"


class TestShortVarDeclaration:
    def test_simple(self) -> None:
        r = _extract(_wrap("secret := req"))
        assert r.defines == ["secret"]
        assert r.uses == ["req"]

    def test_multiple_targets(self) -> None:
        r = _extract(_wrap("a, b := split(s)"))
        assert r.defines == ["a", "b"]
        assert r.uses == ["s"]

    def test_selector_on_right_uses_operand_not_field(self) -> None:
        r = _extract(_wrap("secret := req.Password"))
        assert r.defines == ["secret"]
        assert r.uses == ["req"]

    def test_blank_identifier_is_not_a_definition(self) -> None:
        r = _extract(_wrap("_, err := do(x)"))
        assert r.defines == ["err"]
        assert "x" in r.uses

    def test_channel_receive(self) -> None:
        r = _extract(_wrap("got := <-ch"))
        assert r.defines == ["got"]
        assert r.uses == ["ch"]


class TestVarAndConstDeclaration:
    def test_var_with_type_and_value(self) -> None:
        r = _extract(_wrap("var label string = greet(x)"))
        assert r.defines == ["label"]
        assert r.uses == ["x"]

    def test_var_without_value(self) -> None:
        r = _extract(_wrap("var buf Buffer"))
        assert r.defines == ["buf"]
        assert r.uses == []

    def test_var_multiple_names(self) -> None:
        r = _extract(_wrap("var a, b = f(c)"))
        assert r.defines == ["a", "b"]
        assert r.uses == ["c"]

    def test_const(self) -> None:
        r = _extract(_wrap("const limit = base"))
        assert r.defines == ["limit"]
        assert r.uses == ["base"]


class TestAssignmentStatement:
    def test_simple(self) -> None:
        r = _extract(_wrap("x = secret"))
        assert r.defines == ["x"]
        assert r.uses == ["secret"]

    def test_selector_target_mutates_receiver(self) -> None:
        r = _extract(_wrap("obj.field = secret"))
        assert r.defines == ["obj"]
        assert r.uses == ["secret"]

    def test_index_target_uses_and_defines_container(self) -> None:
        r = _extract(_wrap("arr[i] = v"))
        assert r.defines == ["arr"]
        assert set(r.uses) == {"arr", "i", "v"}

    def test_augmented_reads_its_target(self) -> None:
        r = _extract(_wrap("total += v"))
        assert r.defines == ["total"]
        assert set(r.uses) == {"total", "v"}

    def test_multiple_assignment(self) -> None:
        r = _extract(_wrap("a, b = b, a"))
        assert r.defines == ["a", "b"]
        assert set(r.uses) == {"a", "b"}


class TestOtherStatements:
    def test_expression_statement_call(self) -> None:
        r = _extract(_wrap("log.Println(secret)"))
        assert r.defines == []
        assert set(r.uses) == {"log", "secret"}

    def test_bare_call_uses_arguments_not_callee(self) -> None:
        r = _extract(_wrap("send(secret)"))
        assert r.defines == []
        assert r.uses == ["secret"]

    def test_inc_statement_reads_and_writes(self) -> None:
        r = _extract(_wrap("i++"))
        assert r.defines == ["i"]
        assert r.uses == ["i"]

    def test_dec_statement_reads_and_writes(self) -> None:
        r = _extract(_wrap("i--"))
        assert r.defines == ["i"]
        assert r.uses == ["i"]

    def test_send_statement_defines_nothing(self) -> None:
        r = _extract(_wrap("ch <- secret"))
        assert r.defines == []
        assert set(r.uses) == {"ch", "secret"}

    def test_go_statement(self) -> None:
        r = _extract(_wrap("go handle(secret)"))
        assert r.defines == []
        assert r.uses == ["secret"]

    def test_unhandled_node_falls_back_to_uses(self) -> None:
        # return_statement is owned by classify(), never atomic — the
        # fallback path must still report its identifiers as uses.
        stmt, src = _first("package m\nfunc f() {\n\treturn wrapped(x)\n}\n")
        r = GoDefUseExtractor().extract(stmt, src)
        assert r.defines == []
        assert r.uses == ["x"]


class TestLiteralsAndBuiltins:
    def test_literals_are_not_uses(self) -> None:
        r = _extract(_wrap('s := "hello"'))
        assert r.uses == []

    def test_nil_and_booleans_are_not_uses(self) -> None:
        r = _extract(_wrap("ok := nil"))
        assert r.uses == []

    def test_builtin_callee_is_not_a_use(self) -> None:
        r = _extract(_wrap("n := len(items)"))
        assert r.uses == ["items"]

    def test_type_identifier_is_not_a_use(self) -> None:
        r = _extract(_wrap("var x MyType"))
        assert r.uses == []


class TestHelpers:
    def test_collect_pattern_names_on_plain_identifier(self) -> None:
        stmt, src = _first(_wrap("x = 1"))
        left = stmt.child_by_field_name("left")
        assert _collect_pattern_names(left.children[0], src) == ["x"]

    def test_collect_pattern_names_unknown_shape_is_empty(self) -> None:
        stmt, src = _first(_wrap('m["k"] = 1'))
        left = stmt.child_by_field_name("left")
        index = left.children[0]
        # the index child itself is a literal, not an assignable target
        assert _collect_pattern_names(index.child_by_field_name("index"), src) == []

    def test_collect_identifiers_walks_nested_expressions(self) -> None:
        stmt, src = _first(_wrap("y = a + b*c"))
        right = stmt.child_by_field_name("right")
        assert set(_collect_identifiers(right, src)) == {"a", "b", "c"}


@pytest.fixture
def _go_extractor_registered() -> Any:
    """Guarantee the Go extractor is in the process-global registry.

    ``test_cfg.py`` calls ``clear_def_use_extractors()`` five times, which
    empties a registry this module's pipeline tests depend on. Under xdist
    the two can land in the same worker in either order, so without this the
    end-to-end assertions below measure a pipeline with *no extractor* and
    report zero edges — failing for a reason that has nothing to do with the
    behaviour under test, and (worse) capable of passing for the wrong
    reason if the assertion were ever inverted.
    """
    import importlib

    import hypergumbo_lang_mainstream.go_def_use as mod

    if get_def_use_extractor("go") is None:
        importlib.reload(mod)
    assert get_def_use_extractor("go") is not None
    return mod


@pytest.mark.usefixtures("_go_extractor_registered")
class TestGoDdgEndToEnd:
    """The extractor must produce DDG edges through the real pipeline.

    This is the test that would have caught the inert-extractor failure.
    It exercises go.yaml's ``atomic_statement`` and the extractor together;
    either one missing yields zero edges.
    """

    def _ddg(self, source: str) -> Any:
        body, src = _fn_body(source)
        mapping = load_cfg_mapping("go")
        assert mapping is not None
        cfg = build_function_cfg(body, src, mapping, "go:t.go:1-9:f:function")
        populate_def_use_for_cfg(cfg, body, src, "go")
        return cfg, solve_reaching_defs(cfg)

    def test_def_then_use_yields_a_ddg_edge(self) -> None:
        cfg, result = self._ddg(
            "package main\n"
            "func handler(req *Request) {\n"
            "\tsecret := req.Password\n"
            "\tsend(secret)\n"
            "}\n"
        )
        assert not result.bailed_out
        assert len(result.ddg_edges) >= 1

    def test_statements_carry_populated_def_use(self) -> None:
        cfg, _ = self._ddg(
            "package main\n"
            "func handler(req *Request) {\n"
            "\tsecret := req.Password\n"
            "\tsend(secret)\n"
            "}\n"
        )
        populated = [
            s
            for b in cfg.blocks.values()
            for s in b.statements
            if s.defines or s.uses
        ]
        assert len(populated) >= 2
        assert any("secret" in s.defines for s in populated)

    def test_unrelated_variables_produce_no_edge(self) -> None:
        """Non-vacuity in the other direction: the solver is not
        indiscriminately linking every statement pair."""
        _, result = self._ddg(
            "package main\n"
            "func handler() {\n"
            "\ta := one()\n"
            "\tb := two()\n"
            "\tsink(c)\n"
            "}\n"
        )
        assert result.ddg_edges == []


class TestGoDdgSpecNaming:
    """The spec's namer must reproduce the Go analyzer's symbol names.

    If it does not, ddg_symbols will not intersect the structural BFS's node
    keys and the DDG is silently unusable for Go — inert in a way no unit
    test of the extractor itself could see.
    """

    def _decl(self, source: str, node_type: str) -> tuple[Any, bytes]:
        tree, src = _parse(source)
        node = next(c for c in tree.root_node.children if c.type == node_type)
        return node, src

    def test_plain_function_is_named_by_its_identifier(self) -> None:
        from hypergumbo_lang_mainstream.go_def_use import _go_function_name

        node, src = self._decl(
            "package m\nfunc Handle(w int) {}\n", "function_declaration",
        )
        assert _go_function_name(node, src) == "Handle"

    def test_method_carries_its_receiver_type(self) -> None:
        from hypergumbo_lang_mainstream.go_def_use import _go_function_name

        node, src = self._decl(
            "package m\nfunc (s *Server) Listen() {}\n", "method_declaration",
        )
        assert _go_function_name(node, src) == "Server.Listen"

    def test_value_receiver_also_carries_the_type(self) -> None:
        from hypergumbo_lang_mainstream.go_def_use import _go_function_name

        node, src = self._decl(
            "package m\nfunc (s Server) Close() {}\n", "method_declaration",
        )
        assert _go_function_name(node, src) == "Server.Close"

    def test_kind_slot_distinguishes_method_from_function(self) -> None:
        from hypergumbo_lang_mainstream.go_def_use import _go_symbol_kind

        fn, _ = self._decl("package m\nfunc F() {}\n", "function_declaration")
        me, _ = self._decl("package m\nfunc (s S) M() {}\n", "method_declaration")
        assert _go_symbol_kind(fn) == "function"
        assert _go_symbol_kind(me) == "method"

    def test_ids_match_what_the_go_analyzer_emits(self) -> None:
        """The property that actually matters, asserted against the analyzer's
        own id builder rather than against a hand-written string."""
        from hypergumbo_core.analyze.base import make_symbol_id
        from hypergumbo_lang_mainstream.go_def_use import (
            _go_function_name,
            _go_symbol_kind,
        )

        node, src = self._decl(
            "package m\nfunc (s *Server) Listen() {}\n", "method_declaration",
        )
        got = make_symbol_id(
            "go", "srv.go", node.start_point[0] + 1, node.end_point[0] + 1,
            _go_function_name(node, src), _go_symbol_kind(node),
        )
        assert got == "go:srv.go:2-2:Server.Listen:method"


class TestCollectHelperEdgeCases:
    def test_computed_callee_contributes_its_identifiers(self) -> None:
        """A callee that is itself a call is walked, not skipped.

        `f(a)(x)` — the outer call's `function` child is a call_expression,
        so it falls through to the generic recursion and `a` is reported.
        (`fns[i](x)` looks like the obvious fixture and is not: tree-sitter-go
        parses it as a type_conversion_expression, not a call.)
        """
        r = _extract(_wrap("f(a)(x)"))
        assert set(r.uses) == {"a", "x"}

    def test_selector_target_with_non_identifier_operand_defines_nothing(self) -> None:
        stmt, src = _first(_wrap("a[0].field = v"))
        left = stmt.child_by_field_name("left")
        assert _collect_pattern_names(left.children[0], src) == []

    def test_index_target_with_non_identifier_operand_defines_nothing(self) -> None:
        stmt, src = _first(_wrap("a.b[0] = v"))
        left = stmt.child_by_field_name("left")
        assert _collect_pattern_names(left.children[0], src) == []
