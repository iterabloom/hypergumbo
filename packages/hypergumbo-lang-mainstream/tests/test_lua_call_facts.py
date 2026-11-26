# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/lua.py
"""What the Lua analyzer binds itself and what it reports (ADR-0064, batch 5b).

The analyzer binds a call only where Lua's own rules name the callee
(``_LuaCalls``): a ``local function`` in scope (``lexical_scope``); a field of
a module a ``require`` of a file of the tree binds, held by what that file
returns (``import``); a field declared on the caller's own local table
(``lexical_scope``); ``self``'s own table (``own_type``); a table a local was
constructed from (``receiver_type``). Every other call is an unresolved stub
with the facts its site has, and the shared linkers decide through the file's
table (``ImportScope`` of FILES).

The first part pins each route at the analyzer, each with a NAMESAKE in
another file that a bind by name would pick; the second runs the whole
pipeline (``run_behavior_map``), in both orders of the files.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.import_scope import MODULES_ARE_FILES
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.lua import (
    LUA_VALUE_METHODS,
    OPENRESTY_VALUE_METHODS,
    _LuaCalls,
    _import_scope,
    _module_names,
    analyze_lua,
)


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _calls(root: Path, files: dict[str, str], caller: str) -> list[Edge]:
    """The ``calls`` edges of the symbol named ``caller`` (the one in ``main.lua``)."""
    result = analyze_lua(_write(root, files))
    src = next(s.id for s in result.symbols if s.name == caller and s.path == "main.lua")
    return [e for e in result.edges if e.edge_type == "calls" and e.src == src]


def _bound(edges: list[Edge]) -> list[tuple[str, str, str]]:
    """(file, target name, mark) of each resolved edge, in line order."""
    return [
        (e.dst.split(":")[1], e.dst.split(":")[-2], e.meta["resolved_by"])
        for e in sorted(edges, key=lambda e: e.line) if e.is_resolved
    ]


_FACTS = ("qualifier", "receiver_type_hint", "enclosing_class", "stdlib_member_name", "call_construct")


def _stubs(edges: list[Edge]) -> list[tuple[str, dict[str, Any]]]:
    """(callee name, facts) of each unresolved edge, in line order."""
    return [
        (e.meta["callee_name"], {k: e.meta[k] for k in _FACTS if k in e.meta})
        for e in sorted(edges, key=lambda e: e.line) if not e.is_resolved
    ]


_MODULE = "local M = {}\n\nfunction M.helper()\nend\n\nfunction M:run()\nend\n\nreturn M\n"


# --- bare calls: Lua's lexical scoping --------------------------------------------


def test_a_bare_call_binds_the_local_function_in_scope_and_nothing_else(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "other.lua": "local function helper() end\nfunction shout() end\n",
        "main.lua": (
            "local function helper() end\n"
            "local function main(cb)\n"
            "  helper()\n"            # the local function
            "  cb()\n"                # a parameter: a value
            "  shout()\n"             # a global: not bound by the analyzer
            "  print('x')\n"          # a basic function
            "  for i, f in ipairs({}) do f() end\n"
            "  for helper = 1, 2 do helper() end\n"
            "  do local helper = 1; helper() end\n"
            "  local function rec() rec() end\n"
            "end\n"
        ),
    }, "main")
    assert _bound(edges) == [("main.lua", "helper", "lexical_scope")]
    assert _stubs(edges) == [
        ("cb", {}), ("shout", {}), ("print", {"stdlib_member_name": True}), ("ipairs", {"stdlib_member_name": True}),
        ("f", {}), ("helper", {}), ("helper", {}),
    ]


def test_a_local_reads_the_binding_before_its_own_statement(tmp_path: Path) -> None:
    """``local insert = table.insert`` makes ``insert`` the library's;
    ``local x = x`` reads the outer ``x``; a ``local f`` that a later
    ``function f()`` statement assigns is that function."""
    edges = _calls(tmp_path, {
        "other.lua": "local M = {}\nfunction M.insert() end\nfunction concat() end\nreturn M\n",
        "main.lua": (
            "local insert = table.insert\n"
            "local fmt = string.format\n"
            "local hex\n"
            "do\n  function hex() end\nend\n"
            "local function main()\n"
            "  insert({}, 1)\n"
            "  fmt('%d', 1)\n"
            "  hex()\n"
            "  local main = main\n"
            "  main()\n"
            "end\n"
        ),
    }, "main")
    assert _bound(edges) == [("main.lua", "hex", "lexical_scope"), ("main.lua", "main", "lexical_scope")]
    assert _stubs(edges) == [("insert", {"stdlib_member_name": True}), ("fmt", {"stdlib_member_name": True})]


def test_the_caller_is_found_by_position_not_by_name(tmp_path: Path) -> None:
    """Two ``local function helper`` in one file: a call in the first belongs
    to the first (by name it went to the second)."""
    result = analyze_lua(_write(tmp_path, {"main.lua": (
        "local function helper()\n  print(1)\nend\n"
        "local function helper()\n  print(2)\nend\n"
    )}))
    callers = {e.line: e.src for e in result.edges if e.edge_type == "calls"}
    assert callers == {2: "lua:main.lua:1-3:helper:function", 5: "lua:main.lua:4-6:helper:function"}


# --- a module of the tree -----------------------------------------------------------


def test_a_field_of_a_required_module_is_what_that_file_returns(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "pkg/util.lua": _MODULE,
        "decoy.lua": _MODULE,
        "main.lua": (
            "local util = require('pkg.util')\n"
            "local helper = util.helper\n"
            "local run = require('pkg.util').run\n"
            "local function main()\n"
            "  util.helper()\n"
            "  util:run()\n"
            "  require('pkg.util').helper()\n"
            "  helper()\n"
            "  run()\n"
            "  util.missing()\n"
            "  util.sub.deep()\n"
            "end\n"
        ),
    }, "main")
    assert _bound(edges) == [("pkg/util.lua", "M.helper", "import"), ("pkg/util.lua", "M.run", "import")] + [
        ("pkg/util.lua", "M.helper", "import"), ("pkg/util.lua", "M.helper", "import"),
        ("pkg/util.lua", "M.run", "import"),
    ]
    assert _stubs(edges) == [
        ("missing", {"qualifier": "util", "call_construct": "function"}),
        ("deep", {"call_construct": "function"}),
    ]
    (missing,) = [e for e in edges if e.meta.get("callee_name") == "missing"]
    assert missing.dst == "lua:pkg.util:0-0:missing:unresolved"


def test_what_a_module_returns(tmp_path: Path) -> None:
    """``return {f = f}``, ``return setmetatable(_M, mt)``, a re-export
    ``_M.g = other.g``, a module that IS a function, ``init.lua``."""
    edges = _calls(tmp_path, {
        "fields.lua": "local function f() end\nlocal function g() end\nreturn { f = f, g = g, [1] = g }\n",
        "meta.lua": (
            "local other = require('fields')\nlocal _M = {}\nfunction _M.h() end\n"
            "_M.g = other.g\nreturn setmetatable(_M, {})\n"
        ),
        "fn.lua": "local function go() end\nreturn go\n",
        "dir/init.lua": "local D = {}\nfunction D.k() end\nreturn D\n",
        "plain.lua": "return 42\n",
        "main.lua": (
            "local fields, meta, fn = require('fields'), require('meta'), require('fn')\n"
            "local dir, plain = require('dir'), require('plain')\n"
            "local function main()\n"
            "  fields.f()\n  meta.h()\n  meta.g()\n  fn()\n  dir.k()\n  plain.x()\n"
            "end\n"
        ),
    }, "main")
    assert _bound(edges) == [
        ("fields.lua", "f", "import"), ("meta.lua", "_M.h", "import"), ("fields.lua", "g", "import"),
        ("fn.lua", "go", "import"), ("dir/init.lua", "D.k", "import"),
    ]
    assert _stubs(edges) == [("x", {"qualifier": "plain", "call_construct": "function"})]


def test_what_cannot_be_followed_stays_unresolved(tmp_path: Path) -> None:
    """A module with no ``return``, a field its table does not hold, two
    modules passing a name to each other, a nested field set by assignment,
    a declaration of a nested name (no symbol), a value read off a call."""
    edges = _calls(tmp_path, {
        "noret.lua": "function f() end\n",
        "fields.lua": "local function f() end\nreturn { f = f }\n",
        "ping.lua": "local pong = require('pong')\nreturn { f = pong.f }\n",
        "pong.lua": "local ping = require('ping')\nreturn { f = ping.f }\n",
        "main.lua": (
            "local noret, fields, ping = require('noret'), require('fields'), require('ping')\n"
            "local T = { go = function() end }\n"
            "local function go() end\n"
            "local U = { go = go }\n"
            "T.sub.f = go\n"
            "function T.sub.g() end\n"
            "local v = get().field\n"
            "local function main()\n"
            "  noret.f()\n  fields.nope()\n  ping.f()\n  T.go()\n  U.go()\n  v()\n"
            "end\n"
            "function T.sub:m()\n  self:x()\nend\n"
        ),
    }, "main")
    assert _bound(edges) == [("main.lua", "go", "lexical_scope")]
    assert [name for name, _ in _stubs(edges)] == ["f", "nope", "f", "T.go", "v"]


def test_a_library_module_is_reported_with_its_name_and_no_qualifier(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "cjson.lua_decoy/x.lua": "",
        "main.lua": (
            "local cjson = require('cjson')\nlocal decode = cjson.decode\n"
            "local function main()\n  cjson.encode({})\n  cjson:x()\n  decode('')\nend\n"
        ),
    }, "main")
    assert _bound(edges) == []
    assert _stubs(edges) == [
        ("encode", {"call_construct": "function"}), ("x", {"call_construct": "function"}),
        ("decode", {}),
    ]
    assert [e.dst for e in sorted(edges, key=lambda e: e.line)] == [
        "lua:cjson:0-0:encode:unresolved", "lua:cjson:0-0:x:unresolved", "lua:cjson:0-0:decode:unresolved",
    ]


def test_module_names_from_both_roots() -> None:
    assert _module_names("db/init.lua", "kong") == ["db.init", "db", "kong.db.init", "kong.db"]
    assert _module_names("x.lua", "") == ["x"]


def test_a_module_of_the_tree_named_after_the_trees_directory(tmp_path: Path) -> None:
    root = _write(tmp_path / "kong", {
        "db/init.lua": "local _M = {}\nfunction _M.connect() end\nreturn _M\n",
        "main.lua": "local db = require 'kong.db'\nlocal function main()\n  db.connect()\nend\n",
    })
    result = analyze_lua(root)
    assert [e.dst for e in result.edges if e.edge_type == "calls"] == ["lua:db/init.lua:2-2:_M.connect:function"]


# --- tables of the caller's file, ``self`` and constructed values --------------------


def test_a_field_of_the_callers_own_local_table(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "other.lua": "local _M = {}\nfunction _M.f() end\nfunction _M.g() end\nreturn _M\n",
        "main.lua": (
            "local _M = {}\n"
            "function _M.f() end\n"
            "function _M.f() end\n"         # a redefinition: the later one
            "local function main()\n"
            "  _M.f()\n"
            "  _M.g()\n"                    # declared elsewhere only
            "  local _M = {}\n"
            "  _M.f()\n"                    # another local of that name
            "end\n"
            "return _M\n"
        ),
    }, "main")
    assert [e.dst for e in edges if e.is_resolved] == ["lua:main.lua:3-3:_M.f:function"]
    assert _bound(edges) == [("main.lua", "_M.f", "lexical_scope")]
    assert _stubs(edges) == [("_M.g", {}), ("_M.f", {})]


def test_self_is_the_enclosing_table(tmp_path: Path) -> None:
    result = analyze_lua(_write(tmp_path, {
        "other.lua": "local T = {}\nfunction T:save() end\nfunction T:gone() end\nreturn T\n",
        "main.lua": (
            "local T = {}\n"
            "function T:save() end\n"
            "function T:run()\n  self:save()\n  self.save(self)\n  self:gone()\n  self:close()\n"
            "  local function inner() self:save() end\nend\n"
            "function T.plain(self)\n  self:save()\nend\n"
            "G = {}\nfunction G:a() end\nfunction G:b()\n  self:a()\nend\n"
        ),
    }))
    by_line = {
        e.line: (e.dst.split(":")[-2], (e.meta or {}).get("resolved_by")) if e.is_resolved
        else ({k: e.meta[k] for k in _FACTS if k in e.meta})
        for e in result.edges if e.edge_type == "calls"
    }
    assert by_line == {
        4: ("T.save", "own_type"), 5: ("T.save", "own_type"),
        6: {"enclosing_class": "T", "call_construct": "method"},
        7: {"enclosing_class": "T", "call_construct": "method", "stdlib_member_name": True},
        8: ("T.save", "own_type"), 11: ("T.save", "own_type"), 16: ("G.a", "own_type"),
    }


def test_a_constructed_local_reaches_its_tables_field(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "store.lua": "local Store = {}\nfunction Store.new() end\nfunction Store:execute() end\nreturn Store\n",
        "decoy.lua": "local Store = {}\nfunction Store:execute() end\nfunction Store:only() end\nreturn Store\n",
        "main.lua": (
            "local Store = require('store')\n"
            "local R = {}\nfunction R:execute() end\n"
            "local function main()\n"
            "  local a = Store.new()\n  a:execute()\n"
            "  local b = R:new()\n  b:execute()\n"
            "  local c = setmetatable({}, R)\n  c:execute()\n"
            "  function c.own() end\n  c.own()\n"
            "  a:only()\n"
            "end\n"
        ),
    }, "main")
    assert _bound(edges) == [
        ("store.lua", "Store.new", "import"), ("store.lua", "Store.execute", "receiver_type"),
        ("main.lua", "R.execute", "receiver_type"), ("main.lua", "R.execute", "receiver_type"),
        ("main.lua", "c.own", "lexical_scope"),
    ]
    typed = [e for e in edges if e.is_resolved and e.meta["resolved_by"] == "receiver_type"]
    assert {e.meta["resolution_quality"] for e in typed} == {"typed"}
    assert _stubs(edges) == [
        ("new", {"call_construct": "method"}),  # R declares no ``new``
        ("setmetatable", {"stdlib_member_name": True}),
        ("only", {"receiver_type_hint": "Store", "call_construct": "method"}),
    ]


# --- what goes out unresolved -------------------------------------------------------


def test_calls_on_receivers_and_library_tables(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "main.lua": (
            "local function main(x, cfg, t)\n"
            "  x:get()\n  x:frob()\n  s:upper()\n"
            "  string.format('%d', 1)\n  ngx.req.get_headers()\n  kong.log.err('e')\n"
            "  cfg.x.get()\n  x.f()\n"
            "  t[1]()\n  x()()\n  t.a:b()\n  x().g()\n"
            "end\n"
        ),
    }, "main")
    assert _bound(edges) == []
    assert _stubs(edges) == [
        ("get", {"call_construct": "method", "stdlib_member_name": True}),
        ("frob", {"call_construct": "method"}),
        ("upper", {"call_construct": "method", "stdlib_member_name": True}),
        ("string.format", {"stdlib_member_name": True}),
        ("ngx.req.get_headers", {"stdlib_member_name": True}),
        ("kong.log.err", {}),
        ("cfg.x.get", {}),
        ("x.f", {}),
        ("t[1]", {}),
        ("x()", {}), ("x", {}),
        ("b", {"call_construct": "method"}),
        ("g", {"call_construct": "method"}), ("x", {}),
    ]


def test_the_screens_hold_the_library_and_not_the_trees_names() -> None:
    assert {"format", "sub", "close", "write", "__index", "__tostring"} <= LUA_VALUE_METHODS
    assert {"get", "set", "connect", "receive", "setkeepalive"} <= OPENRESTY_VALUE_METHODS
    assert not {"execute", "helper", "run"} & (LUA_VALUE_METHODS | OPENRESTY_VALUE_METHODS)


def test_require_is_an_import_whatever_its_shape(tmp_path: Path) -> None:
    result = analyze_lua(_write(tmp_path, {"main.lua": (
        "local a = require('a')\nrequire 'b'\nfoo.require('c')\nrequire(name)\n"
    )}))
    assert [(e.edge_type, e.dst) for e in result.edges] == [
        ("imports", "lua:a:0-0:module:module"), ("imports", "lua:b:0-0:module:module"),
        ("imports", "lua:c:0-0:module:module"),
    ]


# --- the table ------------------------------------------------------------------------


def test_every_file_has_a_table_of_its_chunk_level_requires(tmp_path: Path) -> None:
    root = _write(tmp_path, {
        "pkg/util.lua": _MODULE,
        "two.lua": "", "two/init.lua": "",
        "main.lua": (
            "local util = require('pkg.util')\n"
            "local helper = util.helper\n"
            "local run = require('pkg.util').run\n"
            "local lib = require('cjson')\n"
            "local amb = require('two')\n"
            "require('pkg.util')\n"
            "local function f() local u = require('pkg.util') end\n"
        ),
    })
    result = analyze_lua(root)
    assert set(result.import_scopes) == {"pkg/util.lua", "two.lua", "two/init.lua", "main.lua"}
    scope = result.import_scopes["main.lua"]
    assert (scope.module_kind, scope.library_names_screened) == (MODULES_ARE_FILES, True)
    assert scope.namespace("util") == "pkg/util.lua"
    assert (scope.named("helper").module, scope.named("helper").name) == ("pkg/util.lua", "helper")
    assert (scope.named("run").module, scope.named("run").name) == ("pkg/util.lua", "run")
    assert scope.namespace("lib") is None and scope.namespace("amb") is None and scope.namespace("u") is None
    assert result.import_scopes["two.lua"].namespace("util") is None

    import tree_sitter
    import tree_sitter_lua
    from hypergumbo_lang_mainstream.lua import FileAnalysis, _Scopes

    parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_lua.language()))
    files = {}
    for rel in ("pkg/util.lua", "two.lua", "two/init.lua", "main.lua"):
        source = (root / rel).read_bytes()
        files[rel] = FileAnalysis(rel, source, parser.parse(source), [], scopes=_Scopes(source))
    _, counts = _import_scope(files["main.lua"], _LuaCalls(files, ""))
    assert dict(counts) == {
        "add_namespace": 1, "add_named": 2, "require recorded": 2,
        "require left out: library": 1, "require left out: two files answer the name": 1,
        "require left out: not a chunk-level local": 2,
    }


# --- through the shared pass -------------------------------------------------------


def _map(tmp_path: Path, files: dict[str, str]) -> dict[str, Any]:
    repo = _write(tmp_path / "repo", files)
    out = tmp_path / "map.json"
    run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


def _resolved(data: dict[str, Any], caller: str) -> list[tuple[str, str, str]]:
    """(target path, target name, mark) of each resolved call from ``caller``."""
    nodes = {n["id"]: n for n in data["nodes"]}
    return sorted(
        (nodes[e["dst"]]["path"], nodes[e["dst"]]["name"], (e.get("meta") or {}).get("resolved_by"))
        for e in data["edges"]
        if e["type"] == "calls" and e.get("is_resolved")
        and not (e.get("meta") or {}).get("superseded_by")
        and nodes.get(e["src"], {}).get("name") == caller and e["dst"] in nodes
    )


def _both_orders(files: dict[str, str]) -> list[dict[str, str]]:
    """The files, and the same files with the decoys named to sort first."""
    flipped = {(f"a_{k[2:]}" if k.startswith("z_") else k): v.replace("z_", "a_") for k, v in files.items()}
    return [files, flipped]


def test_end_to_end_through_a_require_a_constructor_and_the_own_table(tmp_path: Path) -> None:
    for i, files in enumerate(_both_orders({
        "util.lua": _MODULE,
        "z_util.lua": _MODULE,
        "store.lua": "local Store = {}\nfunction Store.new() end\nfunction Store:execute() end\nreturn Store\n",
        "z_store.lua": "local Store = {}\nfunction Store.new() end\nfunction Store:execute() end\nreturn Store\n",
        "main.lua": (
            "local util = require('util')\nlocal Store = require('store')\n"
            "local _M = {}\nfunction _M.own() end\n"
            "function _M.main()\n"
            "  util.helper()\n"
            "  local s = Store.new()\n  s:execute()\n"
            "  _M.own()\n"
            "end\nreturn _M\n"
        ),
    })):
        data = _map(tmp_path / str(i), files)
        assert _resolved(data, "_M.main") == [
            ("main.lua", "_M.own", "lexical_scope"),
            ("store.lua", "Store.execute", "receiver_type"), ("store.lua", "Store.new", "import"),
            ("util.lua", "M.helper", "import"),
        ]


def test_end_to_end_the_name_step_binds_one_holder_and_refuses_the_library(tmp_path: Path) -> None:
    """An untyped receiver: one holder of ``drain`` is bound by the name step
    (``name``); two holders of ``execute`` are a tie; ``format`` and ``get``,
    which the tree declares once each, are the library's (screened)."""
    for i, files in enumerate(_both_orders({
        "pool.lua": "local P = {}\nfunction P:drain() end\nfunction P:format() end\nfunction P:get() end\nreturn P\n",
        "a.lua": "local A = {}\nfunction A:execute() end\nreturn A\n",
        "z_b.lua": "local B = {}\nfunction B:execute() end\nreturn B\n",
        "main.lua": (
            "local function main(x)\n  x:drain()\n  x:execute()\n  x:format()\n  x:get()\nend\nreturn main\n"
        ),
    })):
        data = _map(tmp_path / str(i), files)
        assert _resolved(data, "main") == [("pool.lua", "P.drain", "name")]
