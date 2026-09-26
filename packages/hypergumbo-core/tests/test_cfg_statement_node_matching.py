# SPDX-License-Identifier: AGPL-3.0-or-later
"""A CFG statement is populated from the AST node it stands for, and only that one.

WI-simiv. The same-function sanitizer credit (WI-fasub) was never observed on
the production path, and the two shapes that should have earned it failed for
two reasons in ``populate_def_use_for_cfg``, which matches AST nodes to CFG
statements by ``(line, col, node_type)``:

1. AN INNER NODE OVERWROTE THE OUTER ONE. In ``open("o", "wb").write(e)`` the
   outer call and the inner ``open(...)`` start at the same position and are
   both ``call``. The pre-order walk populated the statement from the outer
   call (``uses=["e"]``) and then again from the inner one (``uses=[]``), so
   the tainted ``e`` had no recorded use and the walk escaped.
2. A SYNTHETIC STATEMENT MATCHED NOTHING. A ``with`` clause is recorded as
   ``context_manager_enter``, a type no AST node has, so the extractor never
   ran on it: ``with open(...) as f`` defined no ``f``, and the clause counted
   as code no statement covers, which forfeits the whole function's
   refutation (INV-lupav) and with it the sanitizer credit.

These tests use a stub extractor that reports which node it was handed, so they
pin the MATCHING and not any language's def/use rules.
"""

from __future__ import annotations

from typing import Any

import pytest

from hypergumbo_core import cfg as C
from hypergumbo_core.cfg import (
    DefUseResult,
    build_function_cfg,
    clear_cfg_mapping_cache,
    load_cfg_mapping,
    populate_def_use_for_cfg,
    uncovered_semantic_lines,
)


class _WhichNode:
    """Reports the node type and text it was asked about, as a 'use'."""

    language = "stub"

    def extract(self, node: Any, source: bytes) -> DefUseResult:
        text = source[node.start_byte:node.end_byte].decode()
        return DefUseResult(uses=[f"{node.type}|{text}"])


def _cfg(source: str, monkeypatch: pytest.MonkeyPatch):
    from tree_sitter_language_pack import get_language
    import tree_sitter

    tree = tree_sitter.Parser(get_language("python")).parse(source.encode())
    body = tree.root_node.children[0].child_by_field_name("body")
    clear_cfg_mapping_cache()
    mapping = load_cfg_mapping("python")
    cfg = build_function_cfg(body, source.encode(), mapping, "python:a.py:1-9:f:function")
    monkeypatch.setitem(C._DEF_USE_EXTRACTORS, "stub", _WhichNode())
    populate_def_use_for_cfg(cfg, body, source.encode(), "stub")
    return cfg, body, mapping


def _statements(cfg) -> dict[int, list[Any]]:
    out: dict[int, list[Any]] = {}
    for block in cfg.blocks.values():
        for stmt in block.statements:
            out.setdefault(stmt.line, []).append(stmt)
    return out


def test_the_outermost_node_populates_a_shared_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, _body, _m = _cfg('def f(e):\n    open("o", "wb").write(e)\n', monkeypatch)
    [stmt] = _statements(cfg)[2]
    assert stmt.uses == ['call|open("o", "wb").write(e)']


def test_a_context_manager_enter_is_populated_from_its_clause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, _body, _m = _cfg(
        'def f(e):\n    with open("o", "wb") as g:\n        g.write(e)\n',
        monkeypatch,
    )
    enter = [s for s in _statements(cfg)[2] if s.node_type == "context_manager_enter"]
    assert len(enter) == 1
    assert enter[0].ast_node_type == "with_clause"
    assert enter[0].uses == ['with_clause|open("o", "wb") as g']


def test_the_clause_is_covered_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """The coverage gate matches the same key, so the clause stops forfeiting
    the function. Before, line 2 was reported uncovered."""
    cfg, body, mapping = _cfg(
        'def f(e):\n    with open("o", "wb") as g:\n        g.write(e)\n',
        monkeypatch,
    )
    source = b'def f(e):\n    with open("o", "wb") as g:\n        g.write(e)\n'
    assert uncovered_semantic_lines(cfg, body, source, mapping) == frozenset()


def test_the_exit_statement_names_no_ast_node(monkeypatch: pytest.MonkeyPatch) -> None:
    """``context_manager_exit`` is the implicit ``__exit__`` call: there is no
    source node to extract from, and none may be claimed."""
    cfg, _body, _m = _cfg(
        'def f(e):\n    with open("o", "wb") as g:\n        g.write(e)\n',
        monkeypatch,
    )
    exits = [
        s for stmts in _statements(cfg).values() for s in stmts
        if s.node_type == "context_manager_exit"
    ]
    assert exits and all(s.ast_node_type is None for s in exits)
