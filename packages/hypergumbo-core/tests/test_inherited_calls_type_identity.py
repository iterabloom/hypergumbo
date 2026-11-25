# SPDX-License-Identifier: AGPL-3.0-or-later
"""A typed receiver's type is a declaration, not a name (ADR-0064).

``inherited_calls`` Site 2 looks a method up on the receiver's type. The type
reaches it as a NAME on the unresolved edge (``receiver_type_hint``), and
until a file's import table said otherwise the name was looked up among every
class of the language: one namesake bound, two refused. With the table
(``LinkerContext.import_scopes``) the linker asks which declaration the name
means from the caller's place (``type_visibility.visible_types``): the one it
means is bound even when a namesake exists, and a name that means no
declaration of the repository from there is not bound to a namesake at all.

A file with no table keeps the name lookup, so a language whose analyzer has
not adopted the table does not move.
"""
# covers: packages/hypergumbo-core/src/hypergumbo_core/linkers/inherited_calls.py
# covers: packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import make_unresolved_edge
from hypergumbo_core.import_scope import ImportScope
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.inherited_calls import link_inherited_calls
from hypergumbo_core.linkers.registry import LinkerContext

CALLER_FILE = "web/Handler.cs"


def _sym(kind: str, name: str, qualified: str | None, path: str, lines: tuple[int, int]) -> Symbol:
    return Symbol(
        id=f"csharp:{path}:{lines[0]}-{lines[1]}:{name}:{kind}", name=name, kind=kind,
        language="csharp", path=path, qualified_name=qualified,
        span=Span(start_line=lines[0], end_line=lines[1], start_col=0, end_col=0),
        origin="test", origin_run_id="test-run",
    )


def _type_with_method(namespace: str | None, type_name: str, method: str, path: str) -> list[Symbol]:
    """A class and one method of it, in ``path``."""
    qualified = f"{namespace}.{type_name}" if namespace else None
    return [
        _sym("class", type_name, qualified, path, (1, 9)),
        _sym("method", f"{type_name}.{method}", f"{qualified}.{method}" if qualified else None,
             path, (3, 5)),
    ]


def _caller() -> Symbol:
    return _sym("method", "Handler.Get", "App.Web.Handler.Get", CALLER_FILE, (10, 20))


def _stub(caller: Symbol, callee: str, **facts: str) -> Edge:
    return make_unresolved_edge(
        "csharp", caller.id, callee, 12, "test-pass", "test-run", **facts,
    )


def _scope(*usings: str) -> ImportScope:
    scope = ImportScope()
    for namespace in usings:
        scope.add_open_wildcard(namespace)
    return scope


def _link(symbols: list[Symbol], stub: Edge, scope: ImportScope | None) -> list[Edge]:
    ctx = LinkerContext(
        repo_root=Path("/"), symbols=symbols, edges=[stub],
        import_scopes={} if scope is None else {CALLER_FILE: scope},
    )
    return link_inherited_calls(ctx).edges


def _two_conns() -> tuple[list[Symbol], Symbol, Symbol]:
    wanted = _type_with_method("App.Db", "Conn", "Execute", "db/Conn.cs")
    namesake = _type_with_method("Legacy", "Conn", "Execute", "legacy/Conn.cs")
    return [*wanted, *namesake], wanted[1], namesake[1]


def test_without_a_table_two_namesakes_are_refused_as_before() -> None:
    symbols, _, _ = _two_conns()
    caller = _caller()
    assert _link([*symbols, caller], _stub(caller, "Execute", receiver_type_hint="Conn"), None) == []


def test_the_table_settles_which_namesake_the_file_means() -> None:
    symbols, wanted, _ = _two_conns()
    caller = _caller()
    stub = _stub(caller, "Execute", receiver_type_hint="Conn")
    (edge,) = _link([*symbols, caller], stub, _scope("App.Db"))
    assert edge.dst == wanted.id
    assert edge.is_resolved
    assert edge.meta["resolved_by"] == "receiver_type"
    assert edge.derived_from[0] == stub.id


def test_a_lone_namesake_the_file_cannot_see_is_not_bound() -> None:
    """One ``Conn`` in the repository, in a namespace the caller's file does
    not import: the name means a library's type here. Without the table the
    lone namesake is bound; with it, nothing is."""
    lone = _type_with_method("Legacy", "Conn", "Execute", "legacy/Conn.cs")
    caller = _caller()
    stub = _stub(caller, "Execute", receiver_type_hint="Conn")
    assert [e.dst for e in _link([*lone, caller], stub, None)] == [lone[1].id]
    assert _link([*lone, caller], stub, _scope("System.Data")) == []


def test_a_name_two_usings_both_supply_is_refused() -> None:
    symbols, _, _ = _two_conns()
    caller = _caller()
    stub = _stub(caller, "Execute", receiver_type_hint="Conn")
    assert _link([*symbols, caller], stub, _scope("App.Db", "Legacy")) == []


def test_a_type_of_the_callers_own_namespace_needs_no_using() -> None:
    near = _type_with_method("App.Web", "Conn", "Execute", "web/Conn.cs")
    far = _type_with_method("Legacy", "Conn", "Execute", "legacy/Conn.cs")
    caller = _caller()
    stub = _stub(caller, "Execute", receiver_type_hint="Conn")
    (edge,) = _link([*near, *far, caller], stub, _scope())
    assert edge.dst == near[1].id


def test_a_qualifier_names_the_type_and_the_edge_says_so() -> None:
    """``Util.Helper()``: the call names its owner. The same lookup settles
    which ``Util``; the mark is ``qualified``, not ``receiver_type``."""
    wanted = _type_with_method("App.Tools", "Util", "Helper", "tools/Util.cs")
    namesake = _type_with_method("Legacy", "Util", "Helper", "legacy/Util.cs")
    caller = _caller()
    stub = _stub(caller, "Helper", qualifier="Util")
    assert stub.meta["qualifier"] == "Util"
    (edge,) = _link([*wanted, *namesake, caller], stub, _scope("App.Tools"))
    assert edge.dst == wanted[1].id
    assert edge.meta["resolved_by"] == "qualified"

    full = _stub(caller, "Helper", qualifier="Legacy.Util")
    (edge,) = _link([*wanted, *namesake, caller], full, _scope("App.Tools"))
    assert edge.dst == namesake[1].id


def test_a_qualifier_is_not_read_in_a_file_with_no_table() -> None:
    """The fact is new with the table; a file without one has no producer of
    it, and nothing is inferred from it there."""
    wanted = _type_with_method("App.Tools", "Util", "Helper", "tools/Util.cs")
    caller = _caller()
    assert _link([*wanted, caller], _stub(caller, "Helper", qualifier="Util"), None) == []


def test_candidates_with_no_qualified_name_keep_the_name_lookup() -> None:
    """A producer that records no qualified names cannot be judged by
    visibility: the table changes nothing for it."""
    lone = _type_with_method(None, "Conn", "Execute", "db/Conn.cs")
    caller = _caller()
    stub = _stub(caller, "Execute", receiver_type_hint="Conn")
    (edge,) = _link([*lone, caller], stub, _scope("Whatever"))
    assert edge.dst == lone[1].id


# --- one type in several files, and the caller's own type ---------------------


def test_a_type_declared_in_two_files_is_one_type() -> None:
    """A partial class: two declarations, one qualified name. The name is
    settled (it is not a tie), and a method of either part is on the type."""
    first = _sym("class", "Conn", "App.Db.Conn", "db/Conn.cs", (1, 9))
    second = _type_with_method("App.Db", "Conn", "Execute", "db/Conn.Extra.cs")
    namesake = _type_with_method("Legacy", "Conn", "Execute", "legacy/Conn.cs")
    caller = _caller()
    stub = _stub(caller, "Execute", receiver_type_hint="Conn")
    (edge,) = _link([first, *second, *namesake, caller], stub, _scope("App.Db"))
    assert edge.dst == second[1].id


def _handler_with(method: str | None, namespace: str, path: str) -> list[Symbol]:
    symbols = [_sym("class", "Handler", f"{namespace}.Handler", path, (1, 30))]
    if method:
        symbols.append(_sym("method", f"Handler.{method}", f"{namespace}.Handler.{method}", path, (22, 24)))
    return symbols


def test_a_bare_call_starts_at_the_callers_own_type_not_at_a_namesake() -> None:
    """``Helper()`` inside ``App.Web.Handler``, which has no ``Helper``; an
    unrelated ``Other.Handler`` has one. By name the namesake's method is
    bound (the first class of that name that has it). With the table the walk
    starts at the type the caller is declared in, and finds nothing."""
    own = _handler_with(None, "App.Web", CALLER_FILE)
    namesake = _handler_with("Helper", "Other", "other/Handler.cs")
    caller = _caller()
    stub = _stub(caller, "Helper", enclosing_class="Handler")
    assert [e.dst for e in _link([*own, *namesake, caller], stub, None)] == [namesake[1].id]
    assert _link([*own, *namesake, caller], stub, _scope()) == []


def test_a_bare_call_finds_a_method_of_another_part_of_its_own_type() -> None:
    own = _handler_with(None, "App.Web", CALLER_FILE)
    other_part = _handler_with("Helper", "App.Web", "web/Handler.Helpers.cs")
    namesake = _handler_with("Helper", "Other", "other/Handler.cs")
    caller = _caller()
    stub = _stub(caller, "Helper", enclosing_class="Handler")
    (edge,) = _link([*own, *namesake, *other_part, caller], stub, _scope())
    assert edge.dst == other_part[1].id
    assert edge.meta["resolved_by"] == "own_type"


def test_a_caller_whose_type_has_no_qualified_name_keeps_the_name_walk() -> None:
    own = [_sym("class", "Handler", None, CALLER_FILE, (1, 30)),
           _sym("method", "Handler.Helper", None, CALLER_FILE, (22, 24))]
    caller = _sym("method", "Handler.Get", None, CALLER_FILE, (10, 20))
    stub = _stub(caller, "Helper", enclosing_class="Handler")
    (edge,) = _link([*own, caller], stub, _scope())
    assert edge.dst == own[1].id


# --- a generic type and its non-generic namesake are two types ----------------


def _arity(symbols: list[Symbol], arity: int) -> list[Symbol]:
    symbols[0].meta = {"type_arity": arity}
    return symbols


def test_the_number_of_type_arguments_is_part_of_which_type_is_meant() -> None:
    """``Policy`` and ``Policy<TResult>`` share a name and a qualified name and
    are different types. Where the declarations say how many type parameters
    they take, the hint's own type arguments choose."""
    plain = _arity(_type_with_method("App.Db", "Policy", "Execute", "db/Policy.cs"), 0)
    generic = _arity(_type_with_method("App.Db", "Policy", "Execute", "db/Policy.TResult.cs"), 1)
    caller = _caller()
    symbols = [*plain, *generic, caller]
    (edge,) = _link(symbols, _stub(caller, "Execute", receiver_type_hint="Policy<int>"), _scope("App.Db"))
    assert edge.dst == generic[1].id
    (edge,) = _link(symbols, _stub(caller, "Execute", receiver_type_hint="Policy"), _scope("App.Db"))
    assert edge.dst == plain[1].id
    # No declaration takes two: the name means no type of the repository here.
    assert _link(symbols, _stub(caller, "Execute", receiver_type_hint="Policy<int, string>"),
                 _scope("App.Db")) == []


def test_parts_of_one_generic_type_are_still_one_type() -> None:
    first = _arity([_sym("class", "Policy", "App.Db.Policy", "db/Policy.TResult.cs", (1, 9))], 1)
    second = _arity(_type_with_method("App.Db", "Policy", "Execute", "db/Policy.TResult.Extra.cs"), 1)
    plain = _arity(_type_with_method("App.Db", "Policy", "Execute", "db/Policy.cs"), 0)
    caller = _caller()
    stub = _stub(caller, "Execute", receiver_type_hint="Policy<Dictionary<string, int>>")
    (edge,) = _link([*first, *second, *plain, caller], stub, _scope("App.Db"))
    assert edge.dst == second[1].id


def test_declarations_that_do_not_say_their_arity_are_told_apart_by_name_only() -> None:
    """A producer that records no arity: the two namesakes cannot be told
    apart, and they are not one type either. Refused."""
    plain = _type_with_method("App.Db", "Policy", "Execute", "db/Policy.cs")
    generic = _arity(_type_with_method("App.Db", "Policy", "Execute", "db/Policy.TResult.cs"), 1)
    caller = _caller()
    stub = _stub(caller, "Execute", receiver_type_hint="Policy<int>")
    (edge,) = _link([*plain, *generic, caller], stub, _scope("App.Db"))
    # One qualified name, arity known for one declaration only: treated as
    # parts of one type, as before the arity existed (first part with the method).
    assert edge.dst in (plain[1].id, generic[1].id)


def test_a_bare_call_stays_in_the_generic_or_the_plain_type_it_is_written_in() -> None:
    """``Helper()`` inside the non-generic ``Handler``; the generic
    ``Handler<T>`` of the same namespace has a ``Helper``, the plain one does
    not. The caller is declared in the plain one."""
    plain = _arity([_sym("class", "Handler", "App.Web.Handler", CALLER_FILE, (1, 30))], 0)
    generic = _arity(_handler_with("Helper", "App.Web", "web/Handler.T.cs"), 1)
    caller = _caller()
    stub = _stub(caller, "Helper", enclosing_class="Handler")
    assert _link([*plain, *generic, caller], stub, _scope()) == []
    other_part = _arity(_handler_with("Helper", "App.Web", "web/Handler.Helpers.cs"), 0)
    (edge,) = _link([*plain, *generic, *other_part, caller], stub, _scope())
    assert edge.dst == other_part[1].id


# --- what counts as a type ------------------------------------------------------


def test_every_type_like_kind_can_be_a_receivers_type() -> None:
    """A record, an enum, an actor: a method declared on one is a method on a
    type. The linker reads the kinds from the symbol-kind registry (plus the
    Ruby ``module`` it always had), not from a list of its own, which had
    left ``record`` out: a C# ``Point.Origin()`` on a record was refused."""
    from hypergumbo_core.symbol_kinds import type_like_kind_names

    caller = _caller()
    for kind in sorted(type_like_kind_names() | {"module"}):
        holder = [
            _sym(kind, "Point", "App.Geo.Point", "geo/Point.cs", (1, 9)),
            _sym("method", "Point.Origin", "App.Geo.Point.Origin", "geo/Point.cs", (3, 5)),
        ]
        stub = _stub(caller, "Origin", qualifier="Point")
        edges = _link([*holder, caller], stub, _scope("App.Geo"))
        assert [e.dst for e in edges] == [holder[1].id], kind


# --- a call on super / base starts at the parent ---------------------------------


def _extends(child: Symbol, parent: Symbol) -> Edge:
    return Edge.create(src=child.id, dst=parent.id, edge_type="extends", line=1,
                       origin="test", origin_run_id="test-run")


def _link_all(symbols: list[Symbol], edges: list[Edge]) -> list[Edge]:
    ctx = LinkerContext(repo_root=Path("/"), symbols=symbols, edges=edges)
    return link_inherited_calls(ctx).edges


def test_a_call_on_the_parent_does_not_bind_the_callers_own_override() -> None:
    """``base.Run()`` inside ``Child.Run``: the callee is the parent's Run.
    With only ``enclosing_class`` the walk starts AT the caller's type and
    finds the override, which is the caller itself. The fact
    ``super_receiver`` makes it start one step up."""
    base = _type_with_method("App", "Base", "Run", "Base.cs")
    child = _type_with_method("App", "Child", "Run", "Child.cs")
    caller = child[1]
    extends = _extends(child[0], base[0])
    plain = _stub(caller, "Run", enclosing_class="Child")
    assert [e.dst for e in _link_all([*base, *child], [extends, plain])] == [caller.id]
    up = _stub(caller, "Run", enclosing_class="Child", super_receiver=True)
    assert up.meta["super_receiver"] is True
    (edge,) = _link_all([*base, *child], [extends, up])
    assert edge.dst == base[1].id
    assert edge.meta["resolved_by"] == "own_type"


def test_a_call_on_the_parent_reaches_a_grandparent_and_stops_where_none_has_it() -> None:
    top = _type_with_method("App", "Top", "Run", "Top.cs")
    middle = [_sym("class", "Middle", "App.Middle", "Middle.cs", (1, 9))]
    child = _type_with_method("App", "Child", "Run", "Child.cs")
    edges = [_extends(child[0], middle[0]), _extends(middle[0], top[0])]
    up = _stub(child[1], "Run", enclosing_class="Child", super_receiver=True)
    (edge,) = _link_all([*top, *middle, *child], [*edges, up])
    assert edge.dst == top[1].id
    # No ancestor declares it (the parent is a library's type): unresolved,
    # never the caller's own method.
    alone = _type_with_method("App", "Solo", "Run", "Solo.cs")
    lone = _stub(alone[1], "Run", enclosing_class="Solo", super_receiver=True)
    assert _link_all(alone, [lone]) == []


# --- a member that is not a method can be what a call invokes --------------------


def _type_with_member(kind: str, member: str) -> list[Symbol]:
    return [
        _sym("class", "Clock", "App.Util.Clock", "util/Clock.cs", (1, 9)),
        _sym(kind, f"Clock.{member}", f"App.Util.Clock.{member}", "util/Clock.cs", (3, 3)),
    ]


def test_an_invoked_field_or_property_of_the_type_is_the_callee() -> None:
    """``public static Func<TimeSpan, Task> Sleep = Task.Delay;`` then
    ``Clock.Sleep(t)``: the call invokes what the member holds, and the member
    is all the source names. Once the type is settled and no method of that
    name is on it, the member is bound."""
    caller = _caller()
    for kind in ("field", "property"):
        holder = _type_with_member(kind, "Sleep")
        named = _stub(caller, "Sleep", qualifier="Clock")
        (edge,) = _link([*holder, caller], named, _scope("App.Util"))
        assert (edge.dst, edge.edge_type, edge.meta["resolved_by"]) == (holder[1].id, "calls", "qualified")
        typed = _stub(caller, "Sleep", receiver_type_hint="Clock")
        (edge,) = _link([*holder, caller], typed, _scope("App.Util"))
        assert (edge.dst, edge.meta["resolved_by"]) == (holder[1].id, "receiver_type")


def test_a_method_of_that_name_is_preferred_to_a_member() -> None:
    holder = _type_with_member("field", "Sleep")
    method = _sym("method", "Clock.Sleep", "App.Util.Clock.Sleep", "util/Clock.cs", (5, 7))
    caller = _caller()
    (edge,) = _link([*holder, method, caller], _stub(caller, "Sleep", qualifier="Clock"), _scope("App.Util"))
    assert edge.dst == method.id


def test_a_member_is_bound_only_where_the_type_is_settled() -> None:
    """With no table the type is found by name alone, and a name-found type's
    field is not a reason to bind a call: unchanged, nothing bound."""
    holder = _type_with_member("field", "Sleep")
    caller = _caller()
    assert _link([*holder, caller], _stub(caller, "Sleep", receiver_type_hint="Clock"), None) == []


# --- an alias names a type whose own name is different ----------------------------


def test_a_receiver_typed_by_an_alias_reaches_the_aliased_type() -> None:
    """``using Svc = App.Db.Conn;`` and a receiver declared ``Svc``: the type
    is ``Conn``. The candidates are found by the ALIASED name, not by the
    alias, which no class is called."""
    conn = _type_with_method("App.Db", "Conn", "Execute", "db/Conn.cs")
    namesake = _type_with_method("Legacy", "Conn", "Execute", "legacy/Conn.cs")
    scope = _scope()
    scope.add_named("App.Db", "Conn", alias="Svc")
    caller = _caller()
    (edge,) = _link([*conn, *namesake, caller], _stub(caller, "Execute", receiver_type_hint="Svc"), scope)
    assert edge.dst == conn[1].id


# --- a language whose imports name files ------------------------------------------


def test_a_typed_receiver_follows_a_named_import_to_its_file() -> None:
    """TypeScript-shaped: two classes ``Conn`` in two files, no namespaces.
    The caller's file imports one of them; by name the two were a tie."""
    from hypergumbo_core.import_scope import MODULES_ARE_FILES

    def ts(kind: str, name: str, path: str, lines: tuple[int, int]) -> Symbol:
        return Symbol(
            id=f"typescript:{path}:{lines[0]}-{lines[1]}:{name}:{kind}", name=name, kind=kind,
            language="typescript", path=path,
            span=Span(start_line=lines[0], end_line=lines[1], start_col=0, end_col=0),
            origin="test", origin_run_id="test-run",
        )

    wanted = [ts("class", "Conn", "src/db.ts", (1, 9)), ts("method", "Conn.execute", "src/db.ts", (3, 5))]
    namesake = [ts("class", "Conn", "legacy/db.ts", (1, 9)), ts("method", "Conn.execute", "legacy/db.ts", (3, 5))]
    caller = ts("function", "run", "src/main.ts", (10, 20))
    stub = make_unresolved_edge(
        "typescript", caller.id, "execute", 12, "test-pass", "test-run", receiver_type_hint="Conn",
    )
    symbols = [*wanted, *namesake, caller]
    assert link_inherited_calls(LinkerContext(repo_root=Path("/"), symbols=symbols, edges=[stub])).edges == []
    scope = ImportScope(module_kind=MODULES_ARE_FILES)
    scope.add_named("src/db.ts", "Conn")
    ctx = LinkerContext(repo_root=Path("/"), symbols=symbols, edges=[stub],
                        import_scopes={"src/main.ts": scope})
    (edge,) = link_inherited_calls(ctx).edges
    assert edge.dst == wanted[1].id
    assert edge.meta["resolved_by"] == "receiver_type"


# --- a function called by its imported name ----------------------------------------


def _ts(kind: str, name: str, path: str, lines: tuple[int, int] = (1, 5)) -> Symbol:
    return Symbol(
        id=f"typescript:{path}:{lines[0]}-{lines[1]}:{name}:{kind}", name=name, kind=kind,
        language="typescript", path=path,
        span=Span(start_line=lines[0], end_line=lines[1], start_col=0, end_col=0),
        origin="test", origin_run_id="test-run",
    )


def _ts_stub(caller: Symbol, callee: str, **facts: str) -> Edge:
    return make_unresolved_edge("typescript", caller.id, callee, 12, "test-pass", "test-run", **facts)


def _file_scope() -> ImportScope:
    from hypergumbo_core.import_scope import MODULES_ARE_FILES

    return ImportScope(module_kind=MODULES_ARE_FILES)


def _ts_link(symbols: list[Symbol], stub: Edge, scope: ImportScope | None) -> list[Edge]:
    ctx = LinkerContext(
        repo_root=Path("/"), symbols=symbols, edges=[stub],
        import_scopes={} if scope is None else {"src/main.ts": scope},
    )
    return link_inherited_calls(ctx).edges


def test_a_bare_call_to_an_imported_function_reaches_the_file_the_import_names() -> None:
    """``import {helper} from './util'``; ``helper()``. Two functions named
    helper exist; the import says which."""
    wanted, decoy = _ts("function", "helper", "src/util.ts"), _ts("function", "helper", "legacy/util.ts")
    caller = _ts("function", "run", "src/main.ts", (10, 20))
    scope = _file_scope()
    scope.add_named("src/util.ts", "helper")
    stub = _ts_stub(caller, "helper")
    (edge,) = _ts_link([wanted, decoy, caller], stub, scope)
    assert (edge.dst, edge.edge_type, edge.is_resolved) == (wanted.id, "calls", True)
    assert edge.meta["resolved_by"] == "import"
    assert edge.derived_from[0] == stub.id
    # No binding for the name, or no table at all: nothing is bound.
    assert _ts_link([wanted, decoy, caller], stub, _file_scope()) == []
    assert _ts_link([wanted, decoy, caller], stub, None) == []


def test_an_imported_function_under_another_local_name() -> None:
    """``import {helper as h} from './util'``; ``h()``."""
    wanted, decoy = _ts("function", "helper", "src/util.ts"), _ts("function", "h", "legacy/h.ts")
    caller = _ts("function", "run", "src/main.ts", (10, 20))
    scope = _file_scope()
    scope.add_named("src/util.ts", "helper", alias="h")
    (edge,) = _ts_link([wanted, decoy, caller], _ts_stub(caller, "h"), scope)
    assert edge.dst == wanted.id


def test_a_call_through_a_namespace_binding_reaches_that_files_function() -> None:
    """``import * as util from './util'``; ``util.helper()``."""
    wanted, decoy = _ts("function", "helper", "src/util.ts"), _ts("function", "helper", "legacy/util.ts")
    caller = _ts("function", "run", "src/main.ts", (10, 20))
    scope = _file_scope()
    scope.add_namespace("src/util.ts", "util")
    (edge,) = _ts_link([wanted, decoy, caller], _ts_stub(caller, "helper", qualifier="util"), scope)
    assert (edge.dst, edge.meta["resolved_by"]) == (wanted.id, "import")
    # A qualifier that is no binding of the file is not a module of it.
    assert _ts_link([wanted, decoy, caller], _ts_stub(caller, "helper", qualifier="other"), scope) == []


def test_the_bound_file_must_declare_the_function() -> None:
    """A re-export: the binding names a file that does not declare the name.
    Nothing is bound (the lookup does not follow re-exports)."""
    elsewhere = _ts("function", "helper", "src/impl.ts")
    caller = _ts("function", "run", "src/main.ts", (10, 20))
    scope = _file_scope()
    scope.add_named("src/index.ts", "helper")
    assert _ts_link([elsewhere, caller], _ts_stub(caller, "helper"), scope) == []


def test_a_nearer_namesake_of_another_arity_does_not_hide_the_type() -> None:
    """C# looks a type up by name AND arity together. The caller's own
    namespace declares a non-generic ``Registry``; a namespace the file uses
    declares ``Registry<TKey>``. A receiver declared ``Registry<string>`` is
    the generic one: the nearer scope does not declare a type of that arity."""
    nearer = _arity(_type_with_method("App.Web", "Registry", "Dispose", "web/Registry.cs"), 0)
    generic = _arity(_type_with_method("Lib", "Registry", "Dispose", "lib/Registry.cs"), 1)
    caller = _caller()
    symbols = [*nearer, *generic, caller]
    (edge,) = _link(symbols, _stub(caller, "Dispose", receiver_type_hint="Registry<string>"), _scope("Lib"))
    assert edge.dst == generic[1].id
    # Written without type arguments, the same receiver is the nearer one.
    (edge,) = _link(symbols, _stub(caller, "Dispose", receiver_type_hint="Registry"), _scope("Lib"))
    assert edge.dst == nearer[1].id


# --- found on the JavaScript / TypeScript adapter -----------------------------------


def test_the_caller_of_a_component_script_binds_typescript_declarations() -> None:
    """A call at the top of a ``.vue`` script has the component FILE for its
    caller, labelled ``vue``; the function it imports is TypeScript. One
    language for calls (``type_visibility.call_language``)."""
    helper = _ts("function", "helper", "src/util.ts")
    component = Symbol(
        id="vue:src/Main.vue:1-40:Main.vue:file", name="Main.vue", kind="file", language="vue",
        path="src/Main.vue", span=Span(start_line=1, end_line=40, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run",
    )
    scope = _file_scope()
    scope.add_named("src/util.ts", "helper")
    stub = make_unresolved_edge("vue", component.id, "helper", 12, "test-pass", "test-run")
    ctx = LinkerContext(repo_root=Path("/"), symbols=[helper, component], edges=[stub],
                        import_scopes={"src/Main.vue": scope})
    (edge,) = link_inherited_calls(ctx).edges
    assert edge.dst == helper.id
    # A typed receiver in the same script: the class is TypeScript too.
    conn = [_ts("class", "Conn", "src/db.ts", (1, 9)), _ts("method", "Conn.execute", "src/db.ts", (3, 5))]
    scope.add_named("src/db.ts", "Conn")
    typed = make_unresolved_edge("vue", component.id, "execute", 14, "test-pass", "test-run",
                                 receiver_type_hint="Conn")
    ctx = LinkerContext(repo_root=Path("/"), symbols=[*conn, component], edges=[typed],
                        import_scopes={"src/Main.vue": scope})
    (edge,) = link_inherited_calls(ctx).edges
    assert edge.dst == conn[1].id


def test_a_call_on_a_receiver_is_not_a_call_of_an_imported_function() -> None:
    """``import {merge} from './util'`` and, elsewhere, ``x.merge()``: the
    member call has a receiver, so the import does not say what it invokes."""
    merge = _ts("function", "merge", "src/util.ts")
    caller = _ts("function", "run", "src/main.ts", (10, 20))
    scope = _file_scope()
    scope.add_named("src/util.ts", "merge")
    member = make_unresolved_edge("typescript", caller.id, "merge", 12, "test-pass", "test-run",
                                  call_construct="method")
    assert _ts_link([merge, caller], member, scope) == []


def test_a_type_of_a_language_whose_imports_name_files_is_one_file() -> None:
    """Two classes ``Handler`` in two TypeScript files share the qualified
    name ``Handler`` and are two types. ``this.helper()`` in one of them must
    not find the other's ``helper`` (in C# the two would be parts of one
    partial class)."""
    own = [_ts("class", "Handler", "src/main.ts", (1, 30)),
           Symbol(id="typescript:src/main.ts:10-20:Handler.get:method", name="Handler.get", kind="method",
                  language="typescript", path="src/main.ts", qualified_name="Handler.get",
                  span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
                  origin="test", origin_run_id="test-run")]
    own[0].qualified_name = "Handler"
    other = [_ts("class", "Handler", "legacy/main.ts", (1, 30)), _ts("method", "Handler.helper", "legacy/main.ts", (3, 5))]
    other[0].qualified_name, other[1].qualified_name = "Handler", "Handler.helper"
    stub = make_unresolved_edge("typescript", own[1].id, "helper", 12, "test-pass", "test-run",
                                enclosing_class="Handler")
    assert _ts_link([*own, *other], stub, _file_scope()) == []
