# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each Lua call route stamps what scoped its choice (WI-hirol).

``Edge.meta["resolved_by"]`` (:mod:`hypergumbo_core.resolved_by`) names the
evidence that chose a call-family edge's target. One fixture line per route:

* ``local m = require("x"); m.f()`` is ``import``;
* ``a:m()`` on a local whose table is known is ``receiver_type``;
* ``Account.new()``, looked up under the table the call site names, is
  ``qualified``;
* ``self:m()`` with no typed receiver falls to the name lookup and finds a
  method of the caller's own table: ``own_type``; a bare ``f()`` found by
  name across the repository is ``name``.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/lua.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.lua import analyze_lua

_FILES = {
    "util.lua": "local M = {}\n\nfunction M.greet()\nend\n\nreturn M\n",
    "account.lua": """Account = {}

function Account.new()
  return setmetatable({}, {__index = Account})
end

function Account:deposit(v)
end

function Account:audit()
  self:deposit(1)
  helper2()
end

function helper2()
end

return Account
""",
    "main.lua": """local util = require("util")

local function run()
  util.greet()
  local a = Account.new()
  a:deposit(5)
end
""",
}


def _edges(root: Path) -> list[Edge]:
    for rel, text in _FILES.items():
        (root / rel).write_text(text)
    return analyze_lua(root).edges


def _mark(edges: list[Edge], path: str, line: int, dst_name: str) -> str:
    hits = [
        e for e in edges
        if e.edge_type == "calls" and e.is_resolved and e.line == line
        and f":{path}:" in e.src and e.dst.split(":")[-2] == dst_name
    ]
    assert len(hits) == 1, [(e.src, e.line, e.dst) for e in edges]
    return hits[0].meta["resolved_by"]


def test_every_route(tmp_path: Path) -> None:
    edges = _edges(tmp_path)
    assert _mark(edges, "main.lua", 4, "M.greet") == "import"
    assert _mark(edges, "main.lua", 5, "Account.new") == "qualified"
    assert _mark(edges, "main.lua", 6, "Account.deposit") == "receiver_type"
    assert _mark(edges, "account.lua", 11, "Account.deposit") == "own_type"
    assert _mark(edges, "account.lua", 12, "helper2") == "name"
