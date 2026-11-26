# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each Lua call route stamps what scoped its choice (WI-hirol).

``Edge.meta["resolved_by"]`` (:mod:`hypergumbo_core.resolved_by`) names the
evidence that chose a call-family edge's target. One fixture line per route:

* ``local m = require("x"); m.f()`` is ``import``;
* ``a:m()`` on a local constructed from a table is ``receiver_type``;
* ``Account.new()`` on the caller's own local table ``Account``, and a bare
  call of a ``local function``, are ``lexical_scope``;
* ``self:m()`` finds a method of the caller's own table: ``own_type``.

Since ADR-0064 the analyzer marks no ``name`` bind: a call of a GLOBAL
(``Account`` and ``helper2`` of another file) goes out unresolved, for the
shared linkers (the name step).
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
    "local.lua": """local Account = {}

function Account.new()
  return setmetatable({}, {__index = Account})
end

function Account:deposit(v)
end

local function helper2()
end

local function run()
  local a = Account.new()
  a:deposit(5)
  helper2()
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
    assert _mark(edges, "local.lua", 14, "Account.new") == "lexical_scope"
    assert _mark(edges, "local.lua", 15, "Account.deposit") == "receiver_type"
    assert _mark(edges, "local.lua", 16, "helper2") == "lexical_scope"
    assert _mark(edges, "account.lua", 11, "Account.deposit") == "own_type"
    # Globals of another file: unresolved, the receiver's type as written.
    unresolved = {
        (e.line, e.dst.split(":")[-2]): e.meta for e in edges
        if e.edge_type == "calls" and not e.is_resolved and ":main.lua:" in e.src
    }
    assert set(unresolved) == {(5, "Account.new"), (6, "deposit")}
    assert unresolved[(6, "deposit")]["receiver_type_hint"] == "Account"
    assert not any(
        e.is_resolved for e in edges if e.edge_type == "calls" and e.line == 12 and ":account.lua:" in e.src
    )
