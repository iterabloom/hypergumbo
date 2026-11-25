# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which type a name means from one place in one file (``type_visibility``, ADR-0064)."""
# covers: packages/hypergumbo-core/src/hypergumbo_core/type_visibility.py
from __future__ import annotations

from hypergumbo_core.import_scope import ImportScope
from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.type_visibility import dotted, visible_types


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
