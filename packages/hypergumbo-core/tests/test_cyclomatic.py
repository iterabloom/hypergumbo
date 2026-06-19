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
