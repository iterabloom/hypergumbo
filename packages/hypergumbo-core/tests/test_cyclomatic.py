# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the grammar-agnostic cyclomatic-complexity walker.

``compute_cyclomatic_complexity`` touches only ``node.type`` /
``node.children`` / ``node.is_named`` / ``node.text``, so it is exercised
here with lightweight duck-typed nodes — this isolates the *algorithm*
(decision-point counting) from any specific tree-sitter grammar. The
correctness of the per-language node-type names in ``BRANCH_NODE_TYPES`` is
verified separately, against real grammars, in each analyzer package's test
suite (mainstream ``test_symbol_introspection.py``, extended1
``test_solidity.py``, common ``test_wgsl.py``).
"""

from __future__ import annotations

from hypergumbo_core.analyze.cyclomatic import (
    _HOMOICONIC_SPECS,
    BRANCH_NODE_TYPES,
    SHORT_CIRCUIT_OPS,
    compute_cyclomatic_complexity,
)


class _FakeNode:
    """Duck-typed stand-in for a tree-sitter node.

    Exposes exactly the attributes the walker reads: ``type``, ``children``,
    ``is_named`` and ``text``.
    """

    def __init__(
        self,
        type: str,
        children: "list[_FakeNode] | None" = None,
        *,
        is_named: bool = True,
        text: bytes = b"",
    ) -> None:
        self.type = type
        self.children = children or []
        self.is_named = is_named
        self.text = text


def _op(token: str) -> _FakeNode:
    """An anonymous (unnamed) operator child carrying its literal text."""
    return _FakeNode(token, is_named=False, text=token.encode("utf-8"))


class TestUnknownLanguage:
    def test_unknown_language_returns_none(self) -> None:
        assert compute_cyclomatic_complexity(_FakeNode("whatever"), "klingon") is None


class TestBaseComplexity:
    def test_no_branches_returns_one(self) -> None:
        # A bare function body with no decision points -> base complexity 1.
        tree = _FakeNode("function_declaration", [
            _FakeNode("block", [_FakeNode("return_statement")]),
        ])
        assert compute_cyclomatic_complexity(tree, "go") == 1


class TestBranchCounting:
    def test_each_branch_node_adds_one(self) -> None:
        # Two nested if_statements -> 1 (base) + 2 = 3.
        tree = _FakeNode("function_declaration", [
            _FakeNode("if_statement", [
                _FakeNode("if_statement"),
            ]),
        ])
        assert compute_cyclomatic_complexity(tree, "go") == 3

    def test_non_branch_non_binary_node_does_not_count(self) -> None:
        # Nodes that are neither branch nodes nor binary expressions are
        # walked but never increment (covers the else-fall-through path).
        tree = _FakeNode("function_declaration", [
            _FakeNode("return_statement"),
            _FakeNode("expression_statement"),
        ])
        assert compute_cyclomatic_complexity(tree, "go") == 1


class TestShortCircuitCounting:
    def test_short_circuit_op_adds_one(self) -> None:
        # binary_expression with an && operator child -> +1.
        tree = _FakeNode("function_declaration", [
            _FakeNode("binary_expression", [
                _FakeNode("identifier"),
                _op("&&"),
                _FakeNode("identifier"),
            ]),
        ])
        assert compute_cyclomatic_complexity(tree, "go") == 2

    def test_non_op_unnamed_child_does_not_count(self) -> None:
        # An unnamed child whose text is not a short-circuit operator (e.g.
        # the relational ``>``) is inspected but does not increment.
        tree = _FakeNode("function_declaration", [
            _FakeNode("binary_expression", [
                _FakeNode("identifier"),
                _op(">"),
                _FakeNode("identifier"),
            ]),
        ])
        assert compute_cyclomatic_complexity(tree, "go") == 1

    def test_named_children_in_binary_expr_are_skipped(self) -> None:
        # Named children of a binary_expression are skipped (the ``continue``
        # branch); only the unnamed operator child can bump complexity.
        tree = _FakeNode("function_declaration", [
            _FakeNode("binary_expression", [
                _FakeNode("identifier"),
                _FakeNode("identifier"),
            ]),
        ])
        assert compute_cyclomatic_complexity(tree, "go") == 1

    def test_language_without_short_circuit_ops_skips_binary_scan(self) -> None:
        # ``bash`` is in BRANCH_NODE_TYPES but absent from SHORT_CIRCUIT_OPS,
        # so ``short_circuit_ops`` is empty and a binary_expression with an
        # && child is NOT counted (covers the ``and short_circuit_ops``
        # falsy short-circuit in the elif).
        assert "bash" in BRANCH_NODE_TYPES
        assert "bash" not in SHORT_CIRCUIT_OPS
        tree = _FakeNode("function", [
            _FakeNode("binary_expression", [_op("&&")]),
        ])
        assert compute_cyclomatic_complexity(tree, "bash") == 1


class TestRelocatedLanguageTables:
    """Lock the slice-B additions present in the relocated core tables."""

    def test_solidity_and_wgsl_have_branch_tables(self) -> None:
        assert "solidity" in BRANCH_NODE_TYPES
        assert "wgsl" in BRANCH_NODE_TYPES

    def test_solidity_and_wgsl_have_short_circuit_ops(self) -> None:
        assert SHORT_CIRCUIT_OPS["solidity"] == frozenset({"&&", "||"})
        assert SHORT_CIRCUIT_OPS["wgsl"] == frozenset({"&&", "||"})

    def test_solidity_branch_set_counts_if_for(self) -> None:
        # Mirror the real solidity classify() shape: 3 if + 1 for -> CC 5,
        # built from fake nodes (real-grammar check lives in extended1).
        tree = _FakeNode("function_definition", [
            _FakeNode("if_statement", [_FakeNode("if_statement")]),
            _FakeNode("for_statement", [_FakeNode("if_statement")]),
        ])
        assert compute_cyclomatic_complexity(tree, "solidity") == 5

    def test_wgsl_branch_set_counts_if_for_and_switch_arm(self) -> None:
        tree = _FakeNode("function_declaration", [
            _FakeNode("if_statement"),
            _FakeNode("for_statement"),
            _FakeNode("switch_statement", [
                _FakeNode("case_compound_statement"),
                _FakeNode("case_compound_statement"),
            ]),
            _FakeNode("loop_statement"),
        ])
        # base 1 + if 1 + for 1 + 2 arms + loop 1 = 6
        assert compute_cyclomatic_complexity(tree, "wgsl") == 6

    def test_cmake_counts_command_wrappers_not_closers_or_else(self) -> None:
        # CMake function()/macro() body: each if_command/elseif_command/
        # foreach_command/while_command is a decision point; else_command and
        # the end*_command closers add nothing.
        tree = _FakeNode("function_def", [
            _FakeNode("if_command"),
            _FakeNode("elseif_command"),
            _FakeNode("else_command"),
            _FakeNode("endif_command"),
            _FakeNode("foreach_command"),
            _FakeNode("endforeach_command"),
            _FakeNode("while_command"),
            _FakeNode("endwhile_command"),
        ])
        # base 1 + if + elseif + foreach + while = 5
        assert compute_cyclomatic_complexity(tree, "cmake") == 5


# ---------------------------------------------------------------------------
# Homoiconic head-symbol walker (clojure / commonlisp / scheme / racket /
# elixir / fennel / janet). The duck-typed nodes below mimic each grammar's
# *shape* (the form-wrapping node type, the head-symbol child, the anonymous
# delimiter tokens); the real per-callable CC values are verified against the
# actual grammars in each owning analyzer package's test suite.
# ---------------------------------------------------------------------------


def _lparen() -> _FakeNode:
    """An anonymous opening-paren delimiter token (skipped as not is_named)."""
    return _FakeNode("(", is_named=False, text=b"(")


def _rparen() -> _FakeNode:
    return _FakeNode(")", is_named=False, text=b")")


def _list_form(form_type: str, head: _FakeNode, *args: _FakeNode) -> _FakeNode:
    """A delimited form node: ( <head> <args...> ) with anonymous parens."""
    return _FakeNode(form_type, [_lparen(), head, *args, _rparen()])


class TestHomoiconicSpecTable:
    def test_all_seven_homoiconic_languages_present(self) -> None:
        assert set(_HOMOICONIC_SPECS) == {
            "clojure", "commonlisp", "scheme", "racket", "elixir",
            "fennel", "janet",
        }

    def test_homoiconic_languages_absent_from_node_type_tables(self) -> None:
        # They are dispatched to the head-symbol walker, so they must NOT also
        # appear in the node.type-keyed tables (the homoiconic branch returns
        # first, but keeping the tables disjoint avoids confusion).
        for lang in _HOMOICONIC_SPECS:
            assert lang not in BRANCH_NODE_TYPES
            assert lang not in SHORT_CIRCUIT_OPS


class TestHomoiconicBaseAndCounting:
    def test_no_control_forms_returns_base_one(self) -> None:
        # A scheme define whose body is a single non-control call.
        tree = _list_form(
            "list", _FakeNode("symbol", text=b"define"),
            _list_form("list", _FakeNode("symbol", text=b"+")),
        )
        assert compute_cyclomatic_complexity(tree, "scheme") == 1

    def test_control_head_adds_one(self) -> None:
        tree = _list_form(
            "list", _FakeNode("symbol", text=b"define"),
            _list_form("list", _FakeNode("symbol", text=b"cond")),
        )
        assert compute_cyclomatic_complexity(tree, "scheme") == 2

    def test_nested_control_forms_accumulate(self) -> None:
        inner = _list_form("list", _FakeNode("symbol", text=b"when"))
        outer = _list_form("list", _FakeNode("symbol", text=b"if"), inner)
        tree = _list_form(
            "list", _FakeNode("symbol", text=b"define"), outer,
        )
        # base 1 + if 1 + when 1 = 3
        assert compute_cyclomatic_complexity(tree, "scheme") == 3

    def test_variadic_form_counts_once(self) -> None:
        # (and a b c) is a single form -> +1 regardless of operand count; the
        # operand symbols are not themselves forms, so they add nothing.
        tree = _list_form(
            "list", _FakeNode("symbol", text=b"and"),
            _FakeNode("symbol", text=b"a"),
            _FakeNode("symbol", text=b"b"),
            _FakeNode("symbol", text=b"c"),
        )
        assert compute_cyclomatic_complexity(tree, "scheme") == 2

    def test_non_control_head_not_counted(self) -> None:
        # A plain call (head not in the control set) is walked but not counted.
        tree = _list_form("list", _FakeNode("symbol", text=b"display"))
        assert compute_cyclomatic_complexity(tree, "scheme") == 1

    def test_form_with_no_named_head_not_counted(self) -> None:
        # A form node with only anonymous children -> no head -> not counted.
        tree = _FakeNode("list", [_lparen(), _rparen()])
        assert compute_cyclomatic_complexity(tree, "scheme") == 1

    def test_form_head_of_wrong_node_type_not_counted(self) -> None:
        # First named child is a nested list (not a ``symbol``) -> no head.
        tree = _list_form(
            "list", _list_form("list", _FakeNode("symbol", text=b"f")),
        )
        assert compute_cyclomatic_complexity(tree, "scheme") == 1


class TestHomoiconicElixir:
    def test_identifier_head_counted(self) -> None:
        tree = _FakeNode("call", [_FakeNode("identifier", text=b"if")])
        assert compute_cyclomatic_complexity(tree, "elixir") == 2

    def test_module_qualified_dot_head_not_counted(self) -> None:
        # ``Enum.map`` -> first named child is a ``dot`` node (not identifier),
        # so it has no head and is correctly skipped.
        tree = _FakeNode("call", [_FakeNode("dot"), _FakeNode("arguments")])
        assert compute_cyclomatic_complexity(tree, "elixir") == 1


class TestHomoiconicClojureSubchild:
    def test_head_via_sym_name_subchild(self) -> None:
        # clojure ``sym_lit`` wraps the bare name in a ``sym_name`` child.
        head = _FakeNode("sym_lit", [_FakeNode("sym_name", text=b"when")])
        tree = _list_form("list_lit", head)
        assert compute_cyclomatic_complexity(tree, "clojure") == 2

    def test_head_falls_back_to_sym_lit_text_when_no_subchild(self) -> None:
        # If a ``sym_lit`` has no ``sym_name`` child, fall back to its own text.
        head = _FakeNode("sym_lit", text=b"cond")
        tree = _list_form("list_lit", head)
        assert compute_cyclomatic_complexity(tree, "clojure") == 2


class TestHomoiconicCommonLisp:
    def test_loop_macro_alias_counted(self) -> None:
        # ``(loop ...)`` parses to ``list_lit -> loop_macro``; the dedicated
        # head node aliases to the head string ``"loop"``.
        tree = _list_form("list_lit", _FakeNode("loop_macro"))
        assert compute_cyclomatic_complexity(tree, "commonlisp") == 2

    def test_head_lowercased_for_case_insensitive_match(self) -> None:
        # CL is case-insensitive: an uppercase ``IF`` head matches ``if``.
        tree = _list_form("list_lit", _FakeNode("sym_lit", text=b"IF"))
        assert compute_cyclomatic_complexity(tree, "commonlisp") == 2


class TestHomoiconicDedicatedControlTypes:
    def test_fennel_dedicated_nodes_counted(self) -> None:
        # fennel ``for``/``each``/``match`` are dedicated node types.
        tree = _FakeNode("fn", [
            _FakeNode("for"), _FakeNode("each"), _FakeNode("match"),
        ])
        # base 1 + for + each + match = 4
        assert compute_cyclomatic_complexity(tree, "fennel") == 4

    def test_fennel_anonymous_keyword_token_not_double_counted(self) -> None:
        # The grammar emits BOTH a named ``for`` construct node AND an anonymous
        # ``for`` keyword token of the same .type; the is_named guard counts the
        # named one only.
        tree = _FakeNode("fn", [
            _FakeNode("for", [_FakeNode("for", is_named=False, text=b"for")]),
        ])
        assert compute_cyclomatic_complexity(tree, "fennel") == 2

    def test_fennel_list_form_head_also_counted(self) -> None:
        tree = _FakeNode("fn", [
            _list_form("list", _FakeNode("symbol", text=b"when")),
        ])
        assert compute_cyclomatic_complexity(tree, "fennel") == 2

    def test_janet_dedicated_if_while_counted(self) -> None:
        tree = _FakeNode("extra_defs", [
            _FakeNode("if"), _FakeNode("while"),
        ])
        # base 1 + if + while = 3
        assert compute_cyclomatic_complexity(tree, "janet") == 3

    def test_janet_tuple_head_counted(self) -> None:
        tree = _FakeNode("extra_defs", [
            _list_form("tuple", _FakeNode("symbol", text=b"cond")),
        ])
        assert compute_cyclomatic_complexity(tree, "janet") == 2
