# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-mozas / INV-midag: an enclosing symbol is found by its declaration's
POSITION, never its name. The shared index every analyzer's enclosing lookup
reads."""
from __future__ import annotations

from dataclasses import dataclass

from hypergumbo_core.analyze.base import symbol_declared_by, symbols_at
from hypergumbo_core.ir import Span, Symbol


def _sym(name: str, path: str, line: int, col: int, span: bool = True) -> Symbol:
    return Symbol(
        id=f"kotlin:{path}:{line}-{line + 2}:{name}:method", name=name, kind="method",
        language="kotlin", path=path, origin="test",
        span=Span(start_line=line, end_line=line + 2, start_col=col, end_col=1) if span else None,
    )


@dataclass
class _Node:
    start_point: tuple[int, int]


def test_two_same_named_declarations_are_two_entries() -> None:
    a, b = _sym("parse", "A.kt", 2, 4), _sym("parse", "A.kt", 8, 4)
    index = symbols_at([a, b])
    assert symbol_declared_by(_Node((1, 4)), index) is a  # tree-sitter rows are 0-based
    assert symbol_declared_by(_Node((7, 4)), index) is b


def test_a_node_that_declares_nothing_has_no_symbol() -> None:
    index = symbols_at([_sym("parse", "A.kt", 2, 4)])
    assert symbol_declared_by(_Node((1, 0)), index) is None


def test_the_file_filter_and_a_spanless_symbol() -> None:
    here, there = _sym("f", "A.kt", 2, 0), _sym("f", "B.kt", 2, 0)
    index = symbols_at([here, there, _sym("g", "A.kt", 9, 0, span=False)], "A.kt")
    assert list(index.values()) == [here]
