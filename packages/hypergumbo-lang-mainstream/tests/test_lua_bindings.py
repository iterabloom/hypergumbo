# SPDX-License-Identifier: AGPL-3.0-or-later
"""File-scope bindings the Lua analyzer records: subclass tables and constructions.

Lua has no class syntax. A class is a table made by calling something on its
parent (``lapis.Application:extend("App")``, middleclass ``Base:subclass("X")``,
rxi/classic ``Object:extend()``), and an application object is a table made by
calling a constructor (``local app = lapis.Application()``). Before these were
recorded the analyzer emitted only functions and the file, so no framework row
keyed on ``base_classes`` or ``constructed_from`` could ever fire for Lua
(openresty.yaml's Lapis ``application`` row among them).

The properties asserted:

* ``T = R:extend(...)`` / ``T = R:subclass(...)`` at file scope emits a
  ``class`` symbol ``T`` whose ``meta["base_classes"]`` names ``R``;
* ``x = C(...)`` / ``x = R:m(...)`` at file scope emits a ``variable`` symbol
  ``x`` whose ``meta["constructed_from"]`` names the callee;
* a name's head is read through a ``require``: ``local l = require "lapis"``
  makes ``l.Application`` record as ``lapis.Application``, and a local table
  that merely shares the module's name records nothing that could pass for it.
"""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.lua import analyze_lua


def _analyze(tmp_path: Path, source: str) -> list[Symbol]:
    (tmp_path / "app.lua").write_text(source)
    return analyze_lua(tmp_path).symbols


def _one(symbols: list[Symbol], name: str, kind: str) -> Symbol:
    found = [s for s in symbols if s.name == name and s.kind == kind]
    assert len(found) == 1, [(s.name, s.kind) for s in symbols]
    return found[0]


def _meta(sym: Symbol, key: str) -> object:
    return (sym.meta or {}).get(key)


class TestSubclassTables:
    def test_extend_on_a_required_module_records_the_module_path(
        self, tmp_path: Path,
    ) -> None:
        syms = _analyze(tmp_path, (
            'local lapis = require("lapis")\n'
            'local App = lapis.Application:extend("App", {})\n'
            "return App\n"
        ))
        app = _one(syms, "App", "class")
        assert _meta(app, "base_classes") == ["lapis.Application"]
        assert app.stable_id and app.span.start_line == 2

    def test_a_require_alias_is_read_through(self, tmp_path: Path) -> None:
        syms = _analyze(tmp_path, (
            'local l = require "lapis"\n'
            "local App = l.Application:extend()\n"
        ))
        assert _meta(_one(syms, "App", "class"), "base_classes") == [
            "lapis.Application",
        ]

    def test_a_field_of_a_required_module_bound_to_a_local(
        self, tmp_path: Path,
    ) -> None:
        syms = _analyze(tmp_path, (
            'local Application = require("lapis").Application\n'
            "local App = Application:extend()\n"
        ))
        assert _meta(_one(syms, "App", "class"), "base_classes") == [
            "lapis.Application",
        ]

    def test_subclass_global_and_dotted_targets(self, tmp_path: Path) -> None:
        syms = _analyze(tmp_path, (
            "local M = {}\n"
            "local Base = Object:extend()\n"
            "Derived = Base:subclass('Derived')\n"
            "M.Leaf, M.n = Base:extend(), 2\n"
        ))
        # An unbound head is a global: recorded as written.
        assert _meta(_one(syms, "Base", "class"), "base_classes") == ["Object"]
        # A bare local class is recorded by its in-file name.
        assert _meta(_one(syms, "Derived", "class"), "base_classes") == ["Base"]
        assert _meta(_one(syms, "M.Leaf", "class"), "base_classes") == ["Base"]
        assert not [s for s in syms if s.name == "M.n"]

    def test_a_local_table_named_like_the_module_is_not_the_module(
        self, tmp_path: Path,
    ) -> None:
        syms = _analyze(tmp_path, (
            "local lapis = {}\n"
            "local App = lapis.Application:extend()\n"
            "local app = lapis.Application()\n"
        ))
        assert _meta(_one(syms, "App", "class"), "base_classes") is None
        assert _meta(_one(syms, "app", "variable"), "constructed_from") is None

    def test_a_shadowing_require_rebinds_the_head(self, tmp_path: Path) -> None:
        syms = _analyze(tmp_path, (
            "local lapis = {}\n"
            'local lapis = require("lapis")\n'
            "local App = lapis.Application:extend()\n"
        ))
        assert _meta(_one(syms, "App", "class"), "base_classes") == [
            "lapis.Application",
        ]

    def test_only_file_scope_bindings_are_recorded(self, tmp_path: Path) -> None:
        syms = _analyze(tmp_path, (
            "local function make()\n"
            "  local Inner = Base:extend()\n"
            "  local obj = Inner()\n"
            "  return obj\n"
            "end\n"
        ))
        assert [s.kind for s in syms if s.name in ("Inner", "obj")] == []


class TestConstructedBindings:
    def test_instance_of_a_required_class(self, tmp_path: Path) -> None:
        syms = _analyze(tmp_path, (
            'local lapis = require "lapis"\n'
            "local app = lapis.Application()\n"
            "return app\n"
        ))
        app = _one(syms, "app", "variable")
        assert _meta(app, "constructed_from") == "lapis.Application"
        assert app.stable_id

    def test_callee_forms(self, tmp_path: Path) -> None:
        syms = _analyze(tmp_path, (
            'local lrucache = require "resty.lrucache"\n'
            "local cache = lrucache.new(200)\n"
            "local mt = setmetatable({}, {})\n"
            "local obj = Thing:new()\n"
            "counter = make_counter()\n"
            "local a, b = first(), second()\n"
            "local t = {}\n"
            "local s = 'x'\n"
            "local f = (get())()\n"
        ))
        cf = {
            s.name: _meta(s, "constructed_from")
            for s in syms if s.kind == "variable"
        }
        assert cf == {
            "cache": "resty.lrucache.new",
            "mt": "setmetatable",
            "obj": "Thing:new",
            "counter": "make_counter",
            "a": "first",
            "b": "second",
            "f": None,
        }

    def test_bindings_without_a_module_and_targets_without_a_name(
        self, tmp_path: Path,
    ) -> None:
        syms = _analyze(tmp_path, (
            "local ngx = ngx\n"                    # a global's alias
            "local Obj = Object\n"
            "local mod = require(name)\n"          # computed module: an import
            "local x, y\n"                         # bound, to nothing
            "local p, q = pair()\n"                # q takes the tail: bound
            "t[k] = make()\n"                      # no static target name
            "M.field = make()\n"                   # a field is not a binding
            "local s1 = ngx.shared.foo:get()\n"
            "local C1 = Obj:extend()\n"
            "local Q = q:extend()\n"
            "local X = x.Base:extend()\n"
            "local r = mod.thing()\n"
            "local _, rest = discard()\n"         # ``_`` is the discard idiom
        ))
        got = {
            (s.name, s.kind): (s.meta or {}).get("constructed_from")
            or (s.meta or {}).get("base_classes")
            for s in syms if s.kind in ("class", "variable")
        }
        assert got == {
            ("p", "variable"): "pair",
            ("s1", "variable"): "ngx.shared.foo:get",
            ("C1", "class"): ["Object"],
            ("Q", "class"): ["q"],
            ("X", "class"): None,
            ("r", "variable"): None,
        }

    def test_a_require_binding_is_an_import_not_a_variable(
        self, tmp_path: Path,
    ) -> None:
        syms = _analyze(tmp_path, (
            'local lapis = require("lapis")\n'
            'local App = require("lapis").Application\n'
        ))
        assert [s for s in syms if s.kind in ("variable", "class")] == []

    def test_new_symbols_do_not_change_call_resolution(
        self, tmp_path: Path,
    ) -> None:
        """The bindings are not entered in the name resolver: ``App()`` was
        an unresolved call before they existed and stays one."""
        (tmp_path / "app.lua").write_text(
            "local App = Base:extend()\n"
            "local function main()\n"
            "  return App()\n"
            "end\n"
        )
        result = analyze_lua(tmp_path)
        app = _one(result.symbols, "App", "class")
        assert not [e for e in result.edges if e.dst == app.id]


class TestLapisApplicationEndToEnd:
    def test_both_lapis_forms_become_hosted_applications(
        self, tmp_path: Path,
    ) -> None:
        """The whole chain on a Lapis project: rockspec -> framework detected
        as ``lapis`` -> openresty.yaml loaded (Lapis's rows live there) ->
        the class row and the instance row match -> two hosted_application
        entries, and a plain table beside them gets none."""
        (tmp_path / "app-1.0-1.rockspec").write_text(
            'package = "app"\nversion = "1.0-1"\n'
            'dependencies = { "lapis" }\n'
        )
        (tmp_path / "app.lua").write_text(
            'local lapis = require("lapis")\n'
            "local App = lapis.Application:extend()\n"
            "return App\n"
        )
        (tmp_path / "api.lua").write_text(
            'local l = require "lapis"\n'
            "local app = l.Application()\n"
            "local cache = setmetatable({}, {})\n"
            "return app\n"
        )
        out = tmp_path / "bm.json"
        run_behavior_map(
            repo_root=tmp_path, out_path=out,
            include_sketch_precomputed=False, progress=False,
        )
        bm = json.loads(out.read_text())
        names = {n["id"]: n["name"] for n in bm["nodes"]}
        hosted = sorted(
            names.get(e["symbol_id"], "?") for e in bm["entrypoints"]
            if e["kind"] == "hosted_application"
        )
        assert hosted == ["App", "app"], bm["profile"]
