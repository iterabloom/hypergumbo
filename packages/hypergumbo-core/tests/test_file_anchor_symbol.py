# SPDX-License-Identifier: AGPL-3.0-or-later
"""``file_anchor_symbol`` names the file node the ``make_file_id`` id already names.

INV-bamij. Analyzers anchor a call that sits in no function on this symbol; it
is only useful if its id is exactly the file node's id (the ``imports`` edges'
source), so a call edge from it lands on a real node rather than minting one.
"""

from __future__ import annotations

from hypergumbo_core.analyze.base import file_anchor_symbol, make_file_id


def test_the_anchor_is_the_file_node() -> None:
    sym = file_anchor_symbol("lua", "lib/a.lua", "lua-v1", "run-1")
    assert sym.id == make_file_id("lua", "lib/a.lua") == "lua:lib/a.lua:1-1:file:file"
    assert (sym.kind, sym.language, sym.path) == ("file", "lua", "lib/a.lua")
    assert (sym.origin, sym.origin_run_id) == (["lua-v1"], "run-1")


class _Node:
    def __init__(self, type_: str, line: int, col: int, parent: "_Node | None" = None) -> None:
        self.type, self.start_point, self.parent = type_, (line - 1, col), parent


def test_the_nearest_declared_type_is_found_and_an_undeclared_one_skipped() -> None:
    from hypergumbo_core.analyze.base import enclosing_declared_symbol

    outer_sym = file_anchor_symbol("swift", "a.swift", "p", "r")  # any Symbol will do
    root = _Node("source_file", 1, 0)
    outer = _Node("class_declaration", 2, 0, root)      # declared
    inner = _Node("class_declaration", 5, 4, outer)     # no symbol: walked past
    body = _Node("property_declaration", 6, 8, inner)
    call = _Node("call_expression", 6, 20, body)
    index = {(2, 0): outer_sym}
    assert enclosing_declared_symbol(call, index, {"class_declaration"}) is outer_sym
    assert enclosing_declared_symbol(call, index, {"object_declaration"}) is None
