# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which type a name means from one place in one file (``type_visibility``, ADR-0064)."""
# covers: packages/hypergumbo-core/src/hypergumbo_core/type_visibility.py
from __future__ import annotations

from hypergumbo_core.import_scope import ImportScope
from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core import type_visibility
from hypergumbo_core.type_visibility import dotted, without_type_arguments


def _type(qualified_name: str | None, name: str = "", path: str = "a.cs") -> Symbol:
    short = name or (qualified_name or "").rpartition(".")[2]
    return Symbol(
        id=f"csharp:{path}:1-2:{qualified_name or short}:class", name=short, kind="class",
        language="csharp", path=path, span=Span(1, 2, 0, 0), qualified_name=qualified_name,
    )


def _scope(*usings: str, aliases: dict[str, tuple[str, str]] | None = None) -> ImportScope:
    scope = ImportScope()
    for namespace in usings:
        scope.add_open_wildcard(namespace)
    for local, (module, name) in (aliases or {}).items():
        scope.add_named(module, name, alias=local)
    return scope


def visible_types(written: str, caller: str | None, scope: ImportScope,
                  candidates: list[Symbol], caller_path: str | None = None) -> list[Symbol] | None:
    """The function under test, with its candidate lookup made from a list."""
    def named(short: str) -> list[Symbol]:
        return [c for c in candidates if c.name == short]
    return type_visibility.visible_types(written, caller, scope, named, caller_path=caller_path)


def _names(found: list[Symbol] | None) -> list[str] | None:
    return None if found is None else sorted(s.qualified_name or "" for s in found)


def test_the_callers_own_namespace_answers_before_a_using() -> None:
    """``namespace App { ... new Conn() }`` with ``using Other;`` and a Conn in
    both: C# takes App.Conn, and the search stops there."""
    conns = [_type("App.Conn"), _type("Other.Conn")]
    assert _names(visible_types("Conn", "App.Main.Run", _scope("Other"), conns)) == ["App.Conn"]


def test_the_innermost_enclosing_scope_answers_first() -> None:
    conns = [_type("App.Conn"), _type("App.Web.Conn"), _type("Conn")]
    assert _names(visible_types("Conn", "App.Web.Handler.Get", _scope(), conns)) == ["App.Web.Conn"]
    assert _names(visible_types("Conn", "App.Jobs.Worker.Run", _scope(), conns)) == ["App.Conn"]
    assert _names(visible_types("Conn", "Tool.Run", _scope(), conns)) == ["Conn"]


def test_a_type_nested_in_the_callers_type_is_in_scope() -> None:
    inner = [_type("App.Outer.Inner"), _type("Lib.Inner")]
    assert _names(visible_types("Inner", "App.Outer.Go", _scope("Lib"), inner)) == ["App.Outer.Inner"]


def test_a_using_supplies_the_name_when_no_enclosing_scope_declares_it() -> None:
    conns = [_type("Db.Conn"), _type("Net.Conn")]
    assert _names(visible_types("Conn", "App.Main.Run", _scope("Db"), conns)) == ["Db.Conn"]


def test_two_usings_that_both_supply_the_name_are_a_tie() -> None:
    conns = [_type("Db.Conn"), _type("Net.Conn")]
    assert _names(visible_types("Conn", "App.Main.Run", _scope("Db", "Net"), conns)) == [
        "Db.Conn", "Net.Conn",
    ]


def test_a_name_no_visible_scope_declares_means_nothing_here() -> None:
    """``Exception`` with ``using System;`` and an in-repository
    ``Tool.Errors.Exception`` the file does not import: not that one."""
    elsewhere = [_type("Tool.Errors.Exception")]
    assert visible_types("Exception", "App.Main.Run", _scope("System"), elsewhere) == []
    assert visible_types("Exception", "App.Main.Run", _scope(), []) == []


def test_an_alias_is_what_it_names_and_nothing_else() -> None:
    types = [_type("A.B"), _type("App.Svc"), _type("A.B.T")]
    scope = _scope(aliases={"Svc": ("A", "B")})
    # The alias wins over a type of the caller's own namespace with that name.
    assert _names(visible_types("Svc", "App.Main.Run", scope, types)) == ["A.B"]
    # An alias of a namespace: ``Svc.T`` is ``A.B.T``.
    assert _names(visible_types("Svc.T", "App.Main.Run", scope, types)) == ["A.B.T"]
    assert visible_types("Svc.Missing", "App.Main.Run", scope, types) == []


def test_a_qualified_name_is_resolved_from_the_enclosing_scopes_outward() -> None:
    helpers = [_type("App.Util.Helper"), _type("Other.Util.Helper")]
    assert _names(visible_types("Util.Helper", "App.Web.Handler.Get", _scope(), helpers)) == [
        "App.Util.Helper",
    ]
    assert _names(visible_types("Other.Util.Helper", "App.Web.Handler.Get", _scope(), helpers)) == [
        "Other.Util.Helper",
    ]
    assert _names(visible_types("Util.Helper", "App.Main.Run", _scope("Other"), helpers)) == [
        "App.Util.Helper",
    ]


def test_type_arguments_and_the_global_prefix_are_not_part_of_the_name() -> None:
    boxes = [_type("App.Box"), _type("Box")]
    assert _names(visible_types("Box<int>", "App.Main.Run", _scope(), boxes)) == ["App.Box"]
    assert _names(visible_types("global::Box", "App.Main.Run", _scope(), boxes)) == ["Box"]


def test_a_candidate_with_no_qualified_name_cannot_be_judged() -> None:
    assert visible_types("Conn", "App.Main.Run", _scope(), [_type("App.Conn"), _type(None, "Conn")]) is None


def test_a_caller_with_no_qualified_name_sees_the_global_namespace_and_the_usings() -> None:
    conns = [_type("Conn"), _type("Db.Conn")]
    assert _names(visible_types("Conn", None, _scope("Db"), conns)) == ["Conn"]
    assert _names(visible_types("Conn", None, _scope("Db"), conns[1:])) == ["Db.Conn"]


def test_separators_are_one() -> None:
    assert dotted("a::b::C") == dotted("\\a\\b\\C") == "a.b.C"


def test_the_type_arguments_written_are_counted_at_the_top_level() -> None:
    from hypergumbo_core.type_visibility import written_arity

    assert written_arity("Policy") == 0
    assert written_arity("Policy<int>") == 1
    assert written_arity("Map<string, List<int>>") == 2
    assert written_arity("A.B.Triple<X, Func<Y, Z>, W>") == 3


# --- languages whose imports name files -------------------------------------------


def _file_type(name: str, path: str) -> Symbol:
    return Symbol(
        id=f"typescript:{path}:1-2:{name}:class", name=name, kind="class",
        language="typescript", path=path, span=Span(1, 2, 0, 0),
    )


def _file_scope() -> ImportScope:
    from hypergumbo_core.import_scope import MODULES_ARE_FILES

    return ImportScope(module_kind=MODULES_ARE_FILES)


def _paths(found: list[Symbol] | None) -> list[str] | None:
    return None if found is None else sorted(s.path for s in found)


def test_a_declaration_of_the_callers_own_file_answers_first() -> None:
    conns = [_file_type("Conn", "src/main.ts"), _file_type("Conn", "src/db.ts")]
    scope = _file_scope()
    scope.add_named("src/db.ts", "Conn")
    assert _paths(visible_types("Conn", None, scope, conns, caller_path="src/main.ts")) == ["src/main.ts"]


def test_an_imported_name_is_the_declaration_in_the_file_the_import_names() -> None:
    conns = [_file_type("Conn", "src/db.ts"), _file_type("Conn", "legacy/db.ts")]
    scope = _file_scope()
    scope.add_named("src/db.ts", "Conn")
    assert _paths(visible_types("Conn", None, scope, conns, caller_path="src/main.ts")) == ["src/db.ts"]


def test_an_import_alias_is_looked_up_by_the_original_name() -> None:
    """``import {Conn as C} from './db'``: no class is called ``C``."""
    conns = [_file_type("Conn", "src/db.ts"), _file_type("C", "other/c.ts")]
    scope = _file_scope()
    scope.add_named("src/db.ts", "Conn", alias="C")
    assert _paths(visible_types("C", None, scope, conns, caller_path="src/main.ts")) == ["src/db.ts"]


def test_a_name_reached_through_a_namespace_binding() -> None:
    """``import * as db from './db'`` then ``db.Conn``; Rust ``use crate::db;``
    then ``db::Conn``."""
    conns = [_file_type("Conn", "src/db.ts"), _file_type("Conn", "legacy/db.ts")]
    scope = _file_scope()
    scope.add_namespace("src/db.ts", "db")
    assert _paths(visible_types("db.Conn", None, scope, conns, caller_path="src/main.ts")) == ["src/db.ts"]
    assert _paths(visible_types("db::Conn", None, scope, conns, caller_path="src/main.ts")) == ["src/db.ts"]
    # An unknown qualifier, and a path deeper than one member, name nothing here.
    assert visible_types("other.Conn", None, scope, conns, caller_path="src/main.ts") == []
    assert visible_types("db.inner.Conn", None, scope, conns, caller_path="src/main.ts") == []


def test_a_name_no_import_binds_is_not_a_type_of_the_repository_here() -> None:
    """A global, a library type, or a name re-exported through a file that
    does not declare it: nothing, however many namesakes exist."""
    conns = [_file_type("Conn", "legacy/db.ts"), _file_type("Conn", "src/db.ts")]
    assert visible_types("Conn", None, _file_scope(), conns, caller_path="src/main.ts") == []
    reexport = _file_scope()
    reexport.add_named("src/index.ts", "Conn")
    assert visible_types("Conn", None, reexport, conns, caller_path="src/main.ts") == []


def test_a_wildcard_import_of_a_file_supplies_its_declarations() -> None:
    """Rust ``use crate::db::*;``: every item of that file is in scope. Two
    wildcard files that both declare the name are a tie; an explicit binding
    and the caller's own file still answer first."""
    conns = [_file_type("Conn", "src/db.rs"), _file_type("Conn", "src/net.rs"), _file_type("Conn", "src/old.rs")]
    one = _file_scope()
    one.add_open_wildcard("src/db.rs")
    assert _paths(visible_types("Conn", None, one, conns, caller_path="src/main.rs")) == ["src/db.rs"]
    two = _file_scope()
    two.add_open_wildcard("src/db.rs")
    two.add_open_wildcard("src/net.rs")
    assert _paths(visible_types("Conn", None, two, conns, caller_path="src/main.rs")) == ["src/db.rs", "src/net.rs"]
    two.add_named("src/old.rs", "Conn")
    assert _paths(visible_types("Conn", None, two, conns, caller_path="src/main.rs")) == ["src/old.rs"]


def test_a_written_type_without_its_type_arguments() -> None:
    assert without_type_arguments("Policy") == "Policy"
    assert without_type_arguments("Policy<int>") == "Policy"
    assert without_type_arguments("App.Map<string, List<int>> ") == "App.Map"
