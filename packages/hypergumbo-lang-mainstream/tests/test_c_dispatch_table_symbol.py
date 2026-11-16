# SPDX-License-Identifier: AGPL-3.0-or-later
"""A C dispatch table is a symbol its edges can start from (WI-dirij).

c.py draws every ``dispatches_to`` edge from a ``kind=variable`` id for the
table -- ``static int tbl[] = { f }`` or ``static struct ops o = { .f = f }``
-- and the ``dispatch_table`` reference to it, so the table must be emitted as
a symbol by the same id once it holds a function. Without it the src names no
node and ``create_boundary_nodes`` turns the first-party table into an
``external_symbol``. The array of function POINTERS (``static int (*cmds[])(int)
= { f }``), the idiomatic spelling, is detected too: its ``array_declarator``
sits under ``function_declarator`` > ``parenthesized_declarator`` >
``pointer_declarator``, not directly under the ``init_declarator``.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/c.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.edge_source import unemitted_edge_sources
from hypergumbo_lang_mainstream.c import analyze_c

_SRC = """\
int helper(int x) { return x; }
int other(int x) { return x; }
struct ops { int (*f)(int); int (*g)(int); };
static int tbl[] = { helper };
static struct ops o = { .f = helper, .g = other };
static int (*cmds[])(int) = { helper, other };
static int nums[] = { 1, 2 };
int user(int i) { return tbl[i] + cmds[i](i) + o.f(i); }
"""


def _result(tmp_path: Path):
    (tmp_path / "t.c").write_text(_SRC)
    return analyze_c(tmp_path)


def test_every_dispatch_edge_starts_at_an_emitted_symbol(tmp_path: Path) -> None:
    result = _result(tmp_path)
    assert unemitted_edge_sources(result.symbols, result.edges) == []


def test_the_tables_are_variables_and_a_data_array_is_not(tmp_path: Path) -> None:
    result = _result(tmp_path)
    variables = {s.name: s for s in result.symbols if s.kind == "variable"}
    assert set(variables) == {"tbl", "o", "cmds"}
    assert all(s.stable_id for s in variables.values())
    by_id = {s.id: s.name for s in result.symbols}
    dispatched = sorted(
        (by_id[e.src], by_id[e.dst]) for e in result.edges if e.edge_type == "dispatches_to")
    assert dispatched == [
        ("cmds", "helper"), ("cmds", "other"),
        ("o", "helper"), ("o", "other"),
        ("tbl", "helper"),
    ]


def test_a_use_of_the_table_references_its_symbol(tmp_path: Path) -> None:
    result = _result(tmp_path)
    by_id = {s.id: s.name for s in result.symbols}
    refs = sorted(
        (by_id[e.src], by_id[e.dst]) for e in result.edges
        if e.edge_type == "references" and (e.meta or {}).get("ref_construct") == "dispatch_table")
    assert refs == [("user", "cmds"), ("user", "tbl")]
